"""fe2_boot -- the block bootstrap for two-leg arms, on the engine's own draws.

`fe_boot.draw_positions` is reused unchanged, so a two-leg bootstrap draws exactly the paths
`fe_boot.boot` would (same seed, block, path count and order, chunk by chunk). What changes is
only the per-path step: each path is a two-leg `plant` of the reconstructed series, and the arms'
target panels are `(W, 2, T)` -- the path axis first, so `oq[p]` is a contiguous `(2, T)` panel
the kernel takes directly.

The caller owns the market reconstruction (the framework rebuilds its `MarketData` panels per
chunk, an engine-level check just uses `chunk_series`), which is why `boot2` takes two callables:

    series(pos, W) -> {"base": (opens, closes, ctx), "calibrated": (opens, closes, ctx)}
        opens/closes are `(W, 2, T)`; `ctx` is whatever the arm needs to compute that model's
        targets (the framework passes the `MarketData` panel, the engine check the target panel),
        so the targets follow the financing model exactly as the single-leg bootstrap does.
    wants(ctx)     -> `(W, 2, T)` target weights of one arm for one model.

With leg B identically zero (`opens[:, 1] = closes[:, 1] = 0`, `wants[:, 1] = 0`) `boot2` is
bit-identical to `fe_boot.boot` for the corresponding one-leg arms -- the parity cell in
`fe2_parity.py` asserts it path by path.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_sim
from research.fast_engine.fe_config import (
    BLOCK,
    PRE_BOOT_PATHS,
    SEED,
    TAX_BOOT_EXT,
    TAX_BOOT_L,
    TAX_BOOT_PATHS,
    TAX_CAPITALS,
)
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE

from . import fe2_sim

TAX_MODES = ("progressive", "flat")
DEFAULT_CHUNK = 256
DBG0 = np.zeros((0, 6), dtype=np.float64)


# ----------------------------------------------------------------------------- kernel driver
@njit(parallel=True, cache=True, nogil=True)
def _boot_chunk2(
    ob,
    cb,
    wb,
    oc,
    cc,
    wc,
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
    rule_index,
    mode_ids,
    cash_rate,
    p0,
    n_pre,
    n_tax,
    dbg0,
    out_pre,
    out_at,
):
    """One chunk of two-leg paths, `prange` over them (`fe_boot._boot_chunk`, one leg pair).

    `ob`/`cb`/`wc` etc. are `(W, 2, T)` (base) and the wants panel is `(n_arms, W, 2, T)`;
    `out_pre` is `(2, n_arms, W)` and `out_at` `(n_caps, n_modes, n_arms, W)`.
    """
    W = ob.shape[0]
    n_arms = wb.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                nav, _d, _s, _y, _g, _gn, _h, _ls, _y2 = fe2_sim.sim2(
                    ob[p], cb[p], wb[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h, _ls, _y2 = fe2_sim.sim2(
                    oc[p], cc[p], wc[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h, _ls, _y2 = fe2_sim.sim2(
                            ob[p], cb[p], wb[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a], usd_rule[a],
                            defer[a], mode, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                        )
                        out_at[ci, mi, a, p] = (scal[fe2_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1


@njit(parallel=True, cache=True, nogil=True)
def _pt2(
    ob,
    cb,
    wq,
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
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    dbg0,
    out,
):
    """Pre-tax two-leg paths, `prange` over paths: the pooled OOS bootstrap's inner loop.

    `ob`/`cb` are `(W, 2, T)`, `wq` `(n_arms, W, 2, T)`, `out` `(n_arms, W)` (the annualised
    path CAGR, `(nav[k] / nav[0]) ** (252 / k) - 1`).
    """
    k = i1 - i0 + 1
    n_arms = wq.shape[0]
    for p in prange(ob.shape[0]):
        for a in range(n_arms):
            nav, _d, _s, _y, _g, _gn, _h, _ls, _y2 = fe2_sim.sim2(
                ob[p], cb[p], wq[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                pay_yr, pay_part, capital, i0, i1, cost, band[a], usd_rule[a], defer[a],
                MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
            )
            out[a, p] = (nav[k] / nav[0]) ** (252 / k) - 1


# ----------------------------------------------------------------------------- engine-level series
def chunk_series(win=None):
    """A `series` provider for `boot2`: the engine's own reconstruction with leg B at zero.

    Leg A's base/calibrated panels are exactly `fe_boot.chunk_legs`'s (`so, sc, w_r2` and
    `so_c, sc_c, w_r2c`) and the contexts are those arms' own target panels; leg B is an
    identically-zero instrument, the configuration the bit-for-bit parity cell uses. Pair it with
    `leg_a_only` as the arms' `wants`.
    """
    win = win if win is not None else fe_boot.BootWindow()

    def provider(pos, W):
        legs, _e2, _w_e2 = fe_boot.chunk_legs(win.q, win.y, pos)
        ob, cb, w_b = legs["base"]
        oc, cc, w_c = legs["calibrated"]
        T = ob.shape[1]
        zeros = np.zeros((W, 1, T), dtype=np.float64)
        return {
            "base": (np.ascontiguousarray(np.concatenate([ob[:, None, :], zeros], axis=1)),
                     np.ascontiguousarray(np.concatenate([cb[:, None, :], zeros], axis=1)), w_b),
            "calibrated": (np.ascontiguousarray(np.concatenate([oc[:, None, :], zeros], axis=1)),
                           np.ascontiguousarray(np.concatenate([cc[:, None, :], zeros], axis=1)), w_c),
        }

    return provider


def leg_a_only(ctx):
    """`wants` for a `chunk_series` row: the engine's leg-A target panel, leg B left at zero."""
    w = np.asarray(ctx, dtype=np.float64)
    return np.ascontiguousarray(np.stack([w, np.zeros_like(w)], axis=-2))


