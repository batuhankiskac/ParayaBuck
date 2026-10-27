from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from parayabuck.core.models import Market
from parayabuck.data.alpaca_stream import guard_sdk_cast, supervise_sdk_stream
from parayabuck.execution.alerting import emit_execution_alert
from parayabuck.execution.alpaca_client import parse_broker_timestamp
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.utils.objects import field_value as _field

log = get_logger("parayabuck.execution.alpaca_updates")

# Event given to a trade-update frame the SDK could not decode. It is not in the
# runtime's booking vocabulary, so the runtime schedules a reconciliation.
UNDECODED_TRADE_EVENT = "undecoded"


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
    # The execution's own price and quantity from the frame, when Alpaca sends
    # them. ``average_fill_price`` above stays the order's cumulative average.
    execution_price: float | None = None
    execution_quantity: float | None = None


UpdateHandler = Callable[[TradeUpdate], Awaitable[None]]


@dataclass(frozen=True)
class _UndecodedFrame:
    """A trade-update frame the SDK model could not decode, kept as raw data."""

    payload: Any
    error: str


class AlpacaTradingUpdateStream:
    """TradingStream adapter; broker events are the primary order-state source."""

    # Set by stop(); a run() that starts after it returns without connecting.
    _stop_requested = False

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        handler: UpdateHandler,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        from alpaca.trading.stream import TradingStream

        # Same close timeout as the bar stream: websockets' 10 s default would
        # let an unresponsive close hold the old consumer past the stop join.
        websocket_params = {
            "ping_interval": 10,
            "ping_timeout": 180,
            "max_queue": 1024,
            "close_timeout": 2,
        }
        self._stream = TradingStream(api_key, secret_key, paper=paper, websocket_params=websocket_params)
        self._handler = handler
        guard_sdk_cast(self._stream, self._undecoded_frame)
        # A reconnect may have missed updates: the owner reconciles from the broker.
        supervise_sdk_stream(self._stream, label="trade updates", on_reconnect=on_reconnect)
        self._stream.subscribe_trade_updates(self._on_update)

    @staticmethod
    def _undecoded_frame(frame: Any, exc: BaseException) -> _UndecodedFrame:
        data = frame.get("data") if isinstance(frame, dict) else None
        payload = data if isinstance(data, dict) else frame
        return _UndecodedFrame(payload=payload, error=f"{type(exc).__name__}: {exc}")

    async def _on_update(self, raw: Any) -> None:
        # This coroutine runs inside alpaca-py's TradingStream dispatch: an
        # exception escaping it can stall the stream for good. A malformed
        # broker event is logged and reconciliation is requested, which recovers
        # the order state from the broker. Handler failures are the runtime's own
        # concern and are handled (and halted on) inside the handler.
        if isinstance(raw, _UndecodedFrame):
            await self._on_undecoded(raw)
            return
        try:
            update = self._parse(raw)
        except Exception as exc:
            # Handed on as undecoded so the runtime reconciles the order state
            # from the broker; a drop here would leave a missed fill to the
            # periodic reconcile alone.
            log.error("Malformed Alpaca trade update; requesting reconciliation: %r", raw, exc_info=True)
            await self._on_undecoded(_UndecodedFrame(payload=raw, error=f"{type(exc).__name__}: {exc}"))
            return
        await self._handler(update)

    async def _on_undecoded(self, frame: _UndecodedFrame) -> None:
        # The SDK could not decode a frame, so its fill facts are unread. The
        # order may still be known to the runtime, so the frame is handed on
        # with an unknown event: the runtime then schedules a reconciliation,
        # which reads the broker's state and books any fill.
        log.error("Undecodable Alpaca trade update (%s); requesting reconciliation: %r", frame.error, frame.payload)
        try:
            update = self._parse(frame.payload)
        except Exception:
            # The fields that failed to decode may be the ones _parse needs, so
            # fall back to the order identity alone: the runtime reconciles it.
            identity = _identity_update(frame.payload)
            if identity is not None:
                emit_execution_alert(
                    "paper_incident",
                    message=(
                        f"Undecodable Alpaca trade update for client_order_id={identity.client_order_id}; "
                        f"reconciliation requested ({frame.error})"
                    ),
                    category="undecoded_trade_update",
                    incident_id=f"undecoded_trade_update:{identity.client_order_id}:unparsed",
                    client_order_id=identity.client_order_id,
                )
                await self._handler(identity)
                return
            log.error("Undecodable Alpaca trade update has no usable identity: %r", frame.payload, exc_info=True)
            emit_execution_alert(
                "paper_incident",
                message=f"Undecodable Alpaca trade update with no usable identity ({frame.error}); dropped",
                category="undecoded_trade_update",
                incident_id=f"undecoded_trade_update:unidentified:{frame.error[:120]}",
            )
            return
        emit_execution_alert(
            "paper_incident",
            message=(
                f"Undecodable Alpaca trade update for client_order_id={update.client_order_id}; "
                f"reconciliation requested ({frame.error})"
            ),
            category="undecoded_trade_update",
            incident_id=f"undecoded_trade_update:{update.client_order_id}:{update.activity_id}",
            # One episode per order, not per activity id: each undecodable frame of the order is a repeat.
            episode_key=f"undecoded_trade_update:{update.client_order_id}",
            client_order_id=update.client_order_id,
        )
        await self._handler(replace(update, event=UNDECODED_TRADE_EVENT))

    @staticmethod
    def _parse(raw: Any) -> TradeUpdate:
        received_at = datetime.now(UTC)
        order = _field(raw, "order", raw)
        asset_class = str(_field(order, "asset_class", "")).lower()
        raw_symbol = str(_field(order, "symbol", ""))
        market = Market.CRYPTO if "crypto" in asset_class or "/" in raw_symbol else Market.EQUITY
        timestamp = _field(raw, "timestamp") or _field(order, "updated_at") or datetime.now(UTC)
        if isinstance(timestamp, str):
            timestamp = parse_broker_timestamp(timestamp)
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
            execution_price=_finite_or_none(_field(raw, "price")),
            execution_quantity=_finite_or_none(_field(raw, "qty")),
        )

    def run(self) -> None:
        # A stop that arrived before this run began must not be lost: the SDK
        # forgets it, and the run would then consume until process exit.
        if self._stop_requested:
            return
        self._stream.run()

    def stop(self) -> None:
        self._stop_requested = True
        # alpaca-py creates the event loop only inside run(); its stop()
        # dereferences that loop, so a never-run stream has nothing to stop.
        if getattr(self._stream, "_loop", ...) is None:
            return
        self._stream.stop()


def _identity_update(raw: Any) -> TradeUpdate | None:
    """An undecoded update carrying only the order identity, or None without one.

    The runtime answers an undecoded event for a known order by reconciling
    from the broker, so no fill facts are read or booked here.
    """
    order = _field(raw, "order", raw)
    client_order_id = str(_field(order, "client_order_id", "") or "")
    if not client_order_id:
        return None
    symbol = str(_field(order, "symbol", "") or "")
    return TradeUpdate(
        event=UNDECODED_TRADE_EVENT,
        client_order_id=client_order_id,
        broker_order_id=str(_field(order, "id", "") or ""),
        symbol=symbol,
        market=Market.CRYPTO if "/" in symbol else Market.EQUITY,
        filled_qty=0.0,
        average_fill_price=None,
        timestamp=datetime.now(UTC),
    )


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = ["UNDECODED_TRADE_EVENT", "AlpacaTradingUpdateStream", "TradeUpdate"]
