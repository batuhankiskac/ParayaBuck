from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order
from pydantic import TypeAdapter, ValidationError

from parayabuck.logger import get_logger
from parayabuck.markets.symbols import normalize_symbol
from parayabuck.utils.http import patch_session_timeout
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca_client")

_DEFAULT_TIMEOUT = 15.0
_MAX_ORDERS_PAGE_SIZE = 500
# Asset metadata is re-read from Alpaca at least once a day.
_ASSET_CACHE_TTL_SECONDS = 24 * 60 * 60.0
# A non-tradable read is re-checked within a minute: a halted or refused symbol
# that the broker re-enables must not stay blocked (its exit held) for a day.
_NON_TRADABLE_ASSET_CACHE_TTL_SECONDS = 60.0
_ORDER_ADAPTER: TypeAdapter[Order] = TypeAdapter(Order)
# Broker timestamps carry nanoseconds, which ``datetime.fromisoformat`` rejects.
_FRACTION_BEYOND_MICROSECONDS = re.compile(r"(\.\d{6})\d+")


@dataclass(frozen=True)
class BrokerClock:
    """Broker's own view of the market clock (Alpaca ``get_clock``).

    All datetimes are timezone-aware. ``timestamp`` is the broker's current
    time; ``next_open`` / ``next_close`` are the next session boundaries.
    """

    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