def boot2(
    arms,
    series,
    seed=SEED,
    block=BLOCK,
    n_pre=PRE_BOOT_PATHS,
    n_tax=TAX_BOOT_PATHS,
    capitals=("100k",),
    modes=TAX_MODES,
    m=fe_sim.DEFAULT_M,
    chunk=DEFAULT_CHUNK,
    cash_yield="none",
    rule_index=True,
    label="boot2",
    quiet=True,
    window=None,
):
    """The clean/fresh block bootstrap over two-leg arms.

    `arms` is a list of `{"id": str, "rule": str, "wants": callable(row) -> (W, 2, T)}`;
    `series(pos, W)` returns what the docstring above describes (its `ctx` is handed to `wants`).
    Returns the same shape as the framework's plugin `boot`: raw per-path CAGRs under
    `pre[model][arm]` and `tax[capital][mode][arm]`.
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array([MODE_PROGRESSIVE if md == "progressive" else MODE_FLAT for md in modes],
                        dtype=np.int64)
    ids = [a["id"] for a in arms]
    band = np.array([fe_sim.RULE[a["rule"]]["band"] for a in arms], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[a["rule"]]["usd_rule"] for a in arms], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a["rule"]]["defer"] for a in arms], dtype=np.bool_)
    pre = {md: {a: np.empty(n_pre) for a in ids} for md in ("base", "calibrated")}
    tax = {cl: {md: {a: np.empty(n_tax) for a in ids} for md in modes} for cl in capitals}
    cash_rate = env.cash_rate
    rng = np.random.default_rng(seed)
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
        row = series(pos, W)
        ob, cb, ctx_b = row["base"]
        oc, cc, ctx_c = row["calibrated"]
        wb = np.ascontiguousarray(np.stack([np.asarray(a["wants"](ctx_b), dtype=np.float64)
                                            for a in arms]))
        wc = np.ascontiguousarray(np.stack([np.asarray(a["wants"](ctx_c), dtype=np.float64)
                                            for a in arms]))
        if wb.shape[1] != W:
            raise ValueError(f"wants panels must be (W, 2, T); got {wb.shape} for W = {W}")
        out_pre = np.empty((2, len(arms), W), dtype=np.float64)
        out_at = np.empty((len(capitals), len(modes), len(arms), W), dtype=np.float64)
        _boot_chunk2(
            ob, cb, wb, oc, cc, wc, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
            env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, TAX_CAPITALS["100k"], cap_at,
            win.i0, win.i1, cost, win.yrs, env.Y0, env.M_lo, band, usd_rule, defer, rule_index,
            mode_ids, cash_rate, p0, n_pre, n_tax, DBG0, out_pre, out_at,
        )
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for ai, arm in enumerate(arms):
            for bi, md in enumerate(("base", "calibrated")):
                if npre:
                    pre[md][arm["id"]][p0 : p0 + npre] = out_pre[bi, ai, :npre]
            for ci, cl in enumerate(capitals):
                for mi, md in enumerate(modes):
                    if ntax:
                        tax[cl][md][arm["id"]][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]
        if not quiet:
            print(f"  {label} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)
    return {
        "pre": pre,
        "tax": tax,
        "params": {"seed": seed, "block": block, "n_pre": n_pre, "n_tax": n_tax,
                   "capitals": list(capitals), "modes": list(modes), "m": m,
                   "calendar": [win.dates[0], win.dates[-1]], "cash_yield": cash_yield,
                   "arms": ids},
    }


__all__ = ["DEFAULT_CHUNK", "TAX_MODES", "boot2", "chunk_series"]
