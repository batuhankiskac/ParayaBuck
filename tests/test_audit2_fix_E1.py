"""Audit wave 2 fixes E1: stop/shutdown races, stop signal exit, stream restart, snapshot order.

Covers R2-4-1, R2-4-2, C2-9-1, C2-9-3, C2-9-4, C2-9-5, C2-10-1 and C2-10-2.
Every test uses in-memory shells or tmp_path; nothing touches the network,
credentials or production paths.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
import time
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn

import parayabuck.web.app as app_module
import parayabuck.web.lifespan as lifespan_module
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.monitoring.snapshot import SnapshotWriter, read_snapshot
from parayabuck.runtime import EngineRuntime, StartupCancelled
from parayabuck.runtime_health import WorkerHealth
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import engine as engine_router
from parayabuck.web.routers import health as health_router


def _server() -> app_module._ExitingServer:
    return app_module._ExitingServer(uvicorn.Config(SimpleNamespace(state=SimpleNamespace()), port=8765))


# ---------------------------------------------------------------------------
# R2-4-1: a stop signal must reach main() so the engine-stop check can run
# ---------------------------------------------------------------------------


def test_stop_signal_is_handled_without_re_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    raised: list[int] = []
    monkeypatch.setattr(app_module.signal, "raise_signal", lambda sig: raised.append(sig))
    server = _server()

    with server.capture_signals():
        server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit is True
    assert raised == []


def test_main_exits_non_zero_after_a_signal_stop_with_stuck_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real Server class: a SIGTERM stop that left workers running must exit non-zero."""
    fake_app = SimpleNamespace(state=SimpleNamespace(engine_stop_failed=True))
    fake_server_module = types.ModuleType("parayabuck.web.server")
    fake_server_module.app = fake_app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "parayabuck.web.server", fake_server_module)
    seen: dict[str, Any] = {}

    def run_like_uvicorn(self: app_module._ExitingServer) -> None:
        with self.capture_signals():
            self.handle_exit(signal.SIGTERM, None)
        seen["timeout"] = self.config.timeout_graceful_shutdown

    monkeypatch.setattr(app_module._ExitingServer, "run", run_like_uvicorn)
    monkeypatch.setattr(app_module, "_probe_web_bind", lambda host, port: None)
    monkeypatch.setattr(app_module, "setup_logging", lambda **_kwargs: None)
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing-settings.yaml"))
    monkeypatch.setenv("OPEN_BROWSER", "0")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8765")

    with pytest.raises(SystemExit) as outcome:
        app_module.main()

    assert outcome.value.code
    assert seen["timeout"] == app_module._GRACEFUL_SHUTDOWN_SECONDS


# ---------------------------------------------------------------------------
# R2-4-2: the engine refuses a pending start before the connection drain
# ---------------------------------------------------------------------------


def test_shutdown_refuses_the_engine_start_before_the_connection_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def fake_parent_shutdown(self: Any, sockets: Any = None) -> None:
        order.append("drain")

    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_parent_shutdown)
    app = SimpleNamespace(state=SimpleNamespace(engine_request_shutdown=lambda: order.append("refuse")))
    server = app_module._ExitingServer(uvicorn.Config(app, port=8765))

    asyncio.run(server.shutdown())

    assert order == ["refuse", "drain"]


def test_lifespan_publishes_the_shutdown_hook_for_the_server() -> None:
    calls: list[str] = []

    class _Manager:
        engine_state = "stopped"

        def should_auto_start(self) -> bool:
            return False

        def request_shutdown(self) -> None:
            calls.append("request_shutdown")

        def stop_engine(self) -> None:
            calls.append("stop_engine")

    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace())

    async def run() -> None:
        async with lifespan_module.create_lifespan(lambda: manager)(app):
            assert app.state.engine_request_shutdown == manager.request_shutdown

    asyncio.run(run())

    assert calls == ["request_shutdown", "stop_engine"]


# ---------------------------------------------------------------------------
# C2-9-1: a stop during startup is reported as "stopping" until the attempt ends
# ---------------------------------------------------------------------------


