from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.core.models import (
    Direction,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
)
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream
from parayabuck.execution.ledger import ExecutionLedger
from tests.helpers import apply_entry_fill, make_broker

pytestmark = pytest.mark.paper_chaos


def test_parentless_bracket_children_keep_their_exit_roles() -> None:
    updates = []
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)

    async def handler(update: object) -> None:
        updates.append(update)

    async def scenario() -> None:
        stream._handler = handler
        common = {
            "asset_class": "us_equity",
            "symbol": "AAPL",
            "order_class": "bracket",
            "qty": "10",
            "filled_qty": "0",
            "updated_at": datetime.now(UTC),
        }
        await stream._on_update(
            {
                "event": "new",
                "order": {**common, "id": "stop", "type": "stop", "stop_price": "95"},
            }
        )
        await stream._on_update({"event": "new", "order": {**common, "id": "target", "type": "limit"}})

    asyncio.run(scenario())
    assert [item.role for item in updates] == [
        OrderRole.PROTECTIVE_STOP,
        OrderRole.TAKE_PROFIT,
    ]


def test_uuid_exit_without_parent_links_to_unique_managed_lot(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    order = OrderIntent(
        "signal",
        "GE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        client_order_id="parayabuck-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "GE", 10, 100, Direction.LONG, datetime.now(UTC)),
        "broker-entry",
    )

    transition = ledger.apply_execution_event(
        ExecutionEvent(
            "acct",
            "broker-generated-uuid",
            "broker-stop",
            None,
            OrderRole.MANUAL_EXIT,
            "GE",
            Market.EQUITY,
            OrderState.FILLED,
            10,
            10,
            95,
            datetime.now(UTC),
        )
    )

    row = ledger.connection.execute(
        """
        SELECT entry_client_order_id, realized_pnl, status
        FROM managed_positions WHERE entry_client_order_id=?
        """,
        (order.client_order_id,),
    ).fetchone()
    assert transition.entry_client_order_id == order.client_order_id
    assert transition.realized_pnl_delta == -50
    assert row == (order.client_order_id, -50.0, "closed")
    ledger.close()


def test_corrupt_risk_state_payload_is_treated_as_absent(tmp_path: Path) -> None:
    """A truncated/corrupt risk_state row must not fail startup: it is
    treated as absent and rebuilt from the account."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.connection.execute(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "acct",
            datetime.now(UTC).date().isoformat(),
            "{truncated json",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    ledger.connection.commit()
    assert ledger.load_risk_state("acct", datetime.now(UTC).date()) is None
    ledger.close()


def test_fill_activity_recovers_exit_pnl_when_stream_was_offline(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    created = datetime.now(UTC) - timedelta(minutes=5)
    order = OrderIntent(
        "signal",
        "GE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=created,
        client_order_id="parayabuck-offline-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "GE", 10, 100, Direction.LONG, created),
        "broker-entry",
    )
    activity = {
        "id": "activity-1",
        "order_id": "broker-stop-child",
        "symbol": "GE",
        "side": "sell",
        "qty": "10",
        "price": "95",
        "commission": "1",
        "transaction_time": datetime.now(UTC).isoformat(),
    }
    broker = make_broker(
        tmp_path,
        ledger=ledger,
        client=SimpleNamespace(
            _request=lambda *_args, **kwargs: [] if kwargs.get("data", {}).get("page_token") else [activity]
        ),
    )
    broker._account_id = "acct"

    ReconciliationService(broker)._reconcile_fill_activity_fees()
    ReconciliationService(broker)._reconcile_fill_activity_fees()

    row = ledger.connection.execute(
        """
        SELECT exit_filled_qty, realized_pnl, exit_fees, status
        FROM managed_positions WHERE entry_client_order_id=?
        """,
        (order.client_order_id,),
    ).fetchone()
    assert row == (10.0, -51.0, 1.0, "closed")
    ledger.close()


def test_fill_activity_recovers_entry_fill_idempotently(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "entry-recovery.sqlite3"))
    created = datetime.now(UTC) - timedelta(minutes=5)
    order = OrderIntent(
        "signal",
        "XLE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=created,
        client_order_id="parayabuck-offline-equity-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    activities = [
        {
            "id": f"buy-activity-{index}",
            "order_id": "broker-entry",
            "symbol": "XLE",
            "side": "buy",
            "qty": "5",
            "price": str(price),
            "commission": "0",
            "transaction_time": (created + timedelta(seconds=index)).isoformat(),
        }
        for index, price in enumerate((99, 101), 1)
    ]
    broker = make_broker(
        tmp_path,
        ledger=ledger,
        client=SimpleNamespace(
            _request=lambda *_args, **kwargs: [] if kwargs.get("data", {}).get("page_token") else activities
        ),
    )
    broker._account_id = "acct"

    ReconciliationService(broker)._reconcile_fill_activity_fees()
    ReconciliationService(broker)._reconcile_fill_activity_fees()

    position = ledger.managed_for_account("acct")["XLE"]
    assert position.owned_qty == 10
    assert position.filled_avg_price == 100
    ledger.close()
