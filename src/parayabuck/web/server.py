from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
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

        self.trading_thread: Any = None
        self.broker: Any = None
        self.engine: Any = None
        self.engine_state: str = "stopped"
        self.last_account: dict[str, Any] | None = None
        self.log_queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.stream_connections: set[WebSocket] = set()

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

    def start_engine(self) -> None:
        if self.engine_state == "running":
            return

        key, secret = self.get_credentials()
        if not key or not secret:
            raise ValueError("Alpaca API key/secret missing. Configure settings first.")

        self.engine_state = "starting"
        paper = self.is_paper_mode()

        try:
            cfg = load_config(str(self.config_path))
            from parayabuck.data.alpaca import AlpacaDataSource
            from parayabuck.execution.alpaca import AlpacaBroker
            from parayabuck.execution.live_engine import LiveTradingEngine

            broker = AlpacaBroker(key, secret, paper=paper)
            data_source = AlpacaDataSource(key, secret)
            symbols = list(cfg.symbols.keys())

            engine = LiveTradingEngine(
                broker=broker,
                data_source=data_source,
                symbols=symbols,
                timeframe=cfg.app.timeframe,
                poll_interval=cfg.app.poll_interval,
                max_positions=cfg.portfolio.max_positions if cfg.portfolio else 5,
                risk_pct=0.02,
                stop_loss_pct=0.04,
                take_profit_pct=0.06,
                trail_pct=0.03,
                max_daily_loss=cfg.risk.max_daily_loss,
                max_drawdown=cfg.risk.max_drawdown,
                strategy_allocations=cfg.strategy_allocations,
            )

            self.broker = broker
            self.engine = engine

            import threading
            def run_loop():
                try:
                    self.engine_state = "running"
                    engine.run()
                except Exception:
                    log.exception("Engine failure")
                    self.engine_state = "error"
                finally:
                    self.engine_state = "stopped"

            self.trading_thread = threading.Thread(target=run_loop, daemon=True)
            self.trading_thread.start()
            log.info("Live engine started in background thread.")

        except Exception as e:
            self.engine_state = "error"
            raise e

    def stop_engine(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        self.engine_state = "stopped"
        log.info("Live engine stopped.")

    def fetch_account_snapshot(self) -> dict[str, Any]:
        if self.broker is None:
            key, secret = self.get_credentials()
            if key and secret:
                try:
                    from parayabuck.execution.alpaca import AlpacaBroker
                    self.broker = AlpacaBroker(key, secret, paper=self.is_paper_mode())
                except Exception as e:
                    log.warning(f"Could not connect broker: {e}")

        if self.broker is not None:
            try:
                acct = self.broker.get_account()
                positions_list = []
                for p in acct.positions.values():
                    if not p.is_flat:
                        positions_list.append({
                            "symbol": p.symbol,
                            "direction": "LONG" if p.is_long else "SHORT",
                            "qty": abs(p.qty),
                            "avg_price": p.avg_price,
                            "unrealized_pnl": getattr(p, "unrealized_pnl", 0.0),
                        })

                snapshot = {
                    "equity": acct.equity,
                    "cash": acct.balance,
                    "daily_pnl": acct.daily_pnl,
                    "realized_pnl": acct.realized_pnl,
                    "unrealized_pnl": acct.unrealized_pnl,
                    "drawdown": acct.drawdown,
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
            "time": record.asctime if hasattr(record, "asctime") else "",
        }
        for q in list(manager.log_queues):
            try:
                q.put_nowait(log_entry)
            except Exception:
                pass

ws_log_handler = WebSocketLogHandler()
ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(ws_log_handler)

# FastAPI App
app = FastAPI(title="ParayaBuck Modern Web Trading Terminal")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# Pydantic Schemas
class SettingsPayload(BaseModel):
    api_key: str = Field("", description="Alpaca API Key")
    secret_key: str = Field("", description="Alpaca Secret Key")
    paper_mode: bool = Field(True, description="Paper mode toggle")
    max_daily_loss: float = Field(300.0)
    max_drawdown: float = Field(1500.0)
    timeframe: str = Field("15m")
    poll_interval: int = Field(60, ge=5, le=3600)
    host: str = Field("127.0.0.1")
    port: int = Field(8000, ge=1, le=65535)
    open_browser: bool = Field(True)
    log_level: str = Field("INFO")


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
        "state": manager.engine_state,
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key),
    }


