"""Overlay target construction for short_term_restore_v1 (a numba signal kernel).

A generalisation of `short_term_reentry_v1/engine/fe_overlay.py`: the overlay can cut K's QLD
weight to a partial factor `f1` (or to zero), optionally cut to zero at a second, deeper stage,
and restore either on a rebound from the post-cut low or on a price **bracket** around the cut
close. This is *signal* code only: it builds the per-session state mask; the simulation, tax and
bootstrap stay in `research/fast_engine` (via `fe_boot_targets`).

Mask codes: 0 = full (target `w`), 1 = zero (target 0), 2 = partial (target `f1 * w`).

Parameters (see PREREGISTRATION.md section 3):
  d1    first cut: close <= (1 - d1) * H, H = trailing-20 high truncated at the in-stint start
  f1    factor after the first cut (0.0 -> code 1, otherwise code 2)
  d2    second cut to zero while partial: close <= (1 - d2) * H_cut; d2 >= 1 disables it
  kind  0 = rebound restore: close >= (1 + r) * L, L = min close since the cut (inclusive)
        1 = bracket restore: close >= X_cut (the first-cut close) or close <= (1 - e) * X_cut;
            e >= 1 disables the lower leg
Records (diagnostics): `ex_i` first-cut close, `re_i` restore close (-1 still cut at the end,
-2 ended by K's own weight going to zero), `hi_v` H at the cut, `lo_i`/`lo_v` minimum close over
the episode, `c2_i` second-cut close (-1 none), `how` restore leg (0 rebound/upper, 1 lower).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange


@njit(cache=True, inline="always")
def _record(k, ex_i, re_i, hi_v, lo_i, lo_v, c2_i, how, ex_t, re_t, h, lo_t, lv, c2_t, hw):
    if ex_i.shape[0] > k:
        ex_i[k] = ex_t
        re_i[k] = re_t
        hi_v[k] = h
        lo_i[k] = lo_t
        lo_v[k] = lv
        c2_i[k] = c2_t
        how[k] = hw


@njit(cache=True)
def overlay_core(c, w, max20, i0, i1, d1, f1, d2, kind, r, e, mask,
                 ex_i, re_i, hi_v, lo_i, lo_v, c2_i, how):
    """One path's state mask and episode records; returns the number of first cuts."""
    anchor = i0 - 19
    if anchor < 0:
        anchor = 0
    state = 0          # 0 full, 1 zero, 2 partial
    cut_code = 1 if f1 <= 0.0 else 2
    ex_t = -1
    hi_ex = 0.0
    x_cut = 0.0
    lo_t = -1
    lo_vv = 0.0
    c2_t = -1
    n = 0
    for t in range(i0, i1 + 1):
        if w[t] <= 0.0:
            if state != 0:
                _record(n, ex_i, re_i, hi_v, lo_i, lo_v, c2_i, how,
                        ex_t, -2, hi_ex, lo_t, lo_vv, c2_t, -1)
                n += 1
                state = 0
            anchor = t
            mask[t] = 0
            continue
        if state == 0:
            if t - anchor >= 19:
                H = max20[t]
            else:
                H = c[anchor]
                for j in range(anchor + 1, t + 1):
                    if c[j] > H:
                        H = c[j]
            if c[t] <= (1.0 - d1) * H:
                state = cut_code
                ex_t = t
                hi_ex = H
                x_cut = c[t]
                lo_t = t
                lo_vv = c[t]
                c2_t = -1
                if state == 2 and d2 < 1.0 and c[t] <= (1.0 - d2) * H:
                    state = 1
                    c2_t = t
                mask[t] = state
            else:
                mask[t] = 0
        else:
            if c[t] < lo_vv:
                lo_vv = c[t]
                lo_t = t
            hit = False
            hw = 0
            if kind == 0:
                hit = c[t] >= (1.0 + r) * lo_vv
            else:
                if c[t] >= x_cut:
                    hit = True
                elif e < 1.0 and c[t] <= (1.0 - e) * x_cut:
                    hit = True
                    hw = 1
            if hit:
                _record(n, ex_i, re_i, hi_v, lo_i, lo_v, c2_i, how,
                        ex_t, t, hi_ex, lo_t, lo_vv, c2_t, hw)
                n += 1
                state = 0
                anchor = t
                mask[t] = 0
            else:
                if state == 2 and d2 < 1.0 and c[t] <= (1.0 - d2) * hi_ex:
                    state = 1
                    c2_t = t
                mask[t] = state
    if state != 0:
        _record(n, ex_i, re_i, hi_v, lo_i, lo_v, c2_i, how,
                ex_t, -1, hi_ex, lo_t, lo_vv, c2_t, -1)
        n += 1
    return n


@njit(parallel=True, cache=True, nogil=True)
def overlay_masks(c, w, max20, i0, i1, d1, f1, d2, kind, r, e, masks, counts):
    """`overlay_core` over a chunk of paths (one path per `prange` iteration)."""
    P = c.shape[0]
    empty_i = np.empty(0, dtype=np.int64)
    empty_f = np.empty(0, dtype=np.float64)
    for p in prange(P):
        counts[p] = overlay_core(
            c[p], w[p], max20[p], i0, i1, d1, f1, d2, kind, r, e, masks[p],
            empty_i, empty_i, empty_f, empty_i, empty_f, empty_i, empty_i,
        )


def apply_mask(mask, w, f1):
    """Target weights from a state mask: 0 -> w, 1 -> 0, 2 -> f1 * w (numpy, any shape)."""
    return np.ascontiguousarray(np.where(mask == 1, 0.0, np.where(mask == 2, f1 * w, w)))


__all__ = ["overlay_core", "overlay_masks", "apply_mask"]
