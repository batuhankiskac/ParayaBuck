"""valuation_v1 kernel option: the block bootstrap with a **per-arm** target series.

`research/fast_engine/fe_boot.boot` drives every arm of one bootstrap from a single target-weight
array (`w_r2`), because every published arm of the K campaign differs in its *fill rule*, not in
its targets. The valuation arms differ only in their targets (K's rule, a valuation-scaled target),
so the bootstrap needs one wants row per arm. This module is a copy of `fe_boot` with exactly that
change:

  * `fe_boot.draw_positions` is imported and called unchanged, so the drawn paths are the
    published ones for any (seed, block), path for path;
  * `chunk_legs_multi` is `fe_boot.chunk_legs` with the multiplier applied to `raw` before the
    0.98 cap, stacked over arms;
  * `_boot_chunk_multi` is `fe_boot._boot_chunk` with `wq_*` gaining a leading arm axis and
    `wq_b[a, p]` in place of `wq_b[p]`; the anchor leg (E2, unused by this study) is dropped.

`parity_multi_arms()` proves the option is inert when it is off: with `mult == 1.0` for the single
arm K it must reproduce `fe_boot.boot`'s dictionary **exactly** (both blocks, every cell).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_config, fe_signals, fe_sim
from research.fast_engine.fe_boot import TAX_BOOT_EXT, TAX_BOOT_L, draw_positions
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE


def sized_mult(on_eff, factor, vol, mult=None):
    """`fe_signals.sized_wants` with an optional per-session multiplier on `raw` (before the cap).

    `mult=None` is bit-identical to `fe_signals.sized_wants` (asserted in `screen.py`).
    """
    vt = fe_signals.vt_of(np.atleast_2d(np.asarray(vol, dtype=float)))
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    if mult is not None:
        raw = raw * np.atleast_2d(np.asarray(mult, dtype=float))
    fcap = np.where(raw > fe_config.CAP, fe_config.CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def chunk_legs_multi(q, y, pos, mults):
    """`fe_boot.chunk_legs` for a list of per-arm multipliers; returns per-model (o, c, W[arm]).

    `mults` is a sequence of (P, T) multiplier panels (or scalars) — one row per arm, time on the
    last axis, aligned with the reconstructed path.
    """
    idx = pos + 1
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
    out = {}
    for model, spread in fe_config.FIN.items():
        so, sc = fe_signals.synthetic_2x(qo, qc, zeros, yyp + spread, fe_config.EXP_Q)
        vol = fe_signals.vol_n(sc)
        stack = np.stack([sized_mult(on_eff, factor, vol, mu) for mu in mults], axis=0)
        out[model] = (so, sc, stack)
    return out


@njit(parallel=True, cache=True, nogil=True)
def _boot_chunk_multi(
    oq_b,
    cq_b,
    wq_b,
    oq_c,
    cq_c,
    wq_c,
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
    mode_ids,
    p0,
    n_pre,
    n_tax,
    out_pre,
    out_at,
):
    """`fe_boot._boot_chunk` with `wq_*` shaped (n_arms, W, T); the anchor leg is dropped."""
    rate = np.zeros(0, dtype=np.float64)
    W = oq_b.shape[0]
    n_arms = band.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                for bi in range(2):
                    if bi == 0:
                        nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                            oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a],
                            defer[a], MODE_PRE_TAX, True, Y0, M_lo, rate, False, dbg0,
                        )
                    else:
                        nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                            oq_c[p], cq_c[p], wq_c[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a],
                            defer[a], MODE_PRE_TAX, True, Y0, M_lo, rate, False, dbg0,
                        )
                    out_pre[bi, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h = fe_sim.sim_one(
                            oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a], usd_rule[a],
                            defer[a], mode, True, Y0, M_lo, rate, False, dbg0,
                        )
                        out_at[ci, mi, a, p] = (scal[fe_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1


def boot_arms(
    mults,
    arms,
    rules=None,
    seed=fe_config.SEED,
    block=fe_config.BLOCK,
    n_pre=fe_config.PRE_BOOT_PATHS,
    n_tax=fe_config.TAX_BOOT_PATHS,
    capitals=("100k", "1M"),
    modes=("progressive", "flat"),
    m=fe_sim.DEFAULT_M,
    chunk=fe_boot.DEFAULT_CHUNK,
    quiet=False,
    window=None,
    label="boot",
    keep_paths=False,
):
    """`fe_boot.boot` with one target-weight multiplier panel per arm.

    The returned dictionary has the same shape as `fe_boot.boot`'s (with `arms` and the
    `{arm}_vs_{arm0}` delta keys), so a study can drop it into RESULTS.json unchanged.
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    i0, i1 = win.i0, win.i1
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([fe_config.TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    n_arms = len(arms)
    rule = [a if rules is None else rules[a] for a in arms]  # all valuation arms run K's fill rule
    band = np.array([fe_sim.RULE[r]["band"] for r in rule], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[r]["usd_rule"] for r in rule], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[r]["defer"] for r in rule], dtype=np.bool_)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array(
        [MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT for mode in modes], dtype=np.int64
    )

    pre = {(mi, a): np.empty(n_pre, dtype=np.float64) for mi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    rng = np.random.default_rng(seed)

    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = draw_positions(rng, block, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
        panels = []
        for a in arms:
            mu = np.asarray(mults[a], dtype=np.float64).ravel()
            if mu.shape[0] == len(win.dates):
                mu = np.concatenate([mu, mu[-1:]])  # the series carries one extra (liquidation) column
            if mu.shape[0] != len(win.dates) + 1:
                raise ValueError(f"multiplier for {a} must cover the {len(win.dates)}-session calendar")
            panels.append(np.broadcast_to(mu[None, :], (W, mu.shape[0])))
        legs = chunk_legs_multi(win.q, win.y, pos, panels)
        so, sc, wq_b = legs["base"]
        so_c, sc_c, wq_c = legs["calibrated"]
        out_pre = np.empty((2, n_arms, W), dtype=np.float64)
        out_at = np.empty((len(capitals), len(modes), n_arms, W), dtype=np.float64)
        _boot_chunk_multi(
            so, sc, np.ascontiguousarray(wq_b), so_c, sc_c, np.ascontiguousarray(wq_c),
            env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
            env.pay_ptr, env.pay_yr, env.pay_part, fe_config.TAX_CAPITALS["100k"], cap_at,
            win.i0, win.i1, cost, win.yrs, env.Y0, env.M_lo, band, usd_rule, defer, mode_ids,
            p0, n_pre, n_tax, out_pre, out_at,
        )
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for bi, mi in enumerate(("base", "calibrated")):
            for ai, a in enumerate(arms):
                if npre:
                    pre[(mi, a)][p0 : p0 + npre] = out_pre[bi, ai, :npre]
        for ci, cl in enumerate(capitals):
            for mi, mode in enumerate(modes):
                for ai, a in enumerate(arms):
                    if ntax:
                        at[cl][mode][a][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]
        if not quiet:
            print(f"  {label} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    out = {
        "n_paths_pre": n_pre,
        "n_paths_tax": n_tax,
        "seed": seed,
        "block": block,
        "arms": list(arms),
        "calendar_start": win.dates[0],
        "calendar_end": win.dates[-1],
        "pre": {},
        "tax": {},
    }
    ctrl = arms[0]
    for mdi in (("base", "calibrated") if n_pre > 0 else ()):
        base = pre[(mdi, ctrl)]
        out["pre"][mdi] = {}
        for arm in arms:
            d = pre[(mdi, arm)] - base
            out["pre"][mdi][arm] = {
                "cagr_median": float(np.median(pre[(mdi, arm)])),
                f"diff_vs_{ctrl}_median_pp": float(np.median(d) * 100),
                f"diff_vs_{ctrl}_mean_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": fe_config.TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            base = at[cl][mode][ctrl]
            for arm in arms:
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
    if keep_paths:
        out["paths"] = {"pre": pre, "tax": at}
    return out


def parity_multi_arms(window=None, quiet=True) -> dict:
    """The option-off parity cell: all multipliers 1.0, one arm -> `fe_boot.boot` exactly."""
    win = window if window is not None else fe_boot.BootWindow()
    ones = np.ones(len(win.dates))
    pair = ("C0", "K")  # the engine's boot indexes its pre-tax baseline by CONTROL
    ref = fe_boot.boot(
        seed=fe_config.SEED,
        block=fe_config.BLOCK,
        arms=pair,
        n_pre=200,
        n_tax=100,
        capitals=("100k",),
        modes=("progressive", "flat"),
        anchor_e2=False,
        deltas=("K",),
        baselines=("C0",),
        label="parity-ref",
        window=win,
        quiet=quiet,
        keep_paths=True,
    )
    got = boot_arms(
        {"C0": ones, "K": ones},
        pair,
        seed=fe_config.SEED,
        block=fe_config.BLOCK,
        n_pre=200,
        n_tax=100,
        capitals=("100k",),
        modes=("progressive", "flat"),
        label="parity-new",
        window=win,
        quiet=quiet,
        keep_paths=True,
    )
    diffs = []
    for model in ("base", "calibrated"):
        for arm in pair:
            a = ref["paths"]["pre"][(model, arm)]
            b = got["paths"]["pre"][(model, arm)]
            diffs.append(float(np.max(np.abs(a - b))) if len(a) else 0.0)
    for mode in ("progressive", "flat"):
        for arm in pair:
            a = ref["paths"]["tax"]["100k"][mode][arm]
            b = got["paths"]["tax"]["100k"][mode][arm]
            diffs.append(float(np.max(np.abs(a - b))))
    for k in ("cagr_median", "diff_vs_C0_median_pp", "diff_vs_C0_mean_pp", "share_gt_0"):
        diffs.append(float(abs(ref["pre"]["base"]["K"][k] - got["pre"]["base"]["K"][k])))
    diffs.append(float(abs(ref["tax"]["100k"]["progressive"]["K_vs_C0"]["median_delta_cagr_pp"]
                           - got["tax"]["100k"]["progressive"]["K_vs_C0"]["median_delta_cagr_pp"])))
    return {
        "seed": fe_config.SEED,
        "block": fe_config.BLOCK,
        "paths_pre": 200,
        "paths_tax": 100,
        "max_abs_diff": max(diffs),
        "pass": max(diffs) == 0.0,
    }


__all__ = ["sized_mult", "chunk_legs_multi", "boot_arms", "parity_multi_arms"]
