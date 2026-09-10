"""Fetch or verify official Treasury daily curve pages for X01.

The network probe writes only sanitized HTTP/error receipts. Existing
hash-pinned year files are reused when the public endpoint is unavailable.
No credentials are used or stored.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_treasury"
EXISTING = REPO / "data/research/external/crossasset_public_2026_09_07/x01_treasury"
BASE_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
YEARS = tuple(range(2016, 2027))
COMMON_HEADERS = ("Date", "1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr")
ALLOWED_HEADERS_BY_YEAR = {
    **{year: COMMON_HEADERS for year in (2016, 2017)},
    **{year: ("Date", "1 Mo", "2 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr") for year in (2018, 2019, 2020, 2021)},
    **{year: ("Date", "1 Mo", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr") for year in (2022, 2023, 2024)},
    **{year: ("Date", "1 Mo", "1.5 Month", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr") for year in (2025, 2026)},
}
REQUIRED_HEADERS = set(COMMON_HEADERS)
OPTIONAL_ONSET_DATES = {
    "2 Mo": date(2018, 10, 16),
    "4 Mo": date(2022, 10, 19),
    "1.5 Month": date(2025, 2, 18),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _endpoint(year: int) -> str:
    return BASE_URL.format(year=year)


def _existing_year(year: int) -> Path:
    return EXISTING / f"treasury_daily_yield_curve_{year}.csv"


def _validate_treasury_text(text: str, year: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not text.strip():
        raise ValueError(f"Treasury payload is empty for {year}")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    expected_fields = list(ALLOWED_HEADERS_BY_YEAR[year])
    if fields != expected_fields:
        raise ValueError(f"Treasury header/schema does not match frozen {year} set")
    rows = list(reader)
    if not rows:
        raise ValueError(f"Treasury payload is empty for {year}")
    dates = [row.get("Date", "") for row in rows]
    parsed_dates: list[datetime] = []
    for row in rows:
        if None in row or any(row.get(field) is None or not str(row.get(field, "")).strip() for field in COMMON_HEADERS):
            raise ValueError(f"Treasury row width or empty required value for {year}")
        try:
            parsed = datetime.strptime(row["Date"], "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(f"Treasury date format is invalid for {year}") from exc
        if parsed.year != year:
            raise ValueError(f"Treasury row date is outside requested year {year}")
        for field in expected_fields:
            if field in OPTIONAL_ONSET_DATES:
                value = str(row[field]).strip()
                if parsed.date() < OPTIONAL_ONSET_DATES[field] and value:
                    raise ValueError(f"Treasury {field} has a pre-onset value for {year}")
                if parsed.date() >= OPTIONAL_ONSET_DATES[field] and not value:
                    raise ValueError(f"Treasury {field} is blank after onset for {year}")
        parsed_dates.append(parsed)
        try:
            finite = all(math.isfinite(float(row[field])) for field in expected_fields if field != "Date" and str(row[field]).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Treasury yield is nonfinite for {year}") from exc
        if not finite:
            raise ValueError(f"Treasury yield is nonfinite for {year}")
    if len(parsed_dates) != len(set(parsed_dates)):
        raise ValueError(f"Treasury dates are duplicated for {year}")
    if parsed_dates != sorted(parsed_dates):
        raise ValueError(f"Treasury dates are not strictly increasing for {year}")
    coverage = {"rows": len(rows), "first": min(dates), "last": max(dates), "columns": list(fields), "requested_year": year, "all_dates_in_requested_year": True, "dates_strictly_increasing": True}
    return rows, coverage


def _canonicalize_treasury_text(text: str, year: int) -> str:
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    expected_fields = list(ALLOWED_HEADERS_BY_YEAR[year])
    if fields != expected_fields:
        raise ValueError(f"Treasury header/schema does not match frozen {year} set")
    rows = list(reader)
    parsed: list[tuple[datetime, dict[str, str]]] = []
    for row in rows:
        if None in row or any(row.get(field) is None or not str(row.get(field, "")).strip() for field in COMMON_HEADERS):
            raise ValueError(f"Treasury row width or empty required value for {year}")
        try:
            date = datetime.strptime(row["Date"], "%m/%d/%Y")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Treasury date format is invalid for {year}") from exc
        if date.year != year:
            raise ValueError(f"Treasury row date is outside requested year {year}")
        for field in expected_fields:
            if field in OPTIONAL_ONSET_DATES:
                value = str(row[field]).strip()
                if date.date() < OPTIONAL_ONSET_DATES[field] and value:
                    raise ValueError(f"Treasury {field} has a pre-onset value for {year}")
                if date.date() >= OPTIONAL_ONSET_DATES[field] and not value:
                    raise ValueError(f"Treasury {field} is blank after onset for {year}")
        try:
            if any(not math.isfinite(float(row[field])) for field in expected_fields if field != "Date" and str(row[field]).strip()):
                raise ValueError(f"Treasury yield is nonfinite for {year}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Treasury yield is nonfinite for {year}") from exc
        parsed.append((date, row))
    if len({date for date, _row in parsed}) != len(parsed):
        raise ValueError(f"Treasury dates are duplicated for {year}")
    parsed.sort(key=lambda item: item[0])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=expected_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(row for _date, row in parsed)
    return output.getvalue()


def _validate_offline_file(path: Path, year: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Validate an existing file in its raw order before any canonicalization."""
    return _validate_treasury_text(path.read_text(encoding="utf-8"), year)


