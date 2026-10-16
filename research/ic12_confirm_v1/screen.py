"""ic12_confirm_v1 -- the confirmation study of IC12 (NFCI veto) on a real-time signal.

    python -m research.ic12_confirm_v1.fetch_vintages          # once: the ALFRED vintages
    python -m research.ic12_confirm_v1.screen --save-state     # once: the real-time state CSV
    python -m research.ic12_confirm_v1.screen --harness        # before any arm
    python -m research.ic12_confirm_v1.screen --run            # RESULTS.json + SUMMARY.txt

The plan is `PREREGISTRATION.md` (committed before any non-control arm is simulated). Simulation,
after-tax accounting and the bootstraps all go through `research.fast_engine`; this module builds
arms, calls the framework's own stage functions (`research.batch_screen.stages`) and aggregates.
No per-day or per-path Python loop, no engine edit.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.batch_screen import api, boot as B, stages, windows
from research.batch_screen.stages import GATE_CAPITAL
from research.fast_engine import fe_study
from research.fast_engine.fe_config import (
    BLOCK,
    PRE_BOOT_PATHS,
    SEED,
    TAX_BOOT_PATHS,
    TAX_CAPITALS,
    TAX_WINDOWS,
)
from research.ideas import IC12
from research.plugins_c2 import native

from . import ic12_rt, rt_state

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
HARNESS = HERE / "HARNESS.json"

CONTROL = "k_control/K"
STUDY_ARMS = ("IC12/A", "IC12/B", "IC12rt/A", "IC12rt/B")
NEW_ARMS = ("IC12rt/A", "IC12rt/B")
OLD_ARMS_ONLY = ("A", "B")

#: this study's E3 (PREREGISTRATION §6): a seed no earlier study used, three blocks.
E3_SEED = 20261017
E3_BLOCKS = (10, 20, 40)
E3_PATHS = 1000
E2_SHARE = 0.60
PURE_YEARS = tuple(range(2011, 2022))
STAGE2_KEYS = ("G1_base", "G1_calibrated", "G2_base", "G2_calibrated", "G3_base",
               "G3_calibrated", "G4", "G5_base", "G5_calibrated", "G6_base", "G6_calibrated",
               "G7", "G8", "G9")

# ----------------------------------------------------------------------------- helpers
def rnd(obj):
    """Round every float to 6 dp and sort every dict, so RESULTS.json is byte-stable."""
    if isinstance(obj, dict):
        return {str(k): rnd(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [rnd(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return rnd(obj.tolist())
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return round(f, 6) if np.isfinite(f) else None
    return obj


def arms() -> dict:
    """The study's arms: the control, the two committed IC12 arms, the two real-time arms."""
    out = {CONTROL: api.load_arm("k_control", "K")}
    for a in OLD_ARMS_ONLY:
        out[f"IC12/{a}"] = api.load_arm("IC12", a)
        arm = api.resolve_arm(ic12_rt, a)          # spec = research.ideas.IC12.ARMS[a]
        arm.update({"id": f"IC12rt/{a}", "idea": "IC12rt", "arm_id": a, "mod": ic12_rt})
        out[f"IC12rt/{a}"] = arm
    return out


def boot_vectors(arm_list, seed, block, n_pre, n_tax, label):
    """Raw per-path CAGRs of one draw; arms listed together share the draw (paired Δ)."""
    return B.boot(arm_list, seed=seed, block=block, n_pre=n_pre, n_tax=n_tax,
                  capitals=(GATE_CAPITAL,), modes=B.TAX_MODES, cash_yield="none",
                  label=label, quiet=True)


def e3_vectors(arm):
    """This study's E3 draws (seed 20261017, blocks 10/20/40) for ONE arm, by block and mode."""
    out = {}
    for blk in E3_BLOCKS:
        b = boot_vectors([arm], E3_SEED, blk, 0, E3_PATHS, f"E3 block {blk}")
        out[f"block{blk}"] = {md: b["tax"][GATE_CAPITAL][md][arm["id"]] for md in B.TAX_MODES}
    return out


