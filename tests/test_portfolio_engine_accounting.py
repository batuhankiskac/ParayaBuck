from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    equity_regulatory_fee,
    session_date_for,
)
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import (
    RebalancePolicy,
    TargetPortfolio,
    TargetWeight,
)


def test_equity_taf_cat_and_sec_fee_model() -> None:
    buy = equity_regulatory_fee(
        OrderSide.BUY,
        100,
        50,
        taf_per_share=0.000195,
        taf_cap=9.79,
        cat_per_share=0.000003,
        sec_fee_rate=0.00002060,
    )
    sell = equity_regulatory_fee(
        OrderSide.SELL,
        100,
        50,
        taf_per_share=0.000195,
        taf_cap=9.79,
        cat_per_share=0.000003,
        sec_fee_rate=0.00002060,
    )
    assert buy == pytest.approx(0.0003)
    assert sell == pytest.approx(0.1228)


def _bar(timestamp: datetime, symbol: str, close: float, market: Market, volume: float) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d" if market == Market.EQUITY else "1h",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        market,
    )


class _ScheduleStrategy(PortfolioStrategy):
    """Returns a fixed target per (year, month) offset from ``epoch``."""

    name = "schedule"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(
        self,
        targets_by_month: dict[int, dict[str, float]],
        epoch: datetime,
        default_targets: dict[str, float] | None = None,
    ) -> None:
        self._targets_by_month = targets_by_month
        self._default_targets = default_targets
        self._epoch = epoch
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        offset = (context.decision_timestamp.year - self._epoch.year) * 12 + (
            context.decision_timestamp.month - self._epoch.month
        )
        if offset in self._targets_by_month:
            targets = self._targets_by_month[offset]
        elif self._default_targets is not None:
            targets = self._default_targets
        else:
            targets = {}
        weights = tuple(TargetWeight(symbol, w) for symbol, w in targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(targets.values()),
            universe_version="test",
        )


class _CryptoScheduleStrategy(_ScheduleStrategy):
    name = "crypto_schedule"
    market = Market.CRYPTO
    timeframe = "1h"


def _engine(
    strategy: PortfolioStrategy,
    *,
    commission: float = 0.0,
    crypto_taker_fee_bps: float = 0.0,
    state_path: str | None = None,
    state=None,
    risk_limits=None,
) -> PortfolioBacktestEngine:
    frequency = "hours:24" if strategy.market == Market.CRYPTO else "monthly"
    return PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(strategy.market, frequency),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        commission=commission,
        crypto_taker_fee_bps=crypto_taker_fee_bps,
        benchmark_symbol="SPY" if strategy.market == Market.EQUITY else "BTC/USD",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        state_path=state_path,
        state=state,
        risk_limits=risk_limits,
    )


class _HoldStrategy(PortfolioStrategy):
    """Buys once and then holds: the target mirrors the current weight."""

    name = "hold"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, symbol: str, initial_weight: float) -> None:
        self._symbol = symbol
        self._initial_weight = initial_weight
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        qty = context.holdings.get(self._symbol, 0.0)
        price = context.prices.get(self._symbol, 0.0)
        weight = qty * price / context.equity if price > 0 and context.equity > 0 else 0.0
        if weight <= 0.0:
            weight = self._initial_weight
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight(self._symbol, weight),),
            cash_weight=1.0 - weight,
            universe_version="test",
        )


def _equity_days(days: int, epoch: datetime) -> list[Bar]:
    return [
        _bar(epoch + timedelta(days=index), "SPY", 100.0 + index * 0.1, Market.EQUITY, 5_000_000)
        for index in range(days)
    ]


