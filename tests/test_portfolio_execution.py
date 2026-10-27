from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

NOW = datetime(2026, 8, 8, tzinfo=UTC)

# The pre-VT35 caps these risk tests were written against (0.5 % daily latch,
# 10 % per asset, 6 positions). Passed explicitly, because the default limits
# are the shipped RiskLimits.
PRE_VT35_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.005,
    drawdown_halt=0.05,
    maximum_gross=0.60,
    maximum_equity=0.50,
    maximum_equity_asset=0.10,
    minimum_cash=0.40,
    maximum_positions=6,
)


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
    return AssetTradingRules(0.01, 0.01, 1.0)


def test_risk_overlay_enforces_account_sleeve_asset_and_cash_caps() -> None:
    decision = PortfolioRiskOverlay(PRE_VT35_LIMITS).apply(
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
    # The limit is a fraction of DAY-START equity (equity - daily_pnl), so the
    # pinned loss trips under either denominator convention.
    account = _account((_position("SPY", 100, 100),), daily_pnl=-600.0)
    decision = PortfolioRiskOverlay(PRE_VT35_LIMITS).apply(
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
        decision_id="test-decision",
    )
    assert [order.side for order in orders] == [
        PortfolioOrderSide.SELL,
        PortfolioOrderSide.BUY,
    ]
    assert orders[0].symbol == "SPY"
    assert orders[0].quantity == pytest.approx(50.0)
    assert orders[1].symbol == "QQQ"
    assert all(order.symbol != "IWM" for order in orders)
    assert all(order.client_order_id.startswith("parayabuck-") for order in orders)


def test_planner_skips_symbol_with_existing_open_order() -> None:
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.05}),
        _account(),
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        decision_id="test-decision",
        open_order_symbols={"SPY"},
    )
    assert orders == ()


def test_planner_pending_buy_does_not_block_risk_reducing_sell() -> None:
    account = _account((_position("SPY", 100, 100),))
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.05}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        decision_id="test-decision",
        open_order_sides={"SPY": {"buy"}},
    )

    assert len(orders) == 1
    assert orders[0].side == PortfolioOrderSide.SELL


def test_planner_pending_buy_still_suppresses_risk_increasing_duplicate() -> None:
    account = _account((_position("SPY", 100, 100),))
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.15}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        decision_id="test-decision",
        open_order_sides={"SPY": {"buy"}},
    )

    assert orders == ()


def test_planner_broker_symbol_only_order_still_blocks_risk_reducing_duplicate() -> None:
    account = _account((_position("SPY", 100, 100),))
    orders = TargetToOrderPlanner().plan(
        _target({"SPY": 0.05}),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100},
        rules={"SPY": _rule("SPY")},
        approved_symbols=("SPY",),
        decision_id="test-decision",
        open_order_symbols={"SPY"},
    )

    assert orders == ()


def test_overlay_counts_deduplicated_broker_open_slots_across_markets() -> None:
    account = _account(tuple(_position(f"HELD{index}", 100, 100) for index in range(5)))
    limits = PortfolioRiskLimits(
        maximum_gross=1.0,
        maximum_equity=1.0,
        maximum_equity_asset=1.0,
        minimum_cash=0.0,
        maximum_positions=7,
    )

    decision = PortfolioRiskOverlay(limits).apply(
        _target({"NEW1": 0.10, "NEW2": 0.10}),
        account,
        market=Market.EQUITY,
        approved_symbols=("NEW1", "NEW2"),
        maximum_allocation=1.0,
        mode="paper",
        open_order_symbols=("BTCUSD", "BTC/USD"),
    )

    # Five held equities plus one distinct broker-only BTC/USD order leave
    # exactly one of the two new target slots under the account cap.
    assert len(decision.target.weights) == 1
    assert "position_count_limit" in decision.reasons


def test_overlay_broker_only_order_consumes_last_account_slot() -> None:
    account = _account(tuple(_position(f"HELD{index}", 100, 100, ownership="external") for index in range(5)))
    limits = PortfolioRiskLimits(
        maximum_gross=1.0,
        maximum_equity=1.0,
        maximum_equity_asset=1.0,
        minimum_cash=0.0,
        maximum_positions=6,
    )

    decision = PortfolioRiskOverlay(limits).apply(
        _target({"NEW": 0.10}),
        account,
        market=Market.EQUITY,
        approved_symbols=("NEW",),
        maximum_allocation=1.0,
        mode="paper",
        open_order_symbols=("MANUAL",),
    )

    assert decision.target.weights == ()
    assert "position_count_limit" in decision.reasons


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
        decision_id="test-decision",
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
        decision_id="test-decision",
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
    payload = json.loads((Path(__file__).parent / "fixtures" / "paper_loss_replay.json").read_text())
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
    decision = PortfolioRiskOverlay(PRE_VT35_LIMITS).apply(
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


def _mixed_position(symbol: str, total_qty: float, managed_qty: float, price: float = 100.0) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=total_qty,
        avg_price=price,
        current_price=price,
        market=Market.EQUITY,
        qty_available=total_qty,
        ownership="mixed",
        managed_qty=managed_qty,
    )