def _coverage(path: Path, year: int) -> dict[str, Any]:
    rows, coverage = _validate_treasury_text(path.read_text(encoding="utf-8"), year)
    dates = [row["Date"] for row in rows]
    coverage["main_cutoff_rows"] = sum(datetime.strptime(value, "%m/%d/%Y").date().isoformat() <= "2026-07-31" for value in dates)
    coverage["post_cutoff_rows"] = sum(datetime.strptime(value, "%m/%d/%Y").date().isoformat() > "2026-07-31" for value in dates)
    return coverage


def _bundle_admission(records: list[dict[str, Any]]) -> tuple[bool, str]:
    admitted = len(records) == len(YEARS) and {record.get("year") for record in records} == set(YEARS) and all(
        record.get("status") == "DOWNLOADED"
        and bool(record.get("path"))
        and bool(record.get("sha256"))
        and record.get("coverage", {}).get("dates_strictly_increasing") is True
        and (REPO / str(record["path"])).is_file()
        and sha256(REPO / str(record["path"])) == record["sha256"]
        for record in records
    )
    return admitted, "COMPLETE" if admitted else "FAILED"


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for year in YEARS:
        url = _endpoint(year)
        record: dict[str, Any] = {"year": year, "url": url, "query": {"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "page": "", "_format": "csv"}}
        try:
            response = requests.get(url, params=record["query"], timeout=30)
            record["http_status"] = response.status_code
            content_type = response.headers.get("content-type", "")
            record["content_type"] = content_type.split(";", 1)[0]
            if response.status_code == 200:
                try:
                    rows, coverage = _validate_treasury_text(response.text, year)
                except ValueError as exc:
                    record["status"] = "PROBE_FAILED"
                    record["error"] = str(exc)
                else:
                    path = OUT / f"treasury_daily_yield_curve_{year}.csv"
                    path.write_text(response.text, encoding="utf-8")
                    record["status"] = "DOWNLOADED"
                    record["path"] = str(path.relative_to(REPO))
                    record["sha256"] = sha256(path)
                    record["coverage"] = coverage
                    records.append(record)
                    continue
            else:
                record["status"] = "PROBE_FAILED"
                record["error"] = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            record["status"] = "PROBE_FAILED"
            record["error"] = type(exc).__name__ + ": " + str(exc)
        existing = _existing_year(year)
        if existing.is_file():
            record["fallback_status"] = "REUSED_EXISTING_HASH_PINNED_FILE"
            record["fallback_path"] = str(existing.relative_to(REPO))
            record["fallback_sha256"] = sha256(existing)
            try:
                record["fallback_coverage"] = _coverage(existing, year)
            except ValueError as exc:
                record["fallback_status"] = "INVALID_EXISTING_FILE"
                record["fallback_error"] = str(exc)
        else:
            record["fallback_status"] = "MISSING"
        records.append(record)
    bundle_admitted, bundle_status = _bundle_admission(records)
    receipt = {"schema": "x01-treasury-fetch-receipt-v1", "main_cutoff": "2026-07-31", "requested_start": "2016-01-01", "requested_end": "2026-12-31", "requested_years": list(YEARS), "endpoint": BASE_URL, "records": records, "network_downloaded_years": [r["year"] for r in records if r.get("status") == "DOWNLOADED"], "reused_years": [r["year"] for r in records if r.get("fallback_status") == "REUSED_EXISTING_HASH_PINNED_FILE"], "missing_years": [r["year"] for r in records if r.get("fallback_status") == "MISSING"], "bundle_admitted": bundle_admitted, "status": bundle_status}
    path = OUT / "official_treasury_year_fetch_receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def offline_verify() -> dict[str, Any]:
    """Validate already downloaded task files without making network calls."""
    receipt_path = OUT / "official_treasury_year_fetch_receipt.json"
    prior = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {"records": []}
    prior_by_year = {int(item["year"]): item for item in prior.get("records", [])}
    records: list[dict[str, Any]] = []
    for year in YEARS:
        path = OUT / f"treasury_daily_yield_curve_{year}.csv"
        record = dict(prior_by_year.get(year, {"year": year, "url": _endpoint(year)}))
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            _validate_offline_file(path, year)
            canonical = _canonicalize_treasury_text(path.read_text(encoding="utf-8"), year)
            path.write_text(canonical, encoding="utf-8")
            _rows, coverage = _validate_treasury_text(canonical, year)
            record.update({"year": year, "status": "DOWNLOADED", "path": str(path.relative_to(REPO)), "sha256": sha256(path), "coverage": coverage, "offline_validation": True})
        except (OSError, ValueError) as exc:
            record.update({"year": year, "status": "FAILED", "error": str(exc), "offline_validation": False})
        records.append(record)
    bundle_admitted, bundle_status = _bundle_admission(records)
    receipt = {"schema": "x01-treasury-fetch-receipt-v1", "main_cutoff": "2026-07-31", "requested_start": "2016-01-01", "requested_end": "2026-12-31", "requested_years": list(YEARS), "endpoint": BASE_URL, "records": records, "network_downloaded_years": [r["year"] for r in records if r.get("status") == "DOWNLOADED"], "reused_years": [], "missing_years": [r["year"] for r in records if r.get("status") == "FAILED"], "bundle_admitted": bundle_admitted, "status": bundle_status, "offline_validation": True}
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(offline_verify() if args.offline_verify else main(), indent=2, sort_keys=True))
