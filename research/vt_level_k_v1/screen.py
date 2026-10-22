#!/usr/bin/env python3
"""vt_level_k_v1 screen (see PREREGISTRATION.md). Reads data/historical only (+ the report-only
Yahoo October extension). Production K is the control; the only changed parameter is `vt`.

  K    production K, vt = 0.35 (control; asserted against research/band_h2_combo_v1/RESULTS.json)
  V30  K with vt = 0.30
  V25  K with vt = 0.25
  R2   production R2 (band off, no deferral, vt = 0.35) -- reference row only

Stages: pre (base/calibrated x m = 1/2/4), AT (three windows, five capitals), B12 (clean bootstrap
seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, $100k, B1/B2), OOS (six windows) +
G8 pooled bootstrap, E2 rolling January starts, E5 (m = 4, decisions), PROF (drawdown profile of
the pre-tax and after-tax main path), EPI (October 2026, report only).

Gates: ewma_band_v1's G1-G9 with C0 -> K and G9 <= 2 x K, after tax at $100k in all three windows,
and the clean bootstrap B1/B2 (median >= 0, share > 0 >= 55 %). Selection: an arm qualifies only if
every gate passes; otherwise the study's output is the measured trade-off.

Modes
  --smoke    synthetic prices only: vt plumbing, monotone mean weight, distinct NAV paths,
             after-tax <= pre-tax, band/deferral fire.
  --harness  engine parity (`python -m research.fast_engine.parity --quick` must PASS), K anchors,
             the deep K/R2 reproduction against band_h2_combo_v1/RESULTS.json, and a 400/200-path
             local-bootstrap <-> fe_boot.boot bitwise check at vt = 0.35.
  --run      harness, then every stage, the gates and the profile tables; writes RESULTS.json,
             SUMMARY.txt.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_config import (
    CAP,
    E2_START_YEARS,
    EXP_Q,
    EXTRA_FIN,
    FIN,
    GATE_CAPITAL,
    M,
    M_STRESS,
    OOS_WINDOWS,
    SEED,
    BLOCK,
    START,
    TAX_CAPITALS,
    TAX_WINDOWS,
    VOL_TARGET,
)
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE
from research.vt_level_k_v1 import targets as tg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
REF = ROOT / "research/band_h2_combo_v1/RESULTS.json"
PARITY = ROOT / "research/fast_engine/PARITY.json"
EPI_QQQ = ROOT / "data/historical/vt_level_k_v1_qqq_2026q4.csv"
EPI_QLD = ROOT / "data/historical/vt_level_k_v1_qld_2026q4.csv"

ARMS = ("K", "V30", "V25", "R2")
VTS = {"K": 0.35, "V30": 0.30, "V25": 0.25, "R2": 0.35}
CANDIDATES = ("V30", "V25")
CONTROL = "K"
BASELINE = "R2"
RULE_ARM = {"K": "K", "V30": "K", "V25": "K", "R2": "R2"}   # fe_sim.simulate label
RULE_KEY = {"K": "K", "V30": "K", "V25": "K", "R2": "C0"}   # fe_sim.RULE key
TOL = 1e-4          # 0.01 pp for the harness anchors
DEEP_TOL = 2e-6     # RESULTS.json stores round(x, 6)
OOS_PATHS_PER_MARKET = 2_000
OOS_BOOT_L = 253 + 2520
OOS_CHUNK = 250
SUBPERIODS = fe_study.SUBPERIODS


# ----------------------------------------------------------------------------- simulate wrapper
def sim(o, c, w, env, capital, i0, i1, arm, mode="pre_tax", m=M, trace=False):
    """`fe_sim.simulate` with this study's arm labels mapped to a fill rule."""
    return fe_sim.simulate(o, c, w, env, capital, i0, i1, arm=RULE_ARM[arm], mode=mode, m=m, trace=trace)


def calmar(cagr, dd):
    return float(cagr / dd) if dd and dd > 1e-12 else None


