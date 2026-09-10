"""Probe public X01 alternatives without admitting unverified market data."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_alternatives"
SYMBOLS = ("SHY", "IEF", "TLT")
START = "2016-01-01"
END = "2026-07-31"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _probe(provider: str, symbol: str, url: str) -> dict[str, object]:
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "ParayaBuck-research-probe/2026-09-08"})
        body = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        return {
            "provider": provider,
            "symbol": symbol,
            "url": url,
            "http_status": response.status_code,
            "content_type": content_type,
            "body_bytes": len(body),
            "body_sha256": _digest(body),
            "admitted": False,
            "status": "PROBE_ONLY_NOT_ADMITTED",
        }
    except requests.RequestException as exc:
        return {
            "provider": provider,
            "symbol": symbol,
            "url": url,
            "http_status": None,
            "content_type": "",
            "body_bytes": 0,
            "body_sha256": None,
            "admitted": False,
            "status": "PROBE_FAILED",
            "error_type": type(exc).__name__,
        }


def main() -> dict[str, object]:
    probes: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        probes.append(_probe("Stooq public CSV", symbol, f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&d1=20160101&d2=20260731&i=d"))
        probes.append(_probe("Yahoo Finance chart", symbol, f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=1451606400&period2=1785542400&interval=1d&events=div%2Csplits"))
    receipt = {
        "schema": "x01-public-alternative-probe-receipt-v1",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "symbols": list(SYMBOLS),
        "requested_window": {"start": START, "end_inclusive": END},
        "probes": probes,
        "admitted_rows": 0,
        "status": "ALTERNATIVES_PROBED_NO_ADMISSION",
        "blocker": "Public responses require full historical schema, split/distribution reconciliation, and source provenance before admission; no response was admitted.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