@app.get("/api/account")
async def get_account() -> dict[str, Any]:
    return manager.fetch_account_snapshot()


@app.post("/api/engine/start")
async def start_engine() -> dict[str, str]:
    try:
        manager.start_engine()
        return {"status": "started", "message": "Bot engine started successfully."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/engine/stop")
async def stop_engine() -> dict[str, str]:
    manager.stop_engine()
    return {"status": "stopped", "message": "Bot engine stopped."}


@app.post("/api/mode")
async def toggle_mode(payload: ModePayload) -> dict[str, Any]:
    if manager.engine_state == "running":
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
        "api_key": key or "",
        "secret_key": secret or "",
        "paper_mode": manager.is_paper_mode(),
        "max_daily_loss": cfg_raw.get("risk", {}).get("max_daily_loss", 300.0),
        "max_drawdown": cfg_raw.get("risk", {}).get("max_drawdown", 1500.0),
        "timeframe": cfg_raw.get("app", {}).get("timeframe", "15m"),
        "poll_interval": cfg_raw.get("app", {}).get("poll_interval", 60),
        "host": cfg_raw.get("app", {}).get("host", "127.0.0.1"),
        "port": cfg_raw.get("app", {}).get("port", 8000),
        "open_browser": cfg_raw.get("app", {}).get("open_browser", True),
        "log_level": cfg_raw.get("app", {}).get("log_level", "INFO"),
    }


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload) -> dict[str, str]:
    try:
        # Write .env
        env_content = f"ALPACA_API_KEY={payload.api_key}\nALPACA_SECRET_KEY={payload.secret_key}\n"
        with open(manager.env_path, "w") as f:
            f.write(env_content)

        # Write settings.yaml
        cfg_raw: dict[str, Any] = {}
        if manager.config_path.exists():
            with open(manager.config_path) as f:
                cfg_raw = yaml.safe_load(f) or {}

        cfg_raw.setdefault("alpaca", {})["paper"] = payload.paper_mode
        cfg_raw.setdefault("risk", {})["max_daily_loss"] = payload.max_daily_loss
        cfg_raw.setdefault("risk", {})["max_drawdown"] = payload.max_drawdown
        cfg_raw["app"] = {
            "host": payload.host.strip() or "127.0.0.1",
            "port": payload.port,
            "open_browser": payload.open_browser,
            "log_level": payload.log_level.upper(),
            "timeframe": payload.timeframe,
            "poll_interval": payload.poll_interval,
        }

        with open(manager.config_path, "w") as f:
            yaml.safe_dump(cfg_raw, f, sort_keys=False, default_flow_style=False)

        manager.broker = None
        return {"status": "success", "message": "Settings and credentials saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# WebSockets
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    manager.stream_connections.add(websocket)
    try:
        while True:
            account_data = manager.fetch_account_snapshot()
            status_data = {
                "state": manager.engine_state,
                "paper": manager.is_paper_mode(),
            }
            await websocket.send_json(
                {"type": "update", "account": account_data, "status": status_data}
            )
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        manager.stream_connections.remove(websocket)
    except Exception:
        if websocket in manager.stream_connections:
            manager.stream_connections.remove(websocket)


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    manager.log_queues.add(queue)

    try:
        while True:
            log_entry = await queue.get()
            await websocket.send_json(log_entry)
    except WebSocketDisconnect:
        manager.log_queues.remove(queue)
    except Exception:
        if queue in manager.log_queues:
            manager.log_queues.remove(queue)