def test_entry_fee_reduces_realized_pnl_on_full_and_partial_exits() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    # Month 0: 60% SPY; month 1: 40%; month 2: 0% (partial then full exit).
    strategy = _ScheduleStrategy({0: {"SPY": 0.6}, 1: {"SPY": 0.4}, 2: {}}, epoch)
    engine = _engine(strategy, commission=2.0)
    result = engine.run(_equity_days(130, epoch), liquidate_at_end=True)
    assert result.closed_positions == 1
    # Without entry-fee accounting the realized PnL would be too high by the
    # entry fee (paid when buying 600 shares). The commission on the partial
    # exit also reduces the accumulator.
    # Reconciled invariant: with everything liquidated, realized PnL equals
    # the equity change and absorbs the entry fee plus every fill fee.
    equity_change = result.ending_equity - result.starting_equity
    assert sum(result.realized_pnls) == pytest.approx(equity_change)
    assert len(result.realized_pnls) == 2
    assert len(result.closed_position_events) == 1
    assert result.closed_position_events[0][2] == pytest.approx(equity_change)
    # The persisted state must agree with the economic result: realized PnL
    # is credited at every fill (partial AND full), exactly once.
    assert result.state.realized_pnl == pytest.approx(equity_change)
    assert result.state.realized_pnl == pytest.approx(sum(result.realized_pnls))
    # One buy fill plus one partial-sell and one final-sell fill: 3 fills at
    # 2.0 commission each; the entry fee is a real cost inside the PnL.
    assert result.fee_total == pytest.approx(3 * 2.0)
    sell_proceeds = sum(fill.qty * fill.fill_price for fill in result.fills if fill.side == OrderSide.SELL)
    buy_cost = sum(fill.qty * fill.fill_price for fill in result.fills if fill.side == OrderSide.BUY)
    manual = sell_proceeds - buy_cost - result.fee_total
    assert sum(result.realized_pnls) == pytest.approx(manual)


def test_state_realized_pnl_matches_economic_realized_delta_at_every_fill() -> None:
    """Persisted state realized PnL must reconcile with cash + unrealized.

    Regression for the double-count: the entry-fee share was deducted from
    state.realized_pnl once inside the per-fill accumulator and once again
    separately, so the persisted state disagreed with the equity change by
    exactly one entry commission.
    """
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy({0: {"SPY": 0.6}, 2: {}}, epoch)
    engine = _engine(strategy, commission=1.0)
    result = engine.run(_equity_days(200, epoch), liquidate_at_end=False)
    state = result.state
    # Mid-run (position still open): realized PnL equals the partial-exit
    # deltas; cash + holdings value + realized must reconstruct equity.
    unrealized = state.holdings.get("SPY", 0.0) * (state.last_prices.get("SPY", 0.0) - state.avg_prices.get("SPY", 0.0))
    assert state.realized_pnl == pytest.approx(result.ending_equity - result.starting_equity - unrealized)
    # The full-exit run ends flat: realized PnL equals the total equity change.
    closed = _engine(strategy, commission=1.0)
    full = closed.run(_equity_days(200, epoch), liquidate_at_end=True)
    assert full.state.realized_pnl == pytest.approx(full.ending_equity - full.starting_equity)
    assert full.state.realized_pnl == pytest.approx(sum(full.realized_pnls))


def test_continuous_run_differs_from_month_end_liquidation_run() -> None:
    """The continuous OOS methodology must not silently behave like monthly
    buy/sell churn: liquidating at the end of every month changes turnover,
    closed positions and return vs the continuous book."""
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _equity_days(200, epoch)
    strategy = _ScheduleStrategy({}, epoch, default_targets={"SPY": 0.6})
    continuous = _engine(strategy)
    continuous_result = continuous.run(bars, liquidate_at_end=False)
    liquidated = _engine(strategy)
    liquidated_result = liquidated.run(bars, liquidate_at_end=True)
    # The liquidation run sells the whole book at the end: it records one
    # closed position and its end equity differs from the continuous book
    # (forced exit prices/fees at the last bar).
    assert liquidated_result.closed_positions == 1
    assert continuous_result.closed_positions == 0
    assert liquidated_result.ending_equity != pytest.approx(continuous_result.ending_equity)
    # Monthly fold diagnostics must be derived from the CONTINUOUS book: the
    # strategy holds through month ends, which a fold-liquidation design
    # would turn into forced monthly buy/sell churn.
    assert continuous_result.rebalance_count == liquidated_result.rebalance_count


