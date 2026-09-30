"""The account auditor fails closed on synthetic VT35 PAPER evidence.

The fixture is the CURRENT production identity: equity-only VT35 deciding on
every NYSE session close, a QLD buy that consumes the whole 0.98 allocation
inside the configured 0.98 gross cap and 0.02 cash buffer, and one startup
catch-up decision on the cold start only. Decisions are labelled 00:00 ET and
decided at that session's NYSE close, so every order, fill and journal summary
is linked to its decision by id rather than by wall clock.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_paper_soak", ROOT / "scripts/audit_paper_soak.py")
assert spec and spec.loader
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)

# Seven consecutive NYSE sessions (Labor Day 2026-09-07 and the weekend are
# closed), labelled 00:00 ET == 04:00 UTC in September (EDT).
SESSIONS = (
    date(2026, 8, 31),
    date(2026, 9, 1),
    date(2026, 9, 2),
    date(2026, 9, 3),
    date(2026, 9, 4),
    date(2026, 9, 8),
    date(2026, 9, 9),
)
# Cold start on 2026-08-31 midday; a plain restart mid-session on 2026-09-03.
SESSION_STARTS = {
    "session-1": datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    "session-2": datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
}
DECISIONS = (
    ("eq-cold-1", "startup_catch_up", "session-1", "filled"),
    ("eq-scheduled-1", "scheduled", "session-1", "filled"),
    ("eq-scheduled-2", "scheduled", "session-1", "rejected"),
    ("eq-scheduled-3", "scheduled", "session-1", "partially_filled"),
    ("eq-scheduled-4", "scheduled", "session-2", "canceled"),
    ("eq-scheduled-5", "scheduled", "session-2", "submitted"),
    ("eq-scheduled-6", "scheduled", "session-2", None),
)
EQUITY = 100_000.0
# A real QLD entry: the whole 0.98 allocation inside the configured gross cap
# and cash buffer. The old hard-coded 0.6/0.4 bounds flagged exactly this.
GROSS_HEADROOM = 0.98 * EQUITY
CASH_HEADROOM = EQUITY - 0.02 * EQUITY


def _label(session: date) -> str:
    return datetime.combine(session, datetime.min.time(), tzinfo=auditor.NEW_YORK).astimezone(UTC).isoformat()


def _close(session: date) -> datetime:
    return auditor._session_close(session)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    db = tmp_path / "portfolio.sqlite3"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    journal = tmp_path / "journal.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE portfolio_runtime_sessions(session_id TEXT, configuration_digest TEXT, mode TEXT, started_at TEXT);
            CREATE TABLE portfolio_decisions(decision_id TEXT, session_id TEXT, configuration_digest TEXT, decision_timestamp TEXT, market TEXT, strategy_name TEXT);
            CREATE TABLE portfolio_orders(client_order_id TEXT, decision_id TEXT, market TEXT, symbol TEXT, side TEXT, quantity REAL, cumulative_filled_quantity REAL, status TEXT, created_at TEXT);
            CREATE TABLE portfolio_fills(activity_id TEXT, client_order_id TEXT);
            CREATE TABLE portfolio_execution_events(id INTEGER, client_order_id TEXT);
            CREATE TABLE portfolio_rebalance_executions(decision_id TEXT);
            CREATE TABLE portfolio_buy_admission_observations(observation_id TEXT, client_order_id TEXT, decision_id TEXT, observed_at TEXT, account_equity REAL, account_cash REAL, realized_gross REAL, committed_buy_notional REAL, gross_headroom REAL, cash_headroom REAL, admitted_batch_notional REAL);
        """)
        for session_id, started_at in SESSION_STARTS.items():
            con.execute(
                "INSERT INTO portfolio_runtime_sessions VALUES (?,?,?,?)",
                (session_id, "config-1", "paper", started_at.isoformat()),
            )
        for index, (decision_id, origin, session_id, status) in enumerate(DECISIONS):
            session = SESSIONS[index]
            label = _label(session)
            con.execute(
                "INSERT INTO portfolio_decisions VALUES (?,?,?,?,?,?)",
                (decision_id, session_id, "config-1", label, "equity", "equity_leveraged_trend_v1"),
            )
            order_id = f"order-{index}"
            if status:
                con.execute(
                    "INSERT INTO portfolio_orders VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        order_id,
                        decision_id,
                        "equity",
                        "QLD",
                        "buy",
                        980.0,
                        980.0 if status == "filled" else 490.0 if status == "partially_filled" else 0.0,
                        status,
                        _close(session).isoformat(),
                    ),
                )
                con.execute(
                    "INSERT INTO portfolio_buy_admission_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"admission-{index}",
                        order_id,
                        decision_id,
                        _close(session).isoformat(),
                        EQUITY,
                        EQUITY,
                        0.0,
                        0.0,
                        GROSS_HEADROOM,
                        CASH_HEADROOM,
                        0.98 * EQUITY,
                    ),
                )
                if status in {"filled", "partially_filled"}:
                    con.execute("INSERT INTO portfolio_fills VALUES (?,?)", (f"fill-{index}", order_id))
                if status == "partially_filled":
                    con.execute("INSERT INTO portfolio_execution_events VALUES (?,?)", (index, order_id))
            payload = {
                "schema_version": 2,
                "receipt_id": decision_id,
                "decision_id": decision_id,
                "decision_timestamp": label,
                "origin": origin,
                "market": "equity",
                "mode": "paper",
                "strategy": {
                    "name": "equity_leveraged_trend_v1",
                    "version": "1",
                    "maximum_allocation": 0.98,
                },
                "configuration_digest": "config-1",
                "universe": {"version": "universe-1"},
                "account_before": {"cash": EQUITY, "equity": EQUITY},
                "capacity_snapshot": {
                    "effective_global_slot_claims": 7,
                    "global_slot_cap": 8,
                },
                "target_state": {"kind": "INTENDED_POST_RISK", "weights": {}},
                "planned_orders": [{"kind": "INTENDED", "client_order_id": order_id}] if status else [],
                "submission_snapshot": [],
                "execution_confirmation": {"confirmed_positions_after": None},
                "database_link": {"decision_table": "portfolio_decisions"},
            }
            (receipts / f"{decision_id}.v2.json").write_text(json.dumps(payload), encoding="utf-8")
        con.execute("INSERT INTO portfolio_rebalance_executions VALUES ('eq-cold-1')")
    with sqlite3.connect(journal) as con:
        con.execute(
            "CREATE TABLE operational_events("
            "event_id TEXT, kind TEXT, market TEXT, mode TEXT, occurred_at TEXT, decision_id TEXT, fields_json TEXT)"
        )
        con.executemany(
            "INSERT INTO operational_events VALUES (?,?,?,?,?,?,?)",
            [
                (
                    "reconcile",
                    "reconciliation_recovered",
                    None,
                    "paper",
                    _close(date(2026, 9, 1)).isoformat(),
                    None,
                    "{}",
                ),
                # The engine reconciles at startup, so the restart at 12:00 UTC
                # on 2026-09-03 has its own run inside any window started there.
                (
                    "reconcile-complete",
                    "reconciliation_succeeded",
                    None,
                    "paper",
                    "2026-09-03T12:05:00+00:00",
                    None,
                    "{}",
                ),
                *[
                    (
                        f"summary-{decision_id}",
                        "decision_summary",
                        "equity",
                        "paper",
                        _close(SESSIONS[index]).isoformat(),
                        decision_id,
                        "{}",
                    )
                    for index, (decision_id, _, _, _) in enumerate(DECISIONS)
                ],
            ],
        )
    return db, receipts, journal


