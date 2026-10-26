"""Engine lifecycle manager — extracted from web/server.py."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from parayabuck.config import (
    DEFAULT_DECISION_DB_PATH,
    credential_file_mode_warning,
    credential_from_directory,
    load_config,
)
from parayabuck.live_safety import (
    LIVE_API_KEY_ENV,
    LIVE_SECRET_KEY_ENV,
    LiveOperatorApproval,
    LiveStartupProof,
    credential_env_names,
    operator_halt_active,
    operator_halt_marker_path,
    read_operator_halt,
)
from parayabuck.logger import get_logger
from parayabuck.trading_host import TRADING_HOST_REFUSAL, trading_host_authorized
from parayabuck.utils.io import atomic_write
from parayabuck.web.schemas.settings import SettingsPayload, ValidatedSettings

log = get_logger("parayabuck.web.engine")

_AUTO_START_RETRY_BACKOFF: tuple[int, ...] = (5, 15, 30, 60)
_ACTIVE_ENGINE_STATES: frozenset[str] = frozenset(
    {"starting", "running", "warming_up", "degraded", "halted", "stopping", "stop_failed"}
)
# A failed start is invisible to systemd: /healthz stays 200, so
# Restart=on-failure never fires, and every alarm that could report it runs
# inside a runtime that never started. Every start-failure path therefore
# emits ``runtime_start_failed``; a retrying auto-start stays quiet for the
# first attempts, then repeats the alert every
# ``_AUTO_START_RETRY_ALERT_REPEAT`` attempts so an endless retry loop
# cannot flood the log/journal while still reporting itself.
# A running engine whose account loop has not completed a pass this long is
# hung: the systemd watchdog pings stop (see watchdog_healthy). Before the first
# pass, startup gets the longer grace.
_WATCHDOG_ACCOUNT_LOOP_MAX_AGE_SECONDS = 120.0
_WATCHDOG_STARTUP_GRACE_SECONDS = 600.0
_AUTO_START_RETRY_ALERT_AFTER = 3
_AUTO_START_RETRY_ALERT_REPEAT = 10


def _retry_alert_due(attempts: int) -> bool:
    """True on the first alerting retry and every Nth attempt after it."""
    if attempts < _AUTO_START_RETRY_ALERT_AFTER:
        return False
    return (attempts - _AUTO_START_RETRY_ALERT_AFTER) % _AUTO_START_RETRY_ALERT_REPEAT == 0


def _alert_start_failure(*, reason: str, message: str, auto_start: bool, attempts: int | None = None) -> None:
    """Alert the operator that the engine did not start. Never raises."""
    try:
        from parayabuck.monitoring.alerts import emit as emit_alert

        emit_alert(
            "runtime_start_failed",
            message=message,
            reason=reason,
            auto_start=auto_start,
            attempts=attempts,
        )
    except Exception:  # pragma: no cover - alerting must never mask the failure
        log.exception("Could not emit runtime_start_failed alert")


def _alert_live_rearm_required(reason: str) -> None:
    """Tell the owner LIVE is waiting for a manual re-arm. Never raises."""
    try:
        from parayabuck.monitoring.alerts import emit as emit_alert

        emit_alert(
            "live_rearm_required",
            message=(
                "LIVE trading is configured but did not start automatically; a manual re-arm is needed "
                f"(parayabuck authorize-live, then arm in the dashboard): {reason}"
            ),
            reason=reason,
        )
    except Exception:  # pragma: no cover - alerting must never mask the refusal
        log.exception("Could not emit live_rearm_required alert")


_START_ENGINE_BLOCKING_STATES: frozenset[str] = _ACTIVE_ENGINE_STATES - {"starting"}
# Reported instead of a probe error when the live gates are closed, so the
# operator sees why the account binding cannot be verified yet.
_LIVE_PROBE_SKIPPED_REASON = "live account probe skipped: live.enabled=false or PARAYABUCK_ENABLE_LIVE is not YES"


def _yaml_block(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """Return ``raw[name]``, creating it when absent or an empty YAML block.

    ``load_config`` treats ``runtime:`` (a key with no value) like a missing
    block; ``setdefault`` would return that ``None`` and crash the caller.
    """
    value = raw.get(name)
    if value is None:
        value = raw[name] = {}
    return value


@dataclass(frozen=True)
class LiveArmToken:
    account_id: str
    account_suffix: str
    credential_fingerprint: str
    config_digest: str
    issued_at: datetime
    expires_at: datetime


# Global Web Server State
class EngineManager:
    def __init__(self, backtest_manager: Any | None = None, ticket_store: Any | None = None) -> None:
        self.config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
        self.env_path = Path(os.getenv("ENV_PATH") or ".env")
        if not self.config_path.exists() and Path("config/settings.yaml").exists():
            self.config_path = Path("config/settings.yaml")

        self.broker: Any = None
        self.runtime: Any = None
        self.engine_state: str = "stopped"
        self.last_engine_error: str | None = None
        self.last_account: dict[str, Any] | None = None
        self._last_account_monotonic = 0.0
        self._account_cache_ttl_seconds = 5.0
        self._account_lock = threading.Lock()
        self._state_lock = threading.RLock()
        if backtest_manager is None:
            try:
                from parayabuck.web.managers.backtest import BacktestManager

                backtest_manager = BacktestManager(engine_state_provider=self.current_engine_state)
            except Exception:
                backtest_manager = None
        self._backtest_manager = backtest_manager
        self.control_token = secrets.token_urlsafe(32)
        self._live_armed = False
        self._pending_live_arm: LiveArmToken | None = None
        self._live_arm_token: LiveArmToken | None = None
        # Composition-owned applications bind an app-local ticket store.  A
        # missing store keeps the legacy direct-call behavior below.
        self._ticket_store = ticket_store
        self._paper_mode_cache: tuple[int, bool] | None = None
        # Bounded autonomous startup recovery (Web auto-start path only):
        # attempt counter, next-attempt deadline (time.monotonic()), and the
        # sleeping retry task, which stop_engine/app shutdown cancel.
        self._retry_attempt = 0
        self._retry_next_at: float | None = None
        self._retry_task: asyncio.Task[None] | None = None
        # The loop owning _retry_task: Task.cancel() is not thread-safe, but
        # stop_engine runs on a to_thread worker and must cancel via
        # loop.call_soon_threadsafe instead.
        self._retry_task_loop: asyncio.AbstractEventLoop | None = None
        # Set by stop_engine while a start attempt is still in flight, so the
        # running startup sequence stops before its order-producing steps.
        self._start_cancel = threading.Event()
        self.security_warning = _tracked_secret_warning(Path.cwd())
        if self.security_warning:
            log.critical("%s", self.security_warning)

    def bind_backtest_manager(self, backtest_manager: Any) -> None:
        """Bind the composition-owned backtest guard used by engine starts."""
        if backtest_manager is None or not hasattr(backtest_manager, "lock"):
            raise ValueError("EngineManager requires a bound BacktestManager guard")
        with self._state_lock:
            self._backtest_manager = backtest_manager

    def bind_ticket_store(self, ticket_store: Any) -> None:
        """Bind the app-local WebSocket ticket store used by this manager."""
        if ticket_store is None or not all(
            callable(getattr(ticket_store, name, None)) for name in ("issue", "consume")
        ):
            raise ValueError("EngineManager requires a bound WebSocket ticket store")
        with self._state_lock:
            self._ticket_store = ticket_store

    def _read_paper_mode(self) -> bool | None:
        """Tri-state mode read: True/False from a readable config, None otherwise.

        None means the mode is unknown (missing, unreadable or corrupt file);
        each caller picks its own fail-safe direction for that case.
        """
        try:
            if self.config_path.exists():
                modified_ns = self.config_path.stat().st_mtime_ns
                cached = self._paper_mode_cache
                if cached is not None and cached[0] == modified_ns:
                    return cached[1]
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if not isinstance(cfg, dict):
                    raise yaml.YAMLError("settings root is not a mapping")
                runtime = cfg.get("runtime") or {}
                runtime_mode = runtime.get("mode")
                if runtime_mode is None and (cfg.get("alpaca") or {}).get("paper") is False:
                    # A legacy live configuration must never be silently shown
                    # as paper while the operator migrates to runtime.mode.
                    paper = False
                else:
                    paper = str(runtime_mode or "paper").lower() != "live"
                self._paper_mode_cache = (modified_ns, paper)
                return paper
        except (OSError, yaml.YAMLError, AttributeError) as exc:
            log.error("Could not read paper-mode flag from %s: %s", self.config_path, exc)
        return None

    def is_paper_mode(self) -> bool:
        """Badge/mode flag: an unknown mode is shown as NOT paper.

        A config we cannot read must not silently present live mode as
        paper, so the operator notices the corruption. Never use this to
        pick a broker endpoint: see :meth:`display_broker_paper`.
        """
        return self._read_paper_mode() is True

    def display_broker_paper(self) -> bool:
        """Endpoint for the display poll: fails closed to PAPER when unknown.

        An unreadable or corrupt config must never send credentials to the
        live endpoint for a read-only display poll.
        """
        return self._read_paper_mode() is not False

    def get_credentials(self) -> tuple[str | None, str | None]:
        """The Alpaca key pair of the CONFIGURED mode (B3: never the other's).

        PAPER (and an unreadable mode, which fails closed to PAPER) reads
        ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY``; LIVE reads only
        ``ALPACA_LIVE_API_KEY``/``ALPACA_LIVE_SECRET_KEY`` and never falls
        back to the PAPER names.
        """
        if self._read_paper_mode() is False:
            return self.get_live_credentials()
        return self.get_paper_credentials()

    def get_paper_credentials(self) -> tuple[str | None, str | None]:
        """The PAPER key pair (the settings drawer edits only these)."""
        return self._read_credentials(live=False)

    def get_live_credentials(self) -> tuple[str | None, str | None]:
        """The LIVE key pair (arming, LIVE start); never the PAPER names."""
        return self._read_credentials(live=True)

    def _read_credentials(self, *, live: bool) -> tuple[str | None, str | None]:
        """One mode's two keys; the env file wins over the process environment.

        Read per call (the settings drawer rewrites the file) but only these
        two keys: reloading the whole file with override=True on every poll
        rewrote unrelated process environment variables.
        """
        key_name, secret_name = credential_env_names(live=live)
        values = dotenv_values(self.env_path) if self.env_path.exists() else {}
        if values:
            self._warn_loose_credential_file()
        # systemd credential files ($CREDENTIALS_DIRECTORY/<name>) win first.
        key = credential_from_directory(key_name) or values.get(key_name) or os.getenv(key_name)
        secret = credential_from_directory(secret_name) or values.get(secret_name) or os.getenv(secret_name)
        return key, secret

    def configure_alert_delivery(self) -> None:
        """Install the opt-in e-mail alert sink from the current settings. Never raises."""
        try:
            from parayabuck.monitoring.email_alerts import configure_email_alerts

            cfg = load_config(str(self.config_path))
            configure_email_alerts(cfg.alerting.email, mode=cfg.runtime.mode.value)
        except Exception as exc:
            log.warning("E-mail alert delivery not configured: %s", exc)

    def _warn_loose_credential_file(self) -> None:
        """Log (once per path and mode) a group/other-readable credential file.

        A warning only, never a refusal: the VM must not fail to start on it.
        """
        warning = credential_file_mode_warning(self.env_path)
        if warning is None:
            return
        try:
            key = (str(self.env_path), self.env_path.stat().st_mode)
        except OSError:
            return
        if getattr(self, "_credential_mode_warned", None) != key:
            self._credential_mode_warned = key
            log.warning("%s", warning)

    def operator_halt_marker(self) -> Path:
        """Halt marker of the configured runtime data directory.

        The directory is the parent of ``runtime.decision_db_path`` (the same
        rule the CLI uses), so PAPER's ``data/runtime`` and LIVE's
        ``data/runtime/live`` each have their own marker. An unreadable config
        falls back to the PAPER default directory.
        """
        try:
            decision_path = load_config(str(self.config_path)).runtime.decision_db_path
        except Exception:
            decision_path = DEFAULT_DECISION_DB_PATH
        return operator_halt_marker_path(Path(decision_path).parent)

    def _operator_halt_refusal(self) -> str | None:
        """Refusal text while an operator halt is in force, else None."""
        marker = self.operator_halt_marker()
        if not operator_halt_active(marker):
            return None
        record = read_operator_halt(marker) or {}
        return (
            f"Engine start refused: operator halt is in force ({marker}; reason: "
            f"{record.get('reason') or 'unspecified'}). Run `parayabuck resume` to clear it."
        )

    def should_auto_start(self) -> bool:
        """Auto-start configured paper engines without weakening live safeguards.

        LIVE auto-starts only with a valid standing LIVE authorization
        (docs/live_readiness_v1.md B2); otherwise the engine stays stopped and
        ``live_rearm_required`` tells the owner a manual re-arm is needed.
        An operator halt marker keeps the engine stopped across restarts.
        """
        halt_refusal = self._operator_halt_refusal()
        if halt_refusal is not None:
            log.error("%s", halt_refusal)
            return False
        if not trading_host_authorized():
            log.warning("%s", TRADING_HOST_REFUSAL)
            return False
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            app_config = (raw or {}).get("app") or {}
            runtime_config = (raw or {}).get("runtime") or {}
            legacy_live = "mode" not in runtime_config and ((raw or {}).get("alpaca") or {}).get("paper") is False
            if (
                str(runtime_config.get("mode", "paper")).lower() == "live"
                and not legacy_live
                and app_config.get("auto_start_engine", True)
            ):
                return self._live_auto_start_allowed()
            key, secret = self.get_credentials()
            return bool(
                app_config.get("auto_start_engine", True)
                and str(runtime_config.get("mode", "paper")).lower() == "paper"
                and not legacy_live
                and key
                and secret
                and not self.security_warning
            )
        except Exception as exc:
            # Auto-start is a safety-relevant arm: a silent False would leave
            # the engine off with no explanation.
            log.error("Auto-start decision failed; engine will not start: %s", exc)
            return False

    def _live_auto_start_allowed(self) -> bool:
        """LIVE unattended start gate: every LIVE guard plus a valid standing
        authorization. Emits ``live_rearm_required`` when it says no."""
        reason: str | None = None
        try:
            key, secret = self.get_live_credentials()
            if self.security_warning:
                reason = "the tracked-secret security check is blocking engine starts"
            elif os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                reason = "PARAYABUCK_ENABLE_LIVE is not YES"
            elif not key or not secret:
                reason = f"{LIVE_API_KEY_ENV} / {LIVE_SECRET_KEY_ENV} are not set"
            else:
                from parayabuck.live_authorization import standing_authorization_status

                status = standing_authorization_status(load_config(str(self.config_path)))
                if not status.valid:
                    reason = status.detail
        except Exception as exc:
            reason = f"LIVE configuration could not be verified: {exc}"
        if reason is None:
            log.warning("LIVE auto-start authorized by the standing LIVE authorization")
            return True
        log.warning("LIVE engine not started automatically; manual re-arm required: %s", reason)
        _alert_live_rearm_required(reason)
        return False

    def _standing_startup_proof(self, key: str, secret: str) -> LiveStartupProof:
        """Startup proof for an unattended LIVE start from the standing
        authorization. The runtime re-verifies it (signature, expiry, config,
        cap, host, account) before any broker mutation."""
        from parayabuck.live_authorization import load_standing_authorization
        from parayabuck.live_safety import LiveApprovalError

        cfg = load_config(str(self.config_path))
        digests, digest_error = self._resolve_live_configuration_digests()
        if digests is None:
            raise ValueError(f"Live configuration could not be verified: {digest_error}")
        try:
            authorization = load_standing_authorization(cfg, configuration_digests=digests)
        except LiveApprovalError as exc:
            _alert_live_rearm_required(str(exc))
            raise ValueError(f"LIVE auto-start needs a manual re-arm: {exc}") from exc
        now = datetime.now(UTC)
        return LiveStartupProof(
            account_id=authorization.account_id,
            credential_fingerprint=self._credential_fingerprint(key, secret),
            configuration_digests=digests,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            authorization="standing",
        )

    def current_engine_state(self) -> str:
        with self._state_lock:
            if self.runtime is not None:
                try:
                    runtime_health = self.runtime.health()
                    self.engine_state = runtime_health.state
                except Exception as exc:
                    # A failing health probe must not take down /api/status
                    # and every websocket subscriber; fall back to the last
                    # known state.
                    log.warning("Runtime health probe failed: %s", exc)
                return self.engine_state
            return self.engine_state

    def watchdog_healthy(self, *, now: datetime | None = None) -> bool:
        """systemd watchdog gate: True while the process may be pinged.

        No running engine (stopped, never started, halted before start) keeps
        the web process alive. A running engine must have finished an account
        loop pass (successful or not) within ``_WATCHDOG_ACCOUNT_LOOP_MAX_AGE_SECONDS``; a hung
        account loop therefore stops the pings and systemd restarts the unit.
        Lock-free on purpose: a blocked pinger would be a false watchdog kill.
        """
        runtime = self.runtime
        if runtime is None or not getattr(runtime, "_running", False):
            return True
        moment = now if now is not None else datetime.now(UTC)
        last = getattr(runtime, "last_account_loop_pass_at", None)
        if isinstance(last, datetime):
            return (moment - last).total_seconds() <= _WATCHDOG_ACCOUNT_LOOP_MAX_AGE_SECONDS
        started = getattr(runtime, "_watchdog_started_at", None)
        if isinstance(started, datetime):
            return (moment - started).total_seconds() <= _WATCHDOG_STARTUP_GRACE_SECONDS
        return False

    def has_active_runtime(self) -> bool:
        with self._state_lock:
            return self.runtime is not None or self.engine_state in _ACTIVE_ENGINE_STATES

    def _validated_settings_payload(self, payload: SettingsPayload) -> ValidatedSettings:
        """Validate a settings save and build the full settings.yaml mapping.

        Every rejection path raises ``ValueError`` BEFORE any engine is
        stopped or any file is written.

        Implements PATCH semantics: fields not in ``payload.model_fields_set``
        preserve the current config instead of clobbering with defaults.
        """
        if self.engine_state in {"starting", "stopping"}:
            raise ValueError("Bot is currently changing state; wait a moment and try again")

        fields_set = payload.model_fields_set

        # Determine current paper mode before mutation for entering_live detection and digest.
        current_paper = self.is_paper_mode()
        # paper_mode is explicit in payload; otherwise derive from live_trading_enabled if provided.
        if "paper_mode" in fields_set and "live_trading_enabled" in fields_set:
            if payload.paper_mode == payload.live_trading_enabled:
                raise ValueError("Contradictory trading mode: paper_mode and live_trading_enabled disagree")
        if "paper_mode" in fields_set:
            target_paper = payload.paper_mode
        elif "live_trading_enabled" in fields_set:
            target_paper = not payload.live_trading_enabled
        else:
            target_paper = current_paper
        entering_live = current_paper and not target_paper
        if entering_live and payload.restart_engine:
            raise ValueError("Saving live mode requires restart_engine=false; start live separately after arming")
        if entering_live:
            live_key, live_secret = self.get_live_credentials()
            if not live_key or not live_secret:
                raise ValueError(f"Alpaca LIVE credentials ({LIVE_API_KEY_ENV} / {LIVE_SECRET_KEY_ENV}) are required")
            self._require_valid_live_arm(
                live_key,
                live_secret,
                consume=False,
            )
        # The drawer edits the PAPER key pair only; LIVE keys live in the
        # host .env (ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY).
        current_key, current_secret = self.get_credentials() if current_paper else self.get_paper_credentials()
        # Preserve credentials when fields omitted or empty.
        if "api_key" in fields_set:
            api_key = payload.api_key.strip() or current_key or ""
        else:
            api_key = current_key or ""
        if "secret_key" in fields_set:
            secret_key = payload.secret_key.strip() or current_secret or ""
        else:
            secret_key = current_secret or ""
        if any(char in api_key + secret_key for char in "\r\n"):
            raise ValueError("Credentials may not contain newlines")
        # Container credential immutability: in container mode credentials are external secrets.
        credentials_changed = (api_key, secret_key) != (
            current_key or "",
            current_secret or "",
        )
        if credentials_changed and os.getenv("PARAYABUCK_CONTAINER") == "YES":
            raise ValueError(
                "Credential changes are not allowed in container mode; update the host .env or secret manager and recreate the container"
            )
        # Live dollar cap finite check before assignment.
        if "live_dollar_cap" in fields_set:
            if not math.isfinite(payload.live_dollar_cap):
                raise ValueError("live.hard_dollar_cap must be finite")
        if "daily_loss_limit_pct" in fields_set:
            if not math.isfinite(payload.daily_loss_limit_pct):
                raise ValueError("Risk ratios must be finite")
        if "max_drawdown_pct" in fields_set:
            if not math.isfinite(payload.max_drawdown_pct):
                raise ValueError("Risk ratios must be finite")
        # Write settings.yaml
        cfg_raw: dict[str, Any] = {}
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                cfg_raw = yaml.safe_load(f) or {}

        old_app = dict(cfg_raw.get("app") or {})

        # Obsolete engine/risk keys from older Web saves would make the next
        # load_config() reject the whole file. Strip them so saving settings
        # heals previously corrupted configurations instead of preserving them.
        obsolete_engine_keys = {
            "timeframe",
            "higher_timeframe",
            "warmup_bars",
            "ai_logging_enabled",
            "ai_log_path",
            "prediction_horizon_bars",
        }
        engine_raw = dict(cfg_raw.get("engine") or {})
        for key in obsolete_engine_keys:
            engine_raw.pop(key, None)
        if engine_raw:
            cfg_raw["engine"] = engine_raw
        else:
            cfg_raw.pop("engine", None)
        obsolete_risk_keys = {
            "risk_per_trade_pct",
            "minimum_crypto_order_notional",
            "maximum_crypto_order_notional",
            "max_sector_exposure_pct",
            "max_open_risk_pct",
            "minimum_risk_reward",
            "consecutive_loss_reduction_after",
            "volatility_targeting_enabled",
            "annualized_volatility_target",
            "minimum_volatility_scale",
            "maximum_volatility_scale",
        }
        risk_raw = dict(cfg_raw.get("risk") or {})
        for key in obsolete_risk_keys:
            risk_raw.pop(key, None)
        cfg_raw["risk"] = risk_raw

        _yaml_block(cfg_raw, "runtime")
        runtime_save = dict(cfg_raw["runtime"])
        # PATCH semantics for runtime
        if "paper_mode" in fields_set or "live_trading_enabled" in fields_set:
            runtime_save["mode"] = "paper" if target_paper else "live"
        cfg_raw["runtime"] = runtime_save
        live = _yaml_block(cfg_raw, "live")
        if "paper_mode" in fields_set or "live_trading_enabled" in fields_set:
            live["enabled"] = not target_paper
            if target_paper and not current_paper:
                # Leaving LIVE must produce a paper-clean config: doctor
                # --strict --paper rejects hard_dollar_cap>0, so the cap and
                # the approval path are cleared unless explicitly re-set.
                if "live_dollar_cap" not in fields_set:
                    live["hard_dollar_cap"] = 0.0
                if "live_operator_approval_path" not in fields_set:
                    live["operator_approval_path"] = ""
        if "live_dollar_cap" in fields_set:
            live["hard_dollar_cap"] = payload.live_dollar_cap
        if "live_operator_approval_path" in fields_set:
            live["operator_approval_path"] = payload.live_operator_approval_path
        strategy = _yaml_block(cfg_raw, "strategy")
        risk = cfg_raw["risk"]
        if "daily_loss_limit_pct" in fields_set:
            risk["daily_loss_limit_pct"] = payload.daily_loss_limit_pct / 100
        if "max_drawdown_pct" in fields_set:
            risk["max_drawdown_pct"] = payload.max_drawdown_pct / 100
        if "equity_trading_enabled" in fields_set:
            _yaml_block(strategy, "equity")["enabled"] = payload.equity_trading_enabled
        # App PATCH semantics
        app_patch: dict[str, Any] = {}
        if "host" in fields_set:
            app_patch["host"] = payload.host.strip() or "127.0.0.1"
        else:
            app_patch["host"] = old_app.get("host", "127.0.0.1")
        if "port" in fields_set:
            app_patch["port"] = payload.port
        else:
            app_patch["port"] = old_app.get("port", 8000)
        if "open_browser" in fields_set:
            app_patch["open_browser"] = payload.open_browser
        else:
            app_patch["open_browser"] = old_app.get("open_browser", True)
        if "log_level" in fields_set:
            app_patch["log_level"] = payload.log_level.upper()
        else:
            app_patch["log_level"] = old_app.get("log_level", "INFO")
        app_patch["auto_start_engine"] = old_app.get("auto_start_engine", True)
        cfg_raw["app"] = app_patch

        # Validate the complete temporary configuration before persistence.
        temporary_config = self.config_path.with_name(f".{self.config_path.name}.validate")
        atomic_write(
            temporary_config,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )
        try:
            load_config(str(temporary_config))
        finally:
            temporary_config.unlink(missing_ok=True)
        if entering_live and credentials_changed:
            self.invalidate_live_arming()
            raise ValueError("Credentials changed; request and type a fresh live confirmation first")
        if entering_live:
            token = self._live_arm_token
            candidate_digest = self._digest_config(cfg_raw, paper=False)
            if token is None or token.config_digest != candidate_digest:
                self.invalidate_live_arming()
                raise ValueError("Settings changed after live verification; confirm the live account again")
        return ValidatedSettings(
            cfg_raw=cfg_raw,
            old_app=old_app,
            api_key=api_key,
            secret_key=secret_key,
            credentials_changed=credentials_changed,
        )

    def start_engine(self, *, auto_start: bool = False) -> None:
        # One runner per Alpaca account: refuse before any state change so the
        # UI keeps reporting 'stopped' instead of a failed start.
        if not trading_host_authorized():
            raise ValueError(TRADING_HOST_REFUSAL)
        # A sticky operator halt refuses manual and automatic starts alike;
        # checked before any state change so the console keeps 'stopped'.
        halt_refusal = self._operator_halt_refusal()
        if halt_refusal is not None:
            log.error("%s", halt_refusal)
            raise ValueError(halt_refusal)
        # Mutual exclusion with the Web backtest runner: the engine must not
        # start while an expensive in-process backtest is using the process.
        # The backtest lock is taken FIRST on both paths (BacktestManager.start
        # re-reads the engine state under it), so the two guards cannot
        # interleave the way two independent checks could.
        guard = getattr(self, "_backtest_manager", None)
        if guard is None:
            raise ValueError("Engine/backtest exclusion is not configured; refusing to start the engine")
        # Lock order for both start paths is BacktestManager.lock ->
        # EngineManager._state_lock.  BacktestManager.start holds its lock
        # while reading this manager's state through the bound provider.
        with guard.lock:
            if guard.is_running:
                raise ValueError(
                    "A Web backtest is still running; wait for it to finish before starting the trading engine"
                )
            with self._state_lock:
                if self.runtime is not None or self.engine_state in _START_ENGINE_BLOCKING_STATES:
                    raise ValueError(f"Engine is already {self.engine_state}")
                # The auto-start retry loop re-enters while its own failed
                # attempt left the state at 'starting'. A manual start during
                # a pending retry is refused so two runtimes can never race.
                if self.engine_state == "starting" and not auto_start:
                    if self._retry_task is not None:
                        raise ValueError(
                            "Engine auto-start retry is in progress; wait for it to finish or stop the engine"
                        )
                    raise ValueError("Engine is already starting")
                self.engine_state = "starting"
                self.last_engine_error = None
                # Fresh cancel event per attempt: a stop request may only
                # cancel the startup that is in flight now.
                self._start_cancel.clear()

        key, secret = self.get_credentials()
        if not key or not secret:
            message = "Alpaca API key/secret missing. Configure settings first."
            with self._state_lock:
                self.engine_state = "error"
                self.last_engine_error = message
            _alert_start_failure(reason="credentials_missing", message=message, auto_start=auto_start)
            raise ValueError(message)

        runtime: Any = None
        interrupted = False
        try:
            from parayabuck.runtime import EngineRuntime, StartupCancelled, TransientStartupError
        except Exception as exc:
            # The runtime module must be importable for a start attempt; a
            # failure is permanent and must not leave the state at 'starting'.
            with self._state_lock:
                self._reset_retry_state()
                self.engine_state = "error"
                self.last_engine_error = str(exc)
            self.invalidate_live_arming()
            _alert_start_failure(
                reason="runtime_import_failed",
                message=f"Engine runtime is not importable: {exc}",
                auto_start=auto_start,
            )
            raise
        try:
            # Never memoized: a secret committed after import must still block
            # this start, so the gate rescans on every attempt.
            warning = _scan_tracked_secrets(Path.cwd())
            if warning:
                raise ValueError(warning)
            cfg = load_config(str(self.config_path))
            live_mode = cfg.runtime.mode.value == "live"
            if live_mode and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES before starting the application."
                )
            live_startup_proof: LiveStartupProof | None = None
            if live_mode:
                # B3: LIVE authenticates only with the LIVE key pair.
                live_key, live_secret = self.get_live_credentials()
                if not live_key or not live_secret:
                    raise ValueError(
                        f"Alpaca LIVE credentials ({LIVE_API_KEY_ENV} / {LIVE_SECRET_KEY_ENV}) are not configured"
                    )
                key, secret = live_key, live_secret
            if live_mode and auto_start:
                # Unattended (reboot/deploy) LIVE start: only a valid standing
                # LIVE authorization may stand in for the interactive arm.
                live_startup_proof = self._standing_startup_proof(key, secret)
            elif live_mode:
                # Keep the fresh interactive confirmation alive until the
                # neutral proof is constructed; EngineRuntime consumes and
                # independently verifies that proof before reconciliation.
                token = self._require_valid_live_arm(key, secret, consume=False)
                digests, digest_error = self._resolve_live_configuration_digests()
                if digests is None:
                    raise ValueError(f"Live configuration could not be verified: {digest_error}")
                live_startup_proof = LiveStartupProof(
                    account_id=token.account_id,
                    credential_fingerprint=token.credential_fingerprint,
                    configuration_digests=digests,
                    issued_at=token.issued_at,
                    expires_at=token.expires_at,
                )
                self.invalidate_live_arming()

            runtime = EngineRuntime(
                api_key=key,
                secret_key=secret,
                config=cfg,
                live_startup_proof=live_startup_proof,
                startup_cancel=self._start_cancel,
            )
            runtime.start()
            # The runtime's broker takes over account polling. Swap it in under
            # the account lock: a poll between a separate reset and this
            # assignment used to build a lazy AlpacaBroker that was then
            # overwritten without being closed (a leaked HTTP pool).
            replaced_broker = None
            with self._account_lock:
                with self._state_lock:
                    # A stop request issued while the engine was starting wins:
                    # shut the fresh runtime down instead of leaving it running
                    # after an explicit stop.
                    interrupted = self.engine_state == "stopping"
                    if not interrupted:
                        replaced_broker = self.broker
                        self.runtime = runtime
                        self.broker = runtime.broker
                        self.engine_state = "running"
                        self._reset_retry_state()
            if replaced_broker is not None and replaced_broker is not runtime.broker:
                try:
                    replaced_broker.close()
                except Exception:
                    log.exception("Closing the replaced account broker failed")
            if interrupted:
                raise RuntimeError("Engine start was interrupted by a stop request")
            if live_mode:
                self.invalidate_live_arming()
            log.info("ParayaBuck engine started from Web UI")

        except TransientStartupError as exc:
            # Network-class startup failure: never a permanent incident. The
            # auto-start loop retries with bounded backoff while the state
            # stays 'starting'; a manual start surfaces the message and the
            # operator retries explicitly.
            if runtime is not None:
                try:
                    runtime.stop()
                except Exception:
                    log.exception("Cleanup after transient engine start failure")
            failed = False
            attempts = 0
            with self._state_lock:
                if interrupted or self.engine_state == "stopping":
                    self.engine_state = "stopped"
                    self.last_engine_error = None
                else:
                    self.engine_state = "starting" if auto_start else "error"
                    self.last_engine_error = f"transient: {exc}"
                    # The retry loop records its counter after the attempt
                    # that failed, so the attempt number is the next one.
                    failed = True
                    attempts = self._retry_attempt + 1
            self.invalidate_live_arming()
            # A retrying auto-start keeps the state at 'starting' and the
            # failure is expected to clear; alert only once the retries are
            # visibly not clearing it. A manual transient failure ends in
            # 'error' with no retry behind it, so it always alerts.
            if failed and (not auto_start or _retry_alert_due(attempts)):
                _alert_start_failure(
                    reason="transient_retrying" if auto_start else "transient_no_retry",
                    message=f"Transient engine start failure: {exc}",
                    auto_start=auto_start,
                    attempts=attempts,
                )
            raise
        except StartupCancelled as exc:
            # An operator stop arrived while the startup sequence was still
            # running: it aborted before any order submission, so the engine
            # ends up stopped — the outcome the operator asked for.
            with self._state_lock:
                self._reset_retry_state()
                self.engine_state = "stopped"
                self.last_engine_error = None
            self.invalidate_live_arming()
            raise RuntimeError(str(exc)) from exc

        except Exception as exc:
            if runtime is not None:
                try:
                    runtime.stop()
                except Exception:
                    log.exception("Cleanup after failed engine start")
            failed = False
            with self._state_lock:
                self._reset_retry_state()
                if interrupted:
                    self.engine_state = "stopped"
                    self.last_engine_error = None
                else:
                    self.engine_state = "error"
                    self.last_engine_error = str(exc)
                    failed = True
                    # A failed runtime closes its ledger during cleanup. Never
                    # expose that stopped runtime (or its broker) to account/status
                    # polling, otherwise the UI repeatedly queries a closed SQLite
                    # connection and reports a misleading "stopped (degraded)".
                    if self.runtime is runtime:
                        self.runtime = None
                        self.broker = None
            self.invalidate_live_arming()
            # Every permanent failure is operator-actionable and terminal: a
            # rotated key rejected by Alpaca, a startup guard (secret scan,
            # live arming) blocking the start, a SQLite error in
            # begin_session, a catch-up exception. None of them can be seen
            # from the running process, so all of them alert.
            if failed:
                _alert_start_failure(
                    reason="startup_rejected" if isinstance(exc, ValueError) else "startup_error",
                    message=f"Engine start failed: {exc}",
                    auto_start=auto_start,
                )
            raise

    def stop_engine(self) -> None:
        with self._state_lock:
            # A start attempt still in flight must abort before its
            # resume/catch-up steps submit orders (see StartupCancelled).
            self._start_cancel.set()
            # A sleeping auto-start retry must not outlive an explicit stop:
            # cancel it (a start attempt already in flight is interrupted via
            # the 'stopping' state instead).
            self._cancel_pending_retry()
            if self.engine_state == "stopped" and self.runtime is None:
                return
            if self.runtime is None:
                # No runtime yet: either a start is still in flight (mark the
                # stop so start_engine aborts when its runtime is ready) or
                # the engine is already down.
                if self.engine_state == "starting":
                    self.engine_state = "stopping"
                return
            self.engine_state = "stopping"
            runtime = self.runtime
        # Account polling uses the runtime broker and ledger. Serialize it with
        # shutdown so no request can enter while those SQLite handles close.
        with self._account_lock:
            if runtime is not None:
                try:
                    stopped = runtime.stop()
                except Exception as exc:
                    with self._state_lock:
                        self.engine_state = "stop_failed"
                    self.invalidate_live_arming()
                    raise RuntimeError(f"Engine could not stop cleanly: {exc}") from exc
                if stopped is False:
                    with self._state_lock:
                        self.engine_state = "stop_failed"
                    self.invalidate_live_arming()
                    raise RuntimeError("Runtime workers did not stop; process restart required")
            with self._state_lock:
                self.engine_state = "stopped"
                self.last_engine_error = None
                self.runtime = None
                broker = self.broker
                self.broker = None
                self.last_account = None
        # runtime.stop() closed the runtime's own broker; a lazily created
        # account broker must be closed here or its HTTP pool leaks.
        if broker is not None and broker is not getattr(runtime, "broker", None):
            try:
                broker.close()
            except Exception:
                log.exception("Closing the account broker after engine stop failed")
        self.invalidate_live_arming()
        log.info("ParayaBuck engine stopped")

    def reset_broker(self) -> None:
        """Close and drop the account-snapshot broker without leaking its pool.

        The broker lazily created for /api/account polling holds a pooled HTTP
        session; dropping the reference without closing leaks one pool per
        reset (mode toggle, settings save, engine start). A runtime-owned
        broker is closed by EngineRuntime.stop() instead, so it is skipped.
        """
        with self._account_lock:
            broker = self.broker
            # A running runtime keeps serving account polling with its own
            # broker; dropping it would make the next poll open a second
            # AlpacaBroker (and HTTP pool) on the same key.
            runtime_broker = getattr(self.runtime, "broker", None)
            self.broker = runtime_broker
            if runtime_broker is None:
                # The cached snapshot belongs to the dropped broker identity:
                # serving it as fresh would report the old account's equity.
                self.last_account = None
                self._last_account_monotonic = 0.0
        if broker is not None and broker is not runtime_broker:
            broker.close()

    def _reset_retry_state(self) -> None:
        """Clear auto-start retry bookkeeping (caller holds the state lock)."""
        self._retry_attempt = 0
        self._retry_next_at = None
        self._retry_task = None
        self._retry_task_loop = None

    def _cancel_pending_retry(self) -> None:
        """Cancel a sleeping auto-start retry, if any; safe from any thread."""
        with self._state_lock:
            task = self._retry_task
            loop = self._retry_task_loop
            self._reset_retry_state()
            if task is not None and not task.done():
                if loop is not None and not loop.is_closed():
                    # Task.cancel() schedules via loop.call_soon, which is
                    # documented as not thread-safe; this runs on a worker
                    # thread whenever stop_engine is to_thread'ed.
                    loop.call_soon_threadsafe(task.cancel)
                else:
                    task.cancel()

    async def auto_start_engine(self) -> None:
        """Bounded autonomous startup recovery for the Web auto-start path.

        Runs one ``start_engine(auto_start=True)`` attempt per iteration and
        retries transient (network-class) failures with backoff
        [5, 15, 30, 60]s, capped at 60s, while ``engine_state`` stays
        'starting'. Permanent failures, an explicit stop, or app shutdown end
        the loop; every successful start resets the retry counters.
        """
        from parayabuck.runtime import TransientStartupError

        try:
            while True:
                try:
                    await asyncio.to_thread(self.start_engine, auto_start=True)
                    return
                except TransientStartupError as exc:
                    with self._state_lock:
                        if self.engine_state != "starting":
                            return
                        attempt = self._retry_attempt + 1
                        delay = _AUTO_START_RETRY_BACKOFF[min(attempt, len(_AUTO_START_RETRY_BACKOFF)) - 1]
                        self._retry_attempt = attempt
                        self._retry_next_at = time.monotonic() + delay
                        self._retry_task = asyncio.current_task()
                        self._retry_task_loop = asyncio.get_running_loop()
                        self.last_engine_error = f"transient: {exc}; retry in {delay}s"
                    log.warning(
                        "Automatic engine start attempt %d failed transiently (%s); retrying in %ds",
                        attempt,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    with self._state_lock:
                        self._retry_task = None
                        self._retry_next_at = None
        except asyncio.CancelledError:
            with self._state_lock:
                self._reset_retry_state()
                if self.runtime is None and self.engine_state in {"starting", "stopping"}:
                    self.engine_state = "stopped"
                    self.last_engine_error = None
            raise

    def reset_safety_lock(self) -> None:
        with self._state_lock:
            runtime = self.runtime
            state = self.current_engine_state()
        if runtime is None or state not in {"halted", "degraded"}:
            raise ValueError("A halted runtime is required to reset the safety lock")
        runtime.reset_safety_lock()

    def start_new_risk_epoch(self) -> dict[str, object]:
        """Operator-verified drawdown-baseline reset (explicit recovery for
        the portfolio_drawdown_halt operational incident)."""
        with self._state_lock:
            runtime = self.runtime
        if runtime is None:
            raise ValueError("The trading engine is not running; start it before resetting the risk epoch")
        baseline = runtime.start_new_risk_epoch()
        return {"baseline_equity": baseline}

    def fetch_account_snapshot(self) -> dict[str, Any]:
        with self._account_lock:
            now = time.monotonic()
            if self.last_account is not None and now - self._last_account_monotonic < self._account_cache_ttl_seconds:
                return {
                    **self.last_account,
                    "age_seconds": max(0.0, now - self._last_account_monotonic),
                    "stale": False,
                    "source": "cache",
                }
            return self._refresh_account_snapshot()

    def _refresh_account_snapshot(self) -> dict[str, Any]:
        if self.broker is None:
            key, secret = self.get_credentials()
            if key and secret:
                try:
                    from parayabuck.execution.alpaca import AlpacaBroker

                    self.broker = AlpacaBroker(key, secret, paper=self.display_broker_paper())
                except Exception as e:
                    log.warning("Could not connect broker: %s", e)

        if self.broker is not None:
            try:
                acct = self.broker.account()
                positions_list = []
                for p in acct.positions:
                    if not p.is_flat:
                        positions_list.append(
                            {
                                "symbol": p.symbol,
                                "market": p.market.value,
                                "direction": "LONG" if p.qty > 0 else "SHORT",
                                "total_qty": abs(p.qty),
                                "qty": abs(p.qty),
                                "managed_qty": p.managed_qty,
                                "external_qty": p.external_qty,
                                "ownership_state": p.ownership,
                                "avg_price": p.avg_price,
                                "current_price": p.current_price,
                                "market_value": p.market_value,
                                "unrealized_pnl": p.unrealized_pnl,
                            }
                        )

                snapshot = {
                    "equity": acct.equity,
                    "cash": acct.cash,
                    "daily_pnl": acct.daily_pnl,
                    "realized_pnl": acct.realized_pnl,
                    "unrealized_pnl": acct.unrealized_pnl,
                    "fees": acct.fees,
                    "drawdown": max(0.0, acct.peak_equity - acct.equity),
                    "gross_exposure": sum(position.market_value for position in acct.positions if not position.is_flat),
                    "positions": positions_list,
                    "account_suffix": getattr(self.broker, "account_suffix", ""),
                    "snapshot_at": datetime.now(UTC).isoformat(),
                    "age_seconds": 0.0,
                    "stale": False,
                    "source": "broker",
                }
                self.last_account = snapshot
                self._last_account_monotonic = time.monotonic()
                return snapshot
            except Exception as e:
                log.warning("Error fetching account snapshot: %s", e)

        if self.last_account:
            age = max(0.0, time.monotonic() - self._last_account_monotonic)
            return {
                **self.last_account,
                "age_seconds": age,
                "stale": True,
                "source": "cache",
            }

        return {
            "equity": 0.0,
            "cash": 0.0,
            "daily_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
            "positions": [],
            "account_suffix": "",
            "snapshot_at": None,
            "age_seconds": None,
            "stale": True,
            "source": "unavailable",
        }

    def request_live_arming(self) -> str:
        if self.has_active_runtime():
            raise ValueError("Stop the engine before requesting live mode")
        if os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
            raise ValueError("PARAYABUCK_ENABLE_LIVE=YES is required for live mode")
        key, secret = self.get_live_credentials()
        if not key or not secret:
            raise ValueError(
                f"Alpaca LIVE credentials ({LIVE_API_KEY_ENV} / {LIVE_SECRET_KEY_ENV}) are required "
                "before live verification"
            )
        from parayabuck.execution.alpaca import AlpacaBroker
        from parayabuck.live_safety import LiveApprovalError, verify_account_environment

        live_broker = AlpacaBroker(key, secret, paper=False)
        try:
            live_broker.account()
            suffix = live_broker.account_suffix
            if not suffix:
                raise ValueError("Alpaca account identity could not be verified")
            try:
                verify_account_environment(
                    live=True,
                    account_number=str(getattr(live_broker, "account_number", "") or ""),
                    api_key=key,
                    expected_account_id=load_config(str(self.config_path)).broker.expected_account_id,
                )
            except LiveApprovalError as exc:
                raise ValueError(str(exc)) from exc
            now = datetime.now(UTC)
            self._pending_live_arm = LiveArmToken(
                account_id=live_broker.account_id,
                account_suffix=suffix,
                credential_fingerprint=self._credential_fingerprint(key, secret),
                config_digest=self._config_digest(paper=False),
                issued_at=now,
                expires_at=now + timedelta(minutes=2),
            )
            self._live_armed = False
            self._live_arm_token = None
            return suffix
        finally:
            # Temporary live-credential verification sessions must never leak:
            # close the broker's HTTP session pool before returning.
            live_broker.close()

    def confirm_live_arming(self, phrase: str) -> None:
        pending = self._pending_live_arm
        key, secret = self.get_live_credentials()
        if (
            pending is None
            or key is None
            or secret is None
            or datetime.now(UTC) >= pending.expires_at
            or pending.credential_fingerprint != self._credential_fingerprint(key, secret)
            or pending.config_digest != self._config_digest(paper=False)
            or not secrets.compare_digest(phrase.strip(), f"LIVE {pending.account_suffix}")
        ):
            self.invalidate_live_arming()
            raise ValueError("Live confirmation phrase does not match the verified account")
        now = datetime.now(UTC)
        self._live_arm_token = replace(
            pending,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._pending_live_arm = None
        self._live_armed = True

    def invalidate_live_arming(self) -> None:
        self._live_armed = False
        self._pending_live_arm = None
        self._live_arm_token = None

    def live_prerequisites(self) -> dict[str, Any]:
        """Operator-facing live-arm readiness checklist computed WITHOUT secrets.

        Mirrors the exact gates enforced by EngineRuntime.start() and the web
        arming flow: config live.enabled, runtime.mode=live,
        live.hard_dollar_cap>0, PARAYABUCK_ENABLE_LIVE=YES, the operator
        approval file binding (LiveOperatorApproval: HMAC signature, account
        match, configuration-digest match, expiry), the in-process
        LiveArmToken phrase, credentials, and an idle engine. Never returns
        credentials, the approval HMAC key, or approval-file contents.
        """
        items: list[dict[str, Any]] = []
        raw, config_error = self._load_raw_config_for_prerequisites()
        live_cfg = raw.get("live", {}) if isinstance(raw.get("live", {}), dict) else {}
        runtime_cfg = raw.get("runtime", {}) if isinstance(raw.get("runtime", {}), dict) else {}

        def item(
            item_id: str,
            label: str,
            satisfied: bool,
            missing: str | None = None,
            state: str | None = None,
        ) -> None:
            items.append(
                {
                    "id": item_id,
                    "label": label,
                    "satisfied": satisfied,
                    "missing": missing,
                    "state": state or ("ok" if satisfied else "missing"),
                }
            )

        self._append_live_config_checks(item, live_cfg, runtime_cfg)
        approval, approval_error, hmac_key, approval_path = self._check_approval_signature(item, live_cfg)
        key, secret = self.get_live_credentials()
        # The probe authenticates against the LIVE endpoint, so PAPER keys
        # must never reach it while the live gates are closed (settings-drawer
        # refreshes would otherwise send them on every open).
        if bool(live_cfg.get("enabled", False)) and os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES":
            account_id, account_probe_error = self._probe_live_account(key, secret)
        else:
            account_id, account_probe_error = None, _LIVE_PROBE_SKIPPED_REASON
        expected_digests, digest_error = self._resolve_live_configuration_digests()
        self._append_live_binding_check(item, approval, account_id, account_probe_error, expected_digests, digest_error)
        self._append_live_session_checks(item, key, secret)
        return {
            "all_met": all(entry["satisfied"] for entry in items),
            "items": items,
            "config_error": config_error,
        }

    def _load_raw_config_for_prerequisites(
        self,
    ) -> tuple[dict[str, Any], str | None]:
        try:
            if self.config_path.exists():
                raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
                return raw, None
        except (OSError, yaml.YAMLError) as exc:
            return {}, str(exc)
        return {}, None

    def _append_live_config_checks(
        self,
        item: Callable[..., None],
        live_cfg: dict[str, Any],
        runtime_cfg: dict[str, Any],
    ) -> None:
        live_enabled = bool(live_cfg.get("enabled", False))
        item(
            "config_live_enabled",
            "Config: live.enabled=true",
            live_enabled,
            "live.enabled is false in settings.yaml" if not live_enabled else None,
        )
        mode_live = str(runtime_cfg.get("mode", "paper")).lower() == "live"
        item(
            "runtime_mode_live",
            "Config: runtime.mode=live",
            mode_live,
            "runtime.mode is not 'live' in settings.yaml" if not mode_live else None,
        )
        try:
            cap = float(live_cfg.get("hard_dollar_cap", 0) or 0)
        except (TypeError, ValueError):
            cap = 0.0
        cap_ok = cap > 0
        item(
            "live_cap",
            "Config: live.hard_dollar_cap > 0",
            cap_ok,
            f"live.hard_dollar_cap must be greater than 0 (currently {cap:g})" if not cap_ok else None,
        )
        env_ok = os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES"
        item(
            "enable_live_env",
            "PARAYABUCK_ENABLE_LIVE=YES",
            env_ok,
            "PARAYABUCK_ENABLE_LIVE=YES is not set; restart the application with it" if not env_ok else None,
        )

    def _check_approval_signature(
        self,
        item: Callable[..., None],
        live_cfg: dict[str, Any],
    ) -> tuple[LiveOperatorApproval | None, str | None, str, str]:
        approval_path = str(live_cfg.get("operator_approval_path") or "")
        key_env_name = str(live_cfg.get("operator_approval_key_env") or "PARAYABUCK_OPERATOR_APPROVAL_KEY")
        hmac_key = os.getenv(key_env_name, "")
        file_ok = bool(approval_path) and Path(approval_path).exists()
        item(
            "approval_file",
            "Operator approval file present",
            file_ok,
            (
                "live.operator_approval_path is not configured"
                if not approval_path
                else f"operator approval file not found: {approval_path}"
            )
            if not file_ok
            else None,
        )
        key_ok = len(hmac_key) >= 32
        item(
            "approval_key",
            "Operator approval HMAC key configured",
            key_ok,
            f"approval key env {key_env_name} is missing or shorter than 32 characters" if not key_ok else None,
        )
        approval: LiveOperatorApproval | None = None
        approval_error: str | None = None
        if file_ok and key_ok:
            try:
                parsed = json.loads(Path(approval_path).read_text(encoding="utf-8"))
                approval = LiveOperatorApproval(
                    operator_id=str(parsed.get("operator_id", "")),
                    account_id=str(parsed.get("account_id", "")),
                    configuration_digests=tuple(str(entry) for entry in parsed.get("configuration_digests", ())),
                    maximum_dollar_exposure=float(parsed.get("maximum_dollar_exposure", 0)),
                    approved_at=datetime.fromisoformat(str(parsed.get("approved_at", ""))),
                    expires_at=datetime.fromisoformat(str(parsed.get("expires_at", ""))),
                    nonce=str(parsed.get("nonce", "")),
                    signature=str(parsed.get("signature", "")),
                )
                expected = approval.calculated_signature(hmac_key)
                current = datetime.now(UTC)
                if not hmac.compare_digest(approval.signature, expected):
                    approval_error = "approval file signature does not match the operator approval key"
                elif approval.approved_at > current:
                    approval_error = "approval file is not yet valid"
                elif approval.expires_at <= current:
                    approval_error = "approval file has expired"
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                approval_error = f"approval file is unreadable or invalid: {exc}"
        signature_ok = approval is not None and approval_error is None
        if not signature_ok and approval_error is None:
            approval_error = "approval file or operator approval HMAC key is missing (see checks above)"
        item(
            "approval_signature",
            "Approval signature and expiry valid",
            signature_ok,
            approval_error if not signature_ok else None,
        )
        return approval, approval_error, hmac_key, approval_path

    def _probe_live_account(self, key: str | None, secret: str | None) -> tuple[str | None, str | None]:
        if not key or not secret:
            return None, None
        try:
            from parayabuck.execution.alpaca import AlpacaBroker

            live_broker = AlpacaBroker(key, secret, paper=False)
            try:
                live_broker.account()
                return live_broker.account_id, None
            finally:
                live_broker.close()
        except Exception as exc:
            log.warning("Live account probe failed during preflight: %s", exc)
            return None, str(exc)

    def _resolve_live_configuration_digests(
        self,
    ) -> tuple[tuple[str, ...] | None, str | None]:
        try:
            cfg = load_config(str(self.config_path))
            from parayabuck.execution.portfolio import (
                portfolio_execution_policy_from_config,
                portfolio_risk_limits_from_config,
            )
            from parayabuck.portfolio import product_strategy_from
            from parayabuck.portfolio.runtime_service import configuration_digest
            from parayabuck.universe import equity_universe_digest

            equity_universe = cfg.universe.equity
            digests: list[str] = []
            for selection, symbols, universe_version in (
                (
                    cfg.strategy.equity,
                    equity_universe.symbols if equity_universe.enabled else (),
                    equity_universe_digest(equity_universe),
                ),
            ):
                if not selection.enabled or not symbols:
                    continue
                strategy = product_strategy_from(
                    selection.name,
                    selection.parameters,
                    timeframe=selection.timeframe,
                )
                digests.append(
                    configuration_digest(
                        strategy,
                        "live",
                        selection.maximum_allocation,
                        universe_version,
                        portfolio_risk_limits_from_config(cfg.risk),
                        portfolio_execution_policy_from_config(cfg.execution),
                    )
                )
            return tuple(digests), None
        except Exception as exc:
            return None, str(exc)

    def _append_live_binding_check(
        self,
        item: Callable[..., None],
        approval: LiveOperatorApproval | None,
        account_id: str | None,
        account_probe_error: str | None,
        expected_digests: tuple[str, ...] | None,
        digest_error: str | None,
    ) -> None:
        binding_state = "ok"
        binding_missing: str | None = None
        if approval is None:
            binding_state = "missing"
            binding_missing = "approval file is not loadable (see approval checks above)"
        elif account_id is None:
            binding_state = "unverifiable"
            binding_missing = (
                f"cannot verify the live account binding: {account_probe_error}"
                if account_probe_error is not None
                else "cannot verify the live account binding without reachable live credentials"
            )
        elif expected_digests is None:
            binding_state = "unverifiable"
            binding_missing = f"cannot verify the configuration binding: {digest_error}"
        elif approval.account_id != account_id:
            binding_state = "missing"
            binding_missing = "approval file was issued for a different Alpaca account"
        elif set(approval.configuration_digests) != set(expected_digests):
            binding_state = "missing"
            binding_missing = "approval file configuration digests do not match the current settings"
        item(
            "approval_binding",
            "Approval binds this account and configuration",
            binding_state == "ok",
            binding_missing,
            binding_state,
        )

    def _append_live_session_checks(
        self,
        item: Callable[..., None],
        key: str | None,
        secret: str | None,
    ) -> None:
        armed = bool(self._live_armed and self._live_arm_token is not None)
        item(
            "web_arm_token",
            "Live confirmation phrase verified in this session",
            armed,
            ("complete the LIVE <account-suffix> phrase confirmation (mode chip) before starting")
            if not armed
            else None,
        )
        creds_ok = bool(key and secret)
        item(
            "credentials",
            "Alpaca LIVE credentials configured",
            creds_ok,
            f"{LIVE_API_KEY_ENV} and {LIVE_SECRET_KEY_ENV} are required in the host .env" if not creds_ok else None,
        )
        engine_idle = not self.has_active_runtime()
        item(
            "engine_stopped",
            "Trading engine is stopped",
            engine_idle,
            "stop the engine before arming live mode" if not engine_idle else None,
        )

    def _require_valid_live_arm(self, key: str, secret: str, *, consume: bool = False) -> LiveArmToken:
        token = self._live_arm_token
        valid = (
            token is not None
            and self._live_armed
            and datetime.now(UTC) < token.expires_at
            and token.credential_fingerprint == self._credential_fingerprint(key, secret)
            and token.config_digest == self._config_digest(paper=False)
        )
        if not valid:
            self.invalidate_live_arming()
            raise ValueError("Live trading requires a fresh confirmation for these credentials and settings.")
        assert token is not None
        if consume:
            self.invalidate_live_arming()
        return token

    @staticmethod
    def _credential_fingerprint(key: str, secret: str) -> str:
        return hashlib.sha256(f"{key}\0{secret}".encode()).hexdigest()

    def _config_digest(self, *, paper: bool) -> str:
        raw: dict[str, Any] = {}
        if self.config_path.exists():
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return self._digest_config(raw, paper=paper)

    @staticmethod
    def _digest_config(raw: dict[str, Any], *, paper: bool) -> str:
        raw = json.loads(json.dumps(raw))
        _yaml_block(raw, "runtime")["mode"] = "paper" if paper else "live"
        # Only the mode flip itself survives the arm: the arm→/api/mode
        # toggle and the settings-drawer live entry flip runtime.mode and
        # live.enabled after the arm is verified, so those two stay out of
        # the digest. The dollar cap and the approval path are live safety
        # gates: changing either after arming must invalidate the arm.
        live = _yaml_block(raw, "live")
        live.pop("enabled", None)
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def issue_ws_ticket(self, kind: str) -> str:
        store: Any = self._ticket_store
        if store is None:
            # Direct legacy calls retain the process-wide compatibility store;
            # composed FastAPI apps always bind their own store in WebState.
            from parayabuck.web.managers.websocket import ticket_store as legacy_store

            store = legacy_store
        return str(store.issue(kind))

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
        store: Any = self._ticket_store
        if store is None:
            from parayabuck.web.managers.websocket import ticket_store as legacy_store

            store = legacy_store
        return bool(store.consume(ticket, kind))


_PLACEHOLDERS = {
    "",
    "your_secret_key_here",
    "your-secret-key",
    "replace_me",
    "changeme",
    "<secret>",
}


_SECRET_WARNING_CACHE: dict[Path, str | None] = {}


def _tracked_secret_warning(root: Path) -> str | None:
    """Constructor-time warning, scanned once per resolved root.

    Tests build many EngineManagers; the engine-start gate calls
    _scan_tracked_secrets directly so it never sees this memo.
    """
    resolved = root.resolve()
    if resolved not in _SECRET_WARNING_CACHE:
        _SECRET_WARNING_CACHE[resolved] = _scan_tracked_secrets(resolved)
    return _SECRET_WARNING_CACHE[resolved]


def _scan_tracked_secrets(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # The secret scan cannot prove anything when git is unavailable; the
        # failure must be loud so the operator knows the block was skipped.
        log.critical(
            "SECURITY CHECK FAILED: cannot list tracked files for secret scan (%s); the secret-key guard is inactive",
            exc,
        )
        return None
    for relative in result.stdout.decode(errors="ignore").split("\0"):
        if not relative:
            continue
        path = root / relative
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.finditer(
            r"(?m)^\s*(?:ALPACA_SECRET_KEY|ALPACA_LIVE_SECRET_KEY|PARAYABUCK_SMTP_PASSWORD)\s*=\s*([^\s#]+)", text
        ):
            value = match.group(1).strip().strip("'\"").lower()
            if value not in _PLACEHOLDERS and not any(
                marker in value for marker in ("your_", "example", "placeholder")
            ):
                return (
                    f"SECURITY BLOCK: tracked file {relative} contains a non-placeholder "
                    "secret (ALPACA_SECRET_KEY, ALPACA_LIVE_SECRET_KEY or PARAYABUCK_SMTP_PASSWORD). "
                    "Rotate it and remove it from Git history."
                )
    return None


def _quote_dotenv_value(value: str) -> str:
    quote = '"'
    escaped = value.replace(quote, "\\" + quote)
    return f'"{escaped}"'


def _atomic_update_credentials(path: Path, api_key: str, secret_key: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed = {"ALPACA_API_KEY": api_key, "ALPACA_SECRET_KEY": secret_key}
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in managed:
            name = match.group(1)
            if name not in seen:
                # Quote values so characters like '#' or '=' inside a key or
                # secret cannot corrupt the dotenv file on the next load.
                lines.append(f"{name}={_quote_dotenv_value(managed[name])}")
                seen.add(name)
        else:
            lines.append(line)
    for name, value in managed.items():
        if name not in seen:
            lines.append(f"{name}={_quote_dotenv_value(value)}")
    atomic_write(path, "\n".join(lines).rstrip() + "\n", 0o600)
