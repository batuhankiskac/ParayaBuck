"""IC21 -- breakeven-inflation momentum veto (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** `weight *= 0.5` while `T10YIE_t - T10YIE_{t-126} > +0.40 pp`
* **B** `weight = 0` while `T10YIE_t - T10YIE_{t-126} > +0.70 pp`
* **C** 3-of-4 vote `{SMA200, TSM252, DON126, breakeven 126-session change <= +0.40 pp}`, on iff `>= 3`

Literal reading and the frozen conventions (everything the spec leaves open):

* the series is `data.extra("t10yie", ffill=True)` -- the FRED 10-year breakeven inflation rate, in
  percent, registered by `batch_screen_v1` (`data/historical/batch_screen_v1_t10yie.csv`,
  2003-01-02 ->), aligned to the panel's sessions by date and `ffill`ed.  The 126-session change is
  in the series' own unit (percentage points), so the thresholds are read as written.
* the comparison operators are the spec's: `>` for arms A/B (halve / zero) and `<=` for arm C's
  fourth vote.  A NaN change is therefore neutral for A/B (no halving, no zeroing -- the trigger
  `> thr` is False) and votes OFF for C (`NaN <= thr` is False).  That matters before 2003: those
  sessions are a leading NaN block in every window, and the arms degenerate to production there.
* **`NotApplicable` outside the bank's declared evaluable set**: the bank's `data.oos.evaluable` is
  `{M3, M4, M5}` ("2003-01 start -> only M3/M4/M5 have live values; reduced 3-window G7").  The test
  implemented here is the mechanical one -- a window with **no** finite `T10YIE` value raises
  `NotApplicable` -- which resolves to exactly that set: `GSPC_1957_1999`, `M1` (both end 1999) and
  `M2` (1933-1956) contain no value at all, while `M3`/`M4`/`M5` and the main window do.  The three
  live OOS windows are `k = 3`, the framework's minimum, so G7 runs on `{M3, M4, M5}` with
  `n_c >= k - 1 = 2`.
* arm C replaces the E2 vote (feeding production's early state machine and sizing unchanged); arms
  A/B keep production's vote and every state and scale the finished target by 0.5 / 0 -- a
  scale-down cannot breach the 0.98 cap, so no re-cap is needed.  Execution stays production K.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "t10yie", "n": 126, "thr": 0.40, "mode": "halve"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "t10yie", "n": 126, "thr": 0.70, "mode": "veto_zero"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "t10yie", "n": 126, "thr": 0.40, "mode": "vote"},
}


def change(data, spec) -> np.ndarray:
    """`(P, T)` the `n`-session change of the breakeven series (NaN where undefined)."""
    rate = data.extra(str(spec["key"]), ffill=True)
    if not np.isfinite(rate).any():
        raise NotApplicable(f"the {spec['key']!r} series (2003-01-02 ->) has no value in this "
                            "window")
    return np.atleast_2d(rate) - c1_lib.lag(rate, int(spec["n"]))


def targets(data, spec):
    """Production K's target, throttled by breakeven momentum, or the 3-of-4 vote."""
    chg = change(data, spec)
    if spec["mode"] == "vote":
        vote = c1_lib.vote_3_of_4(data.price, chg <= float(spec["thr"]))
        comps = fe_signals.early_components(data.price)
        on_eff, factor = fe_signals.r2_state(data.price, vote, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)

    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    hit = chg > float(spec["thr"])
    return wants * np.where(hit, 0.0 if spec["mode"] == "veto_zero" else 0.5, 1.0)
