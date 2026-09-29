from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca_stream import AlpacaBarStream


class _FakeStream:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[Any, tuple[str, ...]]] = []

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self.subscriptions.append((handler, symbols))


def _stream(stock: Any) -> AlpacaBarStream:
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = stock
    return stream


def test_subscribed_bars_are_delivered_as_equity_bars() -> None:
    stock = _FakeStream()
    stream = _stream(stock)
    received: list[Bar] = []

    async def handler(bar: Bar) -> None:
        received.append(bar)

    stream.subscribe(["AAPL"], handler, timeframe="1m")
    raw = SimpleNamespace(
        timestamp=datetime(2026, 7, 2, 14, 30, tzinfo=UTC),
        symbol="AAPL",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100,
    )
    stock_handler, symbols = stock.subscriptions[0]
    asyncio.run(stock_handler(raw))

    assert symbols == ("AAPL",)
    assert received == [Bar(raw.timestamp, "AAPL", "1m", 1.0, 2.0, 0.5, 1.5, 100.0, Market.EQUITY)]
