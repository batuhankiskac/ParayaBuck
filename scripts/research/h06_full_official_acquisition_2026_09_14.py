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
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
LOCAL_ROOT = REPO / "data/research/local/h06_full_acquisition_2026_09_14"
MANIFEST = REPO / "data/research_manifests/h06_full_official_acquisition_2026_09_14.json"
RECEIPT = REPO / "data/research_receipts/h06_full_official_acquisition_receipt_2026_09_14.json"
STATUS = "H06_FULL_ACQUISITION_INCOMPLETE"
SCHEMA = "h06_full_official_acquisition_v1"
MAX_REQUESTS_PER_SECOND = 5
DEFAULT_REQUESTS_PER_SECOND = 1
LANDING_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
JULY_DIRECTORY_URL = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/index.json"
LANDING_CACHE = LOCAL_ROOT / "source_discovery/insider-transactions-data-sets.html"
JULY_DIRECTORY_CACHE = LOCAL_ROOT / "source_discovery/qtr3-index.json"
STATE_PATH = LOCAL_ROOT / "acquisition_state.json"
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


def validate_submission_bytes(body: bytes, accession: str) -> str:
    prefix = body[:256].lstrip().lower()
    if prefix.startswith((b"<html", b"<!doctype", b"<?xml", b"access denied")):
        raise ValueError(f"submission_error_response:{accession}")
    accession_bytes = accession.encode()
    header_match = re.search(rb"(?:<ACCESSION-NUMBER>|ACCESSION NUMBER:\s*)\s*([^\r\n<]+)", body, re.IGNORECASE)
    if not header_match or header_match.group(1).strip() != accession_bytes:
        raise ValueError(f"submission_accession_mismatch:{accession}")
    return acceptance_datetime(body)


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
        self.delay = 1.0 / requests_per_second
        self.session = requests.Session()
        self.transport = transport or self._session_get
        self.sleeper = sleeper
        self._last_request: float | None = None

    @staticmethod
    def _urlopen(request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - pinned HTTPS SEC URLs only
            return decode_http_body(bytes(response.read()), response.headers.get("Content-Encoding"))

    def _session_get(self, request: urllib.request.Request) -> bytes:
        response = self.session.get(
            request.full_url,
            headers=dict(request.header_items()),
            timeout=60,
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
        if not url.startswith("https://www.sec.gov/"):
            raise ValueError("official_sec_https_url_required")
        if cache_path.is_file():
            return cache_path.read_bytes(), True
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(retries):
            if self._last_request is not None:
                remaining = self.delay - (time.monotonic() - self._last_request)
                if remaining > 0:
                    self.sleeper(remaining)
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
            )
            self._last_request = time.monotonic()
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
        self, urls: Iterable[str], target: Path, accession: str, *, retries: int = 4
    ) -> tuple[dict[str, Any], str]:
        if target.is_file():
            body = target.read_bytes()
            return {
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "accepted_at_raw_eastern": validate_submission_bytes(body, accession),
            }, "REUSED_EXISTING_VERIFIED"
        errors: list[str] = []
        for url in sorted(set(urls)):
            part = Path(f"{target}.part")
            if part.exists():
                part.unlink()
            try:
                body, _ = self.get(url, part, retries=retries)
                accepted = validate_submission_bytes(body, accession)
                with part.open("rb+") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(part, target)
                return {
                    "bytes": len(body),
                    "sha256": sha256_bytes(body),
                    "accepted_at_raw_eastern": accepted,
                    "source_url": url,
                }, "DOWNLOADED_AND_VALIDATED"
            except (OSError, ValueError, urllib.error.URLError) as exc:
                if part.exists():
                    part.unlink()
                errors.append(type(exc).__name__)
        raise ValueError(f"july_submission_unavailable:{accession}:{','.join(errors)}")


