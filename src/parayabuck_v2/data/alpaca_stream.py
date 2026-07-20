from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from parayabuck_v2.core.models import Bar, Market

BarHandler = Callable[[Bar], Awaitable[None]]


class AlpacaBarStream:
    """Thin Alpaca WebSocket adapter. REST is intentionally not used for polling."""

    def __init__(self, api_key: str, secret_key: str, stock_feed: str = "iex") -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import CryptoDataStream, StockDataStream

        self.stock = StockDataStream(api_key, secret_key, feed=DataFeed(stock_feed))
        self.crypto = CryptoDataStream(api_key, secret_key)

    @staticmethod
    def _convert(raw: Any, market: Market, timeframe: str) -> Bar:
        return Bar(
            timestamp=raw.timestamp,
            symbol=str(raw.symbol),
            timeframe=timeframe,
            open=float(raw.open),
            high=float(raw.high),
            low=float(raw.low),
            close=float(raw.close),
            volume=float(raw.volume),
            market=market,
        )

    def subscribe(
        self,
        stock_symbols: list[str],
        crypto_symbols: list[str],
        handler: BarHandler,
        timeframe: str = "1m",
    ) -> None:
        async def stock_handler(raw: Any) -> None:
            await handler(self._convert(raw, Market.EQUITY, timeframe))

        async def crypto_handler(raw: Any) -> None:
            await handler(self._convert(raw, Market.CRYPTO, timeframe))

        if stock_symbols:
            self.stock.subscribe_bars(stock_handler, *stock_symbols)
        if crypto_symbols:
            normalized = [symbol.replace("/", "") for symbol in crypto_symbols]
            self.crypto.subscribe_bars(crypto_handler, *normalized)

    def run_stock(self) -> None:
        self.stock.run()

    def run_crypto(self) -> None:
        self.crypto.run()

    def stop(self) -> None:
        self.stock.stop()
        self.crypto.stop()
