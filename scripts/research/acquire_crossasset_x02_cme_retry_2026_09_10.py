"""Bounded official CME settlement-route retry for X02.

This revision is intentionally isolated from the frozen X02-X06 matrix.  It
records raw response bytes and response headers for each exact request, while
keeping retrieval, publication, settlement, and decision times separate.
No DataMine credentials, subscriptions, or economic calculations are used.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_10/x02_cme_retry"
MANIFEST = OUT / "retry_manifest.json"
TARGET_CUTOFF = "2026-07-31T23:59:59Z"
ATTEMPTS = (20, 45)

# The first two are the frozen candidate routes. The remaining routes are
# official CME settlement/download variants documented by CME, including the
# public FTP settlement files and the documented DataMine file API sample.
ENDPOINTS = (
    ("cme_settlements_page", "GET", "https://www.cmegroup.com/market-data/settlements.html", "html"),
    ("cme_settlements_page", "HEAD", "https://www.cmegroup.com/market-data/settlements.html", "html"),
    ("cme_treasury_settlements_page", "GET", "https://www.cmegroup.com/markets/interest-rates/us-treasury/30-year-us-treasury-bond.settlements.html", "html"),
    ("cme_treasury_settlements_page", "HEAD", "https://www.cmegroup.com/markets/interest-rates/us-treasury/30-year-us-treasury-bond.settlements.html", "html"),
    ("cme_daily_settlements_page", "GET", "https://www.cmegroup.com/market-data/daily-settlements.html", "html"),
    ("cme_nymex_future_csv", "GET", "https://ftp.cmegroup.com/settle/nymex_future.csv", "csv"),
    ("cme_cme_future_csv", "GET", "https://ftp.cmegroup.com/settle/cme_future.csv", "csv"),
    ("cme_cbt_future_csv", "GET", "https://ftp.cmegroup.com/settle/cbt_future.csv", "csv"),
    ("cme_comex_future_csv", "GET", "https://ftp.cmegroup.com/settle/comex_future.csv", "csv"),
    ("cme_nymex_cutoff_csv", "GET", "https://ftp.cmegroup.com/settle/nymex.settle.20260730.csv", "csv"),
    ("cme_gold_settlement_api", "GET", "https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/Gold", "json"),
    ("cme_datamine_download_sample", "GET", "https://datamine.new.cmegroup.com/cme/api/v2/download?fid=20250625-EOD_XCBT_C_FUT_0_ETH_P", "csv_or_binary"),
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_payload(body: bytes, content_type: str, expected: str) -> dict[str, Any]:
    result: dict[str, Any] = {"format": expected, "row_count": None, "columns": [], "contract_rows": 0, "settlement_rows": 0, "publication_time_rows": 0, "available_at_rows": 0, "date_min": None, "date_max": None, "parse_error": None}
    if not body:
        return result
    text = body.decode("utf-8", errors="replace")
    if expected == "json" or "json" in content_type.lower():
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            result["parse_error"] = f"json:{type(exc).__name__}"
            return result
        rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            result["parse_error"] = "json_rows_not_list"
            return result
        result["row_count"] = len(rows)
        if rows and isinstance(rows[0], dict):
            keys = sorted({str(key) for row in rows if isinstance(row, dict) for key in row})
            result["columns"] = keys[:100]
            result["contract_rows"] = sum(bool(row.get("contract", row.get("contractCode", row.get("symbol")))) for row in rows if isinstance(row, dict))
            result["settlement_rows"] = sum(any("settle" in str(key).lower() for key in row) for row in rows if isinstance(row, dict))
        return result
    if expected in {"csv", "csv_or_binary"} or "csv" in content_type.lower() or body[:2] != b"PK":
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as exc:
            result["parse_error"] = f"csv:{type(exc).__name__}"
            return result
        if rows and any(str(value).strip() for value in rows[0].values() if value is not None):
            result["row_count"] = len(rows)
            result["columns"] = sorted(str(key) for key in rows[0])[:100]
            result["contract_rows"] = sum(any(token in " ".join(str(key).lower() for key in row) for token in ("contract", "symbol", "month")) for row in rows)
            result["settlement_rows"] = sum(any("settle" in str(key).lower() for key in row) for row in rows)
            date_values: list[str] = []
            for row in rows:
                for key, value in row.items():
                    if "date" in str(key).lower() and value and re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", str(value).strip()):
                        date_values.append(str(value).strip().replace("/", "-"))
            if date_values:
                result["date_min"], result["date_max"] = min(date_values), max(date_values)
        return result
    result["parse_error"] = "binary_or_unrecognized"
    return result


def probe_one(index: int, source_id: str, method: str, url: str, expected: str, timeout: int) -> dict[str, Any]:
    retrieved_at = now_utc()
    started = time.monotonic()
    slug = f"{index:02d}_{safe_slug(source_id)}_{method.lower()}_{timeout}s"
    body_path = OUT / "bodies" / f"{slug}.body"
    headers_path = OUT / "headers" / f"{slug}.headers.json"
    receipt_path = OUT / "receipts" / f"{slug}.receipt.json"
    receipt: dict[str, Any] = {
        "schema": "crossasset-x02-cme-retry-receipt-v1",
        "source_id": source_id,
        "card": "X02",
        "kind": "official_cme_http_probe",
        "method": method,
        "endpoint": url,
        "retrieved_at": retrieved_at,
        "timeout_seconds": timeout,
        "target_cutoff": TARGET_CUTOFF,
        "status": "REQUEST_ERROR",
        "http_status": None,
        "error": None,
        "body_path": None,
        "body_sha256": None,
        "bytes": 0,
        "response_headers_path": None,
        "response_headers_sha256": None,
        "publication_time_rows": 0,
        "available_at_rows": 0,
        "credential_fields_persisted": False,
        "parse": {"format": expected, "row_count": None, "columns": [], "contract_rows": 0, "settlement_rows": 0, "publication_time_rows": 0, "available_at_rows": 0, "date_min": None, "date_max": None, "parse_error": None},
    }
    try:
        response = requests.request(method, url, timeout=(10, timeout), allow_redirects=False, headers={"User-Agent": "ParayaBuck-X02-research-probe/2026-09-10", "Accept": "text/html,text/csv,application/json,*/*"})
        body = response.content if method == "GET" else b""
        response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        body_path.parent.mkdir(parents=True, exist_ok=True)
        headers_path.parent.mkdir(parents=True, exist_ok=True)
        headers_path.write_text(json.dumps(response_headers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        headers_digest = sha_bytes(headers_path.read_bytes())
        receipt.update({"status": "HTTP_SUCCESS" if 200 <= response.status_code < 300 else "HTTP_ERROR", "http_status": response.status_code, "body_path": str(body_path.relative_to(REPO)) if body else None, "body_sha256": sha_bytes(body) if body else None, "bytes": len(body), "response_headers_path": str(headers_path.relative_to(REPO)), "response_headers_sha256": headers_digest, "parse": parse_payload(body, response_headers.get("content-type", ""), expected)})
        if body:
            body_path.write_bytes(body)
    except requests.RequestException as exc:
        receipt["error"] = type(exc).__name__
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"slug": slug, "receipt_path": str(receipt_path.relative_to(REPO)), "receipt_sha256": sha_bytes(receipt_path.read_bytes()), **receipt}


def verify_record(record: dict[str, Any]) -> None:
    receipt_path = REPO / str(record["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("endpoint") != record.get("endpoint") or receipt.get("status") != record.get("status") or receipt.get("http_status") != record.get("http_status"):
        raise ValueError(f"receipt_binding_mismatch:{record['slug']}")
    if sha_bytes(receipt_path.read_bytes()) != record.get("receipt_sha256"):
        raise ValueError(f"receipt_hash_mismatch:{record['slug']}")
    if receipt.get("response_headers_path"):
        header_path = REPO / str(receipt["response_headers_path"])
        if sha_bytes(header_path.read_bytes()) != receipt.get("response_headers_sha256"):
            raise ValueError(f"header_hash_mismatch:{record['slug']}")
    if receipt.get("body_path"):
        body_path = REPO / str(receipt["body_path"])
        if body_path.stat().st_size != receipt.get("bytes") or sha_bytes(body_path.read_bytes()) != receipt.get("body_sha256"):
            raise ValueError(f"body_hash_mismatch:{record['slug']}")
    elif receipt.get("bytes") != 0 or receipt.get("body_sha256") is not None:
        raise ValueError(f"error_body_binding_mismatch:{record['slug']}")


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(index, source_id, method, url, expected, timeout) for index, (source_id, method, url, expected) in enumerate(ENDPOINTS, 1) for timeout in ATTEMPTS]
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(probe_one, *job) for job in jobs]
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda row: row["slug"])
    for record in records:
        verify_record(record)
    manifest = {"schema": "crossasset-x02-cme-retry-manifest-v1", "card": "X02", "target_cutoff": TARGET_CUTOFF, "attempt_timeouts_seconds": list(ATTEMPTS), "probe_count": len(records), "records": records, "economic_run": False, "admitted_rows": 0, "conclusion": "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS" if not any(record["parse"].get("settlement_rows", 0) and record["parse"].get("contract_rows", 0) and record["status"] == "HTTP_SUCCESS" for record in records) else "DIAGNOSTIC_ROWS_REQUIRE_REVIEW"}
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = sha_bytes(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": manifest["conclusion"], "probe_count": len(records), "successful": sum(record["status"] == "HTTP_SUCCESS" for record in records), "request_errors": sum(record["status"] == "REQUEST_ERROR" for record in records), "http_errors": sum(record["status"] == "HTTP_ERROR" for record in records), "settlement_rows": sum(record["parse"].get("settlement_rows", 0) for record in records), "manifest_sha256": manifest["manifest_sha256"]}


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claimed = manifest.get("manifest_sha256")
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    expected = sha_bytes(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if claimed != expected:
        raise ValueError("manifest_canonical_hash_mismatch")
    records = manifest.get("records", [])
    if manifest.get("probe_count") != len(records):
        raise ValueError("manifest_probe_count_mismatch")
    for record in records:
        verify_record(record)
    if manifest.get("card") != "X02" or manifest.get("admitted_rows") != 0 or manifest.get("conclusion") != "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS":
        raise ValueError("manifest_admission_claim_invalid")
    return {"status": "X02_CME_RETRY_VERIFIED", "probe_count": len(records), "manifest_sha256": claimed, "admitted_rows": manifest.get("admitted_rows")}


if __name__ == "__main__":
    print(json.dumps(verify() if "--verify" in sys.argv[1:] else run(), sort_keys=True))
