"""ib21 -- bond-equity momentum spread as a risk-regime modifier (idea_bank_v1, arm A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** `weight *= 0.5` while `r_IEF252 > r_QQQ252`
* **B** `weight *= 0.75` while `r_IEF252 - r_QQQ252 > +10 pp`
* **C** early-path entry permitted only while `r_IEF252 - r_QQQ252 <= 0`

Literal reading and the frozen conventions:

* `r_IEF252` is the 252-session total return of the registered Yahoo `IEF` series
  (`data.extra("ief", ffill=True)`: `x_t / x_{t-252} - 1`, with the last published value carried
  across sessions the ETF did not trade, e.g. foreign holidays), `r_QQQ252` the same on the arm's
  own `data.price` (QQQ's price close on the main window, the market's own index/ETF close on the
  OOS and instrument windows -- the series the production vote runs on), both in percent/100, so
  B's `+10 pp` threshold is `0.10`.
* A session whose spread is undefined (the first 252 sessions of a panel, or the part of a window
  before IEF's 2002-07-30 inception) is *neutral* for the multipliers (A/B: no change) and
  **blocks** the early path's entry in C (`<= 0` cannot be affirmed) -- the same NaN convention as
  the other gates of this task: an unverifiable condition is False.
* Applicability (`NotApplicable` when IEF has no value in the window): `M1` (^IXIC 1975-1999),
  `M2` (^GSPC 1935-1956) and `GSPC_1957_1999`. `M3`/`M4`/`M5`, the main window and all 21 ETF
  windows carry IEF from 2002-07-30 on (and the part before is neutral), so E1 and the standard
  G7/G8 apply -- the bank's note ("G7/G8 not evaluable") held only for the three windows above
  under the framework's per-window availability rule.
* A/B multiply the whole production target (E2 and the early path); C adds the spread condition to
  the early path's entry only (the vectorised state machine of `ib01`, verified against
  `fe_signals.r2_state`), leaving the vote, the exit rule and the E2 override untouched.
* Execution stays production K, `leg = 2x`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 252, "cut": 0.5, "lead": 0.0,
          "scope": "all"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 252, "cut": 0.75, "lead": 0.10,
          "scope": "all"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 252, "cut": 1.0, "lead": 0.0,
          "scope": "early"},
}


def _total_return(x, lag):
    """`x_t / x_{t-lag} - 1` (NaN where either side is undefined)."""
    out = np.full_like(x, np.nan)
    out[:, lag:] = x[:, lag:] / x[:, :-lag] - 1.0
    return out


def _spread(data, lag):
    """`(r_IEF - r_instrument)` of the window's own instrument, or `NotApplicable`."""
    ief = data.extra("ief", ffill=True)
    if not np.isfinite(ief).any():
        raise NotApplicable("Yahoo IEF starts 2002-07-30 and has no value in this window")
    return _total_return(ief, lag) - _total_return(data.price, lag)


def _r2_state_gated(price, e2_on, entry_gate):
    """`fe_signals.r2_state` with a per-session early-entry gate (see `ib01`, identical code)."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    gate = np.atleast_2d(np.asarray(entry_gate, dtype=bool))
    sma50_up, vol_p, med_p, lag_cond = fe_signals.early_components(price)
    entry = (~e2_on) & sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond & gate
    reset = e2_on | (~sma50_up)
    idx = np.arange(price.shape[1])
    last_entry = np.maximum.accumulate(np.where(entry, idx, -1), axis=1)
    last_reset = np.maximum.accumulate(np.where(reset, idx, -1), axis=1)
    active = last_entry > last_reset
    return e2_on | active, np.where(e2_on, 1.0, np.where(active, 0.5, 0.0))


def targets(data, spec):
    spread = _spread(data, int(spec["lag"]))
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    if spec["scope"] == "early":
        on_eff, factor = _r2_state_gated(data.price, e2, spread <= float(spec["lead"]))
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    lead = spread > float(spec["lead"])  # NaN -> False
    return np.where(lead, float(spec["cut"]) * wants, wants)
