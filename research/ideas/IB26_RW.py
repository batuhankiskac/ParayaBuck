"""IB26_RW -- IB26 (curve-conditioned off-state IEF leg) on the framework's reduced windows.

The bank's IB26 arms (`docs/research/idea_bank_v1.json`; parameters frozen, nothing tuned) are
registered `needs_kernel` in `research/ideas/IB26.py` (written before `fast_engine2` existed) and
re-expressed in `research/ideas/IB26_2L.py`, where they are recorded `not_applicable` because IEF
(2002-07-30) does not reach the framework's fixed main window. Both files are untouched. This
module adds the same three arms again with **new arm names** (`_RW`) and the declaration that makes
them evaluable: `start = "auto"`, the window derived from the arm's own inputs.

    A_RW  while off: hold IEF at 0.98 if T10Y3M > 1.0 pp, else cash
    B_RW  as A plus a requirement that the 60-session DGS10 change > -0.50 pp
    C_RW  the mirror: hold IEF while off only if T10Y3M <= 1.0 pp

Readings (fixed before any run):

* **Off.** "While the vote is off" is the production path state, off = `on_eff == False` (E2's
  2-of-3 vote *and* the early re-entry inactive) -- the reading `ib25_2L.py` and
  `research/two_leg_arms_v1/PREREGISTRATION.md` fix, and one the arm needs: the production target of
  leg A (the synthetic 2x QLD*) is exactly zero while off, so the 0.98 IEF target never overlaps a
  QLD* position and `wants + wants_b <= 0.98`.
* **Units.** `T10Y3M` and `DGS10` are FRED *percent* levels (the registered series:
  `two_leg_arms_v1_T10Y3M.csv`, 1982-01-04 ->, and `batch_screen_v1_dgs10.csv`, 1962-01-02 ->), so
  the bank's "1.0 pp" is the level 1.0 and its "-0.50 pp over 60 sessions" is a level change of
  -0.50. Both series are read with `ffill=True` (they are absent on non-publication days) and the
  60-session lookback is the panel's own session lag.
* **Missing input -> no IEF.** A NaN comparison is False in both directions, so a window (or a
  warm-up session) without a curve prints cash rather than a guess -- the neutral convention
  `research/plugins_b1/README.md` records for the whole bank batch. In practice T10Y3M covers the
  whole declared window and DGS10 covers it from 1962.
* **Reduced window.** `start = "auto"` with `leg_b = "ief"` gives session 852 (2002-07-31) ->
  2026-09-29, with K re-simulated on the same window as the paired control
  (`_control_w20020731.json`), the after-tax windows clipped up, the early January rolling starts
  dropped and both bootstraps drawn from the window's own sessions. The bank's own oracle for the
  main window is a 2003-07 start (IEF + a 252-session warm-up); the framework's `"auto"` rule is
  the pre-registered one and starts one session after IEF's first session, so the arm runs ~one
  year longer than the bank's note -- disclosed, not tuned. The six OOS windows and the 21 ETFs are
  unchanged: IEF has no price on the pre-2002 windows and an interior gap on the 1999-2026 ETF
  windows, so `leg_b_usable` refuses them (`NotApplicable`). The bank's own reduced-OOS idea (build
  a BOND10* from the monthly GS10 series for the US windows) is a *third instrument construction*
  and is not attempted here.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A_RW": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
             "inputs": ["t10y3m"], "off_leg": "IEF", "ief_weight": 0.98, "slope_min": 1.0},
    "B_RW": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
             "inputs": ["t10y3m", "dgs10"], "off_leg": "IEF", "ief_weight": 0.98, "slope_min": 1.0,
             "dgs10_change_min": -0.50, "dgs10_lookback": 60},
    "C_RW": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
             "inputs": ["t10y3m"], "off_leg": "IEF", "ief_weight": 0.98, "slope_min": 1.0,
             "mirror": True},
}

def production_state(data):
    """`(wants, on_eff)` of the production E2 + early-re-entry path on this panel."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol), on_eff

def curve_pays(data, spec):
    """`(P, T)` bool: does the curve pay for the intermediate-Treasury leg on this session?"""
    slope = data.extra("t10y3m", ffill=True)
    min_slope = float(spec["slope_min"])
    pays = (slope <= min_slope) if spec.get("mirror") else (slope > min_slope)
    lookback = spec.get("dgs10_lookback")
    if lookback:
        d = data.extra("dgs10", ffill=True)
        k = int(lookback)
        lag = np.full_like(d, np.nan)
        lag[:, k:] = d[:, :-k]
        pays = pays & ((d - lag) > float(spec["dgs10_change_min"]))
    return pays

def targets(data, spec):
    """Production leg A; leg B the 0.98 IEF sleeve on the off sessions the curve pays for."""
    wants, on_eff = production_state(data)
    off = ~on_eff
    return wants, float(spec["ief_weight"]) * (off & curve_pays(data, spec)), spec["leg_b"]
