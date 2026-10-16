"""IB18 -- options-expiration week tilt.  Idea bank `idea_bank_v1`.

    A  target *= 0.75 during the Mon-Fri week containing the third Friday of every month
    B  target *= 0.75 only in the quarterly (Mar/Jun/Sep/Dec) expiration weeks
    C  target *= 0.85 outside expiration weeks (the mirror sign)

Calendar only, no extra data.  The third Friday is a calendar fact, computed from the panel's own
session dates: a session belongs to the expiration week iff the Friday of its Monday-Friday week
falls in the same calendar month and on day-of-month 15..21 -- the Fridays in 15..21 are exactly
the month's third Fridays, and the same-month test drops the straddling weeks (e.g. a Monday 31
July whose Friday is 4 August).

**Which calendar.**  On a real window the panel's `days` are the sessions.  Inside a bootstrap
chunk the panel's *market* columns are drawn source sessions in drawn order, so the arm reads the
drawn sessions' own dates through `extra_days`/`extra_index` -- the same calendar convention the
framework's extra series use ("the chunk replays the extra series together with its returns"), and
the only one that makes the expiry effect mean what it means.  (`data.days` is not usable there:
on the clean bootstrap it is the chunk's synthesised 2015-2026 label calendar and one column
shorter than the panel, and on the OOS bootstrap it is the whole market's session list; this is
reported as a framework inconsistency, see `research/plugins_b2/NOTES.md`.)

Applied as the bank says: the *target* is multiplied, after the production E2 + early-re-entry
state machine and the 0.98 cap.  The K execution layer is unchanged, so a tilt the drift band does
not let through simply does not fill.

Applicable on every window (calendar only).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "tilt": 0.75, "quarterly_only": False,
          "mirror": False},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "tilt": 0.75, "quarterly_only": True,
          "mirror": False},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "tilt": 0.85, "quarterly_only": False,
          "mirror": True},
}

#: the quarterly expiration months (Mar/Jun/Sep/Dec)
QUARTERLY = (3, 6, 9, 12)


def session_calendar(data) -> np.ndarray:
    """`(P, T)` session labels, one per panel column (see the module docstring)."""
    if data.extra_index is not None:  # a bootstrap chunk: the drawn source sessions
        return np.asarray(data.extra_days)[np.asarray(data.extra_index)]
    d = np.asarray(data.days, dtype="<U10")
    if d.size == data.T - 1:  # defensive: a chunk column 0 has no label of its own
        d = np.concatenate([d[:1], d])
    if d.size != data.T:
        raise ValueError(f"calendar length {d.size} != panel width {data.T}")
    return np.tile(d.reshape(1, -1), (data.P, 1))


def expiration_week(days: np.ndarray) -> np.ndarray:
    """(P, T) bool: the session's Mon-Fri week contains the month's third Friday."""
    d = np.asarray(days, dtype="datetime64[D]")
    dow = (d.astype(np.int64) + 3) % 7  # 1970-01-01 was a Thursday -> Monday = 0
    fri = (d + (4 - dow).astype("timedelta64[D]")).astype("datetime64[D]")
    dom = (fri - fri.astype("datetime64[M]")).astype(np.int64) + 1
    same_month = fri.astype("datetime64[M]") == d.astype("datetime64[M]")
    return same_month & (dom >= 15) & (dom <= 21)


def targets(data, spec):
    d = session_calendar(data)
    w = expiration_week(d)
    if spec["quarterly_only"]:
        mon = d.astype("datetime64[M]").astype(np.int64) % 12 + 1
        w = w & np.isin(mon, QUARTERLY)
    if spec["mirror"]:
        w = ~w
    ee = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, ee, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * np.where(w, float(spec["tilt"]), 1.0)
