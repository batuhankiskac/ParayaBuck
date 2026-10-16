"""IB26_2L -- IB26 (curve-conditioned off-state IEF leg), re-expressed on the two-leg kernel.

The bank's IB26 arms (`docs/research/idea_bank_v1.json`; parameters frozen, nothing tuned) were
registered as `IB26/A`,`IB26/B`,`IB26/C` with `needs_kernel` before `research/fast_engine2`
existed. Those arms stay in `research/ideas/IB26.py` untouched; this module adds the same three
arms with the two-leg kernel's declaration (`leg_b = "ief"`, the registered Yahoo IEF adjclose
series) and the suffix `_2L`:

    A_2L  while off: hold IEF at 0.98 if T10Y3M > 1.0 pp, else cash
    B_2L  as A_2L plus a requirement that the 60-session DGS10 change > -0.50 pp
    C_2L  the mirror: hold IEF while off only if T10Y3M <= 1.0 pp

Readings fixed before the run (see `research/two_leg_arms_v1/PREREGISTRATION.md`):

* "while off" is the production path state `on_eff == False` (E2's 2-of-3 vote and the early
  re-entry both inactive); leg A is then production's own target, which is exactly zero, and the
  rest of the window is production's QLD* weight.
* the curve series is FRED `T10Y3M` in percentage points, registered through the framework's
  extra-data mechanism by `research/two_leg_arms_v1/register_t10y3m.py` (the framework's
  `--fetch-fred` only accepts the ids in its module-level `FRED` tuple). `slope_min = 0.010` is in
  `slope_series` units / 100, i.e. the 1.0 pp the bank fixes; the comparison is
  `T10Y3M/100 > 0.010`. Both series are used with `ffill=True` (FRED has no value on
  non-publication sessions), and a window in which the series has no value at all raises
  `NotApplicable` (the framework's convention for a missing input).
* arm B's `dgs10_change_min = -0.005` is `DGS10/100` today minus `DGS10/100` `dgs10_lookback = 60`
  sessions earlier, required to be `> -0.005`; before the lookback is full the condition is False.
* **Availability.** IEF (Yahoo, adjusted close) starts 2002-07-30, so all three arms are
  `NotApplicable` on the framework's fixed main window (2000-03-09 ->): `leg_b_usable` refuses a
  leg whose series does not reach the window. The bank's own oracle for IB26 is a reduced main
  window starting 2003-07 (its `data.main_window` field), which the framework cannot express
  (stage 1 hardcodes the main window). The arms are registered and screened anyway, so the outcome
  is recorded rather than assumed; the pre-registered reduced gate is in the study's
  PREREGISTRATION.md.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

#: the curve series (FRED T10Y3M, percentage points), registered by
#: `research/two_leg_arms_v1/register_t10y3m.py`; IB26's arm B spec names it only through arm A.
SLOPE_KEY = "t10y3m"
SLOPE_NAME = "T10Y3M"

ARMS = {
    "A_2L": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "off_leg": "IEF",
             "ief_weight": 0.98, "slope_min": 0.010, "slope_series": "T10Y3M"},
    "B_2L": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "off_leg": "IEF",
             "ief_weight": 0.98, "slope_min": 0.010, "dgs10_change_min": -0.005,
             "dgs10_lookback": 60},
    "C_2L": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "off_leg": "IEF",
             "ief_weight": 0.98, "slope_min": 0.010, "mirror": True},
}

def _lag(a, n):
    """`(P, T)` `a` shifted back `n` sessions (NaN before the window is full)."""
    out = np.full_like(a, np.nan)
    out[:, n:] = a[:, :-n]
    return out

def curve_condition(data, spec):
    """`(P, T)` bool: the curve pays for the off-state IEF leg on this panel."""
    slope = data.extra(SLOPE_KEY, ffill=True) / 100.0
    if not np.isfinite(slope).any():
        raise NotApplicable(f"the {SLOPE_NAME} series has no value in this window")
    lo = float(spec["slope_min"])
    ok = slope <= lo if spec.get("mirror") else slope > lo
    ok = ok & np.isfinite(slope)
    if spec.get("dgs10_change_min") is not None:
        d = data.extra("dgs10", ffill=True) / 100.0
        if not np.isfinite(d).any():
            raise NotApplicable("the DGS10 series has no value in this window")
        chg = d - _lag(d, int(spec["dgs10_lookback"]))
        ok = ok & np.isfinite(chg) & (chg > float(spec["dgs10_change_min"]))
    return ok

def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    sleeve = float(spec["ief_weight"]) * (~on_eff) * curve_condition(data, spec)
    return wants, sleeve, spec["leg_b"]

__all__ = ["ARMS", "curve_condition", "targets"]
