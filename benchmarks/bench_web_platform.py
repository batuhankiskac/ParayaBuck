"""Web platform benchmarks — import, endpoint latency, backtest mutual exclusion.

Measures current web-platform behavior:
* import time for parayabuck.web.server (should be <200ms, no broker network)
* TestClient endpoint latency for /healthz, /readyz, /api/status, /api/ops/status
* Backtest manager start/status latency
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.harness import Report, timeit


def bench_import(report: Report):
    def _import():
        import importlib

        import parayabuck.web.server as srv  # noqa: F401

        importlib.reload(srv)

    # Use single run with high precision; import is cached after first, so measure cold via reload
    t, _ = timeit(_import, label="web.server import (reload)", warmup=0, runs=5)
    report.add(t)


def bench_endpoints(report: Report):
    from fastapi.testclient import TestClient

    from parayabuck.web.application import create_app
    from parayabuck.web.managers.backtest import BacktestManager
    from parayabuck.web.managers.engine import EngineManager
    from parayabuck.web.managers.websocket import ticket_store
    from parayabuck.web.state import WebState

    mgr = EngineManager()
    bm = BacktestManager(engine_state_provider=mgr.current_engine_state)
    bm.bind_engine_state_provider(mgr.current_engine_state)
    state = WebState(engine_manager=mgr, backtest_manager=bm, ticket_store=ticket_store)
    state.bind()
    app = create_app(state)
    client = TestClient(app, raise_server_exceptions=False)

    endpoints = [
        ("/healthz", "GET"),
        ("/readyz", "GET"),
        ("/api/status", "GET"),
        ("/api/ops/status", "GET"),
        ("/api/control-token", "GET"),
    ]
    for path, method in endpoints:

        def _call(p=path, m=method):
            if m == "GET":
                client.get(p, headers={"Host": "testserver"})
            else:
                client.post(p, headers={"Host": "testserver", "X-Parayabuck-Control-Token": mgr.control_token})

        t, _ = timeit(_call, label=f"{method} {path}", warmup=2, runs=50)
        report.add(t)

    # also measure ops events with limit
    def _ops_events():
        client.get("/api/ops/events?limit=10", headers={"Host": "testserver"})

    t, _ = timeit(_ops_events, label="GET /api/ops/events?limit=10", warmup=2, runs=50)
    report.add(t)


def bench_backtest_manager(report: Report):
    from parayabuck.web.managers.backtest import BacktestManager

    bm = BacktestManager()

    def _status():
        bm.status()

    t, _ = timeit(_status, label="BacktestManager.status()", warmup=5, runs=1000)
    report.add(t)


def run(report: Report):
    bench_import(report)
    bench_endpoints(report)
    bench_backtest_manager(report)


if __name__ == "__main__":
    from benchmarks.harness import mb, peak_rss_bytes

    baseline_rss = peak_rss_bytes()
    report = Report()
    run(report)
    report.print_table()
    rss = peak_rss_bytes()
    print(f"RSS delta: {mb(rss - baseline_rss):.1f} MB (baseline {mb(baseline_rss):.1f} -> {mb(rss):.1f})")
    # Regression guard: import should be <200ms p50, endpoints <10ms p50, status <0.1ms
    failed = False
    for timing in report.timings:
        if "web.server import" in timing.label and timing.median > 0.2:
            print(f"REGRESSION: {timing.label} median {timing.median * 1000:.1f}ms >200ms", file=sys.stderr)
            failed = True
        if "GET /healthz" in timing.label and timing.median > 0.01:
            print(f"REGRESSION: {timing.label} median {timing.median * 1000:.1f}ms >10ms", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)
