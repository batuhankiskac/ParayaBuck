"""Bootstrap driver that accepts an EXOGENOUS per-path target-weight array.

`research/fast_engine/fe_boot.boot` drives every arm from the same target series it
computes per path (`chunk_legs`), so a study arm whose target weight differs per
path (an oracle) cannot be expressed. This module adds exactly that: the path draws
are still `fe_boot.draw_positions` (same seed -> same paths), the market
reconstruction is a verbatim copy of `fe_boot.chunk_legs` extended to also return
the QQQ closes, and the simulation is still `fe_sim.sim_one` under `prange`.

Nothing here changes the engine: an arm is (price arrays, target array, fill rule)
and the fill rule is K's. The control path is inert: calling `run_pretax` /
`run_tax` with the target arrays `fe_boot.boot` would build (and the real price
arrays) reproduces `fe_boot.boot` bit-for-bit -- `screen.py --harness` (R4) asserts
that on 200 paths, every cell.

`chunk_legs_pc` is asserted equal to `fe_boot.chunk_legs` (legs and E2 vote) in the
same harness.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_signals, fe_sim
from research.fast_engine.fe_config import EXP_Q, FIN
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE

_SC_LIQ = fe_sim.SC_LIQ  # passed explicitly to the tax kernel below


def chunk_legs_pc(q, y, pos):
    """`fe_boot.chunk_legs` plus the reconstructed QQQ closes it is built from.

    Returns (pc, e2, legs, w_e2) with legs[model] = (open, close, wants). Identical
    arithmetic to the original, line for line.
    """
    idx = pos + 1  # pool = arange(1, Tsrc)
    yy = y[idx - 1]
    prev = q[1][idx - 1]
    r_on_tr = (q[0][idx] + q[2][idx]) / prev
    r_on_p = q[0][idx] / prev
    r_id = q[1][idx] / q[0][idx]
    P = idx.shape[0]
    one = np.ones((P, 1))
    qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
    qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
    pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
    e2 = fe_signals.e2_up(pc)
    yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
    comps = fe_signals.early_components(pc)
    on_eff, factor = fe_signals.r2_state(pc, e2, comps)
    zeros = np.zeros_like(qc)
    so, sc = fe_signals.synthetic_2x(qo, qc, zeros, yyp + FIN["base"], EXP_Q)
    so_c, sc_c = fe_signals.synthetic_2x(qo, qc, zeros, yyp + FIN["calibrated"], EXP_Q)
    w_r2 = fe_signals.sized_wants(on_eff, factor, fe_signals.vol_n(sc))
    w_r2c = fe_signals.sized_wants(on_eff, factor, fe_signals.vol_n(sc_c))
    w_e2 = fe_signals.sized_wants(e2, np.ones_like(factor), fe_signals.vol_n(sc))
    return pc, e2, {"base": (so, sc, w_r2), "calibrated": (so_c, sc_c, w_r2c)}, w_e2


# ----------------------------------------------------------------------------- kernels
@njit(parallel=True, cache=True, nogil=True)
def run_pretax(
    oq_b,
    cq_b,
    wq_b,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    capital,
    i0,
    i1,
    cost,
    band,
    usd_rule,
    defer,
    Y0,
    M_lo,
    out,
):
    """One arm, one chunk: `fe_boot._boot_chunk`'s pre-tax leg with an exogenous target."""
    W = oq_b.shape[0]
    k = i1 - i0 + 1
    rate = np.zeros(0, dtype=np.float64)
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    for p in prange(W):
        nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
            oq_b[p],
            cq_b[p],
            wq_b[p],
            fx,
            upm,
            mth_of,
            yr_of,
            scale_y,
            dec_tail,
            pay_ptr,
            pay_yr,
            pay_part,
            capital,
            i0,
            i1,
            cost,
            band,
            usd_rule,
            defer,
            MODE_PRE_TAX,
            True,
            Y0,
            M_lo,
            rate,
            False,
            dbg0,
        )
        out[p] = (nav[k] / nav[0]) ** (252 / k) - 1


