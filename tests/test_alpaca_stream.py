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


class _RemoveLegFakeStream(_FakeStream):
    def __init__(self) -> None:
        super().__init__()
        self.fail_unsubscribe_updated = False
        self.updated_subscriptions: list[tuple[str, ...]] = []
        self.updated_unsubscriptions: list[tuple[str, ...]] = []

    def subscribe_updated_bars(self, handler: Any, *symbols: str) -> None:
        self.updated_subscriptions.append(symbols)

    def unsubscribe_updated_bars(self, *symbols: str) -> None:
        if self.fail_unsubscribe_updated:
            self.fail_unsubscribe_updated = False
            raise RuntimeError("updated unsubscribe rejected")
        self.updated_unsubscriptions.append(symbols)


def test_update_subscriptions_rolls_back_added_leg_when_remove_leg_fails() -> None:
    """Within one market: if the removed symbols' updated-bars leg fails
    after the added symbols were already subscribed, the added symbols must
    be unsubscribed again; otherwise the socket diverges from the local
    bookkeeping and no outer rollback covers this market."""
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stock = _RemoveLegFakeStream()
    crypto = _FakeStream()
    stream.stock = stock
    stream.crypto = crypto

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe(["AAPL", "MSFT"], [], handler)

    import pytest

    stock.fail_unsubscribe_updated = True
    with pytest.raises(RuntimeError, match="updated unsubscribe rejected"):
        stream.update_subscriptions(["MSFT", "NVDA"], [])

    # NVDA (added) must be unsubscribed again: plain and updated legs.
    assert ("NVDA",) in stock.unsubscriptions
    assert ("NVDA",) in stock.updated_unsubscriptions
    # AAPL (removed) must be re-subscribed: plain and updated legs.
    assert ("AAPL",) in [symbols for _, symbols in stock.subscriptions]
    assert ("AAPL",) in stock.updated_subscriptions
    assert stream._stock_symbols == {"AAPL", "MSFT"}


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


class _ExplodingFakeStream(_FakeStream):
    def __init__(self) -> None:
        super().__init__()
        self.explode = False

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        if self.explode:
            raise RuntimeError("websocket rejected subscription")
        self.subscriptions.append((handler, symbols))

    def unsubscribe_bars(self, *symbols: str) -> None:
        self.unsubscriptions.append(symbols)


def test_update_subscriptions_rolls_back_stock_membership_when_crypto_fails() -> None:
    """A failed crypto change must not leave the stock socket membership and
    the local bookkeeping out of sync; the whole update is rolled back."""
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stock = _FakeStream()
    crypto = _ExplodingFakeStream()
    stream.stock = stock
    stream.crypto = crypto

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe(["AAPL", "MSFT"], ["BTC/USD"], handler)

    import pytest

    crypto.explode = True
    with pytest.raises(RuntimeError, match="websocket rejected subscription"):
        stream.update_subscriptions(["MSFT", "NVDA"], ["ETH/USD"])

    # The stock change (added NVDA, removed AAPL) was rolled back.
    assert stock.unsubscriptions == [("AAPL",), ("NVDA",)]
    assert ("AAPL",) in [symbols for _, symbols in stock.subscriptions]
    assert stream._stock_symbols == {"AAPL", "MSFT"}
    assert stream._crypto_symbols == {"BTC/USD"}


class _UpdatedBarFakeStream(_FakeStream):
    def __init__(self) -> None:
        super().__init__()
        self.fail_updated = False

    def subscribe_updated_bars(self, handler: Any, *symbols: str) -> None:
        if self.fail_updated:
            raise RuntimeError("updated-bars subscription rejected")


def test_update_subscriptions_rolls_back_partial_stock_subscribe() -> None:
    """If the updated-bars leg fails after the plain subscription was
    accepted, the added symbols must be unsubscribed again before raising."""
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    stock = _UpdatedBarFakeStream()
    crypto = _FakeStream()
    stream.stock = stock
    stream.crypto = crypto

    async def handler(_bar: Any) -> None:
        pass

    stream.subscribe(["AAPL"], ["BTC/USD"], handler)

    import pytest

    stock.fail_updated = True
    with pytest.raises(RuntimeError, match="updated-bars subscription rejected"):
        stream.update_subscriptions(["AAPL", "NVDA"], ["BTC/USD"])

    assert ("NVDA",) in stock.unsubscriptions
    assert stream._stock_symbols == {"AAPL"}
    assert stream._crypto_symbols == {"BTC/USD"}
