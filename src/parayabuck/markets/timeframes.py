from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
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
    fit in 61 calendar days. For equity the span is derived from the real
    NYSE session calendar (holiday-aware) via pandas-market-calendars when
    available, falling back to a Mon-Fri session estimate otherwise. Crypto
    trades 24/7 and scales purely by time.
    """
    minutes = TIMEFRAME_MINUTES[timeframe]
    if "/" in symbol:
        bars_per_day = 1440 / minutes
        multiplier = 1.05
        return max(2, math.ceil(bars / max(0.01, bars_per_day) * multiplier))
    bars_per_session = (390 / minutes) if minutes < 1440 else 1
    needed_sessions = max(1, math.ceil(bars / bars_per_session))
    try:
        from pandas_market_calendars import get_calendar

        calendar = get_calendar("NYSE")
        today = datetime.now(UTC).date()
        # Grow the backward window until it contains enough real sessions,
        # then tighten to the exact minimum with a binary search.
        days = max(2, math.ceil(needed_sessions * 7 / 5))
        while days <= 4000:
            start = today - timedelta(days=days)
            try:
                sessions = calendar.schedule(start_date=start, end_date=today)
            except Exception:
                break
            if len(sessions) >= needed_sessions:
                low, high = 2, days
                while low < high:
                    mid = (low + high) // 2
                    probe = calendar.schedule(
                        start_date=today - timedelta(days=mid), end_date=today
                    )
                    if len(probe) >= needed_sessions:
                        high = mid
                    else:
                        low = mid + 1
                return low
            days += max(2, days // 4)
    except Exception:
        pass
    # Mon-Fri density with a holiday buffer.
    return max(2, math.ceil(needed_sessions * 7 / 5 * 1.1))


__all__ = [
    "NEW_YORK",
    "TIMEFRAME_MINUTES",
    "bars_to_calendar_days",
    "is_regular_equity_time",
    "timeframe_duration",
    "weekday_count",
]
