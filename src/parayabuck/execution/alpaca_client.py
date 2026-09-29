from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order
from pydantic import TypeAdapter

from parayabuck.core.models import Market
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import normalize_symbol
from parayabuck.utils.http import patch_session_timeout
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca_client")

_DEFAULT_TIMEOUT = 15.0
_MAX_ORDERS_PAGE_SIZE = 500
_ORDER_LIST_ADAPTER: TypeAdapter[list[Order]] = TypeAdapter(list[Order])


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
        self._asset_cache: dict[str, Any] = {}
        self._closed: bool = False
        session = getattr(self._client, "_session", None)
        if session is not None:
            try:
                patch_session_timeout(session, timeout=_DEFAULT_TIMEOUT)
            except Exception:
                log.warning("Failed to patch Alpaca session timeout", exc_info=True)

    @property
    def client(self) -> Any:
        return self._client

    @client.setter
    def client(self, value: Any) -> None:
        self._client = value

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        """Close the Alpaca REST session pool; idempotent and safe for fakes."""
        session = getattr(self._client, "_session", None)
        if session is None:
            self._closed = True
            return
        try:
            session.close()
        except Exception as exc:
            log.warning("Alpaca session close warning: %s", exc)
        finally:
            self._closed = True

    # -- thin TradingClient wrappers -----------------------------------

    def get_account(self) -> Any:
        return self._client.get_account()

    def get_all_positions(self) -> Any:
        return self._client.get_all_positions()

    def get_open_position(self, symbol: str) -> Any:
        # Alpaca expects the asset-lookup symbol (no slash for crypto).
        lookup = normalize_symbol(symbol).asset_lookup_symbol
        return self._client.get_open_position(lookup)

    def get_asset(self, symbol: str) -> Any:
        return self._asset(symbol)

    def get_orders(self, request: Any = None) -> Any:
        """List orders, exhausting unbounded OPEN queries before returning."""
        request_fields = request.to_request_fields() if request is not None else {}
        status = request_fields.get("status")
        status = getattr(status, "value", status)
        normalized_status = str(status).lower().split(".")[-1] if status is not None else "open"
        if normalized_status == "open" and request_fields.get("limit") is None:
            return self.get_open_orders(request)
        return self._client.get_orders(request)

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
            page = _ORDER_LIST_ADAPTER.validate_python(page_payload)
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

    def get_order_by_id(self, order_id: str) -> Any:
        return self._client.get_order_by_id(order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        getter = getattr(self._client, "get_order_by_client_id", None)
        if getter is None:
            return None
        return getter(client_order_id)

    def submit_order(self, request: Any) -> Any:
        return self._client.submit_order(request)

    def cancel_order_by_id(self, order_id: str) -> Any:
        canceller = getattr(self._client, "cancel_order_by_id", None)
        if canceller is not None:
            return canceller(order_id)
        # Fallback alias used by some fakes
        alt = getattr(self._client, "cancel_order", None)
        if alt is not None:
            return alt(order_id)
        raise AttributeError("underlying client has no cancel_order_by_id")

    def request(self, method: str, path: str, data: dict[str, object] | None = None) -> Any:
        """Raw ``_request`` wrapper for Fill activity pagination (FILL).

        Mirrors ``TradingClient._request(method, path, data=...)``; returns
        whatever the underlying client returns (typically a list payload).
        """
        req = getattr(self._client, "_request", None)
        if req is None:
            raise AttributeError("underlying client has no _request")
        if data is not None:
            return req(method, path, data=data)
        return req(method, path)

    # -- normalization helpers (kept at SDK boundary) -------------------

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return str(self._field(value, name, "")).lower().split(".")[-1]

    def _asset(self, symbol: str) -> Any:
        identity = normalize_symbol(symbol)
        cache_key = identity.canonical
        asset = (
            self._asset_cache.get(cache_key)
            or self._asset_cache.get(symbol)
            or self._asset_cache.get(identity.asset_lookup_symbol)
        )
        if asset is None:
            asset = self._client.get_asset(identity.asset_lookup_symbol)
            self._asset_cache[cache_key] = asset
        return asset

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        increment = getattr(self._asset(symbol), "price_increment", None)
        if increment is not None:
            return Decimal(str(increment))
        return Decimal("0.01") if reference_price >= 1 else Decimal("0.0001")

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
        rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        return float(rounded)


__all__ = ["AlpacaClient"]
