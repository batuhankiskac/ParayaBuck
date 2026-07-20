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

        self.trading_thread: Any = None
        self.broker: Any = None
        self.engine: Any = None
        self.engine_state: str = "stopped"
        self.last_account: dict[str, Any] | None = None
        self.log_queues: set[
            tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]
        ] = set()
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

        paper = self.is_paper_mode()

        try:
            cfg = load_config(str(self.config_path))
            if not paper and os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES":
                raise ValueError(
                    "Live trading is not armed. Set PARAYABUCK_ENABLE_LIVE=YES "
                    "before starting the application."
                )
            from parayabuck.data.alpaca import AlpacaDataSource
            from parayabuck.execution.alpaca import AlpacaBroker
            from parayabuck.execution.live_engine import LiveTradingEngine

            broker = AlpacaBroker(
                key,
                secret,
                paper=paper,
                peak_state_path=Path("data/runtime")
                / ("paper_equity_peak.json" if paper else "live_equity_peak.json"),
            )
            data_source = AlpacaDataSource(
                key,
                secret,
                stock_feed=cfg.alpaca.stock_feed,
                data_delayed=cfg.alpaca.data_delayed,
            )
            market_filter = cfg.raw.get("market_filter", {})
            vix_proxy = str(market_filter.get("vix_proxy_symbol", "VIXY"))
            # Data-only helpers must not accidentally become independently
            # traded runtime symbols.
            symbols = [symbol for symbol in cfg.symbols if symbol not in {vix_proxy, "BIL"}]

            engine = LiveTradingEngine(
                broker=broker,
                data_source=data_source,
                symbols=symbols,
                timeframe=cfg.app.timeframe,
                poll_interval=cfg.app.poll_interval,
                max_positions=cfg.portfolio.max_positions if cfg.portfolio else 5,
                risk_pct=cfg.risk.risk_per_trade / cfg.account.starting_balance,
                stop_loss_pct=cfg.risk.stop_loss_pct,
                take_profit_pct=cfg.risk.take_profit_pct,
                trail_pct=cfg.risk.trail_pct,
                crypto_stop_loss_pct=cfg.risk.crypto_stop_loss_pct,
                crypto_take_profit_pct=cfg.risk.crypto_take_profit_pct,
                atr_stop_multiplier=cfg.risk.atr_stop_multiplier,
                atr_tp_multiplier=cfg.risk.atr_tp_multiplier,
                max_daily_loss=cfg.risk.max_daily_loss,
                max_drawdown=cfg.risk.max_drawdown,
                strategy_allocations=cfg.strategy_allocations,
                max_position_size=cfg.risk.max_position_size,
                risk_per_trade_amount=cfg.risk.risk_per_trade,
                max_pct_per_position=(
                    cfg.portfolio.max_pct_per_position if cfg.portfolio else 0.10
                ),
                max_pct_per_sector=(
                    cfg.portfolio.max_pct_per_sector if cfg.portfolio else 0.30
                ),
                min_cash_pct=cfg.portfolio.min_cash_pct if cfg.portfolio else 0.05,
                fractional_shares=cfg.alpaca.fractional_shares,
                strategy_params_by_name={cfg.strategy.name: cfg.strategy.params},
                use_global_market_filter=bool(market_filter.get("enabled", True)),
                benchmark_symbol=str(market_filter.get("benchmark_symbol", "SPY")),
                vix_proxy_symbol=vix_proxy,
                benchmark_sma_period=int(market_filter.get("benchmark_sma_period", 200)),
                vix_proxy_sma_period=int(market_filter.get("vix_proxy_sma_period", 50)),
                vix_high_ratio=float(market_filter.get("vix_high_ratio", 1.5)),
            )

            self.broker = broker
            self.engine = engine

            def run_loop() -> None:
                try:
                    self.engine_state = "running"
                    engine.run()
                except Exception:
                    log.exception("Engine failure")
                    self.engine_state = "error"
                finally:
                    if self.engine_state != "error":
                        self.engine_state = "stopped"

            self.trading_thread = threading.Thread(target=run_loop, daemon=True)
            self.trading_thread.start()
            log.info("Live engine started in background thread.")

        except Exception:
            self.engine_state = "error"
            raise

    def stop_engine(self) -> None:
        with self._state_lock:
            if self.engine_state == "stopped":
                return
            self.engine_state = "stopping"
            engine = self.engine
            thread = self.trading_thread
        if engine is not None:
            engine.stop()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        with self._state_lock:
            self.engine_state = "stopped" if not thread or not thread.is_alive() else "error"
        log.info("Live engine stopped.")

    def fetch_account_snapshot(self) -> dict[str, Any]:
        if self.broker is None:
            key, secret = self.get_credentials()
            if key and secret:
                try:
                    from parayabuck.execution.alpaca import AlpacaBroker
                    paper = self.is_paper_mode()
                    self.broker = AlpacaBroker(
                        key,
                        secret,
                        paper=paper,
                        peak_state_path=Path("data/runtime")
                        / ("paper_equity_peak.json" if paper else "live_equity_peak.json"),
                    )
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
                            "unrealized_pnl": p.unrealized_pnl(
                                p.current_price if p.current_price is not None else p.avg_price
                            ),
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
        for queue, loop in list(manager.log_queues):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, log_entry)
            except Exception:
                pass

ws_log_handler = WebSocketLogHandler()
ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(ws_log_handler)

# FastAPI App
app = FastAPI(title="ParayaBuck Modern Web Trading Terminal")


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
    max_daily_loss: float = Field(300.0, gt=0)
    max_drawdown: float = Field(1500.0, gt=0)
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "1d"] = "15m"
    poll_interval: int = Field(60, ge=5, le=3600)
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
        "state": manager.engine_state,
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
        if manager.engine_state in {"starting", "running", "stopping"}:
            raise ValueError("Stop the engine before changing settings")
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
    if not _trusted_browser_origin(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager.stream_connections.add(websocket)
    try:
        while True:
            account_data = await asyncio.to_thread(manager.fetch_account_snapshot)
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
    if not _trusted_browser_origin(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    queue_ref = (queue, asyncio.get_running_loop())
    manager.log_queues.add(queue_ref)

    try:
        while True:
            log_entry = await queue.get()
            await websocket.send_json(log_entry)
    except WebSocketDisconnect:
        manager.log_queues.remove(queue_ref)
    except Exception:
        if queue_ref in manager.log_queues:
            manager.log_queues.remove(queue_ref)
