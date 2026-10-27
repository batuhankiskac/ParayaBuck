"""Order submission and normalization — safety-sensitive.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- ``PortfolioOrderRequest`` → validated broker request → ``submit`` →
  ``broker_order_id`` or :class:`AmbiguousOrderState`.
- Equity-only guard, quantity flooring to ``quantity_increment``; the local
  tradable / minimum-size / minimum-notional / open-order guards raise
  :class:`DefiniteOrderRejection` (nothing reached the broker); ``DAY``
  ``MarketOrderRequest``, ``client_order_id`` as recovery key.
- ``_submit_with_recovery`` delegates to the broker facade, which owns the
  single recovery decision: on submit raise, try ``get_order_by_client_id``
  → terminal with a fill recovered, terminal or 404 definite rejection
  (only when the submit error is a broker refusal) or re-raise, working
  recover, lookup fail →
  :class:`AmbiguousOrderState` (no blind resubmit).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from parayabuck.core.models import Market
from parayabuck.execution.alerting import emit_execution_alert as _emit_alert
from parayabuck.execution.alpaca_models import AmbiguousOrderState, DefiniteOrderRejection
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    floor_increment_decimal,
)
from parayabuck.logger import get_logger

log = get_logger("parayabuck.execution.alpaca_orders")

__all__ = ["OrderService"]


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
        if order.market != Market.EQUITY:
            raise ValueError(f"Portfolio orders are equity-only; refusing {order.market.value} order {order.symbol}")
        # Local pre-submit guards: nothing reaches the broker, so a refusal is
        # a KNOWN outcome. The runtime books the row ``rejected``, emits
        # ``order_rejected`` and re-plans instead of halting.
        # Any failure of these pre-send reads (not only a ValueError) is a
        # known not-sent outcome: the POST has not been made.
        try:
            rules = self.asset_trading_rules(order.symbol, order.market)
        except Exception as exc:
            raise DefiniteOrderRejection(f"Portfolio order {order.client_order_id} not submitted: {exc}") from exc
        quantity = Decimal(str(order.quantity))
        increment = Decimal(str(rules.quantity_increment))
        quantity = floor_increment_decimal(quantity, increment)
        if quantity < Decimal(str(rules.minimum_order_size)):
            raise DefiniteOrderRejection(
                f"Portfolio order {order.client_order_id} not submitted: below minimum size for {order.symbol}"
            )
        if float(quantity) * order.reference_price < rules.minimum_order_notional:
            raise DefiniteOrderRejection(
                f"Portfolio order {order.client_order_id} not submitted: below minimum notional for {order.symbol}"
            )
        try:
            has_open_order = self._has_open_order(order.symbol)
        except Exception as exc:
            # The open-order read is a GET before the POST: a failure here
            # proves nothing was sent, so it must not be booked as ambiguous.
            raise DefiniteOrderRejection(
                f"Portfolio order {order.client_order_id} not submitted: open orders could not be read: {exc}"
            ) from exc
        if has_open_order:
            raise DefiniteOrderRejection(
                f"Portfolio order {order.client_order_id} not submitted: an open order already exists for "
                f"{order.symbol}"
            )
        request = MarketOrderRequest(
            symbol=order.symbol,
            qty=float(quantity),
            side=(OrderSide.BUY if order.side == PortfolioOrderSide.BUY else OrderSide.SELL),
            time_in_force=TimeInForce.DAY,
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
