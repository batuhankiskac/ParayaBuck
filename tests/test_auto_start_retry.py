"""G3: bounded autonomous startup recovery.

Covers the runtime-side network-failure classification
(``TransientStartupError``, never a permanent incident) and the Web-side
auto-start retry loop (bounded backoff [5, 15, 30, 60]s, cancellation on
stop/shutdown, no duplicate runtimes, counters reset on success).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from parayabuck import runtime as runtime_module
from parayabuck.config import RiskLimits, RuntimeMode
from parayabuck.core.models import Market
from parayabuck.execution.alpaca import (
    AmbiguousOrderState,
    OwnershipDriftError,
    ProtectionReconciliationError,
)
from parayabuck.runtime import EngineRuntime, TransientStartupError
from parayabuck.web import server

_REAL_SLEEP = asyncio.sleep


# ---------------------------------------------------------------------------
# Runtime-side classification
# ---------------------------------------------------------------------------


def _bare_runtime(**attrs: Any) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.PAPER  # type: ignore[attr-defined]
    runtime._running = False  # type: ignore[attr-defined]
    runtime._state = "stopped"  # type: ignore[attr-defined]
    runtime._stop_event = threading.Event()  # type: ignore[attr-defined]
    for name, value in attrs.items():
        setattr(runtime, name, value)
    return runtime


class _ReconcilingBroker:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc

    def reconcile(self) -> None:
        if self.exc is not None:
            raise self.exc


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def test_reconcile_connection_error_is_transient_and_not_recorded() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(requests.exceptions.ConnectionError("refused")))
    with pytest.raises(TransientStartupError, match="transient network failure"):
        runtime.start()
    # G2/G3 boundary: no permanent incident and no halt are recorded.
    assert getattr(runtime, "_circuit_breaker_reason", None) is None
    assert runtime._state == "starting"


def test_reconcile_timeout_is_transient() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(requests.exceptions.Timeout("read timed out")))
    with pytest.raises(TransientStartupError):
        runtime.start()
    assert getattr(runtime, "_circuit_breaker_reason", None) is None


def test_reconcile_http_5xx_and_429_are_transient() -> None:
    for status in (500, 502, 503, 429):
        runtime = _bare_runtime(broker=_ReconcilingBroker(_http_error(status)))
        with pytest.raises(TransientStartupError):
            runtime.start()
        assert getattr(runtime, "_circuit_breaker_reason", None) is None, status


def test_reconcile_http_4xx_is_permanent() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(_http_error(401)))
    with pytest.raises(requests.exceptions.HTTPError):
        runtime.start()
    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert runtime._state == "halted"


def test_reconcile_protection_failure_stays_permanent() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(ProtectionReconciliationError("managed qty mismatch")))
    with pytest.raises(ProtectionReconciliationError, match="managed qty mismatch"):
        runtime.start()
    assert runtime._circuit_breaker_reason == "protection_failure"
    assert runtime._state == "halted"


def test_reconcile_ownership_drift_stays_permanent() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(OwnershipDriftError("broker qty below managed")))
    with pytest.raises(OwnershipDriftError):
        runtime.start()
    # OwnershipDriftError subclasses ProtectionReconciliationError, so the
    # protection reason applies (fail-closed, never retried).
    assert runtime._circuit_breaker_reason == "protection_failure"
    assert runtime._state == "halted"


def test_reconcile_ambiguous_order_state_stays_permanent() -> None:
    runtime = _bare_runtime(broker=_ReconcilingBroker(AmbiguousOrderState("submitting crash window")))
    with pytest.raises(AmbiguousOrderState):
        runtime.start()
    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert runtime._state == "halted"


def test_transient_classifier_table() -> None:
    from parayabuck.runtime import _is_transient_network_error

    transient = [
        requests.exceptions.ConnectionError("conn"),
        requests.exceptions.Timeout("timeout"),
        requests.exceptions.ConnectTimeout("connect timeout"),
        requests.exceptions.ReadTimeout("read timeout"),
        _http_error(500),
        _http_error(429),
        _http_error(503),
        ConnectionError("builtin conn"),
        TimeoutError("builtin timeout"),
    ]
    for exc in transient:
        assert _is_transient_network_error(exc), exc

    permanent = [
        _http_error(400),
        _http_error(401),
        _http_error(403),
        _http_error(404),
        ValueError("bad config"),
        KeyError("missing key"),
        ProtectionReconciliationError("protection"),
        OwnershipDriftError("drift"),
        AmbiguousOrderState("ambiguous"),
    ]
    for exc in permanent:
        assert not _is_transient_network_error(exc), exc


def test_transient_classifier_follows_wrapped_request_failures() -> None:
    """The Alpaca SDK raises APIError wrapping the requests HTTPError as
    __context__; the classifier must see through the wrapper (5xx transient,
    4xx permanent)."""
    from parayabuck.runtime import _is_transient_network_error

    try:
        raise _http_error(503)
    except requests.exceptions.RequestException as http_error:
        try:
            raise RuntimeError("alpaca APIError")
        except RuntimeError as api_error:
            wrapped = api_error
            captured = http_error
    assert wrapped.__context__ is captured
    assert _is_transient_network_error(wrapped)

    try:
        raise _http_error(401)
    except requests.exceptions.RequestException as http_error:
        try:
            raise RuntimeError("alpaca APIError")
        except RuntimeError as api_error:
            wrapped = api_error
            captured = http_error
    assert wrapped.__context__ is captured
    assert not _is_transient_network_error(wrapped)


def _full_paper_runtime(**overrides: Any) -> EngineRuntime:
    """A paper runtime whose start() reaches the warmup stage cheaply."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.PAPER  # type: ignore[attr-defined]
    runtime.paper = True  # type: ignore[attr-defined]
    runtime._running = False  # type: ignore[attr-defined]
    runtime._state = "stopped"  # type: ignore[attr-defined]
    runtime._stop_event = threading.Event()  # type: ignore[attr-defined]
    runtime._state_lock = threading.RLock()  # type: ignore[attr-defined]
    runtime._symbols_by_market = {Market.EQUITY: set(), Market.CRYPTO: set()}  # type: ignore[attr-defined]
    runtime._strategy_specs = ()  # type: ignore[attr-defined]
    runtime._portfolio_runtimes = ()  # type: ignore[attr-defined]
    runtime._managed_pin_symbols = set()  # type: ignore[attr-defined]
    runtime.broker = SimpleNamespace(  # type: ignore[attr-defined]
        account_id="acct",
        account=lambda: SimpleNamespace(positions=()),
        reconcile=lambda: None,
    )
    runtime.ledger = SimpleNamespace(  # type: ignore[attr-defined]
        managed_entries_for_account=lambda account_id: (),
        load_safety_incidents=lambda account_id: (),
        load_risk_state=lambda account_id, day: None,
        clear_safety_incident=lambda account_id, reason=None: None,
    )
    runtime.stream = SimpleNamespace(subscribe=lambda *a, **k: None)  # type: ignore[attr-defined]
    runtime.trading_updates = SimpleNamespace(run=lambda: None)  # type: ignore[attr-defined]
    runtime.app_config = SimpleNamespace(  # type: ignore[attr-defined]
        runtime=SimpleNamespace(
            execution_timeframe="5m",
            market_state_timeframe="5m",
            scanner_refresh_hours=4,
        ),
        universe=SimpleNamespace(
            equity=SimpleNamespace(
                enabled=False,
                dynamic_selection_enabled=False,
                maximum_stream_symbols=100,
                reference_symbols=(),
            ),
            crypto=SimpleNamespace(
                enabled=False,
                dynamic_selection_enabled=False,
                maximum_stream_symbols=100,
                pinned_symbols=(),
            ),
        ),
        risk=RiskLimits(),
        live=SimpleNamespace(
            enabled=False,
            hard_dollar_cap=1000,
            operator_approval_key_env="",
            operator_approval_path="",
        ),
        alpaca=SimpleNamespace(stock_historical_feed="iex", data_delayed=False),
        execution=SimpleNamespace(rebalance_band_pct=0.02, minimum_order_notional=10.0),
    )
    runtime.aggregators = {}  # type: ignore[attr-defined]
    runtime._events = None  # type: ignore[attr-defined]
    runtime._workers = {}  # type: ignore[attr-defined]
    runtime._not_ready_symbols = set()  # type: ignore[attr-defined]
    runtime.market_state = SimpleNamespace(allowed_trade_symbols=None)  # type: ignore[attr-defined]
    runtime._decision_loop = lambda: None  # type: ignore[attr-defined]
    runtime._account_loop = lambda: None  # type: ignore[attr-defined]
    runtime._run_stream_worker = lambda name, target: None  # type: ignore[attr-defined]
    runtime.stop = lambda: True  # type: ignore[attr-defined]
    for name, value in overrides.items():
        setattr(runtime, name, value)
    return runtime


