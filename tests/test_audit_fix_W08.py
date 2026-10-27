"""Audit 2026-10-27 fixes owned by worker W08 (runtime.py account loop, trade
updates, deferred BUY advance, exit-SELL retry, fresh decision, readiness
backoff and stream restart delays).

Each test drives one invariant with synthetic fakes. Locks are replaced by
doubles that either forbid acquisition or flip a gate on entry, which makes
the check-then-lock races deterministic.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.alpaca_updates import TradeUpdate
from parayabuck.runtime import EngineRuntime, WorkerHealth


def _base_runtime() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._startup_committed = True
    runtime._warming_up = False
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {}
    runtime._market_entry_halts = {}
    runtime._portfolio_runtimes = ()
    return runtime


class _ForbiddenLock:
    """Stands in for _portfolio_lock and counts acquisitions. Callers assert
    ``entries == 0`` afterwards, because an exception raised here would be
    swallowed by a caller's ``except Exception``."""

    def __init__(self) -> None:
        self.entries = 0

    def __enter__(self) -> None:
        self.entries += 1
        raise AssertionError("portfolio lock must not be taken on this path")

    def __exit__(self, *exc: object) -> bool:
        return False


class _LockThatFlipsGate:
    """Simulates a halt or latch raised by another thread while this one waits
    for the portfolio lock: the gate changes between the pre-lock read and the
    locked section."""

    def __init__(self, runtime: EngineRuntime, **changes: Any) -> None:
        self._runtime = runtime
        self._changes = changes

    def __enter__(self) -> _LockThatFlipsGate:
        for name, value in self._changes.items():
            setattr(self._runtime, name, value)
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


# --- A-1 / CC-6: the deferred-BUY advance -----------------------------------


class _AdvancePortfolio:
    market = Market.EQUITY

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def advance_rebalance(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        if self.raises:
            raise TypeError("internal failure inside the advance")
        return SimpleNamespace(status="buy_ready", decision_id="dec-1")


def _advance_runtime(portfolio: _AdvancePortfolio) -> EngineRuntime:
    runtime = _base_runtime()
    runtime._portfolio_runtimes = (portfolio,)
    # The equity signal-basis check may hit the provider; it is not under test.
    runtime._equity_signal_basis_latch = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    return runtime


def test_advance_never_retries_a_type_error_with_the_buy_gate_dropped() -> None:
    """A TypeError raised inside advance_rebalance used to re-run the advance
    without submit_deferred_buys, which defaults to True: a closed BUY gate was
    then fail-open and the side effects repeated."""
    portfolio = _AdvancePortfolio(raises=True)
    runtime = _advance_runtime(portfolio)
    assert runtime._advance_portfolio_rebalance(Market.EQUITY) is None
    assert len(portfolio.calls) == 1
    assert portfolio.calls[0]["submit_deferred_buys"] is True


def test_advance_submits_deferred_buys_when_all_gates_are_open() -> None:
    portfolio = _AdvancePortfolio()
    runtime = _advance_runtime(portfolio)
    runtime._advance_portfolio_rebalance(Market.EQUITY)
    assert [call["submit_deferred_buys"] for call in portfolio.calls] == [True]


def test_advance_rechecks_the_halt_gate_under_the_portfolio_lock() -> None:
    """A halt raised while the advance waited for _portfolio_lock must withhold
    the deferred BUY (CC-6)."""
    portfolio = _AdvancePortfolio()
    runtime = _advance_runtime(portfolio)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _circuit_breaker_reason="ownership_drift")
    runtime._advance_portfolio_rebalance(Market.EQUITY)
    assert len(portfolio.calls) == 1
    assert portfolio.calls[0]["submit_deferred_buys"] is False


# --- C8-1 / C9-1: the rejected-SELL retry hook -------------------------------


class _ExitRepository:
    def __init__(self, rows: list[dict[str, str]], retried: tuple[str, ...] = ()) -> None:
        self._rows = tuple(rows)
        self._retried = set(retried)

    def latest_decision_id(self, _account_id: str, _market: Market) -> str:
        return "dec-1"

    def orders_for_decision(self, _decision_id: str, side: str | None = None) -> tuple[dict[str, str], ...]:
        return self._rows

    def has_order(self, client_order_id: str) -> bool:
        return client_order_id in self._retried


