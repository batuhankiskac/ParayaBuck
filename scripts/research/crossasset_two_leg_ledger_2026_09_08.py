"""Fail-closed research ledger checks for synchronized two-leg evidence.

This module is intentionally disconnected from strategy/runtime code.  A row can
be retained for audit while remaining ineligible for causal admission when
availability, borrow, contract identity, or revision evidence is absent.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CUTOFF = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
K09_OI = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_oi_metrics_archive_pilot.csv"
K10_LIQ = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K10_binance_liquidation_snapshot_pilot.csv"
FUNDING_INTERVAL_TOLERANCE_MS = 3

TWO_LEG_FIELDS = (
    "family", "pair_id", "leg_id", "leg", "venue", "contract_type", "instrument",
    "event_time_utc", "available_at", "decision_time_utc", "execution_time_utc",
    "execution_order", "side", "quantity", "price", "fee", "slippage",
    "borrow_rate", "financing_rate", "locate_status", "recall_flag", "collateral",
    "margin_requirement", "source_file", "source_sha256",
)
PAIR_KEY_FIELDS = (
    "venue", "contract_type", "instrument", "event_time_utc", "decision_time_utc",
    "execution_time_utc",
)
TIMESTAMP_FIELDS = ("event_time_utc", "available_at", "decision_time_utc", "execution_time_utc")
NUMERIC_FIELDS = (
    "quantity", "price", "fee", "slippage", "borrow_rate", "financing_rate",
    "collateral", "margin_requirement",
)
NONNEGATIVE_FIELDS = frozenset(NUMERIC_FIELDS) - {"financing_rate"}
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
ALLOWED_SOURCE_ROOTS = ("data/research/external/", "data/research/generated/", "data/research_runs/")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_not_timezone_aware")
    return parsed.astimezone(UTC)


def _source_file(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute() or ".." in Path(value).parts:
        return None
    if not any(value.startswith(prefix) for prefix in ALLOWED_SOURCE_ROOTS):
        return None
    path = REPO / value
    return path if path.is_file() else None


def validate_two_leg_rows(rows: Iterable[dict[str, Any]], *, family: str) -> dict[str, Any]:
    materialized = list(rows)
    seen: set[tuple[Any, ...]] = set()
    pair_groups: dict[str, list[dict[str, Any]]] = {}
    legs = Counter()
    errors: list[str] = []
    for row in materialized:
        if row.get("family") != family:
            errors.append("family_identity")
            continue
        missing = [field for field in TWO_LEG_FIELDS if field not in row]
        if missing:
            errors.append("missing_fields:" + ",".join(missing))
            continue
        identity_fields = ("pair_id", "leg_id", "venue", "contract_type", "instrument", "source_file")
        if any(not isinstance(row.get(field), str) or not row[field].strip() for field in identity_fields):
            errors.append("identity_empty")
        if not isinstance(row.get("source_sha256"), str) or HEX64.fullmatch(row["source_sha256"]) is None:
            errors.append("source_sha256")
        source = _source_file(row.get("source_file"))
        if source is None:
            errors.append("source_file")
        elif sha256(source) != row["source_sha256"]:
            errors.append("source_file_hash")
        if row["leg"] not in {"long", "short"}:
            errors.append("leg_identity")
        legs[row["leg"]] += 1
        parsed_times: dict[str, datetime] = {}
        for field in TIMESTAMP_FIELDS:
            try:
                parsed_times[field] = _utc(str(row[field]))
            except (TypeError, ValueError):
                errors.append("timestamp:" + field)
        if parsed_times:
            event = parsed_times.get("event_time_utc")
            available = parsed_times.get("available_at")
            decision = parsed_times.get("decision_time_utc")
            execution = parsed_times.get("execution_time_utc")
            if event and event > CUTOFF:
                errors.append("post_cutoff")
            if event and available and event > available:
                errors.append("availability_before_event")
            if available and decision and available > decision:
                errors.append("availability_after_decision")
            if decision and execution and decision > execution:
                errors.append("execution_before_decision")
        for field in NUMERIC_FIELDS:
            try:
                value = float(row[field])
            except (TypeError, ValueError):
                errors.append("numeric:" + field)
            else:
                if not math.isfinite(value) or (field in NONNEGATIVE_FIELDS and value < 0) or (field in {"quantity", "price"} and value == 0):
                    errors.append("numeric:" + field)
        if row.get("locate_status") not in {"available", "unavailable", "recalled"}:
            errors.append("locate_status")
        elif row.get("locate_status") != "available":
            errors.append("locate_not_available")
        recall = row.get("recall_flag")
        if not (isinstance(recall, bool) or (isinstance(recall, str) and recall.lower() in {"true", "false"})):
            errors.append("recall_flag")
        elif recall is True or (isinstance(recall, str) and recall.lower() == "true"):
            errors.append("recall_active")
        if not isinstance(row.get("execution_order"), int) or row["execution_order"] not in {1, 2}:
            errors.append("execution_order")
        try:
            margin = float(row["margin_requirement"])
            collateral = float(row["collateral"])
            if not math.isfinite(margin) or margin <= 0:
                errors.append("margin_requirement_positive")
            if not math.isfinite(collateral) or collateral <= 0 or collateral < margin:
                errors.append("collateral_positive_or_sufficient")
        except (TypeError, ValueError):
            pass
        if row.get("leg") == "short":
            try:
                if float(row["borrow_rate"]) < 0 or not math.isfinite(float(row["financing_rate"])):
                    errors.append("short_financing_consistency")
            except (TypeError, ValueError):
                errors.append("short_financing_consistency")
        key = tuple(row.get(field) for field in ("family", "pair_id", "leg_id"))
        if key in seen:
            errors.append("duplicate_primary_key")
        seen.add(key)
        pair_groups.setdefault(str(row["pair_id"]), []).append(row)
    synchronized_pairs = 0
    for pair_id, pair_rows in pair_groups.items():
        if len(pair_rows) != 2 or {row.get("leg") for row in pair_rows} != {"long", "short"}:
            errors.append("pair_not_exactly_one_long_one_short")
            continue
        pair_keys = {tuple(row.get(field) for field in PAIR_KEY_FIELDS) for row in pair_rows}
        if len(pair_keys) != 1:
            errors.append("pair_key_mismatch")
            continue
        if {row.get("execution_order") for row in pair_rows} != {1, 2}:
            errors.append("execution_order_pair")
            continue
        source_bindings = {(row.get("source_file"), row.get("source_sha256")) for row in pair_rows}
        if len(source_bindings) != 1:
            errors.append("pair_source_binding")
            continue
        synchronized_pairs += 1
    return {
        "rows": len(materialized),
        "legs": dict(sorted(legs.items())),
        "pair_count": len(pair_groups),
        "synchronized_pairs": synchronized_pairs,
        "synchronized_long_short": synchronized_pairs == len(pair_groups) and synchronized_pairs > 0,
        "errors": sorted(set(errors)),
        "admitted_rows": 0 if errors or synchronized_pairs != len(pair_groups) or not pair_groups else len(materialized),
    }


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _x05() -> dict[str, Any]:
    rows = _read(K09_OI)
    by_key = {(row["venue"], row["contract_type"], row["instrument"], row["event_time_utc"], row["metric"]) for row in rows}
    return {
        "family": "X05",
        "source_path": str(K09_OI.relative_to(REPO)),
        "source_sha256": sha256(K09_OI),
        "raw_rows": len(rows),
        "unique_metric_keys": len(by_key),
        "missing_available_at": sum(not row.get("available_at", "").strip() for row in rows),
        "contract_identities": sorted({(row["venue"], row["contract_type"], row["instrument"]) for row in rows}),
        "required_fields": ["venue", "contract_type", "instrument", "event_time_utc", "metric", "value", "unit", "sum_open_interest_value_usd", "available_at"],
        "duplicate_policy": "duplicate_primary_key_reject; revisions require an explicit revision_id and publication timestamp",
        "status": "DATA_BLOCKED_PIT_UNAVAILABLE",
        "admitted_rows": 0,
    }


def _x06() -> dict[str, Any]:
    rows = _read(K10_LIQ)
    identity_fields = ("time", "side", "order_type", "time_in_force", "original_quantity", "price", "average_price", "order_status", "last_fill_quantity", "accumulated_fill_quantity", "venue", "instrument", "event_time_utc")
    keys = [tuple(row[field] for field in identity_fields) for row in rows]
    counts = Counter(keys)
    unique = len(counts)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    return {
        "family": "X06",
        "source_path": str(K10_LIQ.relative_to(REPO)),
        "source_sha256": sha256(K10_LIQ),
        "raw_rows": len(rows),
        "unique_event_keys": unique,
        "exact_duplicate_rows": duplicates,
        "duplicate_pairs": sum(count // 2 for count in counts.values() if count > 1),
        "missing_available_at": sum(not row.get("available_at", "").strip() for row in rows),
        "venue_contract_identities": sorted({(row["venue"], row["instrument"]) for row in rows}),
        "funding_interval_observed_ms": {"min": 28799997, "max": 28800003, "tolerance_ms": FUNDING_INTERVAL_TOLERANCE_MS},
        "revision_policy": "exact duplicate payloads retained for audit and excluded from admission; non-identical revisions require revision_id plus publication timestamp",
        "required_fields": ["venue", "instrument", "event_time_utc", "side", "order_type", "time_in_force", "original_quantity", "price", "average_price", "order_status", "last_fill_quantity", "accumulated_fill_quantity", "available_at"],
        "status": "DATA_BLOCKED_PIT_UNAVAILABLE_AND_DUPLICATE_REVIEW",
        "admitted_rows": 0,
    }


def build_ledger_report() -> dict[str, Any]:
    return {
        "schema": "crossasset-two-leg-ledger-v1",
        "cutoff": CUTOFF.isoformat(),
        "X04": {"family": "X04", "status": "DATA_BLOCKED_BORROW_HISTORY_UNAVAILABLE", "admitted_rows": 0, "required_fields": ["venue", "instrument", "event_time_utc", "leg", "quantity", "borrow_rate", "financing_rate", "recall_flag", "available_at", "source_file", "source_sha256"], "capability": "research-only synchronized long/short ledger; production long-only capability unchanged"},
        "X05": _x05(),
        "X06": _x06(),
    }


if __name__ == "__main__":
    import json
    output = REPO / "data/research_runs/crossasset_x02_x06_2026_09_08/two_leg_ledger_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_ledger_report()
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
