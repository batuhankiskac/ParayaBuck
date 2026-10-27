"""Local-only control-plane security primitives.

Extracted from web/server.py protect_local_control_api middleware.
Preserves the exact loopback-Host + Origin + control-token checks from the
monolith so every route mounted through the factory inherits the same
local-only boundary. Security headers (CSP, Referrer-Policy, nosniff) and the
static Cache-Control are also preserved here.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from fastapi.responses import JSONResponse
from starlette.requests import Request

_TRUSTED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_CONTROL_TOKEN_HEADER = "x-parayabuck-control-token"
_CONTROL_TOKEN_PATH = "/api/control-token"
# Read-only routes that still require a trusted (loopback) peer: the control
# token itself, and the live checklist, whose messages name the configured
# approval path and HMAC key env variable.
_PEER_GATED_GET_PATHS = frozenset({_CONTROL_TOKEN_PATH, "/api/live/prerequisites"})


def _trusted_browser_origin_for_middleware(origin: str | None) -> bool:
    """Exact server.py middleware semantics: empty Origin = non-browser client -> trusted.

    The HTTP middleware must allow curl/API clients without an Origin header
    to proceed to the control-token check, matching the original server.py
    `_trusted_browser_origin`. The WebSocket routes share this predicate.
    """
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def is_trusted_host(host_header: str) -> bool:
    """Loopback-only Host validation."""
    host = urlparse(f"//{host_header}").hostname or ""
    allowed = set(_TRUSTED_HOSTS)
    if os.getenv("PYTEST_CURRENT_TEST"):
        allowed.add("testserver")
    return host in allowed


def is_trusted_client(client_host: str | None) -> bool:
    """Peer-address check for the control token and every mutating request.

    The Host header is client-supplied, so it cannot stop a remote peer from
    fetching the control token when the server is reachable off-host. Only a
    loopback peer is trusted. The single exception is the documented
    container mode (``PARAYABUCK_CONTAINER=YES`` on a machine that is NOT the
    trading host): Docker publishes the port on the host's loopback only and
    proxies it through the bridge network, so the peer is a private address.
    """
    if not client_host:
        return False
    if os.getenv("PYTEST_CURRENT_TEST") and client_host == "testclient":
        return True
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    from parayabuck.trading_host import trading_host_authorized

    return os.getenv("PARAYABUCK_CONTAINER") == "YES" and not trading_host_authorized() and address.is_private


_TICKET_QUERY = re.compile(r"(ticket=)[^&\s\"']+")
# The websockets library logs the handshake request line (with ?ticket=) at DEBUG
# under its own logger names; a filter on a parent logger does not see child records.
_UVICORN_LOGGERS = ("uvicorn.error", "uvicorn.access", "websockets", "websockets.server", "websockets.client")
# Largest request body any control route accepts (settings and backtest payloads are a few KB).
_MAX_REQUEST_BODY_BYTES = 64 * 1024


class TicketRedactionFilter(logging.Filter):
    """Redact single-use WebSocket ``?ticket=`` values from server log lines.

    uvicorn logs WebSocket handshakes (``uvicorn.error``) and requests
    (``uvicorn.access``) with the full query string; a logged ticket could be
    replayed within its TTL. Only string arguments are rewritten, so the
    access formatter still receives its positional args.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _TICKET_QUERY.sub(r"\1[redacted]", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TICKET_QUERY.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def install_ticket_log_redaction() -> None:
    """Attach :class:`TicketRedactionFilter` to the uvicorn loggers once."""
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, TicketRedactionFilter) for existing in logger.filters):
            logger.addFilter(TicketRedactionFilter())


def validate_control_token(provided: str | None, expected: str | None) -> bool:
    if not isinstance(provided, str) or not provided:
        return False
    if not isinstance(expected, str) or not expected:
        return False
    return secrets.compare_digest(provided, expected)


def build_protect_middleware(
    control_token_provider: Callable[[Request], str],
) -> Callable[[Request, Callable[..., Any]], Any]:
    """Return an ``@app.middleware("http")``-compatible callable that enforces

    * loopback-only Host header (``testserver`` allowed under pytest),
    * loopback-only peer address for the control token, the live checklist
      and mutating methods,
    * loopback-only Origin for mutating methods,
    * ``x-parayabuck-control-token`` for mutating methods,
    * security headers (CSP, Referrer-Policy, nosniff) and ``no-store`` for
    * static assets.

    ``control_token_provider`` is called per-request so a rotated token in
    tests / runtime is observed immediately. The closure avoids importing
    ``WebState`` at module import time — no circular import.
    """

    async def protect_local_control_api(request: Request, call_next: Any) -> Any:
        if not is_trusted_host(request.headers.get("host") or ""):
            return JSONResponse(status_code=403, content={"detail": "Untrusted Host header"})
        declared_length = request.headers.get("content-length") or ""
        # ASCII digits only: str.isdigit() also accepts "²", and int() on that raises before the token check.
        if declared_length and not (declared_length.isascii() and declared_length.isdigit()):
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        if declared_length and int(declared_length) > _MAX_REQUEST_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        if request.headers.get("transfer-encoding"):
            # A chunked body declares no length, so the cap above cannot bound it.
            return JSONResponse(status_code=411, content={"detail": "Length Required: send Content-Length"})
        mutating = request.method not in {"GET", "HEAD", "OPTIONS"}
        if (mutating or request.url.path in _PEER_GATED_GET_PATHS) and not is_trusted_client(
            request.client.host if request.client is not None else None
        ):
            return JSONResponse(status_code=403, content={"detail": "Untrusted client address"})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _trusted_browser_origin_for_middleware(
            request.headers.get("origin")
        ):
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                expected_token = control_token_provider(request)
            except Exception:
                expected_token = None
            if not validate_control_token(request.headers.get(_CONTROL_TOKEN_HEADER), expected_token):
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

    return protect_local_control_api


class WebSocketTicketStore:
    """Secure random ticket store: TTL 30s, scope-bound, single-use, thread-safe."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, kind: str) -> str:
        if kind not in {"stream", "logs"}:
            raise ValueError("Unknown WebSocket channel")
        now = time.monotonic()
        ticket = secrets.token_urlsafe(24)
        with self._lock:
            # prune expired
            self._tickets = {k: v for k, v in self._tickets.items() if now < v[1]}
            self._tickets[ticket] = (kind, now + self._ttl)
        return ticket

    def consume(self, ticket: str, kind: str) -> bool:
        with self._lock:
            value = self._tickets.pop(ticket, None)
        return bool(value and value[0] == kind and time.monotonic() < value[1])

    @property
    def tickets(self) -> dict[str, tuple[str, float]]:
        with self._lock:
            return dict(self._tickets)
