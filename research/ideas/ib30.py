"""ib30 -- time-under-water (duration of the all-time-high drawdown) scaling (bank A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** `weight *= clip(1 - u/756, 0.50, 1.00)`, `u` = sessions since the last all-time high
* **B** `weight *= clip(1 - u/378, 0.50, 1.00)`
* **C** mirror: `weight *= clip(1 + u/756, 1.00, 1.25)`

Literal reading and the frozen conventions:

* `u_t = t - argmax_{s <= t} price_s`, on `data.price` (QQQ's price close on the main window, the
  market's own index/ETF close elsewhere -- the series the production vote runs on, and the bank's
  "the arm's own instrument close" for the OOS/instrument stages). A session that makes a new high
  (or ties one) has `u = 0`; sessions are counted, not calendar days.
* The multiplier is applied to the whole production target (E2 and the early path). C's
  `clip(..., 1.00, 1.25)` deliberately raises the target above the production 0.98 cap (up to
  1.225) -- that is what the frozen arm says; the engine simulates whatever target weight it is
  given.
* Execution stays production K, `leg = 2x`, no cash yield. No extra data -> every window is
  applicable (and `u` needs only one session of history, so nothing is neutral).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "divisor": 756.0, "slope": -1.0,
          "lo": 0.50, "hi": 1.00},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "divisor": 378.0, "slope": -1.0,
          "lo": 0.50, "hi": 1.00},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "divisor": 756.0, "slope": 1.0,
          "lo": 1.00, "hi": 1.25},
}


def _sessions_since_high(price):
    """`(P, T)` sessions since the last close that set (or tied) the running all-time high."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    idx = np.arange(p.shape[1])
    run_max = np.maximum.accumulate(p, axis=1)
    last = np.maximum.accumulate(np.where(p >= run_max, idx, 0), axis=1)
    return (idx - last).astype(np.float64)


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    u = _sessions_since_high(data.price)
    mult = np.clip(1.0 + float(spec["slope"]) * u / float(spec["divisor"]),
                   float(spec["lo"]), float(spec["hi"]))
    return wants * mult
