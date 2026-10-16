"""IC50 -- hit-rate (sign-persistence) sizing, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  weight *= clip((hit60 - 0.45)/0.10, 0.75, 1.10)
    B  the same multiplier applied only while the vote is on (early path unchanged)
    C  early weight *= clip((hit20 - 0.45)/0.10, 0.50, 1.00)

Readings fixed here:

* **hitN** at session t = the share of up days among the N daily returns ending at t of the
  arm's own instrument close `data.price` (the vote's series; "up" = close_t > close_{t-1}).
  Undefined for the first N sessions -> multiplier 1 (production).
* **"weight"** is production's per-session weight before the total cap: the multiplier scales the
  path factor (1 on the vote, 0.5 on the early path) times `min(1, 0.35/vol20)`, and the 0.98 total
  cap is applied after it (`fe_signals.sized_wants(on, factor * mult, vol)`), so a multiplier above
  1 cannot breach production's cap.
* **"while the vote is on"** = E2's 2-of-3 vote is on (path factor 1); **"early weight"** = the
  half-weight early re-entry position (E2 off, early path active, factor 0.5).
* The statistic is a function of each path's own price only, so a bootstrap chunk computes it on
  the reconstructed path. **Availability**: closes only -- every window is applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "center": 0.45, "scale": 0.10,
          "lo": 0.75, "hi": 1.10, "applies": "all"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "center": 0.45, "scale": 0.10,
          "lo": 0.75, "hi": 1.10, "applies": "vote_on"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 20, "center": 0.45, "scale": 0.10,
          "lo": 0.50, "hi": 1.00, "applies": "early"},
}


def hit_rate(price, n):
    """`(P, T)` share of up days among the `n` returns ending at t; NaN for t < n."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    up = (price[:, 1:] > price[:, :-1]).astype(np.float64)
    cs = np.concatenate([np.zeros((price.shape[0], 1)), np.cumsum(up, axis=1)], axis=1)
    out = np.full(price.shape, np.nan)
    if price.shape[1] > n:
        out[:, n:] = (cs[:, n:] - cs[:, :-n]) / n
    return out


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    hit = hit_rate(data.price, int(spec["n"]))
    mult = np.clip((hit - float(spec["center"])) / float(spec["scale"]), float(spec["lo"]), float(spec["hi"]))
    mult = np.where(np.isfinite(mult), mult, 1.0)
    if spec["applies"] == "vote_on":
        mult = np.where(factor == 1.0, mult, 1.0)
    elif spec["applies"] == "early":
        mult = np.where(factor == 0.5, mult, 1.0)
    elif spec["applies"] != "all":
        raise ValueError(f"unknown applies {spec['applies']!r}")
    return fe_signals.sized_wants(on_eff, factor * mult, data.vol)
