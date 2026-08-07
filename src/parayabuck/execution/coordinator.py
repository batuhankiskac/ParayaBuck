from __future__ import annotations

import threading

from parayabuck.core.models import (
    ExecutionEvent,
    ExecutionTransition,
    OrderRole,
    OrderState,
)
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.logger import get_logger
from parayabuck.risk.manager import RiskManager

log = get_logger("parayabuck.execution.coordinator")


class ExecutionCoordinator:
    """Broker-independent order lifecycle, reservation and fill-delta coordinator."""

    def __init__(
        self,
        orders: OrderStateMachine,
        risk: RiskManager,
        ledger: ExecutionLedger | None = None,
    ) -> None:
        self.orders = orders
        self.risk = risk
        self.ledger = ledger
        self._cumulative_fills: dict[str, float] = {}
        self._lock = threading.RLock()

    def apply(self, event: ExecutionEvent) -> ExecutionTransition:
        with self._lock:
            return self._apply_unlocked(event)

    def _apply_unlocked(self, event: ExecutionEvent) -> ExecutionTransition:
        if self.ledger is not None:
            transition = self.ledger.apply_execution_event(event)
        else:
            previous = self._cumulative_fills.get(event.client_order_id, 0.0)
            cumulative = min(
                max(0.0, event.cumulative_filled_qty),
                max(0.0, event.requested_qty),
            )
            effective = max(previous, cumulative)
            self._cumulative_fills[event.client_order_id] = effective
            delta = max(0.0, cumulative - previous)
            transition = ExecutionTransition(
                previous_state=None,
                current_state=event.state,
                fill_delta=delta,
                position_delta=delta if event.role == OrderRole.ENTRY else -delta,
                realized_pnl_delta=0.0,
                entry_client_order_id=(
                    event.client_order_id if event.role == OrderRole.ENTRY else None
                ),
            )

        lifecycle = self.orders.orders.get(event.client_order_id)
        if (
            event.role == OrderRole.ENTRY
            and lifecycle is not None
            and event.state is not None
            and lifecycle.state != event.state
        ):
            try:
                self.orders.transition(
                    event.client_order_id,
                    event.state,
                    "normalized_execution_event",
                )
            except ValueError as exc:
                # Duplicate, late and out-of-order events never move the local
                # lifecycle backwards, but a repeated transition is worth
                # observing so lifecycle bugs do not stay silent.
                log.warning(
                    "Ignored order transition %s -> %s for %s: %s",
                    lifecycle.state.value,
                    event.state.value,
                    event.client_order_id,
                    exc,
                )

        if lifecycle is not None and event.role == OrderRole.ENTRY:
            if event.state in {
                OrderState.ACCEPTED,
                OrderState.PARTIALLY_FILLED,
                OrderState.RECONCILIATION_REQUIRED,
            }:
                self.risk.reserve(
                    lifecycle.order,
                    event.requested_qty - event.cumulative_filled_qty,
                )
            elif event.state is not None:
                self.risk.release(event.client_order_id)
        if transition.realized_pnl_delta:
            self.risk.record_realized_pnl(transition.realized_pnl_delta)
        return transition


__all__ = ["ExecutionCoordinator"]
