"""Crypto synthetic OCO — exit, failover, reprotection.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- ``process_crypto_price`` per-symbol ``threading.Lock`` (non-blocking,
  same-symbol serialized, different symbols not blocked, no global lock).
- Target / stop / emergency (``stop * (1 - 75 bps)``) / intrabar both-hit.
- ``claim_symbol_exit`` idempotent, ``_cancel_stop_then_exit`` (bounded
  5 s poll, 0.25 s interval, verify terminal, partial fill → cancel
  remainder, ambiguous → manual intervention).
- Reprotection of remainder, pending-exit restart recovery via
  ``reconcile``.
- Never duplicate exit, never leave unprotected remainder.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from parayabuck.core.models import Direction, ExecutionEvent, Market, OrderIntent, OrderRole, OrderState
from parayabuck.execution.alpaca_models import AmbiguousOrderState, CryptoPriceObservation
from parayabuck.execution.ledger import ManagedPosition
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol

log = get_logger("parayabuck.execution.alpaca_crypto")

__all__ = ["CryptoService"]


class CryptoService:
    """Crypto execution lifecycle — production implementation."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    # -- delegated helpers ------------------------------------------------

    def _field(self, value: Any, name: str, default: Any = None) -> Any:
        return self._broker._field(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._broker._normalized_field(value, name)  # type: ignore[no-any-return]

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        return self._broker._submit_with_recovery(request, client_order_id)

    def _wait_for_fill(self, order_id: str, intent: OrderIntent, timeout: float = 5.0) -> tuple[OrderState, Any]:
        return self._broker._wait_for_fill(order_id, intent, timeout=timeout)  # type: ignore[no-any-return]

    def _available_position_qty(
        self, symbol: str, requested_qty: float, *, reserve_fee_on_lookup_failure: bool, attempts: int = 5
    ) -> float:
        return self._broker._available_position_qty(  # type: ignore[no-any-return]
            symbol, requested_qty, reserve_fee_on_lookup_failure=reserve_fee_on_lookup_failure, attempts=attempts
        )

    def _reprotect_after_terminal_exit(self, position: ManagedPosition) -> None:
        broker = self._broker
        ledger = getattr(broker, "ledger", None)
        if ledger is None:
            from parayabuck.execution.alpaca_models import ProtectionReconciliationError

            raise ProtectionReconciliationError(f"Cannot re-protect {position.canonical_symbol}: no ledger")
        current = next(
            (
                item
                for item in ledger.managed_entries_for_account(getattr(broker, "_account_id", "") or "unknown")
                if item.entry_client_order_id == position.entry_client_order_id
            ),
            None,
        )
        if current is None or current.owned_qty <= 0:
            return
        resolution = broker.resolve_available_position_qty(
            current.canonical_symbol,
            current.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if not resolution.verified or resolution.available_qty <= 0:
            ledger.mark_manual_intervention(
                getattr(broker, "_account_id", "") or "unknown", current.entry_client_order_id
            )
            from parayabuck.execution.alpaca_models import ProtectionReconciliationError

            raise ProtectionReconciliationError(f"Remaining exit quantity is unverified for {current.canonical_symbol}")
        client_id = f"{current.entry_client_order_id}-stop-reprotect"
        log.warning(
            "Re-protection requested: market=crypto symbol=%s entry_client_order_id=%s managed_qty=%s verified_available_qty=%s quantity_source=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            current.owned_qty,
            resolution.available_qty,
            resolution.source,
        )
        protected = broker._submit_stop(
            current.canonical_symbol,
            Market.CRYPTO,
            resolution.available_qty,
            current.current_stop or current.original_stop,
            True,
            client_order_id=client_id,
        )
        if protected is None:
            if broker._has_protective_order(current.canonical_symbol, True, resolution.available_qty):
                ledger.mark_manual_intervention(
                    getattr(broker, "_account_id", "") or "unknown", current.entry_client_order_id
                )
                from parayabuck.execution.alpaca_models import AmbiguousOrderState

                raise AmbiguousOrderState(
                    f"Protective stop exists while re-protection failed for {current.canonical_symbol}"
                )
            from parayabuck.core.models import Direction, ExecutionEvent, OrderIntent, OrderRole, OrderState

            intent = OrderIntent(
                current.source_signal_id,
                current.canonical_symbol,
                Market.CRYPTO,
                Direction.LONG,
                resolution.available_qty,
                current.filled_avg_price,
                current.current_stop or current.original_stop,
                current.original_target,
                current.strategy,
                client_order_id=current.entry_client_order_id,
            )
            exit_fill, exit_broker_id = broker._flatten(intent, resolution.available_qty)
            ledger.apply_execution_event(
                ExecutionEvent(
                    account_id=getattr(broker, "_account_id", "") or "unknown",
                    client_order_id=exit_fill.client_order_id,
                    broker_order_id=exit_broker_id,
                    parent_order_id=None,
                    role=OrderRole.EMERGENCY_EXIT,
                    symbol=current.canonical_symbol,
                    market=Market.CRYPTO,
                    state=OrderState.FILLED,
                    requested_qty=exit_fill.qty,
                    cumulative_filled_qty=exit_fill.qty,
                    average_fill_price=exit_fill.price,
                    occurred_at=exit_fill.timestamp,
                )
            )
            ledger.set_exit_state(
                getattr(broker, "_account_id", "") or "unknown",
                current.entry_client_order_id,
                stop_state="open",
                target_state="emergency_exit_filled",
                clear_exit_order=True,
            )
            return
        ledger.set_exit_state(
            getattr(broker, "_account_id", "") or "unknown",
            current.entry_client_order_id,
            stop_state="open",
            target_state="reprotected_after_terminal_exit",
            clear_exit_order=True,
        )
        log.warning(
            "Re-protection active: market=crypto symbol=%s entry_client_order_id=%s order_client_id=%s managed_qty=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            client_id,
            resolution.available_qty,
        )

    # -- per-symbol lock + price trigger ---------------------------------

    def process_crypto_price(self, observation: CryptoPriceObservation) -> str | None:
        broker = self._broker
        ledger = getattr(broker, "ledger", None)
        if ledger is None or not getattr(broker, "_account_id", ""):
            return None
        symbol = canonical_symbol(observation.symbol, Market.CRYPTO)
        guard = getattr(broker, "_symbol_exit_locks_guard", None)
        if guard is None:
            broker._symbol_exit_locks_guard = threading.Lock()
            guard = broker._symbol_exit_locks_guard
        if not hasattr(broker, "_symbol_exit_locks"):
            broker._symbol_exit_locks = {}
        with guard:
            lock = broker._symbol_exit_locks.setdefault(symbol, threading.Lock())
        if not lock.acquire(blocking=False):
            log.warning(
                "Crypto exit observation deferred because symbol lock is busy: symbol=%s source=%s observed_at=%s",
                symbol,
                observation.source,
                observation.timestamp.isoformat(),
            )
            return None
        try:
            entries = [
                item
                for item in ledger.managed_entries_for_account(broker._account_id)
                if item.market == Market.CRYPTO and item.canonical_symbol == symbol
            ]
            for item in entries:
                reason: str | None = None
                target_seen = observation.high >= item.original_target
                active_stop = item.current_stop or item.original_stop
                stop_seen = observation.low <= active_stop
                if target_seen and stop_seen:
                    log.critical(
                        "Intrabar path ambiguous: market=crypto symbol=%s "
                        "entry_client_order_id=%s stop_state=%s target_state=%s "
                        "recovery_action=resolve_broker_stop",
                        symbol,
                        item.entry_client_order_id,
                        item.stop_state,
                        item.target_state,
                    )
                    if not item.protective_stop_broker_order_id:
                        ledger.mark_manual_intervention(broker._account_id, item.entry_client_order_id)
                        raise AmbiguousOrderState(f"Intrabar stop/target order is unknown for {symbol}")
                    stop = broker._client.get_order_by_id(item.protective_stop_broker_order_id)
                    stop_status = self._normalized_field(stop, "status")
                    if stop_status == "filled":
                        claimed = ledger.claim_symbol_exit(
                            broker._account_id,
                            item.entry_client_order_id,
                            reason="intrabar_stop",
                        )
                        if claimed is not None:
                            self._cancel_stop_then_exit(claimed, "intrabar_stop")
                        return "stop"
                    ledger.mark_manual_intervention(broker._account_id, item.entry_client_order_id)
                    raise AmbiguousOrderState(f"Intrabar stop/target path unresolved for {symbol}: {stop_status}")
                if target_seen:
                    reason = "target"
                else:
                    emergency_price = active_stop * (1 - broker.crypto_emergency_exit_bps / 10_000)
                    if observation.low <= emergency_price:
                        reason = "stop_failover"
                    elif observation.low <= active_stop and item.stop_state == "open":
                        ledger.set_exit_state(
                            broker._account_id,
                            item.entry_client_order_id,
                            stop_state="triggered_unfilled",
                        )
                        log.warning(
                            "Crypto stop triggered but remains unfilled: market=crypto symbol=%s source=%s",
                            symbol,
                            observation.source,
                        )
                if reason is None:
                    continue
                claimed = ledger.claim_symbol_exit(
                    broker._account_id,
                    item.entry_client_order_id,
                    reason=reason,
                )
                if claimed is None:
                    continue
                log.warning(
                    "Crypto %s triggered: market=crypto symbol=%s managed_qty=%s source=%s",
                    reason,
                    symbol,
                    claimed.owned_qty,
                    observation.source,
                )
                self._cancel_stop_then_exit(claimed, reason)
                return reason
            return None
        finally:
            lock.release()

    # -- cancel → exit lifecycle -----------------------------------------

    def _poll_stop_cancellation(self, position: Any, stop_id: str) -> tuple[Any, str]:
        broker = self._broker
        ledger = getattr(broker, "ledger", None)
        if ledger is None:
            raise AmbiguousOrderState(f"Stop cancellation is unverified for {position.canonical_symbol}: no ledger")
        deadline = time.monotonic() + getattr(broker, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_stop: Any = None
        last_status = ""
        cancellation_lookup_logged = False
        while time.monotonic() < deadline:
            try:
                last_stop = broker._client.get_order_by_id(stop_id)
                last_status = self._normalized_field(last_stop, "status")
                filled_qty = float(self._field(last_stop, "filled_qty", 0) or 0)
                if last_status == "filled" and filled_qty > 0:
                    return last_stop, "filled"
                if last_status in {"canceled", "cancelled", "expired", "rejected"}:
                    return last_stop, last_status
            except Exception as exc:
                if not cancellation_lookup_logged:
                    log.warning("Stop cancellation lookup failed for %s: %s", stop_id, exc)
                    cancellation_lookup_logged = True
            time.sleep(getattr(broker, "crypto_stop_cancel_poll_interval_seconds", 0.01))
        return last_stop, last_status

    def _cancel_stop_then_exit(self, position: ManagedPosition, reason: str) -> None:
        broker = self._broker
        ledger = getattr(broker, "ledger", None)
        if ledger is None:
            raise AmbiguousOrderState(f"Cannot cancel stop for {position.canonical_symbol}: no ledger")
        stop_id = position.protective_stop_broker_order_id
        if not stop_id:
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"Protective stop id is unknown for {position.canonical_symbol}")
        try:
            broker._client.cancel_order_by_id(stop_id)
        except Exception as exc:
            log.warning(
                "Cancel request for stop %s of %s reported an error: %s", stop_id, position.canonical_symbol, exc
            )
        stop, stop_status = self._poll_stop_cancellation(position, stop_id)
        if stop_status == "filled":
            filled_qty = float(self._field(stop, "filled_qty", 0) or 0)
            filled_price = float(self._field(stop, "filled_avg_price", 0) or 0)
            if filled_qty > 0 and filled_price > 0:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        broker._account_id,
                        position.protective_stop_client_order_id or f"{position.entry_client_order_id}-stop",
                        stop_id,
                        None,
                        OrderRole.PROTECTIVE_STOP,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        OrderState.FILLED,
                        position.owned_qty,
                        filled_qty,
                        filled_price,
                        datetime.now(UTC),
                    )
                )
            ledger.set_exit_state(
                broker._account_id,
                position.entry_client_order_id,
                stop_state="filled",
                target_state="cancelled_by_stop_fill",
            )
            return
        if stop_status not in {"canceled", "cancelled", "expired"}:
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(
                f"Stop cancellation is unconfirmed for {position.canonical_symbol}: {stop_status or 'unknown'}"
            )
        ledger.set_exit_state(
            broker._account_id,
            position.entry_client_order_id,
            stop_state="cancelled",
        )
        qty = self._available_position_qty(
            position.canonical_symbol,
            position.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if qty <= 0:
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"No verified managed quantity available for {position.canonical_symbol}")
        suffix = "target-exit" if reason == "target" else "emergency-exit"
        client_id = f"{position.entry_client_order_id}-{suffix}"
        result = self._submit_with_recovery(
            MarketOrderRequest(
                symbol=normalize_symbol(position.canonical_symbol, Market.CRYPTO).trading_symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                client_order_id=client_id,
            ),
            client_id,
        )
        broker_id = str(self._field(result, "id", "") or "")
        if not broker_id:
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"Exit broker id is unknown for {position.canonical_symbol}")
        ledger.set_exit_state(
            broker._account_id,
            position.entry_client_order_id,
            target_state="target_exit_submitted" if reason == "target" else "emergency_exit_submitted",
            exit_client_order_id=client_id,
            exit_broker_order_id=broker_id,
        )
        intent = OrderIntent(
            position.source_signal_id,
            position.canonical_symbol,
            Market.CRYPTO,
            Direction.LONG,
            qty,
            position.filled_avg_price,
            position.original_stop,
            position.original_target,
            position.strategy,
            client_order_id=client_id,
        )
        state, fill = self._wait_for_fill(broker_id, intent, timeout=5.0)
        if fill is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    broker._account_id,
                    client_id,
                    broker_id,
                    None,
                    OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    fill.qty,
                    fill.price,
                    fill.timestamp,
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                broker._account_id,
                position.entry_client_order_id,
                target_state="target_exit_filled" if reason == "target" else "emergency_exit_filled",
            )
            return
        if state == OrderState.PARTIALLY_FILLED:
            ledger.set_exit_state(
                broker._account_id,
                position.entry_client_order_id,
                target_state="target_exit_partially_filled",
            )
            try:
                broker._client.cancel_order_by_id(broker_id)
            except Exception as exc:
                log.warning(
                    "Partial crypto exit cancel response ambiguous: broker_order_id=%s error=%s", broker_id, exc
                )
            terminal_state, final_fill = self._wait_for_fill(
                broker_id,
                intent,
                timeout=getattr(broker, "crypto_stop_cancel_timeout_seconds", 5.0),
            )
            if final_fill is not None:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        broker._account_id,
                        client_id,
                        broker_id,
                        None,
                        OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        terminal_state,
                        qty,
                        final_fill.qty,
                        final_fill.price,
                        final_fill.timestamp,
                    )
                )
            if terminal_state == OrderState.FILLED:
                ledger.set_exit_state(
                    broker._account_id,
                    position.entry_client_order_id,
                    target_state="target_exit_filled" if reason == "target" else "emergency_exit_filled",
                )
                return
            if terminal_state not in {OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}:
                ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
                raise AmbiguousOrderState(
                    "Partial crypto exit cancellation is unconfirmed for "
                    f"{position.canonical_symbol}: {terminal_state.value}"
                )
            self._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
        raise AmbiguousOrderState(
            f"Crypto {reason} exit is not terminal for {position.canonical_symbol}: {state.value}"
        )
