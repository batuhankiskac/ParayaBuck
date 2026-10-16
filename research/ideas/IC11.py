"""IC11 -- tail-percentile (CVaR) risk sizing (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** `vt = min(1, 0.35 / sigma_cvar)`,
  `sigma_cvar = 2.063 * sqrt(252) * |mean of the worst 5 % of the last 252 daily log returns|`
* **B** `vt = min(1, 0.35 / (0.5 * sigma_cvar + 0.5 * vol20))`
* **C** `vt = min(1, 0.35 / max(sigma_cvar, vol20))`

Literal reading and the frozen conventions (everything the spec leaves open):

* the returns are the **held leg's** (`data.close`, the same series `data.vol` = `vol_n` reads), 252
  of them per session, daily **log** returns.  `vol20` is production's `data.vol` (annualised
  sample vol of the leg's last 20 returns, NaN before index 20).
* "the worst 5 %" of a 252-session window is 12.6 sessions, so `k = ceil(0.05 * 252) = 13` order
  statistics are averaged; taken as the mean of the 13 smallest values (`np.partition`, no
  interpolation), which is the discrete tail mean a CVaR is.  `k` is frozen in the spec as
  `tail_k`.
* the annualisation is the spec's own: `2.063 * sqrt(252) * |mean|`, the Gaussian-comparable scale
  (`E[-X | X < q_5%] = 2.0627` for a standard normal, i.e. `2.063` estimates the daily sigma of a
  normal with that tail mean).  The `|.|` is the spec's.
* `min(1, 0.35 / den)` follows the engine's `vt_of` convention where the denominator is undefined
  or non-positive: `vt = 1.0` (no leverage limit) rather than a division by zero.  Before 252
  sessions `sigma_cvar` is NaN, i.e. the warm-up sizes at `vt = 1`; the measured window starts at
  index 253, so no measured session sees that regime.
* only the sizing changes: production's E2 vote, early-re-entry state machine, `factor` and the 0.98
  total cap are unchanged (`c1_lib.sized_wants_vt` is `fe_signals.sized_wants` with `vt` passed in).
  Execution stays production K.
* no extra data: every window is applicable (closes only, as the bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "n": 252, "tail_k": 13, "scale": 2.063, "mode": "cvar"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "n": 252, "tail_k": 13, "scale": 2.063, "mode": "blend"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "n": 252, "tail_k": 13, "scale": 2.063, "mode": "max"},
}


def sigma_cvar(close, spec) -> np.ndarray:
    """`(P, T)` the spec's annualised tail-mean estimate (NaN until the window is complete)."""
    r = c1_lib.log_returns(close)
    tail = c1_lib.trailing_tail_mean(r, int(spec["n"]), int(spec["tail_k"]))
    return float(spec["scale"]) * np.sqrt(252.0) * np.abs(tail)


def targets(data, spec):
    """The production state machine sized on the tail-mean vol estimate."""
    s = sigma_cvar(data.close, spec)
    vol20 = np.atleast_2d(np.asarray(data.vol, dtype=np.float64))
    if spec["mode"] == "cvar":
        den = s
    elif spec["mode"] == "blend":
        den = 0.5 * s + 0.5 * vol20
    else:
        den = np.maximum(s, vol20)
    vt = c1_lib.vt_of_denominator(den)

    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return c1_lib.sized_wants_vt(on_eff, factor, vt)
