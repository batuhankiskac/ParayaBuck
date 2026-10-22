#!/usr/bin/env python3
"""short_term_reentry_v1 -- tradeable short-term exit + rebound-confirmed re-entry on K.

See PREREGISTRATION.md (committed before any non-control arm was simulated). Control = production
K exactly (R2 + A1u gain band + H2 December deferral) on `research/fast_engine`; R2 is a reference
row. The six candidate arms add a QQQ-close overlay on top of K's unchanged weight path:

  EXIT      close <= (1 - d) * trailing-20-session high (truncated at the current in-stint)
  RE-ENTRY  while out, close >= (1 + r) * post-exit low, or close > SMA10 (arms E/F)

`Ah..Fh` are the non-selectable tax-free diagnostic variants (K's targets, the 2x leg's return
zeroed on the out sessions, no sells).

Stages: pre (base/calibrated x m = 1/2/4), AT (three windows, $100k, progressive, liquidated),
B12 (clean bootstrap: 5,000 pre-tax / 1,000 after-tax paths, seed 20261011, block 20, B1/B2),
OOS (six ewma_band_v1 windows) + the G8 pooled OOS bootstrap, DIAG (exits/yr, false alarms,
re-entry vs exit fill price), EPI (October 2026, report only, Yahoo extension).

Modes
  --smoke    synthetic prices only: kernel == reference, re-entry mechanics, hedge zero return.
  --harness  parity --quick, K anchors, overlay kernel vs the Python reference for all six arms,
             never-firing identity, hedge identities, bootstrap-extension parity, chunk_legs_pc.
  --run      harness, then every stage, the gates and the tables; writes RESULTS.json and
             SUMMARY.txt.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE
from research.short_term_oracle_v1.engine import fe_boot_targets as fbt
from research.short_term_reentry_v1.engine import fe_overlay as fov

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

FULL_OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
EPI_QQQ = ROOT / "data/historical/short_term_reentry_v1_qqq_2026q4.csv"
EPI_QLD = ROOT / "data/historical/short_term_reentry_v1_qld_2026q4.csv"

# ----------------------------------------------------------------------------- pre-registered constants
START = fe_config.START
M = fe_config.M
GATE_CAPITAL = fe_config.GATE_CAPITAL
TAX_CAPITALS = fe_config.TAX_CAPITALS
TAX_WINDOWS = fe_config.TAX_WINDOWS
FIN = fe_config.FIN
EXP_Q = fe_config.EXP_Q
EXTRA_FIN = fe_config.EXTRA_FIN
SEED, BLOCK = fe_config.SEED, fe_config.BLOCK
PRE_PATHS = fe_config.PRE_BOOT_PATHS          # 5,000 (G5)
TAX_PATHS = fe_config.TAX_BOOT_PATHS          # 1,000 (B1/B2)
CHUNK = 128
TOL_PP = 1e-4                                 # 0.01 pp
OOS_PATHS = 2_000
OOS_BOOT_L = 253 + 2_520
OOS_CHUNK = 250

CONTROL = "K"
R2REF = "R2"
ARMS = ("A", "B", "C", "D", "E", "F")
HEDGES = {a: a + "h" for a in ARMS}
ALL_LABELS = (CONTROL, R2REF) + ARMS + tuple(HEDGES[a] for a in ARMS)

# the six pre-registered arms
SPEC = {
    "A": {"d": 0.03, "kind_r": 0, "r": 0.02},
    "B": {"d": 0.03, "kind_r": 0, "r": 0.04},
    "C": {"d": 0.05, "kind_r": 0, "r": 0.02},
    "D": {"d": 0.05, "kind_r": 0, "r": 0.04},
    "E": {"d": 0.03, "kind_r": 1, "r": 0.0},
    "F": {"d": 0.05, "kind_r": 1, "r": 0.0},
}
ARM_TITLE = {
    "A": "A_d3_r2", "B": "B_d3_r4", "C": "C_d5_r2", "D": "D_d5_r4",
    "E": "E_d3_sma10", "F": "F_d5_sma10",
}
TITLE = {a: ARM_TITLE[a] for a in ARMS}
TITLE.update({HEDGES[a]: ARM_TITLE[a] + "_hedge" for a in ARMS})

RULE_LABEL = {lab: ("C0" if lab == R2REF else "K") for lab in ALL_LABELS}

# K's published anchors (task brief + band_h2_combo_v1/RESULTS.json)
REF_K = {
    ("base", 2): {"cagr": 0.157649, "max_dd": 0.466698},
    ("calibrated", 2): {"cagr": 0.151785, "max_dd": 0.467948},
    ("base", 1): {"cagr": 0.160173, "max_dd": 0.463529},
    ("calibrated", 1): {"cagr": 0.154303, "max_dd": 0.464786},
}
REF_K_AT = {"2000_03_to_2026_09": 0.107533, "2010_01_to_end": 0.152354, "2017_01_to_end": 0.239720}
REF_K_AT_DELTA_VS_R2 = {"2000_03_to_2026_09": 0.005933, "2010_01_to_end": 0.005294,
                        "2017_01_to_end": 0.010859}
SUBPERIODS = fe_study.SUBPERIODS


def _til(lab):
    return TITLE.get(lab, lab)


# ----------------------------------------------------------------------------- reference overlay (harness)
def overlay_ref(c, w, max20, sma_up10, i0, i1, d, kind, r):
    """Straight Python transcription of PREREGISTRATION.md section 3 (slow; harness only)."""
    anchor = max(i0 - 19, 0)
    in_st = True
    L = 0.0
    ex_t = -1
    hi_ex = 0.0
    lo_t = -1
    lo_vv = 0.0
    mask = np.zeros(len(c), dtype=np.int8)
    eps = []
    for t in range(i0, i1 + 1):
        if w[t] <= 0.0:
            if not in_st:
                eps.append({"ex": ex_t, "re": -2, "hi": hi_ex, "lo_i": lo_t, "lo_v": lo_vv})
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
                ex_t, hi_ex, lo_t, lo_vv = t, H, t, c[t]
                mask[t] = 1
            else:
                mask[t] = 0
        else:
            if c[t] < lo_vv:
                lo_vv, lo_t = c[t], t
            hit = (c[t] >= (1.0 + r) * lo_vv) if kind == 0 else bool(sma_up10[t])
            if hit:
                eps.append({"ex": ex_t, "re": t, "hi": hi_ex, "lo_i": lo_t, "lo_v": lo_vv})
                in_st = True
                anchor = t
                mask[t] = 0
            else:
                mask[t] = 1
    if not in_st:
        eps.append({"ex": ex_t, "re": -1, "hi": hi_ex, "lo_i": lo_t, "lo_v": lo_vv})
    return mask, eps


def overlay_records(c, w, max20, sma_up10, i0, i1, d, kind, r):
    """Run the numba kernel with diagnostic arrays and return (mask, count, records)."""
    T = len(c)
    mask = np.zeros(T, dtype=np.int8)
    m = i1 - i0 + 2
    ex_i = np.zeros(m, dtype=np.int64)
    re_i = np.zeros(m, dtype=np.int64)
    hi_v = np.zeros(m, dtype=np.float64)
    lo_i = np.zeros(m, dtype=np.int64)
    lo_v = np.zeros(m, dtype=np.float64)
    n = fov.overlay_core(np.ascontiguousarray(c), np.ascontiguousarray(w),
                         np.ascontiguousarray(max20), np.ascontiguousarray(sma_up10),
                         i0, i1, d, kind, r, mask, ex_i, re_i, hi_v, lo_i, lo_v)
    recs = [{"ex": int(ex_i[k]), "re": int(re_i[k]), "hi": float(hi_v[k]),
             "lo_i": int(lo_i[k]), "lo_v": float(lo_v[k])} for k in range(n)]
    return mask, int(n), recs


# ----------------------------------------------------------------------------- inputs / arms
def build_main():
    """Per-model price paths, K's target series, the overlay masks/targets and the hedge prices."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    qqq = np.ascontiguousarray(q[1], dtype=float)
    T = len(qqq)
    e2 = fe_signals.e2_up(qqq)[0]
    comps = fe_signals.early_components(qqq)
    max20 = fov.rolling_max(qqq)[0]
    sma10 = fe_signals.sma_up(qqq, 10)[0]

    models = {}
    for md, spread in FIN.items():
        o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
        w = fe_signals.arm_signals(qqq, e2, fe_signals.vol_n(c)[0], "R2", comps)[0][0]
        models[md] = {"o": np.ascontiguousarray(o), "c": np.ascontiguousarray(c),
                      "w": np.ascontiguousarray(w)}

    masks, counts, episodes = {}, {}, {}
    for arm in ARMS:
        sp = SPEC[arm]
        for md in models:
            mask = np.zeros(T, dtype=np.int8)
            if md == "base":
                mask, n, recs = overlay_records(qqq, models[md]["w"], max20, sma10,
                                                START, T - 1, sp["d"], sp["kind_r"], sp["r"])
                episodes[arm] = recs
            else:
                cnt = np.zeros(1, dtype=np.int64)
                fov.overlay_masks(qqq[None, :], models[md]["w"][None, :], max20[None, :],
                                  sma10[None, :], START, T - 1, sp["d"], sp["kind_r"], sp["r"],
                                  mask[None, :], cnt)
                n = int(cnt[0])
            masks[(md, arm)] = mask
            counts[(md, arm)] = int(n)

    targets = {md: {} for md in models}
    prices = {md: {} for md in models}
    for md in models:
        w = models[md]["w"]
        o, c = models[md]["o"], models[md]["c"]
        targets[md][CONTROL] = w
        targets[md][R2REF] = w
        prices[md][CONTROL] = (o, c)
        prices[md][R2REF] = (o, c)
        for arm in ARMS:
            msk = masks[(md, arm)]
            targets[md][arm] = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
            prices[md][arm] = (o, c)
            hm = np.zeros_like(msk)
            hm[1:] = msk[:-1]
            oo = np.empty_like(o)
            cc = np.empty_like(c)
            fbt.masked_prices(o[None, :], c[None, :], hm[None, :], oo[None, :], cc[None, :])
            targets[md][HEDGES[arm]] = w
            prices[md][HEDGES[arm]] = (np.ascontiguousarray(oo[0]), np.ascontiguousarray(cc[0]))

    return {"days": days, "qqq": qqq, "e2": e2, "max20": max20, "sma10": sma10,
            "models": models, "masks": masks, "counts": counts, "targets": targets,
            "prices": prices, "episodes": episodes, "T": T}


