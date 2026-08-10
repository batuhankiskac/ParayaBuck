from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from parayabuck.core.models import Bar, Market
from parayabuck.logger import get_logger

log = get_logger("parayabuck.data.alpaca_stream")

BarHandler = Callable[[Bar], Awaitable[None]]


class AlpacaBarStream:
    """Thin Alpaca WebSocket adapter. REST is intentionally not used for polling."""

    def __init__(self, api_key: str, secret_key: str, stock_stream_feed: str = "iex") -> None:
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
            feed=DataFeed(stock_stream_feed),
            websocket_params=websocket_params,
        )
        self.crypto = CryptoDataStream(
            api_key,
            secret_key,
            websocket_params=websocket_params,
        )
        # Set when a subscription rollback leg fails: the socket membership is
        # then UNKNOWN and callers must halt entries and force a reconnect
        # from the authoritative desired membership instead of assuming the
        # previous state survived.
        self._rollback_failed = False

    @property
    def rollback_failed(self) -> bool:
        return self._rollback_failed

    def clear_rollback_failure(self) -> None:
        self._rollback_failed = False

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

        self._stock_handler = stock_handler
        self._crypto_handler = crypto_handler
        self._stock_updated_handler = stock_updated_handler
        self._crypto_updated_handler = crypto_updated_handler
        self._stock_symbols = set(stock_symbols)
        self._crypto_symbols = set(crypto_symbols)

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

    def update_subscriptions(
        self, stock_symbols: list[str], crypto_symbols: list[str]
    ) -> dict[str, tuple[str, ...]]:
        """Atomically converge running streams on a newly approved symbol set.

        Stock and crypto memberships are applied in order. If the second
        operation fails, the first is rolled back (re-subscribing the removed
        symbols and unsubscribing the added ones) before the error propagates,
        so socket membership and local bookkeeping never diverge and callers
        can safely assume "the subscription call raised -> nothing changed".
        """
        desired_stock = set(stock_symbols)
        desired_crypto = set(crypto_symbols)
        current_stock = set(getattr(self, "_stock_symbols", set()))
        current_crypto = set(getattr(self, "_crypto_symbols", set()))
        added_stock = tuple(sorted(desired_stock - current_stock))
        removed_stock = tuple(sorted(current_stock - desired_stock))
        added_crypto = tuple(sorted(desired_crypto - current_crypto))
        removed_crypto = tuple(sorted(current_crypto - desired_crypto))

        stock_handler = getattr(self, "_stock_handler")
        stock_updated_handler = getattr(self, "_stock_updated_handler")
        crypto_handler = getattr(self, "_crypto_handler")
        crypto_updated_handler = getattr(self, "_crypto_updated_handler")
        applied: list[tuple[object, tuple[str, ...], tuple[str, ...], Any, Any]] = []
        try:
            self._update_market_subscription(
                self.stock,
                added_stock,
                removed_stock,
                stock_handler,
                stock_updated_handler,
            )
            applied.append(
                (
                    self.stock,
                    added_stock,
                    removed_stock,
                    stock_handler,
                    stock_updated_handler,
                )
            )
            self._update_market_subscription(
                self.crypto,
                added_crypto,
                removed_crypto,
                crypto_handler,
                crypto_updated_handler,
            )
            applied.append(
                (self.crypto, added_crypto, removed_crypto, crypto_handler, crypto_updated_handler)
            )
        except Exception as original:
            # Roll back every completed market in reverse order: re-subscribe
            # what was unsubscribed and unsubscribe what was added. If a
            # rollback leg fails the socket state is unknown; callers must
            # halt entries and force a reconnect instead of trusting it.
            for stream, added, removed, handler, updated_handler in reversed(applied):
                try:
                    self._update_market_subscription(
                        stream, removed, added, handler, updated_handler
                    )
                except Exception:
                    self._rollback_failed = True
                    log.exception("Rollback of stream subscription change failed")
            raise original
        self._stock_symbols = desired_stock
        self._crypto_symbols = desired_crypto
        return {
            "added_stock": added_stock,
            "removed_stock": removed_stock,
            "added_crypto": added_crypto,
            "removed_crypto": removed_crypto,
        }

    @staticmethod
    def _update_market_subscription(
        stream: Any,
        added: tuple[str, ...],
        removed: tuple[str, ...],
        handler: Any,
        updated_handler: Any,
    ) -> None:
        """Converge one market's socket membership and roll back every leg this
        call applied if any later leg fails.

        A failure at any point must undo both the added and the removed legs;
        otherwise a remove-leg failure (for example) leaves the freshly added
        symbols subscribed while the local bookkeeping still says otherwise,
        and the outer transaction rollback never sees this market at all.
        """
        subscribe_updated = getattr(stream, "subscribe_updated_bars", None)
        unsubscribe_updated = getattr(stream, "unsubscribe_updated_bars", None)
        plain_added = plain_removed = False
        try:
            if added:
                stream.subscribe_bars(handler, *added)
                plain_added = True
                if subscribe_updated is not None:
                    subscribe_updated(updated_handler, *added)
            if removed:
                stream.unsubscribe_bars(*removed)
                plain_removed = True
                if unsubscribe_updated is not None:
                    unsubscribe_updated(*removed)
        except Exception:
            # Undo everything this call applied so socket membership and the
            # local bookkeeping never diverge, even before the outer
            # transaction rollback (which only covers whole completed markets).
            if plain_added:
                try:
                    stream.unsubscribe_bars(*added)
                except Exception:
                    log.exception("Rollback of a partial subscription failed")
                if unsubscribe_updated is not None:
                    try:
                        unsubscribe_updated(*added)
                    except Exception:
                        log.exception("Rollback of a partial updated subscription failed")
            if plain_removed:
                try:
                    stream.subscribe_bars(handler, *removed)
                except Exception:
                    log.exception("Rollback of a partial unsubscription failed")
                if subscribe_updated is not None:
                    try:
                        subscribe_updated(updated_handler, *removed)
                    except Exception:
                        log.exception("Rollback of a partial updated unsubscription failed")
            raise

    def run_stock(self) -> None:
        self.stock.run()

    def run_crypto(self) -> None:
        self.crypto.run()

    def stop(self) -> None:
        self.stock.stop()
        self.crypto.stop()
