"""vol_shape_p15 -- vol_shape_v1's I2 "exposure-shape" arm at p = 1.5, as a batch_screen idea.

`research/vol_shape_v1` (tag `archive/research/vol-shape-v1-2026-10`) replaced production's
vol-target sizing `min(1, 0.35/vol20)` with the family `min(1, c_p / vol20**p)` and calibrated
`c_p` once, before looking at the main window, on the pre-2000 ^NDX window (1985-10-01 →
2000-03-08, base financing, zero dividends) so that `mean(vt)` matches K's. For p = 1.5 that is

    p = 1.5,  c = 0.24593188954326456   (research/vol_shape_v1/PRE_C, frozen by its
                                         PREREGISTRATION.md; mean(vt) = 0.8396819604 = K's)

Published anchors (`research/vol_shape_v1/RESULTS.json`, m = 2, K's rule, production E2 + early
re-entry state machine, only the sizing changed):

    pre-tax CAGR          base 16.5333 %   calibrated 15.9416 %   m = 1 base 16.7883 %
    pre-tax max DD        base 39.8756 %   calibrated 39.9865 %
    turnover / year       5.484678
    after tax, $100k     11.2438 / 15.6758 / 24.1747 %  (Δ vs K +0.4905 / +0.4404 / +0.2027 pp)
    B1-recent bootstrap  median Δ vs K +0.2507 pp, share > 0 63.5 %
    B2-flat bootstrap    median Δ vs K +0.3311 pp, share > 0 68.3 %

Its report rejected the arm on the strict gate set (G8: the pooled OOS bootstrap), and the
framework's stage 2 must reproduce that verdict.

Fixed parameters: p and c exactly as frozen above (`c` at full float64 precision: the value in
`vol_shape_v1.shapes`/`screen.PRE_C`, not the 6-dp copy in the SUMMARY tables).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "p15": {"rule": "K", "leg": "2x", "cash_yield": "none", "p": 1.5, "c": 0.24593188954326456},
}


def targets(data, spec):
    """`vol_shape_v1.shapes.apply_shape`: `min(1, c / vol**p)` with production's <= 0 / NaN
    convention, on the same E2 + early-re-entry state machine. `(1.0, 0.35)` is bit-identical to
    `fe_signals.sized_wants` (vol_shape_v1's own `target_identity_main` check)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    vol = data.vol
    pos = vol > 0
    vt = np.where(pos, np.minimum(1.0, spec["c"] / np.where(pos, vol, 1.0) ** spec["p"]), 1.0)
    raw = on_eff * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap
