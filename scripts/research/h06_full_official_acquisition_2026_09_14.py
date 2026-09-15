#!/usr/bin/env python3
"""Deterministic, fair-access H06 SEC inventory and acquisition gate.

This module deliberately stops before normalization when any official source is
missing.  It contains no economic fields or strategy calculations.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
LOCAL_ROOT = REPO / "data/research/local/h06_full_acquisition_2026_09_14"
MANIFEST = REPO / "data/research_manifests/h06_full_official_acquisition_2026_09_14.json"
RECEIPT = REPO / "data/research_receipts/h06_full_official_acquisition_receipt_2026_09_14.json"
STATUS = "H06_FULL_ACQUISITION_INCOMPLETE"
SCHEMA = "h06_full_official_acquisition_v1"
MAX_REQUESTS_PER_SECOND = 5
QUARTER_CONFIGURED_REQUEST_STARTS_PER_SECOND = 1
JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND = 4
JULY_DEFAULT_WORKERS = 4
METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND = 1
DAILY_FEED_CONFIGURED_REQUEST_STARTS_PER_SECOND = 4
DAILY_FEED_ACQUISITION_CONCURRENCY = 4
DEFAULT_REQUESTS_PER_SECOND = QUARTER_CONFIGURED_REQUEST_STARTS_PER_SECOND
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_SECONDS = [1, 2, 4]
LANDING_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
JULY_DIRECTORY_URL = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/index.json"
SUBMISSIONS_BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
LANDING_CACHE = LOCAL_ROOT / "source_discovery/insider-transactions-data-sets.html"
JULY_DIRECTORY_CACHE = LOCAL_ROOT / "source_discovery/qtr3-index.json"
STATE_PATH = LOCAL_ROOT / "acquisition_state.json"
JULY_GROUP_AUDIT_PATH = LOCAL_ROOT / "july_accession_group_audit.json"
JULY_ALIAS_CANARY_PATH = LOCAL_ROOT / "july_participant_alias_canaries.json"
SUBMISSIONS_BULK_PATH = LOCAL_ROOT / "sec_submissions_bulk/submissions.zip"
SUBMISSIONS_BULK_RECEIPT_PATH = LOCAL_ROOT / "sec_submissions_bulk/receipt.json"
RELEVANT_TARGET_PATH = LOCAL_ROOT / "relevant_accession_target.tsv"
ACCEPTANCE_MAP_PATH = LOCAL_ROOT / "relevant_accession_acceptance_map.tsv"
ACCEPTANCE_MAP_RECEIPT_PATH = LOCAL_ROOT / "relevant_accession_acceptance_map_receipt.json"
ACCEPTANCE_REPRESENTATION_CANARY_PATH = LOCAL_ROOT / "bulk_acceptance_representation_canaries.json"
OFFLINE_REBUILD_PATH = LOCAL_ROOT / "official_source_offline_rebuild.json"
DAILY_FEED_ROOT = LOCAL_ROOT / "sec_daily_feed_fallback"
DAILY_FEED_RECEIPT_PATH = DAILY_FEED_ROOT / "receipt.json"
DAILY_OLDLOADS_ROOT = LOCAL_ROOT / "sec_daily_oldloads_fallback"
DAILY_OLDLOADS_RECEIPT_PATH = DAILY_OLDLOADS_ROOT / "receipt.json"
DAILY_FEED_FALLBACKS: dict[str, dict[str, Any]] = {
    "2025-01-08": {
        "url": "https://www.sec.gov/Archives/edgar/Feed/2025/QTR1/20250108.nc.tar.gz",
        "accessions": ["0000950170-25-003425"],
    },
    "2025-03-20": {
        "url": "https://www.sec.gov/Archives/edgar/Feed/2025/QTR1/20250320.nc.tar.gz",
        "accessions": ["0001225208-25-003519"],
    },
    "2025-06-06": {
        "url": "https://www.sec.gov/Archives/edgar/Feed/2025/QTR2/20250606.nc.tar.gz",
        "accessions": ["0001641172-25-013948"],
    },
}
DAILY_OLDLOADS_FALLBACKS: dict[str, dict[str, Any]] = {
    "2025-01-08": {
        "url": "https://www.sec.gov/Archives/edgar/Oldloads/2025/QTR1/20250108.gz",
        "accessions": ["0000950170-25-003425"],
    },
    "2025-03-20": {
        "url": "https://www.sec.gov/Archives/edgar/Oldloads/2025/QTR1/20250320.gz",
        "accessions": ["0001225208-25-003519"],
    },
    "2025-06-06": {
        "url": "https://www.sec.gov/Archives/edgar/Oldloads/2025/QTR2/20250606.gz",
        "accessions": ["0001641172-25-013948"],
    },
}
EXPECTED_ZIP_MEMBERS = {
    "DERIV_HOLDING.tsv",
    "DERIV_TRANS.tsv",
    "FOOTNOTES.tsv",
    "FORM_345_metadata.json",
    "FORM_345_readme.htm",
    "NONDERIV_HOLDING.tsv",
    "NONDERIV_TRANS.tsv",
    "OWNER_SIGNATURE.tsv",
    "REPORTINGOWNER.tsv",
    "SUBMISSION.tsv",
}
REQUIRED_TSV_MEMBERS = {name for name in EXPECTED_ZIP_MEMBERS if name.endswith(".tsv")}
PINNED_ACQUIRED = {
    "2026Q2": {
        "bytes": 11_498_860,
        "sha256": "11f1b2bbbdcbe6347a34437c02d04202fda0eca1dbb023726e4b56504b802e27",
        "retrieved_at": "2026-09-14T19:51:44Z",
        "form_4_count": 49_832,
        "form_4_a_count": 713,
        "unique_issuer_ciks": 4_508,
        "unique_reporting_owner_ciks": 30_423,
    }
}
RETRYABLE_HTTP = {403, 429, 500, 502, 503, 504}
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}")
EASTERN = ZoneInfo("America/New_York")
OLDLOADS_CONTROL_BLOCK_BYTES = 266


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def nested_self_hash(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return sha256_bytes(canonical(payload))


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("integrity", {})["self_sha256"] = nested_self_hash(value)
    return value


def verify_seal(value: dict[str, Any]) -> None:
    actual = value.get("integrity", {}).get("self_sha256")
    if not isinstance(actual, str) or actual != nested_self_hash(value):
        raise ValueError("self_hash_mismatch")


def _env_file_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip().strip("\"'")
    return None


def resolve_sec_user_agent() -> tuple[str, str]:
    """Resolve a declared contact identity without logging or persisting it."""
    direct = os.environ.get("SEC_USER_AGENT", "").strip()
    if direct:
        if not EMAIL_RE.search(direct):
            raise ValueError("SEC_USER_AGENT_STILL_UNAVAILABLE")
        return direct, "ENV"
    for path in (REPO / ".env", REPO / ".env.local"):
        value = (_env_file_value(path, "SEC_USER_AGENT") or "").strip()
        if value:
            if not EMAIL_RE.search(value):
                raise ValueError("SEC_USER_AGENT_STILL_UNAVAILABLE")
            return value, "LOCAL_ENV_FILE"
    name = subprocess.run(
        ["git", "config", "user.name"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "config", "user.email"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not name or not EMAIL_RE.fullmatch(email):
        raise ValueError("SEC_USER_AGENT_STILL_UNAVAILABLE")
    return f"ParayaBuck Research {name} {email}", "LOCAL_GIT_IDENTITY"


def decode_http_body(body: bytes, content_encoding: str | None) -> bytes:
    if content_encoding == "gzip":
        return gzip.decompress(body)
    if content_encoding == "deflate":
        return zlib.decompress(body)
    return body


def discover_quarter_urls(body: bytes) -> dict[str, dict[str, str]]:
    text = body.decode("utf-8", "replace")
    rows: dict[str, list[dict[str, str]]] = {}
    pattern = re.compile(
        r"<tr[^>]*>.*?<a\s+href=[\"']([^\"']+_form345\.zip)[\"'][^>]*>\s*"
        r"(20\d{2})\s+Q([1-4])\s+345\s*</a>.*?"
        r"views-field-filesize[^>]*>\s*([^<]+?)\s*</td>.*?</tr>",
        re.IGNORECASE | re.DOTALL,
    )
    for href, year, quarter, displayed_size in pattern.findall(text):
        period = f"{year}Q{quarter}"
        rows.setdefault(period, []).append(
            {
                "period": period,
                "published_label": f"{year} Q{quarter} 345",
                "url": urllib.parse.urljoin(LANDING_URL, href),
                "page_displayed_size": " ".join(displayed_size.split()),
            }
        )
    expected = expected_quarters()
    if any(len(rows.get(period, [])) != 1 for period in expected):
        raise ValueError("SEC_QUARTER_URL_DISCOVERY_AMBIGUOUS")
    return {period: rows[period][0] for period in expected}


def expected_quarters() -> list[str]:
    return [f"{year}Q{quarter}" for year in range(2019, 2027) for quarter in range(1, (2 if year == 2026 else 4) + 1)]


def quarter_inventory(discovered: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in range(2019, 2027):
        last_quarter = 2 if year == 2026 else 4
        for quarter in range(1, last_quarter + 1):
            name = f"{year}q{quarter}_form345.zip"
            period = f"{year}Q{quarter}"
            published = discovered.get(period) if discovered else None
            prefix = "datastandardsinnovation/data" if (year, quarter) == (2026, 2) else "structureddata/data"
            rows.append(
                {
                    "period": period,
                    "coverage": f"{year} Q{quarter}",
                    "published_label": published["published_label"] if published else f"{year} Q{quarter} 345",
                    "url": published["url"]
                    if published
                    else f"https://www.sec.gov/files/{prefix}/insider-transactions-data-sets/{name}",
                    "page_displayed_size": published["page_displayed_size"] if published else None,
                    "local_path": f"data/research/local/h06_full_acquisition_2026_09_14/sec_bulk/{name}",
                    "source_role": "official_sec_insider_transactions_quarterly_package",
                    "retrieval_status": "NOT_ACQUIRED",
                    "retrieved_at": None,
                    "bytes": None,
                    "sha256": None,
                }
            )
    return rows


def validate_quarter_inventory(rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    expected = expected_quarters()
    actual = [str(row["period"]) for row in rows]
    if actual != expected or len(actual) != 30 or len(set(actual)) != 30:
        raise ValueError("quarter_inventory_incomplete_or_duplicate")


def validate_cached_source(row: dict[str, Any], root: Path = REPO) -> None:
    path = root / str(row["local_path"])
    expected_sha = row.get("sha256")
    expected_bytes = row.get("bytes")
    if not expected_sha or expected_bytes is None or not path.is_file():
        raise ValueError(f"source_not_acquired:{row['period']}")
    if path.stat().st_size != expected_bytes:
        raise ValueError(f"source_byte_count_mismatch:{row['period']}")
    if sha256_file(path) != expected_sha:
        raise ValueError(f"source_sha256_mismatch:{row['period']}")
    validate_zip_file(path, str(row["period"]))


def validate_zip_file(path: Path, period: str) -> None:
    if path.read_bytes()[:4] != b"PK\x03\x04":
        raise ValueError(f"source_zip_magic_invalid:{period}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            metadata = {name for name in names if name.casefold().endswith("_metadata.json")}
            readme = {name for name in names if name.casefold().endswith("_readme.htm")}
            if (
                len(names) != 10
                or not REQUIRED_TSV_MEMBERS.issubset(names)
                or len(metadata) != 1
                or len(readme) != 1
                or archive.testzip() is not None
            ):
                raise ValueError(f"source_zip_invalid:{period}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"source_zip_invalid:{period}") from exc


def _count_tsv_rows(archive: zipfile.ZipFile, member: str) -> int:
    with archive.open(member) as raw:
        return sum(1 for _ in io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")) - 1


def inspect_quarter_zip(path: Path, period: str) -> dict[str, Any]:
    validate_zip_file(path, period)
    with zipfile.ZipFile(path) as archive:
        with archive.open("SUBMISSION.tsv") as raw:
            submissions = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t"))
        form_accessions = {row["ACCESSION_NUMBER"] for row in submissions if row.get("DOCUMENT_TYPE") in {"4", "4/A"}}
        with archive.open("REPORTINGOWNER.tsv") as raw:
            owner_rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t")
            owners = {
                row["RPTOWNERCIK"]
                for row in owner_rows
                if row.get("ACCESSION_NUMBER") in form_accessions and row.get("RPTOWNERCIK")
            }
        return {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "zip_test": "PASS",
            "sec_schema": "PASS",
            "submission_rows": len(submissions),
            "form_4_count": sum(row.get("DOCUMENT_TYPE") == "4" for row in submissions),
            "form_4_a_count": sum(row.get("DOCUMENT_TYPE") == "4/A" for row in submissions),
            "unique_issuer_ciks": len(
                {
                    row["ISSUERCIK"]
                    for row in submissions
                    if row.get("ACCESSION_NUMBER") in form_accessions and row.get("ISSUERCIK")
                }
            ),
            "unique_reporting_owner_ciks": len(owners),
            "transaction_rows": _count_tsv_rows(archive, "NONDERIV_TRANS.tsv")
            + _count_tsv_rows(archive, "DERIV_TRANS.tsv"),
        }


def apply_local_acquisitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        path = REPO / str(row["local_path"])
        pin = PINNED_ACQUIRED.get(str(row["period"]))
        if not path.is_file():
            continue
        metadata = inspect_quarter_zip(path, str(row["period"]))
        if pin and (metadata["bytes"] != pin["bytes"] or metadata["sha256"] != pin["sha256"]):
            raise ValueError(f"pinned_source_drift:{row['period']}")
        row.update(metadata)
        row["retrieved_at"] = (
            pin["retrieved_at"]
            if pin
            else datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")
        )
        row["retrieval_status"] = "REUSED_EXISTING_VERIFIED"
    return rows


def reconcile_accessions(index_accessions: Iterable[str], archive_accessions: Iterable[str]) -> dict[str, Any]:
    index = list(index_accessions)
    archive = list(archive_accessions)
    if len(index) != len(set(index)) or len(archive) != len(set(archive)):
        raise ValueError("duplicate_accession")
    missing = sorted(set(index) - set(archive))
    unexpected = sorted(set(archive) - set(index))
    return {
        "status": "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE" if not missing and not unexpected else "INCOMPLETE",
        "index_count": len(index),
        "archive_count": len(archive),
        "missing_from_archive": missing,
        "unexpected_in_archive": unexpected,
    }


def discover_july_index_urls(body: bytes) -> list[dict[str, str]]:
    payload = json.loads(body)
    items = payload.get("directory", {}).get("item", [])
    discovered: list[dict[str, str]] = []
    for item in items:
        name = str(item.get("name", ""))
        if not re.fullmatch(r"master\.202607\d{2}\.idx", name):
            continue
        day = int(name.removeprefix("master.202607").removesuffix(".idx"))
        if day > 31:
            continue
        discovered.append(
            {
                "name": name,
                "url": urllib.parse.urljoin(JULY_DIRECTORY_URL, str(item.get("href", name))),
                "displayed_size": str(item.get("size", "")),
                "last_modified": str(item.get("last-modified", "")),
            }
        )
    discovered.sort(key=lambda row: row["name"])
    if len(discovered) != 22 or len({row["name"] for row in discovered}) != 22:
        raise ValueError("JULY_2026_DIRECTORY_DISCOVERY_DISCREPANCY")
    return discovered


def parse_master_index(body: bytes, source_name: str) -> list[dict[str, str]]:
    text = body.decode("latin-1")
    header = re.search(r"^CIK\|Company Name\|Form Type\|Date Filed\|File ?Name\s*$", text, re.MULTILINE)
    if header is None:
        raise ValueError(f"master_index_header_missing:{source_name}")
    rows: list[dict[str, str]] = []
    for line in text[header.end() :].splitlines():
        if not line.strip() or line.startswith("-"):
            continue
        fields = line.split("|")
        if len(fields) != 5:
            raise ValueError(f"master_index_malformed_row:{source_name}")
        cik, company, form, filed, archive_path = fields
        if form not in {"4", "4/A"}:
            continue
        filename = Path(archive_path).name
        match = re.fullmatch(r"(\d{10}-\d{2}-\d{6})\.txt", filename)
        if not match:
            raise ValueError(f"master_index_accession_invalid:{source_name}")
        rows.append(
            {
                "source_index": source_name,
                "cik": cik,
                "company_name_audit": company,
                "form": form,
                "filing_date": filed,
                "archive_path": archive_path,
                "accession": match.group(1),
            }
        )
    return rows


def group_index_accessions(entries: Iterable[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        groups.setdefault(entry["accession"], []).append(entry)
    return groups


def audit_july_accession_groups(
    groups: dict[str, list[dict[str, str]]], *, include_groups: bool = True
) -> dict[str, Any]:
    """Audit accession identity while retaining participant-specific SEC routes."""
    form_conflicts = filing_date_conflicts = basename_conflicts = 0
    participant_cik_multiplicity = path_multiplicity = 0
    records: list[dict[str, Any]] = []
    for accession in sorted(groups):
        rows = groups[accession]
        forms = sorted({row["form"] for row in rows})
        filing_dates = sorted({row["filing_date"] for row in rows})
        ciks = sorted({row["cik"] for row in rows})
        labels = sorted({row["company_name_audit"] for row in rows})
        paths = sorted({row["archive_path"] for row in rows})
        basenames = sorted({Path(path).name for path in paths})
        if len(forms) != 1:
            form_conflicts += 1
        if len(filing_dates) != 1:
            filing_date_conflicts += 1
        if basenames != [f"{accession}.txt"]:
            basename_conflicts += 1
        if len(ciks) > 1:
            participant_cik_multiplicity += 1
        if len(paths) > 1:
            path_multiplicity += 1
        records.append(
            {
                "accession": accession,
                "forms": forms,
                "filing_dates": filing_dates,
                "participant_row_count": len(rows),
                "participant_ciks": ciks,
                "company_name_audit_labels": labels,
                "archive_paths": paths,
                "archive_basenames": basenames,
            }
        )
    result: dict[str, Any] = {
        "schema": "h06_july_accession_group_audit_v2",
        "identity_key": "ACCESSION_NUMBER",
        "participant_path_semantics": "EXPECTED_PARTICIPANT_PATH_MULTIPLICITY",
        "raw_index_entries": sum(len(rows) for rows in groups.values()),
        "unique_accession_groups": len(groups),
        "singleton_groups": sum(len(rows) == 1 for rows in groups.values()),
        "multi_row_groups": sum(len(rows) > 1 for rows in groups.values()),
        "max_group_size": max((len(rows) for rows in groups.values()), default=0),
        "form_conflicts": form_conflicts,
        "filing_date_conflicts": filing_date_conflicts,
        "basename_accession_conflicts": basename_conflicts,
        "participant_cik_multiplicity_groups": participant_cik_multiplicity,
        "path_multiplicity_groups": path_multiplicity,
        "status": "PASS"
        if not (form_conflicts or filing_date_conflicts or basename_conflicts)
        else "JULY_INDEX_ACCESSION_GROUP_CONFLICT",
    }
    if include_groups:
        result["groups"] = records
    return result


def load_july_accession_groups() -> dict[str, list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    paths = sorted((LOCAL_ROOT / "sec_daily_index").glob("master.202607*.idx"))
    if len(paths) != 22:
        raise ValueError("JULY_2026_OFFICIAL_DAILY_INDEXES_INCOMPLETE")
    for path in paths:
        entries.extend(parse_master_index(path.read_bytes(), path.name))
    groups = group_index_accessions(entries)
    audit = audit_july_accession_groups(groups)
    if audit["status"] != "PASS":
        raise ValueError("JULY_INDEX_ACCESSION_GROUP_CONFLICT")
    return groups


def deterministic_alias_canaries(groups: dict[str, list[dict[str, str]]]) -> list[str]:
    ordered = sorted(groups)
    size_two = [accession for accession in ordered if len(groups[accession]) == 2][:3]
    larger = [accession for accession in ordered if len(groups[accession]) > 2][:2]
    maximum = max((len(rows) for rows in groups.values()), default=0)
    max_group = next((accession for accession in ordered if len(groups[accession]) == maximum), None)
    selected = size_two + larger + ([max_group] if max_group else [])
    if len(size_two) != 3 or len(larger) != 2 or max_group is None:
        raise ValueError("JULY_ALIAS_CANARY_SELECTION_INCOMPLETE")
    return list(dict.fromkeys(selected))


def bounded_unresolved_accessions(
    ordered: Iterable[str], cache_root: Path, *, max_new: int | None, start_after: str | None
) -> list[str]:
    unresolved = [
        accession
        for accession in ordered
        if not (cache_root / f"{accession}.txt").is_file()
        and (start_after is None or accession > start_after)
    ]
    return unresolved if max_new is None else unresolved[:max_new]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = Path(f"{path}.part")
    with part.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def stable_acquisition_inventory(state: dict[str, Any]) -> dict[str, Any]:
    quarter_fields = (
        "period",
        "published_label",
        "url",
        "page_displayed_size",
        "local_path",
        "bytes",
        "sha256",
        "submission_rows",
        "form_4_count",
        "form_4_a_count",
        "unique_issuer_ciks",
        "unique_reporting_owner_ciks",
        "transaction_rows",
    )
    july_fields = ("name", "url", "local_path", "bytes", "sha256", "form_4_entries", "form_4_a_entries")
    return {
        "schema": "h06_stable_official_source_inventory_v1",
        "landing_page": state.get("landing_page"),
        "july_directory": state.get("july_directory"),
        "quarters": [{key: row.get(key) for key in quarter_fields} for row in state.get("quarters", [])],
        "july_indexes": [{key: row.get(key) for key in july_fields} for row in state.get("july_indexes", [])],
        "july_index_entries": state.get("july_index_entries", {}),
    }


def aggregate_bulk_counts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    submissions = form_4 = form_4_a = transaction_rows = 0
    issuers: set[str] = set()
    owners: set[str] = set()
    accession_quarters: dict[str, list[str]] = {}
    for row in rows:
        path = REPO / str(row["local_path"])
        with zipfile.ZipFile(path) as archive:
            with archive.open("SUBMISSION.tsv") as raw:
                submission_rows = list(
                    csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t")
                )
            submissions += len(submission_rows)
            form_accessions = {
                item["ACCESSION_NUMBER"] for item in submission_rows if item.get("DOCUMENT_TYPE") in {"4", "4/A"}
            }
            form_4 += sum(item.get("DOCUMENT_TYPE") == "4" for item in submission_rows)
            form_4_a += sum(item.get("DOCUMENT_TYPE") == "4/A" for item in submission_rows)
            issuers.update(
                item["ISSUERCIK"]
                for item in submission_rows
                if item.get("ACCESSION_NUMBER") in form_accessions and item.get("ISSUERCIK")
            )
            for accession in form_accessions:
                accession_quarters.setdefault(accession, []).append(str(row["period"]))
            with archive.open("REPORTINGOWNER.tsv") as raw:
                owner_rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t")
                owners.update(
                    item["RPTOWNERCIK"]
                    for item in owner_rows
                    if item.get("ACCESSION_NUMBER") in form_accessions and item.get("RPTOWNERCIK")
                )
            transaction_rows += int(row["transaction_rows"])
    duplicates = {accession: sorted(periods) for accession, periods in accession_quarters.items() if len(periods) > 1}
    return {
        "submissions": submissions,
        "form_4": form_4,
        "form_4_a": form_4_a,
        "unique_issuers": len(issuers),
        "unique_owners": len(owners),
        "transaction_rows": transaction_rows,
        "unique_form_4_accessions": len(accession_quarters),
        "cross_quarter_duplicate_accessions": len(duplicates),
        "cross_quarter_duplicate_accession_sha256": sha256_bytes(canonical(duplicates)),
    }


def acquire_quarters_and_july_indexes(*, offline: bool = False) -> dict[str, Any]:
    prior_state = json.loads(STATE_PATH.read_text()) if STATE_PATH.is_file() else {}
    if offline:
        if not LANDING_CACHE.is_file() or not JULY_DIRECTORY_CACHE.is_file():
            raise ValueError("offline_discovery_cache_missing")
        user_agent = ""
        user_agent_source = str(prior_state.get("sec_user_agent_source", "UNAVAILABLE_OFFLINE"))
        client: FairAccessClient | None = None
        landing_body = LANDING_CACHE.read_bytes()
        july_directory_body = JULY_DIRECTORY_CACHE.read_bytes()
    else:
        user_agent, user_agent_source = resolve_sec_user_agent()
        client = FairAccessClient(user_agent)
        landing_body, _ = client.get(LANDING_URL, LANDING_CACHE)
        july_directory_body, _ = client.get(JULY_DIRECTORY_URL, JULY_DIRECTORY_CACHE)
    discovered = discover_quarter_urls(landing_body)
    rows = quarter_inventory(discovered)
    prior_by_period = {row["period"]: row for row in prior_state.get("quarters", [])}
    for row in rows:
        target = REPO / str(row["local_path"])
        if offline:
            metadata = inspect_quarter_zip(target, str(row["period"]))
            run_status = "REUSED_EXISTING_VERIFIED"
        else:
            assert client is not None
            metadata, run_status = client.download_zip_atomic(str(row["url"]), target, str(row["period"]))
        pin = PINNED_ACQUIRED.get(str(row["period"]))
        if pin and (metadata["bytes"] != pin["bytes"] or metadata["sha256"] != pin["sha256"]):
            raise ValueError(f"pinned_source_drift:{row['period']}")
        prior = prior_by_period.get(row["period"], {})
        row.update(metadata)
        row["retrieval_status"] = run_status
        row["retrieved_at"] = (
            prior.get("retrieved_at")
            or (pin or {}).get("retrieved_at")
            or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        )
        row["initial_acquisition"] = prior.get("initial_acquisition") or run_status
        if not offline:
            _atomic_json(
                STATE_PATH,
                {
                    "schema": "h06_sec_acquisition_state_v1",
                    "sec_user_agent_source": user_agent_source,
                    "sec_user_agent_declared": True,
                    "quarters": rows[: rows.index(row) + 1],
                    "july_indexes": prior_state.get("july_indexes", []),
                },
            )
    july_sources = discover_july_index_urls(july_directory_body)
    july_rows: list[dict[str, Any]] = []
    all_index_entries: list[dict[str, str]] = []
    for source in july_sources:
        target = LOCAL_ROOT / "sec_daily_index" / source["name"]
        if offline:
            body = target.read_bytes()
            run_status = "REUSED_EXISTING_VERIFIED"
        else:
            assert client is not None
            body, cached = client.get(source["url"], target)
            run_status = "REUSED_EXISTING_VERIFIED" if cached else "DOWNLOADED_AND_VALIDATED"
        entries = parse_master_index(body, source["name"])
        all_index_entries.extend(entries)
        july_rows.append(
            {
                **source,
                "local_path": str(target.relative_to(REPO)),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "retrieval_status": run_status,
                "form_4_entries": sum(row["form"] == "4" for row in entries),
                "form_4_a_entries": sum(row["form"] == "4/A" for row in entries),
            }
        )
    accession_groups = group_index_accessions(all_index_entries)
    state = {
        "schema": "h06_sec_acquisition_state_v1",
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_declared": True,
        "landing_page": {
            "url": LANDING_URL,
            "local_path": str(LANDING_CACHE.relative_to(REPO)),
            "bytes": len(landing_body),
            "sha256": sha256_bytes(landing_body),
        },
        "july_directory": {
            "url": JULY_DIRECTORY_URL,
            "local_path": str(JULY_DIRECTORY_CACHE.relative_to(REPO)),
            "bytes": len(july_directory_body),
            "sha256": sha256_bytes(july_directory_body),
        },
        "quarters": rows,
        "july_indexes": july_rows,
        "july_index_entries": {
            "form_4": sum(row["form"] == "4" for row in all_index_entries),
            "form_4_a": sum(row["form"] == "4/A" for row in all_index_entries),
            "unique_accessions": len(accession_groups),
            "duplicate_entry_count": len(all_index_entries) - len(accession_groups),
        },
    }
    if not offline:
        _atomic_json(STATE_PATH, state)
    return state


def acceptance_datetime(body: bytes) -> str:
    match = re.search(rb"<ACCEPTANCE-DATETIME>\s*(\d{14})", body, re.IGNORECASE)
    if not match:
        raise ValueError("acceptance_datetime_required")
    value = datetime.strptime(match.group(1).decode(), "%Y%m%d%H%M%S")
    return value.isoformat(timespec="seconds")


def normalize_sec_acceptance(value: str, *, eastern_naive: bool = False) -> str:
    if eastern_naive:
        parsed = datetime.fromisoformat(value).replace(tzinfo=EASTERN)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def submission_metadata(body: bytes, accession: str, expected_forms: Iterable[str] | None = None) -> dict[str, str]:
    prefix = body[:256].lstrip().lower()
    if prefix.startswith((b"<html", b"<!doctype", b"<?xml", b"access denied")):
        raise ValueError(f"submission_error_response:{accession}")
    accession_bytes = accession.encode()
    header_match = re.search(rb"(?:<ACCESSION-NUMBER>|ACCESSION NUMBER:\s*)\s*([^\r\n<]+)", body, re.IGNORECASE)
    if not header_match or header_match.group(1).strip() != accession_bytes:
        raise ValueError(f"submission_accession_mismatch:{accession}")
    form_match = re.search(
        rb"(?:<CONFORMED-SUBMISSION-TYPE>|CONFORMED SUBMISSION TYPE:\s*)\s*([^\r\n<]+)", body, re.IGNORECASE
    )
    if not form_match:
        raise ValueError(f"submission_form_required:{accession}")
    form = form_match.group(1).decode("latin-1").strip()
    allowed = set(expected_forms or {"4", "4/A"})
    if form not in allowed or form not in {"4", "4/A"}:
        raise ValueError(f"submission_form_mismatch:{accession}")
    filing_match = re.search(rb"(?:<FILED-AS-OF-DATE>|FILED AS OF DATE:\s*)\s*(\d{8})", body, re.IGNORECASE)
    filing_date = ""
    if filing_match:
        filing_date = datetime.strptime(filing_match.group(1).decode(), "%Y%m%d").date().isoformat()
    accepted = acceptance_datetime(body)
    return {
        "accession": accession,
        "form": form,
        "filing_date": filing_date,
        "accepted_at_raw_eastern": accepted,
        "accepted_at_utc": normalize_sec_acceptance(accepted, eastern_naive=True),
    }


def validate_submission_bytes(body: bytes, accession: str, expected_forms: Iterable[str] | None = None) -> str:
    return submission_metadata(body, accession, expected_forms)["accepted_at_raw_eastern"]


class FairAccessClient:
    """Sequential cached SEC client with bounded retry and injectable transport."""

    def __init__(
        self,
        user_agent: str,
        *,
        requests_per_second: int = DEFAULT_REQUESTS_PER_SECOND,
        transport: Callable[[urllib.request.Request], bytes] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip() or not EMAIL_RE.search(user_agent):
            raise ValueError("descriptive_sec_user_agent_with_contact_required")
        if requests_per_second < 1 or requests_per_second > MAX_REQUESTS_PER_SECOND:
            raise ValueError("sec_rate_limit_must_be_between_1_and_5")
        self.user_agent = user_agent
        self.configured_request_starts_per_second = requests_per_second
        self.delay = 1.0 / requests_per_second
        self._thread_local = threading.local()
        self.transport = transport or self._session_get
        self.sleeper = sleeper
        self._last_request: float | None = None
        self._rate_lock = threading.Lock()

    @staticmethod
    def _urlopen(request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - pinned HTTPS SEC URLs only
            return decode_http_body(bytes(response.read()), response.headers.get("Content-Encoding"))

    def _session_get(self, request: urllib.request.Request) -> bytes:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
        response = session.get(
            request.full_url,
            headers=dict(request.header_items()),
            timeout=(5, 5),
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status_code,
                response.reason,
                response.headers,
                None,
            )
        return bytes(response.content)

    def get(self, url: str, cache_path: Path, *, retries: int = 3) -> tuple[bytes, bool]:
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname not in {"www.sec.gov", "data.sec.gov"}:
            raise ValueError("official_sec_https_url_required")
        if cache_path.is_file():
            return cache_path.read_bytes(), True
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(retries):
            with self._rate_lock:
                if self._last_request is not None:
                    remaining = self.delay - (time.monotonic() - self._last_request)
                    if remaining > 0:
                        self.sleeper(remaining)
                self._last_request = time.monotonic()
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
            )
            try:
                body = self.transport(request)
                cache_path.write_bytes(body)
                return body, False
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_HTTP or attempt + 1 == retries:
                    raise
                self.sleeper(float(2**attempt))
            except (OSError, urllib.error.URLError):
                if attempt + 1 == retries:
                    raise
                self.sleeper(float(2**attempt))
        raise AssertionError("unreachable")

    def download_zip_atomic(
        self, url: str, target: Path, period: str, *, retries: int = 4
    ) -> tuple[dict[str, Any], str]:
        if target.is_file():
            try:
                return inspect_quarter_zip(target, period), "REUSED_EXISTING_VERIFIED"
            except ValueError:
                target.unlink()
        part = Path(f"{target}.part")
        if part.exists():
            part.unlink()
        try:
            body, _ = self.get(url, part, retries=retries)
            prefix = body[:256].lstrip().lower()
            if not body.startswith(b"PK\x03\x04") or prefix.startswith(
                (b"<html", b"<!doctype", b"<?xml", b"access denied")
            ):
                raise ValueError(f"non_zip_response_rejected:{period}")
            with part.open("rb+") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            metadata = inspect_quarter_zip(part, period)
            os.replace(part, target)
            return metadata, "DOWNLOADED_AND_VALIDATED"
        except BaseException:
            if part.exists():
                part.unlink()
            raise

    def download_submission_atomic(
        self,
        urls: Iterable[str],
        target: Path,
        accession: str,
        *,
        expected_forms: Iterable[str] | None = None,
        retries: int = DEFAULT_MAX_RETRIES,
    ) -> tuple[dict[str, Any], str]:
        if target.is_file():
            body = target.read_bytes()
            parsed = submission_metadata(body, accession, expected_forms)
            return {
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                **parsed,
            }, "REUSED_EXISTING_VERIFIED"
        errors: list[str] = []
        for url in sorted(set(urls)):
            part = Path(f"{target}.part")
            if part.exists():
                part.unlink()
            try:
                body, _ = self.get(url, part, retries=retries)
                parsed = submission_metadata(body, accession, expected_forms)
                with part.open("rb+") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(part, target)
                return {
                    "bytes": len(body),
                    "sha256": sha256_bytes(body),
                    **parsed,
                    "source_url": url,
                }, "DOWNLOADED_AND_VALIDATED"
            except (OSError, ValueError, urllib.error.URLError) as exc:
                if part.exists():
                    part.unlink()
                errors.append(type(exc).__name__)
        raise ValueError(f"july_submission_unavailable:{accession}:{','.join(errors)}")


def _july_inventory_row(
    accession: str,
    participant_rows: list[dict[str, str]],
    metadata: dict[str, Any],
    run_status: str,
    prior_row: dict[str, Any],
) -> dict[str, Any]:
    paths = sorted({row["archive_path"] for row in participant_rows})
    official_urls = [f"https://www.sec.gov/Archives/{path}" for path in paths]
    direct_source = metadata.pop("source_url", None)
    prior_source = prior_row.get("successful_source_url") or prior_row.get("source_url")
    return {
        "accession": accession,
        "forms": sorted({row["form"] for row in participant_rows}),
        "filing_dates": sorted({row["filing_date"] for row in participant_rows}),
        "participant_index_rows": len(participant_rows),
        "participant_ciks": sorted({row["cik"] for row in participant_rows}),
        "company_name_audit_labels": sorted({row["company_name_audit"] for row in participant_rows}),
        "official_archive_paths": paths,
        "official_archive_urls": official_urls,
        "successful_source_url": direct_source or prior_source or official_urls[0],
        "successful_source_url_basis": (
            "CURRENT_DOWNLOAD_RESPONSE"
            if direct_source
            else "PRIOR_RECEIPT"
            if prior_source
            else "RECONSTRUCTED_CANONICAL_FIRST_ALIAS_AFTER_ALIAS_SEMANTICS_PROVEN"
        ),
        "local_path": str((LOCAL_ROOT / "sec_july_accessions" / f"{accession}.txt").relative_to(REPO)),
        **metadata,
        "retrieval_status": run_status,
        "initial_acquisition": prior_row.get("initial_acquisition") or run_status,
    }


def _july_inventory_payload(
    *,
    user_agent_source: str,
    groups: dict[str, list[dict[str, str]]],
    rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    concurrency: int = JULY_DEFAULT_WORKERS,
) -> dict[str, Any]:
    completed = {row["accession"] for row in rows}
    return {
        "schema": "h06_july_archive_inventory_v2",
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_declared": True,
        "sec_user_agent_value_persisted": False,
        "request_policy": {
            "configured_request_starts_per_second": JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND,
            "concurrency": concurrency,
            "max_retries_per_route": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
        },
        "accessions": sorted(rows, key=lambda row: row["accession"]),
        "failures": sorted(failures, key=lambda row: row["accession"]),
        "missing_accessions": sorted(set(groups) - completed),
        "processed": len(completed),
        "expected": len(groups),
        "reconciliation": reconcile_accessions(groups, completed),
    }


def acquire_july_archives(
    *, max_new: int | None = None, start_after: str | None = None, workers: int = JULY_DEFAULT_WORKERS
) -> dict[str, Any]:
    if max_new is not None and max_new < 0:
        raise ValueError("july_max_new_must_be_nonnegative")
    if start_after is not None and not ACCESSION_RE.fullmatch(start_after):
        raise ValueError("july_start_after_invalid")
    if workers < 1 or workers > 4:
        raise ValueError("july_workers_must_be_between_1_and_4")
    user_agent, user_agent_source = resolve_sec_user_agent()
    client = FairAccessClient(
        user_agent, requests_per_second=JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND
    )
    groups = load_july_accession_groups()
    audit = audit_july_accession_groups(groups)
    _atomic_json(JULY_GROUP_AUDIT_PATH, audit)
    inventory_path = LOCAL_ROOT / "july_archive_inventory.json"
    prior = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
    prior_rows = {row["accession"]: row for row in prior.get("accessions", [])}
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    target_root = LOCAL_ROOT / "sec_july_accessions"
    newly_acquired = 0
    since_checkpoint = 0

    def acquire_one(accession: str) -> tuple[str, dict[str, Any], str]:
        participant_rows = groups[accession]
        paths = sorted({row["archive_path"] for row in participant_rows})
        urls = [f"https://www.sec.gov/Archives/{path}" for path in paths]
        target = target_root / f"{accession}.txt"
        forms = sorted({row["form"] for row in participant_rows})
        metadata, run_status = client.download_submission_atomic(
            urls, target, accession, expected_forms=forms
        )
        return accession, metadata, run_status

    ordered = sorted(groups)
    for accession in ordered:
        target = target_root / f"{accession}.txt"
        if not target.is_file():
            continue
        try:
            _, metadata, run_status = acquire_one(accession)
            rows.append(
                _july_inventory_row(
                    accession, groups[accession], metadata, run_status, prior_rows.get(accession, {})
                )
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            failures.append(
                {
                    "accession": accession,
                    "filing_dates": sorted({row["filing_date"] for row in groups[accession]}),
                    "status": "JULY_ACCESSION_ARCHIVE_FAILURE",
                    "error": type(exc).__name__,
                    "official_archive_paths": sorted({row["archive_path"] for row in groups[accession]}),
                }
            )
    unresolved = bounded_unresolved_accessions(
        ordered, target_root, max_new=max_new, start_after=start_after
    )

    for offset in range(0, len(unresolved), 100):
        batch = unresolved[offset : offset + 100]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(acquire_one, accession): accession for accession in batch}
            for future in as_completed(futures):
                accession = futures[future]
                participant_rows = groups[accession]
                paths = sorted({row["archive_path"] for row in participant_rows})
                try:
                    _, metadata, run_status = future.result()
                    rows.append(
                        _july_inventory_row(
                            accession, participant_rows, metadata, run_status, prior_rows.get(accession, {})
                        )
                    )
                    newly_acquired += 1
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    failures.append(
                        {
                            "accession": accession,
                            "filing_dates": sorted({row["filing_date"] for row in participant_rows}),
                            "status": "JULY_ACCESSION_ARCHIVE_FAILURE",
                            "error": type(exc).__name__,
                            "official_archive_paths": paths,
                        }
                    )
                since_checkpoint += 1
                if since_checkpoint >= 25:
                    _atomic_json(
                        inventory_path,
                        _july_inventory_payload(
                            user_agent_source=user_agent_source,
                            groups=groups,
                            rows=rows,
                            failures=failures,
                            concurrency=workers,
                        ),
                    )
                    since_checkpoint = 0
        _atomic_json(
            inventory_path,
            _july_inventory_payload(
                user_agent_source=user_agent_source,
                groups=groups,
                rows=rows,
                failures=failures,
                concurrency=workers,
            ),
        )
    inventory = _july_inventory_payload(
        user_agent_source=user_agent_source,
        groups=groups,
        rows=rows,
        failures=failures,
        concurrency=workers,
    )
    inventory["run_newly_acquired"] = newly_acquired
    _atomic_json(inventory_path, inventory)
    return inventory


def rebuild_july_archive_progress() -> dict[str, Any]:
    groups = load_july_accession_groups()
    _atomic_json(JULY_GROUP_AUDIT_PATH, audit_july_accession_groups(groups))
    inventory_path = LOCAL_ROOT / "july_archive_inventory.json"
    prior = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
    prior_rows = {row["accession"]: row for row in prior.get("accessions", [])}
    rows: list[dict[str, Any]] = []
    for accession in sorted(groups):
        target = LOCAL_ROOT / "sec_july_accessions" / f"{accession}.txt"
        if not target.is_file():
            continue
        body = target.read_bytes()
        participant_rows = groups[accession]
        forms = sorted({row["form"] for row in participant_rows})
        parsed = submission_metadata(body, accession, forms)
        metadata: dict[str, Any] = {"bytes": len(body), "sha256": sha256_bytes(body), **parsed}
        rows.append(
            _july_inventory_row(
                accession,
                participant_rows,
                metadata,
                "REUSED_EXISTING_VERIFIED",
                prior_rows.get(accession, {}),
            )
        )
    inventory = _july_inventory_payload(
        user_agent_source=prior.get("sec_user_agent_source", "LOCAL_GIT_IDENTITY"),
        groups=groups,
        rows=rows,
        failures=[],
    )
    _atomic_json(inventory_path, inventory)
    return inventory


def prove_july_alias_semantics() -> dict[str, Any]:
    groups = load_july_accession_groups()
    selected = deterministic_alias_canaries(groups)
    user_agent, user_agent_source = resolve_sec_user_agent()
    client = FairAccessClient(
        user_agent, requests_per_second=JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND
    )
    canaries: list[dict[str, Any]] = []
    for accession in selected:
        rows = groups[accession]
        forms = sorted({row["form"] for row in rows})
        paths = sorted({row["archive_path"] for row in rows})
        if len(paths) < 2:
            raise ValueError(f"july_alias_canary_requires_two_routes:{accession}")
        routes: list[dict[str, Any]] = []
        for route_index, path in enumerate(paths[:2], start=1):
            url = f"https://www.sec.gov/Archives/{path}"
            cache = LOCAL_ROOT / "july_alias_canaries" / accession / f"route-{route_index}.txt"
            body, reused = client.get(url, cache, retries=DEFAULT_MAX_RETRIES)
            parsed = submission_metadata(body, accession, forms)
            routes.append(
                {
                    "url": url,
                    "local_path": str(cache.relative_to(REPO)),
                    "bytes": len(body),
                    "sha256": sha256_bytes(body),
                    "retrieval_status": "REUSED_EXISTING_VERIFIED" if reused else "DOWNLOADED_AND_VALIDATED",
                    **parsed,
                }
            )
        comparable = {(row["accession"], row["accepted_at_utc"], row["form"]) for row in routes}
        if len(comparable) != 1:
            raise ValueError(f"JULY_PARTICIPANT_ALIAS_METADATA_MISMATCH:{accession}")
        if len({row["sha256"] for row in routes}) != 1:
            raise ValueError(f"JULY_PARTICIPANT_ALIAS_BYTES_DIFFER:{accession}")
        canaries.append(
            {
                "accession": accession,
                "participant_row_count": len(rows),
                "result": "PARTICIPANT_ARCHIVE_ALIASES_BYTE_IDENTICAL",
                "routes": routes,
            }
        )
    receipt = {
        "schema": "h06_july_participant_alias_canaries_v1",
        "status": "JULY_PARTICIPANT_PATH_SEMANTICS_PROVEN",
        "selection": "lexicographic_first_three_size_2_first_two_size_gt_2_first_maximum_size",
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_declared": True,
        "sec_user_agent_value_persisted": False,
        "request_policy": {
            "configured_request_starts_per_second": JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND,
            "concurrency": 1,
            "max_retries_per_route": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
        },
        "canaries": canaries,
    }
    _atomic_json(JULY_ALIAS_CANARY_PATH, receipt)
    return receipt


def inspect_submissions_zip(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic != b"PK\x03\x04":
        raise ValueError("submissions_zip_magic_invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(name for name in archive.namelist() if name.endswith(".json"))
            if not names or archive.testzip() is not None:
                raise ValueError("submissions_zip_invalid")
            sample_name = next((name for name in names if Path(name).name.startswith("CIK")), names[0])
            sample = json.loads(archive.read(sample_name))
            recent = sample.get("filings", {}).get("recent", {}) if isinstance(sample, dict) else {}
            required = {"accessionNumber", "acceptanceDateTime", "filingDate", "form"}
            if not isinstance(recent, dict) or not required.issubset(recent):
                raise ValueError("submissions_zip_schema_invalid")
            main_members = sum(bool(re.fullmatch(r"CIK\d{10}\.json", Path(name).name)) for name in names)
            historical_members = len(names) - main_members
            return {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "zip_test": "PASS",
                "schema_status": "PASS",
                "json_member_count": len(names),
                "member_naming": {
                    "main_pattern": "CIK##########.json",
                    "main_member_count": main_members,
                    "other_json_member_count": historical_members,
                },
                "recent_required_fields": sorted(required),
                "sample_member": sample_name,
            }
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("submissions_zip_invalid") from exc


def acquire_submissions_bulk() -> dict[str, Any]:
    prior = json.loads(SUBMISSIONS_BULK_RECEIPT_PATH.read_text()) if SUBMISSIONS_BULK_RECEIPT_PATH.is_file() else {}
    if SUBMISSIONS_BULK_PATH.is_file():
        metadata = inspect_submissions_zip(SUBMISSIONS_BULK_PATH)
        run_status = "REUSED_EXISTING_VERIFIED"
    else:
        user_agent, _ = resolve_sec_user_agent()
        SUBMISSIONS_BULK_PATH.parent.mkdir(parents=True, exist_ok=True)
        part = Path(f"{SUBMISSIONS_BULK_PATH}.part")
        if part.exists():
            part.unlink()
        error: Exception | None = None
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                response = requests.get(
                    SUBMISSIONS_BULK_URL,
                    headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                    timeout=(60, 600),
                    stream=True,
                )
                if response.status_code >= 400:
                    raise urllib.error.HTTPError(
                        SUBMISSIONS_BULK_URL, response.status_code, response.reason, response.headers, None
                    )
                with part.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                metadata = inspect_submissions_zip(part)
                os.replace(part, SUBMISSIONS_BULK_PATH)
                run_status = "DOWNLOADED_AND_VALIDATED"
                break
            except (OSError, ValueError, requests.RequestException, urllib.error.URLError) as exc:
                error = exc
                if part.exists():
                    part.unlink()
                retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in RETRYABLE_HTTP
                if not retryable or attempt + 1 == DEFAULT_MAX_RETRIES:
                    raise
                time.sleep(float(2**attempt))
        else:
            raise AssertionError("unreachable") from error
    _, user_agent_source = resolve_sec_user_agent()
    receipt = {
        "schema": "h06_sec_submissions_bulk_receipt_v1",
        "status": "SEC_SUBMISSIONS_BULK_ACQUIRED",
        "url": SUBMISSIONS_BULK_URL,
        "local_path": str(SUBMISSIONS_BULK_PATH.relative_to(REPO)),
        **metadata,
        "retrieved_at": prior.get("retrieved_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "retrieval_status": run_status,
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_declared": True,
        "sec_user_agent_value_persisted": False,
        "request_policy": {
            "configured_request_starts_per_second": DAILY_FEED_CONFIGURED_REQUEST_STARTS_PER_SECOND,
            "concurrency": DAILY_FEED_ACQUISITION_CONCURRENCY,
            "max_retries": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
        },
    }
    _atomic_json(SUBMISSIONS_BULK_RECEIPT_PATH, receipt)
    return receipt


def _download_large_official_file(url: str, target: Path) -> str:
    """Download one immutable SEC archive atomically with bounded retries."""
    if target.is_file():
        return "REUSED_EXISTING"
    user_agent, _ = resolve_sec_user_agent()
    target.parent.mkdir(parents=True, exist_ok=True)
    part = Path(f"{target}.part")
    if part.exists():
        part.unlink()
    for attempt in range(DEFAULT_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
                timeout=(60, 600),
                stream=True,
            )
            if response.status_code >= 400:
                raise urllib.error.HTTPError(url, response.status_code, response.reason, response.headers, None)
            with part.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            with tarfile.open(part, "r:gz") as archive:
                if not archive.getmembers():
                    raise ValueError("sec_daily_feed_archive_empty")
            os.replace(part, target)
            return "DOWNLOADED_AND_VALIDATED"
        except (OSError, ValueError, tarfile.TarError, requests.RequestException, urllib.error.URLError):
            if part.exists():
                part.unlink()
            if attempt + 1 == DEFAULT_MAX_RETRIES:
                raise
            time.sleep(float(2**attempt))
    raise AssertionError("unreachable")


def acquire_daily_feed_fallback_archives() -> dict[str, Any]:
    """Acquire only the exact official daily feeds needed for true bulk gaps."""
    prior = json.loads(DAILY_FEED_RECEIPT_PATH.read_text()) if DAILY_FEED_RECEIPT_PATH.is_file() else {}
    prior_by_date = {row["filing_date"]: row for row in prior.get("archives", [])}
    archives: list[dict[str, Any]] = []
    for filing_date, specification in DAILY_FEED_FALLBACKS.items():
        url = specification["url"]
        target = DAILY_FEED_ROOT / Path(url).name
        run_status = _download_large_official_file(url, target)
        with tarfile.open(target, "r:gz") as archive:
            members = archive.getmembers()
        archives.append(
            {
                "filing_date": filing_date,
                "url": url,
                "local_path": str(target.relative_to(REPO)),
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "tar_gzip_test": "PASS",
                "member_count": len(members),
                "target_accessions": specification["accessions"],
                "retrieved_at": prior_by_date.get(filing_date, {}).get("retrieved_at")
                or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "retrieval_status": run_status,
            }
        )
    receipt = {
        "schema": "h06_sec_daily_feed_fallback_receipt_v1",
        "status": "SEC_DAILY_FEED_FALLBACK_ARCHIVES_ACQUIRED",
        "reason": "three relevant quarterly accessions absent from submissions.zip",
        "archives": archives,
        "request_policy": {
            "configured_request_starts_per_second": METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND,
            "concurrency": 1,
            "max_retries": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
        },
        "sec_user_agent_value_persisted": False,
    }
    _atomic_json(DAILY_FEED_RECEIPT_PATH, receipt)
    return receipt


def _validate_gzip_stream(path: Path) -> int:
    uncompressed_bytes = 0
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            uncompressed_bytes += len(chunk)
    if not uncompressed_bytes:
        raise ValueError("sec_daily_oldloads_archive_empty")
    return uncompressed_bytes


def acquire_daily_oldloads_fallback_archives() -> dict[str, Any]:
    """Validate and receipt-bind exact-date Oldloads used for acceptance headers."""
    prior = (
        json.loads(DAILY_OLDLOADS_RECEIPT_PATH.read_text())
        if DAILY_OLDLOADS_RECEIPT_PATH.is_file()
        else {}
    )
    prior_by_date = {row["filing_date"]: row for row in prior.get("archives", [])}
    archives: list[dict[str, Any]] = []
    for filing_date, specification in DAILY_OLDLOADS_FALLBACKS.items():
        url = specification["url"]
        target = DAILY_OLDLOADS_ROOT / Path(url).name
        if not target.is_file():
            _download_large_official_file(url, target)
        archives.append(
            {
                "filing_date": filing_date,
                "url": url,
                "local_path": str(target.relative_to(REPO)),
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "gzip_test": "PASS",
                "uncompressed_bytes": _validate_gzip_stream(target),
                "target_accessions": specification["accessions"],
                "retrieved_at": prior_by_date.get(filing_date, {}).get("retrieved_at")
                or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "retrieval_status": "REUSED_EXISTING_VERIFIED",
            }
        )
    receipt = {
        "schema": "h06_sec_daily_oldloads_fallback_receipt_v1",
        "status": "SEC_DAILY_OLDLOADS_FALLBACK_ARCHIVES_ACQUIRED",
        "reason": "Feed NC records lack acceptance datetime; exact Oldloads headers required",
        "rejected_feed_receipt": str(DAILY_FEED_RECEIPT_PATH.relative_to(REPO)),
        "rejected_feed_reason": "NC members contain filing metadata but no ACCEPTANCE-DATETIME",
        "observed_schema": {
            "control_block_bytes": OLDLOADS_CONTROL_BLOCK_BYTES,
            "accession_slice": [0, 20],
            "form_slice": [20, 40],
            "receipt_date_slice": [160, 168],
            "acceptance_date_slice": [168, 176],
            "acceptance_time_slice": [176, 182],
            "submission_frame": "<SUBMISSION> through </SUBMISSION>",
        },
        "archives": archives,
        "request_policy": {
            "configured_request_starts_per_second": DAILY_FEED_CONFIGURED_REQUEST_STARTS_PER_SECOND,
            "concurrency": DAILY_FEED_ACQUISITION_CONCURRENCY,
            "max_retries": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
        },
        "sec_user_agent_value_persisted": False,
    }
    _atomic_json(DAILY_OLDLOADS_RECEIPT_PATH, receipt)
    return receipt


def _parse_sec_date(value: str) -> str:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"sec_filing_date_invalid:{value}")


def derive_relevant_accession_target() -> dict[str, Any]:
    metadata: dict[str, dict[str, str]] = {}
    routine: set[str] = set()
    signal: set[str] = set()
    for path in sorted((LOCAL_ROOT / "sec_bulk").glob("*_form345.zip")):
        with zipfile.ZipFile(path) as archive:
            with archive.open("SUBMISSION.tsv") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t"):
                    if row.get("DOCUMENT_TYPE") not in {"4", "4/A"}:
                        continue
                    accession = row["ACCESSION_NUMBER"]
                    metadata[accession] = {
                        "form": row["DOCUMENT_TYPE"],
                        "filing_date": _parse_sec_date(row["FILING_DATE"]),
                        "issuer_cik": row.get("ISSUERCIK", ""),
                    }
            with archive.open("NONDERIV_TRANS.tsv") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t"):
                    accession = row.get("ACCESSION_NUMBER", "")
                    if accession not in metadata:
                        continue
                    filed_year = int(metadata[accession]["filing_date"][:4])
                    code = row.get("TRANS_CODE")
                    if 2019 <= filed_year <= 2025 and code in {"P", "S"}:
                        routine.add(accession)
                    try:
                        positive = float(row.get("TRANS_SHARES") or "") > 0 and float(
                            row.get("TRANS_PRICEPERSHARE") or ""
                        ) > 0
                    except ValueError:
                        positive = False
                    if (
                        filed_year >= 2022
                        and code == "P"
                        and row.get("TRANS_ACQUIRED_DISP_CD") == "A"
                        and row.get("DIRECT_INDIRECT_OWNERSHIP") == "D"
                        and positive
                    ):
                        signal.add(accession)
    union = sorted(routine | signal)
    buffer = io.StringIO(newline="")
    fields = [
        "accession",
        "form",
        "filing_date",
        "issuer_cik",
        "routine_history_target",
        "signal_structural_target",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for accession in union:
        writer.writerow(
            {
                "accession": accession,
                **metadata[accession],
                "routine_history_target": "1" if accession in routine else "0",
                "signal_structural_target": "1" if accession in signal else "0",
            }
        )
    content = buffer.getvalue().encode()
    RELEVANT_TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    part = Path(f"{RELEVANT_TARGET_PATH}.part")
    part.write_bytes(content)
    os.replace(part, RELEVANT_TARGET_PATH)
    return {
        "schema": "h06_relevant_accession_target_v1",
        "routine_history_target_count": len(routine),
        "signal_structural_target_count": len(signal),
        "union_count": len(union),
        "path": str(RELEVANT_TARGET_PATH.relative_to(REPO)),
        "sha256": sha256_bytes(content),
        "outcome_fields_used": False,
    }


def _aligned_filing_rows(payload: dict[str, Any]) -> Iterable[dict[str, str]]:
    container: Any = payload.get("filings", {}).get("recent") if isinstance(payload.get("filings"), dict) else None
    if not isinstance(container, dict) or "accessionNumber" not in container:
        container = payload
    required = ("accessionNumber", "acceptanceDateTime", "filingDate", "form")
    if not all(isinstance(container.get(key), list) for key in required):
        return
    lengths = {len(container[key]) for key in required}
    if len(lengths) != 1:
        raise ValueError("submissions_filing_arrays_misaligned")
    for index in range(next(iter(lengths), 0)):
        yield {key: str(container[key][index]) for key in required}


def download_json_atomic(client: FairAccessClient, url: str, target: Path) -> bytes:
    if target.is_file():
        body = target.read_bytes()
        json.loads(body)
        return body
    part = Path(f"{target}.part")
    if part.exists():
        part.unlink()
    try:
        body, _ = client.get(url, part, retries=DEFAULT_MAX_RETRIES)
        json.loads(body)
        with part.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, target)
        return body
    except BaseException:
        if part.exists():
            part.unlink()
        raise


def _merge_acceptance_record(
    records: dict[str, dict[str, Any]],
    row: dict[str, str],
    *,
    cik: str,
    member: str,
    member_sha256: str,
    source_kind: str,
    parent_archive_sha256: str = "",
    conflicts: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    accession = row["accessionNumber"]
    normalized = {
        "accession": accession,
        "form": row["form"],
        "filing_date": row["filingDate"],
        "acceptance_datetime": row["acceptanceDateTime"],
        "accepted_at_utc": normalize_sec_acceptance(row["acceptanceDateTime"]),
    }
    source = {
        "cik": cik,
        "member": member,
        "member_sha256": member_sha256,
        "source_kind": source_kind,
        "parent_archive_sha256": parent_archive_sha256,
    }
    if conflicts is not None and accession in conflicts:
        candidate = {**normalized, "sources": [source]}
        if candidate not in conflicts[accession]:
            conflicts[accession].append(candidate)
        return
    prior = records.get(accession)
    if prior is None:
        records[accession] = {**normalized, "sources": [source]}
        return
    comparison = ("form", "filing_date", "accepted_at_utc")
    if any(prior[key] != normalized[key] for key in comparison):
        if prior["form"] == normalized["form"] and prior["filing_date"] == normalized["filing_date"]:
            ordered = sorted([prior, {**normalized, "sources": [source]}], key=lambda item: item["accepted_at_utc"])
            earlier, later = ordered
            earlier_wall = datetime.fromisoformat(earlier["acceptance_datetime"].replace("Z", "+00:00")).replace(
                tzinfo=None
            )
            wall_as_eastern = earlier_wall.replace(tzinfo=EASTERN).astimezone(UTC).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            if wall_as_eastern == later["accepted_at_utc"]:
                combined_sources = sorted(
                    prior["sources"] + [source], key=lambda item: (item["member"], item["cik"])
                )
                records[accession] = {
                    **later,
                    "sources": combined_sources,
                    "participant_timestamp_representation": "UTC_PLUS_MISLABELED_EASTERN_Z_ALIAS_RESOLVED",
                    "participant_timestamp_alias_values": sorted(
                        {prior["acceptance_datetime"], normalized["acceptance_datetime"]}
                    ),
                    "participant_timestamp_offset_hours": int(
                        (
                            datetime.fromisoformat(later["accepted_at_utc"].replace("Z", "+00:00"))
                            - datetime.fromisoformat(earlier["accepted_at_utc"].replace("Z", "+00:00"))
                        ).total_seconds()
                        // 3600
                    ),
                }
                return
        if conflicts is not None:
            conflicts[accession] = [prior, {**normalized, "sources": [source]}]
            records.pop(accession, None)
            return
        raise ValueError(f"duplicate_acceptance_metadata_conflict:{accession}")
    if source not in prior["sources"]:
        prior["sources"].append(source)
        prior["sources"].sort(key=lambda item: (item["member"], item["cik"]))


def extract_bulk_acceptance_records(targets: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    conflicts: dict[str, list[dict[str, Any]]] = {}
    recent_mapped: set[str] = set()
    historical_mapped: set[str] = set()
    referenced_files: set[str] = set()
    with zipfile.ZipFile(SUBMISSIONS_BULK_PATH) as archive:
        archive_names = set(archive.namelist())
        for member in sorted(name for name in archive_names if name.endswith(".json")):
            body = archive.read(member)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                continue
            cik_match = re.search(r"CIK(\d{10})", Path(member).name)
            cik = str(payload.get("cik") or (cik_match.group(1) if cik_match else ""))
            files = payload.get("filings", {}).get("files", []) if isinstance(payload.get("filings"), dict) else []
            if isinstance(files, list):
                referenced_files.update(str(item.get("name")) for item in files if isinstance(item, dict) and item.get("name"))
            kind = "bulk_recent" if isinstance(payload.get("filings"), dict) else "bulk_historical"
            member_sha = sha256_bytes(body)
            for row in _aligned_filing_rows(payload):
                accession = row["accessionNumber"]
                if accession not in targets:
                    continue
                _merge_acceptance_record(
                    records,
                    row,
                    cik=cik,
                    member=member,
                    member_sha256=member_sha,
                    source_kind=kind,
                    conflicts=conflicts,
                )
                (recent_mapped if kind == "bulk_recent" else historical_mapped).add(accession)
    return records, {
        "submissions_bulk_mapped": len(recent_mapped),
        "referenced_history_mapped": len(historical_mapped - recent_mapped),
        "referenced_file_count": len(referenced_files),
        "referenced_files_present_in_zip": sum(1 for name in referenced_files if name in archive_names),
        "duplicate_conflicting_metadata_accessions": sorted(conflicts),
        "duplicate_conflicting_metadata_count": len(conflicts),
        "participant_timestamp_alias_resolved_count": sum(
            row.get("participant_timestamp_representation")
            == "UTC_PLUS_MISLABELED_EASTERN_Z_ALIAS_RESOLVED"
            for row in records.values()
        ),
    }


def prove_bulk_participant_timestamp_representation(
    records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    resolved = [
        row
        for row in records.values()
        if row.get("participant_timestamp_representation")
        == "UTC_PLUS_MISLABELED_EASTERN_Z_ALIAS_RESOLVED"
    ]
    selected: list[dict[str, Any]] = []
    for offset, count in ((4, 3), (5, 2)):
        selected.extend(
            sorted(
                (row for row in resolved if row.get("participant_timestamp_offset_hours") == offset),
                key=lambda row: row["accession"],
            )[:count]
        )
    amendment = next(
        (row for row in sorted(resolved, key=lambda item: item["accession"]) if row["form"] == "4/A"),
        None,
    )
    if amendment is not None and amendment["accession"] not in {row["accession"] for row in selected}:
        selected.append(amendment)
    if len(selected) < 5:
        raise ValueError("bulk_participant_timestamp_canary_selection_incomplete")
    user_agent, user_agent_source = resolve_sec_user_agent()
    client = FairAccessClient(
        user_agent, requests_per_second=METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND
    )
    canaries: list[dict[str, Any]] = []
    for row in selected:
        accession = row["accession"]
        urls = [
            f"https://www.sec.gov/Archives/edgar/data/{int(source['cik'])}/{accession}.txt"
            for source in row["sources"]
            if str(source.get("cik", "")).isdigit()
        ]
        target = LOCAL_ROOT / "bulk_acceptance_representation_canaries" / f"{accession}.txt"
        metadata, _ = client.download_submission_atomic(
            urls, target, accession, expected_forms=[row["form"]]
        )
        if metadata["accepted_at_utc"] != row["accepted_at_utc"]:
            raise ValueError(f"bulk_participant_timestamp_resolution_rejected:{accession}")
        canaries.append(
            {
                "accession": accession,
                "form": row["form"],
                "bulk_alias_values": row["participant_timestamp_alias_values"],
                "resolved_accepted_at_utc": row["accepted_at_utc"],
                "complete_submission_accepted_at_raw_eastern": metadata["accepted_at_raw_eastern"],
                "complete_submission_sha256": metadata["sha256"],
                "result": "EXACT_INSTANT_MATCH",
            }
        )
    receipt = {
        "schema": "h06_bulk_participant_timestamp_representation_canaries_v1",
        "status": "SUBMISSIONS_BULK_PARTICIPANT_TIMESTAMP_REPRESENTATION_VALIDATED",
        "resolution": "choose UTC value when the alternate Z-labeled wall time converts from America/New_York to it",
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_value_persisted": False,
        "canaries": canaries,
    }
    _atomic_json(ACCEPTANCE_REPRESENTATION_CANARY_PATH, receipt)
    return receipt


def acquire_referenced_history_metadata(
    target_rows: dict[str, dict[str, str]],
    records: dict[str, dict[str, Any]],
    *,
    skip_accessions: set[str] | None = None,
) -> int:
    unresolved = set(target_rows) - set(records) - (skip_accessions or set())
    if not unresolved:
        return 0
    user_agent, _ = resolve_sec_user_agent()
    client = FairAccessClient(
        user_agent, requests_per_second=METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND
    )
    by_cik: dict[str, set[str]] = {}
    for accession in unresolved:
        by_cik.setdefault(accession[:10], set()).add(accession)
    newly_mapped: set[str] = set()
    with zipfile.ZipFile(SUBMISSIONS_BULK_PATH) as archive:
        names = set(archive.namelist())
        for cik in sorted(by_cik):
            main_name = f"CIK{cik}.json"
            candidates = [name for name in names if Path(name).name == main_name]
            if len(candidates) != 1:
                continue
            payload = json.loads(archive.read(candidates[0]))
            files = payload.get("filings", {}).get("files", []) if isinstance(payload, dict) else []
            target_dates = {target_rows[accession]["filing_date"] for accession in by_cik[cik]}
            for item in files if isinstance(files, list) else []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                filing_from = str(item.get("filingFrom") or "0000-00-00")
                filing_to = str(item.get("filingTo") or "9999-99-99")
                if not any(filing_from <= filing_date <= filing_to for filing_date in target_dates):
                    continue
                name = str(item["name"])
                member_candidates = [member for member in names if Path(member).name == name]
                if member_candidates:
                    body = archive.read(member_candidates[0])
                    member = member_candidates[0]
                    source_kind = "bulk_historical"
                else:
                    url = f"https://data.sec.gov/submissions/{name}"
                    target = LOCAL_ROOT / "sec_submissions_bulk/referenced_history" / name
                    body = download_json_atomic(client, url, target)
                    member = str(target.relative_to(REPO))
                    source_kind = "referenced_history_download"
                history = json.loads(body)
                for row in _aligned_filing_rows(history):
                    accession = row["accessionNumber"]
                    if accession not in by_cik[cik]:
                        continue
                    _merge_acceptance_record(
                        records,
                        row,
                        cik=cik,
                        member=member,
                        member_sha256=sha256_bytes(body),
                        source_kind=source_kind,
                    )
                    newly_mapped.add(accession)
    return len(newly_mapped)


def acquire_header_fallbacks(
    target_rows: dict[str, dict[str, str]], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    unresolved = sorted(set(target_rows) - set(records))
    if not unresolved:
        return {"mapped": 0, "unavailable": [], "filing_date_reconciliations": []}
    user_agent, _ = resolve_sec_user_agent()
    client = FairAccessClient(
        user_agent, requests_per_second=METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND
    )
    participant_ciks: dict[str, set[str]] = {accession: set() for accession in unresolved}
    for path in sorted((LOCAL_ROOT / "sec_bulk").glob("*_form345.zip")):
        with zipfile.ZipFile(path) as archive:
            with archive.open("SUBMISSION.tsv") as raw:
                for row in csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t"
                ):
                    accession = row.get("ACCESSION_NUMBER", "")
                    if accession in participant_ciks and row.get("ISSUERCIK"):
                        participant_ciks[accession].add(row["ISSUERCIK"])
            with archive.open("REPORTINGOWNER.tsv") as raw:
                for row in csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), delimiter="\t"
                ):
                    accession = row.get("ACCESSION_NUMBER", "")
                    if accession in participant_ciks and row.get("RPTOWNERCIK"):
                        participant_ciks[accession].add(row["RPTOWNERCIK"])
    mapped = 0
    unavailable: list[str] = []
    filing_date_reconciliations: list[dict[str, str]] = []
    for accession in unresolved:
        ciks = sorted(participant_ciks[accession] | {accession[:10]})
        urls = [
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}.txt"
            for cik in ciks
        ]
        target = LOCAL_ROOT / "sec_acceptance_header_fallback" / f"{accession}.txt"
        try:
            metadata, _ = client.download_submission_atomic(
                urls, target, accession, expected_forms=[target_rows[accession]["form"]]
            )
        except ValueError:
            unavailable.append(accession)
            continue
        body = target.read_bytes()
        parsed = submission_metadata(body, accession, [target_rows[accession]["form"]])
        target_filing_date = target_rows[accession]["filing_date"]
        source_kind = "complete_submission_header_fallback"
        if parsed["filing_date"] and parsed["filing_date"] != target_filing_date:
            if parsed["accepted_at_utc"][:10] != target_filing_date:
                raise ValueError(f"header_fallback_filing_date_mismatch:{accession}")
            source_kind = "complete_submission_header_post_acceptance_correction"
            filing_date_reconciliations.append(
                {
                    "accession": accession,
                    "structured_quarter_filing_date": target_filing_date,
                    "complete_submission_filed_as_of_date": parsed["filing_date"],
                    "complete_submission_accepted_at_raw_eastern": parsed["accepted_at_raw_eastern"],
                    "complete_submission_accepted_at_utc": parsed["accepted_at_utc"],
                    "rule": "structured filing date must equal exact UTC acceptance calendar date",
                }
            )
        _merge_acceptance_record(
            records,
            {
                "accessionNumber": accession,
                "acceptanceDateTime": parsed["accepted_at_utc"],
                "filingDate": target_filing_date,
                "form": parsed["form"],
            },
            cik=";".join(ciks),
            member=str(target.relative_to(REPO)),
            member_sha256=sha256_bytes(body),
            source_kind=source_kind,
        )
        mapped += 1
    return {
        "mapped": mapped,
        "unavailable": unavailable,
        "filing_date_reconciliations": filing_date_reconciliations,
    }


def extract_daily_feed_fallbacks(
    target_rows: dict[str, dict[str, str]], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Extract exact missing accessions from receipt-bound official daily feeds."""
    unresolved = set(target_rows) - set(records)
    if not unresolved:
        return {"mapped": 0, "archives": []}
    receipt = json.loads(DAILY_FEED_RECEIPT_PATH.read_text())
    archive_rows = {row["filing_date"]: row for row in receipt["archives"]}
    mapped = 0
    used_archives: list[dict[str, Any]] = []
    for filing_date, specification in DAILY_FEED_FALLBACKS.items():
        wanted = sorted(unresolved & set(specification["accessions"]))
        if not wanted:
            continue
        archive_row = archive_rows[filing_date]
        archive_path = REPO / archive_row["local_path"]
        archive_sha = sha256_file(archive_path)
        if archive_sha != archive_row["sha256"]:
            raise ValueError(f"daily_feed_archive_sha_mismatch:{filing_date}")
        extracted: list[dict[str, Any]] = []
        with tarfile.open(archive_path, "r:gz") as archive:
            names = {member.name: member for member in archive.getmembers()}
            for accession in wanted:
                member_name = f"./{accession}.nc"
                member = names.get(member_name) or names.get(f"{accession}.nc")
                if member is None:
                    raise ValueError(f"daily_feed_accession_missing:{accession}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"daily_feed_member_unreadable:{accession}")
                body = handle.read()
                parsed = submission_metadata(body, accession, [target_rows[accession]["form"]])
                if parsed["filing_date"] != target_rows[accession]["filing_date"]:
                    raise ValueError(f"daily_feed_filing_date_mismatch:{accession}")
                target = DAILY_FEED_ROOT / "extracted" / f"{accession}.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                part = Path(f"{target}.part")
                part.write_bytes(body)
                os.replace(part, target)
                body_sha = sha256_bytes(body)
                _merge_acceptance_record(
                    records,
                    {
                        "accessionNumber": accession,
                        "acceptanceDateTime": parsed["accepted_at_utc"],
                        "filingDate": parsed["filing_date"],
                        "form": parsed["form"],
                    },
                    cik=target_rows[accession]["issuer_cik"],
                    member=f"{archive_row['local_path']}#{member.name}",
                    member_sha256=body_sha,
                    source_kind="official_sec_daily_feed_complete_submission",
                    parent_archive_sha256=archive_sha,
                )
                extracted.append(
                    {
                        "accession": accession,
                        "member": member.name,
                        "extracted_path": str(target.relative_to(REPO)),
                        "extracted_body_sha256": body_sha,
                        "accepted_at_raw_eastern": parsed["accepted_at_raw_eastern"],
                        "accepted_at_utc": parsed["accepted_at_utc"],
                    }
                )
                mapped += 1
        used_archives.append(
            {
                "filing_date": filing_date,
                "url": archive_row["url"],
                "archive_path": archive_row["local_path"],
                "archive_sha256": archive_sha,
                "extracted": extracted,
            }
        )
    return {"mapped": mapped, "archives": used_archives}


def oldloads_control_metadata(
    control: bytes, submission: bytes, accession: str, expected_form: str
) -> dict[str, str]:
    """Parse the current SEC PDS control block preceding an Oldloads submission."""
    if len(control) != OLDLOADS_CONTROL_BLOCK_BYTES:
        raise ValueError(f"oldloads_control_block_length:{accession}")
    fields = control.decode("latin-1")
    control_accession = fields[0:20].strip()
    form = fields[20:40].strip()
    receipt_date = fields[160:168]
    acceptance_date = fields[168:176]
    acceptance_time = fields[176:182]
    if control_accession != accession:
        raise ValueError(f"oldloads_control_accession_mismatch:{accession}")
    if form != expected_form or form not in {"4", "4/A"}:
        raise ValueError(f"oldloads_control_form_mismatch:{accession}")
    framed = submission.lstrip(b" ")
    if not re.fullmatch(rb"<SUBMISSION>[\s\S]*</SUBMISSION>\r?\n?", framed):
        raise ValueError(f"oldloads_submission_frame_invalid:{accession}")
    filing_match = re.search(rb"<FILING-DATE>\s*(\d{8})", submission, re.IGNORECASE)
    if not filing_match:
        raise ValueError(f"oldloads_filing_date_required:{accession}")
    accepted = datetime.strptime(acceptance_date + acceptance_time, "%Y%m%d%H%M%S")
    return {
        "accession": accession,
        "form": form,
        "receipt_date": datetime.strptime(receipt_date, "%Y%m%d").date().isoformat(),
        "filing_date": datetime.strptime(filing_match.group(1).decode(), "%Y%m%d").date().isoformat(),
        "accepted_at_raw_eastern": accepted.isoformat(timespec="seconds"),
        "accepted_at_utc": normalize_sec_acceptance(
            accepted.isoformat(timespec="seconds"), eastern_naive=True
        ),
    }


def _extract_oldloads_targets(path: Path, wanted: set[str]) -> dict[str, tuple[bytes, dict[str, str]]]:
    found: dict[str, tuple[bytes, dict[str, str]]] = {}
    with gzip.open(path, "rb") as handle:
        while wanted - set(found):
            control = handle.read(OLDLOADS_CONTROL_BLOCK_BYTES)
            if not control:
                break
            if len(control) != OLDLOADS_CONTROL_BLOCK_BYTES:
                raise ValueError("oldloads_truncated_control_block")
            accession = control[:20].decode("latin-1").strip()
            form = control[20:40].decode("latin-1").strip()
            parts: list[bytes] | None = [] if accession in wanted else None
            while True:
                line = handle.readline()
                if not line:
                    raise ValueError(f"oldloads_submission_truncated:{accession}")
                if parts is not None:
                    parts.append(line)
                if line.rstrip(b"\r\n") == b"</SUBMISSION>":
                    break
            if parts is not None:
                submission = b"".join(parts)
                found[accession] = (
                    control + submission,
                    oldloads_control_metadata(control, submission, accession, form),
                )
    missing = sorted(wanted - set(found))
    if missing:
        raise ValueError(f"oldloads_target_accessions_missing:{','.join(missing)}")
    return found


def extract_daily_oldloads_fallbacks(
    target_rows: dict[str, dict[str, str]], records: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    unresolved = set(target_rows) - set(records)
    if not unresolved:
        return {"mapped": 0, "archives": []}
    receipt = json.loads(DAILY_OLDLOADS_RECEIPT_PATH.read_text())
    archive_rows = {row["filing_date"]: row for row in receipt["archives"]}
    mapped = 0
    used_archives: list[dict[str, Any]] = []
    for filing_date, specification in DAILY_OLDLOADS_FALLBACKS.items():
        wanted = unresolved & set(specification["accessions"])
        if not wanted:
            continue
        archive_row = archive_rows[filing_date]
        archive_path = REPO / archive_row["local_path"]
        archive_sha = sha256_file(archive_path)
        if archive_sha != archive_row["sha256"]:
            raise ValueError(f"daily_oldloads_archive_sha_mismatch:{filing_date}")
        extracted: list[dict[str, Any]] = []
        for accession, (body, parsed) in _extract_oldloads_targets(archive_path, wanted).items():
            if parsed["form"] != target_rows[accession]["form"]:
                raise ValueError(f"daily_oldloads_form_mismatch:{accession}")
            if parsed["filing_date"] != target_rows[accession]["filing_date"]:
                raise ValueError(f"daily_oldloads_filing_date_mismatch:{accession}")
            target = DAILY_OLDLOADS_ROOT / "extracted" / f"{accession}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            part = Path(f"{target}.part")
            part.write_bytes(body)
            os.replace(part, target)
            body_sha = sha256_bytes(body)
            _merge_acceptance_record(
                records,
                {
                    "accessionNumber": accession,
                    "acceptanceDateTime": parsed["accepted_at_utc"],
                    "filingDate": parsed["filing_date"],
                    "form": parsed["form"],
                },
                cik=target_rows[accession]["issuer_cik"],
                member=archive_row["local_path"],
                member_sha256=body_sha,
                source_kind="official_sec_daily_oldloads_complete_submission",
                parent_archive_sha256=archive_sha,
            )
            extracted.append(
                {
                    "accession": accession,
                    "extracted_path": str(target.relative_to(REPO)),
                    "extracted_body_sha256": body_sha,
                    **parsed,
                }
            )
            mapped += 1
        used_archives.append(
            {
                "filing_date": filing_date,
                "url": archive_row["url"],
                "archive_path": archive_row["local_path"],
                "archive_sha256": archive_sha,
                "extracted": extracted,
            }
        )
    return {"mapped": mapped, "archives": used_archives}


def _canary_submission_paths() -> list[tuple[str, Path]]:
    closure = REPO / "data/research/external/h06_structural_blocker_closure_2026_09_14"
    july = LOCAL_ROOT / "sec_july_accessions"
    return [
        ("AAPL", closure / "aapl-form4-predecision-0000320193-24-000130.txt"),
        ("NVDA", closure / "nvda-form4-predecision-0001045810-24-000122.txt"),
        ("JULY_1", july / "0000001750-26-000074.txt"),
        ("JULY_2", july / "0000001750-26-000076.txt"),
        ("JULY_FORM_4_A", july / "0000350852-26-000058.txt"),
    ]


def validate_bulk_acceptance_canaries(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    canaries: list[dict[str, Any]] = []
    for label, path in _canary_submission_paths():
        if not path.is_file():
            raise ValueError(f"acceptance_canary_body_missing:{label}")
        body = path.read_bytes()
        match = re.search(rb"(?:<ACCESSION-NUMBER>|ACCESSION NUMBER:\s*)\s*([^\r\n<]+)", body, re.IGNORECASE)
        if not match:
            raise ValueError(f"acceptance_canary_accession_missing:{label}")
        accession = match.group(1).decode().strip()
        complete = submission_metadata(body, accession)
        bulk = records.get(accession)
        if bulk is None:
            raise ValueError(f"acceptance_canary_bulk_missing:{accession}")
        if (
            complete["form"] != bulk["form"]
            or complete["accepted_at_utc"] != bulk["accepted_at_utc"]
            or (complete["filing_date"] and complete["filing_date"] != bulk["filing_date"])
        ):
            raise ValueError(f"SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_REJECTED:{accession}")
        canaries.append(
            {
                "label": label,
                "accession": accession,
                "form": complete["form"],
                "filing_date": bulk["filing_date"],
                "complete_submission_sha256": sha256_bytes(body),
                "complete_accepted_at_raw_eastern": complete["accepted_at_raw_eastern"],
                "normalized_accepted_at_utc": complete["accepted_at_utc"],
                "bulk_sources": bulk["sources"],
                "result": "EXACT_INSTANT_MATCH",
            }
        )
    return {
        "status": "SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_VALIDATED",
        "timezone_normalization": "America/New_York_to_UTC",
        "canaries": canaries,
    }


def build_relevant_acceptance_map() -> dict[str, Any]:
    target_receipt = derive_relevant_accession_target()
    with RELEVANT_TARGET_PATH.open(newline="", encoding="utf-8") as handle:
        target_rows = list(csv.DictReader(handle, delimiter="\t"))
    target_by_accession = {row["accession"]: row for row in target_rows}
    quarterly_targets = set(target_by_accession)
    july_inventory = json.loads((LOCAL_ROOT / "july_archive_inventory.json").read_text())
    if july_inventory.get("reconciliation", {}).get("status") != "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE":
        raise ValueError("JULY_2026_ACCESSION_RECONCILIATION_INCOMPLETE")
    july_rows = {row["accession"]: row for row in july_inventory["accessions"]}
    bulk_targets = set(quarterly_targets)
    for label, path in _canary_submission_paths():
        match = re.search(
            rb"(?:<ACCESSION-NUMBER>|ACCESSION NUMBER:\s*)\s*([^\r\n<]+)",
            path.read_bytes(),
            re.IGNORECASE,
        )
        if match is None:
            raise ValueError(f"acceptance_canary_accession_missing:{label}")
        bulk_targets.add(match.group(1).decode().strip())
    records, coverage = extract_bulk_acceptance_records(bulk_targets)
    representation_canaries = prove_bulk_participant_timestamp_representation(records)
    bulk_conflicts = set(coverage["duplicate_conflicting_metadata_accessions"])
    referenced_history_mapped = acquire_referenced_history_metadata(
        target_by_accession, records, skip_accessions=bulk_conflicts
    )
    target_metadata_conflicts: list[str] = []
    for accession in sorted(quarterly_targets & set(records)):
        target = target_by_accession[accession]
        record = records[accession]
        if record["form"] != target["form"] or record["filing_date"] != target["filing_date"]:
            target_metadata_conflicts.append(accession)
            records.pop(accession)
    unresolved = sorted(quarterly_targets - set(records))
    header_fallback = {"mapped": 0, "unavailable": [], "filing_date_reconciliations": []}
    daily_oldloads_fallback = {"mapped": 0, "archives": []}
    if unresolved:
        header_fallback = acquire_header_fallbacks(target_by_accession, records)
        unresolved = sorted(quarterly_targets - set(records))
    if unresolved:
        daily_oldloads_fallback = extract_daily_oldloads_fallbacks(target_by_accession, records)
        unresolved = sorted(quarterly_targets - set(records))
    canaries = validate_bulk_acceptance_canaries(records)
    for accession in sorted(quarterly_targets & set(records)):
        target = target_by_accession[accession]
        record = records[accession]
        if record["form"] != target["form"] or record["filing_date"] != target["filing_date"]:
            raise ValueError(f"acceptance_map_target_conflict:{accession}")
    if unresolved:
        raise ValueError(f"H06_RELEVANT_ACCEPTANCE_MAP_UNRESOLVED:{len(unresolved)}")

    def record_authority(row: dict[str, Any]) -> str:
        source_kinds = {source["source_kind"] for source in row["sources"]}
        if "official_sec_daily_oldloads_complete_submission" in source_kinds:
            return "official_sec_daily_oldloads_complete_submission"
        if source_kinds & {
            "complete_submission_header_fallback",
            "complete_submission_header_post_acceptance_correction",
        }:
            return "complete_submission_header_fallback"
        if "referenced_history_download" in source_kinds:
            return "validated_submissions_referenced_history"
        return "validated_submissions_bulk"

    authority_counts: dict[str, int] = {}
    for accession in quarterly_targets:
        authority = record_authority(records[accession])
        authority_counts[authority] = authority_counts.get(authority, 0) + 1
    buffer = io.StringIO(newline="")
    fields = [
        "accession",
        "form",
        "filing_date",
        "accepted_at_utc",
        "authority",
        "source_ciks",
        "source_members",
        "source_member_shas",
        "parent_submissions_bulk_sha256",
        "parent_daily_archive_shas",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    bulk_sha256 = sha256_file(SUBMISSIONS_BULK_PATH)
    for accession in sorted(quarterly_targets | set(july_rows)):
        if accession in july_rows:
            row = july_rows[accession]
            writer.writerow(
                {
                    "accession": accession,
                    "form": row["form"],
                    "filing_date": row["filing_date"],
                    "accepted_at_utc": row["accepted_at_utc"],
                    "authority": "complete_submission",
                    "source_ciks": ";".join(row["participant_ciks"]),
                    "source_members": row["local_path"],
                    "source_member_shas": row["sha256"],
                    "parent_submissions_bulk_sha256": "",
                    "parent_daily_archive_shas": "",
                }
            )
        else:
            row = records[accession]
            authority = record_authority(row)
            writer.writerow(
                {
                    "accession": accession,
                    "form": row["form"],
                    "filing_date": row["filing_date"],
                    "accepted_at_utc": row["accepted_at_utc"],
                    "authority": authority,
                    "source_ciks": ";".join(sorted({source["cik"] for source in row["sources"]})),
                    "source_members": ";".join(sorted({source["member"] for source in row["sources"]})),
                    "source_member_shas": ";".join(
                        sorted({source["member_sha256"] for source in row["sources"]})
                    ),
                    "parent_submissions_bulk_sha256": bulk_sha256
                    if authority.startswith("validated_submissions_")
                    else "",
                    "parent_daily_archive_shas": ";".join(
                        sorted(
                            {
                                source["parent_archive_sha256"]
                                for source in row["sources"]
                                if source.get("parent_archive_sha256")
                            }
                        )
                    ),
                }
            )
    content = buffer.getvalue().encode()
    part = Path(f"{ACCEPTANCE_MAP_PATH}.part")
    part.write_bytes(content)
    os.replace(part, ACCEPTANCE_MAP_PATH)
    receipt = {
        "schema": "h06_relevant_accession_acceptance_map_v1",
        "status": "COMPLETE",
        "target_set": target_receipt,
        "quarterly_target_count": len(quarterly_targets),
        "july_body_count": len(july_rows),
        "total_map_rows": len(quarterly_targets | set(july_rows)),
        **coverage,
        "referenced_history_download_mapped": referenced_history_mapped,
        "header_fallback_mapped": header_fallback["mapped"],
        "header_fallback_unavailable": header_fallback["unavailable"],
        "header_fallback_filing_date_reconciliations": header_fallback[
            "filing_date_reconciliations"
        ],
        "daily_oldloads_fallback": daily_oldloads_fallback,
        "quarterly_authority_counts": dict(sorted(authority_counts.items())),
        "bulk_target_metadata_conflicts_resolved_by_header": len(target_metadata_conflicts),
        "unresolved": 0,
        "conflicts": 0,
        "path": str(ACCEPTANCE_MAP_PATH.relative_to(REPO)),
        "sha256": sha256_bytes(content),
        "submissions_bulk_sha256": bulk_sha256,
        "acceptance_semantics": canaries,
        "participant_timestamp_representation_canaries": representation_canaries,
    }
    _atomic_json(ACCEPTANCE_MAP_RECEIPT_PATH, receipt)
    return receipt


def offline_complete_rebuild() -> dict[str, Any]:
    before_state = acquire_quarters_and_july_indexes(offline=True)
    before = source_inventory_hashes(before_state)
    rebuild_july_archive_progress()
    derive_relevant_accession_target()
    inspect_submissions_zip(SUBMISSIONS_BULK_PATH)
    build_relevant_acceptance_map()
    after_state = acquire_quarters_and_july_indexes(offline=True)
    after = source_inventory_hashes(after_state)
    result = {
        "schema": "h06_official_source_offline_rebuild_v1",
        "network_enabled": False,
        "before": before,
        "after": after,
        "match": before == after,
        "status": "OFFICIAL_SOURCE_OFFLINE_REBUILD_MATCH" if before == after else "MISMATCH",
    }
    _atomic_json(OFFLINE_REBUILD_PATH, result)
    if not result["match"]:
        raise ValueError("OFFICIAL_SOURCE_OFFLINE_REBUILD_MISMATCH")
    return result


def _optional_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.is_file() else {}


def _stable_july_body_inventory(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "accession",
        "form",
        "filing_date",
        "participant_index_rows",
        "participant_ciks",
        "official_archive_paths",
        "successful_source_url",
        "bytes",
        "sha256",
        "accepted_at_raw_eastern",
        "accepted_at_utc",
        "local_path",
    )
    return {
        "schema": "h06_stable_july_body_inventory_v1",
        "accessions": [
            {key: row.get(key) for key in fields}
            for row in sorted(value.get("accessions", []), key=lambda item: item["accession"])
        ],
        "failures": value.get("failures", []),
        "reconciliation": value.get("reconciliation", {}),
    }


def source_inventory_hashes(state: dict[str, Any]) -> dict[str, Any]:
    july_archive = _optional_json(LOCAL_ROOT / "july_archive_inventory.json")
    group_audit = _optional_json(JULY_GROUP_AUDIT_PATH)
    bulk = _optional_json(SUBMISSIONS_BULK_RECEIPT_PATH)
    daily_feed = _optional_json(DAILY_FEED_RECEIPT_PATH)
    daily_oldloads = _optional_json(DAILY_OLDLOADS_RECEIPT_PATH)
    target = _optional_json(ACCEPTANCE_MAP_RECEIPT_PATH).get("target_set", {})
    acceptance = _optional_json(ACCEPTANCE_MAP_RECEIPT_PATH)
    return {
        "quarter_inventory_sha256": sha256_bytes(canonical(stable_acquisition_inventory(state))),
        "july_index_group_sha256": sha256_bytes(canonical(group_audit)) if group_audit else None,
        "july_body_inventory_sha256": (
            sha256_bytes(canonical(_stable_july_body_inventory(july_archive))) if july_archive else None
        ),
        "submissions_bulk_sha256": bulk.get("sha256"),
        "daily_feed_fallback_sha256": sha256_bytes(canonical(daily_feed)) if daily_feed else None,
        "daily_oldloads_fallback_sha256": (
            sha256_bytes(canonical(daily_oldloads)) if daily_oldloads else None
        ),
        "relevant_target_sha256": target.get("sha256"),
        "acceptance_map_sha256": acceptance.get("sha256"),
    }


def local_data_inventory(state: dict[str, Any]) -> dict[str, Any]:
    july_archive = _optional_json(LOCAL_ROOT / "july_archive_inventory.json")
    bulk = _optional_json(SUBMISSIONS_BULK_RECEIPT_PATH)
    acceptance = _optional_json(ACCEPTANCE_MAP_RECEIPT_PATH)
    daily_feed = _optional_json(DAILY_FEED_RECEIPT_PATH)
    daily_oldloads = _optional_json(DAILY_OLDLOADS_RECEIPT_PATH)
    history_paths = sorted((LOCAL_ROOT / "sec_submissions_bulk/referenced_history").glob("*.json"))
    fallback_paths = sorted((LOCAL_ROOT / "sec_acceptance_header_fallback").glob("*.txt"))
    daily_feed_paths = sorted(DAILY_FEED_ROOT.glob("*.nc.tar.gz"))
    daily_feed_extracted_paths = sorted((DAILY_FEED_ROOT / "extracted").glob("*.txt"))
    daily_oldloads_paths = sorted(DAILY_OLDLOADS_ROOT.glob("*.gz"))
    daily_oldloads_extracted_paths = sorted((DAILY_OLDLOADS_ROOT / "extracted").glob("*.txt"))
    all_files = [path for path in LOCAL_ROOT.rglob("*") if path.is_file() and not path.name.endswith(".part")]
    quarter_rows = state.get("quarters", [])
    index_rows = state.get("july_indexes", [])
    return {
        "root": str(LOCAL_ROOT.relative_to(REPO)),
        "quarterly_zips": {
            "path_pattern": "sec_bulk/*_form345.zip",
            "count": len(quarter_rows),
            "total_bytes": sum(int(row.get("bytes") or 0) for row in quarter_rows),
            "combined_digest": sha256_bytes(
                canonical([(row.get("local_path"), row.get("bytes"), row.get("sha256")) for row in quarter_rows])
            ),
            "regeneration_source": LANDING_URL,
        },
        "july_indexes": {
            "path_pattern": "sec_daily_index/master.202607*.idx",
            "count": len(index_rows),
            "total_bytes": sum(int(row.get("bytes") or 0) for row in index_rows),
            "combined_digest": sha256_bytes(
                canonical([(row.get("local_path"), row.get("bytes"), row.get("sha256")) for row in index_rows])
            ),
            "regeneration_source": JULY_DIRECTORY_URL,
        },
        "july_complete_submissions": {
            "path_pattern": "sec_july_accessions/*.txt",
            "count": len(july_archive.get("accessions", [])),
            "total_bytes": sum(int(row.get("bytes") or 0) for row in july_archive.get("accessions", [])),
            "combined_digest": sha256_bytes(canonical(_stable_july_body_inventory(july_archive)))
            if july_archive
            else None,
            "regeneration_source": "official participant archive paths retained from July master indexes",
        },
        "submissions_bulk": {
            "path": str(SUBMISSIONS_BULK_PATH.relative_to(REPO)),
            "count": 1 if SUBMISSIONS_BULK_PATH.is_file() else 0,
            "bytes": bulk.get("bytes"),
            "sha256": bulk.get("sha256"),
            "regeneration_source": SUBMISSIONS_BULK_URL,
        },
        "referenced_history_cache": {
            "path_pattern": "sec_submissions_bulk/referenced_history/*.json",
            "count": len(history_paths),
            "total_bytes": sum(path.stat().st_size for path in history_paths),
            "combined_digest": sha256_bytes(canonical([(path.name, sha256_file(path)) for path in history_paths])),
            "regeneration_source": "official filenames referenced by SEC filer submissions JSON",
        },
        "header_fallback_cache": {
            "path_pattern": "sec_acceptance_header_fallback/*.txt",
            "count": len(fallback_paths),
            "total_bytes": sum(path.stat().st_size for path in fallback_paths),
            "combined_digest": sha256_bytes(canonical([(path.name, sha256_file(path)) for path in fallback_paths])),
            "regeneration_source": "exact official accession complete-submission route",
        },
        "daily_feed_fallback": {
            "path_pattern": "sec_daily_feed_fallback/*.nc.tar.gz",
            "count": len(daily_feed_paths),
            "total_bytes": sum(path.stat().st_size for path in daily_feed_paths),
            "combined_digest": sha256_bytes(
                canonical([(path.name, sha256_file(path)) for path in daily_feed_paths])
            ),
            "receipt_sha256": sha256_bytes(canonical(daily_feed)) if daily_feed else None,
            "extracted_count": len(daily_feed_extracted_paths),
            "extracted_combined_digest": sha256_bytes(
                canonical([(path.name, sha256_file(path)) for path in daily_feed_extracted_paths])
            ),
            "regeneration_source": "exact official SEC Feed daily archives listed in receipt",
        },
        "daily_oldloads_fallback": {
            "path_pattern": "sec_daily_oldloads_fallback/*.gz",
            "count": len(daily_oldloads_paths),
            "total_bytes": sum(path.stat().st_size for path in daily_oldloads_paths),
            "combined_digest": sha256_bytes(
                canonical([(path.name, sha256_file(path)) for path in daily_oldloads_paths])
            ),
            "receipt_sha256": sha256_bytes(canonical(daily_oldloads)) if daily_oldloads else None,
            "extracted_count": len(daily_oldloads_extracted_paths),
            "extracted_combined_digest": sha256_bytes(
                canonical([(path.name, sha256_file(path)) for path in daily_oldloads_extracted_paths])
            ),
            "regeneration_source": "exact official SEC Oldloads daily archives listed in receipt",
        },
        "relevant_target": acceptance.get("target_set", {}),
        "acceptance_map": {
            "path": acceptance.get("path"),
            "rows": acceptance.get("total_map_rows"),
            "sha256": acceptance.get("sha256"),
        },
        "acquisition_state": {
            "path": str(STATE_PATH.relative_to(REPO)),
            "sha256": sha256_file(STATE_PATH) if STATE_PATH.is_file() else None,
        },
        "total_local_bytes": sum(path.stat().st_size for path in all_files),
        "staged_in_git": False,
    }


def blocked_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    if not STATE_PATH.is_file():
        rows = apply_local_acquisitions(quarter_inventory())
        state: dict[str, Any] = {
            "sec_user_agent_source": "UNAVAILABLE",
            "sec_user_agent_declared": False,
            "quarters": rows,
            "july_indexes": [],
            "july_index_entries": {},
        }
    else:
        state = json.loads(STATE_PATH.read_text())
        rows = state["quarters"]
    validate_quarter_inventory(rows)
    acquired = [row for row in rows if row.get("sha256") and row.get("sec_schema") == "PASS"]
    july_indexes = state.get("july_indexes", [])
    july_archive = _optional_json(LOCAL_ROOT / "july_archive_inventory.json")
    group_audit = _optional_json(JULY_GROUP_AUDIT_PATH)
    group_audit_summary = {key: value for key, value in group_audit.items() if key != "groups"}
    alias_canaries = _optional_json(JULY_ALIAS_CANARY_PATH)
    submissions_bulk = _optional_json(SUBMISSIONS_BULK_RECEIPT_PATH)
    acceptance_map = _optional_json(ACCEPTANCE_MAP_RECEIPT_PATH)
    offline_rebuild = _optional_json(OFFLINE_REBUILD_PATH)
    july_reconciled = len(july_archive.get("accessions", []))
    july_expected = int(state.get("july_index_entries", {}).get("unique_accessions", 0))
    july_failures = len(july_archive.get("failures", []))
    blockers: list[str] = []
    if not state.get("sec_user_agent_declared"):
        blockers.append("SEC_USER_AGENT_STILL_UNAVAILABLE")
    if len(acquired) != 30:
        blockers.append("SEC_OFFICIAL_QUARTERLY_PACKAGES_INCOMPLETE")
    if len(july_indexes) != 22:
        blockers.append("JULY_2026_OFFICIAL_DAILY_INDEXES_INCOMPLETE")
    if group_audit.get("status") != "PASS":
        blockers.append("JULY_INDEX_ACCESSION_GROUP_CONFLICT")
    if alias_canaries.get("status") != "JULY_PARTICIPANT_PATH_SEMANTICS_PROVEN":
        blockers.append("JULY_PARTICIPANT_PATH_SEMANTICS_NOT_PROVEN")
    if july_reconciled != july_expected or july_failures:
        blockers.append("JULY_2026_OFFICIAL_INDEX_AND_ARCHIVE_RECONCILIATION_NOT_ACQUIRED")
    if submissions_bulk.get("status") != "SEC_SUBMISSIONS_BULK_ACQUIRED":
        blockers.append("SEC_SUBMISSIONS_BULK_NOT_ACQUIRED")
    if acceptance_map.get("acceptance_semantics", {}).get("status") != "SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_VALIDATED":
        blockers.append("SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_NOT_VALIDATED")
    if acceptance_map.get("status") != "COMPLETE" or acceptance_map.get("unresolved") != 0:
        blockers.append("H06_RELEVANT_ACCEPTANCE_MAP_INCOMPLETE")
    if offline_rebuild.get("match") is not True:
        blockers.append("OFFICIAL_SOURCE_OFFLINE_REBUILD_NOT_MATCHED")
    totals = (
        aggregate_bulk_counts(acquired)
        if acquired
        else {
            "submissions": 0,
            "form_4": 0,
            "form_4_a": 0,
            "unique_issuers": 0,
            "unique_owners": 0,
            "transaction_rows": 0,
            "unique_form_4_accessions": 0,
            "cross_quarter_duplicate_accessions": 0,
            "cross_quarter_duplicate_accession_sha256": sha256_bytes(canonical({})),
        }
    )
    hashes = source_inventory_hashes(state)
    local_inventory = local_data_inventory(state)
    acquisition_hash = sha256_bytes(canonical(hashes))
    complete = not blockers
    acquisition_status = (
        "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE"
        if complete
        else "H06_JULY_RECONCILIATION_INCOMPLETE"
        if july_reconciled != july_expected or july_failures
        else "H06_ACCEPTANCE_METADATA_BRIDGE_BLOCKED"
    )
    common = {
        "schema": SCHEMA,
        "status": acquisition_status,
        "cutoff": "2026-07-31T23:59:59Z",
        "economic_boundary": "NO_H06_SCORE_OR_OUTCOME_ACCESS",
    }
    manifest = seal(
        {
            **common,
            "official_inventory_created_before_download": True,
            "quarters_expected": 30,
            "quarters_acquired": len(acquired),
            "sec_user_agent_source": state.get("sec_user_agent_source"),
            "sec_user_agent_declared": bool(state.get("sec_user_agent_declared")),
            "quarter_sources": rows,
            "july_2026": {
                "index_days_expected": 22,
                "index_days_acquired": len(july_indexes),
                "directory_discovery": "COMPLETE" if len(july_indexes) == 22 else "INCOMPLETE",
                "form_4_entries": state.get("july_index_entries", {}).get("form_4", 0),
                "form_4_a_entries": state.get("july_index_entries", {}).get("form_4_a", 0),
                "unique_accessions": state.get("july_index_entries", {}).get("unique_accessions", 0),
                "duplicate_entry_count": state.get("july_index_entries", {}).get("duplicate_entry_count", 0),
                "group_audit": group_audit_summary,
                "participant_alias_canaries": alias_canaries,
                "archive_reconciled": july_reconciled,
                "archive_failures": july_failures,
                "archive_unresolved": max(july_expected - july_reconciled, 0) + july_failures,
                "reconciliation": (
                    "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE"
                    if july_expected and july_reconciled == july_expected and not july_failures
                    else "INCOMPLETE"
                ),
                "sources": july_indexes,
            },
            "source_coverage": totals,
            "submissions_bulk": submissions_bulk,
            "relevant_accession_acceptance_map": acceptance_map,
            "offline_rebuild": offline_rebuild,
            "source_inventory_hashes": hashes,
            "local_data_inventory": local_inventory,
            "deterministic_acquisition_inventory_sha256": acquisition_hash,
            "local_root": "data/research/local/h06_full_acquisition_2026_09_14",
            "fair_access": {
                "routes": {
                    "quarter_and_index": {
                        "configured_request_starts_per_second": QUARTER_CONFIGURED_REQUEST_STARTS_PER_SECOND,
                        "concurrency": 1,
                        "max_retries": DEFAULT_MAX_RETRIES,
                        "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                    },
                    "july_archive": {
                        "configured_request_starts_per_second": JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND,
                        "concurrency": JULY_DEFAULT_WORKERS,
                        "max_retries_per_alias": DEFAULT_MAX_RETRIES,
                        "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                    },
                    "submissions_metadata": {
                        "configured_request_starts_per_second": METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND,
                        "concurrency": 1,
                        "max_retries": DEFAULT_MAX_RETRIES,
                        "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                    },
                    "daily_feed_fallback": {
                        "configured_request_starts_per_second": DAILY_FEED_CONFIGURED_REQUEST_STARTS_PER_SECOND,
                        "concurrency": DAILY_FEED_ACQUISITION_CONCURRENCY,
                        "max_retries": DEFAULT_MAX_RETRIES,
                        "backoff_seconds": DEFAULT_BACKOFF_SECONDS,
                    },
                },
                "cache_required": True,
                "bounded_backoff": True,
                "sec_user_agent_source": state.get("sec_user_agent_source"),
                "user_agent_value_persisted": False,
            },
            "remaining_blockers": blockers,
        }
    )
    receipt = seal(
        {
            **common,
            "manifest_path": str(MANIFEST.relative_to(REPO)),
            "sec_user_agent_source": state.get("sec_user_agent_source"),
            "sec_user_agent_declared": bool(state.get("sec_user_agent_declared")),
            "sec_user_agent_value_persisted": False,
            "fair_access": manifest["fair_access"],
            "quarters": {
                "expected": 30,
                "acquired": len(acquired),
                "reused": sum(row.get("initial_acquisition") == "REUSED_EXISTING_VERIFIED" for row in acquired),
                "newly_downloaded": sum(
                    row.get("initial_acquisition") == "DOWNLOADED_AND_VALIDATED" for row in acquired
                ),
                "failures": 30 - len(acquired),
                "sources": rows,
            },
            "july": {
                "discovered_master_indexes": len(july_indexes),
                "acquired_indexes": len(july_indexes),
                **state.get("july_index_entries", {}),
                "archive_reconciled": july_reconciled,
                "archive_failures": july_failures,
                "unresolved": max(july_expected - july_reconciled, 0) + july_failures,
                "group_audit": group_audit_summary,
                "participant_alias_canaries": alias_canaries,
            },
            "submissions_bulk": submissions_bulk,
            "relevant_accession_acceptance_map": acceptance_map,
            "offline_rebuild": offline_rebuild,
            "total_bulk": totals,
            "source_inventory_hashes": hashes,
            "local_data_inventory": local_inventory,
            "deterministic_acquisition_inventory_sha256": acquisition_hash,
            "remaining_blockers": blockers,
        }
    )
    return manifest, receipt


def verify_tracked_artifacts() -> None:
    manifest = json.loads(MANIFEST.read_text())
    receipt = json.loads(RECEIPT.read_text())
    verify_seal(manifest)
    verify_seal(receipt)
    validate_quarter_inventory(manifest["quarter_sources"])
    if manifest["status"] not in {
        "H06_FULL_ACQUISITION_INCOMPLETE",
        "H06_JULY_RECONCILIATION_INCOMPLETE",
        "H06_ACCEPTANCE_METADATA_BRIDGE_BLOCKED",
        "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE",
    } or not 0 <= int(manifest["quarters_acquired"]) <= 30:
        raise ValueError("acquisition_status_drift")
    print("H06_FULL_OFFICIAL_ACQUISITION_VERIFIED")


def write_blocked_artifacts() -> None:
    manifest, receipt = blocked_artifacts()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-blocker", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--acquire", action="store_true")
    parser.add_argument("--acquire-july-archives", action="store_true")
    parser.add_argument("--july-max-new", type=int)
    parser.add_argument("--july-start-after")
    parser.add_argument("--july-workers", type=int, default=JULY_DEFAULT_WORKERS)
    parser.add_argument("--audit-july-groups", action="store_true")
    parser.add_argument("--prove-july-aliases", action="store_true")
    parser.add_argument("--acquire-submissions-bulk", action="store_true")
    parser.add_argument("--acquire-daily-feed-fallbacks", action="store_true")
    parser.add_argument("--acquire-daily-oldloads-fallbacks", action="store_true")
    parser.add_argument("--build-acceptance-map", action="store_true")
    parser.add_argument("--offline-complete-rebuild", action="store_true")
    parser.add_argument("--rebuild-july-progress", action="store_true")
    parser.add_argument("--offline-rebuild", action="store_true")
    args = parser.parse_args()
    if args.acquire:
        state = acquire_quarters_and_july_indexes()
        print(
            "H06_SEC_ACQUISITION_PROGRESS "
            f"quarters={len(state['quarters'])}/30 july_indexes={len(state['july_indexes'])}/22"
        )
        return 0
    if args.offline_rebuild:
        state = acquire_quarters_and_july_indexes(offline=True)
        print(f"H06_SEC_OFFLINE_REBUILD hash={sha256_bytes(canonical(stable_acquisition_inventory(state)))}")
        return 0
    if args.acquire_july_archives:
        inventory = acquire_july_archives(
            max_new=args.july_max_new, start_after=args.july_start_after, workers=args.july_workers
        )
        print(
            "H06_JULY_ARCHIVE_PROGRESS "
            f"reconciled={len(inventory['accessions'])}/{inventory['expected']} "
            f"failures={len(inventory['failures'])}"
        )
        return 0
    if args.audit_july_groups:
        audit = audit_july_accession_groups(load_july_accession_groups())
        _atomic_json(JULY_GROUP_AUDIT_PATH, audit)
        print(
            "H06_JULY_GROUP_AUDIT "
            f"groups={audit['unique_accession_groups']} hard_conflicts="
            f"{audit['form_conflicts'] + audit['filing_date_conflicts'] + audit['basename_accession_conflicts']}"
        )
        return 0
    if args.prove_july_aliases:
        receipt = prove_july_alias_semantics()
        print(f"H06_JULY_ALIAS_CANARIES status={receipt['status']} count={len(receipt['canaries'])}")
        return 0
    if args.acquire_submissions_bulk:
        receipt = acquire_submissions_bulk()
        print(f"H06_SUBMISSIONS_BULK status={receipt['status']} bytes={receipt['bytes']}")
        return 0
    if args.acquire_daily_feed_fallbacks:
        receipt = acquire_daily_feed_fallback_archives()
        print(f"H06_DAILY_FEED_FALLBACK status={receipt['status']} archives={len(receipt['archives'])}")
        return 0
    if args.acquire_daily_oldloads_fallbacks:
        receipt = acquire_daily_oldloads_fallback_archives()
        print(
            f"H06_DAILY_OLDLOADS_FALLBACK status={receipt['status']} "
            f"archives={len(receipt['archives'])}"
        )
        return 0
    if args.build_acceptance_map:
        receipt = build_relevant_acceptance_map()
        print(f"H06_ACCEPTANCE_MAP status={receipt['status']} rows={receipt['total_map_rows']}")
        return 0
    if args.offline_complete_rebuild:
        receipt = offline_complete_rebuild()
        print(f"H06_OFFICIAL_SOURCE_OFFLINE_REBUILD status={receipt['status']}")
        return 0
    if args.rebuild_july_progress:
        inventory = rebuild_july_archive_progress()
        print(f"H06_JULY_ARCHIVE_OFFLINE_PROGRESS reconciled={len(inventory['accessions'])}/{inventory['expected']}")
        return 0
    if args.write_blocker:
        write_blocked_artifacts()
    if args.verify:
        verify_tracked_artifacts()
    if (
        not args.write_blocker
        and not args.verify
        and not args.offline_rebuild
        and not args.acquire_july_archives
        and not args.audit_july_groups
        and not args.prove_july_aliases
        and not args.acquire_submissions_bulk
        and not args.acquire_daily_feed_fallbacks
        and not args.acquire_daily_oldloads_fallbacks
        and not args.build_acceptance_map
        and not args.offline_complete_rebuild
        and not args.rebuild_july_progress
    ):
        parser.error(
            "choose --acquire, --acquire-july-archives, --rebuild-july-progress, "
            "--offline-rebuild, --write-blocker, or --verify"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
