#!/usr/bin/env python3
"""Causal normalization for the immutable SEC Companyfacts pilot.

This is research-only.  Raw SEC payloads remain untouched.  Each normalized
observation keeps its accession, form, period, filed date, acceptance time
when present, unit, and revision lineage.  Rows without an acceptance time use
a conservative next NYSE session-open boundary after the filed date; that
boundary is explicitly marked and is not presented as an exact intraday
availability time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, UTC
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas_market_calendars as market_calendars


REPO = Path(__file__).resolve().parents[2]
INPUT = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
OUTPUT = Path(
    os.environ.get(
        "PARAYABUCK_SEC_NORMALIZED_OUTPUT",
        str(REPO / "data/research/external/equity_fundamentals_normalized_2026_09_07"),
    )
).resolve()
ARCHIVED = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
TAGS = {
    "Assets",
    "StockholdersEquity",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "GrossProfit",
    "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsOfDividends",
    "PaymentsForRepurchaseOfCommonStock",
    "PaymentsForRepurchaseOfEquity",
    "ProceedsFromStockOptionsExercised",
    "ProceedsFromIssuanceOfCommonStock",
}
FLOW_TAGS = TAGS - {"Assets", "StockholdersEquity"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _iso_date(value: str | None) -> str | None:
    return value if value and len(value) == 10 else None


def _available(row: Mapping[str, Any], accepted: str | None) -> tuple[str | None, str]:
    if accepted:
        return accepted, "accepted_timestamp"
    filed = _iso_date(str(row.get("filed", "")))
    if not filed:
        return None, "missing_filed_and_accepted"
    # A filing date without time is only safe at the next NYSE session open.
    # This avoids a UTC-midnight boundary that could precede a late filing's
    # actual publication and gives a daily signal a causal next-session gate.
    filed_date = date.fromisoformat(filed)
    calendar = market_calendars.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=filed_date, end_date=filed_date + timedelta(days=14))
    future = schedule[schedule.index.date > filed_date]
    if future.empty:
        return None, "missing_next_nyse_session"
    return future.iloc[0]["market_open"].to_pydatetime().isoformat(), "filed_date_next_nyse_open"


def _submission_acceptance(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.glob("submissions_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        filings = payload.get("filings", {}).get("recent", {})
        columns = {key: filings.get(key, []) for key in ("accessionNumber", "acceptanceDateTime")}
        for accn, accepted in zip(columns["accessionNumber"], columns["acceptanceDateTime"]):
            if accn and accepted:
                result[str(accn)] = str(accepted)
    # Historical submissions files use top-level parallel arrays rather than
    # the current endpoint's filings.recent object.
    for path in ARCHIVED.glob("CIK*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for accn, accepted in zip(payload.get("accessionNumber", []), payload.get("acceptanceDateTime", [])):
            if accn and accepted:
                result[str(accn)] = str(accepted)
    return result


def _revision_key(row: Mapping[str, Any]) -> str:
    return "|".join(
        str(row.get(key, ""))
        for key in ("cik", "tag", "unit", "start", "end")
    )


def _raw_rows(root: Path) -> list[dict[str, Any]]:
    acceptance = _submission_acceptance(root)
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("companyfacts_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cik = str(payload.get("cik", path.stem.removeprefix("companyfacts_"))).zfill(10)
        for namespace, facts in payload.get("facts", {}).items():
            for tag, definition in facts.items():
                if tag not in TAGS:
                    continue
                for unit, observations in definition.get("units", {}).items():
                    for observation in observations:
                        accn = str(observation.get("accn", ""))
                        accepted = acceptance.get(accn)
                        available_at, availability_basis = _available(observation, accepted)
                        rows.append(
                            {
                                "source": "sec_edgar_companyfacts",
                                "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                                "cik": cik,
                                "namespace": namespace,
                                "tag": tag,
                                "unit": str(unit),
                                "value": observation.get("val"),
                                "accn": accn,
                                "form": observation.get("form"),
                                "fy": observation.get("fy"),
                                "fp": observation.get("fp"),
                                "frame": observation.get("frame"),
                                "start": observation.get("start"),
                                "end": observation.get("end"),
                                "filed": observation.get("filed"),
                                "accepted": accepted,
                                "available_at": available_at,
                                "availability_basis": availability_basis,
                            }
                        )
    return rows


def _with_lineage(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["revision_group"] = _revision_key(row)
        grouped[row["revision_group"]].append(row)
    for group_rows in grouped.values():
        group_rows.sort(key=lambda r: (r.get("available_at") or "9999", r.get("accn") or ""))
        for sequence, row in enumerate(group_rows, start=1):
            row["revision_sequence"] = sequence
            row["revision_count"] = len(group_rows)
            row["revision_is_latest_observed"] = sequence == len(group_rows)
    return sorted(rows, key=lambda r: (r["cik"], r["tag"], r.get("end") or "", r.get("available_at") or ""))


def _with_flow_conversion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ytd_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["period_type"] = "instant" if not row.get("start") else "duration"
        row["flow_basis"] = "not_applicable"
        row["normalized_flow_value"] = None
        if row["tag"] not in FLOW_TAGS or not row.get("start") or not row.get("end"):
            continue
        form = str(row.get("form") or "")
        fy = str(row.get("fy") or "")
        if form == "10-K":
            row["normalized_flow_value"] = row["value"]
            row["flow_basis"] = "annual_10k_reported_period"
        elif form == "10-Q":
            key = (row["cik"], row["tag"], row["unit"], fy, row["start"])
            ytd_groups[key].append(row)
    for group in ytd_groups.values():
        group.sort(key=lambda r: (r.get("end") or "", r.get("available_at") or "", r.get("accn") or ""))
        prior_by_end: dict[str, dict[str, Any]] = {}
        for row in group:
            end = str(row.get("end"))
            prior = max((x for x in prior_by_end.values() if x.get("end", "") < end), key=lambda x: x.get("end", ""), default=None)
            # Q1 is its own period; later 10-Q YTD rows become quarter deltas.
            if prior is None:
                row["normalized_flow_value"] = row["value"]
                row["flow_basis"] = "q1_ytd_as_first_period"
            elif isinstance(row.get("value"), (int, float)) and isinstance(prior.get("value"), (int, float)):
                row["normalized_flow_value"] = row["value"] - prior["value"]
                row["flow_basis"] = "10q_ytd_delta_from_prior_ytd"
            else:
                row["flow_basis"] = "blocked_non_numeric_ytd"
            prior_by_end[end] = row
    return rows


def normalize_companyfacts(root: Path = INPUT) -> list[dict[str, Any]]:
    return _with_flow_conversion(_with_lineage(_raw_rows(root)))


def asof(rows: Iterable[Mapping[str, Any]], available_at: str) -> list[dict[str, Any]]:
    """Return the latest known revision for each period at a causal boundary."""
    eligible = [dict(row) for row in rows if row.get("available_at") and row["available_at"] <= available_at]
    latest: dict[str, dict[str, Any]] = {}
    for row in eligible:
        key = str(row["revision_group"])
        old = latest.get(key)
        if old is None or (row.get("available_at", ""), row.get("accn", "")) > (old.get("available_at", ""), old.get("accn", "")):
            latest[key] = row
    return sorted(latest.values(), key=lambda row: (row["cik"], row["tag"], row.get("end") or ""))


def split_consistent_market_cap(
    price: float,
    shares: float,
    *,
    price_basis: str,
    shares_basis: str,
    cumulative_split_factor: float | None = None,
) -> float:
    """Guard a market-cap join against adjusted-price/share-basis mismatch.

    SEC shares are reported in the issuer's contemporaneous basis.  A raw
    price can join directly to those shares.  A split-adjusted historical
    price requires an explicit, as-of cumulative split factor so the share
    count is adjusted by the inverse price adjustment.  Missing factors fail
    closed; an end-of-sample factor must never be silently reused.
    """
    if price <= 0 or shares < 0:
        raise ValueError("price and shares must be non-negative, with price > 0")
    if price_basis == "raw" and shares_basis == "reported":
        return float(price) * float(shares)
    if price_basis == "split_adjusted" and shares_basis == "reported":
        if cumulative_split_factor is None or cumulative_split_factor <= 0:
            raise ValueError("split_adjusted price requires an explicit positive as-of split factor")
        return float(price) * float(shares) * float(cumulative_split_factor)
    raise ValueError(f"unsupported price/share basis: {price_basis}/{shares_basis}")


def main() -> int:
    rows = normalize_companyfacts()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    normalized = OUTPUT / "normalized_observations.jsonl"
    with normalized.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    receipt = {
        "status": "COMPLETE",
        "provider": "U.S. Securities and Exchange Commission EDGAR",
        "input_directory": str(INPUT.relative_to(REPO)),
        "input_receipt_sha256": sha256(INPUT / "acquisition_receipt.json"),
        "output": str(normalized.relative_to(REPO)),
        "output_sha256": sha256(normalized),
        "row_count": len(rows),
        "ciks": sorted({row["cik"] for row in rows}),
        "schema": {
            "lineage": ["accn", "form", "start", "end", "filed", "accepted", "revision_group", "revision_sequence"],
            "causal_boundary": ["available_at", "availability_basis"],
            "units": ["unit", "value"],
            "flows": ["normalized_flow_value", "flow_basis"],
        },
        "pit_limitations": [
            "Current Companyfacts endpoint is not a historical response snapshot.",
            "Rows without acceptanceDateTime use a conservative next-day filed-date boundary.",
            "PIT economic universe membership and split-consistent market-cap joins remain separate gates.",
        ],
        "credential_fields_persisted": False,
    }
    receipt_path = OUTPUT / "normalization_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"NORMALIZED rows={len(rows)} output={normalized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
