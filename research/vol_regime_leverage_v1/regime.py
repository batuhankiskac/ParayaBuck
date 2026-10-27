"""regime -- the volatility-regime percentile Q and the regime-scaled target arms V1-V4.

Signal code only: one numba rank kernel (a percentile over a trailing or expanding window, no
simulation, no tax, no bootstrap) and numpy weight arithmetic. Every simulation goes through
fe_sim.simulate. Pre-registered in PREREGISTRATION.md; constants below are the pre-registered values.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine.fe_config import CAP

WIN = 252          # trailing window of Q (V1, V3, V4) and the minimum history of the expanding Q (V2)
L0, L1 = 2.0, 1.5  # L = 2 - 1.5 Q, QQQ-equivalent exposure
MULT_NORM = 1.25   # V3 normaliser: L/1.25 has mean 1 under uniform Q
MULT_CAP = 1.2     # V3 upper clip


@njit(cache=True, nogil=True)
def _pct_row(v, window, out):
    """Q_t = (# window values <= v_t) / (# window values), window including t.

    window > 0: the last `window` finite values (trailing); window == 0: every finite value so far
    (expanding). Non-finite inputs are skipped; Q is NaN until the window holds `window` values
    (252 for the expanding case). Sorted buffer with binary-search insert/delete.
    """
    T = v.shape[0]
    srt = np.empty(T, dtype=np.float64)
    ins = np.empty(T, dtype=np.float64)
    need = window if window > 0 else 252
    n = 0
    k = 0
    for t in range(T):
        x = v[t]
        if not np.isfinite(x):
            continue
        lo = 0
        hi = n
        while lo < hi:  # upper bound: insert after equal values
            mid = (lo + hi) // 2
            if srt[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        for j in range(n, lo, -1):
            srt[j] = srt[j - 1]
        srt[lo] = x
        n += 1
        ins[k] = x
        k += 1
        if window > 0 and k > window:
            old = ins[k - 1 - window]
            lo = 0
            hi = n
            while lo < hi:  # lower bound of the value leaving the window
                mid = (lo + hi) // 2
                if srt[mid] < old:
                    lo = mid + 1
                else:
                    hi = mid
            for j in range(lo, n - 1):
                srt[j] = srt[j + 1]
            n -= 1
        if k >= need:
            lo = 0
            hi = n
            while lo < hi:  # count of window values <= x
                mid = (lo + hi) // 2
                if srt[mid] <= x:
                    lo = mid + 1
                else:
                    hi = mid
            out[t] = lo / n


@njit(parallel=True, cache=True, nogil=True)
def _pct_panel(V, window, out):
    for r in prange(V.shape[0]):
        _pct_row(V[r], window, out[r])


def pct_rank(V, window):
    """Percentile rank of each value in its trailing (window > 0) or expanding (window == 0) history."""
    V = np.ascontiguousarray(np.atleast_2d(np.asarray(V, dtype=float)))
    out = np.full(V.shape, np.nan)
    _pct_panel(V, int(window), out)
    return out


def pct_rank_brute(v, window):
    """Reference (slow, numpy) implementation of pct_rank for one series; used by the parity check."""
    v = np.asarray(v, dtype=float)
    out = np.full(v.shape, np.nan)
    fin = []
    for t in range(v.shape[0]):
        if not np.isfinite(v[t]):
            continue
        fin.append(v[t])
        win = fin[-window:] if window > 0 else fin
        need = window if window > 0 else 252
        if len(win) >= need:
            w = np.asarray(win)
            out[t] = np.count_nonzero(w <= v[t]) / len(w)
    return out


def month_end_hold(Q, month_ids):
    """Q at the most recent month-end session <= t (the value is known at that close)."""
    Q = np.atleast_2d(np.asarray(Q, dtype=float))
    m = np.asarray(month_ids)
    T = Q.shape[1]
    flag = np.ones(T, dtype=bool)
    flag[:-1] = m[1:] != m[:-1]
    last = np.maximum.accumulate(np.where(flag, np.arange(T), -1))
    out = np.full(Q.shape, np.nan)
    ok = last >= 0
    out[:, ok] = Q[:, last[ok]]
    return out


def arm_weights(vol_p, factor, w_k, month_ids, Qexp=None):
    """Target QLD weights of the regime arms, from QQQ vol20, K's state factor and K's weight.

    factor in {0, 0.5, 1} is r2_state's factor (E2 on = 1, early re-entry = 0.5, off = 0).
    V1/V2/V4: w = min(CAP, factor * L / 2), L = 2 - 1.5 Q (Q = trailing / expanding / month-held).
    V3:       w = min(CAP, w_K * min(MULT_CAP, L / MULT_NORM)).
    Warm-up (Q undefined): every arm uses w_K.
    """
    vol_p = np.atleast_2d(np.asarray(vol_p, dtype=float))
    factor = np.atleast_2d(np.asarray(factor, dtype=float))
    w_k = np.atleast_2d(np.asarray(w_k, dtype=float))
    Q252 = pct_rank(vol_p, WIN)
    Qe = pct_rank(vol_p, 0) if Qexp is None else np.atleast_2d(np.asarray(Qexp, dtype=float))
    Qm = month_end_hold(Q252, month_ids)

    def replace(Q):
        lev = (L0 - L1 * Q) / 2.0
        return np.where(np.isfinite(Q), np.minimum(CAP, factor * lev), w_k)

    V3 = np.where(np.isfinite(Q252),
                  np.minimum(CAP, w_k * np.minimum(MULT_CAP, (L0 - L1 * Q252) / MULT_NORM)), w_k)
    return {"V1": replace(Q252), "V2": replace(Qe), "V3": V3, "V4": replace(Qm),
            "Q252": Q252, "Qexp": Qe, "Qmonth": Qm}