def test_warmup_network_failure_is_transient() -> None:
    runtime = _full_paper_runtime()

    def _warmup() -> None:
        raise requests.exceptions.ConnectionError("data API down")

    runtime._warmup = _warmup  # type: ignore[attr-defined]
    with pytest.raises(TransientStartupError, match="transient network failure"):
        runtime.start()
    assert getattr(runtime, "_circuit_breaker_reason", None) is None


def test_warmup_non_network_failure_keeps_current_behavior() -> None:
    runtime = _full_paper_runtime()

    def _warmup() -> None:
        raise ValueError("unsupported timeframe")

    runtime._warmup = _warmup  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="unsupported timeframe"):
        runtime.start()
    assert runtime._state == "error"


def _tail_capable_runtime(**overrides: Any) -> EngineRuntime:
    """A paper runtime whose start() runs the whole way through the final
    account fetch (reconcile -> pin -> restore -> subscribe -> warmup ->
    account fetch -> readiness)."""
    runtime = _full_paper_runtime(**overrides)
    runtime._warmup = lambda: None  # type: ignore[attr-defined]
    runtime.symbols = []  # type: ignore[attr-defined]
    runtime._circuit_breaker_reason: str | None = None  # type: ignore[attr-defined]
    runtime._entry_risk_latch = {  # type: ignore[attr-defined]
        Market.EQUITY: None,
        Market.CRYPTO: None,
    }
    runtime.broker = SimpleNamespace(  # type: ignore[attr-defined]
        account_id="acct",
        reconcile=lambda: None,
        account=lambda: SimpleNamespace(
            equity=100_000.0,
            daily_pnl=0.0,
            drawdown_pct=0.0,
            realized_pnl=0.0,
            peak_equity=100_000.0,
            unrealized_pnl=0.0,
            positions=(),
        ),
    )
    runtime.ledger = SimpleNamespace(  # type: ignore[attr-defined]
        managed_entries_for_account=lambda account_id: (),
        load_safety_incidents=lambda account_id: (),
        load_risk_state=lambda account_id, day: None,
        clear_safety_incident=lambda account_id, reason=None: None,
        save_safety_incident=lambda *args, **kwargs: None,
        save_risk_state=lambda *args, **kwargs: None,
        total_fees_for_account=lambda account_id: 0.0,
    )
    return runtime