@njit(parallel=True, cache=True, nogil=True)
def run_tax(
    oq_b,
    cq_b,
    wq_b,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    capital,
    i0,
    i1,
    cost,
    band,
    usd_rule,
    defer,
    mode,
    yrs,
    sc_liq,
    Y0,
    M_lo,
    out,
):
    """One arm, one chunk: the after-tax leg with an exogenous target (liquidated CAGR)."""
    W = oq_b.shape[0]
    rate = np.zeros(0, dtype=np.float64)
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    for p in prange(W):
        _nav, _d, scal, _y, _g, _gn, _h = fe_sim.sim_one(
            oq_b[p],
            cq_b[p],
            wq_b[p],
            fx,
            upm,
            mth_of,
            yr_of,
            scale_y,
            dec_tail,
            pay_ptr,
            pay_yr,
            pay_part,
            capital,
            i0,
            i1,
            cost,
            band,
            usd_rule,
            defer,
            mode,
            True,
            Y0,
            M_lo,
            rate,
            False,
            dbg0,
        )
        out[p] = (scal[sc_liq] / capital) ** (1 / yrs) - 1


@njit(cache=True)
def episode_mask_one(c, e2, i0, i1, cut, min_depth, restore_k, false_frac, mask):
    """The pre-registered oracle intervals for one path; fills `mask` (0/1) and returns
    the number of firing episodes.

    Episodes: 20-session-high closes with the E2 vote on, no episode open; an episode
    ends at the first close >= its start high or the first E2-off close. Depth is the
    maximum decline to the minimum close. A firing episode zeroes the target from the
    first close at least `cut` below the start high through `max(low + restore_k,
    cut_index + 1)` (half-open). Shallow episodes (cut <= depth < min_depth) fire on
    the deterministic Bresenham fraction `false_frac` of their time order.
    """
    j_shallow = 0
    n_fire = 0
    t = i0
    while t <= i1:
        is20 = False
        if t >= 19:
            h = c[t]
            is20 = True
            k = t - 19
            while k < t:
                if c[k] > h:
                    is20 = False
                    break
                k += 1
        if e2[t] and is20:
            H = c[t]
            end = i1
            u = t + 1
            while u <= i1:
                if c[u] >= H:
                    end = u
                    break
                if not e2[u]:
                    end = u
                    break
                u += 1
            lo = t
            for u in range(t + 1, end + 1):
                if c[u] < c[lo]:
                    lo = u
            depth = 1.0 - c[lo] / H
            fire = False
            if depth >= min_depth:
                fire = True
            elif depth >= cut:
                sel = np.floor((j_shallow + 1) * false_frac + 1e-12) > np.floor(
                    j_shallow * false_frac + 1e-12
                )
                j_shallow += 1
                fire = sel
            if fire:
                thr = H * (1.0 - cut)
                ce = lo
                for u in range(t, lo + 1):
                    if c[u] <= thr:
                        ce = u
                        break
                r = lo + restore_k
                if r <= ce:
                    r = ce + 1
                for u in range(ce, r):
                    mask[u] = 1
                n_fire += 1
            t = end + 1
        else:
            t += 1
    return n_fire


@njit(parallel=True, cache=True, nogil=True)
def episode_masks(pc, e2, i0, i1, cut, min_depth, restore_k, false_frac, masks, counts):
    """`episode_mask_one` over a chunk of paths."""
    W = pc.shape[0]
    for p in prange(W):
        counts[p] = episode_mask_one(pc[p], e2[p], i0, i1, cut, min_depth, restore_k, false_frac, masks[p])


@njit(parallel=True, cache=True, nogil=True)
def masked_prices(o, c, mask, out_o, out_c):
    """The virtual-hedge price path: the leg's total return is zero on masked sessions.

    A piecewise-constant scale `sc = P_eff / P` is carried forward; it changes only on
    a masked session (by the inverse of that session's return). With an all-zero mask
    `sc` stays 1 and every output equals the input bit for bit (`x * 1.0 == x`).
    """
    W = c.shape[0]
    n = c.shape[1]
    for p in prange(W):
        sc = 1.0
        out_c[p, 0] = c[p, 0]
        out_o[p, 0] = o[p, 0]
        for t in range(1, n):
            if mask[p, t] == 1:
                sc = sc * (c[p, t - 1] / c[p, t])
                out_c[p, t] = c[p, t] * sc
                out_o[p, t] = out_c[p, t]
            else:
                out_c[p, t] = c[p, t] * sc
                out_o[p, t] = o[p, t] * sc