def run_arm(built, label, md, mode, capital, i0, i1, env, m=M):
    o, c = built["prices"][md][label]
    return fe_sim.simulate(o, c, built["targets"][md][label], env, capital, i0, i1,
                           arm=RULE_LABEL[label], mode=mode, m=m)


# ----------------------------------------------------------------------------- diagnostics (report only)
def diagnostics(built):
    """Exits/yr, false-alarm share and depth, exit vs re-entry fill price, per arm (base model)."""
    days, T = built["days"], built["T"]
    qqq = built["qqq"]
    o = built["models"]["base"]["o"]
    years = fe_sim.calendar_years(days[START - 1], days[T - 1])
    out = {}
    for arm in ARMS:
        recs = built["episodes"][arm]
        n_ex = len(recs)
        depths, re_done, re_prices, above, open_end, k_reset = [], 0, [], 0, 0, 0
        hist = {"lt2": 0, "2_3": 0, "3_5": 0, "5_8": 0, "8_10": 0, "ge10": 0}
        for r in recs:
            depth = 1.0 - r["lo_v"] / r["hi"]
            depths.append(depth)
            if depth < 0.02:
                hist["lt2"] += 1
            elif depth < 0.03:
                hist["2_3"] += 1
            elif depth < 0.05:
                hist["3_5"] += 1
            elif depth < 0.08:
                hist["5_8"] += 1
            elif depth < 0.10:
                hist["8_10"] += 1
            else:
                hist["ge10"] += 1
            if r["re"] == -1:
                open_end += 1
            elif r["re"] == -2:
                k_reset += 1
            else:
                re_done += 1
                j_ex = r["ex"] + 1
                j_re = r["re"] + 1
                if j_ex <= T - 1 and j_re <= T - 1 and o[j_ex] > 0:
                    chg = float(o[j_re] / o[j_ex] - 1.0)
                    re_prices.append(chg)
                    if chg > 0:
                        above += 1
        mask = built["masks"][("base", arm)]
        out[arm] = {
            "exits": int(n_ex),
            "exits_per_year": float(n_ex / years),
            "sessions_out": int(mask[START:T].sum()),
            "share_sessions_out": float(mask[START:T].mean()),
            "false_alarms_lt8pct": int(sum(1 for x in depths if x < 0.08)),
            "false_alarm_share": float(sum(1 for x in depths if x < 0.08) / n_ex) if n_ex else None,
            "median_depth": float(np.median(depths)) if depths else None,
            "max_depth": float(max(depths)) if depths else None,
            "depth_histogram": hist,
            "completed_reentries": int(re_done),
            "open_at_data_end": int(open_end),
            "ended_by_K_zero": int(k_reset),
            "mean_reentry_vs_exit_pct": float(np.mean(re_prices) * 100) if re_prices else None,
            "median_reentry_vs_exit_pct": float(np.median(re_prices) * 100) if re_prices else None,
            "share_reentry_above_exit": float(above / len(re_prices)) if re_prices else None,
            "episodes": [
                {"exit": days[r["ex"]], "reentry": (days[r["re"]] if r["re"] >= 0 else None),
                 "depth_pct": round(100.0 * (1.0 - r["lo_v"] / r["hi"]), 2)}
                for r in recs
            ],
        }
    return {"window_years": float(years), "arms": out}


# ----------------------------------------------------------------------------- stages
def stage_pre_tax(built, labels=ALL_LABELS):
    days, T = built["days"], built["T"]
    out = {}
    for md in FIN:
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T - 1)
        out[md] = {}
        for lab in labels:
            row = {}
            for m in (1, 2, 4):
                r = run_arm(built, lab, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T - 1, env, m=m)
                st = fe_study.pre_stats(days, START, T - 1, r)
                row[f"m{m}"] = {
                    "cagr": st["cagr"], "max_dd": st["max_dd"], "calmar": st["calmar"],
                    "cagr_2017": st["2017_end"]["cagr"],
                    "subperiods": {k: v["cagr"] for k, v in st["subperiods"].items()},
                    "turnover_annual": st["turnover_annual"],
                    "sells_per_year": st["sells_per_year"],
                    "trades": st["trades"], "years": st["years"],
                }
            r2 = run_arm(built, lab, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T - 1, env, m=2)
            row["m2"]["tax_usd"] = float(r2["tax_total_usd_at_payment_fx"])
            row["m2"]["n_exits"] = (int(built["counts"][(md, lab)]) if lab in ARMS else None)
            if lab != CONTROL:
                for m in (1, 2, 4):
                    row[f"m{m}"]["delta_cagr_pp"] = (row[f"m{m}"]["cagr"] - out[md][CONTROL][f"m{m}"]["cagr"]) * 100
                    row[f"m{m}"]["delta_max_dd_pp"] = (row[f"m{m}"]["max_dd"] - out[md][CONTROL][f"m{m}"]["max_dd"]) * 100
            out[md][lab] = row
    out["window"] = {"start": days[START], "end": days[T - 1], "sessions": T - START,
                     "years": float(fe_sim.calendar_years(days[START - 1], days[T - 1]))}
    return out