def test_final_account_fetch_network_failure_is_transient() -> None:
    """The startup tail (account fetch after warmup) must classify network
    failures as transient too, so the auto-start loop retries instead of
    aborting permanently after a successful warmup."""
    runtime = _tail_capable_runtime()

    def _account() -> object:
        raise requests.exceptions.ConnectionError("account API down")

    runtime.broker.account = _account  # type: ignore[attr-defined]
    with pytest.raises(TransientStartupError, match="account fetch"):
        runtime.start()
    assert getattr(runtime, "_circuit_breaker_reason", None) is None


def test_verified_startup_reconcile_clears_persisted_incidents() -> None:
    """A successful startup reconciliation is the same verified recovery the
    periodic path uses: persisted PERSISTENT_INCIDENTs must be cleared so a
    restart cannot resurrect an already-resolved halt."""
    cleared: list[tuple[str, str | None]] = []
    runtime = _tail_capable_runtime()
    runtime.ledger = SimpleNamespace(  # type: ignore[attr-defined]
        managed_entries_for_account=lambda account_id: (),
        load_safety_incidents=lambda account_id: (),
        load_risk_state=lambda account_id, day: None,
        clear_safety_incident=lambda account_id, reason=None: cleared.append((account_id, reason)),
        save_risk_state=lambda *args, **kwargs: None,
        total_fees_for_account=lambda account_id: 0.0,
    )
    runtime.start()
    # The verified startup reconcile cleared ALL persisted incidents; the
    # account-poll latch mirror may add a no-breach drawdown clear after.
    assert cleared and cleared[0] == ("acct", None)


