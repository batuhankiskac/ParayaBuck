"""IC48 -- drawdown-velocity sizing.  Idea bank `idea_bank_v2`, arms frozen by the spec.

    A  weight *= clip(1 - dd20 / 0.10, 0.50, 1.00)
    B  weight *= clip(1 - 0.5 * (dd20 / dd252), 0.50, 1.00)      (speed relative to depth)
    C  weight *= clip(1 + dd20 / 0.10, 1.00, 1.25)               (the mirror: buy the fast dip)

with `dd20 = 1 - close / max(close over the last 20 sessions)` and `dd252` the same over 252.

Readings fixed here (the bank's wording, made concrete; no parameter is chosen by the plugin):

* **The state is the arm's own price path**, `data.price` -- the series the production vote runs on
  -- exactly as in the banked `IB29` (the closest tested idea) and in every other batch_screen
  plugin that reads "the arm's own instrument daily close".  The trailing maximum *includes* the
  session itself, and `dd252` is NaN before its window is full, where the multiplier is 1.0
  (production behaviour, the framework's "undefined input -> unchanged weight" convention).
  Note, disclosed: `data.price` is the *underlying* (QQQ), not the 2x leg the account holds, so the
  held leg's own drawdown is roughly twice this one; the bank's signal_inputs names the arm's
  instrument close and the production vote reads the same series, so the underlying is what is used.
* **The multiplier applies to the produced target weight and the production 0.98 cash cap is
  re-applied** (`plugins_b3.helpers.scaled`, the convention IB23 arm C and IB29 use).  Arms A/B
  multiply a weight that is already at or below the cap; arm C's `1.25` can reach the cap (0.98) but
  can never lever the account past 100 % of NAV, which the framework treats as a hard invariant.
* **Applicability**: closes only, so the main window, the six OOS windows, the 21 ETF windows, the
  synthetic smoke panel and both bootstrap families are applicable -- no `NotApplicable`, no extra
  data, no engine change (`rule` = K is the engine's production execution layer).
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "dd_abs", "n": 20,
          "d": 0.10, "lo": 0.50, "hi": 1.00},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "dd_ratio", "n": 20,
          "n_long": 252, "lo": 0.50, "hi": 1.00},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "mirror", "n": 20,
          "d": 0.10, "lo": 1.00, "hi": 1.25},
}


def drawdown(price, window):
    """`(P, T)` drawdown from the trailing `window`-session high (the high includes the session)."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    high = np.full_like(price, np.nan)
    if price.shape[1] >= window:
        high[:, window - 1:] = sliding_window_view(price, window, axis=1).max(axis=2)
    return 1.0 - price / high


def multiplier(dd20, dd_long, spec):
    """The arm's per-session multiplier, 1.0 wherever the drawdown is undefined."""
    mode = spec["mode"]
    if mode == "dd_abs":
        return helpers.nan_clip_ratio(spec["d"] - dd20, spec["d"], spec["lo"], spec["hi"])
    if mode == "dd_ratio":
        return helpers.nan_clip_ratio(dd_long - 0.5 * dd20, dd_long, spec["lo"], spec["hi"])
    if mode == "mirror":
        return helpers.nan_clip_ratio(spec["d"] + dd20, spec["d"], spec["lo"], spec["hi"])
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover


def targets(data, spec):
    dd20 = drawdown(data.price, int(spec["n"]))
    dd_long = drawdown(data.price, int(spec["n_long"])) if "n_long" in spec else None
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    return helpers.scaled(wants, multiplier(dd20, dd_long, spec))
