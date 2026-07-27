from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from parayabuck.config import load_config
from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.server")


@dataclass(frozen=True)
class LiveArmToken:
    account_id: str
    account_suffix: str
    credential_fingerprint: str
    config_digest: str
    issued_at: datetime
    expires_at: datetime
    nonce: str


# Global Web Server State
class EngineManager:
    def __init__(self) -> None:
        self.config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
        self.env_path = Path(os.getenv("ENV_PATH") or ".env")
        if not self.config_path.exists() and Path("config/settings.yaml").exists():
            self.config_path = Path("config/settings.yaml")

        self.broker: Any = None
        self.engine: Any = None
        self.runtime: Any = None
        self.engine_state: str = "stopped"
        self.last_account: dict[str, Any] | None = None
        self._last_account_monotonic = 0.0
        self._account_cache_ttl_seconds = 5.0
        self._account_lock = threading.Lock()
        self.log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = (
            set()
        )
        self.stream_connections: set[WebSocket] = set()
        self._state_lock = threading.Lock()
        self.control_token = secrets.token_urlsafe(32)
        self._live_request_suffix: str | None = None
        self._live_armed = False
        self._pending_live_arm: LiveArmToken | None = None
        self._live_arm_token: LiveArmToken | None = None
        self._ws_tickets: dict[str, tuple[str, float]] = {}
        self.security_warning = _tracked_secret_warning(Path.cwd())
        if self.security_warning:
            log.critical("%s", self.security_warning)

    def is_paper_mode(self) -> bool:
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                return bool(cfg.get("alpaca", {}).get("paper", True))
        except Exception:
            pass
        return True

    def get_credentials(self) -> tuple[str | None, str | None]:
        load_dotenv(self.env_path, override=True)
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")
        return key, secret

    def current_engine_state(self) -> str:
        with self._state_lock:
            if self.runtime is not None and hasattr(self.runtime, "health"):
                runtime_health = self.runtime.health()
                self.engine_state = runtime_health.state
                return self.engine_state
            if (
                self.engine_state == "running"
                and self.runtime is not None
                and not self.runtime.is_running
            ):
                self.engine_state = "error"
            return self.engine_state

    def start_engine(self) -> None:
        with self._state_lock:
            if self.engine_state in {"starting", "running", "stopping"}:
                raise ValueError(f"Engine is already {self.engine_state}")
            self.engine_state = "starting"

        key, secret = self.get_credentials()
        if not key or not secret:
            with self._state_lock:
                self.engine_state = "error"
            raise ValueError("Alpaca API key/secret missing. Configure settings first.")

        try:
            warning = _tracked_secret_warning(Path.cwd())
            if warning:
                raise ValueError(warning)
            cfg = load_config(str(self.config_path))
            if not cfg.alpaca.paper and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES "
                    "before starting the application."
                )
            if not cfg.alpaca.paper:
                self._require_valid_live_arm(key, secret, consume=True)
            from parayabuck.runtime import EngineRuntime

            runtime = EngineRuntime(api_key=key, secret_key=secret, config=cfg)
            self.runtime = runtime
            self.engine = runtime.engine
            self.broker = runtime.broker
            runtime.start()
            if not cfg.alpaca.paper:
                self.invalidate_live_arming()
            self.engine_state = "running"
            log.info("ParayaBuck engine started from Web UI")

        except Exception:
            self.engine_state = "error"
            runtime = self.runtime
            if runtime is not None:
                try:
                    runtime.stop()
                except Exception:
                    log.exception("Cleanup after failed engine start")
            self.invalidate_live_arming()
            raise

    def stop_engine(self) -> None:
        with self._state_lock:
            if self.engine_state == "stopped":
                return
            self.engine_state = "stopping"
            runtime = self.runtime
        if runtime is not None:
            stopped = runtime.stop()
            if stopped is False:
                with self._state_lock:
                    self.engine_state = "stop_failed"
                self.invalidate_live_arming()
                raise RuntimeError("Runtime workers did not stop; process restart required")
        with self._state_lock:
            self.engine_state = "stopped"
            self.runtime = None
            self.engine = None
            self.broker = None
            self.last_account = None
        self.invalidate_live_arming()
        log.info("ParayaBuck engine stopped")

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
                    log.warning(f"Could not connect broker: {e}")

        if self.broker is not None:
            try:
                acct = self.broker.account()
                positions_list = []
                for p in acct.positions:
                    if not p.is_flat:
                        positions_list.append(
                            {
                                "symbol": p.symbol,
                                "direction": "LONG" if p.qty > 0 else "SHORT",
                                "qty": abs(p.qty),
                                "avg_price": p.avg_price,
                                "unrealized_pnl": p.unrealized_pnl,
                                "ownership": p.ownership,
                            }
                        )

                snapshot = {
                    "equity": acct.equity,
                    "cash": acct.cash,
                    "daily_pnl": acct.daily_pnl,
                    "realized_pnl": acct.realized_pnl,
                    "unrealized_pnl": acct.unrealized_pnl,
                    "drawdown": max(0.0, acct.peak_equity - acct.equity),
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
                log.warning(f"Error fetching account snapshot: {e}")

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
        if self.engine_state in {"starting", "running", "stopping"}:
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
            nonce=secrets.token_urlsafe(24),
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
                "nonce": secrets.token_urlsafe(24),
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
            raw = yaml.safe_load(self.config_path.read_text()) or {}
        return self._digest_config(raw, paper=paper)

    @staticmethod
    def _digest_config(raw: dict[str, Any], *, paper: bool) -> str:
        raw = json.loads(json.dumps(raw))
        raw.setdefault("alpaca", {})["paper"] = paper
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
    except (OSError, subprocess.SubprocessError):
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
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if mode is not None:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if mode is not None:
            os.chmod(path, mode)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_update_credentials(path: Path, api_key: str, secret_key: str) -> None:
    existing = path.read_text() if path.exists() else ""
    managed = {"ALPACA_API_KEY": api_key, "ALPACA_SECRET_KEY": secret_key}
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in managed:
            name = match.group(1)
            if name not in seen:
                lines.append(f"{name}={managed[name]}")
                seen.add(name)
        else:
            lines.append(line)
    for name, value in managed.items():
        if name not in seen:
            lines.append(f"{name}={value}")
    _atomic_write(path, "\n".join(lines).rstrip() + "\n", 0o600)


manager = EngineManager()


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> Any:
    try:
        yield
    finally:
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
        for queue, loop in list(manager.log_queues):
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
    return response


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# Pydantic Schemas
class SettingsPayload(BaseModel):
    api_key: str = Field("", description="Alpaca API Key")
    secret_key: str = Field("", description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    daily_loss_limit_pct: float = Field(1.5, gt=0, le=10)
    max_drawdown_pct: float = Field(10.0, gt=0, le=30)
    timeframe: Literal["5m"] = "5m"
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = Field(True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    restart_engine: bool = True


class ModePayload(BaseModel):
    paper: bool


class LiveConfirmationPayload(BaseModel):
    phrase: str


class WebSocketTicketPayload(BaseModel):
    kind: Literal["stream", "logs"]


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


@app.get("/api/control-token")
async def get_control_token() -> JSONResponse:
    response = JSONResponse({"control_token": manager.control_token})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    key, _ = manager.get_credentials()
    return {
        "state": manager.current_engine_state(),
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key),
        "security_warning": getattr(manager, "security_warning", None),
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
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/engine/stop")
async def stop_engine() -> dict[str, str]:
    await asyncio.to_thread(manager.stop_engine)
    return {"status": "stopped", "message": "Bot engine stopped."}


@app.post("/api/mode")
async def toggle_mode(payload: ModePayload) -> dict[str, Any]:
    if manager.engine_state in {"starting", "running", "stopping"}:
        raise HTTPException(
            status_code=400,
            detail="Cannot toggle trading mode while engine is running. Stop the bot first.",
        )

    try:
        if not payload.paper:
            key, secret = manager.get_credentials()
            if hasattr(manager, "_require_valid_live_arm"):
                if not key or not secret:
                    raise ValueError("Alpaca credentials are required")
                manager._require_valid_live_arm(key, secret, consume=False)
            elif not getattr(manager, "_live_armed", False):
                raise ValueError("Complete server-side live arming before selecting live mode")
        raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path) as f:
                raw = yaml.safe_load(f) or {}
        raw.setdefault("alpaca", {})["paper"] = payload.paper
        _atomic_write(
            manager.config_path,
            yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        )

        manager.broker = None  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        with open(manager.config_path) as f:
            cfg_raw = yaml.safe_load(f) or {}

    return {
        # Never send credentials back to the browser. Empty fields mean
        # "preserve the existing value" when settings are saved.
        "api_key": "",
        "secret_key": "",
        "has_credentials": bool(key and secret),
        "paper_mode": manager.is_paper_mode(),
        "daily_loss_limit_pct": 100
        * cfg_raw.get("engine", {}).get("risk", {}).get("daily_loss_limit_pct", 0.015),
        "max_drawdown_pct": 100
        * cfg_raw.get("engine", {}).get("risk", {}).get("max_drawdown_pct", 0.10),
        "timeframe": cfg_raw.get("engine", {}).get("timeframe", "5m"),
        "host": cfg_raw.get("app", {}).get("host", "127.0.0.1"),
        "port": cfg_raw.get("app", {}).get("port", 8000),
        "open_browser": cfg_raw.get("app", {}).get("open_browser", True),
        "log_level": cfg_raw.get("app", {}).get("log_level", "INFO"),
    }


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    try:
        if manager.engine_state in {"starting", "stopping"}:
            raise ValueError("Bot is currently changing state; wait a moment and try again")

        # The web server and trading engine have separate lifecycles. Runtime
        # settings (timeframe and risk limits) only require the
        # trading engine to restart; shutting down the whole application also
        # removes the UI and is both confusing and unnecessary.
        current_paper = manager.is_paper_mode() if hasattr(manager, "is_paper_mode") else True
        entering_live = current_paper and not payload.paper_mode
        if entering_live and payload.restart_engine:
            raise ValueError(
                "Saving live mode requires restart_engine=false; start live separately after arming"
            )
        if entering_live:
            current_key, current_secret = manager.get_credentials()
            if hasattr(manager, "_require_valid_live_arm"):
                if not current_key or not current_secret:
                    raise ValueError("Alpaca credentials are required")
                manager._require_valid_live_arm(
                    current_key,
                    current_secret,
                    consume=False,
                )
            elif not getattr(manager, "_live_armed", False):
                raise ValueError("Complete server-side live arming before saving live mode")
        current_key, current_secret = manager.get_credentials()
        api_key = payload.api_key.strip() or current_key or ""
        secret_key = payload.secret_key.strip() or current_secret or ""
        if any(char in api_key + secret_key for char in "\r\n"):
            raise ValueError("Credentials may not contain newlines")
        # Write settings.yaml
        cfg_raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path) as f:
                cfg_raw = yaml.safe_load(f) or {}

        old_app = dict(cfg_raw.get("app", {}))

        cfg_raw.setdefault("alpaca", {})["paper"] = payload.paper_mode
        engine = cfg_raw.setdefault("engine", {})
        risk = engine.setdefault("risk", {})
        risk["daily_loss_limit_pct"] = payload.daily_loss_limit_pct / 100
        risk["max_drawdown_pct"] = payload.max_drawdown_pct / 100
        engine["timeframe"] = payload.timeframe
        cfg_raw["app"] = {
            "host": payload.host.strip() or "127.0.0.1",
            "port": payload.port,
            "open_browser": payload.open_browser,
            "log_level": payload.log_level.upper(),
        }

        # Validate the complete temporary configuration before persistence.
        temporary_config = manager.config_path.with_name(f".{manager.config_path.name}.validate")
        _atomic_write(
            temporary_config,
            yaml.safe_dump(cfg_raw, sort_keys=False, default_flow_style=False),
        )
        try:
            if cfg_raw.get("symbols"):
                load_config(str(temporary_config))
        finally:
            temporary_config.unlink(missing_ok=True)
        credentials_changed = (api_key, secret_key) != (
            current_key or "",
            current_secret or "",
        )
        if entering_live and credentials_changed:
            if hasattr(manager, "invalidate_live_arming"):
                manager.invalidate_live_arming()
            raise ValueError(
                "Credentials changed; request and type a fresh live confirmation first"
            )
        if entering_live and hasattr(manager, "_live_arm_token"):
            token = manager._live_arm_token
            candidate_digest = manager._digest_config(cfg_raw, paper=False)
            if token is None or token.config_digest != candidate_digest:
                if hasattr(manager, "invalidate_live_arming"):
                    manager.invalidate_live_arming()
                raise ValueError(
                    "Settings changed after live verification; confirm the live account again"
                )

        # Validation and live-token checks are complete before a healthy
        # runtime is stopped.
        restart_engine = manager.engine_state == "running" and payload.restart_engine
        if restart_engine:
            manager.stop_engine()
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
        if credentials_changed and hasattr(manager, "invalidate_live_arming"):
            manager.invalidate_live_arming()
        restart_error: str | None = None
        if restart_engine:
            try:
                manager.start_engine()
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# WebSockets
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    ticket_valid = (
        manager.consume_ws_ticket(ticket, "stream")
        if hasattr(manager, "consume_ws_ticket")
        else True
    )
    if not _trusted_browser_origin(websocket.headers.get("origin")) or not ticket_valid:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager.stream_connections.add(websocket)
    try:
        while True:
            account_data = await asyncio.to_thread(manager.fetch_account_snapshot)
            status_data = {
                "state": manager.current_engine_state(),
                "paper": manager.is_paper_mode(),
            }
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
    except Exception:
        pass
    finally:
        manager.stream_connections.discard(websocket)


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    ticket_valid = (
        manager.consume_ws_ticket(ticket, "logs") if hasattr(manager, "consume_ws_ticket") else True
    )
    if not _trusted_browser_origin(websocket.headers.get("origin")) or not ticket_valid:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    queue_ref = (queue, asyncio.get_running_loop())
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
    except Exception:
        pass
    finally:
        manager.log_queues.discard(queue_ref)
