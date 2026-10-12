"""Deterministic PAPER safety contracts for an equity identity switch (V3 -> VT35)."""

from __future__ import annotations

from datetime import UTC, datetime

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderSide,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_price=100.0,
        current_price=100.0,
        market=Market.EQUITY,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )


def _account(*positions: PositionSnapshot) -> AccountSnapshot:
    invested = sum(position.market_value for position in positions)
    return AccountSnapshot(
        cash=100_000.0 - invested,
        equity=100_000.0,
        peak_equity=100_000.0,
        daily_pnl=0.0,
        positions=tuple(positions),
    )


def _target(weights: dict[str, float]) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.0.0",
        decision_timestamp=NOW,
        weights=tuple(TargetWeight(symbol, weight) for symbol, weight in weights.items()),
        cash_weight=1.0 - sum(weights.values()),
    )


def _rules(*symbols: str) -> dict[str, AssetTradingRules]:
    return {symbol: AssetTradingRules(0.01, 0.01, 1.0) for symbol in symbols}


def _plan(
    target: TargetPortfolio,
    account: AccountSnapshot,
    *,
    symbols: tuple[str, ...],
    open_order_symbols: set[str] | None = None,
):
    return TargetToOrderPlanner().plan(
        target,
        account,
        market=Market.EQUITY,
        prices={symbol: 100.0 for symbol in symbols},
        rules=_rules(*symbols),
        approved_symbols=symbols,
        decision_id="identity-decision",
        open_order_symbols=open_order_symbols,
    )


def test_identity_switch_reuses_current_holdings_as_normal_delta_inventory() -> None:
    orders = _plan(
        _target({"AAPL": 0.10}),
        _account(_position("AAPL", 80.0)),
        symbols=("AAPL", "MSFT"),
    )
    assert [(order.symbol, order.side, order.quantity) for order in orders] == [("AAPL", PortfolioOrderSide.BUY, 20.0)]


def test_identity_switch_exits_unselected_holdings_and_enters_new_names() -> None:
    orders = _plan(
        _target({"MSFT": 0.10}),
        _account(_position("AAPL", 100.0)),
        symbols=("AAPL", "MSFT"),
    )
    assert [(order.symbol, order.side) for order in orders] == [
        ("AAPL", PortfolioOrderSide.SELL),
        ("MSFT", PortfolioOrderSide.BUY),
    ]


def test_identity_switch_enters_new_target_and_skips_equal_target() -> None:
    assert _plan(_target({"MSFT": 0.10}), _account(), symbols=("MSFT",))
    assert (
        _plan(
            _target({"AAPL": 0.10}),
            _account(_position("AAPL", 100.0)),
            symbols=("AAPL",),
        )
        == ()
    )


def test_identity_switch_respects_existing_pending_order_ownership() -> None:
    orders = _plan(
        _target({"AAPL": 0.10}),
        _account(),
        symbols=("AAPL",),
        open_order_symbols={"AAPL"},
    )
    assert orders == ()


def test_new_identity_target_keeps_existing_paper_risk_caps() -> None:
    target = _target({f"S{index}": 0.10 for index in range(8)})
    decision = PortfolioRiskOverlay().apply(
        target,
        _account(),
        market=Market.EQUITY,
        approved_symbols=tuple(f"S{index}" for index in range(8)),
        maximum_allocation=0.60,
        mode="paper",
    )
    assert len(decision.target.weights) == 6
    assert all(weight.weight <= 0.10 for weight in decision.target.weights)
    assert decision.gross_after <= 0.60
    assert decision.target.cash_weight >= 0.40
    assert decision.target.strategy_name == "equity_leveraged_trend_v1"
    assert decision.target.strategy_version == "1.0.0"
