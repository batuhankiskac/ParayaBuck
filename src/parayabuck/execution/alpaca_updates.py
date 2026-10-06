from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from parayabuck.core.models import Market
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.utils.objects import field_value as _field

log = get_logger("parayabuck.execution.alpaca_updates")


@dataclass(frozen=True)
class TradeUpdate:
    event: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    market: Market
    filled_qty: float
    average_fill_price: float | None
    timestamp: datetime
    # ``None`` means Alpaca did not supply a fee.  It is intentionally
    # distinct from an observed zero: execution measurement must not infer
    # that a missing fee is free.
    fee: float | None = None
    activity_id: str | None = None
    source: str = "stream"
    received_at: datetime | None = None
    # ``None`` retains compatibility for local/fake callers that provide a
    # concrete fee but predate explicit provenance; the repository treats a
    # supplied fee as observed in that case.  Stream callers always provide
    # an explicit boolean.
    fee_observed: bool | None = None


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
        # This coroutine runs inside alpaca-py's TradingStream dispatch: an
        # exception escaping it can stall the stream for good. A malformed
        # broker event is logged and dropped; reconciliation recovers the
        # order state from the broker. Handler failures are the runtime's own
        # concern and are handled (and halted on) inside the handler.
        try:
            update = self._parse(raw)
        except Exception:
            log.error("Malformed Alpaca trade update dropped: %r", raw, exc_info=True)
            return
        await self._handler(update)

    @staticmethod
    def _parse(raw: Any) -> TradeUpdate:
        received_at = datetime.now(UTC)
        order = _field(raw, "order", raw)
        asset_class = str(_field(order, "asset_class", "")).lower()
        raw_symbol = str(_field(order, "symbol", ""))
        market = Market.CRYPTO if "crypto" in asset_class or "/" in raw_symbol else Market.EQUITY
        timestamp = _field(raw, "timestamp") or _field(order, "updated_at") or datetime.now(UTC)
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        average = _field(order, "filled_avg_price")
        client_order_id = str(_field(order, "client_order_id", ""))
        missing = object()
        fee_raw = _field(raw, "commission", missing)
        if fee_raw is missing:
            fee_raw = _field(raw, "fee", missing)
        if fee_raw is missing:
            fee_raw = _field(order, "commission", missing)
        fee_observed = fee_raw is not missing and fee_raw is not None
        try:
            fee = float(fee_raw) if fee_observed else None
        except (TypeError, ValueError):
            fee = None
            fee_observed = False
        return TradeUpdate(
            event=str(_field(raw, "event", "")).lower().split(".")[-1],
            client_order_id=client_order_id,
            broker_order_id=str(_field(order, "id", "")),
            symbol=canonical_symbol(raw_symbol, market),
            market=market,
            filled_qty=float(_field(order, "filled_qty", 0) or 0),
            average_fill_price=float(average) if average is not None else None,
            timestamp=timestamp.astimezone(UTC),
            fee=fee,
            activity_id=str(
                _field(raw, "execution_id", _field(raw, "id", ""))
                or (
                    f"{client_order_id}:{_field(raw, 'event', '')}:"
                    f"{timestamp.isoformat()}:{_field(order, 'filled_qty', 0)}"
                )
            ),
            source="stream",
            received_at=received_at,
            fee_observed=fee_observed,
        )

    def run(self) -> None:
        self._stream.run()

    def stop(self) -> None:
        # alpaca-py creates the event loop only inside run(); its stop()
        # dereferences that loop, so a never-run stream has nothing to stop.
        if getattr(self._stream, "_loop", ...) is None:
            return
        self._stream.stop()


__all__ = ["AlpacaTradingUpdateStream", "TradeUpdate"]
