"""Audit wave 3 fixes W04: the rebalance advance latch (C3-2-1, P3-1-4), the
account-loop readvance and retry locking (C3-2-2, C3-5-1, P3-1-5, C3-2-3,
Q3-2-2), the trade-update BUY startup gate (C3-2-4), equity SELLs under the
cached basis latch (P3-3-2), the retry startup cancel (R3-4-4) and the
incident-recovery reconcile lock (C3-1-1).

Every test is deterministic: fixed fixtures, tmp_path state, no network,
no wall-clock dependence beyond monotonic rate-limit state set in the test.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.monitoring import alerts
from parayabuck.portfolio.runtime_models import REBALANCE_PHASE_BUY_READY
from parayabuck.runtime import EngineRuntime, TradingState
from tests.test_audit2_fix_B1 import _DAY, _bars, _Broker, _position, _runtime

_LATCH = "rebalance_reconciliation_required"


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def emit(kind: str, *, message: str, **fields: Any) -> None:
        events.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(alerts, "emit", emit)
    return events


class _Portfolio:
    """A portfolio runtime stub: records every advance and runs one outcome per call."""

    market = Market.EQUITY
    account_id = "acct"

    def __init__(self, outcomes: list[Any] | None = None, phase: str | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[dict[str, Any]] = []
        self.repository = _PhaseRepository(phase)

    def advance_rebalance(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        return None


class _PhaseRepository:
    def __init__(self, phase: str | None) -> None:
        self.phase = phase

    def current_rebalance_execution(self, _account_id: str, _market: Market) -> Any:
        return None if self.phase is None else SimpleNamespace(phase=self.phase)


def _engine(*portfolios: Any) -> EngineRuntime:
    """An engine shell with the flags the advance and readvance paths read."""
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._running = True
    engine._warming_up = False
    engine._startup_committed = True
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._portfolio_lock = threading.RLock()
    engine._state_lock = threading.RLock()
    engine._entry_risk_latch = {}
    engine._market_entry_halts = {}
    engine._portfolio_runtimes = list(portfolios)
    engine._equity_signal_basis_latch = lambda: None  # type: ignore[method-assign]
    engine._equity_signal_basis_cached_latch = lambda: None  # type: ignore[method-assign]
    return engine


def _hold_lock_in_another_thread(lock: Any) -> tuple[threading.Event, threading.Thread]:
    """Hold ``lock`` from a second thread until the returned event is set."""
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with lock:
            acquired.set()
            release.wait(5)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert acquired.wait(5)
    return release, thread


# -- C3-2-1 / P3-1-4: the entry-halt latch lifts only on an advance that submitted --


def test_a_no_op_advance_under_another_latch_does_not_lift_the_failure_latch(emitted: list[dict[str, Any]]) -> None:
    portfolio = _Portfolio(outcomes=[RuntimeError("broker unreachable")])
    engine = _engine(portfolio)
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert engine._market_entry_halts[Market.EQUITY] == _LATCH

    # A daily-loss entry latch now holds the market: the real trading_state is
    # REDUCING, so the advance is a no-op and proves nothing about the broker.
    engine._entry_risk_latch = {Market.EQUITY: "daily_loss"}
    assert engine.trading_state(Market.EQUITY) is TradingState.REDUCING
    engine._advance_portfolio_rebalance(Market.EQUITY)

    assert portfolio.calls[1]["submit_deferred_buys"] is False
    assert engine._market_entry_halts[Market.EQUITY] == _LATCH
    assert Market.EQUITY in engine._advance_failure_latched
    assert Market.EQUITY in engine._rebalance_advance_failures


def test_the_failure_latch_holds_under_the_real_trading_state_until_an_advance_submits(
    emitted: list[dict[str, Any]],
) -> None:
    """The failure latch is itself an entry halt, so the real trading state is REDUCING while it holds.

    An advance that cannot submit therefore never lifts it. Lifting needs an
    advance that submits (checked with the trading state permitted, below).
    """
    portfolio = _Portfolio(outcomes=[RuntimeError("broker unreachable")])
    engine = _engine(portfolio)
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert engine.trading_state(Market.EQUITY) is TradingState.REDUCING

    engine._advance_portfolio_rebalance(Market.EQUITY)

    assert portfolio.calls[1]["submit_deferred_buys"] is False
    assert engine._market_entry_halts[Market.EQUITY] == _LATCH


def test_the_failure_latch_lifts_on_an_advance_that_submits(emitted: list[dict[str, Any]]) -> None:
    portfolio = _Portfolio(outcomes=[RuntimeError("broker unreachable")])
    engine = _engine(portfolio)
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert engine._market_entry_halts[Market.EQUITY] == _LATCH

    # Submission permitted (the B1 model of a recovered market): the advance submits and lifts.
    engine.trading_state = lambda market: TradingState.ACTIVE  # type: ignore[method-assign]
    engine._advance_portfolio_rebalance(Market.EQUITY)

    assert portfolio.calls[1]["submit_deferred_buys"] is True
    assert engine._market_entry_halts[Market.EQUITY] is None
    assert engine._rebalance_advance_failures == {}


# -- C3-2-2 / P3-1-2 / C3-5-1 / P3-1-5 / C3-2-3: the account-loop readvance --


def _ready_portfolio() -> _Portfolio:
    return _Portfolio(phase=REBALANCE_PHASE_BUY_READY)


def test_readvance_skips_a_held_lock_and_retries_on_the_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ready_portfolio()
    engine = _engine(portfolio)
    account = SimpleNamespace(equity=1.0)
    release, thread = _hold_lock_in_another_thread(engine._portfolio_lock)
    try:
        engine._maybe_readvance_held_buy_side(account)
        assert portfolio.calls == []
        # The rate limit was not spent: the pass did not run.
        assert getattr(engine, "_held_buy_readvance_last_mono", None) is None
    finally:
        release.set()
        thread.join(5)

    engine._maybe_readvance_held_buy_side(account)
    assert len(portfolio.calls) == 1
    assert portfolio.calls[0]["submit_deferred_buys"] is True


def test_readvance_skips_a_market_that_is_not_active(monkeypatch: pytest.MonkeyPatch) -> None:
    portfolio = _ready_portfolio()
    engine = _engine(portfolio)
    account = SimpleNamespace(equity=1.0)
    engine._market_entry_halts = {Market.EQUITY: _LATCH}

    engine._maybe_readvance_held_buy_side(account)
    assert portfolio.calls == []
    assert getattr(engine, "_held_buy_readvance_last_mono", None) is None

    engine._market_entry_halts = {Market.EQUITY: None}
    engine._maybe_readvance_held_buy_side(account)
    assert len(portfolio.calls) == 1


def test_readvance_skips_an_account_snapshot_invalidated_in_the_same_pass() -> None:
    portfolio = _ready_portfolio()
    engine = _engine(portfolio)
    first = SimpleNamespace(equity=1.0)
    engine._publish_account_snapshot(first)  # type: ignore[arg-type]
    # An execution earlier in the pass invalidates the cached snapshot.
    engine._invalidate_account_snapshot_after_execution()

    engine._maybe_readvance_held_buy_side(first)  # type: ignore[arg-type]
    assert portfolio.calls == []

    fresh = SimpleNamespace(equity=2.0)
    engine._publish_account_snapshot(fresh)  # type: ignore[arg-type]
    engine._maybe_readvance_held_buy_side(fresh)  # type: ignore[arg-type]
    assert len(portfolio.calls) == 1
    assert portfolio.calls[0]["account"] is fresh


class _GatedPortfolio:
    """A portfolio stub that submits its BUY through the engine's real submit gate, as the runtime does.

    The BUY sizing of the real runtime is not reproduced here; what is under test is that
    the account-loop readvance reaches the advance, the advance reaches the submit gate, and
    a permitted BUY is handed to the broker after the snapshot was published.
    """

    market = Market.EQUITY
    account_id = "acct"

    def __init__(self, broker: list[Any]) -> None:
        self.broker = broker
        self.repository = _PhaseRepository(REBALANCE_PHASE_BUY_READY)
        self.gate_results: list[str | None] = []

    def advance_rebalance(self, *, submit_deferred_buys: bool, submit_gate: Any, account: Any = None) -> None:
        if not submit_deferred_buys:
            return None
        for symbol in ("AAPL", "MSFT"):
            order = SimpleNamespace(symbol=symbol, side=PortfolioOrderSide.BUY, client_order_id=f"buy-{symbol}")
            reason = submit_gate(order)
            self.gate_results.append(reason)
            if reason is None:
                self.broker.append(order)
        return None


def test_readvance_reaches_the_broker_after_the_publish() -> None:
    """Q3-2-2: a held BUY side reaches the submit gate and the broker through the account loop, after the publish."""
    submitted: list[Any] = []
    portfolio = _GatedPortfolio(submitted)
    engine = _engine(portfolio)
    engine.broker = SimpleNamespace(account_id="acct")
    account = SimpleNamespace(equity=100_000.0)
    assert engine._publish_account_snapshot(account)  # type: ignore[arg-type]

    engine._maybe_readvance_held_buy_side(account)  # type: ignore[arg-type]

    assert portfolio.gate_results == [None, None]
    assert [order.symbol for order in submitted] == ["AAPL", "MSFT"]


def test_readvance_is_held_by_the_submit_gate_when_the_basis_is_unverified() -> None:
    submitted: list[Any] = []
    portfolio = _GatedPortfolio(submitted)
    engine = _engine(portfolio)
    engine._equity_signal_basis_cached_latch = lambda: "signal_adjustment_unverified"  # type: ignore[method-assign]
    account = SimpleNamespace(equity=100_000.0)
    engine._publish_account_snapshot(account)  # type: ignore[arg-type]

    engine._maybe_readvance_held_buy_side(account)  # type: ignore[arg-type]

    # The cached basis hold keeps the advance from submitting at all (no gate call, no broker call).
    assert submitted == []
    assert portfolio.gate_results == []


# -- C3-2-4: BUY advances from the trade-update path wait for the startup commit --


def test_trade_update_advance_holds_buys_until_the_startup_commit() -> None:
    portfolio = _Portfolio()
    engine = _engine(portfolio)
    engine._startup_committed = False

    engine._advance_portfolio_rebalance(Market.EQUITY, cached_basis_only=True)
    assert portfolio.calls[-1]["submit_deferred_buys"] is False

    engine._startup_committed = True
    engine._advance_portfolio_rebalance(Market.EQUITY, cached_basis_only=True)
    assert portfolio.calls[-1]["submit_deferred_buys"] is True


def test_startup_resume_still_submits_before_the_commit() -> None:
    """The restart resume runs before the commit by design: it keeps submitting."""
    portfolio = _Portfolio()
    engine = _engine(portfolio)
    engine._startup_committed = False

    engine._advance_portfolio_rebalance(Market.EQUITY)

    assert portfolio.calls[-1]["submit_deferred_buys"] is True


def test_monitor_resume_of_a_deferred_buy_waits_for_the_startup_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_audit2_fix_H2 import _engine as _h2_engine

    engine = _h2_engine()
    engine._startup_committed = False
    engine._symbols_by_market = {Market.EQUITY: {"QLD"}}
    engine._basis_buy_deferred = True
    resumed: list[Market] = []
    monkeypatch.setattr(engine, "verify_equity_signal_basis", lambda *a, **k: True)
    monkeypatch.setattr(engine, "_check_market_data_feed", lambda: None)
    monkeypatch.setattr(engine, "_advance_portfolio_rebalance", lambda market, **_kw: resumed.append(market))
    worker = SimpleNamespace(last_error=None)

    engine._signal_adjustment_monitor_pass(worker)  # type: ignore[arg-type]
    assert resumed == []
    assert engine._basis_buy_deferred is True

    engine._startup_committed = True
    engine._signal_adjustment_monitor_pass(worker)  # type: ignore[arg-type]
    assert resumed == [Market.EQUITY]
    assert engine._basis_buy_deferred is False


# -- P3-3-2: equity SELLs are held under the cached basis latch on the advance and retry paths --


def test_the_retry_gate_holds_equity_exits_under_the_basis_latch_only() -> None:
    engine = _engine()
    engine._equity_signal_basis_cached_latch = lambda: "signal_adjustment_unverified"  # type: ignore[method-assign]

    assert engine._exit_sell_retry_blocked(Market.EQUITY) is True
    assert engine._exit_sell_retry_blocked(Market.CRYPTO) is False
    assert engine._exit_sell_retry_blocked(None) is False


def test_a_residual_sell_is_held_on_the_advance_path_until_the_basis_verifies(tmp_path) -> None:
    broker = _Broker([_position("AAPL", 400)])
    portfolio, repository = _runtime(tmp_path, broker)
    first = portfolio.process_closed_bars(_bars(_DAY))
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    # The SELL cancels unfilled, so its full quantity is owed as a residual.
    repository.update_order(sell.client_order_id, "canceled")
    sells_before = len([order for order in broker.submitted if order.side == PortfolioOrderSide.SELL])

    engine = _engine(portfolio)
    unverified = "signal_adjustment_unverified"
    engine._equity_signal_basis_latch = lambda: unverified  # type: ignore[method-assign]
    engine._equity_signal_basis_cached_latch = lambda: unverified  # type: ignore[method-assign]
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert len([order for order in broker.submitted if order.side == PortfolioOrderSide.SELL]) == sells_before

    engine._equity_signal_basis_latch = lambda: None  # type: ignore[method-assign]
    engine._equity_signal_basis_cached_latch = lambda: None  # type: ignore[method-assign]
    engine._advance_portfolio_rebalance(Market.EQUITY)
    assert len([order for order in broker.submitted if order.side == PortfolioOrderSide.SELL]) == sells_before + 1


def test_the_decision_path_gate_still_lets_sells_flow_under_the_latch() -> None:
    engine = _engine()
    engine._equity_signal_basis_cached_latch = lambda: "signal_adjustment_unverified"  # type: ignore[method-assign]
    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)

    assert engine._advance_submit_block_reason(Market.EQUITY, sell) is None
    assert engine._advance_submit_block_reason(Market.EQUITY, sell, advance_path=True) == "sell_basis_unverified"


# -- R3-4-4: the retry gate holds during a startup cancel --


def test_the_retry_gate_holds_when_the_startup_was_cancelled() -> None:
    engine = _engine()
    assert engine._exit_sell_retry_blocked(Market.EQUITY) is False

    cancel = threading.Event()
    cancel.set()
    engine._startup_cancel = cancel
    assert engine._exit_sell_retry_blocked(Market.EQUITY) is True


# -- C3-2-2: the retry pass takes the portfolio lock without blocking --


def test_exit_retry_pass_skips_while_the_portfolio_lock_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class _Retry:
        market = Market.EQUITY

        def retry_rejected_exit_sells(self, account: object, **kwargs: object) -> tuple[str, ...]:
            calls.append(account)
            return ()

    engine = _engine()
    engine._portfolio_runtimes = [_Retry()]
    monkeypatch.setattr(engine, "_exit_sell_retry_candidate", lambda runtime: True)
    monkeypatch.setattr(engine, "_invalidate_account_snapshot_after_execution", lambda: calls.append("invalidated"))
    release, thread = _hold_lock_in_another_thread(engine._portfolio_lock)
    try:
        engine._maybe_retry_rejected_exit_sells(object())
        assert calls == []
    finally:
        release.set()
        thread.join(5)

    engine._maybe_retry_rejected_exit_sells(object())
    assert len(calls) == 1


# -- C3-1-1: the decision failure path reconciles under the reconcile lock --


def test_decision_failure_recovery_reconciles_under_the_reconcile_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import parayabuck.runtime as runtime_module
    from tests.test_signal_adjustment_parity import _bar, _process_engine

    # A fixed decision day and a full provider window (no wall clock).
    decision_ts = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    window_dates = [decision_ts - timedelta(days=offset) for offset in range(420)][::-1]
    decision_bar = _bar("AAA", decision_ts, 42.0)
    provider = {"AAA": [_bar("AAA", d, 40.0 + i) for i, d in enumerate(window_dates[:-1])]}
    engine, _captured = _process_engine(provider, window_dates)
    engine._portfolio_runtimes[0].history["AAA"].append(decision_bar)
    seen: dict[str, bool] = {}
    engine._reconcile_lock = threading.Lock()

    class _Broker:
        account_id = "acct"

        def reconcile(self) -> dict[str, list[str]]:
            seen["locked"] = engine._reconcile_lock.locked()
            return {"orders": []}

    engine.broker = _Broker()
    portfolio_runtime = engine._portfolio_runtimes[0]

    def boom(_bars: object, **_kwargs: object) -> None:
        raise RuntimeError("decision exploded")

    portfolio_runtime.process_closed_bars = boom
    recovered: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime_module, "automate_incident_recovery", lambda category, **kwargs: recovered.append(kwargs)
    )
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *a, **k: None)

    engine._process_portfolio_timestamp([decision_bar])

    assert len(recovered) == 1
    reconcile_action = recovered[0]["reconcile_action"]
    assert reconcile_action == engine._reconcile_for_incident_recovery
    assert reconcile_action() == {"orders": []}
    assert seen["locked"] is True
