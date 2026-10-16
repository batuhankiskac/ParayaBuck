"""ic37c_confirm_v1.screen -- the confirmation study of IC37-C.

    python -m research.ic37c_confirm_v1.data --fetch          # once: FRED/ALFRED/Yahoo inputs
    python -m research.ic37c_confirm_v1.screen --e1           # E1: proxy vs real IEF
    python -m research.ic37c_confirm_v1.screen --harness      # before any arm (parity + anchors)
    python -m research.ic37c_confirm_v1.screen --run          # RESULTS.json + SUMMARY.txt

The plan is `PREREGISTRATION.md`, committed before any non-control arm was simulated. Every
simulation, after-tax accounting and bootstrap goes through `research.fast_engine` /
`research/fast_engine2`; the arms are built with `research.batch_screen.api`, driven by
`research.batch_screen.stages` / `.boot` and `research.batch_screen.run`'s control record -- all
unmodified. No per-day or per-path Python loop here; this module only assembles arms, calls the
framework's stages and aggregates.

The IC37 plugin is imported **unchanged**; §1 of the pre-registration explains that the study
extends the *series* its keys read (`proxy.use`), not its code.
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

from research.batch_screen import api, boot as B, run as runner, stages, windows
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

from . import data as D, proxy

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RESULTDIR = HERE / "results"
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
HARNESS = HERE / "HARNESS.json"
E1JSON = HERE / "E1.json"

CONTROL = "k_control/K"
REFERENCE = ("IC37/A", "IC37/B")
CANDIDATE = "IC37/C"
ARM_IDS = (CANDIDATE,) + REFERENCE

#: this study's E4 draws (PREREGISTRATION section 4.3): a seed no earlier trial used.
E4_SEED = 20261018
E4_BLOCKS = (10, 20, 40)
E4_PATHS = 1000
E4_SHARE = 0.55
E2_SHARE = 0.60
E1_CORR, E1_TE, E1_CAGR = 0.95, 0.025, 0.02

STAGE1_KEYS = ("at2000_gt_0", "b1_median_gt_0", "maxdd", "turnover")
PRETAX_KEYS = ("G1_base", "G1_calibrated", "G2_base", "G2_calibrated", "G3_base", "G3_calibrated",
               "G4", "G5_base", "G5_calibrated", "G6_base", "G6_calibrated", "G7", "G8", "G9")


# ----------------------------------------------------------------------------- serialisation
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


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rnd(obj), indent=1, sort_keys=True) + "\n")


# ----------------------------------------------------------------------------- arms
def arms():
    """The study's four arms, resolved through the framework (IC37 imported unchanged)."""
    out = {CONTROL: api.load_arm("k_control", "K")}
    for a in ("A", "B", "C"):
        out[f"IC37/{a}"] = api.load_arm("IC37", a)
    return out


def main_window():
    days, p, i0, i1 = windows.main_panel(0.0)
    return days, p, i0, i1


# ----------------------------------------------------------------------------- one arm
def gather(arm, cvec, ctrl_arm=None, quiet=True):
    """Everything the gates need, for one arm, on the extended (proxy) data.

    `cvec=None` means "this arm is its own reference" (the control). The pooled OOS bootstrap (G8)
    is drawn for the arm and the control **in one call** on the applicable market subset, so the Δ
    stays paired; the clean bootstrap B1/B2 and the E4 draws are deterministic functions of
    (seed, block, paths) and are therefore paired across separate calls by construction.
    """
    aid = arm["id"]
    rec = stages.stage1(arm, "none")
    b12 = stages.stage1_vectors(arm, "none", quiet=quiet, label=aid)
    rec["boot"], rec["boot_level"], vecs = {"pre": {}, "tax": {}}, {"pre": {}, "tax": {}}, {}
    for md in ("base", "calibrated"):
        v = b12["pre"][md][aid]
        base = v if cvec is None else np.asarray(cvec["pre"][md], dtype=np.float64)
        rec["boot"]["pre"][md] = B.delta_stats(v - base)
        rec["boot_level"]["pre"][md] = B.delta_stats(v)
        vecs[f"pre_{md}"] = v
    for md in B.TAX_MODES:
        v = b12["tax"][GATE_CAPITAL][md][aid]
        base = v if cvec is None else np.asarray(cvec["tax"][md], dtype=np.float64)
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
                                for md in B.TAX_MODES}} for blk in fresh}

    markets, na_markets = stages.oos_pooled_markets(arm, "none")
    if cvec is not None and "oos_pooled" in cvec:
        # the control's own pooled vector came from the same (seed, block, market list) draw
        ctrl_markets = cvec.get("oos_pooled_markets")
        if ctrl_markets is not None and list(ctrl_markets) != list(markets):
            markets = [k for k in markets if k in ctrl_markets]
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
    return {"record": rec, "vectors": vecs, "e4": e4_vectors(arm)}


