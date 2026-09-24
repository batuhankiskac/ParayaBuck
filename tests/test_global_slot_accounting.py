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
CRYPTOS = ("BCH/USD", "BTC/USD", "LTC/USD")


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
        risk_reducing=side == PortfolioOrderSide.SELL,
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


@pytest.mark.parametrize(
    ("positions", "reserved", "open_symbols", "candidates", "expected"),
    [
        # C5: a working crypto opening occupies the second market slot.
        ((_position("BTC/USD", Market.CRYPTO),), ((Market.CRYPTO, "LTC/USD"),), (), ("DOGE/USD",), ()),
        # C6: adding to an already held crypto symbol costs no new slot.
        (
            (_position("BTC/USD", Market.CRYPTO), _position("LTC/USD", Market.CRYPTO)),
            (),
            (),
            ("BTC/USD",),
            ("BTC/USD",),
        ),
        # C8: global room never overrides the crypto market cap.
        ((_position("BTC/USD", Market.CRYPTO), _position("LTC/USD", Market.CRYPTO)), (), (), ("DOGE/USD",), ()),
        # C9: crypto room never overrides the global cap.
        (
            (
                *(_position(symbol, Market.EQUITY) for symbol in EQUITIES),
                _position("BTC/USD", Market.CRYPTO),
                _position("PENDING", Market.EQUITY),
            ),
            (),
            (),
            ("DOGE/USD",),
            (),
        ),
        # C10: both gates open, then the next same-cycle crypto opening is denied.
        ((_position("BTC/USD", Market.CRYPTO),), (), (), ("DOGE/USD", "ETH/USD"), ("DOGE/USD",)),
        # Broker alias and durable local representation of one order count once.
        ((_position("BTC/USD", Market.CRYPTO),), ((Market.CRYPTO, "LTC/USD"),), ("LTCUSD",), ("DOGE/USD",), ()),
    ],
)
def test_market_and_global_final_slot_gates(
    positions: tuple[PositionSnapshot, ...],
    reserved: tuple[tuple[Market, str], ...],
    open_symbols: tuple[str, ...],
    candidates: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    admitted = reserve_opening_order_slots(
        tuple(_order(symbol, Market.CRYPTO) for symbol in candidates),
        _account(positions),
        maximum_positions=8,
        market_position_limits={Market.CRYPTO: 2},
        reserved_position_keys=reserved,
        open_order_symbols=open_symbols,
    )
    assert tuple(order.symbol for order in admitted) == expected


def test_market_gate_preserves_sell_and_cross_market_global_capacity() -> None:
    six_equities = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    two_crypto = (_position("BTC/USD", Market.CRYPTO), _position("LTC/USD", Market.CRYPTO))
    sell = _order("BTC/USD", Market.CRYPTO, PortfolioOrderSide.SELL)
    equity_buy = _order("NEW", Market.EQUITY)
    crypto_buy = _order("DOGE/USD", Market.CRYPTO)
    limits = {Market.CRYPTO: 2}
    assert reserve_opening_order_slots(
        (sell, equity_buy, crypto_buy),
        _account((*six_equities, *two_crypto)),
        maximum_positions=8,
        market_position_limits=limits,
    ) == (sell,)
    assert reserve_opening_order_slots(
        (equity_buy, crypto_buy),
        _account((*six_equities[:5], *two_crypto)),
        maximum_positions=8,
        market_position_limits=limits,
    ) == (equity_buy,)
    assert reserve_opening_order_slots(
        (crypto_buy, equity_buy),
        _account((*six_equities, two_crypto[0])),
        maximum_positions=8,
        market_position_limits=limits,
    ) == (crypto_buy,)


def test_partial_crypto_opening_and_cancel_in_flight_keep_one_market_claim() -> None:
    doge = _order("DOGE/USD", Market.CRYPTO)
    ltc_sell = _order("LTC/USD", Market.CRYPTO, PortfolioOrderSide.SELL)
    limits = {Market.CRYPTO: 2}
    # BTC is partially filled and still working. Its broker position, durable
    # reservation and broker alias are one canonical slot; LTC is the second.
    account = _account((_position("BTC/USD", Market.CRYPTO, 0.1), _position("LTC/USD", Market.CRYPTO)))
    assert reserve_opening_order_slots(
        (ltc_sell, doge),
        account,
        maximum_positions=8,
        market_position_limits=limits,
        reserved_position_keys=((Market.CRYPTO, "BTCUSD"),),
        open_order_symbols=("BTC/USD",),
    ) == (ltc_sell,)
    # Cancel requested but not yet terminal: the working opening still claims
    # BTC even if the account snapshot has not shown its first fill.
    unfilled = _account((_position("LTC/USD", Market.CRYPTO),))
    assert (
        reserve_opening_order_slots(
            (doge,),
            unfilled,
            maximum_positions=8,
            market_position_limits=limits,
            open_order_symbols=("BTCUSD",),
        )
        == ()
    )
    # After terminal cancellation and a fresh account without BTC, exactly
    # one new opening remains available; an existing partial position does not vanish.
    assert reserve_opening_order_slots(
        (doge, _order("ETH/USD", Market.CRYPTO)),
        unfilled,
        maximum_positions=8,
        market_position_limits=limits,
    ) == (doge,)


def test_synthetic_eight_slots_and_six_slot_rollback_preserve_exits() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    seventh = _order("NEW1")
    eighth = _order("BTC/USD", Market.CRYPTO)
    ninth = _order("ETH/USD", Market.CRYPTO)
    assert reserve_opening_order_slots((seventh, eighth, ninth), _account(held), maximum_positions=8) == (
        seventh,
        eighth,
    )
    eight_held = (*held, _position("NEW1", Market.EQUITY), _position("BTC/USD", Market.CRYPTO))
    exit_order = _order("ALL", side=PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((exit_order, ninth), _account(eight_held), maximum_positions=6) == (exit_order,)
    assert reserve_opening_order_slots((ninth,), _account(eight_held[3:]), maximum_positions=6) == (ninth,)


def test_shipped_cap8_counts_restored_working_slot_and_keeps_crypto_cap_two() -> None:
    config = load_config(str(Path(__file__).resolve().parents[1] / "config" / "settings.yaml"))
    limits = portfolio_risk_limits_from_config(config.risk)
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    account = _account(held)
    restored_working = ((Market.EQUITY, "PENDING"),)
    eighth = _order("BTC/USD", Market.CRYPTO)
    ninth = _order("ETH/USD", Market.CRYPTO)
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
            reserved_position_keys=(*restored_working, (Market.CRYPTO, "BTC/USD")),
        )
        == ()
    )

    target = TargetPortfolio(
        strategy_name="synthetic_crypto",
        strategy_version="1",
        decision_timestamp=NOW,
        weights=(
            TargetWeight("BCH/USD", 0.03),
            TargetWeight("BTC/USD", 0.02),
            TargetWeight("LTC/USD", 0.01),
        ),
        cash_weight=0.94,
    )
    decision = PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.CRYPTO,
        approved_symbols=CRYPTOS,
        maximum_allocation=0.05,
        mode="paper",
    )
    assert len(decision.target.weights) == limits.maximum_crypto_positions == 2


