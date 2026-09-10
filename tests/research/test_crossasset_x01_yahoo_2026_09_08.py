"""Offline validation checks for the Yahoo X01 fallback evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.research.acquire_crossasset_x01_yahoo_2026_09_08 import EXPECTED_ROWS, _validate_chart


def _payload(timestamp: int = 1704459600) -> dict[str, object]:
    start = datetime(2016, 1, 4, tzinfo=UTC) if timestamp == 1704459600 else datetime.fromtimestamp(timestamp, UTC)
    dates = []
    current = start
    while len(dates) < EXPECTED_ROWS:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    timestamps = [int(value.timestamp()) for value in dates]
    close = [100.0 + index * 0.01 for index in range(EXPECTED_ROWS)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": "IEF", "instrumentType": "ETF", "currency": "USD", "dataGranularity": "1d"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{"open": close, "high": [x + 1 for x in close], "low": [x - 1 for x in close], "close": close, "volume": [10] * EXPECTED_ROWS}],
                        "adjclose": [{"adjclose": list(close)}],
                    },
                    "events": {"dividends": {}, "splits": {}},
                }
            ],
            "error": None,
        }
    }


def test_yahoo_chart_validation_extracts_bars_and_dividends() -> None:
    rows, dividends, coverage = _validate_chart(_payload(), "IEF")
    assert len(rows) == EXPECTED_ROWS
    assert rows[0]["symbol"] == "IEF"
    assert len(dividends) == 0
    assert coverage["rows"] == EXPECTED_ROWS
    assert coverage["adjusted_close_reconciliation"]["checked_intervals"] == EXPECTED_ROWS - 1


def test_yahoo_chart_validation_rejects_out_of_window_payload() -> None:
    with pytest.raises(ValueError, match="coverage"):
        _validate_chart(_payload(1785542400), "IEF")


def test_yahoo_chart_validation_rejects_adjusted_close_mismatch() -> None:
    payload = _payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][1] = 1.0
    with pytest.raises(ValueError, match="adjusted_close_dividend_reconciliation"):
        _validate_chart(payload, "IEF")
