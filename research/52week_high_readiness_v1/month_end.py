"""Research-only NYSE last-completed-session scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pandas_market_calendars import get_calendar


class NyseLastCompletedSessionPolicy:
    """Use the published exchange calendar, never the presence of future bars."""

    def __init__(self) -> None:
        self._calendar = get_calendar("NYSE")

    def _session_and_next(self, completed_at: datetime):
        session_date = completed_at.astimezone(ZoneInfo("America/New_York")).date()
        schedule = self._calendar.schedule(
            start_date=session_date,
            end_date=session_date + timedelta(days=10),
        )
        dates = [value.date() for value in schedule.index]
        if not dates or dates[0] != session_date:
            return None
        return dates[0], dates[1]

    def should_decide(self, completed_at: datetime) -> bool:
        pair = self._session_and_next(completed_at)
        return bool(pair and pair[0].month != pair[1].month)

    def next_tradable_open(self, completed_at: datetime) -> datetime:
        session_date = completed_at.astimezone(ZoneInfo("America/New_York")).date()
        schedule = self._calendar.schedule(
            start_date=session_date + timedelta(days=1),
            end_date=session_date + timedelta(days=10),
        )
        if schedule.empty:
            raise ValueError("no next NYSE session in calendar window")
        value = schedule.iloc[0]["market_open"]
        return value.to_pydatetime()
