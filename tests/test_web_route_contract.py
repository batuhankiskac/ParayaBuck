"""Freeze Web route contract: no accidental removal/rename/method change."""

from __future__ import annotations

from parayabuck.web.server import app


def _route_map(client_app):
    m = {}
    for r in client_app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path is None:
            continue
        if (
            path.startswith("/docs")
            or path.startswith("/openapi")
            or path.startswith("/redoc")
            or path.startswith("/static")
        ):
            continue
        if methods:
            for meth in methods:
                m[(path, meth)] = getattr(r, "name", "")
        else:
            # websocket
            m[(path, "WS")] = getattr(r, "name", "")
    return m


REQUIRED_ROUTES = [
    ("/", "GET"),
    ("/tools", "GET"),
    ("/healthz", "GET"),
    ("/readyz", "GET"),
    ("/api/control-token", "GET"),
    ("/api/status", "GET"),
    ("/api/ops/status", "GET"),
    ("/api/account", "GET"),
    ("/api/engine/start", "POST"),
    ("/api/engine/stop", "POST"),
    ("/api/engine/reset-safety-lock", "POST"),
    ("/api/safety/risk-epoch", "POST"),
    ("/api/mode", "POST"),
    ("/api/live/prerequisites", "GET"),
    ("/api/live/arming/request", "POST"),
    ("/api/live/arming/confirm", "POST"),
    ("/api/ws-ticket", "POST"),
    ("/api/settings", "GET"),
    ("/api/settings", "POST"),
    ("/api/settings", "PATCH"),
    ("/api/backtest/run", "POST"),
    ("/api/backtest/status", "GET"),
    ("/api/dashboard/overview", "GET"),
    ("/api/equity", "GET"),
    ("/api/crypto", "GET"),
    ("/api/risk", "GET"),
    ("/api/execution", "GET"),
    ("/api/universe", "GET"),
    ("/api/health", "GET"),
    ("/api/explanations", "GET"),
    ("/api/metrics", "GET"),
    ("/ws/stream", "WS"),
    ("/ws/logs", "WS"),
]


def test_required_routes_present():
    m = _route_map(app)
    for path, method in REQUIRED_ROUTES:
        assert (path, method) in m, f"missing route {method} {path} current={sorted(m.keys())}"


def test_no_accidental_method_change():
    # Ensure POST didn't become GET etc. Check mutating routes remain POST
    m = _route_map(app)
    for path, method in REQUIRED_ROUTES:
        assert (path, method) in m
    # Negative: check that /api/engine/start is not GET
    assert ("/api/engine/start", "GET") not in m