def test_restart_rebalance_resume_deferred_while_breaker_active() -> None:
    """A restored PERSISTENT_INCIDENT must hold on the restart-recovery path:
    the persisted two-phase rebalance resume is deferred while the circuit
    breaker is active, so a cross-day restart cannot submit an entry BUY
    while a halt that was never verified is still in force."""
    advanced: list[Market] = []
    runtime = _tail_capable_runtime()
    runtime._circuit_breaker_reason = "ownership_drift"  # type: ignore[attr-defined]
    runtime._portfolio_runtimes = (  # type: ignore[attr-defined]
        SimpleNamespace(market=Market.EQUITY),
        SimpleNamespace(market=Market.CRYPTO),
    )
    runtime._advance_portfolio_rebalance = advanced.append  # type: ignore[attr-defined]
    runtime.start()
    assert advanced == []


# ---------------------------------------------------------------------------
# Web-side auto-start retry loop
# ---------------------------------------------------------------------------


class _FlakyRuntime:
    """Fake EngineRuntime: fails ``start()`` ``failures`` times, then works."""

    def __init__(self, failures: int = 0, *, transient: bool = True, **kwargs: Any) -> None:
        self.failures = failures
        self.transient = transient
        self.broker = SimpleNamespace(account_id="acct")
        self.is_running = False
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self.failures > 0:
            self.failures -= 1
            if self.transient:
                raise TransientStartupError("Alpaca API timeout")
            raise RuntimeError("ownership safety failure")
        self.is_running = True

    def stop(self) -> bool:
        self.stop_calls += 1
        self.is_running = False
        return True

    def health(self) -> Any:
        return SimpleNamespace(state="running")


def _manager_with(monkeypatch: Any, flaky: _FlakyRuntime) -> server.EngineManager:
    monkeypatch.setattr(runtime_module, "EngineRuntime", lambda **kwargs: flaky)
    monkeypatch.setattr(
        server,
        "load_config",
        lambda _path: SimpleNamespace(runtime=SimpleNamespace(mode=SimpleNamespace(value="paper"))),
    )
    manager = server.EngineManager()
    manager.config_path = Path("config/settings.yaml")
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))
    return manager


async def _wait_for_retry_pending(manager: server.EngineManager) -> None:
    for _ in range(500):
        if manager._retry_task is not None:
            return
        await _REAL_SLEEP(0.001)
    raise AssertionError("auto-start retry was never scheduled")


def test_first_transient_fails_second_succeeds(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=1)
    manager = _manager_with(monkeypatch, flaky)

    asyncio.run(manager.auto_start_engine())

    assert flaky.start_calls == 2
    assert manager.engine_state == "running"
    assert manager.runtime is flaky
    assert manager.last_engine_error is None
    # Successful start resets the retry state.
    assert manager._retry_attempt == 0
    assert manager._retry_next_at is None
    assert manager._retry_task is None


def test_backoff_is_bounded_and_capped(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=5)
    manager = _manager_with(monkeypatch, flaky)
    recorded_delays: list[float] = []
    attempts_at_sleep: list[int] = []

    async def _recording_sleep(delay: float) -> None:
        recorded_delays.append(delay)
        attempts_at_sleep.append(manager._retry_attempt)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(server.asyncio, "sleep", _recording_sleep)

    asyncio.run(manager.auto_start_engine())

    assert flaky.start_calls == 6  # five failures then success
    assert recorded_delays == [5, 15, 30, 60, 60]  # capped at 60s
    assert attempts_at_sleep == [1, 2, 3, 4, 5]  # attempt counter climbs
    assert manager._retry_attempt == 0  # reset after success


def test_permanent_failure_stops_retrying(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=999, transient=False)
    manager = _manager_with(monkeypatch, flaky)

    with pytest.raises(RuntimeError, match="ownership safety failure"):
        asyncio.run(manager.auto_start_engine())

    assert flaky.start_calls == 1  # no automatic infinite retry
    assert manager.engine_state == "error"
    assert manager.last_engine_error == "ownership safety failure"
    assert manager.runtime is None
    assert manager._retry_attempt == 0


def test_manual_start_transient_failure_goes_to_error_without_retry(
    monkeypatch: Any,
) -> None:
    flaky = _FlakyRuntime(failures=999)
    manager = _manager_with(monkeypatch, flaky)

    with pytest.raises(TransientStartupError):
        manager.start_engine()  # manual path: no auto_start, no retry

    assert manager.engine_state == "error"
    assert manager.last_engine_error == "transient: Alpaca API timeout"
    assert manager._retry_attempt == 0
    assert flaky.start_calls == 1


