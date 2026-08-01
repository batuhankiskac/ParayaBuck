from __future__ import annotations

from parayabuck.core.models import ExecutionEvent, Fill, OrderIntent, OrderRole, OrderState
from parayabuck.execution.ledger import ExecutionLedger


def apply_entry_fill(
    ledger: ExecutionLedger,
    account_id: str,
    order: OrderIntent,
    fill: Fill,
    broker_order_id: str,
) -> None:
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id=account_id,
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol=order.symbol,
            market=order.market,
            state=OrderState.FILLED,
            requested_qty=order.qty,
            cumulative_filled_qty=fill.qty,
            average_fill_price=fill.price,
            occurred_at=fill.timestamp,
            fee=fill.commission,
        )
    )


__all__ = ["apply_entry_fill"]