# ----------------------------------------------------------------------------- one arm
def gather(arm, ctrl_vec, ctrl_arm=None, quiet=True):
    """Everything the preregistered gates need, for one arm.

    `ctrl_vec=None` means "this arm is the control". `ctrl_arm` is the control's arm object: the
    pooled OOS bootstrap (G8) is drawn for both arms **in one call**, because an arm whose OOS
    market list is a strict subset would otherwise not share the control's paths (the pooled vector
    is the per-market concatenation, and `PREREGISTRATION` counts IC12's M2 as not applicable).
    """
    aid = arm["id"]
    vecs = {}
    rec = stages.stage1(arm, "none")
    b12 = stages.stage1_vectors(arm, "none", quiet=quiet, label=aid)
    rec["boot"], rec["boot_level"] = {"pre": {}, "tax": {}}, {"pre": {}, "tax": {}}
    for md in ("base", "calibrated"):
        v = b12["pre"][md][aid]
        base = v if ctrl_vec is None else ctrl_vec[f"pre_{md}"]
        rec["boot"]["pre"][md] = B.delta_stats(v - base)
        rec["boot_level"]["pre"][md] = B.delta_stats(v)
        vecs[f"pre_{md}"] = v
    for md in B.TAX_MODES:
        v = b12["tax"][GATE_CAPITAL][md][aid]
        base = v if ctrl_vec is None else ctrl_vec[f"tax_{md}"]
        rec["boot"]["tax"][md] = B.delta_stats(v - base)
        rec["boot_level"]["tax"][md] = B.delta_stats(v)
        vecs[f"tax_{md}"] = v

    rec["after_tax"].update({k: v for k, v in stages.stage2_main_windows(arm, "none").items()
                             if k not in rec["after_tax"]})
    rec["oos"] = stages.stage2_oos(arm, "none")
    rec["etf_pre"], rec["etf_after_tax"] = stages.stage2_instruments(arm, "none")
    rec["rolling"] = stages.stage2_rolling(arm, "none")

    fresh = stages.stage2_fresh(arm, "none", label=f"{aid} fresh")
    rec["fresh"] = {blk: {"tax": {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][aid])
                                for md in B.TAX_MODES}}
                    for blk in fresh}

    markets, na_markets = stages.oos_pooled_markets(arm, "none")
    if markets:
        group = [arm] if ctrl_arm is None else [arm, ctrl_arm]
        pooled = B.oos_pooled(group, cash_yield="none", markets=tuple(markets))
        vecs["oos_pooled"] = pooled["pooled"][aid]
        base = vecs["oos_pooled"] if ctrl_arm is None else pooled["pooled"][ctrl_arm["id"]]
        rec["oos_pooled"] = B.delta_stats(vecs["oos_pooled"] - base)
        if len(markets) != len(stages.OOS_POOLED_MARKETS):
            rec["oos_pooled"]["markets"] = list(markets)
            rec["oos_pooled"]["markets_not_applicable"] = na_markets
    else:
        rec["oos_pooled"] = {"not_evaluable": "no OOS market's pooled bootstrap could be drawn",
                             "markets_not_applicable": na_markets}

    return {"record": rec, "vectors": vecs, "e3": e3_vectors(arm)}


# ----------------------------------------------------------------------------- gates
def rolling_cells(rec, ctrl, years):
    ds = np.asarray([rec["rolling"][str(y)]["after_tax_cagr"]
                     - ctrl["rolling"][str(y)]["after_tax_cagr"] for y in years], dtype=np.float64)
    return {"n_starts": int(ds.size), "median_delta_pp": float(np.median(ds) * 100),
            "mean_delta_pp": float(ds.mean() * 100),
            "share_gt_0": float(np.mean(ds > 0)), "share_ge_0": float(np.mean(ds >= 0)),
            "min_delta_pp": float(ds.min() * 100), "max_delta_pp": float(ds.max() * 100)}


