"""Regression tests for audit worker W12: order status, fills, schema and ledger.

Each test uses a fresh tmp_path database and never touches production paths,
the network or credentials. The alert sink is replaced where a fill is refused.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.ledger import ExecutionLedger, parse_risk_state_payload
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.monitoring import alerts
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.risk.state import RiskState
from parayabuck.storage import RUNTIME_SCHEMA_VERSION, StorageInitializationError

_AT = datetime(2026, 9, 4, 15, tzinfo=UTC)
_CLIENT = "w12-buy"


def _submitted(tmp_path: Path, *, rebalance_phase: str | None = None) -> tuple[PortfolioStateRepository, str]:
    """A repository holding one submitted BUY (and optionally a rebalance row)."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    target = TargetPortfolio("w12", "1", _AT, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session("acct", "digest", "paper")
    decision_id = repo.record_decision_with_orders(
        session, "acct", "digest", target, risk, Market.EQUITY, (), rebalance_phase=rebalance_phase
    )
    if rebalance_phase is None:
        order = PortfolioOrderRequest(
            client_order_id=_CLIENT,
            decision_id=decision_id,
            symbol="QLD",
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=10.0,
            reference_price=100.0,
            decision_timestamp=_AT,
            strategy_name="w12",
            strategy_version="1",
        )
        repo.record_order(order, "submitted")
    return repo, decision_id


def _apply(
    repo: PortfolioStateRepository,
    *,
    status: str,
    cumulative: float,
    average: float | None,
    activity: str,
    fee: float | None = None,
    fee_observed: bool | None = None,
) -> bool:
    return repo.apply_trade_update(
        client_order_id=_CLIENT,
        broker_order_id="broker-1",
        status=status,
        cumulative_filled_quantity=cumulative,
        average_fill_price=average,
        occurred_at=_AT,
        activity_id=activity,
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fee_observed=fee_observed,
    )


def _order_row(repo: PortfolioStateRepository) -> tuple[str, float, float | None]:
    row = repo.store.connection.execute(
        "SELECT status, cumulative_filled_quantity, average_fill_price FROM portfolio_orders WHERE client_order_id = ?",
        (_CLIENT,),
    ).fetchone()
    return str(row[0]), float(row[1]), (None if row[2] is None else float(row[2]))


def _fill_count(repo: PortfolioStateRepository) -> int:
    return int(
        repo.store.connection.execute(
            "SELECT COUNT(*) FROM portfolio_fills WHERE client_order_id = ?", (_CLIENT,)
        ).fetchone()[0]
    )


def _user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


# D-5 / Q7-2: status non-regression -------------------------------------------


@pytest.mark.parametrize("terminal", ["canceled", "expired"])
@pytest.mark.parametrize("fill_status", ["fill", "filled"])
def test_late_fill_onto_closed_order_books_fill_and_shows_fill(tmp_path, terminal, fill_status) -> None:
    repo, _ = _submitted(tmp_path)
    repo.update_order(_CLIENT, terminal, "broker-1")

    assert _apply(repo, status=fill_status, cumulative=10.0, average=101.0, activity="late-fill") is True

    assert _order_row(repo) == (fill_status, 10.0, 101.0)
    assert _fill_count(repo) == 1
    assert repo.open_orders("acct") == ()


def test_late_working_event_does_not_reopen_canceled_order(tmp_path) -> None:
    repo, _ = _submitted(tmp_path)
    repo.update_order(_CLIENT, "canceled", "broker-1")

    _apply(repo, status="new", cumulative=0.0, average=None, activity="late-new")

    assert _order_row(repo)[0] == "canceled"
    assert repo.open_orders("acct") == ()


def test_done_for_day_row_stays_final_against_working_status(tmp_path) -> None:
    repo, _ = _submitted(tmp_path)
    repo.update_order(_CLIENT, "done_for_day", "broker-1")

    repo.update_order(_CLIENT, "accepted")

    assert _order_row(repo)[0] == "done_for_day"
    assert repo.open_orders("acct") == ()


