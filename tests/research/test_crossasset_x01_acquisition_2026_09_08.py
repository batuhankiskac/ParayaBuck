"""Offline tests for the X01 acquisition contract and unit checks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.research.acquire_crossasset_x01_alpaca_2026_09_08 import (
    END_EXCLUSIVE,
    OUT,
    START,
    SYMBOLS,
    _coverage,
    _fetch,
    _query,
    _rows,
)


def test_query_freezes_sip_raw_split_window() -> None:
    raw = _query(",".join(SYMBOLS), "raw")
    assert raw == {
        "symbols": "SHY,IEF,TLT",
        "timeframe": "1Day",
        "start": START,
        "end": END_EXCLUSIVE,
        "feed": "sip",
        "adjustment": "raw",
        "limit": 10000,
        "sort": "asc",
    }
    assert _query("IEF", "split")["adjustment"] == "split"


def test_rows_and_coverage_validate_units_and_cutoff() -> None:
    payload = [{"bars": {"IEF": [{"t": "2016-01-04T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 10}]}}]
    rows = _rows(payload, "split")
    assert rows[0]["symbol"] == "IEF"
    assert rows[0]["adjustment"] == "split"
    assert _coverage(rows)["values_finite_and_units_valid"] is True
    assert _coverage(rows)["unique_symbol_timestamp"] is True


def test_coverage_rejects_post_cutoff_and_duplicate_key(tmp_path: Path) -> None:
    rows = [
        {"timestamp": "2026-08-01T00:00:00Z", "symbol": "IEF", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"timestamp": "2026-08-01T00:00:00Z", "symbol": "IEF", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    result = _coverage(rows)
    assert result["unique_symbol_timestamp"] is False
    assert result["values_finite_and_units_valid"] is False


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_pagination_failure_discards_first_page() -> None:
    responses = iter(
        [
            _Response(200, {"bars": {}, "next_page_token": "opaque-token"}),
            _Response(500, {"message": "temporary failure"}),
        ]
    )
    with patch("scripts.research.acquire_crossasset_x01_alpaca_2026_09_08.requests.get", side_effect=lambda *args, **kwargs: next(responses)):
        result = _fetch("raw", symbols="IEF", max_attempts=1)
    assert result["status"] == "BLOCKED_PAGINATION"
    assert result["pages"] == []
    assert result["pages_received"] == 1
    assert result["statuses"] == [200, 500]
    assert result["page_token_hops"] == 1
    assert "page_token" not in result["request"]


@pytest.mark.parametrize(
    ("bars", "failure"),
    [
        ({"IEF": [{"t": "2016-01-04T05:00:00Z", "o": 100, "h": 99, "l": 98, "c": 99, "v": 1}]}, "finite_units_ohlc"),
        ({"IEF": [{"t": "2016-01-04T05:00:00Z", "o": "nan", "h": 101, "l": 99, "c": 100, "v": 1}]}, "finite_units_ohlc"),
        ({"SPY": [{"t": "2016-01-04T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}]}, "missing_symbol"),
        ({"IEF": [{"t": "2016-01-05T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}, {"t": "2016-01-04T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}]}, "timestamp_order"),
    ],
)
def test_successful_download_validation_fails_closed(bars: dict[str, list[dict[str, object]]], failure: str) -> None:
    response = _Response(200, {"bars": bars})
    with patch("scripts.research.acquire_crossasset_x01_alpaca_2026_09_08.requests.get", return_value=response):
        result = _fetch("raw", symbols="IEF", max_attempts=1)
    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["pages"] == []
    assert failure in result["validation_failures"]


def test_successful_download_rejects_malformed_or_unexpected_payload_schema() -> None:
    payloads = [
        ({"bars": {"IEF": [], "SPY": []}}, "unexpected_symbol"),
        ({"bars": {"IEF": [{"t": "2016-01-04T05:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100, "v": 10, "unexpected": 1}]}}, "row_schema"),
    ]
    for payload, failure in payloads:
        response = _Response(200, payload)
        with patch("scripts.research.acquire_crossasset_x01_alpaca_2026_09_08.requests.get", return_value=response):
            result = _fetch("raw", symbols="IEF", max_attempts=1)
        assert result["status"] == "BLOCKED_VALIDATION"
        assert result["pages"] == []
        assert failure in result["validation_failures"]


def test_authoritative_receipt_pins_each_sanitized_request() -> None:
    receipt = json.loads((OUT / "receipt.json").read_text(encoding="utf-8"))
    for adjustment in ("raw", "split"):
        record = receipt["adjustments"][adjustment]
        assert record["primary_request"]["adjustment"] == adjustment
        assert record["primary_request"]["symbols"] == "SHY,IEF,TLT"
        assert [item["symbol"] for item in record["fallback_attempts"]] == list(SYMBOLS)
        assert all(item["request"]["adjustment"] == adjustment for item in record["fallback_attempts"])
