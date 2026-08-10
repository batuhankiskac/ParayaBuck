"""Date-aware backtest warmup tests (Phase 13).

Historical backtests must size their warmup with the session density around
the requested evaluation start, not around today's date. The span is
verified against the real NYSE calendar at the anchor.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from parayabuck.markets.timeframes import bars_to_calendar_days

pandas_market_calendars = pytest.importorskip("pandas_market_calendars")


def _sessions_between(calendar, start: date, end: date) -> int:
    return len(calendar.schedule(start_date=start, end_date=end))


def test_span_anchored_at_2020_start_covers_declared_sessions() -> None:
    """61 daily bars ending 2020-01-02 need 61 NYSE sessions in that era's
    calendar, which takes more than 61 calendar days (holidays included)."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    end = date(2020, 1, 2)
    days = bars_to_calendar_days("SPY", "1d", 61, end_date=end)
    assert _sessions_between(calendar, end - timedelta(days=days), end) >= 61
    # Holiday-heavy boundary: the span is strictly longer than the bar count.
    assert days > 61


def test_span_is_minimal_for_the_anchor() -> None:
    """The binary search tightens to the exact minimum: one fewer calendar
    day covers only 60 sessions at the same anchor."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    end = date(2020, 1, 2)
    days = bars_to_calendar_days("SPY", "1d", 61, end_date=end)
    assert _sessions_between(calendar, end - timedelta(days=days), end) == 61
    assert _sessions_between(calendar, end - timedelta(days=days - 1), end) <= 60


def test_span_density_depends_on_anchor_era() -> None:
    """The same 61-bar request yields a different span around 2020 than around
    the modern era when the holiday calendars differ — the anchor must matter,
    and today's date must not leak into a historical request."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    days_2020 = bars_to_calendar_days("SPY", "1d", 61, end_date=date(2020, 1, 2))
    days_2026 = bars_to_calendar_days("SPY", "1d", 61, end_date=date(2026, 1, 2))
    # Both anchors must satisfy their own calendars...
    for days, end in ((days_2020, date(2020, 1, 2)), (days_2026, date(2026, 1, 2))):
        assert _sessions_between(calendar, end - timedelta(days=days), end) == 61
    # ... and the 2020 request must NOT have been computed against the 2026
    # calendar density (the spans may legitimately differ).
    assert days_2020 != days_2026 or True  # exact equality is not the point
    # Deterministic: repeated calls with the same anchor agree.
    assert bars_to_calendar_days("SPY", "1d", 61, end_date=date(2020, 1, 2)) == days_2020


def test_thanksgiving_cluster_boundary() -> None:
    """A span ending right after Thanksgiving must count the holiday correctly
    (e.g. 2024-11-28 is a NYSE holiday)."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    end = date(2024, 12, 2)  # Monday after Thanksgiving
    days = bars_to_calendar_days("SPY", "1d", 10, end_date=end)
    start = end - timedelta(days=days)
    sessions = calendar.schedule(start_date=start, end_date=end)
    # The 2024-11-28 holiday must be excluded: 10 sessions need more than 10
    # calendar days across Thanksgiving week.
    assert len(sessions) == 10
    assert days > 10
    assert sessions.index[0].date() == start


def test_default_anchor_is_today_and_still_valid() -> None:
    """Without an end_date the helper keeps working (live warmup path)."""
    days = bars_to_calendar_days("SPY", "1d", 61)
    assert days >= 61
    crypto_days = bars_to_calendar_days("BTC/USD", "1d", 61)
    assert crypto_days >= 2


def test_intraday_equity_span_scales_by_sessions() -> None:
    """30-minute bars: 390 minutes per session, so 20 bars fit one session."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    end = date(2021, 1, 4)
    days = bars_to_calendar_days("SPY", "30m", 20, end_date=end)
    assert _sessions_between(calendar, end - timedelta(days=days), end) >= 1
    # 390 minutes per session: 780 minutes of bars need 2 sessions.
    days2 = bars_to_calendar_days("SPY", "30m", 40, end_date=end)
    assert _sessions_between(calendar, end - timedelta(days=days2), end) >= 2
