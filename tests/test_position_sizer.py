from __future__ import annotations

import pytest

from parayabuck.models import Account, Order, Side
from parayabuck.risk.sizer import PositionSizer


def _account() -> Account:
    return Account(balance=10_000, equity=10_000)


def test_crypto_minimum_quantity_is_based_on_dollar_notional() -> None:
    sizer = PositionSizer(min_crypto_order_notional=12.0)
    sizer.update_history("ETH/USD", 1_881, 1_879, 1_880.05)

    sized = sizer.size_order(Order("ETH/USD", Side.BUY, 1), _account())

    assert sized.qty == pytest.approx(12.0 / 1_880.05)
    assert sized.qty > 0.00053453


@pytest.mark.parametrize(
    ("symbol", "price"),
    [("SOL/USD", 76.97), ("ADA/USD", 0.16), ("BTC/USD", 118_000.0)],
)
def test_crypto_minimum_is_above_alpaca_ten_dollar_cost_basis(
    symbol: str, price: float
) -> None:
    sizer = PositionSizer(min_crypto_order_notional=12.0)
    sizer.update_history(symbol, price * 1.01, price * 0.99, price)

    sized = sizer.size_order(Order(symbol, Side.BUY, 1), _account())

    assert sized.qty * price == pytest.approx(12.0)
    assert sized.qty * price >= 10.0


def test_fractional_stock_minimum_quantity_is_based_on_dollar_notional() -> None:
    sizer = PositionSizer(fractional_shares=True, min_stock_order_notional=5.0)
    sizer.update_history("AAPL", 251, 249, 250)

    sized = sizer.size_order(Order("AAPL", Side.BUY, 1), _account())

    assert sized.qty == pytest.approx(0.02)


def test_whole_share_stock_keeps_one_share_minimum() -> None:
    sizer = PositionSizer(fractional_shares=False, min_stock_order_notional=5.0)
    sizer.update_history("AAPL", 251, 249, 250)

    sized = sizer.size_order(Order("AAPL", Side.BUY, 1), _account())

    assert sized.qty == 1
