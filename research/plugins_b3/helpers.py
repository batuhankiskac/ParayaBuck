"""plugins_b3 -- shared vectorised signal helpers for the IB03/IB11/IB15/IB23 plugins.

Pure numpy, `(P, T)` panels (P = 1 on a real window, a bootstrap chunk otherwise), no engine edit:
every helper is a signal construction the plugin contract allows (`targets` returns target
weights; the engine's fill rule is untouched). The per-session recursions are vectorised over
paths (the same shape `fe_signals.r2_state` uses) because an EMA / Wilder ATR / dead-band state is
inherently recursive.
"""

from __future__ import annotations

import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.batch_screen import extra as _extra
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

#: how many paths are median-ed at once (bounds the `sliding_window_view` temporary)
BLOCK = 32


# ----------------------------------------------------------------------------- trailing stats
def _trailing(a, n):
    """`(P, T)` trailing `n`-session sum of a **NaN-free** array (the raw cumsum difference)."""
    P, T = a.shape
    out = np.full((P, T), np.nan)
    if T < n:
        return out
    cs = np.concatenate([np.zeros((P, 1)), np.cumsum(a, axis=1)], axis=1)  # (P, T + 1)
    out[:, n - 1 :] = cs[:, n:] - cs[:, : T - n + 1]
    return out


def _finite_count(a, n):
    """`(P, T)` number of finite values in the trailing `n`-session window (NaN before it is
    full)."""
    return _trailing(np.isfinite(a).astype(np.float64), n)


def trailing_sum(a, n):
    """`(P, T)` trailing `n`-session sum (the window includes the session itself), NaN unless the
    window holds `n` finite values -- so a leading or interior NaN leaves a NaN window rather than
    poisoning every later sum (which a raw `np.cumsum` would do)."""
    a = np.atleast_2d(np.asarray(a, dtype=float))
    fin = np.isfinite(a)
    s = _trailing(np.where(fin, a, 0.0), n)
    return np.where(_finite_count(a, n) == n, s, np.nan)


def trailing_std_full(a, n):
    """`(P, T)` trailing `n`-session sample std (ddof = 1), NaN unless the window is complete.

    "Complete" = the window holds `n` finite values (the engine's own `vol_n` convention of a NaN
    value until the window is full); a window with an interior gap stays NaN rather than being
    reported as the std of the few values it has.
    """
    a = np.atleast_2d(np.asarray(a, dtype=float))
    fin = np.isfinite(a)
    cnt = _finite_count(a, n)
    s1 = _trailing(np.where(fin, a, 0.0), n)
    s2 = _trailing(np.where(fin, a * a, 0.0), n)
    var = np.maximum(s2 - s1 * s1 / n, 0.0) / (n - 1)
    return np.where(cnt == n, np.sqrt(var), np.nan)


def trailing_median_full(a, n, block=BLOCK):
    """`(P, T)` trailing `n`-session median, NaN unless the window holds `n` finite values."""
    a = np.atleast_2d(np.asarray(a, dtype=float))
    P, T = a.shape
    out = np.full((P, T), np.nan)
    if T < n:
        return out
    cnt = _finite_count(a, n)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows: masked below
        for s in range(0, P, block):
            e = min(P, s + block)
            med = np.nanmedian(sliding_window_view(a[s:e], n, axis=1), axis=2)
            ok = cnt[s:e, n - 1 :] == n
            out[s:e, n - 1 :] = np.where(ok, med, np.nan)
    return out


def lag(a, n):
    """`(P, T)` shifted `n` sessions forward (NaN in the leading `n` columns)."""
    a = np.atleast_2d(np.asarray(a, dtype=float))
    out = np.full_like(a, np.nan)
    out[:, n:] = a[:, :-n]
    return out


