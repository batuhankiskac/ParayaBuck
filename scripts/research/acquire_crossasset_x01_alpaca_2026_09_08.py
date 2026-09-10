"""Acquire X01 SHY/IEF/TLT raw and split-adjusted Alpaca SIP bars.

The script is research-only. Credentials are read from the environment and
never written. Failed probes produce a sanitized receipt so access blockers
remain auditable without fabricating data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_alpaca"
SYMBOLS = ("SHY", "IEF", "TLT")
BASE_URL = "https://data.alpaca.markets/v2/stocks/bars"
START = "2016-01-01"
END_EXCLUSIVE = "2026-08-01"
ALPACA_REQUIRED_BAR_FIELDS = frozenset({"t", "o", "h", "l", "c", "v"})
ALPACA_OPTIONAL_BAR_FIELDS = frozenset({"n", "vw"})
ALPACA_PAGE_FIELDS = frozenset({"bars", "next_page_token"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _query(symbols: str, adjustment: str, feed: str = "sip") -> dict[str, Any]:
    return {
        "symbols": symbols,
        "timeframe": "1Day",
        "start": START,
        "end": END_EXCLUSIVE,
        "feed": feed,
        "adjustment": adjustment,
        "limit": 10000,
        "sort": "asc",
    }


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _fetch(adjustment: str, symbols: str = ",".join(SYMBOLS), feed: str = "sip", max_attempts: int = 4) -> dict[str, Any]:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    request_params = _query(symbols, adjustment, feed)
    pages: list[Any] = []
    statuses: list[int] = []
    errors: list[str] = []
    token_count = 0
    for attempt in range(max_attempts):
        try:
            response = requests.get(BASE_URL, headers=headers, params=request_params, timeout=45)
            statuses.append(response.status_code)
            if response.status_code == 200:
                payload = _safe_json(response)
                if not isinstance(payload, dict):
                    errors.append("successful response was not a JSON object")
                    return {"status": "BLOCKED_PARSE", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": 0, "errors": errors, "validation_failures": ["row_schema"], "request": request_params}
                pages.append(payload)
                token = payload.get("next_page_token")
                while token:
                    token_count += 1
                    page_params = dict(request_params, page_token=token)
                    response = requests.get(BASE_URL, headers=headers, params=page_params, timeout=45)
                    statuses.append(response.status_code)
                    if response.status_code != 200:
                        errors.append(f"pagination status {response.status_code}")
                        return {"status": "BLOCKED_PAGINATION", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": len(pages), "errors": errors, "request": request_params}
                    payload = _safe_json(response)
                    if not isinstance(payload, dict):
                        errors.append("pagination response was not a JSON object")
                        return {"status": "BLOCKED_PAGINATION", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": len(pages), "errors": errors, "request": request_params}
                    pages.append(payload)
                    token = payload.get("next_page_token")
                try:
                    expected_symbols = set(symbols.split(","))
                    normalized_rows = _rows(pages, adjustment)
                    coverage = _coverage(normalized_rows, expected_symbols)
                    payload_symbols = {symbol for page in pages for symbol in page["bars"]}
                    unexpected = payload_symbols - expected_symbols
                    if unexpected:
                        coverage["admissible"] = False
                        coverage["validation_failures"] = sorted(set(coverage["validation_failures"]) | {"unexpected_symbol"})
                except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                    errors.append(f"validation failed: {type(exc).__name__}")
                    return {"status": "BLOCKED_VALIDATION", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": len(pages), "errors": errors, "validation_failures": ["row_schema"], "request": request_params}
                if not coverage["admissible"]:
                    errors.append("validation failed: " + ",".join(coverage["validation_failures"]))
                    return {"status": "BLOCKED_VALIDATION", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": len(pages), "errors": errors, "validation_failures": coverage["validation_failures"], "request": request_params}
                return {"status": "DOWNLOADED", "pages": pages, "statuses": statuses, "page_token_hops": token_count, "pages_received": len(pages), "errors": errors, "validation_failures": [], "request": request_params}
            if response.status_code not in (429, 500, 502, 503, 504):
                body = _safe_json(response)
                errors.append(str(body.get("message", "provider error")) if isinstance(body, dict) else f"HTTP {response.status_code}")
                break
        except requests.RequestException as exc:
            errors.append(type(exc).__name__ + ": " + str(exc))
        if attempt + 1 < max_attempts:
            time.sleep(1.5 * (attempt + 1))
    return {"status": "BLOCKED", "pages": [], "statuses": statuses, "page_token_hops": token_count, "pages_received": 0, "errors": errors, "validation_failures": [], "request": request_params}


def _rows(payloads: list[Any], adjustment: str, expected_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    payload_symbols: set[str] = set()
    for page in payloads:
        if not isinstance(page, dict) or not set(page).issubset(ALPACA_PAGE_FIELDS) or "bars" not in page:
            raise ValueError("row_schema: Alpaca page is not an object with a bars mapping")
        bars = page["bars"]
        if not isinstance(bars, dict):
            raise ValueError("row_schema: Alpaca bars is not an object")
        for symbol, values in bars.items():
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("row_schema: Alpaca symbol is invalid")
            payload_symbols.add(symbol)
            if not isinstance(values, list):
                raise ValueError("row_schema: Alpaca symbol bars is not a list")
            for bar in values:
                if not isinstance(bar, dict) or not ALPACA_REQUIRED_BAR_FIELDS.issubset(bar) or not set(bar).issubset(ALPACA_REQUIRED_BAR_FIELDS | ALPACA_OPTIONAL_BAR_FIELDS):
                    raise ValueError("row_schema: Alpaca bar fields are invalid")
                rows.append({"timestamp": bar.get("t"), "symbol": symbol, "adjustment": adjustment, "open": bar.get("o"), "high": bar.get("h"), "low": bar.get("l"), "close": bar.get("c"), "volume": bar.get("v")})
    if expected_symbols is not None and payload_symbols != expected_symbols:
        raise ValueError("identity: Alpaca payload symbols do not match requested symbols")
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "symbol", "adjustment", "open", "high", "low", "close", "volume"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _coverage(rows: list[dict[str, Any]], expected_symbols: set[str] | None = None) -> dict[str, Any]:
    required_fields = {"timestamp", "symbol", "adjustment", "open", "high", "low", "close", "volume"}
    validation_failures: set[str] = set()
    if any(not isinstance(row, dict) or set(row) != required_fields for row in rows):
        validation_failures.add("row_schema")
    keys = [(row.get("symbol"), row.get("timestamp")) for row in rows if isinstance(row, dict)]
    valid = bool(rows)
    timestamps_by_symbol: dict[str, list[datetime]] = {}
    valid = valid and "row_schema" not in validation_failures
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_fields:
            continue
        date = str(row.get("timestamp") or "")[:10]
        if not (START <= date < END_EXCLUSIVE):
            valid = False
            validation_failures.add("date_window")
        try:
            timestamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            values = [float(row[field]) for field in ("open", "high", "low", "close", "volume")]
            if timestamp.tzinfo is None:
                raise ValueError("timestamp has no timezone")
            timestamps_by_symbol.setdefault(str(row.get("symbol")), []).append(timestamp)
            row_valid = all(value == value and abs(value) != float("inf") for value in values) and all(value > 0 for value in values[:4]) and values[4] >= 0 and values[2] <= values[0] <= values[1] and values[2] <= values[3] <= values[1]
            if not row_valid:
                validation_failures.add("finite_units_ohlc")
            valid = valid and row_valid
        except (TypeError, ValueError):
            valid = False
            validation_failures.add("finite_units_ohlc")
    symbols = {str(row.get("symbol")) for row in rows if isinstance(row, dict)}
    if len(keys) != len(set(keys)):
        valid = False
        validation_failures.add("duplicate_key")
    if any(timestamps != sorted(timestamps) for timestamps in timestamps_by_symbol.values()):
        valid = False
        validation_failures.add("timestamp_order")
    if expected_symbols is not None:
        missing = expected_symbols - symbols
        unexpected = symbols - expected_symbols
        if missing:
            valid = False
            validation_failures.add("missing_symbol")
        if unexpected:
            valid = False
            validation_failures.add("unexpected_symbol")
    return {"rows": len(rows), "symbols": sorted(symbols), "first": min((row["timestamp"] for row in rows), default=None), "last": max((row["timestamp"] for row in rows), default=None), "unique_symbol_timestamp": len(keys) == len(set(keys)), "timestamps_ordered_by_symbol": all(timestamps == sorted(timestamps) for timestamps in timestamps_by_symbol.values()), "values_finite_and_units_valid": valid, "validation_failures": sorted(validation_failures), "admissible": valid and bool(rows) and (expected_symbols is None or symbols == expected_symbols)}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    records: dict[str, Any] = {}
    for adjustment in ("raw", "split"):
        result = _fetch(adjustment)
        primary_result = result
        fallback_attempts: list[dict[str, Any]] = []
        fallback_results: list[dict[str, Any]] = []
        if result["status"] != "DOWNLOADED":
            # The documented multi-symbol endpoint also accepts one-symbol
            # requests. Exercise that pagination shape before recording an
            # access blocker; successful fallback pages remain provisional
            # until the same coverage gates pass.
            for symbol in SYMBOLS:
                fallback = _fetch(adjustment, symbols=symbol, max_attempts=1)
                fallback_results.append(fallback)
                fallback_attempts.append({"symbol": symbol, "request": fallback["request"], "status": fallback["status"], "http_statuses": fallback["statuses"], "page_token_hops": fallback["page_token_hops"], "pages_received": fallback["pages_received"], "errors": fallback["errors"]})
            if fallback_attempts and all(item["status"] == "DOWNLOADED" for item in fallback_attempts):
                fallback_pages = [page for fallback in fallback_results for page in fallback["pages"]]
                result = {"status": "DOWNLOADED_FALLBACK", "pages": fallback_pages, "statuses": primary_result["statuses"], "page_token_hops": primary_result["page_token_hops"], "pages_received": len(fallback_pages), "errors": primary_result["errors"], "request": primary_result["request"]}
        raw_pages = OUT / f"{adjustment}_pages.json"
        raw_pages.write_text(json.dumps(result["pages"], separators=(",", ":")) + "\n", encoding="utf-8")
        rows = _rows(result["pages"], adjustment)
        coverage = _coverage(rows, set(SYMBOLS))
        if result["status"] in ("DOWNLOADED", "DOWNLOADED_FALLBACK") and set(coverage["symbols"]) != set(SYMBOLS):
            result["status"] = "BLOCKED_INCOMPLETE_SYMBOLS"
            rows = []
            coverage = _coverage(rows)
        rows.sort(key=lambda row: (row["symbol"], row["timestamp"] or ""))
        normalized = OUT / f"{adjustment}_bars.csv"
        _write_rows(normalized, rows)
        records[adjustment] = {"status": result["status"], "primary_request": primary_result["request"], "primary_http_statuses": primary_result["statuses"], "primary_page_token_hops": primary_result["page_token_hops"], "primary_pages_received": primary_result["pages_received"], "http_statuses": result["statuses"], "page_token_hops": result["page_token_hops"], "errors": result["errors"], "validation_failures": result.get("validation_failures", []), "fallback_attempts": fallback_attempts, "coverage": coverage, "raw_pages": str(raw_pages.relative_to(REPO)), "raw_pages_sha256": sha256(raw_pages), "normalized": str(normalized.relative_to(REPO)), "normalized_sha256": sha256(normalized)}
    raw_rows = _rows(json.loads((OUT / "raw_pages.json").read_text(encoding="utf-8")), "raw")
    split_rows = _rows(json.loads((OUT / "split_pages.json").read_text(encoding="utf-8")), "split")
    raw_keys = {(row["symbol"], row["timestamp"]) for row in raw_rows}
    split_keys = {(row["symbol"], row["timestamp"]) for row in split_rows}
    raw_split_key_parity = raw_keys == split_keys
    if not raw_split_key_parity:
        raw_rows = []
        split_rows = []
        for adjustment in ("raw", "split"):
            normalized = OUT / f"{adjustment}_bars.csv"
            _write_rows(normalized, [])
            records[adjustment]["status"] = "BLOCKED_KEY_PARITY"
            records[adjustment]["coverage"] = _coverage([])
            records[adjustment]["normalized_sha256"] = sha256(normalized)
    receipt = {"schema": "x01-alpaca-acquisition-receipt-v1", "retrieved_at": datetime.now().astimezone().isoformat(), "endpoint": BASE_URL, "query": _query(",".join(SYMBOLS), "raw"), "adjustments": records, "raw_split_key_parity": raw_split_key_parity, "distribution_source": "NOT_ACQUIRED", "historical_duration_source": "NOT_ACQUIRED", "status": "ACQUIRED" if raw_split_key_parity and all(item["status"] in ("DOWNLOADED", "DOWNLOADED_FALLBACK") for item in records.values()) and raw_rows and split_rows else "BLOCKED_OR_PARTIAL"}
    receipt_path = OUT / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def offline_reconcile() -> dict[str, Any]:
    """Add sanitized request contracts to the existing receipt without HTTP calls."""
    receipt_path = OUT / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for adjustment in ("raw", "split"):
        record = receipt["adjustments"][adjustment]
        record["primary_request"] = _query(",".join(SYMBOLS), adjustment)
        record.setdefault("primary_http_statuses", record.get("http_statuses", []))
        record.setdefault("primary_page_token_hops", record.get("page_token_hops", 0))
        record.setdefault("primary_pages_received", 0)
        record.setdefault("primary_errors", record.get("errors", []))
        record.setdefault("validation_failures", [])
        reconciled: list[dict[str, Any]] = []
        for item in record.get("fallback_attempts", []):
            fallback = dict(item)
            symbol = str(fallback["symbol"])
            fallback["request"] = _query(symbol, adjustment)
            fallback.setdefault("pages_received", 0)
            fallback.setdefault("page_token_hops", 0)
            fallback.setdefault("http_statuses", [])
            fallback.setdefault("errors", [])
            fallback.setdefault("validation_failures", [])
            reconciled.append(fallback)
        record["fallback_attempts"] = reconciled
    receipt["query"] = _query(",".join(SYMBOLS), "raw")
    receipt["offline_reconciled"] = True
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-reconcile", action="store_true")
    args = parser.parse_args()
    print(json.dumps(offline_reconcile() if args.offline_reconcile else main(), indent=2, sort_keys=True))
