"""Acquire and validate Yahoo X01 fallback evidence without economic admission."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_yahoo"
SYMBOLS = ("SHY", "IEF", "TLT")
START = "2016-01-01"
END_EXCLUSIVE = "2026-08-01"
EXPECTED_ROWS = 2659
ADJUSTED_CLOSE_TOLERANCE = 0.001
QUERY = {
    "period1": "1451606400",
    "period2": "1785542400",
    "interval": "1d",
    "events": "div,splits",
    "includeAdjustedClose": "true",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _url(symbol: str) -> str:
    return "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _reconcile_adjusted_close(rows: list[dict[str, Any]], dividends: list[dict[str, Any]]) -> dict[str, Any]:
    """Check Yahoo's adjusted-close recurrence against quoted close plus ex-date cash."""
    by_date = {str(item["event_timestamp"])[:10]: float(item["amount"]) for item in dividends}
    checked = 0
    maximum_error = 0.0
    for previous, current in zip(rows, rows[1:]):
        prior_close = float(previous["close"])
        current_close = float(current["close"])
        prior_adjusted = float(previous["adjclose"])
        current_adjusted = float(current["adjclose"])
        expected = (current_close + by_date.get(str(current["date"]), 0.0)) / prior_close
        actual = current_adjusted / prior_adjusted
        error = abs(actual / expected - 1.0)
        maximum_error = max(maximum_error, error)
        checked += 1
    if maximum_error > ADJUSTED_CLOSE_TOLERANCE:
        raise ValueError("adjusted_close_dividend_reconciliation")
    return {"checked_intervals": checked, "max_relative_error": maximum_error, "tolerance": ADJUSTED_CLOSE_TOLERANCE}


