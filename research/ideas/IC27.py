"""IC27 -- credit-equity relative strength (HYG vs QQQ).  Idea bank `idea_bank_v2`, arms frozen.

    A  weight *= 0.75  while r_HYG(126) - r_QQQ(126) < -3 pp
    B  weight *= 0.50  while r_HYG(126) - r_QQQ(126) < -6 pp
    C  the early path's entry additionally requires r_HYG(126) - r_QQQ(126) >= 0

**Readings fixed here** (the bank's wording made exact; no parameter is chosen by the plugin):

* **The horizon is 126 sessions**, as every arm's formula writes it (`r(126)`).  The bank's
  parenthesis "(12-month price returns)" contradicts the formula (126 sessions are ~6 months); the
  formula is the operative, parameter-bearing text and is used.  Flagged, not resolved by choice.
* `r_HYG(126)` is the 126-session return of the registered Yahoo `HYG` **adjusted** close (key
  `hyg`, `data/historical/plugins_c2_hyg.csv`, 2007-04-11 ->; the bank's "daily total-return
  close"); `r_QQQ(126)` is the same on the arm's own `data.price` (QQQ's price close on main, the
  instrument's close on the ETF windows -- the series the production vote runs on, `ib21`'s
  convention); thresholds in fractions (`-3 pp` = `-0.03`).
* **On a real window** `r_HYG(126) = x_t / x_{t-126} - 1` on the forward-filled series (the last
  published value carried across a session HYG did not trade).  **Inside a bootstrap chunk** the
  HYG path is rebuilt exactly like the chunk's market path: column `t` carries the HYG return of
  the drawn source session (`extra_index[:, t]`, vs the pool's previous session), and the 126-column
  return is the product of those returns -- so the two legs of the spread are both the chunk's own
  resampled paths, rather than a ratio of HYG levels on two unrelated drawn dates.  Both
  constructions are `exp(trailing 126-sum of log returns) - 1`, identical on a real window.
* **Undefined -> neutral**, the bank's fixed convention ("before 2007 the state is treated as
  neutral and weight is unchanged"): A/B leave the weight unchanged and C does **not** block the
  early entry where the spread is undefined (unlike `ib21`'s C, whose bank text fixed no
  convention).
* A/B multiply the whole production target (E2 and the early leg); C adds the condition to the
  early leg's entry only (`plugins_b3.helpers.r2_state_with_entry`), leaving the vote, the exit and
  the E2 override untouched; sizing, cap and the execution layer (`rule` = K) are production's.

**Applicability**: `NotApplicable` where the spread is undefined on every session of the panel --
`GSPC_1957_1999`, `M1`, `M2` (all end before 2007); `M3`-`M5`, the main window (live from late
2007), the 21 ETFs and both bootstrap families are applicable (the bank's 3-window OOS set).
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _extra
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "n": 126,
          "below": -0.03, "mult": 0.75},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "n": 126,
          "below": -0.06, "mult": 0.50},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "entry_gate", "n": 126,
          "min_spread": 0.0},
}


def _log_ret(levels):
    """Per-column log return of a `(P, T)` level array (NaN in column 0 / where undefined)."""
    out = np.full(levels.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 1:] = np.log(levels[:, 1:] / levels[:, :-1])
    return out


def hyg_log_returns(data):
    """`(P, T)` HYG log return into every panel column (see the module docstring)."""
    hyg = data.extra("hyg", ffill=True)  # validates/logs the key; the real-window construction
    if data.extra_index is None:
        return _log_ret(hyg)
    name = _extra.resolve_series("hyg", data.extra_window or data.window)
    pool = _extra.forward_fill(_extra.values_on(name, data.extra_days).reshape(1, -1))
    pool_lr = _log_ret(pool)[0]
    lr = pool_lr[np.asarray(data.extra_index)]
    lr[:, 0] = np.nan  # column 0 is the chunk's artificial base row
    return lr


def spread(data, n):
    """`r_HYG(n) - r_price(n)`, NaN where either return is undefined."""
    r_hyg = np.exp(helpers.trailing_sum(hyg_log_returns(data), n)) - 1.0
    price = np.atleast_2d(np.asarray(data.price, dtype=np.float64))
    r_px = np.full(price.shape, np.nan)
    r_px[:, n:] = price[:, n:] / price[:, :-n] - 1.0
    return r_hyg - r_px


def targets(data, spec):
    spr = spread(data, int(spec["n"]))
    if not np.isfinite(spr).any():
        raise NotApplicable("Yahoo HYG starts 2007-04-11: the 126-session spread has no value in "
                            "this window")
    price = data.price
    comps = fe_signals.early_components(price)
    e2 = fe_signals.e2_up(price)
    if spec["mode"] == "entry_gate":
        gate = np.where(np.isfinite(spr), spr >= float(spec["min_spread"]), True)
        on_eff, factor = helpers.r2_state_with_entry(price, e2, comps[0] & gate, comps)
        return helpers.capped(on_eff, factor, data.vol)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    weak = np.isfinite(spr) & (np.nan_to_num(spr, nan=0.0) < float(spec["below"]))
    return np.where(weak, float(spec["mult"]) * wants, wants)
