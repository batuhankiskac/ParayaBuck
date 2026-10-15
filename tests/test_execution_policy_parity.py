"""Execution-policy parity fixtures (Phase 2).

The exact same (account, target, prices, rules) fed through production
planning (TargetToOrderPlanner) and backtest target application
(PortfolioBacktestEngine) must produce the same ORDER-EXISTENCE decision.
Fill mechanics may differ; order existence must not.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine, _Position
from parayabuck.config import DownsizeGainBand
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    GainBandHold,
    PortfolioExecutionPolicy,
    PortfolioOrderSide,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import configuration_digest

# A1u (docs/research/gain_band_v3.md): the shipped downsize gain band. The lots
# are the 100 managed shares of the parity fixtures' account, oldest first, with
# the older lot deep in profit (60 % gain fraction on a 20-share sell).
GAIN_BAND = DownsizeGainBand()
GAIN_BAND_LOTS = ((60.0, 40.0), (40.0, 99.5))


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        super().__init__({"targets": dict(targets)})

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, w) for symbol, w in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


def _target(targets: dict[str, float], timestamp: datetime | None = None) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="fixed_target",
        strategy_version="1.0.0",
        decision_timestamp=timestamp or datetime(2025, 1, 1, tzinfo=UTC),
        weights=tuple(TargetWeight(symbol, w) for symbol, w in targets.items()),
        cash_weight=1.0 - sum(targets.values()),
        universe_version="test",
    )


def _rule(symbol: str, *, notional_min: float = 1.0) -> AssetTradingRules:
    return AssetTradingRules(0.01, 0.01, notional_min)


def _account(*, qty: float, price: float = 100.0, equity: float = 100_000.0) -> AccountSnapshot:
    position = PositionSnapshot(
        "SPY",
        qty,
        price,
        price,
        Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )
    return AccountSnapshot(
        cash=equity - qty * price,
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=(position,),
    )


def _engine(
    policy: PortfolioExecutionPolicy,
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
    lots: tuple[tuple[float, float], ...] = (),
) -> PortfolioBacktestEngine:
    strategy = _FixedTargetStrategy({})
    engine = PortfolioBacktestEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=equity,
        slippage_bps=0.0,
        spread_bps=0.0,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        execution_policy=policy,
        maximum_allocation=0.50,
    )
    engine._cash = equity - qty * price
    engine._last_price["SPY"] = price
    engine._positions["SPY"] = _Position(
        "SPY",
        Market.EQUITY,
        "1d",
        qty,
        price,
        datetime(2025, 1, 1, tzinfo=UTC),
        lots=[[lot_qty, lot_cost] for lot_qty, lot_cost in lots],
    )
    return engine


def _planned_verdict(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
    lots: tuple[tuple[float, float], ...] = (),
    holds: list[GainBandHold] | None = None,
) -> dict[str, PortfolioOrderSide]:
    """Planner order-existence verdict: symbol -> side for produced orders."""
    account = _account(qty=qty, price=price, equity=equity)
    orders = TargetToOrderPlanner(policy).plan(
        _target(targets),
        account,
        market=Market.EQUITY,
        prices={"SPY": price, "QQQ": price},
        rules={symbol: _rule(symbol) for symbol in (*targets, "SPY")},
        approved_symbols=tuple(targets),
        decision_id="parity-decision",
        open_lots={"SPY": lots} if lots else {},
        gain_band_holds=holds,
    )
    return {order.symbol: order.side for order in orders}


def _backtest_verdict(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
    lots: tuple[tuple[float, float], ...] = (),
) -> dict[str, OrderSide]:
    """Backtest target-application verdict: symbol -> side for produced orders."""
    engine = _engine(policy, qty=qty, price=price, equity=equity, lots=lots)
    engine._apply_target(_target(targets))
    return {order.symbol: order.side for order in engine._pending}


def _assert_parity(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
    lots: tuple[tuple[float, float], ...] = (),
) -> None:
    planned = _planned_verdict(policy, targets, qty=qty, price=price, equity=equity, lots=lots)
    backtest = _backtest_verdict(policy, targets, qty=qty, price=price, equity=equity, lots=lots)
    assert set(planned) == set(backtest), (planned, backtest)
    for symbol in planned:
        assert planned[symbol].value == backtest[symbol].value, (symbol, planned, backtest)


def test_49bps_drift_both_skip() -> None:
    """100 shares @ $100 on $100k = 0.10 weight; target 0.1049 = 49 bps."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    _assert_parity(policy, {"SPY": 0.1049}, qty=100)
    # Neither side produced any order at all.
    assert _planned_verdict(policy, {"SPY": 0.1049}, qty=100) == {}
    assert _backtest_verdict(policy, {"SPY": 0.1049}, qty=100) == {}


