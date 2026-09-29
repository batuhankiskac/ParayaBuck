"""Execution-event ledger invariants: fee booking, unlinked fills, order side.

Every fixture is a synthetic ``tmp_path`` ledger — no network, no credentials,
no production paths.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import (
    Direction,
    ExecutionEvent,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
)
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger


def _entry(ledger: ExecutionLedger) -> OrderIntent:
    order = OrderIntent(
        signal_id="signal-1",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=5.0,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        strategy="test",
        client_order_id="entry-1",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="entry-1",
            broker_order_id="broker-entry",
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=100.0,
            occurred_at=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
            fee=0.25,
        )
    )
    return order


def _booked_exit(ledger: ExecutionLedger, parent_broker_id: str) -> None:
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="exit-client-1",
            broker_order_id="broker-exit",
            parent_order_id=parent_broker_id,
            role=OrderRole.MANUAL_EXIT,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=105.0,
            occurred_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
        )
    )


def _fill_activity_broker(ledger: ExecutionLedger, activities: list[dict[str, Any]]) -> AlpacaBroker:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(
        _request=lambda *_args, **kwargs: [] if kwargs.get("data", {}).get("page_token") else activities
    )
    broker.ledger = ledger
    broker._account_id = "acct"
    return broker


def _exit_fee_activity() -> list[dict[str, Any]]:
    return [
        {
            "id": "act-2",
            "order_id": "broker-exit",
            "side": "sell",
            "symbol": "AAPL",
            "qty": "5",
            "price": "105",
            "commission": "0.5",
            "transaction_time": "2026-01-02T15:00:00+00:00",
        }
    ]


def test_fill_activity_fee_for_booked_exit_is_applied_to_the_position(tmp_path: Path) -> None:
    """A SELL fill activity whose broker order is already booked must reach
    ``managed_positions``: recording the fee row alone leaves the exit cost
    permanently unbooked while the watermark advances past it."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)
    _booked_exit(ledger, "broker-entry")
    broker = _fill_activity_broker(ledger, _exit_fee_activity())

    assert broker._reconcile_fill_activity_fees() is True

    entry_fees, exit_fees, realized = ledger.connection.execute(
        "SELECT entry_fees, exit_fees, realized_pnl FROM managed_positions"
    ).fetchone()
    assert (entry_fees, exit_fees) == (0.25, 0.5)
    assert realized == pytest.approx(24.25)
    assert ledger.total_fees_for_account("acct") == pytest.approx(0.75)
    ledger.close()


def test_fill_activity_fee_is_booked_exactly_once_on_replay(tmp_path: Path) -> None:
    """Replaying the same activity must never double-count the exit fee."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)
    _booked_exit(ledger, "broker-entry")
    broker = _fill_activity_broker(ledger, _exit_fee_activity())

    assert broker._reconcile_fill_activity_fees() is True
    assert broker._reconcile_fill_activity_fees() is True

    assert ledger.total_fees_for_account("acct") == pytest.approx(0.75)
    assert ledger.connection.execute("SELECT COUNT(*) FROM fee_activities").fetchone()[0] == 2
    ledger.close()


def _unlinked_exit_event(cumulative: float) -> ExecutionEvent:
    # A real execution event; FILL-activity backfill rows (``alpaca-fill-
    # activity-*``) record completed broker fills and are stamped terminal.
    return ExecutionEvent(
        account_id="acct",
        client_order_id="manual-exit-broker-exit",
        broker_order_id="broker-exit",
        parent_order_id=None,
        role=OrderRole.MANUAL_EXIT,
        symbol="AAPL",
        market=Market.EQUITY,
        state=OrderState.FILLED,
        requested_qty=5.0,
        cumulative_filled_qty=cumulative,
        average_fill_price=105.0,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        fee=0.5,
    )


def test_unlinked_exit_fill_stays_non_terminal_and_warns(tmp_path: Path, caplog: Any) -> None:
    """A MANUAL_EXIT fill that predates its managed lot has no owner: the order
    row must not be stamped terminal and the loss must be logged."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)

    with caplog.at_level(logging.WARNING, logger="parayabuck.execution.ledger"):
        ledger.apply_execution_event(_unlinked_exit_event(5.0))

    row = ledger.connection.execute(
        "SELECT terminal_state, cumulative_filled_qty FROM orders WHERE client_order_id=?",
        ("manual-exit-broker-exit",),
    ).fetchone()
    assert row == (None, 5.0)
    assert "recovery_action=await_managed_entry_link" in caplog.text
    ledger.close()


