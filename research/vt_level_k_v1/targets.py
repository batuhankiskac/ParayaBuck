"""vt_level_k_v1 targets -- production's signal pipeline with a study-local vol-target level.

The only change a study arm makes is `vt` in `w = on_eff * factor * min(1, vt / vol20)`, capped at
0.98. Everything else (E2 vote, early re-entry state, synthetic 2x leg, financing) is
`research.fast_engine`'s. `sized_wants(..., vt=VOL_TARGET)` is asserted bitwise equal to
`fe_signals.sized_wants` in `--harness`; the local bootstrap below consumes the same path draws
and the same numba chunk kernel as `fe_boot.boot` and is asserted bitwise equal to it at 0.35.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_boot, fe_data, fe_signals
from research.fast_engine.fe_config import CAP, EXP_Q, FIN, VOL_TARGET
from research.fast_engine.fe_sim import RULE


def vt_of(vol, vt=VOL_TARGET):
    """`fe_signals.vt_of` with the target level as a parameter."""
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, vt / np.where(v > 0, v, 1.0)), 1.0)


def sized_wants(on_eff, factor, vol_instr, vt=VOL_TARGET):
    """`fe_signals.sized_wants` with `vt` as a parameter (CAP and the form are unchanged)."""
    wt = vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)), vt)
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * wt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def arm_targets(price, e2, vol_instr, vts, comps=None):
    """{arm label: target weights} from one state pass; only `vt` differs between arms."""
    comps = fe_signals.early_components(price) if comps is None else comps
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    return {arm: sized_wants(on_eff, factor, vol_instr, vt) for arm, vt in vts.items()}


def main_inputs_vt(spread, vts):
    """`fe_data.main_inputs` plus every arm's own target series and the E2 anchor."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price = q[1]
    e2 = fe_signals.e2_up(price)[0]
    vol_instr = fe_signals.vol_n(c)[0]
    comps = fe_signals.early_components(price)
    wants = arm_targets(price, e2, vol_instr, vts, comps)
    w_e2 = sized_wants(e2, np.ones_like(wants["K"]), vol_instr, VOL_TARGET)
    return days, o, c, e2, wants, w_e2


def chunk_legs(q, y, pos, vts):
    """`fe_boot.chunk_legs` with per-arm target weights (same draw-to-market reconstruction)."""
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
    so, sc = fe_signals.synthetic_2x(qo, qc, zeros, yyp + FIN["base"], EXP_Q)
    so_c, sc_c = fe_signals.synthetic_2x(qo, qc, zeros, yyp + FIN["calibrated"], EXP_Q)
    vol_b = fe_signals.vol_n(sc)
    vol_c = fe_signals.vol_n(sc_c)
    w_b = {a: sized_wants(on_eff, factor, vol_b, vt) for a, vt in vts.items()}
    w_c = {a: sized_wants(on_eff, factor, vol_c, vt) for a, vt in vts.items()}
    w_e2 = sized_wants(e2, np.ones_like(factor), vol_b, VOL_TARGET)
    return so, sc, so_c, sc_c, w_b, w_c, w_e2


def boot(
    arms,
    vts,
    seed,
    block,
    n_pre,
    n_tax,
    capitals,
    modes,
    m=2,
    chunk=256,
    window=None,
    quiet=False,
):
    """`fe_boot.boot` with an arm-specific target series. Same draws, same numba chunk kernel.

    Each arm is simulated through `fe_boot._boot_chunk` with `n_arms = 1`, so the only difference
    from the engine's own bootstrap is which target array the arm reads. At `vt = 0.35` the whole
    dictionary is bitwise equal to `fe_boot.boot` (asserted in `--harness`).
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([fe_boot.TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    cost = fe_boot.fe_sim.COST_PER_SIDE * m
    mode_ids = np.array(
        [fe_boot.MODE_PROGRESSIVE if mode == "progressive" else fe_boot.MODE_FLAT for mode in modes],
        dtype=np.int64,
    )
    pre = {(mdi, a): np.empty(n_pre, dtype=np.float64) for mdi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    anchor = {mode: np.empty(n_tax, dtype=np.float64) for mode in modes}
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    rng = np.random.default_rng(seed)

    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_boot.TAX_BOOT_L, win.npool, fe_boot.TAX_BOOT_EXT)
        so, sc, so_c, sc_c, w_b, w_c, w_e2 = chunk_legs(win.q, win.y, pos, vts)
        for ai, arm in enumerate(arms):
            spec = RULE["C0"] if arm == "R2" else RULE["K"]
            band = np.array([spec["band"]], dtype=np.bool_)
            usd = np.array([spec["usd_rule"]], dtype=np.bool_)
            defer = np.array([spec["defer"]], dtype=np.bool_)
            out_pre = np.empty((2, 1, W), dtype=np.float64)
            out_at = np.empty((len(capitals), len(modes), 1, W), dtype=np.float64)
            out_anchor = np.empty((len(modes), W), dtype=np.float64)
            fe_boot._boot_chunk(
                so,
                sc,
                w_b[arm],
                so_c,
                sc_c,
                w_c[arm],
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
                fe_boot.TAX_CAPITALS["100k"],
                cap_at,
                win.i0,
                win.i1,
                cost,
                win.yrs,
                env.Y0,
                env.M_lo,
                band,
                usd,
                defer,
                mode_ids,
                p0,
                n_pre,
                n_tax,
                ai == 0,
                out_pre,
                out_at,
                out_anchor,
                dbg0,
            )
            npre = max(0, min(W, n_pre - p0))
            ntax = max(0, min(W, n_tax - p0))
            for bi, mdi in enumerate(("base", "calibrated")):
                if npre:
                    pre[(mdi, arm)][p0 : p0 + npre] = out_pre[bi, 0, :npre]
            for ci, cl in enumerate(capitals):
                for mi, mode in enumerate(modes):
                    if ntax:
                        at[cl][mode][arm][p0 : p0 + ntax] = out_at[ci, mi, 0, :ntax]
            if ai == 0:
                for mi, mode in enumerate(modes):
                    if ntax:
                        anchor[mode][p0 : p0 + ntax] = out_anchor[mi, :ntax]
        if not quiet:
            print(f"  {seed}/{block} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

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
    for mdi in ("base", "calibrated"):
        base = pre[(mdi, "K")]
        out["pre"][mdi] = {}
        for arm in arms:
            c = pre[(mdi, arm)]
            d = c - base
            out["pre"][mdi][arm] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_K_median_pp": float(np.median(d) * 100),
                "diff_vs_K_mean_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": fe_boot.TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in ("K",):
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
    out["anchor_e2_100k"] = {
        mode: {"cagr_median": float(np.median(anchor[mode]))} for mode in modes
    }
    return out