def test_51bps_drift_both_trade() -> None:
    """Target 0.1051 = 51 bps drift: both produce a BUY."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    _assert_parity(policy, {"SPY": 0.1051}, qty=100)
    planned = _planned_verdict(policy, {"SPY": 0.1051}, qty=100)
    backtest = _backtest_verdict(policy, {"SPY": 0.1051}, qty=100)
    assert planned == {"SPY": PortfolioOrderSide.BUY}
    assert backtest == {"SPY": OrderSide.BUY}


def test_exact_boundary_verdicts_agree() -> None:
    """At exactly the band edge float arithmetic decides consistently: the two
    paths must never disagree, whatever the exact float resolution is."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    for target in (0.1049, 0.105, 0.1051):
        _assert_parity(policy, {"SPY": target}, qty=100)


def test_small_notional_both_skip() -> None:
    """With a zero band, a $9 order is below the shared $10 minimum: both skip."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.0, minimum_order_notional=10.0)
    # 0.00009 weight on $100k at $100 = 0.09 shares = $9 notional.
    _assert_parity(policy, {"SPY": 0.00009}, qty=0)
    assert _planned_verdict(policy, {"SPY": 0.00009}, qty=0) == {}
    assert _backtest_verdict(policy, {"SPY": 0.00009}, qty=0) == {}


def test_normal_rebalance_produces_equivalent_economic_deltas() -> None:
    """A normal rebalance (0.30 target from 0.10 current) produces the same
    symbol/side/quantity in both paths."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.005, minimum_order_notional=10.0)
    account = _account(qty=100)
    planned = TargetToOrderPlanner(policy).plan(
        _target({"SPY": 0.30}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        decision_id="test-decision",
    )
    engine = _engine(policy, qty=100)
    engine._apply_target(_target({"SPY": 0.30}))
    assert len(planned) == 1
    assert len(engine._pending) == 1
    assert planned[0].symbol == engine._pending[0].symbol == "SPY"
    assert planned[0].side.value == engine._pending[0].side.value == "buy"
    assert planned[0].quantity == pytest.approx(engine._pending[0].qty) == pytest.approx(200.0)


def test_broker_minimum_wins_when_larger() -> None:
    """Production enforces max(configured minimum, broker minimum); a broker
    minimum above the policy floor must still gate order existence."""
    policy = PortfolioExecutionPolicy(rebalance_band_pct=0.0, minimum_order_notional=10.0)
    account = _account(qty=0)
    orders = TargetToOrderPlanner(policy).plan(
        _target({"SPY": 0.00011}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": _rule("SPY", notional_min=50.0)},
        approved_symbols=("SPY",),
        decision_id="test-decision",
    )
    # $11 notional: above the $10 policy floor but below the $50 broker min.
    assert orders == ()


def test_configuration_digest_includes_execution_policy() -> None:
    """Changing the rebalance band changes the configuration digest, so a
    live authorization generated for another execution policy cannot be
    reused silently."""
    strategy = _FixedTargetStrategy({"SPY": 0.10})
    baseline = configuration_digest(strategy, "paper", 0.50, "universe-v1", execution_policy=PortfolioExecutionPolicy())
    other_band = configuration_digest(
        strategy,
        "paper",
        0.50,
        "universe-v1",
        execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=0.01),
    )
    other_notional = configuration_digest(
        strategy,
        "paper",
        0.50,
        "universe-v1",
        execution_policy=PortfolioExecutionPolicy(minimum_order_notional=25.0),
    )
    assert baseline != other_band
    assert baseline != other_notional


def test_configuration_digest_includes_the_downsize_gain_band() -> None:
    """A1u changes order existence, so the schedule belongs to the digest an
    operator authorizes; dropping it must produce a different digest."""
    strategy = _FixedTargetStrategy({"SPY": 0.10})
    arguments = (strategy, "paper", 0.50, "universe-v1")
    without = configuration_digest(*arguments, execution_policy=PortfolioExecutionPolicy())
    with_band = configuration_digest(
        *arguments,
        execution_policy=PortfolioExecutionPolicy(downsize_gain_band=GAIN_BAND),
    )
    other_tiers = configuration_digest(
        *arguments,
        execution_policy=PortfolioExecutionPolicy(
            downsize_gain_band=DownsizeGainBand(thresholds=(0.02,), bands=(0.005, 0.05))
        ),
    )
    assert len({without, with_band, other_tiers}) == 3


def test_gain_band_parity_planner_and_backtest_agree() -> None:
    """The same (target, current, lots) triple produces the same order-existence
    verdict through production planning and through backtest target application."""
    policy = PortfolioExecutionPolicy(downsize_gain_band=GAIN_BAND)
    # 100 shares @ $100 on $100k = 0.10 weight; the lots carry a 60 % gain.
    for target in (0.1049, 0.105, 0.0801, 0.088, 0.0901, 0.0, 0.20):
        _assert_parity(policy, {"SPY": target}, qty=100, lots=GAIN_BAND_LOTS)


def test_gain_band_parity_without_a_lot_book_falls_back_in_both() -> None:
    """No lots anywhere: both paths keep the constant band and trade."""
    policy = PortfolioExecutionPolicy(downsize_gain_band=GAIN_BAND)
    _assert_parity(policy, {"SPY": 0.08}, qty=100)
    assert _planned_verdict(policy, {"SPY": 0.08}, qty=100) == {"SPY": PortfolioOrderSide.SELL}
    assert _backtest_verdict(policy, {"SPY": 0.08}, qty=100) == {"SPY": OrderSide.SELL}


def test_gain_band_hold_is_reported_by_the_planner_only_when_it_binds() -> None:
    """The diagnostics sink names the suppressed downsize that the backtest
    would also have executed."""
    policy = PortfolioExecutionPolicy(downsize_gain_band=GAIN_BAND)
    holds: list[GainBandHold] = []
    assert _planned_verdict(policy, {"SPY": 0.08}, qty=100, lots=GAIN_BAND_LOTS, holds=holds) == {}
    assert [(hold.symbol, hold.band) for hold in holds] == [("SPY", 0.08)]
    engine = _engine(policy, qty=100, lots=GAIN_BAND_LOTS)
    engine._apply_target(_target({"SPY": 0.08}))
    assert engine._pending == []
    assert list(engine._gain_band_holds) == holds
    holds = []
    _planned_verdict(policy, {"SPY": 0.1049}, qty=100, lots=GAIN_BAND_LOTS, holds=holds)
    assert holds == []  # the constant band already skips this drift


def test_live_authorization_digest_includes_execution_policy() -> None:
    """The Web UI live-arm authorization digest is computed over the raw
    configuration; adding/changing the execution section must invalidate an
    existing live approval."""
    from parayabuck.web.managers.engine import EngineManager

    base = {"runtime": {"mode": "live"}}
    with_band = {"runtime": {"mode": "live"}, "execution": {"rebalance_band_pct": 0.01}}
    with_notional = {"runtime": {"mode": "live"}, "execution": {"minimum_order_notional": 25}}
    digests = {EngineManager._digest_config(raw, paper=False) for raw in (base, with_band, with_notional)}
    assert len(digests) == 3
