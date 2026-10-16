"""research/plugins_c1/c1_lib.py -- shared vectorised helpers for the IC01..IC60 plugins.

Every helper is numpy over a `(P, T)` panel (one path on a real window, a bootstrap chunk
otherwise), no Python loop over sessions and none over paths except a bounded block over paths where
a temporary would otherwise be `(P, T, window)` sized.  The production pieces (`e2_up`, `don_up`,
`r2_state`, `sized_wants`, `vol_n`) are called, never re-implemented, except `r2_state_gated` --
`fe_signals.r2_state`'s loop with one extra entry condition, which the check script proves reduces
to the engine's own function when the gate is `True` everywhere.

Calendar helpers (`day_labels`, `last_sessions_of_year`) follow the framework's extra-series
convention: on a plain window the panel's own `days` are the labels, inside a bootstrap chunk the
labels are the **drawn source sessions** `extra_days[extra_index]` (see `batch_screen.extra.aligned`
and `plugins_b2/README.md` finding 1 -- `data.days` in a chunk is the chunk's synthesised label
calendar and is one column shorter than the market arrays, so it cannot be indexed against them).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP, MED_WINDOW, SMA_N, VOL_N, VOL_TARGET

#: how many paths one trailing-window reduction handles at once (bounds the strided temporary)
BLOCK = 32


# ----------------------------------------------------------------------------- calendar
def day_labels(data) -> np.ndarray:
    """`(P, T)` ISO session labels aligned to the panel's market columns."""
    if data.extra_index is None:
        return np.asarray(data.days, dtype="<U10")[None, :]
    pool = np.asarray(data.extra_days, dtype="<U10")
    return pool[np.asarray(data.extra_index)]


def account_labels(data) -> np.ndarray:
    """`(T,)` labels of the **account calendar** the engine simulates the panel's columns on.

    `fe_sim.sim_one` reads market column `i` together with calendar day `i` (`oq[i]`, `dec_tail[i]`,
    `wq[i - 1]`), so the account label of column `i` is `data.days[i]`: on a plain window `days` has
    the panel's width; in the clean bootstrap it is the fixed 2015-09 -> 2026-09 calendar, one entry
    shorter than the chunk (the last column lies beyond `i1` and is never filled -- it repeats the
    last label here); in the OOS pooled bootstrap it is the market's whole session list, of which
    the first `T` entries are the chunk's calendar.
    """
    days = np.asarray(data.days, dtype="<U10")
    T = int(data.T)
    if len(days) >= T:
        return days[:T]
    return np.concatenate([days, np.repeat(days[-1:], T - len(days))])


def december_tail_mask(labels, tail: int) -> np.ndarray:
    """`(T,)` True on the last `tail` December sessions of each year of `labels` -- the same set
    `fe_sim.december_tail` (H2's window) builds, over the label list as given."""
    lab = np.asarray(labels, dtype="<U10")
    T = len(lab)
    year = lab.astype("datetime64[Y]").astype(np.int64)
    dec = (lab.astype("datetime64[M]").astype(np.int64) % 12) == 11
    out = np.zeros(T, dtype=bool)
    if tail <= 0 or not dec.any():
        return out
    # rank of each December session counted from the end of its year's December run
    idx = np.arange(T)
    key = np.where(dec, year, -1)
    later = np.zeros(T, dtype=np.int64)
    for y in np.unique(year[dec]):  # one entry per calendar year (<= 30), not per session
        sel = idx[key == y]
        later[sel] = np.arange(len(sel))[::-1]
    out[dec] = later[dec] < tail
    return out


def year_month(data) -> tuple:
    """`(P, T)` calendar year and month of each session label."""
    lab = day_labels(data)
    year = lab.astype("datetime64[Y]").astype(np.int64) + 1970
    month = lab.astype("datetime64[M]").astype(np.int64) % 12 + 1
    return year, month


