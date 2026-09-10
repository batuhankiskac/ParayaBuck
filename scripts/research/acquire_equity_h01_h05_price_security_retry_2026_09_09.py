"""Bounded Yahoo price/security evidence retry for frozen H01-H05 symbols.

This runner is evidence-only.  It writes an isolated retry bundle, never
changes the frozen H01-H05 outputs, and never admits economic rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/equity_h01_h05_price_security_retry_2026_09_09"
SYMBOLS = ("A", "AA", "AAL", "AAPL", "AAP", "ABT", "ABCB", "ABM")
START = "2016-01-01"
END_EXCLUSIVE = "2025-01-01"
PERIOD1 = 1451606400
PERIOD2 = 1735689600
MAX_ATTEMPTS = 2
BACKOFF_SECONDS = 2.0
TIMEOUT_SECONDS = 20
UA = "ParayaBuck-research-probe/2026-09-09"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _expected_rows() -> dict[str, int]:
    path = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
    result = {symbol: 0 for symbol in SYMBOLS}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row.get("symbol")
            timestamp = str(row.get("timestamp", ""))[:10]
            if symbol in result and START <= timestamp < END_EXCLUSIVE:
                result[symbol] += 1
    return result


def _chart_url(host: str, symbol: str) -> str:
    return f"https://{host}/v8/finance/chart/{symbol}"


def _download_url(host: str, symbol: str) -> str:
    return f"https://{host}/v7/finance/download/{symbol}"


def _validate_chart(payload: Any, symbol: str, expected_rows: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"chart"}:
        raise ValueError("response_schema")
    chart = payload["chart"]
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise ValueError("response_schema")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("response_schema")
    result = results[0]
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("identity")
    if meta.get("symbol") != symbol or meta.get("currency") != "USD" or meta.get("dataGranularity") != "1d":
        raise ValueError("identity")
    if meta.get("instrumentType") not in {"EQUITY", "ETF"}:
        raise ValueError("identity")
    timestamps = result.get("timestamp")
    quote = result.get("indicators", {}).get("quote") if isinstance(result.get("indicators"), dict) else None
    adjusted = result.get("indicators", {}).get("adjclose") if isinstance(result.get("indicators"), dict) else None
    if not isinstance(timestamps, list) or len(timestamps) != expected_rows:
        raise ValueError("coverage_count")
    if not isinstance(quote, list) or len(quote) != 1 or not isinstance(quote[0], dict):
        raise ValueError("response_schema")
    fields = {"open", "high", "low", "close", "volume"}
    if set(quote[0]) != fields:
        raise ValueError("response_schema")
    arrays = dict(quote[0])
    if isinstance(adjusted, list) and len(adjusted) == 1 and isinstance(adjusted[0], dict) and set(adjusted[0]) == {"adjclose"}:
        arrays["adjclose"] = adjusted[0]["adjclose"]
    if any(not isinstance(values, list) or len(values) != len(timestamps) for values in arrays.values()):
        raise ValueError("response_schema")
    dates = [datetime.fromtimestamp(int(value), UTC).date().isoformat() for value in timestamps]
    if dates != sorted(set(dates)) or any(not (START <= value < END_EXCLUSIVE) for value in dates):
        raise ValueError("coverage_dates")
    for index in range(len(dates)):
        values = {field: arrays[field][index] for field in fields}
        if any(not _finite(value) for value in values.values()):
            raise ValueError("finite")
        if any(float(values[field]) <= 0 for field in ("open", "high", "low", "close")) or float(values["volume"]) < 0:
            raise ValueError("units")
        low, high = float(values["low"]), float(values["high"])
        if high < low or not low <= float(values["open"]) <= high or not low <= float(values["close"]) <= high:
            raise ValueError("ohlc")
    events = result.get("events", {})
    if not isinstance(events, dict) or set(events) - {"dividends", "splits"}:
        raise ValueError("event_schema")
    quote_dates = set(dates)
    event_counts: dict[str, int] = {}
    for kind in ("dividends", "splits"):
        values = events.get(kind, {}) or {}
        if not isinstance(values, dict):
            raise ValueError("event_schema")
        event_counts[kind] = len(values)
        for epoch, event in values.items():
            if not isinstance(event, dict) or "date" not in event or not _finite(event["date"]):
                raise ValueError("event_identity")
            event_timestamp_date = datetime.fromtimestamp(int(epoch), UTC).date().isoformat()
            # Yahoo may expose a declaration/ex-date distinct from the event
            # timestamp key (including a prior-day holiday/weekend).  Keep
            # both semantics in the immutable body; only the keyed event must
            # lie on a quoted session in this bounded request.
            if event_timestamp_date not in quote_dates:
                raise ValueError("event_date")
    return {
        "rows": len(dates),
        "first": dates[0],
        "last": dates[-1],
        "event_counts": event_counts,
        "identity": {"symbol": meta.get("symbol"), "instrument_type": meta.get("instrumentType"), "currency": meta.get("currency"), "granularity": meta.get("dataGranularity")},
        "adjusted_close_present": "adjclose" in arrays,
    }


def _validate_download(body: bytes, symbol: str, expected_rows: int) -> dict[str, Any]:
    text = body.decode("utf-8")
    rows = list(csv.reader(text.splitlines()))
    if not rows or rows[0] != ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        raise ValueError("download_schema")
    if len(rows) - 1 != expected_rows:
        raise ValueError("coverage_count")
    dates = [row[0] for row in rows[1:]]
    if dates != sorted(set(dates)) or any(not (START <= date < END_EXCLUSIVE) for date in dates):
        raise ValueError("coverage_dates")
    for row in rows[1:]:
        if len(row) != 7 or any(not _finite(value) for value in row[1:]):
            raise ValueError("finite")
    return {"rows": len(dates), "first": dates[0], "last": dates[-1], "header": rows[0]}


def _attempt(symbol: str, variant: str, url: str, params: dict[str, Any], expected_rows: int, attempt: int) -> dict[str, Any]:
    retrieved = datetime.now(UTC).isoformat()
    request_url = url + ("?" + urlencode(params) if params else "")
    base = {"symbol": symbol, "variant": variant, "attempt": attempt, "endpoint": url, "request_url": request_url, "retrieved_at_utc": retrieved, "credential_fields_persisted": False, "admitted": False}
    try:
        response = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return {**base, "http_status": None, "content_type": None, "status": "REQUEST_ERROR", "error_type": type(exc).__name__, "source_absence_claim": False}
    body = response.content
    stem = f"{_safe_name(symbol)}_{_safe_name(variant)}_attempt{attempt}"
    body_path = OUT / "bodies" / f"{stem}.body"
    headers_path = OUT / "headers" / f"{stem}.json"
    body_path.write_bytes(body)
    headers_path.write_text(json.dumps(dict(response.headers), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    result: dict[str, Any] = {**base, "http_status": response.status_code, "final_url": response.url, "content_type": response.headers.get("content-type", "").split(";", 1)[0], "body_path": str(body_path.relative_to(REPO)), "body_sha256": sha256_file(body_path), "body_bytes": len(body), "headers_path": str(headers_path.relative_to(REPO)), "headers_sha256": sha256_file(headers_path), "headers_bytes": headers_path.stat().st_size, "source_absence_claim": False}
    if response.status_code != 200:
        return {**result, "status": f"HTTP_{response.status_code}", "rows": 0}
    try:
        summary = _validate_chart(response.json(), symbol, expected_rows) if variant.startswith("chart") else _validate_download(body, symbol, expected_rows)
    except (TypeError, ValueError, OverflowError, UnicodeDecodeError) as exc:
        return {**result, "status": "HTTP_200_VALIDATION_FAILED", "error": str(exc), "rows": 0}
    return {**result, "status": "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION", **summary}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bodies").mkdir(exist_ok=True)
    (OUT / "headers").mkdir(exist_ok=True)
    expected = _expected_rows()
    routes: list[tuple[str, str, dict[str, Any]]] = []
    for symbol in SYMBOLS:
        params = {"period1": PERIOD1, "period2": PERIOD2, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}
        routes.extend([(f"chart_query1_{symbol}", _chart_url("query1.finance.yahoo.com", symbol), params), (f"chart_query2_{symbol}", _chart_url("query2.finance.yahoo.com", symbol), params)])
        routes.append((f"download_query1_{symbol}", _download_url("query1.finance.yahoo.com", symbol), {"period1": PERIOD1, "period2": PERIOD2, "interval": "1d", "events": "history", "includeAdjustedClose": "true"}))
    records: list[dict[str, Any]] = []
    for index, (variant, url, params) in enumerate(routes):
        symbol = variant.rsplit("_", 1)[-1]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            records.append(_attempt(symbol, variant, url, params, expected[symbol], attempt))
            if records[-1].get("status") == "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION":
                break
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
        if index < len(routes) - 1:
            time.sleep(BACKOFF_SECONDS)
    successes = [record for record in records if record.get("status") == "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION"]
    receipt = {
        "schema": "equity-h01-h05-price-security-retry-v1",
        "run_started_at_utc": records[0]["retrieved_at_utc"] if records else datetime.now(UTC).isoformat(),
        "symbols": list(SYMBOLS),
        "requested_window": {"start": START, "end_exclusive": END_EXCLUSIVE, "period1": PERIOD1, "period2": PERIOD2},
        "max_attempts_per_route": MAX_ATTEMPTS,
        "backoff_seconds": BACKOFF_SECONDS,
        "expected_rows_from_pinned_v3": expected,
        "records": records,
        "successful_schema_valid_routes": len(successes),
        "admitted_rows": 0,
        "status": "RETRY_EVIDENCE_CAPTURED_NO_PIT_ADMISSION" if successes else "RETRY_ACCESS_EXHAUSTED_FOR_FROZEN_RUN",
        "source_absence_claim": False,
        "blockers": ["historical common-security continuity remains unproven", "historical raw-price equivalence remains unproven", "Yahoo response publication/available_at vintages are absent", "no economic or portfolio admission"],
    }
    path = OUT / "retry_receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(path.relative_to(REPO))
    receipt["receipt_sha256"] = sha256_file(path)
    return receipt


def revalidate_existing() -> dict[str, Any]:
    """Reclassify captured HTTP-200 bodies after a local validator correction."""
    path = OUT / "retry_receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    expected = receipt["expected_rows_from_pinned_v3"]
    for record in receipt["records"]:
        if record.get("http_status") != 200 or not record.get("body_path"):
            continue
        body_path = REPO / record["body_path"]
        try:
            body = body_path.read_bytes()
            symbol = str(record["symbol"])
            summary = _validate_chart(json.loads(body), symbol, int(expected[symbol])) if str(record["variant"]).startswith("chart") else _validate_download(body, symbol, int(expected[symbol]))
        except (OSError, TypeError, ValueError, OverflowError, UnicodeDecodeError) as exc:
            record.update({"status": "HTTP_200_VALIDATION_FAILED", "error": str(exc), "rows": 0})
        else:
            record.pop("error", None)
            record.update({"status": "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION", **summary})
    successes = [record for record in receipt["records"] if record.get("status") == "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION"]
    receipt["successful_schema_valid_routes"] = len(successes)
    receipt["status"] = "RETRY_EVIDENCE_CAPTURED_NO_PIT_ADMISSION" if successes else "RETRY_ACCESS_EXHAUSTED_FOR_FROZEN_RUN"
    receipt.pop("receipt_path", None)
    receipt.pop("receipt_sha256", None)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(path.relative_to(REPO))
    receipt["receipt_sha256"] = sha256_file(path)
    return receipt


def verify_receipt(path: Path = OUT / "retry_receipt.json") -> dict[str, Any]:
    """Verify the isolated retry receipt, captured bytes, and validation counts."""
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("schema") != "equity-h01-h05-price-security-retry-v1" or receipt.get("admitted_rows") != 0 or receipt.get("source_absence_claim") is not False:
        raise ValueError("receipt_policy")
    expected = receipt.get("expected_rows_from_pinned_v3", {})
    successes = 0
    for record in receipt.get("records", []):
        if record.get("credential_fields_persisted") is not False or not record.get("endpoint", "").startswith("https://"):
            raise ValueError("receipt_transport")
        status = str(record.get("status", ""))
        if status == "REQUEST_ERROR":
            if record.get("http_status") is not None or record.get("body_path") or record.get("headers_path"):
                raise ValueError("request_error_binding")
            continue
        if not isinstance(record.get("http_status"), int) or not record.get("body_path") or not record.get("headers_path"):
            raise ValueError("receipt_binding")
        body = REPO / record["body_path"]
        headers = REPO / record["headers_path"]
        if sha256_file(body) != record.get("body_sha256") or body.stat().st_size != record.get("body_bytes"):
            raise ValueError("body_hash_mismatch")
        if sha256_file(headers) != record.get("headers_sha256") or headers.stat().st_size != record.get("headers_bytes"):
            raise ValueError("headers_hash_mismatch")
        if record.get("http_status") == 200:
            try:
                summary = _validate_chart(json.loads(body.read_bytes()), record["symbol"], int(expected[record["symbol"]])) if str(record["variant"]).startswith("chart") else _validate_download(body.read_bytes(), record["symbol"], int(expected[record["symbol"]]))
            except (TypeError, ValueError, OverflowError, UnicodeDecodeError):
                if status != "HTTP_200_VALIDATION_FAILED" or record.get("rows") != 0:
                    raise ValueError("validation_status_mismatch")
            else:
                if status != "HTTP_200_SCHEMA_VALID_NO_PIT_ADMISSION" or record.get("rows") != summary["rows"]:
                    raise ValueError("success_status_mismatch")
                successes += 1
        elif status != f"HTTP_{record['http_status']}":
            raise ValueError("http_status_mismatch")
    if receipt.get("successful_schema_valid_routes") != successes:
        raise ValueError("success_count_mismatch")
    return {"records": len(receipt.get("records", [])), "successful_schema_valid_routes": successes, "admitted_rows": 0, "status": receipt.get("status")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-revalidate", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify_receipt() if args.verify else revalidate_existing() if args.offline_revalidate else main()
    print(json.dumps(result, indent=2, sort_keys=True))