# BF-1 / D8-3: a fill with no cumulative average is refused ------------------


@pytest.mark.parametrize("status", ["fill", "partial_fill"])
def test_fill_without_average_is_refused_and_order_stays_working(tmp_path, monkeypatch, status) -> None:
    repo, _ = _submitted(tmp_path)
    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))

    booked = _apply(repo, status=status, cumulative=4.0, average=None, activity="no-average")

    assert booked is False
    assert _order_row(repo) == ("submitted", 0.0, None)
    assert _fill_count(repo) == 0
    assert [row.client_order_id for row in repo.open_orders("acct")] == [_CLIENT]
    assert emitted and emitted[0][0] == "paper_incident"
    assert emitted[0][1]["category"] == "invalid_fill_facts"


def test_refused_fill_is_booked_in_full_once_priced(tmp_path, monkeypatch) -> None:
    repo, _ = _submitted(tmp_path)
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: None)
    assert _apply(repo, status="partial_fill", cumulative=4.0, average=None, activity="no-average") is False

    booked = _apply(repo, status="partial_fill", cumulative=4.0, average=100.5, activity="with-average")

    assert booked is True
    assert _order_row(repo) == ("partial_fill", 4.0, 100.5)
    assert _fill_count(repo) == 1


def test_status_only_event_without_average_still_applies(tmp_path) -> None:
    # No new quantity, so there is no fill to price: the status still moves.
    repo, _ = _submitted(tmp_path)
    _apply(repo, status="partial_fill", cumulative=4.0, average=100.5, activity="partial-1")

    assert _apply(repo, status="pending_cancel", cumulative=4.0, average=None, activity="pending") is True
    assert _order_row(repo) == ("pending_cancel", 4.0, 100.5)


# P9-4: rebalance phase moves -------------------------------------------------


