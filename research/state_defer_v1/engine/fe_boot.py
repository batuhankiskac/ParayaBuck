"""fe_boot -- the block bootstrap with the per-path simulation in numba (prange over paths).

The path draws are the published ones: `band_h2_combo_v1.boot` (== tax_asym_v2 / vt_level_tax_v1
/ tax_harvest_v2) draws, per path, one `integers(0, npool, 1)` for the start, one
`random((1, L))` for the jump mask and one `integers(0, npool, (1, L))` for the fresh draws, then
walks `pos[t] = jump[t] ? fresh[t] : (pos[t-1] + 1) % npool` and prepends the `ext` sessions
before the start. `draw_positions` replays exactly those three calls in the same order, so
`boot(seed, block, ...)` here draws the SAME paths as the Python `boot` for any (seed, block, P).

The path's market reconstruction (QQQ overnight/intraday/dividend + DTB3 resampled, the two
financing models, the E2/R2 target series) is numpy, batched over the chunk; the per-path
simulation is `fe_sim.sim_one` under `prange`. The result dictionary is `screen.boot`'s, so a
study can drop it in unchanged.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from . import fe_data, fe_signals, fe_sim
from .fe_config import (
    BLOCK,
    CANDIDATES,
    EXP_Q,
    FIN,
    PRE_BOOT_PATHS,
    SEED,
    TAX_BOOT_EXT,
    TAX_BOOT_HI,
    TAX_BOOT_I0,
    TAX_BOOT_L,
    TAX_BOOT_LO,
    TAX_BOOT_PATHS,
    TAX_CAPITALS,
)
from .fe_sim import ARMS, CONTROL
from .fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE

TAX_MODES = ("progressive", "flat")
DEFAULT_CHUNK = 256


# ----------------------------------------------------------------------------- path draws
@njit(cache=True, nogil=True)
def _positions(pos, jump, fresh, npool, ext, out):
    """The published recurrence for a whole chunk; `out` is (W, L + ext) of source positions."""
    W = pos.shape[0]
    L = pos.shape[1]
    for p in range(W):
        for t in range(1, L):
            if jump[p, t]:
                pos[p, t] = fresh[p, t]
            else:
                pos[p, t] = (pos[p, t - 1] + 1) % npool
        for k in range(ext):
            out[p, ext - 1 - k] = (pos[p, 0] - (k + 1)) % npool
        for t in range(L):
            out[p, ext + t] = pos[p, t]


@njit(cache=True, nogil=True)
def _oos_positions(pos, jump, fresh, npool):
    """The pooled-OOS draw recurrence of `screen.boot_oos_market`, in place, one chunk at a time."""
    W = pos.shape[0]
    L = pos.shape[1]
    for p in range(W):
        for t in range(1, L):
            if jump[p, t]:
                pos[p, t] = fresh[p, t]
            else:
                pos[p, t] = (pos[p, t - 1] + 1) % npool


def oos_positions(rng, block, n_paths, length, npool):
    """`screen.boot_oos_market`'s three rng calls, chunk by chunk (same stream, same draws).

    The published code draws `integers(0, npool, P)`, then `random((P, L))`, then
    `integers(0, npool, (P, L))` for a whole chunk, so a chunk must be drawn in one call; the
    recurrence runs in numba.
    """
    pos = np.empty((n_paths, length), dtype=np.int64)
    pos[:, 0] = rng.integers(0, npool, n_paths)
    jump = rng.random((n_paths, length)) < 1.0 / block
    fresh = rng.integers(0, npool, (n_paths, length))
    _oos_positions(pos, jump, fresh, npool)
    return pos


def draw_positions(rng, block, n_paths, length, npool, ext):
    """Draw `n_paths` bootstrap positions, replaying `boot()`'s three rng calls per path.

    `rng` is consumed sequentially, one path at a time, so calling this chunk by chunk reproduces
    exactly what one call for all paths would draw.
    """
    pos = np.empty((n_paths, length), dtype=np.int64)
    jump = np.empty((n_paths, length), dtype=np.bool_)
    fresh = np.empty((n_paths, length), dtype=np.int64)
    for p in range(n_paths):
        pos[p, 0] = rng.integers(0, npool, 1)[0]
        jump[p] = rng.random((1, length)) < 1.0 / block
        fresh[p] = rng.integers(0, npool, (1, length))
    out = np.empty((n_paths, length + ext), dtype=np.int64)
    _positions(pos, jump, fresh, npool, ext, out)
    return out


# ----------------------------------------------------------------------------- signals per chunk
def chunk_legs(q, y, pos):
    """One chunk's legs: the published draw-to-market reconstruction, batched (`pos` = idx-1).

    Returns (legs, e2, wants_e2) with `legs[model] = (open, close, wants)`.
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
    return {"base": (so, sc, w_r2), "calibrated": (so_c, sc_c, w_r2c)}, e2, w_e2