def test_multiple_entries_use_average_price_and_full_exit_realizes_all() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    # 0.3 then 0.6 (adds to position), then 0 -> average price accounting.
    strategy = _ScheduleStrategy({0: {"SPY": 0.3}, 1: {"SPY": 0.6}, 2: {}}, epoch)
    engine = _engine(strategy, commission=1.0)
    result = engine.run(_equity_days(130, epoch), liquidate_at_end=True)
    assert result.closed_positions == 1
    buy_fills = [fill for fill in result.fills if fill.side == OrderSide.BUY]
    assert len(buy_fills) == 2
    average_price = result.state.avg_prices.get("SPY")
    assert average_price is None  # position fully closed
    equity_change = result.ending_equity - result.starting_equity
    assert sum(result.realized_pnls) == pytest.approx(equity_change)


def test_crypto_taker_fee_applies_on_both_sides() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _CryptoScheduleStrategy({0: {"BTC/USD": 0.5}, 1: {}}, epoch)
    engine = _engine(strategy, crypto_taker_fee_bps=5.0)
    bars = [
        _bar(
            epoch + timedelta(hours=index),
            "BTC/USD",
            50_000.0 + index * 10,
            Market.CRYPTO,
            100_000,
        )
        for index in range(24 * 65)
    ]
    result = engine.run(bars, liquidate_at_end=True)
    assert result.closed_positions == 1
    # Both the buy and the sell pay the taker fee.
    buy_fee = sum(fill.fee for fill in result.fills if fill.side == OrderSide.BUY)
    sell_fee = sum(fill.fee for fill in result.fills if fill.side == OrderSide.SELL)
    assert buy_fee > 0 and sell_fee > 0
    equity_change = result.ending_equity - result.starting_equity
    assert sum(result.realized_pnls) == pytest.approx(equity_change)


def test_partial_sell_is_realized_without_counting_a_full_close() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy(
        {0: {"SPY": 0.6}, 1: {"SPY": 0.4}},
        epoch,
        default_targets={"SPY": 0.4},
    )
    result = _engine(strategy).run(_equity_days(130, epoch), liquidate_at_end=False)
    assert result.realized_pnl_events
    assert result.closed_positions == 0
    assert result.closed_position_events == ()
    assert result.state.realized_pnl == pytest.approx(sum(result.realized_pnls))
    assert "SPY" in result.state.holdings


def test_terminal_close_reports_lifetime_pnl_after_partial_sell() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy(
        {0: {"SPY": 0.6}, 1: {"SPY": 0.4}},
        epoch,
        default_targets={"SPY": 0.4},
    )
    result = _engine(strategy, commission=1.0).run(_equity_days(130, epoch), liquidate_at_end=True)
    lifetime = result.ending_equity - result.starting_equity
    assert result.closed_positions == 1
    assert len(result.realized_pnl_events) >= 2
    assert result.closed_position_events[0][2] == pytest.approx(lifetime)
    assert sum(result.realized_pnls) == pytest.approx(lifetime)


def test_realized_plus_unrealized_reconciles_without_liquidation() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    # Buy 60% once and hold: no partial exits, so the reconciliation is
    # sum(realized) + unrealized == equity change, with unrealized net of the
    # remaining entry fee.
    strategy = _HoldStrategy("SPY", 0.6)
    engine = _engine(strategy, commission=2.0)
    result = engine.run(_equity_days(130, epoch), liquidate_at_end=False)
    assert result.realized_pnls == ()
    state = result.state
    last_price = state.last_prices["SPY"]
    qty = state.holdings["SPY"]
    avg = state.avg_prices["SPY"]
    unrealized = qty * (last_price - avg) - state.entry_fees.get("SPY", 0.0)
    equity_change = result.ending_equity - result.starting_equity
    assert unrealized == pytest.approx(equity_change)


