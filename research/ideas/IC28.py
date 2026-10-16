"""IC28 -- cyclical-vs-defensive sector ratio, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  3-of-4 vote {SMA200, TSM252, DON126, r_cyclicals/defensives(126) > 0}, on iff >= 3
    B  weight *= 0.75 while r_cyclicals/defensives(126) < 0
    C  early-path entry additionally requires r_cyclicals/defensives(126) > 0

Inputs: the registered sector SPDR total-return closes (extra keys `sec_xly`, `sec_xli`, `sec_xlb`
= cyclicals; `sec_xlp`, `sec_xlu`, `sec_xlv` = defensives; adjclose, 1998-12-22 ->).

Readings fixed here:

* **Baskets.** Each basket is equal-weighted and rebalanced daily: its daily return is the mean of
  its three ETFs' daily total returns. ``r(126)`` is the 126-session return of the ratio of the two
  basket indices, ``prod_{126} (1 + r_cyc) / (1 + r_def) - 1``; its sign is computed as the sum of
  the 126 daily log ratios. Undefined (NaN) wherever any of the 126 returns is missing.
* **Daily returns per column.** On a real window: the date-aligned, forward-filled closes'
  session-to-session change (on a non-US window a session the US market is closed on carries a
  zero return). Inside a bootstrap chunk: the drawn source session's own return on the pool
  calendar (`extra_days[extra_index]`), i.e. the chunk rebuilds the basket paths from the same
  drawn sessions as its market path, so r(126) is a function of the resampled history exactly as
  the price votes are (column 0, the chunk's artificial base row, has no return).
* **Missing -> neutral.** Where r(126) is undefined the 4th vote counts as passing (production
  2-of-3), B does not cut and C adds no entry requirement.
* **C** adds the condition to the early path's *entry* test only (its exit stays "below SMA50"):
  implemented by and-ing it into `early_components`' 20-session momentum condition, which
  `r2_state` reads at entry only.
* **Availability.** The spec scopes the OOS stage to M3/M4/M5 ("sector SPDRs are absent on
  GSPC_1957_1999, M1 and M2"): those three windows raise `NotApplicable` (both ^GSPC windows and
  ^IXIC 1975-1999 end in 1999, a year after the SPDRs start), as does any window or chunk on which
  r(126) is nowhere defined. The main window, the bootstraps and the 21 ETFs are applicable. In
  the pooled OOS bootstrap (M3/M4/M5 pools reach back to 1966/1987/1984) r(126) is defined only
  where all 126 drawn sessions are post-1998, so a chunk is often nowhere defined (-> refused) and
  otherwise mostly neutral; that is the honest limit of a 1998+ input on those pools.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

CYCLICALS = ("sec_xly", "sec_xli", "sec_xlb")
DEFENSIVES = ("sec_xlp", "sec_xlu", "sec_xlv")
#: the idea's declared OOS scope (`data.oos.evaluable` = M3, M4, M5)
SCOPED_OUT = ("GSPC_1957_1999", "M1", "M2")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "lookback": 126, "mode": "vote4", "need": 3},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "lookback": 126, "mode": "scale", "factor": 0.75},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "lookback": 126, "mode": "early_entry"},
}

_POOL = {}


def _ffill1(a):
    idx = np.where(np.isnan(a), 0, np.arange(a.size))
    np.maximum.accumulate(idx, out=idx)
    return a[idx]


def _pool_returns(name, pool_days):
    """Daily returns of one series on a pool calendar (forward-filled closes), cached."""
    pool_days = np.asarray(pool_days, dtype="<U10")
    ck = (name, pool_days.size, str(pool_days[0]), str(pool_days[-1]))
    if ck not in _POOL:
        lv = _ffill1(_ext.values_on(name, pool_days))
        r = np.full(lv.size, np.nan)
        r[1:] = lv[1:] / lv[:-1] - 1.0
        _POOL[ck] = r
    return _POOL[ck]


def daily_returns(data, key):
    """`(P, T)` daily total return of an extra series per panel column (see the docstring)."""
    if data.extra_index is None:
        lv = data.extra(key, ffill=True)
        r = np.full(lv.shape, np.nan)
        r[:, 1:] = lv[:, 1:] / lv[:, :-1] - 1.0
        return r
    name = _ext.resolve_series(key, data.extra_window or data.window)
    r = _pool_returns(name, data.extra_days)[np.asarray(data.extra_index)]
    r[:, 0] = np.nan
    return r


def ratio_return_sign(data, n):
    """`(P, T)`: +1 / -1 / 0 for r_cyc/def(n) > 0 / < 0 / == 0, NaN where undefined."""
    rc = np.mean([daily_returns(data, k) for k in CYCLICALS], axis=0)
    rd = np.mean([daily_returns(data, k) for k in DEFENSIVES], axis=0)
    lr = np.log1p(rc) - np.log1p(rd)
    bad = ~np.isfinite(lr)
    z = np.zeros((lr.shape[0], 1))
    cs = np.concatenate([z, np.cumsum(np.where(bad, 0.0, lr), axis=1)], axis=1)
    cb = np.concatenate([z, np.cumsum(bad, axis=1)], axis=1)
    out = np.full(lr.shape, np.nan)
    if lr.shape[1] > n:
        s = cs[:, n + 1:] - cs[:, 1:-n]          # sum of lr over columns t-n+1 .. t, t >= n
        nb = cb[:, n + 1:] - cb[:, 1:-n]
        out[:, n:] = np.where(nb == 0, np.sign(s), np.nan)
    return out


def targets(data, spec):
    if (data.extra_window or data.window) in SCOPED_OUT:
        raise NotApplicable("the idea's spec scopes the OOS stage to M3/M4/M5: the sector SPDRs "
                            "(1998-12-22 ->) are absent on GSPC_1957_1999, M1 and M2")
    sgn = ratio_return_sign(data, int(spec["lookback"]))
    if not np.isfinite(sgn).any():
        raise NotApplicable("the sector SPDR baskets have no 126-session ratio return in this window")
    known = np.isfinite(sgn)
    pos = known & (np.nan_to_num(sgn) > 0)
    neg = known & (np.nan_to_num(sgn) < 0)
    price = data.price
    comps = fe_signals.early_components(price)
    mode = spec["mode"]
    if mode == "vote4":
        s = fe_signals.sma_up(price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(price).astype(int)
        d = fe_signals.don_up(price, DON_N).astype(int)
        v4 = np.where(known, pos, True).astype(int)
        vote = (s + t + d + v4) >= int(spec["need"])
        on_eff, factor = fe_signals.r2_state(price, vote, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(price)
    if mode == "early_entry":
        sma50_up, vol_p, med_p, lag_cond = comps
        comps = (sma50_up, vol_p, med_p, lag_cond & np.where(known, pos, True))
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    if mode == "scale":
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        wants = fe_signals.sized_wants(on_eff, factor, data.vol)
        return wants * np.where(neg, float(spec["factor"]), 1.0)
    raise ValueError(f"unknown mode {mode!r}")
