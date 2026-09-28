from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from parayabuck.core.models import Market, OrderRole
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.utils.objects import field_value as _field


@dataclass(frozen=True)
class TradeUpdate:
    event: str
    client_order_id: str
    broker_order_id: str
    parent_order_id: str | None
    role: OrderRole
    symbol: str
    market: Market
    requested_qty: float
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
        received_at = datetime.now(UTC)
        order = _field(raw, "order", raw)
        asset_class = str(_field(order, "asset_class", "")).lower()
        raw_symbol = str(_field(order, "symbol", ""))
        market = Market.CRYPTO if "crypto" in asset_class or "/" in raw_symbol else Market.EQUITY
        timestamp = _field(raw, "timestamp") or _field(order, "updated_at") or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        average = _field(order, "filled_avg_price")
        parent_order_id = _field(order, "parent_order_id")
        order_type = str(_field(order, "type", "")).lower().split(".")[-1]
        order_class = str(_field(order, "order_class", "")).lower().split(".")[-1]
        stop_price = _field(order, "stop_price")
        client_order_id = str(_field(order, "client_order_id", ""))
        if parent_order_id:
            role = (
                OrderRole.PROTECTIVE_STOP
                if stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                else OrderRole.TAKE_PROFIT
            )
        elif order_class == "bracket" and order_type not in {
            "limit",
            "stop",
            "stop_limit",
            "trailing_stop",
        }:
            # The parent of an Alpaca bracket has no parent_order_id. Its
            # order_class alone must not make an entry look like a target leg.
            role = OrderRole.ENTRY
        elif order_class == "bracket":
            # Some stream payloads omit parent_order_id on bracket children.
            role = (
                OrderRole.PROTECTIVE_STOP
                if stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"}
                else OrderRole.TAKE_PROFIT
            )
        elif "-target-exit" in client_order_id:
            role = OrderRole.TAKE_PROFIT
        elif "-emergency-exit" in client_order_id or client_order_id.startswith("parayabuck-exit-"):
            role = OrderRole.EMERGENCY_EXIT
        elif "-stop" in client_order_id or client_order_id.startswith("parayabuck-stop-"):
            role = OrderRole.PROTECTIVE_STOP
        elif client_order_id.startswith("parayabuck-"):
            role = OrderRole.ENTRY
        else:
            role = OrderRole.MANUAL_EXIT
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
        update = TradeUpdate(
            event=str(_field(raw, "event", "")).lower().split(".")[-1],
            client_order_id=client_order_id,
            broker_order_id=str(_field(order, "id", "")),
            parent_order_id=str(parent_order_id) if parent_order_id else None,
            role=role,
            symbol=canonical_symbol(raw_symbol, market),
            market=market,
            requested_qty=float(_field(order, "qty", 0) or 0),
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
        await self._handler(update)

    def run(self) -> None:
        self._stream.run()

    def stop(self) -> None:
        # alpaca-py creates the event loop only inside run(); its stop()
        # dereferences that loop, so a never-run stream has nothing to stop.
        if getattr(self._stream, "_loop", ...) is None:
            return
        self._stream.stop()


__all__ = ["AlpacaTradingUpdateStream", "TradeUpdate"]
