from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.logger import get_logger
from parayabuck.universe import validate_active_crypto_universe, validate_active_universe
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.web.server")


class BacktestManager:
    """Runs the production-strategy backtest CLI in a background thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
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
        with self._lock:
            return self.state == "running" and self._thread is not None and self._thread.is_alive()

    def start(self, payload: dict[str, Any]) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Bir backtest zaten çalışıyor; bitmesini bekleyin")
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
            self._thread = self._thread_factory(
                target=self._run, args=(argv,), daemon=True, name="web-backtest"
            )
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
            argv += ["--param", f"{name}={json.dumps(value)}"]
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
                with self.owner._lock:
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
                    error = (
                        f"Rapor üretilmedi: {report_path} "
                        "(CLI hatasız çıktı ama rapor dosyası yazılmadı)"
                    )
                else:
                    raw = json.loads(report_path.read_text(encoding="utf-8"))
                    summary = raw.get("summary")
                if universe_path.exists():
                    universe = json.loads(universe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                error = f"Rapor okunamadı: {exc}"
        with self._lock:
            self.state = "error" if error else "done"
            self.error = error
            self.summary = summary
            self.universe = universe
            self.finished_at = datetime.now(UTC).isoformat()
        log.info("Web backtest finished: %s (%s)", self.run_id, self.state)

    def status(self) -> dict[str, Any]:
        is_running = self.is_running
        with self._lock:
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
        self.log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = (
            set()
        )
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
        self._paper_mode_cache: tuple[int, bool] | None = None
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
        return True

    def get_credentials(self) -> tuple[str | None, str | None]:
        load_dotenv(self.env_path, override=True)
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        return key, secret

    def should_auto_start(self) -> bool:
        """Auto-start configured paper engines without weakening live safeguards."""
        try:
            raw = (
                yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
                if self.config_path.exists()
                else {}
            )
            app_config = (raw or {}).get("app", {})
            runtime_config = (raw or {}).get("runtime", {})
            legacy_live = (
                "mode" not in runtime_config and (raw or {}).get("alpaca", {}).get("paper") is False
            )
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
            if (
                self.engine_state == "running"
                and self.runtime is not None
                and not self.runtime.is_running
            ):
                self.engine_state = "error"
            return self.engine_state

    def has_active_runtime(self) -> bool:
        with self._state_lock:
            return self.runtime is not None or self.engine_state in {
                "starting",
                "running",
                "warming_up",
                "degraded",
                "halted",
                "stopping",
                "stop_failed",
            }

    def _validated_settings_payload(self, payload: SettingsPayload) -> ValidatedSettings:
        """Validate a settings save and build the full settings.yaml mapping.

        Every rejection path raises ``ValueError`` BEFORE any engine is
        stopped or any file is written.
        """
        if self.engine_state in {"starting", "stopping"}:
            raise ValueError("Bot is currently changing state; wait a moment and try again")

        # The web server and trading engine have separate lifecycles. Runtime
        # settings (timeframe and risk limits) only require the
        # trading engine to restart; shutting down the whole application also
        # removes the UI and is both confusing and unnecessary.
        current_paper = self.is_paper_mode()
        entering_live = current_paper and not payload.paper_mode
        if entering_live and payload.restart_engine:
            raise ValueError(
                "Saving live mode requires restart_engine=false; start live separately after arming"
            )
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
        api_key = payload.api_key.strip() or current_key or ""
        secret_key = payload.secret_key.strip() or current_secret or ""
        if any(char in api_key + secret_key for char in "\r\n"):
            raise ValueError("Credentials may not contain newlines")
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

        cfg_raw.setdefault("runtime", {})["mode"] = "paper" if payload.paper_mode else "live"
        cfg_raw.setdefault("runtime", {})["execution_timeframe"] = payload.execution_timeframe
        live = cfg_raw.setdefault("live", {})
        live["enabled"] = not payload.paper_mode
        live["hard_dollar_cap"] = payload.live_dollar_cap
        live["operator_approval_path"] = payload.live_operator_approval_path
        strategy = cfg_raw.setdefault("strategy", {})
        risk = cfg_raw["risk"]
        risk["daily_loss_limit_pct"] = payload.daily_loss_limit_pct / 100
        risk["max_drawdown_pct"] = payload.max_drawdown_pct / 100
        strategy.setdefault("equity", {})["enabled"] = payload.equity_trading_enabled
        strategy.setdefault("crypto", {})["enabled"] = payload.crypto_trading_enabled
        cfg_raw["app"] = {
            "host": payload.host.strip() or "127.0.0.1",
            "port": payload.port,
            "open_browser": payload.open_browser,
            "auto_start_engine": old_app.get("auto_start_engine", True),
            "log_level": payload.log_level.upper(),
        }

        # Validate the complete temporary configuration before persistence.
        temporary_config = self.config_path.with_name(f".{self.config_path.name}.validate")
        _atomic_write(
            temporary_config,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )
        try:
            if cfg_raw.get("symbols") or cfg_raw.get("universe"):
                load_config(str(temporary_config))
        finally:
            temporary_config.unlink(missing_ok=True)
        credentials_changed = (api_key, secret_key) != (
            current_key or "",
            current_secret or "",
        )
        if entering_live and credentials_changed:
            self.invalidate_live_arming()
            raise ValueError(
                "Credentials changed; request and type a fresh live confirmation first"
            )
        if entering_live:
            token = self._live_arm_token
            candidate_digest = self._digest_config(cfg_raw, paper=False)
            if token is None or token.config_digest != candidate_digest:
                self.invalidate_live_arming()
                raise ValueError(
                    "Settings changed after live verification; confirm the live account again"
                )
        return ValidatedSettings(
            cfg_raw=cfg_raw,
            old_app=old_app,
            api_key=api_key,
            secret_key=secret_key,
            credentials_changed=credentials_changed,
        )

    def start_engine(self) -> None:
        with self._state_lock:
            if self.runtime is not None or self.engine_state in {
                "starting",
                "running",
                "warming_up",
                "degraded",
                "halted",
                "stopping",
                "stop_failed",
            }:
                raise ValueError(f"Engine is already {self.engine_state}")
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
            warning = _tracked_secret_warning(Path.cwd())
            if warning:
                raise ValueError(warning)
            cfg = load_config(str(self.config_path))
            live_mode = cfg.runtime.mode.value == "live"
            if live_mode and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES "
                    "before starting the application."
                )
            if live_mode:
                self._require_valid_live_arm(key, secret, consume=True)
            from parayabuck.runtime import EngineRuntime

            runtime = EngineRuntime(api_key=key, secret_key=secret, config=cfg)
            runtime.start()
            with self._state_lock:
                # A stop request issued while the engine was starting wins:
                # shut the fresh runtime down instead of leaving it running
                # after an explicit stop.
                interrupted = self.engine_state == "stopping"
                if not interrupted:
                    self.runtime = runtime
                    self.broker = runtime.broker
                    self.engine_state = "running"
            if interrupted:
                raise RuntimeError("Engine start was interrupted by a stop request")
            if live_mode:
                self.invalidate_live_arming()
            log.info("ParayaBuck engine started from Web UI")

        except Exception as exc:
            if runtime is not None:
                try:
                    runtime.stop()
                except Exception:
                    log.exception("Cleanup after failed engine start")
            with self._state_lock:
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

    def reset_safety_lock(self) -> None:
        with self._state_lock:
            runtime = self.runtime
            state = self.current_engine_state()
        if runtime is None or state not in {"halted", "degraded"}:
            raise ValueError("A halted runtime is required to reset the safety lock")
        runtime.reset_safety_lock()

    def fetch_account_snapshot(self) -> dict[str, Any]:
        with self._account_lock:
            if (
                self.last_account is not None
                and time.monotonic() - self._last_account_monotonic
                < self._account_cache_ttl_seconds
            ):
                age = max(0.0, time.monotonic() - self._last_account_monotonic)
                return {
                    **self.last_account,
                    "age_seconds": age,
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
                if (
                    runtime is not None
                    and hasattr(runtime, "ledger")
                    and bool(self.broker.account_id)
                ):
                    managed = runtime.ledger.managed_for_account(self.broker.account_id)
                positions_list = []
                for p in acct.positions:
                    if not p.is_flat:
                        owned = managed.get(p.symbol)
                        positions_list.append(
                            {
                                "symbol": p.symbol,
                                "market": p.market.value,
                                "direction": "LONG" if p.qty > 0 else "SHORT",
                                "qty": abs(p.qty),
                                "avg_price": p.avg_price,
                                "current_price": p.current_price,
                                "market_value": p.market_value,
                                "unrealized_pnl": p.unrealized_pnl,
                                "ownership": p.ownership,
                                "protection_state": (
                                    owned.protection_state.value if owned is not None else None
                                ),
                                "stop_price": (
                                    owned.current_stop or owned.original_stop
                                    if owned is not None
                                    else None
                                ),
                                "target_price": owned.original_target
                                if owned is not None
                                else None,
                                "stop_order_state": owned.stop_state if owned is not None else None,
                                "target_exit_state": (
                                    owned.target_state if owned is not None else None
                                ),
                                "manual_intervention_required": bool(
                                    owned is not None
                                    and owned.status == "manual_intervention_required"
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
                    "gross_exposure": sum(
                        position.market_value for position in acct.positions if not position.is_flat
                    ),
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
        self._live_request_suffix = suffix
        self._live_armed = False
        self._live_arm_token = None
        return suffix

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
        self._live_arm_token = LiveArmToken(
            **{
                **pending.__dict__,
                "issued_at": now,
                "expires_at": now + timedelta(minutes=5),
            }
        )
        self._pending_live_arm = None
        self._live_armed = True

    def invalidate_live_arming(self) -> None:
        self._live_request_suffix = None
        self._live_armed = False
        self._pending_live_arm = None
        self._live_arm_token = None

    def _require_valid_live_arm(
        self, key: str, secret: str, *, consume: bool = False
    ) -> LiveArmToken:
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
            raise ValueError(
                "Live trading requires a fresh confirmation for these credentials and settings."
            )
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
        ticket = secrets.token_urlsafe(24)
        self._ws_tickets[ticket] = (kind, time.monotonic() + 30.0)
        return ticket

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
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
            "SECURITY CHECK FAILED: cannot list tracked files for secret scan (%s); "
            "the secret-key guard is inactive",
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


def _atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    atomic_write(path, content, mode)


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
    _atomic_write(path, "\n".join(lines).rstrip() + "\n", 0o600)


manager = EngineManager()


async def _auto_start_paper_engine() -> None:
    try:
        await asyncio.to_thread(manager.start_engine)
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
            try:
                loop.call_soon_threadsafe(_enqueue_log, queue, log_entry)
            except Exception:
                pass


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
    if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        return JSONResponse(status_code=403, content={"detail": "Untrusted Host header"})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _trusted_browser_origin(
        request.headers.get("origin")
    ):
        return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        request.headers.get("x-parayabuck-control-token", ""),
        manager.control_token,
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


# Pydantic Schemas
@dataclass(frozen=True)
class ValidatedSettings:
    """Validated settings-save result: the new config mapping and credentials."""

    cfg_raw: dict[str, Any]
    old_app: dict[str, Any]
    api_key: str
    secret_key: str
    credentials_changed: bool


class SettingsPayload(BaseModel):
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


class ModePayload(BaseModel):
    paper: bool


class LiveConfirmationPayload(BaseModel):
    phrase: str


class WebSocketTicketPayload(BaseModel):
    kind: Literal["stream", "logs"]


class BacktestPayload(BaseModel):
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


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness endpoint; runtime readiness remains visible in /api/status."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Configuration/runtime readiness, distinct from process liveness."""
    try:
        load_config(str(manager.config_path))
        config_valid = True
        config_error = None
    except Exception as exc:
        config_valid = False
        config_error = str(exc)
    runtime = getattr(manager, "runtime", None)
    health = runtime.health() if runtime is not None and hasattr(runtime, "health") else None
    # A runtime that exists (or is still transitioning) is expected to trade.
    # degraded/halted/stopping runtimes must NOT be reported ready just
    # because the config parses; /healthz already covers process liveness.
    runtime_expected = manager.has_active_runtime()
    trading_ready = bool(health and health.healthy)
    ready = config_valid and (not runtime_expected or trading_ready)
    return JSONResponse(
        {
            "ready": ready,
            "config_valid": config_valid,
            "trading_ready": trading_ready,
            "runtime_expected": runtime_expected,
            "error": config_error,
        },
        status_code=200 if ready else 503,
    )