class _ExitPortfolio:
    market = Market.EQUITY
    account_id = "acct"

    def __init__(self, repository: _ExitRepository, submitted: tuple[str, ...] = ()) -> None:
        self.repository = repository
        self._submitted = submitted
        self.calls: list[object] = []

    def retry_rejected_exit_sells(self, account: object, *, submit_gate: object = None) -> tuple[str, ...]:
        self.calls.append(account)
        return self._submitted


_REJECTED_SELL = [{"client_order_id": "sell-1", "status": "rejected", "symbol": "QLD"}]


def _exit_runtime(portfolio: _ExitPortfolio, monkeypatch: pytest.MonkeyPatch) -> tuple[EngineRuntime, list[int]]:
    runtime = _base_runtime()
    runtime._portfolio_runtimes = (portfolio,)
    invalidations: list[int] = []
    monkeypatch.setattr(
        runtime, "_invalidate_account_snapshot_after_execution", lambda: invalidations.append(1), raising=False
    )
    return runtime, invalidations


def test_exit_retry_without_a_pending_rejected_sell_skips_the_portfolio_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ExitPortfolio(_ExitRepository([{"client_order_id": "sell-1", "status": "filled", "symbol": "QLD"}]))
    runtime, _ = _exit_runtime(portfolio, monkeypatch)
    forbidden = _ForbiddenLock()
    runtime._portfolio_lock = forbidden
    runtime._maybe_retry_rejected_exit_sells(object())
    assert portfolio.calls == []
    assert forbidden.entries == 0


def test_exit_retry_already_persisted_skips_the_portfolio_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rejected row keeps its status after its one retry was persisted; the
    pre-check must not take the lock on every pass for it."""
    portfolio = _ExitPortfolio(_ExitRepository(_REJECTED_SELL, retried=("sell-1-retry1",)))
    runtime, _ = _exit_runtime(portfolio, monkeypatch)
    forbidden = _ForbiddenLock()
    runtime._portfolio_lock = forbidden
    runtime._maybe_retry_rejected_exit_sells(object())
    assert portfolio.calls == []
    assert forbidden.entries == 0


def test_pending_rejected_sell_is_retried_once_under_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ExitPortfolio(_ExitRepository(_REJECTED_SELL), submitted=("sell-1-retry1",))
    runtime, invalidations = _exit_runtime(portfolio, monkeypatch)
    runtime._maybe_retry_rejected_exit_sells(object())
    assert len(portfolio.calls) == 1
    assert invalidations == [1]


def test_halt_raised_while_waiting_for_the_lock_stops_the_exit_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ExitPortfolio(_ExitRepository(_REJECTED_SELL), submitted=("sell-1-retry1",))
    runtime, invalidations = _exit_runtime(portfolio, monkeypatch)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _circuit_breaker_reason="ownership_drift")
    runtime._maybe_retry_rejected_exit_sells(object())
    assert portfolio.calls == []
    assert invalidations == []


def test_market_entry_halt_raised_while_waiting_stops_that_markets_exit_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ExitPortfolio(_ExitRepository(_REJECTED_SELL), submitted=("sell-1-retry1",))
    runtime, _ = _exit_runtime(portfolio, monkeypatch)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _market_entry_halts={Market.EQUITY: "ambiguous_rebalance"})
    runtime._maybe_retry_rejected_exit_sells(object())
    assert portfolio.calls == []


# --- C9-2: the fresh decision re-reads its gates under the lock ---------------


class _Captured(BaseException):
    """Escapes the decision path's ``except Exception`` handlers so the test
    can read the arguments of process_closed_bars."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__("captured")
        self.kwargs = kwargs


class _DecisionPortfolio:
    market = Market.CRYPTO
    symbols = ("BTC/USD",)
    strategy = SimpleNamespace(timeframe="1d", name="test")

    def process_closed_bars(self, _bars: list[Bar], **kwargs: Any) -> None:
        raise _Captured(kwargs)