def test_client_order_ids_are_deterministic_and_structured() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy({}, epoch, default_targets={"SPY": 0.6})
    first = _engine(strategy)
    second = _engine(strategy)
    result_a = first.run(_equity_days(60, epoch), liquidate_at_end=False)
    result_b = second.run(_equity_days(60, epoch), liquidate_at_end=False)
    order_ids_a = [fill.client_order_id for fill in result_a.fills]
    order_ids_b = [fill.client_order_id for fill in result_b.fills]
    assert order_ids_a == order_ids_b
    first_id = order_ids_a[0]
    assert first_id.startswith("schedule|1.0.0|")
    parts = first_id.split("|")
    assert parts[3] == "SPY"
    assert parts[4] == "buy"
    assert parts[5] == "1"
    assert result_a.rebalances[0].rebalance_number == 1


def test_rebalance_record_reports_signed_and_actual_weights() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy({0: {"SPY": 0.6}, 1: {"SPY": 0.3}}, epoch)
    engine = _engine(strategy)
    result = engine.run(_equity_days(100, epoch), liquidate_at_end=False)
    assert len(result.rebalances) >= 2
    first = result.rebalances[0]
    assert dict(first.pre_trade_weights)["SPY"] == 0.0
    assert dict(first.target_weights)["SPY"] == 0.6
    # The first decision buys SPY: the signed trade weight is positive.
    signed = dict(first.signed_trade_weights)
    assert signed["SPY"] > 0
    assert first.expected_turnover > 0
    second = result.rebalances[1]
    assert dict(second.pre_trade_weights)["SPY"] > 0
    assert dict(second.signed_trade_weights)["SPY"] < 0  # reduces the position
    assert second.realized_turnover > 0
    assert second.cost > 0


def test_restart_keeps_cash_and_never_reissues_filled_orders(tmp_path) -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _equity_days(120, epoch)
    state_path = str(tmp_path / "state.json")

    strategy = _ScheduleStrategy({0: {"SPY": 0.6}}, epoch)
    first_engine = _engine(strategy, state_path=state_path)
    first_engine.run(bars[:60], liquidate_at_end=False)

    state = PortfolioBacktestEngine.load_state(state_path)
    assert state is not None and state.cash is not None
    persisted_cash = state.cash

    reference_engine = _engine(strategy)
    reference = reference_engine.run(bars, liquidate_at_end=False)

    restarted_engine = _engine(strategy, state=state)
    restarted = restarted_engine.run(bars[60:], warmup=bars[:60], liquidate_at_end=False)

    # Cash at restart equals the interrupted run's persisted cash.
    assert restarted.state.cash is not None
    assert restarted.state.cash == persisted_cash
    # Position quantities, cash and fills after the restart match the
    # uninterrupted run exactly.
    assert restarted.state.holdings == reference.state.holdings
    assert restarted.state.cash == pytest.approx(reference.state.cash)
    assert [fill.timestamp for fill in restarted.fills] == [
        fill.timestamp for fill in reference.fills if fill.timestamp >= bars[60].timestamp
    ]
    assert [fill.qty for fill in restarted.fills] == [
        fill.qty for fill in reference.fills if fill.timestamp >= bars[60].timestamp
    ]
    # Idempotent client order ids: identical decisions produce identical ids.
    restart_ids = [fill.client_order_id for fill in restarted.fills]
    ref_ids = [fill.client_order_id for fill in reference.fills if fill.timestamp >= bars[60].timestamp]
    assert restart_ids == ref_ids


def test_state_round_trip_preserves_economic_fields(tmp_path) -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    strategy = _ScheduleStrategy({}, epoch, default_targets={"SPY": 0.6})
    state_path = str(tmp_path / "state.json")
    engine = _engine(strategy, commission=1.0, state_path=state_path)
    engine.run(_equity_days(60, epoch), liquidate_at_end=False)
    state = PortfolioBacktestEngine.load_state(state_path)
    assert state is not None
    assert state.schema_version == 3
    assert state.last_processed_bar is not None
    assert state.last_prices.get("SPY") is not None
    assert state.entry_fees.get("SPY") is not None
    assert state.cash is not None
    assert state.last_target.get("SPY") == 0.6
    assert state.peak_equity is not None
    assert state.day_start_equity is not None
    assert state.last_observed_equity is not None
    assert state.risk_session_date is not None


