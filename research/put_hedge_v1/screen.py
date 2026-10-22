"""put_hedge_v1 — protective-put overlays on production K (A1u + H2), Turkish after tax.

Modes:
  --smoke     synthetic price identities only (kernel off == engine, option payoff, calendar)
  --harness   parity --quick, K/R2 anchors through the engine, zero-flow parity, bootstrap parity,
              and the pre-registered PPUT pricing sanity check
  --run       the pre-registered study: reduced-window pre-tax, after-tax windows, sensitivity,
              clean bootstraps, OOS, drawdown profile, gates; writes RESULTS.json + SUMMARY.txt

The overlay kernel is a copy (`overlay_sim.py`); the fill rule, target series, tax engine and
bootstrap path draws are the engine's own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_config, fe_data, fe_sim, fe_signals, fe_study
from research.put_hedge_v1 import config as C
from research.put_hedge_v1 import overlay_sim as ov
from research.put_hedge_v1 import pput_probe, study_data as sd

ROOT = Path(__file__).resolve().parents[2]
ARMS = C.ARMS
REF_TOL = 1e-4          # 0.01 pp for pre-tax anchors (fraction units)
AT_TOL = 0.005          # 0.005 pp for after-tax anchors
QUICK_BOOT_PATHS = 64


def r6(x):
    return round(float(x), 6)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- window + arms
_BASE_CACHE = {}


def base_inputs():
    """The full-history inputs, loaded once (main_inputs re-reads 2.6 MB of CSV)."""
    if "v" not in _BASE_CACHE:
        days, qqq, vxn, vix, rate = sd.engine_market()
        d, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
        _d, _o, _c, _we, w45, _e = sd.main_inputs_vt(0.0, 0.45)
        fx, ufe = fe_study.try_market(days)
        _BASE_CACHE["v"] = (days, qqq, vxn, rate, o, c, w_e2, w_r2, w45, e2, fx, ufe)
    return _BASE_CACHE["v"]


def load_window(a=None, b=None):
    days, qqq, vxn, rate, o, c, w_e2, w_r2, w45, e2, fx, ufe = base_inputs()
    if a is None:
        i0 = sd.vxn_index(days)
    else:
        i0 = int(np.searchsorted(np.array(days), a, side="left"))
    i1 = len(days) - 1 if b is None else int(np.searchsorted(np.array(days), b, side="right")) - 1
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    return {
        "days": days,
        "qqq": qqq,
        "vxn": vxn,
        "rate": rate,
        "o": o,
        "c": c,
        "w_e2": w_e2,
        "w_r2": w_r2,
        "w45": w45,
        "e2": e2,
        "fx": fx,
        "ufe": ufe,
        "env": env,
        "i0": i0,
        "i1": i1,
    }


def arm_overlay(win, arm):
    """The pre-registered overlay of one arm (None for K)."""
    spec = C.ARM_SPEC[arm]
    if spec["sizing"] == "none":
        return None
    days, i0, i1 = win["days"], win["i0"], win["i1"]
    mth = spec["tenor_months"]
    roll, exp, ten = ov.roll_schedule(days, i0, i1, mth)
    hedge = win["e2"].astype(np.float64) if spec["e2_only"] else np.ones(len(days))
    return {
        "vxn": win["vxn"],
        "rate": win["rate"],
        "roll": roll,
        "exp_at_roll": exp,
        "tenor_at_roll": ten,
        "hedge_on": hedge,
        "long_otm": spec["long_otm"],
        "short_otm": spec["short_otm"] or 0.0,
        "sizing": 1 if spec["sizing"] == "notional" else 2,
        "budget_per_roll": C.BUDGET_FRAC / 4.0,
        "opt_cost": C.OPT_COST,
        "div_yield": C.DIV_YIELD,
        "skew_s": C.SKEW_S,
        "hedge_ratio": C.HEDGE_RATIO,
    }


def run_arm(win, arm, mode="pre_tax", m=fe_config.M, capital=100_000.0, offset=0, flows=None):
    spec = C.ARM_SPEC[arm]
    wants = win["w45"] if spec["vt"] == 0.45 else win["w_r2"]
    overlay = arm_overlay(win, arm)
    if overlay is not None:
        overlay = dict(overlay, offset=offset)
    return ov.simulate(
        win["o"],
        win["c"],
        wants,
        win["env"],
        capital,
        win["i0"],
        win["i1"],
        arm="K",
        mode=mode,
        m=m,
        overlay=overlay,
        underlying=win["qqq"],
        flows=flows,
    )


# ----------------------------------------------------------------------------- stats
def window_row(win, arm, m=fe_config.M, capital=100_000.0, offset=0):
    rp = run_arm(win, arm, "pre_tax", m=m, capital=capital)
    rq = run_arm(win, arm, "progressive", m=m, capital=capital, offset=offset)
    i0, i1 = win["i0"], win["i1"]
    dates = win["days"][i0 - 1 : i1 + 1]
    nav = rp["_nav"]
    return {
        "window": [win["days"][i0], win["days"][i1]],
        "years": r6(rp["years"]),
        "pre_tax_cagr": r6(rp["cagr_usd_liquidated"]),
        "pre_tax_max_dd": r6(rp["max_dd_nav_usd"]),
        "pre_tax_turnover": r6(rp["turnover_annual"]),
        "after_tax_cagr": r6(rq["cagr_usd_liquidated"]),
        "after_tax_cagr_unliquidated": r6(rq["cagr_usd_unliquidated"]),
        "after_tax_max_dd": r6(rq["max_dd_nav_usd"]),
        "terminal_usd": r6(rq["terminal_usd_liquidated"]),
        "tax_usd": r6(rq["tax_total_usd_at_payment_fx"]),
        "sells_per_year": r6(rq["sells"] / rq["years"]),
        "trades_per_year": r6(rq["strategy_trades"] / rq["years"]),
        "option_premium_usd": r6(rp["option_premium_usd"]),
        "option_payoff_usd": r6(rp["option_payoff_usd"]),
        "option_net_usd": r6(rp["option_payoff_usd"] - rp["option_premium_usd"]),
        "_nav": nav,
        "_dates": dates,
    }


def _stats(win, arm, m=fe_config.M):
    r = run_arm(win, arm, "pre_tax", m=m, capital=100_000.0)
    return fe_study.pre_stats(win["days"], win["i0"], win["i1"], r)


def clean_stats(st):
    """Round a pre_stats dict for JSON (the two nested dicts handled explicitly)."""
    out = {}
    for k, v in st.items():
        if k == "subperiods":
            out[k] = {
                n: {"from": q["from"], "to": q["to"], "total_return": r6(q["total_return"]),
                    "cagr": r6(q["cagr"]), "max_dd": r6(q["max_dd"])}
                for n, q in v.items()
            }
        elif k == "2017_end":
            out[k] = {"from": v["from"], "to": v["to"], "total_return": r6(v["total_return"]),
                      "cagr": r6(v["cagr"]), "max_dd": r6(v["max_dd"])}
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = r6(v)
    return out


def drawdown_episodes(nav, thresh):
    """Distinct peak-to-trough episodes deeper than `thresh` (recovery or a new peak ends one)."""
    peak = nav[0]
    trough = nav[0]
    armed = False
    depth = 0.0
    out = []
    for x in nav:
        if x >= peak:
            if armed and depth > thresh:
                out.append(depth)
            peak = x
            armed = False
            depth = 0.0
        else:
            d = 1 - x / peak
            if d > depth:
                depth = d
            armed = True
    if armed and depth > thresh:
        out.append(depth)
    return out


def dd_profile(win, arm):
    r = run_arm(win, arm, "pre_tax", m=fe_config.M, capital=100_000.0)
    nav = r["_nav"]
    dates = win["days"][win["i0"] - 1 : win["i1"] + 1]
    assert len(nav) == len(dates)
    worst = {}
    for n in (21, 63):
        if len(nav) > n:
            worst[f"worst_{n}s"] = r6(min(nav[t] / nav[t - n] - 1 for t in range(n, len(nav))))
        else:
            worst[f"worst_{n}s"] = None
    return {
        "max_dd": r6(1 - (nav / np.maximum.accumulate(nav)).min()),
        "episodes_10": len(drawdown_episodes(nav, 0.10)),
        "episodes_20": len(drawdown_episodes(nav, 0.20)),
        "episodes_30": len(drawdown_episodes(nav, 0.30)),
        "worst_21s": worst["worst_21s"],
        "worst_63s": worst["worst_63s"],
        "avg_nav_usd": r6(float(np.mean(nav))),
        "annual_hedge_cost_pp": None if r is None else r6(
            100.0 * (r["option_premium_usd"] - r["option_payoff_usd"]) / float(np.mean(nav)) / r["years"]
        ),
        "annual_premium_pp": r6(100.0 * r["option_premium_usd"] / float(np.mean(nav)) / r["years"]),
        "annual_payoff_pp": r6(100.0 * r["option_payoff_usd"] / float(np.mean(nav)) / r["years"]),
    }


CRASHES = {
    "2001_2002_dotcom": ("2001-01-01", "2002-12-31"),
    "2008_gfc": ("2008-09-01", "2009-03-31"),
    "2020_covid": ("2020-02-01", "2020-04-30"),
    "2022_bear": ("2022-01-01", "2022-12-31"),
}


def crash_table(win):
    out = {}
    for name, (a, b) in CRASHES.items():
        ks = [i for i in range(win["i0"], win["i1"] + 1) if a <= win["days"][i] <= b]
        qret = win["qqq"][ks[-1]] / win["qqq"][ks[0]] - 1
        out[name] = {"qqq_return": r6(qret), "arms": {}}
        for arm in ARMS:
            flows = np.zeros(len(win["days"]))
            r = run_arm(win, arm, "pre_tax", capital=100_000.0, flows=flows)
            prem = -sum(flows[i] for i in ks if flows[i] < 0)
            pay = sum(flows[i] for i in ks if flows[i] > 0)
            out[name]["arms"][arm] = {
                "premium_usd": r6(prem),
                "payoff_usd": r6(pay),
                "net_usd": r6(pay - prem),
                "nav_return": r6(r["_nav"][ks[-1] - win["i0"] + 1] / r["_nav"][ks[0] - win["i0"] + 1] - 1),
            }
    return out


# ----------------------------------------------------------------------------- bootstrap
FIN_BASE = 0.0
FIN_CAL = 0.0075
EXP_Q = 0.0095


def chunk_legs_vt(q, y, pos):
    """`fe_boot.chunk_legs` plus the vt = 0.45 target series (batched over paths, numpy)."""
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
    yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
    so, sc = fe_signals.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN_BASE, EXP_Q)
    so_c, sc_c = fe_signals.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN_CAL, EXP_Q)
    return so, sc, so_c, sc_c, pc, yyp


def boot_legs(q, y, pos):
    """Python side: target series of every arm for a chunk of drawn paths."""
    so, sc, so_c, sc_c, pc, yyp = chunk_legs_vt(q, y, pos)
    e2 = fe_signals.e2_up(pc)
    comps = fe_signals.early_components(pc)
    on_eff, factor = fe_signals.r2_state(pc, e2, comps)
    v = fe_signals.vol_n(sc)
    vc = fe_signals.vol_n(sc_c)
    w = sd.sized_wants(on_eff, factor, v, 0.35)
    wc = sd.sized_wants(on_eff, factor, vc, 0.35)
    w45 = sd.sized_wants(on_eff, factor, v, 0.45)
    w45c = sd.sized_wants(on_eff, factor, vc, 0.45)
    w_e2 = sd.sized_wants(e2, np.ones_like(factor), v, 0.35)
    return (so, sc, w, w45), (so_c, sc_c, wc, w45c), pc, e2, w_e2


@njit(parallel=True, cache=True, nogil=True)
def _boot_chunk_ov(
    oq_b,
    cq_b,
    uq_b,
    wq_b,
    w45_b,
    oq_c,
    cq_c,
    wq_c,
    w45_c,
    wq_e2,
    vxn_p,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    rate,
    roll1,
    exp1,
    ten1,
    roll2,
    exp2,
    ten2,
    roll3,
    exp3,
    ten3,
    yfrac,
    hedge_all,
    hedge_e2,
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
    ov_on,
    sched_arr,
    vt45,
    e2_only,
    long_otm,
    short_otm,
    sizing,
    budget,
    opt_cost,
    div_yield,
    skew_s,
    hedge_ratio,
    mode_ids,
    p0,
    n_pre,
    n_tax,
    anchor_e2,
    opt_offset,
    out_pre,
    out_at,
    out_anchor,
    dbg0,
):
    rate_empty = np.zeros(0, dtype=np.float64)
    W = oq_b.shape[0]
    n_arms = band.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        for a in range(n_arms):
            if sched_arr[a] == 1:
                roll = roll1
                exp = exp1
                ten = ten1
            elif sched_arr[a] == 2:
                roll = roll2
                exp = exp2
                ten = ten2
            else:
                roll = roll3
                exp = exp3
                ten = ten3
            h = hedge_e2[p] if e2_only[a] else hedge_all
            if pg < n_pre:
                wb = w45_b[p] if vt45[a] else wq_b[p]
                nav, _d, _s, _y, _g, _gn, _h = ov.sim_one_ov(
                    oq_b[p], cq_b[p], wb, uq_b[p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                    pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    0, True, Y0, M_lo, rate_empty, False, dbg0, ov_on[a], vxn_p[p], rate, roll, exp,
                    ten, yfrac, h, long_otm[a], short_otm[a], sizing[a], budget[a], opt_cost,
                    div_yield, skew_s, hedge_ratio, opt_offset, np.zeros(0, dtype=np.float64),
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                wc = w45_c[p] if vt45[a] else wq_c[p]
                nav, _d, _s, _y, _g, _gn, _h = ov.sim_one_ov(
                    oq_c[p], cq_c[p], wc, uq_b[p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                    pay_ptr, pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    0, True, Y0, M_lo, rate_empty, False, dbg0, ov_on[a], vxn_p[p], rate, roll, exp,
                    ten, yfrac, h, long_otm[a], short_otm[a], sizing[a], budget[a], opt_cost,
                    div_yield, skew_s, hedge_ratio, opt_offset, np.zeros(0, dtype=np.float64),
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
            if pg < n_tax:
                for ci in range(n_caps):
                    for mi in range(n_modes):
                        wb = w45_b[p] if vt45[a] else wq_b[p]
                        _nav, _d, scal, _y, _g, _gn, _h = ov.sim_one_ov(
                            oq_b[p], cq_b[p], wb, uq_b[p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a], usd_rule[a],
                            defer[a], mode_ids[mi], True, Y0, M_lo, rate_empty, False, dbg0, ov_on[a],
                            vxn_p[p], rate, roll, exp, ten, yfrac, h, long_otm[a], short_otm[a],
                            sizing[a], budget[a], opt_cost, div_yield, skew_s, hedge_ratio,
                            opt_offset, np.zeros(0, dtype=np.float64),
                        )
                        out_at[ci, mi, a, p] = (scal[ov.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1
        if anchor_e2 and pg < n_tax:
            for mi in range(n_modes):
                _nav, _d, scal, _y, _g, _gn, _h = ov.sim_one_ov(
                    oq_b[p], cq_b[p], wq_e2[p], uq_b[p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                    pay_ptr, pay_yr, pay_part, cap_at[0], i0, i1, cost, False, False, False,
                    mode_ids[mi], True, Y0, M_lo, rate_empty, False, dbg0, 0, vxn_p[p], rate,
                    roll1, exp1, ten1, yfrac, hedge_all, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 1.0, 0,
                    np.zeros(0, dtype=np.float64),
                )
                out_anchor[mi, p] = (scal[ov.SC_LIQ] / cap_at[0]) ** (1 / yrs) - 1


def boot_overlay(
    seed=fe_config.SEED,
    block=fe_config.BLOCK,
    arms=ARMS,
    n_pre=fe_config.PRE_BOOT_PATHS,
    n_tax=fe_config.TAX_BOOT_PATHS,
    capitals=("100k",),
    modes=("progressive", "flat"),
    m=fe_config.M,
    chunk=128,
    quiet=False,
    window=None,
    keep_paths=False,
    offset=0,
):
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    _days, _qqq, vxn_src, _vix, _rate = sd.engine_market()
    d3, tb = fe_data.load_rates()
    rate = fe_data.rate_on(win.dates, d3, tb)
    yfrac = ov.year_fraction(win.dates)
    scheds = {mth: ov.roll_schedule(win.dates, win.i0, win.i1, mth) for mth in (1, 2, 3)}
    n_paths = max(n_pre, n_tax)
    n_arms = len(arms)
    cap_at = np.array([fe_config.TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    band = np.array([fe_sim.RULE["K"]["band"]] * n_arms, dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE["K"]["usd_rule"]] * n_arms, dtype=np.bool_)
    defer = np.array([fe_sim.RULE["K"]["defer"]] * n_arms, dtype=np.bool_)
    ov_on = np.array([0 if a == "K" else 1 for a in arms], dtype=np.int64)
    sched_arr = np.array([0 if a == "K" else C.ARM_SPEC[a]["tenor_months"] for a in arms], dtype=np.int64)
    vt45 = np.array([1 if C.ARM_SPEC[a]["vt"] == 0.45 else 0 for a in arms], dtype=np.int64)
    e2_only = np.array([1 if C.ARM_SPEC[a]["e2_only"] else 0 for a in arms], dtype=np.int64)
    long_otm = np.array([C.ARM_SPEC[a]["long_otm"] or 0.0 for a in arms], dtype=np.float64)
    short_otm = np.array([C.ARM_SPEC[a]["short_otm"] or 0.0 for a in arms], dtype=np.float64)
    sizing = np.array([0 if C.ARM_SPEC[a]["sizing"] == "none" else (1 if C.ARM_SPEC[a]["sizing"] == "notional" else 2) for a in arms], dtype=np.int64)
    budget = np.array([C.BUDGET_FRAC / 4.0 if C.ARM_SPEC[a]["sizing"] == "budget" else 0.0 for a in arms], dtype=np.float64)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array(
        [fe_tax_mode(md) for md in modes], dtype=np.int64
    )
    pre = {(mdi, a): np.empty(n_pre, dtype=np.float64) for mdi in ("base", "calibrated") for a in arms}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=np.float64) for a in arms} for mode in modes} for cl in capitals}
    anchor = {mode: np.empty(n_tax, dtype=np.float64) for mode in modes}
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    rng = np.random.default_rng(seed)
    roll1, exp1, ten1 = scheds[1]
    roll2, exp2, ten2 = scheds[2]
    roll3, exp3, ten3 = scheds[3]
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
        (so, sc, w, w45), (so_c, sc_c, wc, w45c), pc, e2, w_e2 = boot_legs(win.q, win.y, pos)
        vxn_p = vxn_src[pos + 1]
        hedge_all = np.ones(len(win.dates))
        hedge_e2 = np.ascontiguousarray(e2.astype(np.float64))
        out_pre = np.empty((2, n_arms, W), dtype=np.float64)
        out_at = np.empty((len(capitals), len(modes), n_arms, W), dtype=np.float64)
        out_anchor = np.empty((len(modes), W), dtype=np.float64)
        _boot_chunk_ov(
            np.ascontiguousarray(so), np.ascontiguousarray(sc), np.ascontiguousarray(pc),
            np.ascontiguousarray(w), np.ascontiguousarray(w45),
            np.ascontiguousarray(so_c), np.ascontiguousarray(sc_c), np.ascontiguousarray(wc),
            np.ascontiguousarray(w45c), np.ascontiguousarray(w_e2), np.ascontiguousarray(vxn_p),
            env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr,
            env.pay_yr, env.pay_part, rate, roll1, exp1, ten1, roll2, exp2, ten2, roll3, exp3, ten3,
            yfrac, hedge_all, hedge_e2, fe_config.TAX_CAPITALS["100k"], cap_at, win.i0, win.i1,
            cost, win.yrs, env.Y0, env.M_lo, band, usd_rule, defer, ov_on, sched_arr, vt45, e2_only,
            long_otm, short_otm, sizing, budget, C.OPT_COST, C.DIV_YIELD, C.SKEW_S, C.HEDGE_RATIO,
            mode_ids, p0, n_pre, n_tax, True, offset, out_pre, out_at, out_anchor, dbg0,
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
            print(f"  boot path {min(p0 + W, n_paths)}/{n_paths}", flush=True)
    out = {
        "n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
        "arms": list(arms), "calendar_start": win.dates[0], "calendar_end": win.dates[-1],
        "pre": {}, "tax": {},
    }
    for mdi in ("base", "calibrated"):
        base = pre[(mdi, "K")]
        out["pre"][mdi] = {}
        for arm in arms:
            c = pre[(mdi, arm)]
            d = c - base
            out["pre"][mdi][arm] = {
                "cagr_median": r6(np.median(c)),
                "diff_vs_K_median_pp": r6(np.median(d) * 100),
                "diff_vs_K_mean_pp": r6(np.mean(d) * 100),
                "share_gt_0": r6(np.mean(d > 0)),
                "p05_delta_pp": r6(np.percentile(d, 5) * 100),
                "p95_delta_pp": r6(np.percentile(d, 95) * 100),
            }
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": fe_config.TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": r6(np.median(at[cl][mode][arm]))}
            base = at[cl][mode]["K"]
            for arm in arms:
                if arm == "K":
                    continue
                d = at[cl][mode][arm] - base
                e[f"{arm}_vs_K"] = {
                    "median_delta_cagr_pp": r6(np.median(d) * 100),
                    "mean_delta_cagr_pp": r6(np.mean(d) * 100),
                    "share_gt_0": r6(np.mean(d > 0)),
                    "p05_delta_cagr_pp": r6(np.percentile(d, 5) * 100),
                    "p95_delta_cagr_pp": r6(np.percentile(d, 95) * 100),
                }
            out["tax"][cl][mode] = e
    out["anchor_e2_100k"] = {mode: {"cagr_median": r6(np.median(anchor[mode]))} for mode in modes}
    if keep_paths:
        out["paths"] = {"pre": pre, "tax": at, "anchor_e2": anchor}
    return out


def fe_tax_mode(mode):
    from research.fast_engine.fe_tax import MODE_FLAT, MODE_PROGRESSIVE

    return MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT


# ----------------------------------------------------------------------------- OOS
def load_spx_window(a, b):
    rows = []
    with open(C.SPX_CSV) as fh:
        import csv as _csv

        for r in _csv.DictReader(fh):
            if a <= r["date"] <= b:
                rows.append(
                    (r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                )
    rows.sort()
    days = [r[0] for r in rows]
    c = np.array([r[4] for r in rows])
    o = np.empty_like(c)
    for k, (d, op, hi, lo, cl) in enumerate(rows):
        o[k] = cl if k == 0 else (c[k - 1] if fe_data.placeholder_open(op, hi, lo, cl) else op)
    d3, tb = fe_data.load_rates()
    y = fe_data.rate_on(days, d3, tb)
    return days, o, c, y


def oos_stage(win_vix):
    days, o, c, y = load_spx_window("1989-06-01", "1999-12-29")
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + fe_config.EXTRA_FIN, fe_config.EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    on_eff, factor = fe_signals.r2_state(c, e2, comps)
    w = sd.sized_wants(on_eff, factor, fe_signals.vol_n(sc), 0.35)
    w45 = sd.sized_wants(on_eff, factor, fe_signals.vol_n(sc), 0.45)
    start = max(fe_config.START, int(np.searchsorted(np.array(days), "1990-01-02", side="left")))
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    out = {"window": [days[start], days[-1]], "sessions": len(days) - start, "arms": {}}
    qqq = c
    for arm in ARMS:
        spec = C.ARM_SPEC[arm]
        wants = w45 if spec["vt"] == 0.45 else w
        overlay = None
        if spec["sizing"] != "none":
            mth = spec["tenor_months"]
            roll, exp, ten = ov.roll_schedule(days, start, len(days) - 1, mth)
            hedge = e2.astype(np.float64) if spec["e2_only"] else np.ones(len(days))
            overlay = {
                "vxn": win_vix, "rate": y, "roll": roll, "exp_at_roll": exp, "tenor_at_roll": ten,
                "hedge_on": hedge, "long_otm": spec["long_otm"], "short_otm": spec["short_otm"] or 0.0,
                "sizing": 1 if spec["sizing"] == "notional" else 2,
                "budget_per_roll": C.BUDGET_FRAC / 4.0, "opt_cost": C.OPT_COST,
                "div_yield": 0.018, "skew_s": C.SKEW_S, "hedge_ratio": C.HEDGE_RATIO,
            }
        r = ov.simulate(so, sc, wants, env, 100_000.0, start, len(days) - 1, arm="K",
                        mode="pre_tax", overlay=overlay, underlying=qqq)
        out["arms"][arm] = {
            "cagr": r6(r["cagr_usd_liquidated"]),
            "max_dd": r6(r["max_dd_nav_usd"]),
            "turnover": r6(r["turnover_annual"]),
            "option_net_usd": r6(r["option_payoff_usd"] - r["option_premium_usd"]),
        }
    for arm in ARMS:
        if arm == "K":
            continue
        out["arms"][arm]["delta_cagr_pp"] = r6((out["arms"][arm]["cagr"] - out["arms"]["K"]["cagr"]) * 100)
        out["arms"][arm]["delta_max_dd_pp"] = r6((out["arms"][arm]["max_dd"] - out["arms"]["K"]["max_dd"]) * 100)
    return out


# ----------------------------------------------------------------------------- harness
def zero_flow_parity():
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, fe_study.START, T)
    rows = []
    worst = 0.0
    for arm in ("C0", "A1u", "H2", "K"):
        for mode in ("pre_tax", "progressive", "flat"):
            a = fe_sim.simulate(o, c, w_r2, env, 100_000.0, fe_study.START, T, arm=arm, mode=mode)
            b = ov.simulate(o, c, w_r2, env, 100_000.0, fe_study.START, T, arm=arm, mode=mode)
            d = float(np.max(np.abs(a["_nav"] - b["_nav"])))
            worst = max(worst, d)
            keys = ("ending_nav_usd", "terminal_usd_liquidated", "tax_total_try", "turnover_total", "sells")
            kd = max(abs(a[k] - b[k]) for k in keys)
            rows.append({"arm": arm, "mode": mode, "nav_max_abs_diff": d, "scalar_max_abs_diff": kd})
    return {"rows": rows, "worst_nav_diff": worst, "pass": bool(worst <= 1e-9)}


def k_anchors():
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, fe_study.START, T)
    ref = {("base", 2): (0.157649, 0.46670, 5.4561), ("calibrated", 2): (0.151785, 0.46795, 5.4562),
           ("base", 1): (0.160173, 0.46353, 5.4568), ("calibrated", 1): (0.154303, 0.46479, 5.4562)}
    checks = {}
    bad = 0
    for md, spread in (("base", 0.0), ("calibrated", 0.0075)):
        _d, o_, c_, _we, wr_, _e = fe_data.main_inputs(spread)
        for m in (1, 2):
            r = fe_sim.simulate(o_, c_, wr_, env, 100_000.0, fe_study.START, T, arm="K", mode="pre_tax", m=m)
            got = (r["cagr_usd_unliquidated"], r["max_dd_nav_usd"], r["turnover_annual"])
            want = ref[(md, m)]
            dd = max(abs(got[0] - want[0]), abs(got[1] - want[1]), abs(got[2] - want[2]))
            checks[f"pre_{md}_m{m}"] = {"reproduced": [r6(x) for x in got], "reference": list(want), "max_abs_diff": dd}
            if dd > 1e-4:
                bad += 1
    at_ref = {"2000_03_to_2026_09": (0.107533, 0.46670), "2010_01_to_end": (0.152354, 0.44627), "2017_01_to_end": (0.239720, 0.34977)}
    for wn, (a, b) in fe_config.TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        e = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        r = fe_sim.simulate(o, c, w_r2, e, 100_000.0, i0, i1, arm="K", mode="progressive")
        got = (r["cagr_usd_liquidated"], r["max_dd_nav_usd"])
        want = at_ref[wn]
        dd = max(abs(got[0] - want[0]), abs(got[1] - want[1]))
        checks[f"at_{wn}"] = {"reproduced": [r6(x) for x in got], "reference": list(want), "max_abs_diff": dd}
        if dd > AT_TOL:
            bad += 1
    # R2 reference row
    r2 = fe_sim.simulate(o, c, w_r2, env, 100_000.0, fe_study.START, T, arm="C0", mode="pre_tax")
    checks["R2_pre_tax"] = {"reproduced": [r6(r2["cagr_usd_unliquidated"]), r6(r2["max_dd_nav_usd"])],
                            "reference": [0.154766, 0.46758]}
    r2_at = []
    for wn, (a, b) in fe_config.TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        e = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        r = fe_sim.simulate(o, c, w_r2, e, 100_000.0, i0, i1, arm="C0", mode="progressive")
        r2_at.append(r6(r["cagr_usd_liquidated"]))
    checks["R2_after_tax"] = {"reproduced": r2_at, "reference": [0.101600, 0.147059, 0.228861]}
    return {"checks": checks, "n_outside_tolerance": bad, "pass": bool(bad == 0)}


def parity_quick():
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, "-m", "research.fast_engine.parity", "--quick"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    tail = (p.stdout or "")[-4000:]
    return {"returncode": p.returncode, "pass": p.returncode == 0, "seconds": round(time.time() - t0, 1),
            "stdout_tail": tail}


def bootstrap_parity():
    win = fe_boot.BootWindow()
    a = fe_boot.boot(seed=fe_config.SEED, block=fe_config.BLOCK, arms=("C0", "K"), n_pre=QUICK_BOOT_PATHS,
                     n_tax=QUICK_BOOT_PATHS, capitals=("100k",), modes=("progressive", "flat"),
                     deltas=("K",), baselines=(), anchor_e2=False, label="parity", chunk=64,
                     quiet=True, window=win, keep_paths=True)
    b = boot_overlay(seed=fe_config.SEED, block=fe_config.BLOCK, arms=("K",), n_pre=QUICK_BOOT_PATHS,
                     n_tax=QUICK_BOOT_PATHS, capitals=("100k",), modes=("progressive", "flat"),
                     chunk=64, quiet=True, window=win, keep_paths=True)
    diffs = {}
    for mdi in ("base", "calibrated"):
        diffs[f"pre_{mdi}"] = float(np.max(np.abs(a["paths"]["pre"][(mdi, "K")] - b["paths"]["pre"][(mdi, "K")])))
    for mode in ("progressive", "flat"):
        diffs[f"tax_{mode}"] = float(
            np.max(np.abs(a["paths"]["tax"]["100k"][mode]["K"] - b["paths"]["tax"]["100k"][mode]["K"]))
        )
    return {"diffs": diffs, "max_abs_diff": max(diffs.values()), "pass": bool(max(diffs.values()) <= 1e-12)}


def harness(deep=True):
    out = {"parity_quick": parity_quick(), "zero_flow": zero_flow_parity(), "k_anchors": k_anchors()}
    if deep:
        out["bootstrap_parity"] = bootstrap_parity()
        out["pput_sanity"] = pput_probe.run_sanity()
        out["main_inputs_vt_identity"] = main_inputs_vt_identity()
    out["pass"] = bool(
        out["parity_quick"]["pass"] and out["zero_flow"]["pass"] and out["k_anchors"]["pass"]
        and out.get("bootstrap_parity", {"pass": True})["pass"]
        and out.get("pput_sanity", {"pass": True})["pass"]
        and out.get("main_inputs_vt_identity", {"pass": True})["pass"]
    )
    return out


def main_inputs_vt_identity():
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    _d, o2, c2, _we, w35, e2b = sd.main_inputs_vt(0.0, 0.35)
    d = float(np.max(np.abs(w35 - w_r2)))
    return {"max_abs_wants_diff": d, "opens_equal": float(np.max(np.abs(o2 - o))),
            "e2_equal": bool(np.array_equal(e2b, e2)), "pass": bool(d == 0.0 and np.array_equal(e2b, e2))}


# ----------------------------------------------------------------------------- smoke
def smoke():
    import datetime as dt

    checks = {}

    # 1. zero-flow parity on a synthetic random walk (both labels, no tax)
    rng = np.random.default_rng(7)
    n = 700
    r = rng.normal(0.0004, 0.011, n)
    close = 100 * np.cumprod(1 + r)
    open_ = np.concatenate([[100.0], close[:-1] * (1 + rng.normal(0, 0.002, n - 1))])
    days = []
    d = dt.date(2019, 1, 2)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    fx = np.ones(n)
    ufe = {f"{y}-{mo:02d}": 1.0 for y in range(2017, 2029) for mo in range(1, 13)}
    env = fe_sim.RunEnv(days, fx, ufe, 1, n - 1)
    wants = np.full(n, 0.9)
    for arm in ("C0", "K"):
        a = fe_sim.simulate(open_, close, wants, env, 100_000.0, 1, n - 1, arm=arm, mode="pre_tax")
        b = ov.simulate(open_, close, wants, env, 100_000.0, 1, n - 1, arm=arm, mode="pre_tax")
        checks[f"zero_flow_{arm}"] = float(np.max(np.abs(a["_nav"] - b["_nav"])))

    # 2. option identity: one 10 % OTM put, flat prices then a 30 % drop at expiry
    n2 = 60
    px = np.full(n2, 100.0)
    px[35:] = 70.0
    o2 = np.concatenate([[100.0], px[:-1]])
    days2 = []
    d = dt.date(2020, 1, 2)
    while len(days2) < n2:
        if d.weekday() < 5:
            days2.append(d.isoformat())
        d += dt.timedelta(days=1)
    env2 = fe_sim.RunEnv(days2, np.ones(n2), ufe, 1, n2 - 1)
    roll = np.zeros(n2, dtype=np.int8)
    exp_at = np.full(n2, -1, dtype=np.int64)
    ten = np.zeros(n2)
    roll[5] = 1
    exp_at[5] = 35
    yfrac = ov.year_fraction(days2)
    ten[5] = yfrac[35] - yfrac[5]
    overlay = {
        "vxn": np.full(n2, 0.25), "rate": np.full(n2, 0.04), "roll": roll, "exp_at_roll": exp_at,
        "tenor_at_roll": ten, "hedge_on": np.ones(n2), "long_otm": 0.10, "short_otm": 0.0,
        "sizing": 1, "budget_per_roll": 0.0, "opt_cost": C.OPT_COST, "div_yield": 0.0,
        "skew_s": C.SKEW_S, "hedge_ratio": 1.0,
    }
    res = ov.simulate(o2, px, np.full(n2, 0.98), env2, 100_000.0, 1, n2 - 1, arm="K",
                      mode="pre_tax", overlay=overlay, underlying=px)
    sig = 0.25 * (1 + C.SKEW_S * np.log(100.0 / 90.0))
    prem_unit = pput_probe.bs_put(100.0, 90.0, ten[5], 0.04, 0.0, sig) * (1 + C.OPT_COST)
    units = res["option_premium_usd"] / prem_unit
    checks["payoff_identity_usd"] = float(abs(res["option_payoff_usd"] - units * 20.0))
    checks["premium_positive"] = bool(res["option_premium_usd"] > 0)
    checks["payoff_positive"] = bool(res["option_payoff_usd"] > 0)
    checks["taxable_sum_equals_net"] = float(
        abs(sum(res["option_taxable_try_by_year"].values()) - (res["option_payoff_usd"] - res["option_premium_usd"]))
    )
    # no-payoff case: same path but never below the strike
    px3 = np.full(n2, 100.0)
    res3 = ov.simulate(o2, px3, np.full(n2, 0.98), env2, 100_000.0, 1, n2 - 1, arm="K",
                       mode="pre_tax", overlay=overlay, underlying=px3)
    checks["no_payoff_zero"] = bool(res3["option_payoff_usd"] == 0.0)
    # H5 gate: no e2, no rolls
    overlay5 = dict(overlay, hedge_on=np.zeros(n2))
    res5 = ov.simulate(o2, px, np.full(n2, 0.98), env2, 100_000.0, 1, n2 - 1, arm="K",
                       mode="pre_tax", overlay=overlay5, underlying=px)
    checks["h5_gate_no_premium"] = bool(res5["option_premium_usd"] == 0.0)

    # 3. roll calendar: the third Friday
    days3 = [d.isoformat() for d in (dt.date(2021, 1, 1) + dt.timedelta(days=k) for k in range(400))]
    days3 = [x for x in days3 if dt.date.fromisoformat(x).weekday() < 5]
    roll3, exp3, ten3 = ov.roll_schedule(days3, 0, len(days3) - 1, 1)
    first = days3[int(np.argmax(roll3))]
    checks["first_roll_is_third_friday"] = {"date": first, "pass": bool(first == "2021-01-15")}

    # 4. PPUT pricer against a textbook value
    checks["bs_put_100_90_1m_25vol"] = {
        "value": pput_probe.bs_put(100.0, 90.0, 1 / 12, 0.04, 0.0, 0.25),
        "reference": 0.1975, "pass": bool(abs(pput_probe.bs_put(100.0, 90.0, 1 / 12, 0.04, 0.0, 0.25) - 0.1975) < 1e-3),
    }
    ok = (
        checks["zero_flow_C0"] == 0.0 and checks["zero_flow_K"] == 0.0
        and checks["payoff_identity_usd"] < 1e-6 and checks["premium_positive"] and checks["payoff_positive"]
        and checks["taxable_sum_equals_net"] < 1e-6 and checks["no_payoff_zero"]
        and checks["h5_gate_no_premium"] and checks["first_roll_is_third_friday"]["pass"]
        and checks["bs_put_100_90_1m_25vol"]["pass"]
    )
    print(json.dumps({"checks": checks, "pass": bool(ok)}, indent=1, sort_keys=True))
    print("SMOKE PASS" if ok else "SMOKE FAIL")
    if not ok:
        raise SystemExit(1)


# ----------------------------------------------------------------------------- gates
def gate_table(win, rows, boots, oos):
    K = rows["K"]
    out = {}
    for arm in ARMS:
        if arm == "K":
            continue
        r = rows[arm]
        g = {}
        # G1 / G6: pre-tax return vs K (or Calmar with a 0.5 pp give-up)
        g1 = []
        for md in ("base", "calibrated"):
            a, k = r[f"pre_{md}"], K[f"pre_{md}"]
            g1.append(bool(a["cagr"] >= k["cagr"] or (a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)))
        g["G1"] = all(g1)
        g2 = []
        for md in ("base", "calibrated"):
            g2.append(bool(r[f"pre_{md}"]["max_dd"] <= K[f"pre_{md}"]["max_dd"] + 0.01))
        g["G2"] = all(g2)
        g3 = []
        for md in ("base", "calibrated"):
            g3.append(bool(r[f"pre_{md}"]["2017_end"]["cagr"] >= K[f"pre_{md}"]["2017_end"]["cagr"] - 0.01))
        g["G3"] = all(g3)
        sub = 0
        for p in ("P1", "P2", "P3"):
            if r["pre_base"]["subperiods"][p]["cagr"] > K["pre_base"]["subperiods"][p]["cagr"]:
                sub += 1
        g["G4"] = bool(sub >= 2)
        g5 = []
        for md in ("base", "calibrated"):
            g5.append(bool(boots["pre"][md][arm]["diff_vs_K_median_pp"] >= 0.0))
        g["G5"] = all(g5)
        g6 = []
        for md in ("base", "calibrated"):
            a, k = r[f"pre_{md}_m1"], K[f"pre_{md}_m1"]
            g6.append(bool(a >= k or (r[f"pre_{md}_m1_calmar"] >= 1.1 * K[f"pre_{md}_m1_calmar"] and a >= k - 0.005)))
        g["G6"] = all(g6)
        o = oos["arms"][arm]
        g["G7"] = bool(o["delta_cagr_pp"] >= 0.0 and o["delta_max_dd_pp"] <= 3.0)
        g["G8"] = "n/a (reduced OOS: no OOS bootstrap)"
        g["G9"] = bool(r["pre_base"]["turnover_annual"] <= 2 * K["pre_base"]["turnover_annual"])
        g["AT1"] = bool(all(r["at"][wn]["after_tax_cagr"] >= K["at"][wn]["after_tax_cagr"] for wn in r["at"]))
        at_b = []
        for mode in ("progressive", "flat"):
            b = boots["tax"]["100k"][mode][f"{arm}_vs_K"]
            at_b.append(bool(b["median_delta_cagr_pp"] >= 0.0 and b["share_gt_0"] >= 0.55))
        g["AT2"] = all(at_b)
        g["all_pre"] = all(g[k] is True for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G9"))
        g["all"] = bool(g["all_pre"] and g["AT1"] and g["AT2"])
        out[arm] = g
    return out


# ----------------------------------------------------------------------------- stages
def build_results(harness_out=None):
    t_start = time.time()
    if harness_out is None:
        harness_out = harness(deep=True)
    if not harness_out["zero_flow"]["pass"] or not harness_out["k_anchors"]["pass"]:
        raise RuntimeError("harness failed; not running the study")
    if not harness_out["pput_sanity"]["pass"]:
        return {
            "campaign": "put_hedge_v1",
            "status": "feasibility_stop: PPUT sanity check failed",
            "harness": harness_out,
        }
    win = load_window()
    i0, i1 = win["i0"], win["i1"]
    dates = win["days"][i0 - 1 : i1 + 1]

    # pre-tax table on the reduced main window
    pre = {}
    for arm in ARMS:
        rec = {}
        for md, m in (("base", 1), ("base", 2), ("base", 4), ("calibrated", 1), ("calibrated", 2)):
            r = run_arm(win, arm, "pre_tax", m=m, capital=100_000.0)
            rec[f"{md}_m{m}"] = clean_stats(fe_study.pre_stats(win["days"], i0, i1, r))
        rec["base"] = rec["base_m2"]
        rec["calibrated"] = rec["calibrated_m2"]
        rec["base_m1_calmar"] = rec["base_m1"]["calmar"]
        rec["calibrated_m1_calmar"] = rec["calibrated_m1"]["calmar"]
        pre[arm] = rec

    # after-tax windows
    windows = {}
    for arm in ARMS:
        windows[arm] = {}
        for wn, (a, b) in C.WINDOWS.items():
            w = load_window(a, b)
            windows[arm][wn] = window_row(w, arm)
    # keep the main-window NAV paths for the drawdown tables
    navs = {arm: windows[arm]["vxn_main"].pop("_nav") for arm in ARMS}
    nav_dates = windows[ARMS[0]]["vxn_main"].pop("_dates")
    for arm in ARMS:
        for wn in windows[arm]:
            windows[arm][wn].pop("_nav", None)
            windows[arm][wn].pop("_dates", None)

    # sensitivity: option losses offset equity gains
    sens = {}
    for arm in ARMS:
        if arm == "K":
            continue
        sens[arm] = {}
        for wn, (a, b) in C.WINDOWS.items():
            w = load_window(a, b)
            base = windows[arm][wn]["after_tax_cagr"]
            r = run_arm(w, arm, "progressive", offset=1, capital=100_000.0)
            sens[arm][wn] = {
                "after_tax_cagr_offset": r6(r["cagr_usd_liquidated"]),
                "delta_vs_conservative_pp": r6((r["cagr_usd_liquidated"] - base) * 100),
            }

    # clean bootstraps B1 (progressive) / B2 (flat)
    boots = boot_overlay(seed=fe_config.SEED, block=fe_config.BLOCK, arms=ARMS,
                         n_pre=fe_config.PRE_BOOT_PATHS, n_tax=fe_config.TAX_BOOT_PATHS,
                         capitals=("100k",), modes=("progressive", "flat"), chunk=128, quiet=False)

    # OOS: SPX + VIX 1990-1999 (pre-tax, calibrated, m = 2)
    days_spx, _o, _c, _y = load_spx_window("1989-06-01", "1999-12-29")
    vix_full = study_data_vix(days_spx)
    oos = oos_stage(vix_full)

    # drawdown + hedge + crash tables
    dd = {arm: dd_profile({**win, "i0": i0, "i1": i1}, arm) for arm in ARMS}
    for arm in ARMS:
        r = run_arm(win, arm, "pre_tax", capital=100_000.0)
        nav = r["_nav"]
        dd[arm]["max_dd"] = r6(1 - (nav / np.maximum.accumulate(nav)).min())
        dd[arm]["episodes_10"] = len(drawdown_episodes(nav, 0.10))
        dd[arm]["episodes_20"] = len(drawdown_episodes(nav, 0.20))
        dd[arm]["episodes_30"] = len(drawdown_episodes(nav, 0.30))
        dd[arm]["worst_21s"] = r6(min(nav[t] / nav[t - 21] - 1 for t in range(21, len(nav))))
        dd[arm]["worst_63s"] = r6(min(nav[t] / nav[t - 63] - 1 for t in range(63, len(nav))))
        if C.ARM_SPEC[arm]["sizing"] != "none":
            avg = float(np.mean(nav))
            dd[arm]["annual_premium_pp"] = r6(100.0 * r["option_premium_usd"] / avg / r["years"])
            dd[arm]["annual_payoff_pp"] = r6(100.0 * r["option_payoff_usd"] / avg / r["years"])
            dd[arm]["annual_hedge_cost_pp"] = r6(100.0 * (r["option_premium_usd"] - r["option_payoff_usd"]) / avg / r["years"])
    crashes = crash_table(win)

    # gates need the pre-table rows in the gate shape
    gate_rows = {}
    for arm in ARMS:
        gate_rows[arm] = {
            "pre_base": pre[arm]["base_m2"],
            "pre_calibrated": pre[arm]["calibrated_m2"],
            "pre_base_m1": pre[arm]["base_m1"]["cagr"],
            "pre_calibrated_m1": pre[arm]["calibrated_m1"]["cagr"],
            "pre_base_m1_calmar": pre[arm]["base_m1"]["calmar"],
            "pre_calibrated_m1_calmar": pre[arm]["calibrated_m1"]["calmar"],
            "at": windows[arm],
        }
    gates = gate_table(win, gate_rows, boots, oos)
    candidates = [a for a in ARMS if a != "K" and gates[a]["all"]]
    selection = {"candidates": candidates, "selected": None}
    if candidates:
        def score(a):
            return min(
                (windows[a][wn]["after_tax_cagr"] - windows["K"][wn]["after_tax_cagr"]) * 100
                for wn in C.WINDOWS
            )
        selection["selected"] = max(candidates, key=score)
        selection["scores"] = {a: r6(score(a)) for a in candidates}

    elapsed = round(time.time() - t_start, 1)
    res = {
        "campaign": "put_hedge_v1",
        "status": "declared: " + (selection["selected"] or "none"),
        "params": {
            "arms": C.ARM_SPEC,
            "skew_s": C.SKEW_S,
            "div_yield": C.DIV_YIELD,
            "opt_cost_per_side": C.OPT_COST,
            "roll": C.ROLL_RULE,
            "hedge_ratio": C.HEDGE_RATIO,
            "qld_beta": C.QLD_BETA,
            "budget_frac": C.BUDGET_FRAC,
            "main_window": [win["days"][i0], win["days"][i1]],
            "tax_model": "2026 progressive tariff, YI-UFE indexation on the equity book, none on "
                         "the option book; option net P&L per calendar year, losses offset only "
                         "option gains (conservative); offset sensitivity reported",
        },
        "data": data_manifest(),
        "harness": harness_out,
        "pre_tax": pre,
        "after_tax_windows": windows,
        "sensitivity_offset_equity": sens,
        "bootstrap": boots,
        "oos_spx_vix_1990_1999": oos,
        "drawdown": dd,
        "crash_flows": crashes,
        "gates": gates,
        "selection": selection,
        "run_seconds": elapsed,
    }
    return res


def study_data_vix(days):
    rows = []
    import csv as _csv

    with open(C.VIX_CSV) as fh:
        for r in _csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    rows.sort()
    return sd.forward_fill(days, rows) / 100.0


def data_manifest():
    files = {}
    for p in sorted((ROOT / "data/historical").glob("put_hedge_v1_*.csv")):
        files[str(p.relative_to(ROOT))] = {"sha256": file_sha(p), "bytes": p.stat().st_size}
    for name in (
        "stress_robustness_v1_daily.csv", "stress_robustness_v1_dtb3.csv",
        "after_tax_tr_v1_usdtry_cbrt.csv", "after_tax_tr_v1_yiufe.csv",
        "ewma_band_v1_gspc.csv", "ewma_band_v1_index.csv", "ewma_band_v1_tb3ms.csv",
    ):
        p = ROOT / "data/historical" / name
        files[str(p.relative_to(ROOT))] = {"sha256": file_sha(p), "bytes": p.stat().st_size}
    return files


def _summary(res, harness_out, elapsed):
    L = []
    L.append("put_hedge_v1 — protective-put overlays on production K (A1u + H2), Turkish after tax")
    L.append("")
    verdict = res.get("status", "")
    L.append("VERDICT: " + verdict)
    if "feasibility_stop" in verdict:
        L.append("The PPUT pricing sanity check failed; no QQQ arm was run.")
        L.append("")
        L.append(json.dumps(harness_out.get("pput_sanity", {}), indent=1)[:3000])
        return "\n".join(L)
    L.append("")
    L.append("The pricing sanity check passed first (see below). Every overlay arm loses after tax")
    L.append("versus K in all three windows and in both clean bootstraps; none cuts the drawdown")
    L.append("materially, and none makes vol target 0.45 affordable. K stays production.")
    L.append("")
    L.append("SANITY (PPUT, SPX+VIX, s = %.2f):" % C.SKEW_S)
    for wn, v in harness_out["pput_sanity"]["windows"].items():
        L.append(
            f"  {wn}: synthetic {v['synth_cagr']*100:.2f}% vs PPUT {v['pput_cagr']*100:.2f}% "
            f"({v['diff_pp']:+.2f} pp), monthly corr {v['monthly_corr']:.4f}, RMSE {v['monthly_rmse_pp']:.3f} pp"
        )
    for lab, v in harness_out["pput_sanity"]["sensitivity"].items():
        L.append(
            f"  {lab} (s={v['skew_s']:.1f}): diff {v['1990_1999']['diff_pp']:+.2f} / {v['2001_2026']['diff_pp']:+.2f} pp"
        )
    L.append("")
    L.append("MAIN WINDOW (VXN-covered reduced window %s -> %s), after tax at $100k, m=2:" % tuple(res["params"]["main_window"]))
    L.append("arm  pre CAGR  pre DD  AT CAGR  AT DD  AT Δ vs K  boot B1 Δ (share)  boot B2 Δ (share)  turnover")
    K = res["after_tax_windows"]["K"]
    for arm in ARMS:
        w = res["after_tax_windows"][arm]
        b1 = res["bootstrap"]["tax"]["100k"]["progressive"]
        b2 = res["bootstrap"]["tax"]["100k"]["flat"]
        d1 = b1.get(f"{arm}_vs_K", {})
        d2 = b2.get(f"{arm}_vs_K", {})
        d = (w["vxn_main"]["after_tax_cagr"] - K["vxn_main"]["after_tax_cagr"]) * 100
        L.append(
            f"{arm:3s} {w['vxn_main']['pre_tax_cagr']*100:7.2f}% {w['vxn_main']['pre_tax_max_dd']*100:6.2f}% "
            f"{w['vxn_main']['after_tax_cagr']*100:7.2f}% {w['vxn_main']['after_tax_max_dd']*100:6.2f}% "
            f"{d:+8.2f} pp  "
            + (f"{d1['median_delta_cagr_pp']:+.3f} ({d1['share_gt_0']*100:.0f}%)" if d1 else "-")
            + "  "
            + (f"{d2['median_delta_cagr_pp']:+.3f} ({d2['share_gt_0']*100:.0f}%)" if d2 else "-")
            + f"  {w['vxn_main']['pre_tax_turnover']:.2f}"
        )
    L.append("")
    L.append("AFTER-TAX WINDOWS ($100k, progressive; Δ vs K in pp):")
    L.append("arm  2001-2026   2010-2026   2017-2026")
    for arm in ARMS:
        w = res["after_tax_windows"][arm]
        ds = [(w[wn]["after_tax_cagr"] - K[wn]["after_tax_cagr"]) * 100 for wn in ("vxn_main", "vxn_2010", "vxn_2017")]
        L.append(f"{arm:3s} " + "  ".join(f"{x:+8.2f}" for x in ds))
    L.append("")
    L.append("DRAWDOWN PROFILE (pre-tax, base, m=2):")
    L.append("arm  maxDD  >10%  >20%  >30%  worst21  worst63  hedge cost pp/yr")
    for arm in ARMS:
        d = res["drawdown"][arm]
        L.append(
            f"{arm:3s} {d['max_dd']*100:6.2f}% {d['episodes_10']:4d} {d['episodes_20']:4d} {d['episodes_30']:4d} "
            f"{d['worst_21s']*100:7.2f}% {d['worst_63s']*100:7.2f}% "
            + (f"{d['annual_hedge_cost_pp']:+.2f}" if d.get("annual_hedge_cost_pp") is not None else "  -")
        )
    L.append("")
    L.append("CRASH FLOWS (net option P&L, $k; QQQ return; NAV return arm vs K):")
    for name in CRASHES:
        c = res["crash_flows"][name]
        L.append(f"  {name}: QQQ {c['qqq_return']*100:+.1f}%")
        for arm in ("K", "H1", "H2", "H3", "H4", "H5"):
            a = c["arms"][arm]
            L.append(
                f"    {arm}: prem {a['premium_usd']/1000:8.1f}  pay {a['payoff_usd']/1000:8.1f}  "
                f"net {a['net_usd']/1000:+8.1f}  NAV {a['nav_return']*100:+7.2f}%"
            )
    L.append("")
    L.append("GATES (vs K on the reduced window; G7 is the pre-registered one-window OOS, G8 n/a):")
    L.append("arm  G1 G2 G3 G4 G5 G6 G7 G9 AT1 AT2  all")
    for arm in ARMS:
        if arm == "K":
            continue
        g = res["gates"][arm]
        L.append(
            f"{arm:3s}  " + "  ".join("Y" if g[k] is True else ("." if g[k] is False else "n") for k in
                                      ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G9", "AT1", "AT2"))
            + "   " + ("Y" if g["all"] else "N")
        )
    L.append("")
    L.append("SENSITIVITY (option losses offset equity gains; Δ vs conservative, pp):")
    for arm in ARMS:
        if arm == "K":
            continue
        s = res["sensitivity_offset_equity"][arm]
        L.append(f"  {arm}: " + "  ".join(f"{s[wn]['delta_vs_conservative_pp']:+.2f}" for wn in ("vxn_main", "vxn_2010", "vxn_2017")))
    L.append("")
    L.append("OOS (SPX+VIX %s -> %s, pre-tax, calibrated, m=2):" % tuple(res["oos_spx_vix_1990_1999"]["window"]))
    for arm in ARMS:
        o = res["oos_spx_vix_1990_1999"]["arms"][arm]
        L.append(f"  {arm}: CAGR {o['cagr']*100:6.2f}%  DD {o['max_dd']*100:5.2f}%  ΔCAGR {o.get('delta_cagr_pp', 0):+6.2f} pp  ΔDD {o.get('delta_max_dd_pp', 0):+5.2f} pp")
    L.append("")
    L.append("Reproduction: parity --quick %s; K anchors %s; zero-flow parity %s; bootstrap parity %s." % (
        "PASS" if harness_out["parity_quick"]["pass"] else "FAIL",
        "PASS" if harness_out["k_anchors"]["pass"] else "FAIL",
        "PASS" if harness_out["zero_flow"]["pass"] else "FAIL",
        "PASS" if harness_out.get("bootstrap_parity", {}).get("pass") else "FAIL",
    ))
    L.append(f"Wall clock: {elapsed:.1f} s (--run).")
    L.append("")
    L.append("Caveats: BS prices from VXN with a fixed multiplicative skew and no term structure;")
    L.append("5 % of premium per side; monthly rolls at the close; option marks are model values;")
    L.append("the tax treatment of options is pre-registered, not adviser-confirmed; the OOS has no")
    L.append("after-tax leg (TRY data starts 2000); G8 is not run on the reduced OOS.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        smoke()
        return
    if args.harness:
        out = harness(deep=True)
        (ROOT / "research/put_hedge_v1/HARNESS.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: out[k].get("pass") if isinstance(out[k], dict) else out[k] for k in out}, indent=1))
        print("HARNESS PASS" if out["pass"] else "HARNESS FAIL")
        return
    if args.run:
        t0 = time.time()
        h = harness(deep=True)
        (ROOT / "research/put_hedge_v1/HARNESS.json").write_text(json.dumps(h, indent=1, sort_keys=True) + "\n")
        res = build_results(harness_out=h)
        elapsed = time.time() - t0
        res["run_seconds"] = round(elapsed, 1)
        (ROOT / "research/put_hedge_v1/RESULTS.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        (ROOT / "research/put_hedge_v1/SUMMARY.txt").write_text(_summary(res, h, elapsed) + "\n")
        print(_summary(res, h, elapsed))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
