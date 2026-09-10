"""Bounded official public-route acquisition for the frozen K01-K12 cards.

This revision only adds route evidence under the isolated max-testing package.
It never changes the frozen adapter/secondary manifests and never converts a
retrieval timestamp or bucket timestamp into row-level causal availability.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes"
MANIFEST = OUT / "route_manifest.json"
ASSESSMENT = OUT / "route_assessment.json"
CUTOFF = "2026-07-31T23:59:59Z"
PRIOR_EVIDENCE = (
    REPO / "data/research_manifests/crypto_queue_causal_adapters_2026_09_08.json",
    REPO / "data/research_manifests/crypto_secondary_causal_gate_2026_09_08.json",
    REPO / "data/research/external/crypto_source_gap_audit_2026_09_09/kraken_r2/manifest.json",
)
LONG_HISTORY = (
    REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K01_MVRV_BTC_ETH_1d.csv",
    REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K03_FEE_USAGE_BTC_ETH_1d.csv",
    REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K04_SUPPLY_ISSUANCE_BTC_ETH_1d.csv",
)
WINDOWS = {
    "historical_2024_01_03": (1704240000, 1704326400),
    "cutoff_2026_07_30": (1785369600, 1785456000),
}
LONG_WINDOW = ("full_2022_to_cutoff", 1640995200, 1785456000)
KRAKEN_SYMBOLS = ("PI_XBTUSD", "PI_ETHUSD")
KRAKEN_ANALYTICS = ("future-basis", "funding", "open-interest")
CM_PROBES = (
    ("K01", "CapMVRVCur,CapMrktCurUSD"),
    ("K03", "FeeTotNtv"),
    ("K04", "IssTotNtv,SplyCur"),
    ("K12", "SOPR"),
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _headers(status: int | None, headers: object) -> bytes:
    lines = [f"HTTP status: {status if status is not None else 'null'}"]
    if headers is not None:
        lines.extend(f"{key}: {value}" for key, value in headers.items())
    return ("\n".join(lines) + "\n").encode()


def fetch(url: str) -> tuple[int | None, str | None, bytes, bytes, str]:
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=45) as response:
            body = response.read()
            return response.status, None, body, _headers(response.status, response.headers), response.geturl()
    except HTTPError as exc:
        body = exc.read()
        return exc.code, f"HTTPError:{exc.code}", body, _headers(exc.code, exc.headers), exc.geturl()
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}:{exc}", b"", b"", url


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def parse_kraken(body: bytes, status: int | None, error: str | None) -> dict[str, object]:
    payload = _json(body)
    result = payload.get("result") if isinstance(payload, dict) else None
    timestamps = result.get("timestamp") if isinstance(result, dict) else None
    values = result.get("data") if isinstance(result, dict) else None
    value_arrays = values if isinstance(values, dict) else {"data": values}
    valid = isinstance(timestamps, list) and isinstance(values, (dict, list)) and bool(value_arrays) and (len(values) == len(timestamps) if isinstance(values, list) else all(isinstance(value, list) and len(timestamps) == len(value) for value in value_arrays.values()))
    fields = sorted(result) if isinstance(result, dict) else []
    return {
        "http_success": status == 200,
        "schema": "result.timestamp+result.data_arrays" if valid else "unverified_or_error",
        "row_count": len(timestamps) if valid else None,
        "count_semantics": "analytics_bucket_arrays" if valid else "not_applicable",
        "timestamp_start_epoch": timestamps[0] if valid and timestamps else None,
        "timestamp_end_epoch": timestamps[-1] if valid and timestamps else None,
        "timestamp_unit": "milliseconds" if valid and timestamps and int(timestamps[0]) > 10**12 else "seconds" if valid and timestamps else None,
        "value_fields": sorted(value_arrays) if valid else [],
        "value_samples": {key: value[:2] for key, value in value_arrays.items()} if valid else {},
        "result_fields": fields,
        "more": result.get("more") if isinstance(result, dict) else None,
        "publication_time_field_present": False,
        "available_at_field_present": False,
        "units_explicit_in_response": False,
        "error_payload": payload.get("errors") if isinstance(payload, dict) else error,
    }


def parse_coinmetrics(body: bytes, status: int | None, error: str | None) -> dict[str, object]:
    payload = _json(body)
    rows = payload.get("data") if isinstance(payload, dict) else None
    valid = isinstance(rows, list)
    rows = rows if valid else []
    fields = sorted({field for row in rows if isinstance(row, dict) for field in row})
    assets = sorted({str(row.get("asset")) for row in rows if isinstance(row, dict) and row.get("asset")})
    times = sorted(str(row.get("time")) for row in rows if isinstance(row, dict) and row.get("time"))
    return {
        "http_success": status == 200,
        "schema": "json_data_array" if valid else "unverified_or_error",
        "row_count": len(rows) if valid else None,
        "count_semantics": "json_data_array" if valid else "not_applicable",
        "assets": assets,
        "fields": fields,
        "timestamp_start": times[0] if times else None,
        "timestamp_end": times[-1] if times else None,
        "publication_time_field_present": any("publication" in field.lower() for field in fields),
        "available_at_field_present": any(field == "available_at" for field in fields),
        "units_explicit_in_response": "unit" in fields,
        "error_payload": payload.get("error") if isinstance(payload, dict) else error,
    }


def write_probe(slug: str, card: str, kind: str, url: str, parser: object) -> dict[str, object]:
    status, error, body, headers, final_url = fetch(url)
    parsed = parser(body, status, error)
    body_path = OUT / f"{slug}.body"
    headers_path = OUT / f"{slug}.headers"
    receipt_path = OUT / f"{slug}.receipt.json"
    body_path.write_bytes(body)
    headers_path.write_bytes(headers)
    has_rows = isinstance(parsed.get("row_count"), int) and int(parsed["row_count"]) > 0
    available = bool(parsed.get("available_at_field_present") or parsed.get("publication_time_field_present"))
    admission = "PIT_PENDING" if status == 200 and has_rows and not available else "DATA_ACCESS_BLOCKED"
    receipt = {
        "schema": "crypto_max_public_route_receipt_v1",
        "card": card,
        "kind": kind,
        "endpoint": url,
        "final_url": final_url,
        "retrieved_at_utc": now_utc(),
        "http_status": status,
        "error": error,
        "credential_fields_persisted": False,
        "body_path": str(body_path.relative_to(REPO)),
        "body_bytes": len(body),
        "body_sha256": sha_bytes(body),
        "headers_path": str(headers_path.relative_to(REPO)),
        "headers_bytes": len(headers),
        "headers_sha256": sha_bytes(headers),
        "parsed": parsed,
        "available_at_semantics": "explicit_row_or_publication field required; retrieval/bucket time never substitutes",
        "admission": admission,
    }
    receipt_path.write_bytes(canonical(receipt))
    return {
        "slug": slug,
        "card": card,
        "kind": kind,
        "endpoint": url,
        "final_url": final_url,
        "body": str(body_path.relative_to(REPO)),
        "headers": str(headers_path.relative_to(REPO)),
        "receipt": str(receipt_path.relative_to(REPO)),
        "status": status,
        "error": error,
        "body_bytes": len(body),
        "body_sha256": sha_bytes(body),
        "headers_bytes": len(headers),
        "headers_sha256": sha_bytes(headers),
        "parsed": parsed,
        "admission": admission,
    }


def prior_route_evidence() -> dict[str, object]:
    return {
        "source_files": [{"path": str(path.relative_to(REPO)), "sha256": sha_file(path)} for path in PRIOR_EVIDENCE],
        "route_matrix_semantics": {
            "K02": "prior Coin Metrics catalog/adjusted-transfer probes: catalog visible, metric data HTTP403; no entitlement/PIT",
            "K05": "prior 22 configured asset announcement/confirmation probes: contract IDs visible, asset-specific unlock transaction IDs absent",
            "K06": "prior Farside current/WP and SEC issuer/series probes: current snapshot/blocked mapping, no immutable daily flow vintage",
            "K07": "prior Coin Metrics exchange-flow and Glassnode methodology probes: rows exist but mutable labels/status-time have no PIT vintage",
            "K08": "prior Coin Metrics catalog/flow probes: exact exchange-flow metric unavailable/HTTP400; no contract/label PIT",
            "K10": "prior Binance/Deribit plus isolated Kraken R2 routes: one pilot day, missing availability and no admitted liquidation rows",
            "K11": "prior Binance full-day signed-trade archive: exact IDs and archive bound retained, every row available_at blank; archive evidence only",
        },
        "reprobe_policy": "Do not repeat a frozen exact-exhausted route; new-route rows above are separately receipt-backed.",
    }


def long_history_evidence() -> list[dict[str, object]]:
    evidence = []
    for path in LONG_HISTORY:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        times = sorted(row["event_time"] for row in rows)
        evidence.append({
            "path": str(path.relative_to(REPO)),
            "sha256": sha_file(path),
            "rows": len(rows),
            "start": times[0] if times else None,
            "end": times[-1] if times else None,
            "available_at_rows": sum(bool(row.get("available_at", "").strip()) for row in rows),
        })
    return evidence


def run() -> dict[str, object]:
    OUT.mkdir(parents=True, exist_ok=True)
    probes: list[dict[str, object]] = []
    for window, (since, to) in WINDOWS.items():
        for symbol in KRAKEN_SYMBOLS:
            for metric in KRAKEN_ANALYTICS:
                params = urlencode({"since": since, "interval": 86400, "to": to})
                url = f"https://futures.kraken.com/api/charts/v1/analytics/{symbol}/{metric}?{params}"
                slug = f"kraken_{symbol.lower()}_{metric.replace('-', '_')}_{window}"
                probes.append(write_probe(slug, "K09", "kraken_market_analytics", url, parse_kraken))
    for card, metrics in CM_PROBES:
        params = urlencode({"assets": "btc,eth", "metrics": metrics, "frequency": "1d", "start_time": "2024-01-01", "end_time": "2024-01-04", "page_size": 1000})
        url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?{params}"
        slug = f"coinmetrics_{card.lower()}_metric_alias_probe"
        probes.append(write_probe(slug, card, "coinmetrics_metric_alias_route", url, parse_coinmetrics))
    manifest = {
        "schema": "crypto_max_public_route_manifest_v1",
        "historical_cutoff": CUTOFF,
        "route_scope": "new official Kraken K09 analytics families and metric-parametric Coin Metrics probes for K01/K03/K04/K12",
        "prior_route_evidence": prior_route_evidence(),
        "long_history_evidence": long_history_evidence(),
        "economic_run": False,
        "admitted_rows": 0,
        "credential_fields_persisted": False,
        "probes": probes,
        "counts": {
            "total_probes": len(probes),
            "kraken_probes": sum(p["card"] == "K09" for p in probes),
            "coinmetrics_probes": sum(p["kind"] == "coinmetrics_metric_alias_route" for p in probes),
            "http_200": sum(p["status"] == 200 for p in probes),
            "rows_observed": sum(int(p["parsed"]["row_count"] or 0) for p in probes),
            "pit_pending_probes": sum(p["admission"] == "PIT_PENDING" for p in probes),
            "admitted_rows": 0,
        },
        "source_absence_claim": False,
        "next_stage_approval": "NOT_APPROVED",
        "manifest_sha256": None,
    }
    manifest["manifest_sha256"] = sha_bytes(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"}))
    MANIFEST.write_bytes(canonical(manifest))
    by_card: dict[str, list[dict[str, object]]] = {f"K{i:02d}": [] for i in range(1, 13)}
    for probe in probes:
        by_card[str(probe["card"])].append(probe)
    assessment = {
        "schema": "crypto_max_public_route_assessment_v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "economic_run": False,
        "admitted_rows": 0,
        "next_stage_approval": "NOT_APPROVED",
        "cards": {
            card: {
                "probes": rows,
                "rows_observed": sum(int(row["parsed"]["row_count"] or 0) for row in rows),
                "available_or_publication_fields": sum(bool(row["parsed"].get("available_at_field_present") or row["parsed"].get("publication_time_field_present")) for row in rows),
                "admitted_rows": 0,
                "status": "PIT_PENDING" if any(row["admission"] == "PIT_PENDING" for row in rows) else "DATA_ACCESS_BLOCKED" if rows else "NO_NEW_ROUTE_PROBED",
            }
            for card, rows in by_card.items()
        },
    }
    ASSESSMENT.write_bytes(canonical(assessment))
    receipt = {
        "schema": "crypto_max_public_route_validation_receipt_v1",
        "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": sha_file(MANIFEST)},
        "assessment": {"path": str(ASSESSMENT.relative_to(REPO)), "sha256": sha_file(ASSESSMENT)},
        "probe_count": len(probes),
        "body_header_receipts_bound": True,
        "economic_run": False,
        "network_used": True,
        "admitted_rows": 0,
    }
    (OUT / "validation_receipt.json").write_bytes(canonical(receipt))
    return {"status": "CRYPTO_MAX_PUBLIC_ROUTE_ACQUISITION_COMPLETE_WITHOUT_ADMISSION", "probe_count": len(probes), "manifest_sha256": manifest["manifest_sha256"], "rows_observed": manifest["counts"]["rows_observed"], "admitted_rows": 0}


def reparse() -> dict[str, object]:
    """Reparse captured bodies offline without issuing requests or changing URLs."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    probes = manifest.get("probes")
    if not isinstance(probes, list):
        raise ValueError("probe_inventory_missing")
    for probe in probes:
        body = REPO / probe["body"]
        receipt_path = REPO / probe["receipt"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        parser = parse_kraken if str(probe["kind"]).startswith("kraken_market_analytics") else parse_coinmetrics
        parsed = parser(body.read_bytes(), probe.get("status"), probe.get("error"))
        has_rows = isinstance(parsed.get("row_count"), int) and int(parsed["row_count"]) > 0
        available = bool(parsed.get("available_at_field_present") or parsed.get("publication_time_field_present"))
        admission = "PIT_PENDING" if probe.get("status") == 200 and has_rows and not available else "DATA_ACCESS_BLOCKED"
        receipt["parsed"] = parsed
        receipt["admission"] = admission
        receipt_path.write_bytes(canonical(receipt))
        probe["parsed"] = parsed
        probe["admission"] = admission
    manifest["counts"] = {
        "total_probes": len(probes),
        "kraken_probes": sum(p["card"] == "K09" for p in probes),
        "coinmetrics_probes": sum(p["kind"] == "coinmetrics_metric_alias_route" for p in probes),
        "http_200": sum(p["status"] == 200 for p in probes),
        "rows_observed": sum(int(p["parsed"]["row_count"] or 0) for p in probes),
        "pit_pending_probes": sum(p["admission"] == "PIT_PENDING" for p in probes),
        "admitted_rows": 0,
    }
    manifest["prior_route_evidence"] = prior_route_evidence()
    manifest["long_history_evidence"] = long_history_evidence()
    manifest["manifest_sha256"] = sha_bytes(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"}))
    MANIFEST.write_bytes(canonical(manifest))
    by_card: dict[str, list[dict[str, object]]] = {f"K{i:02d}": [] for i in range(1, 13)}
    for probe in probes:
        by_card[str(probe["card"])].append(probe)
    assessment = {
        "schema": "crypto_max_public_route_assessment_v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "economic_run": False,
        "admitted_rows": 0,
        "next_stage_approval": "NOT_APPROVED",
        "cards": {
            card: {
                "probes": rows,
                "rows_observed": sum(int(row["parsed"]["row_count"] or 0) for row in rows),
                "available_or_publication_fields": sum(bool(row["parsed"].get("available_at_field_present") or row["parsed"].get("publication_time_field_present")) for row in rows),
                "admitted_rows": 0,
                "status": "PIT_PENDING" if any(row["admission"] == "PIT_PENDING" for row in rows) else "DATA_ACCESS_BLOCKED" if rows else "NO_NEW_ROUTE_PROBED",
            }
            for card, rows in by_card.items()
        },
    }
    ASSESSMENT.write_bytes(canonical(assessment))
    receipt_path = OUT / "validation_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest"] = {"path": str(MANIFEST.relative_to(REPO)), "sha256": sha_file(MANIFEST)}
    receipt["assessment"] = {"path": str(ASSESSMENT.relative_to(REPO)), "sha256": sha_file(ASSESSMENT)}
    receipt["probe_count"] = len(probes)
    receipt_path.write_bytes(canonical(receipt))
    return {"status": "CRYPTO_MAX_PUBLIC_ROUTE_REPARSED_OFFLINE", "probe_count": len(probes), "manifest_sha256": manifest["manifest_sha256"], "rows_observed": manifest["counts"]["rows_observed"], "admitted_rows": 0}


def append_long_history() -> dict[str, object]:
    """Fetch one longer official Kraken window, leaving prior probes untouched."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    probes = manifest.get("probes", [])
    since, to = LONG_WINDOW[1:]
    additions = []
    for symbol in KRAKEN_SYMBOLS:
        for metric in KRAKEN_ANALYTICS:
            params = urlencode({"since": since, "interval": 86400, "to": to})
            url = f"https://futures.kraken.com/api/charts/v1/analytics/{symbol}/{metric}?{params}"
            slug = f"kraken_{symbol.lower()}_{metric.replace('-', '_')}_{LONG_WINDOW[0]}"
            if any(probe.get("slug") == slug for probe in probes):
                raise ValueError(f"long_probe_already_present:{slug}")
            additions.append(write_probe(slug, "K09", "kraken_market_analytics_long_history", url, parse_kraken))
    manifest["probes"] = probes + additions
    MANIFEST.write_bytes(canonical(manifest))
    result = reparse()
    result["status"] = "CRYPTO_MAX_PUBLIC_ROUTE_LONG_HISTORY_ACQUIRED"
    result["added_probes"] = len(additions)
    return result


def verify() -> dict[str, object]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = sha_bytes(canonical({key: value for key, value in payload.items() if key != "manifest_sha256"}))
    if payload.get("manifest_sha256") != expected:
        raise ValueError("manifest_canonical_hash_mismatch")
    prior = payload.get("prior_route_evidence", {}).get("source_files", [])
    if not isinstance(prior, list) or not prior:
        raise ValueError("prior_route_evidence_missing")
    for declaration in prior:
        path = REPO / declaration["path"]
        if not path.is_file() or sha_file(path) != declaration.get("sha256"):
            raise ValueError(f"prior_route_hash_mismatch:{declaration.get('path')}")
    long_history = payload.get("long_history_evidence")
    if not isinstance(long_history, list) or len(long_history) != 3:
        raise ValueError("long_history_evidence_missing")
    for declaration in long_history:
        path = REPO / declaration["path"]
        if not path.is_file() or sha_file(path) != declaration.get("sha256") or declaration.get("available_at_rows") != 0:
            raise ValueError(f"long_history_pin_mismatch:{declaration.get('path')}")
    probes = payload.get("probes")
    if not isinstance(probes, list) or len(probes) != payload["counts"]["total_probes"]:
        raise ValueError("probe_count_mismatch")
    for probe in probes:
        for key in ("body", "headers", "receipt"):
            path = REPO / probe[key]
            if not path.is_file():
                raise ValueError(f"artifact_missing:{probe['slug']}:{key}")
        receipt = json.loads((REPO / probe["receipt"]).read_text(encoding="utf-8"))
        if receipt.get("endpoint") != probe.get("endpoint") or not str(receipt.get("endpoint", "")).startswith("https://"):
            raise ValueError(f"receipt_endpoint_mismatch:{probe['slug']}")
        if receipt.get("http_status") != probe.get("status") or receipt.get("error") != probe.get("error"):
            raise ValueError(f"receipt_transport_mismatch:{probe['slug']}")
        if receipt.get("body_sha256") != sha_file(REPO / probe["body"]) or receipt.get("body_bytes") != (REPO / probe["body"]).stat().st_size:
            raise ValueError(f"receipt_body_mismatch:{probe['slug']}")
        if receipt.get("headers_sha256") != sha_file(REPO / probe["headers"]) or receipt.get("headers_bytes") != (REPO / probe["headers"]).stat().st_size:
            raise ValueError(f"receipt_headers_mismatch:{probe['slug']}")
        if probe.get("body_sha256") != receipt.get("body_sha256") or probe.get("headers_sha256") != receipt.get("headers_sha256"):
            raise ValueError(f"manifest_receipt_mismatch:{probe['slug']}")
        if receipt.get("parsed") != probe.get("parsed") or receipt.get("admission") != probe.get("admission"):
            raise ValueError(f"parsed_receipt_mismatch:{probe['slug']}")
        if probe.get("admission") == "PIT_PENDING" and probe["parsed"].get("available_at_field_present"):
            raise ValueError(f"pit_admission_inconsistent:{probe['slug']}")
    if payload.get("economic_run") is not False or payload.get("admitted_rows") != 0:
        raise ValueError("unsafe_economic_flag")
    return {"status": "CRYPTO_MAX_PUBLIC_ROUTE_MANIFEST_VERIFIED", "checked_probes": len(probes), "read_only": True, "network_free": True, "manifest_sha256": expected}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--reparse", action="store_true")
    parser.add_argument("--long", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else reparse() if args.reparse else append_long_history() if args.long else run(), sort_keys=True))


if __name__ == "__main__":
    main()