def _decision_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[EngineRuntime, list[str], list[str]]:
    runtime = _base_runtime()
    portfolio = _DecisionPortfolio()
    runtime._portfolio_runtimes = (portfolio,)
    runtime.broker = SimpleNamespace(account_id="acct")
    account = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    lease = SimpleNamespace(snapshot=account)
    runtime._entry_readiness_reasons = lambda _market, **_kwargs: ()  # type: ignore[method-assign]
    runtime._fresh_account_snapshot_lease = lambda **_kwargs: lease  # type: ignore[method-assign]
    runtime._account_snapshot_lease_is_current = lambda _lease: True  # type: ignore[method-assign]
    runtime._claim_account_snapshot_lease = lambda _lease: True  # type: ignore[method-assign]
    runtime._release_account_snapshot_lease = lambda _lease: None  # type: ignore[method-assign]
    deferred: list[str] = []
    decided: list[str] = []
    runtime._defer_portfolio_batch = (  # type: ignore[method-assign]
        lambda _bars, _market, reason: deferred.append(reason) or True
    )
    return runtime, deferred, decided


_CRYPTO_BAR = [Bar(datetime(2026, 10, 26, tzinfo=UTC), "BTC/USD", "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.CRYPTO)]


def test_decision_picks_up_a_latch_raised_while_waiting_for_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _deferred, _ = _decision_runtime(monkeypatch)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _entry_risk_latch={Market.CRYPTO: "daily_loss"})
    with pytest.raises(_Captured) as captured:
        runtime._process_portfolio_timestamp(list(_CRYPTO_BAR))
    assert captured.value.kwargs["entry_latch"] == "daily_loss"


def test_decision_is_deferred_when_reconciliation_fails_while_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, deferred, _ = _decision_runtime(monkeypatch)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _reconcile_failed=True)
    runtime._process_portfolio_timestamp(list(_CRYPTO_BAR))
    assert deferred == ["reconciliation_failed"]


def test_decision_is_dropped_when_a_hard_halt_is_raised_while_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, deferred, _ = _decision_runtime(monkeypatch)
    runtime._portfolio_lock = _LockThatFlipsGate(runtime, _circuit_breaker_reason="ownership_drift")
    runtime._process_portfolio_timestamp(list(_CRYPTO_BAR))
    assert deferred == []


# --- Trade-update handler: C6/Perf 6, CC-9 and the refused-fill add-on ------


class _TradeRepository:
    def __init__(self, runtime: EngineRuntime, *, applied: bool | None = True) -> None:
        self._runtime = runtime
        self._applied = applied
        self.statuses: list[str] = []
        self.threads: dict[str, int] = {}
        self.generation_at_booking: int | None = None

    def has_order(self, client_order_id: str) -> bool:
        self.threads["has_order"] = threading.get_ident()
        return client_order_id == "sell-1"

    def order_side(self, client_order_id: str) -> str | None:
        self.threads["order_side"] = threading.get_ident()
        return "sell"

    def apply_trade_update(self, **kwargs: Any) -> bool | None:
        self.threads["apply"] = threading.get_ident()
        self.generation_at_booking = int(getattr(self._runtime, "_account_snapshot_generation", 0))
        self.statuses.append(kwargs["status"])
        return self._applied


def _trade_runtime(repository: _TradeRepository) -> tuple[EngineRuntime, dict[str, Any]]:
    runtime = _base_runtime()
    fresh = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    record: dict[str, Any] = {"persist_threads": [], "advance": [], "reconcile": 0}
    runtime._portfolio_repository = repository
    runtime.broker = SimpleNamespace(account_id="acct", snapshot_if_fresh=lambda _age: fresh, account=lambda: fresh)
    runtime.config = SimpleNamespace(account_poll_interval_seconds=15.0, maximum_account_age_for_entry_seconds=30.0)
    runtime._last_account_at = datetime.now(UTC)
    runtime._last_account_snapshot = fresh
    runtime._last_account_snapshot_at = datetime.now(UTC)

    def persist(_account: AccountSnapshot) -> None:
        record["persist_threads"].append(threading.get_ident())

    def advance(market: Market) -> None:
        record["advance"].append(market)

    def reconcile() -> None:
        record["reconcile"] += 1

    runtime._persist_risk_state = persist  # type: ignore[method-assign]
    runtime._advance_portfolio_rebalance = advance  # type: ignore[method-assign]
    runtime._schedule_reconcile = reconcile  # type: ignore[method-assign]
    return runtime, record


def _fill_update() -> TradeUpdate:
    return TradeUpdate(
        event="fill",
        client_order_id="sell-1",
        broker_order_id="broker-sell-1",
        symbol="QLD",
        market=Market.EQUITY,
        filled_qty=10.0,
        average_fill_price=100.0,
        timestamp=datetime.now(UTC),
        fee=0.0,
        activity_id="activity-fill-1",
    )


