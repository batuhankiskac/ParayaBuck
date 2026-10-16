"""IC17 -- high-yield credit-spread (OAS) veto.  Idea bank `idea_bank_v2`, arms frozen by the spec.

    A  weight = 0     while HYOAS_t > 1.20 * median(HYOAS, 252 sessions) AND HYOAS_t > HYOAS_{t-20}
    B  weight *= 0.5  in the same state
    C  3-of-4 vote {SMA200, TSM252, DON126, HYOAS <= 1.20x its 252-session median}, on iff >= 3

**The data precondition does not hold -- read this first.**  The bank assumes FRED
`BAMLH0A0HYM2` from 1996-12-31 ("main_window: full").  FRED's CSV endpoint now serves only the
**trailing three years** of every ICE BofA series (the registered file,
`data/historical/plugins_c2_hyoas.csv`, starts 2023-10-16; `cosd=1996-12-31` is ignored, ALFRED
vintages return nothing, and no Yahoo symbol carries the OAS -- see
`research/plugins_c2/build_extra.py`).  With a 252-observation median the state would exist only
from late 2024, i.e. on ~7 % of the main window, so the arm cannot be what the bank registered.
The plugin therefore raises `NotApplicable` on **every** window while the registered series starts
after `REQUIRED_START` (one month after the bank's 1996-12-31), and the runner records the three
arms as `not_applicable` on the main window (registered, counted in N, never screened).  A
substitute series (BAA10Y, JNK/HYG prices) would be a different idea and is not approximated.

The rule itself is implemented as specified so that it runs unchanged if a full-history file is
ever registered under the key `hyoas`:

* computed on the series' own observations (one per FRED business day; "252 sessions" and "20
  sessions" are 252 and 20 observations; the median includes the current one and needs 252 finite
  values) and read as of each session's date (`research/plugins_c2/native.py`); no publication lag
  is applied (the bank specifies none; FRED posts the OAS the next morning -- a caveat, not a
  parameter);
* undefined state -> production behaviour (A/B unchanged weight, C the production 2-of-3 vote);
* the bank's OOS set (`GSPC_1957_1999`, `M1`, `M3`-`M5`; `M2` dropped) would follow from the same
  "no defined state in the panel -> NotApplicable" rule.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers
from research.plugins_c2 import native

#: the bank's precondition: the series exists from 1996-12-31 (one month of tolerance)
REQUIRED_START = "1997-01-31"

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.0,
          "ratio": 1.20, "median_n": 252, "mom_n": 20},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "mult": 0.5,
          "ratio": 1.20, "median_n": 252, "mom_n": 20},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote3of4",
          "ratio": 1.20, "median_n": 252},
}


def daily_states(oas, spec):
    """`(stress, calm)` per observation, as 1.0 / 0.0 / NaN (undefined)."""
    med = native.trailing_median(oas, int(spec["median_n"]))
    lim = float(spec["ratio"]) * med
    calm = native.as_float(oas <= lim, np.isfinite(oas) & np.isfinite(med))
    stress = None
    if "mom_n" in spec:
        prev = native.lag(oas, int(spec["mom_n"]))
        ok = np.isfinite(oas) & np.isfinite(med) & np.isfinite(prev)
        stress = native.as_float((oas > lim) & (oas > prev), ok)
    return stress, calm


def targets(data, spec):
    dates, oas = native.observations(data, "hyoas")
    if dates.size == 0 or dates[0] > REQUIRED_START:
        first = dates[0] if dates.size else "never"
        raise NotApplicable(
            f"the registered BAMLH0A0HYM2 starts {first}, not 1996-12-31: FRED serves only the "
            "trailing three years of ICE BofA series, so the bank's full-history precondition fails")
    stress_d, calm_d = daily_states(oas, spec)
    state = native.asof(dates, stress_d if spec["mode"] == "veto" else calm_d,
                        native.session_labels(data))
    if not np.isfinite(state).any():
        raise NotApplicable("the HY OAS state has no value in this window")

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
