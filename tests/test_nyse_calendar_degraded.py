"""A broken NYSE calendar degrades once, visibly, and is not re-loaded per call."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from parayabuck.markets import nyse_calendar


def test_calendar_failure_is_cached_logged_once_and_recovers(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr(nyse_calendar, "_degraded_reason", None)
    monkeypatch.setattr(nyse_calendar, "_degraded_retry_at", 0.0)
    calls: list[date] = []

    def broken(start: date, end: date):
        calls.append(start)
        raise ImportError("pandas_market_calendars missing")

    monkeypatch.setattr(nyse_calendar, "nyse_sessions", broken)
    monday = date(2026, 9, 21)
    expected = (datetime(2026, 9, 21, 13, 30, tzinfo=UTC), datetime(2026, 9, 21, 20, 0, tzinfo=UTC))

    with caplog.at_level("WARNING", logger="parayabuck.markets.nyse_calendar"):
        for _ in range(3):
            assert nyse_calendar.session_bounds(monday) == expected
    assert len(calls) == 1
    assert len([record for record in caplog.records if "unavailable" in record.getMessage()]) == 1
    assert "ImportError" in (nyse_calendar.calendar_degraded_reason() or "")
    assert nyse_calendar.session_bounds(date(2026, 9, 26)) is None  # Saturday fallback

    def healthy(start: date, end: date):
        return ((monday, *expected),)

    monkeypatch.setattr(nyse_calendar, "nyse_sessions", healthy)
    monkeypatch.setattr(nyse_calendar, "_degraded_retry_at", 0.0)
    assert nyse_calendar.session_bounds(monday) == expected
    assert nyse_calendar.calendar_degraded_reason() is None