def _phase(repo: PortfolioStateRepository, decision_id: str) -> str:
    row = repo.store.connection.execute(
        "SELECT phase FROM portfolio_rebalance_executions WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    return str(row[0])


def test_rebalance_phase_move_applies_only_from_expected_phase(tmp_path) -> None:
    repo, decision_id = _submitted(tmp_path, rebalance_phase="sell_pending")

    assert repo.update_rebalance_execution(decision_id, "buy_ready", from_phase="buy_pending") is False
    assert _phase(repo, decision_id) == "sell_pending"

    assert repo.update_rebalance_execution(decision_id, "buy_ready", from_phase="sell_pending") is True
    assert _phase(repo, decision_id) == "buy_ready"


def test_rebalance_phase_move_reports_unknown_decision(tmp_path) -> None:
    repo, _ = _submitted(tmp_path, rebalance_phase="sell_pending")

    assert repo.update_rebalance_execution("no-such-decision", "complete") is False


# P7-5: admission observations -----------------------------------------------


def test_buy_admission_observation_is_written_once_per_order(tmp_path) -> None:
    repo, decision_id = _submitted(tmp_path)
    observation = {
        "account_equity": 1000.0,
        "account_cash": 500.0,
        "realized_gross": 0.0,
        "committed_buy_notional": 0.0,
        "gross_headroom": 1000.0,
        "cash_headroom": 500.0,
        "admitted_batch_notional": 100.0,
    }

    repo.record_buy_admission_observation(decision_id, (_CLIENT,), **observation)
    repo.record_buy_admission_observation(decision_id, (_CLIENT,), **observation)

    count = repo.store.connection.execute(
        "SELECT COUNT(*) FROM portfolio_buy_admission_observations WHERE client_order_id = ?", (_CLIENT,)
    ).fetchone()[0]
    assert count == 1


# Perf 1 / Perf 2: indexes ----------------------------------------------------


def test_fee_and_fill_hot_path_indexes_exist(tmp_path) -> None:
    repo, _ = _submitted(tmp_path)
    names = {str(row[0]) for row in repo.store.connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert "idx_portfolio_fills_occurred_at" in names
    assert "idx_portfolio_execution_events_order_identity" in names


# BK-2: schema version --------------------------------------------------------


def test_fresh_database_is_stamped_with_the_current_schema_version(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    PortfolioStateRepository(path).close()

    assert _user_version(path) == RUNTIME_SCHEMA_VERSION


def test_newer_schema_version_is_refused_without_writing(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    PortfolioStateRepository(path).close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version = {RUNTIME_SCHEMA_VERSION + 1}")
        connection.commit()

    with pytest.raises(StorageInitializationError, match="newer"):
        PortfolioStateRepository(path)
    with pytest.raises(StorageInitializationError, match="newer"):
        ExecutionLedger(str(path))

    assert _user_version(path) == RUNTIME_SCHEMA_VERSION + 1


# BK-7: column add and backfill are atomic ------------------------------------


def test_column_add_and_backfill_roll_back_together_and_rerun_on_reopen(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    connection = repo.store.connection
    connection.execute("ALTER TABLE portfolio_fills DROP COLUMN fill_delay_seconds")

    with pytest.raises(sqlite3.OperationalError):
        repo._add_column_with_backfill("portfolio_fills", "fill_delay_seconds REAL", "UPDATE no_such_table SET x = 1")
    assert "fill_delay_seconds" not in _columns(connection, "portfolio_fills")
    repo.close()

    reopened = PortfolioStateRepository(path)
    assert "fill_delay_seconds" in _columns(reopened.store.connection, "portfolio_fills")
    reopened.close()


# BK-9: one strict risk_state parser -----------------------------------------


def _payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "day_start_equity": 100.0,
        "peak_equity": 120.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "fees": 0.0,
        "peak_epoch": 2,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_strict_parser_reads_required_and_optional_fields() -> None:
    parsed = parse_risk_state_payload(_payload())

    assert parsed["peak_equity"] == 120.0
    assert parsed["peak_epoch"] == 2
    assert parsed["circuit_breaker_reason"] is None


def test_strict_parser_refuses_a_renamed_peak_key() -> None:
    renamed = json.dumps({"day_start_equity": 1.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "fees": 0.0})

    with pytest.raises(ValueError, match="peak_equity"):
        parse_risk_state_payload(renamed)


@pytest.mark.parametrize(
    "raw",
    [
        "[1, 2]",
        '{"day_start_equity": NaN, "peak_equity": 1, "realized_pnl": 0, "unrealized_pnl": 0, "fees": 0}',
        "not json",
    ],
)
def test_strict_parser_refuses_non_object_non_finite_or_unreadable(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_risk_state_payload(raw)


def test_load_risk_state_round_trips_peak_epoch(tmp_path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "runtime.sqlite3"))
    state = RiskState(
        account_id="acct",
        session_date=date(2026, 10, 27),
        day_start_equity=100.0,
        peak_equity=120.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fees=0.0,
        circuit_breaker_reason=None,
        updated_at=_AT,
        peak_epoch=3,
    )
    ledger.save_risk_state(state)

    loaded = ledger.load_risk_state("acct", date(2026, 10, 27))
    ledger.close()

    assert loaded is not None
    assert (loaded.peak_equity, loaded.peak_epoch) == (120.0, 3)


def test_load_risk_state_treats_a_renamed_peak_key_as_absent(tmp_path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "runtime.sqlite3"))
    renamed = json.dumps({"day_start_equity": 1.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "fees": 0.0})
    ledger.connection.execute(
        "INSERT INTO risk_state(account_id, session_date, payload, updated_at) VALUES (?, ?, ?, ?)",
        ("acct", "2026-10-27", renamed, _AT.isoformat()),
    )
    ledger.connection.commit()

    loaded = ledger.load_risk_state("acct", date(2026, 10, 27))
    ledger.close()

    assert loaded is None
