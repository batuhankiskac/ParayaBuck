#!/usr/bin/env python3
"""Frozen H06 structural rules and fail-closed upstream-blocked artifacts.

The module intentionally exposes only row-level structural decisions.  It has
no score, ranking, return, portfolio, or outcome calculation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ACQUISITION = REPO / "data/research_manifests/h06_full_official_acquisition_2026_09_14.json"
MANIFEST = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
OWNER_MANIFEST = REPO / "data/research_manifests/h06_owner_year_classification_2026_09_14.json"
RECEIPT = REPO / "data/research_receipts/h06_structural_admission_receipt_2026_09_14.json"
HOLDOUT = REPO / "data/research_receipts/h06_true_holdout_signal_custody_2026_09_14.json"
STATUS = "H06_FULL_ACQUISITION_INCOMPLETE"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def nested_self_hash(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("integrity", {})["self_sha256"] = nested_self_hash(value)
    return value


def verify_seal(value: dict[str, Any]) -> None:
    if value.get("integrity", {}).get("self_sha256") != nested_self_hash(value):
        raise ValueError("self_hash_mismatch")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(UTC)


def active_version(versions: Iterable[dict[str, Any]], at: str) -> dict[str, Any] | None:
    """Select the latest visible resolved lineage version without rewriting history."""
    cutoff = parse_utc(at)
    eligible = [row for row in versions if row.get("lineage_resolved") is True and parse_utc(str(row["available_at"])) <= cutoff]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (parse_utc(str(row["available_at"])), str(row["accession"])))


def classify_owner_year(rows: Iterable[dict[str, Any]], owner_cik: str, year: int, cutoff: str) -> dict[str, Any]:
    cutoff_time = parse_utc(cutoff)
    months: dict[int, set[int]] = {prior: set() for prior in range(year - 3, year)}
    ids: list[str] = []
    for row in rows:
        transaction_date = date.fromisoformat(str(row["transaction_date"]))
        if str(row["reporting_owner_cik"]) != owner_cik:
            continue
        if row.get("non_derivative") is not True or row.get("transaction_code") not in {"P", "S"}:
            continue
        if row.get("lineage_status") not in {"ORIGINAL_ACTIVE", "AMENDED_VERSION_ACTIVE"}:
            continue
        if transaction_date.year not in months or parse_utc(str(row["available_at"])) >= cutoff_time:
            continue
        months[transaction_date.year].add(transaction_date.month)
        ids.append(str(row["row_id"]))
    ordered_sets = [months[prior] for prior in range(year - 3, year)]
    if any(not values for values in ordered_sets):
        classification = "NON_PARTITIONABLE"
        common: set[int] = set()
    else:
        common = set.intersection(*ordered_sets)
        classification = "ROUTINE" if common else "OPPORTUNISTIC"
    return {
        "reporting_owner_cik": owner_cik,
        "year": year,
        "counts": {str(prior): sum(date.fromisoformat(str(row["transaction_date"])).year == prior and str(row["reporting_owner_cik"]) == owner_cik and row.get("row_id") in ids for row in rows) for prior in range(year - 3, year)},
        "month_sets": {str(prior): sorted(months[prior]) for prior in range(year - 3, year)},
        "common_month_intersection": sorted(common),
        "classification": classification,
        "classification_cutoff": cutoff,
        "input_transaction_ids": sorted(ids),
        "input_sha256": hashlib.sha256(canonical(sorted(ids))).hexdigest(),
    }


def open_private_status(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    private = ("private purchase", "privately negotiated", "private transaction")
    explicit_open = ("open market purchase", "open-market purchase", "purchased in the open market")
    if any(term in normalized for term in private):
        return "PRIVATE_PURCHASE_EXPLICIT"
    if any(term in normalized for term in explicit_open):
        return "OPEN_MARKET_EXPLICIT"
    return "P_PURCHASE_AMBIGUOUS"


def ten_b5_status(transaction_date: str, disclosure: bool | None, official_text: str = "") -> str:
    after_compliance = date.fromisoformat(transaction_date) >= date(2023, 4, 1)
    if after_compliance:
        if disclosure is True:
            return "EXCLUDE_10B5_1_CHECKED"
        if disclosure is False:
            return "PASS_POST_COMPLIANCE_EXPLICIT_FALSE"
        return "EXCLUDE_POST_COMPLIANCE_DISCLOSURE_MISSING"
    normalized = " ".join(official_text.casefold().split())
    if "not pursuant to a rule 10b5-1" in normalized or "not made under a rule 10b5-1" in normalized:
        return "PASS_EXPLICIT_OFFICIAL_NON_PLAN_EVIDENCE"
    return "10B5_1_DISCLOSURE_NOT_REQUIRED_AT_THE_TIME"


def finite_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


PRIMARY_ORDER = (
    ("lineage", "EXCLUDE_LINEAGE"),
    ("nonderivative", "EXCLUDE_DERIVATIVE"),
    ("purchase_code", "EXCLUDE_NOT_PURCHASE_CODE"),
    ("acquired", "EXCLUDE_NOT_ACQUIRED"),
    ("direct", "EXCLUDE_INDIRECT"),
    ("valid_shares", "EXCLUDE_INVALID_SHARES"),
    ("valid_price", "EXCLUDE_INVALID_PRICE"),
    ("owner_opportunistic", "EXCLUDE_OWNER_ROUTINE"),
    ("identity", "EXCLUDE_IDENTITY"),
    ("timing", "EXCLUDE_TIMING"),
    ("ten_b5", "EXCLUDE_10B5_1"),
    ("open_market", "EXCLUDE_OPEN_PRIVATE_AMBIGUOUS"),
    ("denominator", "EXCLUDE_DENOMINATOR"),
)


def signal_gate(row: dict[str, Any]) -> dict[str, Any]:
    owner_class = row.get("owner_classification")
    open_status = row.get("open_private_status")
    gates = {
        "lineage": row.get("lineage_status") in {"ORIGINAL_ACTIVE", "AMENDED_VERSION_ACTIVE"},
        "nonderivative": row.get("non_derivative") is True,
        "purchase_code": row.get("transaction_code") == "P",
        "acquired": row.get("acquired_disposed") == "A",
        "direct": row.get("ownership_form") == "D",
        "valid_shares": finite_positive(row.get("shares")),
        "valid_price": finite_positive(row.get("price")),
        "owner_opportunistic": owner_class == "OPPORTUNISTIC",
        "identity": row.get("identity_status") == "IDENTITY_EXACT",
        "timing": bool(row.get("accepted_at")) and row.get("visible_by_decision") is True,
        "ten_b5": str(row.get("ten_b5_status", "")).startswith("PASS_"),
        "open_market": open_status == "OPEN_MARKET_EXPLICIT",
        "denominator": row.get("denominator_status") == "EXACT",
    }
    primary = None
    for gate, exclusion in PRIMARY_ORDER:
        if gates[gate]:
            continue
        if gate == "owner_opportunistic" and owner_class == "NON_PARTITIONABLE":
            primary = "EXCLUDE_OWNER_NON_PARTITIONABLE"
        elif gate == "open_market" and open_status == "PRIVATE_PURCHASE_EXPLICIT":
            primary = "EXCLUDE_PRIVATE"
        else:
            primary = exclusion
        break
    return {"gate_statuses": gates, "admission_status": "STRUCTURALLY_ADMITTED_H06_TRANSACTION" if primary is None else "EXCLUDED", "primary_exclusion": primary}


def exact_identity(*, cik_match: bool, class_count: int, symbol_match: bool, common_equity: bool, lifetime_count: int) -> str:
    if not common_equity:
        return "NON_COMMON_EQUITY"
    if class_count == 0 or lifetime_count == 0:
        return "IDENTITY_MISSING"
    if not cik_match or class_count != 1 or not symbol_match or lifetime_count != 1:
        return "IDENTITY_AMBIGUOUS"
    return "IDENTITY_EXACT"


def select_shares_fact(facts: Iterable[dict[str, Any]], decision: str) -> dict[str, Any] | None:
    cutoff = parse_utc(decision)
    eligible = [row for row in facts if row.get("exact_context") is True and parse_utc(str(row["available_at"])) <= cutoff and date.fromisoformat(str(row["instant"])) <= cutoff.date()]
    if not eligible:
        return None
    latest_key = max((str(row["instant"]), str(row["available_at"])) for row in eligible)
    latest = [row for row in eligible if (str(row["instant"]), str(row["available_at"])) == latest_key]
    values = {str(row["shares"]) for row in latest}
    return latest[0] if len(values) == 1 else None


def denominator_status(shares: Any, raw_close: Any, *, intervening_split: bool) -> str:
    if intervening_split:
        return "SPLIT_INTERVENING_BLOCKED"
    if not finite_positive(shares):
        return "MISSING_SHARES"
    if not finite_positive(raw_close):
        return "MISSING_RAW_PRICE"
    return "EXACT"


def map_to_decisions(available_session: str, decisions: Iterable[dict[str, Any]]) -> list[str]:
    result = []
    for decision in decisions:
        prior = list(decision["prior_21_sessions"])
        if available_session in prior and available_session <= str(decision["decision_session"]):
            result.append(str(decision["decision_session"]))
    return result


def blocked_artifacts() -> dict[Path, dict[str, Any]]:
    blockers = [
        "SEC_USER_AGENT_WITH_OPERATOR_APPROVED_CONTACT_NOT_AVAILABLE",
        "SEC_OFFICIAL_QUARTERLY_PACKAGES_2019Q1_THROUGH_2026Q1_NOT_ACQUIRED",
        "JULY_2026_OFFICIAL_INDEX_AND_ARCHIVE_RECONCILIATION_NOT_ACQUIRED",
    ]
    base = {"status": STATUS, "cutoff": "2026-07-31T23:59:59Z", "remaining_blockers": blockers, "economic_boundary": "NO_H06_SCORE_OR_OUTCOME_ACCESS"}
    zero_years = {str(year): {"owner_year_total": 0, "routine": 0, "opportunistic": 0, "non_partitionable": 0} for year in range(2022, 2027)}
    zero_gates = {key: 0 for key in ("transaction_universe_2022_plus", "nonderivative", "purchase_code_p", "acquired_a", "direct_d", "valid_numeric", "opportunistic", "open_market_explicit", "ten_b5_pass", "identity_exact", "denominator_exact", "structurally_admitted")}
    return {
        MANIFEST: seal({**base, "schema": "h06_full_structural_admission_v1", "upstream_acquisition_status": STATUS, "structural_admission_performed": False, "signal_gate_counts": zero_gates, "primary_exclusions": {}, "decision_map": {"monthly_decisions": 0, "mappings": 0}, "deterministic_outputs": {"owner_classification": None, "structural_admission": None, "decision_map": None}}),
        OWNER_MANIFEST: seal({**base, "schema": "h06_owner_year_classification_v1", "classification_performed": False, "years": zero_years, "routine_history": {"p_rows": 0, "s_rows": 0, "qualifying_rows": 0, "late_not_visible_exclusions": 0, "unresolved_lineage_exclusions": 0}}),
        RECEIPT: seal({**base, "schema": "h06_structural_admission_receipt_v1", "admission_rows": 0, "local_artifacts": [], "reason": "upstream official SEC acquisition incomplete"}),
        HOLDOUT: seal({**base, "schema": "h06_true_holdout_signal_custody_v1", "custody_status": "TRUE_HOLDOUT_SIGNAL_SIDE_BLOCKED_NO_ECONOMIC_ACCESS", "signal_side_rows_processed": 0, "economics_accessed": False, "outcome_fields_present": False}),
    }


def write_blocked_artifacts() -> None:
    for path, value in blocked_artifacts().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def verify_tracked_artifacts() -> None:
    acquisition = json.loads(ACQUISITION.read_text())
    verify_seal(acquisition)
    if acquisition["status"] != STATUS:
        raise ValueError("unexpected_upstream_status")
    for path in (MANIFEST, OWNER_MANIFEST, RECEIPT, HOLDOUT):
        value = json.loads(path.read_text())
        verify_seal(value)
        if value["status"] != STATUS:
            raise ValueError(f"status_drift:{path.name}")
    print("H06_FULL_STRUCTURAL_ADMISSION_BLOCKER_VERIFIED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-blocker", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.write_blocker:
        write_blocked_artifacts()
    if args.verify:
        verify_tracked_artifacts()
    if not args.write_blocker and not args.verify:
        parser.error("choose --write-blocker or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
