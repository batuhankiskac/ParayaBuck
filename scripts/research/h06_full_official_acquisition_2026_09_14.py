#!/usr/bin/env python3
"""Deterministic, fair-access H06 SEC inventory and acquisition gate.

This module deliberately stops before normalization when any official source is
missing.  It contains no economic fields or strategy calculations.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
LOCAL_ROOT = REPO / "data/research/local/h06_full_acquisition_2026_09_14"
MANIFEST = REPO / "data/research_manifests/h06_full_official_acquisition_2026_09_14.json"
RECEIPT = REPO / "data/research_receipts/h06_full_official_acquisition_receipt_2026_09_14.json"
STATUS = "H06_FULL_ACQUISITION_INCOMPLETE"
SCHEMA = "h06_full_official_acquisition_v1"
MAX_REQUESTS_PER_SECOND = 5
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


def quarter_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in range(2019, 2027):
        last_quarter = 2 if year == 2026 else 4
        for quarter in range(1, last_quarter + 1):
            prefix = "datastandardsinnovation/data" if (year, quarter) == (2026, 2) else "structureddata/data"
            name = f"{year}q{quarter}_form345.zip"
            rows.append(
                {
                    "period": f"{year}Q{quarter}",
                    "coverage": f"{year} Q{quarter}",
                    "url": f"https://www.sec.gov/files/{prefix}/insider-transactions-data-sets/{name}",
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
    expected = [f"{year}Q{quarter}" for year in range(2019, 2027) for quarter in range(1, (2 if year == 2026 else 4) + 1)]
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
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != EXPECTED_ZIP_MEMBERS or archive.testzip() is not None:
            raise ValueError(f"source_zip_invalid:{row['period']}")


def apply_pinned_local_acquisitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        pin = PINNED_ACQUIRED.get(str(row["period"]))
        if pin is None:
            continue
        row.update(pin)
        row["retrieval_status"] = "ACQUIRED_AND_VALIDATED"
        validate_cached_source(row)
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


def acceptance_datetime(body: bytes) -> str:
    match = re.search(rb"<ACCEPTANCE-DATETIME>\s*(\d{14})", body, re.IGNORECASE)
    if not match:
        raise ValueError("acceptance_datetime_required")
    value = datetime.strptime(match.group(1).decode(), "%Y%m%d%H%M%S")
    return value.isoformat(timespec="seconds")


class FairAccessClient:
    """Sequential cached SEC client with bounded retry and injectable transport."""

    def __init__(
        self,
        user_agent: str,
        *,
        requests_per_second: int = MAX_REQUESTS_PER_SECOND,
        transport: Callable[[urllib.request.Request], bytes] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip() or not re.search(r"(?:@|https?://)", user_agent):
            raise ValueError("descriptive_sec_user_agent_with_contact_required")
        if requests_per_second < 1 or requests_per_second > MAX_REQUESTS_PER_SECOND:
            raise ValueError("sec_rate_limit_must_be_between_1_and_5")
        self.user_agent = user_agent
        self.delay = 1.0 / requests_per_second
        self.transport = transport or self._urlopen
        self.sleeper = sleeper
        self._last_request: float | None = None

    @staticmethod
    def _urlopen(request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - pinned HTTPS SEC URLs only
            return bytes(response.read())

    def get(self, url: str, cache_path: Path, *, retries: int = 3) -> tuple[bytes, bool]:
        if not url.startswith("https://www.sec.gov/"):
            raise ValueError("official_sec_https_url_required")
        if cache_path.is_file():
            return cache_path.read_bytes(), True
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(retries):
            if self._last_request is not None:
                self.sleeper(self.delay)
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
            self._last_request = time.monotonic()
            try:
                body = self.transport(request)
                cache_path.write_bytes(body)
                return body, False
            except (OSError, urllib.error.URLError):
                if attempt + 1 == retries:
                    raise
                self.sleeper(float(2**attempt))
        raise AssertionError("unreachable")


def blocked_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    rows = apply_pinned_local_acquisitions(quarter_inventory())
    validate_quarter_inventory(rows)
    acquired = [row for row in rows if row["retrieval_status"] == "ACQUIRED_AND_VALIDATED"]
    blockers = [
        "SEC_USER_AGENT_WITH_OPERATOR_APPROVED_CONTACT_NOT_AVAILABLE",
        "SEC_OFFICIAL_QUARTERLY_PACKAGES_2019Q1_THROUGH_2026Q1_NOT_ACQUIRED",
        "JULY_2026_OFFICIAL_INDEX_AND_ARCHIVE_RECONCILIATION_NOT_ACQUIRED",
    ]
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
            "acquired_form_4_count": sum(int(row["form_4_count"]) for row in acquired),
            "acquired_form_4_a_count": sum(int(row["form_4_a_count"]) for row in acquired),
            "acquired_unique_issuer_ciks_nonadditive": sum(int(row["unique_issuer_ciks"]) for row in acquired),
            "acquired_unique_reporting_owner_ciks_nonadditive": sum(int(row["unique_reporting_owner_ciks"]) for row in acquired),
            "quarter_sources": rows,
            "july_2026": {
                "index_days_expected": 22,
                "index_days_acquired": 0,
                "reconciliation": "INCOMPLETE",
                "daily_index_url_template": "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.YYYYMMDD.idx",
            },
            "local_root": "data/research/local/h06_full_acquisition_2026_09_14",
            "fair_access": {"maximum_requests_per_second": 5, "cache_required": True, "bounded_backoff": True},
            "remaining_blockers": blockers,
        }
    )
    receipt = seal(
        {
            **common,
            "manifest_path": str(MANIFEST.relative_to(REPO)),
            "attempts": [
                {"attempted_at": "2026-09-14T19:33:22Z", "source": "official SEC 2026 Q2 package", "url": rows[-1]["url"], "result": "HTTP_403_AND_INITIAL_BROWSER_DOWNLOAD_FAILED", "partial_browser_bytes_admitted": False},
                {"attempted_at": "2026-09-14T19:51:44Z", "source": "official SEC 2026 Q2 package", "url": rows[-1]["url"], "result": "USER_COMPLETED_BROWSER_DOWNLOAD_VALIDATED", "bytes": rows[-1]["bytes"], "sha256": rows[-1]["sha256"]},
            ],
            "coverage": {"quarters_expected": 30, "quarters_acquired": len(acquired), "files_acquired": len(acquired)},
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
    if manifest["status"] != STATUS or manifest["quarters_acquired"] != 1:
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
    args = parser.parse_args()
    if args.acquire:
        user_agent = os.environ.get("SEC_USER_AGENT", "")
        client = FairAccessClient(user_agent)
        for row in quarter_inventory():
            client.get(str(row["url"]), REPO / str(row["local_path"]))
        return 0
    if args.write_blocker:
        write_blocked_artifacts()
    if args.verify:
        verify_tracked_artifacts()
    if not args.write_blocker and not args.verify:
        parser.error("choose --acquire, --write-blocker, or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
