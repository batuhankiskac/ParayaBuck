"""Order submission and normalization — safety-sensitive.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- ``PortfolioOrderRequest`` → validated broker request → ``submit`` →
  ``broker_order_id`` or :class:`AmbiguousOrderState`.
- Quantity flooring to ``quantity_increment``, notional guard,
  open-order guard (``_has_open_order``), ``MarketOrderRequest``
  (``GTC`` crypto else ``DAY``), ``client_order_id`` as recovery key.
- ``_submit_with_recovery`` delegates to the broker facade, which owns the
  single recovery decision: on submit raise, try ``get_order_by_client_id``
  → terminal re-raise, working recover, 404 re-raise, lookup fail →
  :class:`AmbiguousOrderState` (no blind resubmit).
"""

from __future__ import annotations

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

    def _has_open_order(self, symbol: str) -> bool:
        return bool(self._broker._has_open_order(symbol))

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return self._broker.asset_trading_rules(symbol, market)  # type: ignore[no-any-return]

    # -- recovery --------------------------------------------------------

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        # One safety-critical recovery decision for every submit path; the
        # broker facade owns the definite-rejection short-circuit and the
        # 404/terminal re-raise rules (same delegation as the other services).
        return self._broker._submit_with_recovery(request, client_order_id)

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