def gate_block(rec, ctrl, e3, ctrl_e3):
    """Every gate of PREREGISTRATION §6, plus the verdict, for one arm."""
    s1_ok, s1 = stages.stage1_pass(rec, ctrl)
    g = stages.gates(rec, ctrl)
    wins = {wn: rec["after_tax"][wn]["after_tax_cagr"] - ctrl["after_tax"][wn]["after_tax_cagr"]
            for wn in TAX_WINDOWS}
    n_pos = sum(1 for v in wins.values() if v > 0)
    roll21 = rolling_cells(rec, ctrl, range(2001, 2022))
    e2 = {"windows": {k: float(v * 100) for k, v in wins.items()},
          "n_windows_positive": int(n_pos), "rolling": roll21,
          "pass": bool(n_pos >= 2 and roll21["median_delta_pp"] > 0
                       and roll21["share_gt_0"] >= E2_SHARE)}
    roll_pure = rolling_cells(rec, ctrl, PURE_YEARS)
    w2017 = float(wins["2017_01_to_end"] * 100)
    e2p = {"window_2017_delta_pp": w2017, "rolling": roll_pure, "years": list(PURE_YEARS),
           "pass": bool(w2017 > 0 and roll_pure["median_delta_pp"] > 0
                        and roll_pure["share_gt_0"] >= E2_SHARE)}
    e3c, ok_e3 = {}, True
    for blk in e3:
        for md in B.TAX_MODES:
            cell = B.delta_stats(e3[blk][md] - ctrl_e3[blk][md])
            good = bool(cell["median_delta_pp"] >= 0 and cell["share_gt_0"] >= 0.55)
            e3c[f"{blk}_{md}"] = {"median_delta_pp": cell["median_delta_pp"],
                                  "share_gt_0": cell["share_gt_0"], "pass": good}
            ok_e3 = ok_e3 and good
    e3_gate = {"cells": e3c, "pass": bool(ok_e3)}
    avail = stages.applicability(rec)
    required = {"stage1": bool(s1_ok), **{k: bool(g.get(k, False)) for k in STAGE2_KEYS},
                "AT": bool(g["AT_pass"]), "E2": bool(e2["pass"]), "E2p": bool(e2p["pass"]),
                "E3": bool(e3_gate["pass"]), "E4": bool(g["E1"]["pass"]),
                "holdout_sufficient": bool(avail["sufficient"])}
    return {
        "stage1": {"gates": s1, "pass": bool(s1_ok)},
        "pre_tax": {k: g[k] for k in STAGE2_KEYS + ("G4_wins", "G7_detail", "G8_median_pp")
                    if k in g},
        "AT": {"windows": {k: round(float(v * 100), 6) for k, v in wins.items()},
               "B1_recent": g["AT_B1_recent"], "B2_flat": g["AT_B2_flat"], "pass": g["AT_pass"]},
        "E2": e2, "E2p": e2p, "E3": e3_gate, "E4": g["E1"], "FRESH_report_only": g["FRESH"],
        "availability": avail, "required": required,
        "failed": sorted(k for k, v in required.items() if not v),
        "pass": bool(all(required.values())),
    }


