from __future__ import annotations

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


__all__ = ["NEW_YORK", "TIMEFRAME_MINUTES", "is_regular_equity_time", "timeframe_duration"]
