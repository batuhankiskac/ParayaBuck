#!/usr/bin/env python3
"""Read-only, fail-closed audit of the PAPER observation ledger.

ParayaBuck is equity-only: only equity decisions are expected and any other
market's decision fails S1. Gates S6 (crypto slots) and S11 (crypto universe
scanner) were retired with crypto support; the remaining gate numbers are kept
stable so reports stay comparable.

Receipts are decision snapshots. SQLite portfolio tables remain authoritative for
orders, fills and their final statuses. This program never imports a broker.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "equity": ("equity_leveraged_trend_v1", 0.98),
}
TERMINAL = {"filled", "fill", "rejected", "canceled", "cancelled", "expired", "done_for_day"}
AMBIGUOUS = {"reconciliation_required", "manual_intervention_required"}
KNOWN = (
    TERMINAL
    | AMBIGUOUS
    | {
        "submitting",
        "submitted",
        "accepted",
        "partially_filled",
        "partial_fill",
        "new",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "accepted_for_bidding",
        "replaced",
        "stopped",
        "suspended",
        "calculated",
    }
)
GATES = tuple(f"S{index}" for index in range(1, 13) if index not in {6, 11})


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"missing database: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        raise ValueError(f"missing table: {table}")
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def _receipt_files(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError(f"missing receipt directory: {root}")
    receipts = []
    for path in sorted(root.glob("*.v2.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["_file"] = path.name
        receipts.append(receipt)
    return receipts


def audit(
    db: Path,
    receipts_dir: Path,
    config: Path,
    journal: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Return gate evidence; no database, receipt or configuration writes."""
    issues: dict[str, list[str]] = {gate: [] for gate in GATES}

    def flag(gate: str, message: str) -> None:
        issues[gate].append(message)

    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    expected_config = (
        settings["runtime"]["mode"] == "paper"
        and settings["live"]["enabled"] is False
        and settings["live"]["hard_dollar_cap"] == 0
        and settings["risk"]["max_positions"] == 8
        and settings["risk"]["max_gross_exposure_pct"] == 0.98
        and settings["risk"]["cash_buffer_pct"] == 0.02
        and all(
            settings["strategy"][market]["name"] == name
            and settings["strategy"][market]["maximum_allocation"] == allocation
            for market, (name, allocation) in EXPECTED.items()
        )
    )
    if not expected_config:
        flag("S1", "current configuration differs from frozen PAPER identity")
    if settings["live"]["enabled"] or settings["runtime"]["mode"] == "live":
        flag("S12", "current configuration enables LIVE")

    with _readonly(db) as connection:
        sessions = _rows(connection, "portfolio_runtime_sessions")
        decisions = _rows(connection, "portfolio_decisions")
        orders = _rows(connection, "portfolio_orders")
        fills = _rows(connection, "portfolio_fills")
        events = _rows(connection, "portfolio_execution_events")
        rebalances = _rows(connection, "portfolio_rebalance_executions")
        admissions = _rows(connection, "portfolio_buy_admission_observations")
    receipts = _receipt_files(receipts_dir)
    journal_events: list[dict[str, Any]] = []
    if journal is not None:
        with _readonly(journal) as connection:
            journal_events = _rows(connection, "operational_events")
    else:
        flag("S2", "no operational journal supplied for warm/eligible decision-gap evidence")
        flag("S8", "no operational journal supplied for reconciliation evidence")

    if start is not None or end is not None:
        if (start is not None and start.tzinfo is None) or (end is not None and end.tzinfo is None):
            raise ValueError("Observation boundaries must include a UTC offset")

        def within(value: object) -> bool:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (start is None or stamp >= start) and (end is None or stamp <= end)

        decisions = [row for row in decisions if within(row["decision_timestamp"])]
        selected_decisions = {row["decision_id"] for row in decisions}
        sessions = [row for row in sessions if row["session_id"] in {item["session_id"] for item in decisions}]
        orders = [
            row
            for row in orders
            if row["decision_id"] in selected_decisions or (row.get("created_at") and within(row["created_at"]))
        ]
        selected_orders = {row["client_order_id"] for row in orders}
        fills = [
            row
            for row in fills
            if row["client_order_id"] in selected_orders or (row.get("occurred_at") and within(row["occurred_at"]))
        ]
        events = [
            row
            for row in events
            if row["client_order_id"] in selected_orders or (row.get("received_at") and within(row["received_at"]))
        ]
        rebalances = [
            row
            for row in rebalances
            if row["decision_id"] in selected_decisions or (row.get("updated_at") and within(row["updated_at"]))
        ]
        admissions = [
            row
            for row in admissions
            if row["client_order_id"] in selected_orders or (row.get("observed_at") and within(row["observed_at"]))
        ]
        receipts = [row for row in receipts if within(row["decision_timestamp"])]
        journal_events = [row for row in journal_events if within(row["occurred_at"])]

    if not sessions or not decisions or not receipts:
        flag("S2", "no complete forward observation")
        flag("S10", "no V2 receipt coverage")
    if any(session["mode"] != "paper" for session in sessions):
        flag("S1", "non-PAPER runtime session observed")
        flag("S12", "non-PAPER runtime session observed")
    session_ids = {row["session_id"] for row in sessions}
    decision_ids = {row["decision_id"] for row in decisions}
    order_ids = {row["client_order_id"] for row in orders}
    admissions_by_order: dict[str, list[dict[str, Any]]] = {}
    for admission in admissions:
        admissions_by_order.setdefault(admission["client_order_id"], []).append(admission)
    by_receipt: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        by_receipt.setdefault(str(receipt.get("decision_id")), []).append(receipt)
    if any(len(group) != 1 for group in by_receipt.values()):
        flag("S4", "duplicate decision receipt identity")
    digest_set = {row["configuration_digest"] for row in decisions + sessions}
    if len(digest_set) != 1:
        flag("S1", "configuration digest changed during observation")
    for decision in decisions:
        market = decision["market"]
        decision_id = decision["decision_id"]
        if decision["session_id"] not in session_ids:
            flag("S3", f"orphan decision {decision_id}")
        if market not in EXPECTED or decision["strategy_name"] != EXPECTED.get(market, (None,))[0]:
            flag("S1", f"strategy mismatch at {decision_id}")
        group = by_receipt.get(decision_id, [])
        if len(group) != 1:
            flag("S10", f"missing or duplicate V2 receipt for {decision_id}")
            continue
        receipt = group[0]
        required = (
            "receipt_id",
            "decision_timestamp",
            "origin",
            "market",
            "mode",
            "strategy",
            "configuration_digest",
            "universe",
            "account_before",
            "capacity_snapshot",
            "target_state",
            "planned_orders",
            "submission_snapshot",
            "execution_confirmation",
            "database_link",
        )
        if receipt.get("schema_version") != 2 or any(key not in receipt for key in required):
            flag("S10", f"incomplete V2 receipt for {decision_id}")
            continue
        if (
            receipt["receipt_id"] != decision_id
            or receipt["market"] != market
            or receipt["decision_timestamp"] != decision["decision_timestamp"]
        ):
            flag("S10", f"receipt/decision identity conflict at {decision_id}")
        if receipt["origin"] not in {"scheduled", "startup_catch_up"}:
            flag("S9", f"invalid origin at {decision_id}")
        if receipt["mode"] != "paper":
            flag("S1", f"non-PAPER receipt at {decision_id}")
            flag("S12", f"non-PAPER receipt at {decision_id}")
        strategy = receipt["strategy"]
        if (
            strategy.get("name") != decision["strategy_name"]
            or strategy.get("maximum_allocation") != EXPECTED.get(market, (None, None))[1]
            or receipt["configuration_digest"] != decision["configuration_digest"]
        ):
            flag("S1", f"receipt identity mismatch at {decision_id}")
        capacity = receipt["capacity_snapshot"]
        global_claims = capacity.get("effective_global_slot_claims")
        if not isinstance(global_claims, int):
            flag("S5", f"global slot evidence unavailable at {decision_id}")
        elif global_claims > 8 or capacity.get("global_slot_cap") != 8:
            flag("S5", f"global slot breach at {decision_id}: {global_claims}")
        if receipt["target_state"].get("kind") != "INTENDED_POST_RISK" or any(
            order.get("kind") != "INTENDED" for order in receipt["planned_orders"]
        ):
            flag("S10", f"intent labeling missing at {decision_id}")
        if receipt["execution_confirmation"].get("confirmed_positions_after") is not None:
            flag("S10", f"projected position presented as confirmed at {decision_id}")
    for receipt in receipts:
        if receipt.get("decision_id") not in decision_ids:
            flag("S3", f"orphan receipt {receipt.get('decision_id')}")
    for order in orders:
        key = order["client_order_id"]
        if order["decision_id"] not in decision_ids:
            flag("S3", f"orphan order {key}")
        status = str(order["status"]).lower()
        if status not in KNOWN:
            flag("S3", f"unknown order status {key}: {status}")
        if status in AMBIGUOUS:
            flag("S3", f"unresolved ambiguous order {key}")
        quantity = float(order["quantity"])
        cumulative = float(order["cumulative_filled_quantity"])
        if (
            cumulative < 0
            or cumulative > quantity + 1e-7
            or (status in {"filled", "fill"} and cumulative < quantity - 1e-7)
        ):
            flag("S3", f"impossible fill quantity at {key}")
        if status == "rejected" and cumulative > 1e-7:
            flag("S3", f"rejected order has fill at {key}")
        if order["side"] == "buy":
            observations = admissions_by_order.get(key, [])
            submitted_at = order.get("submit_call_started_at")
            if submitted_at:
                observations = [item for item in observations if item.get("observed_at", "") <= submitted_at]
            selected_admission = (
                max(observations, key=lambda item: (item.get("observed_at", ""), item.get("observation_id", "")))
                if observations
                else None
            )
            if selected_admission is None:
                flag("S7", f"BUY has no final admission snapshot: {key}")
            elif (
                selected_admission["decision_id"] != order["decision_id"]
                or selected_admission["gross_headroom"] < -1e-7
                or selected_admission["cash_headroom"] < -1e-7
                or selected_admission["admitted_batch_notional"]
                > min(selected_admission["gross_headroom"], selected_admission["cash_headroom"]) + 1e-6
                or selected_admission["gross_headroom"]
                > max(
                    0.0,
                    0.6 * selected_admission["account_equity"]
                    - selected_admission["realized_gross"]
                    - selected_admission["committed_buy_notional"],
                )
                + 1e-6
                or selected_admission["cash_headroom"]
                > max(
                    0.0,
                    selected_admission["account_cash"]
                    - 0.4 * selected_admission["account_equity"]
                    - selected_admission["committed_buy_notional"],
                )
                + 1e-6
            ):
                flag("S7", f"BUY final admission violated: {key}")
    for admission in admissions:
        if admission["client_order_id"] not in order_ids:
            flag("S7", f"orphan admission {admission['client_order_id']}")
    for fill in fills:
        if fill["client_order_id"] not in order_ids:
            flag("S3", f"orphan fill {fill['activity_id']}")
    for event in events:
        if event["client_order_id"] not in order_ids:
            flag("S3", f"orphan execution event {event['id']}")
    for rebalance in rebalances:
        if rebalance["decision_id"] not in decision_ids:
            flag("S3", f"orphan rebalance {rebalance['decision_id']}")
    if len(order_ids) != len(orders):
        flag("S4", "duplicate client order ID")
    logical = [(row["decision_id"], row["market"], row["symbol"], row["side"]) for row in orders]
    if len(logical) != len(set(logical)):
        flag("S4", "duplicate logical submission")
    for event in journal_events:
        kind = str(event.get("kind", ""))
        if event.get("mode") == "live":
            flag("S12", f"LIVE journal event {event['event_id']}")
        if kind in {"broker_local_drift", "reconciliation_failure"} and not any(
            later.get("kind") == "reconciliation_recovered"
            and later.get("occurred_at", "") > event.get("occurred_at", "")
            for later in journal_events
        ):
            flag("S8", f"unreconciled incident {event['event_id']}")
    counts = Counter((receipt.get("market"), receipt.get("origin")) for receipt in receipts)
    for market in EXPECTED:
        for origin in ("scheduled", "startup_catch_up"):
            if counts[market, origin] == 0:
                flag("S9" if origin == "startup_catch_up" else "S2", f"no {market} {origin} decision observed")
        scheduled = sorted(
            datetime.fromisoformat(str(receipt["decision_timestamp"]))
            for receipt in receipts
            if receipt.get("market") == market and receipt.get("origin") == "scheduled"
        )
        maximum_gap_days = 8
        for before, after in zip(scheduled, scheduled[1:]):
            if (after - before).total_seconds() > maximum_gap_days * 86400:
                flag("S2", f"unexplained {market} scheduled decision gap: {before} to {after}")
    if journal is not None:
        if not any(event.get("kind") == "reconciliation_succeeded" for event in journal_events):
            flag("S8", "no successful reconciliation run observed")
        journal_decisions = {
            event.get("decision_id") for event in journal_events if event.get("kind") == "decision_summary"
        }
        if journal_decisions:
            for decision_id in decision_ids - journal_decisions:
                flag("S2", f"decision lacks operational summary: {decision_id}")
            for decision_id in journal_decisions - decision_ids:
                flag("S2", f"operational decision lacks persisted row: {decision_id}")
        else:
            flag("S2", "no decision-summary journal evidence")
    return {
        "status": "PASS" if all(not issue for issue in issues.values()) else "FAIL",
        "gates": {gate: {"status": "PASS" if not issue else "FAIL", "issues": issue} for gate, issue in issues.items()},
        "counts": {
            "sessions": len(sessions),
            "decisions": len(decisions),
            "orders": len(orders),
            "fills": len(fills),
            "execution_events": len(events),
            "rebalance_executions": len(rebalances),
            "buy_admissions": len(admissions),
            "receipts_v2": len(receipts),
            "decision_classes": {
                f"{market}_{origin}": counts[market, origin]
                for market in EXPECTED
                for origin in ("scheduled", "startup_catch_up")
            },
            "order_statuses": dict(Counter(str(order["status"]) for order in orders)),
            "operational_events": dict(Counter(str(event.get("kind")) for event in journal_events)),
            "restarts_observed": max(0, len(sessions) - 1),
            "reconciliation_runs_observed": sum(
                event.get("kind") == "reconciliation_succeeded" for event in journal_events
            ),
            "reconciliation_failures": sum(event.get("kind") == "reconciliation_failure" for event in journal_events),
            "reconciliation_recoveries": sum(
                event.get("kind") == "reconciliation_recovered" for event in journal_events
            ),
            "ambiguous_submissions": sum(event.get("kind") == "ambiguous_order" for event in journal_events),
            "drift_incidents": sum(event.get("kind") == "broker_local_drift" for event in journal_events),
            "no_order_decisions": sum(
                not any(order["decision_id"] == decision["decision_id"] for order in orders) for decision in decisions
            ),
        },
        "evidence": {
            "db": str(db),
            "receipts": str(receipts_dir),
            "config": str(config),
            "journal": str(journal) if journal else None,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/runtime/parayabuck.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "data/runtime/paper_receipts")
    parser.add_argument("--config", type=Path, default=ROOT / "config/settings.yaml")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--start", type=datetime.fromisoformat, help="V2 observation start including UTC offset")
    parser.add_argument("--end", type=datetime.fromisoformat, help="V2 observation end including UTC offset")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = audit(args.db, args.receipts, args.config, args.journal, args.start, args.end)
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        report = {
            "status": "FAIL",
            "error": str(exc),
            "gates": {gate: {"status": "FAIL", "issues": ["audit evidence unavailable"]} for gate in GATES},
        }
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    print(
        f"PAPER SOAK: {report['status']} | "
        + ", ".join(f"{gate}={result['status']}" for gate, result in report["gates"].items()),
        file=__import__("sys").stderr,
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