def test_v1_state_without_cash_migrates_by_deriving_cash(tmp_path) -> None:
    from parayabuck.portfolio.models import PortfolioState

    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _equity_days(60, epoch)
    strategy = _HoldStrategy("SPY", 0.6)
    first_engine = _engine(strategy)
    result = first_engine.run(bars, liquidate_at_end=False)
    v1 = PortfolioState.from_dict({**result.state.to_dict(), "schema_version": 1, "cash": None})
    migrated = PortfolioState.from_dict(v1.to_dict())
    # The v1 state keeps cash=None; the engine derives cash once on resume.
    assert migrated.cash is None
    engine = _engine(strategy, state=migrated)
    resumed = engine.run(bars[60:], warmup=bars[:60], liquidate_at_end=False)
    assert resumed.state.holdings == result.state.holdings
    assert resumed.state.cash is not None
    assert resumed.state.cash == pytest.approx(
        100_000.0 - result.state.holdings["SPY"] * result.state.avg_prices["SPY"]
    )


def test_restart_risk_state_equivalence_with_drawdown_after_restart(tmp_path) -> None:
    """A -> B -> C -> D uninterrupted must equal A -> B, save, restart, C -> D:
    ending equity, holdings, cash, fills, realized PnL, daily-loss and
    drawdown-halt behavior, pending orders and the persisted risk lifecycle.

    The scenario forces a high-water before the restart (the ramp peak is
    captured in the saved state) and the drawdown limit is crossed only AFTER
    the restart, so an unpersisted peak would silently disable the halt.
    """
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    ramp = [
        _bar(epoch + timedelta(days=index), "SPY", 100 * 1.005**index, Market.EQUITY, 5_000_000) for index in range(40)
    ]
    crash = [
        _bar(
            epoch + timedelta(days=40 + index),
            "SPY",
            100 * 1.005**39 * 0.988**index,
            Market.EQUITY,
            5_000_000,
        )
        for index in range(30)
    ]
    bars = ramp + crash
    strategy = _ScheduleStrategy({}, epoch, default_targets={"SPY": 0.6})
    limits = PortfolioRiskLimits(
        daily_loss_limit=0.005,
        drawdown_halt=0.05,
        maximum_equity=0.60,
        maximum_equity_asset=0.60,
    )
    state_path = str(tmp_path / "state.json")

    reference_engine = _engine(strategy, risk_limits=limits)
    reference = reference_engine.run(bars, liquidate_at_end=False)

    first_engine = _engine(strategy, state_path=state_path, risk_limits=limits)
    first_engine.run(bars[:40], liquidate_at_end=False)
    saved = PortfolioBacktestEngine.load_state(state_path)
    assert saved is not None
    # The high-water of the ramp was persisted before the restart.
    assert saved.peak_equity == pytest.approx(reference.state.peak_equity)

    restarted_engine = _engine(strategy, state=saved, risk_limits=limits)
    restarted = restarted_engine.run(bars[40:], warmup=bars[:40], liquidate_at_end=False)

    assert restarted.state.cash == pytest.approx(reference.state.cash)
    assert restarted.state.holdings == reference.state.holdings
    assert restarted.state.avg_prices == pytest.approx(reference.state.avg_prices)
    assert restarted.state.realized_pnl == pytest.approx(reference.state.realized_pnl)
    assert restarted.state.pending_orders == reference.state.pending_orders
    assert restarted.state.pending_target == reference.state.pending_target
    assert restarted.state.last_processed_bar == reference.state.last_processed_bar
    cutoff = bars[39].timestamp
    assert [fill.timestamp for fill in restarted.fills] == [
        fill.timestamp for fill in reference.fills if fill.timestamp > cutoff
    ]
    assert [fill.qty for fill in restarted.fills] == [fill.qty for fill in reference.fills if fill.timestamp > cutoff]
    # Persisted risk lifecycle identical: peak, day-start, last observed and
    # the active session date all survived the restart.
    assert restarted.state.peak_equity == pytest.approx(reference.state.peak_equity)
    assert restarted.state.day_start_equity == pytest.approx(reference.state.day_start_equity)
    assert restarted.state.last_observed_equity == pytest.approx(reference.state.last_observed_equity)
    assert restarted.state.risk_session_date == reference.state.risk_session_date
    # Ending equity matches to the cent.
    assert restarted.ending_equity == pytest.approx(reference.ending_equity, abs=1e-6)
    # Daily-loss and drawdown halt behavior after the restart matches the
    # uninterrupted run exactly (the drawdown limit is crossed only after the
    # restart, so this proves the persisted peak feeds the halt).
    reference_post = [record for record in reference.rebalances if record.decision_timestamp > cutoff]
    assert len(restarted.rebalances) == len(reference_post)
    assert [record.risk_overlay_halted for record in restarted.rebalances] == [
        record.risk_overlay_halted for record in reference_post
    ]
    assert [record.risk_overlay_reasons for record in restarted.rebalances] == [
        record.risk_overlay_reasons for record in reference_post
    ]
    # The drawdown halt genuinely fired after the restart in both runs.
    post_reasons = [reason for record in reference_post for reason in record.risk_overlay_reasons]
    assert "portfolio_drawdown_halt" in post_reasons


