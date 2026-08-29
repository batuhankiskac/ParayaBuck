"""Protective-stop lifecycle — equity & bracket.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- Detect existing protection (batch ``get_orders`` once, fee-reserve
  tolerance, nested bracket via ``get_order_by_id(nested=True)``).
- Calculate ``required_qty`` (remaining effective, ``0.9975`` fee-reserve
  when ``qty_available == 0``).
- Under/over detection, cancel+confirm before replace, submit remainder
  only, re-protect after execution changes.
- Equity fractional ``DAY`` vs integral ``GTC``, crypto via separate
  module.
"""

from __future__ import annotations

import time
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Any
from uuid import uuid4

from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrderByIdRequest, GetOrdersRequest, StopLimitOrderRequest, StopOrderRequest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca_models import AmbiguousOrderState
from parayabuck.execution.portfolio import floor_increment_decimal
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, same_symbol

log = get_logger("parayabuck.execution.alpaca_protection")

__all__ = ["ProtectionService"]


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


class ProtectionService:
    """Protective order lifecycle — production implementation."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    # -- delegated helpers ------------------------------------------------

    def _field(self, value: Any, name: str, default: Any = None) -> Any:
        return self._broker._field(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._broker._normalized_field(value, name)  # type: ignore[no-any-return]

    def _asset(self, symbol: str) -> Any:
        return self._broker._asset(symbol)

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        return self._broker._price_step(symbol, market, reference_price)  # type: ignore[no-any-return]

    def _round_to_step(self, value: float, step: Decimal) -> float:
        return self._broker._round_to_step(value, step)  # type: ignore[no-any-return]

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        return self._broker._submit_with_recovery(request, client_order_id)

    def _available_qty_from_error(self, exc: Exception) -> float:
        return self._broker._available_qty_from_error(exc)  # type: ignore[no-any-return]

    def _round_qty_down(self, symbol: str, qty: float) -> float:
        return float(floor_increment_decimal(Decimal(str(qty)), self._qty_step(symbol)))

    def _qty_step(self, symbol: str) -> Decimal:
        increment = getattr(self._asset(symbol), "min_trade_increment", None)
        return Decimal(str(increment)) if increment is not None else Decimal("0.000000001")

    # -- protection detection --------------------------------------------

    @staticmethod
    def _protection_matches(protected: Decimal, required: Decimal) -> tuple[bool, str]:
        tolerance = max(Decimal("0.000000001"), required * Decimal("0.000000001"))
        mismatch = "missing" if protected == 0 else ("under" if protected < required - tolerance else "over")
        return abs(protected - required) <= tolerance, mismatch

    def _has_nested_bracket_protection(
        self,
        parent_order_ids: str | set[str],
        symbol: str,
        position_is_long: bool,
        required_qty: float,
    ) -> bool:
        broker = self._broker
        closing_side = "sell" if position_is_long else "buy"

        def protective_qty(order: Any) -> Decimal:
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                side == closing_side
                and status not in {"canceled", "expired", "filled", "rejected"}
                and (stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"})
            ):
                return max(
                    Decimal(0),
                    abs(Decimal(str(self._field(order, "qty", 0) or 0)))
                    - abs(Decimal(str(self._field(order, "filled_qty", 0) or 0))),
                )
            return sum(
                (protective_qty(leg) for leg in (self._field(order, "legs") or ())),
                start=Decimal(0),
            )

        ids = {parent_order_ids} if isinstance(parent_order_ids, str) else parent_order_ids
        protected = Decimal(0)
        for parent_order_id in ids:
            parent: Any = broker._client.get_order_by_id(
                parent_order_id,
                GetOrderByIdRequest(nested=True),
            )
            if same_symbol(str(self._field(parent, "symbol", symbol)), symbol):
                protected += protective_qty(parent)
        matches, broker._last_protection_mismatch = self._protection_matches(protected, Decimal(str(required_qty)))
        broker._last_protected_qty = float(protected)
        return matches

    def _open_protective_orders(self, symbol: str, position_is_long: bool, open_orders: Any = None) -> list[Any]:
        broker = self._broker
        if open_orders is None:
            orders: Any = broker._client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
            )
        else:
            orders = open_orders
        if isinstance(orders, dict):
            log.warning("Unexpected get_orders payload shape for %s; treating as no open orders", symbol)
            return []
        closing_side = "sell" if position_is_long else "buy"
        canonical = canonical_symbol(symbol)
        ledger = getattr(broker, "ledger", None)
        account_id = getattr(broker, "_account_id", "") or "unknown"
        legacy_external_management = ledger is None and bool(getattr(broker, "manage_external_positions", True))
        managed_order_ids = ledger.managed_order_ids(account_id, canonical) if ledger is not None else set()
        managed_client_ids = ledger.managed_client_order_ids(account_id, canonical) if ledger is not None else set()

        def is_owned(order: Any, inherited: bool = False) -> bool:
            order_id = str(self._field(order, "id", "") or "")
            client_id = str(self._field(order, "client_order_id", "") or "")
            parent_id = str(self._field(order, "parent_order_id", "") or "")
            return (
                inherited
                or legacy_external_management
                or order_id in managed_order_ids
                or parent_id in managed_order_ids
                or client_id in managed_client_ids
                or client_id.startswith("parayabuck-")
            )

        protective: list[Any] = []

        def visit(order: Any, inherited: bool = False) -> None:
            owned = is_owned(order, inherited)
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                owned
                and side == closing_side
                and status
                not in {
                    "canceled",
                    "cancelled",
                    "done_for_day",
                    "expired",
                    "filled",
                    "rejected",
                    "replaced",
                    "stopped",
                }
                and (stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"})
            ):
                protective.append(order)
                return
            for leg in self._field(order, "legs") or ():
                visit(leg, owned)

        for order in orders or ():
            if not same_symbol(str(self._field(order, "symbol", "")), symbol):
                continue
            visit(order)
        return protective

    def _has_protective_order(
        self,
        symbol: str,
        position_is_long: bool,
        required_qty: float,
        open_orders: Any = None,
    ) -> bool:
        broker = self._broker
        protective_orders = self._open_protective_orders(symbol, position_is_long, open_orders)
        protected = sum(
            (
                max(
                    Decimal(0),
                    abs(Decimal(str(self._field(order, "qty", 0) or 0)))
                    - abs(Decimal(str(self._field(order, "filled_qty", 0) or 0))),
                )
                for order in protective_orders
            ),
            start=Decimal(0),
        )
        matches, broker._last_protection_mismatch = self._protection_matches(protected, Decimal(str(required_qty)))
        broker._last_protected_qty = float(protected)
        return matches

    def _cancel_protective_orders(self, symbol: str, position_is_long: bool) -> None:
        for order in self._open_protective_orders(symbol, position_is_long):
            order_id = self._field(order, "id")
            if order_id:
                self._broker._client.cancel_order_by_id(order_id)
                self._wait_for_cancel_confirmation(str(order_id), symbol)

    def _wait_for_cancel_confirmation(self, order_id: str, symbol: str) -> None:
        broker = self._broker
        deadline = time.monotonic() + getattr(broker, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_status = ""
        while time.monotonic() < deadline:
            try:
                order = broker._client.get_order_by_id(order_id)
                last_status = self._normalized_field(order, "status")
                if last_status in {"canceled", "cancelled", "expired", "rejected"}:
                    return
                if last_status == "filled":
                    _emit_alert(
                        "ambiguous_order",
                        message=(f"Protective order filled while cancellation was pending for {symbol}"),
                        symbol=symbol,
                        reason="protective_order_filled_during_cancel",
                    )
                    raise AmbiguousOrderState(f"Protective order filled while cancellation was pending for {symbol}")
            except AmbiguousOrderState:
                raise
            except Exception:
                pass
            time.sleep(getattr(broker, "crypto_stop_cancel_poll_interval_seconds", 0.25))
        _emit_alert(
            "ambiguous_order",
            message=(f"Protective order cancellation is unconfirmed for {symbol}: {last_status or 'lookup_failed'}"),
            symbol=symbol,
            reason="cancel_unconfirmed",
            last_status=last_status or "lookup_failed",
        )
        raise AmbiguousOrderState(
            f"Protective order cancellation is unconfirmed for {symbol}: {last_status or 'lookup_failed'}"
        )

    # -- submit ----------------------------------------------------------

    def submit_stop(
        self,
        symbol: str,
        market: Market,
        qty: float,
        stop_price: float,
        position_is_long: bool,
        client_order_id: str | None = None,
    ) -> str | None:
        broker = self._broker
        client_order_id = client_order_id or f"parayabuck-stop-{uuid4().hex}"
        side = OrderSide.SELL if position_is_long else OrderSide.BUY
        step = self._price_step(symbol, market, stop_price)
        stop_price = self._round_to_step(stop_price, step)
        qty_number: float = qty
        equity_tif = TimeInForce.GTC
        if market == Market.EQUITY:
            qty_decimal = Decimal(str(qty))
            if qty_decimal == qty_decimal.to_integral_value():
                qty_number = int(qty_decimal)
            else:
                qty_number = float(qty_decimal.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN))
                equity_tif = TimeInForce.DAY
        if qty_number <= 0 or stop_price <= 0:
            log.warning("Skipping protective stop for %s: qty=%s stop=%s rounds to zero", symbol, qty, stop_price)
            return None
        try:
            if market == Market.CRYPTO:
                offset = Decimal(str(getattr(broker, "crypto_stop_limit_offset_bps", 25.0))) / Decimal(10000)
                stop_decimal = Decimal(str(stop_price))
                distance_steps = max(
                    1,
                    int((stop_decimal * offset / step).to_integral_value(rounding=ROUND_CEILING)),
                )
                limit_decimal = stop_decimal + (-distance_steps * step if position_is_long else distance_steps * step)
                limit_price = float(limit_decimal)
                if limit_price <= 0:
                    log.warning("Skipping protective stop for %s: limit price rounds to %s", symbol, limit_price)
                    return None
                request: Any = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=stop_price,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
            else:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=equity_tif,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            result = self._submit_with_recovery(request, client_order_id)
            if market == Market.CRYPTO:
                broker_id = str(self._field(result, "id", "") or "")
                ledger = getattr(broker, "ledger", None)
                if broker_id and ledger is not None and "-stop" in client_order_id:
                    entry_client_order_id = client_order_id.rsplit("-stop", 1)[0]
                    ledger.register_protective_stop(
                        broker._account_id or "unknown",
                        entry_client_order_id,
                        client_order_id,
                        broker_id,
                    )
            return client_order_id
        except AmbiguousOrderState:
            log.critical(
                "Stop submit ambiguous: market=%s symbol=%s order_client_id=%s recovery_action=protection_reconciliation_required",
                market.value,
                symbol,
                client_order_id,
            )
            raise
        except Exception as exc:
            available = self._available_qty_from_error(exc)
            if market == Market.CRYPTO and 0 < available < qty_number:
                available = self._round_qty_down(symbol, available)
                if available > 0:
                    log.warning("Retrying protective stop for %s with Alpaca available qty %s", symbol, available)
                    try:
                        retry_client_order_id = f"parayabuck-stop-{uuid4().hex}"
                        retry_request = StopLimitOrderRequest(
                            symbol=symbol,
                            qty=available,
                            side=side,
                            time_in_force=TimeInForce.GTC,
                            stop_price=stop_price,
                            limit_price=limit_price,
                            client_order_id=retry_client_order_id,
                        )
                        result = self._submit_with_recovery(retry_request, retry_client_order_id)
                        ledger = getattr(broker, "ledger", None)
                        broker_id = str(self._field(result, "id", "") or "")
                        if ledger is not None and broker_id and "-stop" in client_order_id:
                            ledger.register_protective_stop(
                                broker._account_id or "unknown",
                                client_order_id.rsplit("-stop", 1)[0],
                                retry_client_order_id,
                                broker_id,
                            )
                        return retry_client_order_id
                    except AmbiguousOrderState:
                        raise
                    except Exception as retry_exc:
                        exc = retry_exc
            log.error("Protective stop failed for %s: %s", symbol, exc)
            return None