# ----------------------------------------------------------------------------- the parallel driver
@njit(parallel=True, cache=True, nogil=True)
def _boot_chunk(
    oq_b,
    cq_b,
    wq_b,
    oq_c,
    cq_c,
    wq_c,
    wq_e2,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    cap_pre,
    cap_at,
    i0,
    i1,
    cost,
    yrs,
    Y0,
    M_lo,
    band,
    usd_rule,
    defer,
    dfr_trig,
    dfr_state,
    dfr_max,
    dfr_drift,
    dfr_ufe_lag,
    mtail,
    dec_wide,
    early_m,
    upm2,
    mode_ids,
    p0,
    n_pre,
    n_tax,
    anchor_e2,
    out_pre,
    out_at,
    out_anchor,
    dbg0,
    dfr0,
):
    """One chunk of bootstrap paths, `prange` over them.

    `out_pre` is (2, n_arms, W), `out_at` (n_caps, n_modes, n_arms, W), `out_anchor` (n_modes, W);
    a path fills only the blocks it belongs to (`p0 + p < n_pre` / `< n_tax`).
    """
    rate = np.zeros(0, dtype=np.float64)  # the bootstraps run with no idle-cash yield
    W = oq_b.shape[0]
    n_arms = band.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
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
                    cap_pre,
                    i0,
                    i1,
                    cost,
                    band[a],
                    usd_rule[a],
                    defer[a],
                    MODE_PRE_TAX,
                    True,
                    Y0,
                    M_lo,
                    rate,
                    False,
                    dbg0,
                    mtail,
                    dec_wide,
                    early_m,
                    upm2,
                    dfr_trig[a],
                    dfr_state[a],
                    dfr_max[a],
                    dfr_drift[a],
                    dfr_ufe_lag[a],
                    dfr0,
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                    oq_c[p],
                    cq_c[p],
                    wq_c[p],
                    fx,
                    upm,
                    mth_of,
                    yr_of,
                    scale_y,
                    dec_tail,
                    pay_ptr,
                    pay_yr,
                    pay_part,
                    cap_pre,
                    i0,
                    i1,
                    cost,
                    band[a],
                    usd_rule[a],
                    defer[a],
                    MODE_PRE_TAX,
                    True,
                    Y0,
                    M_lo,
                    rate,
                    False,
                    dbg0,
                    mtail,
                    dec_wide,
                    early_m,
                    upm2,
                    dfr_trig[a],
                    dfr_state[a],
                    dfr_max[a],
                    dfr_drift[a],
                    dfr_ufe_lag[a],
                    dfr0,
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
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
                            cap_at[ci],
                            i0,
                            i1,
                            cost,
                            band[a],
                            usd_rule[a],
                            defer[a],
                            mode,
                            True,
                            Y0,
                            M_lo,
                            rate,
                            False,
                            dbg0,
                            mtail,
                            dec_wide,
                            early_m,
                            upm2,
                            dfr_trig[a],
                            dfr_state[a],
                            dfr_max[a],
                            dfr_drift[a],
                            dfr_ufe_lag[a],
                            dfr0,
                        )
                        out_at[ci, mi, a, p] = (scal[fe_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1
            if anchor_e2:
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    _nav, _d, scal, _y, _g, _gn, _h = fe_sim.sim_one(
                        oq_b[p],
                        cq_b[p],
                        wq_e2[p],
                        fx,
                        upm,
                        mth_of,
                        yr_of,
                        scale_y,
                        dec_tail,
                        pay_ptr,
                        pay_yr,
                        pay_part,
                        cap_at[0],
                        i0,
                        i1,
                        cost,
                        False,
                        False,
                        False,
                        mode,
                        True,
                        Y0,
                        M_lo,
                        rate,
                        False,
                        dbg0,
                        mtail,
                        dec_wide,
                        early_m,
                        upm2,
                        0,
                        0,
                        0,
                        0.0,
                        0,
                        dfr0,
                    )
                    out_anchor[mi, p] = (scal[fe_sim.SC_LIQ] / cap_at[0]) ** (1 / yrs) - 1


# ----------------------------------------------------------------------------- boot()
class BootWindow:
    """The bootstrap's fixed 2015-09 -> 2026-09 calendar and its market tables (built once)."""

    def __init__(self):
        all_days, arr, y = fe_data.load()
        sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
        need = TAX_BOOT_L + TAX_BOOT_EXT
        if len(sub) < need:
            raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
        self.dates = list(sub[:need])
        self.fx = fe_data.fx_at(self.dates)
        self.ufe = fe_data.load_ufe()
        self.q = arr["QQQ"]
        self.y = y
        self.npool = self.q.shape[1] - 1
        self.i0, self.i1 = TAX_BOOT_I0, len(self.dates) - 1
        self.yrs = fe_sim.calendar_years(self.dates[self.i0 - 1], self.dates[self.i1])
        # one env: the flat mode simply never touches the instalment/scale tables (the kernel
        # guards every use by `mode`), and the market tables are mode-independent.
        self.env = fe_sim.RunEnv(self.dates, self.fx, self.ufe, self.i0, self.i1)


def boot(
    seed=SEED,
    block=BLOCK,
    arms=ARMS,
    n_pre=PRE_BOOT_PATHS,
    n_tax=TAX_BOOT_PATHS,
    capitals=("100k", "1M"),
    modes=TAX_MODES,
    anchor_e2=True,
    deltas=CANDIDATES,
    baselines=(CONTROL,),
    control=CONTROL,
    label="E1",
    m=fe_sim.DEFAULT_M,
    chunk=DEFAULT_CHUNK,
    quiet=False,
    window=None,
    keep_paths=False,
):
    """Block bootstrap; the same dict as `screen.boot` (plus `paths` when `keep_paths`)."""
    win = window if window is not None else BootWindow()
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    n_arms = len(arms)
    band = np.array([fe_sim.RULE[a]["band"] for a in arms], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[a]["usd_rule"] for a in arms], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a]["defer"] for a in arms], dtype=np.bool_)
    dfr_trig = np.array([fe_sim.RULE[a]["dfr_trig"] for a in arms], dtype=np.int64)
    dfr_state = np.array([fe_sim.RULE[a]["dfr_state"] for a in arms], dtype=np.int64)
    dfr_max = np.array([fe_sim.RULE[a]["dfr_max"] for a in arms], dtype=np.int64)
    dfr_drift = np.array([fe_sim.RULE[a]["dfr_drift"] for a in arms], dtype=np.float64)
    dfr_ufe_lag = np.array([fe_sim.RULE[a]["dfr_ufe_lag"] for a in arms], dtype=np.int64)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array([MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT for mode in modes], dtype=np.int64)

    # the pre-tax leg only covers the first `n_pre` paths and the after-tax leg the first
    # `n_tax`, exactly like the Python boot's per-path append conditions.
    pre = {(mdi, a): np.empty(n_pre, dtype=np.float64) for mdi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    anchor = {mode: np.empty(n_tax, dtype=np.float64) for mode in modes}
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    dfr0 = np.zeros((0, 5), dtype=np.float64)
    rng = np.random.default_rng(seed)

    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = draw_positions(rng, block, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
        legs, _e2, w_e2 = chunk_legs(win.q, win.y, pos)
        so, sc, w_r2 = legs["base"]
        so_c, sc_c, w_r2c = legs["calibrated"]
        out_pre = np.empty((2, n_arms, W), dtype=np.float64)
        out_at = np.empty((len(capitals), len(modes), n_arms, W), dtype=np.float64)
        out_anchor = np.empty((len(modes), W), dtype=np.float64)
        _boot_chunk(
            so,
            sc,
            w_r2,
            so_c,
            sc_c,
            w_r2c,
            w_e2,
            env.fx,
            env.upm,
            env.mth_of,
            env.yr_of,
            env.scale_y,
            env.dec_tail,
            env.pay_ptr,
            env.pay_yr,
            env.pay_part,
            TAX_CAPITALS["100k"],
            cap_at,
            win.i0,
            win.i1,
            cost,
            win.yrs,
            env.Y0,
            env.M_lo,
            band,
            usd_rule,
            defer,
            dfr_trig,
            dfr_state,
            dfr_max,
            dfr_drift,
            dfr_ufe_lag,
            env.mtail,
            env.dec_wide,
            env.early_m,
            env.upm2,
            mode_ids,
            p0,
            n_pre,
            n_tax,
            anchor_e2,
            out_pre,
            out_at,
            out_anchor,
            dbg0,
            dfr0,
        )
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for bi, mdi in enumerate(("base", "calibrated")):
            for ai, a in enumerate(arms):
                if npre:
                    pre[(mdi, a)][p0 : p0 + npre] = out_pre[bi, ai, :npre]
        for ci, cl in enumerate(capitals):
            for mi, mode in enumerate(modes):
                for ai, a in enumerate(arms):
                    if ntax:
                        at[cl][mode][a][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]
        for mi, mode in enumerate(modes):
            if ntax:
                anchor[mode][p0 : p0 + ntax] = out_anchor[mi, :ntax]
        if not quiet:
            print(f"  {label} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    out = {
        "n_paths_pre": n_pre,
        "n_paths_tax": n_tax,
        "seed": seed,
        "block": block,
        "arms": list(arms),
        "control": control,
        "calendar_start": win.dates[0],
        "calendar_end": win.dates[-1],
        "pre": {},
        "tax": {},
    }
    for mdi in ("base", "calibrated") if n_pre > 0 else ():  # no pre-tax paths -> no block
        base = pre[(mdi, control)]
        out["pre"][mdi] = {}
        for arm in arms:
            c = pre[(mdi, arm)]
            d = c - base
            out["pre"][mdi][arm] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in baselines:
                base = at[cl][mode][ctrl]
                for arm in deltas:
                    if arm == ctrl:
                        continue
                    d = at[cl][mode][arm] - base
                    e[f"{arm}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100),
                    }
            out["tax"][cl][mode] = e
    out["anchor_e2_100k"] = {
        mode: ({"cagr_median": float(np.median(anchor[mode]))} if anchor_e2 else {}) for mode in modes
    }
    if keep_paths:
        out["paths"] = {"pre": pre, "tax": at, "anchor_e2": anchor}
    return out
