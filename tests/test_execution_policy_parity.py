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
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderSide,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import configuration_digest


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
    return AssetTradingRules(symbol, Market.EQUITY, 0.01, 0.01, 0.01, notional_min)


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
    engine._positions["SPY"] = _Position("SPY", Market.EQUITY, "1d", qty, price, datetime(2025, 1, 1, tzinfo=UTC))
    return engine


def _planned_verdict(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
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
    )
    return {order.symbol: order.side for order in orders}


def _backtest_verdict(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
) -> dict[str, OrderSide]:
    """Backtest target-application verdict: symbol -> side for produced orders."""
    engine = _engine(policy, qty=qty, price=price, equity=equity)
    engine._apply_target(_target(targets))
    return {order.symbol: order.side for order in engine._pending}


def _assert_parity(
    policy: PortfolioExecutionPolicy,
    targets: dict[str, float],
    *,
    qty: float,
    price: float = 100.0,
    equity: float = 100_000.0,
) -> None:
    planned = _planned_verdict(policy, targets, qty=qty, price=price, equity=equity)
    backtest = _backtest_verdict(policy, targets, qty=qty, price=price, equity=equity)
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


def test_live_authorization_digest_includes_execution_policy() -> None:
    """The Web UI live-arm authorization digest is computed over the raw
    configuration; adding/changing the execution section must invalidate an
    existing live approval."""
    from parayabuck.web import server as web_server

    base = {"runtime": {"mode": "live"}}
    with_band = {"runtime": {"mode": "live"}, "execution": {"rebalance_band_pct": 0.01}}
    with_notional = {"runtime": {"mode": "live"}, "execution": {"minimum_order_notional": 25}}
    digests = {web_server.EngineManager._digest_config(raw, paper=False) for raw in (base, with_band, with_notional)}
    assert len(digests) == 3