def e4_vectors(arm):
    """E4 (PREREGISTRATION section 4.3): seed 20261018, blocks 10/20/40, 1,000 after-tax paths."""
    out = {}
    for blk in E4_BLOCKS:
        b = B.boot([arm], seed=E4_SEED, block=blk, n_pre=0, n_tax=E4_PATHS,
                   capitals=(GATE_CAPITAL,), modes=B.TAX_MODES, cash_yield="none",
                   label=f"E4 block {blk}", quiet=True)
        out[f"block{blk}"] = {md: b["tax"][GATE_CAPITAL][md][arm["id"]] for md in B.TAX_MODES}
    return out


# ----------------------------------------------------------------------------- gates
def rolling_cells(rec, ctrl, years=range(2001, 2022)):
    ds = np.asarray([rec["rolling"][str(y)]["after_tax_cagr"]
                     - ctrl["rolling"][str(y)]["after_tax_cagr"] for y in years], dtype=np.float64)
    return {"n_starts": int(ds.size), "median_delta_pp": float(np.median(ds) * 100),
            "mean_delta_pp": float(ds.mean() * 100), "share_gt_0": float(np.mean(ds > 0)),
            "share_ge_0": float(np.mean(ds >= 0)), "min_delta_pp": float(ds.min() * 100),
            "max_delta_pp": float(ds.max() * 100)}


def e2_gate(rec, ctrl):
    """E2: 21 January starts 2001-2021 -- median Δ **> 0** and share Δ > 0 ≥ 60 %."""
    r = rolling_cells(rec, ctrl)
    return {**r, "pass": bool(r["median_delta_pp"] > 0 and r["share_gt_0"] >= E2_SHARE)}


def e4_gate(e4, ctrl_e4):
    """E4: six fresh cells (blocks 10/20/40 x progressive/flat) median Δ ≥ 0 and share > 0 ≥ 55 %."""
    cells, ok = {}, True
    for blk in sorted(e4):
        for md in B.TAX_MODES:
            cell = B.delta_stats(e4[blk][md] - ctrl_e4[blk][md])
            good = bool(cell["median_delta_pp"] >= 0 and cell["share_gt_0"] >= E4_SHARE)
            cells[f"{blk}_{md}"] = {"median_delta_pp": cell["median_delta_pp"],
                                    "share_gt_0": cell["share_gt_0"], "pass": good}
            ok = ok and good
    return {"cells": cells, "pass": bool(ok)}


def gate_block(rec, ctrl, e4, ctrl_e4):
    """Every gate of PREREGISTRATION section 5, plus the verdict, for one arm."""
    s1_ok, s1 = stages.stage1_pass(rec, ctrl)
    g = stages.pre_tax_gates(rec, ctrl)
    at = stages.after_tax_gates(rec, ctrl)
    e2 = e2_gate(rec, ctrl)
    e4g = e4_gate(e4, ctrl_e4)
    etf = stages.e1_gate(rec, ctrl)
    fresh = stages.fresh_gate(rec, ctrl)
    avail = stages.applicability(rec)
    required = {"stage1": bool(s1_ok)}
    required.update({k: bool(g.get(k, False)) for k in PRETAX_KEYS})
    required["G7_evaluable"] = bool(g.get("G7", False))
    required.update({"AT": bool(at["pass"]),
                     "AT_B1_recent": bool(at["B1_recent"]["pass"]),
                     "AT_B2_flat": bool(at["B2_flat"]["pass"]),
                     "E2": bool(e2["pass"]), "E3_oos": bool(g.get("G7", False)),
                     "E4": bool(e4g["pass"])})
    return {
        "stage1": {"gates": s1, "pass": bool(s1_ok)},
        "pre_tax": {k: g[k] for k in PRETAX_KEYS + ("G4_wins", "G7_detail", "G8_median_pp")
                    if k in g},
        "AT": {"windows": {k: round(float(v["delta_pp"]), 6) for k, v in at["windows"].items()},
               "B1_recent": at["B1_recent"], "B2_flat": at["B2_flat"], "pass": at["pass"]},
        "E2": e2, "E3": {"n_applicable": len(stages.applicable_oos(rec)),
                         "applicable": stages.applicable_oos(rec),
                         "detail": g.get("G7_detail"), "pass": bool(g.get("G7", False))},
        "E4": e4g,
        "report_only": {"E1_etf": etf, "FRESH_20261016": fresh, "availability": avail},
        "required": required,
        "failed": sorted(k for k, v in required.items() if not v),
        "pass": bool(all(required.values())),
    }