def _audit(db: Path, receipts: Path, journal: Path, **bounds: object) -> dict[str, object]:
    return auditor.audit(db, receipts, ROOT / "config/settings.yaml", journal, **bounds)


def _drop_decision(db: Path, receipts: Path, journal: Path, decision_id: str, index: int) -> None:
    """Remove a decision and every row that belongs to it."""
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM portfolio_decisions WHERE decision_id=?", (decision_id,))
        con.execute(
            "DELETE FROM portfolio_buy_admission_observations WHERE client_order_id=?",
            (f"order-{index}",),
        )
        con.execute("DELETE FROM portfolio_orders WHERE decision_id=?", (decision_id,))
        con.execute("DELETE FROM portfolio_fills WHERE client_order_id=?", (f"order-{index}",))
    (receipts / f"{decision_id}.v2.json").unlink()
    with sqlite3.connect(journal) as con:
        con.execute("DELETE FROM operational_events WHERE decision_id=?", (decision_id,))


def test_clean_vt35_fixture_passes(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    report = _audit(db, receipts, journal)
    assert report["status"] == "PASS", report
    assert report["counts"]["decision_classes"] == {  # type: ignore[index]
        "equity_scheduled": 6,
        "equity_startup_catch_up": 1,
    }
    assert report["counts"]["expected_nyse_sessions"] == len(SESSIONS)  # type: ignore[index]
    assert report["counts"]["cold_starts_observed"] == 1  # type: ignore[index]
    assert report["counts"]["startup_catch_up_required"] is True  # type: ignore[index]
    assert report["counts"]["reconciliation_runs_observed"] == 1  # type: ignore[index]
    assert report["counts"]["restarts_observed"] == 1  # type: ignore[index]
    assert set(report["gates"]) == {f"S{index}" for index in (1, 2, 3, 4, 5, 7, 8, 9, 10, 12)}  # type: ignore[index]


def test_window_starting_inside_a_session_keeps_that_session_intact(tmp_path: Path) -> None:
    """``--start`` is a wall clock; the decision it splits from its orders is not.

    The restart happened at 12:00 UTC on 2026-09-03, hours after that day's
    00:00 ET label. Selecting on the label dropped the decision while keeping
    its order and journal row, which S3 reported as an orphan and S2 as a gap.
    """
    db, receipts, journal = _fixture(tmp_path)
    report = _audit(db, receipts, journal, start=datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    assert report["status"] == "PASS", report
    assert report["counts"]["decisions"] == 4  # type: ignore[index]
    assert report["counts"]["orders"] == 3  # type: ignore[index]


def test_window_excludes_a_legacy_decision_before_the_start(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO portfolio_runtime_sessions VALUES ('legacy','old-config','paper','2026-07-31T12:00:00+00:00')"
        )
        con.execute(
            "INSERT INTO portfolio_decisions VALUES "
            "('legacy-decision','legacy','old-config','2026-07-31T04:00:00+00:00','equity','equity_leveraged_trend_v1')"
        )
    (receipts / "2026-07-31_equity_v3.json").write_text('{"legacy": true}', encoding="utf-8")
    report = _audit(db, receipts, journal, start=datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
    assert report["status"] == "PASS", report
    assert report["counts"]["decisions"] == 6  # type: ignore[index]


def test_a_session_missing_from_both_sources_fails_s2(tmp_path: Path) -> None:
    """VT35 decides every NYSE session; a lost Wednesday must not pass.

    The removed decision's own order, receipt and journal summary go with it,
    so neither evidence source mentions 2026-09-03 at all.
    """
    db, receipts, journal = _fixture(tmp_path)
    _drop_decision(db, receipts, journal, "eq-scheduled-3", 3)
    report = _audit(db, receipts, journal)
    assert report["status"] == "FAIL", report
    assert report["gates"]["S2"]["issues"] == ["no decision for NYSE session 2026-09-03"]  # type: ignore[index]


def test_restart_without_a_catch_up_decision_is_not_an_s9_failure(tmp_path: Path) -> None:
    """VT35 catches up once per configuration digest, never on a warm restart."""
    db, receipts, journal = _fixture(tmp_path)
    # The window opens on the morning AFTER the deployment's only cold start,
    # so the catch-up it owed is already history: nothing is due here.
    report = _audit(db, receipts, journal, start=datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
    assert report["status"] == "PASS", report
    assert report["gates"]["S9"]["status"] == "PASS"  # type: ignore[index]
    assert report["counts"]["decision_classes"] == {  # type: ignore[index]
        "equity_scheduled": 6,
        "equity_startup_catch_up": 0,
    }
    assert report["counts"]["cold_starts_observed"] == 0  # type: ignore[index]
    assert report["counts"]["startup_catch_up_required"] is False  # type: ignore[index]


def test_cold_start_that_decided_only_scheduled_fails_s9(tmp_path: Path) -> None:
    """A genuinely cold deployment owes the operator one catch-up decision."""
    db, receipts, journal = _fixture(tmp_path)
    receipt_path = receipts / "eq-cold-1.v2.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["origin"] = "scheduled"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    report = _audit(db, receipts, journal)
    assert report["gates"]["S9"]["status"] == "FAIL"  # type: ignore[index]
    assert report["gates"]["S9"]["issues"] == [  # type: ignore[index]
        "cold start observed without a equity startup catch-up decision"
    ]


def test_declined_catch_up_satisfies_s9(tmp_path: Path) -> None:
    """``already_invested`` at the cold start is a legitimate skip, not a gap."""
    db, receipts, journal = _fixture(tmp_path)
    receipt_path = receipts / "eq-cold-1.v2.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["origin"] = "scheduled"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    with sqlite3.connect(journal) as con:
        con.execute(
            "INSERT INTO operational_events VALUES ('no-trade-cold','portfolio_no_trade','equity','paper',?,NULL,?)",
            (
                SESSION_STARTS["session-1"].isoformat(),
                json.dumps({"reason": "already_invested"}),
            ),
        )
    report = _audit(db, receipts, journal)
    assert report["status"] == "PASS", report
    assert report["counts"]["startup_catch_up_required"] is False  # type: ignore[index]


def test_startup_reconciliation_failure_resolved_by_a_later_clean_run_passes(tmp_path: Path) -> None:
    """A one-off F05 startup failure must not fail every later window."""
    db, receipts, journal = _fixture(tmp_path)
    with sqlite3.connect(journal) as con:
        con.execute("DELETE FROM operational_events WHERE kind='reconciliation_recovered'")
        con.execute(
            "INSERT INTO operational_events VALUES "
            "('startup-failure','reconciliation_failure','equity','paper',?,NULL,'{}')",
            (SESSION_STARTS["session-2"].isoformat(),),
        )
    report = _audit(db, receipts, journal)
    assert report["status"] == "PASS", report
    assert report["counts"]["reconciliation_failures"] == 1  # type: ignore[index]


def test_unreconciled_incident_still_fails_s8(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    with sqlite3.connect(journal) as con:
        con.execute("DELETE FROM operational_events WHERE kind='reconciliation_succeeded'")
        con.execute(
            "INSERT INTO operational_events VALUES "
            "('startup-failure','reconciliation_failure','equity','paper',?,NULL,'{}')",
            (SESSION_STARTS["session-2"].isoformat(),),
        )
    report = _audit(db, receipts, journal)
    assert report["gates"]["S8"]["status"] == "FAIL"  # type: ignore[index]


def test_a_real_full_allocation_buy_is_not_an_admission_violation(tmp_path: Path) -> None:
    """Regression: S7 used the library defaults (0.6 gross / 0.4 cash)."""
    db, receipts, journal = _fixture(tmp_path)
    with sqlite3.connect(db) as con:
        headroom = con.execute(
            "SELECT gross_headroom, cash_headroom FROM portfolio_buy_admission_observations "
            "WHERE client_order_id='order-0'"
        ).fetchone()
    assert headroom == (0.98 * EQUITY, 0.98 * EQUITY)
    report = _audit(db, receipts, journal)
    assert report["gates"]["S7"]["status"] == "PASS", report  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutation", "gate"),
    [
        ("global_nine", "S5"),
        ("duplicate_order", "S4"),
        ("orphan_fill", "S3"),
        ("wrong_strategy", "S1"),
        ("non_equity_decision", "S1"),
        ("live", "S12"),
        ("ambiguous", "S3"),
        ("buy_over_capacity", "S7"),
        ("forged_cash_headroom", "S7"),
        ("missing_receipt", "S10"),
        ("missing_reconciliation", "S8"),
    ],
)
def test_independent_invalid_states_fail(tmp_path: Path, mutation: str, gate: str) -> None:
    db, receipts, journal = _fixture(tmp_path)
    if mutation == "global_nine":
        path = receipts / "eq-scheduled-3.v2.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["capacity_snapshot"]["effective_global_slot_claims"] = 9
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "duplicate_order":
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO portfolio_orders VALUES "
                "('order-duplicate','eq-scheduled-3','equity','QLD','buy',1,0,'submitted','2026-09-03T20:00:00+00:00')"
            )
    elif mutation == "orphan_fill":
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO portfolio_fills VALUES ('orphan','missing-order')")
    elif mutation == "wrong_strategy":
        with sqlite3.connect(db) as con:
            con.execute("UPDATE portfolio_decisions SET strategy_name='wrong' WHERE decision_id='eq-scheduled-3'")
    elif mutation == "non_equity_decision":
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE portfolio_decisions SET market='crypto', strategy_name='crypto_low_variance_hysteresis_v1' "
                "WHERE decision_id='eq-scheduled-3'"
            )
    elif mutation == "live":
        with sqlite3.connect(journal) as con:
            con.execute(
                "INSERT INTO operational_events VALUES "
                "('live','broker_submit','equity','live','2026-09-03T20:00:00+00:00',NULL,'{}')"
            )
    elif mutation == "ambiguous":
        with sqlite3.connect(db) as con:
            con.execute("UPDATE portfolio_orders SET status='reconciliation_required' WHERE client_order_id='order-0'")
    elif mutation == "buy_over_capacity":
        # More admitted than the smaller of the two headrooms can carry.
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE portfolio_buy_admission_observations SET admitted_batch_notional=? "
                "WHERE client_order_id='order-0'",
                (CASH_HEADROOM + 1.0,),
            )
    elif mutation == "forged_cash_headroom":
        # Cash headroom beyond the configured 0.02 buffer: a real BUY violation.
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE portfolio_buy_admission_observations SET cash_headroom=? WHERE client_order_id='order-0'",
                (CASH_HEADROOM + 1.0,),
            )
    elif mutation == "missing_receipt":
        (receipts / "eq-scheduled-3.v2.json").unlink()
    elif mutation == "missing_reconciliation":
        with sqlite3.connect(journal) as con:
            con.execute("DELETE FROM operational_events WHERE kind='reconciliation_succeeded'")
    report = _audit(db, receipts, journal)
    assert report["status"] == "FAIL"
    assert report["gates"][gate]["status"] == "FAIL", report  # type: ignore[index]


def test_naive_boundaries_are_rejected(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    with pytest.raises(ValueError, match="UTC offset"):
        auditor.audit(
            db,
            receipts,
            ROOT / "config/settings.yaml",
            journal,
            start=datetime(2026, 9, 1) + timedelta(hours=12),
        )