def session_year(data):
    """`(T,)` calendar year aligned to the panel's *columns*.

    A real window's `days` (and `MarketData.year`) is one entry per column; a bootstrap chunk's
    labels columns `1..T-1` -- column 0 is the artificial base row, which repeats the first drawn
    session (`batch_screen.api.MarketData.extra`) -- so its first label is repeated here.
    """
    year = np.asarray(data.year, dtype=np.int64)
    T = data.price.shape[1]
    if year.size == T:
        return year
    if year.size == T - 1:
        return np.concatenate([year[:1], year])
    raise ValueError(f"the panel's year vector has {year.size} entries for {T} sessions")


def nan_clip_ratio(num, den, lo, hi):
    """`clip(num / den, lo, hi)` element-wise, 1.0 wherever the ratio is undefined.

    The "signal unavailable -> production behaviour" convention every plugins_b3 module uses: a
    1.0 multiplier leaves the production target weight untouched, so a window (or a session) whose
    extra series does not exist behaves exactly like the K control there instead of manufacturing
    a cash position out of a data gap.
    """
    good = np.isfinite(num) & np.isfinite(den) & (den > 0)
    ratio = np.where(good, num / np.where(good, den, 1.0), 1.0)
    return np.clip(ratio, lo, hi)


# ----------------------------------------------------------------------------- recursions
def ema(a, n):
    """`(P, T)` exponential moving average, alpha = 2 / (n + 1), seeded with the mean of the first
    `n` finite values (NaN before that; a NaN input holds the last value, a gap in the seed simply
    takes the first `n` finite observations)."""
    a = np.atleast_2d(np.asarray(a, dtype=float))
    P, T = a.shape
    out = np.full((P, T), np.nan)
    alpha = 2.0 / (n + 1.0)
    state = np.full(P, np.nan)
    acc = np.zeros(P)
    cnt = np.zeros(P, dtype=np.int64)
    for t in range(T):
        v = a[:, t]
        fin = np.isfinite(v)
        val = np.where(fin, v, 0.0)
        acc = acc + val
        cnt += fin
        seed = np.isnan(state) & (cnt >= n)
        state = np.where(seed, acc / n, state)
        upd = fin & ~seed & np.isfinite(state)
        state = np.where(upd, alpha * val + (1.0 - alpha) * state, state)
        out[:, t] = state
    return out


def wilder_atr(high, low, close, n):
    """`(P, T)` Wilder (1978) ATR: `TR = max(h - l, |h - c_prev|, |l - c_prev|)`, seeded with the
    mean of the first `n` finite TRs, then `ATR_t = ((n - 1) ATR_{t-1} + TR_t) / n`.

    A NaN TR (a session whose high/low/previous close is missing) holds the previous ATR; NaN
    before the seed.
    """
    high = np.atleast_2d(np.asarray(high, dtype=float))
    low = np.atleast_2d(np.asarray(low, dtype=float))
    close = np.atleast_2d(np.asarray(close, dtype=float))
    prev = np.concatenate([np.full_like(close[:, :1], np.nan), close[:, :-1]], axis=1)
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    P, T = tr.shape
    out = np.full((P, T), np.nan)
    state = np.full(P, np.nan)
    acc = np.zeros(P)
    cnt = np.zeros(P, dtype=np.int64)
    for t in range(T):
        v = tr[:, t]
        fin = np.isfinite(v)
        val = np.where(fin, v, 0.0)
        acc = acc + val
        cnt += fin
        seed = np.isnan(state) & (cnt >= n)
        state = np.where(seed, acc / n, state)
        upd = fin & ~seed & np.isfinite(state)
        state = np.where(upd, ((n - 1.0) * state + val) / n, state)
        out[:, t] = state
    return out


def keltner_up(price, high, low, ema_n, atr_n, k):
    """`close > EMA(ema_n) + k * ATR(atr_n)` (Keltner channel, Wilder 1978's ATR)."""
    return price > ema(price, ema_n) + k * wilder_atr(high, low, price, atr_n)


