"""IB16 -- pre-holiday exposure top-up, as a batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  ``target = 0.98 (cap)`` on the last session before a detected holiday, while the vote is on
    B  ``target *= 1.25`` (capped at 0.98) on that session
    C  ``target *= 0.75`` on that session (the mirror sign)

Readings fixed here:

* **Holiday detection is from the session index itself**, as the spec's mechanism says: a
  Monday-Friday date inside the window's calendar on which the window has no session is a holiday
  (the local market's own calendar -- ^GSPC/^IXIC for the US windows, ^N225/^GDAXI/^FTSE for
  M3-M5, the listing market for an ETF).  Exchange half-days are sessions and are not holidays.
  A holiday cluster (e.g. Thanksgiving Thursday+Friday) marks the one session before it once.
* **"the last session before"** = the last session strictly earlier than the holiday; only holidays
  between the window's first and last session are detected (a holiday after the data's last
  session cannot change a trade -- the target of the last session is filled at the next open,
  which is outside the window).
* **Arm A** sets the target to the 0.98 cap when the vote is on *and* the early path is active
  (the production state machine's `on_eff`: E2 on or the half-weight re-entry) -- i.e. "while the
  trend vote is on" = a position is held that session.  Arms B/C multiply the production target,
  which is inert by itself when the vote is off (target 0).  Arm B's product is capped at 0.98.
* **Chunk calendars.**  A bootstrap chunk's market arrays carry one column more than `data.days`
  (the artificial base row at column 0; the sim reads columns `0 .. len(days)-1`), so the mask is
  laid on the first `len(days)` columns and the last one stays False.  The chunk's `days` are the
  *replay* calendar the framework carries, not the drawn source sessions' dates (its documented
  convention) -- a calendar overlay is therefore applied to a reshuffled calendar inside the
  bootstrap, which is worth knowing when reading this arm's bootstrap Δ, and is not corrected here:
  the framework has no channel that re-draws the calendar together with the returns.
* **Inputs**: the panel's own calendar and price close only -- every window is applicable, no
  `NotApplicable`.

`targets` is pure vectorised numpy; the execution layer is the engine's `K`.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "cap", "target": CAP},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "up", "mult": 1.25},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "down", "mult": 0.75},
}


def day_aligned_mask(data, mask):
    """Lay a `(len(data.days),)` calendar mask on the panel's columns `(1, T_price)`.

    `data.days` labels the panel's first `len(days)` columns on every window; a bootstrap chunk's
    market arrays carry one extra trailing column beyond the simulated range (see the module doc).
    """
    out = np.zeros((1, data.price.shape[1]), dtype=bool)
    out[0, : mask.size] = mask
    return out


def preholiday_mask(days):
    """``(T,)`` bool: session ``t`` is the last session before a weekday the window does not trade."""
    d = np.asarray(days).astype("datetime64[D]")
    cal = np.arange(int(d[0].astype(np.int64)), int(d[-1].astype(np.int64)) + 1)
    cal = cal.astype("datetime64[D]")
    bdays = cal[np.is_busday(cal)]                        # Mon-Fri
    holidays = bdays[~np.isin(bdays, d)]
    pos = np.searchsorted(d, holidays, side="left") - 1   # the last session strictly before it
    out = np.zeros(d.size, dtype=bool)
    out[pos[pos >= 0]] = True
    return out


def targets(data, spec):
    """The production target series, topped up / cut on the session before a holiday."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    pre = day_aligned_mask(data, preholiday_mask(data.days))
    if spec["mode"] == "cap":
        return np.where(pre & on_eff, spec["target"], wants)
    if spec["mode"] == "up":
        return np.where(pre, np.minimum(wants * spec["mult"], CAP), wants)
    return np.where(pre, wants * spec["mult"], wants)
