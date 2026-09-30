from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from parayabuck.core.models import (
    ExecutionEvent,
    Fill,
    OrderIntent,
    OrderRole,
    OrderState,
)
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger


def make_broker(
    tmp_path: Path,
    *,
    client: Any = None,
    ledger: ExecutionLedger | None = None,
    peak_state_path: Path | str | None = None,
    **kwargs: Any,
) -> AlpacaBroker:
    """Build a real ``AlpacaBroker`` for a test.

    Tests must never bypass ``AlpacaBroker.__init__``: an uninitialised broker
    owns no service objects, so it can only reach fallback code inside the
    facade instead of the production service implementations. The peak
    sidecar is always confined to ``tmp_path`` so no test touches a
    production state path.
    """
    return AlpacaBroker(
        "test-api-key",
        "test-secret-key",
        paper=True,
        client=client if client is not None else SimpleNamespace(),
        ledger=ledger,
        peak_state_path=(peak_state_path if peak_state_path is not None else tmp_path / "paper_equity_peak.json"),
        **kwargs,
    )


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


__all__ = ["apply_entry_fill", "make_broker"]
