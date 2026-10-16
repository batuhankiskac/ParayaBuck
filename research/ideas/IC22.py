"""IC22 -- broad trade-weighted dollar regime veto.  Idea bank `idea_bank_v2`, arms frozen by the spec.

    A  weight *= 0.5  while the 60-session dollar change > +4 % AND dollar > median252(dollar)
    B  weight = 0     while the 60-session dollar change > +6 %
    C  3-of-4 vote {SMA200, TSM252, DON126, 60-session dollar change <= +4 %}, on iff >= 3

**Readings fixed here** (the bank's wording made exact; no parameter is chosen by the plugin):

* **The series** is the already registered FRED `DTWEXBGS` (key `dtwexbgs`,
  `data/historical/batch_screen_v1_dtwexbgs.csv`, daily from 2006-01-02).  The state is computed on
  the series' own observations (one per FRED business day with a value; "60 sessions" / "252
  sessions" are 60 / 252 observations) and read as of each session's date
  (`research/plugins_c2/native.py`): `chg_i = x_i / x_{i-60} - 1`, `median252` includes the current
  observation and needs 252 finite values.  "> +4 %" is `chg > 0.04`.
* **No publication lag** is applied: the bank specifies none.  Disclosed: the Fed's H.10 release
  posts the daily values weekly, so a real-time reader sees them up to a week late; this is a
  caveat on the result, not a parameter of the arm.
* **Undefined -> production**: before 2006 (and before the first 60 / 252 observations) the state
  is absent and the weight is unchanged -- the bank's fixed convention for the main window -- and
  C falls back to the production 2-of-3 vote (`plugins_b3.helpers.vote_3_of_4`).
* A/B multiply the whole production target (E2 and the early leg); C replaces the vote that feeds
  the unchanged early re-entry machine; sizing, cap and the execution layer (`rule` = K) are
  production's.

**Applicability**: `NotApplicable` where the state is undefined on every session of the panel --
`GSPC_1957_1999`, `M1` (both end 1999) and `M2` (1935-1956); `M3`-`M5` (live from 2006), the main
window, the 21 ETFs and both bootstrap families are applicable -- the bank's pre-registered
3-window OOS set (reduced 3-window G7).
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers
from research.plugins_c2 import native

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.5,
          "chg_n": 60, "chg_max": 0.04, "median_n": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.0,
          "chg_n": 60, "chg_max": 0.06},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote3of4",
          "chg_n": 60, "chg_max": 0.04},
}


def dollar_states(x, spec):
    """`(stress, calm)` per observation, as 1.0 / 0.0 / NaN (undefined).

    `stress` = change > chg_max (AND level > median_n-median when the arm has `median_n`);
    `calm` = change <= chg_max (arm C's fourth vote).
    """
    prev = native.lag(x, int(spec["chg_n"]))
    chg = x / prev - 1.0
    ok = np.isfinite(chg)
    hot = chg > float(spec["chg_max"])
    if "median_n" in spec:
        med = native.trailing_median(x, int(spec["median_n"]))
        ok = ok & np.isfinite(med)
        hot = hot & (x > med)
    stress = native.as_float(hot, ok)
    calm = native.as_float(chg <= float(spec["chg_max"]), np.isfinite(chg))
    return stress, calm


def targets(data, spec):
    dates, x = native.observations(data, "dtwexbgs")
    stress_d, calm_d = dollar_states(x, spec)
    state = native.asof(dates, stress_d if spec["mode"] == "veto" else calm_d,
                        native.session_labels(data))
    if not np.isfinite(state).any():
        raise NotApplicable("FRED DTWEXBGS starts 2006-01-02: the dollar state has no value in "
                            "this window")

    price = data.price
    comps = fe_signals.early_components(price)
    if spec["mode"] == "vote3of4":
        s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
        t = fe_signals.tsm_up_price(price).astype(np.int64)
        d = fe_signals.don_up(price, DON_N).astype(np.int64)
        ok = np.isfinite(state)
        vote = helpers.vote_3_of_4(s, t, d, np.where(ok, state, 0.0) > 0.5, ok)
        on_eff, factor = fe_signals.r2_state(price, vote, comps)
        return helpers.capped(on_eff, factor, data.vol)
    on_eff, factor = fe_signals.r2_state(price, fe_signals.e2_up(price), comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    stressed = np.isfinite(state) & (np.nan_to_num(state) > 0.5)
    return np.where(stressed, float(spec["mult"]) * wants, wants)
