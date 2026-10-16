#!/usr/bin/env python3
"""vol_shape_v1 screen — I2 exposure-shape replication at matched average weight (see PREREGISTRATION.md).

Modes
  --smoke       synthetic prices only: the shape identity, the target-panel identity against
                `fe_boot.chunk_legs`, the per-arm bootstrap kernel against `fe_sim.simulate`, and the
                OOS draw equivalence. Simulates no arm on real data.
  --harness     the engine's parity check (`python -m research.fast_engine.parity --quick`) and K's
                published anchors (band_h2_combo_v1/RESULTS.json), plus the identity checks that make
                the shape option provably inert at (p = 1, c = 0.35) on real data, and the pre-tax
                bootstrap parity against `fe_boot.boot` (option off == engine, bit for bit).
  --calibrate   solve the c_p constants on 1985-10-01 -> 2000-03-08 and print them.
  --run         harness, then every stage, the gates and the verdicts; writes RESULTS.json and
                SUMMARY.txt. Run twice; RESULTS.json must be byte-identical.

Every arm runs production K's execution layer (`arm="K"`): only the target-weight array differs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_tax import MODE_PROGRESSIVE
from research.fast_engine.fe_config import (
    EXP_Q,
    EXTRA_FIN,
    FIN,
    GATE_CAPITAL,
    M,
    M_STRESS,
    OOS_WINDOWS,
    START,
    TAX_CAPITALS,
    TAX_WINDOWS,
)
from research.vol_shape_v1 import boot_shape, shapes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
REF_BAND_H2 = ROOT / "research/band_h2_combo_v1/RESULTS.json"

ARMS = ("C0", "E1", "E2", "E3", "E4")
CTRL = "C0"
DATA_ARMS = tuple(a for a in ARMS if a != CTRL)
TOL_PP = 0.01  # the harness anchor tolerance (pp)

# the c_p constants frozen by PREREGISTRATION.md (solved once on 1985-10-01 -> 2000-03-08)
PRE_C = {
    "C0": 0.35,
    "E1": 0.42445881653676754,
    "E2": 0.29196422627125695,
    "E3": 0.24593188954326456,
    "E4": 0.17706308553599898,
}

# K's published anchors (research/band_h2_combo_v1/RESULTS.json, m = 2 unless stated)
ANCHORS = {
    "pre_base_m2_cagr": 0.157649,
    "pre_base_m2_max_dd": 0.466698,
    "pre_base_m2_turnover": 5.456136,
    "pre_cal_m2_cagr": 0.151785,
    "pre_cal_m2_max_dd": 0.467948,
    "pre_base_m1_cagr": 0.160173,
    "pre_cal_m1_cagr": 0.154303,
    "pre_base_m2_2017_end": 0.332306,
    "at_100k_2000": 0.107533,
    "at_100k_2010": 0.152354,
    "at_100k_2017": 0.239720,
    "boot_pre_base_K_vs_C0": -0.007121,
    "boot_pre_cal_K_vs_C0": -0.011741,
    "boot_tax_prog_K_vs_C0": 0.285562,
    "boot_tax_prog_K_vs_C0_share": 0.75,
    "boot_tax_flat_K_vs_C0": 0.131071,
    "boot_tax_flat_K_vs_C0_share": 0.64,
}

GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9"]
AT_PRE_AT_MARGIN = 0.0  # after-tax gate: beats K in all three windows
NONINF_MARGIN = 0.0005  # tier-N pre-tax non-inferiority margin: -0.05 pp


def rnd(x):
    """`trend_robustness_v2.rnd`: round every float leaf to 6 dp before serialising."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: rnd(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v) for v in x]
    return x


# ----------------------------------------------------------------------------- weights per arm
IDENTITY_SHAPES = {CTRL: (1.0, PRE_C[CTRL]), "K": (1.0, PRE_C[CTRL])}  # for the K-anchor parity run


def shape_of(cal) -> dict:
    """{arm: (p, c)}: the arm's exponent and its pre-2000-calibrated constant."""
    out = {CTRL: (1.0, PRE_C[CTRL])}
    for arm, p in shapes.P_LIST.items():
        out[arm] = (p, PRE_C[arm])
    return out


