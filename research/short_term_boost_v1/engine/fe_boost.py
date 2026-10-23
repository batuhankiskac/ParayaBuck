"""Boost-mask construction for short_term_boost_v1 (a numba signal kernel).

After a deep short-term QQQ drop while the E2 vote is on, raise K's QLD target for a fixed number
of sessions (`short_term_restore_v1` found the 2x leg earns +0.5-0.6 %/session after an 8 %
20-session drop). Signal code only; simulation, tax and bootstrap stay in `research/fast_engine`.

Mask codes: 0 = K's target `w`, 3 = boosted target (`apply_mask`).

Parameters (PREREGISTRATION.md section 3):
  d     trigger: e2[t] and w[t] > 0 and close[t] <= (1 - d) * max20[t] (plain trailing-20 high)
  N     boost length in sessions (the trigger session counts as the first)
  g     guard: end the boost early if close <= (1 - g) * H_trig (g >= 1 disables)
Ends early when e2 turns off or w <= 0. Re-arms only after a close with
close > (1 - d) * max20 (the trigger condition false) once the boost has ended.
Records: `ex_i` trigger close, `re_i` last boosted close, `hi_v` H_trig, `how` end reason
(0 length, 1 guard, 2 e2/w off, 3 data end).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange


@njit(cache=True)
def boost_core(c, w, e2, max20, i0, i1, d, N, g, mask, ex_i, re_i, hi_v, how):
    armed = True
    on = False
    k = 0
    t0 = -1
    H = 0.0
    n = 0
    rec = ex_i.shape[0] > 0
    for t in range(i0, i1 + 1):
        trig = e2[t] and w[t] > 0.0 and c[t] <= (1.0 - d) * max20[t]
        if on:
            reason = -1
            if (not e2[t]) or w[t] <= 0.0:
                reason = 2
            elif g < 1.0 and c[t] <= (1.0 - g) * H:
                reason = 1
            if reason >= 0:
                on = False
                if rec:
                    re_i[n] = t - 1
                    how[n] = reason
                n += 1
                mask[t] = 0
                armed = not trig
                continue
            mask[t] = 3
            k += 1
            if k >= N:
                on = False
                if rec:
                    re_i[n] = t
                    how[n] = 0
                n += 1
            continue
        if not armed:
            mask[t] = 0
            if not trig:
                armed = True
            continue
        if trig:
            on = True
            k = 1
            t0 = t
            H = max20[t]
            if rec:
                ex_i[n] = t
                hi_v[n] = H
            mask[t] = 3
            if k >= N:
                on = False
                if rec:
                    re_i[n] = t
                    how[n] = 0
                n += 1
            armed = False
        else:
            mask[t] = 0
    if on:
        if rec:
            re_i[n] = i1
            how[n] = 3
        n += 1
    return n


@njit(parallel=True, cache=True, nogil=True)
def boost_masks(c, w, e2, max20, i0, i1, d, N, g, masks, counts):
    P = c.shape[0]
    ei = np.empty(0, dtype=np.int64)
    ef = np.empty(0, dtype=np.float64)
    for p in prange(P):
        counts[p] = boost_core(c[p], w[p], e2[p], max20[p], i0, i1, d, N, g, masks[p],
                               ei, ei, ef, ei)


def apply_mask(mask, w, mult, cap=0.98):
    """Code 3 -> min(cap, mult * w) when mult > 0, else cap; never below w. Code 0 -> w."""
    boosted = np.full_like(w, cap) if mult <= 0.0 else np.minimum(cap, mult * w)
    boosted = np.maximum(boosted, w)
    return np.ascontiguousarray(np.where(mask == 3, boosted, w))


__all__ = ["boost_core", "boost_masks", "apply_mask"]
