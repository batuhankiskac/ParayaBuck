from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from parayabuck.core.models import Bar, Market

BarHandler = Callable[[Bar], Awaitable[None]]


class AlpacaBarStream:
    """Thin Alpaca equity WebSocket adapter. REST is intentionally not used for polling."""

    def __init__(self, api_key: str, secret_key: str, stock_stream_feed: str = "iex") -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

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
            feed=DataFeed(stock_stream_feed),
            websocket_params=websocket_params,
        )

    @staticmethod
    def _convert(raw: Any, timeframe: str) -> Bar:
        return Bar(
            timestamp=raw.timestamp,
            symbol=str(raw.symbol),
            timeframe=timeframe,
            open=float(raw.open),
            high=float(raw.high),
            low=float(raw.low),
            close=float(raw.close),
            volume=float(raw.volume),
            market=Market.EQUITY,
        )

    def subscribe(
        self,
        stock_symbols: list[str],
        handler: BarHandler,
        timeframe: str = "1m",
        updated_handler: BarHandler | None = None,
    ) -> None:
        async def stock_handler(raw: Any) -> None:
            await handler(self._convert(raw, timeframe))

        async def stock_updated_handler(raw: Any) -> None:
            target = updated_handler or handler
            await target(self._convert(raw, timeframe))

        if stock_symbols:
            self.stock.subscribe_bars(stock_handler, *stock_symbols)
            subscribe_updated = getattr(self.stock, "subscribe_updated_bars", None)
            if subscribe_updated is not None:
                subscribe_updated(stock_updated_handler, *stock_symbols)

    def run_stock(self) -> None:
        self.stock.run()

    @staticmethod
    def _stop_sdk_stream(stream: Any) -> None:
        # alpaca-py creates the event loop only inside run(); its stop()
        # dereferences that loop, so a never-run stream has nothing to stop.
        if getattr(stream, "_loop", ...) is None:
            return
        stream.stop()

    def stop(self) -> None:
        self._stop_sdk_stream(self.stock)
