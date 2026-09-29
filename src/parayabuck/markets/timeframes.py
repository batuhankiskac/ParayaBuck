from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from parayabuck.logger import get_logger

log = get_logger("parayabuck.markets.timeframes")

TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}
NEW_YORK = ZoneInfo("America/New_York")


def timeframe_duration(timeframe: str) -> timedelta:
    try:
        return timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def is_regular_equity_time(timestamp: datetime) -> bool:
    local = timestamp.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and 9 * 60 + 30 <= minute < 16 * 60


def equity_day_bar_is_closed(timestamp: datetime, now: datetime) -> bool:
    """Whether an equity 1d bar is closed at ``now``.

    Equity daily bars close at the 16:00 ET session close of their session
    date, NOT 24 hours after the 09:30 ET open. A bar for a past session
    date is always closed; a bar for today is closed once 16:00 ET has
    passed.
    """
    local = timestamp.astimezone(NEW_YORK)
    today = now.astimezone(NEW_YORK)
    if local.date() < today.date():
        return True
    if local.date() > today.date():
        return False
    return today >= local.replace(hour=16, minute=0, second=0, microsecond=0)


def weekday_count(start: datetime, end: datetime) -> int:
    """Approximate number of trading days (Mon-Fri) in an inclusive window."""
    total_days = (end.date() - start.date()).days + 1
    if total_days <= 0:
        return 0
    full_weeks, remainder = divmod(total_days, 7)
    weekend_days = full_weeks * 2
    weekday = start.date().weekday()
    for offset in range(remainder):
        if (weekday + offset) % 7 >= 5:
            weekend_days += 1
    return total_days - weekend_days


def bars_to_calendar_days(symbol: str, timeframe: str, bars: int, end_date: datetime | date | None = None) -> int:
    """Calendar span needed to collect ``bars`` of ``timeframe`` history.

    Equity bars only exist on regular trading sessions: 61 daily bars do not
    fit in 61 calendar days. For equity the span is derived from the real
    NYSE session calendar (holiday-aware) via pandas-market-calendars when
    available, falling back to a Mon-Fri session estimate otherwise.

    ``end_date`` anchors the session-density calculation: historical
    backtests must size their warmup around the requested evaluation start
    (e.g. 2020), whose holiday calendar differs from today's, never around
    the current date.

    Results are memoised per (symbol, timeframe, bars, anchor): the NYSE
    calendar lookup is expensive. ``end_date=None`` resolves to the current
    date BEFORE the memo lookup, so a long-lived process never reuses a span
    anchored on the day it started.
    """
    # For live warmup the anchor is today; historical backtests pass the
    # evaluation start so session density reflects that era's calendar. A
    # datetime is a date, so the anchor keeps whatever the caller passed.
    anchor: date = end_date if end_date is not None else datetime.now(UTC).date()
    return _bars_to_calendar_days(symbol, timeframe, bars, anchor)


@lru_cache(maxsize=2048)
def _bars_to_calendar_days(symbol: str, timeframe: str, bars: int, today: date) -> int:
    minutes = TIMEFRAME_MINUTES[timeframe]
    bars_per_session = (390 / minutes) if minutes < 1440 else 1
    needed_sessions = max(1, math.ceil(bars / bars_per_session))
    calendar_error: Exception | None = None
    try:
        from pandas_market_calendars import get_calendar

        calendar = get_calendar("NYSE")
        # Grow the backward window until it contains enough real sessions,
        # then tighten to the exact minimum with a binary search.
        days = max(2, math.ceil(needed_sessions * 7 / 5))
        while days <= 4000:
            start = today - timedelta(days=days)
            try:
                sessions = calendar.schedule(start_date=start, end_date=today)
            except Exception as exc:
                calendar_error = exc
                break
            if len(sessions) >= needed_sessions:
                low, high = 2, days
                while low < high:
                    mid = (low + high) // 2
                    probe = calendar.schedule(start_date=today - timedelta(days=mid), end_date=today)
                    if len(probe) >= needed_sessions:
                        high = mid
                    else:
                        low = mid + 1
                return low
            days += max(2, days // 4)
    except Exception as exc:
        calendar_error = exc
    if calendar_error is not None:
        # The heuristic below over-provisions, so data stays sufficient — but
        # a broken pandas-market-calendars install would otherwise silently
        # resize every warmup window for the life of the process.
        log.warning(
            "NYSE calendar unavailable; using Mon-Fri session estimate: %s",
            calendar_error,
        )
    # Mon-Fri density with a holiday buffer.
    return max(2, math.ceil(needed_sessions * 7 / 5 * 1.1))


__all__ = [
    "NEW_YORK",
    "TIMEFRAME_MINUTES",
    "bars_to_calendar_days",
    "equity_day_bar_is_closed",
    "is_regular_equity_time",
    "timeframe_duration",
    "weekday_count",
]
