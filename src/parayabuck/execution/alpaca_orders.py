"""Order submission and normalization — safety-sensitive.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- ``PortfolioOrderRequest`` → validated broker request → ``submit`` →
  ``broker_order_id`` or :class:`AmbiguousOrderState`.
- Quantity flooring to ``quantity_increment``, notional guard,
  open-order guard (``_has_open_order``), ``MarketOrderRequest``
  (``GTC`` crypto else ``DAY``), ``client_order_id`` as recovery key.
- ``_submit_with_recovery``: on submit raise, try ``get_order_by_client_id``
  → terminal re-raise, working recover, 404 re-raise, lookup fail →
  :class:`AmbiguousOrderState` (no blind resubmit).
- ``order_state`` normalization to :class:`OrderState` (``filled``,
  ``canceled``, ``rejected``, ``expired``, ``unknown`` / ambiguous).
- ``filled_qty`` / ``avg_price`` / ``fees`` centralization.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca_models import AmbiguousOrderState
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    floor_increment_decimal,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol

log = get_logger("parayabuck.execution.alpaca_orders")

__all__ = ["OrderService"]


def _emit_alert(kind: str, *, message: str, **fields: object) -> None:
    try:
        from parayabuck.monitoring import alerts
    except ImportError:
        log.debug("alerts module unavailable; skipping %s alert", kind)
        return
    try:
        alerts.emit(kind, message=message, **fields)
    except Exception:
        log.warning("Alert emission failed for kind=%s (order flow unaffected)", kind, exc_info=True)


class OrderService:
    """Portfolio order gateway — production implementation."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    # -- helpers delegated to broker/client ------------------------------

    def _field(self, value: Any, name: str, default: Any = None) -> Any:
        return self._broker._field(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._broker._normalized_field(value, name)  # type: ignore[no-any-return]

    def _fetch_broker_order(self, client_order_id: str, broker_order_id: str) -> Any | None:
        return self._broker._fetch_broker_order(client_order_id, broker_order_id)

    def _has_open_order(self, symbol: str) -> bool:
        return bool(self._broker._has_open_order(symbol))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return self._broker.asset_trading_rules(symbol, market)  # type: ignore[no-any-return]

    # -- fill extraction -------------------------------------------------

    def _fill_from_broker_order(
        self, order: Any, client_order_id: str, broker_order_id: str
    ) -> tuple[str, str, float, float, datetime, str, float | None] | None:
        filled_qty = float(self._field(order, "filled_qty", 0) or self._field(order, "cumulative_qty", 0) or 0)
        avg_price = float(self._field(order, "filled_avg_price", 0) or 0)
        if filled_qty <= 0 or avg_price <= 0:
            return None
        filled_at_raw = self._field(
            order, "filled_at", self._field(order, "submitted_at", datetime.now(UTC).isoformat())
        )
        occurred_at = datetime.fromisoformat(str(filled_at_raw))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return (
            client_order_id,
            str(self._field(order, "id", "") or broker_order_id),
            filled_qty,
            avg_price,
            occurred_at,
            f"{client_order_id}:recovered:{filled_qty}",
            # The order lookup does not establish a fee.  Treating absence as
            # a zero fee would corrupt execution-quality measurements.
            None,
        )

    def resolve_portfolio_submission(
        self, client_order_id: str, broker_order_id: str = ""
    ) -> tuple[str, str] | str | None:
        get_by_client = getattr(self._broker._client, "get_order_by_client_id", None)
        if get_by_client is None:
            return None
        broker_order = self._fetch_broker_order(client_order_id, broker_order_id)
        if broker_order is None:
            return "canceled"
        status = str(self._normalized_field(broker_order, "status") or "").lower()
        if status == "filled":
            recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
            if recovered is not None:
                if fill_recovered := getattr(self._broker, "portfolio_fill_recovered", None):
                    fill_recovered(*recovered)
                return "filled"
            return "submitted"
        if status in {"canceled", "cancelled", "expired", "rejected", "done_for_day"}:
            return status
        return "submitted"

    # -- recovery --------------------------------------------------------

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        broker = self._broker
        client = broker._client
        try:
            return client.submit_order(request)
        except Exception as exc:
            # Attempt recovery via client_order_id lookup
            get_by_client = getattr(client, "get_order_by_client_id", None)
            if get_by_client is None:
                raise AmbiguousOrderState(
                    f"Order submission failed and recovery unavailable for {client_order_id}: {exc}"
                ) from exc
            try:
                order = get_by_client(client_order_id)
            except Exception as lookup_exc:
                if getattr(lookup_exc, "status_code", None) == 404:
                    raise
                raise AmbiguousOrderState(
                    f"Order submission result is ambiguous for {client_order_id}: lookup failed"
                ) from lookup_exc
            if order is None:
                raise
            status = str(self._normalized_field(order, "status") or "").lower()
            if status in {"canceled", "cancelled", "expired", "rejected", "done_for_day"}:
                raise
            # Working or filled — recover
            _emit_alert(
                "order_recovered",
                message=f"Order recovered via client_order_id lookup: {client_order_id}",
                client_order_id=client_order_id,
                status=status,
            )
            return order

    # -- public ----------------------------------------------------------

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        rules = self.asset_trading_rules(order.symbol, order.market)
        quantity = Decimal(str(order.quantity))
        increment = Decimal(str(rules.quantity_increment))
        quantity = floor_increment_decimal(quantity, increment)
        if quantity < Decimal(str(rules.minimum_order_size)):
            _emit_alert(
                "order_rejected",
                message=(f"Portfolio order below minimum size: {order.symbol} client_order_id={order.client_order_id}"),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="below_minimum_size",
            )
            raise ValueError(f"Portfolio order is below minimum size: {order.symbol}")
        if float(quantity) * order.reference_price < rules.minimum_order_notional:
            _emit_alert(
                "order_rejected",
                message=(
                    f"Portfolio order below minimum notional: {order.symbol} client_order_id={order.client_order_id}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="below_minimum_notional",
            )
            raise ValueError(f"Portfolio order is below minimum notional: {order.symbol}")
        if self._has_open_order(order.symbol):
            _emit_alert(
                "order_rejected",
                message=(
                    f"Portfolio order rejected: open order already exists for "
                    f"{order.symbol} client_order_id={order.client_order_id}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="open_order_exists",
            )
            raise ValueError(f"Open order already exists for {order.symbol}")
        request = MarketOrderRequest(
            symbol=order.symbol,
            qty=float(quantity),
            side=(OrderSide.BUY if order.side == PortfolioOrderSide.BUY else OrderSide.SELL),
            time_in_force=(TimeInForce.GTC if order.market == Market.CRYPTO else TimeInForce.DAY),
            client_order_id=order.client_order_id,
        )
        result = self._submit_with_recovery(request, order.client_order_id)
        broker_order_id = str(self._field(result, "id", "") or "")
        if not broker_order_id:
            _emit_alert(
                "ambiguous_order",
                message=(
                    f"Alpaca order response has no id for client_order_id={order.client_order_id} symbol={order.symbol}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
            )
            raise AmbiguousOrderState(f"Alpaca order response has no id for {order.client_order_id}")
        return broker_order_id

    def portfolio_open_order_symbols(self) -> set[str]:
        provider = getattr(self._broker, "portfolio_open_orders", None)
        if provider is None:
            return set()
        orders = provider() or ()
        return {canonical_symbol(str(getattr(order, "symbol", ""))) for order in orders if getattr(order, "symbol", "")}