# ----------------------------------------------------------------------------- E1: the proxy
def yearly_returns(dates, values):
    """Calendar-year total returns of a series as `{year: return}`."""
    yrs = np.array([int(d[:4]) for d in dates])
    out = {}
    for y in sorted(set(yrs.tolist())):
        i = np.flatnonzero(yrs == y)
        lo = i[0] - 1 if i[0] > 0 else i[0]
        out[y] = float(values[i[-1]] / values[lo] - 1)
    return out


def validate_proxy():
    """E1 (PREREGISTRATION section 2): the synthetic IEF proxy against real IEF."""
    pd_, pv = proxy.ief_proxy()
    rd_, rv = proxy.real_ief()
    pos = np.searchsorted(pd_, rd_)
    ok = pd_[np.clip(pos, 0, pd_.size - 1)] == rd_
    dts, rv, pv = rd_[ok], rv[ok], pv[pos[ok]]
    years = int(np.ceil((int(dts[-1][:4]) - int(dts[0][:4]))))
    rp, rr = np.diff(pv) / pv[:-1], np.diff(rv) / rv[:-1]
    corr = float(np.corrcoef(rp, rr)[0, 1])
    te = float(np.std(rp - rr) * np.sqrt(252.0))
    n = rp.size
    cagr_p = float((pv[-1] / pv[0]) ** (252.0 / n) - 1)
    cagr_r = float((rv[-1] / rv[0]) ** (252.0 / n) - 1)
    gap = cagr_p - cagr_r
    yp, yr = yearly_returns(dts, pv), yearly_returns(dts, rv)
    yearly = {str(y): {"proxy": yp[y], "real": yr[y], "gap_pp": (yp[y] - yr[y]) * 100}
              for y in sorted(yp)}
    dec = {}
    yri = np.array([int(d[:4]) for d in dts[1:]])
    for y in sorted(set(yri.tolist())):
        m = yri == y
        dec[str(y)] = {"corr": float(np.corrcoef(rp[m], rr[m])[0, 1]),
                       "te_pp": float(np.std(rp[m] - rr[m]) * np.sqrt(252.0) * 100),
                       "n": int(m.sum())}
    checks = {
        "corr_ge": {"value": corr, "threshold": E1_CORR, "pass": bool(corr >= E1_CORR)},
        "te_le": {"value": te, "threshold": E1_TE, "pass": bool(te <= E1_TE)},
        "cagr_gap_le": {"value": abs(gap), "threshold": E1_CAGR, "pass": bool(abs(gap) <= E1_CAGR)},
    }
    return {"window": [str(dts[0]), str(dts[-1])], "sessions": int(n), "years": years,
            "proxy_cagr": cagr_p, "real_cagr": cagr_r, "cagr_gap_pp": gap * 100,
            "daily_corr": corr, "tracking_error_pp": te * 100,
            "beta": float(np.polyfit(rr, rp, 1)[0]),
            "checks": checks, "yearly": yearly,
            "per_year": {k: {kk: vv for kk, vv in v.items() if kk != "n"} for k, v in dec.items()},
            "valid": bool(all(c["pass"] for c in checks.values()))}


def gate_profile():
    """The gate's own series (report only): how often `DGS10 - BE > 0.25` is true."""
    dc, dv = D.read_two_col("data/historical/ic37c_confirm_v1_dgs10.csv")
    bc, bv = proxy.be_proxy()
    pos = np.searchsorted(dc, bc)
    ok = dc[np.clip(pos, 0, dc.size - 1)] == bc
    real = dv[pos[ok]] - bv[ok]
    bcd = bc[ok]
    t10 = proxy.real_t10yie()
    yrs = np.array([int(d[:4]) for d in bcd])
    on = real > 0.25
    by_year = {}
    for y in sorted(set(yrs.tolist())):
        m = yrs == y
        by_year[str(y)] = float(on[m].mean())
    pre = yrs < 2003
    return {"real_yield_first": str(bcd[0]), "t10yie_first": str(t10[0][0]),
            "n_sessions": int(ok.sum()), "share_gate_on": float(on.mean()),
            "share_gate_on_pre_2003": float(on[pre].mean()) if pre.any() else None,
            "by_year": by_year}


