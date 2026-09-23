"""Read-only synthetic cap-8 parity on the corrected production main."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime

from parayabuck import load_config
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    portfolio_risk_limits_from_config,
    reserve_opening_order_slots,
)


def _position(symbol: str, market: Market) -> PositionSnapshot:
    return PositionSnapshot(symbol, 1, 100, 100, market, qty_available=1, managed_qty=1)


def _order(symbol: str, market: Market, side: PortfolioOrderSide) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        f"parity-{symbol}-{side.value}",
        "parity",
        symbol,
        market,
        side,
        1,
        100,
        datetime(2026, 9, 24, tzinfo=UTC),
        "parity",
        "1",
        side == PortfolioOrderSide.SELL,
    )


def test_only_in_memory_account_position_cap_changes() -> None:
    config = load_config("config/settings.yaml")
    assert config.runtime.mode.value == "paper"
    assert config.live.enabled is False
    assert config.risk.max_positions == 6
    candidate = replace(config.risk, max_positions=8)
    before, after = asdict(config.risk), asdict(candidate)
    assert {key for key in before if before[key] != after[key]} == {"max_positions"}
    limits = portfolio_risk_limits_from_config(candidate)
    assert limits.maximum_positions == 8
    assert limits.maximum_crypto_positions == 2
    assert limits.maximum_gross == limits.maximum_equity == 0.6
    assert limits.minimum_cash == 0.4
    assert limits.maximum_equity_asset == 0.1
    assert limits.maximum_crypto_asset == 0.05
    assert limits.maximum_crypto == 0.1


def test_eight_account_slots_and_two_crypto_slots_without_early_sell_release() -> None:
    held = tuple(_position(symbol, Market.EQUITY) for symbol in ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG"))
    account = AccountSnapshot(99_400, 100_000, 100_000, 0, held)
    seventh = _order("TSLA", Market.EQUITY, PortfolioOrderSide.BUY)
    eighth = _order("BTC/USD", Market.CRYPTO, PortfolioOrderSide.BUY)
    ninth = _order("ETH/USD", Market.CRYPTO, PortfolioOrderSide.BUY)
    assert reserve_opening_order_slots((seventh, eighth, ninth), account, maximum_positions=8) == (seventh, eighth)
    pending = ((Market.EQUITY, "TSLA"), (Market.CRYPTO, "BTC/USD"))
    sell = _order("AAPL", Market.EQUITY, PortfolioOrderSide.SELL)
    assert reserve_opening_order_slots((sell, ninth), account, maximum_positions=8, reserved_position_keys=pending) == (
        sell,
    )
    assert reserve_opening_order_slots((sell, ninth), account, maximum_positions=6) == (sell,)
