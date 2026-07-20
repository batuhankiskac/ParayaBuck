from __future__ import annotations

from typing import Any

from parayabuck_v2.data.alpaca_stream import AlpacaBarStream


class _FakeStream:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[Any, tuple[str, ...]]] = []

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self.subscriptions.append((handler, symbols))


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