# ----------------------------------------------------------------------------- E5 (report only)
def leg_leg_held(arm):
    """E5: how much of the main window the leg-B sleeve is held, by calendar year."""
    days, p, i0, i1 = main_window()
    t = api.call_targets(arm["mod"], p, arm)
    w = np.asarray(t.wants_b)[0]
    yrs = np.asarray([int(d[:4]) for d in days])
    by_year = {}
    for y in sorted(set(yrs[i0:i1 + 1].tolist())):
        m = (yrs == y)[i0:i1 + 1]
        by_year[str(y)] = {"share_held": float((w[i0:i1 + 1][m] > 0).mean()),
                           "mean_weight": float(w[i0:i1 + 1][m].mean())}
    ww = w[i0:i1 + 1]
    return {"share_held": float((ww > 0).mean()), "mean_weight": float(ww.mean()),
            "mean_weight_when_held": float(ww[ww > 0].mean()) if (ww > 0).any() else 0.0,
            "max_weight": float(ww.max()), "by_year": by_year}


def year_delta(arm, ctrl_arm):
    """E5: the arm-K Δ of log NAV on the main window, per calendar year (progressive, $100k)."""
    days, p, i0, i1 = main_window()
    env = windows.try_env(days, i0, i1, "none")
    kw = dict(mode="progressive", m=2, capital=TAX_CAPITALS[GATE_CAPITAL])
    a = stages.run_one(arm, p, env, i0, i1, **kw)
    k = stages.run_one(ctrl_arm, p, env, i0, i1, **kw)
    d = np.diff(np.log(np.asarray(a["_nav"]))) - np.diff(np.log(np.asarray(k["_nav"])))
    labels = list(np.asarray(days)[i0:i1 + 1])
    yrs = np.array([int(x[:4]) for x in labels])
    per_year = {str(y): float(d[yrs == y].sum() * 100) for y in sorted(set(yrs.tolist()))}
    m22 = yrs == 2022
    return {"total_dlog_nav_pp": float(d.sum() * 100), "per_year": per_year,
            "y2022_dlog_nav_pp": float(d[m22].sum() * 100),
            "legs": {"arm": a["_legs"], "control_turnover_annual": k["turnover_annual"]},
            "arm_turnover_annual": a["turnover_annual"]}


def real_vs_proxy_window(arm, days, i0, i1):
    """E5: the arm's after-tax CAGR under both data modes on one window (2002-08 ->)."""
    out = {}
    for mode in ("proxy", "real"):
        proxy.use(mode)
        p = windows.main_panel(0.0)[1]
        env = windows.try_env(days, i0, i1, "none")
        r = stages.run_one(arm, p, env, i0, i1, "progressive", 2, TAX_CAPITALS[GATE_CAPITAL])
        out[mode] = {"after_tax_cagr": float(r["cagr_usd_liquidated"]),
                     "sells_per_year": float(r["sells"] / r["years"]),
                     "turnover_annual": float(r["turnover_annual"])}
    proxy.use("proxy")
    out["window"] = [days[i0], days[i1]]
    out["delta_pp"] = (out["proxy"]["after_tax_cagr"] - out["real"]["after_tax_cagr"]) * 100
    return out


# ----------------------------------------------------------------------------- harness
def _anchor(check, got, ref, tol_pp=0.01):
    return {"check": check, "got": float(got), "reference": float(ref),
            "diff_pp": (float(got) - float(ref)) * 100,
            "pass": bool(abs(float(got) - float(ref)) <= tol_pp / 100.0 + 1e-12)}


def _git_clean(paths):
    p = subprocess.run(["git", "status", "--porcelain", "--"] + list(paths), cwd=str(ROOT),
                       capture_output=True, text=True)
    return {"check": "no_framework_drift", "pass": bool(not p.stdout.strip()),
            "dirty": p.stdout.strip().splitlines()}


