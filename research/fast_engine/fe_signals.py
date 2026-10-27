"""fe_signals -- the production signal/target pipeline, vectorised in numpy over paths.

`trend_robustness_v2.sma_up` / `vol_n` / `vt_of` / `synthetic_2x` and
`band_h2_combo_v1.e2_up` / `early_components` / `r2_state` / `sized_wants` / `arm_signals` /
`wants_of`, ported unchanged. Every routine accepts a `[paths, T]` batch (time on the last axis),
so a bootstrap chunk is turned into target-weight arrays with one call per stage instead of one
call per path; each row is computed independently, so batching cannot change a value
(parity.py asserts bitwise equality against the per-path Python calls).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange
from numpy.lib.stride_tricks import sliding_window_view

from .fe_config import CAP, DON_N, LAG_FAST, MED_WINDOW, SMA_FAST, SMA_N, TSM_N, VOL_N, VOL_TARGET


# ----------------------------------------------------------------------------- market mechanics
def synthetic_2x(q_open, q_close, q_div, y, expense):
    """`trend_robustness_v2.synthetic_2x`: daily-reset 2x on total return."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    g_cc = 1 + 2 * r_cc - (expense + y[..., :-1]) / 252
    close = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), close[..., :-1] * (1 + 2 * r_on)], axis=-1)
    return opn, close


def sma_up(c, n):
    """`trend_robustness_v2.sma_up`: close[t] > n-session SMA ending at t. NaN -> False."""
    c = np.atleast_2d(c)
    cs = np.cumsum(c, axis=1)
    sma = np.full_like(c, np.nan)
    sma[:, n - 1 :] = (cs[:, n - 1 :] - np.concatenate([np.zeros((c.shape[0], 1)), cs[:, :-n]], axis=1)) / n
    return c > sma


def vol_n(a, n=VOL_N):
    """`trend_robustness_v2.vol_n`: annualized sample vol of the n returns ending at t."""
    a = np.atleast_2d(a)
    r = a[:, 1:] / a[:, :-1] - 1
    vol = np.full_like(a, np.nan)
    z = np.zeros((a.shape[0], 1))
    s1 = np.concatenate([z, np.cumsum(r, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(r * r, axis=1)], axis=1)
    w1 = s1[:, n:] - s1[:, :-n]
    w2 = s2[:, n:] - s2[:, :-n]
    vol[:, n:] = np.sqrt(np.maximum(w2 - w1 * w1 / n, 0) / (n - 1) * 252)
    return vol


def vt_of(vol):
    """`trend_robustness_v2.vt_of`: vt(vol) = min(1, 0.35/vol); vol <= 0 -> 1.0."""
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, VOL_TARGET / np.where(v > 0, v, 1.0)), 1.0)


# ----------------------------------------------------------------------------- the E2 vote
def tsm_up_price(c, n=TSM_N):
    """`screen.tsm_up_price`: 252-session price return > 0."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    """`screen.don_up`: close > midpoint of the n-session Donchian channel."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1 :] = c[:, n - 1 :] > mid
    return up


def e2_up(price):
    """`screen.e2_up`: at least 2 of SMA200, 252-session price return > 0, DON126."""
    s = sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


# ----------------------------------------------------------------------------- the R2 early leg
def _rolling_median_vol(price_vol, window=MED_WINDOW, block=32):
    """`screen._rolling_median_vol`: trailing rolling median of the vol series, NaN until full."""
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    if T < window:
        return out
    for s in range(0, P, block):
        e = min(P, s + block)
        blk = price_vol[s:e]
        fin = np.isfinite(blk)
        first = np.argmax(fin, axis=1)
        for r in range(e - s):
            if np.any(np.diff(fin[r].astype(np.int8)) < 0):
                raise ValueError("interior NaN in the vol series; use the reference median")
        j0 = int(first.min())
        if T - j0 < window:
            continue
        out[s:e, j0 + window - 1 :] = np.median(sliding_window_view(blk[:, j0:], window, axis=1), axis=2)
    return out