def parse_broker_timestamp(value: Any) -> datetime:
    """An Alpaca timestamp as an aware datetime; a naive value is taken as UTC."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _FRACTION_BEYOND_MICROSECONDS.sub(r"\1", str(value).strip().replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _aware_datetime(value: Any, name: str) -> datetime:
    parsed = value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Broker clock {name} is not a timezone-aware datetime")
    return parsed


class AlpacaClient:
    """Thin Alpaca SDK adapter isolating REST lifecycle and DTO normalization.

    Owns :class:`alpaca.trading.client.TradingClient`, bounds every REST
    call with a 15s socket timeout via :func:`patch_session_timeout`, and
    keeps raw Alpaca DTO adaptation (``field_value`` / ``_field`` /
    ``_normalized_field``) at the SDK boundary.

    No brokerage abstraction is introduced — this is Alpaca-specific and
    intentionally small (<400 lines). ``close()`` is idempotent and safe
    for fakes that have no ``_session``.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        *,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client: Any = client
        else:
            self._client = TradingClient(api_key, secret_key, paper=paper)
        self._asset_cache: dict[str, tuple[Any, float]] = {}
        self._clock: Callable[[], float] = time.monotonic
        session = getattr(self._client, "_session", None)
        if session is not None:
            try:
                patch_session_timeout(session, timeout=_DEFAULT_TIMEOUT)
            except Exception:
                log.warning("Failed to patch Alpaca session timeout", exc_info=True)

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        """Close the Alpaca REST session pool; idempotent and safe for fakes."""
        session = getattr(self._client, "_session", None)
        if session is None:
            return
        try:
            session.close()
        except Exception as exc:
            log.warning("Alpaca session close warning: %s", exc)

    # -- market clock ----------------------------------------------------

    def clock(self) -> BrokerClock:
        """Read the broker market clock (one REST call, no side effects)."""
        raw: Any = self._client.get_clock()
        return BrokerClock(
            timestamp=_aware_datetime(self._field(raw, "timestamp"), "timestamp"),
            is_open=bool(self._field(raw, "is_open", False)),
            next_open=_aware_datetime(self._field(raw, "next_open"), "next_open"),
            next_close=_aware_datetime(self._field(raw, "next_close"), "next_close"),
        )

    # -- exhaustive OPEN-order query -------------------------------------

    def get_open_orders(self, request: Any = None) -> list[Order]:
        """Return the complete OPEN-order collection using order-ID cursors.

        ``GetOrdersRequest`` in the pinned alpaca-py version cannot represent
        Alpaca's order-ID cursors. This named unbounded operation uses the
        authenticated lower-level GET method so broker-wide and symbol-filtered
        working-order reads share one completeness guarantee. An explicit
        request limit is rejected; callers needing all working orders must not
        accidentally turn it into a one-page query.
        """
        request_fields = request.to_request_fields() if request is not None else {}
        status = request_fields.get("status")
        status = getattr(status, "value", status)
        normalized_status = str(status).lower().split(".")[-1] if status is not None else "open"
        if normalized_status != "open":
            raise ValueError("get_open_orders only accepts OPEN order requests")
        if request_fields.get("limit") is not None:
            raise ValueError("get_open_orders is exhaustive and does not accept an explicit limit")

        query = {key: getattr(value, "value", value) for key, value in request_fields.items() if value is not None}
        query["status"] = "open"
        symbols = query.get("symbols")
        if isinstance(symbols, list):
            query["symbols"] = ",".join(str(symbol) for symbol in symbols)

        direction = str(getattr(query.get("direction", "desc"), "value", query.get("direction", "desc")))
        direction = direction.lower().split(".")[-1]
        if direction not in {"asc", "desc"}:
            raise ValueError(f"Unsupported Alpaca order direction: {direction}")
        query["direction"] = direction
        query["limit"] = _MAX_ORDERS_PAGE_SIZE
        cursor_parameter = "before_order_id" if direction == "desc" else "after_order_id"
        return self._get_all_open_orders(query, cursor_parameter)

    def _get_all_open_orders(self, query: dict[str, Any], cursor_parameter: str) -> list[Order]:
        """Fetch every OPEN page in stable API order or fail without a partial result."""
        get = getattr(self._client, "get", None)
        if get is None:
            raise RuntimeError("Alpaca client does not expose authenticated GET for order pagination")

        orders: list[Order] = []
        seen_order_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            page_query = dict(query)
            if cursor is not None:
                page_query[cursor_parameter] = cursor
            page_payload = get("/orders", page_query)
            page = [self._order_row(row) for row in page_payload]
            if len(page) > _MAX_ORDERS_PAGE_SIZE:
                raise RuntimeError("Alpaca returned more orders than the requested page size")

            previous_order_ids = seen_order_ids.copy()
            page_ids: list[str] = []
            new_orders = 0
            for order in page:
                raw_order_id = self._field(order, "id")
                order_id = str(raw_order_id) if raw_order_id is not None else ""
                if not order_id:
                    raise RuntimeError("Alpaca returned an order without an ID during pagination")
                page_ids.append(order_id)
                if order_id in seen_order_ids:
                    continue
                seen_order_ids.add(order_id)
                orders.append(order)
                new_orders += 1

            if len(page) < _MAX_ORDERS_PAGE_SIZE:
                return orders
            if "after" in query or "until" in query:
                raise RuntimeError("Cannot exhaust OPEN orders with timestamp filters and order-ID cursors together")
            if not page_ids or new_orders == 0:
                raise RuntimeError("Alpaca order pagination made no progress")

            next_cursor = page_ids[-1]
            if next_cursor == cursor or next_cursor in seen_cursors or next_cursor in previous_order_ids:
                raise RuntimeError("Alpaca order pagination cursor did not advance")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def order_by_client_id(self, client_order_id: str) -> Any:
        """One order by client id; see :meth:`_single_order`."""
        return self._single_order(
            lambda: self._client.get_order_by_client_id(client_order_id),
            "/orders:by_client_order_id",
            {"client_order_id": client_order_id},
        )

    def order_by_id(self, broker_order_id: str) -> Any:
        """One order by broker id; see :meth:`_single_order`."""
        return self._single_order(
            lambda: self._client.get_order_by_id(broker_order_id),
            f"/orders/{broker_order_id}",
            None,
        )

    def _single_order(self, read: Callable[[], Any], path: str, query: dict[str, Any] | None) -> Any:
        """Read one order, surviving a status the pinned SDK model rejects.

        The SDK parses the response into a strict model, so one unknown status
        fails every read of that order. On a validation error the same endpoint
        is read again as a raw row, which keeps the broker's own status.
        """
        try:
            return read()
        except ValidationError:
            log.warning("Alpaca order read %s did not match the SDK model; reading the raw row", path)
        return self._order_row(self._client.get(path, query))

    @staticmethod
    def _order_row(row: Any) -> Any:
        """An order row as the SDK model, or the raw row when the model rejects it.

        Alpaca can report a status or enum the pinned SDK does not know. The raw
        row keeps the broker's string, which reconciliation reports as an
        unrecognized status and leaves unchanged. Non-mapping rows (SDK objects)
        pass through untouched.
        """
        if not isinstance(row, dict):
            return row
        try:
            return _ORDER_ADAPTER.validate_python(row)
        except ValidationError:
            log.warning("Alpaca order %s has a value the SDK does not recognize; keeping the raw row", row.get("id"))
            return row

    # -- normalization helpers (kept at SDK boundary) -------------------

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return str(self._field(value, name, "")).lower().split(".")[-1]

    def _asset(self, symbol: str) -> Any:
        """Alpaca asset metadata, cached for at most ``_ASSET_CACHE_TTL_SECONDS``.

        ``tradable``/``fractionable``/increments can change (a halt, a
        delisting, a fractional-trading change), so an entry is re-read once
        it is a day old instead of being trusted for the life of the process.
        A non-tradable entry is only trusted for ``_NON_TRADABLE_ASSET_CACHE_TTL_SECONDS``.
        """
        identity = normalize_symbol(symbol)
        cache_key = identity.canonical
        now = self._clock()
        cached = self._asset_cache.get(cache_key)
        if cached is not None:
            tradable = bool(getattr(cached[0], "tradable", False))
            ttl = _ASSET_CACHE_TTL_SECONDS if tradable else _NON_TRADABLE_ASSET_CACHE_TTL_SECONDS
            if now - cached[1] < ttl:
                return cached[0]
        asset = self._client.get_asset(identity.asset_lookup_symbol)
        self._asset_cache[cache_key] = (asset, now)
        return asset


__all__ = ["AlpacaClient", "BrokerClock"]
