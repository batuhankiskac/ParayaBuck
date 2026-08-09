from __future__ import annotations

from typing import Any

from parayabuck.data.alpaca_stream import AlpacaBarStream


class _FakeStream:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[Any, tuple[str, ...]]] = []
        self.unsubscriptions: list[tuple[str, ...]] = []

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self.subscriptions.append((handler, symbols))

    def unsubscribe_bars(self, *symbols: str) -> None:
        self.unsubscriptions.append(symbols)


def test_crypto_subscription_preserves_alpaca_pair_symbols() -> None:
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = _FakeStream()
    stream.crypto = _FakeStream()

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe([], ["BTC/USD", "ETH/USD"], handler)

    assert len(stream.crypto.subscriptions) == 1
    _, symbols = stream.crypto.subscriptions[0]
    assert symbols == ("BTC/USD", "ETH/USD")


def test_runtime_subscription_update_adds_and_removes_exact_symbols() -> None:
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stream.stock = _FakeStream()
    stream.crypto = _FakeStream()

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe(["AAPL", "MSFT"], ["BTC/USD"], handler)
    changes = stream.update_subscriptions(["MSFT", "NVDA"], ["ETH/USD"])

    assert changes == {
        "added_stock": ("NVDA",),
        "removed_stock": ("AAPL",),
        "added_crypto": ("ETH/USD",),
        "removed_crypto": ("BTC/USD",),
    }
    assert stream.stock.unsubscriptions == [("AAPL",)]
    assert stream.crypto.unsubscriptions == [("BTC/USD",)]