def main_weights(shapes_of) -> dict:
    """{model: {arm: wants}} on the main window (financing-model-specific synthetic 2x leg)."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    e2 = fe_signals.e2_up(q[1])[0]
    comps = fe_signals.early_components(q[1])
    on_eff, factor = fe_signals.r2_state(q[1], e2, comps)
    out = {}
    for md, spread in FIN.items():
        _d, _o, c_, _we, _wr, _e = fe_data.main_inputs(spread)
        vol = fe_signals.vol_n(c_)[0]
        out[md] = {a: shapes.apply_shape(on_eff, factor, vol, *shapes_of[a]) for a in ARMS}
    return out


# ----------------------------------------------------------------------------- stages
def main_stage(shapes_of) -> dict:
    """Pre-tax main window: base/calibrated x m = 1/2/4, plus the exposure diagnostics."""
    days, o, c, _we, _wr, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    W = main_weights(shapes_of)
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(T - START + 1),
                      "years": float(fe_sim.calendar_years(days[START - 1], days[-1]))},
           "arms": {}}
    meas = slice(START - 1, T)  # the target sessions the measured window's decisions read
    mw = {a: float(np.mean(W["base"][a][meas])) for a in ARMS}
    for md, spread in FIN.items():
        d_, o_, c_, _we, _wr, _e = fe_data.main_inputs(spread)
        for a in ARMS:
            rec = out["arms"].setdefault(a, {"mean_weight": mw[a],
                                             "mean_abs_dw": float(np.mean(np.abs(np.diff(W["base"][a][meas])))), "cells": {}})
            for m in (1, 2, M_STRESS):
                r = fe_sim.simulate(o_, c_, W[md][a], env, TAX_CAPITALS[GATE_CAPITAL], START, T,
                                    arm="K", mode="pre_tax", m=m)
                rec["cells"][f"{md}_m{m}"] = fe_study.pre_stats(d_, START, T, r)
    ck = out["arms"][CTRL]["cells"]["base_m2"]["cagr"]
    for a in ARMS:
        rec = out["arms"][a]
        rec["mean_weight_drift_pp"] = (rec["mean_weight"] - mw[CTRL]) * 100
        rec["exposure_normalised_cagr"] = float((1 + rec["cells"]["base_m2"]["cagr"]) ** (mw[CTRL] / rec["mean_weight"]) - 1)
        rec["exposure_normalised_cagr_delta_pp"] = (rec["exposure_normalised_cagr"] - ck) * 100
    return out


def after_tax_stage(shapes_of, capitals=("10k", "100k", "1M")) -> dict:
    """After tax (progressive, liquidated, base financing, m = 2) on the three windows."""
    days, o, c, _we, _wr, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    W = main_weights(shapes_of)["base"]
    out = {}
    for cap_label in capitals:
        out[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            i0, i1 = fe_study.window_indices(days, a, b)
            env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
            out[cap_label][wn] = {}
            for arm in ARMS:
                rq = fe_sim.simulate(o, c, W[arm], env, TAX_CAPITALS[cap_label], i0, i1, arm="K", mode="progressive")
                rp = fe_sim.simulate(o, c, W[arm], env, 1.0, i0, i1, arm="K", mode="pre_tax")
                out[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "tax_funding_sales": rq["tax_funding_sales"],
                    "postponements": rq["postponements"],
                    "blocked_band": rq["blocked_band"],
                    "deferred": rq["deferred"],
                    "index_relief_try": rq["index_relief_try"],
                }
    return out


def cost_stress_stage(shapes_of) -> dict:
    """m = 4 after-tax stress at the gate capital (report only)."""
    days, o, c, _we, _wr, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    W = main_weights(shapes_of)["base"]
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        out[wn] = {}
        for arm in ARMS:
            rq = fe_sim.simulate(o, c, W[arm], env, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm="K",
                                 mode="progressive", m=M_STRESS)
            out[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                            "max_dd_after_tax": rq["max_dd_nav_usd"]}
    return out


def oos_stage(shapes_of) -> dict:
    """The six OOS windows (pre-tax, calibrated, m = 2), own E2 + own early path, R-USD rule."""
    oos = {}
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    oos["GSPC_1957_1999"] = _oos_window(gd, go, gc, gy, gstart, shapes_of)
    for key in shapes.OOS_MARKETS:
        md_, mo_, mc_, my_, mstart_ = fe_data.load_market(key)
        oos[key] = _oos_window(md_, mo_, mc_, my_, mstart_, shapes_of)
    for wn in OOS_WINDOWS:
        for arm in ARMS:
            if arm == CTRL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CTRL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CTRL]["max_dd"]) * 100
    return oos


def _oos_window(days, o, c, y, start, shapes_of) -> dict:
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    on_eff, factor = fe_signals.r2_state(c, e2, comps)
    vol = fe_signals.vol_n(sc)[0]
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in ARMS:
        w = shapes.apply_shape(on_eff, factor, vol, *shapes_of[arm])
        r = fe_sim.simulate(so, sc, w, env, 1.0, start, len(days) - 1, arm="K", mode="pre_tax", m=M,
                            rule_index=False)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


def boot_stage(shapes_of) -> dict:
    """The clean bootstrap (seed 20261011, block 20, 5,000 pre / 1,000 after-tax paths)."""
    arms = list(ARMS)
    return boot_shape.boot_shape(arms, shapes_of, n_pre=5000, n_tax=1000, capitals=("100k", "1M"),
                                 modes=("progressive", "flat"), seed=20261011, block=20, m=M,
                                 keep_paths=False, quiet=False)


def oos_boot_stage(shapes_of) -> dict:
    return boot_shape.oos_pooled_boot(list(ARMS), shapes_of, shapes.OOS_MARKETS, quiet=False)


def decision_diff_stage(shapes_of) -> dict:
    """Report only: fill-decision differences of each arm vs K on the main pre-tax path."""
    days, o, c, _we, _wr, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    W = main_weights(shapes_of)["base"]
    tr = {a: fe_sim.simulate(o, c, W[a], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm="K",
                             mode="pre_tax", trace=True) for a in ARMS}
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(T - START + 1)},
           "sells": {a: int(np.sum(tr[a]["_dec"] == 2)) for a in ARMS}, "vs_C0": {}}
    d0 = tr[CTRL]["_dec"]
    for a in DATA_ARMS:
        da = tr[a]["_dec"]
        out["vs_C0"][a] = {
            "sessions_decision_differs": int(np.sum(da != d0)),
            "share_decision_differs": float(np.mean(da != d0)),
            "sells_where_C0_holds": int(np.sum((da == 2) & (d0 != 2))),
            "holds_where_C0_sells": int(np.sum((da != 2) & (d0 == 2))),
            "share_target_differs_gt_0_5pp": float(np.mean(np.abs(W[a][START - 1 : T] - W[CTRL][START - 1 : T]) > 0.005)),
        }
    return out


# ----------------------------------------------------------------------------- gates
def g_return(a, c):
    """G1/G6: CAGR >= C0, or Calmar >= 1.1 x C0 with CAGR >= C0 - 0.5 pp."""
    return bool(a["cagr"] >= c["cagr"] or (a["calmar"] >= 1.10 * c["calmar"] and a["cagr"] >= c["cagr"] - 0.005))


def pre_tax_gates(main, boot, oos, oosboot, arm) -> dict:
    r = {}
    for md in FIN:
        a2, c2 = main["arms"][arm]["cells"][f"{md}_m2"], main["arms"][CTRL]["cells"][f"{md}_m2"]
        a1, c1 = main["arms"][arm]["cells"][f"{md}_m1"], main["arms"][CTRL]["cells"][f"{md}_m1"]
        r[f"G1_{md}"] = g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01 + 1e-12)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01 - 1e-12)
        r[f"G5_{md}"] = bool(boot["pre"][md][arm]["diff_vs_ctrl_median_pp"] >= 0)
        r[f"G6_{md}"] = g_return(a1, c1)
    a2, c2 = main["arms"][arm]["cells"]["base_m2"], main["arms"][CTRL]["cells"]["base_m2"]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    r["G9"] = bool(a2["turnover_annual"] <= 2.0 * c2["turnover_annual"] + 1e-12)
    keys = list(OOS_WINDOWS)
    dc = {k: oos[k][arm]["delta_cagr_pp"] / 100 for k in keys}
    dd = {k: oos[k][arm]["delta_max_dd_pp"] / 100 for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005 - 1e-12)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    r["G7"] = bool(med >= 0 and n_c >= 5 and n_d >= 5 and max(dd.values()) <= 0.08 + 1e-12)
    r["G7_detail"] = {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                      "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                      "max_dd_diff_max": max(dd.values())}
    r["G8"] = bool(oosboot["pooled"][arm]["diff_vs_ctrl_median_pp"] >= 0)
    r["G8_pooled_median_pp"] = oosboot["pooled"][arm]["diff_vs_ctrl_median_pp"]
    # tier-N pre-tax non-inferiority (declared in the pre-registration; interpreted, not adopted)
    r["G5N_base"] = bool(boot["pre"]["base"][arm]["diff_vs_ctrl_median_pp"] >= -NONINF_MARGIN * 100)
    r["G5N_calibrated"] = bool(boot["pre"]["calibrated"][arm]["diff_vs_ctrl_median_pp"] >= -NONINF_MARGIN * 100)
    r["pass_all"] = bool(all(r[k] for k in GATE_KEYS))
    return r


def after_tax_gates(at, boot, arm) -> dict:
    """The after-tax gate set: all three windows at $100k, and B1/B2 at $100k."""
    r = {"windows": {}, "median_delta_pp": {}}
    for wn in TAX_WINDOWS:
        d = at["100k"][wn][arm]["after_tax_cagr"] - at["100k"][wn][CTRL]["after_tax_cagr"]
        r["windows"][wn] = {"delta_pp": d * 100, "pass": bool(d >= AT_PRE_AT_MARGIN - 1e-12)}
    r["B1_progressive"] = {
        "median_delta_pp": boot["tax"]["100k"]["progressive"][f"{arm}_vs_{CTRL}"]["median_delta_cagr_pp"],
        "share_gt_0": boot["tax"]["100k"]["progressive"][f"{arm}_vs_{CTRL}"]["share_gt_0"],
    }
    r["B1_progressive"]["pass"] = bool(r["B1_progressive"]["median_delta_pp"] >= 0 and r["B1_progressive"]["share_gt_0"] >= 0.55)
    r["B2_flat"] = {
        "median_delta_pp": boot["tax"]["100k"]["flat"][f"{arm}_vs_{CTRL}"]["median_delta_cagr_pp"],
        "share_gt_0": boot["tax"]["100k"]["flat"][f"{arm}_vs_{CTRL}"]["share_gt_0"],
    }
    r["B2_flat"]["pass"] = bool(r["B2_flat"]["median_delta_pp"] >= 0 and r["B2_flat"]["share_gt_0"] >= 0.55)
    r["pass_all"] = bool(all(v["pass"] for v in r["windows"].values())
                         and r["B1_progressive"]["pass"] and r["B2_flat"]["pass"])
    return r


def verdicts(main, boot, at, oos, oosboot, dd) -> dict:
    out = {}
    for arm in DATA_ARMS:
        g = pre_tax_gates(main, boot, oos, oosboot, arm)
        a = after_tax_gates(at, boot, arm)
        tier = "N" if abs(main["arms"][arm]["mean_weight_drift_pp"]) <= 1.0 else "F"
        share = dd["vs_C0"][arm]["share_decision_differs"]
        supported = bool(share >= 0.01)
        strict = bool(g["pass_all"] and a["pass_all"])
        noninf = bool(g["G5N_base"] and g["G5N_calibrated"] and g["G9"] and a["pass_all"])
        failed = [k for k in GATE_KEYS if not g[k]] + [k for k in ("B1_progressive", "B2_flat") if not a[k]["pass"]] \
            + [wn for wn, v in a["windows"].items() if not v["pass"]]
        out[arm] = {
            "tier": tier,
            "mean_weight_drift_pp": main["arms"][arm]["mean_weight_drift_pp"],
            "share_decision_differs": share,
            "support_ge_1pct": supported,
            "pre_tax_pass_all": g["pass_all"],
            "after_tax_pass_all": a["pass_all"],
            "failed": failed,
            "tierN_pre_tax_non_inferior": noninf,
            "verdict": ("adopt" if (strict and supported)
                        else "reject"
                        + (" (exposure-neutral non-inferior, no gain)" if noninf else "")
                        + ("" if supported else " (support < 1 % of sessions)")),
        }
    return out


# ----------------------------------------------------------------------------- harness
def _anchor(label, got, want, tol_pp=TOL_PP, scale=100.0):
    diff = (got - want) * scale
    return {"reference": want, "reproduced": got, "diff": diff, "pass": bool(abs(diff) <= tol_pp)}


def harness() -> dict:
    checks = {}
    # (1) the engine's own parity suite
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                       cwd=ROOT, capture_output=True, text=True)
    tail = [ln for ln in p.stdout.strip().splitlines() if ln.strip()][-1:]
    checks["parity_quick"] = {"returncode": int(p.returncode), "last_line": tail[0] if tail else "",
                              "wall_s": time.time() - t0, "pass": bool(p.returncode == 0 and "PASS" in (tail[0] if tail else ""))}
    # (2) the calibration constants the pre-registration froze
    cal = shapes.calibrate()
    checks["calibration_frozen"] = {
        arm: {"frozen": PRE_C[arm], "recomputed": cal["c"][arm],
              "diff": float(cal["c"][arm] - PRE_C[arm]), "pass": bool(abs(cal["c"][arm] - PRE_C[arm]) <= 1e-12)}
        for arm in ARMS
    }
    # (3) the shape option is inert at (p = 1, c = 0.35), on real data
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    W = main_weights(shape_of(cal))
    checks["target_identity_main"] = {
        md: {"bitwise_equal": bool(np.array_equal(W[md][CTRL][0], w_r2))} for md in FIN
    }
    checks["vt_identity_ndx"] = {
        "bitwise_equal": bool(np.array_equal(shapes.vt_shape(shapes.ndx_vol()[1], 1.0, 0.35),
                                            fe_signals.vt_of(shapes.ndx_vol()[1])))}
    # (4) K's published anchors, through the engine
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    pre = {}
    for md, spread in FIN.items():
        d_, o_, c_, _we, _wr, _e = fe_data.main_inputs(spread)
        r = fe_sim.simulate(o_, c_, W[md][CTRL], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm="K",
                            mode="pre_tax", m=M)
        pre[md] = fe_study.pre_stats(d_, START, T, r)
    checks["anchor_pre_base_cagr"] = _anchor("pre base m2 CAGR", pre["base"]["cagr"], ANCHORS["pre_base_m2_cagr"])
    checks["anchor_pre_base_max_dd"] = _anchor("pre base m2 max DD", pre["base"]["max_dd"], ANCHORS["pre_base_m2_max_dd"])
    checks["anchor_pre_base_turnover"] = _anchor("pre base m2 turnover", pre["base"]["turnover_annual"],
                                                 ANCHORS["pre_base_m2_turnover"])
    checks["anchor_pre_cal_cagr"] = _anchor("pre cal m2 CAGR", pre["calibrated"]["cagr"], ANCHORS["pre_cal_m2_cagr"])
    checks["anchor_pre_cal_max_dd"] = _anchor("pre cal m2 max DD", pre["calibrated"]["max_dd"], ANCHORS["pre_cal_m2_max_dd"])
    at = after_tax_stage(shape_of(cal), capitals=(GATE_CAPITAL,))
    checks["anchor_at_100k_2000"] = _anchor("after tax $100k 2000", at["100k"]["2000_03_to_2026_09"]["C0"]["after_tax_cagr"],
                                            ANCHORS["at_100k_2000"])
    checks["anchor_at_100k_2010"] = _anchor("after tax $100k 2010", at["100k"]["2010_01_to_end"]["C0"]["after_tax_cagr"],
                                            ANCHORS["at_100k_2010"])
    checks["anchor_at_100k_2017"] = _anchor("after tax $100k 2017", at["100k"]["2017_01_to_end"]["C0"]["after_tax_cagr"],
                                            ANCHORS["at_100k_2017"])
    # (5) the option off reproduces the engine's own bootstrap bit for bit (K arm only)
    eng = fe_boot.boot(seed=20261011, block=20, arms=(CTRL, "K"), n_pre=5000, n_tax=1000,
                       capitals=("100k", "1M"), modes=("progressive", "flat"), anchor_e2=False,
                       deltas=("K",), baselines=(CTRL,), quiet=True, keep_paths=True)
    mine = boot_shape.boot_shape([CTRL, "K"], IDENTITY_SHAPES, n_pre=5000, n_tax=1000,
                                 capitals=("100k", "1M"), modes=("progressive", "flat"),
                                 seed=20261011, block=20, m=M, keep_paths=True, quiet=True)
    diff_cells, maxdiff = 0, 0.0
    pre_keys = (("cagr_median", "cagr_median"), ("diff_vs_ctrl_median_pp", "diff_vs_C0_median_pp"),
                ("diff_vs_ctrl_mean_pp", "diff_vs_C0_mean_pp"), ("share_gt_0", "share_gt_0"))
    # The 'K' arm only, and only the cells that do not reference the baseline: this study's kernel
    # runs K's fill rule for every arm, so the engine's 'C0' arm (R2's rule) is not comparable and
    # neither is any `_vs_C0_` delta. The claim is: the shape option off reproduces the engine's K
    # arm -- its per-path arrays bit for bit, and its own medians.
    n_cells = 0
    for md in FIN:
        a, b = eng["pre"][md]["K"]["cagr_median"], mine["pre"][md]["K"]["cagr_median"]
        maxdiff = max(maxdiff, abs(a - b))
        diff_cells += int(a != b)
        n_cells += 1
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            a, b = eng["tax"][cl][mode]["K"]["cagr_median"], mine["tax"][cl][mode]["K"]["cagr_median"]
            maxdiff = max(maxdiff, abs(a - b))
            diff_cells += int(a != b)
            n_cells += 1
    paths_equal = True
    for mdi in FIN:
        paths_equal &= bool(np.array_equal(eng["paths"]["pre"][(mdi, "K")], mine["paths"]["pre"][(mdi, "K")]))
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            paths_equal &= bool(np.array_equal(eng["paths"]["tax"][cl][mode]["K"],
                                               mine["paths"]["tax"][cl][mode]["K"]))
    checks["boot_option_off_equals_engine"] = {"engine_cells": n_cells, "cells_differ": diff_cells,
                                               "max_abs_diff": float(maxdiff), "paths_bitwise_equal": bool(paths_equal),
                                               "pass": bool(diff_cells == 0 and maxdiff == 0.0 and paths_equal)}
    # the engine's own K anchors on the clean bootstrap (K vs C0), for the record
    checks["anchor_boot_pre_base"] = _anchor("boot pre base K vs C0 median",
                                             eng["pre"]["base"]["K"]["diff_vs_C0_median_pp"] / 100,
                                             ANCHORS["boot_pre_base_K_vs_C0"] / 100, tol_pp=0.02)
    checks["anchor_boot_pre_cal"] = _anchor("boot pre cal K vs C0 median",
                                            eng["pre"]["calibrated"]["K"]["diff_vs_C0_median_pp"] / 100,
                                            ANCHORS["boot_pre_cal_K_vs_C0"] / 100, tol_pp=0.02)
    checks["anchor_boot_tax_prog"] = _anchor("boot tax K vs C0 prog median",
                                             eng["tax"]["100k"]["progressive"]["K_vs_C0"]["median_delta_cagr_pp"] / 100,
                                             ANCHORS["boot_tax_prog_K_vs_C0"] / 100, tol_pp=0.02)
    checks["anchor_boot_tax_flat"] = _anchor("boot tax K vs C0 flat median",
                                             eng["tax"]["100k"]["flat"]["K_vs_C0"]["median_delta_cagr_pp"] / 100,
                                             ANCHORS["boot_tax_flat_K_vs_C0"] / 100, tol_pp=0.02)
    # (6) the OOS pooled kernel against the engine's per-path simulation (identity shape)
    md_, mo_, mc_, my_, mstart_ = fe_data.load_market("M1")
    so, sc = fe_signals.synthetic_2x(mo_, mc_, np.zeros_like(mc_), my_ + EXTRA_FIN, EXP_Q)
    e2m = fe_signals.e2_up(mc_)[0]
    compsm = fe_signals.early_components(mc_)
    on_eff, factor = fe_signals.r2_state(mc_, e2m, compsm)
    vol = fe_signals.vol_n(sc)[0]
    w = shapes.apply_shape(on_eff, factor, vol, 1.0, PRE_C[CTRL])
    L = boot_shape.OOS_BOOT_L
    envm = fe_sim.RunEnv(md_[-L:], *fe_data.usd_market(md_[-L:]), START, L - 1)
    wq = np.repeat(w[:L].reshape(1, -1)[None, :, :], 3, axis=0)
    out = np.empty((1, 3), dtype=np.float64)
    boot_shape._boot_pt_shape(np.repeat(np.ascontiguousarray(sc[:L])[None, :], 3, axis=0),
                              np.repeat(np.ascontiguousarray(sc[:L])[None, :], 3, axis=0), wq,
                              envm.fx, envm.upm, envm.mth_of, envm.yr_of, envm.scale_y, envm.dec_tail,
                              envm.pay_ptr, envm.pay_yr, envm.pay_part, START, L - 1,
                              fe_sim.COST_PER_SIDE * M, envm.Y0, envm.M_lo, out, boot_shape.DBG0)
    ref = []
    for _ in range(3):
        r = fe_sim.simulate(sc[:L], sc[:L], w[:L], envm, 1.0, START, L - 1, arm="K", mode="pre_tax", m=M)
        nav = r["_nav"]
        k = L - START
        ref.append((nav[k] / nav[0]) ** (252 / k) - 1)
    dmax = float(np.max(np.abs(out[0] - np.array(ref))))
    checks["oos_kernel_equals_simulate"] = {"max_abs_diff": dmax, "pass": bool(dmax <= 1e-12)}
    # (7) the draw routine against an independent replay of the published recurrence
    r1 = np.random.default_rng(20261011)
    r2 = np.random.default_rng(20261011)
    pos_a = fe_boot.draw_positions(r1, 20, 64, 1000, 6000, 0)
    pos_b = boot_shape.draws_reference(r2, 20, 64, 1000, 6000)
    checks["draw_reference"] = {"bitwise_equal": bool(np.array_equal(pos_a, pos_b)),
                               "pass": bool(np.array_equal(pos_a, pos_b))}
    checks["pass"] = bool(all(v["pass"] for v in checks.values() if isinstance(v, dict) and "pass" in v))
    return checks


# ----------------------------------------------------------------------------- smoke
def _synthetic_market(seed, n=400, L=1400):
    """A synthetic QQQ-like (open, close, dividend) triple plus a synthetic DTB3 series."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.011, L)
    q = np.zeros((3, L))
    q[1] = 100 * np.cumprod(1 + r)
    q[0] = q[1] * (1 + rng.normal(0, 0.002, L))
    q[2] = np.abs(rng.normal(0.0001, 0.00005, L))
    return q, np.full(L, 0.04)


