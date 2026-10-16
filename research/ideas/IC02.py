"""IC02 -- skip-month (12-1) momentum vote.  Idea bank `idea_bank_v2`, arms frozen by the spec.

    A  2-of-3 vote {SMA200, r(t-252 -> t-21) > 0, DON126}      (12-1 replaces TSM252)
    B  2-of-3 vote {SMA200, r(t-126 -> t-21) > 0, DON126}      (6-1 replaces TSM252)
    C  production E2 + the early path's entry additionally requires r(t-252 -> t-21) > 0

**The one reading the bank's shorthand leaves open**, fixed here literally: a return written
`r(a -> b)` is the price return *from* session `a` *to* session `b`, i.e. `close[b] / close[a] - 1`
-- so at session `t`, `r(t-252 -> t-21) = close[t-21] / close[t-252]`, which is the standard 12-1
convention (`price[t-skip] / price[t-n]`, `n` = 252, `skip` = 21) and the "same horizon, a
different estimator" the bank describes; arm B is the 6-1 leg (`n` = 126, same 21-session skip).
The condition `> 0` is therefore `close[t-21] > close[t-n]`.  Before `t = n` the return does not
exist and the vote is **off** (`False`), exactly as `fe_signals.tsm_up_price` treats its own
undefined prefix; that prefix lies inside the engine's warm-up (the measured window starts at
session 253), so it is not a live position.

Everything else is production: the 2-of-3 majority, the E2 sizing `min(1, 0.35/vol20)` x path
factor with the 0.98 cap, the early re-entry machine (only arm C's *entry* gate changes, via
`plugins_b3.helpers.r2_state_with_entry`), the execution layer (`rule` = K).  Closes only, so the
main window, the six OOS windows, the 21 ETF windows and both bootstrap families are applicable:
no `NotApplicable`, no extra data, no engine change.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "vote": "skip12_1", "n": 252,
          "skip": 21},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "vote": "skip12_1", "n": 126,
          "skip": 21},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "entry_gate", "n": 252,
          "skip": 21},
}


def skip_momentum_up(price, n, skip):
    """`(P, T)` bool: `close[t - skip] > close[t - n]`, False before `t` reaches `n`."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    T = price.shape[1]
    up = np.zeros(price.shape, dtype=bool)
    if T > n:
        up[:, n:] = price[:, n - skip : T - skip] > price[:, : T - n]
    return up


def vote_with(price, leg_up):
    """`fe_signals.e2_up`'s 2-of-3 majority with the TSM252 component replaced by `leg_up`."""
    s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
    d = fe_signals.don_up(price, DON_N).astype(np.int64)
    return (s + leg_up.astype(np.int64) + d) >= 2


def targets(data, spec):
    price = data.price
    skip_up = skip_momentum_up(price, int(spec["n"]), int(spec["skip"]))
    comps = fe_signals.early_components(price)
    if spec.get("mode") == "entry_gate":
        e2 = fe_signals.e2_up(price)
        on_eff, factor = helpers.r2_state_with_entry(price, e2, comps[0] & skip_up, comps)
        return helpers.capped(on_eff, factor, data.vol)
    on_eff, factor = fe_signals.r2_state(price, vote_with(price, skip_up), comps)
    return helpers.capped(on_eff, factor, data.vol)