def acquire_july_archives() -> dict[str, Any]:
    user_agent, user_agent_source = resolve_sec_user_agent()
    client = FairAccessClient(user_agent, requests_per_second=5)
    entries: list[dict[str, str]] = []
    for path in sorted((LOCAL_ROOT / "sec_daily_index").glob("master.202607*.idx")):
        entries.extend(parse_master_index(path.read_bytes(), path.name))
    groups = group_index_accessions(entries)
    inventory_path = LOCAL_ROOT / "july_archive_inventory.json"
    prior = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
    prior_rows = {row["accession"]: row for row in prior.get("accessions", [])}
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    target_root = LOCAL_ROOT / "sec_july_accessions"
    for index, accession in enumerate(sorted(groups), start=1):
        participant_rows = groups[accession]
        paths = sorted({row["archive_path"] for row in participant_rows})
        urls = [f"https://www.sec.gov/Archives/{path}" for path in paths]
        target = target_root / f"{accession}.txt"
        try:
            metadata, run_status = client.download_submission_atomic(urls, target, accession)
            prior_row = prior_rows.get(accession, {})
            rows.append(
                {
                    "accession": accession,
                    "forms": sorted({row["form"] for row in participant_rows}),
                    "participant_index_rows": len(participant_rows),
                    "official_archive_paths": paths,
                    "local_path": str(target.relative_to(REPO)),
                    **metadata,
                    "retrieval_status": run_status,
                    "initial_acquisition": prior_row.get("initial_acquisition") or run_status,
                }
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            failures.append({"accession": accession, "error": type(exc).__name__, "official_archive_paths": paths})
        if index % 100 == 0:
            _atomic_json(
                inventory_path,
                {
                    "schema": "h06_july_archive_inventory_v1",
                    "sec_user_agent_source": user_agent_source,
                    "sec_user_agent_declared": True,
                    "accessions": rows,
                    "failures": failures,
                    "processed": index,
                    "expected": len(groups),
                },
            )
    inventory = {
        "schema": "h06_july_archive_inventory_v1",
        "sec_user_agent_source": user_agent_source,
        "sec_user_agent_declared": True,
        "accessions": rows,
        "failures": failures,
        "processed": len(groups),
        "expected": len(groups),
        "reconciliation": reconcile_accessions(groups, [row["accession"] for row in rows]),
    }
    _atomic_json(inventory_path, inventory)
    return inventory


def rebuild_july_archive_progress() -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    for path in sorted((LOCAL_ROOT / "sec_daily_index").glob("master.202607*.idx")):
        entries.extend(parse_master_index(path.read_bytes(), path.name))
    groups = group_index_accessions(entries)
    inventory_path = LOCAL_ROOT / "july_archive_inventory.json"
    prior = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
    prior_rows = {row["accession"]: row for row in prior.get("accessions", [])}
    rows: list[dict[str, Any]] = []
    for accession in sorted(groups):
        target = LOCAL_ROOT / "sec_july_accessions" / f"{accession}.txt"
        if not target.is_file():
            continue
        body = target.read_bytes()
        accepted = validate_submission_bytes(body, accession)
        participant_rows = groups[accession]
        rows.append(
            {
                "accession": accession,
                "forms": sorted({row["form"] for row in participant_rows}),
                "participant_index_rows": len(participant_rows),
                "official_archive_paths": sorted({row["archive_path"] for row in participant_rows}),
                "local_path": str(target.relative_to(REPO)),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "accepted_at_raw_eastern": accepted,
                "retrieval_status": "REUSED_EXISTING_VERIFIED",
                "initial_acquisition": prior_rows.get(accession, {}).get(
                    "initial_acquisition", "DOWNLOADED_AND_VALIDATED"
                ),
            }
        )
    missing = sorted(set(groups) - {row["accession"] for row in rows})
    inventory = {
        "schema": "h06_july_archive_inventory_v1",
        "sec_user_agent_source": prior.get("sec_user_agent_source", "LOCAL_GIT_IDENTITY"),
        "sec_user_agent_declared": True,
        "accessions": rows,
        "failures": [],
        "missing_accessions": missing,
        "processed": len(rows),
        "expected": len(groups),
        "reconciliation": reconcile_accessions(groups, [row["accession"] for row in rows]),
    }
    _atomic_json(inventory_path, inventory)
    return inventory


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
    july_archive_path = LOCAL_ROOT / "july_archive_inventory.json"
    july_archive = json.loads(july_archive_path.read_text()) if july_archive_path.is_file() else {}
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
    if july_reconciled != july_expected or july_failures:
        blockers.append("JULY_2026_OFFICIAL_INDEX_AND_ARCHIVE_RECONCILIATION_NOT_ACQUIRED")
    else:
        blockers.append("REQUIRED_QUARTERLY_ACCESSION_ACCEPTANCE_AND_SIGNAL_BODY_ACQUISITION_NOT_COMPLETE")
        blockers.append("FULL_POPULATION_STRUCTURAL_PIPELINE_NOT_IMPLEMENTED")
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
    acquisition_hash = sha256_bytes(canonical(stable_acquisition_inventory(state)))
    common = {
        "schema": SCHEMA,
        "status": STATUS,
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
            "deterministic_acquisition_inventory_sha256": acquisition_hash,
            "local_root": "data/research/local/h06_full_acquisition_2026_09_14",
            "fair_access": {
                "actual_requests_per_second": DEFAULT_REQUESTS_PER_SECOND,
                "maximum_requests_per_second": 5,
                "cache_required": True,
                "bounded_backoff": True,
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
            },
            "total_bulk": totals,
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
    if manifest["status"] != STATUS or not 0 <= int(manifest["quarters_acquired"]) <= 30:
        raise ValueError("acquisition_status_drift")
    print("H06_FULL_OFFICIAL_ACQUISITION_BLOCKER_VERIFIED")


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
        inventory = acquire_july_archives()
        print(
            "H06_JULY_ARCHIVE_PROGRESS "
            f"reconciled={len(inventory['accessions'])}/{inventory['expected']} "
            f"failures={len(inventory['failures'])}"
        )
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
        and not args.rebuild_july_progress
    ):
        parser.error(
            "choose --acquire, --acquire-july-archives, --rebuild-july-progress, "
            "--offline-rebuild, --write-blocker, or --verify"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
