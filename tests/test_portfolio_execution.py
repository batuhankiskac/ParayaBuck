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
        managed_qty=qty if ownership == "managed" else 0.0,
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


def test_overlay_removes_purely_external_symbol_from_tradable_target() -> None:
    account = _account(
        (
            _position("SPY", 100, 100, ownership="external"),
            _position("MSFT", 50, 100, ownership="managed"),
        )
    )
    decision = PortfolioRiskOverlay().apply(
        _target({"SPY": 0.30, "MSFT": 0.10, "QQQ": 0.10}),
        account,
        market=Market.EQUITY,
        approved_symbols=("SPY", "MSFT", "QQQ"),
        maximum_allocation=0.50,
        mode="paper",
    )
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # A manually opened position is immutable gross, never tradable inventory:
    # neither a BUY that grows it nor a SELL against it may be planned.
    assert "SPY" not in weights
    assert weights["MSFT"] == pytest.approx(0.10)
    assert decision.gross_after >= 0.10  # external SPY still counted


def test_planner_never_orders_a_purely_external_position() -> None:
    account = _account(
        (
            _position("SPY", 100, 100, ownership="external"),
            _position("IWM", 20, 100, ownership="external"),
        )
    )
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.30, "QQQ": 0.05}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100, "QQQ": 100, "IWM": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY", "QQQ", "IWM")},
        approved_symbols=("SPY", "QQQ", "IWM"),
    )
    assert all(order.symbol != "SPY" for order in orders)
    assert all(order.symbol != "IWM" for order in orders)
    assert [order.symbol for order in orders] == ["QQQ"]
    assert orders[0].side == PortfolioOrderSide.BUY


def test_planner_sells_managed_position_when_target_drops_to_zero() -> None:
    account = _account((_position("SPY", 100, 100, ownership="managed"),))
    orders = TargetToOrderPlanner().plan(
        _target({"QQQ": 0.05}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100, "QQQ": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY", "QQQ")},
        approved_symbols=("SPY", "QQQ"),
    )
    assert [order.symbol for order in orders] == ["SPY", "QQQ"]
    sell = next(order for order in orders if order.symbol == "SPY")
    assert sell.side == PortfolioOrderSide.SELL
    assert sell.quantity == pytest.approx(100.0)


def test_planner_client_order_id_is_scoped_to_decision_identity() -> None:
    """Two configurations that share a strategy name, version and timestamp
    must never produce the same client order id: the broker idempotency key
    must be derived from the account-local decision identity."""
    account = _account((_position("SPY", 0, 100, ownership="managed"),))
    orders_a = TargetToOrderPlanner().plan(
        _target({"SPY": 0.10}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY",)},
        approved_symbols=("SPY",),
        decision_id="decision-aaa",
    )
    orders_b = TargetToOrderPlanner().plan(
        _target({"SPY": 0.10}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY",)},
        approved_symbols=("SPY",),
        decision_id="decision-bbb",
    )
    orders_again = TargetToOrderPlanner().plan(
        _target({"SPY": 0.10}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={symbol: _rule(symbol) for symbol in ("SPY",)},
        approved_symbols=("SPY",),
        decision_id="decision-aaa",
    )
    assert orders_a and orders_b
    assert orders_a[0].client_order_id != orders_b[0].client_order_id
    assert orders_again[0].client_order_id == orders_a[0].client_order_id


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
        managed_qty=raw_position["quantity"],
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
