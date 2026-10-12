"""Account-wide occupied/reserved slot proof with synthetic broker state."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product
from pathlib import Path

import pytest

from parayabuck import load_config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    TargetToOrderPlanner,
    portfolio_risk_limits_from_config,
    reserve_opening_order_slots,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

NOW = datetime(2024, 6, 14, tzinfo=UTC)
EQUITIES = ("ALL", "CMG", "EME", "ETN", "GDDY", "TDG")
# Foreign crypto held in the shared brokerage account: never traded by the
# equity-only bot, but each non-flat position still occupies an account slot.
FOREIGN_CRYPTOS = ("BCH/USD", "BTC/USD", "LTC/USD")


def _position(symbol: str, market: Market, qty: float = 10.0) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_price=100.0,
        current_price=100.0,
        market=market,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty,
    )


def _account(positions: tuple[PositionSnapshot, ...]) -> AccountSnapshot:
    return AccountSnapshot(
        cash=100_000.0 - sum(position.market_value for position in positions),
        equity=100_000.0,
        peak_equity=100_000.0,
        daily_pnl=0.0,
        positions=positions,
    )


def _order(
    symbol: str, market: Market = Market.EQUITY, side: PortfolioOrderSide = PortfolioOrderSide.BUY
) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=f"synthetic-{market.value}-{symbol}-{side.value}",
        decision_id="synthetic",
        symbol=symbol,
        market=market,
        side=side,
        quantity=1.0,
        reference_price=100.0,
        decision_timestamp=NOW,
        strategy_name="synthetic",
        strategy_version="1",
    )


@pytest.mark.parametrize(
    ("held", "reserved", "orders", "expected"),
    [
        (6, (), ("NEW1",), ()),
        (5, (), ("NEW1", "NEW2"), ("NEW1",)),
        (6, (), ("ALL",), ("ALL",)),
        (5, ((Market.EQUITY, "PENDING"),), ("NEW1",), ()),
        (6, (), ("NEW1",), ()),
        (5, (), ("NEW1", "NEW2", "NEW3"), ("NEW1",)),
    ],
)
def test_opening_slots_are_reserved_in_planner_order(
    held: int,
    reserved: tuple[tuple[Market, str], ...],
    orders: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    account = _account(tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:held]))
    planned = tuple(_order(symbol) for symbol in orders)
    admitted = reserve_opening_order_slots(planned, account, maximum_positions=6, reserved_position_keys=reserved)
    assert tuple(order.symbol for order in admitted) == expected
    assert admitted == reserve_opening_order_slots(
        planned, account, maximum_positions=6, reserved_position_keys=reserved
    )


def test_sell_and_partial_exit_do_not_release_slot_until_broker_is_flat() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    sell = _order("ALL", side=PortfolioOrderSide.SELL)
    buy = _order("NEW1")
    for qty in (10.0, 0.1):
        account = _account((_position("ALL", Market.EQUITY, qty), *held[1:]))
        assert reserve_opening_order_slots((sell, buy), account, maximum_positions=6) == (sell,)
    flat = _account(held[1:])
    assert reserve_opening_order_slots((buy,), flat, maximum_positions=6) == (buy,)


def test_foreign_crypto_position_and_order_alias_occupy_one_global_slot() -> None:
    """A foreign crypto position in the shared account and its working broker
    order (alias spelling) occupy one account slot; an equity opening that
    would exceed the global cap is denied."""
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4])
    account = _account((*held, _position("BTC/USD", Market.CRYPTO)))
    new1 = _order("NEW1")
    new2 = _order("NEW2")
    assert reserve_opening_order_slots(
        (new1, new2),
        account,
        maximum_positions=6,
        open_order_symbols=("BTCUSD",),
    ) == (new1,)
    assert (
        reserve_opening_order_slots(
            (new1,),
            account,
            maximum_positions=6,
            reserved_position_keys=((Market.EQUITY, "PENDING"),),
            open_order_symbols=("BTCUSD",),
        )
        == ()
    )


def test_partial_opening_and_cancel_in_flight_keep_one_slot_claim() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4])
    exit_order = _order("ALL", side=PortfolioOrderSide.SELL)
    new1 = _order("NEW1")
    new2 = _order("NEW2")
    # PART is partially filled and still working. Its broker position,
    # durable reservation and open broker order are one canonical slot.
    account = _account((*held, _position("PART", Market.EQUITY, 0.1)))
    assert reserve_opening_order_slots(
        (exit_order, new1, new2),
        account,
        maximum_positions=6,
        reserved_position_keys=((Market.EQUITY, "PART"),),
        open_order_symbols=("PART",),
    ) == (exit_order, new1)
    # Cancel requested but not yet terminal: the working opening still claims
    # its slot even if the account snapshot has not shown its first fill.
    unfilled = _account(held)
    assert reserve_opening_order_slots(
        (new1, new2),
        unfilled,
        maximum_positions=6,
        open_order_symbols=("PART",),
    ) == (new1,)
    # After terminal cancellation and a fresh account without PART, exactly
    # its one slot is available again.
    assert reserve_opening_order_slots((new1, new2, _order("NEW3")), unfilled, maximum_positions=6) == (new1, new2)


def test_synthetic_eight_slots_and_six_slot_rollback_preserve_exits() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    seventh = _order("NEW1")
    eighth = _order("NEW2")
    ninth = _order("NEW3")
    assert reserve_opening_order_slots((seventh, eighth, ninth), _account(held), maximum_positions=8) == (
        seventh,
        eighth,
    )
    eight_held = (*held, _position("NEW1", Market.EQUITY), _position("NEW2", Market.EQUITY))
    exit_order = _order("ALL", side=PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((exit_order, ninth), _account(eight_held), maximum_positions=6) == (exit_order,)
    assert reserve_opening_order_slots((ninth,), _account(eight_held[3:]), maximum_positions=6) == (ninth,)


def test_shipped_cap8_counts_restored_working_slot() -> None:
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    limits = portfolio_risk_limits_from_config(config.risk)
    assert limits.maximum_positions == 8
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    account = _account(held)
    restored_working = ((Market.EQUITY, "PENDING"),)
    eighth = _order("NEW1")
    ninth = _order("NEW2")
    assert reserve_opening_order_slots(
        (eighth, ninth),
        account,
        maximum_positions=limits.maximum_positions,
        reserved_position_keys=restored_working,
    ) == (eighth,)
    assert (
        reserve_opening_order_slots(
            (ninth,),
            account,
            maximum_positions=limits.maximum_positions,
            reserved_position_keys=(*restored_working, (Market.EQUITY, "NEW1")),
        )
        == ()
    )


def test_opening_reservation_transitions_to_position_or_releases_once() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:5])
    pending = ((Market.EQUITY, "NEW1"),)
    second = _order("NEW2")
    assert (
        reserve_opening_order_slots((second,), _account(held), maximum_positions=6, reserved_position_keys=pending)
        == ()
    )
    # A canceled/rejected order disappears from both live order sources. A
    # repeated terminal update cannot release any second slot.
    assert reserve_opening_order_slots((second,), _account(held), maximum_positions=6) == (second,)
    assert reserve_opening_order_slots((second,), _account(held), maximum_positions=6) == (second,)
    filled = _account((*held, _position("NEW1", Market.EQUITY)))
    assert reserve_opening_order_slots((second,), filled, maximum_positions=6) == ()


def test_foreign_crypto_open_order_symbol_shares_account_cap() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:5])
    equity = _order("NEW1")
    assert reserve_opening_order_slots((equity,), _account(held), maximum_positions=6) == (equity,)
    assert (
        reserve_opening_order_slots((equity,), _account(held), maximum_positions=6, open_order_symbols=("BTCUSD",))
        == ()
    )


def test_production_overlay_and_band_suppressed_exit_cannot_open_seventh_slot() -> None:
    held = (
        *(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4]),
        _position("GDDY", Market.EQUITY, 20),
        _position("TDG", Market.EQUITY, 1),
    )
    account = _account(held)
    tradable = ("GDDY", "NEW1", "TDG")
    target = TargetPortfolio(
        strategy_name="synthetic_equity",
        strategy_version="1",
        decision_timestamp=NOW,
        weights=(TargetWeight("GDDY", 0.025), TargetWeight("NEW1", 0.025)),
        cash_weight=0.95,
    )
    # Sleeve room covers the four fixed (unapproved) holdings plus the target.
    limits = PortfolioRiskLimits(maximum_positions=6, maximum_equity=0.10)
    risk = PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.EQUITY,
        approved_symbols=tradable,
        maximum_allocation=0.10,
        mode="paper",
    )
    rules = {symbol: AssetTradingRules(0.01, 0.01, 1.0) for symbol in tradable}
    planned = TargetToOrderPlanner().plan(
        risk.target,
        account,
        market=Market.EQUITY,
        prices={symbol: 100.0 for symbol in tradable},
        rules=rules,
        approved_symbols=tradable,
        decision_id="slot-decision",
    )
    assert [(order.symbol, order.side.value) for order in planned] == [("GDDY", "buy"), ("NEW1", "buy")]
    assert TargetToOrderPlanner().policy.rebalance_band_pct == 0.005
    admitted = reserve_opening_order_slots(planned, account, maximum_positions=6)
    assert [(order.symbol, order.side.value) for order in admitted] == [("GDDY", "buy")]


def test_executed_sell_still_needs_fresh_zero_position_before_replacement() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    buy = _order("NEW1")
    sell = _order("ALL", side=PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((sell, buy), _account(held), maximum_positions=6) == (sell,)
    assert reserve_opening_order_slots((buy,), _account(held[1:]), maximum_positions=6) == (buy,)


def test_band_eligible_sell_does_not_release_slot_at_order_planning() -> None:
    held = (
        *(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4]),
        _position("GDDY", Market.EQUITY, 20),
        _position("TDG", Market.EQUITY, 10),
    )
    account = _account(held)
    tradable = ("GDDY", "NEW1", "TDG")
    target = TargetPortfolio(
        strategy_name="synthetic_equity",
        strategy_version="1",
        decision_timestamp=NOW,
        weights=(TargetWeight("GDDY", 0.025), TargetWeight("NEW1", 0.025)),
        cash_weight=0.95,
    )
    rules = {symbol: AssetTradingRules(0.01, 0.01, 1.0) for symbol in tradable}
    planned = TargetToOrderPlanner().plan(
        target,
        account,
        market=Market.EQUITY,
        prices={symbol: 100.0 for symbol in tradable},
        rules=rules,
        approved_symbols=tradable,
        decision_id="slot-decision",
    )
    assert any(order.symbol == "TDG" and order.side == PortfolioOrderSide.SELL for order in planned)
    assert any(order.symbol == "NEW1" and order.side == PortfolioOrderSide.BUY for order in planned)
    admitted = reserve_opening_order_slots(planned, account, maximum_positions=6)
    assert all(order.symbol != "NEW1" for order in admitted)
    after_confirmed_zero = _account(held[:-1])
    new1 = next(order for order in planned if order.symbol == "NEW1")
    assert reserve_opening_order_slots((new1,), after_confirmed_zero, maximum_positions=6) == (new1,)


def test_table_of_positions_foreign_crypto_and_partial_exits_never_spends_seventh_slot() -> None:
    for equities, cryptos, pending_buys, pending_sells, partial in product(
        range(7), range(3), range(3), range(2), (False, True)
    ):
        if equities + cryptos > 6:
            continue
        held = tuple(
            _position(symbol, Market.EQUITY, 0.1 if partial and index == 0 else 10)
            for index, symbol in enumerate(EQUITIES[:equities])
        )
        held += tuple(_position(symbol, Market.CRYPTO) for symbol in FOREIGN_CRYPTOS[:cryptos])
        reserved = tuple((Market.EQUITY, f"PENDING{index}") for index in range(pending_buys))
        if equities + cryptos + pending_buys > 6:
            continue
        if pending_sells and held:
            reserved += ((held[0].market, held[0].symbol),)
        candidates = (_order("NEW1"), _order("NEW2"), _order("NEW3"))
        admitted = reserve_opening_order_slots(
            candidates, _account(held), maximum_positions=6, reserved_position_keys=reserved
        )
        occupied = {(position.market, position.symbol) for position in held} | set(reserved)
        for order in admitted:
            occupied.add((order.market, order.symbol))
            assert len(occupied) <= 6