@app.get("/api/control-token")
async def get_control_token() -> JSONResponse:
    response = JSONResponse({"control_token": manager.control_token})
    response.headers["Cache-Control"] = "no-store"
    return response


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
    except Exception:
        result["strategy_status"] = "configuration_error"
        # Mirror the universe-section failure state so consumers never see a
        # missing scanner key when the config cannot be loaded.
        result["scanner"] = {"last_run_at": None, "status": "error"}
    runtime = getattr(manager, "runtime", None)
    runtime_refresh: dict[str, Any] = {}
    if runtime is not None and hasattr(runtime, "health"):
        health = runtime.health()
        result["runtime"] = asdict(health)
        runtime_refresh = health.universe_refresh
        result["strategy_status"] = health.strategy_status
        repository = getattr(runtime, "_portfolio_repository", None)
        if repository is not None:
            result["operations"] = repository.operations_snapshot()
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
                json.loads(candidate_path.read_text(encoding="utf-8"))
                if candidate_path.exists()
                else []
            )
            active_payload = (
                json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else {}
            )
            active_crypto_payload = (
                json.loads(active_crypto_path.read_text(encoding="utf-8"))
                if active_crypto_path.exists()
                else {}
            )
            candidate_items = (
                candidate_payload.get(
                    "candidates",
                    candidate_payload.get("symbols", candidate_payload),
                )
                if isinstance(candidate_payload, dict)
                else candidate_payload
            )
            result["candidate_universe_size"] = (
                len(candidate_items) if isinstance(candidate_items, (list, dict)) else 0
            )
            equity_symbols, validated_scanner_status = validate_active_universe(cfg.universe.equity)
            crypto_symbols, validated_crypto_scanner_status = validate_active_crypto_universe(
                cfg.universe.crypto
            )
            if runtime is None:
                result["active_equity_symbol_count"] = len(equity_symbols)
                result["active_crypto_symbol_count"] = len(crypto_symbols)
            result["scanner"] = {
                "last_run_at": active_payload.get("selection_timestamp"),
                "status": runtime_refresh.get("status", {}).get("equity", validated_scanner_status),
                "equity_status": runtime_refresh.get("status", {}).get(
                    "equity", validated_scanner_status
                ),
                "crypto_status": runtime_refresh.get("status", {}).get(
                    "crypto", validated_crypto_scanner_status
                ),
                "equity_snapshot_status": validated_scanner_status,
                "crypto_snapshot_status": validated_crypto_scanner_status,
                "scanner_version": active_payload.get("scanner_version"),
                "crypto_last_run_at": active_crypto_payload.get("selection_timestamp"),
                "crypto_scanner_version": active_crypto_payload.get("scanner_version"),
                "next_refresh_at": runtime_refresh.get("next_refresh_at"),
                "refresh_errors": runtime_refresh.get("errors", {}),
                "equity_candidate_count": len(active_payload.get("candidate_symbols", ())),
                "equity_shortlist_count": len(
                    candidate_payload.get("shortlist_symbols", ())
                    if isinstance(candidate_payload, dict)
                    else ()
                ),
                "equity_sector_metadata_coverage": active_payload.get("sector_metadata_coverage"),
                "crypto_candidate_count": len(active_crypto_payload.get("candidate_symbols", ())),
            }
            result["universe"] = {
                "equity": {
                    "symbols": list(active_payload.get("selected_symbols", ())),
                    "scores": active_payload.get("score_breakdown", {}),
                    "eliminated": active_payload.get("eliminated", {}),
                },
                "crypto": {
                    "symbols": list(active_crypto_payload.get("selected_symbols", ())),
                    "scores": active_crypto_payload.get("score_breakdown", {}),
                    "eliminated": active_crypto_payload.get("eliminated", {}),
                },
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result["scanner"] = {"last_run_at": None, "status": "error"}
    return result


@app.get("/api/account")
async def get_account() -> dict[str, Any]:
    return await asyncio.to_thread(manager.fetch_account_snapshot)


@app.post("/api/engine/start")
async def start_engine() -> dict[str, str]:
    try:
        await asyncio.to_thread(manager.start_engine)
        return {"status": "started", "message": "Bot engine started successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
        _atomic_write(
            manager.config_path,
            yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        )

        manager.broker = None  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        log.exception("Trading-mode toggle failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Trading mode could not be changed") from None


@app.post("/api/live/arming/request")
async def request_live_arming() -> dict[str, str]:
    try:
        suffix = await asyncio.to_thread(manager.request_live_arming)
        return {
            "status": "confirmation_required",
            "account_suffix": suffix,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/live/arming/confirm")
async def confirm_live_arming(payload: LiveConfirmationPayload) -> dict[str, str]:
    try:
        manager.confirm_live_arming(payload.phrase)
        return {
            "status": "armed",
            "message": "Live mode armed for this server process. Start remains a separate action.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/ws-ticket")
async def websocket_ticket(payload: WebSocketTicketPayload) -> dict[str, str]:
    try:
        return {"ticket": manager.issue_ws_ticket(payload.kind)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    key, secret = manager.get_credentials()
    cfg_raw: dict[str, Any] = {}
    if manager.config_path.exists():
        with open(manager.config_path, encoding="utf-8") as f:
            cfg_raw = yaml.safe_load(f) or {}

    return {
        # Never send credentials back to the browser. Empty fields mean
        # "preserve the existing value" when settings are saved.
        "api_key": "",
        "secret_key": "",
        "has_credentials": bool(key and secret),
        "paper_mode": manager.is_paper_mode(),
        "daily_loss_limit_pct": 100 * cfg_raw.get("risk", {}).get("daily_loss_limit_pct", 0.015),
        "max_drawdown_pct": 100 * cfg_raw.get("risk", {}).get("max_drawdown_pct", 0.10),
        "timeframe": cfg_raw.get("runtime", {}).get("execution_timeframe", "5m"),
        "host": cfg_raw.get("app", {}).get("host", "127.0.0.1"),
        "port": cfg_raw.get("app", {}).get("port", 8000),
        "open_browser": cfg_raw.get("app", {}).get("open_browser", True),
        "log_level": cfg_raw.get("app", {}).get("log_level", "INFO"),
        "equity_trading_enabled": cfg_raw.get("strategy", {})
        .get("equity", {})
        .get("enabled", True),
        "crypto_trading_enabled": cfg_raw.get("strategy", {})
        .get("crypto", {})
        .get("enabled", False),
        "live_trading_enabled": cfg_raw.get("live", {}).get("enabled", False),
        "live_dollar_cap": cfg_raw.get("live", {}).get("hard_dollar_cap", 0.0),
        "live_operator_approval_path": cfg_raw.get("live", {}).get("operator_approval_path", ""),
    }


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    try:
        validated = manager._validated_settings_payload(payload)
        cfg_raw = validated.cfg_raw
        old_app = validated.old_app
        api_key = validated.api_key
        secret_key = validated.secret_key
        credentials_changed = validated.credentials_changed

        # Validation and live-token checks are complete before a healthy
        # runtime is stopped.
        active_runtime = manager.has_active_runtime()
        restart_engine = active_runtime and payload.restart_engine
        if active_runtime and not payload.restart_engine:
            raise ValueError("Stop the bot before saving settings without a restart")
        if restart_engine:
            await asyncio.to_thread(manager.stop_engine)
            if manager.engine_state != "stopped":
                raise RuntimeError(
                    "Bot could not stop safely. Use the Durdur button and try again."
                )
        _atomic_update_credentials(manager.env_path, api_key, secret_key)
        _atomic_write(
            manager.config_path,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )

        manager.broker = None
        if credentials_changed:
            manager.invalidate_live_arming()
        restart_error: str | None = None
        if restart_engine:
            try:
                await asyncio.to_thread(manager.start_engine)
            except Exception as exc:
                # The settings are already safely persisted. A transient
                # Alpaca timeout during the automatic restart must not report
                # the entire save operation as a 500 failure.
                restart_error = str(exc)
                log.warning("Settings saved, but automatic engine restart failed: %s", exc)

        # These values belong to the web process itself. Saving them is safe,
        # but they take effect the next time the application is launched.
        app_restart_required = any(
            old_app.get(name, default) != getattr(payload, name)
            for name, default in {
                "host": "127.0.0.1",
                "port": 8000,
                "open_browser": True,
                "log_level": "INFO",
            }.items()
        )
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
            state, paper = await asyncio.to_thread(
                lambda: (manager.current_engine_state(), manager.is_paper_mode())
            )
            status_data = {"state": state, "paper": paper}
            await websocket.send_json(
                {"type": "update", "account": account_data, "status": status_data}
            )
            try:
                # The browser never sends messages; this paces the loop and,
                # unlike asyncio.sleep, detects a dead peer: writing to a lost
                # connection only logs "socket.send() raised exception." in
                # asyncio instead of raising, so receive() is the only way to
                # notice the disconnect.
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except TimeoutError:
                pass
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
                done, _pending = await asyncio.wait(
                    {get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                )
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
