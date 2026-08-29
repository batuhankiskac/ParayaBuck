"""WebSocket ticket and log-queue manager — extracted from web/server.py.

Ticket: secure random, TTL 30s, scope-bound (stream|logs), single-use, lock-protected
so concurrent POST /api/ws-ticket callers never drop each other's tickets.

Log queues: bounded (500), drop oldest on full (coalesce), thread-safe via
lock + call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress
from typing import Any

from parayabuck.web.security import WebSocketTicketStore

# Shared ticket store — single instance ensures concurrent issuance is safe
_ticket_store = WebSocketTicketStore(ttl_seconds=30.0)

# Bounded log queues: set of (Queue, loop)
_log_queues: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = set()
_log_queues_lock = threading.Lock()
_LOG_QUEUE_MAXSIZE = 500


def issue_ws_ticket(kind: str) -> str:
    """Issue a single-use ticket for ``kind`` (stream|logs). Thread-safe."""
    return _ticket_store.issue(kind)


def consume_ws_ticket(ticket: str, kind: str) -> bool:
    """Consume ticket — single-use, TTL 30s, scope-bound. Returns True if valid."""
    return _ticket_store.consume(ticket, kind)


def get_log_queues_snapshot() -> tuple[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop], ...]:
    with _log_queues_lock:
        return tuple(_log_queues)


def add_log_queue(queue: asyncio.Queue[dict[str, Any]], loop: asyncio.AbstractEventLoop) -> None:
    with _log_queues_lock:
        _log_queues.add((queue, loop))


def remove_log_queue(queue: asyncio.Queue[dict[str, Any]], loop: asyncio.AbstractEventLoop) -> None:
    with _log_queues_lock:
        _log_queues.discard((queue, loop))


def enqueue_log(target: asyncio.Queue[dict[str, Any]], entry: dict[str, Any]) -> None:
    """Backpressure: if full, drop oldest entry then enqueue."""
    if target.full():
        try:
            target.get_nowait()
        except asyncio.QueueEmpty:
            return
    try:
        target.put_nowait(entry)
    except asyncio.QueueFull:
        return


class WebSocketLogHandler(logging.Handler):
    """Fan-out handler that coalesces into each subscriber's bounded queue."""

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        log_entry = {
            "level": record.levelname,
            "levelno": record.levelno,
            "name": record.name,
            "message": record.getMessage(),
            "time": self.formatter.formatTime(record) if self.formatter else "",
        }
        subscribers = get_log_queues_snapshot()
        for queue, loop in subscribers:
            with suppress(Exception):
                loop.call_soon_threadsafe(enqueue_log, queue, log_entry)


# Install singleton handler exactly once
_ws_log_handler = WebSocketLogHandler()
_ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger("parayabuck").addHandler(_ws_log_handler)


# Expose for EngineManager delegation / tests
ticket_store = _ticket_store
log_queues = _log_queues
log_queues_lock = _log_queues_lock
