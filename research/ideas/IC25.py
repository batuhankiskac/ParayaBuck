"""IC25 -- pre-FOMC announcement drift overlay (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  target = min(0.98, 1.0) on the session before each scheduled announcement; production
       target otherwise
    B  target = min(0.98, 2.0 * production vt) on the pre-announcement session
    C  mirror: target = 0.5 * production vt on the announcement session itself

Literal reading and the frozen conventions:

* **Calendar.**  The scheduled FOMC announcement dates 1994-2026 already registered by plugins_b1
  (`FOMC_DATES`, extra key `fomc_dates`: hand-transcribed from the Federal Reserve's public
  calendars, 8 scheduled meetings a year, emergency/intermeeting moves excluded; source, range and
  SHA-256 in `EXTRA_DATA.json` and `research/plugins_b1/DATA_MANIFEST.json`).  No new data.
* **Sessions.**  On a market calendar, the *announcement session* of a date ``A`` is the first
  session on or after ``A`` and the *pre-announcement session* ("announcement eve") is the
  session immediately before it; both must lie within 7 calendar days of ``A`` (an announcement
  outside the calendar's span marks nothing).  On a US calendar these are exactly the announcement
  day and the trading day before it.  Foreign markets (M3/M4/M5) use their own calendar dates as
  written -- the literal reading; no time-zone shift is applied.
* **"Target on a session" = the position held through that session.**  The bank's mechanism
  holds the overlay "from the open of that session to the close of the announcement eve".  The
  engine decides at the close and fills at the next open, so the position held through session
  ``s`` is the target decided at the close of ``s - 1``: the overlay is written into the target
  of the column *before* the eve (A, B) or before the announcement session (C), and production's
  target is restored on the next column (filled at the following open -- the earliest exit the
  next-open fill allows).  The fill convention is production's (the bank: "the fill stays
  next-open as production"); execution is production K, so the restoring downsize after A/B is
  subject to K's A1u gain band and H2 December deferral exactly as any other downsize.
* **"production vt" = production's target weight** (the E2 vote / early-path factor times
  ``min(1, 0.35/vol20)`` with the 0.98 cap): arm C's mirror *reduces* exposure, so reading it as
  the bare vol multiplier would buy into the announcement while production is flat.  Arm B uses
  the same quantity.  Arm A's target is the literal 0.98 on every pre-announcement session,
  regardless of the vote and of the vol target ("the full target (0.98, regardless of the vol
  target)"): the Lucca-Moench drift is unconditional.
* **Bootstrap chunks.**  The flags are computed on the pool calendar and carried by the column's
  source session (``extra_days[extra_index[p, t]]``, `extra.aligned`'s convention), so a column
  that replays an eve session is an eve column; the overlay target is written into the column
  before it, which is the decision the strategy would take with the (pre-scheduled, publicly
  known) calendar in hand.
* **Availability.**  `NotApplicable` where the window has no pre-announcement session (M2,
  1934-1956).  GSPC_1957_1999 and M1 are live for 1994-1999 only (neutral = production before),
  as the bank pre-registers.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

#: max calendar-day distance between an announcement date and the session it marks
MAX_GAP_DAYS = 7

_CACHE = {}

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "session": "eve", "mode": "fixed",
          "level": 1.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "session": "eve", "mode": "mult",
          "level": 2.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "session": "announcement",
          "mode": "mult", "level": 0.5},
}


def _ordinals(days):
    return np.array([date.fromisoformat(str(d)).toordinal() for d in days], dtype=np.int64)


def calendar_flags(pool_days, name):
    """``(eve, announcement)`` boolean flags over a session calendar (ISO strings)."""
    pool_days = np.asarray(pool_days)
    ck = (name, pool_days.size, str(pool_days[0]), str(pool_days[-1]))
    if ck in _CACHE:
        return _CACHE[ck]
    dates, vals = _ext._column(name)
    ann = np.asarray(dates)[np.isfinite(vals) & (vals > 0)]
    po = _ordinals(pool_days)
    ao = _ordinals(ann)
    eve = np.zeros(po.size, dtype=bool)
    day = np.zeros(po.size, dtype=bool)
    j = np.searchsorted(po, ao, side="left")          # first session on or after A
    ok_a = (j < po.size)
    ok_a &= np.where(ok_a, po[np.clip(j, 0, po.size - 1)] - ao <= MAX_GAP_DAYS, False)
    day[j[ok_a]] = True
    k = j - 1
    ok_e = ok_a & (k >= 0)
    ok_e &= np.where(ok_e, ao - po[np.clip(k, 0, po.size - 1)] <= MAX_GAP_DAYS, False)
    eve[k[ok_e]] = True
    _CACHE[ck] = (eve, day)
    return eve, day


def column_flags(data, which):
    """``(P, T)`` flag of the session each column replays."""
    name = _ext.resolve_series("fomc_dates", data.extra_window or data.window)
    if data.extra_index is None:
        eve, day = calendar_flags(data.days, name)
        f = eve if which == "eve" else day
        out = np.tile(f, (data.P, 1))
    else:
        eve, day = calendar_flags(data.extra_days, name)
        f = eve if which == "eve" else day
        out = f[np.asarray(data.extra_index)]
        out[:, 0] = False                              # the chunk's artificial base row
    return out


def targets(data, spec):
    flag = column_flags(data, spec["session"])
    if not flag.any():
        raise NotApplicable("the scheduled FOMC calendar (1994-2026) marks no session in this "
                            "window")
    # the target decided at the close of column t is held through column t+1
    hold = np.zeros_like(flag)
    hold[:, :-1] = flag[:, 1:]
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    level = float(spec["level"])
    if spec["mode"] == "fixed":
        over = np.full_like(wants, min(CAP, level))
    else:
        over = np.minimum(CAP, level * wants)
    return np.where(hold, over, wants)


__all__ = ["ARMS", "calendar_flags", "column_flags", "targets"]