def test_v2_state_without_risk_lifecycle_still_loads(tmp_path) -> None:
    """A pre-v3 state (no risk lifecycle fields) loads safely and the engine
    seeds the lifecycle from the starting equity once."""
    from parayabuck.portfolio.models import PortfolioState

    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _equity_days(120, epoch)
    strategy = _HoldStrategy("SPY", 0.6)
    engine = _engine(strategy)
    result = engine.run(bars[:60], liquidate_at_end=False)
    v2_raw = result.state.to_dict()
    v2_raw.pop("peak_equity")
    v2_raw.pop("day_start_equity")
    v2_raw.pop("last_observed_equity")
    v2_raw.pop("risk_session_date")
    v2_raw["schema_version"] = 2
    v2 = PortfolioState.from_dict(v2_raw)
    assert v2.peak_equity is None
    resumed = _engine(strategy, state=v2).run(bars[60:], warmup=bars[:60], liquidate_at_end=False)
    # Seeded from the starting equity, so the resumed run is self-consistent
    # and re-derives the lifecycle from its own observations.
    assert resumed.state.peak_equity is not None
    assert resumed.state.peak_equity >= resumed.starting_equity
    assert resumed.state.risk_session_date is not None


def test_restart_after_liquidated_run_does_not_resurrect_positions(tmp_path) -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _equity_days(60, epoch)
    strategy = _HoldStrategy("SPY", 0.6)
    state_path = str(tmp_path / "state.json")
    first_engine = _engine(strategy, commission=1.0, state_path=state_path)
    first = first_engine.run(bars, liquidate_at_end=True)
    state = PortfolioBacktestEngine.load_state(state_path)
    assert state is not None
    # Liquidation cleared the position bookkeeping while keeping cash.
    assert state.holdings == {}
    assert state.pending_orders == ()
    assert state.cash is not None
    # A restart from this state must not re-buy the liquidated position.
    restarted_engine = _engine(strategy, state=state)
    restarted = restarted_engine.run([], liquidate_at_end=False)
    assert restarted.state.holdings == {}
    assert restarted.state.cash == pytest.approx(first.state.cash)


class _DailyRiskStrategy(PortfolioStrategy):
    """Fixed target that decides on every bar (intermediate risk allowed)."""

    name = "daily_risk"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, symbol: str, weight: float, market: Market = Market.EQUITY) -> None:
        self._symbol = symbol
        self._weight = weight
        self.market = market
        self.timeframe = "1h" if market == Market.CRYPTO else "1d"
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight(self._symbol, self._weight),),
            cash_weight=1.0 - self._weight,
            universe_version="test",
        )

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True


def _risk_limits() -> PortfolioRiskLimits:
    return PortfolioRiskLimits(
        daily_loss_limit=0.005,
        drawdown_halt=0.05,
        maximum_gross=0.60,
        maximum_equity=0.50,
        maximum_equity_asset=0.60,
        minimum_cash=0.0,
        maximum_positions=6,
    )