def test_stop_during_an_in_flight_start_reports_stopping_and_cancels_it() -> None:
    shell = SimpleNamespace(
        _state_lock=threading.RLock(),
        _start_cancel=threading.Event(),
        _cancel_pending_retry=lambda: None,
        engine_state="starting",
        runtime=None,
        _start_attempt_monotonic=time.monotonic(),
    )

    assert EngineManager.stop_engine(shell) == "stopping"
    assert shell.engine_state == "stopping"
    assert shell._start_cancel.is_set()

    # A second stop while the attempt still runs is also "stopping".
    shell.engine_state = "stopping"
    assert EngineManager.stop_engine(shell) == "stopping"


def test_stop_without_a_start_in_flight_reports_stopped() -> None:
    shell = SimpleNamespace(
        _state_lock=threading.RLock(),
        _start_cancel=threading.Event(),
        _cancel_pending_retry=lambda: None,
        engine_state="stopped",
        runtime=None,
        _start_attempt_monotonic=None,
    )

    assert EngineManager.stop_engine(shell) == "stopped"


def test_stop_route_answers_stopping_until_the_attempt_has_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    for outcome in ("stopping", "stopped"):
        manager = SimpleNamespace(stop_engine=lambda *, operator_request=False, _o=outcome: _o)
        monkeypatch.setattr(engine_router, "get_engine_manager", lambda request, _m=manager: _m)

        response = asyncio.run(engine_router.stop_engine(SimpleNamespace()))

        assert response["status"] == outcome


def test_advance_gate_refuses_orders_once_the_startup_is_cancelled() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True  # type: ignore[attr-defined]
    runtime._circuit_breaker_reason = None  # type: ignore[attr-defined]
    cancel = threading.Event()
    runtime._startup_cancel = cancel  # type: ignore[attr-defined]
    order = SimpleNamespace(side=PortfolioOrderSide.SELL)

    assert runtime._advance_submit_block_reason(Market.EQUITY, order) is None
    cancel.set()
    assert runtime._advance_submit_block_reason(Market.EQUITY, order) == "startup_cancelled"


def test_startup_catch_up_aborts_before_its_first_order_when_cancelled() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True  # type: ignore[attr-defined]
    runtime._warming_up = False  # type: ignore[attr-defined]
    runtime._startup_committed = False  # type: ignore[attr-defined]
    cancel = threading.Event()
    cancel.set()
    runtime._startup_cancel = cancel  # type: ignore[attr-defined]

    class _Untouchable:
        def __iter__(self) -> Any:
            raise AssertionError("catch-up must not reach a portfolio runtime after a cancel")

    runtime._portfolio_runtimes = _Untouchable()  # type: ignore[attr-defined]

    with pytest.raises(StartupCancelled):
        runtime._run_startup_catch_up(account=None)


# ---------------------------------------------------------------------------
# C2-9-5: the watchdog gate closes for a stop that did not finish
# ---------------------------------------------------------------------------


def test_watchdog_stops_pinging_when_a_stop_failed() -> None:
    shell = SimpleNamespace(engine_state="stop_failed", runtime=None)

    assert EngineManager.watchdog_healthy(shell) is False


def test_watchdog_stops_pinging_when_a_stop_hangs_past_its_bound() -> None:
    hung = SimpleNamespace(engine_state="stopping", runtime=None)
    hung._stop_started_monotonic = time.monotonic() - (engine_module._WATCHDOG_STOP_MAX_SECONDS + 1)
    fresh = SimpleNamespace(engine_state="stopping", runtime=None)
    fresh._stop_started_monotonic = time.monotonic()

    assert EngineManager.watchdog_healthy(hung) is False
    assert EngineManager.watchdog_healthy(fresh) is True


# ---------------------------------------------------------------------------
# C2-10-1: readiness probes run off the event loop
# ---------------------------------------------------------------------------


def test_readyz_probes_the_engine_off_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, int] = {}

    class _Manager:
        config_path = str(tmp_path / "missing-settings.yaml")
        engine_state = "stopped"

        def current_engine_state(self) -> str:
            seen["current_engine_state"] = threading.get_ident()
            return "stopped"

        def has_active_runtime(self) -> bool:
            seen["has_active_runtime"] = threading.get_ident()
            return False

    manager = _Manager()
    monkeypatch.setattr(health_router, "get_engine_manager", lambda request: manager)

    async def probe() -> int:
        loop_ident = threading.get_ident()
        await health_router.readyz(SimpleNamespace())
        return loop_ident

    loop_ident = asyncio.run(probe())

    assert seen["current_engine_state"] != loop_ident
    assert seen["has_active_runtime"] != loop_ident