def test_opening_reservation_transitions_to_position_or_releases_once() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:5])
    pending = ((Market.CRYPTO, "BTC/USD"),)
    second = _order("ETH/USD", Market.CRYPTO)
    assert (
        reserve_opening_order_slots((second,), _account(held), maximum_positions=6, reserved_position_keys=pending)
        == ()
    )
    # A canceled/rejected order disappears from both live order sources. A
    # repeated terminal update cannot release any second slot.
    assert reserve_opening_order_slots((second,), _account(held), maximum_positions=6) == (second,)
    assert reserve_opening_order_slots((second,), _account(held), maximum_positions=6) == (second,)
    filled = _account((*held, _position("BTC/USD", Market.CRYPTO)))
    assert reserve_opening_order_slots((second,), filled, maximum_positions=6) == ()


def test_broker_open_order_symbol_and_cross_market_intents_share_cap() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:5])
    equity = _order("NEW1")
    crypto = _order("BTC/USD", Market.CRYPTO)
    assert reserve_opening_order_slots((crypto, equity), _account(held), maximum_positions=6) == (crypto,)
    assert (
        reserve_opening_order_slots((equity,), _account(held), maximum_positions=6, open_order_symbols=("BTCUSD",))
        == ()
    )


def test_production_overlay_and_band_suppressed_ltc_exit_cannot_open_seventh_slot() -> None:
    held = (
        *(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4]),
        _position("BCH/USD", Market.CRYPTO, 20),
        _position("LTC/USD", Market.CRYPTO, 1),
    )
    account = _account(held)
    target = TargetPortfolio(
        strategy_name="synthetic_crypto",
        strategy_version="1",
        decision_timestamp=NOW,
        weights=(TargetWeight("BCH/USD", 0.025), TargetWeight("BTC/USD", 0.025)),
        cash_weight=0.95,
    )
    limits = PortfolioRiskLimits(maximum_positions=6, maximum_crypto_positions=2, maximum_crypto=0.05)
    risk = PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.CRYPTO,
        approved_symbols=CRYPTOS,
        maximum_allocation=0.05,
        mode="paper",
    )
    rules = {symbol: AssetTradingRules(symbol, Market.CRYPTO, 0.000001, 0.000001, 0.01, 1.0) for symbol in CRYPTOS}
    planned = TargetToOrderPlanner().plan(
        risk.target,
        account,
        market=Market.CRYPTO,
        prices={symbol: 100.0 for symbol in CRYPTOS},
        rules=rules,
        approved_symbols=CRYPTOS,
    )
    assert [(order.symbol, order.side.value) for order in planned] == [("BCH/USD", "buy"), ("BTC/USD", "buy")]
    assert TargetToOrderPlanner().policy.rebalance_band_pct == 0.005
    admitted = reserve_opening_order_slots(planned, account, maximum_positions=6)
    assert [(order.symbol, order.side.value) for order in admitted] == [("BCH/USD", "buy")]


