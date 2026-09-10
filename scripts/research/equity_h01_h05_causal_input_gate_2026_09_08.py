#!/usr/bin/env python3
"""Build a frozen causal input gate for equity H01-H05.

The command consumes only the already acquired local SEC pilot and canonical
equity files.  It writes a new research bundle and never touches runtime,
PAPER, LIVE, or the earlier pilot artifacts.  It is a schema/coverage
diagnostic; it deliberately does not calculate returns or promote a strategy.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas_market_calendars as market_calendars

REPO = Path(__file__).resolve().parents[2]
FIXED = REPO / "data/research_manifests/equity_h01_h05_minimal_pit_pilot_2026_09_07.json"
SELECTION = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2/pilot_selection.json"
RAW = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
BARS = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
OUTPUT = REPO / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r11"
MANIFEST = REPO / "data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r11.json"

TAGS = (
    "Assets",
    "StockholdersEquity",
    "GrossProfit",
    "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsOfDividends",
    "PaymentsForRepurchaseOfCommonStock",
    "PaymentsForRepurchaseOfEquity",
    "ProceedsFromIssuanceOfCommonStock",
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)
FLOW_TAGS = set(TAGS) - {
    "Assets",
    "StockholdersEquity",
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
}

MAX_REPORTING_AGE_DAYS = 430
MONETARY_UNITS = {
    "USD": ("USD", 1.0),
    "USD_THOUSANDS": ("USD", 1_000.0),
    "USD_MILLIONS": ("USD", 1_000_000.0),
    "USD_BILLIONS": ("USD", 1_000_000_000.0),
    "CNY": ("CNY", 1.0),
    "CNY_THOUSANDS": ("CNY", 1_000.0),
    "CNY_MILLIONS": ("CNY", 1_000_000.0),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def monetary_unit(row: Mapping[str, Any] | str | None) -> tuple[str, float] | None:
    if row is None:
        return None
    unit = row if isinstance(row, str) else row.get("unit")
    if unit is None:
        return None
    return MONETARY_UNITS.get(str(unit).strip().upper().replace(" ", "_"))


def compatible_monetary_units(*rows: Mapping[str, Any] | str | None) -> bool:
    signatures = [monetary_unit(row) for row in rows]
    return bool(signatures) and all(signature is not None and signature == signatures[0] for signature in signatures)


def end_age_days(row: Mapping[str, Any] | None, decision: str) -> int | None:
    if row is None or not row.get("end"):
        return None
    try:
        return (date.fromisoformat(decision[:10]) - date.fromisoformat(str(row["end"]))).days
    except ValueError:
        return None


def fresh_reporting_period(row: Mapping[str, Any] | None, decision: str) -> bool:
    age = end_age_days(row, decision)
    return age is not None and 0 <= age <= MAX_REPORTING_AGE_DAYS


def bounded_duration_days(row: Mapping[str, Any] | None, *, maximum: int = MAX_REPORTING_AGE_DAYS) -> int | None:
    if row is None or not row.get("start") or not row.get("end"):
        return None
    try:
        days = (date.fromisoformat(str(row["end"])) - date.fromisoformat(str(row["start"]))).days
    except ValueError:
        return None
    return days if 0 < days <= maximum else None


def has_causal_fact(rows: Iterable[Mapping[str, Any]], *, cik: str, tag: str, cutoff: str) -> bool:
    return any(
        row.get("cik") == cik
        and row.get("tag") == tag
        and row.get("available_at")
        and str(row["available_at"]) <= cutoff
        and (not row.get("end") or str(row["end"]) <= cutoff[:10])
        for row in rows
    )


def iso(value: str | None) -> str | None:
    return value if value and len(value) == 10 else None


@lru_cache(maxsize=4096)
def next_nyse_open(filed: str) -> str | None:
    try:
        filed_date = date.fromisoformat(filed)
    except ValueError:
        return None
    schedule = market_calendars.get_calendar("NYSE").schedule(
        start_date=filed_date, end_date=filed_date + timedelta(days=14)
    )
    future = schedule[schedule.index.date > filed_date]
    return str(future.iloc[0]["market_open"].to_pydatetime().isoformat()) if not future.empty else None


@lru_cache(maxsize=4096)
def nyse_close(decision: str) -> str:
    schedule = market_calendars.get_calendar("NYSE").schedule(start_date=decision, end_date=decision)
    if schedule.empty:
        raise RuntimeError(f"BLOCKED_NON_SESSION_DECISION:{decision}")
    return str(schedule.iloc[0]["market_close"].to_pydatetime().isoformat())


def acceptance_map() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(RAW.glob("submissions_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        recent = payload.get("filings", {}).get("recent", {})
        for accn, accepted in zip(
            recent.get("accessionNumber", []), recent.get("acceptanceDateTime", []), strict=False
        ):
            if accn and accepted:
                result[str(accn)] = str(accepted)
    archive = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
    for path in sorted(archive.glob("CIK*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for accn, accepted in zip(
            payload.get("accessionNumber", []), payload.get("acceptanceDateTime", []), strict=False
        ):
            if accn and accepted:
                result[str(accn)] = str(accepted)
    return result


def raw_facts() -> list[dict[str, Any]]:
    accepted = acceptance_map()
    rows: list[dict[str, Any]] = []
    for path in sorted(RAW.glob("companyfacts_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cik = str(payload.get("cik", path.stem.removeprefix("companyfacts_"))).zfill(10)
        for namespace, facts in payload.get("facts", {}).items():
            for tag, definition in facts.items():
                if tag not in TAGS:
                    continue
                for unit, observations in definition.get("units", {}).items():
                    for observation in observations:
                        accepted_at = accepted.get(str(observation.get("accn", "")))
                        filed = iso(str(observation.get("filed", "")))
                        available_at = accepted_at or (next_nyse_open(filed) if filed else None)
                        rows.append(
                            {
                                "cik": cik,
                                "namespace": namespace,
                                "tag": tag,
                                "unit": str(unit),
                                "value": observation.get("val"),
                                "accn": str(observation.get("accn", "")),
                                "form": str(observation.get("form", "")),
                                "fy": observation.get("fy"),
                                "fp": observation.get("fp"),
                                "start": observation.get("start"),
                                "end": observation.get("end"),
                                "filed": filed,
                                "accepted": accepted_at,
                                "available_at": available_at,
                                "availability_basis": "accepted_timestamp"
                                if accepted_at
                                else "filed_date_next_nyse_open",
                            }
                        )
    return rows


def with_flow_conversion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["period_type"] = "instant" if not row.get("start") else "duration"
        row["normalized_flow_value"] = None
        row["flow_basis"] = "not_applicable"
        if row["tag"] not in FLOW_TAGS or not row.get("start") or not row.get("end"):
            continue
        if row["form"] == "10-K":
            row["normalized_flow_value"] = row["value"]
            row["flow_basis"] = "annual_10k_reported_period"
        elif row["form"] == "10-Q":
            groups[(row["cik"], row["tag"], row["unit"], str(row.get("fy", "")), str(row["start"]))].append(row)
    for group in groups.values():
        group.sort(key=lambda row: (str(row.get("end", "")), str(row.get("available_at", "")), row["accn"]))
        seen: list[dict[str, Any]] = []
        for row in group:
            end = str(row["end"])
            earlier = [
                candidate
                for candidate in seen
                if str(candidate["end"]) < end
                and candidate.get("available_at")
                and str(candidate["available_at"]) <= str(row.get("available_at", ""))
            ]
            any_earlier = [candidate for candidate in seen if str(candidate["end"]) < end]
            previous = max(
                earlier,
                key=lambda candidate: (
                    str(candidate["end"]),
                    str(candidate.get("available_at", "")),
                    candidate["accn"],
                ),
                default=None,
            )
            if previous is None:
                if any_earlier:
                    row["flow_basis"] = "blocked_prior_not_available_at_current_boundary"
                else:
                    row["normalized_flow_value"] = row["value"]
                    row["flow_basis"] = "q1_ytd_as_first_period"
            elif isinstance(row["value"], (int, float)) and isinstance(previous["value"], (int, float)):
                row["normalized_flow_value"] = row["value"] - previous["value"]
                row["flow_basis"] = "10q_ytd_delta_from_prior_ytd"
                row["_predecessor_accn"] = previous["accn"]
                row["_predecessor_available_at"] = previous.get("available_at")
                row["_predecessor_availability_basis"] = previous.get("availability_basis")
            else:
                row["flow_basis"] = "blocked_non_numeric_ytd"
            seen.append(row)
    return rows


def revision_group(row: Mapping[str, Any]) -> str:
    return "|".join(str(row.get(key, "")) for key in ("cik", "tag", "unit", "start", "end"))


def latest(
    rows: Iterable[Mapping[str, Any]],
    *,
    cik: str,
    tag: str,
    cutoff: str,
    duration: bool | None = None,
    end_before: str | None = None,
    end_exact: str | None = None,
) -> dict[str, Any] | None:
    eligible = []
    for row in rows:
        if row.get("cik") != cik or row.get("tag") != tag:
            continue
        if not row.get("available_at") or str(row["available_at"]) > cutoff:
            continue
        if duration is not None and (bool(row.get("start")) != duration):
            continue
        if row.get("end") and str(row["end"]) > cutoff[:10]:
            continue
        if end_before is not None and (not row.get("end") or str(row["end"]) >= end_before):
            continue
        if end_exact is not None and str(row.get("end")) != end_exact:
            continue
        value = row.get("normalized_flow_value") if duration else row.get("value")
        if value is None:
            continue
        candidate = dict(row)
        candidate["_value"] = value
        candidate["_revision_group"] = revision_group(row)
        eligible.append(candidate)
    if not eligible:
        return None
    by_group: dict[str, dict[str, Any]] = {}
    for row in eligible:
        old = by_group.get(row["_revision_group"])
        if old is None or (str(row.get("available_at", "")), row["accn"]) > (
            str(old.get("available_at", "")),
            old["accn"],
        ):
            by_group[row["_revision_group"]] = row
    return max(
        by_group.values(), key=lambda row: (str(row.get("end", "")), str(row.get("available_at", "")), row["accn"])
    )


def annual_asset_pair(
    rows: Iterable[Mapping[str, Any]], *, cik: str, cutoff: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    annual = [
        row
        for row in rows
        if row.get("cik") == cik
        and row.get("tag") == "Assets"
        and not row.get("start")
        and row.get("form") == "10-K"
        and row.get("end")
        and str(row["end"]) <= cutoff[:10]
        and row.get("available_at")
        and str(row["available_at"]) <= cutoff
        and fresh_reporting_period(row, cutoff)
    ]
    current_ends = sorted({str(row["end"]) for row in annual})
    for end in reversed(current_ends):
        current = latest(annual, cik=cik, tag="Assets", cutoff=cutoff, duration=False, end_before=None)
        if current is None or str(current.get("end")) != end:
            current = max(
                (
                    dict(row, _value=row.get("value"), _revision_group=revision_group(row))
                    for row in annual
                    if str(row["end"]) == end
                ),
                key=lambda row: (str(row.get("available_at", "")), row["accn"]),
                default=None,
            )
        if current is None:
            continue
        try:
            current_date = date.fromisoformat(end)
        except ValueError:
            continue
        candidates = []
        for row in annual:
            if str(row["end"]) >= end:
                continue
            try:
                gap = (current_date - date.fromisoformat(str(row["end"]))).days
            except ValueError:
                continue
            if 300 <= gap <= 430 and str(row.get("available_at", "")) <= str(current.get("available_at", "")):
                candidates.append(row)
        if candidates:
            prior_end = max(str(row["end"]) for row in candidates)
            prior = max(
                (
                    dict(row, _value=row.get("value"), _revision_group=revision_group(row))
                    for row in candidates
                    if str(row["end"]) == prior_end
                ),
                key=lambda row: (str(row.get("available_at", "")), row["accn"]),
                default=None,
            )
            if compatible_monetary_units(current, prior):
                return current, prior
    return None, None


def h02_component_pair(
    rows: Iterable[Mapping[str, Any]], *, cik: str, decision: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Select a fresh annual gross-profit flow and matching ending assets.

    A defensible annual window is a 10-K duration of 300-430 days.  The
    denominator is the instant Assets fact with the exact same end date and
    compatible monetary unit.  A TTM adapter is intentionally absent until
    four causally aligned quarters are available.
    """
    facts = list(rows)
    decision_cutoff = nyse_close(decision)
    candidates: list[dict[str, Any]] = []
    for row in facts:
        if (
            row.get("cik") != cik
            or row.get("tag") != "GrossProfit"
            or row.get("form") != "10-K"
            or not row.get("start")
            or not row.get("end")
            or row.get("normalized_flow_value") is None
            or not row.get("available_at")
            or str(row["available_at"]) > decision_cutoff
            or not fresh_reporting_period(row, decision)
        ):
            continue
        try:
            duration_days = (date.fromisoformat(str(row["end"])) - date.fromisoformat(str(row["start"]))).days
        except ValueError:
            continue
        if 300 <= duration_days <= 430:
            candidates.append(dict(row, _value=row["normalized_flow_value"], _revision_group=revision_group(row)))
    candidates.sort(key=lambda row: (str(row["end"]), str(row.get("available_at", "")), row["accn"]), reverse=True)
    for gross in candidates:
        assets = latest(
            facts,
            cik=cik,
            tag="Assets",
            cutoff=decision_cutoff,
            duration=False,
            end_exact=str(gross["end"]),
        )
        if assets is not None and fresh_reporting_period(assets, decision) and compatible_monetary_units(gross, assets):
            return gross, assets
    return None, None


