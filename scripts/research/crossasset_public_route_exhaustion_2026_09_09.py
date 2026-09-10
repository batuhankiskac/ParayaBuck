"""Probe free official X01/X03 routes and freeze sanitized evidence.

This runner never admits economic rows.  It records the response bytes and
HTTP result for reproducibility, while preserving publication/report dates
only when the source itself exposes them.  SEC credentials are read from the
process environment and are never written to an artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_09/public_route_exhaustion"

ROUTES = [
    # Official iShares pages and public holdings download routes.  asOfDate
    # is intentionally probed as a route capability check; a response is not
    # treated as PIT evidence without an issuer publication timestamp.
    ("X01", "ishares_product_SHY", "https://www.ishares.com/us/products/239452/ishares-1-3-year-treasury-bond-etf"),
    ("X01", "ishares_product_IEF", "https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf"),
    ("X01", "ishares_product_TLT", "https://www.ishares.com/us/products/239454/ishares-20-plus-year-treasury-bond-etf"),
    ("X01", "ishares_holdings_SHY_20260731", "https://www.ishares.com/us/products/239452/ishares-1-3-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20260731"),
    ("X01", "ishares_holdings_IEF_20260731", "https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20260731"),
    ("X01", "ishares_holdings_TLT_20260731", "https://www.ishares.com/us/products/239454/ishares-20-plus-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20260731"),
    ("X01", "ishares_holdings_SHY_20250131", "https://www.ishares.com/us/products/239452/ishares-1-3-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20250131"),
    ("X01", "ishares_holdings_IEF_20250131", "https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20250131"),
    ("X01", "ishares_holdings_TLT_20250131", "https://www.ishares.com/us/products/239454/ishares-20-plus-year-treasury-bond-etf/1467271812596.ajax?tab=all&fileType=json&asOfDate=20250131"),
    # SEC issuer identity and document routes for the two unresolved funds.
    ("X03", "sec_submissions_CIK0000930667", "https://data.sec.gov/submissions/CIK0000930667.json"),
    ("X03", "sec_tickers_exchange", "https://www.sec.gov/files/company_tickers_exchange.json"),
    ("X03", "sec_EWJ_index_20260228", "https://www.sec.gov/Archives/edgar/data/930667/000100472626002861/0001004726-26-002861-index.htm"),
    ("X03", "sec_EWJ_headers_20260228", "https://www.sec.gov/Archives/edgar/data/930667/0001004726-26-002861/0001004726-26-002861-index-headers.html"),
    ("X03", "sec_EWJ_primary_xml_20260228", "https://www.sec.gov/Archives/edgar/data/930667/000100472626002861/primary_doc.xml"),
    ("X03", "sec_EWZ_index_20250228", "https://www.sec.gov/Archives/edgar/data/930667/000175272425090529/0001752724-25-090529-index.htm"),
    ("X03", "sec_EWZ_headers_20250228", "https://www.sec.gov/Archives/edgar/data/930667/000175272425090529/0001752724-25-090529-index-headers.html"),
    ("X03", "sec_EWZ_primary_xml_20250228", "https://www.sec.gov/Archives/edgar/data/930667/000175272425090529/primary_doc.xml"),
    ("X03", "sec_series_identity_202412", "https://www.sec.gov/Archives/edgar/data/930667/000119312524286411/0001193125-24-286411-index-headers.html"),
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fetch(card: str, slug: str, url: str) -> dict[str, object]:
    # SEC_USER_AGENT is process-local only.  It is deliberately not copied
    # into headers/receipts or included in any hash/report.
    user_agent = os.environ.get("SEC_USER_AGENT", "ParayaBuck-public-route-probe")
    headers = {"User-Agent": user_agent} if "sec.gov" in url else {"User-Agent": "ParayaBuck-public-route-probe"}
    retrieved = _utc_now()
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=30) as response:
            body = response.read()
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
        suffix = ".csv" if "text/csv" in content_type.lower() or "filetype=csv" in url.lower() else (".xml" if "xml" in content_type.lower() or url.endswith(".xml") else ".html")
        body_path = OUT / "bodies" / f"{slug}{suffix}"
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        return {
            "schema": "crossasset-public-route-probe-v1",
            "source_id": slug,
            "card": card,
            "kind": "official_public_route",
            "endpoint": url,
            "final_url": final_url,
            "retrieved_at": retrieved,
            "status": "HTTP_SUCCESS" if 200 <= status < 300 else "HTTP_ERROR",
            "http_status": status,
            "content_type": content_type,
            "body_path": str(body_path.relative_to(REPO)),
            "bytes": len(body),
            "body_sha256": _sha(body),
            "row_count": None,
            "count_semantics": "raw response bytes; no rows admitted",
            "credential_fields_persisted": False,
            "admissible": False,
            "publication_or_pit": None,
            "blocker": "route response requires source-native publication/available_at and complete historical identity before admission",
        }
    except HTTPError as exc:
        # Preserve an HTTP terminal response (including 404/403) separately
        # from transport failure.  The response body is still evidence of the
        # route result and is saved when the server supplies one.
        body = exc.read()
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        body_path = None
        body_hash = None
        if body:
            suffix = ".csv" if "text/csv" in content_type.lower() else (".xml" if "xml" in content_type.lower() else ".html")
            body_file = OUT / "bodies" / f"{slug}{suffix}"
            body_file.parent.mkdir(parents=True, exist_ok=True)
            body_file.write_bytes(body)
            body_path = str(body_file.relative_to(REPO))
            body_hash = _sha(body)
        return {
            "schema": "crossasset-public-route-probe-v1",
            "source_id": slug,
            "card": card,
            "kind": "official_public_route",
            "endpoint": url,
            "retrieved_at": retrieved,
            "status": "HTTP_ERROR",
            "http_status": int(exc.code),
            "content_type": content_type,
            "body_path": body_path,
            "bytes": len(body),
            "body_sha256": body_hash,
            "row_count": None,
            "count_semantics": "raw error response bytes; no rows admitted",
            "credential_fields_persisted": False,
            "admissible": False,
            "publication_or_pit": None,
            "blocker": "HTTP route result is recorded; exact historical PIT source remains unavailable from this endpoint",
        }
    except Exception as exc:  # noqa: BLE001 - sanitized terminal receipt
        return {
            "schema": "crossasset-public-route-probe-v1",
            "source_id": slug,
            "card": card,
            "kind": "official_public_route",
            "endpoint": url,
            "retrieved_at": retrieved,
            "status": "REQUEST_ERROR",
            "http_status": None,
            "bytes": 0,
            "body_sha256": None,
            "row_count": None,
            "count_semantics": "no response body",
            "credential_fields_persisted": False,
            "admissible": False,
            "error": type(exc).__name__ + ": " + str(exc)[:200],
            "publication_or_pit": None,
            "blocker": "route failed; retry required before classifying source exhaustion",
        }


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def run() -> dict[str, object]:
    receipts = [_fetch(*route) for route in ROUTES]
    for receipt in receipts:
        receipt_path = OUT / "receipts" / f"{receipt['source_id']}.json"
        _write_json_atomic(receipt_path, receipt)
        receipt["receipt_path"] = str(receipt_path.relative_to(REPO))
        receipt["receipt_sha256"] = _sha(receipt_path.read_bytes())
    report = {
        "schema": "crossasset-public-route-exhaustion-v1",
        "requested_window": {"start": "2016-01-01", "end_inclusive": "2026-07-31"},
        "retrieved_at": _utc_now(),
        "routes": receipts,
        "summary": {
            "x01_routes": sum(r["card"] == "X01" for r in receipts),
            "x03_routes": sum(r["card"] == "X03" for r in receipts),
            "successful": sum(r["status"] == "HTTP_SUCCESS" for r in receipts),
            "http_errors": sum(r["status"] == "HTTP_ERROR" for r in receipts),
            "transport_errors": sum(r["status"] == "REQUEST_ERROR" for r in receipts),
            "admitted_rows": 0,
            "economic_results_run": False,
        },
        "terminality": "A successful response is evidence of route reachability only; no route is terminally exhausted for PIT admission unless its native historical publication/availability and identity fields are present. Request failures remain retryable.",
    }
    _write_json_atomic(OUT / "route_exhaustion_report.json", report)
    report_without_pin = json.loads(json.dumps(report))
    report["report_sha256"] = hashlib.sha256(json.dumps(report_without_pin, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    _write_json_atomic(OUT / "route_exhaustion_report.json", report)
    return report


def verify_report(path: Path = OUT / "route_exhaustion_report.json") -> dict[str, object]:
    """Verify the frozen route report without making network requests."""
    if not path.is_absolute():
        path = REPO / path
    report = json.loads(path.read_text(encoding="utf-8"))
    expected = hashlib.sha256(json.dumps({key: value for key, value in report.items() if key != "report_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if report.get("report_sha256") != expected:
        raise AssertionError("route report canonical hash mismatch")
    for route in report.get("routes", []):
        receipt_path = REPO / route["receipt_path"]
        if not receipt_path.is_file() or _sha(receipt_path.read_bytes()) != route.get("receipt_sha256"):
            raise AssertionError(f"route receipt hash mismatch: {route.get('source_id')}")
        body_path = route.get("body_path")
        if body_path:
            body = REPO / body_path
            if not body.is_file() or body.stat().st_size != route.get("bytes") or _sha(body.read_bytes()) != route.get("body_sha256"):
                raise AssertionError(f"route body hash mismatch: {route.get('source_id')}")
        elif route.get("bytes") != 0 or route.get("body_sha256") is not None:
            raise AssertionError(f"error route has body metadata: {route.get('source_id')}")
    return {"routes": len(report.get("routes", [])), "report_sha256": report["report_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acquire or verify frozen crossasset public route evidence")
    parser.add_argument("--verify", action="store_true", help="verify the frozen report without network or writes")
    parser.add_argument("--report", type=Path, default=OUT / "route_exhaustion_report.json")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_report(args.report), sort_keys=True))
    else:
        print(json.dumps(run()["summary"], sort_keys=True))