def harness():
    """PREREGISTRATION section 7: parity, K's anchors, the bank_v3 reproduction, no drift."""
    checks = []
    t0 = time.time()
    proxy.use("proxy")
    print("  1/5 research.fast_engine.parity --quick", flush=True)
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": "."})
    checks.append({"check": "parity_quick", "pass": p.returncode == 0,
                   "tail": (p.stdout or "").strip().splitlines()[-1:]})

    print("  2/5 K's anchors through the engine", flush=True)
    k = api.load_arm("k_control", "K")
    rec_k = stages.stage1(k, "none")
    win_k = stages.stage2_main_windows(k, "none")
    for n, got, ref in (("K_pre_base_m2", rec_k["main"]["base"]["m2"]["cagr"], 0.157649),
                        ("K_pre_cal_m2", rec_k["main"]["calibrated"]["m2"]["cagr"], 0.151785),
                        ("K_dd_base_m2", rec_k["main"]["base"]["m2"]["max_dd"], 0.466698),
                        ("K_dd_cal_m2", rec_k["main"]["calibrated"]["m2"]["max_dd"], 0.467948),
                        ("K_at2000", rec_k["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"],
                         0.107533),
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

    print("  4/5 IC37/C reproduces its batch_bank_v3 record (real data)", flush=True)
    proxy.use("real")
    arm = api.load_arm("IC37", "C")
    win = windows.arm_window(arm)
    checks.append({"check": "IC37C_window_is_20030103",
                   "pass": bool(win.tag == "w20030103" and win.i0 == 960),
                   "tag": win.tag, "i0": int(win.i0)})
    ctrl = runner.control_record("none", stage2=False, quiet=True, win=win)
    crec, cvec = ctrl["record"], ctrl["vectors"]
    rec = stages.stage1(arm, "none", win=win)
    b12 = stages.stage1_vectors(arm, "none", quiet=True, label="IC37/C real", win=win)
    rec["after_tax"].update({k: v for k, v in
                             stages.stage2_main_windows(arm, "none", win=win).items()
                             if k not in rec["after_tax"]})
    for wn, ref in (("2000_03_to_2026_09", 0.587870), ("2010_01_to_end", 0.176424),
                    ("2017_01_to_end", 0.643077)):
        checks.append(_anchor(f"IC37C_AT_{wn}_delta",
                              (rec["after_tax"][wn]["after_tax_cagr"]
                               - crec["after_tax"][wn]["after_tax_cagr"]) * 100, ref))
    b1d = float(np.median(b12["tax"][GATE_CAPITAL]["progressive"]["IC37/C"]
                          - np.asarray(cvec["tax"]["progressive"]))) * 100
    b2d = float(np.median(b12["tax"][GATE_CAPITAL]["flat"]["IC37/C"]
                          - np.asarray(cvec["tax"]["flat"]))) * 100
    checks.append(_anchor("IC37C_B1_median", b1d, 12.229612))
    checks.append(_anchor("IC37C_B2_median", b2d, 14.510757))
    checks.append({"check": "IC37C_repro_sessions",
                   "pass": bool(int(rec["window"]["sessions"]) == 5972),
                   "sessions": int(rec["window"]["sessions"])})
    proxy.use("proxy")

    print("  5/5 no framework drift + the proxy registry", flush=True)
    checks.append(_git_clean(["research/batch_screen", "research/fast_engine",
                              "research/fast_engine2", "research/fast_engine_opts",
                              "research/ideas/IC37.py"]))
    km = proxy.use("proxy")
    checks.append({"check": "proxy_keys_point_at_the_derived_series",
                   "pass": bool(km["ief"] == proxy.IEF_PROXY and km["t10yie"] == proxy.BE_PROXY),
                   "keys": km})
    c = api.load_arm("IC37", "C")
    checks.append({"check": "resolve_arm_IC37C_unchanged",
                   "pass": bool(c["leg_b"] == "ief" and c["rule"] == "K"
                                and c["params"]["form"] == "residual"
                                and c["params"]["real_min_pp"] == 0.25
                                and c["params"]["start"] == "auto"
                                and list(c["params"]["inputs"]) == ["dgs10", "t10yie"]),
                   "spec": c["spec"]})
    ver = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "research/ideas/IC37.py"],
                         cwd=str(ROOT))
    checks.append({"check": "IC37_module_bytes_equal_HEAD",
                   "pass": bool(ver.returncode == 0),
                   "code_hash": api.code_hash("IC37")})
    v = D.verify()
    checks.append({"check": "data_manifest_verified", "pass": bool(v["ok"]), "bad": v["bad"]})
    bad = [c for c in checks if not c["pass"]]
    print(f"  harness {'PASS' if not bad else 'FAIL'} ({len(checks) - len(bad)}/{len(checks)} "
          f"checks, {time.time() - t0:.0f}s)", flush=True)
    for c in bad:
        print(f"    FAIL {c['check']}: {c}", flush=True)
    return checks, (not bad)


