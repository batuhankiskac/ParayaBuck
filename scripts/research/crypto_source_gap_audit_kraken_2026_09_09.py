"""Acquire and verify the isolated Kraken public K10 source-gap revision.

This package is intentionally separate from the frozen crypto manifests.  It
captures public responses only and never treats retrieval time as causal
availability.  The verifier is read-only and checks every body, header, and
receipt hash plus the route-specific parsed schema summary.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crypto_source_gap_audit_2026_09_09/kraken_r2"
MANIFEST = OUT / "manifest.json"
REPORT = REPO / "docs/research/crypto_source_gap_audit_2026_09_09_r2.md"
CUTOFF = "2026-07-31T23:59:59Z"
SYMBOLS = ("PI_XBTUSD", "PI_ETHUSD")
ANALYTICS = ("liquidation-volume", "open-interest")
INTERVALS = (3600, 86400)
WINDOWS = {
    "historical_2024_01_03": ("2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z", 1704240000, 1704326400),
    "cutoff_2026_07_30": ("2026-07-30T00:00:00Z", "2026-07-31T00:00:00Z", 1785369600, 1785456000),
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_from_epoch(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def _headers_text(status: int | None, headers: Any) -> bytes:
    lines = [f"HTTP status: {status if status is not None else 'null'}"]
    if headers is not None:
        lines.extend(f"{key}: {value}" for key, value in headers.items())
    return ("\n".join(lines) + "\n").encode("utf-8")


def fetch(url: str) -> tuple[int | None, str | None, bytes, bytes, str]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read()
            return response.status, None, body, _headers_text(response.status, response.headers), response.geturl()
    except HTTPError as exc:
        body = exc.read()
        return exc.code, f"HTTPError:{exc.code}", body, _headers_text(exc.code, exc.headers), exc.geturl()
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}:{exc}", b"", b"", url


def _write_probe(slug: str, url: str, kind: str, parsed: dict[str, Any]) -> dict[str, Any]:
    body = parsed.pop("_body")
    headers = parsed.pop("_headers")
    status = parsed["status"]
    body_path = OUT / f"{slug}.body"
    header_path = OUT / f"{slug}.headers"
    receipt_path = OUT / f"{slug}.receipt.json"
    body_path.write_bytes(body)
    header_path.write_bytes(headers)
    receipt = {
        "schema": "crypto_kraken_source_gap_receipt_v1",
        "card": "K10",
        "k09_relevance": "NO_RELEVANCE_K09_OHLCV_CARD",
        "kind": kind,
        "provider": "futures.kraken.com",
        "endpoint": url,
        "final_url": parsed["final_url"],
        "retrieved_at_utc": parsed["retrieved_at_utc"],
        "http_status": status,
        "error": parsed["error"],
        "credential_fields_persisted": False,
        "body_path": str(body_path.relative_to(REPO)),
        "body_bytes": len(body),
        "body_sha256": sha_bytes(body),
        "headers_path": str(header_path.relative_to(REPO)),
        "headers_bytes": len(headers),
        "headers_sha256": sha_bytes(headers),
        "parsed": parsed["parsed"],
        "admission": parsed["admission"],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "slug": slug,
        "kind": kind,
        "endpoint": url,
        "body": str(body_path.relative_to(REPO)),
        "headers": str(header_path.relative_to(REPO)),
        "receipt": str(receipt_path.relative_to(REPO)),
        "status": status,
        "error": parsed["error"],
        "retrieved_at_utc": parsed["retrieved_at_utc"],
        "body_bytes": len(body),
        "body_sha256": sha_bytes(body),
        "headers_bytes": len(headers),
        "headers_sha256": sha_bytes(headers),
        "parsed": parsed["parsed"],
        "admission": parsed["admission"],
        "credential_fields_persisted": False,
    }


def _json_body(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _analytics_summary(body: bytes, status: int | None, error: str | None) -> tuple[dict[str, Any], str]:
    payload = _json_body(body)
    result = payload.get("result") if payload else None
    timestamps = result.get("timestamp") if isinstance(result, dict) else None
    values = result.get("data") if isinstance(result, dict) else None
    valid = isinstance(timestamps, list) and isinstance(values, list) and len(timestamps) == len(values)
    parsed = {
        "http_success": status == 200,
        "schema": "result.timestamp+result.data_arrays" if valid else "unverified_or_error",
        "row_count": len(timestamps) if valid else None,
        "count_semantics": "analytics_bucket_arrays" if valid else "not_applicable",
        "timestamp_start_utc": utc_from_epoch(int(timestamps[0])) if valid and timestamps else None,
        "timestamp_end_utc": utc_from_epoch(int(timestamps[-1])) if valid and timestamps else None,
        "data_sample": values[:3] if valid else None,
        "more": result.get("more") if isinstance(result, dict) else None,
        "publication_time_field_present": False,
        "available_at_field_present": False,
        "units_explicit_in_response": False,
        "error_payload": payload.get("errors") if payload else None,
    }
    if status == 200 and valid:
        admission = "BLOCKED_FOR_K10_PIT_PENDING_NO_ROW_AVAILABLE_AT_OR_PUBLICATION"
    else:
        admission = "BLOCKED_FOR_K10_ROUTE_ERROR_OR_SCHEMA_UNVERIFIED"
    return parsed, admission


def _trade_summary(body: bytes, status: int | None, error: str | None) -> tuple[dict[str, Any], str]:
    payload = _json_body(body)
    history = payload.get("history") if payload else None
    valid = isinstance(history, list)
    rows = history if valid else []
    types = Counter(str(row.get("type")) for row in rows if isinstance(row, dict))
    times = [str(row.get("time")) for row in rows if isinstance(row, dict) and row.get("time")]
    fields = sorted({key for row in rows if isinstance(row, dict) for key in row})
    parsed = {
        "http_success": status == 200,
        "schema": "history_trade_records" if valid else "unverified_or_error",
        "row_count": len(rows) if valid else None,
        "count_semantics": "json_history_array" if valid else "not_applicable",
        "event_types": dict(sorted(types.items())),
        "liquidation_rows": types.get("liquidation", 0),
        "first_event_time": min(times) if times else None,
        "last_event_time": max(times) if times else None,
        "fields": fields,
        "publication_time_field_present": any("publication_time" in row for row in rows if isinstance(row, dict)),
        "available_at_field_present": any("available_at" in row for row in rows if isinstance(row, dict)),
        "explicit_units": False,
        "retention_documented": "most recent 100 trades and up to past 7 days or engine restart",
        "error_payload": payload.get("error") if payload else None,
    }
    if status == 200 and valid:
        admission = "BLOCKED_FOR_K10_RETENTION_OR_PIT_OR_MISSING_LIQUIDATION_ROWS"
    else:
        admission = "BLOCKED_FOR_K10_ROUTE_ERROR_OR_SCHEMA_UNVERIFIED"
    return parsed, admission


def _probe(slug: str, url: str, kind: str, summary_fn: Any) -> dict[str, Any]:
    status, error, body, headers, final_url = fetch(url)
    parsed, admission = summary_fn(body, status, error)
    data = {
        "status": status,
        "error": error,
        "final_url": final_url,
        "retrieved_at_utc": now_utc(),
        "parsed": parsed,
        "admission": admission,
        "_body": body,
        "_headers": headers,
    }
    return _write_probe(slug, url, kind, data)


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    probes: list[dict[str, Any]] = []
    for window_slug, (_, _, since, to) in WINDOWS.items():
        for symbol in SYMBOLS:
            for metric in ANALYTICS:
                for interval in INTERVALS:
                    query = f"since={since}&interval={interval}&to={to}"
                    url = f"https://futures.kraken.com/api/charts/v1/analytics/{symbol}/{metric}?{query}"
                    slug = f"analytics_{symbol.lower()}_{metric.replace('-', '_')}_{window_slug}_{interval}s"
                    probes.append(_probe(slug, url, "public_market_analytics", _analytics_summary))
    for symbol in SYMBOLS:
        url = f"https://futures.kraken.com/derivatives/api/v3/history?symbol={symbol}"
        probes.append(_probe(f"trade_history_{symbol.lower()}_recent", url, "public_trade_history", _trade_summary))
        # The public endpoint accepts an RFC3339 string cursor; a numeric
        # millisecond cursor is rejected as invalidArgument.
        cutoff_last_time = "2026-07-31T23%3A59%3A59.000Z"
        url = f"https://futures.kraken.com/derivatives/api/v3/history?symbol={symbol}&lastTime={cutoff_last_time}"
        probes.append(_probe(f"trade_history_{symbol.lower()}_cutoff_lasttime", url, "public_trade_history", _trade_summary))
    manifest: dict[str, Any] = {
        "schema": "crypto_kraken_source_gap_manifest_v1",
        "card": "K10",
        "k09_relevance": "NO_RELEVANCE_K09_OHLCV_CARD",
        "historical_cutoff": CUTOFF,
        "frozen_at_utc": now_utc(),
        "status": "ACQUISITION_COMPLETE_BLOCKED_FOR_K10",
        "economic_run": False,
        "credential_fields_persisted": False,
        "routes": {
            "market_analytics": "Official public analytics route; liquidation-volume and open-interest; documented intervals 60..604800 seconds",
            "trade_history": "Official public trade history; type=liquidation schema but most recent 100/past 7 days or engine restart",
        },
        "probes": probes,
        "artifacts": {
            "report": {
                "path": str(REPORT.relative_to(REPO)),
                "sha256": sha_file(REPORT) if REPORT.is_file() else "PENDING_REPORT",
            }
        },
        "counts": {
            "total_probes": len(probes),
            "analytics_probes": sum(p["kind"] == "public_market_analytics" for p in probes),
            "trade_history_probes": sum(p["kind"] == "public_trade_history" for p in probes),
            "successful_http_200": sum(p["status"] == 200 for p in probes),
            "liquidation_rows_observed": sum(p["parsed"].get("liquidation_rows", 0) for p in probes),
            "admitted_rows": 0,
        },
        "source_gap_conclusion": {
            "kraken_public_routes": "NO_UNTRIED_FREE_CAUSAL_ROUTE_FOUND_WITHIN_KRAKEN_R2_SCOPE",
            "global_crypto_package": "PENDING_INDEPENDENT_REVIEW_OF_OTHER_PROVIDERS",
            "reason": "Both newly identified official public Kraken routes were probed. Responses remain non-admissible because row-level publication/available_at, explicit units, retention breadth, or liquidation rows are missing; no retrieval timestamp substitution is made.",
        },
        "next_stage_approval": "NOT_APPROVED",
        "manifest_sha256": "PENDING_FREEZE",
    }
    canonical = copy.deepcopy(manifest)
    canonical.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _verify_receipt(record: dict[str, Any]) -> None:
    body = REPO / record["body"]
    headers = REPO / record["headers"]
    receipt = REPO / record["receipt"]
    if not body.is_file() or not headers.is_file() or not receipt.is_file():
        raise ValueError(f"missing_probe_artifact:{record['slug']}")
    data = read_json(receipt)
    if data.get("body_sha256") != sha_file(body) or data.get("body_bytes") != body.stat().st_size:
        raise ValueError(f"body_receipt_mismatch:{record['slug']}")
    if data.get("headers_sha256") != sha_file(headers) or data.get("headers_bytes") != headers.stat().st_size:
        raise ValueError(f"header_receipt_mismatch:{record['slug']}")
    # The receipt is a transport-level witness, but the frozen manifest is
    # the admission boundary.  Require both declarations to agree so a body
    # and receipt cannot be replaced together without a manifest rebuild.
    for receipt_key, manifest_key in (("body_sha256", "body_sha256"), ("body_bytes", "body_bytes"), ("headers_sha256", "headers_sha256"), ("headers_bytes", "headers_bytes")):
        if data.get(receipt_key) != record.get(manifest_key):
            raise ValueError(f"manifest_receipt_{receipt_key}_mismatch:{record['slug']}")
    for key in ("endpoint", "final_url", "retrieved_at_utc", "provider", "card", "kind"):
        if not data.get(key):
            raise ValueError(f"receipt_field_missing:{record['slug']}:{key}")
    if not str(data["endpoint"]).startswith("https://") or not str(data["final_url"]).startswith("https://"):
        raise ValueError(f"receipt_endpoint_https_required:{record['slug']}")
    try:
        parsed_retrieval = datetime.fromisoformat(str(data["retrieved_at_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"receipt_retrieval_timestamp_invalid:{record['slug']}") from exc
    if parsed_retrieval.tzinfo is None or parsed_retrieval.utcoffset() is None:
        raise ValueError(f"receipt_retrieval_timestamp_naive:{record['slug']}")
    if data.get("http_status") != record.get("status") or data.get("error") != record.get("error"):
        raise ValueError(f"receipt_transport_binding_mismatch:{record['slug']}")
    if data.get("endpoint") != record.get("endpoint") or data.get("kind") != record.get("kind"):
        raise ValueError(f"receipt_binding_mismatch:{record['slug']}")
    if data.get("body_path") != record.get("body") or data.get("headers_path") != record.get("headers"):
        raise ValueError(f"receipt_path_binding_mismatch:{record['slug']}")
    if data.get("credential_fields_persisted") is not False:
        raise ValueError(f"credential_flag_invalid:{record['slug']}")
    if data.get("parsed") != record.get("parsed") or data.get("admission") != record.get("admission"):
        raise ValueError(f"parsed_summary_binding_mismatch:{record['slug']}")
    parsed = data["parsed"]
    if data.get("kind") == "public_market_analytics" and data.get("http_status") == 200:
        if parsed.get("schema") != "result.timestamp+result.data_arrays" or parsed.get("row_count", 0) <= 0:
            raise ValueError(f"analytics_schema_invalid:{record['slug']}")
        if parsed.get("publication_time_field_present") or parsed.get("available_at_field_present"):
            raise ValueError(f"analytics_pit_claim_unbound:{record['slug']}")
    if data.get("kind") == "public_trade_history" and data.get("http_status") == 200:
        if parsed.get("schema") != "history_trade_records" or parsed.get("row_count", 0) > 100:
            raise ValueError(f"trade_history_schema_invalid:{record['slug']}")
        if parsed.get("row_count", 0) > 0 and not {"time", "type", "size", "side", "uid"}.issubset(set(parsed.get("fields", []))):
            raise ValueError(f"trade_history_identity_fields_missing:{record['slug']}")


def verify_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    manifest = read_json(path)
    if manifest.get("manifest_sha256") == "PENDING_FREEZE":
        raise ValueError("manifest_not_frozen")
    canonical = copy.deepcopy(manifest)
    declared = canonical.pop("manifest_sha256", None)
    expected = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if declared != expected:
        raise ValueError("manifest_canonical_hash_mismatch")
    probes = manifest.get("probes")
    if not isinstance(probes, list) or len(probes) != manifest.get("counts", {}).get("total_probes"):
        raise ValueError("manifest_probe_count_mismatch")
    for record in probes:
        _verify_receipt(record)
    for name, artifact in manifest.get("artifacts", {}).items():
        artifact_path = REPO / artifact["path"]
        if not artifact_path.is_file() or sha_file(artifact_path) != artifact.get("sha256"):
            raise ValueError(f"artifact_hash_mismatch:{name}")
    if manifest.get("counts", {}).get("admitted_rows") != 0:
        raise ValueError("economic_admission_must_be_zero")
    if manifest.get("economic_run") is not False or manifest.get("next_stage_approval") != "NOT_APPROVED":
        raise ValueError("scope_flag_invalid")
    return {
        "status": "KRAKEN_R2_MANIFEST_VERIFIED",
        "checked_probes": len(probes),
        "checked_files": len(probes) * 3 + 1,
        "manifest_sha256": declared,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify_manifest() if args.verify else run()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
