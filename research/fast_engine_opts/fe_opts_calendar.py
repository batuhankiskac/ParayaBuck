"""fe_opts_calendar -- the NYSE early-close (13:00 ET) calendar, derived from the exchange rules.

An NYSE session closes early at 13:00 ET on three kinds of day:

  1. the Friday after Thanksgiving Day (the 4th Thursday of November);
  2. the day before Independence Day -- July 3, whenever July 3 is itself a session;
  3. Christmas Eve -- December 24, whenever December 24 is itself a session.

"Whenever it is a session" is where the holiday-observance rule enters: an NYSE holiday that falls
on a **Saturday** is observed on the **preceding Friday**, and one that falls on a **Sunday** on the
**following Monday** (NYSE Rule 7.2 / the published hours-and-calendars table). So July 3 is not a
session when July 4 is a Saturday (observed Friday) or a Monday (July 3 is a Sunday), and December
24 is not a session when December 25 is a Saturday (observed Friday) or a Monday (December 24 is a
Sunday). The rule is applied *to the sessions the window actually has* (`flags`), so a calendar the
data disagrees with simply produces no flag.

Evidence: `docs/research/kernel_opts_v1.md` §"the early-close calendar" -- in the engine's own QQQ
bars the presence/absence of the July 3 and December 24 sessions matches the observance rule in
every year 1999-2026, and the derived dates' median volume is ~0.4 of the session median against
1.0 for all sessions together. The ceiling is that this is the *published rule*, not an
authoritative day-by-day calendar: the volume signature is unambiguous for the Christmas Eve and
Black Friday dates and ambiguous for a handful of July 3 dates in thin holiday weeks (the flags
are what the arm's mechanism asks for; a wrong flag costs one session of decision lag a few times
a year).

Source: NYSE "Hours & Calendars" (https://www.nyse.com/markets/hours-calendars) and NYSE Rule 7.2
(days and hours of trading, 1:00 p.m. early closings).
"""

from __future__ import annotations

import calendar as _cal
from datetime import date as _date

import numpy as np

__all__ = ["early_close_dates", "flags", "is_early_close_date", "thanksgiving_friday"]


def thanksgiving_friday(year: int) -> _date:
    """The Friday after the 4th Thursday of November: the NYSE's first early-close day."""
    nov = _cal.monthcalendar(year, 11)
    thursdays = [w[_cal.THURSDAY] for w in nov if w[_cal.THURSDAY]]
    return _date(year, 11, thursdays[3] + 1)


def is_early_close_date(d: _date) -> bool:
    """The rule of the module docstring, as a pure date test (no session list involved)."""
    if d == thanksgiving_friday(d.year):
        return True
    if d.month == 7 and d.day == 3:
        # a session iff July 4 is Tue..Fri (Sat -> observed Friday July 3, Sun -> observed Monday)
        return 2 <= _date(d.year, 7, 4).weekday() <= 4
    if d.month == 12 and d.day == 24:
        # a session iff December 25 is Tue..Fri (Sat -> observed Friday, Sun -> observed Monday)
        return 2 <= _date(d.year, 12, 25).weekday() <= 4
    return False


def early_close_dates(year: int) -> list:
    """The rule's dates of one year: at most three, in calendar order."""
    out = [thanksgiving_friday(year)]
    if 2 <= _date(year, 7, 4).weekday() <= 4:
        out.append(_date(year, 7, 3))
    if 2 <= _date(year, 12, 25).weekday() <= 4:
        out.append(_date(year, 12, 24))
    return sorted(out)


def flags(days) -> np.ndarray:
    """`(T,)` bool: is each session of the window an early close?

    `days` is the window's own session list (ISO strings, as the engine carries them), so a date
    the window does not trade is silently not flagged. Cheap enough to call per window: the dates
    are memoised per year.
    """
    cache = {}
    out = np.zeros(len(days), dtype=np.bool_)
    for k, d in enumerate(days):
        y = int(d[:4])
        if y not in cache:
            cache[y] = {x.isoformat() for x in early_close_dates(y)}
        out[k] = d in cache[y]
    return out