def test_risk_overlay_counts_external_exposure_exactly_once() -> None:
    """AAPL external $10k + managed $2k on a $100k account: the external
    exposure is counted exactly once in gross_after."""
    overlay = PortfolioRiskOverlay(PortfolioRiskLimits(maximum_equity_asset=0.25, maximum_equity=0.5))
    account = _account(
        (_mixed_position("AAPL", 120, 20),),
    )
    decision = overlay.apply(
        _target({"AAPL": 0.02, "QQQ": 0.20}),
        account,
        market=Market.EQUITY,
        approved_symbols=("AAPL", "QQQ"),
        maximum_allocation=0.50,
        mode="paper",
    )
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # The external floor keeps the total AAPL target at 0.10.
    assert weights["AAPL"] == pytest.approx(0.10)
    assert weights["QQQ"] == pytest.approx(0.20)
    # gross_after = external 0.10 + managed 0.20; external is not double counted.
    assert decision.gross_after == pytest.approx(0.30)
    assert decision.gross_before == pytest.approx(0.12)
    assert decision.target.cash_weight == pytest.approx(0.70)
    # The managed portion of the target is exactly QQQ's 0.20.
    assert decision.target.metadata["risk_overlay_managed_exposure"] == pytest.approx(0.20)


def test_risk_overlay_caps_managed_exposure_not_external() -> None:
    """maximum_allocation caps the managed sleeve; external stays immutable."""
    overlay = PortfolioRiskOverlay(PortfolioRiskLimits(maximum_equity_asset=0.5, maximum_equity=0.6))
    account = _account(
        (_mixed_position("AAPL", 130, 30),),
    )
    decision = overlay.apply(
        _target({"AAPL": 0.30, "QQQ": 0.30}),
        account,
        market=Market.EQUITY,
        approved_symbols=("AAPL", "QQQ"),
        maximum_allocation=0.40,
        mode="paper",
    )
    assert "exposure_scaled_to_limit" in decision.reasons
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # Managed desired 0.50 is scaled to the managed limit 0.40 (scale 0.8).
    # Only the MANAGED portion scales: AAPL = 0.10 external + 0.20*0.8 = 0.26,
    # QQQ = 0.30*0.8 = 0.24. External inventory is never scaled.
    assert weights["AAPL"] == pytest.approx(0.26)
    assert weights["QQQ"] == pytest.approx(0.24)
    # Actual expected account gross: AAPL total 0.26 + QQQ 0.24.
    assert decision.gross_after == pytest.approx(0.50)
    assert decision.target.metadata["risk_overlay_managed_exposure"] == pytest.approx(0.40)


def test_gross_after_matches_expected_account_gross_for_mixed_inventory() -> None:
    """gross_after equals the true expected account gross after rebalancing."""
    account = _account(
        (_mixed_position("AAPL", 120, 20),),
    )
    decision = PortfolioRiskOverlay().apply(
        _target({"AAPL": 0.02, "QQQ": 0.20, "IWM": 0.20}),
        account,
        market=Market.EQUITY,
        approved_symbols=("AAPL", "QQQ", "IWM"),
        maximum_allocation=0.50,
        mode="paper",
    )
    weights = {weight.symbol: weight.weight for weight in decision.target.weights}
    # AAPL total target is floored at the external 0.10.
    assert weights["AAPL"] == pytest.approx(0.10)
    expected_gross = weights["AAPL"] + weights["QQQ"] + weights["IWM"]
    assert decision.gross_after == pytest.approx(expected_gross)
    assert decision.gross_after <= 0.6


def test_planner_never_sells_external_units_of_a_mixed_position() -> None:
    """The planner may sell only managed units; external inventory is never
    a sell source even when the target is below the external holding."""
    account = _account(
        (_mixed_position("AAPL", 120, 20),),
    )
    planner = TargetToOrderPlanner()
    # Strategy wants AAPL at 0.05 total, below the 0.10 external floor: the
    # overlay floors the target, the planner sells exactly the managed 20.
    floor = PortfolioRiskOverlay().apply(
        _target({"AAPL": 0.05}),
        account,
        market=Market.EQUITY,
        approved_symbols=("AAPL",),
        maximum_allocation=0.50,
        mode="paper",
    )
    orders = planner.plan(
        floor.target,
        account,
        market=Market.EQUITY,
        prices={"AAPL": 100},
        rules={"AAPL": _rule("AAPL")},
        approved_symbols=("AAPL",),
        decision_id="test-decision",
    )
    assert len(orders) == 1
    assert orders[0].side == PortfolioOrderSide.SELL
    assert orders[0].quantity == pytest.approx(20.0)
    # A target at exactly the external floor also sells only the managed lot.
    orders_flat = planner.plan(
        _target({"AAPL": 0.10}),
        account,
        market=Market.EQUITY,
        prices={"AAPL": 100},
        rules={"AAPL": _rule("AAPL")},
        approved_symbols=("AAPL",),
        decision_id="test-decision",
    )
    assert len(orders_flat) == 1
    assert orders_flat[0].side == PortfolioOrderSide.SELL
    assert orders_flat[0].quantity == pytest.approx(20.0)


def test_position_snapshot_rejects_invalid_managed_quantity() -> None:
    with pytest.raises(ValueError, match="managed_qty"):
        PositionSnapshot(
            symbol="AAPL",
            qty=10.0,
            avg_price=100.0,
            current_price=100.0,
            market=Market.EQUITY,
            qty_available=10.0,
            ownership="mixed",
            managed_qty=11.0,
        )


def test_overlay_rejects_duplicate_canonical_account_positions() -> None:
    account = _account(
        (
            _position("AAPL", 1.0, 100.0),
            _position("aapl", 2.0, 101.0),
        )
    )

    with pytest.raises(ValueError, match="Duplicate account position"):
        PortfolioRiskOverlay().apply(
            _target({"AAPL": 0.10}),
            account,
            market=Market.EQUITY,
            approved_symbols=("AAPL",),
            maximum_allocation=0.50,
            mode="paper",
        )