def _synthetic_days(n, first=(2010, 1, 4)):
    """`n` consecutive weekdays, so the month/year tables are exercised."""
    from datetime import date, timedelta

    d, out = date(*first), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def smoke() -> dict:
    """Synthetic prices only: the identity of the shape option, the panel reconstruction and the
    two new kernels against `fe_sim.simulate`."""
    out = {}
    rng = np.random.default_rng(7)
    vol = np.abs(rng.normal(0.3, 0.12, 400)) + 0.02
    vol[0] = np.nan
    out["shape_identity_vs_sized_wants"] = bool(np.array_equal(
        shapes.apply_shape(np.ones(400), np.ones(400), vol, 1.0, 0.35),
        fe_signals.sized_wants(np.ones(400), np.ones(400), vol)))
    q, y = _synthetic_market(11)
    T = q.shape[1]
    price = q[1]
    e2 = fe_signals.e2_up(price)[0]
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    out["target_identity_vs_wants_of"] = {
        "bitwise_equal": bool(np.array_equal(
            shapes.targets(price, fe_signals.vol_n(q[1])[0], 1.0, 0.35, e2, comps)[2][0],
            fe_signals.arm_signals(price, e2, fe_signals.vol_n(q[1])[0], "R2", comps)[0][0])),
    }
    pos = rng.integers(0, T - 1, (6, 200))
    (so, sc, wq_b), (so_c, sc_c, wq_c) = boot_shape.chunk_panels(q, y, pos, {CTRL: (1.0, 0.35), "K": (1.0, 0.35)})
    legs, _e2, _we2 = fe_boot.chunk_legs(q, y, pos)
    out["panel_identity_vs_chunk_legs"] = {
        "prices": bool(np.array_equal(so, legs["base"][0]) and np.array_equal(sc, legs["base"][1])
                       and np.array_equal(sc_c, legs["calibrated"][1])),
        "wants_base": bool(np.array_equal(wq_b[0], legs["base"][2]) and np.array_equal(wq_b[1], legs["base"][2])),
        "wants_calibrated": bool(np.array_equal(wq_c[0], legs["calibrated"][2])),
    }
    # the per-arm kernel vs the engine's own simulate on the same paths
    days = _synthetic_days(400)
    env = fe_sim.RunEnv(days, np.ones(400), fe_data.usd_market(days)[1], 253, 399)
    i0, i1 = 253, 399
    k = i1 - i0 + 1
    yrs = fe_sim.calendar_years(days[i0 - 1], days[i1])
    wq2 = np.ascontiguousarray(wq_b[:, :3, 300:700])
    so2, sc2 = np.ascontiguousarray(so[:3, 300:700]), np.ascontiguousarray(sc[:3, 300:700])
    pre = np.empty((2, 2, 3), dtype=np.float64)
    tax = np.empty((1, 1, 2, 3), dtype=np.float64)
    boot_shape._boot_chunk_shape(
        so2, sc2, wq2, so2, sc2, wq2, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
        env.pay_ptr, env.pay_yr, env.pay_part, 100_000.0, np.array([100_000.0]), i0, i1,
        fe_sim.COST_PER_SIDE * 2, yrs, env.Y0, env.M_lo, np.array([MODE_PROGRESSIVE]), 0, 3, 3,
        pre, tax, boot_shape.DBG0)
    dmax = 0.0
    for a in range(2):
        for p in range(3):
            sp = fe_sim.simulate(so2[p][None, :], sc2[p][None, :], wq2[a][None, p], env, 100_000.0, i0, i1,
                                 arm="K", mode="pre_tax", m=2)
            nav = sp["_nav"]
            dmax = max(dmax, abs(pre[0, a, p] - ((nav[-1] / nav[0]) ** (252 / k) - 1)))
            sq = fe_sim.simulate(so2[p][None, :], sc2[p][None, :], wq2[a][None, p], env, 100_000.0, i0, i1,
                                 arm="K", mode="progressive", m=2)
            dmax = max(dmax, abs(tax[0, 0, a, p] - sq["cagr_usd_liquidated"]))
    out["boot_chunk_kernel_vs_simulate"] = {"max_abs_diff": float(dmax), "pass": bool(dmax <= 1e-12)}
    buf = np.empty((2, 3), dtype=np.float64)
    boot_shape._boot_pt_shape(so2, sc2, wq2, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
                              env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, i0, i1,
                              fe_sim.COST_PER_SIDE * 2, env.Y0, env.M_lo, buf, boot_shape.DBG0)
    dmax2 = 0.0
    for a in range(2):
        for p in range(3):
            sp = fe_sim.simulate(so2[p][None, :], sc2[p][None, :], wq2[a][None, p], env, 1.0, i0, i1,
                                 arm="K", mode="pre_tax", m=2)
            nav = sp["_nav"]
            dmax2 = max(dmax2, abs(buf[a, p] - ((nav[-1] / nav[0]) ** (252 / k) - 1)))
    out["oos_kernel_vs_simulate"] = {"max_abs_diff": float(dmax2), "pass": bool(dmax2 <= 1e-12)}
    r1, r2 = np.random.default_rng(5), np.random.default_rng(5)
    out["draw_reference"] = {"bitwise_equal": bool(np.array_equal(
        fe_boot.draw_positions(r1, 20, 32, 500, 3000, 0), boot_shape.draws_reference(r2, 20, 32, 500, 3000)))}
    for v in out.values():
        if isinstance(v, dict) and "pass" not in v:
            v["pass"] = bool(all(bool(x) for x in v.values()))
    out["pass"] = bool(out["shape_identity_vs_sized_wants"]) and all(
        v["pass"] for v in out.values() if isinstance(v, dict))
    return out