def stage_after_tax(built, labels=ALL_LABELS):
    days, T = built["days"], built["T"]
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        out[wn] = {}
        for lab in labels:
            rq = run_arm(built, lab, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
            rp = run_arm(built, lab, "base", "pre_tax", 1.0, i0, i1, env)
            out[wn][lab] = {
                "pre_tax_cagr": float(rp["cagr_usd_liquidated"]),
                "after_tax_cagr": float(rq["cagr_usd_liquidated"]),
                "after_tax_cagr_unliquidated": float(rq["cagr_usd_unliquidated"]),
                "max_dd_after_tax": float(rq["max_dd_nav_usd"]),
                "max_dd_pre_tax": float(rp["max_dd_nav_usd"]),
                "tax_usd": float(rq["tax_total_usd_at_payment_fx"]),
                "sells_per_year": float(rq["sells"] / rq["years"]),
                "qld_sells_per_year": float(rq["qld_sells"] / rq["years"]),
                "terminal_usd": float(rq["terminal_usd_liquidated"]),
                "years": float(rq["years"]),
            }
            if lab != CONTROL:
                out[wn][lab]["delta_pre_tax_pp"] = (out[wn][lab]["pre_tax_cagr"] - out[wn][CONTROL]["pre_tax_cagr"]) * 100
                out[wn][lab]["delta_after_tax_pp"] = (out[wn][lab]["after_tax_cagr"] - out[wn][CONTROL]["after_tax_cagr"]) * 100
                out[wn][lab]["delta_max_dd_pp"] = (out[wn][lab]["max_dd_after_tax"] - out[wn][CONTROL]["max_dd_after_tax"]) * 100
                out[wn][lab]["delta_tax_usd"] = out[wn][lab]["tax_usd"] - out[wn][CONTROL]["tax_usd"]
                out[wn][lab]["extra_qld_sells_per_year"] = (out[wn][lab]["qld_sells_per_year"]
                                                            - out[wn][CONTROL]["qld_sells_per_year"])
    return out


# ----------------------------------------------------------------------------- bootstrap
def boot_run(specs, n_pre, n_tax, seed=SEED, block=BLOCK, capitals=(GATE_CAPITAL,),
             modes=("progressive", "flat"), m=M, chunk=CHUNK, window=None, quiet=True):
    """Block bootstrap over exogenous per-path targets (the oracle study's extension).

    `specs[label]` = {"rule": (band, usd_rule, defer), "kind": "k"|"sell"|"hedge",
    "d", "kind_r", "r", "pre": bool}. Kind "k" uses production R2's target series (control and
    R2); "sell" zeroes the overlay intervals; "hedge" keeps the target and zeroes the leg's
    return on those sessions.
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    labels = list(specs)
    n_paths = max(n_pre, n_tax)
    rng = np.random.default_rng(seed)
    pre = {(md, a): np.empty(n_pre, dtype=float) for md in FIN for a in labels}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=float) for a in labels} for mode in modes}
          for cl in capitals}
    cost = fe_sim.COST_PER_SIDE * m
    cap_pre = TAX_CAPITALS[GATE_CAPITAL]

    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool,
                                     fe_config.TAX_BOOT_EXT)
        pc, e2, legs, _w_e2 = fbt.chunk_legs_pc(win.q, win.y, pos)
        max20 = fov.rolling_max(pc)
        sma10 = fe_signals.sma_up(pc, 10)
        masks = {}
        for md in FIN:
            w = legs[md][2]
            for label, sp in specs.items():
                if sp["kind"] == "k":
                    continue
                key = (md, sp["d"], sp["kind_r"], sp["r"])
                if key in masks:
                    continue
                msk = np.zeros(pc.shape, dtype=np.int8)
                cnt = np.zeros(W, dtype=np.int64)
                fov.overlay_masks(pc, w, max20, sma10, win.i0, win.i1,
                                  sp["d"], sp["kind_r"], sp["r"], msk, cnt)
                masks[key] = msk

        def arrays(label, sp, md):
            so, sc, w = legs[md]
            if sp["kind"] == "k":
                return so, sc, w
            msk = masks[(md, sp["d"], sp["kind_r"], sp["r"])]
            wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
            if sp["kind"] == "hedge":
                hm = np.zeros_like(msk)
                hm[:, 1:] = msk[:, :-1]
                oo = np.empty_like(so)
                cc = np.empty_like(sc)
                fbt.masked_prices(so, sc, hm, oo, cc)
                return oo, cc, wq
            return so, sc, wq

        if p0 < n_pre:
            for label, sp in specs.items():
                if not sp.get("pre", True):
                    continue
                for md in FIN:
                    oo, cc, wq = arrays(label, sp, md)
                    got = np.empty(W, dtype=float)
                    fbt.run_pretax(oo, cc, wq, env.fx, env.upm, env.mth_of, env.yr_of,
                                   env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr,
                                   env.pay_part, cap_pre, win.i0, win.i1, cost,
                                   sp["rule"][0], sp["rule"][1], sp["rule"][2],
                                   env.Y0, env.M_lo, got)
                    n = min(W, n_pre - p0)
                    pre[(md, label)][p0:p0 + n] = got[:n]

        so, sc, w = legs["base"]
        for label, sp in specs.items():
            oo, cc, wq = arrays(label, sp, "base")
            for mode in modes:
                mi = MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT
                for cl in capitals:
                    got = np.empty(W, dtype=float)
                    fbt.run_tax(oo, cc, wq, env.fx, env.upm, env.mth_of, env.yr_of,
                                env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr,
                                env.pay_part, TAX_CAPITALS[cl], win.i0, win.i1, cost,
                                sp["rule"][0], sp["rule"][1], sp["rule"][2], mi,
                                win.yrs, fbt._SC_LIQ, env.Y0, env.M_lo, got)
                    n = min(W, n_tax - p0)
                    at[cl][mode][label][p0:p0 + n] = got[:n]
        if not quiet:
            print(f"  bootstrap path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    return {"pre": pre, "tax": at, "window": win, "labels": labels}


def summarise_boot(raw):
    labels = raw["labels"]
    out = {"pre": {}, "tax": {}}
    for md in FIN:
        base = raw["pre"][(md, CONTROL)]
        out["pre"][md] = {}
        for a in labels:
            c = raw["pre"][(md, a)]
            d = c - base
            out["pre"][md][a] = {
                "cagr_median": float(np.median(c)),
                "median_delta_pp": float(np.median(d) * 100),
                "mean_delta_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
    for cl in raw["tax"]:
        out["tax"][cl] = {}
        for mode in raw["tax"][cl]:
            base = raw["tax"][cl][mode][CONTROL]
            out["tax"][cl][mode] = {}
            for a in labels:
                c = raw["tax"][cl][mode][a]
                d = c - base
                out["tax"][cl][mode][a] = {
                    "cagr_median": float(np.median(c)),
                    "median_delta_pp": float(np.median(d) * 100),
                    "mean_delta_pp": float(np.mean(d) * 100),
                    "share_gt_0": float(np.mean(d > 0)),
                    "p05_delta_pp": float(np.percentile(d, 5) * 100),
                    "p95_delta_pp": float(np.percentile(d, 95) * 100),
                }
    return out


def stage_bootstrap(quiet=True):
    specs = {CONTROL: {"rule": (True, True, True), "kind": "k"},
             R2REF: {"rule": (False, False, False), "kind": "k"}}
    for arm in ARMS:
        sp = SPEC[arm]
        specs[arm] = {"rule": (True, True, True), "kind": "sell", "d": sp["d"],
                      "kind_r": sp["kind_r"], "r": sp["r"], "pre": True}
        specs[HEDGES[arm]] = {"rule": (True, True, True), "kind": "hedge", "d": sp["d"],
                              "kind_r": sp["kind_r"], "r": sp["r"], "pre": True}
    raw = boot_run(specs, n_pre=PRE_PATHS, n_tax=TAX_PATHS, capitals=(GATE_CAPITAL,),
                   modes=("progressive", "flat"), quiet=quiet)
    out = summarise_boot(raw)
    out["settings"] = {"seed": SEED, "block": BLOCK, "paths_pre": PRE_PATHS,
                       "paths_tax": TAX_PATHS, "capital": GATE_CAPITAL,
                       "modes": ["progressive", "flat"], "pre_models": list(FIN),
                       "chunk": CHUNK,
                       "calendar": [raw["window"].dates[0], raw["window"].dates[-1]]}
    return out


# ----------------------------------------------------------------------------- OOS (G7 / G8)
def oos_window(days, o, c, y, start):
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    w = fe_signals.arm_signals(c, e2, fe_signals.vol_n(sc)[0], "R2", comps)[0][0]
    max20 = fov.rolling_max(c)[0]
    sma10 = fe_signals.sma_up(c, 10)[0]
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    out = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for lab in (CONTROL,) + ARMS:
        if lab == CONTROL:
            wq = w
        else:
            sp = SPEC[lab]
            msk = np.zeros(len(c), dtype=np.int8)
            cnt = np.zeros(1, dtype=np.int64)
            fov.overlay_masks(np.ascontiguousarray(c)[None, :], np.ascontiguousarray(w)[None, :],
                              np.ascontiguousarray(max20)[None, :], np.ascontiguousarray(sma10)[None, :],
                              start, len(days) - 1, sp["d"], sp["kind_r"], sp["r"],
                              msk[None, :], cnt)
            wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
        r = fe_sim.simulate(so, sc, wq, env, 1.0, start, len(days) - 1, arm="K",
                            mode="pre_tax", m=M, rule_index=False)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        out[lab] = st
    for lab in ARMS:
        out[lab]["delta_cagr_pp"] = (out[lab]["cagr"] - out[CONTROL]["cagr"]) * 100
        out[lab]["delta_max_dd_pp"] = (out[lab]["max_dd"] - out[CONTROL]["max_dd"]) * 100
    return out


def stage_oos():
    out = {}
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    out["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = fe_data.load_market(key)
        out[key] = oos_window(md_, mo_, mc_, my_, mstart_)
    return out


def oos_boot_market(key, rng):
    days, o, c, y, start = fe_data.load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    L = OOS_BOOT_L
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, L)
    labels = (CONTROL,) + ARMS
    acc = {a: [] for a in labels}
    cost = fe_sim.COST_PER_SIDE * M
    for chunk in range(0, OOS_PATHS, OOS_CHUNK):
        P = min(OOS_CHUNK, OOS_PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1.0 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        idxpos = np.empty((P, L), dtype=np.int64)
        fe_boot._positions(pos, jump, fresh, npool, 0, idxpos)
        idx = pool[idxpos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = fe_signals.e2_up(cc)
        comps = fe_signals.early_components(cc)
        so_c, sc_c = fe_signals.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        w = fe_signals.arm_signals(cc, e2, fe_signals.vol_n(sc_c)[0], "R2", comps)[0][0]
        max20 = fov.rolling_max(cc)
        sma10 = fe_signals.sma_up(cc, 10)
        for lab in (CONTROL,) + ARMS:
            if lab == CONTROL:
                wq = w
            else:
                sp = SPEC[lab]
                msk = np.zeros(cc.shape, dtype=np.int8)
                cnt = np.zeros(P, dtype=np.int64)
                ww = np.ascontiguousarray(np.broadcast_to(w, cc.shape))
                fov.overlay_masks(cc, ww, max20, sma10, START, L,
                                  sp["d"], sp["kind_r"], sp["r"], msk, cnt)
                wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
            got = np.empty(P, dtype=float)
            fbt.run_pretax(so_c, sc_c, np.ascontiguousarray(wq), env.fx, env.upm, env.mth_of,
                           env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr,
                           env.pay_part, 1.0, START, L, cost, True, True, True,
                           env.Y0, env.M_lo, got)
            acc[lab].append(got.copy())
    return {a: np.concatenate(v) for a, v in acc.items()}


def stage_oos_boot():
    rng = np.random.default_rng(SEED)
    per = {k: oos_boot_market(k, rng) for k in ("M1", "M2", "M3", "M4", "M5")}
    pooled = {a: np.concatenate([per[k][a] for k in per]) for a in per["M1"]}
    out = {"per_market": {}, "pooled": {}}
    for k in per:
        out["per_market"][k] = {
            a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
            for a in per[k]
        }
    for a in pooled:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {
            "diff_vs_K_median_pp": float(np.median(d) * 100),
            "diff_vs_K_mean_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)),
            "paths": int(len(d)),
        }
    out["settings"] = {"paths_per_market": OOS_PATHS, "sessions": OOS_BOOT_L,
                       "seed": SEED, "block": BLOCK, "markets": ["M1", "M2", "M3", "M4", "M5"]}
    return out


# ----------------------------------------------------------------------------- October 2026 (report only)
def _ffill_rates(days):
    tb = {}
    with fe_config.DTB3_CSV.open() as fh:
        for r in csv.DictReader(fh):
            tb[r["observation_date"]] = r["DTB3"]
    out, last = [], None
    dset = set(days)
    for d in sorted(set(tb) | dset):
        v = tb.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in dset:
            out.append(last)
    return np.array(out, dtype=float)


def load_extended_qqq():
    """Committed QQQ bars plus the Yahoo extension (only dates after the committed end)."""
    raw = {}
    with fe_config.BARS.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "QQQ":
                raw[r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    with EPI_QQQ.open() as fh:
        for r in csv.DictReader(fh):
            if r["date"] not in raw:
                raw[r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    days = sorted(raw)
    arr = np.array([raw[d] for d in days], dtype=float)
    return days, np.ascontiguousarray(arr[:, 0]), np.ascontiguousarray(arr[:, 1]), np.ascontiguousarray(arr[:, 2])


def stage_epi():
    days, oq, cq, dv = load_extended_qqq()
    y = _ffill_rates(days)
    so, sc = fe_signals.synthetic_2x(oq, cq, dv, y, EXP_Q)
    e2 = fe_signals.e2_up(cq)[0]
    comps = fe_signals.early_components(cq)
    w = fe_signals.arm_signals(cq, e2, fe_signals.vol_n(sc)[0], "R2", comps)[0][0]
    max20 = fov.rolling_max(cq)[0]
    sma10 = fe_signals.sma_up(cq, 10)[0]
    i0 = bisect.bisect_left(days, "2026-01-02")
    i_ref = bisect.bisect_left(days, "2026-10-05")     # vt_level_k_v1's reference date
    i_peak = int(np.argmax(cq[bisect.bisect_left(days, "2026-09-01"):])) + bisect.bisect_left(days, "2026-09-01")
    i_ep = i_peak + 1
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), i0, len(days) - 1)
    out = {"covered": True, "data_end": days[-1], "reference_date_vt_level_k": days[i_ref],
           "peak_date": days[i_peak], "peak_close_qqq": float(cq[i_peak]),
           "episode_start": days[i_ep],
           "sessions_in_episode": int(len(days) - i_ep),
           "qqq_return_from_reference_pct": float((cq[-1] / cq[i_ref] - 1.0) * 100),
           "qqq_return_from_peak_pct": float((cq[-1] / cq[i_peak] - 1.0) * 100),
           "arms": {}, "overlay": {}}

    def measure(nav, off):
        j_peak = i_peak - off
        j_ep = i_ep - off
        seg = nav[j_ep:]
        return {
            "return_from_peak_pct": float((nav[-1] / nav[j_peak] - 1.0) * 100),
            "max_dd_inside_episode_pct": float((1.0 - np.min(seg) / np.max(seg)) * 100),
            "trough_date": days[i_ep + int(np.argmin(seg))],
        }

    for lab in (CONTROL,) + ARMS:
        if lab == CONTROL:
            wq = w
        else:
            sp = SPEC[lab]
            msk = np.zeros(len(cq), dtype=np.int8)
            cnt = np.zeros(1, dtype=np.int64)
            fov.overlay_masks(cq[None, :], np.ascontiguousarray(w)[None, :], max20[None, :],
                              sma10[None, :], START, len(cq) - 1, sp["d"], sp["kind_r"], sp["r"],
                              msk[None, :], cnt)
            wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
            out["overlay"][lab] = {
                "state_at_peak": ("out" if msk[i_peak] == 1 else "in"),
                "exits_during_episode": int(sum(1 for j in range(i_ep, len(cq))
                                                if msk[j] == 1 and (j == i_ep or msk[j - 1] == 0))),
                "sessions_out_in_episode": int(msk[i_ep:].sum()),
            }
        r = fe_sim.simulate(so, sc, wq, env, TAX_CAPITALS[GATE_CAPITAL], i0, len(days) - 1,
                            arm="K", mode="pre_tax", m=M)
        out["arms"][lab] = measure(r["_nav"], i0 - 1)
    out["context"] = {"QQQ": {"return_from_reference_pct": out["qqq_return_from_reference_pct"],
                              "return_from_peak_pct": out["qqq_return_from_peak_pct"]}}
    qld = {}
    with EPI_QLD.open() as fh:
        for r in csv.DictReader(fh):
            qld[r["date"]] = float(r["close"])
    qd = sorted(qld)
    qn = np.array([qld[d] for d in qd])
    j_peak = bisect.bisect_left(qd, days[i_peak])
    out["context"]["QLD"] = {"return_from_peak_pct": float((qn[-1] / qn[j_peak] - 1.0) * 100),
                             "max_dd_inside_episode_pct": float((1.0 - np.min(qn[j_peak + 1:])
                                                                 / np.max(qn[j_peak + 1:])) * 100)}
    return out


# ----------------------------------------------------------------------------- gates
def gates(pre, at, boot, oos, oos_boot):
    out = {}
    for arm in ARMS:
        g = {}

        def g1(mkey):
            oks = {}
            for md in ("base", "calibrated"):
                a, k = pre[md][arm][mkey], pre[md][CONTROL][mkey]
                direct = a["cagr"] >= k["cagr"]
                cal = (a["calmar"] is not None and k["calmar"] is not None
                       and a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)
                oks[md] = bool(direct or cal)
            return oks

        g1m2, g1m1 = g1("m2"), g1("m1")
        g["G1"] = {"pass": all(g1m2.values()), "detail": g1m2,
                   "delta_cagr_pp": {md: (pre[md][arm]["m2"]["cagr"] - pre[md][CONTROL]["m2"]["cagr"]) * 100
                                     for md in ("base", "calibrated")},
                   "calmar_ratio": {md: (pre[md][arm]["m2"]["calmar"] / pre[md][CONTROL]["m2"]["calmar"])
                                    for md in ("base", "calibrated")}}
        g["G2"] = {"pass": all(pre[md][arm]["m2"]["max_dd"] <= pre[md][CONTROL]["m2"]["max_dd"] + 0.01
                               for md in ("base", "calibrated")),
                   "delta_dd_pp": {md: (pre[md][arm]["m2"]["max_dd"] - pre[md][CONTROL]["m2"]["max_dd"]) * 100
                                   for md in ("base", "calibrated")}}
        g["G3"] = {"pass": all(pre[md][arm]["m2"]["cagr_2017"] >= pre[md][CONTROL]["m2"]["cagr_2017"] - 0.01
                               for md in ("base", "calibrated")),
                   "delta_2017_pp": {md: (pre[md][arm]["m2"]["cagr_2017"]
                                          - pre[md][CONTROL]["m2"]["cagr_2017"]) * 100
                                     for md in ("base", "calibrated")}}
        n_above = sum(1 for p in SUBPERIODS
                      if pre["base"][arm]["m2"]["subperiods"][p[0]]
                      > pre["base"][CONTROL]["m2"]["subperiods"][p[0]])
        g["G4"] = {"pass": n_above >= 2, "n_above": n_above,
                   "delta_pp": {p[0]: (pre["base"][arm]["m2"]["subperiods"][p[0]]
                                       - pre["base"][CONTROL]["m2"]["subperiods"][p[0]]) * 100
                                for p in SUBPERIODS}}
        g5 = {md: boot["pre"][md][arm]["median_delta_pp"] for md in ("base", "calibrated")}
        g["G5"] = {"pass": all(v >= 0 for v in g5.values()), "median_delta_pp": g5,
                   "share_gt_0": {md: boot["pre"][md][arm]["share_gt_0"] for md in ("base", "calibrated")}}
        g["G6"] = {"pass": all(g1m1.values()), "detail": g1m1}
        d_oos = {w: oos[w][arm]["delta_cagr_pp"] for w in oos}
        ddd = {w: oos[w][arm]["delta_max_dd_pp"] for w in oos}
        n_ok = sum(1 for w in d_oos if d_oos[w] >= -0.5 and ddd[w] <= 3.0)
        g["G7"] = {"pass": bool(np.median(list(d_oos.values())) >= 0 and n_ok >= 5
                                and max(ddd.values()) <= 8.0),
                   "median_delta_pp": float(np.median(list(d_oos.values()))),
                   "n_windows_ok": n_ok, "max_delta_dd_pp": float(max(ddd.values())),
                   "delta_cagr_pp": d_oos, "delta_dd_pp": ddd}
        g["G8"] = {"pass": oos_boot["pooled"][arm]["diff_vs_K_median_pp"] >= 0,
                   "median_delta_pp": oos_boot["pooled"][arm]["diff_vs_K_median_pp"],
                   "share_gt_0": oos_boot["pooled"][arm]["share_gt_0"]}
        ratio = pre["base"][arm]["m2"]["turnover_annual"] / pre["base"][CONTROL]["m2"]["turnover_annual"]
        g["G9"] = {"pass": ratio <= 2.0, "turnover_ratio": ratio}
        atd = {wn: at[wn][arm]["delta_after_tax_pp"] for wn in TAX_WINDOWS}
        g["G10"] = {"pass": all(v > 0 for v in atd.values()), "delta_pp": atd}
        bt = {}
        for mode, key in (("progressive", "G11"), ("flat", "G12")):
            d = boot["tax"][GATE_CAPITAL][mode][arm]
            bt[key] = {"median_delta_pp": d["median_delta_pp"], "share_gt_0": d["share_gt_0"],
                       "pass": bool(d["median_delta_pp"] >= 0 and d["share_gt_0"] >= 0.55)}
        g["G11"], g["G12"] = bt["G11"], bt["G12"]
        g["all"] = all(g[k]["pass"] for k in
                       ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11", "G12"))
        g["failed"] = [k for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                                   "G10", "G11", "G12") if not g[k]["pass"]]
        out[arm] = g
    passing = [a for a in ARMS if out[a]["all"]]
    if passing:
        sel = max(passing, key=lambda a: (at["2000_03_to_2026_09"][a]["after_tax_cagr"],
                                          pre["base"][a]["m2"]["cagr"]))
    else:
        sel = None
    out["selected"] = sel
    out["passing"] = passing
    out["control"] = CONTROL
    out["rule"] = ("every one of G1-G12 must pass; among passers, the highest after-tax CAGR on "
                   "2000-03..2026-09 wins; if none passes, no arm is selected")
    return out


# ----------------------------------------------------------------------------- harness
def _parity_quick():
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                       cwd=str(ROOT), capture_output=True, text=True)
    ok = (p.returncode == 0) and ("parity PASS" in p.stdout)
    return {"pass": bool(ok), "returncode": int(p.returncode)}


def harness(deep=True):
    checks, bad = {}, 0
    checks["R1_parity_quick"] = _parity_quick()
    if not checks["R1_parity_quick"]["pass"]:
        bad += 1

    built = build_main()
    days, T = built["days"], built["T"]

    # R2: K's anchors and K - R2 through this study's code
    anchors = {"pre_tax": {}, "after_tax": {}, "K_minus_R2": {}}
    bad_anchor = 0
    for md in FIN:
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T - 1)
        for m in (1, 2):
            r = run_arm(built, CONTROL, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T - 1, env, m=m)
            ref = REF_K[(md, m)]
            dc = r["cagr_usd_unliquidated"] - ref["cagr"]
            dd = r["max_dd_nav_usd"] - ref["max_dd"]
            anchors["pre_tax"][f"{md}_m{m}"] = {
                "reference_cagr": ref["cagr"], "reproduced_cagr": r["cagr_usd_unliquidated"],
                "diff_pp": dc * 100, "reference_max_dd": ref["max_dd"],
                "reproduced_max_dd": r["max_dd_nav_usd"], "diff_dd_pp": dd * 100}
            if abs(dc) > TOL_PP or abs(dd) > TOL_PP:
                bad_anchor += 1
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        rk = run_arm(built, CONTROL, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
        rr = run_arm(built, R2REF, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
        anchors["after_tax"][wn] = {"reference": REF_K_AT[wn], "reproduced": rk["cagr_usd_liquidated"],
                                    "diff_pp": (rk["cagr_usd_liquidated"] - REF_K_AT[wn]) * 100}
        dkr = rk["cagr_usd_liquidated"] - rr["cagr_usd_liquidated"]
        anchors["K_minus_R2"][wn] = {"value_pp": dkr * 100,
                                     "reference_pp": REF_K_AT_DELTA_VS_R2[wn] * 100}
        if abs(anchors["after_tax"][wn]["diff_pp"]) > 0.01:
            bad_anchor += 1
        if abs(dkr - REF_K_AT_DELTA_VS_R2[wn]) > 1e-4:
            bad_anchor += 1
    checks["R2_K_anchors"] = {"pass": bool(bad_anchor == 0), "n_failed_cells": bad_anchor,
                              "detail": anchors}
    if bad_anchor:
        bad += 1

    # R3: the overlay kernel against the Python reference, all six arms, both models
    mism, cells = 0, 0
    detail = {}
    for arm in ARMS:
        sp = SPEC[arm]
        for md in FIN:
            w = built["models"][md]["w"]
            ref_mask, ref_eps = overlay_ref(built["qqq"], w, built["max20"], built["sma10"],
                                            START, T - 1, sp["d"], sp["kind_r"], sp["r"])
            got_mask = built["masks"][(md, arm)]
            got_eps = built["episodes"][arm] if md == "base" else None
            cells += 1
            ok = np.array_equal(ref_mask, got_mask)
            if got_eps is not None:
                ok = ok and len(ref_eps) == len(got_eps) and all(
                    a["ex"] == b["ex"] and a["re"] == b["re"] and a["hi"] == b["hi"]
                    and a["lo_i"] == b["lo_i"] and a["lo_v"] == b["lo_v"]
                    for a, b in zip(ref_eps, got_eps))
            if not ok:
                mism += 1
            detail[f"{arm}_{md}"] = {"masks_equal": bool(np.array_equal(ref_mask, got_mask)),
                                     "sessions_out": int(got_mask.sum()),
                                     "exits": int(built["counts"][(md, arm)])}
    checks["R3_overlay_kernel_vs_reference"] = {"pass": bool(mism == 0), "cells": cells,
                                                "mismatches": mism}
    if mism:
        bad += 1

    # R4: a never-firing overlay is exactly K (targets bitwise, NAV bitwise)
    ok_t = ok_nav = True
    msk = np.zeros(T, dtype=np.int8)
    cnt = np.zeros(1, dtype=np.int64)
    fov.overlay_masks(built["qqq"][None, :], built["models"]["base"]["w"][None, :],
                      built["max20"][None, :], built["sma10"][None, :], START, T - 1,
                      1.0, 0, 0.5, msk[None, :], cnt)
    w0 = np.where(msk == 1, 0.0, built["models"]["base"]["w"])
    if int(cnt[0]) != 0 or not np.array_equal(w0, built["models"]["base"]["w"]):
        ok_t = False
    env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T - 1)
    k_nav = run_arm(built, CONTROL, "base", "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T - 1, env)["_nav"]
    r0 = fe_sim.simulate(built["models"]["base"]["o"], built["models"]["base"]["c"], w0, env,
                         TAX_CAPITALS[GATE_CAPITAL], START, T - 1, arm="K", mode="pre_tax", m=M)
    ok_nav = np.array_equal(r0["_nav"], k_nav)
    checks["R4_never_firing_identity"] = {"pass": bool(ok_t and ok_nav),
                                          "targets_bitwise_equal": bool(ok_t),
                                          "nav_bitwise_equal": bool(ok_nav)}
    if not (ok_t and ok_nav):
        bad += 1

    # R5: hedge identities -- all-zero mask is the identity; a masked session has zero return
    ok_px = True
    for md in FIN:
        o, c = built["models"][md]["o"], built["models"][md]["c"]
        hm = np.zeros((1, T), dtype=np.int8)
        oo, cc = np.empty_like(o)[None, :], np.empty_like(c)[None, :]
        fbt.masked_prices(o[None, :], c[None, :], hm, oo, cc)
        if not (np.array_equal(oo[0], o) and np.array_equal(cc[0], c)):
            ok_px = False
    hm = np.zeros((1, T), dtype=np.int8)
    hm[0, 1000] = 1
    o, c = built["models"]["base"]["o"], built["models"]["base"]["c"]
    oo, cc = np.empty_like(o)[None, :], np.empty_like(c)[None, :]
    fbt.masked_prices(o[None, :], c[None, :], hm, oo, cc)
    zero_ret = (abs(cc[0, 1000] / cc[0, 999] - 1.0) < 1e-15
                and abs(cc[0, 1001] / cc[0, 1000] - c[1001] / c[1000]) < 1e-12)
    checks["R5_hedge_identities"] = {"pass": bool(ok_px and zero_ret),
                                     "all_zero_mask_identity": bool(ok_px),
                                     "masked_session_zero_return": bool(zero_ret)}
    if not (ok_px and zero_ret):
        bad += 1

    # R6: chunk_legs_pc == fe_boot.chunk_legs
    win = fe_boot.BootWindow()
    pos = fe_boot.draw_positions(np.random.default_rng(7), BLOCK, 16,
                                 fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
    pc, e2c, legs, w_e2 = fbt.chunk_legs_pc(win.q, win.y, pos)
    l2, e2b, w2 = fe_boot.chunk_legs(win.q, win.y, pos)
    ok = all(np.array_equal(legs[md][i], l2[md][i]) for md in FIN for i in range(3))
    checks["R6_chunk_legs_pc"] = {"pass": bool(ok and np.array_equal(e2c, e2b)
                                              and np.array_equal(w_e2, w2))}
    if not checks["R6_chunk_legs_pc"]["pass"]:
        bad += 1

    # R7: the bootstrap extension with K's targets reproduces fe_boot.boot bit for bit
    if deep:
        specs = {CONTROL: {"rule": (True, True, True), "kind": "k"},
                 "C0": {"rule": (False, False, False), "kind": "k"}}
        raw = boot_run(specs, n_pre=200, n_tax=200, capitals=(GATE_CAPITAL,),
                       modes=("progressive", "flat"), chunk=256, quiet=True)
        ref = fe_boot.boot(seed=SEED, block=BLOCK, arms=("C0", CONTROL), n_pre=200, n_tax=200,
                           capitals=(GATE_CAPITAL,), modes=("progressive", "flat"),
                           anchor_e2=False, deltas=(), baselines=(CONTROL,), label="R7",
                           quiet=True, keep_paths=True)
        cells, mism = 0, 0
        for md in FIN:
            for a in ("C0", CONTROL):
                cells += 1
                if not np.array_equal(raw["pre"][(md, a)], ref["paths"]["pre"][(md, a)]):
                    mism += 1
        for mode in ("progressive", "flat"):
            for a in ("C0", CONTROL):
                cells += 1
                if not np.array_equal(raw["tax"][GATE_CAPITAL][mode][a],
                                      ref["paths"]["tax"][GATE_CAPITAL][mode][a]):
                    mism += 1
        checks["R7_boot_extension_parity"] = {"pass": bool(mism == 0), "cells": cells,
                                              "mismatches": mism}
        if mism:
            bad += 1

    checks["n_failed"] = int(bad)
    checks["pass"] = bool(bad == 0)
    return checks


def _jsonable(x):
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    raise TypeError(type(x))


# ----------------------------------------------------------------------------- smoke
def smoke():
    """Synthetic prices only: kernel/reference equality, mechanics, hedge zero return."""
    rng = np.random.default_rng(11)
    n = 900
    r = rng.normal(0.0003, 0.014, n)
    price = 100.0 * np.cumprod(1 + r)
    w = np.full(n, 0.9)
    w[200:260] = 0.0
    max20 = fov.rolling_max(price)[0]
    sma10 = fe_signals.sma_up(price, 10)[0]
    checks = {}
    for arm in ARMS:
        sp = SPEC[arm]
        ref, _ = overlay_ref(price, w, max20, sma10, 40, n - 1, sp["d"], sp["kind_r"], sp["r"])
        got, cnt, recs = overlay_records(price, w, max20, sma10, 40, n - 1,
                                        sp["d"], sp["kind_r"], sp["r"])
        checks[f"kernel_eq_ref_{arm}"] = bool(np.array_equal(ref, got))
        checks[f"fires_{arm}"] = bool(cnt > 0)
        # the first exit must be at least d below the trailing high, the first re-entry after it
        if recs:
            r0 = recs[0]
            checks[f"exit_depth_{arm}"] = bool(price[r0["ex"]] <= (1 - sp["d"]) * r0["hi"] + 1e-12)
            checks[f"reentry_after_exit_{arm}"] = bool(r0["re"] == -1 or r0["re"] > r0["ex"])
    # a zeroing overlay is K's own targets
    z = np.zeros(n, dtype=np.int8)
    c0 = np.zeros(1, dtype=np.int64)
    fov.overlay_masks(price[None, :], w[None, :], max20[None, :], sma10[None, :], 40, n - 1,
                      1.0, 0, 0.5, z[None, :], c0)
    checks["never_fires"] = bool(int(c0[0]) == 0 and z.sum() == 0)
    # hedge identity and zero return
    o = price.copy()
    hm = np.zeros((1, n), dtype=np.int8)
    hm[0, 300] = 1
    oo, cc = np.empty_like(o)[None, :], np.empty_like(price)[None, :]
    fbt.masked_prices(o[None, :], price[None, :], hm, oo, cc)
    checks["hedge_zero_return"] = bool(abs(cc[0, 300] / cc[0, 299] - 1.0) < 1e-15)
    hm[0, 300] = 0
    fbt.masked_prices(o[None, :], price[None, :], hm, oo, cc)
    checks["hedge_identity"] = bool(np.array_equal(oo[0], o) and np.array_equal(cc[0], price))
    checks["pass"] = bool(all(checks.values()))
    return {"pass": checks["pass"], "checks": checks}


# ----------------------------------------------------------------------------- the run
def run():
    t_start = time.time()
    h = harness(deep=True)
    built = build_main()
    days, T = built["days"], built["T"]

    pre = stage_pre_tax(built)
    at = stage_after_tax(built)
    diag = diagnostics(built)
    print("  clean bootstrap (5,000 pre-tax / 1,000 after-tax paths) ...", flush=True)
    boot = stage_bootstrap(quiet=False)
    print("  OOS windows ...", flush=True)
    oos = stage_oos()
    print("  pooled OOS bootstrap (5 markets x 2,000 paths) ...", flush=True)
    oos_boot = stage_oos_boot()
    epi = stage_epi()
    g = gates(pre, at, boot, oos, oos_boot)

    elapsed = time.time() - t_start
    res = {
        "campaign": "short_term_reentry_v1",
        "status": "candidate study with gates; nothing adopted",
        "params": {
            "main_window": {"start": days[START], "end": days[T - 1], "sessions": T - START},
            "after_tax_windows": {k: list(v) for k, v in TAX_WINDOWS.items()},
            "capital_usd": TAX_CAPITALS[GATE_CAPITAL], "mode": "progressive, liquidated", "m": M,
            "bootstrap": {"seed": SEED, "block": BLOCK, "paths_pre": PRE_PATHS,
                          "paths_tax": TAX_PATHS,
                          "calendar": [fe_config.TAX_BOOT_LO, fe_config.TAX_BOOT_HI]},
            "arms": {arm: SPEC[arm] for arm in ARMS},
            "overlay": ("exit when close <= (1-d) * trailing-20-session high truncated at the "
                        "current in-stint; re-enter on close >= (1+r) * post-exit low (A-D) or "
                        "close > SMA10 (E-F), never at the exit session; K's weight path "
                        "unchanged underneath; hedge variants zero the 2x return while out"),
            "diagnostic_variants": {HEDGES[a]: TITLE[HEDGES[a]] for a in ARMS},
        },
        "harness": h,
        "pre_tax": pre,
        "after_tax": at,
        "diagnostics": diag,
        "bootstrap": boot,
        "oos": oos,
        "oos_bootstrap": oos_boot,
        "episode_2026": epi,
        "gates": g,
        "wall_clock_s": float(elapsed),
    }
    FULL_OUT.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable))
    write_summary(res)
    return res


# ----------------------------------------------------------------------------- summary
def write_summary(res):
    pre, at, boot, diag = res["pre_tax"], res["after_tax"], res["bootstrap"], res["diagnostics"]
    g, oos, ob = res["gates"], res["oos"], res["oos_bootstrap"]
    L = []
    L.append("short_term_reentry_v1 -- tradeable short-term exit + rebound-confirmed re-entry on K")
    L.append("")
    L.append(f"main window {res['params']['main_window']['start']} .. {res['params']['main_window']['end']}"
             f"  ({res['params']['main_window']['sessions']} sessions); after tax $100k progressive, m=2")
    L.append(f"wall clock of --run: {res['wall_clock_s']:.1f} s")
    L.append(f"harness pass = {res['harness']['pass']} (parity --quick = "
             f"{res['harness']['R1_parity_quick']['pass']})")
    L.append("")
    L.append("== pre-tax main window (CAGR %, max DD %, sells/yr, turnover/yr) ==")
    L.append("  arm                base CAGR  cal CAGR   base DD   cal DD  sells/yr  turn/yr  exits")
    for lab in (CONTROL, R2REF) + ARMS + tuple(HEDGES[a] for a in ARMS):
        b, c = pre["base"][lab]["m2"], pre["calibrated"][lab]["m2"]
        ex = "-" if lab not in ARMS else f"{b['n_exits']}"
        L.append(f"  {_til(lab):<18} {b['cagr']*100:>8.2f}  {c['cagr']*100:>8.2f}  "
                 f"{b['max_dd']*100:>8.2f}  {c['max_dd']*100:>8.2f}  {b['sells_per_year']:>8.2f}  "
                 f"{b['turnover_annual']:>7.2f}  {ex:>5}")
    L.append("")
    L.append("== after tax $100k: CAGR (%) with delta vs K (pp) ==")
    L.append("  arm                     2000(d)            2010(d)            2017(d)        tax$ (2000)")
    for lab in (R2REF,) + ARMS + tuple(HEDGES[a] for a in ARMS):
        cells = " ".join(f"{at[wn][lab]['after_tax_cagr']*100:>7.2f}({at[wn][lab]['delta_after_tax_pp']:+.2f})"
                         for wn in TAX_WINDOWS)
        L.append(f"  {_til(lab):<18} {cells}  {at['2000_03_to_2026_09'][lab]['tax_usd']:>10,.0f}")
    L.append("")
    L.append("== clean bootstrap, median delta vs K (pp) / share > 0 ==")
    L.append("  arm                 pre base  pre cal   B1 recent    B2 flat")
    for lab in (R2REF,) + ARMS + tuple(HEDGES[a] for a in ARMS):
        pb = boot["pre"]["base"][lab]
        pc_ = boot["pre"]["calibrated"][lab]
        b1 = boot["tax"][GATE_CAPITAL]["progressive"][lab]
        b2 = boot["tax"][GATE_CAPITAL]["flat"][lab]
        L.append(f"  {_til(lab):<18} {pb['median_delta_pp']:>+8.2f} {pc_['median_delta_pp']:>+8.2f}   "
                 f"{b1['median_delta_pp']:>+7.2f}/{b1['share_gt_0']:.0%}  "
                 f"{b2['median_delta_pp']:>+7.2f}/{b2['share_gt_0']:.0%}")
    L.append("")
    L.append("== overlay diagnostics (base model, main window) ==")
    L.append("  arm                exits/yr  false<8%  med depth  mean re-entry vs exit  above")
    for arm in ARMS:
        d = diag["arms"][arm]
        L.append(f"  {_til(arm):<18} {d['exits_per_year']:>8.2f}  {d['false_alarm_share']:.0%}"
                 f"  {d['median_depth']*100:>8.2f}%  {d['mean_reentry_vs_exit_pct']:>+19.2f}%"
                 f"  {d['share_reentry_above_exit']:.0%}")
    L.append("")
    L.append("== OOS (calibrated, m=2): delta CAGR (pp) and delta max DD (pp) vs K ==")
    L.append("  arm                  " + " ".join(f"{w:>14s}" for w in oos))
    for arm in ARMS:
        cells = " ".join(f"{oos[w][arm]['delta_cagr_pp']:>+6.2f}/{oos[w][arm]['delta_max_dd_pp']:>+5.2f}"
                         for w in oos)
        L.append(f"  {_til(arm):<18} {cells}")
    L.append(f"  pooled OOS bootstrap median delta: "
             + "  ".join(f"{_til(a)} {ob['pooled'][a]['diff_vs_K_median_pp']:+.2f}" for a in ARMS))
    L.append("")
    L.append("== gates ==")
    L.append("  arm                " + " ".join(f"{k:>5s}" for k in
                                               ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8",
                                                "G9", "G10", "G11", "G12")))
    for arm in ARMS:
        L.append(f"  {_til(arm):<18} " + " ".join(
            f"{'pass' if g[arm][k]['pass'] else 'FAIL':>5s}"
            for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11", "G12")))
    L.append(f"  selected = {g['selected']}  (passing arms: {g['passing']})")
    L.append("")
    L.append("== October 2026 (report only, pre-tax, extended Yahoo series) ==")
    epi = res["episode_2026"]
    L.append(f"  data end {epi['data_end']}; QQQ peak {epi['peak_date']} {epi['peak_close_qqq']:.2f}; "
             f"QQQ {epi['qqq_return_from_peak_pct']:+.2f}% from peak "
             f"({epi['qqq_return_from_reference_pct']:+.2f}% from {epi['reference_date_vt_level_k']})")
    for lab in (CONTROL,) + ARMS:
        a = epi["arms"][lab]
        extra = ""
        if lab in ARMS:
            o = epi["overlay"][lab]
            extra = f"  out {o['sessions_out_in_episode']} sess, {o['exits_during_episode']} exits"
        L.append(f"  {_til(lab):<18} {a['return_from_peak_pct']:>+7.2f}%  "
                 f"DD {a['max_dd_inside_episode_pct']:>5.2f}%{extra}")
    SUMMARY.write_text("\n".join(L) + "\n")


# ----------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="synthetic-data checks only")
    ap.add_argument("--harness", action="store_true", help="reproduction and parity checks")
    ap.add_argument("--run", action="store_true", help="run every stage; write RESULTS.json / SUMMARY.txt")
    args = ap.parse_args(argv)
    if args.smoke:
        r = smoke()
        print(json.dumps(r, indent=1, default=_jsonable))
        return 0 if r["pass"] else 1
    if args.harness:
        r = harness(deep=True)
        print(json.dumps(r, indent=1, default=_jsonable))
        return 0 if r["pass"] else 1
    if args.run:
        r = run()
        print(f"wrote {FULL_OUT.name} and {SUMMARY.name}; harness pass={r['harness']['pass']}")
        return 0 if r["harness"]["pass"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
