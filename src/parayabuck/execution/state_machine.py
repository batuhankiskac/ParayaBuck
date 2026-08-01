from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from parayabuck.core.models import OrderIntent, OrderState

_ALLOWED: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATED, OrderState.REJECTED}),
    OrderState.VALIDATED: frozenset({OrderState.SUBMITTED, OrderState.REJECTED}),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.REJECTED,
            OrderState.RECONCILIATION_REQUIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.REJECTED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED}
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.RECONCILIATION_REQUIRED: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.MANUAL_INTERVENTION_REQUIRED,
        }
    ),
    OrderState.MANUAL_INTERVENTION_REQUIRED: frozenset(),
    # Kept for compatibility with persisted/runtime values created before the
    # recoverable reconciliation state was introduced.
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.MANUAL_INTERVENTION_REQUIRED,
        }
    ),
}


@dataclass
class OrderLifecycle:
    order: OrderIntent
    state: OrderState = OrderState.CREATED
    history: list[tuple[datetime, OrderState, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.history.append((datetime.now(UTC), self.state, "created"))


class OrderStateMachine:
    def __init__(self) -> None:
        self.orders: dict[str, OrderLifecycle] = {}
        self._lock = threading.RLock()

    def create(self, order: OrderIntent) -> OrderLifecycle:
        with self._lock:
            if order.client_order_id in self.orders:
                return self.orders[order.client_order_id]
            lifecycle = OrderLifecycle(order)
            self.orders[order.client_order_id] = lifecycle
            return lifecycle

    def transition(
        self, client_order_id: str, state: OrderState, reason: str = ""
    ) -> OrderLifecycle:
        with self._lock:
            lifecycle = self.orders[client_order_id]
            # Broker acknowledgements can arrive through both the synchronous
            # submit response and the asynchronous trade-update stream. Treat the
            # second observation as an idempotent no-op.
            if state == lifecycle.state:
                return lifecycle
            if state not in _ALLOWED[lifecycle.state]:
                raise ValueError(f"Invalid order transition {lifecycle.state} -> {state}")
            lifecycle.state = state
            lifecycle.history.append((datetime.now(UTC), state, reason))
            return lifecycle
