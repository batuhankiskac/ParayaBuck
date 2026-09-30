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
from dotenv import load_dotenv

from parayabuck.config import load_config
from parayabuck.live_safety import LiveOperatorApproval, LiveStartupProof
from parayabuck.logger import get_logger
from parayabuck.trading_host import TRADING_HOST_REFUSAL, trading_host_authorized
from parayabuck.utils.io import atomic_write
from parayabuck.web.schemas.settings import SettingsPayload, ValidatedSettings

log = get_logger("parayabuck.web.engine")

# Compatibility export only.  Mutual exclusion is owned by each composed
# EngineManager/WebState pair, never by this module-level name.
backtest_manager: Any | None = None

_AUTO_START_RETRY_BACKOFF: tuple[int, ...] = (5, 15, 30, 60)
_ACTIVE_ENGINE_STATES: frozenset[str] = frozenset(
    {"starting", "running", "warming_up", "degraded", "halted", "stopping", "stop_failed"}
)
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
        # Share bounded log queues with websocket manager so the single
        # WebSocketLogHandler fans out to queues created via either path.
        try:
            from parayabuck.web.managers.websocket import log_queues as _shared_q
            from parayabuck.web.managers.websocket import log_queues_lock as _shared_lock

            self.log_queues = _shared_q
            self._log_queues_lock = _shared_lock
        except Exception:
            self.log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = set()  # type: ignore[no-redef]
            self._log_queues_lock = threading.Lock()
        self._state_lock = threading.RLock()
        if backtest_manager is None:
            try:
                from parayabuck.web.managers.backtest import BacktestManager

                backtest_manager = BacktestManager(engine_state_provider=self.current_engine_state)
            except Exception:
                backtest_manager = None
        self._backtest_manager = backtest_manager
        self.control_token = secrets.token_urlsafe(32)
        self._live_request_suffix: str | None = None
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

    def is_paper_mode(self) -> bool:
        try:
            if self.config_path.exists():
                modified_ns = self.config_path.stat().st_mtime_ns
                cached = self._paper_mode_cache
                if cached is not None and cached[0] == modified_ns:
                    return cached[1]
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
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
        except (OSError, yaml.YAMLError) as exc:
            # A config we cannot read must not silently present live mode as
            # paper: surface it so the operator notices the corruption.
            log.error("Could not read paper-mode flag from %s: %s", self.config_path, exc)
        return False

    def get_credentials(self) -> tuple[str | None, str | None]:
        load_dotenv(self.env_path, override=True)
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        return key, secret

    def should_auto_start(self) -> bool:
        """Auto-start configured paper engines without weakening live safeguards."""
        if not trading_host_authorized():
            log.warning("%s", TRADING_HOST_REFUSAL)
            return False
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            app_config = (raw or {}).get("app") or {}
            runtime_config = (raw or {}).get("runtime") or {}
            legacy_live = "mode" not in runtime_config and ((raw or {}).get("alpaca") or {}).get("paper") is False
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
            current_key, current_secret = self.get_credentials()
            if not current_key or not current_secret:
                raise ValueError("Alpaca credentials are required")
            self._require_valid_live_arm(
                current_key,
                current_secret,
                consume=False,
            )
        current_key, current_secret = self.get_credentials()
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
        # Normalize the pre-v1 risk_monitor_timeframe rename on save so the
        # operator UI heals configs that still carry the old key.
        runtime_save = dict(cfg_raw["runtime"])
        if "risk_monitor_timeframe" in runtime_save:
            runtime_save.setdefault("market_state_timeframe", runtime_save.pop("risk_monitor_timeframe"))
        # PATCH semantics for runtime
        if "paper_mode" in fields_set or "live_trading_enabled" in fields_set:
            runtime_save["mode"] = "paper" if target_paper else "live"
        if "execution_timeframe" in fields_set:
            runtime_save["execution_timeframe"] = payload.execution_timeframe
        cfg_raw["runtime"] = runtime_save
        live = _yaml_block(cfg_raw, "live")
        if "paper_mode" in fields_set or "live_trading_enabled" in fields_set:
            live["enabled"] = not target_paper
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
            with self._state_lock:
                self.engine_state = "error"
                self.last_engine_error = "Alpaca API key/secret missing. Configure settings first."
            raise ValueError("Alpaca API key/secret missing. Configure settings first.")

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
            # Close the lazy /api/account broker (if any) instead of leaking
            # its pooled HTTP session; the runtime's broker takes over
            # account polling from here.
            self.reset_broker()
            with self._state_lock:
                # A stop request issued while the engine was starting wins:
                # shut the fresh runtime down instead of leaving it running
                # after an explicit stop.
                interrupted = self.engine_state == "stopping"
                if not interrupted:
                    self.runtime = runtime
                    self.broker = runtime.broker
                    self.engine_state = "running"
                    self._reset_retry_state()
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
            with self._state_lock:
                if interrupted or self.engine_state == "stopping":
                    self.engine_state = "stopped"
                    self.last_engine_error = None
                else:
                    self.engine_state = "starting" if auto_start else "error"
                    self.last_engine_error = f"transient: {exc}"
            self.invalidate_live_arming()
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
            with self._state_lock:
                self._reset_retry_state()
                if interrupted:
                    self.engine_state = "stopped"
                    self.last_engine_error = None
                else:
                    self.engine_state = "error"
                    self.last_engine_error = str(exc)
                    # A failed runtime closes its ledger during cleanup. Never
                    # expose that stopped runtime (or its broker) to account/status
                    # polling, otherwise the UI repeatedly queries a closed SQLite
                    # connection and reports a misleading "stopped (degraded)".
                    if self.runtime is runtime:
                        self.runtime = None
                        self.broker = None
            self.invalidate_live_arming()
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

                    paper = self.is_paper_mode()
                    self.broker = AlpacaBroker(key, secret, paper=paper)
                except Exception as e:
                    log.warning("Could not connect broker: %s", e)

        if self.broker is not None:
            try:
                acct = self.broker.account()
                managed: dict[str, Any] = {}
                runtime = self.runtime
                runtime_ledger = getattr(runtime, "ledger", None)
                if runtime_ledger is not None and bool(self.broker.account_id):
                    managed = runtime_ledger.managed_for_account(self.broker.account_id)
                positions_list = []
                for p in acct.positions:
                    if not p.is_flat:
                        owned = managed.get(p.symbol)
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
                                "ownership": p.ownership,
                                "protection_state": (owned.protection_state.value if owned is not None else None),
                                "stop_price": (
                                    owned.current_stop or owned.original_stop if owned is not None else None
                                ),
                                "target_price": owned.original_target if owned is not None else None,
                                "stop_order_state": owned.stop_state if owned is not None else None,
                                "target_exit_state": (owned.target_state if owned is not None else None),
                                "manual_intervention_required": bool(
                                    owned is not None and owned.status == "manual_intervention_required"
                                ),
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
        key, secret = self.get_credentials()
        if not key or not secret:
            raise ValueError("Alpaca credentials are required before live verification")
        from parayabuck.execution.alpaca import AlpacaBroker

        live_broker = AlpacaBroker(key, secret, paper=False)
        try:
            live_broker.account()
            suffix = live_broker.account_suffix
            if not suffix:
                raise ValueError("Alpaca account identity could not be verified")
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
        key, secret = self.get_credentials()
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
        key, secret = self.get_credentials()
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
            from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
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
                        PortfolioExecutionPolicy(
                            rebalance_band_pct=cfg.execution.rebalance_band_pct,
                            minimum_order_notional=cfg.execution.minimum_order_notional,
                        ),
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
            "Alpaca credentials configured",
            creds_ok,
            "Alpaca API key and secret are required (settings)" if not creds_ok else None,
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

    Four EngineManagers are built at import; the engine-start gate calls
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
        for match in re.finditer(r"(?m)^\s*ALPACA_SECRET_KEY\s*=\s*([^\s#]+)", text):
            value = match.group(1).strip().strip("'\"").lower()
            if value not in _PLACEHOLDERS and not any(
                marker in value for marker in ("your_", "example", "placeholder")
            ):
                return (
                    f"SECURITY BLOCK: tracked file {relative} contains a non-placeholder "
                    "ALPACA_SECRET_KEY. Rotate the key and remove it from Git history."
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


# Singleton for legacy import path `from parayabuck.web.managers.engine import manager`
# Used as fallback in routers/websocket.py when WebState not yet bound.
manager = EngineManager()
