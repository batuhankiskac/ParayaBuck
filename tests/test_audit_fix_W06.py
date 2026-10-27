"""Decision-boundary regressions from the 2026-10-27 audit (worker W06).

Covers P9-1, P-2, C-3, D7-1 and R-5 in ``portfolio/runtime_service.py``. The
P9-2 close of an unrecoverable BLOCKED rebalance is pinned in
``tests/test_rebalance_block_recovery.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
)
from parayabuck.portfolio.runtime_service import (
    REBALANCE_STATUS_CLOSED_UNRESOLVED,
    PortfolioRuntime,
    PortfolioStateRepository,
    _no_order_reason,
)

_DAY = datetime(2025, 1, 1, tzinfo=UTC)
# A month later: crosses the monthly cadence boundary, so a forced or recovered
# decision there is a real scheduled session.
_NEXT_MONTH = _DAY + timedelta(days=40)
_TARGETS = {"AAPL": 0.15, "MSFT": 0.20}


class _Strategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = dict(targets)
        self.parameters = {"targets": dict(targets)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items()),
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


class _Broker:
    """Paper broker double. ``positions=None`` starts with AAPL 400 (the usual SELL setup)."""

    def __init__(self, positions: tuple[PositionSnapshot, ...] | None = None) -> None:
        self.positions = [_position("AAPL", 400)] if positions is None else list(positions)
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []
        # When set, the next account() call returns this snapshot (a stale read).
        self.stale_account: AccountSnapshot | None = None

    def account(self) -> AccountSnapshot:
        if self.stale_account is not None:
            snapshot, self.stale_account = self.stale_account, None
            return snapshot
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(100_000.0 - value, 100_000.0, 100_000.0, 0.0, tuple(self.positions))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol, qty, 100.0, 100.0, Market.EQUITY, qty_available=qty, ownership="managed", managed_qty=qty
    )


def _bars(day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in _TARGETS]


def _runtime(repository: PortfolioStateRepository, broker: _Broker, **options: object) -> PortfolioRuntime:
    settings: dict[str, object] = {"mode": "paper", "maximum_allocation": 0.50, "account_id": "acct"}
    settings.update(options)
    return PortfolioRuntime(_Strategy(_TARGETS), tuple(sorted(_TARGETS)), broker, repository, **settings)


def _settle(repository: PortfolioStateRepository, order: PortfolioOrderRequest, activity_id: str) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=order.reference_price,
        occurred_at=order.decision_timestamp,
        activity_id=activity_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _phase(repository: PortfolioStateRepository, decision_id: str) -> tuple[str, str]:
    row = repository.store.connection.execute(
        "SELECT phase, status FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0]), str(row[1])


def _sell_submitted(tmp_path):
    """A decision whose SELL (AAPL 400 -> 150) is working; its MSFT BUY is deferred."""
    broker = _Broker()
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(repository, broker)
    first = runtime.process_closed_bars(_bars(_DAY), force_scheduled=True)
    assert first.status == "sell_phase_submitted"
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    return runtime, repository, broker, first, sell


def _buy_ready(tmp_path):
    """The SELL has filled and the deferred BUY side is withheld (buy_ready)."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    assert runtime.advance_rebalance(submit_deferred_buys=False) is None
    state = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert state is not None and state.phase == REBALANCE_PHASE_BUY_READY
    return runtime, repository, broker, first


def _buy_pending(tmp_path):
    """The deferred MSFT BUY has been submitted and is still working."""
    runtime, repository, broker, first = _buy_ready(tmp_path)
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    return runtime, repository, broker, first


def _buy_orders(orders, decision_id: str) -> list[PortfolioOrderRequest]:
    return [order for order in orders if order.decision_id == decision_id and order.side == PortfolioOrderSide.BUY]


def test_p9_1_superseded_buy_ready_discards_its_buy_side(tmp_path) -> None:
    """A new session closes the withheld BUY side; it is never submitted with the old target."""
    runtime, repository, broker, first = _buy_ready(tmp_path)

    result = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)
    assert _buy_orders(broker.submitted, first.decision_id) == []
    # The new session plans its own target instead of resuming the old decision.
    assert result.decision_id not in {None, first.decision_id}


def test_p9_1_rebalance_in_progress_restores_the_cadence(tmp_path) -> None:
    """A session that finds an open rebalance records no decision, so it stays due."""
    runtime, repository, broker, first = _buy_pending(tmp_path)
    before = runtime.previous_timestamp

    result = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert result.status == "rebalance_in_progress"
    assert runtime.previous_timestamp == before
    assert _phase(repository, first.decision_id)[0] == REBALANCE_PHASE_BUY_PENDING
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL, PortfolioOrderSide.BUY]


