"""The account auditor fails closed on synthetic equity-only PAPER evidence.

ParayaBuck is equity-only, so the clean fixture carries equity decisions only
and a decision for any other market (e.g. a historical crypto row) fails S1.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_paper_soak", ROOT / "scripts/audit_paper_soak.py")
assert spec and spec.loader
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    db = tmp_path / "portfolio.sqlite3"
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    journal = tmp_path / "journal.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE portfolio_runtime_sessions(session_id TEXT, configuration_digest TEXT, mode TEXT);
            CREATE TABLE portfolio_decisions(decision_id TEXT, session_id TEXT, configuration_digest TEXT, decision_timestamp TEXT, market TEXT, strategy_name TEXT);
            CREATE TABLE portfolio_orders(client_order_id TEXT, decision_id TEXT, market TEXT, symbol TEXT, side TEXT, quantity REAL, cumulative_filled_quantity REAL, status TEXT);
            CREATE TABLE portfolio_fills(activity_id TEXT, client_order_id TEXT);
            CREATE TABLE portfolio_execution_events(id INTEGER, client_order_id TEXT);
            CREATE TABLE portfolio_rebalance_executions(decision_id TEXT);
            CREATE TABLE portfolio_buy_admission_observations(client_order_id TEXT, decision_id TEXT, gross_headroom REAL, cash_headroom REAL, admitted_batch_notional REAL, account_equity REAL, account_cash REAL, realized_gross REAL, committed_buy_notional REAL);
            INSERT INTO portfolio_runtime_sessions VALUES ('session-1','config-1','paper');
            INSERT INTO portfolio_runtime_sessions VALUES ('session-2','config-1','paper');
        """)
        decisions = [
            ("eq-scheduled-1", "equity", "scheduled", "equity_leveraged_trend_v1"),
            ("eq-catchup-1", "equity", "startup_catch_up", "equity_leveraged_trend_v1"),
            ("eq-scheduled-2", "equity", "scheduled", "equity_leveraged_trend_v1"),
            ("eq-scheduled-3", "equity", "scheduled", "equity_leveraged_trend_v1"),
            ("eq-catchup-2", "equity", "startup_catch_up", "equity_leveraged_trend_v1"),
            ("eq-scheduled-4", "equity", "scheduled", "equity_leveraged_trend_v1"),
        ]
        statuses = ["filled", None, "rejected", "partially_filled", "canceled", "submitted"]
        for index, (decision_id, market, origin, strategy) in enumerate(decisions):
            stamp = f"2026-09-{index + 1:02d}T00:00:00+00:00"
            con.execute(
                "INSERT INTO portfolio_decisions VALUES (?,?,?,?,?,?)",
                (
                    decision_id,
                    "session-2" if origin == "startup_catch_up" else "session-1",
                    "config-1",
                    stamp,
                    market,
                    strategy,
                ),
            )
            order_id = f"order-{index}"
            status = statuses[index]
            if status:
                con.execute(
                    "INSERT INTO portfolio_orders VALUES (?,?,?,?,?,?,?,?)",
                    (
                        order_id,
                        decision_id,
                        market,
                        "QLD",
                        "buy",
                        1.0,
                        1.0 if status == "filled" else 0.5 if status == "partially_filled" else 0.0,
                        status,
                    ),
                )
                con.execute(
                    "INSERT INTO portfolio_buy_admission_observations VALUES (?,?,?,?,?,?,?,?,?)",
                    (order_id, decision_id, 1000, 1000, 100, 100000, 50000, 1000, 0),
                )
                if status in {"filled", "partially_filled"}:
                    con.execute("INSERT INTO portfolio_fills VALUES (?,?)", (f"fill-{index}", order_id))
                if status == "partially_filled":
                    con.execute("INSERT INTO portfolio_execution_events VALUES (?,?)", (index, order_id))
            payload = {
                "schema_version": 2,
                "receipt_id": decision_id,
                "decision_id": decision_id,
                "decision_timestamp": stamp,
                "origin": origin,
                "market": market,
                "mode": "paper",
                "strategy": {
                    "name": strategy,
                    "version": "1",
                    "maximum_allocation": 0.98,
                },
                "configuration_digest": "config-1",
                "universe": {"version": "universe-1"},
                "account_before": {"cash": 50000, "equity": 100000},
                "capacity_snapshot": {
                    "effective_global_slot_claims": 7,
                    "global_slot_cap": 8,
                },
                "target_state": {"kind": "INTENDED_POST_RISK", "weights": {}},
                "planned_orders": [{"kind": "INTENDED", "client_order_id": order_id}] if status else [],
                "submission_snapshot": [],
                "execution_confirmation": {"confirmed_positions_after": None},
                "database_link": {"decision_table": "portfolio_decisions"},
                "final_buy_admission": {"gross_headroom": 1000, "cash_headroom": 1000} if status else None,
            }
            (receipts / f"{decision_id}.v2.json").write_text(json.dumps(payload), encoding="utf-8")
        con.execute("INSERT INTO portfolio_rebalance_executions VALUES ('eq-catchup-2')")
    with sqlite3.connect(journal) as con:
        con.execute(
            "CREATE TABLE operational_events(event_id TEXT, kind TEXT, market TEXT, mode TEXT, occurred_at TEXT, decision_id TEXT)"
        )
        con.executemany(
            "INSERT INTO operational_events VALUES (?,?,?,?,?,?)",
            [
                ("reconcile", "reconciliation_recovered", None, "paper", "2026-09-01T00:00:00+00:00", None),
                ("reconcile-complete", "reconciliation_succeeded", None, "paper", "2026-09-01T00:00:00+00:00", None),
                *[
                    (
                        f"summary-{decision_id}",
                        "decision_summary",
                        market,
                        "paper",
                        "2026-09-03T00:00:00+00:00",
                        decision_id,
                    )
                    for decision_id, market, _, _ in decisions
                ],
            ],
        )
    return db, receipts, journal


