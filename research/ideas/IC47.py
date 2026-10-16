"""IC47 -- recovery-ramp sizing of the early position (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** while in the early state, early weight = production early weight x `clip(recovered, 0.5, 1.0)`
* **B** the same with `clip(recovered, 0.25, 1.0)`
* **C** early weight = `0.5 x production target x (1 + recovered)`, capped at the full production
  target

with `recovered = (close - trough126) / (max252 - trough126)` (0 at the trough, 1 at the old high).

Literal reading and the frozen conventions (everything the spec leaves open):

* `close` is `data.price` -- the series the production vote and the early-path conditions run on
  (QQQ's price close on the main window, the index/ETF close elsewhere).  `trough126` is the
  trailing 126-session minimum and `max252` the trailing 252-session maximum, both **including**
  the session; then `close <= max252` and `close >= trough126`, so `recovered` is in `[0, 1]`.
  Where `max252 == trough126` (a flat 252-session window, the ratio is 0/0) `recovered := 1`; before
  252 sessions it is undefined and the early weight is production's (`recovered := 1`).  The
  measured window starts at index 253, so no measured session is affected by either convention.
* the **early state** is production's (`fe_signals.r2_state`: E2 off, the early path active, factor
  0.5); its entry/exit conditions are untouched -- only the size of the early position changes.
  "Production early weight" is `0.5 x vt` and "the full production target" is the E2-on weight
  `vt = min(1, 0.35/vol20)`, so the arms are a change of the early `factor`:
  A/B `0.5 x clip(recovered, lo, 1)`, C `min(1, 0.5 x (1 + recovered))` (C's cap never binds since
  `recovered <= 1`; it is applied literally).  `fe_signals.sized_wants` then applies the vol target
  and the 0.98 total cap unchanged.  Sessions with E2 on, or with no position, are production's.
* execution stays production K (`rule = "K"`, `leg = "2x"`, `cash_yield = "none"`).
* no extra data: every window is applicable (closes only, as the bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "trough_n": 126, "high_n": 252, "mode": "clip", "lo": 0.50},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "trough_n": 126, "high_n": 252, "mode": "clip", "lo": 0.25},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "trough_n": 126, "high_n": 252, "mode": "ramp"},
}


def recovered(price, spec) -> np.ndarray:
    """`(P, T)` the recovered fraction of the drawdown, in `[0, 1]` (1 where undefined)."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    lo = c1_lib.trailing_min(p, int(spec["trough_n"]))
    hi = c1_lib.trailing_max(p, int(spec["high_n"]))
    den = hi - lo
    ok = np.isfinite(lo) & np.isfinite(hi) & (den > 0)
    rec = np.where(ok, (p - np.where(ok, lo, 0.0)) / np.where(ok, den, 1.0), 1.0)
    return np.clip(rec, 0.0, 1.0)


def targets(data, spec):
    """Production's state machine with the early factor scaled by the recovered fraction."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    rec = recovered(data.price, spec)
    if spec["mode"] == "clip":
        early = 0.5 * np.clip(rec, float(spec["lo"]), 1.0)
    else:
        early = np.minimum(1.0, 0.5 * (1.0 + rec))
    is_early = on_eff & (~np.atleast_2d(e2)) & (factor == 0.5)
    factor = np.where(is_early, early, factor)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