def test_redelivered_unlinked_exit_fill_stays_non_terminal(tmp_path: Path, caplog: Any) -> None:
    """Reconciliation re-emits the same exit event on every pass, so a second
    delivery carrying no new fill delta must not re-stamp the row terminal —
    that is the ``filled`` order with no ownership movement the guard exists
    to prevent. The fee is booked once and the warning is not repeated."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)

    with caplog.at_level(logging.WARNING, logger="parayabuck.execution.ledger"):
        ledger.apply_execution_event(_unlinked_exit_event(5.0))
        ledger.apply_execution_event(_unlinked_exit_event(5.0))

    row = ledger.connection.execute(
        "SELECT terminal_state, cumulative_filled_qty FROM orders WHERE client_order_id=?",
        ("manual-exit-broker-exit",),
    ).fetchone()
    assert row == (None, 5.0)
    assert ledger.connection.execute(
        "SELECT client_order_id, fee FROM fee_activities WHERE client_order_id=?",
        ("manual-exit-broker-exit",),
    ).fetchall() == [("manual-exit-broker-exit", 0.5)]
    assert caplog.text.count("Unlinked execution fill") == 1
    ledger.close()


def test_unlinked_exit_fill_stays_non_terminal_across_partial_then_full(tmp_path: Path) -> None:
    """A partial fill and the completing fill are separate deliveries; neither
    may terminalize a row whose lot was never linked."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)

    ledger.apply_execution_event(_unlinked_exit_event(2.0))
    ledger.apply_execution_event(_unlinked_exit_event(5.0))

    row = ledger.connection.execute(
        "SELECT terminal_state, cumulative_filled_qty FROM orders WHERE client_order_id=?",
        ("manual-exit-broker-exit",),
    ).fetchone()
    assert row == (None, 5.0)
    ledger.close()


def test_linked_exit_fill_is_still_marked_terminal(tmp_path: Path) -> None:
    """The non-terminal marking must apply only to unlinkable fills."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)
    _booked_exit(ledger, "broker-entry")

    row = ledger.connection.execute(
        "SELECT terminal_state FROM orders WHERE client_order_id=?", ("exit-client-1",)
    ).fetchone()
    assert row == ("filled",)
    ledger.close()


def test_execution_event_rows_record_their_side(tmp_path: Path) -> None:
    """The adverse-slippage sign is derived from ``orders.side``. A linked exit
    takes the opposite of its entry; an exit with no link has no proven side
    and must stay empty rather than be guessed."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)
    _booked_exit(ledger, "broker-entry")
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="alpaca-fill-activity-broker-unknown",
            broker_order_id="broker-unknown",
            parent_order_id=None,
            role=OrderRole.MANUAL_EXIT,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=1.0,
            cumulative_filled_qty=1.0,
            average_fill_price=105.0,
            occurred_at=datetime(2026, 1, 2, 15, 30, tzinfo=UTC),
        )
    )

    rows = dict(
        ledger.connection.execute("SELECT client_order_id, side FROM orders").fetchall()  # type: ignore[arg-type]
    )
    assert rows["entry-1"] == "long"
    assert rows["exit-client-1"] == "sell"
    # No managed lot links this fill, so the role proves nothing about its side.
    assert rows["alpaca-fill-activity-broker-unknown"] == ""
    ledger.close()


def test_short_entry_exit_rows_record_the_opposite_side(tmp_path: Path) -> None:
    """A short position is closed with a buy. Deriving the side from the order
    role stamps "sell" and inverts the slippage sign of the closing fill."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    short = OrderIntent(
        signal_id="signal-2",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.SHORT,
        qty=5.0,
        entry_price=100.0,
        stop_price=105.0,
        target_price=90.0,
        strategy="test",
        client_order_id="entry-short",
    )
    ledger.register_entry_order("acct", short, "broker-short-entry")
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="entry-short",
            broker_order_id="broker-short-entry",
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=100.0,
            occurred_at=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
        )
    )
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="short-exit-1",
            broker_order_id="broker-short-exit",
            parent_order_id="broker-short-entry",
            role=OrderRole.PROTECTIVE_STOP,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=105.0,
            occurred_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
        )
    )

    rows = dict(
        ledger.connection.execute("SELECT client_order_id, side FROM orders").fetchall()  # type: ignore[arg-type]
    )
    assert rows["entry-short"] == "short"
    assert rows["short-exit-1"] == "buy"
    ledger.close()


def test_existing_empty_side_rows_heal_on_conflict(tmp_path: Path) -> None:
    """Rows written by the previous version stored ``side=''``; the conflict
    clause must backfill them without clobbering a known side."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    _entry(ledger)
    _booked_exit(ledger, "broker-entry")
    ledger.connection.execute("UPDATE orders SET side='' WHERE client_order_id='exit-client-1'")
    ledger.connection.commit()

    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="exit-client-1",
            broker_order_id="broker-exit",
            parent_order_id="broker-entry",
            role=OrderRole.MANUAL_EXIT,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=105.0,
            occurred_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
        )
    )

    assert ledger.connection.execute("SELECT side FROM orders WHERE client_order_id='exit-client-1'").fetchone() == (
        "sell",
    )
    ledger.close()


def test_slippage_is_omitted_for_an_unknown_side(tmp_path: Path) -> None:
    """An unrecognized side must fail closed to "no slippage", not default to
    the short sign."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    order = OrderIntent(
        signal_id="signal-1",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=5.0,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        strategy="test",
        client_order_id="entry-1",
    )
    ledger.register_entry_order("acct", order, "broker-entry", reference_price=100.0)
    ledger.connection.execute("UPDATE orders SET side='flat' WHERE client_order_id='entry-1'")
    ledger.connection.commit()

    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="entry-1",
            broker_order_id="broker-entry",
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=101.0,
            occurred_at=datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
        )
    )

    assert ledger.connection.execute("SELECT slippage_bps FROM orders WHERE client_order_id='entry-1'").fetchone() == (
        None,
    )
    ledger.close()