# ----------------------------------------------------------------------------- stages
def pre_tax_main(arms=ARMS):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD row."""
    days, o0, c0, e2, wants0, _e2w = tg.main_inputs_vt(0.0, VTS)
    T = len(days) - 1
    fx, ufe = fe_study.try_market(days)
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    env_usd = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, T)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, e_, wants_, _e2w_ = tg.main_inputs_vt(spread, VTS)
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = sim(o_, c_, wants_[arm], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = fe_study.pre_stats(d_, START, T, r)
        for arm in arms:
            r = sim(o_, c_, wants_[arm], env_usd, TAX_CAPITALS[GATE_CAPITAL], START, T, arm, "pre_tax", m=M)
            pre_usd[md][arm] = fe_study.pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {
        "start": days[START],
        "end": days[-1],
        "sessions": T - START + 1,
        "years": float(fe_study.calendar_years(days[START - 1], days[-1])),
    }
    return pre, pre_usd


def after_tax_windows(capitals, arms=ARMS):
    """After tax at the given capitals (progressive, liquidated, base financing, m = 2)."""
    days, o, c, e2, wants, _e2w = tg.main_inputs_vt(0.0, VTS)
    fx, ufe = fe_study.try_market(days)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            i0, i1 = fe_study.window_indices(days, a, b)
            env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
            win[cap_label][wn] = {}
            for arm in arms:
                rq = sim(o, c, wants[arm], env, TAX_CAPITALS[cap_label], i0, i1, arm, "progressive")
                rp = sim(o, c, wants[arm], env, 1.0, i0, i1, arm, "pre_tax")
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "terminal_usd_unliquidated": rq["terminal_usd_unliquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "postponements": rq["postponements"],
                    "blocked_band": rq["blocked_band"],
                    "deferred": rq["deferred"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "deferrals_by_year": rq["deferrals_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                }
    return win


def cost_stress(arms=ARMS):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, e2, wants, _e2w = tg.main_inputs_vt(0.0, VTS)
    fx, ufe = fe_study.try_market(days)
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        out[wn] = {}
        for arm in arms:
            rq = sim(o, c, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm, "progressive", m=M_STRESS)
            rp = sim(o, c, wants[arm], env, 1.0, i0, i1, arm, "pre_tax", m=M_STRESS)
            out[wn][arm] = {
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "turnover_annual": rp["turnover_annual"],
                "postponements": rq["postponements"],
            }
    for wn in out:
        for arm in arms:
            out[wn][arm]["delta_after_tax_pp"] = (out[wn][arm]["after_tax_cagr"] - out[wn][CONTROL]["after_tax_cagr"]) * 100
    return out


def oos_window(days, o, c, y, start, arms=ARMS):
    """One OOS window: own E2 + own early path, own 2x leg (calibrated, m = 2), R-USD rule."""
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    wants = tg.arm_targets(c, e2, fe_signals.vol_n(sc)[0], VTS, comps)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = sim(so, sc, wants[arm], env, 1.0, start, len(days) - 1, arm, "pre_tax", m=M)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    for arm in arms:
        if arm == CONTROL:
            continue
        res[arm]["delta_cagr_pp"] = (res[arm]["cagr"] - res[CONTROL]["cagr"]) * 100
        res[arm]["delta_max_dd_pp"] = (res[arm]["max_dd"] - res[CONTROL]["max_dd"]) * 100
    return res


def oos_stages(arms=ARMS):
    out = {}
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    out["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, arms)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = fe_data.load_market(key)
        out[key] = oos_window(md_, mo_, mc_, my_, mstart_, arms)
    return out


def oos_boot_market(key, rng, arms=ARMS):
    """`boot_oos_market`: block-20 paths inside one market, pre-tax, calibrated leg, R-USD."""
    days, o, c, y, start = fe_data.load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, OOS_BOOT_L)
    L = OOS_BOOT_L
    acc = {a: [] for a in arms}
    cost = fe_sim.COST_PER_SIDE * M
    mode_ids = np.array([MODE_PRE_TAX], dtype=np.int64)
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    for chunk in range(0, OOS_PATHS_PER_MARKET, OOS_CHUNK):
        P = min(OOS_CHUNK, OOS_PATHS_PER_MARKET - chunk)
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
        wants = tg.arm_targets(cc, e2, fe_signals.vol_n(sc_c)[0], VTS, comps)
        for arm in arms:
            spec = fe_sim.RULE[RULE_KEY[arm]]
            out_pre = np.empty((1, 1, P), dtype=np.float64)
            out_at = np.empty((1, 1, 1, P), dtype=np.float64)
            fe_boot._boot_chunk(
                so_c, sc_c, wants[arm], so_c, sc_c, wants[arm], wants[CONTROL],
                env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
                env.pay_ptr, env.pay_yr, env.pay_part,
                1.0, np.array([1.0]), START, L, cost,
                fe_sim.calendar_years(days[START - 1], days[L]), env.Y0, env.M_lo,
                np.array([spec["band"]], dtype=np.bool_),
                np.array([spec["usd_rule"]], dtype=np.bool_),
                np.array([spec["defer"]], dtype=np.bool_),
                mode_ids, 0, P, 0, False, out_pre, out_at,
                np.empty((1, P), dtype=np.float64), dbg0,
            )
            acc[arm].append(out_pre[0, 0, :].copy())
    return {a: np.concatenate(v) for a, v in acc.items()}


def oos_bootstrap(arms=ARMS):
    """Pooled OOS bootstrap (G8): M1-M5 in order on one generator, 2,000 paths each."""
    rng = np.random.default_rng(SEED)
    per = {k: oos_boot_market(k, rng, arms) for k in ("M1", "M2", "M3", "M4", "M5")}
    pooled = {a: np.concatenate([per[k][a] for k in per]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in per:
        out["per_market"][k] = {
            a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)} for a in arms
        }
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {
            "diff_vs_K_median_pp": float(np.median(d) * 100),
            "diff_vs_K_mean_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)),
            "paths": int(len(d)),
        }
    return out


def stage_e2(arms=ARMS):
    """Rolling January starts 2001..2021, after tax at $100k (report only)."""
    days, o, c, e2, wants, _e2w = tg.main_inputs_vt(0.0, VTS)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        env = fe_sim.RunEnv(days, fx, ufe, i0, T)
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = sim(o, c, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], i0, T, arm, "progressive")
            rp = sim(o, c, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], i0, T, arm, "pre_tax")
            rec[arm] = {
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "terminal_usd": rq["terminal_usd_liquidated"],
                "tax_usd": rq["tax_total_usd_at_payment_fx"],
                "years": float(rq["years"]),
                "sells_per_year": rq["sells"] / rq["years"],
            }
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


# ----------------------------------------------------------------------------- drawdown profile
def episodes(nav):
    """Maximal underwater intervals (see PREREGISTRATION.md section 7)."""
    nav = np.asarray(nav, dtype=float)
    out = []
    peak_val = trough = nav[0]
    peak_i = trough_i = 0
    for t in range(1, len(nav)):
        if nav[t] >= peak_val:
            if trough < peak_val:
                out.append(
                    {
                        "peak_index": int(peak_i),
                        "trough_index": int(trough_i),
                        "recover_index": int(t),
                        "dd": float(1.0 - trough / peak_val),
                        "days": int(t - peak_i),
                        "censored": False,
                    }
                )
            peak_val = trough = nav[t]
            peak_i = trough_i = t
        elif nav[t] < trough:
            trough = nav[t]
            trough_i = t
    if trough < peak_val:
        out.append(
            {
                "peak_index": int(peak_i),
                "trough_index": int(trough_i),
                "recover_index": None,
                "dd": float(1.0 - trough / peak_val),
                "days": None,
                "censored": True,
            }
        )
    return out


def profile(nav):
    nav = np.asarray(nav, dtype=float)
    eps = episodes(nav)
    thr = {}
    for level in (0.10, 0.15, 0.20, 0.30, 0.40):
        sel = [e for e in eps if e["dd"] > level]
        rec = [e["days"] for e in sel if not e["censored"]]
        thr[f"{int(level * 100)}"] = {
            "count": len(sel),
            "censored": sum(1 for e in sel if e["censored"]),
            "median_recovery_days": float(np.median(rec)) if rec else None,
            "max_recovery_days": int(max(rec)) if rec else None,
        }
    ret = nav[1:] / nav[:-1] - 1
    def worst(h):
        if len(nav) <= h:
            return None
        return float(np.min(nav[h:] / nav[:-h] - 1.0))
    return {
        "sessions": int(len(nav)),
        "max_dd": float(fe_sim.max_dd(nav)[0]),
        "annual_vol": float(np.std(ret, ddof=1) * math.sqrt(252)),
        "worst_21_session": worst(21),
        "worst_63_session": worst(63),
        "episodes": thr,
        "n_episodes_all": len(eps),
        "max_episode_dd": float(max(e["dd"] for e in eps)) if eps else 0.0,
    }


def profiles():
    days, o, c, e2, wants, _e2w = tg.main_inputs_vt(0.0, VTS)
    T = len(days) - 1
    fx, ufe = fe_study.try_market(days)
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    out = {"pre_tax_base_m2": {}, "after_tax_100k": {}}
    for arm in ARMS:
        rp = sim(o, c, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm, "pre_tax")
        rq = sim(o, c, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm, "progressive")
        out["pre_tax_base_m2"][arm] = profile(rp["_nav"])
        out["after_tax_100k"][arm] = profile(rq["_nav"])
        out["after_tax_100k"][arm]["max_dd_check"] = float(rq["max_dd_nav_usd"])
    out["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1}
    return out


# ----------------------------------------------------------------------------- October 2026 episode
def _ffill_rates(days):
    tb = {}
    with fe_config.DTB3_CSV.open() as fh:
        for r in __import__("csv").DictReader(fh):
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
    """Committed QQQ bars plus the Yahoo extension (dates after the committed end)."""
    import csv
    from collections import defaultdict

    raw = defaultdict(dict)
    with fe_config.BARS.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "QQQ":
                raw[r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    extra = {}
    with EPI_QQQ.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "QQQ" and r["date"] not in raw:
                extra[r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    raw.update(extra)
    days = sorted(raw)
    arr = np.array([raw[d] for d in days], dtype=float)
    return days, arr[:, 0].copy(), arr[:, 1].copy(), arr[:, 2].copy()


def episode_2026():
    days, oq, cq, dv = load_extended_qqq()
    y = _ffill_rates(days)
    so, sc = fe_signals.synthetic_2x(oq, cq, dv, y, EXP_Q)
    e2 = fe_signals.e2_up(cq)[0]
    comps = fe_signals.early_components(cq)
    wants = tg.arm_targets(cq, e2, fe_signals.vol_n(sc)[0], VTS, comps)
    i0 = bisect.bisect_left(days, "2024-01-02")
    i_ep = bisect.bisect_left(days, "2026-10-06")
    i_ref = bisect.bisect_left(days, "2026-10-05")
    if i_ep >= len(days):
        return {"covered": False, "reason": f"committed + downloaded bars end {days[-1]}"}
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), i0, len(days) - 1)
    out = {
        "covered": True,
        "data_end": days[-1],
        "episode_start": days[i_ep],
        "reference_date": days[i_ref],
        "sessions_in_episode": int(len(days) - i_ep),
        "arms": {},
        "context": {},
    }

    def measure(nav, label, off=0):
        """nav[0] is session `off`; an absolute day index j maps to nav[j - off]."""
        j_ref, j_ep = i_ref - off, i_ep - off
        base = nav[j_ref]
        seg = nav[j_ep:]
        return {
            "label": label,
            "return_from_reference": float(seg[-1] / base - 1.0),
            "max_dd_from_prior_peak": float(1.0 - np.min(seg) / np.max(nav[:j_ep])),
            "max_dd_inside_episode": float(1.0 - np.min(seg) / np.max(seg)),
            "peak_date_inside": days[i_ep + int(np.argmax(seg))],
            "trough_date_inside": days[i_ep + int(np.argmin(seg))],
        }

    for arm in ARMS:
        r = sim(so, sc, wants[arm], env, TAX_CAPITALS[GATE_CAPITAL], i0, len(days) - 1, arm, "pre_tax")
        out["arms"][arm] = measure(r["_nav"], arm, i0 - 1)
    out["context"]["QQQ"] = measure(cq, "QQQ")
    qld = {}
    import csv

    with EPI_QLD.open() as fh:
        for r in csv.DictReader(fh):
            qld[r["date"]] = float(r["close"])
    qd = sorted(qld)
    qn = np.array([qld[d] for d in qd])
    j_ref = bisect.bisect_left(qd, days[i_ref])
    j_ep = bisect.bisect_left(qd, days[i_ep])
    out["context"]["QLD"] = {
        "label": "QLD (real, buy-and-hold)",
        "return_from_reference": float(qn[-1] / qn[j_ref] - 1.0),
        "max_dd_from_prior_peak": float(1.0 - np.min(qn[j_ep:]) / np.max(qn[:j_ep])),
        "max_dd_inside_episode": float(1.0 - np.min(qn[j_ep:]) / np.max(qn[j_ep:])),
    }
    return out


# ----------------------------------------------------------------------------- harness helpers
def _leaves(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_leaves(v, f"{prefix}{k}."))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_leaves(v, f"{prefix}{i}."))
    elif isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return {}
    elif isinstance(obj, (int, float, np.integer, np.floating)):
        out[prefix[:-1]] = float(obj)
    return out


def _compare(name, mine, ref, tol=DEEP_TOL):
    a, b = _leaves(mine), _leaves(ref)
    cells, bad, worst = 0, [], 0.0
    for k, v in a.items():
        if k not in b:
            continue
        cells += 1
        d = abs(v - b[k])
        worst = max(worst, d)
        if d > tol:
            bad.append((k, v, b[k], d))
    return {"section": name, "cells": cells, "mismatches": len(bad), "max_abs_diff": worst,
            "pass": not bad, "detail": bad[:10]}


def run_parity():
    """`python -m research.fast_engine.parity --quick` must PASS; restore the generated report."""
    before = PARITY.read_bytes() if PARITY.exists() else None
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "research.fast_engine.parity", "--quick"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    wall = time.time() - t0
    if before is not None:
        PARITY.write_bytes(before)
    ok = proc.returncode == 0
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-3:]
    return {"command": "python -m research.fast_engine.parity --quick", "returncode": proc.returncode,
            "pass": ok, "wall_s": wall, "tail": tail}


def harness(deep_bootstrap=True):
    """Engine parity, K anchors, deep K/R2 reproduction, vt = 0.35 bitwise identity."""
    checks = []
    par = run_parity()
    checks.append({"check": "engine_parity_quick", "pass": par["pass"], "detail": par})

    days, o, c, w_e2_ref, w_r2_ref, e2 = fe_data.main_inputs(0.0)
    _d, _o, _c, _e2, wants, w_e2 = tg.main_inputs_vt(0.0, VTS)
    ok_w = bool(np.array_equal(wants["K"].ravel(), w_r2_ref.ravel())) and bool(
        np.array_equal(wants["R2"].ravel(), w_r2_ref.ravel())
    )
    ok_e2 = bool(np.array_equal(w_e2.ravel(), w_e2_ref.ravel()))
    checks.append({"check": "local_targets_bitwise_vs_fe_signals_at_0.35", "pass": ok_w and ok_e2,
                   "detail": {"r2_equal": ok_w, "e2_equal": ok_e2}})

    pre, pre_usd = pre_tax_main()
    anchors = []

    def chk(label, got, ref, tol=TOL):
        d = abs(got - ref)
        anchors.append({"cell": label, "got": float(got), "reference": float(ref),
                        "diff": float(d), "pass": bool(d <= tol)})

    chk("K.pre.base.m2.cagr", pre["base"]["m2"]["K"]["cagr"], 0.1576486)
    chk("K.pre.cal.m2.cagr", pre["calibrated"]["m2"]["K"]["cagr"], 0.1517848)
    chk("K.pre.base.m2.dd", pre["base"]["m2"]["K"]["max_dd"], 0.4666983)
    chk("K.pre.cal.m2.dd", pre["calibrated"]["m2"]["K"]["max_dd"], 0.4679481)
    chk("K.pre.base.m1.cagr", pre["base"]["m1"]["K"]["cagr"], 0.1601729)
    chk("K.pre.base.m2.turnover", pre["base"]["m2"]["K"]["turnover_annual"], 5.456136)
    chk("R2.pre.base.m2.cagr", pre["base"]["m2"]["R2"]["cagr"], 0.1547660)
    chk("R2.pre.base.m2.dd", pre["base"]["m2"]["R2"]["max_dd"], 0.4675840)
    chk("R2.pre.cal.m2.cagr", pre["calibrated"]["m2"]["R2"]["cagr"], 0.1489613)
    at = after_tax_windows((GATE_CAPITAL,))
    ref_at = {"2000_03_to_2026_09": 0.107533, "2010_01_to_end": 0.152354, "2017_01_to_end": 0.239720}
    for wn, v in ref_at.items():
        chk(f"K.AT.100k.{wn}", at[GATE_CAPITAL][wn]["K"]["after_tax_cagr"], v)
    chk("R2.AT.100k.2000", at[GATE_CAPITAL]["2000_03_to_2026_09"]["R2"]["after_tax_cagr"], 0.101600)
    chk("R2.AT.100k.2010", at[GATE_CAPITAL]["2010_01_to_end"]["R2"]["after_tax_cagr"], 0.147059)
    chk("R2.AT.100k.2017", at[GATE_CAPITAL]["2017_01_to_end"]["R2"]["after_tax_cagr"], 0.228861)
    e2w = sized = tg.sized_wants(e2, np.ones((1, len(days))), fe_signals.vol_n(c)[0], 0.35)
    r_e2 = sim(o, c, e2w, fe_sim.RunEnv(days, *fe_study.try_market(days), START, len(days) - 1),
               TAX_CAPITALS[GATE_CAPITAL], START, len(days) - 1, "R2", "pre_tax")
    chk("E2.pre.cagr", r_e2["cagr_usd_liquidated"], 0.146749)
    chk("E2.pre.dd", r_e2["max_dd_nav_usd"], 0.495643)
    checks.append({"check": "K_and_reference_anchors", "pass": all(a["pass"] for a in anchors),
                   "detail": {"cells": anchors, "worst": max(a["diff"] for a in anchors)}})

    ref = json.loads(REF.read_text())
    deep = []
    mine_pre = {"base": pre["base"], "calibrated": pre["calibrated"]}
    ref_pre = {"base": ref["pre_tax_main"]["base"], "calibrated": ref["pre_tax_main"]["calibrated"]}
    for md in ("base", "calibrated"):
        for mkey in ("m1", "m2", "m4"):
            my = {"K": mine_pre[md][mkey]["K"], "R2": mine_pre[md][mkey]["R2"]}
            rf = {"K": ref_pre[md][mkey]["K"], "R2": ref_pre[md][mkey]["C0"]}
            deep.append(_compare(f"pre.{md}.{mkey}", my, rf))
    at5 = after_tax_windows(("10k", "30k", "100k", "300k", "1M"))
    for cap in at5:
        for wn in at5[cap]:
            my = {"K": at5[cap][wn]["K"], "R2": at5[cap][wn]["R2"]}
            rf = {"K": ref["after_tax_windows"][cap][wn]["K"], "R2": ref["after_tax_windows"][cap][wn]["C0"]}
            deep.append(_compare(f"AT.{cap}.{wn}", my, rf))
    oos = oos_stages()
    for wn in OOS_WINDOWS:
        my = {"K": oos[wn]["K"], "R2": oos[wn]["R2"]}
        rf = {"K": ref["oos"][wn]["K"], "R2": ref["oos"][wn]["C0"]}
        deep.append(_compare(f"oos.{wn}", my, rf))
    cs = cost_stress()
    for wn in cs:
        my = {"K": cs[wn]["K"], "R2": cs[wn]["R2"]}
        rf = {"K": ref["cost_stress_m4"][wn]["K"], "R2": ref["cost_stress_m4"][wn]["C0"]}
        deep.append(_compare(f"cost_m4.{wn}", my, rf))
    deep_ok = all(d["pass"] for d in deep)
    cells = sum(d["cells"] for d in deep)
    mism = sum(d["mismatches"] for d in deep)
    checks.append({"check": "deep_reference_K_and_R2", "pass": deep_ok,
                   "detail": {"cells": cells, "mismatches": mism,
                              "worst": max(d["max_abs_diff"] for d in deep),
                              "failed": [d["section"] for d in deep if not d["pass"]]}})

    if deep_bootstrap:
        win = fe_boot.BootWindow()
        b_mine = tg.boot(("K", "R2"), VTS, SEED, BLOCK, 400, 200, ("100k",), ("progressive", "flat"),
                         window=win, quiet=True)
        b_ref = fe_boot.boot(seed=SEED, block=BLOCK, arms=("C0", "K"), n_pre=400, n_tax=200,
                             capitals=("100k",), modes=("progressive", "flat"),
                             deltas=("K",), baselines=("C0",), window=win, quiet=True)
        same_pre = all(
            b_mine["pre"][md]["K"]["cagr_median"] == b_ref["pre"][md]["K"]["cagr_median"]
            for md in ("base", "calibrated")
        )
        same_at = all(
            b_mine["tax"]["100k"][mode]["K"]["cagr_median"] == b_ref["tax"]["100k"][mode]["K"]["cagr_median"]
            for mode in ("progressive", "flat")
        )
        same_anchor = all(
            b_mine["anchor_e2_100k"][mode]["cagr_median"] == b_ref["anchor_e2_100k"][mode]["cagr_median"]
            for mode in ("progressive", "flat")
        )
        checks.append({"check": "local_boot_bitwise_vs_fe_boot_at_0.35", "pass": same_pre and same_at and same_anchor,
                       "detail": {"pre_equal": same_pre, "tax_equal": same_at, "anchor_equal": same_anchor}})

        clean = tg.boot(("K", "R2"), VTS, SEED, BLOCK, 5000, 1000, ("100k",),
                        ("progressive", "flat"), quiet=True)
        bb = []
        for arm, refv in (("K", (0.038577, 0.040376)), ("R2", (0.035155, 0.039678))):
            for i, mode in enumerate(("progressive", "flat")):
                got = clean["tax"]["100k"][mode][arm]["cagr_median"]
                bb.append({"cell": f"{arm}.{mode}", "got": float(got), "reference": refv[i],
                           "diff": float(abs(got - refv[i])), "pass": bool(abs(got - refv[i]) <= TOL)})
        for md, refv in (("base", 0.077761), ("calibrated", 0.073137)):
            got = clean["pre"][md]["K"]["cagr_median"]
            bb.append({"cell": f"pre.{md}.K", "got": float(got), "reference": refv,
                       "diff": float(abs(got - refv)), "pass": bool(abs(got - refv) <= TOL)})
        checks.append({"check": "clean_bootstrap_medians", "pass": all(x["pass"] for x in bb),
                       "detail": {"cells": bb, "worst": max(x["diff"] for x in bb)}})
    out = {"pass": all(c["pass"] for c in checks), "checks": checks}
    return out


# ----------------------------------------------------------------------------- smoke
def smoke():
    rng = np.random.default_rng(7)
    n = 1600
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    from datetime import date, timedelta

    days, d = [], date(2005, 1, 3)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    y = np.full(n, 0.01)
    so, sc = fe_signals.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = fe_signals.e2_up(price)[0]
    comps = fe_signals.early_components(price)
    vol = fe_signals.vol_n(sc)[0]
    wants = tg.arm_targets(price, e2, vol, VTS, comps)
    checks = {}

    ref = fe_signals.sized_wants(*fe_signals.r2_state(price, e2, comps), vol)
    checks["sized_wants_0.35_bitwise"] = bool(np.array_equal(tg.sized_wants(
        *fe_signals.r2_state(price, e2, comps), vol, 0.35), ref))
    mean_w = {a: float(np.mean(w)) for a, w in wants.items()}
    checks["mean_weight_monotone"] = (
        mean_w["K"] > mean_w["V30"] > mean_w["V25"] and mean_w["K"] > mean_w["R2"] * 0.99
    )
    checks["arms_differ"] = all(
        not np.array_equal(wants[a], wants[b]) for a, b in (("K", "V30"), ("V30", "V25"), ("K", "V25"))
    )
    ufe = {f"{yy}-{mm:02d}": float(1000 + 12 * yy + 5 * mm)
           for yy in range(1995, 2030) for mm in range(1, 13)}
    fx = np.full(n, 30.0)
    env = fe_sim.RunEnv(days, fx, ufe, START, n - 1)
    navs = {}
    finite = True
    for arm in ARMS:
        rp = sim(so, sc, wants[arm], env, 100_000.0, START, n - 1, arm, "pre_tax")
        rq = sim(so, sc, wants[arm], env, 100_000.0, START, n - 1, arm, "progressive")
        navs[arm] = rp["_nav"]
        finite &= bool(np.isfinite(rp["_nav"]).all() and np.isfinite(rq["_nav"]).all())
        finite &= bool(rq["cagr_usd_liquidated"] <= rp["cagr_usd_liquidated"] + 1e-12)
    checks["finite_and_after_tax_le_pre_tax"] = finite
    checks["nav_paths_differ"] = all(
        not np.array_equal(navs[a], navs[b]) for a, b in (("K", "V30"), ("V30", "V25"), ("K", "V25"))
    )
    rk = sim(so, sc, wants["K"], env, 100_000.0, START, n - 1, "K", "progressive")
    checks["band_and_deferral_fire"] = bool(rk["blocked_band"] > 0 and rk["deferred"] > 0)
    checks["pass"] = all(checks.values())
    return {"pass": bool(checks["pass"]), "checks": checks, "mean_weight": mean_w}


# ----------------------------------------------------------------------------- gates / selection
def gates(pre, boot, at, oos, oos_boot, prof):
    out = {}
    for arm in CANDIDATES:
        g = {}
        # G1 / G6 (G1 at m = 1)
        def g1(mkey):
            oks = {}
            for md in ("base", "calibrated"):
                a, k = pre[md][mkey][arm], pre[md][mkey][CONTROL]
                direct = a["cagr"] >= k["cagr"]
                cal = (a["calmar"] is not None and k["calmar"] is not None
                       and a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)
                oks[md] = bool(direct or cal)
            return oks
        g1m2 = g1("m2")
        g1m1 = g1("m1")
        g["G1"] = {"pass": all(g1m2.values()), "detail": g1m2,
                   "delta_cagr_pp": {md: (pre[md]["m2"][arm]["cagr"] - pre[md]["m2"][CONTROL]["cagr"]) * 100
                                     for md in ("base", "calibrated")},
                   "calmar_ratio": {md: (pre[md]["m2"][arm]["calmar"] / pre[md]["m2"][CONTROL]["calmar"])
                                    for md in ("base", "calibrated")}}
        g["G2"] = {"pass": all(pre[md]["m2"][arm]["max_dd"] <= pre[md]["m2"][CONTROL]["max_dd"] + 0.01
                               for md in ("base", "calibrated")),
                   "delta_dd_pp": {md: (pre[md]["m2"][arm]["max_dd"] - pre[md]["m2"][CONTROL]["max_dd"]) * 100
                                   for md in ("base", "calibrated")}}
        g["G3"] = {"pass": all(pre[md]["m2"][arm]["2017_end"]["cagr"] >= pre[md]["m2"][CONTROL]["2017_end"]["cagr"] - 0.01
                               for md in ("base", "calibrated")),
                   "delta_2017_pp": {md: (pre[md]["m2"][arm]["2017_end"]["cagr"]
                                          - pre[md]["m2"][CONTROL]["2017_end"]["cagr"]) * 100
                                     for md in ("base", "calibrated")}}
        n_above = sum(1 for p in SUBPERIODS
                      if pre["base"]["m2"][arm]["subperiods"][p[0]]["cagr"]
                      > pre["base"]["m2"][CONTROL]["subperiods"][p[0]]["cagr"])
        g["G4"] = {"pass": n_above >= 2, "n_above": n_above,
                   "delta_pp": {p[0]: (pre["base"]["m2"][arm]["subperiods"][p[0]]["cagr"]
                                       - pre["base"]["m2"][CONTROL]["subperiods"][p[0]]["cagr"]) * 100
                                for p in SUBPERIODS}}
        g5 = {md: boot["pre"][md][arm]["diff_vs_K_median_pp"] for md in ("base", "calibrated")}
        g["G5"] = {"pass": all(v >= 0 for v in g5.values()), "median_delta_pp": g5,
                   "share_gt_0": {md: boot["pre"][md][arm]["share_gt_0"] for md in ("base", "calibrated")}}
        g["G6"] = {"pass": all(g1m1.values()), "detail": g1m1}
        d_oos = {w: oos[w][arm]["delta_cagr_pp"] for w in OOS_WINDOWS}
        ddd = {w: oos[w][arm]["delta_max_dd_pp"] for w in OOS_WINDOWS}
        n_ok = sum(1 for w in OOS_WINDOWS if d_oos[w] >= -0.5 and ddd[w] <= 3.0)
        g["G7"] = {"pass": bool(np.median(list(d_oos.values())) >= 0 and n_ok >= 5
                                and max(ddd.values()) <= 8.0),
                   "median_delta_pp": float(np.median(list(d_oos.values()))),
                   "n_windows_ok": n_ok, "max_delta_dd_pp": float(max(ddd.values())),
                   "delta_cagr_pp": d_oos, "delta_dd_pp": ddd}
        g["G8"] = {"pass": oos_boot["pooled"][arm]["diff_vs_K_median_pp"] >= 0,
                   "median_delta_pp": oos_boot["pooled"][arm]["diff_vs_K_median_pp"],
                   "share_gt_0": oos_boot["pooled"][arm]["share_gt_0"]}
        ratio = pre["base"]["m2"][arm]["turnover_annual"] / pre["base"]["m2"][CONTROL]["turnover_annual"]
        g["G9"] = {"pass": ratio <= 2.0, "turnover_ratio": ratio}
        atd = {wn: (at[GATE_CAPITAL][wn][arm]["after_tax_cagr"] - at[GATE_CAPITAL][wn][CONTROL]["after_tax_cagr"]) * 100
               for wn in TAX_WINDOWS}
        g["AT"] = {"pass": all(v >= 0 for v in atd.values()), "delta_pp": atd}
        bt = {}
        for mode in ("progressive", "flat"):
            d = boot["tax"][GATE_CAPITAL][mode][f"{arm}_vs_K"]
            bt[mode] = {"median_delta_pp": d["median_delta_cagr_pp"], "share_gt_0": d["share_gt_0"],
                        "pass": bool(d["median_delta_cagr_pp"] >= 0 and d["share_gt_0"] >= 0.55)}
        g["BOOT"] = {"pass": all(v["pass"] for v in bt.values()), "detail": bt}
        g["all"] = all(v["pass"] for k, v in g.items() if k != "detail" and isinstance(v, dict))
        g["failed"] = [k for k, v in g.items() if isinstance(v, dict) and "pass" in v and not v["pass"]]
        out[arm] = g
    out["selected"] = [a for a in CANDIDATES if out[a]["all"]]
    out["control"] = CONTROL
    out["rule"] = ("an arm qualifies only if every gate passes; if none does, no arm is selected and "
                   "the study reports the measured trade-off")
    return out


def cost_per_point(pre, at, prof):
    out = {}
    K_at = at[GATE_CAPITAL]["2000_03_to_2026_09"][CONTROL]["after_tax_cagr"]
    K_dd = prof["after_tax_100k"][CONTROL]["max_dd"]
    K_pre = pre["base"]["m2"][CONTROL]["cagr"]
    K_pdd = pre["base"]["m2"][CONTROL]["max_dd"]
    for arm in CANDIDATES:
        a_at = at[GATE_CAPITAL]["2000_03_to_2026_09"][arm]["after_tax_cagr"]
        a_dd = prof["after_tax_100k"][arm]["max_dd"]
        a_pre = pre["base"]["m2"][arm]["cagr"]
        a_pdd = pre["base"]["m2"][arm]["max_dd"]
        d_at = (K_at - a_at) * 100
        d_dd = (K_dd - a_dd) * 100
        d_pre = (K_pre - a_pre) * 100
        d_pdd = (K_pdd - a_pdd) * 100
        out[arm] = {
            "after_tax": {
                "cagr_given_up_pp": d_at,
                "max_dd_removed_pp": d_dd,
                "pp_per_pp": (d_at / d_dd) if d_dd > 0 else None,
            },
            "pre_tax": {
                "cagr_given_up_pp": d_pre,
                "max_dd_removed_pp": d_pdd,
                "pp_per_pp": (d_pre / d_pdd) if d_pdd > 0 else None,
            },
        }
    return out


# ----------------------------------------------------------------------------- summary
def write_summary(res):
    lines = []
    p = res["params"]
    lines.append("vt_level_k_v1 -- lowering production K's vol target (measurement study)")
    lines.append(f"window {p['main_window']['start']} -> {p['main_window']['end']} "
                 f"({p['main_window']['sessions']} sessions), m = {M}, base financing unless stated")
    lines.append("")
    lines.append("PRE-TAX MAIN (m = 2)")
    lines.append(f"{'arm':5s} {'vt':>4s} {'CAGR base':>10s} {'CAGR cal':>9s} {'DD base':>8s} "
                 f"{'DD cal':>7s} {'Calmar b':>9s} {'m1':>8s} {'turn':>6s} {'sells/yr':>8s}")
    pre = res["pre_tax_main"]
    for a in ARMS:
        b, c = pre["base"]["m2"][a], pre["calibrated"]["m2"][a]
        lines.append(f"{a:5s} {VTS[a]:4.2f} {b['cagr']*100:9.4f}% {c['cagr']*100:8.4f}% "
                     f"{b['max_dd']*100:7.3f}% {c['max_dd']*100:6.3f}% {b['calmar']:9.4f} "
                     f"{pre['base']['m1'][a]['cagr']*100:7.3f}% {b['turnover_annual']:6.3f} "
                     f"{b['sells_per_year']:8.2f}")
    lines.append("")
    lines.append("AFTER TAX at $100k (progressive, liquidated)")
    lines.append(f"{'arm':5s} " + " ".join(f"{w:>18s}" for w in TAX_WINDOWS))
    for a in ARMS:
        cells = []
        for w in TAX_WINDOWS:
            v = res["after_tax_windows"]["100k"][w][a]
            cells.append(f"{v['after_tax_cagr']*100:8.4f}% d{v['after_tax_cagr']*100 - res['after_tax_windows']['100k'][w]['K']['after_tax_cagr']*100:+6.3f}")
        lines.append(f"{a:5s} " + " ".join(f"{c:>18s}" for c in cells))
    lines.append("")
    lines.append("CLEAN BOOTSTRAP $100k, seed 20261011, block 20 (5,000 pre / 1,000 tax)")
    for mode in ("progressive", "flat"):
        lines.append(f"  {mode}:")
        for a in ARMS:
            d = res["bootstrap_clean_seed20261011"]["tax"]["100k"][mode].get(f"{a}_vs_K")
            med = res["bootstrap_clean_seed20261011"]["tax"]["100k"][mode][a]["cagr_median"] * 100
            lines.append(f"    {a:5s} median {med:7.4f}%"
                         + (f"   d vs K {d['median_delta_cagr_pp']:+7.4f} pp  share>0 {d['share_gt_0']*100:5.1f}%"
                            if d else ""))
    lines.append("  pre-tax legs: " + "  ".join(
        f"{a} {res['bootstrap_clean_seed20261011']['pre']['base'][a]['cagr_median']*100:.4f}% / "
        f"{res['bootstrap_clean_seed20261011']['pre']['calibrated'][a]['cagr_median']*100:.4f}%"
        for a in ARMS))
    lines.append("")
    lines.append("DRAWDOWN PROFILE (main window)")
    for label, key in (("pre-tax base m2", "pre_tax_base_m2"), ("after tax $100k", "after_tax_100k")):
        lines.append(f"  {label}:")
        lines.append(f"    {'arm':5s} {'maxDD':>7s} {'vol':>6s} {'w21':>8s} {'w63':>8s} "
                     + " ".join(f"> {t}%".rjust(13) for t in ("10", "15", "20", "30", "40")))
        for a in ARMS:
            v = res["profiles"][key][a]
            eps = " ".join(
                f"{v['episodes'][t]['count']:2d}/{str(v['episodes'][t]['median_recovery_days']):>4s}"
                f"/{str(v['episodes'][t]['max_recovery_days']):>4s}"
                for t in ("10", "15", "20", "30", "40")
            )
            lines.append(f"    {a:5s} {v['max_dd']*100:6.3f}% {v['annual_vol']*100:5.2f}% "
                         f"{v['worst_21_session']*100:7.2f}% {v['worst_63_session']*100:7.2f}% {eps}")
    lines.append("    (episode cells = count/median recovery days/max recovery days; censored excluded)")
    lines.append("")
    lines.append("COST PER POINT OF DRAWDOWN REMOVED (main window, $100k)")
    for a in CANDIDATES:
        c = res["cost_per_point"][a]
        at, pt = c["after_tax"], c["pre_tax"]
        lines.append(f"  {a}: after tax {at['cagr_given_up_pp']:+6.3f} pp CAGR for "
                     f"{at['max_dd_removed_pp']:+5.2f} pp max DD removed -> "
                     f"{at['pp_per_pp']:+.3f} pp/pp" if at["pp_per_pp"] is not None else f"  {a}: n/a")
        lines.append(f"       pre-tax {pt['cagr_given_up_pp']:+6.3f} pp CAGR for "
                     f"{pt['max_dd_removed_pp']:+5.2f} pp max DD removed -> "
                     f"{pt['pp_per_pp']:+.3f} pp/pp" if pt["pp_per_pp"] is not None else f"       pre-tax n/a")
    lines.append("")
    lines.append("OCTOBER 2026 EPISODE (report only; downloaded extension, pre-tax)")
    epi = res["episode_2026"]
    if epi.get("covered"):
        lines.append(f"  data end {epi['data_end']}; episode {epi['episode_start']} -> end; "
                     f"reference {epi['reference_date']}")
        lines.append(f"  {'arm':10s} {'return':>9s} {'DD prior peak':>14s} {'DD inside':>10s}")
        for a in list(ARMS) + ["QQQ", "QLD"]:
            v = epi["arms"].get(a) or epi["context"].get(a)
            lines.append(f"  {a:10s} {v['return_from_reference']*100:8.2f}% "
                         f"{v['max_dd_from_prior_peak']*100:13.2f}% {v['max_dd_inside_episode']*100:9.2f}%")
    else:
        lines.append("  not covered by the data: " + epi.get("reason", ""))
    lines.append("")
    lines.append("GATES (vs K)")
    for a in CANDIDATES:
        g = res["gates"][a]
        lines.append(f"  {a}: " + " ".join(
            f"{k}={'PASS' if g[k]['pass'] else 'FAIL'}"
            for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "AT", "BOOT")))
        lines.append(f"        failed: {', '.join(g['failed']) if g['failed'] else 'none'}")
    lines.append(f"  selected: {res['gates']['selected'] or 'none'}")
    lines.append("")
    lines.append("HARNESS")
    for c in res["harness"]["checks"]:
        lines.append(f"  {c['check']:55s} {'PASS' if c['pass'] else 'FAIL'}")
    lines.append(f"  runtime {res['wall_clock_s']:.1f} s")
    lines.append("")
    lines.append("Not tax advice. Production K is unchanged; no Stage B replay was run.")
    SUMMARY.write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------------------- run
def run():
    t0 = time.time()
    hz = harness(deep_bootstrap=True)
    if not hz["pass"]:
        print("HARNESS FAIL -- refusing to run stages")
        for c in hz["checks"]:
            print(" ", c["check"], c["pass"], json.dumps(c["detail"])[:400])
        return 1

    pre, pre_usd = pre_tax_main()
    at = after_tax_windows(("10k", "30k", "100k", "300k", "1M"))
    clean = tg.boot(ARMS, VTS, SEED, BLOCK, 5000, 1000, ("100k",), ("progressive", "flat"), quiet=True)
    oos = oos_stages()
    oosb = oos_bootstrap()
    e2 = stage_e2()
    cs = cost_stress()
    prof = profiles()
    cpp = cost_per_point(pre, at, prof)
    epi = episode_2026()
    g = gates(pre, clean, at, oos, oosb, prof)

    res = {
        "campaign": "vt_level_k_v1",
        "status": "complete",
        "params": {
            "control": CONTROL,
            "baseline_reference": BASELINE,
            "arms": list(ARMS),
            "vts": VTS,
            "candidates": list(CANDIDATES),
            "main_window": pre["window"],
            "cost_per_side": fe_sim.COST_PER_SIDE,
            "m": M,
            "m_stress": M_STRESS,
            "financing": FIN,
            "cap": CAP,
            "vol_target": VOL_TARGET,
            "rule": {a: RULE_ARM[a] for a in ARMS},
            "gates": res_gate_text(),
        },
        "harness": hz,
        "pre_tax_main": pre,
        "pre_tax_main_r_usd": pre_usd,
        "after_tax_windows": at,
        "cost_stress_m4": cs,
        "bootstrap_clean_seed20261011": clean,
        "oos": oos,
        "oos_bootstrap": oosb,
        "E2_rolling_starts": e2,
        "profiles": prof,
        "cost_per_point": cpp,
        "episode_2026": epi,
        "gates": g,
        "smoke": smoke(),
        "wall_clock_s": time.time() - t0,
    }
    OUT.write_text(json.dumps(res, indent=1, default=float) + "\n")
    write_summary(res)
    print(f"wrote {OUT.name} and {SUMMARY.name} in {res['wall_clock_s']:.1f} s")
    for a in CANDIDATES:
        print(f"  {a}: " + " ".join(f"{k}={'P' if g[a][k]['pass'] else 'F'}"
                                   for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "AT", "BOOT")))
    print(f"  selected: {g['selected'] or 'none'}")
    return 0


def res_gate_text():
    return {
        "G1": "CAGR >= K, or Calmar >= 1.1 x K with CAGR >= K - 0.5 pp (base and calibrated)",
        "G2": "max DD <= K + 1.0 pp (base and calibrated)",
        "G3": "2017+ CAGR >= K - 1.0 pp (base and calibrated)",
        "G4": "CAGR above K in >= 2 of 3 sub-periods",
        "G5": "block-20 pre-tax bootstrap median (arm - K) >= 0 (base and calibrated)",
        "G6": "G1 at m = 1 (base and calibrated)",
        "G7": "six OOS windows: median dCAGR >= 0; >= 5/6 with dCAGR >= -0.5 pp and dDD <= +3 pp; dDD never > +8 pp",
        "G8": "pooled OOS bootstrap (M1-M5, 2,000 paths each): median dCAGR >= 0",
        "G9": "turnover <= 2 x K",
        "AT": "after tax at $100k: arm - K >= 0 in all three windows",
        "BOOT": "clean bootstrap B1/B2 at $100k: median d >= 0 and share > 0 >= 55 %",
        "select": "an arm qualifies only if every gate passes; else none and the trade-off is reported",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.smoke:
        s = smoke()
        print(json.dumps(s, indent=1))
        print("smoke", "PASS" if s["pass"] else "FAIL")
        return 0 if s["pass"] else 1
    if a.harness:
        h = harness()
        for c in h["checks"]:
            print(f"  {c['check']:55s} {'PASS' if c['pass'] else 'FAIL'}")
            if not c["pass"]:
                print("   ", json.dumps(c["detail"])[:1200])
        print("harness", "PASS" if h["pass"] else "FAIL")
        return 0 if h["pass"] else 1
    if a.run:
        return run()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
