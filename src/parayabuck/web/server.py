from __future__ import annotations

import asyncio
import logging
import os
import stat
import threading
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from parayabuck.config import load_config
from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.server")


# Global Web Server State
class EngineManager:
    def __init__(self) -> None:
        self.config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
        self.env_path = Path(".env")
        if not self.config_path.exists() and Path("config/settings.yaml").exists():
            self.config_path = Path("config/settings.yaml")
        if not self.env_path.exists() and Path(".env.example").exists():
            self.env_path = Path(".env.example")

        self.broker: Any = None
        self.engine: Any = None
        self.runtime: Any = None
        self.engine_state: str = "stopped"
        self.last_account: dict[str, Any] | None = None
        self.log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = (
            set()
        )
        self.stream_connections: set[WebSocket] = set()
        self._state_lock = threading.Lock()

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
            cfg = load_config(str(self.config_path))
            if not cfg.alpaca.paper and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES "
                    "before starting the application."
                )
            from parayabuck.runtime import EngineRuntime

            runtime = EngineRuntime(api_key=key, secret_key=secret, config=cfg)
            self.runtime = runtime
            self.engine = runtime.engine
            self.broker = runtime.broker
            runtime.start()
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
            raise

    def stop_engine(self) -> None:
        with self._state_lock:
            if self.engine_state == "stopped":
                return
            self.engine_state = "stopping"
            runtime = self.runtime
        if runtime is not None:
            runtime.stop()
        with self._state_lock:
            self.engine_state = "stopped"
        log.info("ParayaBuck engine stopped")

    def fetch_account_snapshot(self) -> dict[str, Any]:
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
                }
                self.last_account = snapshot
                return snapshot
            except Exception as e:
                log.warning(f"Error fetching account snapshot: {e}")

        if self.last_account:
            return self.last_account

        return {
            "equity": 0.0,
            "cash": 0.0,
            "daily_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
            "positions": [],
        }


manager = EngineManager()


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
                loop.call_soon_threadsafe(queue.put_nowait, log_entry)
            except Exception:
                pass


ws_log_handler = WebSocketLogHandler()
ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger("parayabuck").addHandler(ws_log_handler)

# FastAPI App
app = FastAPI(title="ParayaBuck Trading Terminal")


def _trusted_browser_origin(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser/local API clients
    parsed = urlparse(origin)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def protect_local_control_api(request: Request, call_next: Any) -> Any:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _trusted_browser_origin(
        request.headers.get("origin")
    ):
        return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
    return await call_next(request)


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# Pydantic Schemas
class SettingsPayload(BaseModel):
    api_key: str = Field("", description="Alpaca API Key")
    secret_key: str = Field("", description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    daily_loss_limit_pct: float = Field(1.5, gt=0, le=100)
    max_drawdown_pct: float = Field(10.0, gt=0, le=100)
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "1d"] = "15m"
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = Field(True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ModePayload(BaseModel):
    paper: bool


# Routes
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"is_paper": manager.is_paper_mode()},
    )


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    key, _ = manager.get_credentials()
    return {
        "state": manager.current_engine_state(),
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key),
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
        raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path) as f:
                raw = yaml.safe_load(f) or {}
        raw.setdefault("alpaca", {})["paper"] = payload.paper
        with open(manager.config_path, "w") as f:
            yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=False)

        manager.broker = None  # Force broker reset
        return {"status": "success", "paper": payload.paper}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        restart_engine = manager.engine_state == "running"
        if restart_engine:
            manager.stop_engine()
            if manager.engine_state != "stopped":
                raise RuntimeError(
                    "Bot could not stop safely. Use the Durdur button and try again."
                )

        current_key, current_secret = manager.get_credentials()
        api_key = payload.api_key.strip() or current_key or ""
        secret_key = payload.secret_key.strip() or current_secret or ""
        if any(char in api_key + secret_key for char in "\r\n"):
            raise ValueError("Credentials may not contain newlines")
        # Write credentials with owner-only permissions.
        manager.env_path.touch(mode=0o600, exist_ok=True)
        set_key(manager.env_path, "ALPACA_API_KEY", api_key, quote_mode="never")
        set_key(manager.env_path, "ALPACA_SECRET_KEY", secret_key, quote_mode="never")
        os.chmod(manager.env_path, stat.S_IRUSR | stat.S_IWUSR)

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

        with open(manager.config_path, "w") as f:
            yaml.safe_dump(cfg_raw, f, sort_keys=False, default_flow_style=False)

        manager.broker = None
        if restart_engine:
            manager.start_engine()

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
            "message": "Settings and credentials saved.",
            "engine_restarted": restart_engine,
            "app_restart_required": app_restart_required,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# WebSockets
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    if not _trusted_browser_origin(websocket.headers.get("origin")):
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
    if not _trusted_browser_origin(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
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
