from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
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


def bars_to_calendar_days(symbol: str, timeframe: str, bars: int) -> int:
    """Calendar span needed to collect ``bars`` of ``timeframe`` history.

    Equity bars only exist on regular trading sessions: 61 daily bars do not
    fit in 61 calendar days. The span derives from bars produced per calendar
    day (sessions per day scaled by the ~5/7 trading-day density for equity,
    plus a holiday buffer) instead of treating 1 bar as 1 calendar day.
    """
    minutes = TIMEFRAME_MINUTES[timeframe]
    if "/" in symbol:
        bars_per_day = 1440 / minutes
        multiplier = 1.05
    else:
        bars_per_session = (390 / minutes) if minutes < 1440 else 1
        bars_per_day = bars_per_session * 5 / 7
        multiplier = 1.1
    return max(2, math.ceil(bars / max(0.01, bars_per_day) * multiplier))


__all__ = [
    "NEW_YORK",
    "TIMEFRAME_MINUTES",
    "bars_to_calendar_days",
    "is_regular_equity_time",
    "timeframe_duration",
    "weekday_count",
]