def test_invalidates_the_account_snapshot_before_booking_a_terminal_fill() -> None:
    """CC-9: a decision must not claim the pre-fill snapshot while the fill is
    being booked, so the generation is bumped before the booking as well."""
    repository = _TradeRepository(None)  # type: ignore[arg-type]
    runtime, _record = _trade_runtime(repository)
    repository._runtime = runtime
    start = int(getattr(runtime, "_account_snapshot_generation", 0))
    asyncio.run(runtime._handle_trade_update(_fill_update()))
    assert repository.statuses == ["fill"]
    assert repository.generation_at_booking is not None
    assert repository.generation_at_booking > start


def test_trade_update_sqlite_calls_run_off_the_event_loop_thread() -> None:
    """Perf 6: has_order, the booking and risk-state persistence are synchronous
    SQLite work and must not run on the asyncio loop thread."""
    repository = _TradeRepository(None)  # type: ignore[arg-type]
    runtime, record = _trade_runtime(repository)
    repository._runtime = runtime
    loop_thread = threading.get_ident()
    asyncio.run(runtime._handle_trade_update(_fill_update()))
    assert repository.threads["has_order"] != loop_thread
    assert repository.threads["apply"] != loop_thread
    assert record["persist_threads"] and all(ident != loop_thread for ident in record["persist_threads"])


def test_refused_fill_schedules_a_reconcile_and_stops_the_handler() -> None:
    """The repository returned False: nothing was booked, so the fill must be
    recovered from the broker by reconciliation, and the handler must not
    advance or persist from it."""
    repository = _TradeRepository(None, applied=False)  # type: ignore[arg-type]
    runtime, record = _trade_runtime(repository)
    repository._runtime = runtime
    asyncio.run(runtime._handle_trade_update(_fill_update()))
    assert repository.statuses == ["fill"]
    assert record["reconcile"] == 1
    assert record["advance"] == []
    assert record["persist_threads"] == []


def test_booked_fill_does_not_schedule_a_reconcile() -> None:
    repository = _TradeRepository(None, applied=True)  # type: ignore[arg-type]
    runtime, record = _trade_runtime(repository)
    repository._runtime = runtime
    asyncio.run(runtime._handle_trade_update(_fill_update()))
    assert record["reconcile"] == 0
    assert record["advance"] == [Market.EQUITY]


# --- Perf 1: the risk-state fee total reuses the snapshot's fees ---------------


class _Ledger:
    def __init__(self) -> None:
        self.saved: list[Any] = []
        self.total_calls: list[str] = []

    def save_safety_incident(self, _account_id: str, _reason: str) -> None:
        return None

    def save_risk_state(self, state: Any) -> None:
        self.saved.append(state)

    def total_fees_for_account(self, account_id: str) -> float:
        self.total_calls.append(account_id)
        return 999.0


def _persist_runtime(account_fees: dict[str, float]) -> tuple[EngineRuntime, _Ledger, Any]:
    runtime = _base_runtime()
    ledger = _Ledger()
    runtime.ledger = ledger
    runtime.broker = SimpleNamespace(account_id="acct", _peak_epoch=0)
    runtime._evaluate_entry_risk_latch = lambda _account: None  # type: ignore[method-assign]
    account = SimpleNamespace(
        equity=100.0, daily_pnl=1.0, peak_equity=100.0, realized_pnl=0.0, unrealized_pnl=0.0, **account_fees
    )
    return runtime, ledger, account


def test_risk_state_fees_come_from_the_snapshot_not_a_second_aggregate() -> None:
    runtime, ledger, account = _persist_runtime({"fees": 12.5})
    runtime._persist_risk_state(account)
    assert ledger.total_calls == []
    assert ledger.saved[-1].fees == 12.5


def test_risk_state_falls_back_to_the_ledger_for_an_account_without_fees() -> None:
    runtime, ledger, account = _persist_runtime({})
    runtime._persist_risk_state(account)
    assert ledger.total_calls == ["acct"]
    assert ledger.saved[-1].fees == 999.0


# --- C8-3: capacity_health does not take the portfolio lock ------------------


