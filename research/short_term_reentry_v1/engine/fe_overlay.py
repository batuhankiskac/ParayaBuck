"""Overlay target construction for short_term_reentry_v1 (a numba signal kernel).

The overlay of `PREREGISTRATION.md` section 3, one path at a time, on QQQ price closes and
K's target-weight series. This is *signal* code: it only builds the 0/1 "out" mask; the
simulation, tax and bootstrap all stay in `research/fast_engine` (via
`fe_boot_targets.sim_one` / `run_pretax` / `run_tax`).

State (per path): in/out, the post-exit low `L`, the first session of the current in-stint,
and the exit episode's reference high. The mask is 1 on every decision close whose target is
0 because of the overlay; `target = where(mask == 1, 0.0, w)`.

Episode records (diagnostics only, `overlay_core` writes them when the arrays are non-empty):
`ex_i` exit decision close, `re_i` re-entry decision close (`-1` still out at the data end,
`-2` ended by K's own weight going to zero), `hi_v` the reference high at the exit, `lo_i`
and `lo_v` the minimum close over the episode (exit close included).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange


@njit(cache=True, inline="always")
def _record(k, ex_i, re_i, hi_v, lo_i, lo_v, ex_t, re_t, h, lo_t, lv):
    """Write one episode record if the diagnostic arrays have room (they are empty when not)."""
    if ex_i.shape[0] > k:
        ex_i[k] = ex_t
        re_i[k] = re_t
        hi_v[k] = h
        lo_i[k] = lo_t
        lo_v[k] = lv


@njit(cache=True)
def overlay_core(c, w, max20, sma_up10, i0, i1, d, kind, r, mask, ex_i, re_i, hi_v, lo_i, lo_v):
    """One path's overlay mask and episode records; returns the number of exits.

    `max20[t]` is max(c[t-19..t]) (filled for every t; the trailing-20 window is used when the
    stint is at least 20 sessions old), `sma_up10[t]` is `close[t] > SMA10[t]`. `kind` 0 is the
    rebound rule, 1 the SMA10 rule.
    """
    T = c.shape[0]
    anchor = i0 - 19
    if anchor < 0:
        anchor = 0
    in_st = True
    L = 0.0
    ex_t = -1
    hi_ex = 0.0
    lo_t = -1
    lo_vv = 0.0
    n = 0
    for t in range(i0, i1 + 1):
        if w[t] <= 0.0:
            if not in_st:
                _record(n, ex_i, re_i, hi_v, lo_i, lo_v, ex_t, -2, hi_ex, lo_t, lo_vv)
                n += 1
                in_st = True
            anchor = t
            mask[t] = 0
            continue
        if in_st:
            if t - anchor >= 19:
                H = max20[t]
            else:
                H = c[anchor]
                for j in range(anchor + 1, t + 1):
                    if c[j] > H:
                        H = c[j]
            if c[t] <= (1.0 - d) * H:
                in_st = False
                ex_t = t
                hi_ex = H
                lo_t = t
                lo_vv = c[t]
                mask[t] = 1
            else:
                mask[t] = 0
        else:
            if c[t] < lo_vv:
                lo_vv = c[t]
                lo_t = t
            if kind == 0:
                hit = c[t] >= (1.0 + r) * lo_vv
            else:
                hit = sma_up10[t]
            if hit:
                _record(n, ex_i, re_i, hi_v, lo_i, lo_v, ex_t, t, hi_ex, lo_t, lo_vv)
                n += 1
                in_st = True
                anchor = t
                mask[t] = 0
            else:
                mask[t] = 1
    if not in_st:
        _record(n, ex_i, re_i, hi_v, lo_i, lo_v, ex_t, -1, hi_ex, lo_t, lo_vv)
        n += 1
    return n


@njit(parallel=True, cache=True, nogil=True)
def overlay_masks(c, w, max20, sma_up10, i0, i1, d, kind, r, masks, counts):
    """`overlay_core` over a chunk of paths (one path per `prange` iteration)."""
    P = c.shape[0]
    empty_i = np.empty(0, dtype=np.int64)
    empty_f = np.empty(0, dtype=np.float64)
    for p in prange(P):
        counts[p] = overlay_core(
            c[p], w[p], max20[p], sma_up10[p], i0, i1, d, kind, r, masks[p],
            empty_i, empty_i, empty_f, empty_i, empty_f,
        )


def rolling_max(c):
    """Trailing 20-session max of closes, `[paths, T]` or 1-D (left edge = running max)."""
    c = np.atleast_2d(np.asarray(c, dtype=float))
    T = c.shape[1]
    H = c.copy()
    for t in range(1, min(20, T)):
        H[:, t] = np.maximum(H[:, t - 1], c[:, t])
    if T >= 20:
        H[:, 19:] = np.lib.stride_tricks.sliding_window_view(c, 20, axis=1).max(axis=2)
    return H


__all__ = ["overlay_core", "overlay_masks", "rolling_max"]
