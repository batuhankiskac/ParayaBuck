"""IC18 -- credit-quality-spread (Baa - Aaa) veto, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  weight = 0 while (BAA - AAA)_t (1-month lag) > 1.30 * its expanding median since 1929
    B  weight *= 0.5 in the same state
    C  3-of-4 vote {SMA200, TSM252, DON126, quality spread <= 1.30x its expanding median}, on iff >= 3

Inputs: FRED `BAA` and `AAA` (Moody's seasoned yields, monthly, 1919-01 ->), already registered as
extra keys `baa_m` / `aaa_m` (SHA-256 in `research/batch_screen/EXTRA_DATA.json`).

Readings fixed here:

* **Spread, lag, median.** On the series' own monthly grid, ``q(M) = BAA(M) - AAA(M)`` in
  percentage points and ``med(M) = median(q(1929-01) ... q(M))`` (expanding, the 1929-01 month
  included, nothing before it). At a session of calendar month M the arm uses reference month M-1
  (the spec's 1-month lag): stressed iff ``q(M-1) > 1.30 * med(M-1)``. Months before 1929-01 have
  no state (neutral).
* **Monthly lookup**, not a forward-filled date alignment: the state is looked up by the month of
  the session each column replays -- the panel's own session on a real window, the drawn source
  session (`extra_days[extra_index]`) inside a bootstrap chunk -- so a chunk replays the true
  historical credit state of the session whose return it draws.
* **Missing -> neutral** (A/B do not cut, C counts the 4th vote as passing = production 2-of-3).
* **Availability.** The series covers every window (1919+, including M2 1935-1956); a window on
  which the state is nowhere defined would raise `NotApplicable`, which does not happen on the
  registered data. M3/M4/M5 (non-US markets) are applicable because the spec lists all six OOS
  windows as evaluable.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mult": 1.30, "median_from": "1929-01",
          "lag_months": 1, "mode": "scale", "factor": 0.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mult": 1.30, "median_from": "1929-01",
          "lag_months": 1, "mode": "scale", "factor": 0.5},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mult": 1.30, "median_from": "1929-01",
          "lag_months": 1, "mode": "vote4", "need": 3},
}

_GRID = {}
_STATE = {}


def source_days(data):
    """`(P, T)` ISO day of the session each panel column replays."""
    if data.extra_index is not None:
        return np.asarray(data.extra_days, dtype="<U10")[np.asarray(data.extra_index)]
    d = np.asarray(data.days, dtype="<U10")
    return np.broadcast_to(d.reshape(1, -1), (data.P, data.T))


def monthly_grid(name):
    """`(first month ordinal, values)` of a monthly FRED series over its declared range (cached)."""
    if name not in _GRID:
        meta = _ext.series_meta(name)
        lo, hi = np.datetime64(meta["range"][0], "M"), np.datetime64(meta["range"][1], "M")
        grid = np.arange(lo, hi + np.timedelta64(1, "M"))
        vals = _ext.values_on(name, grid.astype("datetime64[D]").astype(str))
        _GRID[name] = (int(grid[0].astype(np.int64)), vals)
    return _GRID[name]


def monthly_state(window, spec):
    """`(first month ordinal, stressed)` on the monthly grid: 1.0 / 0.0 / NaN (undefined)."""
    baa = _ext.resolve_series("baa_m", window)
    aaa = _ext.resolve_series("aaa_m", window)
    ck = (baa, aaa, float(spec["mult"]), spec["median_from"])
    if ck not in _STATE:
        m0b, vb = monthly_grid(baa)
        m0a, va = monthly_grid(aaa)
        m0 = max(m0b, m0a)
        n = min(m0b + vb.size, m0a + va.size) - m0
        q = vb[m0 - m0b:m0 - m0b + n] - va[m0 - m0a:m0 - m0a + n]
        start = int(np.datetime64(spec["median_from"], "M").astype(np.int64)) - m0
        state = np.full(n, np.nan)
        for k in range(max(start, 0), n):  # ~1,200 monthly medians, computed once per process
            hist = q[max(start, 0):k + 1]
            hist = hist[np.isfinite(hist)]
            if np.isfinite(q[k]) and hist.size:
                state[k] = float(q[k] > float(spec["mult"]) * np.median(hist))
        _STATE[ck] = (m0, state)
    return _STATE[ck]


def stressed_on_sessions(data, spec):
    m0, st = monthly_state(data.extra_window or data.window, spec)
    m = source_days(data).astype("datetime64[M]").astype(np.int64)
    k = m - int(spec["lag_months"]) - m0
    ok = (k >= 0) & (k < st.size)
    return np.where(ok, st[np.clip(k, 0, st.size - 1)], np.nan)


def targets(data, spec):
    st = stressed_on_sessions(data, spec)
    if not np.isfinite(st).any():
        raise NotApplicable("the monthly BAA-AAA state (1929-01 ->) is not defined in this window")
    stressed = np.nan_to_num(st, nan=0.0) > 0.5
    price = data.price
    comps = fe_signals.early_components(price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(price).astype(int)
        d = fe_signals.don_up(price, DON_N).astype(int)
        vote = (s + t + d + (~stressed).astype(int)) >= int(spec["need"])
        on_eff, factor = fe_signals.r2_state(price, vote, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * np.where(stressed, float(spec["factor"]), 1.0)