# ----------------------------------------------------------------------------- run
def run(quiet=True):
    t0 = time.time()
    proxy.use("proxy")
    e1 = validate_proxy()
    print(f"  E1 proxy: corr {e1['daily_corr']:.4f}  TE {e1['tracking_error_pp']:.2f}%  "
          f"CAGR gap {e1['cagr_gap_pp']:+.2f}pp  -> {'VALID' if e1['valid'] else 'INVALID'}",
          flush=True)
    dump(E1JSON, e1)
    amap = arms()

    print("  control (K)", flush=True)
    ctrl = runner.load_control(RESULTDIR, "none", require_stage2=True, quiet=quiet, win=None)
    crec, cvec = ctrl["record"], ctrl["vectors"]
    ctrl_e4 = e4_vectors(amap[CONTROL])
    cvec["oos_pooled_markets"] = (crec.get("oos_pooled") or {}).get("markets",
                                                                   list(stages.OOS_POOLED_MARKETS))

    out = {"study": "ic37c_confirm_v1", "control": CONTROL, "candidate": CANDIDATE,
           "arms": {}, "e1": e1, "e4": {}, "e5": {}, "gate_profile": gate_profile(),
           "params": {"seed": SEED, "block": BLOCK, "n_pre": PRE_BOOT_PATHS,
                      "n_tax": TAX_BOOT_PATHS, "capital": GATE_CAPITAL,
                      "e4_seed": E4_SEED, "e4_blocks": list(E4_BLOCKS), "e4_paths": E4_PATHS,
                      "fresh_seed": stages.FRESH_SEED, "fresh_blocks": list(stages.E3_BLOCKS),
                      "e2_share": E2_SHARE, "e4_share": E4_SHARE, "e1_thresholds":
                          {"corr": E1_CORR, "te": E1_TE, "cagr_gap": E1_CAGR},
                      "window": [crec["window"]["start"], crec["window"]["end"]],
                      "oos_applicable": stages.applicable_oos(crec),
                      "control_main_cagr_base": crec["main"]["base"]["m2"]["cagr"],
                      "control_main_cagr_calibrated": crec["main"]["calibrated"]["m2"]["cagr"],
                      "control_main_max_dd_base": crec["main"]["base"]["m2"]["max_dd"],
                      "control_main_max_dd_calibrated": crec["main"]["calibrated"]["m2"]["max_dd"],
                      "control_AT2000": crec["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"],
                      "control_AT2010": crec["after_tax"]["2010_01_to_end"]["after_tax_cagr"],
                      "control_AT2017": crec["after_tax"]["2017_01_to_end"]["after_tax_cagr"]}}

    for name in ARM_IDS:
        print(f"  {name}", flush=True)
        res = gather(amap[name], cvec, ctrl_arm=amap[CONTROL], quiet=quiet)
        r, gates = res["record"], gate_block(res["record"], crec, res["e4"], ctrl_e4)
        out["arms"][name] = {"spec": amap[name]["spec"], "cells": _cells(r, crec), "gates": gates,
                             "availability": stages.applicability(r)}
        out["e4"][name] = {f"{blk}_{md}": B.delta_stats(res["e4"][blk][md] - ctrl_e4[blk][md])
                           for blk in res["e4"] for md in B.TAX_MODES}

    # ---- E5, report only
    print("  E5 (report only)", flush=True)
    days, _p, i0, i1 = main_window()
    j0 = int(np.searchsorted(days, "2002-08-01"))
    out["e5"] = {"leg_b": {"IC37/C": leg_leg_held(amap["IC37/C"])},
                 "year_delta": {"IC37/C": year_delta(amap["IC37/C"], amap[CONTROL])},
                 "real_vs_proxy_2002_08": real_vs_proxy_window(amap["IC37/C"], days, j0, i1),
                 "e1_2022": {k: v for k, v in e1["yearly"].items() if k in ("2021", "2022", "2023")}}

    cand = out["arms"][CANDIDATE]["gates"]
    out["e1_valid"] = bool(e1["valid"])
    out["verdict"] = ("e1_invalid" if not e1["valid"]
                      else ("candidate_for_owner_decision" if cand["pass"] else "fail"))
    out["candidate_pass"] = bool(cand["pass"])
    out["wall_s"] = round(time.time() - t0, 1)
    return out, ctrl


