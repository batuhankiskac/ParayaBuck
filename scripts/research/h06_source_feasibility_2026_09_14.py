"""Outcome-free H06 source-feasibility contracts and deterministic helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TEN_B5_COMPLIANCE_DATE = date(2023, 4, 1)
OPEN_MARKET = "OPEN_MARKET_EXPLICIT"
PRIVATE = "PRIVATE_PURCHASE_EXPLICIT"
AMBIGUOUS = "P_PURCHASE_AMBIGUOUS"
ECONOMIC_TERMS = frozenset({"return", "returns", "pnl", "sharpe", "drawdown", "performance"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def self_hash(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "self_sha256"}
    return hashlib.sha256(canonical(payload)).hexdigest()


def classify_open_private(*official_text: str) -> str:
    """Classify code-P evidence from filing words, never price proximity."""
    text = " ".join(official_text).casefold()
    private = re.search(r"\b(private (?:purchase|placement|transaction)|privately negotiated)\b", text)
    public = re.search(r"\b(open[- ]market (?:purchase|transaction)|purchased in (?:the )?open market)\b", text)
    if private:
        return PRIVATE
    if public:
        return OPEN_MARKET
    return AMBIGUOUS


def ten_b5_status(filing_date: date, disclosed: bool | None, explicit_non_plan: bool = False) -> str:
    if filing_date < TEN_B5_COMPLIANCE_DATE:
        return "PASS_EXPLICIT_OFFICIAL_NON_PLAN_EVIDENCE" if explicit_non_plan else "10B5_1_DISCLOSURE_NOT_REQUIRED_AT_THE_TIME"
    if disclosed is True:
        return "EXCLUDE_10B5_1_CHECKED"
    if disclosed is False:
        return "PASS_POST_COMPLIANCE_EXPLICIT_FALSE"
    return "EXCLUDE_POST_COMPLIANCE_DISCLOSURE_MISSING"


def classify_owner(owner_cik: str | None, year: int, trades: Iterable[dict[str, Any]]) -> str:
    """Beginning-of-year CMP classifier using only Y-3..Y-1 and owner CIK."""
    if not owner_cik:
        return "NON_PARTITIONABLE"
    months: dict[int, set[int]] = defaultdict(set)
    for trade in trades:
        if trade.get("reporting_owner_cik") != owner_cik:
            continue
        traded = date.fromisoformat(str(trade["transaction_date"]))
        if year - 3 <= traded.year <= year - 1 and trade.get("routine_history_qualifying", True):
            months[traded.year].add(traded.month)
    prior = (year - 3, year - 2, year - 1)
    if any(not months[item] for item in prior):
        return "NON_PARTITIONABLE"
    return "ROUTINE" if set.intersection(*(months[item] for item in prior)) else "OPPORTUNISTIC"


def amendment_status(accepted_at: str, decision_at: str, linkage_resolved: bool, *, amendment: bool) -> str:
    if amendment and not linkage_resolved:
        return "AMENDMENT_LINEAGE_UNRESOLVED"
    if datetime.fromisoformat(accepted_at.replace("Z", "+00:00")) > datetime.fromisoformat(decision_at.replace("Z", "+00:00")):
        return "ORIGINAL_ACTIVE"
    return "AMENDED_VERSION_ACTIVE" if amendment else "ORIGINAL_ACTIVE"


def identity_status(*, cik_interval_matches: bool, common_equity: bool, ticker_only: bool = False) -> str:
    if not common_equity:
        return "NON_COMMON_EQUITY"
    if ticker_only:
        return "IDENTITY_AMBIGUOUS"
    return "IDENTITY_EXACT" if cik_interval_matches else "IDENTITY_MISSING"


def signal_admissible(row: dict[str, Any]) -> bool:
    required = (
        row.get("form_lineage_status") in {"ORIGINAL_ACTIVE", "AMENDED_VERSION_ACTIVE"},
        row.get("derivative") is False,
        row.get("transaction_code") == "P",
        row.get("acquired_disposed") == "A",
        row.get("ownership_form") == "D",
        isinstance(row.get("shares"), (int, float)) and row["shares"] > 0,
        isinstance(row.get("price"), (int, float)) and row["price"] > 0,
        row.get("owner_classification") == "OPPORTUNISTIC",
        row.get("identity_status") == "IDENTITY_EXACT",
        bool(row.get("accepted_at_admissible")),
        bool(row.get("available_before_decision")),
        str(row.get("ten_b5_status", "")).startswith("PASS_"),
        row.get("open_private_status") == OPEN_MARKET,
    )
    return all(required)


def contains_economic_fields(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).casefold() in ECONOMIC_TERMS or contains_economic_fields(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_economic_fields(item) for item in value)
    return False


if __name__ == "__main__":
    raise SystemExit("Contracts are frozen as committed JSON; this module is not an acquisition or economic runner.")