def h03_asset_pair(
    rows: Iterable[Mapping[str, Any]], *, cik: str, flow: Mapping[str, Any] | None, cutoff: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Select assets at the flow end and immediately before its start.

    A beginning instant must be no more than seven calendar days before the
    flow start and both instants must be causally available at the decision.
    """
    if flow is None or not flow.get("start") or not flow.get("end"):
        return None, None
    facts = list(rows)
    ending = latest(facts, cik=cik, tag="Assets", cutoff=cutoff, duration=False, end_exact=str(flow["end"]))
    if ending is None:
        return None, None
    try:
        start_date = date.fromisoformat(str(flow["start"]))
    except ValueError:
        return ending, None
    ends = sorted(
        {
            str(row["end"])
            for row in facts
            if row.get("cik") == cik
            and row.get("tag") == "Assets"
            and not row.get("start")
            and row.get("end")
            and str(row["end"]) < str(flow["start"])
        }
    )
    for end in reversed(ends):
        try:
            gap = (start_date - date.fromisoformat(end)).days
        except ValueError:
            continue
        if 0 <= gap <= 7:
            beginning = latest(facts, cik=cik, tag="Assets", cutoff=cutoff, duration=False, end_exact=end)
            if beginning is not None:
                return ending, beginning
    return ending, None


def period_key(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    return "|".join(str(row.get(key, "")) for key in ("form", "unit", "start", "end"))


def aligned_payout_components(
    rows: Iterable[Mapping[str, Any]], *, cik: str, cutoff: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tags = {
        "PaymentsOfDividends",
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
        "ProceedsFromIssuanceOfCommonStock",
    }
    for row in rows:
        if row.get("cik") != cik or row.get("tag") not in tags:
            continue
        if not row.get("start") or not row.get("end"):
            continue
        raw_by_tag[str(row["tag"])].append(
            dict(row, _value=row.get("normalized_flow_value"), _revision_group=revision_group(row))
        )
        if not row.get("available_at") or str(row["available_at"]) > cutoff:
            continue
        if not fresh_reporting_period(row, cutoff) or bounded_duration_days(row) is None:
            continue
        by_tag[str(row["tag"])].append(
            dict(row, _value=row.get("normalized_flow_value"), _revision_group=revision_group(row))
        )
    dividend_keys = {period_key(row) for row in by_tag["PaymentsOfDividends"]}
    issuance_keys = {period_key(row) for row in by_tag["ProceedsFromIssuanceOfCommonStock"]}
    common = dividend_keys & issuance_keys
    if not common:
        return None, None, None, None

    def choose(tag: str, period: str) -> dict[str, Any] | None:
        matches = [row for row in by_tag[tag] if period_key(row) == period]
        return max(matches, key=lambda row: (str(row.get("available_at", "")), row["accn"]), default=None)

    def same_period(row: Mapping[str, Any], period: str) -> bool:
        parts = period.split("|", 3)
        return len(parts) == 4 and tuple(str(row.get(name, "")) for name in ("form", "start", "end")) == (
            parts[0],
            parts[2],
            parts[3],
        )

    for key in sorted((value for value in common if value is not None), reverse=True):
        common_raw = [row for row in raw_by_tag["PaymentsForRepurchaseOfCommonStock"] if same_period(row, key)]
        common_rows = [row for row in by_tag["PaymentsForRepurchaseOfCommonStock"] if period_key(row) == key]
        if common_raw:
            # A common-stock fact for the same reporting span takes precedence.
            # If it is stale, unavailable, or otherwise unusable, retain the
            # block instead of silently falling back to the broader equity tag.
            common_row = (
                max(common_rows, key=lambda row: (str(row.get("available_at", "")), row["accn"]))
                if common_rows
                else None
            )
            return (
                choose("PaymentsOfDividends", key),
                common_row,
                choose("ProceedsFromIssuanceOfCommonStock", key),
                key,
            )
        if any(period_key(row) == key for row in by_tag["PaymentsForRepurchaseOfEquity"]):
            return (
                choose("PaymentsOfDividends", key),
                choose("PaymentsForRepurchaseOfEquity", key),
                choose("ProceedsFromIssuanceOfCommonStock", key),
                key,
            )
    return None, None, None, None


def cash_amount(row: Mapping[str, Any] | None) -> float | None:
    value = number(row)
    return abs(value) if value is not None else None


def calculate_features(
    *,
    equity_value: float | None,
    market_cap: float | None,
    gross_value: float | None,
    h02_asset_value: float | None,
    income_value: float | None,
    cfo_value: float | None,
    average_assets: float | None,
    annual_asset_value: float | None,
    prior_annual_asset_value: float | None,
    dividend_value: float | None,
    repurchase_value: float | None,
    issuance_value: float | None,
    h02_units_match: bool,
    h03_units_match: bool,
    h03_assets_match: bool,
    h04_units_match: bool,
    h05_units_match: bool,
) -> dict[str, float | None]:
    """Apply the five frozen feature formulae after their input gates pass."""
    return {
        "h01_book_to_market": equity_value / market_cap
        if equity_value is not None and market_cap and market_cap > 0
        else None,
        "h02_gross_profitability": gross_value / h02_asset_value
        if h02_units_match and gross_value is not None and h02_asset_value and h02_asset_value > 0
        else None,
        "h03_accruals": (income_value - cfo_value) / average_assets
        if h03_units_match
        and h03_assets_match
        and income_value is not None
        and cfo_value is not None
        and average_assets
        and average_assets > 0
        else None,
        "h04_asset_growth": annual_asset_value / prior_annual_asset_value - 1.0
        if h04_units_match
        and annual_asset_value is not None
        and prior_annual_asset_value
        and prior_annual_asset_value > 0
        else None,
        "h05_net_payout_yield": (dividend_value + repurchase_value - issuance_value) / market_cap
        if h05_units_match
        and dividend_value is not None
        and repurchase_value is not None
        and issuance_value is not None
        and market_cap
        and market_cap > 0
        else None,
    }


def lineage(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "accn": row.get("accn"),
        "available_at": row.get("available_at"),
        "accepted": row.get("accepted"),
        "availability_basis": row.get("availability_basis"),
        "revision_group": row.get("_revision_group"),
        "period_key": period_key(row),
        "predecessor_accn": row.get("_predecessor_accn"),
        "predecessor_available_at": row.get("_predecessor_available_at"),
        "predecessor_availability_basis": row.get("_predecessor_availability_basis"),
    }


def load_bars(symbols: set[str]) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    with BARS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("symbol") in symbols and row.get("timeframe") == "1d":
                result[(row["symbol"], row["timestamp"][:10])] = row
    return result


def load_lifetimes() -> dict[str, dict[str, str]]:
    with LIFETIMES.open(newline="", encoding="utf-8") as handle:
        return {row["canonical_symbol"]: row for row in csv.DictReader(handle)}


def load_actions(symbols: set[str]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    with ACTIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("symbol") in symbols and row.get("action_type") in {"forward_splits", "reverse_splits"}:
                result[row["symbol"]].append(row)
    return result


def source_inventory() -> list[dict[str, Any]]:
    archive = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
    paths = (
        sorted(RAW.glob("companyfacts_*.json"))
        + sorted(RAW.glob("submissions_*.json"))
        + sorted(archive.glob("CIK*.json"))
    )
    entries: list[dict[str, Any]] = []
    for path in sorted(paths):
        if path.parent == RAW and path.name.startswith("companyfacts_"):
            kind = "companyfacts"
        elif path.parent == RAW:
            kind = "current_submissions"
        else:
            kind = "archived_submissions"
        if kind == "companyfacts":
            cik = path.stem.removeprefix("companyfacts_")
        elif kind == "current_submissions":
            cik = path.stem.removeprefix("submissions_")
        else:
            cik = path.stem.split("-", 1)[0].removeprefix("CIK")
        entries.append(
            {
                "relative_path": str(path.relative_to(REPO)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "provider": "sec_edgar",
                "kind": kind,
                "cik": cik.zfill(10) if cik.isdigit() else None,
            }
        )
    return entries


def source_inventory_hash(entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(payload)


def month_end_dates(bars: Mapping[tuple[str, str], Mapping[str, str]], symbols: list[str]) -> list[str]:
    dates = sorted({key[1] for key in bars if key[0] == symbols[0] and "2022-01-01" <= key[1] <= "2024-12-31"})
    result: list[str] = []
    for month in sorted({value[:7] for value in dates}):
        candidates = [value for value in dates if value[:7] == month]
        if candidates:
            result.append(candidates[-1])
    return result


def number(row: Mapping[str, Any] | None) -> float | None:
    if row is None:
        return None
    try:
        value = float(row["_value"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fixed = json.loads(FIXED.read_text(encoding="utf-8"))
    symbols = [str(row["canonical_symbol"]) for row in fixed["symbols"]]
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))["rows"]
    sec_by_symbol = {str(row["symbol"]): row for row in selection}
    if any(symbol not in sec_by_symbol for symbol in symbols):
        raise RuntimeError("BLOCKED_FIXED_SYMBOL_MISSING_CURRENT_SEC_MAPPING")
    facts = with_flow_conversion(raw_facts())
    bars = load_bars(set(symbols))
    lifetimes = load_lifetimes()
    actions = load_actions(set(symbols))
    decisions = month_end_dates(bars, symbols)
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for symbol in symbols:
        cik = str(sec_by_symbol[symbol]["cik"]).zfill(10)
        identity_facts = [
            row
            for row in facts
            if row["cik"] == cik
            and row["tag"] in {"EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"}
        ]
        identity_status = "CURRENT_ISSUER_CIK_MATCHED" if symbol in sec_by_symbol else "UNRESOLVED_CURRENT_ISSUER_CIK"
        historical_identity_status = "UNPROVEN"
        if not identity_facts:
            identity_status = "CURRENT_ISSUER_CIK_MATCHED;COMMON_SHARE_FACT_MISSING"
        unresolved.append({"symbol": symbol, "cik": cik, "reason": "historical_common_share_continuity_unproven"})
        for decision in decisions:
            cutoff = nyse_close(decision)
            price_row = bars.get((symbol, decision))
            shares = latest(facts, cik=cik, tag="EntityCommonStockSharesOutstanding", cutoff=cutoff)
            if shares is None and not has_causal_fact(
                facts, cik=cik, tag="EntityCommonStockSharesOutstanding", cutoff=cutoff
            ):
                shares = latest(facts, cik=cik, tag="CommonStockSharesOutstanding", cutoff=cutoff)
            assets = latest(facts, cik=cik, tag="Assets", cutoff=cutoff, duration=False)
            gross, h02_assets = h02_component_pair(facts, cik=cik, decision=decision)
            annual_assets, previous_annual_assets = annual_asset_pair(facts, cik=cik, cutoff=cutoff)
            equity = latest(facts, cik=cik, tag="StockholdersEquity", cutoff=cutoff, duration=False)
            income = latest(facts, cik=cik, tag="NetIncomeLoss", cutoff=cutoff, duration=True)
            cfo = latest(facts, cik=cik, tag="NetCashProvidedByUsedInOperatingActivities", cutoff=cutoff, duration=True)
            h03_end_assets, h03_begin_assets = h03_asset_pair(
                facts, cik=cik, flow=income if period_key(income) == period_key(cfo) else None, cutoff=cutoff
            )
            dividend, repurchase, issuance, payout_period_key = aligned_payout_components(facts, cik=cik, cutoff=cutoff)
            split_rows = actions.get(symbol, [])
            future_splits = [row for row in split_rows if row.get("ex_date") and row["ex_date"] > decision]
            # Both the close and the reported shares are taken in the same
            # post-action decision basis.  Therefore the explicit as-of
            # conversion factor is one; any future action closes the gate.
            split_factor = 1.0
            close = float(price_row["close"]) if price_row else None
            share_value = number(shares)
            market_cap = (
                close * share_value
                if close is not None and share_value is not None and close > 0 and share_value >= 0
                else None
            )
            h02_asset_value = number(h02_assets)
            h03_end_asset_value, h03_begin_asset_value = number(h03_end_assets), number(h03_begin_assets)
            annual_asset_value, prior_annual_asset_value = number(annual_assets), number(previous_annual_assets)
            equity_value, gross_value = number(equity), number(gross)
            income_value, cfo_value = number(income), number(cfo)
            flow_periods_match = income is not None and cfo is not None and period_key(income) == period_key(cfo)
            h03_assets_match = flow_periods_match and h03_end_assets is not None and h03_begin_assets is not None
            h02_units_match = (
                gross is not None and h02_assets is not None and compatible_monetary_units(gross, h02_assets)
            )
            h03_units_match = h03_assets_match and compatible_monetary_units(
                income, cfo, h03_end_assets, h03_begin_assets
            )
            h04_units_match = (
                annual_assets is not None
                and previous_annual_assets is not None
                and compatible_monetary_units(annual_assets, previous_annual_assets)
            )
            h05_units_match = (
                dividend is not None
                and repurchase is not None
                and issuance is not None
                and compatible_monetary_units(dividend, repurchase, issuance, "USD")
            )
            average_assets = (
                (h03_end_asset_value + h03_begin_asset_value) / 2.0
                if h03_assets_match and h03_end_asset_value is not None and h03_begin_asset_value is not None
                else None
            )
            dividend_value, repurchase_value, issuance_value = (
                cash_amount(dividend),
                cash_amount(repurchase),
                cash_amount(issuance),
            )
            features = calculate_features(
                equity_value=equity_value,
                market_cap=market_cap,
                gross_value=gross_value,
                h02_asset_value=h02_asset_value,
                income_value=income_value,
                cfo_value=cfo_value,
                average_assets=average_assets,
                annual_asset_value=annual_asset_value,
                prior_annual_asset_value=prior_annual_asset_value,
                dividend_value=dividend_value,
                repurchase_value=repurchase_value,
                issuance_value=issuance_value,
                h02_units_match=h02_units_match,
                h03_units_match=h03_units_match,
                h03_assets_match=h03_assets_match,
                h04_units_match=h04_units_match,
                h05_units_match=h05_units_match,
            )
            rows.append(
                {
                    "symbol": symbol,
                    "cik": cik,
                    "entity_id": lifetimes.get(symbol, {}).get("entity_id"),
                    "decision_date": decision,
                    "decision_boundary": "month_end_close; next-session execution would be required",
                    "price_timestamp": price_row.get("timestamp") if price_row else None,
                    "price_basis": "split_adjusted_canonical_v5",
                    "close": close,
                    "shares": share_value,
                    "shares_fact_end": shares.get("end") if shares else None,
                    "shares_available_at": shares.get("available_at") if shares else None,
                    "market_cap": market_cap,
                    "split_factor_asof": split_factor,
                    "future_split_count": len(future_splits),
                    "market_cap_join_status": "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN"
                    if market_cap is not None and not future_splits
                    else "BLOCKED_SPLIT_OR_PRICE_JOIN",
                    "identity_status": identity_status,
                    "historical_security_continuity_status": historical_identity_status,
                    "decision_cutoff": cutoff,
                    "eligibility_basis": "fixed_date_known_pilot_rows; monthly grid only; no membership/completeness filter",
                    "source_lineage": {
                        name: lineage(fact)
                        for name, fact in {
                            "assets": assets,
                            "h02_assets": h02_assets,
                            "previous_assets": previous_annual_assets,
                            "annual_assets": annual_assets,
                            "h03_end_assets": h03_end_assets,
                            "h03_begin_assets": h03_begin_assets,
                            "equity": equity,
                            "shares": shares,
                            "gross": gross,
                            "income": income,
                            "cfo": cfo,
                            "dividend": dividend,
                            "repurchase": repurchase,
                            "issuance": issuance,
                        }.items()
                    },
                    "flow_basis": {
                        "gross": gross.get("flow_basis") if gross else None,
                        "income": income.get("flow_basis") if income else None,
                        "cfo": cfo.get("flow_basis") if cfo else None,
                        "dividend": dividend.get("flow_basis") if dividend else None,
                        "repurchase": repurchase.get("flow_basis") if repurchase else None,
                        "issuance": issuance.get("flow_basis") if issuance else None,
                    },
                    "payout_period_key": payout_period_key,
                    "h03_period_alignment_status": "PASS"
                    if flow_periods_match and h03_assets_match and h03_units_match
                    else (
                        "BLOCKED_UNIT_MISMATCH"
                        if flow_periods_match and h03_assets_match
                        else "BLOCKED_PERIOD_MISMATCH"
                    ),
                    "h04_comparator_status": "PASS_ANNUAL"
                    if annual_assets is not None and previous_annual_assets is not None and h04_units_match
                    else (
                        "BLOCKED_UNIT_MISMATCH"
                        if annual_assets is not None and previous_annual_assets is not None
                        else "BLOCKED_NO_ANNUAL_ASSET_COMPARATOR"
                    ),
                    "h02_period_status": "PASS_ANNUAL_FRESH_ALIGNED"
                    if h02_assets is not None and gross is not None and h02_units_match
                    else "BLOCKED_STALE_OR_UNALIGNED_PERIOD",
                    "h02_unit_status": "PASS" if h02_units_match else "BLOCKED_UNIT_MISMATCH",
                    "h03_unit_status": "PASS" if h03_units_match else "BLOCKED_UNIT_MISMATCH",
                    "h04_unit_status": "PASS" if h04_units_match else "BLOCKED_UNIT_MISMATCH",
                    "h05_unit_status": "PASS" if h05_units_match else "BLOCKED_UNIT_MISMATCH",
                    "features": features,
                    "feature_status": {
                        "h01_book_to_market": "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN",
                        "h02_gross_profitability": "MISSING_INPUT"
                        if features["h02_gross_profitability"] is None
                        else "READY_SCHEMA_ONLY",
                        "h03_accruals": "BLOCKED_UNIT_MISMATCH"
                        if flow_periods_match and h03_assets_match and not h03_units_match
                        else "BLOCKED_PERIOD_MISMATCH"
                        if not flow_periods_match or not h03_assets_match
                        else ("MISSING_INPUT" if features["h03_accruals"] is None else "READY_SCHEMA_ONLY"),
                        "h04_asset_growth": "BLOCKED_NO_ANNUAL_ASSET_COMPARATOR"
                        if not annual_assets or not previous_annual_assets or not h04_units_match
                        else ("MISSING_INPUT" if features["h04_asset_growth"] is None else "READY_SCHEMA_ONLY"),
                        "h05_net_payout_yield": "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN_OR_BREADTH"
                        if features["h05_net_payout_yield"] is not None
                        else "MISSING_INPUT",
                    },
                }
            )
    summary = {
        "row_count": len(rows),
        "symbol_count": len(symbols),
        "decision_count": len(decisions),
        "symbols": symbols,
        "decisions": decisions,
        "current_issuer_cik_pass_rows": sum(
            row["identity_status"].startswith("CURRENT_ISSUER_CIK_MATCHED") for row in rows
        ),
        "historical_security_continuity_unresolved_rows": sum(
            row["historical_security_continuity_status"] == "UNPROVEN" for row in rows
        ),
        "future_predecessor_violations": sum(
            1
            for row in rows
            for fact in row["source_lineage"].values()
            if fact and fact.get("predecessor_available_at") and fact["predecessor_available_at"] > fact["available_at"]
        ),
        "period_alignment_violations": sum(
            1
            for row in rows
            if (row["features"]["h03_accruals"] is not None and row["h03_period_alignment_status"] != "PASS")
            or (
                row["payout_period_key"] is not None
                and any(
                    row["source_lineage"][name] is None
                    or row["source_lineage"][name]["period_key"] != row["payout_period_key"]
                    for name in ("dividend", "repurchase", "issuance")
                )
            )
        ),
        "period_alignment_blocked_rows": sum(row["h03_period_alignment_status"] != "PASS" for row in rows),
        "h03_h04_reuse_violations": sum(
            1
            for row in rows
            if row["h03_period_alignment_status"] == "PASS"
            and row["source_lineage"]["h03_begin_assets"] is not None
            and row["source_lineage"]["previous_assets"] is not None
            and row["source_lineage"]["h03_begin_assets"]["period_key"]
            == row["source_lineage"]["previous_assets"]["period_key"]
            and (
                date.fromisoformat(row["source_lineage"]["income"]["period_key"].split("|")[-2])
                - date.fromisoformat(row["source_lineage"]["h03_begin_assets"]["period_key"].split("|")[-1])
            ).days
            > 7
        ),
        "market_cap_structural_rows": sum(
            row["market_cap_join_status"] == "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN" for row in rows
        ),
        "feature_coverage": {
            name: sum(row["features"][name] is not None for row in rows)
            for name in (
                "h01_book_to_market",
                "h02_gross_profitability",
                "h03_accruals",
                "h04_asset_growth",
                "h05_net_payout_yield",
            )
        },
        "minimum_feature_breadth": 0.50,
        "feature_gate_status": {
            "h01_book_to_market": "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN",
            "h02_gross_profitability": "READY_SCHEMA_ONLY"
            if sum(row["features"]["h02_gross_profitability"] is not None for row in rows) / len(rows) >= 0.50
            else "BLOCKED_BREADTH",
            "h03_accruals": "READY_SCHEMA_ONLY"
            if sum(row["features"]["h03_accruals"] is not None for row in rows) / len(rows) >= 0.50
            else "BLOCKED_BREADTH",
            "h04_asset_growth": "READY_SCHEMA_ONLY"
            if sum(row["features"]["h04_asset_growth"] is not None for row in rows) / len(rows) >= 0.50
            else "BLOCKED_BREADTH",
            "h05_net_payout_yield": "BLOCKED_PRICE_SHARE_BASIS_UNPROVEN_OR_BREADTH",
        },
        "unresolved_ids": unresolved,
        "economic_run": "PROHIBITED_UNTIL_REVIEWER_APPROVES_SCHEMA_AND_PRICE_SHARE_GATE",
    }
    return rows, summary


def main() -> int:
    if OUTPUT.exists() or MANIFEST.exists():
        raise RuntimeError("REFUSE_EXISTING_EQUITY_COMPLETION_OUTPUT")
    rows, summary = build()
    OUTPUT.mkdir(parents=True)
    output = OUTPUT / "causal_input_rows.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    inventory = source_inventory()
    inventory_path = OUTPUT / "source_inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "status": "COMPLETE_DIAGNOSTIC",
        "summary": summary,
        "output": str(output.relative_to(REPO)),
        "output_sha256": sha256(output),
        "runner_sha256": sha256(Path(__file__)),
        "source_inventory": str(inventory_path.relative_to(REPO)),
        "source_inventory_sha256": source_inventory_hash(inventory),
        "input_sha256": {
            str(path.relative_to(REPO)): sha256(path) for path in (FIXED, SELECTION, LIFETIMES, BARS, ACTIONS)
        },
    }
    (OUTPUT / "diagnostic_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "mission": "equity_h01_h05_causal_input_gate_2026_09_08_r11",
        "status": "COMPLETE_DIAGNOSTIC",
        "runner_sha256": receipt["runner_sha256"],
        "source_inventory": receipt["source_inventory"],
        "source_inventory_sha256": receipt["source_inventory_sha256"],
        "selection_rule": fixed_selection_rule(),
        "inputs": receipt["input_sha256"],
        "output": receipt["output"],
        "output_sha256": receipt["output_sha256"],
        "summary": summary,
        "frozen_formulae": {
            "h01": "StockholdersEquity / (split-adjusted close * reported common shares)",
            "h02": "GrossProfit / Assets",
            "h03": "(NetIncomeLoss - NetCashProvidedByUsedInOperatingActivities) / average(current Assets, prior Assets)",
            "h04": "current annual Assets / prior annual Assets - 1",
            "h05": "(abs(dividend payments) + abs(common repurchases) - abs(common issuance)) / market cap",
        },
        "flow_policy": "10-K reported annual flows; 10-Q YTD delta from prior YTD only when predecessor was available by the current flow boundary; missing predecessors block",
        "h03_assets_policy": "ending instant end equals the matched flow end; beginning instant is the latest causally available Assets end 0-7 calendar days before flow start; outside tolerance blocks",
        "h05_alignment_policy": "dividend, repurchase, and issuance must share exact form/unit/start/end and be fresh with duration <= 430 days; a same-span common repurchase always takes precedence and blocks rather than falling back when stale, unavailable, or unusable; equity repurchase is an exact-key fallback only when no common fact exists",
        "decision_policy": "month-end close timestamp from NYSE calendar; each fact available_at must be <= that close; next-session fill required for returns",
        "price_policy": "canonical v5 split-adjusted close and reported shares use the same post-action decision basis; explicit factor is 1.0; any future split fails closed; historical raw-price equivalence remains unproved",
        "identity_policy": "current issuer/CIK ticker match is reported separately from historical common-security continuity, which remains unproven and unresolved",
        "minimum_feature_breadth": 0.50,
        "economic_run": "PROHIBITED_UNTIL_REVIEWER_APPROVES_SCHEMA_AND_PRICE_SHARE_GATE",
        "credential_fields_persisted": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "rows": summary["row_count"],
                "feature_coverage": summary["feature_coverage"],
                "output": receipt["output"],
                "manifest": str(MANIFEST.relative_to(REPO)),
            },
            sort_keys=True,
        )
    )
    return 0


def fixed_selection_rule() -> str:
    return str(json.loads(FIXED.read_text(encoding="utf-8"))["selection_rule"])


if __name__ == "__main__":
    raise SystemExit(main())
