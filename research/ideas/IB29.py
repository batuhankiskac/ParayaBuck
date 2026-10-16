"""IB29 -- drawdown-depth-scaled exposure, as a batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  ``weight *= clip(1 - dd252/0.30, 0.50, 1.00)``    (dd252 = drawdown from the 252-session high)
    B  ``weight *= clip(1 - ddATH/0.40, 0.50, 1.00)``    (ddATH = drawdown from the all-time high)
    C  ``weight *= clip(1 + dd252/0.30, 1.00, 1.50)``    (the mirror: more exposure into drawdowns)

Readings fixed here:

* **The state is the arm's own price path**: `data.price` (the series the production vote runs on).
  ``dd252`` uses the trailing 252-session maximum *including the session itself*; ``ddATH`` uses the
  expanding maximum over the panel's own sessions -- a plugin has no history before the window's
  first session, so "all-time high" = the highest close the window's data carries up to that
  session (for the main window that starts 1999-03-10, i.e. before the measured window opens).
  NaN before the first full window (t < 251 for A/C) -> the multiplier is 1, i.e. production.
* **The 0.98 total cap is unchanged** (a production invariant; the account cannot hold more than
  its cash).  Every arm multiplies the *pre-cap* target (production's `on_eff x factor x vt`) and
  re-applies the cap, so arms A/B -- multipliers <= 1 -- coincide exactly with the production
  target times their multiplier, and arm C's up-scaling is clipped at 0.98 rather than levering the
  account past 100 %.
* **Inputs**: the panel's own price close -- every window is applicable, no `NotApplicable`.

`targets` is pure vectorised numpy; the execution layer is the engine's `K`.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "dd252", "d": 0.30,
          "lo": 0.50, "hi": 1.00, "window": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "ddath", "d": 0.40,
          "lo": 0.50, "hi": 1.00, "window": 252},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "dd252", "d": 0.30,
          "lo": 1.00, "hi": 1.50, "window": 252, "sign": 1.0},
}


def _cap98(raw):
    """`fe_signals.sized_wants`' total cap, on a pre-cap weight panel."""
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def drawdown(price, mode, window=252):
    """``(P, T)`` drawdown (fraction) from the trailing ``window``-session or expanding high."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    if mode == "ddath":
        high = np.maximum.accumulate(price, axis=1)
    else:
        high = np.full_like(price, np.nan)
        if price.shape[1] >= window:
            high[:, window - 1:] = sliding_window_view(price, window, axis=1).max(axis=2)
    return 1.0 - price / high


def targets(data, spec):
    """The production target series scaled by the drawdown multiplier, then capped at 0.98."""
    dd = drawdown(data.price, spec["mode"], spec["window"])
    mult = np.clip(1.0 + spec.get("sign", -1.0) * dd / spec["d"], spec["lo"], spec["hi"])
    mult = np.where(np.isfinite(mult), mult, 1.0)
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    raw = on_eff * factor * fe_signals.vt_of(data.vol)
    return _cap98(raw * mult)
