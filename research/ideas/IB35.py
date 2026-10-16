"""IB35 -- conditional vol targeting: adjust exposure only in the volatility extremes.
Idea bank `idea_bank_v1`.

    A  re-size only when vol20 is outside its trailing 252-session [P25, P75]; otherwise hold the
       weight
    B  the same with [P10, P90]
    C  the plain fixed 3 pp band (control; between the tested 2.5 and 5 pp)   -> NEEDS_KERNEL

**A/B as implemented.**  The target the engine consumes is production's
`on/factor * min(1, 0.35/vol20)`; the arm makes the *vol multiplier* sticky: it takes the current
`min(1, 0.35/vol20)` only on sessions whose vol20 lies outside the trailing 252-session percentile
band, and carries the previous session's multiplier on every other session ("otherwise hold the
weight").  The vote and the early path are untouched, so a state change still fills -- what the arm
suppresses is the *vol-driven re-sizing* in the middle of the vol distribution, which is the
Bongaerts-Kang-van Dijk claim.  `vol20` is the panel's `data.vol` (production's sizing input); its
trailing percentiles are computed with `pandas.rolling(...).quantile` (an O(T) skiplist, and a
`(P, T, 252)` numpy window is not affordable on a 256-path chunk).  Before 252 sessions of history
the percentiles are undefined, so the multiplier updates every session: the warm-up is production's
and the arm is `Nan`-free.  The 0.98 total cap is production's, applied after the multiplication.
The K execution layer is unchanged (it is a *target* rule, as the bank's plugin model requires).

**Arm C is not expressible and declares `needs_kernel`.**  A "plain fixed 3 pp band" changes the
engine's resize band width `fe_sim.C0_BAND` (`fe_config.BAND = 0.5 pp`, a compiled constant of the
kernel); `rules.register_rule(name, band, usd_rule, defer)` re-combines the engine's three
*booleans* (A1u's gain-scaled band / the USD gain fraction / H2's December deferral) and cannot set
a width, so the 2.5/5/10 pp family it belongs to is only reachable with a kernel copy.  Its spec is
still registered (a frozen trial) and never simulated.

Applicable on every window (its own close only).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "p_lo": 0.25, "p_hi": 0.75,
          "window": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "p_lo": 0.10, "p_hi": 0.90,
          "window": 252},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "band": 0.03,
          "needs_kernel": "the plain fixed 3 pp resize band changes fe_sim.C0_BAND (a compiled "
                          "engine constant); register_rule only re-combines the band/usd/defer "
                          "booleans and cannot set a width"},
}


def trailing_quantile(a: np.ndarray, q: float, window: int) -> np.ndarray:
    """Trailing `window`-session `q`-quantile per path, `NaN` until the window is full."""
    df = pd.DataFrame(np.asarray(a, dtype=np.float64).T)
    return df.rolling(int(window), min_periods=int(window)).quantile(q).to_numpy().T


def sticky_vt(vol, p_lo, p_hi, window):
    """`min(1, 0.35/vol)`, updated only when vol is outside its trailing `[p_lo, p_hi]` band."""
    fresh = fe_signals.vt_of(vol)
    lo = trailing_quantile(vol, p_lo, window)
    hi = trailing_quantile(vol, p_hi, window)
    known = np.isfinite(lo) & np.isfinite(hi)
    update = ((vol < lo) | (vol > hi)) | (~known)
    idx = np.maximum.accumulate(np.where(update, np.arange(vol.shape[1]), 0), axis=1)
    return np.take_along_axis(fresh, idx, axis=1)


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    vt = sticky_vt(data.vol, float(spec["p_lo"]), float(spec["p_hi"]), int(spec["window"]))
    raw = on_eff * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)  # fe_signals.sized_wants
    return raw * fcap
