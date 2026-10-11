"""Freeze Web route contract: no accidental removal/rename/method change.

Provenance: `docs/web_openapi_before.json` was deliberately regenerated on
2026-10-01 from the current app (`parayabuck.web.server:app` -> `app.openapi()`)
after it had been missing since 2026-09-15, which made
``test_openapi_baseline_intact`` skip silently. The 2026-09-15 file was NOT
restored from git history: the crypto routes were removed from the app on
2026-10-01, so restoring it would have frozen a dead API surface. The baseline
 freezes 33 paths / 35 operations as they exist today.
"""

from __future__ import annotations

import json
import pathlib

from parayabuck.web.server import app

BASELINE = pathlib.Path("docs/web_route_contract_before.json")
OPENAPI_BASELINE = pathlib.Path("docs/web_openapi_before.json")


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
    ("/api/ops/events", "GET"),
    ("/api/ops/incidents", "GET"),
    ("/api/ops/decisions/latest", "GET"),
    ("/api/ops/timeline/{decision_id}", "GET"),
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
    # Crypto support was removed: its dashboard panel route must stay gone.
    assert ("/api/crypto", "GET") not in m


def test_openapi_baseline_intact():
    assert OPENAPI_BASELINE.exists(), "baseline not generated"
    baseline = json.loads(OPENAPI_BASELINE.read_text())
    from parayabuck.web.server import app as cur_app

    cur = cur_app.openapi()
    for path, methods in baseline["paths"].items():
        assert path in cur["paths"], f"openapi missing path {path}"
        for method, spec in methods.items():
            assert method in cur["paths"][path], f"missing method {method} {path}"
            cur_spec = cur["paths"][path][method]
            assert cur_spec["operationId"] == spec["operationId"], f"operationId changed {path} {method}"


def test_baseline_doc_exists():
    assert BASELINE.exists(), "baseline not generated"
    data = json.loads(BASELINE.read_text())
    assert "routes" in data
    assert len(data["routes"]) >= 30