def test_capacity_health_reads_limits_without_the_portfolio_lock() -> None:
    runtime = _base_runtime()
    admission = SimpleNamespace(
        account=SimpleNamespace(positions=(), equity=100.0, cash=50.0),
        realized_gross=0.0,
        gross_headroom=10.0,
        committed_buy_notional=0.0,
        cash_headroom=5.0,
    )
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            current_buy_capacity=lambda: admission,
            risk_overlay=SimpleNamespace(limits=SimpleNamespace(maximum_positions=5)),
            last_buy_admission=None,
        ),
    )
    runtime.broker = SimpleNamespace(account_id="acct", working_orders=lambda: (), _field=lambda *_a, **_k: "")
    runtime._portfolio_repository = SimpleNamespace(open_orders=lambda _account_id: ())
    forbidden = _ForbiddenLock()
    runtime._portfolio_lock = forbidden
    health = runtime.capacity_health()
    assert health["maximum_positions"] == 5
    assert forbidden.entries == 0


# --- C9-5 / CC-10: readiness pass ---------------------------------------------


class _HistoryWithoutLiveIteration(dict):
    """A history whose live iteration fails, as it does when the portfolio
    lock holder inserts a key while the readiness pass walks it (CC-10). The
    readiness pass must work on a copy."""

    def values(self):  # type: ignore[override]
        raise RuntimeError("dict changed size during iteration")


def _daily_bars(symbol: str) -> list[Bar]:
    return [
        Bar(datetime(2025, 1, day, tzinfo=UTC), symbol, "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.EQUITY)
        for day in range(1, 6)
    ]


def test_refresh_readiness_walks_a_snapshot_of_the_history() -> None:
    runtime = _base_runtime()
    runtime._last_warmup_retry_at = time.monotonic()
    runtime._warmup_retry_attempt = 0
    runtime._warmup_retry_symbols = frozenset()
    runtime._not_ready_symbols = {"AAA"}
    runtime._historical = None
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("AAA",),
            strategy=SimpleNamespace(minimum_history_bars=5),
            history=_HistoryWithoutLiveIteration({"AAA": _daily_bars("AAA")}),
        ),
    )
    runtime._refresh_readiness()
    assert runtime._not_ready_symbols == set()


# --- C7-1: market stream worker backs off to minutes past the restart limit ---


def _stream_runtime(monkeypatch: pytest.MonkeyPatch, *, restart_count: int, emitted: list[Any]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    worker = WorkerHealth(last_heartbeat=datetime.now(UTC), restart_count=restart_count, alive=True)
    runtime._workers = {"pb-stocks": worker}
    runtime._running = True
    runtime._state = "running"
    runtime._circuit_breaker_reason = None
    runtime._state_lock = threading.RLock()
    runtime._market_entry_halts = {Market.EQUITY: None}

    def record_alert(kind: str, **fields: Any) -> None:
        emitted.append((kind, fields.get("message")))

    monkeypatch.setattr(runtime_module.alerts, "emit", record_alert)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _event: None)
    return runtime


def _run_failing_market_worker(runtime: EngineRuntime, failures: int) -> list[float]:
    delays: list[float] = []

    def wait(seconds: float) -> bool:
        delays.append(seconds)
        if len(delays) == failures:
            runtime._running = False
        return False

    runtime._stop_event = SimpleNamespace(wait=wait)

    def failing() -> None:
        raise RuntimeError("simulated stream outage")

    runtime._run_stream_worker("pb-stocks", failing)
    return delays


def test_market_worker_retry_delay_grows_to_minutes_past_the_restart_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[Any] = []
    runtime = _stream_runtime(monkeypatch, restart_count=5, emitted=emitted)
    delays = _run_failing_market_worker(runtime, failures=4)
    # Restart counts 6..9: 60 s doubling, capped at 300 s (was 16 s forever).
    assert delays == [60.0, 120.0, 240.0, 300.0]
    # The threshold alert is still edge-triggered: one alert, no periodic one yet.
    assert [kind for kind, _message in emitted] == ["circuit_breaker"]


def test_market_worker_emits_one_periodic_still_failing_alert_per_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[Any] = []
    runtime = _stream_runtime(monkeypatch, restart_count=5, emitted=emitted)
    monkeypatch.setattr(runtime_module, "_STREAM_STILL_FAILING_ALERT_SECONDS", 0.0)
    _run_failing_market_worker(runtime, failures=4)
    messages = [message for _kind, message in emitted]
    assert len(messages) == 4
    assert messages[0] == "pb-stocks stream worker restart limit reached; entries halted"
    assert all("still failing" in message for message in messages[1:])
