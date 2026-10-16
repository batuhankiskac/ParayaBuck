"""IC16 -- Fed-funds tightening-cycle veto (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** `weight = 0` while `DFF_t - DFF_{t-126} >= +0.75 pp`
* **B** `weight *= 0.5` while `DFF_t - DFF_{t-126} >= +0.50 pp`
* **C** 3-of-4 vote `{SMA200, TSM252, DON126, DFF 126-session change <= +0.75 pp}`, on iff `>= 3`

Literal reading and the frozen conventions (everything the spec leaves open):

* the series is `data.extra("dff", ffill=True)` -- FRED `DFF` (daily effective federal funds rate,
  in percent), registered by `research/plugins_c1/build_extra.py`; it is aligned to the panel's own
  sessions by date (the drawn source session inside a bootstrap chunk), `ffill`ed so a
  non-publication day (weekend/holiday, where FRED has no value) carries the last published rate.
  The 126-session change is `DFF_t - DFF_{t-126}` **in percentage points** -- the series' own unit,
  so the thresholds are read as the spec writes them (0.75 / 0.50 pp).
* at most 1.9 % of the resolved values are missing (DFF starts 1954-07-01; see `smoke --keys`), all
  of them *leading* NaNs in `M2` (1935-1956) and, in a bootstrap chunk, the drawn sessions before
  1954-07.  A NaN comparison is False in numpy, and the arms are written as the *trigger* condition
  (`>= thr` for A/B, `<= thr` for the vote), so a missing change is **neutral**: no veto, no halving
  for A/B, and the fourth vote OFF for C (the literal reading of the condition as written).
* arm C replaces the E2 vote; the vote then feeds production's early-re-entry state machine and
  `sized_wants` unchanged (early path untouched).  Arms A/B keep production's vote and every state
  and scale the finished target by 0 / 0.5 -- a scale-down can never breach the 0.98 cap, so no
  re-cap is needed.  Execution stays production K.
* all six OOS windows are applicable (DFF starts 1954, before `GSPC_1957_1999` and `M1`; in `M2` the
  pre-1954 part is a leading-NaN no-op), as the bank records.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "dff", "n": 126, "thr": 0.75, "mode": "veto_zero"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "dff", "n": 126, "thr": 0.50, "mode": "halve"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "key": "dff", "n": 126, "thr": 0.75, "mode": "vote"},
}


def change(data, spec) -> np.ndarray:
    """`(P, T)` the `n`-session change of the registered rate series (NaN where undefined)."""
    rate = data.extra(str(spec["key"]), ffill=True)
    return np.atleast_2d(rate) - c1_lib.lag(rate, int(spec["n"]))


def targets(data, spec):
    """Production K's target, vetoed/halved by the policy-rate change, or the 3-of-4 vote."""
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
    hit = chg >= float(spec["thr"])
    return wants * np.where(hit, 0.0 if spec["mode"] == "veto_zero" else 0.5, 1.0)