@njit(cache=True, nogil=True)
def _median_row(vol_row, window, out_row):
    """Trailing `window`-median of one series, by maintaining the window sorted; NaN until full.

    The two middle order statistics of the window are the same values `np.median` picks, so the
    mean of them is the same float -- but this walks the window by one element per session
    instead of copying and partitioning a `(rows, T, window)` array.
    """
    T = vol_row.shape[0]
    f = 0
    while f < T and not np.isfinite(vol_row[f]):
        f += 1
    if T - f < window:
        return
    buf = np.empty(window, dtype=np.float64)  # the window's values, oldest at c % window
    srt = np.empty(window, dtype=np.float64)  # the same multiset, sorted
    n = 0
    half = window // 2
    for c in range(f, T):
        v = vol_row[c]
        if n == window:
            old = buf[c % window]
            lo, hi = 0, n
            while lo < hi:  # lower_bound: any equal element will do
                mid = (lo + hi) // 2
                if srt[mid] < old:
                    lo = mid + 1
                else:
                    hi = mid
            for k in range(lo, n - 1):
                srt[k] = srt[k + 1]
            n -= 1
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if srt[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        for k in range(n, lo, -1):
            srt[k] = srt[k - 1]
        srt[lo] = v
        n += 1
        buf[c % window] = v
        if n == window:
            if window % 2:
                out_row[c] = srt[half]
            else:
                out_row[c] = (srt[half - 1] + srt[half]) / 2


@njit(parallel=True, cache=True, nogil=True)
def _median_vol_nb(price_vol, window, out):
    """`_rolling_median_vol` over a whole panel, one row per `prange` iteration."""
    P = price_vol.shape[0]
    for r in prange(P):
        _median_row(price_vol[r], window, out[r])


def check_vol_series(price_vol):
    """`_rolling_median_vol`'s guard: no finite vol value may be followed by a non-finite one."""
    fin = np.isfinite(price_vol)
    if np.any(np.diff(fin.astype(np.int8), axis=1) < 0):
        raise ValueError("interior NaN in the vol series; use the reference median")


def rolling_median_vol(price_vol, window=MED_WINDOW):
    """`screen._rolling_median_vol` (values bit-identical, see parity), via numba."""
    price_vol = np.atleast_2d(np.asarray(price_vol, dtype=float))
    check_vol_series(price_vol)
    out = np.full(price_vol.shape, np.nan)
    if price_vol.shape[1] >= window:
        _median_vol_nb(np.ascontiguousarray(price_vol), window, out)
    return out


def early_components(price):
    """`screen.early_components`: SMA50, price vol20, its trailing 252-session median, momentum."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = sma_up(price, SMA_FAST)
    vol_p = vol_n(price)
    med_p = rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    return sma50_up, vol_p, med_p, lag_cond


def r2_state(price, e2_on, comps=None):
    """`screen.r2_state`: (on_eff, factor) of the E2 vote plus the half-weight early re-entry."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = early_components(price) if comps is None else comps
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t]
        early_active = np.where(e2t, False, early_active)
        exit_cond = ~sma50_up[:, t]
        entry_cond = sma50_up[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def sized_wants(on_eff, factor, vol_instr):
    """`screen.sized_wants`: min(1, 0.35/vol20) x the path factor, total cap 0.98."""
    vt = vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def arm_signals(price, e2_on, vol_instr, arm, comps=None):
    """`screen.arm_signals`: target weights of the R2 control ("R2"/"C0") or the E2 anchor ("E2")."""
    if arm not in ("R2", "C0", "E2"):
        raise ValueError(f"unknown arm label {arm!r}")
    if arm == "E2":
        on_eff = np.atleast_2d(np.asarray(e2_on, dtype=bool)).copy()
        factor = np.ones_like(on_eff, dtype=float)
    else:
        on_eff, factor = r2_state(price, e2_on, comps)
    return sized_wants(on_eff, factor, vol_instr), None


def wants_of(price, e2, vol_instr, comps=None):
    """`screen.wants_of`: (E2 wants, R2 wants) for one price series, one state loop for the pair."""
    comps = early_components(price) if comps is None else comps
    on_eff, factor = r2_state(price, e2, comps)
    e2_arr = np.atleast_2d(np.asarray(e2, dtype=bool))
    return (sized_wants(e2_arr, np.ones_like(factor), vol_instr), sized_wants(on_eff, factor, vol_instr))


__all__ = [
    "synthetic_2x",
    "sma_up",
    "vol_n",
    "vt_of",
    "tsm_up_price",
    "don_up",
    "e2_up",
    "early_components",
    "r2_state",
    "sized_wants",
    "arm_signals",
    "wants_of",
    "rolling_median_vol",
    "check_vol_series",
]
