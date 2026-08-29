from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from parayabuck.config import DEFAULT_DECISION_DB_PATH, load_config
from parayabuck.core.models import Market
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.universe import validate_active_crypto_universe, validate_active_universe
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.web.server")

# Bounded autonomous startup recovery: retry delays (seconds) for transient
# network failures on the Web auto-start path. Later attempts stay capped at
# the final value, so the loop never busy-spins or floods the logs.
_AUTO_START_RETRY_BACKOFF: tuple[int, ...] = (5, 15, 30, 60)

# Engine states in which the runtime owns workers/resources. Single source of
# truth for every guard that must treat the engine as "active" (backtest
# exclusion, has_active_runtime) — adding a state here updates all of them
# together.
_ACTIVE_ENGINE_STATES: frozenset[str] = frozenset(
    {"starting", "running", "warming_up", "degraded", "halted", "stopping", "stop_failed"}
)
# start_engine's own guard excludes "starting": the auto-start retry loop
# legitimately re-enters start_engine from that state after a transient
# failure (its dedicated retry-pending guard below handles manual starts).
_START_ENGINE_BLOCKING_STATES: frozenset[str] = _ACTIVE_ENGINE_STATES - {"starting"}


class BacktestManager:
    """Runs the production-strategy backtest CLI in a background thread."""

    def __init__(self) -> None:
        # Public + reentrant: EngineManager.start_engine holds it across its
        # own guard so the engine↔backtest exclusion is atomic (no TOCTOU),
        # while is_running can still be called under it.
        self.lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_factory = threading.Thread
        self.state = "idle"
        self.run_id = ""
        self.output_dir = ""
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.progress: list[str] = []
        self.summary: dict[str, Any] | None = None
        self.universe: dict[str, Any] | None = None

    @property
    def is_running(self) -> bool:
        with self.lock:
            return self.state == "running" and self._thread is not None and self._thread.is_alive()

    def start(self, payload: dict[str, Any]) -> str:
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Bir backtest zaten çalışıyor; bitmesini bekleyin")
            # Mutual exclusion with the trading runtime: a large in-process
            # backtest must never starve live/PAPER workers. The engine state
            # is read at call time so tests can monkeypatch the singleton.
            engine_state = manager.current_engine_state()
            if engine_state in _ACTIVE_ENGINE_STATES:
                raise ValueError(
                    "A Web backtest cannot start while the trading engine is "
                    f"active ({engine_state}); stop the engine first"
                )
            self.state = "running"
            self.run_id = f"web-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            self.output_dir = str(Path("reports") / self.run_id)
            self.started_at = datetime.now(UTC).isoformat()
            self.finished_at = None
            self.error = None
            self.summary = None
            self.universe = None
            self.progress = []
            argv = self._build_argv(payload, self.output_dir)
            self._thread = self._thread_factory(target=self._run, args=(argv,), daemon=True, name="web-backtest")
            self._thread.start()
            return self.run_id

    @staticmethod
    def _build_argv(payload: dict[str, Any], output_dir: str) -> list[str]:
        argv = ["backtest", "--output-dir", output_dir]
        market = payload.get("market") or "equity"
        argv += ["--market", market]
        symbols = (payload.get("symbols") or "").strip()
        if symbols:
            argv += ["--symbols", symbols]
        end = datetime.now(UTC)
        days = int(payload.get("days") or 0)
        start = end - timedelta(days=days or int(payload.get("years") or 5) * 365)
        argv += ["--start", start.date().isoformat(), "--end", end.date().isoformat()]
        max_symbols = payload.get("max_symbols") or 0
        if max_symbols > 0:
            argv += ["--candidate-limit", str(int(max_symbols))]
        strategy = (payload.get("strategy") or "").strip()
        if strategy:
            argv += ["--strategy", strategy]
        for name, value in sorted(dict(payload.get("parameters") or {}).items()):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
                raise ValueError(f"Invalid parameter name: {name!r}")
            # Cap serialized value size to prevent argv blowup
            serialized = json.dumps(value)
            if len(serialized) > 4096:
                raise ValueError(f"Parameter value too large for {name!r}")
            argv += ["--param", f"{name}={serialized}"]
        return argv

    def _run(self, argv: list[str]) -> None:
        import contextlib

        from parayabuck import cli as product_cli

        error: str | None = None
        try:
            with (
                contextlib.redirect_stdout(self._stream()),
                contextlib.redirect_stderr(self._stream()),
            ):
                product_cli.main(argv)
        except SystemExit as exc:
            # The CLI raises SystemExit(reason) for user-facing failures;
            # losing the reason hides the actual error behind a generic
            # message in the UI.
            error = str(exc) or "Backtest CLI beklenmedik şekilde çıktı"
        except Exception as exc:
            error = str(exc)
            log.exception("Backtest run failed")
        self._finish(error)

    def _stream(self) -> Any:
        import io

        class ProgressStream(io.StringIO):
            def __init__(self, owner: BacktestManager) -> None:
                super().__init__()
                self.owner = owner

            def write(self, text: str) -> int:
                with self.owner.lock:
                    self.owner.progress.extend(text.splitlines())
                    if len(self.owner.progress) > 300:
                        self.owner.progress = self.owner.progress[-300:]
                return super().write(text)

        return ProgressStream(self)

    def _finish(self, error: str | None) -> None:
        summary: dict[str, Any] | None = None
        universe: dict[str, Any] | None = None
        if error is None:
            reports = sorted(Path(self.output_dir).glob("*/report.json"))
            report_path = reports[-1] if reports else Path(self.output_dir) / "report.json"
            universe_path = report_path.with_name("universe.json")
            try:
                if not report_path.exists():
                    error = f"Rapor üretilmedi: {report_path} (CLI hatasız çıktı ama rapor dosyası yazılmadı)"
                else:
                    raw = json.loads(report_path.read_text(encoding="utf-8"))
                    summary = raw.get("summary")
                if universe_path.exists():
                    universe = json.loads(universe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                error = f"Rapor okunamadı: {exc}"
        with self.lock:
            self.state = "error" if error else "done"
            self.error = error
            self.summary = summary
            self.universe = universe
            self.finished_at = datetime.now(UTC).isoformat()
        log.info("Web backtest finished: %s (%s)", self.run_id, self.state)

    def status(self) -> dict[str, Any]:
        is_running = self.is_running
        with self.lock:
            return {
                "state": "running" if is_running else self.state,
                "run_id": self.run_id,
                "output_dir": self.output_dir,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "progress": list(self.progress[-60:]),
                "summary": self.summary,
                "universe": self.universe,
            }


backtest_manager = BacktestManager()


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
    def __init__(self) -> None:
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
        self.log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = set()
        self._log_queues_lock = threading.Lock()
        # Reentrant: reset_safety_lock and current_engine_state may be invoked
        # while the state lock is already held by the caller.
        self._state_lock = threading.RLock()
        self.control_token = secrets.token_urlsafe(32)
        self._live_request_suffix: str | None = None
        self._live_armed = False
        self._pending_live_arm: LiveArmToken | None = None
        self._live_arm_token: LiveArmToken | None = None
        self._ws_tickets: dict[str, tuple[str, float]] = {}
        # issue_ws_ticket rebuilds the map while consume_ws_ticket pops from
        # it; concurrent issuers must not drop each other's fresh tickets.
        self._ws_tickets_lock = threading.Lock()
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
        self.security_warning = _tracked_secret_warning(Path.cwd())
        if self.security_warning:
            log.critical("%s", self.security_warning)

    def is_paper_mode(self) -> bool:
        try:
            if self.config_path.exists():
                modified_ns = self.config_path.stat().st_mtime_ns
                cached = self._paper_mode_cache
                if cached is not None and cached[0] == modified_ns:
                    return cached[1]
                with open(self.config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                runtime_mode = cfg.get("runtime", {}).get("mode")
                if runtime_mode is None and cfg.get("alpaca", {}).get("paper") is False:
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
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            app_config = (raw or {}).get("app", {})
            runtime_config = (raw or {}).get("runtime", {})
            legacy_live = "mode" not in runtime_config and (raw or {}).get("alpaca", {}).get("paper") is False
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

        old_app = dict(cfg_raw.get("app", {}))

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
        engine_raw = dict(cfg_raw.get("engine", {}))
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
        risk_raw = dict(cfg_raw.get("risk", {}))
        for key in obsolete_risk_keys:
            risk_raw.pop(key, None)
        cfg_raw["risk"] = risk_raw

        cfg_raw.setdefault("runtime", {})
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
        live = cfg_raw.setdefault("live", {})
        if "paper_mode" in fields_set or "live_trading_enabled" in fields_set:
            live["enabled"] = not target_paper
        if "live_dollar_cap" in fields_set:
            live["hard_dollar_cap"] = payload.live_dollar_cap
        if "live_operator_approval_path" in fields_set:
            live["operator_approval_path"] = payload.live_operator_approval_path
        strategy = cfg_raw.setdefault("strategy", {})
        risk = cfg_raw["risk"]
        if "daily_loss_limit_pct" in fields_set:
            risk["daily_loss_limit_pct"] = payload.daily_loss_limit_pct / 100
        if "max_drawdown_pct" in fields_set:
            risk["max_drawdown_pct"] = payload.max_drawdown_pct / 100
        if "equity_trading_enabled" in fields_set:
            strategy.setdefault("equity", {})["enabled"] = payload.equity_trading_enabled
        if "crypto_trading_enabled" in fields_set:
            strategy.setdefault("crypto", {})["enabled"] = payload.crypto_trading_enabled
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
            if cfg_raw.get("symbols") or cfg_raw.get("universe"):
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
        # Mutual exclusion with the Web backtest runner: the engine must not
        # start while an expensive in-process backtest is using the process.
        # The backtest lock is taken FIRST on both paths (BacktestManager.start
        # re-reads the engine state under it), so the two guards cannot
        # interleave the way two independent checks could.
        with backtest_manager.lock:
            if backtest_manager.is_running:
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

        key, secret = self.get_credentials()
        if not key or not secret:
            with self._state_lock:
                self.engine_state = "error"
                self.last_engine_error = "Alpaca API key/secret missing. Configure settings first."
            raise ValueError("Alpaca API key/secret missing. Configure settings first.")

        runtime: Any = None
        interrupted = False
        try:
            from parayabuck.runtime import EngineRuntime, TransientStartupError
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
            warning = _tracked_secret_warning(Path.cwd())
            if warning:
                raise ValueError(warning)
            cfg = load_config(str(self.config_path))
            live_mode = cfg.runtime.mode.value == "live"
            if live_mode and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES before starting the application."
                )
            if live_mode:
                self._require_valid_live_arm(key, secret, consume=True)

            runtime = EngineRuntime(api_key=key, secret_key=secret, config=cfg)
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
                self.broker = None
                self.last_account = None
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
            self.broker = None
        if broker is not None and broker is not getattr(self.runtime, "broker", None):
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
                    "account": self.last_account,
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
        account_id, account_probe_error = self._probe_live_account(key, secret)
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
                if not hmac.compare_digest(approval.signature, expected):
                    approval_error = "approval file signature does not match the operator approval key"
                elif approval.expires_at <= datetime.now(UTC):
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
            from parayabuck.execution.portfolio import portfolio_risk_limits_from_config
            from parayabuck.portfolio import product_strategy_from
            from parayabuck.portfolio.runtime_service import configuration_digest
            from parayabuck.universe import load_active_crypto_symbols, load_active_symbols
            from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
            from parayabuck.universe.scanner import scanner_config_digest

            digests: list[str] = []
            for selection, symbols, universe_version in (
                (
                    cfg.strategy.equity,
                    load_active_symbols(cfg.universe.equity, log_rejection=False),
                    scanner_config_digest(cfg.universe.equity),
                ),
                (
                    cfg.strategy.crypto,
                    load_active_crypto_symbols(cfg.universe.crypto, log_rejection=False),
                    crypto_scanner_config_digest(cfg.universe.crypto),
                ),
            ):
                if not selection.enabled or not symbols:
                    continue
                strategy = product_strategy_from(selection.name, selection.parameters)
                digests.append(
                    configuration_digest(
                        strategy,
                        "live",
                        selection.maximum_allocation,
                        universe_version,
                        portfolio_risk_limits_from_config(cfg.risk),
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
                f"cannot verify the live account binding: live account probe failed: {account_probe_error}"
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
        raw.setdefault("runtime", {})["mode"] = "paper" if paper else "live"
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def issue_ws_ticket(self, kind: str) -> str:
        if kind not in {"stream", "logs"}:
            raise ValueError("Unknown WebSocket channel")
        # Prune expired entries: a ticket fetched but never consumed (page
        # closed before the WebSocket connected) must not accumulate forever.
        now = time.monotonic()
        ticket = secrets.token_urlsafe(24)
        with self._ws_tickets_lock:
            self._ws_tickets = {key: value for key, value in self._ws_tickets.items() if now < value[1]}
            self._ws_tickets[ticket] = (kind, now + 30.0)
        return ticket

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
        with self._ws_tickets_lock:
            value = self._ws_tickets.pop(ticket, None)
        return bool(value and value[0] == kind and time.monotonic() < value[1])


_PLACEHOLDERS = {
    "",
    "your_secret_key_here",
    "your-secret-key",
    "replace_me",
    "changeme",
    "<secret>",
}


def _tracked_secret_warning(root: Path) -> str | None:
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


manager = EngineManager()
_settings_lock = asyncio.Lock()

_WEB_ONLY_FIELDS: frozenset[str] = frozenset({"host", "port", "open_browser", "log_level"})


async def _auto_start_paper_engine() -> None:
    try:
        await manager.auto_start_engine()
    except Exception as exc:
        log.error("Automatic paper engine start failed: %s", exc)


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> Any:
    auto_start_task: asyncio.Task[None] | None = None
    should_auto_start = getattr(manager, "should_auto_start", lambda: False)
    if should_auto_start():
        log.info("Starting configured paper engine automatically")
        auto_start_task = asyncio.create_task(_auto_start_paper_engine())
    try:
        yield
    finally:
        if auto_start_task is not None:
            # A pending backoff sleep must not delay shutdown by up to a full
            # retry interval: cancel the retry so the task settles promptly.
            # An in-flight start attempt is still allowed to finish.
            manager._cancel_pending_retry()
            with suppress(asyncio.CancelledError):
                await auto_start_task
        # Uvicorn shutting down must also release Alpaca's stock and crypto
        # sockets. Daemon threads alone remain active long enough to collide
        # with a newly launched process and trigger the connection limit.
        await asyncio.to_thread(manager.stop_engine)


# Custom WebSocket Log Handler
class WebSocketLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        log_entry = {
            "level": record.levelname,
            "levelno": record.levelno,
            "name": record.name,
            "message": record.getMessage(),
            "time": self.formatter.formatTime(record) if self.formatter else "",
        }
        with manager._log_queues_lock:
            subscribers = tuple(manager.log_queues)
        for queue, loop in subscribers:
            with suppress(Exception):
                loop.call_soon_threadsafe(_enqueue_log, queue, log_entry)


def _enqueue_log(target: asyncio.Queue[dict[str, Any]], entry: dict[str, Any]) -> None:
    if target.full():
        try:
            target.get_nowait()
        except asyncio.QueueEmpty:
            return
    try:
        target.put_nowait(entry)
    except asyncio.QueueFull:
        return


ws_log_handler = WebSocketLogHandler()
ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger("parayabuck").addHandler(ws_log_handler)

# FastAPI App
app = FastAPI(title="ParayaBuck Trading Terminal", lifespan=app_lifespan)


def _trusted_browser_origin(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser/local API clients
    parsed = urlparse(origin)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def protect_local_control_api(request: Request, call_next: Any) -> Any:
    host_header = request.headers.get("host") or ""
    host = urlparse(f"//{host_header}").hostname or ""
    allowed = {"127.0.0.1", "localhost", "::1"}
    # Allow testserver only inside pytest; otherwise loopback-only
    if os.getenv("PYTEST_CURRENT_TEST"):
        allowed = allowed | {"testserver"}
    if host not in allowed:
        return JSONResponse(status_code=403, content={"detail": "Untrusted Host header"})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _trusted_browser_origin(request.headers.get("origin")):
        return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        request.headers.get("x-parayabuck-control-token", "").encode("latin-1", "replace"),
        manager.control_token.encode(),
    ):
        return JSONResponse(status_code=403, content={"detail": "Invalid local control token"})
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self' ws://127.0.0.1:* "
        "ws://localhost:* ws://[::1]:*; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@dataclass(frozen=True)
class ValidatedSettings:
    """Validated settings-save result: the new config mapping and credentials."""

    cfg_raw: dict[str, Any]
    old_app: dict[str, Any]
    api_key: str
    secret_key: str
    credentials_changed: bool


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field("", description="Alpaca API Key")
    secret_key: str = Field("", description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    daily_loss_limit_pct: float = Field(1.5, gt=0, le=10)
    max_drawdown_pct: float = Field(10.0, gt=0, le=30)
    execution_timeframe: Literal["5m"] = "5m"
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = Field(True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    restart_engine: bool = True
    equity_trading_enabled: bool = True
    crypto_trading_enabled: bool = False
    live_trading_enabled: bool = False
    live_dollar_cap: float = Field(0.0, ge=0)
    live_operator_approval_path: str = ""
    expected_config_digest: str | None = Field(default=None, description="Optimistic concurrency digest")


class ModePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper: bool


class LiveConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrase: str


class WebSocketTicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["stream", "logs"]


class BacktestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["equity", "crypto"] = "equity"
    strategy: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)
    years: int = Field(5, ge=1, le=30)
    days: int = Field(0, ge=0)
    symbols: str = ""
    # 0 (default) means the broad eligible universe; an explicit positive
    # value caps the download set for resource-limited runs.
    max_symbols: int = Field(0, ge=0, le=2000)


# Routes
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "is_paper": manager.is_paper_mode(),
            "control_token": manager.control_token,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/tools", response_class=HTMLResponse)
async def serve_tools(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="tools.html",
        context={"control_token": manager.control_token},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness endpoint; runtime readiness remains visible in /api/status."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Deployment/trading readiness, distinct from /healthz process liveness.

    Distinguishes the four readiness layers:
    * process alive        — /healthz (this endpoint implies it);
    * configuration valid  — ``config_valid`` (parsing is not enough);
    * trading expected     — the configuration intends an engine to trade
      (``auto_start_engine`` in paper mode, or live arming);
    * trading ready        — the expected runtime is actually healthy.

    A failed automatic engine start (``auto_start_engine: true`` but the
    engine ended in the error state) is NOT ready: the configuration parsed,
    but the deployment expected trading and is not trading. An operator who
    intentionally stopped the engine leaves ``trading_expected`` false and
    the control plane ready; readiness is never accidentally derived from
    ``runtime is None``.
    """
    try:
        cfg = load_config(str(manager.config_path))
        config_valid = True
        config_error = None
    except Exception as exc:
        config_valid = False
        config_error = str(exc)
        cfg = None
    engine_state = manager.current_engine_state()
    runtime = getattr(manager, "runtime", None)
    health = None
    if runtime is not None and hasattr(runtime, "health"):
        try:
            # A failing probe (e.g. ledger closed mid-stop) must degrade the
            # readiness answer, not 500 the endpoint.
            health = runtime.health()
        except Exception as exc:
            log.warning("Readiness health probe failed: %s", exc)
    trading_ready = bool(health and health.healthy)
    app_config = getattr(cfg, "app", None) if cfg is not None else None
    runtime_config = getattr(cfg, "runtime", None) if cfg is not None else None
    configured_mode = getattr(runtime_config, "mode", None) if runtime_config is not None else None
    configured_to_trade = bool(
        app_config is not None
        and configured_mode is not None
        and getattr(app_config, "auto_start_engine", False)
        and configured_mode.value == "paper"
    )
    engine_error = engine_state == "error"
    # A failed auto-start (or any unexpected engine error while trading is
    # expected) is a 503: the config parses but the deployment is not
    # trading. An intentional operator stop (or auto_start_engine=false)
    # means trading is not expected and the control plane is ready.
    trading_expected = configured_to_trade or manager.has_active_runtime() or engine_error
    ready = config_valid and (not trading_expected or trading_ready)
    return JSONResponse(
        {
            "ready": ready,
            "process_alive": True,
            "config_valid": config_valid,
            "trading_expected": trading_expected,
            "trading_ready": trading_ready,
            "engine_state": engine_state,
            # Legacy alias for older probes.
            "runtime_expected": manager.has_active_runtime(),
            "error": config_error,
        },
        status_code=200 if ready else 503,
    )


@app.get("/api/control-token")
async def get_control_token() -> JSONResponse:
    response = JSONResponse({"control_token": manager.control_token})
    response.headers["Cache-Control"] = "no-store"
    return response


def _strategy_metadata(cfg: Any) -> dict[str, Any]:
    """Declared strategy contracts plus live universe sizes for the Web UI.

    There is deliberately NO global strategy timeframe here: each strategy
    owns its signal timeframe and the UI only surfaces the strategy's own
    metadata (and, when a strategy supports multiple validated timeframes,
    that restricted choice).
    """
    from parayabuck.portfolio import portfolio_strategy_from

    result: dict[str, Any] = {"equity": None, "crypto": None}
    candidate_payload: dict[str, Any] = {}
    active_payload: dict[str, Any] = {}
    active_crypto_payload: dict[str, Any] = {}
    for path, store in (
        (cfg.universe.equity.candidate_universe_path, candidate_payload),
        (cfg.universe.equity.active_universe_path, active_payload),
        (cfg.universe.crypto.active_universe_path, active_crypto_payload),
    ):
        store.update(_dashboard_load_json(path))
    shortlist = candidate_payload.get("shortlist_symbols")
    crypto_candidates = active_crypto_payload.get("candidate_symbols")
    for market_key, selection, pool_size in (
        (
            "equity",
            cfg.strategy.equity,
            len(shortlist) if isinstance(shortlist, list) else None,
        ),
        (
            "crypto",
            cfg.strategy.crypto,
            len(crypto_candidates) if isinstance(crypto_candidates, list) else None,
        ),
    ):
        if not selection.enabled:
            continue
        try:
            strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
            spec = strategy.spec()
            observation_size = (
                len(active_payload.get("observation_set", active_payload.get("selected_symbols", ())))
                if market_key == "equity"
                else len(
                    active_crypto_payload.get("observation_set", active_crypto_payload.get("selected_symbols", ()))
                )
            )
            result[market_key] = {
                "name": spec.name,
                "display_name": spec.display_name,
                "version": spec.version,
                "signal_timeframe": spec.primary_timeframe,
                "supported_timeframes": list(spec.supported_timeframes),
                "rebalance_cadence": spec.rebalance_policy,
                "minimum_history_bars": spec.minimum_history_bars,
                "dynamic_universe_enabled": (
                    cfg.universe.equity.dynamic_selection_enabled
                    if market_key == "equity"
                    else cfg.universe.crypto.dynamic_selection_enabled
                ),
                "candidate_pool_size": pool_size,
                "observation_set_size": observation_size,
            }
        except Exception:
            # A strategy that cannot be constructed must surface as an error,
            # never silently disappear from the status UI.
            result[market_key] = {"name": selection.name, "configuration_error": True}
    return result


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    key, _ = manager.get_credentials()
    result: dict[str, Any] = {
        "state": manager.current_engine_state(),
        "engine_error": getattr(manager, "last_engine_error", None),
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key),
        "security_warning": getattr(manager, "security_warning", None),
        "active_equity_symbol_count": 0,
        "active_crypto_symbol_count": 0,
    }
    # One config load per status poll: strategy and universe state share it.
    cfg: Any = None
    try:
        cfg = load_config(str(manager.config_path))
        configured = []
        if cfg.strategy.equity.enabled:
            configured.append(f"equity/{cfg.strategy.equity.name}")
        if cfg.strategy.crypto.enabled:
            configured.append(f"crypto/{cfg.strategy.crypto.name}")
        result["strategy_status"] = "configured:" + ",".join(configured)
        result["strategy_metadata"] = _strategy_metadata(cfg)
    except Exception:
        result["strategy_status"] = "configuration_error"
        # Mirror the universe-section failure state so consumers never see a
        # missing scanner key when the config cannot be loaded.
        result["scanner"] = {"last_run_at": None, "status": "error"}
    # Bounded auto-start retry state (cheap reads; present only while a retry
    # cycle is active, so a settled engine exposes no retry noise).
    retry_attempt = getattr(manager, "_retry_attempt", 0)
    retry_next_at = getattr(manager, "_retry_next_at", None)
    if retry_attempt > 0 and retry_next_at is not None:
        remaining = max(0.0, retry_next_at - time.monotonic())
        result["engine"] = {
            "retry": {
                "attempt": retry_attempt,
                "retry_in_seconds": round(remaining, 1),
                "next_retry_at": (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat(),
            }
        }
    runtime = getattr(manager, "runtime", None)
    runtime_refresh: dict[str, Any] = {}
    if runtime is not None and hasattr(runtime, "health"):
        try:
            health = runtime.health()
        except Exception as exc:
            # Same contract as current_engine_state: a failing probe must
            # not take down /api/status, which the UI polls every 2s.
            log.warning("Status health probe failed: %s", exc)
            health = None
        if health is not None:
            result["runtime"] = asdict(health)
            runtime_refresh = health.universe_refresh
            result["strategy_status"] = health.strategy_status
            repository = getattr(runtime, "_portfolio_repository", None)
            if repository is not None:
                account_id = getattr(runtime.broker, "account_id", None) or "unknown"
                result["operations"] = repository.operations_snapshot(account_id)
                result["ownership_drift_active"] = health.circuit_breaker_reason == "ownership_drift"
                result["reconciliation_anomalies"] = list(repository.ownership_adjustments(account_id))
            result["active_equity_symbol_count"] = len(
                getattr(runtime, "_symbols_by_market", {}).get(Market.EQUITY, ())
            )
            result["active_crypto_symbol_count"] = len(
                getattr(runtime, "_symbols_by_market", {}).get(Market.CRYPTO, ())
            )
    if cfg is not None:
        try:
            candidate_path = Path(cfg.universe.equity.candidate_universe_path)
            active_path = Path(cfg.universe.equity.active_universe_path)
            active_crypto_path = Path(cfg.universe.crypto.active_universe_path)
            candidate_payload = (
                json.loads(candidate_path.read_text(encoding="utf-8")) if candidate_path.exists() else []
            )
            active_payload = json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else {}
            active_crypto_payload = (
                json.loads(active_crypto_path.read_text(encoding="utf-8")) if active_crypto_path.exists() else {}
            )
            candidate_items = (
                candidate_payload.get(
                    "candidates",
                    candidate_payload.get("symbols", candidate_payload),
                )
                if isinstance(candidate_payload, dict)
                else candidate_payload
            )
            result["candidate_universe_size"] = len(candidate_items) if isinstance(candidate_items, (list, dict)) else 0
            equity_symbols, validated_scanner_status = validate_active_universe(cfg.universe.equity)
            crypto_symbols, validated_crypto_scanner_status = validate_active_crypto_universe(cfg.universe.crypto)
            if runtime is None:
                result["active_equity_symbol_count"] = len(equity_symbols)
                result["active_crypto_symbol_count"] = len(crypto_symbols)
            result["scanner"] = {
                "last_run_at": active_payload.get("selection_timestamp"),
                "status": runtime_refresh.get("status", {}).get("equity", validated_scanner_status),
                "equity_status": runtime_refresh.get("status", {}).get("equity", validated_scanner_status),
                "crypto_status": runtime_refresh.get("status", {}).get("crypto", validated_crypto_scanner_status),
                "equity_snapshot_status": validated_scanner_status,
                "crypto_snapshot_status": validated_crypto_scanner_status,
                "scanner_version": active_payload.get("scanner_version"),
                "crypto_last_run_at": active_crypto_payload.get("selection_timestamp"),
                "crypto_scanner_version": active_crypto_payload.get("scanner_version"),
                "next_refresh_at": runtime_refresh.get("next_refresh_at"),
                "refresh_errors": runtime_refresh.get("errors", {}),
                "equity_candidate_count": len(active_payload.get("candidate_symbols", ())),
                "equity_shortlist_count": len(
                    candidate_payload.get("shortlist_symbols", ()) if isinstance(candidate_payload, dict) else ()
                ),
                "equity_sector_metadata_coverage": active_payload.get("sector_metadata_coverage"),
                "crypto_candidate_count": len(active_crypto_payload.get("candidate_symbols", ())),
            }
            result["universe"] = {
                "equity": {
                    "symbols": list(active_payload.get("observation_set", active_payload.get("selected_symbols", ()))),
                    "pool_size": len(active_payload.get("selected_symbols", ())),
                    "scores": active_payload.get("score_breakdown", {}),
                    "eliminated": active_payload.get("eliminated", {}),
                },
                "crypto": {
                    "symbols": list(
                        active_crypto_payload.get("observation_set", active_crypto_payload.get("selected_symbols", ()))
                    ),
                    "pool_size": len(active_crypto_payload.get("selected_symbols", ())),
                    "scores": active_crypto_payload.get("score_breakdown", {}),
                    "eliminated": active_crypto_payload.get("eliminated", {}),
                },
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result["scanner"] = {"last_run_at": None, "status": "error"}
    return result


@app.get("/api/ops/status")
async def get_ops_status() -> dict[str, Any]:
    """Consolidated operational status — offline health evaluation (Mission 5)."""
    try:
        from parayabuck.monitoring.health import evaluate_health

        cfg = None
        try:
            cfg = load_config(str(manager.config_path))
        except Exception:
            cfg = None
        snap = "data/runtime/health_snapshot.json"
        db = "data/runtime/parayabuck.sqlite3"
        if cfg is not None:
            try:
                snap = cfg.observability.snapshot_path
                db = cfg.runtime.decision_db_path
            except Exception:
                pass
        return evaluate_health(snapshot_path=snap, database_path=db, config=cfg)
    except Exception as exc:  # pragma: no cover
        log.warning("ops status probe failed: %s", exc)
        return {
            "error": str(exc),
            "overall_health": "UNKNOWN",
            "trading_state": "UNKNOWN",
            "checks": [],
            "as_of": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        }


@app.get("/api/account")
async def get_account() -> dict[str, Any]:
    return await asyncio.to_thread(manager.fetch_account_snapshot)


@app.post("/api/engine/start")
async def start_engine() -> dict[str, str]:
    try:
        await asyncio.to_thread(manager.start_engine)
        return {"status": "started", "message": "Bot engine started successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/engine/stop")
async def stop_engine() -> dict[str, str]:
    try:
        await asyncio.to_thread(manager.stop_engine)
    except RuntimeError as exc:
        # Operator-facing failure with a specific reason (e.g. workers did
        # not stop); surface it.
        log.error("Engine stop failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        log.exception("Engine stop failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Engine could not be stopped") from None
    return {"status": "stopped", "message": "Bot engine stopped."}


@app.post("/api/engine/reset-safety-lock")
async def reset_safety_lock() -> dict[str, str]:
    try:
        await asyncio.to_thread(manager.reset_safety_lock)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        log.exception("Safety lock reset failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Safety lock reset failed") from None
    return {
        "status": "reset",
        "message": "Safety lock reset after broker reconciliation and data checks.",
    }


@app.post("/api/safety/risk-epoch")
async def start_new_risk_epoch() -> dict[str, Any]:
    """Operator-verified drawdown-baseline reset: rebases the drawdown peak
    to the current account equity after a verified broker reconciliation and
    starts a new risk epoch. Explicit recovery for portfolio_drawdown_halt."""
    try:
        return await asyncio.to_thread(manager.start_new_risk_epoch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        log.exception("Risk epoch reset failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Risk epoch reset failed") from None


@app.post("/api/mode")
async def toggle_mode(payload: ModePayload) -> dict[str, Any]:
    if manager.has_active_runtime():
        raise HTTPException(
            status_code=400,
            detail="Cannot toggle trading mode while engine is running. Stop the bot first.",
        )

    try:
        if not payload.paper:
            key, secret = manager.get_credentials()
            if not key or not secret:
                raise ValueError("Alpaca credentials are required")
            manager._require_valid_live_arm(key, secret, consume=False)
        raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        raw.setdefault("runtime", {})["mode"] = "paper" if payload.paper else "live"
        if not payload.paper:
            raw.setdefault("live", {})["enabled"] = True
        atomic_write(
            manager.config_path,
            yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        )

        manager.reset_broker()  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Trading-mode toggle failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Trading mode could not be changed") from None


@app.get("/api/live/prerequisites")
async def live_prerequisites() -> dict[str, Any]:
    """Live-arm readiness checklist (no secrets in the response)."""
    return await asyncio.to_thread(manager.live_prerequisites)


@app.post("/api/live/arming/request")
async def request_live_arming() -> dict[str, str]:
    try:
        suffix = await asyncio.to_thread(manager.request_live_arming)
        return {
            "status": "confirmation_required",
            "account_suffix": suffix,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/live/arming/confirm")
async def confirm_live_arming(payload: LiveConfirmationPayload) -> dict[str, str]:
    try:
        manager.confirm_live_arming(payload.phrase)
        return {
            "status": "armed",
            "message": "Live mode armed for this server process. Start remains a separate action.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ws-ticket")
async def websocket_ticket(payload: WebSocketTicketPayload) -> dict[str, str]:
    try:
        return {"ticket": manager.issue_ws_ticket(payload.kind)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    key, secret = manager.get_credentials()
    cfg_raw: dict[str, Any] = {}
    try:
        if manager.config_path.exists():
            with open(manager.config_path, encoding="utf-8") as f:
                cfg_raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        # The settings page is the operator's recovery tool: it must survive
        # a config it exists to fix. Fall back to defaults, like
        # live_prerequisites does.
        log.warning("Settings could not read config (%s): %s", manager.config_path, exc)

    def _section(name: str) -> dict[str, Any]:
        # A YAML section present but empty parses to None.
        value = cfg_raw.get(name, {})
        return value if isinstance(value, dict) else {}

    risk_cfg = _section("risk")
    runtime_cfg = _section("runtime")
    app_cfg = _section("app")
    strategy_cfg = _section("strategy")
    live_cfg = _section("live")

    try:
        current_digest = manager._config_digest(paper=manager.is_paper_mode())
    except Exception:
        current_digest = manager._digest_config(cfg_raw, paper=manager.is_paper_mode())
    return {
        # Never send credentials back to the browser. Empty fields mean
        # "preserve the existing value" when settings are saved.
        "api_key": "",
        "secret_key": "",
        "has_credentials": bool(key and secret),
        "paper_mode": manager.is_paper_mode(),
        "daily_loss_limit_pct": 100 * risk_cfg.get("daily_loss_limit_pct", 0.005),
        "max_drawdown_pct": 100 * risk_cfg.get("max_drawdown_pct", 0.05),
        "timeframe": runtime_cfg.get("execution_timeframe", "5m"),
        "execution_timeframe": runtime_cfg.get("execution_timeframe", "5m"),
        "host": app_cfg.get("host", "127.0.0.1"),
        "port": app_cfg.get("port", 8000),
        "open_browser": app_cfg.get("open_browser", True),
        "log_level": app_cfg.get("log_level", "INFO"),
        "equity_trading_enabled": (strategy_cfg.get("equity") or {}).get("enabled", True),
        "crypto_trading_enabled": (strategy_cfg.get("crypto") or {}).get("enabled", True),
        "live_trading_enabled": live_cfg.get("enabled", False),
        "live_dollar_cap": live_cfg.get("hard_dollar_cap", 0.0),
        "live_operator_approval_path": live_cfg.get("operator_approval_path", ""),
        "config_digest": current_digest,
        "config_version": current_digest,
    }


async def _handle_save_settings(payload: SettingsPayload) -> dict[str, Any]:
    def _current_digest() -> str:
        try:
            return manager._config_digest(paper=manager.is_paper_mode())
        except Exception:
            raw_tmp: dict[str, Any] = {}
            if manager.config_path.exists():
                try:
                    raw_tmp = yaml.safe_load(manager.config_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass
            return manager._digest_config(raw_tmp, paper=manager.is_paper_mode())

    async with _settings_lock:
        # Digest check BEFORE validation and BEFORE stopping runtime.
        if payload.expected_config_digest is not None:
            current = _current_digest()
            if current != payload.expected_config_digest:
                raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

        # Snapshot pre-image for change-class computation.
        raw_before: dict[str, Any] = {}
        if manager.config_path.exists():
            try:
                raw_before = yaml.safe_load(manager.config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                raw_before = {}
        old_paper_before = manager.is_paper_mode()

        validated = manager._validated_settings_payload(payload)
        cfg_raw = validated.cfg_raw
        old_app = validated.old_app
        api_key = validated.api_key
        secret_key = validated.secret_key
        credentials_changed = validated.credentials_changed

        fields_set = set(payload.model_fields_set)
        # Control fields do not represent config mutations.
        config_fields_set = fields_set - {"expected_config_digest", "restart_engine"}

        # --- Change classes ---
        has_web_only_change = False
        has_runtime_change = False

        # Paper/live flip detection (live-safety-affecting + runtime-affecting)
        if "paper_mode" in config_fields_set or "live_trading_enabled" in config_fields_set:
            if "paper_mode" in config_fields_set:
                target_paper = payload.paper_mode
            elif "live_trading_enabled" in config_fields_set:
                target_paper = not payload.live_trading_enabled
            else:
                target_paper = old_paper_before
            if target_paper != old_paper_before:
                has_runtime_change = True

        # Per-field actual-value comparison for remaining fields.
        if "daily_loss_limit_pct" in config_fields_set:
            old_daily_loss: float = (
                100 * float(raw_before.get("risk", {}).get("daily_loss_limit_pct", 0.005))
                if isinstance(raw_before.get("risk", {}), dict)
                else 100 * 0.005
            )
            try:
                old_daily_loss_f = float(old_daily_loss)
            except Exception:
                old_daily_loss_f = 100 * 0.005
            if float(payload.daily_loss_limit_pct) != old_daily_loss_f:
                has_runtime_change = True
        if "max_drawdown_pct" in config_fields_set:
            old_max_dd: float = (
                100 * float(raw_before.get("risk", {}).get("max_drawdown_pct", 0.05))
                if isinstance(raw_before.get("risk", {}), dict)
                else 100 * 0.05
            )
            try:
                old_max_dd_f = float(old_max_dd)
            except Exception:
                old_max_dd_f = 100 * 0.05
            if float(payload.max_drawdown_pct) != old_max_dd_f:
                has_runtime_change = True
        if "execution_timeframe" in config_fields_set:
            runtime_raw = raw_before.get("runtime", {}) if isinstance(raw_before.get("runtime"), dict) else {}
            old_tf = runtime_raw.get("execution_timeframe") if isinstance(runtime_raw, dict) else None
            if old_tf is None or payload.execution_timeframe != old_tf:
                has_runtime_change = True
        if "equity_trading_enabled" in config_fields_set:
            try:
                _eq = (
                    raw_before.get("strategy", {}).get("equity")
                    if isinstance(raw_before.get("strategy"), dict)
                    else None
                )
                old_eq_enabled = _eq.get("enabled", True) if isinstance(_eq, dict) else True
            except Exception:
                old_eq_enabled = True
            if bool(payload.equity_trading_enabled) != bool(old_eq_enabled):
                has_runtime_change = True
        if "crypto_trading_enabled" in config_fields_set:
            try:
                _cr = (
                    raw_before.get("strategy", {}).get("crypto")
                    if isinstance(raw_before.get("strategy"), dict)
                    else None
                )
                old_cr_enabled = _cr.get("enabled", False) if isinstance(_cr, dict) else False
            except Exception:
                old_cr_enabled = False
            if bool(payload.crypto_trading_enabled) != bool(old_cr_enabled):
                has_runtime_change = True
        if "live_dollar_cap" in config_fields_set:
            try:
                old_cap: float = (
                    float(raw_before.get("live", {}).get("hard_dollar_cap", 0.0))
                    if isinstance(raw_before.get("live"), dict)
                    else 0.0
                )
            except Exception:
                old_cap = 0.0
            if float(payload.live_dollar_cap) != old_cap:
                has_runtime_change = True
        if "live_operator_approval_path" in config_fields_set:
            old_op_path: Any = (
                raw_before.get("live", {}).get("operator_approval_path", "")
                if isinstance(raw_before.get("live"), dict)
                else ""
            )
            if (payload.live_operator_approval_path or "") != (old_op_path or ""):
                has_runtime_change = True
        # Web-process-only fields
        if "host" in config_fields_set:
            old_v = old_app.get("host", "127.0.0.1")
            new_v = payload.host.strip() or "127.0.0.1"
            if old_v != new_v:
                has_web_only_change = True
        if "port" in config_fields_set:
            old_v = old_app.get("port", 8000)
            if int(payload.port) != int(old_v):
                has_web_only_change = True
        if "open_browser" in config_fields_set:
            old_v = old_app.get("open_browser", True)
            if bool(payload.open_browser) != bool(old_v):
                has_web_only_change = True
        if "log_level" in config_fields_set:
            old_v = old_app.get("log_level", "INFO")
            new_v = payload.log_level.upper()
            if str(old_v).upper() != str(new_v).upper():
                has_web_only_change = True

        has_credential_change = bool(credentials_changed)
        if has_credential_change:
            has_runtime_change = True

        is_no_op = not has_web_only_change and not has_runtime_change
        # If fields_set empty, is_no_op already true (covers PATCH {}).
        # Also treat case where fields_set contains only unchanged values as no-op.

        # Derived restart decision: only restart when runtime-affecting mutation actually changed.
        active_runtime = manager.has_active_runtime()
        needs_restart = has_runtime_change
        if active_runtime and needs_restart and not payload.restart_engine:
            raise ValueError("Stop the bot before saving settings without a restart")
        restart_engine = bool(active_runtime and needs_restart and payload.restart_engine)

        # No-op fast path: no file touch, no credential touch, no stop/restart.
        if is_no_op:
            # Digest already validated above; if digest was stale we would have raised 409 before reaching here.
            return {
                "status": "success",
                "message": "Settings and credentials saved.",
                "engine_restarted": False,
                "restart_error": None,
                "app_restart_required": False,
            }

        # Final conflict check BEFORE stopping healthy runtime (still inside lock, covers in-process races
        # and external edits detected via file digest).
        if payload.expected_config_digest is not None:
            latest = _current_digest()
            if latest != payload.expected_config_digest:
                raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

        was_stopped_for_restart = False
        if restart_engine:
            await asyncio.to_thread(manager.stop_engine)
            was_stopped_for_restart = True
            if manager.engine_state != "stopped":
                raise RuntimeError("Bot could not stop safely. Use the Durdur button and try again.")
            # Unavoidable external race after stop: re-check digest before write; if conflict,
            # best-effort restore previous runtime.
            if payload.expected_config_digest is not None:
                latest2 = _current_digest()
                if latest2 != payload.expected_config_digest:
                    try:
                        await asyncio.to_thread(manager.start_engine)
                    except Exception as exc2:
                        log.warning("Conflict after stop; best-effort restart failed: %s", exc2)
                    raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

        # TOCTOU final check for the non-restart path as well (immediately before write).
        if not was_stopped_for_restart and payload.expected_config_digest is not None:
            latest3 = _current_digest()
            if latest3 != payload.expected_config_digest:
                raise HTTPException(status_code=409, detail="Configuration has changed; refresh and try again")

        # Credential file: only touch when actually changed; preserve byte-identical when unchanged.
        if has_credential_change:
            _atomic_update_credentials(manager.env_path, api_key, secret_key)
        # else: DO NOT TOUCH .env (native) and never create/mutate in container mode.

        atomic_write(
            manager.config_path,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )

        manager.reset_broker()
        if has_credential_change:
            manager.invalidate_live_arming()
        restart_error: str | None = None
        if restart_engine:
            try:
                await asyncio.to_thread(manager.start_engine)
            except Exception as exc:
                restart_error = str(exc)
                log.warning("Settings saved, but automatic engine restart failed: %s", exc)

        _app_defaults = {"host": "127.0.0.1", "port": 8000, "open_browser": True, "log_level": "INFO"}
        app_restart_required = False
        for name, default in _app_defaults.items():
            if name not in payload.model_fields_set:
                continue
            old_v = old_app.get(name, default)
            new_v = getattr(payload, name)
            if name == "host":
                new_v = new_v.strip() or "127.0.0.1"
            if name == "log_level" and isinstance(old_v, str) and isinstance(new_v, str):
                if old_v.upper() == new_v.upper():
                    continue
                else:
                    app_restart_required = True
                    break
            if old_v != new_v:
                app_restart_required = True
                break

        return {
            "status": "success",
            "message": (
                "Settings saved, but the bot could not restart automatically."
                if restart_error
                else "Settings and credentials saved."
            ),
            "engine_restarted": restart_engine and restart_error is None,
            "restart_error": restart_error,
            "app_restart_required": app_restart_required,
        }


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    try:
        return await _handle_save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None


@app.patch("/api/settings")
async def patch_settings(payload: SettingsPayload) -> dict[str, Any]:
    try:
        return await _handle_save_settings(payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        log.exception("Settings save failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Settings could not be saved") from None


# Backtest (tek tık) — same production CLI and historical universe policy.
@app.post("/api/backtest/run")
async def run_backtest(payload: BacktestPayload) -> dict[str, Any]:
    try:
        run_id = await asyncio.to_thread(backtest_manager.start, payload.model_dump())
        return {"status": "started", "run_id": run_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/backtest/status")
async def backtest_status() -> dict[str, Any]:
    return await asyncio.to_thread(backtest_manager.status)


# ---------------------------------------------------------------------------
# Trading dashboard API. Read-only operator views over the SAME
# state /api/status and /api/account expose — no secrets, no mutations, no
# new ledgers. Every panel degrades to an explicit unavailable/error field
# instead of raising, so the dashboard stays readable while the engine is
# stopped, the ledger is missing, or an optional module import fails.
# ---------------------------------------------------------------------------

_DASHBOARD_DEFAULT_DB = Path(DEFAULT_DECISION_DB_PATH)


def _dashboard_runtime() -> Any:
    """The live EngineRuntime instance, or None when stopped (or stubbed)."""
    return getattr(manager, "runtime", None)


def _manager_engine_state() -> str:
    """Current engine state via the module manager (getattr tolerates stubs)."""
    return getattr(manager, "current_engine_state", lambda: "stopped")()


# Display-only caches for dashboard payload assembly. None of these can ever
# affect trading state, decisions, or the runtime: they memoize reads of
# static/display inputs (the settings file) and short-lived payload slices.
_dashboard_config_cache: dict[tuple[str, int], Any] = {}
_dashboard_config_cache_lock = threading.Lock()

# Parsed universe JSON files (active/candidate scans). The candidate file can
# be tens of MB and is re-parsed by BOTH _dashboard_universe and
# _strategy_metadata on every payload build. Keyed by (path, mtime_ns) so the
# scanner rewriting a file invalidates it on the next build; bounded FIFO so
# repeated scanner rewrites cannot grow the cache without limit.
_dashboard_json_cache: dict[tuple[str, int], dict[str, Any]] = {}
_dashboard_json_cache_lock = threading.Lock()
_DASHBOARD_JSON_CACHE_MAX_ENTRIES = 8


def _dashboard_load_json(path: str | Path) -> dict[str, Any]:
    """Read + parse one JSON file, cached per (path, mtime); {} on any error."""
    target = Path(path)
    try:
        key = (str(target), target.stat().st_mtime_ns)
    except OSError:
        return {}
    with _dashboard_json_cache_lock:
        cached = _dashboard_json_cache.get(key)
        if cached is not None or key in _dashboard_json_cache:
            assert cached is not None
            return cached
    try:
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    with _dashboard_json_cache_lock:
        if len(_dashboard_json_cache) >= _DASHBOARD_JSON_CACHE_MAX_ENTRIES:
            # FIFO eviction of the oldest entry.
            _dashboard_json_cache.pop(next(iter(_dashboard_json_cache)), None)
        _dashboard_json_cache[key] = data
    return data


def _dashboard_config_key() -> tuple[str, int] | None:
    """Cache key for the settings file: (path, mtime_ns); None when unreadable."""
    try:
        path = Path(str(manager.config_path))
        return (str(path), path.stat().st_mtime_ns if path.exists() else 0)
    except OSError:
        return None


def _dashboard_runtime_health() -> dict[str, Any] | None:
    runtime = _dashboard_runtime()
    if runtime is None or not hasattr(runtime, "health"):
        return None
    try:
        return asdict(runtime.health())
    except Exception as exc:
        log.warning("Dashboard health probe failed: %s", exc)
        return None


def _dashboard_config() -> Any | None:
    """The parsed settings Config, cached per file version.

    The dashboard rebuilds its payload on every poll and resolves the ledger
    path from this config in a dozen places; the YAML parse is pure overhead
    and the file is static per process. The cache is keyed by (path, mtime)
    so a settings save (or a swapped config path in tests) invalidates it on
    the next call. The returned Config is treated as read-only by every
    consumer. This cache is DISPLAY-ONLY: trading state never reads it.
    """
    try:
        key = _dashboard_config_key()
        if key is None:
            return None
    except OSError:
        return None
    with _dashboard_config_cache_lock:
        cached = _dashboard_config_cache.get(key)
        if cached is not None or key in _dashboard_config_cache:
            return cached
    try:
        cfg = load_config(str(manager.config_path))
    except Exception:
        return None
    with _dashboard_config_cache_lock:
        _dashboard_config_cache.clear()
        _dashboard_config_cache[key] = cfg
    return cfg


def _dashboard_account_id() -> str:
    runtime = _dashboard_runtime()
    broker_account = getattr(getattr(runtime, "broker", None), "account_id", None)
    if broker_account:
        return str(broker_account)
    # Engine stopped (or stubbed): scope reads to the account this
    # deployment's own ledger last traded with, never another process's.
    connection = _dashboard_readonly_connection()
    if connection is not None:
        try:
            row = connection.execute(
                "SELECT account_id FROM portfolio_runtime_sessions "
                "WHERE account_id IS NOT NULL AND account_id != '' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is not None and row[0]:
                return str(row[0])
        except sqlite3.Error:
            pass
        finally:
            _dashboard_release(connection)
    return "unknown"


def _dashboard_db_path() -> Path:
    cfg = _dashboard_config()
    if cfg is not None:
        configured = getattr(getattr(cfg, "runtime", None), "decision_db_path", None)
        if configured:
            return Path(str(configured))
    return _DASHBOARD_DEFAULT_DB


# Thread-local slot for the ONE read-only connection a payload build shares
# across every panel (decisions, risk, execution, telemetry). Display-only.
_dashboard_request_connection: threading.local = threading.local()


def _dashboard_readonly_connection() -> sqlite3.Connection | None:
    """Read-only ledger connection for the current payload build.

    While :func:`_dashboard_shared_connection` is active every dashboard
    helper receives the SAME connection (opened lazily here); otherwise a
    short-lived connection is opened and the caller must release it with
    :func:`_dashboard_release`. Returns None when the DB is missing.
    """
    shared: sqlite3.Connection | None = getattr(_dashboard_request_connection, "connection", None)
    if shared is not None:
        return shared
    path = _dashboard_db_path()
    if not path.exists():
        return None
    try:
        connection: sqlite3.Connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
    except sqlite3.Error as exc:
        log.warning("Dashboard could not open ledger read-only: %s", exc)
        return None


def _dashboard_release(connection: sqlite3.Connection | None) -> None:
    """Close ``connection`` unless it is the payload-build shared one."""
    if connection is None:
        return
    if getattr(_dashboard_request_connection, "connection", None) is connection:
        return  # shared: the build context owns its lifecycle
    with suppress(sqlite3.Error):
        connection.close()


@contextmanager
def _dashboard_shared_connection() -> Iterator[sqlite3.Connection | None]:
    """Run one payload build against a single shared read-only connection.

    The connection is opened lazily on first use and closed when the build
    finishes; helpers releasing it via :func:`_dashboard_release` no-op while
    the build is active. Display-only: never shared with trading state.
    """
    connection = _dashboard_readonly_connection()
    if connection is not None:
        _dashboard_request_connection.connection = connection
    try:
        yield connection
    finally:
        _dashboard_request_connection.connection = None
        if connection is not None:
            with suppress(sqlite3.Error):
                connection.close()


def _dashboard_safe_panel(name: str, builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one panel builder; a failure becomes an explicit unavailable dict."""
    try:
        payload = builder()
        return payload if isinstance(payload, dict) else {"unavailable": True}
    except Exception as exc:
        log.warning("Dashboard panel %s failed: %s", name, exc)
        return {"unavailable": True, "error": str(exc)}


def _dashboard_mode(cfg: Any) -> dict[str, Any]:
    """PAPER/LIVE badge state, driven by config.live.enabled + runtime mode.

    A live-configured deployment whose engine is not trading right now shows
    LIVE in a clearly-disabled style; a paper config always shows PAPER. The
    badge never derives from broker connectivity or secrets.
    """
    runtime_mode = "paper"
    live_enabled = False
    if cfg is not None:
        configured_mode = getattr(getattr(cfg, "runtime", None), "mode", None)
        runtime_mode = str(getattr(configured_mode, "value", None) or configured_mode or "paper")
        live_enabled = bool(getattr(getattr(cfg, "live", None), "enabled", False))
    paper = bool(getattr(manager, "is_paper_mode", lambda: True)())
    engine_state = _manager_engine_state()
    live_configured = bool(runtime_mode == "live" and live_enabled)
    live_active = bool(live_configured and engine_state in {"running", "warming_up", "degraded"})
    live_disabled = bool(live_configured and not live_active)
    return {
        "paper": paper,
        "runtime_mode": runtime_mode,
        "live_enabled": live_enabled,
        "live_configured": live_configured,
        "live_active": live_active,
        "live_disabled": live_disabled,
        "engine_state": engine_state,
        "badge": (
            "paper"
            if (paper and not live_configured)
            else ("live" if live_active else ("live_disabled" if live_configured else "unknown"))
        ),
    }


def _dashboard_market_decisions(account_id: str) -> dict[str, dict[str, Any]]:
    """Latest persisted decision per market (target + risk JSON payloads)."""
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {}
    try:
        rows = connection.execute(
            """
            SELECT market, decision_timestamp, strategy_name, target_json, risk_json
            FROM portfolio_decisions
            WHERE account_id = ?
            ORDER BY decision_timestamp DESC
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        _dashboard_release(connection)
    latest: dict[str, dict[str, Any]] = {}
    for market, timestamp, strategy_name, target, risk in rows:
        if market in latest:
            continue
        try:
            target_payload = json.loads(str(target)) if target else {}
            risk_payload = json.loads(str(risk)) if risk else {}
        except (TypeError, ValueError):
            continue
        latest[str(market)] = {
            "timestamp": str(timestamp),
            "strategy_name": str(strategy_name),
            "target": target_payload,
            "risk": risk_payload,
        }
    return latest


def _dashboard_next_rebalance_at(last_decision_at: str | None, policy: str | None) -> str | None:
    """Deterministic next rebalance boundary from the strategy cadence.

    Derived from the persisted decision timestamp and the strategy's declared
    rebalance policy (monthly / weekly / hours:N). It is an EXPECTED boundary
    (the decision runs on the first completed bar of the period), not a
    guarantee.
    """
    if not last_decision_at or not policy:
        return None
    try:
        last = datetime.fromisoformat(last_decision_at)
    except ValueError:
        return None
    if policy.startswith("hours:"):
        try:
            hours = int(policy.removeprefix("hours:"))
        except ValueError:
            return None
        return (last + timedelta(hours=hours)).isoformat()
    if policy == "weekly":
        days_ahead = 7 - last.weekday()
        boundary = (last + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        return boundary.isoformat()
    if policy == "monthly":
        year, month = last.year, last.month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        return datetime(year, month, 1, tzinfo=UTC).isoformat()
    return None


def _dashboard_market_status(
    engine_state: str,
    enabled: bool,
    market_health: dict[str, Any],
    health: dict[str, Any] | None,
) -> str:
    """active | warming | halted | scanner_issue | disabled | engine states."""
    if not enabled:
        return "disabled"
    if engine_state in {"stopped", "error", "stop_failed", "stopping", "starting"}:
        return engine_state
    if (health or {}).get("circuit_breaker_reason"):
        return "halted"
    if market_health.get("entry_halt_reason"):
        return "halted"
    if market_health.get("warming_symbols"):
        return "warming"
    if market_health.get("scanner_status") in {"refresh_failed", "apply_failed"}:
        return "scanner_issue"
    if health is None:
        return "unknown"
    return "active"


def _dashboard_market_card(
    market_key: str,
    *,
    cfg: Any,
    health: dict[str, Any] | None,
    account: dict[str, Any],
    meta: dict[str, Any],
    decision: dict[str, Any] | None,
    engine_state: str,
) -> dict[str, Any]:
    """One strategy card (equity or crypto): contract, pools, holdings."""
    strategy_cfg = getattr(cfg, "strategy", None) if cfg is not None else None
    selection = getattr(strategy_cfg, market_key, None)
    if selection is None:
        return {
            "enabled": False,
            "name": market_key,
            "status": "unavailable",
            "strategy": {},
            "eligible_pool": None,
            "observation_set_size": None,
            "positions_count": 0,
            "current_allocation_pct": 0.0,
            "maximum_allocation_pct": 0.0,
            "last_decision_at": None,
            "next_rebalance_at": None,
            "holdings": [],
        }
    enabled = bool(selection.enabled)
    market_health = dict((health or {}).get("markets", {}).get(market_key, {}) or {})
    catch_up = dict(market_health.get("catch_up") or {})
    positions = [position for position in account.get("positions", []) if position.get("market") == market_key]
    equity = max(float(account.get("equity") or 0.0), 1e-9)
    market_value = sum(float(position.get("market_value") or 0.0) for position in positions)
    target = dict((decision or {}).get("target") or {})
    weights = {str(weight.get("symbol")): weight for weight in target.get("weights", [])}
    metadata = dict(target.get("metadata") or {})
    selected = [str(symbol) for symbol in metadata.get("selected_symbols", ())]
    qualified = int(metadata.get("qualified_symbols") or 0)
    hypothesis = str(metadata.get("hypothesis") or "")
    holdings: list[dict[str, Any]] = []
    for position in positions:
        symbol = str(position.get("symbol", ""))
        weight = weights.get(symbol)
        rank = selected.index(symbol) + 1 if symbol in selected else None
        holdings.append(
            {
                "symbol": symbol,
                "direction": position.get("direction"),
                "qty": position.get("total_qty") or position.get("qty") or 0,
                "market_value": position.get("market_value"),
                "current_weight_pct": (float(position.get("market_value") or 0.0) / equity) * 100,
                "target_weight_pct": (float((weight or {}).get("weight") or 0.0) * 100 if weight else None),
                "reason": (weight or {}).get("reason") if weight else None,
                "rank": rank,
                "of_qualified": qualified if rank is not None else None,
                "hypothesis": hypothesis,
                "unrealized_pnl": position.get("unrealized_pnl"),
                "protection_state": position.get("protection_state"),
            }
        )
    return {
        "enabled": enabled,
        "name": selection.name,
        "status": _dashboard_market_status(engine_state, enabled, market_health, health),
        "strategy": {
            "name": (meta or {}).get("name") or selection.name,
            "display_name": (meta or {}).get("display_name"),
            "version": (meta or {}).get("version"),
            "signal_timeframe": (meta or {}).get("signal_timeframe"),
            "rebalance_cadence": (meta or {}).get("rebalance_cadence"),
            "minimum_history_bars": (meta or {}).get("minimum_history_bars"),
            "configuration_error": bool((meta or {}).get("configuration_error")),
        },
        "eligible_pool": (meta or {}).get("candidate_pool_size"),
        "observation_set_size": (meta or {}).get("observation_set_size"),
        "positions_count": len(positions),
        "current_allocation_pct": (market_value / equity) * 100,
        "maximum_allocation_pct": float(selection.maximum_allocation or 0.0) * 100,
        "last_decision_at": (decision or {}).get("timestamp"),
        "next_rebalance_at": _dashboard_next_rebalance_at(
            (decision or {}).get("timestamp"),
            (meta or {}).get("rebalance_cadence"),
        ),
        "startup_catch_up": {
            "status": catch_up.get("status"),
            "at": catch_up.get("at"),
            "next_boundary_after": catch_up.get("next_boundary_after"),
        },
        "holdings": holdings,
    }


# Trailing 22d realized vol per crypto holding, computed from the warmup cache
# with the strategy's own realized_volatility math. Minute-bucketed TTL so the
# ~2 GB cache file is only queried at most once per minute per symbol set.
_crypto_vol_cache: dict[tuple[tuple[str, ...], int], dict[str, float | None]] = {}


def _crypto_realized_vols(symbols: list[str]) -> dict[str, float | None]:
    """Trailing 22d realized vol for crypto symbols (None when unavailable)."""
    if not symbols:
        return {}
    bucket = int(time.monotonic() // 60)
    cache_key = (tuple(sorted(symbols)), bucket)
    # Keep only the current bucket per symbol set: one stale entry per key
    # instead of one per minute the dashboard keeps polling.
    for stale_key in [key for key in _crypto_vol_cache if key[0] == cache_key[0]]:
        _crypto_vol_cache.pop(stale_key, None)
    cached = _crypto_vol_cache.get(cache_key)
    if cached is not None:
        return cached
    result: dict[str, float | None] = {}
    try:
        from parayabuck.data.cache import WarmupCache
        from parayabuck.portfolio.momentum import realized_volatility
    except Exception:
        return dict.fromkeys(symbols)
    try:
        cache = WarmupCache()
        try:
            end = datetime.now(UTC)
            start = end - timedelta(days=90)
            for symbol in symbols:
                try:
                    bars = cache.get(
                        symbol,
                        "1d",
                        market=Market.CRYPTO,
                        feed="crypto",
                        start=start,
                        end=end,
                    )
                except Exception:
                    result[symbol] = None
                    continue
                closes = [float(bar.close) for bar in bars if bar.close > 0]
                if len(closes) < 23:
                    result[symbol] = None
                    continue
                try:
                    result[symbol] = float(realized_volatility(closes[-23:], 22))
                except Exception:
                    result[symbol] = None
        finally:
            cache.close()
    except Exception:
        return dict.fromkeys(symbols)
    _crypto_vol_cache[cache_key] = result
    return result


def _dashboard_crypto_card(
    *,
    cfg: Any,
    health: dict[str, Any] | None,
    account: dict[str, Any],
    meta: dict[str, Any],
    decision: dict[str, Any] | None,
    engine_state: str,
) -> dict[str, Any]:
    card = _dashboard_market_card(
        "crypto",
        cfg=cfg,
        health=health,
        account=account,
        meta=meta,
        decision=decision,
        engine_state=engine_state,
    )
    held = [str(holding.get("symbol", "")) for holding in card.get("holdings", [])]
    vols = _crypto_realized_vols(held)
    for holding in card.get("holdings", []):
        holding["realized_vol_22d"] = vols.get(str(holding.get("symbol", "")))
    return card


def _dashboard_ledger_risk() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Active safety incidents + latest risk-state row for the account."""
    account_id = _dashboard_account_id()
    connection = _dashboard_readonly_connection()
    if connection is None:
        return [], None
    try:
        incident_rows = connection.execute(
            """
            SELECT reason, scope, activated_at, updated_at
            FROM safety_incidents
            WHERE account_id = ?
            ORDER BY activated_at DESC, id DESC
            """,
            (account_id,),
        ).fetchall()
        risk_rows = connection.execute(
            """
            SELECT session_date, payload, updated_at
            FROM risk_state
            WHERE account_id = ?
            ORDER BY session_date DESC, updated_at DESC LIMIT 1
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error:
        return [], None
    finally:
        _dashboard_release(connection)
    incidents = [
        {
            "reason": str(row[0]),
            "scope": str(row[1]),
            "activated_at": str(row[2]),
            "updated_at": str(row[3]),
        }
        for row in incident_rows
    ]
    risk_state: dict[str, Any] | None = None
    if risk_rows:
        try:
            payload = json.loads(str(risk_rows[0][1]))
        except (TypeError, ValueError):
            payload = {}
        risk_state = {
            "session_date": str(risk_rows[0][0]),
            "updated_at": str(risk_rows[0][2]),
            **{str(key): value for key, value in dict(payload).items()},
        }
    return incidents, risk_state


def _dashboard_risk(cfg: Any, health: dict[str, Any] | None, account: dict[str, Any]) -> dict[str, Any]:
    """Risk panel: exposure, buffers, policies, latch, incidents, epoch."""
    equity = max(float(account.get("equity") or 0.0), 1e-9)
    cash = float(account.get("cash") or 0.0)
    gross = float(account.get("gross_exposure") or 0.0)
    positions = account.get("positions", [])
    equity_value = sum(float(p.get("market_value") or 0.0) for p in positions if p.get("market") == "equity")
    crypto_value = sum(float(p.get("market_value") or 0.0) for p in positions if p.get("market") == "crypto")
    daily_pnl = float(account.get("daily_pnl") or 0.0)
    drawdown = float(account.get("drawdown") or 0.0)
    limits = getattr(cfg, "risk", None) if cfg is not None else None
    incidents, risk_state = _dashboard_ledger_risk()
    return {
        "gross_exposure_pct": (gross / equity) * 100,
        "gross_exposure_limit_pct": (float(getattr(limits, "max_gross_exposure_pct", 0.60) or 0.0) * 100),
        "cash_pct": (cash / equity) * 100,
        "cash_buffer_limit_pct": (float(getattr(limits, "cash_buffer_pct", 0.40) or 0.0) * 100),
        "equity_exposure_pct": (equity_value / equity) * 100,
        "crypto_exposure_pct": (crypto_value / equity) * 100,
        "crypto_exposure_limit_pct": (float(getattr(limits, "max_crypto_exposure_pct", 0.10) or 0.0) * 100),
        "max_position_pct": (float(getattr(limits, "max_position_pct", 0.10) or 0.0) * 100),
        "max_positions": int(getattr(limits, "max_positions", 0) or 0),
        "crypto_positions": sum(1 for p in positions if p.get("market") == "crypto"),
        "max_crypto_positions": int(getattr(limits, "max_crypto_positions", 0) or 0),
        "daily_pnl": daily_pnl,
        "daily_loss_limit_pct": (float(getattr(limits, "daily_loss_limit_pct", 0.005) or 0.0) * 100),
        "drawdown": drawdown,
        "max_drawdown_pct": (float(getattr(limits, "max_drawdown_pct", 0.05) or 0.0) * 100),
        "entry_risk_latch": dict((health or {}).get("entry_risk_latch") or {}),
        "circuit_breaker": (health or {}).get("circuit_breaker_reason"),
        "safety_incidents": incidents,
        "risk_epoch": risk_state,
    }


def _fill_expected_vs_actual(order: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any] | None:
    """Per-order expected-vs-actual slippage telemetry; None on degrade."""
    try:
        from parayabuck.execution.telemetry import expected_vs_actual
    except Exception:
        return None
    try:
        return dict(expected_vs_actual(order, [fill]))
    except Exception as exc:
        log.debug("expected_vs_actual comparison unavailable: %s", exc)
        return None


def _dashboard_execution() -> dict[str, Any]:
    """Execution panel: open orders + recent fills with decision lineage."""
    account_id = _dashboard_account_id()
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {
            "open_orders": [],
            "recent_fills": [],
            "last_decision": None,
            "reconciliation_anomalies": [],
        }
    order_rows: list[Any] = []
    fill_rows: list[Any] = []
    anomaly_rows: list[Any] = []
    try:
        from parayabuck.portfolio.runtime_service import TERMINAL_ORDER_STATUSES_SQL

        order_rows = connection.execute(
            f"""
            SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                   o.status, o.reference_price, o.created_at,
                   d.decision_timestamp, d.strategy_name
            FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ?
              AND o.status NOT IN ({TERMINAL_ORDER_STATUSES_SQL})
            ORDER BY o.created_at DESC LIMIT 25
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard open-orders query skipped: %s", exc)
    try:
        fill_rows = connection.execute(
            """
            SELECT f.activity_id, f.client_order_id, o.symbol, o.market, o.side,
                   f.quantity, f.price, f.fee, f.slippage, o.reference_price,
                   f.realized_pnl, f.unrealized_pnl, f.occurred_at,
                   d.decision_timestamp, d.strategy_name,
                   o.quantity, o.status, o.created_at
            FROM portfolio_fills f
            JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ?
            ORDER BY f.occurred_at DESC LIMIT 25
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard recent-fills query skipped: %s", exc)
    try:
        anomaly_rows = connection.execute(
            """
            SELECT symbol, market, quantity, kind, note, created_at
            FROM portfolio_ownership_adjustments
            WHERE account_id = ?
            ORDER BY created_at DESC LIMIT 10
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard reconciliation-anomaly query skipped: %s", exc)
    finally:
        _dashboard_release(connection)

    def _lineage(decision_timestamp: object, strategy_name: object) -> dict[str, Any]:
        return {
            "decision_timestamp": (str(decision_timestamp) if decision_timestamp else None),
            "strategy": str(strategy_name) if strategy_name else None,
        }

    open_orders = [
        {
            "client_order_id": str(row[0]),
            "symbol": str(row[1]),
            "market": str(row[2]),
            "side": str(row[3]),
            "quantity": float(row[4] or 0.0),
            "status": str(row[5]),
            "reference_price": float(row[6]) if row[6] is not None else None,
            "created_at": str(row[7]),
            "lineage": _lineage(row[8], row[9]),
        }
        for row in order_rows
    ]
    recent_fills = []
    for row in fill_rows:
        price = float(row[6] or 0.0)
        reference = float(row[9]) if row[9] is not None else None
        direction = 1.0 if str(row[4]).lower() == "buy" else -1.0
        slippage_bps = ((price - reference) / reference) * 10000 * direction if reference and reference > 0 else None
        fill_entry = {
            "activity_id": str(row[0]),
            "client_order_id": str(row[1]),
            "symbol": str(row[2]),
            "market": str(row[3]),
            "side": str(row[4]),
            "quantity": float(row[5] or 0.0),
            "price": price,
            "fee": float(row[7] or 0.0),
            "slippage": float(row[8] or 0.0),
            "reference_price": reference,
            "slippage_bps": slippage_bps,
            "realized_pnl": float(row[10]) if row[10] is not None else None,
            "unrealized_pnl": float(row[11]) if row[11] is not None else None,
            "occurred_at": str(row[12]),
            "lineage": _lineage(row[13], row[14]),
        }
        fill_entry["expected_vs_actual"] = _fill_expected_vs_actual(
            {
                "client_order_id": str(row[1]),
                "symbol": str(row[2]),
                "market": str(row[3]),
                "side": str(row[4]),
                "quantity": float(row[15] or 0.0),
                "reference_price": reference,
                "created_at": row[17],
                "status": str(row[16]),
            },
            {
                "quantity": float(row[5] or 0.0),
                "price": price,
                "fee": float(row[7] or 0.0),
                "occurred_at": str(row[12]),
            },
        )
        recent_fills.append(fill_entry)
    reconciliation_anomalies = [
        {
            "symbol": str(row[0]),
            "market": str(row[1]),
            "quantity": float(row[2] or 0.0),
            "kind": str(row[3]),
            "note": str(row[4] or ""),
            "created_at": str(row[5]),
        }
        for row in anomaly_rows
    ]
    return {
        "open_orders": open_orders,
        "recent_fills": recent_fills,
        "reconciliation_anomalies": reconciliation_anomalies,
    }


def _dashboard_universe(cfg: Any, health: dict[str, Any] | None) -> dict[str, Any]:
    """Universe panel: discovered assets, pools, scanner freshness, pins."""
    if cfg is None:
        return {"unavailable": True, "reason": "config unavailable"}
    refresh = dict((health or {}).get("universe_refresh") or {})
    status_map = dict(refresh.get("status") or {})
    errors = dict(refresh.get("errors") or {})
    market_health = dict((health or {}).get("markets") or {})
    result: dict[str, Any] = {}
    for market_key, active_path, candidate_path in (
        (
            "equity",
            cfg.universe.equity.active_universe_path,
            cfg.universe.equity.candidate_universe_path,
        ),
        ("crypto", cfg.universe.crypto.active_universe_path, None),
    ):
        active: dict[str, Any] = {}
        candidates: dict[str, Any] = {}
        active.update(_dashboard_load_json(active_path))
        if candidate_path is not None:
            candidates.update(_dashboard_load_json(candidate_path))
        shortlist = candidates.get("shortlist_symbols")
        candidate_symbols = active.get("candidate_symbols", ())
        candidate_items = (
            candidates.get("candidates", candidates.get("symbols", candidates))
            if isinstance(candidates, dict)
            else candidates
        )
        broad_discovered = len(candidate_items) if isinstance(candidate_items, (list, dict)) else 0
        eligible_pool = (
            len(shortlist)
            if market_key == "equity" and isinstance(shortlist, list)
            else len(candidate_symbols)
            if isinstance(candidate_symbols, (list, tuple))
            else 0
        )
        selected = active.get("observation_set", active.get("selected_symbols", ()))
        market = dict(market_health.get(market_key) or {})
        pinned = list(market.get("managed_pins") or ())
        if market_key == "crypto" and not pinned:
            pinned = list(getattr(cfg.universe.crypto, "pinned_symbols", ()) or ())
        result[market_key] = {
            "broad_discovered_count": broad_discovered,
            "eligible_pool_size": eligible_pool,
            "observation_set_size": len(selected),
            "observation_set": list(selected),
            "pinned_symbols": pinned,
            "last_refresh_at": active.get("selection_timestamp"),
            "data_timestamp": active.get("data_timestamp"),
            "scanner_status": status_map.get(market_key) or active.get("status"),
            "scanner_version": active.get("scanner_version"),
            "refresh_errors": dict(errors.get(market_key) or {}),
            "next_refresh_at": refresh.get("next_refresh_at"),
            "eliminated_count": len(active.get("eliminated", {})),
        }
    return result


def _dashboard_database_state() -> dict[str, Any]:
    path = _dashboard_db_path()
    try:
        size = path.stat().st_size if path.exists() else None
    except OSError:
        size = None
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": size,
        "wal_file_present": Path(f"{path}-wal").exists(),
    }


def _dashboard_restart_count() -> int | None:
    """Fallback restart count when the runtime_metrics payload omits it.

    Delegates the formula to ``monitoring.metrics.runtime_session_restart_count``
    so the dashboard can never drift from the authoritative accounting.
    """
    connection = _dashboard_readonly_connection()
    if connection is None:
        return 0
    try:
        # Lazy import mirrors the other monitoring.metrics call sites.
        from parayabuck.monitoring.metrics import runtime_session_restart_count

        return runtime_session_restart_count(connection)
    except (ImportError, sqlite3.Error):
        return 0
    finally:
        _dashboard_release(connection)


def _dashboard_health(
    cfg: Any,
    health: dict[str, Any] | None,
    account: dict[str, Any],
    *,
    runtime_metrics_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """System health panel: workers, queue, freshness, reconciliation, DB."""
    workers = dict((health or {}).get("workers") or {})
    heartbeats = [str(worker.get("last_heartbeat")) for worker in workers.values() if worker.get("last_heartbeat")]
    refresh = dict((health or {}).get("universe_refresh") or {})
    engine_cfg = getattr(cfg, "engine", None) if cfg is not None else None
    if runtime_metrics_payload is None:
        runtime_metrics_payload = _runtime_metrics_payload(_dashboard_account_id())
    restart_count = runtime_metrics_payload.get("restart_count")
    if restart_count is None:
        restart_count = _dashboard_restart_count()
    return {
        "engine_state": _manager_engine_state(),
        "healthy": (health or {}).get("healthy"),
        "workers": workers,
        "restart_count": restart_count,
        "queue": {
            "size": (health or {}).get("queue_size"),
            "age_seconds": (health or {}).get("queue_age_seconds"),
            "dropped_events": (health or {}).get("dropped_events"),
            "coalesced_events": (health or {}).get("coalesced_events"),
        },
        "markets": dict((health or {}).get("markets") or {}),
        "account_freshness": {
            "age_seconds": account.get("age_seconds"),
            "stale": account.get("stale"),
            "source": account.get("source"),
            "maximum_age_for_entry_seconds": getattr(engine_cfg, "maximum_account_age_for_entry_seconds", None),
        },
        "reconciliation": {
            "last_at": (health or {}).get("last_reconciliation_at"),
            "failed": (health or {}).get("circuit_breaker_reason") == "reconciliation_failed",
        },
        "scanner": {
            "last_refresh_at": refresh.get("last_refresh_at"),
            "next_refresh_at": refresh.get("next_refresh_at"),
            "status": dict(refresh.get("status") or {}),
            "errors": dict(refresh.get("errors") or {}),
            "retry_at": dict(refresh.get("retry_at") or {}),
            "retry_attempt": dict(refresh.get("retry_attempt") or {}),
        },
        "database": _dashboard_database_state(),
        "last_heartbeat": max(heartbeats) if heartbeats else None,
        "entry_risk_latch": dict((health or {}).get("entry_risk_latch") or {}),
    }


def _dashboard_explanations(decisions: dict[str, dict[str, Any]], account: dict[str, Any]) -> dict[str, Any]:
    """Per-holding structured reasons from persisted decision metadata."""
    result: dict[str, Any] = {}
    for market_key in ("equity", "crypto"):
        decision = decisions.get(market_key)
        target = dict((decision or {}).get("target") or {})
        metadata = dict(target.get("metadata") or {})
        weights = {str(weight.get("symbol")): weight for weight in target.get("weights", [])}
        selected = [str(symbol) for symbol in metadata.get("selected_symbols", ())]
        qualified = int(metadata.get("qualified_symbols") or 0)
        holdings: list[dict[str, Any]] = []
        for position in account.get("positions", []):
            if position.get("market") != market_key:
                continue
            symbol = str(position.get("symbol", ""))
            weight = weights.get(symbol)
            rank = selected.index(symbol) + 1 if symbol in selected else None
            if weight is not None:
                reason = str(weight.get("reason") or "target_weight")
            elif rank is not None:
                reason = "ranked candidate not in the latest target"
            else:
                reason = "held outside the latest decision (external or pre-decision)"
            holdings.append(
                {
                    "symbol": symbol,
                    "reason": reason,
                    "rank": rank,
                    "of_qualified": qualified if rank is not None else None,
                    "target_weight_pct": (float((weight or {}).get("weight") or 0.0) * 100 if weight else 0.0),
                    "decision_timestamp": (decision or {}).get("timestamp"),
                    "decision_strategy": (decision or {}).get("strategy_name"),
                    "hypothesis": metadata.get("hypothesis"),
                    "universe_version": target.get("universe_version"),
                    "rebalance_reason": target.get("rebalance_reason"),
                    "risk_multiplier": target.get("risk_multiplier"),
                }
            )
        result[market_key] = {
            "strategy_name": (decision or {}).get("strategy_name"),
            "last_decision_at": (decision or {}).get("timestamp"),
            "qualified_symbols": qualified,
            "selected_symbols": selected,
            "hypothesis": metadata.get("hypothesis"),
            "risk_off_detail": metadata.get("risk_off_detail"),
            "holdings": holdings,
        }
    return result


def _telemetry_payload() -> dict[str, Any]:
    """Execution/forward/cost telemetry for the dashboard; degrades explicitly."""
    try:
        from parayabuck.execution.telemetry import (
            cost_calibration,
            execution_metrics,
            forward_metrics,
        )
    except ImportError:
        return {
            "unavailable": True,
            "reason": "parayabuck.execution.telemetry is not available yet",
        }
    except Exception as exc:
        log.warning("Dashboard telemetry import failed: %s", exc)
        return {"unavailable": True, "reason": f"telemetry import error: {exc}"}
    db_path = _dashboard_db_path()
    connection = _dashboard_readonly_connection()
    try:
        if connection is not None:
            # Share the payload-build connection across all three reports.
            # Fall back to per-report connections when the module predates
            # the optional _connection parameter (or is a test stub).
            try:
                execution = execution_metrics(db_path, _connection=connection)
                forward = forward_metrics(db_path, _connection=connection)
                calibration = cost_calibration(db_path, _connection=connection)
            except TypeError:
                execution = execution_metrics(db_path)
                forward = forward_metrics(db_path)
                calibration = cost_calibration(db_path)
        else:
            execution = execution_metrics(db_path)
            forward = forward_metrics(db_path)
            calibration = cost_calibration(db_path)
        return {
            "unavailable": False,
            "execution_metrics": execution,
            "forward_metrics": forward,
            "cost_calibration": calibration,
        }
    except Exception as exc:
        log.warning("Dashboard telemetry failed: %s", exc)
        return {"unavailable": True, "reason": f"telemetry error: {exc}"}
    finally:
        _dashboard_release(connection)


def _runtime_metrics_payload(account_id: str) -> dict[str, Any]:
    """Authoritative runtime metrics (restarts, sessions) for the dashboard."""
    try:
        from parayabuck.monitoring.metrics import runtime_metrics

        return dict(runtime_metrics(_dashboard_db_path(), account_id=account_id))
    except ImportError:
        return {
            "unavailable": True,
            "reason": "parayabuck.monitoring.metrics is not available",
        }
    except Exception as exc:
        log.warning("Dashboard runtime metrics failed: %s", exc)
        return {"unavailable": True, "reason": f"runtime metrics error: {exc}"}


# Display-only dashboard snapshot cache: the browser polls all 9 dashboard
# endpoints every ~2s and every call rebuilds the ENTIRE payload (config,
# ledger reads, telemetry, broker-snapshot panel). Within a short TTL the
# payload is served from this cache, so a poll tick costs one rebuild instead
# of nine. The cache is keyed by engine state and settings-file version, and
# never feeds trading state, decisions, or the runtime (display-only).
_DASHBOARD_SNAPSHOT_TTL_SECONDS = 1.5
_dashboard_snapshot_lock = threading.Lock()
_dashboard_snapshot: tuple[float, str, tuple[str, int] | None, dict[str, Any]] | None = None


def _dashboard_snapshot_reset() -> None:
    """Clear the display-only payload cache (benchmarks/tests)."""
    global _dashboard_snapshot
    with _dashboard_snapshot_lock:
        _dashboard_snapshot = None


def _dashboard_payload() -> dict[str, Any]:
    """Full aggregated dashboard payload; each panel degrades on failure."""
    global _dashboard_snapshot
    engine_state = _manager_engine_state()
    config_key = _dashboard_config_key()
    now = time.monotonic()
    with _dashboard_snapshot_lock:
        cached = _dashboard_snapshot
        if cached is not None and now < cached[0] and cached[1] == engine_state and cached[2] == config_key:
            return cached[3]
    payload = _build_dashboard_payload(engine_state)
    with _dashboard_snapshot_lock:
        _dashboard_snapshot = (
            time.monotonic() + _DASHBOARD_SNAPSHOT_TTL_SECONDS,
            engine_state,
            config_key,
            payload,
        )
    return payload


def _build_dashboard_payload(engine_state: str) -> dict[str, Any]:
    """Assemble the full dashboard payload (called on snapshot-cache miss)."""
    with _dashboard_shared_connection():
        return _build_dashboard_payload_uncached(engine_state)


def _build_dashboard_payload_uncached(engine_state: str) -> dict[str, Any]:
    """Assemble all panels (single shared read-only connection active)."""
    cfg = _dashboard_config()
    health = _dashboard_runtime_health()
    account = _dashboard_account()
    account_id = _dashboard_account_id()
    decisions = _dashboard_market_decisions(account_id)
    # runtime_metrics is consumed by BOTH the health panel and the metrics
    # panel; compute it once per build instead of twice.
    runtime_metrics_payload = _runtime_metrics_payload(account_id)
    meta: dict[str, Any] = {}
    if cfg is not None:
        try:
            meta = _strategy_metadata(cfg)
        except Exception:
            meta = {}
    equity_meta = dict(meta.get("equity") or {})
    crypto_meta = dict(meta.get("crypto") or {})
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "engine_state": engine_state,
        "engine_error": getattr(manager, "last_engine_error", None),
        "mode": _dashboard_safe_panel("mode", lambda: _dashboard_mode(cfg)),
        "account": {
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "gross_exposure": account.get("gross_exposure"),
            "daily_pnl": account.get("daily_pnl"),
            "realized_pnl": account.get("realized_pnl"),
            "unrealized_pnl": account.get("unrealized_pnl"),
            "total_pnl": (float(account.get("realized_pnl") or 0.0) + float(account.get("unrealized_pnl") or 0.0))
            if account.get("realized_pnl") is not None or account.get("unrealized_pnl") is not None
            else None,
            "drawdown": account.get("drawdown"),
            "account_suffix": account.get("account_suffix"),
            "snapshot_at": account.get("snapshot_at"),
            "age_seconds": account.get("age_seconds"),
            "stale": account.get("stale"),
            "source": account.get("source"),
            "last_reconciliation_at": (health or {}).get("last_reconciliation_at"),
        },
        "equity": _dashboard_safe_panel(
            "equity",
            lambda: _dashboard_market_card(
                "equity",
                cfg=cfg,
                health=health,
                account=account,
                meta=equity_meta,
                decision=decisions.get("equity"),
                engine_state=engine_state,
            ),
        ),
        "crypto": _dashboard_safe_panel(
            "crypto",
            lambda: _dashboard_crypto_card(
                cfg=cfg,
                health=health,
                account=account,
                meta=crypto_meta,
                decision=decisions.get("crypto"),
                engine_state=engine_state,
            ),
        ),
        "risk": _dashboard_safe_panel("risk", lambda: _dashboard_risk(cfg, health, account)),
        "execution": _dashboard_safe_panel("execution", _dashboard_execution),
        "universe": _dashboard_safe_panel("universe", lambda: _dashboard_universe(cfg, health)),
        "health": _dashboard_safe_panel(
            "health",
            lambda: _dashboard_health(cfg, health, account, runtime_metrics_payload=runtime_metrics_payload),
        ),
        "explanations": _dashboard_safe_panel(
            "explanations",
            lambda: _dashboard_explanations(decisions, account),
        ),
        "metrics": _dashboard_metrics_payload(account_id, runtime_metrics_payload=runtime_metrics_payload),
    }


def _dashboard_metrics_payload(
    account_id: str,
    *,
    runtime_metrics_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "telemetry": _telemetry_payload(),
        "runtime_metrics": (
            runtime_metrics_payload if runtime_metrics_payload is not None else _runtime_metrics_payload(account_id)
        ),
    }


def _dashboard_account() -> dict[str, Any]:
    fetch = getattr(manager, "fetch_account_snapshot", None)
    if fetch is not None:
        try:
            return dict(fetch())
        except Exception as exc:
            log.warning("Dashboard account snapshot failed: %s", exc)
    return {
        "equity": 0.0,
        "cash": 0.0,
        "daily_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "drawdown": 0.0,
        "gross_exposure": 0.0,
        "positions": [],
        "account_suffix": "",
        "snapshot_at": None,
        "age_seconds": None,
        "stale": True,
        "source": "unavailable",
    }


@app.get("/api/dashboard/overview")
async def dashboard_overview() -> dict[str, Any]:
    return await asyncio.to_thread(_dashboard_payload)


async def _dashboard_panel(key: str) -> dict[str, Any]:
    """One dashboard panel from the shared snapshot payload."""
    payload: dict[str, Any] = await asyncio.to_thread(_dashboard_payload)
    return cast(dict[str, Any], payload[key])


@app.get("/api/equity")
async def dashboard_equity() -> dict[str, Any]:
    return await _dashboard_panel("equity")


@app.get("/api/crypto")
async def dashboard_crypto() -> dict[str, Any]:
    return await _dashboard_panel("crypto")


@app.get("/api/risk")
async def dashboard_risk() -> dict[str, Any]:
    return await _dashboard_panel("risk")


@app.get("/api/execution")
async def dashboard_execution() -> dict[str, Any]:
    return await _dashboard_panel("execution")


@app.get("/api/universe")
async def dashboard_universe() -> dict[str, Any]:
    return await _dashboard_panel("universe")


@app.get("/api/health")
async def dashboard_health() -> dict[str, Any]:
    return await _dashboard_panel("health")


@app.get("/api/explanations")
async def dashboard_explanations() -> dict[str, Any]:
    return await _dashboard_panel("explanations")


@app.get("/api/metrics")
async def dashboard_metrics() -> dict[str, Any]:
    return await _dashboard_panel("metrics")


# WebSockets
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    ticket_valid = manager.consume_ws_ticket(ticket, "stream")
    if not _trusted_browser_origin(websocket.headers.get("origin")) or not ticket_valid:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            account_data = await asyncio.to_thread(manager.fetch_account_snapshot)
            # current_engine_state may call runtime.health() (ledger/broker
            # access) and is_paper_mode re-parses the config on change; both
            # block and must not run on the event loop.
            state, paper = await asyncio.to_thread(lambda: (manager.current_engine_state(), manager.is_paper_mode()))
            status_data = {"state": state, "paper": paper}
            await websocket.send_json({"type": "update", "account": account_data, "status": status_data})
            # The browser never sends messages; this paces the loop and,
            # unlike asyncio.sleep, detects a dead peer: writing to a lost
            # connection only logs "socket.send() raised exception." in
            # asyncio instead of raising, so receive() is the only way to
            # notice the disconnect.
            with suppress(TimeoutError):
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.debug("Stream websocket closed with error: %s", exc)


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    ticket_valid = manager.consume_ws_ticket(ticket, "logs")
    if not _trusted_browser_origin(websocket.headers.get("origin")) or not ticket_valid:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    queue_ref = (queue, asyncio.get_running_loop())
    with manager._log_queues_lock:
        manager.log_queues.add(queue_ref)

    try:
        while True:
            # Race the next log entry against a receive so a disconnected
            # browser is detected instead of writing to a dead socket.
            get_task = asyncio.ensure_future(queue.get())
            recv_task = asyncio.ensure_future(websocket.receive_text())
            try:
                done, _pending = await asyncio.wait({get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (get_task, recv_task):
                    if not task.done():
                        task.cancel()
            if recv_task in done:
                recv_task.result()  # raises WebSocketDisconnect when the peer is gone
            if get_task in done:
                await websocket.send_json(get_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.debug("Logs websocket closed with error: %s", exc)
    finally:
        with manager._log_queues_lock:
            manager.log_queues.discard(queue_ref)


# ---------------------------------------------------------------------------
# Factory compatibility shim — Mission 6 Web Platform extraction.
# ``parayabuck.web.application.create_app`` is the new composition root.
# ``parayabuck.web.server.app`` is kept as a re-export of the factory-built
# app so ``from parayabuck.web.server import app`` and
# ``from parayabuck.web.application import app`` expose the same instance
# after the routers have been extracted. The shim is lazy and never breaks
# import of the legacy monolith: if the factory cannot be imported (e.g.
# missing optional router), the original monolith ``app`` stays in place.
# WebSocket and middleware preserve the same Host/Origin/token/ticket checks
# as the factory's security and lifespan modules.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - import-time compatibility, exercised via TestClient
    from parayabuck.web.application import app as _factory_app  # type: ignore
    from parayabuck.web.application import create_app as _factory_create_app  # type: ignore  # noqa: F401

    # Keep the monolith's routes for direct function-call tests (server.healthz
    # etc.) but expose the factory app as the ASGI entrypoint. The two apps
    # share the same managers via WebState, so their behaviour is identical.
    _legacy_app = app  # type: ignore[has-type]
    if _factory_app is not None:
        app = _factory_app  # type: ignore[no-redef]
except Exception as _factory_shim_error:  # pragma: no cover
    import logging as _shim_logging

    _shim_logging.getLogger("parayabuck.web.server").debug("Factory shim not active: %s", _factory_shim_error)
