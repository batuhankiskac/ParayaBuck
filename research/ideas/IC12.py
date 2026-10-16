"""IC12 -- financial-conditions (NFCI) veto.  Idea bank `idea_bank_v2`, arms frozen by the spec.

    A  weight = 0     while NFCI(1-week lag) > its trailing 156-week median AND NFCI_t > NFCI_{t-4w}
    B  weight *= 0.5  in the same state
    C  3-of-4 vote {SMA200, TSM252, DON126, NFCI <= its trailing 156-week median}, on iff >= 3

**Readings fixed here** (the bank's wording made exact; no parameter is chosen by the plugin):

* **The series** is FRED `NFCI` (registered key `nfci`, weekly, 1971-01-08 ->, file
  `data/historical/plugins_c2_nfci.csv`).  FRED dates an observation by the Friday that ends its
  week; it is published the following Wednesday.  "NFCI(1-week lag)" is the observation **one week
  older than the newest one dated at or before the session** (`native.lag(x, 1)`): on the Monday
  after Friday F the arm uses F-7 (published F-2), and the Wednesday release of F is first used the
  session after the next Friday -- causal on every session, never a look-ahead.
* **Every term is on the lagged series** `x` (one value per week): `stress_w = x_w > median(x_{w-155..w})
  AND x_w > x_{w-4}` -- the "NFCI_t" of the momentum clause is the same lagged value the level clause
  uses (the most literal reading that keeps the rule causal), the median includes the current week
  and needs 156 finite weeks, "4 weeks" is 4 observations.  The weekly state is read **as of** each
  session's date (`research/plugins_c2/native.py`, which explains why the state is computed on the
  series' own calendar and replayed by date inside a bootstrap chunk).
* **Arm C's fourth vote** is `x_w <= median156(x)` on the same lagged series.
* **Undefined -> production**: where the state is undefined (before the first 156 lagged weeks,
  i.e. before 1974-01, which only touches `GSPC_1957_1999`'s first years), A/B leave the weight
  unchanged and C falls back to the production 2-of-3 vote (`plugins_b3.helpers.vote_3_of_4`).
* A/B multiply the whole production target (E2 and the early leg); C replaces the vote that feeds
  the unchanged early re-entry machine; sizing, cap and the execution layer (`rule` = K) are
  production's.

**Applicability**: `NotApplicable` where the state is undefined on every session of the panel --
`M2` (^GSPC 1935-1956), exactly the bank's pre-registered drop (reduced 5-window G7).  The main
window, `GSPC_1957_1999` (live from 1974), `M1`, `M3`-`M5`, the 21 ETFs and both bootstrap families
are applicable.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers
from research.plugins_c2 import native

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.0,
          "lag_weeks": 1, "median_weeks": 156, "mom_weeks": 4},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.5,
          "lag_weeks": 1, "median_weeks": 156, "mom_weeks": 4},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote3of4",
          "lag_weeks": 1, "median_weeks": 156},
}


def weekly_states(dates, nfci, spec):
    """`(stress, calm)` per weekly observation, as 1.0 / 0.0 / NaN (undefined)."""
    x = native.lag(nfci, int(spec["lag_weeks"]))
    med = native.trailing_median(x, int(spec["median_weeks"]))
    calm = native.as_float(x <= med, np.isfinite(x) & np.isfinite(med))
    stress = None
    if "mom_weeks" in spec:
        prev = native.lag(x, int(spec["mom_weeks"]))
        ok = np.isfinite(x) & np.isfinite(med) & np.isfinite(prev)
        stress = native.as_float((x > med) & (x > prev), ok)
    return stress, calm


def targets(data, spec):
    dates, nfci = native.observations(data, "nfci")
    stress_w, calm_w = weekly_states(dates, nfci, spec)
    labels = native.session_labels(data)
    state_w = stress_w if spec["mode"] == "veto" else calm_w
    state = native.asof(dates, state_w, labels)
    if not np.isfinite(state).any():
        raise NotApplicable("the NFCI state (156 lagged weeks from 1971-01-08) has no value in "
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
