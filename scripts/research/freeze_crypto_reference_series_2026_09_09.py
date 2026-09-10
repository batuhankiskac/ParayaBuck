"""Freeze a receipt-backed reference price series for crypto diagnostics.

The series is intentionally a diagnostic reference only: Coin Metrics PriceUSD
has an event timestamp but the captured response has no row-level publication
or available_at field.  This script never treats retrieval time as causal
availability and never changes the frozen K01-K12 gates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes"
BODY = ROOT / "coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.body"
HEADERS = ROOT / "coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.headers"
RECEIPT = ROOT / "coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.receipt.json"
NORMALIZED = ROOT / "coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.csv"
MANIFEST = ROOT / "reference_manifest.json"
URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc,eth&metrics=PriceUSD&frequency=1d&start_time=2022-01-01&end_time=2026-07-31&page_size=10000"


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build() -> dict[str, Any]:
    payload = json.loads(BODY.read_text(encoding="utf-8"))
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("price_data_missing")
    accepted = []
    quarantined = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("asset") or not row.get("time") or row.get("PriceUSD") in (None, ""):
            raise ValueError("price_row_schema")
        if str(row["time"])[:10] > "2026-07-31":
            quarantined += 1
            continue
        accepted.append({"asset": str(row["asset"]), "event_time": str(row["time"]), "price_usd": str(row["PriceUSD"])})
    accepted.sort(key=lambda row: (row["asset"], row["event_time"]))
    if len(accepted) != 3346 or quarantined:
        raise ValueError(f"unexpected_reference_rows:{len(accepted)}:{quarantined}")
    NORMALIZED.write_text(
        "asset,event_time,price_usd,available_at,source_body_sha256\n"
        + "".join(f"{r['asset']},{r['event_time']},{r['price_usd']},,{sha(BODY.read_bytes())}\n" for r in accepted),
        encoding="utf-8",
    )
    receipt = {
        "schema": "crypto_reference_price_receipt_v1",
        "endpoint": URL,
        "final_url": URL,
        "retrieved_at_utc": utc_now(),
        "http_status": 200,
        "error": None,
        "credential_fields_persisted": False,
        "body_path": str(BODY.relative_to(REPO)),
        "body_bytes": BODY.stat().st_size,
        "body_sha256": sha(BODY.read_bytes()),
        "headers_path": str(HEADERS.relative_to(REPO)),
        "headers_bytes": HEADERS.stat().st_size,
        "headers_sha256": sha(HEADERS.read_bytes()),
        "raw_rows": len(rows),
        "accepted_rows": len(accepted),
        "quarantined_rows_after_cutoff": quarantined,
        "assets": sorted({r["asset"] for r in accepted}),
        "event_time_start": accepted[0]["event_time"],
        "event_time_end": accepted[-1]["event_time"],
        "publication_time_field_present": False,
        "available_at_field_present": False,
        "availability_semantics": "diagnostic-only event timestamp; no row-level PIT; retrieval time never substitutes",
        "causal_admissible": False,
    }
    RECEIPT.write_bytes(canonical(receipt))
    manifest = {
        "schema": "crypto_reference_price_manifest_v1",
        "purpose": "diagnostic-only reference returns for K01/K03/K04/K09/K11",
        "provider": "Coin Metrics Community API",
        "metric": "PriceUSD",
        "frequency": "1d",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "endpoint": URL,
        "body": {"path": str(BODY.relative_to(REPO)), "sha256": sha(BODY.read_bytes()), "bytes": BODY.stat().st_size},
        "headers": {"path": str(HEADERS.relative_to(REPO)), "sha256": sha(HEADERS.read_bytes()), "bytes": HEADERS.stat().st_size},
        "receipt": {"path": str(RECEIPT.relative_to(REPO)), "sha256": sha(RECEIPT.read_bytes())},
        "normalized": {"path": str(NORMALIZED.relative_to(REPO)), "sha256": sha(NORMALIZED.read_bytes()), "rows": len(accepted)},
        "rows": len(accepted),
        "asset_rows": {asset: sum(r["asset"] == asset for r in accepted) for asset in sorted({r["asset"] for r in accepted})},
        "available_at_rows": 0,
        "causal_admissible": False,
        "economic_run": False,
        "manifest_sha256": None,
    }
    manifest["manifest_sha256"] = sha(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"}))
    MANIFEST.write_bytes(canonical(manifest))
    return {"status": "CRYPTO_REFERENCE_SERIES_FROZEN_DIAGNOSTIC_ONLY", "rows": len(accepted), "manifest_sha256": manifest["manifest_sha256"]}


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != sha(canonical({k: v for k, v in manifest.items() if k != "manifest_sha256"})):
        raise ValueError("reference_manifest_hash_mismatch")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("endpoint") != URL or receipt.get("http_status") != 200 or receipt.get("causal_admissible") is not False:
        raise ValueError("reference_receipt_transport_or_causal_mismatch")
    if receipt.get("body_sha256") != sha(BODY.read_bytes()) or receipt.get("body_bytes") != BODY.stat().st_size:
        raise ValueError("reference_body_mismatch")
    if receipt.get("headers_sha256") != sha(HEADERS.read_bytes()) or receipt.get("headers_bytes") != HEADERS.stat().st_size:
        raise ValueError("reference_headers_mismatch")
    if receipt.get("accepted_rows") != 3346 or receipt.get("available_at_field_present") is not False:
        raise ValueError("reference_count_or_pit_mismatch")
    if manifest.get("receipt", {}).get("sha256") != sha(RECEIPT.read_bytes()) or manifest.get("normalized", {}).get("sha256") != sha(NORMALIZED.read_bytes()):
        raise ValueError("reference_manifest_artifact_mismatch")
    with NORMALIZED.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 3346 or any(row.get("available_at") for row in rows):
        raise ValueError("reference_normalized_rows_mismatch")
    return {"status": "CRYPTO_REFERENCE_SERIES_VERIFIED", "rows": len(rows), "read_only": True, "network_free": True, "manifest_sha256": manifest["manifest_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