def test_stop_while_waiting_retry_cancels_it(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=999)
    manager = _manager_with(monkeypatch, flaky)

    async def scenario() -> None:
        task = asyncio.create_task(manager.auto_start_engine())
        await _wait_for_retry_pending(manager)
        assert manager.engine_state == "starting"
        manager.stop_engine()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert manager.engine_state == "stopped"
    assert manager.runtime is None
    assert manager.last_engine_error is None
    assert manager._retry_attempt == 0
    assert flaky.start_calls == 1  # no attempt fired after the cancellation


def test_shutdown_while_waiting_retry_cancels_it(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=999)
    manager = _manager_with(monkeypatch, flaky)
    monkeypatch.setattr(manager, "should_auto_start", lambda: True)
    monkeypatch.setattr(server, "manager", manager)

    async def run_lifespan() -> None:
        async with server.app_lifespan(server.app):
            await _wait_for_retry_pending(manager)
            assert manager.engine_state == "starting"

    asyncio.run(run_lifespan())

    assert manager.engine_state == "stopped"
    assert manager.runtime is None
    assert flaky.start_calls == 1  # shutdown canceled the pending retry


def test_manual_start_while_retry_pending_is_refused(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=999)
    manager = _manager_with(monkeypatch, flaky)

    async def scenario() -> None:
        task = asyncio.create_task(manager.auto_start_engine())
        await _wait_for_retry_pending(manager)
        with pytest.raises(ValueError, match="auto-start retry is in progress"):
            manager.start_engine()  # manual start is refused, not duplicated
        assert manager.runtime is None
        assert manager.engine_state == "starting"
        assert flaky.start_calls == 1  # exactly one runtime attempt ever
        manager.stop_engine()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert manager.engine_state == "stopped"


def test_successful_start_resets_retry_counters(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=2)
    manager = _manager_with(monkeypatch, flaky)

    recorded_delays: list[float] = []

    async def _recording_sleep(delay: float) -> None:
        recorded_delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(server.asyncio, "sleep", _recording_sleep)

    asyncio.run(manager.auto_start_engine())

    assert flaky.start_calls == 3
    assert recorded_delays == [5, 15]
    assert manager.engine_state == "running"
    assert manager._retry_attempt == 0
    assert manager._retry_next_at is None
    assert manager._retry_task is None
    assert manager.last_engine_error is None


def test_status_exposes_retry_info_only_while_retrying(monkeypatch: Any) -> None:
    flaky = _FlakyRuntime(failures=999)
    manager = _manager_with(monkeypatch, flaky)
    monkeypatch.setattr(server, "manager", manager)
    # get_status needs a parseable-but-harmless config in this test: the
    # universe payloads are missing files, the validators are stubbed.
    monkeypatch.setattr(
        server,
        "load_config",
        lambda _path: SimpleNamespace(
            runtime=SimpleNamespace(mode=SimpleNamespace(value="paper")),
            strategy=SimpleNamespace(
                equity=SimpleNamespace(enabled=False),
                crypto=SimpleNamespace(enabled=False),
            ),
            universe=SimpleNamespace(
                equity=SimpleNamespace(
                    candidate_universe_path="no/such/candidates.json",
                    active_universe_path="no/such/active.json",
                ),
                crypto=SimpleNamespace(active_universe_path="no/such/crypto.json"),
            ),
        ),
    )
    monkeypatch.setattr(server, "validate_active_universe", lambda equity: ([], "ok"))
    monkeypatch.setattr(server, "validate_active_crypto_universe", lambda crypto: ([], "ok"))

    async def scenario() -> None:
        task = asyncio.create_task(manager.auto_start_engine())
        await _wait_for_retry_pending(manager)
        status = await server.get_status()
        assert status["engine"]["retry"]["attempt"] == 1
        assert status["engine"]["retry"]["retry_in_seconds"] <= 5.0
        assert "next_retry_at" in status["engine"]["retry"]
        manager.stop_engine()
        with pytest.raises(asyncio.CancelledError):
            await task
        idle = await server.get_status()
        assert "engine" not in idle  # settled engine exposes no retry noise

    asyncio.run(scenario())
