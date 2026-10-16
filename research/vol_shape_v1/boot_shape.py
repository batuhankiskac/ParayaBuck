"""vol_shape_v1 — the shape-aware bootstrap drivers.

The engine's own bootstrap (`fe_boot.boot`) feeds **one** target series to every arm: a study's
arms are `(target weights x fill rule)` and only the fill rule is per-arm there. This study's arms
differ in the target weights, so `boot_shape` / `oos_pooled_boot` pass a per-arm target panel
`(n_arms, n_paths, T)` to a kernel that is `fe_boot._boot_chunk` with the target indexed per arm.

Nothing else changes: the path draws are `fe_boot.draw_positions` (the published draw order), the
draw-to-market reconstruction is `fe_boot.chunk_legs` verbatim, the simulation is
`fe_sim.sim_one`, and the aggregation is `fe_boot.boot`'s. `--harness` asserts that with every arm
on the identity shape `(p = 1, c = 0.35)` the whole dictionary is bit-identical to
`fe_boot.boot`'s, i.e. the option is off by construction, not merely off by convention.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_data, fe_signals, fe_sim
from research.fast_engine.fe_config import EXP_Q, FIN, TAX_BOOT_EXT, TAX_BOOT_L, TAX_CAPITALS
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE
from research.vol_shape_v1 import shapes

TAX_MODES = ("progressive", "flat")
DEFAULT_CHUNK = 256
DBG0 = np.zeros((0, 6), dtype=np.float64)  # the engine's bisect buffer, always off
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = 252 + 2520  # ewma_band_v1: TSM_N + trv2.MEASURED


# ----------------------------------------------------------------------------- kernels
@njit(parallel=True, cache=True, nogil=True)
def _boot_chunk_shape(
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
    mode_ids,
    p0,
    n_pre,
    n_tax,
    out_pre,
    out_at,
    dbg0,
):
    """`fe_boot._boot_chunk` with a per-arm target panel (`wq_*` is `(n_arms, W, T)`).

    `out_pre` is `(2, n_arms, W)` (base, calibrated), `out_at` is `(n_caps, n_modes, n_arms, W)`.
    """
    rate = np.zeros(0, dtype=np.float64)
    # `dbg0` is the engine's bisect buffer, always shaped (0, 6) so `sim_one` keeps it off. It is
    # an argument, not a local `np.zeros((0, 6))`: a zero-size allocation inside a kernel makes
    # `dbg_on` compare against a folded constant and numba then indexes the empty buffer (bounds
    # error / heap corruption) -- the engine allocates it in Python for the same reason.
    W = oq_b.shape[0]
    n_arms = wq_b.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                    oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, True, True, True, MODE_PRE_TAX, True, Y0,
                    M_lo, rate, False, dbg0,
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                    oq_c[p], cq_c[p], wq_c[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, True, True, True, MODE_PRE_TAX, True, Y0,
                    M_lo, rate, False, dbg0,
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h = fe_sim.sim_one(
                            oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, True, True, True, mode,
                            True, Y0, M_lo, rate, False, dbg0,
                        )
                        out_at[ci, mi, a, p] = (scal[fe_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1


@njit(parallel=True, cache=True, nogil=True)
def _boot_pt_shape(so, sc, wq, fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr, pay_yr, pay_part,
                   i0, i1, cost, Y0, M_lo, out, dbg0):
    """Pre-tax, one arm set, `prange` over paths: the OOS pooled bootstrap's inner loop."""
    rate = np.zeros(0, dtype=np.float64)
    k = i1 - i0 + 1
    n_arms = wq.shape[0]
    for p in prange(so.shape[0]):
        for a in range(n_arms):
            nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                so[p], sc[p], wq[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr, pay_yr,
                pay_part, 1.0, i0, i1, cost, True, True, True, MODE_PRE_TAX, True, Y0, M_lo, rate,
                False, dbg0,
            )
            out[a, p] = (nav[k] / nav[0]) ** (252 / k) - 1


