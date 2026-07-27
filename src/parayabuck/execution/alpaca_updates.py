from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from parayabuck.core.models import Market, OrderState
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class TradeUpdate:
    event: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    market: Market
    requested_qty: float
    filled_qty: float
    average_fill_price: float | None
    timestamp: datetime

    @property
    def state(self) -> OrderState | None:
        return {
            "new": OrderState.ACCEPTED,
            "accepted": OrderState.ACCEPTED,
            "partial_fill": OrderState.PARTIALLY_FILLED,
            "fill": OrderState.FILLED,
            "canceled": OrderState.CANCELLED,
            "cancelled": OrderState.CANCELLED,
            "rejected": OrderState.REJECTED,
            "expired": OrderState.EXPIRED,
        }.get(self.event)


UpdateHandler = Callable[[TradeUpdate], Awaitable[None]]


class AlpacaTradingUpdateStream:
    """TradingStream adapter; broker events are the primary order-state source."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        handler: UpdateHandler,
    ) -> None:
        from alpaca.trading.stream import TradingStream

        self._stream = TradingStream(api_key, secret_key, paper=paper)
        self._handler = handler
        self._stream.subscribe_trade_updates(self._on_update)

    async def _on_update(self, raw: Any) -> None:
        order = _field(raw, "order", raw)
        asset_class = str(_field(order, "asset_class", "")).lower()
        raw_symbol = str(_field(order, "symbol", ""))
        market = Market.CRYPTO if "crypto" in asset_class or "/" in raw_symbol else Market.EQUITY
        timestamp = _field(raw, "timestamp") or _field(order, "updated_at") or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        average = _field(order, "filled_avg_price")
        update = TradeUpdate(
            event=str(_field(raw, "event", "")).lower().split(".")[-1],
            client_order_id=str(_field(order, "client_order_id", "")),
            broker_order_id=str(_field(order, "id", "")),
            symbol=canonical_symbol(raw_symbol, market),
            market=market,
            requested_qty=float(_field(order, "qty", 0) or 0),
            filled_qty=float(_field(order, "filled_qty", 0) or 0),
            average_fill_price=float(average) if average is not None else None,
            timestamp=timestamp.astimezone(UTC),
        )
        await self._handler(update)

    def run(self) -> None:
        self._stream.run()

    def stop(self) -> None:
        self._stream.stop()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["AlpacaTradingUpdateStream", "TradeUpdate"]
