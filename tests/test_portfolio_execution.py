from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderSide,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

NOW = datetime(2026, 8, 8, tzinfo=UTC)


def _target(weights: dict[str, float]) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="approved",
        strategy_version="1.0.0",
        decision_timestamp=NOW,
        weights=tuple(TargetWeight(symbol, weight) for symbol, weight in weights.items()),
        cash_weight=1.0 - sum(weights.values()),
    )


def _account(
    positions: tuple[PositionSnapshot, ...] = (),
    *,
    daily_pnl: float = 0.0,
    peak: float = 100_000.0,
    equity: float = 100_000.0,
) -> AccountSnapshot:
    return AccountSnapshot(
        cash=equity - sum(position.market_value for position in positions),
        equity=equity,
        peak_equity=peak,
        daily_pnl=daily_pnl,
        positions=positions,
    )


def _position(
    symbol: str,
    qty: float,
    price: float,
    *,
    ownership: str = "managed",
    market: Market = Market.EQUITY,
) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_price=price,
        current_price=price,
        market=market,
        qty_available=qty,
        ownership=ownership,
    )


def _rule(symbol: str) -> AssetTradingRules:
    return AssetTradingRules(symbol, Market.EQUITY, 0.01, 0.01, 0.01, 1.0)


def test_risk_overlay_enforces_account_sleeve_asset_and_cash_caps() -> None:
    decision = PortfolioRiskOverlay().apply(
        _target({"SPY": 0.30, "QQQ": 0.30, "IWM": 0.30}),
        _account(),
        market=Market.EQUITY,
        approved_symbols=("SPY", "QQQ", "IWM"),
        maximum_allocation=0.50,
        mode="paper",
    )
    assert all(weight.weight <= 0.10 for weight in decision.target.weights)
    assert sum(weight.weight for weight in decision.target.weights) <= 0.50
    assert decision.target.cash_weight >= 0.50


def test_daily_loss_halt_allows_reductions_but_forbids_increases() -> None:
    account = _account((_position("SPY", 100, 100),), daily_pnl=-500.0)
    decision = PortfolioRiskOverlay().apply(
        _target({"SPY": 0.05, "QQQ": 0.10}),
        account,
        market=Market.EQUITY,
        approved_symbols=("SPY", "QQQ"),
        maximum_allocation=0.50,
        mode="paper",
    )
    assert decision.halted
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert weights["SPY"] == pytest.approx(0.05)
    assert "QQQ" not in weights


def test_planner_sells_before_buys_quantizes_and_never_sells_external() -> None:
    account = _account(
        (
            _position("SPY", 100.007, 100),
            _position("IWM", 20, 100, ownership="external"),
        )
    )
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.05, "QQQ": 0.05}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100, "QQQ": 100, "IWM": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY", "QQQ", "IWM")},
        approved_symbols=("SPY", "QQQ", "IWM"),
    )
    assert [order.side for order in orders] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    assert orders[0].symbol == "SPY"
    assert orders[0].quantity == pytest.approx(50.0)
    assert orders[1].symbol == "QQQ"
    assert all(order.symbol != "IWM" for order in orders)
    assert all(order.client_order_id.startswith("pbp-") for order in orders)


def test_planner_skips_symbol_with_existing_open_order() -> None:
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.05}),
        _account(),
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        open_order_symbols={"SPY"},
    )
    assert orders == ()


def test_anonymized_paper_loss_replay_triggers_risk_halt() -> None:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "paper_loss_replay.json").read_text()
    )
    raw_account = payload["account"]
    raw_position = payload["managed_position"]
    position = PositionSnapshot(
        symbol=raw_position["symbol"],
        qty=raw_position["quantity"],
        avg_price=raw_position["average_price"],
        current_price=raw_position["current_price"],
        market=Market.EQUITY,
        qty_available=raw_position["quantity"],
        ownership="managed",
    )
    account = AccountSnapshot(
        cash=raw_account["cash"],
        equity=raw_account["equity"],
        peak_equity=raw_account["peak_equity"],
        daily_pnl=raw_account["daily_pnl"],
        positions=(position,),
    )
    decision = PortfolioRiskOverlay().apply(
        _target({raw_position["symbol"]: payload["proposed_increase"], "NEW": 0.10}),
        account,
        market=Market.EQUITY,
        approved_symbols=(raw_position["symbol"], "NEW"),
        maximum_allocation=0.50,
        mode="paper",
    )
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    assert decision.halted
    assert "daily_loss_limit" in decision.reasons
    assert "NEW" not in weights
    assert weights[raw_position["symbol"]] <= position.market_value / account.equity
