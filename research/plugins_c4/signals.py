"""plugins_c4 -- the native-calendar signal statistics of the IC09/IC14/IC19/IC29/IC51 plugins.

Why these are precomputed (and registered as *derived* extra series) instead of being computed
inside `targets()` on the panel:

Every statistic below has a trailing window (26 weeks, 60/126/252 sessions) far longer than the
bootstrap's 20-session block. Inside a bootstrap chunk `data.extra(key)` returns the value of the
*drawn source session* of each column (`batch_screen.api.MarketData.extra`), so consecutive columns
of an extra *price level* come from unrelated dates at every block boundary: a 126-session return
or a 252-session median computed over the chunk would mix levels of different eras (WTI 1999 vs
2022, claims 1969 vs 2020) and be meaningless. The framework's own convention for an exogenous
state is the ^VIX one -- the chunk replays the source session's *value*. These modules therefore
compute each idea's state statistic once, on the source series' own calendar, and register it as a
daily series; the chunk then replays the drawn session's true historical state, exactly as it
replays ^VIX. On a real window the value on each session is the one known at that date.

Every series is expanded to **every calendar day** (the last native value carried forward, from the
first defined value to the last native date + the publication lag), so any session label of any
window (NYSE, Tokyo, Frankfurt, London) or any drawn source session finds its value by exact date.

All parameters are the idea bank's frozen arm text (`docs/research/idea_bank_v2.json` on main);
nothing here reads a market return of the strategy, a NAV, or a result file.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# ----------------------------------------------------------------------------- frozen parameters
#: IC14: 4-week mean of weekly claims vs its own 26-week minimum; usable 1 week after week-end
ICSA_MEAN_W, ICSA_MIN_W, ICSA_LAG_DAYS = 4, 26, 7
#: IC19: WTI 126-session return and 252-session median (native FRED observation sessions)
OIL_RET_N, OIL_MED_N = 126, 252
#: IC29: 126-session total-return relative strength EEM vs SPY
RS_N = 126
#: IC09/IC51: 60-session pairwise correlation, 20-session mean, 252-session median normalisation
CORR_N, SMOOTH_N, MED_N = 60, 20, 252
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")


# ----------------------------------------------------------------------------- 1-D helpers
def trailing_mean(a, n):
    """Trailing `n`-observation mean (window includes the current one), NaN unless all finite."""
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    if a.size < n:
        return out
    w = sliding_window_view(a, n)
    out[n - 1 :] = w.mean(axis=1)  # NaN propagates: a window with a gap stays NaN
    return out


def trailing_min(a, n):
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    if a.size < n:
        return out
    w = sliding_window_view(a, n)
    m = w.min(axis=1)
    m[np.isnan(w).any(axis=1)] = np.nan
    out[n - 1 :] = m
    return out


def trailing_median(a, n):
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    if a.size < n:
        return out
    w = sliding_window_view(a, n)
    m = np.median(w, axis=1)
    m[np.isnan(w).any(axis=1)] = np.nan
    out[n - 1 :] = m
    return out


def trailing_return(a, n):
    """`a_t / a_{t-n} - 1` over native observations, NaN for the first `n`."""
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    out[n:] = a[n:] / a[:-n] - 1.0
    return out


# ----------------------------------------------------------------------------- the statistics
def icsa_shock(values):
    """IC14: `mean4 / min(mean4 over 26 weeks) - 1` on the weekly native observations."""
    m4 = trailing_mean(values, ICSA_MEAN_W)
    mn = trailing_min(m4, ICSA_MIN_W)
    return m4 / mn - 1.0


def oil_state(values):
    """IC19: `(r_oil(126), WTI - median252(WTI))` on the native observations."""
    v = np.asarray(values, dtype=float)
    return trailing_return(v, OIL_RET_N), v - trailing_median(v, OIL_MED_N)


def rel_strength(eem, spy):
    """IC29: `r_EEM(126) - r_SPY(126)` on the common native sessions."""
    return trailing_return(eem, RS_N) - trailing_return(spy, RS_N)


def sector_stats(prices):
    """IC09/IC51 on the nine SPDRs' common sessions: `prices` is `(N, 9)` adjusted closes.

    Returns a dict of `(N,)` arrays:

    * `corr`      20-session mean of the average pairwise 60-session Pearson correlation of the
                  nine daily simple returns (36 pairs; every window needs 60 finite returns)
    * `corr_rel`  `corr / median252(corr)`
    * `disp`      20-session mean of the cross-sectional sample stdev (ddof = 1) of the nine daily
                  returns
    * `disp_rel`  `disp / median252(disp)`
    """
    p = np.asarray(prices, dtype=float)
    N, K = p.shape
    r = np.full((N, K), np.nan)
    r[1:] = p[1:] / p[:-1] - 1.0
    # rolling Pearson correlation over CORR_N returns via windowed sums (exact float formula)
    pc = np.full(N, np.nan)
    if N > CORR_N:
        w = sliding_window_view(r, CORR_N, axis=0)  # (N - n + 1, K, n)
        mu = w.mean(axis=2, keepdims=True)
        d = w - mu
        cov = np.einsum("tin,tjn->tij", d, d)  # (., K, K), common 1/(n-1) cancels
        sd = np.sqrt(np.einsum("tii->ti", cov))
        corr = cov / (sd[:, :, None] * sd[:, None, :])
        pairs = list(combinations(range(K), 2))
        ii = np.array([a for a, _ in pairs])
        jj = np.array([b for _, b in pairs])
        pc[CORR_N - 1 :] = corr[:, ii, jj].mean(axis=1)
    disp_raw = np.std(r, axis=1, ddof=1)
    disp_raw[np.isnan(r).any(axis=1)] = np.nan
    corr_s = trailing_mean(pc, SMOOTH_N)
    disp_s = trailing_mean(disp_raw, SMOOTH_N)
    return {
        "corr": corr_s,
        "corr_rel": corr_s / trailing_median(corr_s, MED_N),
        "disp": disp_s,
        "disp_rel": disp_s / trailing_median(disp_s, MED_N),
    }


# ----------------------------------------------------------------------------- calendar expansion
def to_calendar(dates, cols: dict, lag_days: int = 0):
    """Expand native-dated columns to every calendar day: value known on `date + lag_days`,
    carried forward to the next native observation; from the first date on which *any* column is
    defined to the last native date + `lag_days`. Returns `(days, {name: values})`."""
    d = [date.fromisoformat(x) + timedelta(days=lag_days) for x in dates]
    names = sorted(cols)
    arr = np.column_stack([np.asarray(cols[n], dtype=float) for n in names])
    have = np.isfinite(arr).any(axis=1)
    if not have.any():
        return [], {n: [] for n in names}
    first = d[int(np.argmax(have))]
    last = d[-1]
    span = (last - first).days + 1
    days = [first + timedelta(days=k) for k in range(span)]
    pos = np.searchsorted(np.array([x.toordinal() for x in d]),
                          np.array([x.toordinal() for x in days]), side="right") - 1
    out = {n: arr[pos, i] for i, n in enumerate(names)}
    return [x.isoformat() for x in days], out


__all__ = [
    "CORR_N", "ICSA_LAG_DAYS", "ICSA_MEAN_W", "ICSA_MIN_W", "MED_N", "OIL_MED_N", "OIL_RET_N",
    "RS_N", "SECTORS", "SMOOTH_N", "icsa_shock", "oil_state", "rel_strength", "sector_stats",
    "to_calendar", "trailing_mean", "trailing_median", "trailing_min", "trailing_return",
]