def test_daily_loss_limit_triggers_on_single_day_drop() -> None:
    """A one-day equity loss beyond the configured daily limit halts entries."""
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        _bar(epoch + timedelta(days=0), "SPY", 100.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=1), "SPY", 100.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=2), "SPY", 97.0, Market.EQUITY, 5_000_000),
    ]
    strategy = _DailyRiskStrategy("SPY", 0.6)
    engine = _engine(strategy, risk_limits=_risk_limits())
    result = engine.run(bars, liquidate_at_end=False)
    # The day-2 decision bought 500 shares at the day-1 open; day-3 closes 3%
    # lower on that position, a ~1.5% equity loss that is past the 0.5%
    # daily-loss limit. The day-start equity must be the previous session's
    # closing equity (100 030), not the post-close equity of the loss day.
    snapshot = engine._account_snapshot()
    assert snapshot.daily_pnl == pytest.approx(500.0 * (97.0 - 100.0), abs=10.0)
    assert snapshot.daily_pnl / snapshot.equity <= -0.01
    # The decision on the loss day is halted by the overlay: the target is
    # capped at the current weight instead of re-buying the full 60%.
    last = result.rebalances[-1]
    assert last.risk_overlay_halted
    assert "daily_loss_limit" in last.risk_overlay_reasons
    current_weight = 500.0 * 97.0 / snapshot.equity
    assert dict(last.target_weights)["SPY"] == pytest.approx(current_weight, abs=1e-9)
    assert last.signed_trade_weights == ()


def test_high_water_between_rebalances_feeds_later_drawdown() -> None:
    """Equity that peaks between scheduled rebalances feeds the drawdown
    measurement; in a research backtest the breach is a SHADOW event (the
    reason is recorded where production would have halted) and the curve
    keeps trading instead of freezing on the monotonic peak."""
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    # Month 0: target 60% SPY (capped at the 50% sleeve). Month 1: target 90%.
    # Prices rise to 115 mid-month then fall back to 100 before the next
    # decision: the peak is 107 530, so the later drawdown is ~7% and the
    # decision records a portfolio_drawdown_halt shadow event.
    bars = [
        _bar(epoch + timedelta(days=0), "SPY", 100.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=1), "SPY", 115.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=2), "SPY", 112.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=3), "SPY", 100.0, Market.EQUITY, 5_000_000),
        _bar(epoch + timedelta(days=31), "SPY", 100.0, Market.EQUITY, 5_000_000),
    ]
    strategy = _ScheduleStrategy({0: {"SPY": 0.6}, 1: {"SPY": 0.9}}, epoch)
    engine = _engine(strategy, risk_limits=_risk_limits())
    result = engine.run(bars, liquidate_at_end=False)
    # The mid-month peak must have been recorded even though no decision
    # happened between week 1 and the next month: equity peaked right after
    # the entry filled (at the next bar's open) and the close rose.
    first_fill = result.fills[0]
    expected_peak = 100_000.0 + first_fill.qty * (bars[1].close - first_fill.fill_price)
    assert engine._peak_equity == pytest.approx(expected_peak)
    assert engine._peak_equity > 100_000.0
    # The next-month decision wanted 90%; the ~7% drawdown records the
    # shadow event but does NOT cap the target below the strategy's desired
    # exposure (the sleeve/maximum-allocation caps still apply) — the curve
    # keeps trading instead of freezing on the monotonic peak.
    assert len(result.rebalances) == 2
    month_one = result.rebalances[1]
    assert not month_one.risk_overlay_halted
    assert "portfolio_drawdown_halt" in month_one.risk_overlay_reasons
    # The shadow events are aggregated in the result (production would have
    # halted at this decision).
    assert [(ts, reason) for ts, reason in result.drawdown_halt_shadow_events] == [
        (month_one.decision_timestamp, "portfolio_drawdown_halt")
    ]


