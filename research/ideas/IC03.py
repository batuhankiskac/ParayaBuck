"""IC03 -- CUSUM (change-point) filter vote, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  the momentum vote replaced by CUSUM(h = 3.0 * vol20_daily, reset at each crossing) > 0
    B  same with h = 2.0 * vol20_daily
    C  3-of-4 vote {SMA200, TSM252, DON126, CUSUM(h = 3.0) > 0}, on iff >= 3

Readings fixed here (the most literal one wherever the spec leaves a detail open):

* **"The momentum vote"** is production's TSM252 vote (the bank's own wording elsewhere:
  "Production's momentum vote is a 252-session return"). Arms A/B therefore keep E2's 2-of-3 count
  over {SMA200, CUSUM state, DON126}; arm C adds the CUSUM state as a 4th vote. The early re-entry
  path, the sizing `min(1, 0.35/vol20)` on the held leg and the 0.98 cap are production's.
* **The filter** is Lopez de Prado's symmetric CUSUM filter (the cited sampling scheme) on the
  daily simple return `r_t = price_t / price_{t-1} - 1` of the vote's own series `data.price`:
  ``S+ = max(0, S+ + r_t)``, ``S- = min(0, S- + r_t)``; if ``S- < -h_t`` the state turns OFF and
  ``S-`` resets to 0, elif ``S+ > h_t`` the state turns ON and ``S+`` resets to 0 (only the side
  that crossed resets, as in the reference implementation). "CUSUM > 0" is the state: ON iff the
  most recent crossing was upward. Before the first crossing the state is OFF.
* **h_t** = k * vol20_daily, vol20_daily = `fe_signals.vol_n(price, 20)` / sqrt(252) at session t
  (the same trailing 20-return sample-vol convention production sizes with, here of the vote's
  price series, ending at t). Sessions where vol20 is not yet defined (the first 20) do not
  accumulate.
* The recursion runs per path in a numba kernel (signal construction, no simulation); it is a
  function of each path's own price only, so a bootstrap chunk runs it on the reconstructed path.
* **Availability**: closes only -- every window is applicable (no `NotApplicable`).
"""

from __future__ import annotations

import numpy as np
from numba import njit

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_mult": 3.0, "mode": "replace_tsm"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_mult": 2.0, "mode": "replace_tsm"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_mult": 3.0, "mode": "vote4", "need": 3},
}


@njit(cache=False, nogil=True)  # no on-disk cache: the framework loads plugins by file path
def _cusum_state(price, h, out):
    """Symmetric CUSUM filter state (1 = last crossing up) per row; see the module docstring."""
    P, T = price.shape
    for p in range(P):
        sp = 0.0
        sn = 0.0
        st = 0
        for t in range(1, T):
            ht = h[p, t]
            if np.isfinite(ht):
                r = price[p, t] / price[p, t - 1] - 1.0
                sp = max(0.0, sp + r)
                sn = min(0.0, sn + r)
                if sn < -ht:
                    sn = 0.0
                    st = 0
                elif sp > ht:
                    sp = 0.0
                    st = 1
            out[p, t] = st


def cusum_up(price, h_mult):
    """`(P, T)` bool: the CUSUM filter's state with h_t = h_mult * vol20_t / sqrt(252)."""
    price = np.ascontiguousarray(np.atleast_2d(np.asarray(price, dtype=np.float64)))
    h = np.ascontiguousarray(h_mult * fe_signals.vol_n(price) / np.sqrt(252.0))
    out = np.zeros(price.shape, dtype=np.int64)
    _cusum_state(price, h, out)
    return out.astype(bool)


def targets(data, spec):
    """The production state machine with the CUSUM state in (or added to) the trend vote."""
    price = data.price
    cus = cusum_up(price, float(spec["h_mult"])).astype(int)
    s = fe_signals.sma_up(price, SMA_N).astype(int)
    d = fe_signals.don_up(price, DON_N).astype(int)
    if spec["mode"] == "replace_tsm":
        vote = (s + cus + d) >= 2
    elif spec["mode"] == "vote4":
        t = fe_signals.tsm_up_price(price).astype(int)
        vote = (s + t + d + cus) >= int(spec["need"])
    else:
        raise ValueError(f"unknown mode {spec['mode']!r}")
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, vote, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