def last_sessions_of_year(year, n: int) -> np.ndarray:
    """`(P, T)` True on the last `n` sessions of the session's own calendar year.

    Runs of a year are contiguous and `year` is non-decreasing along the row, so the index of the
    next year's first session is the nearest later position where the year changes.
    """
    year = np.atleast_2d(np.asarray(year))
    P, T = year.shape
    idx = np.arange(T)
    nxt = np.full((P, T), T, dtype=np.int64)
    if T > 1:
        change = np.concatenate([year[:, 1:] != year[:, :-1], np.zeros((P, 1), dtype=bool)], axis=1)
        nxt = np.where(change, idx, T)
        nxt = np.minimum.accumulate(nxt[:, ::-1], axis=1)[:, ::-1]
    return (nxt - 1 - idx) < n


# ----------------------------------------------------------------------------- trailing stats
def lag(a, n: int) -> np.ndarray:
    """`(P, T)` shifted `n` sessions forward (NaN in the leading `n` columns)."""
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    out = np.full_like(a, np.nan)
    if n < a.shape[1]:
        out[:, n:] = a[:, :-n]
    return out


def trailing_max(a, n: int, block: int = BLOCK) -> np.ndarray:
    """`(P, T)` trailing `n`-session maximum *including* the session (NaN until the window is
    complete)."""
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    P, T = a.shape
    out = np.full_like(a, np.nan)
    for s in range(0, P, block):
        e = min(P, s + block)
        if T >= n:
            out[s:e, n - 1:] = np.max(np.lib.stride_tricks.sliding_window_view(a[s:e], n, axis=1),
                                      axis=2)
    return out


def trailing_min(a, n: int, block: int = BLOCK) -> np.ndarray:
    """`(P, T)` trailing `n`-session minimum including the session (NaN until complete)."""
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    P, T = a.shape
    out = np.full_like(a, np.nan)
    for s in range(0, P, block):
        e = min(P, s + block)
        if T >= n:
            out[s:e, n - 1:] = np.min(np.lib.stride_tricks.sliding_window_view(a[s:e], n, axis=1),
                                      axis=2)
    return out


def trailing_log_sum(close, n: int) -> np.ndarray:
    """`(P, T)` sum of the last `n` daily log returns of `close` (NaN until it is complete)."""
    c = np.atleast_2d(np.asarray(close, dtype=np.float64))
    r = np.zeros_like(c)
    r[:, 1:] = np.log(c[:, 1:] / c[:, :-1])
    cs = np.cumsum(r, axis=1)
    out = np.full_like(c, np.nan)
    if c.shape[1] > n:
        out[:, n:] = cs[:, n:] - cs[:, :-n]
    return out


def trailing_sum(r, n: int) -> np.ndarray:
    """`(P, T)` sum of the last `n` sessions of a per-session series `r` (NaN until complete).

    NaN entries are treated as 0 in the running sum, so a series whose leading value is undefined
    behaves like a return series; the returned value is NaN wherever the window would reach into
    the leading column.
    """
    r = np.atleast_2d(np.asarray(r, dtype=np.float64))
    cs = np.cumsum(np.nan_to_num(r, nan=0.0), axis=1)
    out = np.full_like(r, np.nan)
    if r.shape[1] > n:
        out[:, n:] = cs[:, n:] - cs[:, :-n]
    return out


def trailing_tail_mean(r, n: int, k: int, block: int = BLOCK) -> np.ndarray:
    """`(P, T)` mean of the `k` smallest values of the trailing `n`-session window of `r`.

    NaN until the window is complete; `np.partition` picks the order statistics (the mean of the
    `k` smallest is what a "mean of the worst k/n" tail statistic is, with no interpolation).
    """
    r = np.atleast_2d(np.asarray(r, dtype=np.float64))
    P, T = r.shape
    out = np.full_like(r, np.nan)
    for s in range(0, P, block):
        e = min(P, s + block)
        if T < n:
            break
        win = np.lib.stride_tricks.sliding_window_view(r[s:e], n, axis=1)
        part = np.partition(win, k - 1, axis=2)[..., :k]
        out[s:e, n - 1:] = part.mean(axis=2)
    return out


def log_returns(close) -> np.ndarray:
    """`(P, T)` daily log returns of `close` (0 on the first column)."""
    c = np.atleast_2d(np.asarray(close, dtype=np.float64))
    r = np.zeros_like(c)
    r[:, 1:] = np.log(c[:, 1:] / c[:, :-1])
    return r


