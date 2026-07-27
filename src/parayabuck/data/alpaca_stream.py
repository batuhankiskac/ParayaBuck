from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from parayabuck.core.models import Bar, Market

BarHandler = Callable[[Bar], Awaitable[None]]


class AlpacaBarStream:
    """Thin Alpaca WebSocket adapter. REST is intentionally not used for polling."""

    def __init__(self, api_key: str, secret_key: str, stock_feed: str = "iex") -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import CryptoDataStream, StockDataStream

        # alpaca-py otherwise inherits websockets' 10 second close timeout.
        # EngineRuntime only waits a bounded amount of time for these worker
        # threads, so an unresponsive close handshake could leave the previous
        # connection alive while a replacement runtime starts.
        websocket_params = {
            "ping_interval": 10,
            "ping_timeout": 180,
            "max_queue": 1024,
            "close_timeout": 2,
        }
        self.stock = StockDataStream(
            api_key,
            secret_key,
            feed=DataFeed(stock_feed),
            websocket_params=websocket_params,
        )
        self.crypto = CryptoDataStream(
            api_key,
            secret_key,
            websocket_params=websocket_params,
        )

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
        updated_handler: BarHandler | None = None,
    ) -> None:
        async def stock_handler(raw: Any) -> None:
            await handler(self._convert(raw, Market.EQUITY, timeframe))

        async def crypto_handler(raw: Any) -> None:
            await handler(self._convert(raw, Market.CRYPTO, timeframe))

        async def stock_updated_handler(raw: Any) -> None:
            target = updated_handler or handler
            await target(self._convert(raw, Market.EQUITY, timeframe))

        async def crypto_updated_handler(raw: Any) -> None:
            target = updated_handler or handler
            await target(self._convert(raw, Market.CRYPTO, timeframe))

        if stock_symbols:
            self.stock.subscribe_bars(stock_handler, *stock_symbols)
            subscribe_updated = getattr(self.stock, "subscribe_updated_bars", None)
            if subscribe_updated is not None:
                subscribe_updated(stock_updated_handler, *stock_symbols)
        if crypto_symbols:
            # Alpaca's crypto WebSocket uses pair symbols (for example
            # ``BTC/USD``).  Removing the slash produces an invalid subscribe
            # payload and the server replies with ``invalid syntax (400)``.
            self.crypto.subscribe_bars(crypto_handler, *crypto_symbols)
            subscribe_updated = getattr(self.crypto, "subscribe_updated_bars", None)
            if subscribe_updated is not None:
                subscribe_updated(crypto_updated_handler, *crypto_symbols)

    def run_stock(self) -> None:
        self.stock.run()

    def run_crypto(self) -> None:
        self.crypto.run()

    def stop(self) -> None:
        self.stock.stop()
        self.crypto.stop()