# ----------------------------------------------------------------------------- target panels
def chunk_panels(q, y, pos, shapes_of, arms=None):
    """`fe_boot.chunk_legs` (verbatim reconstruction) plus the per-arm target panels.

    `shapes_of[arm]` is `(p, c)`; `arms` selects (and orders) the panels, which must be exactly the
    arms the kernel is asked to run. Returns `(base, calibrated)` panels, each `(n_arms, W, T)`,
    and the arms' price series used for the state machine.
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
    vol, vol_c = fe_signals.vol_n(sc), fe_signals.vol_n(sc_c)
    arms = list(shapes_of) if arms is None else list(arms)
    base = np.stack([shapes.apply_shape(on_eff, factor, vol, *shapes_of[a]) for a in arms])
    cal = np.stack([shapes.apply_shape(on_eff, factor, vol_c, *shapes_of[a]) for a in arms])
    return (so, sc, base), (so_c, sc_c, cal)


def boot_shape(
    arms,
    shapes_of,
    n_pre=5000,
    n_tax=1000,
    capitals=("100k", "1M"),
    modes=TAX_MODES,
    seed=20261011,
    block=20,
    m=2,
    chunk=DEFAULT_CHUNK,
    window=None,
    keep_paths=False,
    quiet=True,
):
    """`fe_boot.boot` with per-arm target weights. Returns `fe_boot.boot`'s dictionary."""
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    n_arms = len(arms)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array([MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT for mode in modes], dtype=np.int64)
    pre = {(mdi, a): np.empty(n_pre, dtype=np.float64) for mdi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    rng = np.random.default_rng(seed)
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, TAX_BOOT_L, win.npool, TAX_BOOT_EXT)
        (so, sc, wq_b), (so_c, sc_c, wq_c) = chunk_panels(win.q, win.y, pos, shapes_of, arms)
        out_pre = np.empty((2, n_arms, W), dtype=np.float64)
        out_at = np.empty((len(capitals), len(modes), n_arms, W), dtype=np.float64)
        _boot_chunk_shape(
            np.ascontiguousarray(so), np.ascontiguousarray(sc), np.ascontiguousarray(wq_b),
            np.ascontiguousarray(so_c), np.ascontiguousarray(sc_c), np.ascontiguousarray(wq_c),
            env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr,
            env.pay_yr, env.pay_part, TAX_CAPITALS["100k"], cap_at, win.i0, win.i1, cost, win.yrs,
            env.Y0, env.M_lo, mode_ids, p0, n_pre, n_tax, out_pre, out_at, DBG0,
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
        if not quiet:
            print(f"  b12 path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    ctrl = arms[0]
    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
           "arms": list(arms), "calendar_start": win.dates[0], "calendar_end": win.dates[-1],
           "pre": {}, "tax": {}}
    for mdi in ("base", "calibrated") if n_pre > 0 else ():
        base = pre[(mdi, ctrl)]
        out["pre"][mdi] = {}
        for arm in arms:
            c = pre[(mdi, arm)]
            d = c - base
            out["pre"][mdi][arm] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_ctrl_median_pp": float(np.median(d) * 100),
                "diff_vs_ctrl_mean_pp": float(np.mean(d) * 100),
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


# ----------------------------------------------------------------------------- OOS pooled bootstrap
def draws_reference(rng, block, n_paths, length, npool):
    """Independent numpy re-implementation of the published draw (`band_h2_combo_v1.boot`'s three
    rng calls per path, in order) -- the reference `fe_boot.draw_positions` is asserted against."""
    out = np.empty((n_paths, length), dtype=np.int64)
    for p in range(n_paths):
        pos = int(rng.integers(0, npool, 1)[0])
        out[p, 0] = pos
        jump = rng.random((1, length)) < 1.0 / block
        fresh = rng.integers(0, npool, (1, length))
        for t in range(1, length):
            if jump[0, t]:
                pos = int(fresh[0, t])
            else:
                pos = (pos + 1) % npool
            out[p, t] = pos
    return out


def oos_pooled_boot(arms, shapes_of, markets, per_market=OOS_PATHS_PER_MARKET, seed=20261011,
                    block=20, m=2, chunk=500, quiet=True):
    """The pooled OOS bootstrap (G8): M1..M5, `per_market` paths each, one shared rng.

    Draw structure and market reconstruction are `ewma_band_v1.screen.boot_oos_market`'s (pool
    `arange(start, len(days))`, `L = 252 + 2520`, block 20, seed 20261011, no back-extension);
    the simulation is production K's execution layer (the engine's `sim_one` with
    band + USD gain fraction + December deferral), not `simulate_band`.

    Calendar convention: `ewma_band_v1`'s pre-tax simulator ignored dates, but K's H2 needs a
    December definition, so each market's resampled paths are run on a fixed calendar of that
    market's own last `L` sessions (`days[-L:]`), the same construction as `fe_boot.BootWindow`
    (fixed calendar, resampled market data), with the drawn data attached to it.
    """
    rng = np.random.default_rng(seed)
    per = {k: {a: [] for a in arms} for k in markets}
    for key in markets:
        days, o, c, y, start = fe_data.load_market(key)
        pool_start, npool = start, len(days) - start
        cal = days[-OOS_BOOT_L:]
        env = fe_sim.RunEnv(cal, *fe_data.usd_market(cal), 253, OOS_BOOT_L - 1)
        for c0 in range(0, per_market, chunk):
            P = min(chunk, per_market - c0)
            pos = fe_boot.draw_positions(rng, block, P, OOS_BOOT_L, npool, 0)
            idx = pos + pool_start
            r_on = o[idx] / c[idx - 1]
            r_id = c[idx] / o[idx]
            cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
            oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
            yy = y[idx - 1]
            yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
            e2 = fe_signals.e2_up(cc)
            so, sc = fe_signals.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + 0.0075, EXP_Q)
            comps = fe_signals.early_components(cc)
            on_eff, factor = fe_signals.r2_state(cc, e2, comps)
            vol = fe_signals.vol_n(sc)
            wq = np.stack([shapes.apply_shape(on_eff, factor, vol, *shapes_of[a]) for a in arms])
            out = np.empty((len(arms), P), dtype=np.float64)
            _boot_pt_shape(
                np.ascontiguousarray(so), np.ascontiguousarray(sc), np.ascontiguousarray(wq),
                env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr,
                env.pay_yr, env.pay_part, 253, OOS_BOOT_L - 1, fe_sim.COST_PER_SIDE * m, env.Y0,
                env.M_lo, out, DBG0,
            )
            for ai, a in enumerate(arms):
                per[key][a].append(out[ai])
        if not quiet:
            print(f"  oos boot {key}: {per_market} paths", flush=True)
    per = {k: {a: np.concatenate(per[k][a]) for a in arms} for k in markets}
    ctrl = arms[0]
    pooled = {a: np.concatenate([per[k][a] for k in markets]) for a in arms}
    out = {"seed": seed, "block": block, "paths_per_market": per_market, "markets": list(markets),
           "per_market": {}, "pooled": {}}
    for k in markets:
        out["per_market"][k] = {
            a: {"cagr_median": float(np.median(per[k][a])),
                "median_delta_pp": float(np.median(per[k][a] - per[k][ctrl]) * 100)}
            for a in arms
        }
    for a in arms:
        d = pooled[a] - pooled[ctrl]
        out["pooled"][a] = {
            "diff_vs_ctrl_median_pp": float(np.median(d) * 100),
            "diff_vs_ctrl_mean_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)),
            "paths": int(len(d)),
        }
    return out
