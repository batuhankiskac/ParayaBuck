"""IC23 -- day-of-week (Monday) exposure tilt, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  target *= 0.75 on Mondays (production target otherwise)
    B  target *= 0.90 on Mondays
    C  mirror: target *= 0.75 on Fridays

Readings fixed here:

* **"On Mondays" = the position held during the Monday session.** The mechanism says the arm
  "holds 0.75x the production target on Mondays only", and the engine decides at the close of
  session t and fills at the open of t+1. So the scaled target is the one decided at the close of
  the session *before* a Monday (filled at Monday's open, held through Monday's session), and the
  target decided at Monday's close (filled Tuesday) is production's again. Scaling the target
  dated Monday instead would hold the cut on Tuesday, which is not the effect the idea names.
  The mirror arm C reads Friday the same way.
* **"Monday"** is the calendar weekday of the session (a Tuesday after a Monday holiday is not a
  Monday). The weekday is that of the session whose return the column replays: the panel's own
  session on a real window, the drawn source session (`extra_days[extra_index]`) inside a
  bootstrap chunk -- the weekday effect lives in the drawn session's return, not in the chunk's
  calendar labels. The last column has no next session and is unchanged.
* Only the target series changes; the K execution layer (bands, gain fraction, December deferral)
  decides whether the cut and the restore actually fill.
* **Availability**: dates only -- every window is applicable (no `NotApplicable`).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

#: weekday numbers, Monday = 0 (numpy day 0 = 1970-01-01, a Thursday -> (day + 3) % 7)
WEEKDAY = {"Monday": 0, "Friday": 4}

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "weekday": "Monday", "factor": 0.75},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "weekday": "Monday", "factor": 0.90},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "weekday": "Friday", "factor": 0.75},
}


def source_days(data):
    """`(P, T)` ISO day of the session each panel column replays."""
    if data.extra_index is not None:
        return np.asarray(data.extra_days, dtype="<U10")[np.asarray(data.extra_index)]
    d = np.asarray(data.days, dtype="<U10")
    return np.broadcast_to(d.reshape(1, -1), (data.P, data.T))


def weekday(days):
    return (days.astype("datetime64[D]").astype(np.int64) + 3) % 7


def held_on_weekday(data, wd):
    """`(P, T)` bool: the target decided at column t is held during a session of weekday `wd`."""
    wdays = weekday(source_days(data))
    out = np.zeros((data.P, data.T), dtype=bool)
    out[:, :-1] = wdays[:, 1:] == wd
    return out


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    hit = held_on_weekday(data, WEEKDAY[spec["weekday"]])
    return wants * np.where(hit, float(spec["factor"]), 1.0)
