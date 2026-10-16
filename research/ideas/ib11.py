"""IB11 -- downside/upside semi-deviation asymmetry as a sizing modifier.

Per `docs/research/idea_bank_v1.json` IB11, `vol20` stays the *level* and the shape of the
distribution enters as a tilt:

    weight *= clip(semiUp_n / semiDown_n, 0.70, 1.00)

    A  n = 60, the whole target weight
    B  n = 252, the whole target weight
    C  n = 60, the early-path leg only

`semiUp_n` / `semiDown_n` use the campaign's own semi-deviation convention, `vol_window_v1`'s V5:
with `r` the daily simple returns of the instrument's close,

    semiDown_n = sqrt(2 * 252 / n * sum(min(r, 0)^2))       semiUp_n = sqrt(2 * 252 / n * sum(max(r, 0)^2))

over the trailing `n` sessions (NaN until the window is full, the engine's own rolling-window
convention). "The arm's own instrument close" is the leg the account holds, `data.close`, i.e. the
same series `data.vol` -- the production sizing input -- is built from; the tilt multiplies the
production target weight (`min(1, 0.35/vol20)` x path factor, rule `K`), and production's total
cap 0.98 still binds afterwards (`helpers.scaled`).

Where the ratio is undefined (a window with no full 60/252-session history, a zero or NaN
downside semi-deviation) the multiplier is 1.0, i.e. production behaviour -- the documented
"unavailable -> production" convention, so no window manufactures a position out of a data gap.
Arm C leaves the vote-driven (`factor = 1`) sessions untouched and scales only the half-weight
early leg (`factor = 0.5`), as its text says.

No extra data, no engine change, every window applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "scope": "all",
          "lo": 0.70, "hi": 1.00},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "scope": "all",
          "lo": 0.70, "hi": 1.00},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "scope": "early",
          "lo": 0.70, "hi": 1.00},
}


def semi(close, n, up: bool):
    """`(P, T)` trailing `n`-session semi-deviation, annualised (the V5 convention)."""
    close = np.atleast_2d(np.asarray(close, dtype=np.float64))
    r = close[:, 1:] / close[:, :-1] - 1.0
    part = np.maximum(r, 0.0) if up else np.minimum(r, 0.0)
    a = np.full(close.shape, np.nan)
    a[:, 1:] = part
    return np.sqrt(2.0 * 252.0 / n * helpers.trailing_sum(a * a, n))


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)

    n = spec["n"]
    tilt = helpers.nan_clip_ratio(semi(data.close, n, True), semi(data.close, n, False),
                                  spec["lo"], spec["hi"])
    if spec["scope"] == "early":
        return helpers.scaled(wants, np.where(factor == 0.5, tilt, 1.0))
    return helpers.scaled(wants, tilt)
