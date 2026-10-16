"""IC13 -- real-activity regime (CFNAI) vote, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json` (main), three arms, parameters frozen by the spec:

    A  weight = 0 while CFNAI_3mma (1-month lag) < -0.70
    B  weight *= 0.5 while CFNAI_3mma (1-month lag) < -0.70
    C  3-of-4 vote {SMA200, TSM252, DON126, CFNAI_3mma >= -0.70}, on iff >= 3

Input: FRED `CFNAI` (monthly, 1967-03 ->), registered by this branch as extra key `cfnai`
(`research/plugins_c3/fetch_cfnai.py`, URL + SHA-256 in `research/batch_screen/EXTRA_DATA.json`).

Readings fixed here:

* **3mma and lag.** FRED stamps each reference month on its first day. At a session of calendar
  month M the arm uses ``CFNAI_3mma(M-1) = mean(CFNAI(M-1), CFNAI(M-2), CFNAI(M-3))`` -- the
  spec's "1-month lag" read literally (reference month M-1 in force from the first session of
  month M; it is the Chicago Fed's own CFNAI-MA3 definition). Caveats, stated not corrected: the
  real release of month M-1 comes out late in month M (so this is up to ~3 weeks early), and FRED
  serves the current vintage, not the real-time one.
* **Computed on the series' own monthly grid**, then looked up by the month of the session each
  column replays -- the panel's own session on a real window, the drawn source session
  (`extra_days[extra_index]`) inside a bootstrap chunk. A forward-filled date alignment would be
  wrong for a monthly series (a month whose first day is not a session would never appear).
* **Missing -> neutral.** Where the 3mma is undefined (before 1967-06, i.e. the first 1957-1967
  decade of `GSPC_1957_1999`) A/B do not cut and C counts the 4th vote as passing, i.e. the
  production 2-of-3 rule.
* **Availability.** A window (or bootstrap chunk) on which the state is nowhere defined raises
  `NotApplicable` -- `M2` (^GSPC 1935-1956), as the spec's `oos.evaluable` (GSPC_1957_1999, M1,
  M3, M4, M5) foresees. The non-US windows M3/M4/M5 are applicable because the spec lists them.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": -0.70, "ma_months": 3,
          "lag_months": 1, "mode": "scale", "factor": 0.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": -0.70, "ma_months": 3,
          "lag_months": 1, "mode": "scale", "factor": 0.5},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": -0.70, "ma_months": 3,
          "lag_months": 1, "mode": "vote4", "need": 3},
}

KEY = "cfnai"
_GRID = {}


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


def cfnai_3mma_on_sessions(data, spec):
    """`(P, T)` CFNAI moving average of the `lag`-month-old reference month, NaN where undefined."""
    m0, v = monthly_grid(_ext.resolve_series(KEY, data.extra_window or data.window))
    n = int(spec["ma_months"])
    ma = np.full(v.size, np.nan)
    if v.size >= n:
        ma[n - 1:] = np.lib.stride_tricks.sliding_window_view(v, n).mean(axis=1)
    m = source_days(data).astype("datetime64[M]").astype(np.int64)
    k = m - int(spec["lag_months"]) - m0
    ok = (k >= 0) & (k < ma.size)
    return np.where(ok, ma[np.clip(k, 0, ma.size - 1)], np.nan)


def targets(data, spec):
    ma = cfnai_3mma_on_sessions(data, spec)
    if not np.isfinite(ma).any():
        raise NotApplicable("CFNAI (FRED, monthly from 1967-03) has no 3-month average in this window")
    known = np.isfinite(ma)
    weak = known & (np.nan_to_num(ma, nan=0.0) < float(spec["threshold"]))
    price = data.price
    comps = fe_signals.early_components(price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(price).astype(int)
        d = fe_signals.don_up(price, DON_N).astype(int)
        vote = (s + t + d + (~weak).astype(int)) >= int(spec["need"])
        on_eff, factor = fe_signals.r2_state(price, vote, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * np.where(weak, float(spec["factor"]), 1.0)