def _validate_chart(payload: Any, symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"chart"}:
        raise ValueError("response_schema")
    chart = payload["chart"]
    if not isinstance(chart, dict) or chart.get("error") is not None or not isinstance(chart.get("result"), list) or len(chart["result"]) != 1:
        raise ValueError("response_schema")
    result = chart["result"][0]
    if not isinstance(result, dict) or not isinstance(result.get("meta"), dict):
        raise ValueError("response_schema")
    meta = result["meta"]
    if meta.get("symbol") != symbol or meta.get("instrumentType") != "ETF" or meta.get("currency") != "USD" or meta.get("dataGranularity") != "1d":
        raise ValueError("identity")
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise ValueError("response_schema")
    quote = indicators.get("quote")
    adjusted = indicators.get("adjclose")
    if not isinstance(quote, list) or len(quote) != 1 or not isinstance(quote[0], dict) or set(quote[0]) != {"open", "high", "low", "close", "volume"}:
        raise ValueError("response_schema")
    if not isinstance(adjusted, list) or len(adjusted) != 1 or not isinstance(adjusted[0], dict) or set(adjusted[0]) != {"adjclose"}:
        raise ValueError("response_schema")
    arrays = {field: quote[0][field] for field in quote[0]}
    arrays["adjclose"] = adjusted[0]["adjclose"]
    if any(not isinstance(values, list) or len(values) != len(timestamps) for values in arrays.values()):
        raise ValueError("response_schema")
    dates = [datetime.fromtimestamp(int(value), UTC).date().isoformat() for value in timestamps]
    if len(dates) != EXPECTED_ROWS or dates != sorted(set(dates)) or any(not (START <= value < END_EXCLUSIVE) for value in dates):
        raise ValueError("coverage")
    rows: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        values = {field: arrays[field][index] for field in arrays}
        if any(not _finite(value) for value in values.values()) or any(float(values[field]) <= 0 for field in ("open", "high", "low", "close", "adjclose")) or float(values["volume"]) < 0:
            raise ValueError("finite_units")
        low, high = float(values["low"]), float(values["high"])
        if high < low or not low <= float(values["open"]) <= high or not low <= float(values["close"]) <= high:
            raise ValueError("ohlc")
        rows.append({"date": date, "timestamp": datetime.fromtimestamp(int(timestamps[index]), UTC).isoformat(), "symbol": symbol, **values})
    dividends: list[dict[str, Any]] = []
    events = result.get("events", {})
    if not isinstance(events, dict) or set(events) - {"dividends", "splits"}:
        raise ValueError("response_schema")
    quote_dates = set(dates)
    for epoch, event in (events.get("dividends", {}) or {}).items():
        if not isinstance(event, dict) or set(event) - {"amount", "date"} or ("date" in event and event["date"] != int(epoch)) or not _finite(event.get("amount")) or float(event["amount"]) < 0:
            raise ValueError("dividend_schema")
        event_date = datetime.fromtimestamp(int(epoch), UTC).date().isoformat()
        if event_date not in quote_dates:
            raise ValueError("dividend_event_date")
        dividends.append({"symbol": symbol, "event_timestamp": datetime.fromtimestamp(int(epoch), UTC).isoformat(), "amount": event["amount"], "retrieved_at": None})
    for epoch, event in (events.get("splits", {}) or {}).items():
        if not isinstance(event, dict) or set(event) - {"numerator", "denominator", "date"} or ("date" in event and event["date"] != int(epoch)) or not _finite(event.get("numerator")) or not _finite(event.get("denominator")) or float(event["numerator"]) <= 0 or float(event["denominator"]) <= 0:
            raise ValueError("split_schema")
        if datetime.fromtimestamp(int(epoch), UTC).date().isoformat() not in quote_dates:
            raise ValueError("split_event_date")
    reconciliation = _reconcile_adjusted_close(rows, dividends)
    return rows, dividends, {"first": dates[0] if dates else None, "last": dates[-1] if dates else None, "rows": len(rows), "dividend_rows": len(dividends), "split_events": len(events.get("splits", {}) or {}), "adjusted_close_reconciliation": reconciliation}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(UTC).isoformat()
    records: dict[str, Any] = {}
    for symbol in SYMBOLS:
        url = _url(symbol)
        request = {"url": url, "params": QUERY}
        try:
            response = requests.get(url, params=QUERY, headers={"User-Agent": "ParayaBuck-research-probe/2026-09-08"}, timeout=30)
            raw_path = OUT / f"{symbol}_response.json"
            raw_path.write_bytes(response.content)
            record: dict[str, Any] = {"symbol": symbol, "request": request, "http_status": response.status_code, "content_type": response.headers.get("content-type", "").split(";", 1)[0], "raw_path": str(raw_path.relative_to(REPO)), "raw_sha256": _sha256(raw_path), "retrieved_at": retrieved_at, "admitted": False}
            if response.status_code != 200:
                record.update({"status": "BLOCKED_HTTP", "error": f"HTTP {response.status_code}", "rows": 0})
            else:
                try:
                    rows, dividends, coverage = _validate_chart(response.json(), symbol)
                except (TypeError, ValueError, OverflowError) as exc:
                    record.update({"status": "BLOCKED_VALIDATION", "error": str(exc), "rows": 0})
                else:
                    bar_path = OUT / f"{symbol}_bars.csv"
                    dividend_path = OUT / f"{symbol}_dividends.csv"
                    _write_csv(bar_path, ["date", "timestamp", "symbol", "open", "high", "low", "close", "volume", "adjclose"], rows)
                    _write_csv(dividend_path, ["symbol", "event_timestamp", "amount", "retrieved_at"], dividends)
                    record.update(coverage)
                    record.update({"bars_path": str(bar_path.relative_to(REPO)), "bars_sha256": _sha256(bar_path), "dividends_path": str(dividend_path.relative_to(REPO)), "dividends_sha256": _sha256(dividend_path), "available_at": None, "status": "PARTIAL_GAP" if coverage["last"] != "2026-07-31" else "ACQUIRED_NO_PIT_ADMISSION"})
            records[symbol] = record
        except requests.RequestException as exc:
            records[symbol] = {"symbol": symbol, "request": request, "http_status": None, "retrieved_at": retrieved_at, "admitted": False, "status": "BLOCKED_REQUEST", "error_type": type(exc).__name__, "rows": 0}
    receipt = {"schema": "x01-yahoo-fallback-receipt-v1", "retrieved_at": retrieved_at, "requested_window": {"start": START, "end_exclusive": END_EXCLUSIVE}, "records": records, "admitted_rows": 0, "status": "ACQUIRED_DATA_NO_ECONOMIC_ADMISSION", "blockers": ["Yahoo has no historical PIT duration/holdings or available_at release vintages", "Yahoo adjusted close is not a substitute for split-adjusted OHLC plus separately reconciled distributions", "No canonical economic result is permitted until independent review of source lineage and PIT gates"]}
    path = OUT / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
