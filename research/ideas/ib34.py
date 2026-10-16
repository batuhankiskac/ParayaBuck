"""ib34 -- lag-1 return autocorrelation gate on the trend vote (idea_bank_v1, arm A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** E2 on iff the 2-of-3 vote holds AND `rho1(252) > 0`
* **B** E2 on iff the 2-of-3 vote holds AND `rho1(252) > -0.05`
* **C** early-path entry additionally requires `rho1(252) > 0`

Literal reading and the frozen conventions:

* `rho1` is the lag-1 autocorrelation of the trailing **252 daily returns** of `data.price`
  (QQQ's price close on the main window, the market's own index/ETF close on OOS/instrument
  windows): the Pearson correlation of the window's 251 consecutive pairs
  `(r_i, r_{i+1})`, computed from rolling sums (equivalent to `numpy.corrcoef(r[:-1], r[1:])` on the
  window; checked against that reference in `research/plugins_b1/check_signals.py`). It is NaN
  until 252 returns exist (and if a window's variance is zero), and a NaN fails the gate
  (`> -0.05` on an undefined statistic is not affirmed).
* The gate enters the **vote** for A/B (`e2 = fe_signals.e2_up(price) & (rho1 > threshold)`), so the
  early path sees the gated vote as off; for C the production vote is kept and the gate is added to
  the early path's *entry* only (the vectorised state machine of `ib01`, verified against
  `fe_signals.r2_state`).
* Execution stays production K, `leg = 2x`, no cash yield. No extra data -> every window is
  applicable (closes only).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "rho_min": 0.0,
          "gate": "vote"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "rho_min": -0.05,
          "gate": "vote"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "rho_min": 0.0,
          "gate": "early"},
}


def _rho1(price, n=252):
    """`(P, T)` lag-1 autocorrelation of the trailing `n` returns (NaN until available)."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    P, T = p.shape
    out = np.full((P, T), np.nan)
    if T - 1 < n:
        return out
    r = p[:, 1:] / p[:, :-1] - 1.0  # r[:, i] is the return of session i + 1
    m = r.shape[1]
    rr = np.zeros_like(r)
    rr[:, :-1] = r[:, :-1] * r[:, 1:]
    z = np.zeros((P, 1))
    cr = np.concatenate([z, np.cumsum(r, axis=1)], axis=1)
    cr2 = np.concatenate([z, np.cumsum(r * r, axis=1)], axis=1)
    crr = np.concatenate([z, np.cumsum(rr, axis=1)], axis=1)
    npair = n - 1
    # the window of session t is the return index range [t - n, t - 1]; the pairs are its m = n - 1
    # consecutive pairs, i.e. x = r[t-n : t-1] and y = r[t-n+1 : t]
    sx = cr[:, n - 1 : m] - cr[:, 0 : m - n + 1]
    sy = cr[:, n : m + 1] - cr[:, 1 : m - n + 2]
    sxx = cr2[:, n - 1 : m] - cr2[:, 0 : m - n + 1]
    syy = cr2[:, n : m + 1] - cr2[:, 1 : m - n + 2]
    sxy = crr[:, n - 1 : m] - crr[:, 0 : m - n + 1]
    cov = npair * sxy - sx * sy
    vx = npair * sxx - sx * sx
    vy = npair * syy - sy * sy
    den = np.sqrt(np.maximum(vx, 0.0) * np.maximum(vy, 0.0))
    ok = den > 0
    out[:, n:] = np.where(ok, cov / np.where(ok, den, 1.0), np.nan)
    return out


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
    ok = _rho1(data.price, int(spec["n"])) > float(spec["rho_min"])  # NaN -> False
    if spec["gate"] == "vote":
        e2 = fe_signals.e2_up(data.price) & ok
        on_eff, factor = fe_signals.r2_state(data.price, e2, fe_signals.early_components(data.price))
    else:
        on_eff, factor = _r2_state_gated(data.price, fe_signals.e2_up(data.price), ok)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
