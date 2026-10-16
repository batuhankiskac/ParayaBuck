"""IC08 -- HAR volatility blend instead of vol20, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  vt = min(1, 0.35/sigma_har),  sigma_har  = 0.40*vol5 + 0.35*vol20 + 0.25*vol60
    B  vt = min(1, 0.35/sigma_har2), sigma_har2 = 0.50*vol20 + 0.30*vol60 + 0.20*vol252
    C  vt = min(1, 0.35/max(vol20, sigma_har))

Readings fixed here:

* Every `voln` is `fe_signals.vol_n(data.close, n)`: the annualised sample vol of the n daily
  returns of the **traded leg** ending at t (the spec's "all annualised, of the traded leg"; n = 20
  is exactly production's sizing input `data.vol`). Only the sizing input changes: the E2 vote,
  the early re-entry state machine (its own vol20-vs-median test runs on the price series, as in
  production), the half weight, the 0.98 cap and the K execution layer are production's.
* **Warm-up**: where a blend is undefined because a longer component (vol60 / vol252) has fewer
  than n returns yet, the arm sizes with production's vol20 (and with production's own convention,
  vt = 1, where vol20 is undefined too). On the main window every component is defined from the
  first measured session (the panel starts 253 sessions earlier); the fallback only touches the
  first sessions of a path/window, where the vote is rarely on.
* `max(vol20, sigma_har)` ignores an undefined argument (`np.fmax`).
* **Availability**: closes only -- every window is applicable (no `NotApplicable`).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": {"5": 0.40, "20": 0.35, "60": 0.25},
          "max_with_vol20": False},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": {"20": 0.50, "60": 0.30, "252": 0.20},
          "max_with_vol20": False},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": {"5": 0.40, "20": 0.35, "60": 0.25},
          "max_with_vol20": True},
}


def blend_vol(close, blend):
    """`sum_n w_n * vol_n(close, n)`, NaN where any component is undefined."""
    out = 0.0
    for n, w in sorted(blend.items(), key=lambda kv: int(kv[0])):
        out = out + float(w) * fe_signals.vol_n(close, int(n))
    return out


def sizing_vol(data, spec):
    vol20 = data.vol
    sig = blend_vol(data.close, spec["blend"])
    if spec["max_with_vol20"]:
        sig = np.fmax(vol20, sig)
    return np.where(np.isfinite(sig), sig, vol20)


def targets(data, spec):
    """The production target series, sized on the HAR blend."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, sizing_vol(data, spec))
