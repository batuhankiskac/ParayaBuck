"""ib17 -- FOMC cycle (five-week meeting cycle) exposure tilt (idea_bank_v1, arm A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** `target *= 1.00` when `w` is even and `0.85` when `w` is odd
* **B** the same cycle definition with `1.00 / 0.70`
* **C** the mirror: `0.85` when `w` is even, `1.00` when `w` is odd

Literal reading and the frozen conventions:

* `w = floor((session date - the most recent scheduled FOMC meeting date) / 7 calendar days)`, week
  0 = the meeting week. The anchor is the *announcement date* (the second day of the two-day
  meeting). `w` is unbounded -- meeting gaps run 4 to 8 weeks -- and the arms use its parity exactly
  as the spec words it, with no wrap to a five-week cycle.
* **Data.** No Yahoo/FRED endpoint publishes scheduled meeting dates, so the calendar is
  hand-transcribed (`research/plugins_b1/make_fomc_data.py`, 8 scheduled meetings per year
  1994-2026, emergency/intermeeting moves excluded) and registered in the framework's registry as
  the two hand-made series `FOMC_DATES` (the source table) and `FOMC_WEEK` (the dense per-calendar-
  day index the plugin reads; `DATA_MANIFEST.json` records the Fed URLs, ranges and SHA-256).
  A dense index is used deliberately: inside a bootstrap chunk `data.extra` replays the *drawn
  source session's* value, so the cycle position stays aligned with the replayed returns. A `w`
  computed from the chunk's own `data.days` labels would not be.
* Sessions where the index is undefined (before the first scheduled meeting, 1994-02-04) take the
  neutral multiplier **1.00**. `NotApplicable` when the index has no value anywhere in the window
  (i.e. `M2`, ^GSPC 1935-1956). Note the deviation from the bank's `oos.evaluable: []`: `M1`,
  `M3`, `M4`, `M5` and `GSPC_1957_1999` all extend past 1994, so under the framework's per-window
  rule they *are* applicable, with the pre-1994 part neutral (the bank's note assumed the calendar
  was absent for the whole window). The bank's pre-registered "no OOS clause" reduced gate is
  therefore not needed; the standard G7/G8 apply to those windows.
* The tilt is a multiplier on the whole production target (E2 and the early path) -- no new trade
  type; execution stays production K, `leg = 2x`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "even": 1.00, "odd": 0.85},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "even": 1.00, "odd": 0.70},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "even": 0.85, "odd": 1.00},
}


def _tilt(week, even, odd):
    """The cycle multiplier at every session; undefined index -> neutral 1.00."""
    w = np.asarray(week, dtype=np.float64)
    known = np.isfinite(w)
    ev = known & (np.remainder(np.where(known, w, 0.0), 2.0) == 0.0)
    return np.where(known, np.where(ev, float(even), float(odd)), 1.0)


def targets(data, spec):
    w = data.extra("fomc_week")
    if not np.isfinite(w).any():
        raise NotApplicable("the FOMC cycle index starts 1994-02-04 and has no value in this "
                            "window")
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * _tilt(w, spec["even"], spec["odd"])