# ----------------------------------------------------------------------------- the panel's OHLC
def relative_high_low(data):
    """The `(high, low)` of the panel's own price series, as `(P, T)`.

    The ETF/instrument windows carry their own OHLC in the panel, so those are used verbatim.
    Every other window (the main window, the five OOS markets, a bootstrap chunk) gets the derived
    relative series `underlying_relhigh` / `underlying_rellow` — the source session's
    `high / close` — multiplied by the panel's own price, which on a real window *is* that file's
    close (so the result is the literal high/low) and inside a bootstrap chunk replays the
    session's intraday shape on the chunk's reconstructed close.

    Raises `NotApplicable` where neither exists (the synthetic smoke panel, a window the key has no
    series for).
    """
    if data.high is not None and data.low is not None:
        return data.high, data.low
    try:
        relh = data.extra("underlying_relhigh")
        rell = data.extra("underlying_rellow")
    except _extra.ExtraError as exc:
        raise NotApplicable(
            f"no underlying high/low for window {data.window!r}: {exc}"
        ) from None
    if not (np.isfinite(relh).any() and np.isfinite(rell).any()):
        raise NotApplicable(f"the high/low series is empty in window {data.window!r}")
    return data.price * relh, data.price * rell


# ----------------------------------------------------------------------------- the R2 state
def r2_state_with_entry(price, e2_on, entry_up, comps=None):
    """`fe_signals.r2_state` with the early leg's *entry* gate replaced by `entry_up`.

    The exit (`~SMA50`), the vote override and the `isfinite(median vol)`/momentum conditions are
    production's, unchanged; only the gate that admits a new early position is the caller's. Used
    by IB03 arm C (a Keltner entry gate) and IB23 arm B (the copper/gold gate).
    """
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    entry_up = np.atleast_2d(np.asarray(entry_up, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = (
        fe_signals.early_components(price) if comps is None else comps
    )
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t]
        early_active = np.where(e2t, False, early_active)
        exit_cond = ~sma50_up[:, t]
        entry_cond = (
            entry_up[:, t]
            & np.isfinite(med_p[:, t])
            & (vol_p[:, t] < med_p[:, t])
            & lag_cond[:, t]
        )
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def capped(on_eff, factor, vol_instr):
    """`fe_signals.sized_wants`: `min(1, 0.35 / vol20) x factor`, total cap 0.98."""
    return fe_signals.sized_wants(on_eff, factor, vol_instr)


def scaled(wants, mult):
    """The production target weight times a per-session multiplier, re-capped at production's
    total cap `CAP` (0.98).

    The weight is the fraction of NAV held in the instrument, and the cap is what keeps the
    account from levering beyond the instrument's own gearing (a weight above 1 would mean
    negative cash). A multiplier at or below 1 is unaffected by the cap; one above 1 (IB15 arm B's
    `clip(1 + 0.5z, 0.7, 1.1)`) can therefore never push the arm past the production cap.
    """
    return np.minimum(np.asarray(wants, dtype=float) * np.asarray(mult, dtype=float), CAP)


def vote_3_of_4(s, t, d, extra_vote, extra_ok):
    """`>= 3` of `{SMA200, TSM252, DON126, extra}`; where the extra vote is unavailable the
    production 2-of-3 vote of the first three is used instead (the "unavailable -> production"
    convention, see `nan_clip_ratio`)."""
    three = np.atleast_2d(np.asarray(s, dtype=np.int64)) + t + d
    four = three + np.asarray(extra_vote, dtype=np.int64)
    ok = np.atleast_2d(np.asarray(extra_ok, dtype=bool))
    return np.where(ok, four >= 3, three >= 2)


__all__ = [
    "BLOCK",
    "capped",
    "scaled",
    "ema",
    "keltner_up",
    "lag",
    "nan_clip_ratio",
    "r2_state_with_entry",
    "relative_high_low",
    "session_year",
    "trailing_median_full",
    "trailing_std_full",
    "trailing_sum",
    "vote_3_of_4",
    "wilder_atr",
]