def _audit(db: Path, receipts: Path, journal: Path) -> dict[str, object]:
    return auditor.audit(db, receipts, ROOT / "config/settings.yaml", journal)


def test_clean_equity_only_fixture_passes(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    report = _audit(db, receipts, journal)
    assert report["status"] == "PASS", report
    assert report["counts"]["decision_classes"] == {  # type: ignore[index]
        "equity_scheduled": 4,
        "equity_startup_catch_up": 2,
    }
    assert report["counts"]["reconciliation_runs_observed"] == 1  # type: ignore[index]
    assert report["counts"]["restarts_observed"] == 1  # type: ignore[index]
    assert set(report["gates"]) == {f"S{index}" for index in (1, 2, 3, 4, 5, 7, 8, 9, 10, 12)}  # type: ignore[arg-type]


def test_v2_start_boundary_excludes_historical_v1_decision(tmp_path: Path) -> None:
    db, receipts, journal = _fixture(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO portfolio_runtime_sessions VALUES ('legacy','old-config','paper')")
        con.execute(
            "INSERT INTO portfolio_decisions VALUES ('legacy-decision','legacy','old-config','2026-08-01T00:00:00+00:00','equity','equity_leveraged_trend_v1')"
        )
    (receipts / "2026-08-01_equity_v3.json").write_text('{"legacy": true}', encoding="utf-8")
    report = auditor.audit(
        db,
        receipts,
        ROOT / "config/settings.yaml",
        journal,
        start=datetime.fromisoformat("2026-09-01T00:00:00+00:00"),
    )
    assert report["status"] == "PASS", report
    assert report["counts"]["decisions"] == 6  # type: ignore[index]


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
        ("buy_headroom", "S7"),
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
                "INSERT INTO portfolio_orders VALUES ('order-duplicate','eq-scheduled-3','equity','QLD','buy',1,0,'submitted')"
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
                "INSERT INTO operational_events VALUES ('live','broker_submit','equity','live','2026-09-03',NULL)"
            )
    elif mutation == "ambiguous":
        with sqlite3.connect(db) as con:
            con.execute("UPDATE portfolio_orders SET status='reconciliation_required' WHERE client_order_id='order-0'")
    elif mutation == "buy_headroom":
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE portfolio_buy_admission_observations SET admitted_batch_notional=1001 WHERE client_order_id='order-0'"
            )
    elif mutation == "forged_cash_headroom":
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE portfolio_buy_admission_observations SET cash_headroom=20000 WHERE client_order_id='order-0'"
            )
    elif mutation == "missing_receipt":
        (receipts / "eq-scheduled-3.v2.json").unlink()
    elif mutation == "missing_reconciliation":
        with sqlite3.connect(journal) as con:
            con.execute("DELETE FROM operational_events WHERE kind='reconciliation_succeeded'")
    report = _audit(db, receipts, journal)
    assert report["status"] == "FAIL"
    assert report["gates"][gate]["status"] == "FAIL", report  # type: ignore[index]