# ----------------------------------------------------------------------------- run
def run() -> None:
    t0 = time.time()
    cal = shapes.calibrate()
    shapes_of = shape_of(cal)
    print("harness ...", flush=True)
    hc = harness()
    for k, v in hc.items():
        if isinstance(v, dict) and "pass" in v and k not in ("calibration_frozen",):
            print(f"  {k}: {'PASS' if v['pass'] else 'FAIL'}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if not hc["pass"]:
        sys.exit(1)
    stages = {}
    for name, fn in (("main", main_stage), ("after_tax", after_tax_stage), ("cost_stress", cost_stress_stage),
                     ("oos", oos_stage), ("oos_boot", oos_boot_stage), ("boot", boot_stage),
                     ("decision_diff", decision_diff_stage)):
        ts = time.time()
        stages[name] = fn(shapes_of)
        print(f"  stage {name}: {time.time() - ts:.1f}s", flush=True)
    gts = {}
    for arm in DATA_ARMS:
        g = pre_tax_gates(stages["main"], stages["boot"], stages["oos"], stages["oos_boot"], arm)
        g["after_tax"] = after_tax_gates(stages["after_tax"], stages["boot"], arm)
        gts[arm] = g
    vd = verdicts(stages["main"], stages["boot"], stages["after_tax"], stages["oos"], stages["oos_boot"],
                  stages["decision_diff"])
    wall = time.time() - t0
    out = {
        "campaign": "vol_shape_v1",
        "params": {
            "arms": list(ARMS),
            "control": CTRL,
            "p": {a: shapes.P_LIST.get(a, 1.0) for a in ARMS},
            "c": {a: PRE_C[a] for a in ARMS},
            "calibration": {"window": [shapes.CAL_FIRST, shapes.CAL_LAST], "sessions": 3648,
                            "source": "data/historical/vol_shape_v1_ndx.csv (^NDX daily, Yahoo chart API)",
                            "rule": "solve c_p so mean(min(1, c/sigma20**p)) = mean(min(1, 0.35/sigma20)) "
                                    "over the finite-sigma sessions; p = 1 fixed to 0.35",
                            "financing": "base (spread 0.0)", "dividends": "zero (index)",
                            "plateau": "c_p x (1 +/- 10 %) is reported, never selected"},
            "gate_set": "ewma_band_v1 G1-G9 vs K; after tax: 3 windows at $100k + B1/B2 (median >= 0, share > 0 >= 55 %)",
            "non_inferiority_tier": "realised mean-weight drift <= 1.0 pp => the pre-tax reading is "
                                    "non-inferiority (G5 median >= -0.05 pp both models, G9 <= 2x K); "
                                    "reported, never adoptable",
            "multiple_testing": "4 exponent arms, no searched parameter; the +/-10 % c_p rows are report-only",
            "bootstrap": {"seed": 20261011, "block": 20, "pre_paths": 5000, "tax_paths": 1000,
                          "caps": ["100k", "1M"], "modes": ["progressive", "flat"]},
            "oos_bootstrap": {"seed": 20261011, "block": 20, "paths_per_market": 2000, "markets": list(shapes.OOS_MARKETS)},
            "financing_spread": FIN, "cost_per_side_m1": fe_sim.COST_PER_SIDE, "expense": EXP_Q, "m": M,
        },
        "calibration": cal,
        "harness": hc,
        "main_window": stages["main"],
        "after_tax_windows": stages["after_tax"],
        "cost_stress_m4": stages["cost_stress"],
        "bootstrap_b12": stages["boot"],
        "oos": stages["oos"],
        "oos_bootstrap": stages["oos_boot"],
        "decision_diff": stages["decision_diff"],
        "gates": gts,
        "verdicts": vd,
        "run_meta": {"wall_s": wall, "git_commit": _git("HEAD"), "engine": "research/fast_engine (tag fast-engine-v1-2026-10)"},
        "status": "complete",
    }
    OUT.write_text(json.dumps(rnd(out), indent=2, sort_keys=True) + "\n")
    SUMMARY.write_text(summary_text(out, wall))
    print(SUMMARY.read_text())


def _git(rev: str) -> str:
    p = subprocess.run(["git", "rev-parse", rev], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip()


def summary_text(o, wall) -> str:
    p = lambda x: f"{x * 100:7.2f}%"  # noqa: E731
    mw = o["main_window"]
    L = ["vol_shape_v1 | I2 exposure-shape replication at matched average weight | control = production K (p = 1)",
         f"main window {mw['window']['start']} -> {mw['window']['end']} ({mw['window']['sessions']} sessions), m = 2, base unless stated",
         f"run wall-clock: {wall:.1f} s | git {o['run_meta']['git_commit'][:12]} | harness {'PASS' if o['harness']['pass'] else 'FAIL'}", ""]
    L.append("calibration (1985-10-01 -> 2000-03-08, ^NDX, base financing, zero dividends):")
    for a in ARMS:
        r = o["calibration"]["arms"][a]
        L.append(f"  {a:3s} p = {r['p']:<5} c = {r['c']:.12f}  mean(vt) = {r['mean_vt_cal_window']:.10f} "
                 f"(K {o['calibration']['target_mean_vt_of_K']:.10f})")
    L.append("")
    L.append("main window (pre-tax, base / calibrated):")
    L.append("  arm   mean_w  drift_pp  mean|dw|  CAGR base  CAGR cal   maxDD base  maxDD cal  2017+ base  turn/yr  m1 base  m1 cal")
    for a in ARMS:
        c = mw["arms"][a]
        b, k = c["cells"]["base_m2"], c["cells"]["calibrated_m2"]
        L.append(f"  {a:3s} {c['mean_weight'] * 100:7.2f}% {c['mean_weight_drift_pp']:+8.3f} {c['mean_abs_dw']:9.4f} "
                 f" {p(b['cagr'])} {p(k['cagr'])}  {p(b['max_dd'])} {p(k['max_dd'])} {p(b['2017_end']['cagr'])} "
                 f" {b['turnover_annual']:8.4f} {p(c['cells']['base_m1']['cagr'])} {p(c['cells']['calibrated_m1']['cagr'])}")
    L.append("")
    L.append("gates vs K (G1-G9 as ewma_band_v1 defines them; base/calibrated):")
    for a in DATA_ARMS:
        g = o["gates"][a]
        L.append(f"  {a:3s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS)
                 + f" -> {'PASS' if g['pass_all'] else 'FAIL'} | tier {o['verdicts'][a]['tier']}"
                 + f" | diffs {o['verdicts'][a]['share_decision_differs'] * 100:.2f}%")
    L.append("")
    L.append("after tax at $100k (progressive, liquidated, m = 2) delta vs K, and the clean bootstrap ($100k):")
    L.append("  arm   d2000    d2010    d2017   | B1 med/share      B2 med/share      | verdict")
    for a in DATA_ARMS:
        w = o["gates"][a]["after_tax"]["windows"]
        b1 = o["gates"][a]["after_tax"]["B1_progressive"]
        b2 = o["gates"][a]["after_tax"]["B2_flat"]
        L.append(f"  {a:3s} {w['2000_03_to_2026_09']['delta_pp']:+8.3f} {w['2010_01_to_end']['delta_pp']:+8.3f} "
                 f"{w['2017_01_to_end']['delta_pp']:+8.3f} | {b1['median_delta_pp']:+7.3f}/{b1['share_gt_0'] * 100:5.1f}% "
                 f"{b2['median_delta_pp']:+7.3f}/{b2['share_gt_0'] * 100:5.1f}% | {o['verdicts'][a]['verdict']}")
    L.append("")
    L.append("clean bootstrap deltas vs K (median pp, share>0): pre-tax leg by model")
    for a in DATA_ARMS:
        pb = o["bootstrap_b12"]["pre"]["base"][a]
        pc = o["bootstrap_b12"]["pre"]["calibrated"][a]
        L.append(f"  {a:3s} pre base {pb['diff_vs_ctrl_median_pp']:+7.3f} ({pb['share_gt_0'] * 100:5.1f}%)  "
                 f"pre cal {pc['diff_vs_ctrl_median_pp']:+7.3f} ({pc['share_gt_0'] * 100:5.1f}%)  "
                 f"G8 pooled OOS {o['oos_bootstrap']['pooled'][a]['diff_vs_ctrl_median_pp']:+7.3f}")
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2) -- delta vs K CAGR pp / max DD pp:")
    for wn in OOS_WINDOWS:
        L.append(f"  {wn:14s} K {p(o['oos'][wn]['C0']['cagr'])} {p(o['oos'][wn]['C0']['max_dd'])} | "
                 + "  ".join(f"{a} {o['oos'][wn][a]['delta_cagr_pp']:+7.3f}/{o['oos'][wn][a]['delta_max_dd_pp']:+6.3f}"
                             for a in DATA_ARMS))
    L.append("")
    L.append("plateau (report only): c_p x (1 +/- 10 %) on the calibration window -- mean(vt) change")
    for a in ARMS:
        r = o["calibration"]["arms"][a]
        L.append(f"  {a:3s} " + "  ".join(f"{k} {v['mean_vt'] - o['calibration']['target_mean_vt_of_K']:+.6f}" for k, v in r["plateau"].items()))
    L.append("")
    L.append("exposure-normalised CAGR diagnostic (base m=2, (1+CAGR)**(w_K/w_arm) - 1):")
    for a in ARMS:
        L.append(f"  {a:3s} {p(mw['arms'][a]['exposure_normalised_cagr'])} (delta vs K "
                 f"{mw['arms'][a]['exposure_normalised_cagr_delta_pp']:+.3f} pp)")
    L.append("")
    L.append("harness checks: " + ", ".join(f"{k}={'P' if (v.get('pass') if isinstance(v, dict) else False) else 'F'}"
                                             for k, v in o["harness"].items() if isinstance(v, dict)))
    return "\n".join(L) + "\n"


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--calibrate":
        cal = shapes.calibrate(verbose=True)
        print(json.dumps(rnd(cal["arms"]), indent=2))
    elif mode == "--smoke":
        out = smoke()
        print(json.dumps(rnd(out), indent=2))
        print(f"smoke {'PASS' if out['pass'] else 'FAIL'}")
        sys.exit(0 if out["pass"] else 1)
    elif mode == "--harness":
        hc = harness()
        print(json.dumps(rnd(hc), indent=2, sort_keys=True))
        print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
        sys.exit(0 if hc["pass"] else 1)
    elif mode == "--run":
        run()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
