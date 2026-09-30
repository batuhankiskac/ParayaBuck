"""Single NYSE trading calendar for bars, health and the decision pipeline.

Every "is the US equity session open / when did it close" question resolves
here, from the exchange schedule (pandas-market-calendars) rather than
weekday arithmetic. Weekday arithmetic says 09:30-16:00 on Thanksgiving and
that a 13:00 close is a 16:00 close; both are wrong, and both used to leak
into the decision bar, stream readiness and the health panel.

This module imports nothing from ``parayabuck`` so any layer may use it.
When the calendar cannot be loaded the fallbacks are the OLD behaviour
(weekday 09:30-16:00, close at 16:00 ET): a degraded calendar must not stop
ingestion or the session-close decision.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# Fallback session boundaries, used only when the calendar is unavailable.
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)

# Lookback/lookahead window for "sessions around now" queries.
CALENDAR_LOOKBACK_DAYS = 14


@lru_cache(maxsize=64)
def nyse_sessions(start: date, end: date) -> tuple[tuple[date, datetime, datetime], ...]:
    """``(session_date, open_utc, close_utc)`` for every NYSE session in [start, end]."""
    from pandas_market_calendars import get_calendar

    calendar = get_calendar("NYSE")
    schedule = calendar.schedule(start_date=start, end_date=end)
    sessions: list[tuple[date, datetime, datetime]] = []
    for session, row in schedule.iterrows():
        market_open = row["market_open"].to_pydatetime()
        market_close = row["market_close"].to_pydatetime()
        sessions.append(
            (
                session.date() if hasattr(session, "date") else session,
                market_open.astimezone(UTC),
                market_close.astimezone(UTC),
            )
        )
    return tuple(sessions)


def session_bounds(session: date) -> tuple[datetime, datetime] | None:
    """``(open_utc, close_utc)`` of one NYSE session, or None if it is closed.

    Holidays and weekends return None. A calendar failure returns the
    regular-weekday window rather than None so a dependency problem can
    never stop the runtime from ingesting or deciding.
    """
    try:
        for day, market_open, market_close in nyse_sessions(session, session):
            if day == session:
                return market_open, market_close
    except Exception:
        if session.weekday() < 5:
            return (
                datetime.combine(session, _REGULAR_OPEN, tzinfo=NEW_YORK).astimezone(UTC),
                datetime.combine(session, _REGULAR_CLOSE, tzinfo=NEW_YORK).astimezone(UTC),
            )
        return None
    return None


def session_close_utc(session: date) -> datetime | None:
    """The session's NYSE close in UTC (early closes included), else None."""
    bounds = session_bounds(session)
    return None if bounds is None else bounds[1]


def session_open_utc(session: date) -> datetime | None:
    """The session's NYSE open in UTC, else None."""
    bounds = session_bounds(session)
    return None if bounds is None else bounds[0]


def is_session_open(moment: datetime) -> bool:
    """Whether ``moment`` falls inside the NYSE regular session for its date.

    False on holidays, on weekends, before the open and after the close —
    including after a 13:00 ET early close.
    """
    local = moment.astimezone(NEW_YORK)
    bounds = session_bounds(local.date())
    if bounds is None:
        return False
    moment_utc = moment.astimezone(UTC)
    return bounds[0] <= moment_utc < bounds[1]


def session_minutes(session: date) -> int:
    """Number of one-minute bars in one NYSE session (390 on a full day)."""
    bounds = session_bounds(session)
    if bounds is None:
        return 0
    return int((bounds[1] - bounds[0]).total_seconds() // 60)


def sessions_around(now: datetime) -> tuple[tuple[date, datetime, datetime], ...]:
    """Sessions in a window around ``now`` (cached by the calendar)."""
    today = now.astimezone(NEW_YORK).date()
    return nyse_sessions(today - timedelta(days=CALENDAR_LOOKBACK_DAYS), today + timedelta(days=CALENDAR_LOOKBACK_DAYS))


__all__ = [
    "CALENDAR_LOOKBACK_DAYS",
    "NEW_YORK",
    "is_session_open",
    "nyse_sessions",
    "session_bounds",
    "session_close_utc",
    "session_minutes",
    "session_open_utc",
    "sessions_around",
]
