#!/usr/bin/env python3
"""Count Schedule 13D index rows without loading any price or return data."""

from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

INITIAL_FORMS = {"SC 13D", "SCHEDULE 13D"}
AMENDMENT_FORMS = {"SC 13D/A", "SCHEDULE 13D/A"}
PERIOD_UNAVAILABLE = {
    "verified_mapped_issuer_events": None,
    "unmapped_events": None,
    "ambiguous_security_class_events": None,
    "qualifying_activist_events": None,
    "verified_unique_issuers": None,
    "simultaneous_live_opportunities": None,
    "event_frequency": None,
    "acceptance_hour_distribution": None,
    "after_hours_frequency": None,
    "repeat_campaign_frequency": None,
    "potential_portfolio_occupancy": None,
    "unavailable_reason_code": "RAW_ACCESSION_CORPUS_REQUIRED",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


INDEX_NAME = re.compile(r"^(?P<year>20\d{2})-QTR(?P<quarter>[1-4])\.gz$")


def parse_index(path: Path) -> tuple[list[dict[str, str]], str]:
    rows: list[dict[str, str]] = []
    last_data_received = ""
    with gzip.open(path, "rt", encoding="latin-1") as handle:
        for raw in handle:
            if raw.startswith("Last Data Received:"):
                last_data_received = raw.split(":", 1)[1].strip()
            fields = raw.rstrip("\n").split("|")
            if len(fields) != 5 or fields[2] not in INITIAL_FORMS | AMENDMENT_FORMS:
                continue
            rows.append(
                {
                    "index_cik": fields[0],
                    "index_company_name": fields[1],
                    "form": fields[2],
                    "filed_date": fields[3],
                    "archive_filename": fields[4],
                }
            )
    if not last_data_received:
        raise ValueError(f"missing SEC Last Data Received header: {path}")
    return rows, last_data_received


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("index_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    files = sorted(args.index_dir.glob("*.gz"))
    if not files:
        raise SystemExit("no .gz indexes found")

    all_rows: list[dict[str, str]] = []
    sources = []
    seen_quarters: set[tuple[int, int]] = set()
    for path in files:
        match = INDEX_NAME.fullmatch(path.name)
        if not match:
            raise ValueError(f"unexpected index filename: {path.name}")
        year = int(match.group("year"))
        quarter = int(match.group("quarter"))
        if (year, quarter) in seen_quarters:
            raise ValueError(f"duplicate quarter: {year} Q{quarter}")
        seen_quarters.add((year, quarter))
        rows, last_data_received = parse_index(path)
        all_rows.extend(rows)
        quarter_end_month = quarter * 3
        quarter_end = datetime(
            year,
            quarter_end_month,
            calendar.monthrange(year, quarter_end_month)[1],
        ).date()
        source_as_of = datetime.strptime(last_data_received, "%B %d, %Y").date()
        source_kind = "static_quarterly" if source_as_of >= quarter_end else "current_full_index"
        source_path = f"/Archives/edgar/full-index/{year}/QTR{quarter}/master.gz"
        sources.append(
            {
                "file": path.name,
                "canonical_sec_url": f"https://www.sec.gov{source_path}",
                "source_index_path": source_path,
                "index_kind": source_kind,
                "last_data_received": last_data_received,
                "local_file_mtime_utc": datetime.fromtimestamp(
                    path.stat().st_mtime, UTC
                ).isoformat(),
                "byte_length": path.stat().st_size,
                "sha256": sha256(path),
                "schedule_13d_family_rows": len(rows),
            }
        )

    initials = [row for row in all_rows if row["form"] in INITIAL_FORMS]
    amendments = [row for row in all_rows if row["form"] in AMENDMENT_FORMS]
    by_month_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in initials:
        by_month_rows[row["filed_date"][:7]].append(row)

    by_month = []
    for month in sorted(by_month_rows):
        rows = by_month_rows[month]
        cik_counts = Counter(row["index_cik"] for row in rows)
        by_month.append(
            {
                "month": month,
                "initial_filing_rows": len(rows),
                "legacy_sc_13d_rows": sum(row["form"] == "SC 13D" for row in rows),
                "xml_schedule_13d_rows": sum(row["form"] == "SCHEDULE 13D" for row in rows),
                "unique_index_ciks": len(cik_counts),
                "index_ciks_with_multiple_initial_rows": sum(value > 1 for value in cik_counts.values()),
                **PERIOD_UNAVAILABLE,
            }
        )

    year_months: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in by_month:
        year_months[str(row["month"])[:4]].append(row)
    by_year = []
    for year in sorted(year_months):
        rows = year_months[year]
        year_initials = [row for row in initials if row["filed_date"].startswith(year)]
        year_ciks = Counter(row["index_cik"] for row in year_initials)
        by_year.append(
            {
                "year": int(year),
                "initial_filing_rows": sum(int(row["initial_filing_rows"]) for row in rows),
                "legacy_sc_13d_rows": sum(int(row["legacy_sc_13d_rows"]) for row in rows),
                "xml_schedule_13d_rows": sum(int(row["xml_schedule_13d_rows"]) for row in rows),
                "unique_index_ciks": len(year_ciks),
                "index_ciks_with_multiple_initial_rows": sum(value > 1 for value in year_ciks.values()),
                **PERIOD_UNAVAILABLE,
            }
        )

    initial_ciks = Counter(row["index_cik"] for row in initials)
    result = {
        "schema_version": "schedule_13d_structural_audit_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "economic_data_accessed": False,
        "coverage": {
            "start": min(row["filed_date"] for row in all_rows),
            "end": max(row["filed_date"] for row in all_rows),
            "official_sec_index_files": len(files),
            "static_quarterly_indexes": sum(
                source["index_kind"] == "static_quarterly" for source in sources
            ),
            "current_full_indexes": sum(
                source["index_kind"] == "current_full_index" for source in sources
            ),
            "note": "The current-quarter full index is partial through its persisted Last Data Received vintage.",
        },
        "definitions": {
            "initial": "Exact SEC master-index form type SC 13D or SCHEDULE 13D.",
            "amendment": "Exact SEC master-index form type SC 13D/A or SCHEDULE 13D/A.",
            "index_cik_warning": "Index CIK/name are discovery metadata, not a verified subject-security or reporting-group identity.",
        },
        "totals": {
            "initial_filing_rows": len(initials),
            "amendment_filing_rows": len(amendments),
            "legacy_sc_13d_rows": sum(row["form"] == "SC 13D" for row in initials),
            "xml_schedule_13d_rows": sum(row["form"] == "SCHEDULE 13D" for row in initials),
            "unique_index_ciks": len(initial_ciks),
            "index_ciks_with_multiple_initial_rows": sum(value > 1 for value in initial_ciks.values()),
        },
        "by_year": by_year,
        "by_month": by_month,
        "unavailable_without_accession_corpus": {
            "verified_mapped_issuer_events": None,
            "unmapped_events": None,
            "ambiguous_security_class_events": None,
            "qualifying_activist_events": None,
            "verified_unique_issuers": None,
            "simultaneous_live_opportunities": None,
            "acceptance_hour_distribution": None,
            "after_hours_frequency": None,
            "repeat_campaign_frequency": None,
            "potential_portfolio_occupancy": None,
            "reason": "Quarter indexes do not contain raw acceptance timestamps, covered security classes, Item 4 text, complete reporting-person rosters, or amendment/campaign linkage. Reporting these fields from index rows would fabricate provenance.",
        },
        "sources": sources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
