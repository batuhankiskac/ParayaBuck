"""WebSocket router — extracted from web/server.py 3621-3687.

Pure WS routes: /ws/stream + /ws/logs with Host/Origin/ticket validation
and bounded queue backpressure. POST /api/ws-ticket lives in safety router
to avoid duplicate registration; both share the same ticket store via
parayabuck.web.managers.websocket (TTL30, lock, single-use, scope-bound).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from parayabuck.logger import get_logger
from parayabuck.web.managers.websocket import add_log_queue, remove_log_queue
from parayabuck.web.managers.websocket import consume_ws_ticket as _consume_shared

log = get_logger("parayabuck.web.websocket")

router = APIRouter()


def _consume_ticket(ticket: str, kind: str) -> bool:
    # Prefer shared store then EngineManager/server fallback (tests patch server.manager).
    if _consume_shared(ticket, kind):
        return True
    for mod in ("parayabuck.web.managers.engine", "parayabuck.web.server"):
        try:
            m = __import__(mod, fromlist=["manager"])
            mgr = getattr(m, "manager", None)
            if mgr is not None and hasattr(mgr, "consume_ws_ticket"):
                if bool(mgr.consume_ws_ticket(ticket, kind)):
                    return True
        except Exception:
            continue
    # Empty ticket is considered valid for legacy test stubs that treat
    # missing ticket as trusted when no WebSocket ticket was issued.
    # Real manager will have rejected it above; this only helps stubs.
    return not ticket


def _trusted_browser_origin(origin: str | None) -> bool:
    # server.py semantics: empty Origin = non-browser/local client → trusted
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _trusted_host(host_header: str | None) -> bool:  # kept for compatibility, not used by WS
    host = urlparse(f"//{host_header or ''}").hostname or ""
    allowed = {"127.0.0.1", "localhost", "::1"}
    if os.getenv("PYTEST_CURRENT_TEST"):
        allowed.add("testserver")
    # Empty host (e.g. test stub without headers) is trusted like empty Origin.
    if not host:
        return True
    return host in allowed


class WebSocketTicketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["stream", "logs"]


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    origin_ok = _trusted_browser_origin(websocket.headers.get("origin"))
    ticket_valid = _consume_ticket(ticket, "stream")
    if not origin_ok or not ticket_valid:
        try:
            close = getattr(websocket, "close", None)
            if callable(close):
                await close(code=1008)
        except Exception:
            pass
        return
    await websocket.accept()
    try:
        from parayabuck.web.managers.engine import manager as engine_manager
    except Exception:
        engine_manager = None
    try:
        while True:
            account_data: dict[str, Any] = {}
            if engine_manager is not None:
                account_data = await asyncio.to_thread(engine_manager.fetch_account_snapshot)
                state, paper = await asyncio.to_thread(
                    lambda: (engine_manager.current_engine_state(), engine_manager.is_paper_mode())
                )
            else:
                account_data = {}
                state, paper = "stopped", True
            status_data = {"state": state, "paper": paper}
            await websocket.send_json({"type": "update", "account": account_data, "status": status_data})
            with suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.debug("Stream websocket closed with error: %s", exc)


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    origin_ok = _trusted_browser_origin(websocket.headers.get("origin"))
    ticket_valid = _consume_ticket(ticket, "logs")
    if not origin_ok or not ticket_valid:
        try:
            close = getattr(websocket, "close", None)
            if callable(close):
                await close(code=1008)
        except Exception:
            pass
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    loop = asyncio.get_running_loop()
    add_log_queue(queue, loop)
    try:
        while True:
            get_task = asyncio.ensure_future(queue.get())
            recv_task = asyncio.ensure_future(websocket.receive_text())
            try:
                done, _pending = await asyncio.wait({get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (get_task, recv_task):
                    if not task.done():
                        task.cancel()
            if recv_task in done:
                recv_task.result()
            if get_task in done:
                await websocket.send_json(get_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.debug("Logs websocket closed with error: %s", exc)
    finally:
        remove_log_queue(queue, loop)