def _cells(r, ctrl):
    at = stages.after_tax_gates(r, ctrl)
    atw = {k: float(v["delta_pp"]) for k, v in at["windows"].items()}
    roll = rolling_cells(r, ctrl)
    return {
        "cagr_base": r["main"]["base"]["m2"]["cagr"],
        "cagr_calibrated": r["main"]["calibrated"]["m2"]["cagr"],
        "max_dd_base": r["main"]["base"]["m2"]["max_dd"],
        "max_dd_calibrated": r["main"]["calibrated"]["m2"]["max_dd"],
        "cagr_m1_base": r["main"]["base"]["m1"]["cagr"],
        "delta_cagr_base_pp": (r["main"]["base"]["m2"]["cagr"]
                               - ctrl["main"]["base"]["m2"]["cagr"]) * 100,
        "delta_cagr_calibrated_pp": (r["main"]["calibrated"]["m2"]["cagr"]
                                     - ctrl["main"]["calibrated"]["m2"]["cagr"]) * 100,
        "delta_max_dd_base_pp": (r["main"]["base"]["m2"]["max_dd"]
                                 - ctrl["main"]["base"]["m2"]["max_dd"]) * 100,
        "delta_max_dd_calibrated_pp": (r["main"]["calibrated"]["m2"]["max_dd"]
                                       - ctrl["main"]["calibrated"]["m2"]["max_dd"]) * 100,
        "turnover_ratio_base": (r["main"]["base"]["m2"]["turnover_annual"]
                                / ctrl["main"]["base"]["m2"]["turnover_annual"]),
        "turnover_ratio_calibrated": (r["main"]["calibrated"]["m2"]["turnover_annual"]
                                      / ctrl["main"]["calibrated"]["m2"]["turnover_annual"]),
        "boot_pre_base_median_pp": r["boot"]["pre"]["base"]["median_delta_pp"],
        "boot_pre_cal_median_pp": r["boot"]["pre"]["calibrated"]["median_delta_pp"],
        "B1_median_pp": r["boot"]["tax"]["progressive"]["median_delta_pp"],
        "B1_share": r["boot"]["tax"]["progressive"]["share_gt_0"],
        "B2_median_pp": r["boot"]["tax"]["flat"]["median_delta_pp"],
        "B2_share": r["boot"]["tax"]["flat"]["share_gt_0"],
        "oos_median_delta_pp": (float(np.median([r["oos"][k]["cagr"] - ctrl["oos"][k]["cagr"]
                                                 for k in stages.applicable_oos(r)])) * 100
                                if stages.applicable_oos(r) else None),
        "oos_applicable": stages.applicable_oos(r),
        "oos_pooled_median_pp": r["oos_pooled"].get("median_delta_pp"),
        "AT2000_delta_pp": atw["2000_03_to_2026_09"],
        "AT2010_delta_pp": atw["2010_01_to_end"],
        "AT2017_delta_pp": atw["2017_01_to_end"],
        "rolling_n": roll["n_starts"],
        "E2_median_pp": roll["median_delta_pp"],
        "E2_share_gt_0": roll["share_gt_0"],
        "sells_per_year": r["after_tax"]["2000_03_to_2026_09"]["sells_per_year"],
        "blocked_band": r["after_tax"]["2000_03_to_2026_09"]["blocked_band"],
        "postponements": r["after_tax"]["2000_03_to_2026_09"]["postponements"],
    }