# ---------------------------------------------------------------------------
# C2-9-3: stop() must not raise on a thread that never started
# ---------------------------------------------------------------------------


def test_stop_survives_a_worker_thread_that_never_started() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._stream_workers_launched = False  # type: ignore[attr-defined]
    runtime._storage_closed = True  # type: ignore[attr-defined]
    runtime._threads = [threading.Thread(target=lambda: None, name="pb-never-started")]  # type: ignore[attr-defined]

    assert runtime.stop() is True


# ---------------------------------------------------------------------------
# C2-9-4: the final snapshot carries the stop outcome and is never overwritten
# ---------------------------------------------------------------------------


def test_final_snapshot_is_not_overwritten_by_a_later_periodic_write(tmp_path: Path) -> None:
    path = tmp_path / "health_snapshot.json"
    writer = SnapshotWriter(path=path, provider=lambda: {"state": "running"})

    writer.write_stopped(state="stopped")

    assert writer.write_once() is False
    assert read_snapshot(path)["runtime_state"] == "stopped"


def test_final_snapshot_reports_a_failed_stop(tmp_path: Path) -> None:
    path = tmp_path / "health_snapshot.json"
    writer = SnapshotWriter(path=path, provider=lambda: {"state": "stopping"})

    writer.write_stopped(state="stop_failed")

    assert read_snapshot(path)["runtime_state"] == "stop_failed"


# ---------------------------------------------------------------------------
# C2-10-2: the stream restart spawns under the _running check and waits outside the lock
# ---------------------------------------------------------------------------


def _restart_shell(*, running: bool) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = running  # type: ignore[attr-defined]
    runtime._threads_lock = threading.Lock()  # type: ignore[attr-defined]
    runtime._threads = []  # type: ignore[attr-defined]
    runtime._workers = {"pb-stocks": WorkerHealth(last_heartbeat=datetime.now(UTC))}  # type: ignore[attr-defined]
    runtime.stream = SimpleNamespace(run_stock=lambda: None)  # type: ignore[attr-defined]
    return runtime


def test_restart_does_not_spawn_a_stream_worker_once_the_engine_is_stopping() -> None:
    runtime = _restart_shell(running=False)

    assert runtime._restart_stream_worker(Market.EQUITY) is False
    assert runtime._threads == []  # type: ignore[attr-defined]


def test_restart_spawns_the_stream_worker_while_running() -> None:
    runtime = _restart_shell(running=True)
    started: list[str] = []
    runtime._run_stream_worker = lambda name, target: started.append(name)  # type: ignore[attr-defined,method-assign]

    assert runtime._restart_stream_worker(Market.EQUITY) is True
    for thread in runtime._threads:  # type: ignore[attr-defined]
        thread.join(timeout=5)
    assert started == ["pb-stocks"]


def test_verify_market_halts_does_not_wait_for_the_restarted_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _restart_shell(running=True)
    halts: dict[Market, str | None] = {Market.EQUITY: "pb-stocks_restart_limit"}
    monkeypatch.setattr(runtime, "_restart_stream_worker", lambda market: True)

    def must_not_wait(market: Market) -> bool:
        raise AssertionError("the delivery wait must run after the reconcile lock is released")

    monkeypatch.setattr(runtime, "_stream_restart_delivering", must_not_wait)

    pending = runtime._verify_market_halts(halts)

    assert pending == [(Market.EQUITY, "pb-stocks_restart_limit")]
    assert halts[Market.EQUITY] == "pb-stocks_restart_limit"


def test_confirm_stream_restarts_clears_only_a_delivering_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _restart_shell(running=True)
    halts: dict[Market, str | None] = {Market.EQUITY: "pb-stocks_restart_limit"}
    pending = [(Market.EQUITY, "pb-stocks_restart_limit")]

    monkeypatch.setattr(runtime, "_stream_restart_delivering", lambda market: True)
    runtime._confirm_stream_restarts(halts, pending)
    assert halts[Market.EQUITY] is None

    halts[Market.EQUITY] = "pb-stocks_restart_limit"
    monkeypatch.setattr(runtime, "_stream_restart_delivering", lambda market: False)
    runtime._confirm_stream_restarts(halts, pending)
    assert halts[Market.EQUITY] == "pb-stocks_restart_limit"