def test_weekly_strategy_risk_state_updates_on_non_rebalance_days() -> None:
    """A weekly strategy still tracks daily PnL and high water between weeks."""
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    # Week 1: Mon 100, Tue 110, Wed 108, Thu 120, Fri 90. Week 2: Mon 88.
    session_days = [
        datetime(2024, 1, 1),
        datetime(2024, 1, 2),
        datetime(2024, 1, 3),
        datetime(2024, 1, 4),
        datetime(2024, 1, 5),
        datetime(2024, 1, 8),
    ]
    closes = [100.0, 110.0, 108.0, 120.0, 90.0, 88.0]
    bars = [
        _bar(day.replace(tzinfo=UTC), "SPY", close, Market.EQUITY, 5_000_000)
        for day, close in zip(session_days, closes, strict=True)
    ]
    strategy = _ScheduleStrategy({0: {"SPY": 0.6}, 1: {"SPY": 0.9}}, epoch)
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(strategy.market, "weekly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        risk_limits=_risk_limits(),
    )
    result = engine.run(bars, liquidate_at_end=False)
    # Thursday's close (120) set the high water; Friday's drop to 90 must be
    # measured from Thursday's equity, not from a stale rebalance-day value.
    first_fill = result.fills[0]
    cash_after_entry = 100_000.0 - first_fill.qty * first_fill.fill_price
    assert engine._peak_equity == pytest.approx(cash_after_entry + first_fill.qty * 120.0)
    # Only week-start decisions are scheduled for this strategy, so the week-1
    # rise and Friday crash were tracked purely by the per-bar risk lifecycle.
    assert len(result.rebalances) == 2
    # The week-2 decision is capped: the ~14% daily loss and ~14% drawdown
    # from Thursday's peak both halt entries, so the 90% target cannot buy.
    week_two = result.rebalances[1]
    assert week_two.risk_overlay_halted
    assert "daily_loss_limit" in week_two.risk_overlay_reasons
    assert "portfolio_drawdown_halt" in week_two.risk_overlay_reasons
    assert dict(week_two.target_weights)["SPY"] == pytest.approx(
        first_fill.qty * 88.0 / (cash_after_entry + first_fill.qty * 88.0), abs=1e-9
    )
    assert week_two.signed_trade_weights == ()
    # The day-start equity after the run is Friday's closing equity: the new
    # session (week 2 Monday) started from the previous session's close.
    assert engine._day_start_equity == pytest.approx(cash_after_entry + first_fill.qty * 90.0)
    snapshot = engine._account_snapshot()
    assert snapshot.daily_pnl == pytest.approx(first_fill.qty * (88.0 - 90.0), abs=10.0)


def test_session_date_for_equity_uses_new_york_trading_date() -> None:
    """Equity session dates follow New York, not UTC."""
    # 2024-07-04 01:00 UTC is 2024-07-03 21:00 ET: the UTC day is the 4th but
    # the trading session belongs to the 3rd.
    late_utc = datetime(2024, 7, 4, 1, 0, tzinfo=UTC)
    assert session_date_for(late_utc, Market.EQUITY) == datetime(2024, 7, 3).date()
    assert session_date_for(late_utc, Market.CRYPTO) == datetime(2024, 7, 4).date()


def test_crypto_daily_reset_uses_utc_boundary() -> None:
    """Crypto daily-loss resets at the UTC calendar boundary (documented)."""
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [_bar(epoch + timedelta(hours=index), "BTC/USD", 100.0, Market.CRYPTO, 100_000) for index in range(48)]
    bars[25] = _bar(epoch + timedelta(hours=25), "BTC/USD", 89.0, Market.CRYPTO, 100_000)
    strategy = _DailyRiskStrategy("BTC/USD", 0.6, market=Market.CRYPTO)
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(strategy.market, "hours:24"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="BTC/USD",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        risk_limits=_risk_limits(),
    )
    result = engine.run(bars, liquidate_at_end=False)
    # The crypto sleeve caps the position at 5% (50 units at $100). Hour 25
    # falls on the second UTC day; its day-start is hour 24's equity, so the
    # >11% drop at hour 25 registers as a daily loss past the 0.5% limit and
    # halts the hour-25 decision. If the day-start had been reset to the
    # post-close equity of hour 25 itself, no daily loss would register.
    decision_at_25 = next(record for record in result.rebalances if record.decision_timestamp == bars[25].timestamp)
    assert decision_at_25.risk_overlay_halted
    assert "daily_loss_limit" in decision_at_25.risk_overlay_reasons
