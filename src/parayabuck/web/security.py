"""Local-only control-plane security primitives.

Extracted from web/server.py protect_local_control_api middleware.
Preserves the exact loopback-Host + Origin + control-token checks from the
monolith so every route mounted through the factory inherits the same
local-only boundary. Security headers (CSP, Referrer-Policy, nosniff) and the
static Cache-Control are also preserved here.
"""

from __future__ import annotations

import os
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


def trusted_browser_origin(origin: str | None) -> bool:
    """Browser Origin must be loopback/local. Empty Origin is treated as non-browser (e.g. curl) and rejected for mutating methods via middleware but allowed for GET."""
    if not origin:
        return False
    parsed = urlparse(origin)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _trusted_browser_origin_for_middleware(origin: str | None) -> bool:
    """Exact server.py middleware semantics: empty Origin = non-browser client -> trusted.

    The standalone `trusted_browser_origin` helper above is stricter (empty
    -> False) for direct callers. The HTTP middleware must allow curl/API
    clients without an Origin header to proceed to the control-token check,
    matching the original server.py `_trusted_browser_origin`.
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


def is_mutating_method(method: str) -> bool:
    return method not in {"GET", "HEAD", "OPTIONS"}


def validate_control_token(provided: str, expected: str) -> bool:
    return secrets.compare_digest(provided.encode("latin-1", "replace"), expected.encode())


def validate_host_header(host_header: str) -> bool:
    return is_trusted_host(host_header)


def build_protect_middleware(
    control_token_provider: Callable[[], str],
) -> Callable[[Request, Callable[..., Any]], Any]:
    """Return an ``@app.middleware("http")``-compatible callable that enforces

    * loopback-only Host header (``testserver`` allowed under pytest),
    * loopback-only Origin for mutating methods,
    * ``x-parayabuck-control-token`` for mutating methods,
    * security headers (CSP, Referrer-Policy, nosniff) and ``no-store`` for
    * static assets.

    ``control_token_provider`` is called per-request so a rotated token in
    tests / runtime is observed immediately. The closure avoids importing
    ``WebState`` at module import time — no circular import.
    """

    async def protect_local_control_api(request: Request, call_next: Any) -> Any:
        host_header = request.headers.get("host") or ""
        host = urlparse(f"//{host_header}").hostname or ""
        allowed = {"127.0.0.1", "localhost", "::1"}
        if os.getenv("PYTEST_CURRENT_TEST"):
            allowed = allowed | {"testserver"}
        if host not in allowed:
            return JSONResponse(status_code=403, content={"detail": "Untrusted Host header"})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _trusted_browser_origin_for_middleware(
            request.headers.get("origin")
        ):
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
            request.headers.get(_CONTROL_TOKEN_HEADER, "").encode("latin-1", "replace"),
            control_token_provider().encode(),
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
