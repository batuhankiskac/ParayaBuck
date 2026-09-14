#!/usr/bin/env python3
"""Outcome-free H06 identity and market-cap bridge contracts.

The module is deliberately offline.  It verifies frozen local inputs, exposes
small deterministic identity/share/split helpers, and writes only structural
research evidence.  It never computes the H06 signal or any economic result.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib.metadata
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas_market_calendars as market_calendars
from pandas.tseries.holiday import USFederalHolidayCalendar

REPO = Path(__file__).resolve().parents[2]
PENDING = ""
NY = ZoneInfo("America/New_York")
BRIDGE_STATUS = "H06_IDENTITY_MARKETCAP_BLOCKED"
PROTOCOL_STATUS = "LATEST_ACCEPTED_REPORTED_SHARES_WOULD_BE_A_PROTOCOL_CHANGE"
PRICE_BASIS_STATUS = "V5_SPLIT_BASIS_NOT_PROVEN"
FUTURE_SPLIT_STATUS = "H06_BLOCKED_FUTURE_SPLIT_NORMALIZATION"

RECOVERY = REPO / "data/research_receipts/h06_local_input_recovery_2026_09_14.json"
MANIFEST = REPO / "data/research_manifests/h06_identity_marketcap_bridge_2026_09_14.json"
TAXONOMY = REPO / "data/research_manifests/h06_security_class_taxonomy_v1_2026_09_14.json"
TIMING = REPO / "data/research_manifests/h06_timing_v2_2026_09_14.json"
PROBE = REPO / "data/research_receipts/h06_identity_marketcap_probe_2026_09_14.json"
REPORT = REPO / "docs/research/h06_identity_marketcap_bridge_2026_09_14.md"

EXPECTED_V5 = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "data/historical/equity_entity_lifetimes_full_v5.csv": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}
EXPECTED_V5_BYTES: dict[str, int | None] = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": 882_354_549,
    "data/historical/equity_entity_lifetimes_full_v5.csv": None,
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": 14_713_370,
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def nested_self_hash(value: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(value)
    payload.setdefault("integrity", {}).pop(field, None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any], field: str) -> dict[str, Any]:
    value.setdefault("integrity", {})[field] = PENDING
    value["integrity"][field] = nested_self_hash(value, field)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_security_class(title: str) -> str:
    text = " ".join(title.casefold().replace("depositary", "depositary ").split())
    if re.search(r"\bwarrants?\b|\bright to purchase\b", text):
        return "WARRANT"
    if any(term in text for term in ("preferred", "preference stock")):
        return "PREFERRED_EQUITY"
    if any(term in text for term in ("american depositary", "depositary share", " ads", "adr")):
        return "ADR_OR_ADS"
    if re.search(r"\bunits?\b", text):
        return "UNIT"
    if any(term in text for term in ("note", "bond", "debenture", "debt")):
        return "NOTE_OR_DEBT"
    if any(term in text for term in ("fund", "etf", "exchange-traded")):
        return "FUND_OR_ETF"
    if any(term in text for term in ("common stock", "common shares", "class a", "class b", "class c capital stock")):
        return "COMMON_EQUITY_EXACT"
    if any(term in text for term in ("stock", "share", "equity")):
        return "AMBIGUOUS_SECURITY_CLASS"
    return "OTHER_NON_COMMON"


def visible_states_as_of(states: list[dict[str, Any]], decision_at: datetime) -> list[dict[str, Any]]:
    return [row for row in states if parse_time(row["available_at"]) <= decision_at]


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timezone_required:{value}")
    return parsed.astimezone(UTC)


def match_v5_interval(
    symbol: str,
    at: datetime,
    lifetimes: list[dict[str, Any]],
    *,
    unresolved_ticker_reuse: bool = False,
) -> dict[str, Any]:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("interval_timestamp_timezone_required")
    if unresolved_ticker_reuse:
        return {"status": "IDENTITY_AMBIGUOUS", "matches": []}
    at_utc = at.astimezone(UTC)
    matches = []
    for row in lifetimes:
        if row.get("canonical_symbol") != symbol and row.get("historical_symbol") != symbol:
            continue
        start = parse_time(row["valid_from"])
        end = parse_time(row["valid_to"]) if row.get("valid_to") else None
        provider_id = row.get("provider_asset_id") or row.get("entity_id")
        if provider_id and start <= at_utc and (end is None or at_utc <= end):
            matches.append(row)
    status = "V5_INTERVAL_EXACT" if len(matches) == 1 else ("IDENTITY_MISSING" if not matches else "IDENTITY_AMBIGUOUS")
    return {"status": status, "matches": matches}


def resolve_identity(
    *,
    issuer_cik_exact: bool,
    accepted_security_state: bool,
    form4_symbol_agrees: bool,
    common_equity: bool,
    transaction_title_compatible: bool,
    v5_interval_status: str,
    ticker_reuse_resolved: bool,
) -> str:
    if not common_equity:
        return "NON_COMMON_EQUITY"
    required = (
        issuer_cik_exact,
        accepted_security_state,
        form4_symbol_agrees,
        transaction_title_compatible,
        v5_interval_status == "V5_INTERVAL_EXACT",
        ticker_reuse_resolved,
    )
    return "IDENTITY_EXACT" if all(required) else "IDENTITY_AMBIGUOUS"


def ticker_change_status(
    old: dict[str, Any],
    new: dict[str, Any],
    intervals: list[dict[str, Any]],
    *,
    class_evidence: bool = False,
    form4_compatible: bool = False,
) -> str:
    if not class_evidence or not form4_compatible:
        return "IDENTITY_AMBIGUOUS"
    if old.get("issuer_cik") != new.get("issuer_cik") or old.get("symbol") == new.get("symbol"):
        return "IDENTITY_AMBIGUOUS"
    if parse_time(old["accepted_at"]) >= parse_time(new["accepted_at"]):
        return "IDENTITY_AMBIGUOUS"
    old_rows = [row for row in intervals if row.get("canonical_symbol") == old["symbol"]]
    new_rows = [row for row in intervals if row.get("canonical_symbol") == new["symbol"]]
    if len(old_rows) != 1 or len(new_rows) != 1 or not old_rows[0].get("valid_to"):
        return "IDENTITY_AMBIGUOUS"
    if parse_time(old_rows[0]["valid_to"]) >= parse_time(new_rows[0]["valid_from"]):
        return "IDENTITY_AMBIGUOUS"
    return "SAME_CIK_TICKER_CONTINUITY_EXACT"


def select_shares_fact(
    facts: list[dict[str, Any]], decision_at: datetime, *, required_class: str | None = None
) -> dict[str, Any]:
    admissible = []
    class_rejected = False
    for fact in facts:
        required_text = ("issuer_cik", "accession", "context_id", "source_sha256")
        if any(not fact.get(field) for field in required_text):
            continue
        if not isinstance(fact.get("dimensions"), list):
            continue
        if fact.get("tag") != "dei:EntityCommonStockSharesOutstanding" or fact.get("form") not in {"10-K", "10-Q"}:
            continue
        if not fact.get("accepted_at") or parse_time(str(fact["accepted_at"])) > decision_at:
            continue
        if parse_time(fact["available_at"]) < h06_timing_v2(parse_time(str(fact["accepted_at"]))):
            continue
        if parse_time(fact["available_at"]) > decision_at:
            continue
        if date.fromisoformat(fact["fact_instant"]) > decision_at.astimezone(NY).date():
            continue
        if fact.get("unit") != "shares":
            continue
        value = Decimal(str(fact["value"]))
        if value <= 0 or value > Decimal("1e14"):
            continue
        if required_class and fact.get("security_class") != required_class:
            class_rejected = True
            continue
        admissible.append(fact)
    if not admissible:
        return {"status": "BLOCKED_SHARES_CLASS_AMBIGUOUS" if class_rejected else "BLOCKED_SHARES_MISSING"}
    latest_key = max((parse_time(row["available_at"]), row["fact_instant"]) for row in admissible)
    latest = [row for row in admissible if (parse_time(row["available_at"]), row["fact_instant"]) == latest_key]
    if len({(str(row["value"]), row.get("context_id")) for row in latest}) != 1:
        return {"status": "BLOCKED_SHARES_CLASS_AMBIGUOUS"}
    return {"status": "SHARES_FACT_EXACT", "fact": latest[0]}


def split_ratio(action: dict[str, Any]) -> Decimal:
    old = Decimal(str(action["old_rate"]))
    new = Decimal(str(action["new_rate"]))
    if old <= 0 or new <= 0:
        raise ValueError("positive_split_rates_required")
    return new / old


def cumulative_split_factor(actions: list[dict[str, Any]]) -> Decimal:
    factor = Decimal(1)
    for action in actions:
        factor *= split_ratio(action)
    return factor


def transform_shares(raw_shares: Decimal, factor: Decimal) -> Decimal:
    if raw_shares <= 0 or factor <= 0:
        raise ValueError("positive_share_basis_required")
    return raw_shares * factor


def market_cap_basis_invariant(
    raw_price: Decimal,
    raw_shares: Decimal,
    adjusted_price: Decimal,
    adjusted_shares: Decimal,
    *,
    tolerance: Decimal = Decimal("1e-9"),
) -> bool:
    left = raw_price * raw_shares
    right = adjusted_price * adjusted_shares
    scale = max(Decimal(1), abs(left), abs(right))
    return abs(left - right) <= tolerance * scale


def first_federal_business_date_after(day: date) -> date:
    calendar = USFederalHolidayCalendar()
    holidays = {stamp.date() for stamp in calendar.holidays(start=day, end=day + timedelta(days=20))}
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate += timedelta(days=1)
    return candidate


def h06_timing_v2(accepted_at: datetime) -> datetime:
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("acceptance_timezone_required")
    accepted_local = accepted_at.astimezone(NY)
    complete_day = first_federal_business_date_after(accepted_local.date())
    calendar = market_calendars.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=complete_day + timedelta(days=1), end_date=complete_day + timedelta(days=14))
    if schedule.empty:
        raise ValueError("no_nyse_session_after_complete_federal_business_day")
    return cast(datetime, schedule.iloc[0]["market_open"].to_pydatetime()).astimezone(UTC)


def contains_economic_output(value: Any) -> bool:
    forbidden = {"score", "ranking", "pnl", "sharpe", "drawdown", "economic_result", "performance"}
    if isinstance(value, dict):
        return any(str(key).casefold() in forbidden or contains_economic_output(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_economic_output(item) for item in value)
    return False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _probe_sources() -> list[dict[str, Any]]:
    return [
        {"case": "common_no_split", "official_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm", "accession": "0000320193-24-000123", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
        {"case": "forward_split", "official_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/nvda-20240607.htm", "accession": "0001045810-24-000144", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
        {"case": "reverse_split", "official_url": "https://www.sec.gov/Archives/edgar/data/40545/000120677421001925/ge3936671-8k.htm", "accession": "0001206774-21-001925", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
        {"case": "ticker_change", "official_url": "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm", "accession": "0001326801-22-000070", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
        {"case": "multi_class", "official_url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204425000014/goog-20241231.htm", "accession": "0001652044-25-000014", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
        {"case": "non_common_listed", "official_url": "https://www.sec.gov/Archives/edgar/data/40545/000120677421001925/ge3936671-8k.htm", "accession": "0001206774-21-001925", "acquisition_status": "READABLE_NOT_REPRODUCIBLE_EVIDENCE", "persisted": False, "sha256": None, "reason": "SEC_DIRECT_HTTP_403_BROWSER_CONTENT_NOT_PERSISTABLE"},
    ]


def _load_lifetime(symbol: str) -> dict[str, Any] | None:
    path = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        matches = [row for row in csv.DictReader(handle) if row["canonical_symbol"] == symbol]
    if len(matches) != 1:
        return None
    row = matches[0]
    return {key: row[key] for key in ("entity_id", "canonical_symbol", "valid_from", "valid_to", "exchange", "rename_chain")}


def build_recovery_receipt() -> dict[str, Any]:
    source_receipt = json.loads((REPO / "data/research_manifests/h06_source_probe_receipt_2026_09_14.json").read_text())
    records = []
    for source in source_receipt["sources"]:
        if source.get("persisted") is False:
            records.append({"path": None, "tracked": False, "untracked": True, "gitignored": True, "required": False, "expected_sha256": source["sha256"], "actual_sha256": None, "expected_bytes": source["bytes"], "actual_bytes": None, "initial_status": "OPTIONAL_NOT_REQUIRED", "recovery_action": "NONE", "recovery_source": source["official_url"], "final_status": "OPTIONAL_NOT_REQUIRED"})
            continue
        path = REPO / source["path"]
        actual = sha256_file(path)
        matches = actual == source["sha256"] and path.stat().st_size == source["bytes"]
        status = "PRESENT_HASH_MATCH" if matches else "BLOCKED_HASH_MISMATCH"
        records.append({"path": source["path"], "tracked": True, "untracked": False, "gitignored": False, "required": True, "expected_sha256": source["sha256"], "actual_sha256": actual, "expected_bytes": source["bytes"], "actual_bytes": path.stat().st_size, "initial_status": status, "recovery_action": "NONE", "recovery_source": "GIT_WORKTREE_AT_ACCEPTED_COMMIT", "final_status": status})
    for relative, expected in EXPECTED_V5.items():
        path = REPO / relative
        actual = sha256_file(path)
        tracked = relative.endswith("equity_entity_lifetimes_full_v5.csv")
        expected_bytes = EXPECTED_V5_BYTES[relative]
        matches = actual == expected and (expected_bytes is None or path.stat().st_size == expected_bytes)
        status = "PRESENT_HASH_MATCH" if matches else "BLOCKED_HASH_MISMATCH"
        records.append({"path": relative, "tracked": tracked, "untracked": not tracked, "gitignored": not tracked, "required": True, "expected_sha256": expected, "actual_sha256": actual, "expected_bytes": expected_bytes, "actual_bytes": path.stat().st_size, "initial_status": status, "recovery_action": "NONE", "recovery_source": "CANONICAL_LOCAL_PATH", "final_status": status})
    if any(row["required"] and row["final_status"] != "PRESENT_HASH_MATCH" for row in records):
        raise ValueError("required_input_hash_or_bytes_mismatch")
    payload = {"schema": "h06-local-input-recovery-v1", "status": "RECOVERY_GATE_PASSED", "accepted_base_commit": "c8c8cc77075e4560912cec31c03ec3f6583ff6db", "tracked_sec_probe_restored_from_git": False, "form4_pdf_needed": False, "form4_pdf_tracked": False, "v5_files_rebuilt": [], "inputs": records, "integrity": {"canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false; omit integrity.receipt_sha256", "receipt_sha256": PENDING}}
    return seal(payload, "receipt_sha256")


def build_taxonomy() -> dict[str, Any]:
    payload = {"schema": "h06-security-class-taxonomy-v1", "status": "FROZEN_FAIL_CLOSED", "statuses": ["COMMON_EQUITY_EXACT", "PREFERRED_EQUITY", "ADR_OR_ADS", "UNIT", "WARRANT", "NOTE_OR_DEBT", "FUND_OR_ETF", "OTHER_NON_COMMON", "AMBIGUOUS_SECURITY_CLASS"], "rule": "filing-specific accepted cover title and class/member evidence control; listed status and symbol suffixes never upgrade a class", "integrity": {"canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false; omit integrity.manifest_sha256", "manifest_sha256": PENDING}}
    return seal(payload, "manifest_sha256")


def build_timing() -> dict[str, Any]:
    payload = {"schema": "h06-timing-v2", "status": "H06_TIMING_V2", "acceptance_source": ["complete submission .txt", ".hdr.sgml"], "acceptance_timezone": "America/New_York", "delay": "one complete U.S. federal business day after SEC ACCEPTANCE-DATETIME", "availability": "first NYSE regular-session open strictly after that complete federal business day", "calendar_implementation": {"federal": "pandas USFederalHolidayCalendar", "exchange": "pandas_market_calendars NYSE", "pandas_version": importlib.metadata.version("pandas"), "pandas_market_calendars_version": importlib.metadata.version("pandas_market_calendars")}, "exact_web_availability_claimed": False, "integrity": {"canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false; omit integrity.manifest_sha256", "manifest_sha256": PENDING}}
    return seal(payload, "manifest_sha256")


def build_probe() -> dict[str, Any]:
    cases: list[dict[str, Any]] = [
        {"case": "COMMON_EQUITY_NO_SPLIT", "issuer_cik": "0000320193", "accession": "0000320193-24-000123", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "AAPL", "active_cover_symbol": None, "cover_class": None, "exchange": "NASDAQ", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": _load_lifetime("AAPL"), "v5_candidate_count": 1, "identity_status": "IDENTITY_MISSING", "reason": "Structural candidate selected from frozen V5; SEC body is not locally receipt-bound", "market_cap_status": "BLOCKED_SECURITY_IDENTITY", "split_actions": [], "split_transformation": "local action interval factor=1; SEC share basis unavailable"},
        {"case": "FORWARD_SPLIT", "issuer_cik": "0001045810", "accession": "0001045810-24-000144", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "NVDA", "active_cover_symbol": None, "cover_class": None, "exchange": "NASDAQ", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": _load_lifetime("NVDA"), "v5_candidate_count": 1, "identity_status": "IDENTITY_MISSING", "reason": "Frozen corporate-action row is old_rate=1,new_rate=10; SEC body is not locally receipt-bound", "market_cap_status": "BLOCKED_SPLIT_BASIS", "split_actions": [{"action_type": "forward_splits", "old_rate": "1", "new_rate": "10"}], "split_transformation": "conditional local-data factor=10"},
        {"case": "REVERSE_SPLIT", "issuer_cik": "0000040545", "accession": "0001206774-21-001925", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "GE", "active_cover_symbol": None, "cover_class": None, "exchange": "NYSE", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": _load_lifetime("GE"), "v5_candidate_count": 1, "identity_status": "IDENTITY_MISSING", "reason": "Frozen corporate-action row is old_rate=8,new_rate=1; exact V5 normalization horizon is unproved", "market_cap_status": "BLOCKED_SPLIT_BASIS", "split_actions": [{"action_type": "reverse_splits", "old_rate": "8", "new_rate": "1"}], "split_transformation": "conditional local-data factor=0.125"},
        {"case": "SAME_CIK_TICKER_CHANGE", "issuer_cik": "0001326801", "accession": "0001326801-22-000070", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "META", "active_cover_symbol": None, "old_symbol": "FB", "cover_class": None, "exchange": "NASDAQ", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": _load_lifetime("META"), "v5_candidate_count": 1, "identity_status": "IDENTITY_AMBIGUOUS", "reason": "V5 stores one META interval from 2016 with a hard-coded rename_chain, not exact old/new intervals", "market_cap_status": "BLOCKED_SECURITY_IDENTITY", "ticker_change_status": "IDENTITY_AMBIGUOUS"},
        {"case": "MULTIPLE_COMMON_CLASSES", "issuer_cik": "0001652044", "accession": "0001652044-25-000014", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "GOOGL/GOOG", "active_cover_symbol": None, "cover_class": None, "exchange": "NASDAQ", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": [_load_lifetime("GOOGL"), _load_lifetime("GOOG")], "v5_candidate_count": 1, "identity_status": "IDENTITY_MISSING", "reason": "Frozen V5 has only GOOGL; SEC class/share contexts are not locally receipt-bound", "market_cap_status": "BLOCKED_SHARES_CLASS_AMBIGUOUS", "shares_context": None},
        {"case": "NON_COMMON_LISTED_SECURITY", "issuer_cik": "0000040545", "accession": "0001206774-21-001925", "accepted_at": None, "form4_symbol": None, "transaction_security_title": None, "candidate_symbol": "GE 25", "active_cover_symbol": None, "cover_class": None, "exchange": "NYSE", "class_status": "AMBIGUOUS_SECURITY_CLASS", "v5": None, "v5_candidate_count": 0, "identity_status": "IDENTITY_MISSING", "reason": "Candidate official URL was identified, but no local SHA-bound filing can prove the listed class", "market_cap_status": "BLOCKED_SECURITY_IDENTITY"},
    ]
    payload = {"schema": "h06-identity-marketcap-probe-v1", "status": BRIDGE_STATUS, "selection_policy": "structural metadata only; no H06 outcome or post-event performance was inspected", "sources": _probe_sources(), "identity_probe": cases, "market_cap_probe": [{"case": row["case"], "shares_accession": row["accession"], "accepted_at": row["accepted_at"], "fact_instant": None, "raw_shares": None, "context_class": row.get("shares_context"), "shares_status": "STRUCTURAL_ONLY_NOT_RECEIPT_BOUND", "split_actions": row.get("split_actions", []), "split_transformation": row.get("split_transformation", "not applicable"), "transformed_shares": None, "final_basis_status": row["market_cap_status"]} for row in cases], "boundaries": {"economic_run": False, "score_calculated": False, "ranking_performed": False, "returns_inspected": False, "pnl_calculated": False, "holdout_economics": False, "thresholds_tuned": False, "v3_changed": False, "paper_changed": False, "live_changed": False, "registry_changed": False, "v5_bytes_changed": False}, "integrity": {"canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false; omit integrity.receipt_sha256", "receipt_sha256": PENDING}}
    if contains_economic_output({"identity_probe": cases, "market_cap_probe": payload["market_cap_probe"]}):
        raise ValueError("probe_contains_economic_output")
    return seal(payload, "receipt_sha256")


def build_manifest(recovery: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    payload = {"schema": "h06-identity-marketcap-bridge-v1", "status": BRIDGE_STATUS, "accepted_feasibility_status": "H06_BLOCKED_MULTIPLE_CAUSAL_REQUIREMENTS", "frozen_mechanism_changed": False, "identity": {"status": "BLOCKED", "sec_anchor": "issuer CIK", "trading_symbol_source": "accepted filing-specific cover XBRL", "class_source": "accepted cover class/member plus Form 4 transaction title", "version_semantics": "only states whose H06_TIMING_V2 availability is at or before decision time", "v5_join_rule": "exact symbol and exactly one interval at timestamp; nonempty provider UUID; no unresolved ticker reuse; no fuzzy name upgrade", "v5_schema_limitations": ["CIK absent", "security name absent", "share-class identifier absent", "META rename is not two intervals"]}, "shares": {"status": "BLOCKED", "tag": "dei:EntityCommonStockSharesOutstanding", "forms": ["10-K", "10-Q"], "authority": "filing-specific Inline XBRL context and dimensions", "acceptance_policy": "accepted and H06_TIMING_V2-available by decision; fact instant not after decision", "protocol_compatibility": PROTOCOL_STATUS}, "price_basis": {"adjustment_type": "Alpaca SIP bars requested with adjustment=split", "status": PRICE_BASIS_STATUS, "ratio_semantics": "internally consistent with new_rate/old_rate, not source-schema pinned", "normalization_horizon": "not proven", "future_split_normalization": FUTURE_SPLIT_STATUS, "conditional_bridge": "transformed_shares = raw_shares * product(new_rate/old_rate) only after ratio and price normalization anchor are proven", "invariant": "raw_price*raw_shares ~= V5_price*transformed_shares", "tolerance": "1e-9 relative"}, "ticker_change": {"case": "META/FB", "cik": "0001326801", "old_symbol": "FB", "new_symbol": "META", "sec_chronology": "not receipt-bound in this probe", "v5_chronology": "single META row from 2016 with embedded rename_chain", "status": "IDENTITY_AMBIGUOUS"}, "multi_class": {"case": "Alphabet", "classes_symbols": "not receipt-bound in this probe", "shares_context": "not receipt-bound in this probe", "status": "BLOCKED_SHARES_CLASS_AMBIGUOUS"}, "remaining_blockers": ["new bounded SEC filing bodies could not be persisted and SHA-bound because direct SEC HTTP returned 403", "Form 4 symbol/title was not receipt-bound for all structural cases", "V5 has no issuer CIK or share-class identifier and does not represent META old/new as exact intervals", PROTOCOL_STATUS, PRICE_BASIS_STATUS, "split ratio provider-schema semantics and V5 normalization horizon are not frozen", FUTURE_SPLIT_STATUS], "inputs": {"recovery_receipt": {"path": str(RECOVERY.relative_to(REPO)), "sha256": hashlib.sha256((json.dumps(recovery, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()).hexdigest()}, "probe_receipt": {"path": str(PROBE.relative_to(REPO)), "sha256": hashlib.sha256((json.dumps(probe, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()).hexdigest()}}, "artifacts": {}, "boundaries": probe["boundaries"], "integrity": {"canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false; omit integrity.manifest_sha256", "manifest_sha256": PENDING}}
    return seal(payload, "manifest_sha256")


def render_report(manifest: dict[str, Any], probe: dict[str, Any]) -> str:
    lines = ["# H06 identity and market-cap bridge — 2026-09-14", "", f"Status: `{manifest['status']}`.", "", "This is an outcome-free structural audit. No H06 signal, ranking, return, P&L, holdout result, or strategy change was produced.", "", "## Local recovery", "", "All nine persisted H06 SEC probe files and all three canonical V5 inputs were present and matched their frozen SHA-256 values. No V5 file was rebuilt and no tracked probe needed Git restoration. The optional Form 4 PDF was not needed and remains untracked.", "", "## Identity path", "", "The SEC-side anchor is issuer CIK. A decision-safe security state must come from accepted filing-specific cover XBRL and retain accession, form, accepted time, title, symbol, exchange, context, dimensions, URL, and source hash. A Form 4 row must agree on issuer CIK, symbol, and transaction class. The V5 join then requires exactly one active symbol interval and no unresolved ticker reuse.", "", "V5 cannot complete that proof as frozen: it has provider UUID, symbol, observed-bar lifetime, exchange, and build-time status, but no CIK, security name, or share-class identifier. META/FB is one META interval with an embedded rename string, not two exact intervals.", "", "## Shares and split basis", "", f"`dei:EntityCommonStockSharesOutstanding` is usable only from accepted filing-specific 10-K/10-Q contexts. The frozen protocol does not authorize latest-accepted carry-forward, so the verdict is `{PROTOCOL_STATUS}`.", "", "V5 acquisition code and manifests prove the request label `adjustment=split`, but not the exact per-row factor or normalization horizon. Corporate actions are internally consistent with `new_rate / old_rate` (AAPL/NVDA forward, GE reverse), yet the provider schema semantics are not pinned. Therefore the exact basis status is `V5_SPLIT_BASIS_NOT_PROVEN` and future-action normalization remains blocked.", "", "## Structural probes", "", "| Case | Class | Identity | Market-cap basis |", "|---|---|---|---|"]
    for row in probe["identity_probe"]:
        lines.append(f"| {row['case']} | {row['class_status']} | {row['identity_status']} | {row['market_cap_status']} |")
    lines.extend(["", "## SEC acquisition limitation", "", "The official filing pages were readable in a browser, but direct bounded HTTP downloads returned 403. Browser content was not persistable through the available interface. The receipt records source pointers and the transport limitation with `persisted=false`; it deliberately supplies no SHA, treats the page facts as non-evidence, and cannot upgrade those cases to exact status.", "", "## Remaining blockers", ""])
    lines.extend(f"- `{item}`" for item in manifest["remaining_blockers"])
    lines.extend(["", "## Boundary", "", "Economics, H06 scores, ranking, returns, P&L, holdout economics, tuning, V3, PAPER, LIVE, registry, and canonical V5 bytes were untouched.", ""])
    return "\n".join(lines)


def build() -> dict[str, Any]:
    recovery = build_recovery_receipt()
    taxonomy = build_taxonomy()
    timing = build_timing()
    probe = build_probe()
    _write_json(RECOVERY, recovery)
    _write_json(TAXONOMY, taxonomy)
    _write_json(TIMING, timing)
    _write_json(PROBE, probe)
    manifest = build_manifest(recovery, probe)
    report_text = render_report(manifest, probe)
    artifact_paths = {"taxonomy": TAXONOMY, "timing": TIMING}
    manifest["artifacts"] = {name: {"path": str(path.relative_to(REPO)), "sha256": sha256_file(path), "bytes": path.stat().st_size} for name, path in artifact_paths.items()}
    report_bytes = report_text.encode("utf-8")
    manifest["artifacts"]["report"] = {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest(), "bytes": len(report_bytes)}
    seal(manifest, "manifest_sha256")
    _write_json(MANIFEST, manifest)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_bytes(report_bytes)
    return {"status": BRIDGE_STATUS, "manifest_sha256": manifest["integrity"]["manifest_sha256"]}


def verify() -> dict[str, Any]:
    for path, field in ((RECOVERY, "receipt_sha256"), (TAXONOMY, "manifest_sha256"), (TIMING, "manifest_sha256"), (PROBE, "receipt_sha256"), (MANIFEST, "manifest_sha256")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["integrity"][field] != nested_self_hash(payload, field):
            raise ValueError(f"self_hash_mismatch:{path}")
    recovery = json.loads(RECOVERY.read_text(encoding="utf-8"))
    if any(row["required"] and row["final_status"] not in {"PRESENT_HASH_MATCH", "RESTORED_FROM_GIT", "RECOVERED_FROM_LOCAL_EXACT_HASH_COPY", "REBUILT_EXACT_HASH_MATCH"} for row in recovery["inputs"]):
        raise ValueError("recovery_gate_not_passed")
    for row in recovery["inputs"]:
        if not row["required"]:
            continue
        path = REPO / row["path"]
        if not path.is_file() or sha256_file(path) != row["expected_sha256"]:
            raise ValueError(f"required_input_hash_mismatch:{row['path']}")
        if row["expected_bytes"] is not None and path.stat().st_size != row["expected_bytes"]:
            raise ValueError(f"required_input_bytes_mismatch:{row['path']}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["status"] != BRIDGE_STATUS or manifest["shares"]["protocol_compatibility"] != PROTOCOL_STATUS:
        raise ValueError("bridge_status_invalid")
    for declaration in [*manifest["inputs"].values(), *manifest["artifacts"].values()]:
        path = REPO / declaration["path"]
        if not path.is_file() or sha256_file(path) != declaration["sha256"]:
            raise ValueError(f"artifact_hash_mismatch:{declaration['path']}")
        if "bytes" in declaration and path.stat().st_size != declaration["bytes"]:
            raise ValueError(f"artifact_bytes_mismatch:{declaration['path']}")
    return {"status": "H06_IDENTITY_MARKETCAP_BRIDGE_VERIFIED", "read_only": True, "economic_run": False}


if __name__ == "__main__":
    import sys

    print(json.dumps(verify() if "--verify" in sys.argv[1:] else build(), sort_keys=True))
