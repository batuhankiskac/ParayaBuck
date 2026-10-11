"""WebSocket router — extracted from web/server.py 3621-3687.

Pure WS routes: /ws/stream + /ws/logs with Host/Origin/ticket validation
and bounded queue backpressure. POST /api/ws-ticket lives in safety router
to avoid duplicate registration; both share the same ticket store via
parayabuck.web.managers.websocket (TTL30, lock, single-use, scope-bound).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from parayabuck.logger import get_logger
from parayabuck.web.managers.websocket import _LOG_QUEUE_MAXSIZE, add_log_queue, remove_log_queue
from parayabuck.web.managers.websocket import consume_ws_ticket as _consume_shared
from parayabuck.web.security import _trusted_browser_origin_for_middleware as _trusted_browser_origin

log = get_logger("parayabuck.web.websocket")

router = APIRouter()


def _web_state(websocket: WebSocket) -> Any | None:
    app = getattr(websocket, "app", None)
    app_state = getattr(app, "state", None)
    return getattr(app_state, "web", None)


def _consume_ticket(ticket: str, kind: str, websocket: WebSocket | None = None) -> bool:
    # A composed app must consume from its own state; falling back to a module
    # singleton here would let one app accept another app's ticket.
    if websocket is not None:
        state = _web_state(websocket)
        if state is not None:
            manager = getattr(state, "engine_manager", None)
            consume = getattr(manager, "consume_ws_ticket", None)
            if callable(consume):
                return bool(consume(ticket, kind))
            store = getattr(state, "ticket_store", None)
            consume = getattr(store, "consume", None)
            return bool(consume(ticket, kind)) if callable(consume) else False

    if _consume_shared(ticket, kind):
        return True
    # Fail closed: with neither an app-scoped nor a process-wide store there
    # is no ticket to validate, so a missing ticket is never trusted.
    return False


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    ticket = getattr(websocket, "query_params", {}).get("ticket", "")
    origin_ok = _trusted_browser_origin(websocket.headers.get("origin"))
    ticket_valid = _consume_ticket(ticket, "stream", websocket)
    if not origin_ok or not ticket_valid:
        try:
            close = getattr(websocket, "close", None)
            if callable(close):
                await close(code=1008)
        except Exception:
            pass
        return
    await websocket.accept()
    state = _web_state(websocket)
    engine_manager = getattr(state, "engine_manager", None) if state is not None else None
    if engine_manager is None:
        # No fallback: the process-wide WebState always binds a manager, and
        # deps.resolve_web_state 503s everywhere else when it is missing.
        await websocket.close(code=1011)
        return
    try:
        while True:
            account_data = await asyncio.to_thread(engine_manager.fetch_account_snapshot)
            state, paper = await asyncio.to_thread(
                lambda: (engine_manager.current_engine_state(), engine_manager.is_paper_mode())
            )
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
    ticket_valid = _consume_ticket(ticket, "logs", websocket)
    if not origin_ok or not ticket_valid:
        try:
            close = getattr(websocket, "close", None)
            if callable(close):
                await close(code=1008)
        except Exception:
            pass
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_LOG_QUEUE_MAXSIZE)
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