# ----------------------------------------------------------------------------- the E2 vote
def vote_2_of_3(price, third) -> np.ndarray:
    """`{SMA200, `third`, DON126} >= 2` -- `fe_signals.e2_up`'s shape with a substituted vote."""
    s = fe_signals.sma_up(price, SMA_N).astype(np.int8)
    t = np.atleast_2d(np.asarray(third, dtype=bool)).astype(np.int8)
    d = fe_signals.don_up(price).astype(np.int8)
    return (s + t + d) >= 2


def vote_3_of_4(price, fourth) -> np.ndarray:
    """`{SMA200, TSM252, DON126, `fourth`} >= 3`."""
    s = fe_signals.sma_up(price, SMA_N).astype(np.int8)
    t = fe_signals.tsm_up_price(price).astype(np.int8)
    d = fe_signals.don_up(price).astype(np.int8)
    e = np.atleast_2d(np.asarray(fourth, dtype=bool)).astype(np.int8)
    return (s + t + d + e) >= 3


# ----------------------------------------------------------------------------- sizing
def sized_wants_vt(on_eff, factor, vt) -> np.ndarray:
    """`fe_signals.sized_wants` with an explicit annualised-vol array in place of `0.35/vol20`.

    Same arithmetic as the engine's function (`raw = on x factor x vt`, then the 0.98 total cap),
    so passing `fe_signals.vt_of(data.vol)` reproduces `sized_wants` bit for bit.
    """
    raw = (np.atleast_2d(np.asarray(on_eff, dtype=np.float64))
           * np.atleast_2d(np.asarray(factor, dtype=np.float64))
           * np.atleast_2d(np.asarray(vt, dtype=np.float64)))
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def vt_of_denominator(den) -> np.ndarray:
    """`min(1, 0.35 / den)`, `1.0` where the estimate is undefined or non-positive.

    The engine's `vt_of` convention (`fe_signals.vt_of`): a zero/NaN vol estimate is treated as no
    leverage limit rather than as a division by zero.
    """
    d = np.atleast_2d(np.asarray(den, dtype=np.float64))
    ok = np.isfinite(d) & (d > 0)
    return np.where(ok, np.minimum(1.0, VOL_TARGET / np.where(ok, d, 1.0)), 1.0)


# ----------------------------------------------------------------------------- the R2 state
def r2_state_gated(price, e2_on, entry_gate, comps=None):
    """`fe_signals.r2_state` with the early path's *entry* condition AND-ed with `entry_gate`.

    Production's loop clears `early_active` when E2 is on, clears it on `~sma50_up`, and enters on
    `~early & ~e2 & finite(med) & production's conditions`; the last two can never fire on the same
    session (entry needs `sma50_up`, the exits need `~sma50_up` or `e2`), so the state is exactly
    "the last entry is more recent than the last reset" and the whole thing is a vectorised
    accumulate.  `entry_gate == True` everywhere reproduces `fe_signals.r2_state` bit for bit.
    """
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    gate = np.atleast_2d(np.asarray(entry_gate, dtype=bool))
    sma50_up, vol_p, med_p, lag_cond = fe_signals.early_components(price) if comps is None else comps
    entry = (~e2_on) & sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond & gate
    reset = e2_on | (~sma50_up)
    idx = np.arange(price.shape[1])
    last_entry = np.maximum.accumulate(np.where(entry, idx, -1), axis=1)
    last_reset = np.maximum.accumulate(np.where(reset, idx, -1), axis=1)
    active = last_entry > last_reset
    return e2_on | active, np.where(e2_on, 1.0, np.where(active, 0.5, 0.0))


__all__ = [
    "BLOCK",
    "account_labels",
    "december_tail_mask",
    "MED_WINDOW",
    "VOL_N",
    "day_labels",
    "lag",
    "last_sessions_of_year",
    "log_returns",
    "r2_state_gated",
    "sized_wants_vt",
    "trailing_log_sum",
    "trailing_sum",
    "trailing_max",
    "trailing_min",
    "trailing_tail_mean",
    "vote_2_of_3",
    "vote_3_of_4",
    "vt_of_denominator",
    "year_month",
]