# ----------------------------------------------------------------------------- E5 (report only)
def state_profile(days, state, i0, i1):
    """How often the veto bites on `[i0, i1]` and how long its episodes are."""
    s = np.asarray(state)[0][i0:i1 + 1]
    on = np.isfinite(s) & (s > 0.5)
    defined = int(np.isfinite(s).sum())
    ep, run = [], 0
    for v in on:
        if v:
            run += 1
        elif run:
            ep.append(run)
            run = 0
    if run:
        ep.append(run)
    ep = np.asarray(ep, dtype=np.int64)
    yrs = np.asarray([int(d[:4]) for d in np.asarray(days)[i0:i1 + 1]])
    by_decade = {}
    for dec in sorted({int(y // 10 * 10) for y in yrs}):
        m = (yrs // 10 * 10) == dec
        d_def = int((np.isfinite(s) & m).sum())
        by_decade[str(dec)] = {"share_on_of_defined": float((on & m).sum() / d_def) if d_def else 0.0,
                               "defined": d_def}
    return {"sessions": int(s.size), "defined_sessions": defined,
            "share_on_of_defined": float(on.sum() / defined) if defined else 0.0,
            "share_on_of_window": float(on.sum() / s.size), "n_episodes": int(ep.size),
            "episode_sessions_mean": float(ep.mean()) if ep.size else 0.0,
            "episode_sessions_median": float(np.median(ep)) if ep.size else 0.0,
            "episode_sessions_max": int(ep.max()) if ep.size else 0,
            "by_decade": by_decade}


def shortfall_decomposition(arm, days, i0, i1, state_rt, ctrl_arm):
    """2017-window shortfall: daily log-NAV difference vs K, per calendar year and per episode."""
    env = windows.try_env(days, i0, i1, "none")
    panel = windows.main_panel(0.0)[1]
    nav_a = stages.run_one(arm, panel, env, i0, i1, "progressive", 2,
                           TAX_CAPITALS[GATE_CAPITAL])["_nav"]
    nav_k = stages.run_one(ctrl_arm, panel, env, i0, i1, "progressive", 2,
                           TAX_CAPITALS[GATE_CAPITAL])["_nav"]
    # `_nav` spans sessions i0-1 .. i1 (fe_study.pre_stats' convention), so diff element k is the
    # return into session i0+k: the session labels are days[i0 : i1+1], one per diff element.
    d = np.diff(np.log(nav_a)) - np.diff(np.log(nav_k))
    labels = list(np.asarray(days)[i0:i1 + 1])
    on = np.asarray(state_rt)[0][i0:i1 + 1] > 0.5
    assert len(labels) == d.size, (len(labels), d.size)
    years = np.asarray([int(x[:4]) for x in labels])
    per_year = {str(y): float(d[years == y].sum() * 100) for y in sorted(set(years.tolist()))}
    ep, cur = [], []
    for k, v in enumerate(on):
        if v:
            cur.append(k)
        elif cur:
            ep.append(cur)
            cur = []
    if cur:
        ep.append(cur)
    eps = []
    for e in ep:
        lo, hi = e[0], min(e[-1] + 1, d.size - 1)      # fold in the fill session
        eps.append({"start": labels[lo], "end": labels[hi], "sessions": len(e),
                    "delta_log_nav_pp": float(d[lo:hi + 1].sum() * 100)})
    eps.sort(key=lambda r: r["delta_log_nav_pp"])
    return {"from": labels[0], "to": labels[-1], "total_delta_log_nav_pp": float(d.sum() * 100),
            "per_year": per_year, "n_episodes": len(eps),
            "worst_episodes": eps[:6], "best_episodes": eps[-3:][::-1]}


# ----------------------------------------------------------------------------- harness
def _anchor(check, got, ref, tol_pp=0.01):
    return {"check": check, "got": float(got), "reference": float(ref),
            "diff_pp": (float(got) - float(ref)) * 100,
            "pass": bool(abs(float(got) - float(ref)) <= tol_pp / 100.0 + 1e-12)}


def harness():
    """PREREGISTRATION §7: parity, the campaign's anchors, the reproduction and the rt parity."""
    checks = []
    t0 = time.time()
    print("  1/5 research.fast_engine.parity --quick", flush=True)
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": "."})
    checks.append({"check": "parity_quick", "pass": p.returncode == 0,
                   "tail": p.stdout.strip().splitlines()[-1] if p.stdout else ""})

    print("  2/5 K's anchors through the engine", flush=True)
    amap = arms()
    k = amap[CONTROL]
    rec_k = stages.stage1(k, "none")
    win_k = stages.stage2_main_windows(k, "none")
    for n, got, ref in (("K_pre_base_m2", rec_k["main"]["base"]["m2"]["cagr"], 0.157649),
                        ("K_pre_cal_m2", rec_k["main"]["calibrated"]["m2"]["cagr"], 0.151785),
                        ("K_dd_base_m2", rec_k["main"]["base"]["m2"]["max_dd"], 0.466698),
                        ("K_dd_cal_m2", rec_k["main"]["calibrated"]["m2"]["max_dd"], 0.467948),
                        ("K_at2000", rec_k["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"], 0.107533),
                        ("K_at2010", win_k["2010_01_to_end"]["after_tax_cagr"], 0.152354),
                        ("K_at2017", win_k["2017_01_to_end"]["after_tax_cagr"], 0.239720)):
        checks.append(_anchor(n, got, ref))

    print("  3/5 R2's anchors (reference row)", flush=True)
    r2 = api.load_arm("r2_control", "R2")
    rec_r2 = stages.stage1(r2, "none")
    for n, got, ref in (("R2_pre_base_m2", rec_r2["main"]["base"]["m2"]["cagr"], 0.154800),
                        ("R2_pre_cal_m2", rec_r2["main"]["calibrated"]["m2"]["cagr"], 0.149000),
                        ("R2_pre_base_m1", rec_r2["main"]["base"]["m1"]["cagr"], 0.157500),
                        ("R2_dd_base_m2", rec_r2["main"]["base"]["m2"]["max_dd"], 0.467600)):
        checks.append(_anchor(n, got, ref))

    print("  4/5 IC12/A, IC12/B reproduce their bank_v2 records", flush=True)
    b12c = stages.stage1_vectors(k, "none", quiet=True, label="harness control")
    for a in OLD_ARMS_ONLY:
        old = json.loads((ROOT / "research" / "batch_screen" / "results" / "bank_v2"
                          / f"IC12__{a}.json").read_text())
        arm = amap[f"IC12/{a}"]
        rec = stages.stage1(arm, "none")
        b12 = stages.stage1_vectors(arm, "none", quiet=True, label=f"IC12/{a}")
        rec["boot"] = {"pre": {}, "tax": {}}
        for md in B.TAX_MODES:
            rec["boot"]["tax"][md] = B.delta_stats(
                b12["tax"][GATE_CAPITAL][md][arm["id"]]
                - b12c["tax"][GATE_CAPITAL][md][CONTROL])
        rec["after_tax"].update(stages.stage2_main_windows(arm, "none"))
        og = old["gates"]
        for wn, key in (("2000_03_to_2026_09", "AT2000"), ("2017_01_to_end", "AT2017")):
            ref = (rec_k["after_tax"].get(wn) or win_k.get(wn))["after_tax_cagr"]
            checks.append(_anchor(
                f"IC12/{a}_{key}",
                rec["after_tax"][wn]["after_tax_cagr"] - ref,
                og["AT_windows"][wn]["delta_pp"] / 100))
        checks.append(_anchor(f"IC12/{a}_B1_median",
                              rec["boot"]["tax"]["progressive"]["median_delta_pp"],
                              og["AT_B1_recent"]["median_delta_pp"]))
        checks.append(_anchor(f"IC12/{a}_B2_median",
                              rec["boot"]["tax"]["flat"]["median_delta_pp"],
                              og["AT_B2_flat"]["median_delta_pp"]))

    print("  5/5 real-time state parity", flush=True)
    vint = rt_state.vintage_series()
    first_vintage = min(vint)
    live_d, live_v = rt_state.live_series()
    live_state = rt_state.weekly_state(live_d, live_v)
    cal, rt = rt_state.load()
    built_d, built = rt_state.build()
    checks.append({"check": "rt_state_file_matches_rebuild", "pass": bool(
        np.array_equal(cal, built_d) and np.allclose(rt, built, equal_nan=True))})
    pre = cal < first_vintage
    checks.append({"check": "rt_pre_vintage_segment_equals_committed", "pass": bool(
        np.allclose(native.asof(live_d, live_state, cal[pre]), rt[pre], equal_nan=True)),
        "n_weekdays": int(pre.sum())})
    pane = windows.main_panel(0.0)[1]
    labels = native.session_labels(pane)
    s_rt = native.asof(cal, rt, labels)
    s_lv = native.asof(live_d, live_state, labels)
    checks.append({"check": "rt_and_live_differ_on_sessions",
                   "pass": bool(int((np.abs(s_rt - s_lv) > 0.5).sum()) > 0),
                   "sessions_differ": int((np.abs(s_rt - s_lv) > 0.5).sum()),
                   "sessions": int(s_rt.size)})
    for a in OLD_ARMS_ONLY:
        spec = api.resolve_arm(ic12_rt, a)["params"]
        want_ic = IC12.targets(pane, spec)
        want_lv = ic12_rt.targets_live(pane, spec)
        want_rt = ic12_rt.targets(pane, spec)
        checks.append({"check": f"IC12rt/{a}_targets_live_equals_IC12",
                       "pass": bool(np.array_equal(want_ic, want_lv))})
        checks.append({"check": f"IC12rt/{a}_targets_rt_differs_from_IC12",
                       "pass": bool(np.abs(want_rt - want_ic).max() > 1e-12),
                       "max_abs_weight_diff": float(np.abs(want_rt - want_ic).max())})
    bad = [c for c in checks if not c["pass"]]
    print(f"  harness {'PASS' if not bad else 'FAIL'} "
          f"({len(checks) - len(bad)}/{len(checks)} checks, {time.time() - t0:.0f}s)", flush=True)
    for c in bad:
        print(f"    FAIL {c['check']}: {c}", flush=True)
    return checks, (not bad)


# ----------------------------------------------------------------------------- run
def run(quiet=True):
    t0 = time.time()
    amap = arms()
    print("  control (K)", flush=True)
    ctrl_res = gather(amap[CONTROL], None, quiet=quiet)
    ctrl_rec = ctrl_res["record"]
    ctrl_vec = ctrl_res["vectors"]           # carries "oos_pooled" (drawn in this very call)
    ctrl_e3 = ctrl_res["e3"]

    out = {"study": "ic12_confirm_v1", "control": CONTROL, "arms": {}, "e3": {},
           "params": {"seed": SEED, "block": BLOCK, "n_pre": PRE_BOOT_PATHS,
                      "n_tax": TAX_BOOT_PATHS, "capital": GATE_CAPITAL,
                      "e3_seed": E3_SEED, "e3_blocks": list(E3_BLOCKS), "e3_paths": E3_PATHS,
                      "fresh_seed": stages.FRESH_SEED, "fresh_blocks": list(stages.E3_BLOCKS),
                      "e2_share": E2_SHARE, "pure_years": list(PURE_YEARS),
                      "control_main_cagr_base": ctrl_rec["main"]["base"]["m2"]["cagr"],
                      "control_main_cagr_calibrated": ctrl_rec["main"]["calibrated"]["m2"]["cagr"],
                      "control_main_max_dd_base": ctrl_rec["main"]["base"]["m2"]["max_dd"],
                      "control_main_max_dd_calibrated": ctrl_rec["main"]["calibrated"]["m2"]["max_dd"],
                      "control_AT2000": ctrl_rec["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"],
                      "control_AT2010": ctrl_rec["after_tax"]["2010_01_to_end"]["after_tax_cagr"],
                      "control_AT2017": ctrl_rec["after_tax"]["2017_01_to_end"]["after_tax_cagr"]}}
    for name in STUDY_ARMS:
        print(f"  {name}", flush=True)
        res = gather(amap[name], ctrl_vec, ctrl_arm=amap[CONTROL], quiet=quiet)
        r, g = res["record"], gate_block(res["record"], ctrl_rec, res["e3"], ctrl_e3)
        out["arms"][name] = {
            "spec": amap[name]["spec"],
            "cells": {
                "cagr_base": r["main"]["base"]["m2"]["cagr"],
                "cagr_calibrated": r["main"]["calibrated"]["m2"]["cagr"],
                "max_dd_base": r["main"]["base"]["m2"]["max_dd"],
                "max_dd_calibrated": r["main"]["calibrated"]["m2"]["max_dd"],
                "delta_cagr_base_pp": (r["main"]["base"]["m2"]["cagr"]
                                       - ctrl_rec["main"]["base"]["m2"]["cagr"]) * 100,
                "delta_cagr_calibrated_pp": (r["main"]["calibrated"]["m2"]["cagr"]
                                             - ctrl_rec["main"]["calibrated"]["m2"]["cagr"]) * 100,
                "delta_max_dd_base_pp": (r["main"]["base"]["m2"]["max_dd"]
                                         - ctrl_rec["main"]["base"]["m2"]["max_dd"]) * 100,
                "delta_max_dd_calibrated_pp": (r["main"]["calibrated"]["m2"]["max_dd"]
                                               - ctrl_rec["main"]["calibrated"]["m2"]["max_dd"]) * 100,
                "turnover_ratio_base": r["main"]["base"]["m2"]["turnover_annual"]
                                       / ctrl_rec["main"]["base"]["m2"]["turnover_annual"],
                "turnover_ratio_calibrated": r["main"]["calibrated"]["m2"]["turnover_annual"]
                                             / ctrl_rec["main"]["calibrated"]["m2"]["turnover_annual"],
                "boot_pre_base_median_pp": r["boot"]["pre"]["base"]["median_delta_pp"],
                "boot_pre_cal_median_pp": r["boot"]["pre"]["calibrated"]["median_delta_pp"],
                "B1_median_pp": r["boot"]["tax"]["progressive"]["median_delta_pp"],
                "B1_share": r["boot"]["tax"]["progressive"]["share_gt_0"],
                "B2_median_pp": r["boot"]["tax"]["flat"]["median_delta_pp"],
                "B2_share": r["boot"]["tax"]["flat"]["share_gt_0"],
                "oos_median_delta_pp": (float(np.median([
                    r["oos"][key]["cagr"] - ctrl_rec["oos"][key]["cagr"]
                    for key in stages.applicable_oos(r)])) * 100
                    if stages.applicable_oos(r) else None),
                "oos_applicable": stages.applicable_oos(r),
                "oos_pooled_median_pp": r["oos_pooled"].get("median_delta_pp"),
                "AT2000_delta_pp": g["AT"]["windows"]["2000_03_to_2026_09"],
                "AT2010_delta_pp": g["AT"]["windows"]["2010_01_to_end"],
                "AT2017_delta_pp": g["AT"]["windows"]["2017_01_to_end"],
                "E2_median_pp": g["E2"]["rolling"]["median_delta_pp"],
                "E2_share_gt_0": g["E2"]["rolling"]["share_gt_0"],
                "E2p_median_pp": g["E2p"]["rolling"]["median_delta_pp"],
                "E2p_share_gt_0": g["E2p"]["rolling"]["share_gt_0"],
                "E4_median_pp": g["E4"]["median_delta_pp"],
                "E4_share_gt_0": g["E4"]["share_gt_0"],
                "sells_per_year": r["after_tax"]["2000_03_to_2026_09"]["sells_per_year"],
            },
            "gates": g,
        }
        out["e3"][name] = {f"{blk}_{md}": B.delta_stats(res["e3"][blk][md] - ctrl_e3[blk][md])
                           for blk in res["e3"] for md in B.TAX_MODES}

    # ---- E1 + E5
    days, panel, i0, i1 = windows.main_panel(0.0)
    labels = native.session_labels(panel)
    vint = rt_state.vintage_series()
    live_d, live_v = rt_state.live_series()
    live_state = rt_state.weekly_state(live_d, live_v)
    cal, rt = rt_state.load()
    s_rt = native.asof(cal, rt, labels)
    s_lv = native.asof(live_d, live_state, labels)
    differ = np.abs(s_rt - s_lv) > 0.5
    first_vintage = min(vint)
    j0, j1 = fe_study.window_indices(days, *TAX_WINDOWS["2017_01_to_end"])
    out["e1"] = {"vintages": len(vint), "first_vintage": first_vintage, "last_vintage": max(vint),
                 "sessions": int(s_rt.size), "sessions_state_differs": int(differ.sum()),
                 "share_differs": float(differ.mean()),
                 "sessions_before_first_vintage": int((np.asarray(days) < first_vintage).sum()),
                 "pure_window": "2017_01_to_end lies entirely inside the vintage era",
                 "pure_rolling_years": list(PURE_YEARS)}
    out["e5"] = {"main_window": {"window": [days[i0], days[i1]],
                                 "live": state_profile(days, s_lv, i0, i1),
                                 "rt": state_profile(days, s_rt, i0, i1)},
                 "shortfall_2017": {}}
    for name in NEW_ARMS:
        out["e5"]["shortfall_2017"][name] = shortfall_decomposition(amap[name], days, j0, j1, s_rt,
                                                                    amap[CONTROL])
        out["arms"][name]["state_profile"] = state_profile(days, s_rt, i0, i1)

    new_pass = {n: out["arms"][n]["gates"]["pass"] for n in NEW_ARMS}
    out["verdict"] = ("pass" if all(new_pass.values())
                      else ("partial" if any(new_pass.values()) else "fail"))
    out["new_arm_pass"] = new_pass
    out["wall_s"] = round(time.time() - t0, 1)
    return out, ctrl_res


def write_outputs(res, checks, harness_ok, wall_s):
    RESULTS.write_text(json.dumps(rnd({k: v for k, v in res.items() if k != "wall_s"}),
                                  indent=1, sort_keys=True) + "\n")
    a = res["arms"]
    lines = [
        f"ic12_confirm_v1 | confirmation of IC12 (NFCI veto) on a real-time (ALFRED vintage) "
        f"signal | control {CONTROL} | harness {'PASS' if harness_ok else 'FAIL'}"
        f" ({sum(1 for c in checks if c['pass'])}/{len(checks)})",
        f"Verdict: {res['verdict'].upper()}  (IC12rt/A pass={res['new_arm_pass']['IC12rt/A']}, "
        f"IC12rt/B pass={res['new_arm_pass']['IC12rt/B']}) | E1 usable: {res['e1']['vintages']} "
        f"vintages {res['e1']['first_vintage']} .. {res['e1']['last_vintage']}, the state differs "
        f"from the committed one on {res['e1']['sessions_state_differs']}/{res['e1']['sessions']} "
        f"main-window sessions",
        f"wall clock of --run: {wall_s} s",
        "",
        "arm          | preCAGR base/cal | maxDD base/cal | dCAGR base/cal | turn/K | "
        "B1 med/sh      | B2 med/sh      | OOSmed | pooled | AT2000/2010/2017 d | E2 med/sh  | "
        "E2p med/sh | E4 med/sh  | E3 (10/20/40 x prog/flat med/share)",
    ]
    for n in STUDY_ARMS:
        c = a[n]["cells"]
        e3 = res["e3"][n]
        e3s = " ".join(f"{k[5:7][-2:]}{'p' if k.endswith('progressive') else 'f'}:"
                       f"{v['median_delta_pp']:+.2f}/{v['share_gt_0']:.2f}"
                       for k, v in sorted(e3.items()))
        lines.append(
            f"{n:<12} | {c['cagr_base']*100:6.2f}/{c['cagr_calibrated']*100:6.2f}  | "
            f"{c['max_dd_base']*100:5.2f}/{c['max_dd_calibrated']*100:5.2f} | "
            f"{c['delta_cagr_base_pp']:+6.2f}/{c['delta_cagr_calibrated_pp']:+6.2f} | "
            f"{c['turnover_ratio_base']:6.3f} | {c['B1_median_pp']:+6.2f}/{c['B1_share']:.3f} | "
            f"{c['B2_median_pp']:+6.2f}/{c['B2_share']:.3f} | "
            f"{c['oos_median_delta_pp']:+6.2f} | {c['oos_pooled_median_pp']:+6.2f} | "
            f"{c['AT2000_delta_pp']:+6.2f}/{c['AT2010_delta_pp']:+6.2f}/{c['AT2017_delta_pp']:+6.2f} | "
            f"{c['E2_median_pp']:+6.2f}/{c['E2_share_gt_0']:.2f} | "
            f"{c['E2p_median_pp']:+6.2f}/{c['E2p_share_gt_0']:.2f} | "
            f"{c['E4_median_pp']:+6.2f}/{c['E4_share_gt_0']:.2f} | {e3s}")
    lines.append("")
    for n in STUDY_ARMS:
        g = a[n]["gates"]
        lines.append(f"{n}: pass={g['pass']} failed={g['failed']}")
    lines.append("")
    for n in STUDY_ARMS:
        c = a[n]["cells"]
        lines.append(f"{n}: sells/yr {c['sells_per_year']:.2f}; OOS windows "
                     f"{','.join(c['oos_applicable'])}")
    lines.append("")
    for n in NEW_ARMS:
        ep = res["e5"]["shortfall_2017"][n]
        lines.append(f"2017-window decomposition {n}: total dlogNAV "
                     f"{ep['total_delta_log_nav_pp']:+.2f} pp over {ep['n_episodes']} episodes | "
                     "per year " + " ".join(f"{k}:{v:+.2f}" for k, v in sorted(ep["per_year"].items())))
        for e in ep["worst_episodes"][:3]:
            lines.append(f"    worst episode {e['start']}..{e['end']} ({e['sessions']} sessions)"
                         f" {e['delta_log_nav_pp']:+.2f} pp")
    for key in ("live", "rt"):
        p = res["e5"]["main_window"][key]
        lines.append(f"veto incidence on the main window ({key} state): "
                     f"{p['share_on_of_defined']*100:.1f}% of defined sessions, "
                     f"{p['n_episodes']} episodes, mean {p['episode_sessions_mean']:.1f} / "
                     f"median {p['episode_sessions_median']:.0f} / max {p['episode_sessions_max']} "
                     f"sessions")
    SUMMARY.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--save-state", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.save_state:
        rt_state.write_state_file()
        print(f"wrote {rt_state.STATE_FILE.relative_to(ROOT)}")
        return 0
    if args.harness:
        checks, ok = harness()
        HARNESS.write_text(json.dumps(rnd(checks), indent=1, sort_keys=True) + "\n")
        return 0 if ok else 1
    if not args.run:
        ap.print_help()
        return 2
    t0 = time.time()
    res, _ctrl = run(quiet=args.quiet)
    wall = time.time() - t0
    checks = json.loads(HARNESS.read_text()) if HARNESS.exists() else []
    harness_ok = bool(checks) and all(c["pass"] for c in checks)
    if args.out:
        Path(args.out).write_text(json.dumps(rnd({k: v for k, v in res.items() if k != "wall_s"}),
                                             indent=1, sort_keys=True) + "\n")
        print(f"wrote {args.out} ({wall:.1f}s)")
        return 0
    write_outputs(res, checks, harness_ok, round(wall, 1))
    print(f"wrote RESULTS.json + SUMMARY.txt ({wall:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
