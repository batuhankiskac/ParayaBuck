"""Single NYSE trading calendar for bars, health and the decision pipeline.

Every "is the US equity session open / when did it close" question resolves
here, from the exchange schedule (pandas-market-calendars) rather than
weekday arithmetic. Weekday arithmetic says 09:30-16:00 on Thanksgiving and
that a 13:00 close is a 16:00 close; both are wrong, and both used to leak
into the decision bar, stream readiness and the health panel.

This module imports nothing from ``parayabuck`` so any layer may use it.
When the calendar cannot be loaded the fallbacks are the OLD behaviour
(weekday 09:30-16:00, close at 16:00 ET): a degraded calendar must not stop
ingestion or the session-close decision. The failure is logged once, the
degraded state is cached (the calendar is retried at most every
``CALENDAR_RETRY_SECONDS``) and :func:`calendar_degraded_reason` exposes it
for health surfaces.
"""

from __future__ import annotations

import logging
import threading
import time as _monotonic_clock
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# Fallback session boundaries, used only when the calendar is unavailable.
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)

# Lookback/lookahead window for "sessions around now" queries.
CALENDAR_LOOKBACK_DAYS = 14

# While degraded, the calendar is re-tried at most this often.
CALENDAR_RETRY_SECONDS = 300.0

_log = logging.getLogger("parayabuck.markets.nyse_calendar")
_degraded_lock = threading.Lock()
_degraded_reason: str | None = None
_degraded_retry_at = 0.0


def calendar_degraded_reason() -> str | None:
    """Why the NYSE calendar is unavailable (weekday fallback in use), or None."""
    return _degraded_reason


def _mark_degraded(exc: Exception) -> None:
    global _degraded_reason, _degraded_retry_at
    with _degraded_lock:
        first = _degraded_reason is None
        _degraded_reason = f"{type(exc).__name__}: {exc}"[:300]
        _degraded_retry_at = _monotonic_clock.monotonic() + CALENDAR_RETRY_SECONDS
    if first:
        _log.warning(
            "NYSE calendar unavailable; using the weekday 09:30-16:00 ET fallback: %s", _degraded_reason, exc_info=True
        )


def _mark_healthy() -> None:
    global _degraded_reason
    if _degraded_reason is None:
        return
    with _degraded_lock:
        _degraded_reason = None
    _log.warning("NYSE calendar available again")


def _fallback_bounds(session: date) -> tuple[datetime, datetime] | None:
    if session.weekday() >= 5:
        return None
    return (
        datetime.combine(session, _REGULAR_OPEN, tzinfo=NEW_YORK).astimezone(UTC),
        datetime.combine(session, _REGULAR_CLOSE, tzinfo=NEW_YORK).astimezone(UTC),
    )


def _fallback_sessions(start: date, end: date) -> tuple[tuple[date, datetime, datetime], ...]:
    """Weekday 09:30-16:00 ET sessions in [start, end] (holidays included)."""
    sessions: list[tuple[date, datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        bounds = _fallback_bounds(cursor)
        if bounds is not None:
            sessions.append((cursor, bounds[0], bounds[1]))
        cursor += timedelta(days=1)
    return tuple(sessions)


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
    if _degraded_reason is not None and _monotonic_clock.monotonic() < _degraded_retry_at:
        return _fallback_bounds(session)
    try:
        sessions = nyse_sessions(session, session)
    except Exception as exc:
        _mark_degraded(exc)
        return _fallback_bounds(session)
    _mark_healthy()
    for day, market_open, market_close in sessions:
        if day == session:
            return market_open, market_close
    return None


def session_close_utc(session: date) -> datetime | None:
    """The session's NYSE close in UTC (early closes included), else None."""
    bounds = session_bounds(session)
    return None if bounds is None else bounds[1]


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


def sessions_around(now: datetime) -> tuple[tuple[date, datetime, datetime], ...]:
    """Sessions in a window around ``now`` (cached by the calendar).

    Same degraded contract as ``session_bounds``: a calendar failure falls
    back to the regular-weekday window so the intent watchdog keeps
    evaluating (old alarms can clear, new misses still alarm) instead of
    throwing on every pass.
    """
    today = now.astimezone(NEW_YORK).date()
    start = today - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    end = today + timedelta(days=CALENDAR_LOOKBACK_DAYS)
    if _degraded_reason is not None and _monotonic_clock.monotonic() < _degraded_retry_at:
        return _fallback_sessions(start, end)
    try:
        sessions = nyse_sessions(start, end)
    except Exception as exc:
        _mark_degraded(exc)
        return _fallback_sessions(start, end)
    _mark_healthy()
    return sessions


__all__ = [
    "CALENDAR_LOOKBACK_DAYS",
    "NEW_YORK",
    "calendar_degraded_reason",
    "is_session_open",
    "nyse_sessions",
    "session_bounds",
    "session_close_utc",
    "sessions_around",
]