def test_p9_1_superseded_sell_in_flight_holds_the_new_decision(tmp_path) -> None:
    """Sells before buys: the new session waits for the superseded SELL and never sends its stale BUY."""
    runtime, repository, broker, first, sell = _sell_submitted(tmp_path)
    before = runtime.previous_timestamp

    held = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert held.status == "rebalance_in_progress"
    assert runtime.previous_timestamp == before
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]
    assert _phase(repository, first.decision_id) == (REBALANCE_PHASE_COMPLETE, REBALANCE_STATUS_CLOSED_UNRESOLVED)

    # The SELL settles; the advance path does not resume the stale BUY side.
    _settle(repository, sell, "sell-fill")
    broker.positions = [_position("AAPL", 150)]
    broker.open_symbols.discard("AAPL")
    assert runtime.advance_rebalance() is None
    assert [order.side for order in broker.submitted] == [PortfolioOrderSide.SELL]

    # The next cycle plans the fresh target now that nothing is working.
    planned = runtime.process_closed_bars(_bars(_NEXT_MONTH + timedelta(days=1)), force_scheduled=True)
    assert planned.status != "rebalance_in_progress"
    assert planned.decision_id not in {None, first.decision_id}
    assert _buy_orders(broker.submitted, first.decision_id) == []


def test_p_2_rebalance_completion_restores_the_cadence(tmp_path) -> None:
    """The early return on a completing BUY must not consume the session."""
    runtime, repository, broker, first = _buy_ready(tmp_path)
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buy = next(order for order in resumed.orders if order.side == PortfolioOrderSide.BUY)
    _settle(repository, buy, "buy-fill")
    broker.positions.append(_position("MSFT", 200))
    broker.open_symbols.discard("MSFT")
    before = runtime.previous_timestamp

    completed = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert completed.status == "rebalance_complete"
    assert runtime.previous_timestamp == before
    # The session is still due: the next cycle plans it rather than reporting a duplicate.
    decided = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)
    assert decided.status != "duplicate_decision"


def test_c_3_startup_catch_up_plans_from_a_fresh_account_after_completion(tmp_path) -> None:
    """A catch-up whose advance completes the rebalance must not plan from the pre-fill snapshot."""
    runtime, repository, broker, first = _buy_ready(tmp_path)
    resumed = runtime.advance_rebalance()
    assert resumed is not None and resumed.status == "buy_phase_submitted"
    buy = next(order for order in resumed.orders if order.side == PortfolioOrderSide.BUY)
    _settle(repository, buy, "buy-fill")
    # The restart's first broker snapshot predates the BUY fill (AAPL only).
    broker.stale_account = broker.account()
    broker.positions.append(_position("MSFT", 200))
    broker.open_symbols.discard("MSFT")
    restarted = _runtime(repository, broker)
    restarted.seed(_bars(_NEXT_MONTH))

    result = restarted.startup_catch_up()

    assert result.status != "rebalance_in_progress"
    assert [order.symbol for order in result.orders if order.side == PortfolioOrderSide.BUY] == []
    # Only the first decision's MSFT BUY ever reached the broker.
    assert [order.symbol for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == ["MSFT"]


def test_d7_1_entry_latch_no_trade_reports_the_latch(tmp_path, monkeypatch) -> None:
    """A latched decision that wanted an entry and placed none reports ENTRY_LATCH, not NO_TARGET_CHANGE."""
    summaries: list[dict[str, object]] = []
    monkeypatch.setattr(runtime_service, "_emit_decision_summary_safe", lambda **kwargs: summaries.append(kwargs))
    broker = _Broker(positions=())
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime = _runtime(repository, broker)

    result = runtime.process_closed_bars(_bars(_DAY), entry_latch="daily_loss_limit", force_scheduled=True)

    assert result.orders == ()
    assert summaries[-1]["no_trade_reason"] == "ENTRY_LATCH"


def test_d7_1_no_order_reason_names_band_and_minimum_notional() -> None:
    """Without a latch, a drift inside the band is REBALANCE_BAND and a larger unplaced change MIN_NOTIONAL."""
    account = AccountSnapshot(95_000.0, 100_000.0, 100_000.0, 0.0, (_position("AAPL", 50),))

    def reason(weights: dict[str, float], orders: tuple[object, ...] = (), *, latched: bool = False) -> str | None:
        target = TargetPortfolio(
            strategy_name="fixed_target",
            strategy_version="1.0.0",
            decision_timestamp=_DAY,
            weights=tuple(TargetWeight(symbol, weight) for symbol, weight in weights.items()),
            cash_weight=0.0,
            universe_version="test",
        )
        return _no_order_reason(
            target,
            orders,
            entry_capped=latched,
            account=account,
            market=Market.EQUITY,
            symbols=("AAPL", "MSFT"),
            rebalance_band=0.005,
        )

    assert reason({"AAPL": 0.05}) is None  # matched holdings: NO_TARGET_CHANGE
    assert reason({"AAPL": 0.052}) == "REBALANCE_BAND"  # drift inside the band
    assert reason({"AAPL": 0.05, "MSFT": 0.20}) == "MIN_NOTIONAL"  # a change beyond the band produced no order
    assert reason({"AAPL": 0.052}, latched=True) == "ENTRY_LATCH"
    assert reason({"AAPL": 0.052}, ("placed order",)) is None  # a placed order is not a no-trade


def test_r_5_configuration_change_does_not_redecide_a_decided_session(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    original = _runtime(repository, _Broker(positions=()))
    decided = original.process_closed_bars(_bars(_DAY), force_scheduled=True)
    assert decided.decision_id is not None

    changed = _runtime(repository, _Broker(positions=()), maximum_allocation=0.40)
    assert changed.configuration_digest != original.configuration_digest

    result = changed.process_closed_bars(_bars(_DAY), force_scheduled=True)

    assert result.status == "duplicate_decision"
    count = repository.store.connection.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0]
    assert count == 1