# ----------------------------------------------------------------------------- outputs
def write_outputs(res, checks, harness_ok, wall_s):
    RESULTS.write_text(json.dumps(rnd({k: v for k, v in res.items() if k != "wall_s"}),
                                  indent=1, sort_keys=True) + "\n")
    a = res["arms"]
    L = [
        f"ic37c_confirm_v1 | confirmation of IC37-C on an extended bond history | control "
        f"{CONTROL} | harness {'PASS' if harness_ok else 'FAIL'} "
        f"({sum(1 for c in checks if c['pass'])}/{len(checks)})",
        f"Verdict: {res['verdict'].upper()} | candidate pass={res['candidate_pass']} | "
        f"E1 valid={res['e1_valid']} | IC37-C was selected after seeing batch_bank_v3 "
        f"(N = 303 ledger trials), so a pass is at most a CANDIDATE FOR OWNER DECISION",
        f"window {res['params']['window'][0]} -> {res['params']['window'][1]}  (proxy mode: the "
        f"synthetic IEF series + the point-in-time breakeven proxy behind IC37's own keys)",
        f"E1 proxy vs real IEF {res['e1']['window'][0]}..{res['e1']['window'][1]}: corr "
        f"{res['e1']['daily_corr']:.4f} (>= {E1_CORR}), TE {res['e1']['tracking_error_pp']:.2f}% "
        f"(<= {E1_TE*100:.1f}%), CAGR gap {res['e1']['cagr_gap_pp']:+.2f}pp (|.| <= {E1_CAGR*100:.1f})",
        f"wall clock of --run: {wall_s} s",
        "",
        "arm      | preCAGR base/cal | dCAGR base/cal | maxDD base/cal | turn/K | "
        "B1 med/sh    | B2 med/sh    | OOSmed | pooled | AT2000/2010/2017 d | E2 med/sh | "
        "E4 6-cell med/sh",
    ]
    for n in ARM_IDS:
        c = a[n]["cells"]
        e4 = res["e4"][n]
        e4s = " ".join(f"{k[5:]}:{v['median_delta_pp']:+.2f}/{v['share_gt_0']:.2f}"
                       for k, v in sorted(e4.items()))
        L.append(
            f"{n:<8} | {c['cagr_base']*100:6.2f}/{c['cagr_calibrated']*100:6.2f}  | "
            f"{c['delta_cagr_base_pp']:+6.2f}/{c['delta_cagr_calibrated_pp']:+6.2f} | "
            f"{c['max_dd_base']*100:5.2f}/{c['max_dd_calibrated']*100:5.2f} | "
            f"{c['turnover_ratio_base']:6.3f} | {c['B1_median_pp']:+6.2f}/{c['B1_share']:.3f} | "
            f"{c['B2_median_pp']:+6.2f}/{c['B2_share']:.3f} | "
            f"{(c['oos_median_delta_pp'] or 0):+6.2f} | {(c['oos_pooled_median_pp'] or 0):+6.2f} | "
            f"{c['AT2000_delta_pp']:+6.2f}/{c['AT2010_delta_pp']:+6.2f}/{c['AT2017_delta_pp']:+6.2f}"
            f" | {a[n]['gates']['E2']['median_delta_pp']:+6.2f}/"
            f"{a[n]['gates']['E2']['share_gt_0']:.2f} | {e4s}")
    L.append("")
    for n in ARM_IDS:
        g = a[n]["gates"]
        L.append(f"{n}: pass={g['pass']} failed={g['failed']}")
    L.append("")
    cand = a[CANDIDATE]["cells"]
    e5 = res["e5"]
    lb = e5["leg_b"]["IC37/C"]
    L.append(f"E5 IC37/C: the IEF leg is held on {lb['share_held']*100:.1f}% of main-window "
             f"sessions (mean weight {lb['mean_weight']:.3f}, {lb['mean_weight_when_held']:.3f} "
             f"when held); sells/yr {cand['sells_per_year']:.2f} vs K "
             f"{'':>0}; turnover {cand['turnover_ratio_base']:.3f} x K")
    yd = e5["year_delta"]["IC37/C"]
    L.append(f"E5 IC37/C arm-K dlogNAV total {yd['total_dlog_nav_pp']:+.2f} pp; 2022 "
             f"{yd['y2022_dlog_nav_pp']:+.2f} pp; per year " +
             " ".join(f"{k}:{v:+.1f}" for k, v in sorted(yd["per_year"].items())))
    rvp = e5["real_vs_proxy_2002_08"]
    L.append(f"E5 real-vs-proxy on {rvp['window'][0]}..{rvp['window'][1]}: proxy after-tax CAGR "
             f"{rvp['proxy']['after_tax_cagr']*100:.2f}% vs real {rvp['real']['after_tax_cagr']*100:.2f}%"
             f" (proxy - real = {rvp['delta_pp']:+.2f} pp)")
    gp = res["gate_profile"]
    L.append(f"E5 gate (DGS10 - BE > 0.25) on {gp['share_gate_on']*100:.1f}% of sessions since "
             f"{gp['real_yield_first']}; {gp['share_gate_on_pre_2003']*100:.1f}% before T10YIE "
             f"existed ({gp['t10yie_first']})")
    SUMMARY.write_text("\n".join(L) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.ic37c_confirm_v1.screen")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--e1", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.e1:
        proxy.use("proxy")
        e1 = validate_proxy()
        dump(E1JSON, e1)
        print(json.dumps(rnd(e1["checks"]), indent=1))
        print(f"E1 {'VALID' if e1['valid'] else 'INVALID'} | corr {e1['daily_corr']:.4f} | "
              f"TE {e1['tracking_error_pp']:.2f}% | CAGR gap {e1['cagr_gap_pp']:+.2f}pp")
        return 0 if e1["valid"] else 1
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
    write_outputs(res, checks, harness_ok, round(wall, 1))
    print(f"wrote RESULTS.json + SUMMARY.txt ({wall:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
