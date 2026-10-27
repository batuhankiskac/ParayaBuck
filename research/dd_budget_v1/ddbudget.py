"""dd_budget_v1 -- a drawdown-based risk budget on the UNDERLYING (QQQ) signal, signal code only.

Pre-registered in PREREGISTRATION.md. Three parts:

- `drawdown_budgets(price)`: for each session t, over the trailing 252 closes (t-251 .. t), the depth
  d_i = 1 - P_i / max(P_{t-251..i}) of every close in the window (running peak from the window start).
  Two budget measures, both known at the close t:
    * Ulcer Index  UI_t  = sqrt(mean_i d_i^2)
    * CDaR 95 %    CD_t  = mean of the worst 13 depths (ceil(0.05 x 252) = 13) in the window.
- `expanding_median(x)`: median of all finite values of x up to and including t (no look-ahead).
- `budget_ratio(med, cur)`: clip(median_budget / current_budget, 0.6, 1.2); cur <= 0 -> 1.2.
- `monthly_hold(x, mth)`: value of x at the last session of the most recent calendar month that
  ends on or before t (the U3 "updated monthly only" rule).
- `arm_ratios(price, mth)` and `weights_from_ratios(...)`: target = 0.35 x ratio; weight =
  on x factor x min(1, target / vol20), cap 0.98 (K's sizing with a time-varying target); warm-up
  (t < 252, or no finite ratio) keeps K's own weight.

Only signal arithmetic lives here (numba kernels for the rolling window and the expanding median, numpy
elsewhere). Simulation goes through fe_sim.simulate and the bootstrap through fe_boot._boot_chunk.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine.fe_config import CAP, VOL_TARGET
from research.fast_engine.fe_signals import vol_n

WIN = 252                   # trailing window, sessions
TAIL_N = 13                 # ceil(0.05 * 252): worst 5 % of the window's depths (CDaR 95 %)
WARM = 252                  # no U arm leaves K's weight before t = 252 (same convention as slow_vol_blend_v1)
CLIP_LO, CLIP_HI = 0.6, 1.2
ARM_NAMES = ("U1", "U2", "U3")


@njit(cache=True, nogil=True)
def _dd_row(p, win, tail, ui, cd):
    T = p.shape[0]
    buf = np.empty(win)
    for t in range(T):
        if t < win - 1:
            ui[t] = np.nan
            cd[t] = np.nan
            continue
        s = t - win + 1
        peak = p[s]
        ss = 0.0
        for j in range(win):
            x = p[s + j]
            if x > peak:
                peak = x
            d = 1.0 - x / peak
            buf[j] = d
            ss += d * d
        ui[t] = np.sqrt(ss / win)
        srt = np.sort(buf)
        tot = 0.0
        for j in range(win - tail, win):
            tot += srt[j]
        cd[t] = tot / tail


@njit(parallel=True, cache=True, nogil=True)
def _dd_panel(P, win, tail, ui, cd):
    for r in prange(P.shape[0]):
        _dd_row(P[r], win, tail, ui[r], cd[r])


def drawdown_budgets(price):
    """(Ulcer Index, CDaR 95 %) of each row of a [paths, T] price panel; NaN before the window is full."""
    P = np.ascontiguousarray(np.atleast_2d(np.asarray(price, dtype=float)))
    ui = np.full(P.shape, np.nan)
    cd = np.full(P.shape, np.nan)
    _dd_panel(P, WIN, TAIL_N, ui, cd)
    return ui, cd


@njit(cache=True, nogil=True)
def _exp_median_row(x, out):
    T = x.shape[0]
    buf = np.empty(T)
    n = 0
    for t in range(T):
        v = x[t]
        if np.isnan(v):
            out[t] = np.nan
            continue
        lo = 0
        hi = n
        while lo < hi:                       # first position whose value is > v (sorted insert)
            mid = (lo + hi) // 2
            if buf[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        j = n
        while j > lo:
            buf[j] = buf[j - 1]
            j -= 1
        buf[lo] = v
        n += 1
        if n % 2 == 1:
            out[t] = buf[n // 2]
        else:
            out[t] = 0.5 * (buf[n // 2 - 1] + buf[n // 2])


@njit(parallel=True, cache=True, nogil=True)
def _exp_median_panel(X, out):
    for r in prange(X.shape[0]):
        _exp_median_row(X[r], out[r])


def expanding_median(x):
    """Median of all finite values of each row up to and including t (NaN where x is NaN)."""
    X = np.ascontiguousarray(np.atleast_2d(np.asarray(x, dtype=float)))
    out = np.full(X.shape, np.nan)
    _exp_median_panel(X, out)
    return out


def budget_ratio(med, cur):
    """clip(median_budget / current_budget, 0.6, 1.2); a zero current budget takes the top of the clip."""
    med = np.asarray(med, dtype=float)
    cur = np.asarray(cur, dtype=float)
    pos = cur > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(pos, med / np.where(pos, cur, 1.0), CLIP_HI)
    return np.clip(raw, CLIP_LO, CLIP_HI)


def monthly_hold(x, mth):
    """Value of x at the last session of the most recent month ending on or before t (NaN if none).

    `mth` is the dense month index per session of the path's calendar (fe_tax.month_index); a session is a
    month end when the next session has a different month index. The last session is not a month end.
    """
    x = np.atleast_2d(np.asarray(x, dtype=float))
    mth = np.asarray(mth)
    T = x.shape[1]
    is_me = np.zeros(T, dtype=bool)
    is_me[:-1] = mth[:-1] != mth[1:]
    last = np.maximum.accumulate(np.where(is_me, np.arange(T), -1))
    out = np.full(x.shape, np.nan)
    ok = last >= 0
    out[:, ok] = x[:, last[ok]]
    return out


def arm_ratios(price, mth):
    """Budget ratios of U1 (UI, daily), U2 (CDaR 95 %, daily) and U3 (UI, held monthly). Price-only, so
    the bootstrap can compute them once per chunk and reuse them for both financings."""
    ui, cd = drawdown_budgets(price)
    r1 = budget_ratio(expanding_median(ui), ui)
    r2 = budget_ratio(expanding_median(cd), cd)
    return {"U1": r1, "U2": r2, "U3": monthly_hold(r1, mth)}


def sized_target(on, factor, sigma, target):
    """K's sizing with a given vol estimate and a (possibly time-varying) target: on x factor x min(1, target / sigma),
    cap CAP. sigma <= 0 or NaN -> vt 1 (as fe_signals.vt_of)."""
    on = np.atleast_2d(np.asarray(on, dtype=float))
    factor = np.atleast_2d(np.asarray(factor, dtype=float))
    sigma = np.atleast_2d(np.asarray(sigma, dtype=float))
    target = np.broadcast_to(np.asarray(target, dtype=float), sigma.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        vt = np.where(sigma > 0, np.minimum(1.0, target / np.where(sigma > 0, sigma, 1.0)), 1.0)
    return np.minimum(CAP, on * factor * vt)


def weights_from_ratios(ratios, c, on, factor, w_k):
    """Target weights of U1-U3 for a [paths, T] synthetic close panel `c` (vol20 of c, as K); K's weight
    `w_k` wherever the warm-up or a missing ratio applies."""
    vol = vol_n(c)
    t = np.arange(vol.shape[1])
    out = {}
    for a in ARM_NAMES:
        target = VOL_TARGET * ratios[a]
        ok = (t >= WARM)[None, :] & np.isfinite(target) & np.isfinite(vol)
        out[a] = np.where(ok, sized_target(on, factor, vol, np.where(np.isfinite(target), target, VOL_TARGET)), w_k)
    return out
