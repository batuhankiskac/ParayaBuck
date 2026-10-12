"""Execution-ledger safety state: the legacy startup guard, portfolio fees and
fail-closed incident loading. Small synthetic SQLite files under tmp_path."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.ledger import CORRUPT_INCIDENT_SCOPE, ExecutionLedger, LegacyLedgerStateError
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.storage import SQLiteStore

_AT = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _legacy_db(path: Path) -> None:
    """A database with the legacy schema (created by an earlier ledger open)."""
    ExecutionLedger(str(path)).close()


def _insert_managed_position(path: Path, status: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO managed_positions(
                account_id, canonical_symbol, market, strategy, source_signal_id, entry_client_order_id,
                broker_order_id, owned_qty, filled_avg_price, original_stop, original_target, status, opened_at,
                remaining_owned_qty
            ) VALUES ('acct', 'XLE', 'equity', 'legacy', 'sig', 'legacy-entry', 'b-1', 5, 100, 95, 110, ?, ?, 5)""",
            (status, _AT.isoformat()),
        )


def _insert_order(path: Path, terminal_state: str | None) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO orders(
                client_order_id, account_id, order_role, canonical_symbol, market, side, requested_qty, terminal_state,
                updated_at
            ) VALUES ('legacy-stop', 'acct', 'protective_stop', 'XLE', 'equity', 'sell', 5, ?, ?)""",
            (terminal_state, _AT.isoformat()),
        )


@pytest.mark.parametrize("status", ["open", "manual_intervention_required"])
def test_open_legacy_managed_position_refuses_startup(tmp_path: Path, status: str) -> None:
    db = tmp_path / "runtime.sqlite3"
    _legacy_db(db)
    _insert_managed_position(db, status)

    with pytest.raises(LegacyLedgerStateError) as excinfo:
        ExecutionLedger(str(db))

    message = str(excinfo.value)
    assert "managed_positions" in message
    assert "XLE" in message and "legacy-entry" in message
    assert "UPDATE managed_positions SET status='closed'" in message


def test_non_terminal_legacy_order_refuses_startup(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _legacy_db(db)
    _insert_order(db, None)

    with pytest.raises(LegacyLedgerStateError, match="orders non-terminal rows: legacy-stop"):
        ExecutionLedger(str(db))


def test_closed_legacy_history_opens_normally(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _legacy_db(db)
    _insert_managed_position(db, "closed")
    _insert_order(db, "filled")

    ExecutionLedger(str(db)).close()


def test_a_refused_open_releases_the_shared_store(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _legacy_db(db)
    _insert_managed_position(db, "open")
    store = SQLiteStore(db)
    store.acquire()

    with pytest.raises(LegacyLedgerStateError):
        ExecutionLedger(str(db), store=store)

    # Only the caller's own claim remains: the store is still usable.
    assert store.connection.execute("SELECT COUNT(*) FROM managed_positions").fetchone() == (1,)
    store.release()


def _record_fill(repo: PortfolioStateRepository, account_id: str, client_order_id: str, fee: float | None) -> None:
    session_id = repo.begin_session(account_id, f"digest-{client_order_id}", "paper")
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_AT, strategy_name="eq", strategy_version="1"
    )
    decision_id = repo.record_decision_with_orders(
        session_id,
        account_id,
        f"digest-{client_order_id}",
        target,
        RiskOverlayDecision(target, False, (), 0.0, 0.0),
        Market.EQUITY,
        (),
    )
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_order_id,
            decision_id=decision_id,
            symbol="QLD",
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=2.0,
            reference_price=100.0,
            decision_timestamp=_AT,
            strategy_name="eq",
            strategy_version="1",
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=f"b-{client_order_id}",
        status="fill",
        cumulative_filled_quantity=2.0,
        average_fill_price=100.0,
        occurred_at=_AT,
        activity_id=f"exec-{client_order_id}",
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_total_fees_come_from_the_accounts_portfolio_fills(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    try:
        _record_fill(repo, "acct", "pb-1", 0.25)
        _record_fill(repo, "acct", "pb-2", None)
        _record_fill(repo, "other", "pb-3", 9.0)

        assert ledger.total_fees_for_account("acct") == pytest.approx(0.25)
        assert ledger.total_fees_for_account("other") == pytest.approx(9.0)
    finally:
        ledger.close()


def test_total_fees_without_portfolio_tables_is_zero(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    try:
        assert ledger.total_fees_for_account("acct") == 0.0
    finally:
        ledger.close()


def test_corrupt_safety_incident_is_kept_as_a_halt(tmp_path: Path) -> None:
    """A row with unparseable timestamps still describes a halt: loading it
    must not silently drop it (fail open)."""
    db = tmp_path / "ledger.sqlite3"
    ledger = ExecutionLedger(str(db))
    try:
        ledger.save_safety_incident("acct", "ownership_drift", activated_at=_AT)
        with ledger.connection:
            ledger.connection.execute(
                "INSERT INTO safety_incidents(account_id, reason, scope, activated_at, updated_at) "
                "VALUES ('acct', 'portfolio_drawdown_halt', 'global', 'not-a-time', 'garbage')"
            )

        incidents = ledger.load_safety_incidents("acct")
    finally:
        ledger.close()

    by_reason = {incident.reason: incident for incident in incidents}
    assert set(by_reason) == {"ownership_drift", "portfolio_drawdown_halt"}
    assert by_reason["portfolio_drawdown_halt"].scope == CORRUPT_INCIDENT_SCOPE
    assert by_reason["ownership_drift"].scope == "global"
