"""Scriptable Fake Alpaca TradingClient for deterministic broker tests.

Fake maintains minimal realistic state:
- orders by broker ID
- orders by client ID
- positions
- activities

Allows scripting per-call behavior for fault injection without ad-hoc SimpleNamespace mocks.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from alpaca.trading.models import Order


@dataclass
class _FakeOrder:
    id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str = "accepted"
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    created_at: float = field(default_factory=time.time)
    # For protective stops: parent info
    order_class: str = "simple"
    legs: list[Any] = field(default_factory=list)


class FakeTradingClient:
    """Deterministic fake for AlpacaBroker tests.

    Scripting hooks:
    - `script_submit`: Callable[[request], SimpleNamespace | Exception] or None for default behavior
    - `script_get_order_by_client_id`: Callable[[client_id], SimpleNamespace | None | Exception]
    - `script_get_order_by_id`: Callable[[order_id], SimpleNamespace | None | Exception]
    - `script_cancel`: Callable[[order_id], SimpleNamespace | Exception]
    - `script_get_account`: Callable[[], SimpleNamespace | Exception]
    - `script_get_all_positions`: Callable[[], list | Exception]
    - `script_get_open_position`: Callable[[symbol], SimpleNamespace | Exception]
    - `script_get_asset`: Callable[[symbol], SimpleNamespace | Exception]
    - `script_get_orders`: Callable[[*args, **kwargs], list | Exception]
    - `script_activities`: Callable[[], list | Exception] for fill activities

    Counters:
    - `submitted_market_orders`, `submitted_stops`, `cancels`, `lookups` track economic actions.
    """

    def __init__(self) -> None:
        self.orders_by_id: dict[str, _FakeOrder] = {}
        self.orders_by_client_id: dict[str, _FakeOrder] = {}
        self.positions: list[SimpleNamespace] = []
        self.activities: list[dict[str, Any]] = []
        self.open_orders: list[SimpleNamespace] = []
        self._account_override: SimpleNamespace | None = None
        self._asset_cache: dict[str, SimpleNamespace] = {}

        # Script hooks
        self.script_submit: Callable[[Any], Any | Exception] | None = None
        self.script_get_order_by_client_id: Callable[[str], Any | Exception | None] | None = None
        self.script_get_order_by_id: Callable[[str], Any | Exception | None] | None = None
        self.script_cancel: Callable[[str], Any | Exception] | None = None
        self.script_get_account: Callable[[], Any | Exception] | None = None
        self.script_get_all_positions: Callable[[], Any | Exception] | None = None
        self.script_get_open_position: Callable[[str], Any | Exception] | None = None
        self.script_get_asset: Callable[[str], Any | Exception] | None = None
        self.script_get_orders: Callable[..., Any] | None = None
        self.script_activities: Callable[[], Any] | None = None

        # Counters for safety assertions
        self.submitted_market_orders = 0
        self.submitted_stops = 0
        self.cancels = 0
        self.lookups = 0

        # For submission failure simulation: if set, submit will create order then raise
        self.submit_then_raise: Exception | None = None
        self.lookup_then_raise: Exception | None = None

        # Session for close() compatibility
        self._session: Any = None

    # Helpers
    def _make_order(self, request: Any) -> SimpleNamespace:
        client_id = getattr(request, "client_order_id", f"parayabuck-{uuid4().hex[:8]}")
        symbol = getattr(request, "symbol", "IWM")
        side = str(getattr(request, "side", "buy"))
        qty = float(getattr(request, "qty", 1.0))
        broker_id = f"broker-{client_id}"
        if "stop" in str(type(request).__name__).lower() or "Stop" in str(type(request).__name__):
            self.submitted_stops += 1
        else:
            self.submitted_market_orders += 1
        order = _FakeOrder(id=broker_id, client_order_id=client_id, symbol=symbol, side=side, qty=qty)
        self.orders_by_id[broker_id] = order
        self.orders_by_client_id[client_id] = order
        # Return Alpaca-like object with id attribute
        return SimpleNamespace(
            id=broker_id,
            client_order_id=client_id,
            symbol=symbol,
            side=side,
            qty=str(qty),
            status=order.status,
            filled_qty=str(order.filled_qty),
            filled_avg_price=str(order.filled_avg_price) if order.filled_avg_price else None,
        )

    # Broker API surface used by AlpacaBroker
    def get_account(self) -> Any:
        if self.script_get_account is not None:
            result = self.script_get_account()
            if isinstance(result, Exception):
                raise result
            return result
        if self._account_override is not None:
            return self._account_override
        return SimpleNamespace(
            id="fake-acct-1234",
            account_number="fake-acct-1234",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            last_equity="100000",
            cash="90000",
            buying_power="100000",
            non_marginable_buying_power="100000",
            initial_margin="0",
            last_maintenance_margin="0",
            daytrade_count=0,
        )

    def set_account(self, *, blocked: bool = False, equity: str = "100000") -> None:
        self._account_override = SimpleNamespace(
            id="fake-acct-1234",
            account_number="fake-acct-1234",
            account_blocked=blocked,
            trading_blocked=blocked,
            trade_suspended_by_user=False,
            equity=equity,
            last_equity=equity,
            cash="90000",
            buying_power="100000",
            non_marginable_buying_power="100000",
            initial_margin="0",
            last_maintenance_margin="0",
            daytrade_count=0,
        )

    def get_all_positions(self) -> Any:
        if self.script_get_all_positions is not None:
            result = self.script_get_all_positions()
            if isinstance(result, Exception):
                raise result
            return result
        return self.positions

    def get_open_position(self, symbol: str) -> Any:
        if self.script_get_open_position is not None:
            result = self.script_get_open_position(symbol)
            if isinstance(result, Exception):
                raise result
            return result
        # Normalize symbol for lookup (BTC/USD -> BTCUSD)
        wanted = symbol.replace("/", "")
        for pos in self.positions:
            if pos.symbol.replace("/", "") == wanted or pos.symbol == symbol:
                return pos
        raise Exception(f"no position for {symbol}")

    def get_asset(self, symbol: str) -> Any:
        if self.script_get_asset is not None:
            result = self.script_get_asset(symbol)
            if isinstance(result, Exception):
                raise result
            return result
        if symbol in self._asset_cache:
            return self._asset_cache[symbol]
        # Default tradable asset
        asset = SimpleNamespace(
            symbol=symbol,
            tradable=True,
            fractionable=True,
            marginable=True,
            min_order_size="0.0001",
            min_trade_increment="0.0001",
            price_increment="0.01",
        )
        self._asset_cache[symbol] = asset
        return asset

    def set_asset(self, symbol: str, *, tradable: bool = True, min_order_size: str = "0.0001") -> None:
        self._asset_cache[symbol] = SimpleNamespace(
            symbol=symbol,
            tradable=tradable,
            fractionable=True,
            marginable=True,
            min_order_size=min_order_size,
            min_trade_increment="0.0001",
            price_increment="0.01",
        )

    def get_orders(self, *args: Any, **kwargs: Any) -> Any:
        if self.script_get_orders is not None:
            result = self.script_get_orders(*args, **kwargs)
            if isinstance(result, Exception):
                raise result
            return result
        # Filter by symbols if provided (for _has_open_order)
        symbols = kwargs.get("symbols") or (args[0].symbols if args and hasattr(args[0], "symbols") else None)
        if symbols:
            wanted = {s.replace("/", "") for s in symbols}
            return [o for o in self.open_orders if o.symbol.replace("/", "") in wanted]
        return self.open_orders

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[Order]:
        """Authenticated GET surface used by the production order pager."""
        if path != "/orders":
            raise Exception(f"unsupported GET {path}")
        query = params or {}
        orders: list[Order] = []
        symbols = query.get("symbols")
        wanted = {value.replace("/", "") for value in symbols.split(",")} if isinstance(symbols, str) else set()
        for index, raw in enumerate(self.open_orders):
            symbol = str(getattr(raw, "symbol", ""))
            if wanted and symbol.replace("/", "") not in wanted:
                continue
            fields = dict(vars(raw)) if hasattr(raw, "__dict__") else {}
            fields["id"] = str(getattr(raw, "id", "") or f"fake-open-order-{index}")
            orders.append(Order.model_construct(**fields))

        direction = str(query.get("direction", "desc"))
        if direction == "asc":
            orders.reverse()
        cursor_key = "before_order_id" if direction == "desc" else "after_order_id"
        cursor = query.get(cursor_key)
        if cursor is not None:
            cursor_index = next((i for i, order in enumerate(orders) if str(order.id) == cursor), None)
            if cursor_index is None:
                return []
            orders = orders[cursor_index + 1 :]
        return orders[: int(query.get("limit", 50))]

    def get_order_by_id(self, request: Any) -> Any:
        self.lookups += 1
        order_id = getattr(request, "order_id", str(request)) if not isinstance(request, str) else request
        # Handle GetOrderByIdRequest
        if hasattr(request, "order_id"):
            order_id = request.order_id
        if self.script_get_order_by_id is not None:
            result = self.script_get_order_by_id(order_id)
            if isinstance(result, Exception):
                raise result
            return result
        o = self.orders_by_id.get(order_id)
        if o is None:
            return None
        return SimpleNamespace(
            id=o.id, client_order_id=o.client_order_id, symbol=o.symbol, side=o.side, qty=str(o.qty), status=o.status
        )

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        self.lookups += 1
        if self.script_get_order_by_client_id is not None:
            result = self.script_get_order_by_client_id(client_order_id)
            if isinstance(result, Exception):
                raise result
            return result
        if self.lookup_then_raise is not None:
            raise self.lookup_then_raise
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            # Simulate 404 by returning None (caller treats as not found)
            return None
        # Return SimpleNamespace with same shape as broker
        return SimpleNamespace(
            id=order.id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            qty=str(order.qty),
            status=order.status,
            filled_qty=str(order.filled_qty),
            filled_avg_price=str(order.filled_avg_price) if order.filled_avg_price else None,
        )

    def submit_order(self, request: Any) -> Any:
        if self.script_submit is not None:
            result = self.script_submit(request)
            if isinstance(result, Exception):
                raise result
            return result
        # Default: create order then maybe raise to simulate network drop after accept
        created = self._make_order(request)
        if self.submit_then_raise is not None:
            raise self.submit_then_raise
        return created

    def cancel_order_by_id(self, order_id: str) -> Any:
        self.cancels += 1
        if self.script_cancel is not None:
            result = self.script_cancel(order_id)
            if isinstance(result, Exception):
                raise result
            return result
        order = self.orders_by_id.get(order_id)
        if order is not None:
            order.status = "canceled"
            return SimpleNamespace(id=order_id, status="canceled")
        # Simulate 404
        raise Exception(f"order {order_id} not found")

    # Activities for fill recovery (GET /account/activities/FILL)
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if "activities" in path and "FILL" in path:
            if self.script_activities is not None:
                result = self.script_activities()
                if isinstance(result, Exception):
                    raise result
                activities = result
            else:
                activities = self.activities
            if not isinstance(activities, list):
                return activities
            params = kwargs.get("data") or {}
            cursor = params.get("page_token")
            start = next((i + 1 for i, item in enumerate(activities) if item.get("id") == cursor), 0) if cursor else 0
            return activities[start : start + int(params.get("page_size", 100))]
        raise Exception(f"unsupported _request {method} {path}")

    # Helper to inject fill activity
    def add_fill_activity(
        self,
        *,
        symbol: str,
        qty: float,
        price: float,
        side: str = "buy",
        order_id: str = "broker-123",
        activity_id: str | None = None,
    ) -> None:
        activity_id = activity_id or f"activity-{uuid4().hex[:8]}"
        self.activities.append(
            {
                "id": activity_id,
                "activity_type": "FILL",
                "symbol": symbol,
                "qty": str(qty),
                "price": str(price),
                "side": side,
                "order_id": order_id,
                "transaction_time": "2026-08-29T00:00:00Z",
                "commission": "0.0",
            }
        )

    def set_position(self, symbol: str, qty: float, market: str = "equity", qty_available: float | None = None) -> None:
        pos = SimpleNamespace(
            symbol=symbol,
            qty=str(qty),
            qty_available=str(qty_available if qty_available is not None else qty),
            avg_entry_price="100",
            current_price="100",
            market_value=str(qty * 100),
            cost_basis=str(qty * 100),
            unrealized_pl="0",
            unrealized_intraday_pl="0",
            asset_class="us_equity" if market == "equity" else "crypto",
        )
        # Replace existing
        self.positions = [p for p in self.positions if p.symbol != symbol]
        self.positions.append(pos)

    def clear(self) -> None:
        self.orders_by_id.clear()
        self.orders_by_client_id.clear()
        self.positions.clear()
        self.open_orders.clear()
        self.activities.clear()
        self.submitted_market_orders = 0
        self.submitted_stops = 0
        self.cancels = 0
        self.lookups = 0
