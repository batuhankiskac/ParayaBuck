"""Runtime and web wiring invariants (2026-10-27).

Each test pins one property:

* the systemd watchdog is fed by the web process, gated on the engine's
  account loop: pinged with no engine or a fresh loop, silent when the loop
  is stale or missing;
* the web lifespan sends READY=1 whether or not the engine starts;
* the broker's expected account id reaches both verify call sites;
* a sticky operator halt refuses ``_start_impl`` before any broker call;
* the premarket probe runs once per NYSE session inside its window and a
  failure latches the reconcile-pending gate;
* the cost-basis drift provider is attached to the broker.

Everything is offline: the broker is a fake, the database lives in tmp_path.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import (
    BrokerConfig,
    Config,
    EngineConfig,
    EquityUniverseConfig,
    RuntimeConfig,
    RuntimeMode,
    UniverseConfig,
)
from parayabuck.core.models import Market
from parayabuck.execution.alpaca_reconciliation import RECONCILIATION_CATEGORIES
from parayabuck.live_safety import HALT_MARKER_FILENAME, LiveStartupProof
from parayabuck.monitoring.premarket import ProbeResult
from parayabuck.runtime import EngineRuntime
from parayabuck.utils import sd_notify
from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.managers.engine import EngineManager

_OPEN = datetime(2026, 10, 27, 13, 30, tzinfo=UTC)
_CLOSE = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)
_SESSIONS = ((date(2026, 10, 27), _OPEN, _CLOSE),)


class _FakeBroker:
    account_id = "PA1"
    account_number = "PA1"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.closed = 0

    def reconcile(self) -> dict[str, list[str]]:
        return {category: [] for category in RECONCILIATION_CATEGORIES}

    def account(self) -> None:
        return None

    def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module, "AlpacaBroker", _FakeBroker)
    yield


def _config(tmp_path: Path, *, expected_account_id: str | None = None) -> Config:
    return Config(
        universe=UniverseConfig(equity=EquityUniverseConfig(symbols=("SPY",))),
        engine=EngineConfig(),
        runtime=RuntimeConfig(mode=RuntimeMode.PAPER, decision_db_path=str(tmp_path / "runtime.sqlite3")),
        broker=BrokerConfig(expected_account_id=expected_account_id),
    )


def _runtime(tmp_path: Path, **kwargs: Any) -> EngineRuntime:
    return EngineRuntime("key", "secret", _config(tmp_path, **kwargs))


# --- Task 1: watchdog gate and pinger -------------------------------------


def _runtime_stub(runtime: Any = None) -> SimpleNamespace:
    """Minimal EngineManager stand-in: the gate reads only ``runtime``."""
    return SimpleNamespace(runtime=runtime)


def test_gate_pings_when_no_engine_is_running() -> None:
    assert EngineManager.watchdog_healthy(_runtime_stub()) is True


def test_gate_pings_when_engine_runtime_is_stopped_even_with_stale_loop() -> None:
    stopped = SimpleNamespace(_running=False, last_account_loop_pass_at=None)
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=stopped)) is True


def test_gate_pings_while_account_loop_is_fresh() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    running = SimpleNamespace(_running=True, last_account_loop_pass_at=now - timedelta(seconds=119))
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=running), now=now) is True


def test_gate_stops_when_account_loop_is_stale() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    hung = SimpleNamespace(_running=True, last_account_loop_pass_at=now - timedelta(seconds=121))
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=hung), now=now) is False


def test_gate_stops_when_running_engine_never_completed_an_iteration() -> None:
    running = SimpleNamespace(_running=True, last_account_loop_pass_at=None)
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=running)) is False


def test_gate_gives_startup_a_grace_before_the_first_pass() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    starting = SimpleNamespace(
        _running=True, last_account_loop_pass_at=None, _watchdog_started_at=now - timedelta(seconds=599)
    )
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=starting), now=now) is True
    stuck = SimpleNamespace(
        _running=True, last_account_loop_pass_at=None, _watchdog_started_at=now - timedelta(seconds=601)
    )
    assert EngineManager.watchdog_healthy(_runtime_stub(runtime=stuck), now=now) is False


def test_pinger_sends_only_while_healthy() -> None:
    sent: list[str] = []
    state = {"healthy": True}
    pinger = sd_notify.WatchdogPinger(lambda: state["healthy"], send=lambda s: sent.append(s) or True)

    assert pinger.ping_once() is True
    state["healthy"] = False
    assert pinger.ping_once() is False
    assert sent == ["WATCHDOG=1"]


def test_pinger_treats_a_raising_gate_as_unhealthy() -> None:
    sent: list[str] = []

    def broken() -> bool:
        raise RuntimeError("gate failed")

    pinger = sd_notify.WatchdogPinger(broken, send=lambda s: sent.append(s) or True)

    assert pinger.ping_once() is False
    assert sent == []


def test_pinger_thread_starts_and_stops_idempotently() -> None:
    sent: list[str] = []
    pinger = sd_notify.WatchdogPinger(lambda: True, interval_seconds=0.01, send=lambda s: sent.append(s) or True)
    pinger.start()
    pinger.start()
    deadline = time.monotonic() + 2.0
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    pinger.stop()
    pinger.stop()

    assert len(sent) >= 2
    assert set(sent) == {"WATCHDOG=1"}


def test_gate_uses_the_runtime_running_flag_not_only_the_timestamp(tmp_path: Path) -> None:
    # A stopped runtime keeps its last timestamp; that must not hold the pings.
    runtime = _runtime(tmp_path)
    runtime._running = False
    runtime.last_account_loop_pass_at = datetime(2020, 1, 1, tzinfo=UTC)
    assert EngineManager.watchdog_healthy(SimpleNamespace(runtime=runtime)) is True


# --- Task 1: lifespan READY ----------------------------------------------


def _notify_socket(monkeypatch: pytest.MonkeyPatch) -> tuple[socket.socket, str]:
    directory = tempfile.mkdtemp(dir="/tmp")
    path = os.path.join(directory, "n.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    return server, path


def _serve_lifespan(manager: Any) -> None:
    async def run() -> None:
        lifespan = create_lifespan(lambda: manager)
        async with lifespan(None):
            await asyncio.sleep(0)

    asyncio.run(run())


def test_lifespan_sends_ready_when_no_engine_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    server, _path = _notify_socket(monkeypatch)
    manager = SimpleNamespace(
        configure_alert_delivery=lambda: None,
        should_auto_start=lambda: False,
        watchdog_healthy=lambda: True,
        stop_engine=lambda: None,
    )
    try:
        _serve_lifespan(manager)
        assert server.recv(64) == b"READY=1"
    finally:
        server.close()


def test_lifespan_sends_ready_even_when_the_engine_gate_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    server, _path = _notify_socket(monkeypatch)
    manager = SimpleNamespace(
        configure_alert_delivery=lambda: None,
        should_auto_start=lambda: False,
        watchdog_healthy=lambda: False,
        stop_engine=lambda: None,
    )
    try:
        _serve_lifespan(manager)
        assert server.recv(64) == b"READY=1"
        # The unhealthy gate suppresses every WATCHDOG datagram.
        server.settimeout(0.2)
        with pytest.raises(socket.timeout):
            server.recv(64)
    finally:
        server.close()


# --- Task 2: expected_account_id and operator halt -----------------------


class _Stop(Exception):
    pass


def test_paper_verify_receives_expected_account_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> None:
        calls.append(kwargs)
        raise _Stop

    monkeypatch.setattr(runtime_module, "verify_account_environment", record)
    monkeypatch.setattr(runtime_module, "trading_host_authorized", lambda: True)
    runtime = _runtime(tmp_path, expected_account_id="PA123")
    runtime.broker = _FakeBroker()

    with pytest.raises(_Stop):
        runtime._start_impl()

    assert len(calls) == 1
    assert calls[0]["live"] is False
    assert calls[0]["expected_account_id"] == "PA123"


def test_live_verify_receives_expected_account_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(runtime_module, "verify_account_environment", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        runtime_module.LiveOperatorApproval,
        "load_verified",
        staticmethod(lambda *_a, **_k: "approval"),
    )
    runtime = _runtime(tmp_path, expected_account_id="PA123")
    runtime.app_config = _config(tmp_path, expected_account_id="PA123")
    runtime.mode = RuntimeMode.LIVE
    runtime.api_key = "key"
    runtime.secret_key = "secret"
    runtime.broker = _FakeBroker()
    fingerprint = hashlib.sha256(b"key\0secret").hexdigest()
    now = datetime.now(UTC)
    runtime._live_startup_proof = LiveStartupProof(
        account_id="PA1",
        credential_fingerprint=fingerprint,
        configuration_digests=("digest",),
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        authorization="operator_approval",
    )

    approval = runtime._verify_live_startup_proof(("digest",))

    assert approval == "approval"
    assert len(calls) == 1
    assert calls[0]["live"] is True
    assert calls[0]["expected_account_id"] == "PA123"


def test_operator_halt_marker_refuses_start_before_any_broker_call(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    (tmp_path / HALT_MARKER_FILENAME).write_text("{}", encoding="utf-8")
    runtime.broker = SimpleNamespace(reconcile=lambda: pytest.fail("broker reached while halted"))

    with pytest.raises(RuntimeError, match="operator halt is in force"):
        runtime._start_impl()


# --- Task 3: premarket probe ---------------------------------------------


def _probe_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, skew_seconds: float = 0.0) -> EngineRuntime:
    monkeypatch.setattr(runtime_module, "sessions_around", lambda _now: _SESSIONS)
    runtime = _runtime(tmp_path)
    runtime._schedule_reconcile = lambda: None  # type: ignore[method-assign]
    clock_now = datetime(2026, 10, 27, 13, 10, tzinfo=UTC)
    runtime.broker = SimpleNamespace(
        account_id="PA1",
        clock=lambda: SimpleNamespace(
            timestamp=clock_now + timedelta(seconds=skew_seconds),
            next_open=_OPEN,
            next_close=_CLOSE,
        ),
        working_orders=lambda: (),
    )
    return runtime


def test_probe_runs_once_inside_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _probe_runtime(tmp_path, monkeypatch)
    seen: list[datetime] = []
    monkeypatch.setattr(
        runtime_module,
        "premarket_probe",
        lambda broker, **kwargs: seen.append(kwargs["now"]) or ProbeResult(ok=True),
    )

    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 10, tzinfo=UTC))
    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 12, tzinfo=UTC))

    assert len(seen) == 1
    assert runtime._reconcile_pending_reason is None


def test_probe_does_not_run_outside_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _probe_runtime(tmp_path, monkeypatch)
    seen: list[datetime] = []
    monkeypatch.setattr(
        runtime_module,
        "premarket_probe",
        lambda broker, **kwargs: seen.append(kwargs["now"]) or ProbeResult(ok=True),
    )

    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 12, 0, tzinfo=UTC))  # 90 min before open
    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 27, tzinfo=UTC))  # 3 min before open

    assert seen == []


def test_probe_failure_latches_reconcile_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _probe_runtime(tmp_path, monkeypatch, skew_seconds=60.0)
    alerts: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, **fields: alerts.append((kind, fields)),
    )

    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 10, tzinfo=UTC))

    assert runtime._reconcile_pending_reason == "premarket_probe_failed"
    assert alerts and alerts[0][0] == "reconciliation_failure"
    assert "clock skew" in alerts[0][1]["detail"]
    # Not halted: entries wait, the runtime keeps running.
    assert getattr(runtime, "_circuit_breaker_reason", None) is None


def test_probe_success_does_not_latch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _probe_runtime(tmp_path, monkeypatch)

    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 10, tzinfo=UTC))

    assert runtime._reconcile_pending_reason is None
    assert runtime._premarket_probed_sessions == {date(2026, 10, 27)}


def test_probe_never_raises_into_the_decision_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _probe_runtime(tmp_path, monkeypatch)

    def boom(*_args: object, **_kwargs: object) -> ProbeResult:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(runtime_module, "premarket_probe", boom)

    runtime._maybe_run_premarket_probe(datetime(2026, 10, 27, 13, 10, tzinfo=UTC))  # must not raise


# --- Task 4: cost-basis provider wiring ----------------------------------


def test_cost_basis_provider_is_attached_and_reads_the_repository(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    calls: list[tuple[str, str, Market]] = []

    def owned_cost_bases(account_id: str, symbol: str, market: Market) -> tuple[float, ...]:
        calls.append((account_id, symbol, market))
        return (101.5, 102.0)

    runtime._portfolio_repository.owned_cost_bases = owned_cost_bases  # type: ignore[method-assign]
    provider = getattr(runtime.broker, "portfolio_owned_cost_bases", None)

    assert callable(provider)
    assert provider("AAPL", Market.EQUITY) == (101.5, 102.0)
    assert calls == [("PA1", "AAPL", Market.EQUITY)]