def test_executed_sell_still_needs_fresh_zero_position_before_replacement() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in EQUITIES)
    buy = _order("NEW1")
    sell = _order("ALL", side=PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((sell, buy), _account(held), maximum_positions=6) == (sell,)
    assert reserve_opening_order_slots((buy,), _account(held[1:]), maximum_positions=6) == (buy,)


def test_band_eligible_ltc_sell_does_not_release_slot_at_order_planning() -> None:
    held = (
        *(_position(symbol, Market.EQUITY) for symbol in EQUITIES[:4]),
        _position("BCH/USD", Market.CRYPTO, 20),
        _position("LTC/USD", Market.CRYPTO, 10),
    )
    account = _account(held)
    target = TargetPortfolio(
        strategy_name="synthetic_crypto",
        strategy_version="1",
        decision_timestamp=NOW,
        weights=(TargetWeight("BCH/USD", 0.025), TargetWeight("BTC/USD", 0.025)),
        cash_weight=0.95,
    )
    rules = {symbol: AssetTradingRules(symbol, Market.CRYPTO, 0.000001, 0.000001, 0.01, 1.0) for symbol in CRYPTOS}
    planned = TargetToOrderPlanner().plan(
        target,
        account,
        market=Market.CRYPTO,
        prices={symbol: 100.0 for symbol in CRYPTOS},
        rules=rules,
        approved_symbols=CRYPTOS,
    )
    assert any(order.symbol == "LTC/USD" and order.side == PortfolioOrderSide.SELL for order in planned)
    assert any(order.symbol == "BTC/USD" and order.side == PortfolioOrderSide.BUY for order in planned)
    admitted = reserve_opening_order_slots(planned, account, maximum_positions=6)
    assert all(order.symbol != "BTC/USD" for order in admitted)
    after_confirmed_zero = _account(held[:-1])
    btc = next(order for order in planned if order.symbol == "BTC/USD")
    assert reserve_opening_order_slots((btc,), after_confirmed_zero, maximum_positions=6) == (btc,)


def test_table_of_mixed_positions_orders_and_partial_exits_never_spends_seventh_slot() -> None:
    for equities, cryptos, pending_buys, pending_sells, partial in product(
        range(7), range(3), range(3), range(2), (False, True)
    ):
        if equities + cryptos > 6:
            continue
        held = tuple(
            _position(symbol, Market.EQUITY, 0.1 if partial and index == 0 else 10)
            for index, symbol in enumerate(EQUITIES[:equities])
        )
        held += tuple(_position(symbol, Market.CRYPTO) for symbol in CRYPTOS[:cryptos])
        reserved = tuple((Market.EQUITY, f"PENDING{index}") for index in range(pending_buys))
        if equities + cryptos + pending_buys > 6:
            continue
        if pending_sells and held:
            reserved += ((held[0].market, held[0].symbol),)
        candidates = (_order("NEW1"), _order("NEW2"), _order("ETH/USD", Market.CRYPTO))
        admitted = reserve_opening_order_slots(
            candidates, _account(held), maximum_positions=6, reserved_position_keys=reserved
        )
        occupied = {(position.market, position.symbol) for position in held} | set(reserved)
        for order in admitted:
            occupied.add((order.market, order.symbol))
            assert len(occupied) <= 6
