"""exit_timing_v2 -- exit-leg tax timing under K (idea_survey_v4 I4), on the Numba engine.

The study is `exit_timing_v1` redone on `research/fast_engine`; see `PREREGISTRATION.md` (committed
before any non-control arm ran on real data). Arms: **K** (production control = E2 + R2 early
re-entry + A1u's gain band + H2's December deferral), **C0** (production R2, the report-only
reference row), **A1u**, **H2** (the control's two devices alone, for the deep reproduction block),
and the candidates **X1** (postpone a December exit-to-zero), **X2** (stage a gain-heavy
exit-to-zero down to the early-leg weight) and **X3** = X1 + X2.

Every arm runs production R2's target series; only the fill rule differs (`engine.RULE`). All
simulation, tax and bootstrap arithmetic runs in numba via `research.fast_engine` and this study's
kernel copy `research/exit_timing_v2/engine.py`; this module only builds target-weight arrays
(vectorised numpy) and aggregates.

Usage:

    python -m research.exit_timing_v2.screen --smoke     # synthetic identities + engine parity
    python -m research.exit_timing_v2.screen --harness   # parity --quick, K anchors, deep block
    python -m research.exit_timing_v2.screen --run       # every stage, gates, RESULTS.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from bisect import bisect_left
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.exit_timing_v2 import engine as en

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered
TOL = 1e-4  # 0.01 pp harness tolerance on the published pre-tax anchors
TAX_TOL_PP = 0.005  # after-tax anchor tolerance (pp)
M = fe_config.M
M_STRESS = fe_config.M_STRESS
SUBPERIODS = fe_study.SUBPERIODS
CONTROL = "K"  # production K = A1u + H2 (the control of THIS study)
REFERENCE = "C0"  # production R2 (K's execution layer off) -- the reference row
CANDIDATES = ("X1", "X2", "X3")
ARMS = (CONTROL, REFERENCE, "A1u", "H2") + CANDIDATES
RULE = en.RULE
DEC_TAIL = en.DEC_TAIL
X2_GAIN = en.X2_GAIN
X2_CAP_SESSIONS = en.X2_CAP_SESSIONS
START = fe_config.START
SEED, BLOCK = fe_config.SEED, fe_config.BLOCK
OOS_PATHS_PER_MARKET = en.OOS_PATHS_PER_MARKET

# after-tax / reporting stage constants (parent campaigns' conventions)
TAX_WINDOWS = fe_config.TAX_WINDOWS
TAX_CAPITALS = fe_config.TAX_CAPITALS
GATE_CAPITAL = fe_config.GATE_CAPITAL
PRE_CAPITAL = en.PRE_CAPITAL
TAX_BOOT_PATHS = fe_config.TAX_BOOT_PATHS
PRE_BOOT_PATHS = fe_config.PRE_BOOT_PATHS
E2_START_YEARS = fe_config.E2_START_YEARS
E1_RINDEX_START = fe_config.E1_RINDEX_START
E3_SEED, E3_BLOCKS, E3_PATHS = fe_config.E3_SEED, fe_config.E3_BLOCKS, fe_config.E3_PATHS
OOS_WINDOWS = fe_config.OOS_WINDOWS
MARKETS = fe_config.MARKETS
INSTRUMENT_FILES = fe_config.INSTRUMENT_FILES
INSTRUMENTS = fe_config.INSTRUMENTS
EXCLUDED_INSTRUMENTS = fe_config.EXCLUDED_INSTRUMENTS
E5_CAPITALS = ("10k", "30k", "300k", "1M")  # report-only
TAX_WINDOWS_LAST = "2017_01_to_end"
TAX_WINDOW_MAIN = "2000_03_to_2026_09"

# the named episodes the drawdown reporting of this study needs (PREREGISTRATION.md section 4.4)
EPISODES = (
    ("2000_02_dotcom", "2000-03-09", "2002-12-31"),
    ("2008_gfc", "2007-10-31", "2009-03-09"),
    ("dec2015_jan2016", "2015-12-01", "2016-01-31"),
    ("dec2018", "2018-12-01", "2018-12-31"),
    ("dec2021_jan2022", "2021-12-01", "2022-01-31"),
)

# gates (pre-registered; every candidate arm vs the control K)
G1_MIN_REL_CAGR_PP = -0.50
G1_MIN_CALMAR_MULT = 1.10
G2_MAX_DD_PP = 1.0
G3_MIN_CAGR_PP = -1.00
G4_MIN_WINS = 2
G5_MIN_MEDIAN_PP = 0.0
G7_MIN_MEDIAN_PP = 0.0
G7_MIN_WINDOWS = 5
G7_MIN_CAGR_PP = -0.50
G7_MAX_DD_PP = 3.0
G7_MAX_DD_HARD_PP = 8.0
G8_MIN_MEDIAN_PP = 0.0
G9_MAX_TURNOVER_MULT = 2.0
T1_MIN_DELTA_PP = 0.0
T2_MIN_MEDIAN_PP = 0.0
T2_MIN_SHARE = 0.55
BOOT_DELTAS = ("A1u", "H2", "K", "C0") + CANDIDATES

# published evidence (not inputs)
V3REF = ROOT / "research/gain_band_v3/RESULTS.json"
H2REF = HERE / "H2_ANCHORS.json"
KREF = ROOT / "research/band_h2_combo_v1/RESULTS.json"

# ----------------------------------------------------------------------------- published anchors
REF_E2 = {  # e2_integration_v1 DEVIATION.json, E2_prod_price_h0 (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json main_window: (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
TAX_ANCHORS = {  # series -> window -> (pre_tax cagr, progressive cagr, progressive max_dd), $100k
    "E2": {
        "2000_03_to_2026_09": (0.146715, 0.095340, 0.495643),
        "2010_01_to_end": (0.218852, 0.144968, 0.446920),
        "2017_01_to_end": (0.322564, 0.226932, 0.376052),
    },
    "R2": {
        "2000_03_to_2026_09": (0.154732, 0.101600, 0.467584),
        "2010_01_to_end": (0.221587, 0.147059, 0.437839),
        "2017_01_to_end": (0.325330, 0.228861, 0.362426),
    },
}
BOOT_ANCHOR = {
    "progressive": {"E2": 0.03079, "R2": 0.035155},
    "flat": {"E2": 0.035201, "R2": 0.039678},
}
REF_K = {  # (cagr, max_dd, turnover), pre-tax main window, nav-based cagr, m = 1/2/4
    ("base", 1): (0.160173, 0.463529, 5.456781),
    ("base", 2): (0.157649, 0.466698, 5.456136),
    ("base", 4): (0.152541, 0.472981, 5.456630),
    ("calibrated", 1): (0.154303, 0.464786, 5.456216),
    ("calibrated", 2): (0.151785, 0.467948, 5.456216),
    ("calibrated", 4): (0.146703, 0.474217, 5.456709),
}
REF_K_RUSD = {  # the R-USD main-window row, m = 2: (cagr, max_dd, turnover)
    "base": (0.157649, 0.466698, 5.456136),
    "calibrated": (0.151785, 0.467948, 5.456216),
}
TAX_ANCHOR_K = {  # after tax at $100k, base, m = 2, liquidated
    "2000_03_to_2026_09": (0.157615, 0.107533, 0.466698),
    "2010_01_to_end": (0.223435, 0.152354, 0.446272),
    "2017_01_to_end": (0.330215, 0.239720, 0.349774),
}
REF_K_COST_M4 = {  # after tax at $100k, m = 4: (after_tax, pre_tax, max_dd after tax)
    "2000_03_to_2026_09": (0.103539, 0.152472, 0.472981),
    "2010_01_to_end": (0.148202, 0.218076, 0.453889),
    "2017_01_to_end": (0.236749, 0.326395, 0.354250),
}
BOOT_ANCHOR_K = {  # clean bootstrap (seed 20261011, block 20), median cagr
    ("progressive", "100k"): 0.038577,
    ("progressive", "1M"): 0.027020,
    ("flat", "100k"): 0.040376,
    ("flat", "1M"): 0.040376,
}
REF_K_DEFERRALS = {  # main path, m = 2, base, $100k, progressive: (band, December)
    "2000_03_to_2026_09": (1836, 81),
    "2010_01_to_end": (1413, 60),
    "2017_01_to_end": (787, 29),
}


# ----------------------------------------------------------------------------- inputs / engine
def main_inputs_x(spread):
    """`fe_data.main_inputs` plus X2's context (the QQQ price series the rule is computed on)."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_config.EXP_Q)
    price = q[1]
    e2 = fe_signals.e2_up(price)[0]
    comps = fe_signals.early_components(price)
    vol_instr = fe_signals.vol_n(c)[0]
    w_e2 = fe_signals.arm_signals(price, e2, vol_instr, "E2", comps)[0][0]
    w_r2 = fe_signals.arm_signals(price, e2, vol_instr, "R2", comps)[0][0]
    return days, o, c, w_e2, w_r2, e2, en.exit_ctx(price, vol_instr, comps)


def arm_run(wants, arm, o, c, env, capital, i0, i1, mode="pre_tax", m=M, rule_index=True,
            trace=False, ctx=None, **kw):
    """One arm through the study's engine kernel (`engine.simulate_x`)."""
    if arm in CANDIDATES:
        if ctx is None:
            raise ValueError(f"arm {arm} needs the X2 context")
        return en.simulate_x(o, c, wants, env, capital, i0, i1, arm=arm, mode=mode, m=m,
                             rule_index=rule_index, trace=trace,
                             w_early=ctx["w_early"][0], hold=ctx["hold"][0], **kw)
    return en.simulate_x(o, c, wants, env, capital, i0, i1, arm=arm, mode=mode, m=m,
                         rule_index=rule_index, trace=trace, **kw)


def try_market(days):
    return fe_study.try_market(days)


def rnd(o):
    """`trend_robustness_v2.rnd`: round every float leaf to 6 decimals (RESULTS.json's precision)."""
    if isinstance(o, dict):
        return {k: rnd(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [rnd(v) for v in o]
    if isinstance(o, bool):
        return o
    if isinstance(o, (float, np.floating)):
        return round(float(o), 6)
    if isinstance(o, (int, np.integer)):
        return int(o)
    return o


# ----------------------------------------------------------------------------- stages
def pre_tax_main(arms=ARMS):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    env_usd = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, T)
    pre, pre_usd = {}, {}
    for md, spread in fe_config.FIN.items():
        d_, o_, c_, _we, wr_, _e, xc = main_inputs_x(spread)
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = arm_run(wr_, arm, o_, c_, env, PRE_CAPITAL, START, T, "pre_tax", m=m, ctx=xc)
                pre[md][f"m{m}"][arm] = fe_study.pre_stats(d_, START, T, r)
        for arm in arms:
            r = arm_run(wr_, arm, o_, c_, env_usd, PRE_CAPITAL, START, T, "pre_tax", m=M,
                        rule_index=False, ctx=xc)
            pre_usd[md][arm] = fe_study.pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(fe_sim.calendar_years(days[START - 1], days[-1]))}
    return pre, pre_usd


def after_tax_windows(capitals, arms=ARMS):
    """After tax at the given capitals (progressive, liquidated, base financing, m = 2)."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            i0, i1 = fe_study.window_indices(days, a, b)
            env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
            win[cap_label][wn] = {}
            for arm in arms:
                rq = arm_run(w_r2, arm, o, c, env, TAX_CAPITALS[cap_label], i0, i1, "progressive", ctx=ctx)
                rp = arm_run(w_r2, arm, o, c, env, 1.0, i0, i1, "pre_tax", ctx=ctx)
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
                    "deferred_exit": rq["deferred_exit"],
                    "staged_events": rq["staged_events"],
                    "staged_sessions": rq["staged_sessions"],
                    "staged_release_cap": rq["staged_release_cap"],
                    "staged_release_gate": rq["staged_release_gate"],
                    "staged_release_target": rq["staged_release_target"],
                    "exit_deferrals_by_year": rq["exit_deferrals_by_year"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "deferrals_by_year": rq["deferrals_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                    "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
                    "neg_gain_try_by_reason": rq["neg_gain_try_by_reason"],
                    "year_gain_try": rq["year_gain_try"],
                }
    return win


def cost_stress(arms=ARMS):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        cost[wn] = {}
        for arm in arms:
            rq = arm_run(w_r2, arm, o, c, env, TAX_CAPITALS[GATE_CAPITAL], i0, i1, "progressive",
                         m=M_STRESS, ctx=ctx)
            rp = arm_run(w_r2, arm, o, c, env, 1.0, i0, i1, "pre_tax", m=M_STRESS, ctx=ctx)
            cost[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                             "pre_tax_cagr": rp["cagr_usd_liquidated"],
                             "max_dd_after_tax": rq["max_dd_nav_usd"],
                             "turnover_annual": rp["turnover_annual"],
                             "postponements": rq["postponements"]}
    return cost


def window_ctx(price, o, c, y, comps=None):
    """(own E2 wants, own R2 wants, ctx) of a stand-alone window's price series."""
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + fe_config.EXTRA_FIN, fe_config.EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c) if comps is None else comps
    vol_instr = fe_signals.vol_n(sc)[0]
    _w_e2, w_r2 = fe_signals.wants_of(c, e2, vol_instr, comps)
    return so, sc, e2, w_r2[0], en.exit_ctx(c, vol_instr, comps)


def oos_window(days, o, c, y, start, arms=ARMS):
    """One OOS window: own E2 + own R2 early path, own 2x leg (calibrated, m = 2), R-USD rule."""
    so, sc, e2, w_r2, ctx = window_ctx(c, o, c, y)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = arm_run(w_r2, arm, so, sc, env, 1.0, start, len(days) - 1, "pre_tax", m=M,
                    rule_index=False, ctx=ctx)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


def oos_stages(arms=ARMS):
    """Six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule."""
    oos = {}
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, arms)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = fe_data.load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, arms)
    for wn in OOS_WINDOWS:
        for arm in arms:
            if arm == CONTROL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    return oos


def instrument_window(days, o, c, y, arms=ARMS, start=START):
    """Own E2 + own R2 early path on the instrument's adjusted close, own 2x leg (R-USD rule)."""
    so, sc, e2, w_r2, ctx = window_ctx(c, o, c, y)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {}
    for arm in arms:
        r = arm_run(w_r2, arm, so, sc, env, 1.0, start, len(days) - 1, "pre_tax", m=M,
                    rule_index=False, ctx=ctx)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


def instrument_stages_usd(arms=ARMS):
    """The 21 quality-passing ETFs, pre-tax, calibrated, m = 2, R-USD rule (+ quality record)."""
    inst, quality = {}, {}
    for key in sorted(INSTRUMENT_FILES):
        idays, io, ih, il, ic, iy = fe_data.load_instrument(key)
        frac, ok = fe_data.quality_ok(ih, il, io, ic)
        quality[key] = {"bad_frac": float(frac), "pass": bool(ok), "sessions": len(idays)}
        if key in EXCLUDED_INSTRUMENTS:
            continue
        r = instrument_window(idays, io, ic, iy, arms)
        for arm in arms:
            if arm == CONTROL:
                continue
            r[arm]["delta_cagr_pp"] = (r[arm]["cagr"] - r[CONTROL]["cagr"]) * 100
            r[arm]["delta_max_dd_pp"] = (r[arm]["max_dd"] - r[CONTROL]["max_dd"]) * 100
        inst[key] = r
    return inst, quality


def boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k", "1M"), modes=("progressive", "flat"), anchor_e2=True,
         deltas=BOOT_DELTAS, baselines=(CONTROL, REFERENCE), label="E1", window=None, quiet=False):
    """Clean bootstrap on the fixed 2015-09 -> 2026-09 calendar (the engine's own path draws)."""
    return en.boot_x(seed=seed, block=block, arms=arms, n_pre=n_pre, n_tax=n_tax, capitals=capitals,
                     modes=modes, anchor_e2=anchor_e2, deltas=deltas, baselines=baselines, label=label,
                     window=window, quiet=quiet)


def oos_bootstrap(arms=(CONTROL,) + CANDIDATES):
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, rng seed SEED, arms in order."""
    rng = np.random.default_rng(SEED)
    per = {k: en.boot_oos_market_x(k, rng, arms) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        out["per_market"][k] = {
            a: {"median_delta_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)} for a in arms
        }
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {"median_delta_pp": float(np.median(d) * 100),
                            "mean_delta_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    return out


def stage_e1(arms=ARMS):
    """E1: after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2."""
    out = {}
    for key in INSTRUMENTS:
        idays, io, ih, il, ic, iy = fe_data.load_instrument(key)
        start = max(START, bisect_left(idays, E1_RINDEX_START))
        so, sc, e2, w_r2, ctx = window_ctx(ic, io, ic, iy)
        env = fe_sim.RunEnv(idays, *try_market(idays), start, len(idays) - 1)
        rec = {"window": {"start": idays[start], "end": idays[-1], "sessions": len(idays) - start}}
        for arm in arms:
            rq = arm_run(w_r2, arm, so, sc, env, TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1,
                         "progressive", ctx=ctx)
            rp = arm_run(w_r2, arm, so, sc, env, PRE_CAPITAL, start, len(idays) - 1, "pre_tax", ctx=ctx)
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                        "pre_tax_cagr": rp["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"],
                        "terminal_usd": rq["terminal_usd_liquidated"],
                        "tax_usd": rq["tax_total_usd_at_payment_fx"],
                        "years": float(rq["years"]),
                        "postponements": rq["postponements"],
                        "blocked_band": rq["blocked_band"],
                        "deferred": rq["deferred"],
                        "sells_per_year": rq["sells"] / rq["years"]}
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
            rec[arm]["delta_pre_tax_pp"] = (rec[arm]["pre_tax_cagr"] - rec[CONTROL]["pre_tax_cagr"]) * 100
            rec[arm]["delta_max_dd_after_tax_pp"] = (
                rec[arm]["max_dd_after_tax"] - rec[CONTROL]["max_dd_after_tax"]) * 100
        out[key] = rec
    return out


def stage_e2(arms=ARMS):
    """E2: rolling January starts 2001..2021 on the QQQ main series, after tax at $100k."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect_left(days, f"{yr}-01-01")
        env = fe_sim.RunEnv(days, fx, ufe, i0, T)
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = arm_run(w_r2, arm, o, c, env, TAX_CAPITALS[GATE_CAPITAL], i0, T, "progressive", ctx=ctx)
            rp = arm_run(w_r2, arm, o, c, env, TAX_CAPITALS[GATE_CAPITAL], i0, T, "pre_tax", ctx=ctx)
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                        "pre_tax_cagr": rp["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"],
                        "terminal_usd": rq["terminal_usd_liquidated"],
                        "tax_usd": rq["tax_total_usd_at_payment_fx"],
                        "years": float(rq["years"]),
                        "sells_per_year": rq["sells"] / rq["years"]}
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


def stage_e3(arms=ARMS):
    """E3: fresh after-tax bootstraps -- seed 20261015, blocks 10 and 40, 1,000 paths, $100k."""
    out = {}
    for block in E3_BLOCKS:
        for mode in ("progressive", "flat"):
            b = boot(seed=E3_SEED, block=block, arms=arms, n_pre=0, n_tax=E3_PATHS,
                     capitals=(GATE_CAPITAL,), modes=(mode,), anchor_e2=False, deltas=BOOT_DELTAS,
                     baselines=(CONTROL, REFERENCE), label=f"E3 block {block} {mode}", quiet=True)
            out[f"block{block}_{mode}"] = b["tax"][GATE_CAPITAL][mode]
    return out


def stage_e5_decisions(arms=ARMS):
    """E5 (report only): fill-decision traces on the main path (pre-tax, $100k, m = 2)."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    traces = {arm: arm_run(w_r2, arm, o, c, env, PRE_CAPITAL, START, T, "pre_tax", trace=True, ctx=ctx)
              for arm in arms}
    out = {"window": {"start": days[START], "end": days[-1],
                      "sessions": int(len(traces[CONTROL]["_dec"]))},
           "sells": {}, "postponements": {}}
    for arm in arms:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"]),
                                     "exit": int(r["deferred_exit"]),
                                     "staged": int(r["staged_events"])}
    for a, b in (("X1", CONTROL), ("X2", CONTROL), ("X3", CONTROL), (CONTROL, REFERENCE),
                 (CONTROL, "A1u"), ("H2", REFERENCE)):
        da, db = traces[a]["_dec"], traces[b]["_dec"]
        out[f"{a}_vs_{b}"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            f"{a}_sells_{b}_holds": int(np.sum((da == 2) & (db != 2))),
            f"{a}_holds_{b}_sells": int(np.sum((da != 2) & (db == 2)))}
    return out


def episode_stage(arms=ARMS):
    """Per-arm max DD and return inside the five named episodes (report only)."""
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    dates = days[START - 1 : T + 1]  # aligned with the engine's NAV path (pre_stats' convention)
    out = {"whole_path": {}, "episodes": {}}
    runs = {arm: arm_run(w_r2, arm, o, c, env, PRE_CAPITAL, START, T, "pre_tax", ctx=ctx) for arm in arms}
    for arm in arms:
        r = runs[arm]
        out["whole_path"][arm] = {
            "cagr": float(r["cagr_usd_unliquidated"]),
            "max_dd": float(r["max_dd_nav_usd"]),
            "max_dd_delta_vs_K_pp": (float(r["max_dd_nav_usd"])
                                     - float(runs[CONTROL]["max_dd_nav_usd"])) * 100,
            "deferred_exit": int(r["deferred_exit"]),
            "staged_events": int(r["staged_events"]),
            "deferred": int(r["deferred"]),
            "blocked_band": int(r["blocked_band"])}
    for name, a, b in EPISODES:
        rec = {}
        for arm in arms:
            st = fe_study.seg(dates, runs[arm]["_nav"], a, b)
            kst = fe_study.seg(dates, runs[CONTROL]["_nav"], a, b)
            rec[arm] = {"from": st["from"], "to": st["to"], "total_return": st["total_return"],
                        "max_dd": st["max_dd"],
                        "delta_total_return_pp": (st["total_return"] - kst["total_return"]) * 100,
                        "delta_max_dd_pp": (st["max_dd"] - kst["max_dd"]) * 100,
                        "max_dd_K": kst["max_dd"]}
        out["episodes"][name] = rec
    return out


# ----------------------------------------------------------------------------- reproduction checks
def _flatten_numeric(d, prefix=""):
    """Every numeric leaf of a nested dict, path-labelled (bools and strings skipped)."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten_numeric(v, f"{prefix}{k}."))
        elif isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
            continue
        else:
            out[f"{prefix}{k}"] = float(v)
    return out


def _compare_subtree(label, mine, theirs, cells, only_mine, only_theirs):
    """Compare every numeric leaf present in both subtrees; record one-sided keys."""
    fm, ft = _flatten_numeric(mine), _flatten_numeric(theirs)
    for k in sorted(set(fm) | set(ft)):
        if k not in fm:
            only_theirs.append(f"{label}.{k}")
        elif k not in ft:
            only_mine.append(f"{label}.{k}")
        else:
            cells.append((f"{label}.{k}", fm[k], ft[k]))


# v3's control was C0 (production R2); this study's control is K. The control-relative *delta*
# cells are therefore recomputed against the C0 row of this study's own results before they are
# compared with v3's published deltas. Every absolute cell is compared exactly as published.
DELTA_REBASE = {
    "delta_after_tax_pp": ("after_tax_cagr", 100.0),
    "delta_pre_tax_pp": ("pre_tax_cagr", 100.0),
    "delta_max_dd_after_tax_pp": ("max_dd_after_tax", 100.0),
    "delta_cagr_pp": ("cagr", 100.0),
    "delta_max_dd_pp": ("max_dd", 100.0),
}


def _rebase_deltas(sub, ref_sub):
    """The arm's cell block with its control-relative deltas rebased on the reference C0 row."""
    out = dict(sub)
    for dk, (fk, mult) in DELTA_REBASE.items():
        if dk in out and fk in sub and fk in ref_sub:
            out[dk] = (sub[fk] - ref_sub[fk]) * mult
    return out


def reference_checks(res, full=True):
    """Reproduce the parent campaigns' published cells with this study's code.

    (a) A1u: every numeric leaf of every A1u cell that `gain_band_v3` published (pre-tax main
        window, after-tax windows at all five capitals, m = 4 cost stress, the seed-20261011 clean
        bootstrap, the six OOS windows, the 21 R-USD instruments, E1/E2/E3) is compared against
        `research/gain_band_v3/RESULTS.json` (both sides store round(x, 6), hence 2e-6). A mismatch
        is a bug in this study.
    (b) H2: the cells `tax_harvest_v2` published for H2 (pre-tax main window at m = 1/2, the three
        after-tax windows at $100k, the m = 4 cost stress, the seed-20261011 bootstrap at
        $100k/$1M) are compared against `H2_ANCHORS.json`. `full=False` compares only the cheap
        cells (pre-tax main, after-tax windows at the gate capital, cost stress, bootstrap).
    """
    v3 = json.loads(V3REF.read_text())
    h2 = json.loads(H2REF.read_text())
    cells, only_mine, only_theirs, sections = [], [], [], []

    def add(label, mine, theirs):
        cells.append((label, float(mine), float(theirs)))

    b3 = v3["bootstrap_reference_seed20261011"]
    br = res["boot_ref"]
    sections.append("pre_tax_main")
    for md in fe_config.FIN:
        for m in ("m1", "m2", "m4"):
            _compare_subtree(f"pre_tax_main.{md}.{m}.A1u", res["pre"][md][m]["A1u"],
                             v3["pre_tax_main"][md][m]["A1u"], cells, only_mine, only_theirs)
        for arm in ("A1u",):
            if arm in v3["pre_tax_main_r_usd"][md]:
                _compare_subtree(f"pre_usd.{md}.{arm}", res["pre_usd"][md][arm],
                                 v3["pre_tax_main_r_usd"][md][arm], cells, only_mine, only_theirs)
    sections.append("after_tax_windows")
    for cl in TAX_CAPITALS:
        if cl not in res["windows"]:
            continue
        for wn in TAX_WINDOWS:
            _compare_subtree(f"after_tax.{cl}.{wn}.A1u", res["windows"][cl][wn]["A1u"],
                             v3["after_tax_windows"][cl][wn]["A1u"], cells, only_mine, only_theirs)
    sections.append("cost_stress_m4")
    for wn in TAX_WINDOWS:
        _compare_subtree(f"cost.{wn}.A1u", res["cost"][wn]["A1u"], v3["cost_stress_m4"][wn]["A1u"],
                         cells, only_mine, only_theirs)
    sections.append("clean_bootstrap")
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            for key in ("A1u", "A1u_vs_C0"):
                _compare_subtree(f"boot.tax.{cl}.{mode}.{key}", br["tax"][cl][mode][key],
                                 b3["tax"][cl][mode][key], cells, only_mine, only_theirs)
    for md in fe_config.FIN:
        _compare_subtree(f"boot.pre.{md}.A1u", br["pre"][md]["A1u"], b3["pre"][md]["A1u"],
                         cells, only_mine, only_theirs)
    for mode in ("progressive", "flat"):
        add(f"boot.anchor_e2.{mode}.cagr_median", br["anchor_e2_100k"][mode]["cagr_median"],
            b3["anchor_e2_100k"][mode]["cagr_median"])
    if full:
        sections.append("e1_e2_e3_oos_instruments")
        n_rebased = 0
        for key in INSTRUMENTS:
            for arm in ("A1u", REFERENCE):
                if arm not in v3["E1_after_tax_etfs_rindex"][key]:
                    continue
                _compare_subtree(
                    f"e1.{key}.{arm}",
                    _rebase_deltas(res["e1"][key][arm], res["e1"][key][REFERENCE]),
                    v3["E1_after_tax_etfs_rindex"][key][arm], cells, only_mine, only_theirs)
                n_rebased += 1
        for yr in E2_START_YEARS:
            for arm in ("A1u", REFERENCE):
                if arm not in v3["E2_rolling_starts"][str(yr)]:
                    continue
                _compare_subtree(
                    f"e2.{yr}.{arm}",
                    _rebase_deltas(res["e2"][str(yr)][arm], res["e2"][str(yr)][REFERENCE]),
                    v3["E2_rolling_starts"][str(yr)][arm], cells, only_mine, only_theirs)
                n_rebased += 1
        for key in sorted(res["e3"]):
            _compare_subtree(f"e3.{key}.A1u_vs_C0", res["e3"][key]["A1u_vs_C0"],
                             v3["E3_fresh_bootstraps"][key]["A1u_vs_C0"], cells, only_mine, only_theirs)
        for wn in OOS_WINDOWS:
            for arm in ("A1u", REFERENCE):
                if arm not in v3["oos"][wn]:
                    continue
                _compare_subtree(
                    f"oos.{wn}.{arm}",
                    _rebase_deltas(res["oos"][wn][arm], res["oos"][wn][REFERENCE]),
                    v3["oos"][wn][arm], cells, only_mine, only_theirs)
                n_rebased += 1
        for key in res["instruments"]:
            for arm in ("A1u", REFERENCE):
                if arm not in v3["instruments"][key]:
                    continue
                _compare_subtree(
                    f"instruments.{key}.{arm}",
                    _rebase_deltas(res["instruments"][key][arm], res["instruments"][key][REFERENCE]),
                    v3["instruments"][key][arm], cells, only_mine, only_theirs)
                n_rebased += 1
        sections.append(f"delta_rebase_on_C0_{n_rebased}_subtrees")

    # ---- H2 against the tagged campaign's published cells
    h2_map = {"cagr": "cagr_liquidated", "max_dd": "max_dd", "turnover_annual": "turnover_annual"}
    for md in fe_config.FIN:
        for m in ("m1", "m2"):
            a, b = res["pre"][md][m]["H2"], h2["pre_tax_main"][md][m]["H2"]
            for tk, mk in h2_map.items():
                add(f"h2.pre.{md}.{m}.{tk}", a[mk], b[tk])
            add(f"h2.pre.{md}.{m}.deferrals", a["deferred"], b["deferrals"])
    for wn in TAX_WINDOWS:
        a, b = res["windows"][GATE_CAPITAL][wn]["H2"], h2["after_tax_windows_100k"][wn]["H2"]
        for tk in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax"):
            add(f"h2.window100k.{wn}.{tk}", a[tk], b[tk])
        add(f"h2.window100k.{wn}.deferrals", a["deferred"], b["deferrals"])
    for wn in TAX_WINDOWS:
        a, b = res["cost"][wn]["H2"], h2["cost_stress_m4_100k"][wn]["H2"]
        for tk in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax", "turnover_annual"):
            add(f"h2.cost_m4.{wn}.{tk}", a[tk], b[tk])
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            a, b = br["tax"][cl][mode], h2["bootstrap_clean_seed20261011"][cl][mode]
            add(f"h2.boot.{cl}.{mode}.cagr_median", a["H2"]["cagr_median"], b["H2_cagr_median"])
            add(f"h2.boot.{cl}.{mode}.R2_cagr_median", a[REFERENCE]["cagr_median"], b["R2_cagr_median"])
            for k in ("median_delta_cagr_pp", "mean_delta_cagr_pp", "share_gt_0",
                      "p05_delta_cagr_pp", "p95_delta_cagr_pp"):
                add(f"h2.boot.{cl}.{mode}.H2_vs_R2.{k}", a["H2_vs_C0"][k], b["H2_vs_R2"][k])

    bad = [(cell, mine, theirs, abs(mine - theirs)) for cell, mine, theirs in cells
           if abs(mine - theirs) > 2e-6]
    return {"n_cells": len(cells), "n_mismatch": len(bad), "tolerance": 2e-6,
            "full": bool(full), "sections": sections,
            "max_abs_diff": float(max((abs(m - t) for _, m, t in cells), default=0.0)),
            "n_keys_only_in_mine": len(only_mine), "n_keys_only_in_v3": len(only_theirs),
            "keys_only_in_mine": only_mine[:20], "keys_only_in_v3": only_theirs[:20],
            "mismatches": [{"cell": c, "mine": m, "reference": t, "diff": d}
                           for c, m, t, d in bad][:400],
            "pass": bool(not bad)}


# ----------------------------------------------------------------------------- harness
def engine_parity_cells():
    """This study's own parity cells: the X options off reproduce the engine bit-for-bit.

    (a) `engine.simulate_x` vs `fe_sim.simulate`: every base arm x mode on the real main window,
        NAV path and every scalar key required to be **exactly** equal;
    (b) `engine.boot_x` vs `fe_boot.boot` (200 pre-tax / 200 after-tax paths, arms C0/A1u/H2/K,
        the engine's own published cells): every shared numeric leaf exactly equal. `boot_x` adds
        the pre-tax paired `{arm}_vs_{ctrl}` cells (its G5 input) which the engine's `boot` does not
        emit; those are the only extras, and they are checked to be the *only* difference.
    """
    days, o, c, w_e2, w_r2, e2, _ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    n_pairs, bad = 0, []
    max_nav_diff = 0.0
    key_diffs = []
    for arm in en.ARMS:
        for mode in ("pre_tax", "progressive", "flat"):
            r0 = fe_sim.simulate(o, c, w_r2, env, PRE_CAPITAL, START, T, arm=arm, mode=mode, m=M)
            r1 = arm_run(w_r2, arm, o, c, env, PRE_CAPITAL, START, T, mode, m=M,
                         ctx={"w_early": np.zeros((1, 0)), "hold": np.zeros((1, 0), dtype=bool)})
            n_pairs += 1
            d = float(np.max(np.abs(r0["_nav"] - r1["_nav"])))
            max_nav_diff = max(max_nav_diff, d)
            if d != 0.0:
                bad.append(f"{arm}/{mode}: NAV {d:g}")
            for k in r0:
                if k in ("_nav", "_dec"):
                    continue
                if r0[k] != r1[k]:
                    key_diffs.append(f"{arm}/{mode}/{k}")
    base_deltas = ("A1u", "H2", "K", "C0")
    b0 = fe_boot.boot(seed=SEED, block=BLOCK, arms=en.ARMS, n_pre=200, n_tax=200,
                      capitals=("100k",), modes=("progressive", "flat"), deltas=base_deltas,
                      baselines=(CONTROL, REFERENCE), label="parity", quiet=True)
    b1 = en.boot_x(seed=SEED, block=BLOCK, arms=en.ARMS, n_pre=200, n_tax=200,
                   capitals=("100k",), modes=("progressive", "flat"), deltas=base_deltas,
                   baselines=(CONTROL, REFERENCE), label="parity", quiet=True)
    fa, fb = _flatten_numeric(b0), _flatten_numeric(b1)
    shared = sorted(set(fa) & set(fb))
    boot_bad = [k for k in shared if fa[k] != fb[k]]
    extras = sorted(set(fb) - set(fa))
    extras_expected = all(k.startswith(("pre.base.", "pre.calibrated.")) and "_vs_" in k for k in extras)
    return {
        "simulate_pairs": n_pairs,
        "simulate_max_abs_nav_diff": max_nav_diff,
        "simulate_key_diffs": key_diffs[:10],
        "boot_shared_cells": len(shared),
        "boot_mismatches": boot_bad[:10],
        "boot_extra_cells": len(extras),
        "boot_extra_cells_are_pre_tax_pairs_only": bool(extras_expected),
        "pass": bool(not bad and not key_diffs and not boot_bad and extras_expected),
    }


def harness(run_boot=True, deep=False, parity_subprocess=True):
    """Control reproduction on real data (no candidate arm) + the engine's own parity.

    The study's control is production **K**, so the label "K" is asserted against the K cells
    `band_h2_combo_v1` published (pre-tax CAGR / max DD / turnover at m = 1/2/4 x base/calibrated,
    the R-USD row, the three after-tax windows at $100k with their max DD, the m = 4 cost stress
    and the clean-bootstrap medians). The "C0" label (production R2) is asserted against
    `fast_reentry_v1`'s R2 anchors and the pure-E2 series against `e2_integration_v1`'s. The
    `--harness` deep part reproduces the parent campaigns' published cells cell-by-cell and stores
    the clean bootstrap that `run()` reuses.
    """
    checks, bad, timings = {}, 0, {}
    days, o, c, w_e2, w_r2, e2, ctx = main_inputs_x(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    # ---- 0. the engine's own parity CLI (must PASS)
    parity = {"ran": False}
    if parity_subprocess:
        cmd = [sys.executable, "-m", "research.fast_engine.parity", "--quick"]
        t0 = time.time()
        pr = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT)})
        last = [ln for ln in pr.stdout.splitlines() if ln.startswith("parity ")]
        wall_s = time.time() - t0
        # RESULTS.json must be byte-identical across runs, so no wall-clock value is stored here;
        # the durations are reported in SUMMARY.txt.
        verdict = re.sub(r"in [0-9.]+s", "in <wall>s", (last[-1] if last else pr.stdout[-400:]))
        parity = {"ran": True, "cmd": " ".join(cmd), "returncode": int(pr.returncode),
                  "verdict": verdict, "pass": bool(pr.returncode == 0)}
        # the CLI rewrites research/fast_engine/PARITY.json; restore it so the engine's tree is
        # left exactly as it was (this study never edits research/fast_engine)
        subprocess.run(["git", "checkout", "--", "research/fast_engine/PARITY.json"], cwd=str(ROOT),
                       capture_output=True, text=True)
        parity["parity_json_restored"] = True
        timings["engine_parity_cli_s"] = round(wall_s, 1)
        if not parity["pass"]:
            bad += 1
    checks["engine_parity_cli"] = parity
    # ---- 1. this study's kernel parity (options off == fe_sim / fe_boot, bit-for-bit)
    mine = engine_parity_cells()
    checks["x_options_off_equals_engine"] = mine
    if not mine["pass"]:
        bad += 1
    # ---- 2. the control: production K (label "K"), pre-tax m = 1/2/4 and the R-USD row
    for md, spread in fe_config.FIN.items():
        d_, o_, c_, _we, wr_, _e, xc = main_inputs_x(spread)
        for m in (1, 2, M_STRESS):
            r = arm_run(wr_, CONTROL, o_, c_, env, 1.0, START, T, "pre_tax", m=m, ctx=xc)
            for key, val in (("cagr_usd_unliquidated", REF_K[(md, m)][0]),
                             ("max_dd_nav_usd", REF_K[(md, m)][1]),
                             ("turnover_annual", REF_K[(md, m)][2])):
                got = float(r[key])
                checks[f"K_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
                if abs(got - val) > TOL:
                    bad += 1
        r = arm_run(wr_, CONTROL, o_, c_, fe_sim.RunEnv(d_, *fe_data.usd_market(d_), START, T), 1.0,
                    START, T, "pre_tax", m=M, rule_index=False, ctx=xc)
        for key, val in (("cagr_usd_unliquidated", REF_K_RUSD[md][0]),
                         ("max_dd_nav_usd", REF_K_RUSD[md][1]),
                         ("turnover_annual", REF_K_RUSD[md][2])):
            got = float(r[key])
            checks[f"K_{md}_rusd_{key}"] = {"reference": val, "reproduced": got, "diff": got - val}
            if abs(got - val) > TOL:
                bad += 1
    # ---- 3. reference rows: production R2 (label "C0") and the pure-E2 fidelity series
    for md, spread in fe_config.FIN.items():
        d_, o_, c_, we_, wr_, e_, _xc = main_inputs_x(spread)
        for label, wants, ref in (("E2", we_, REF_E2), ("C0", wr_, REF_R2)):
            for m in (1, 2):
                r = arm_run(wants, label, o_, c_, env, 1.0, START, T, "pre_tax", m=m)
                for key, val in (("cagr_usd_unliquidated", ref[(md, m)][0]),
                                 ("max_dd_nav_usd", ref[(md, m)][1]),
                                 ("turnover_annual", ref[(md, m)][2])):
                    got = float(r[key])
                    checks[f"{label}_{md}_m{m}_{key}"] = {"reference": val, "reproduced": got,
                                                          "diff": got - val}
                    if abs(got - val) > TOL:
                        bad += 1
    # ---- 4. the explicit K / R2 anchor blocks
    k_anchors = {"reference": {
        "pre_tax": "research/band_h2_combo_v1/RESULTS.json (K): 15.7649 / 15.1785 % CAGR, max DD "
                   "46.6698 / 46.7948 %, turnover 5.456136",
        "after_tax": "research/band_h2_combo_v1/RESULTS.json (K) at $100k: 10.753 / 15.235 / 23.972 % "
                     "CAGR, max DD 46.670 / 44.627 / 34.977 %",
        "bootstrap": "research/band_h2_combo_v1/RESULTS.json (K), seed 20261011 block 20, 1000 paths: "
                     "B1-recent 3.8577 %, B2-flat 4.0376 %"},
        "pre_tax": {f"{md}_m{m}": {
            "reference_cagr": REF_K[(md, m)][0],
            "reproduced_cagr": checks[f"K_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_K[(md, m)][1],
            "reproduced_max_dd": checks[f"K_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_K[(md, m)][2],
            "reproduced_turnover": checks[f"K_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in fe_config.FIN for m in (1, 2, M_STRESS)},
        "after_tax_100k": {}}
    r2_anchors = {"reference": {
        "pre_tax": "fast_reentry_v1/RESULTS.json main_window (R2): 15.48 / 14.90 % CAGR, 46.76 % max DD",
        "after_tax": "vt_level_tax_v1 + r2_tbill_combo_v1 at $100k (R2): 10.16 / 14.71 / 22.89 % CAGR, "
                     "max DD 46.76 / 43.78 / 36.24 %"},
        "pre_tax": {f"{md}_m{m}": {
            "reference_cagr": REF_R2[(md, m)][0],
            "reproduced_cagr": checks[f"C0_{md}_m{m}_cagr_usd_unliquidated"]["reproduced"],
            "reference_max_dd": REF_R2[(md, m)][1],
            "reproduced_max_dd": checks[f"C0_{md}_m{m}_max_dd_nav_usd"]["reproduced"],
            "reference_turnover": REF_R2[(md, m)][2],
            "reproduced_turnover": checks[f"C0_{md}_m{m}_turnover_annual"]["reproduced"]}
            for md in fe_config.FIN for m in (1, 2)},
        "after_tax_100k": {}}
    # the control is not the E2 anchor series (the gain_band_v1 bug)
    k_run = arm_run(w_r2, CONTROL, o, c, env, 1.0, START, T, "pre_tax", ctx=ctx)
    e2_run = arm_run(w_e2, "E2", o, c, env, 1.0, START, T, "pre_tax")
    checks["control_is_not_the_E2_anchor_series"] = {
        "max_abs_nav_diff_vs_E2": float(np.max(np.abs(k_run["_nav"] - e2_run["_nav"]))),
        "cagr_control_K": k_run["cagr_usd_unliquidated"],
        "cagr_E2_anchor": e2_run["cagr_usd_unliquidated"]}
    if not checks["control_is_not_the_E2_anchor_series"]["max_abs_nav_diff_vs_E2"] > 0.01:
        bad += 1
    # the candidates must differ from K (a device that acts cannot reproduce the control's path)
    for arm in CANDIDATES:
        r = arm_run(w_r2, arm, o, c, env, 1.0, START, T, "pre_tax", ctx=ctx)
        checks[f"{arm}_nav_differs_from_K"] = {
            "max_abs_nav_diff": float(np.max(np.abs(r["_nav"] - k_run["_nav"])))}
    tax = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        ewn = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        tax[wn] = {}
        for label, wants, series in (("E2", w_e2, "E2"), ("C0", w_r2, "R2")):
            rp = arm_run(wants, label, o, c, ewn, 100_000.0, i0, i1, "pre_tax")
            rq = arm_run(wants, label, o, c, ewn, 100_000.0, i0, i1, "progressive")
            ref = TAX_ANCHORS[series][wn]
            tax[wn][label] = {
                "pre_tax": {"reference": ref[0], "reproduced": rp["cagr_usd_liquidated"],
                            "diff_pp": (rp["cagr_usd_liquidated"] - ref[0]) * 100},
                "progressive": {"reference": ref[1], "reproduced": rq["cagr_usd_liquidated"],
                                "diff_pp": (rq["cagr_usd_liquidated"] - ref[1]) * 100},
                "progressive_max_dd": {"reference": ref[2], "reproduced": rq["max_dd_nav_usd"],
                                       "diff_pp": (rq["max_dd_nav_usd"] - ref[2]) * 100}}
            for v in tax[wn][label].values():
                if abs(v["diff_pp"]) > TAX_TOL_PP:
                    bad += 1
        r2_anchors["after_tax_100k"][wn] = {
            "reference": list(TAX_ANCHORS["R2"][wn]),
            "reproduced": [tax[wn]["C0"]["pre_tax"]["reproduced"],
                           tax[wn]["C0"]["progressive"]["reproduced"],
                           tax[wn]["C0"]["progressive_max_dd"]["reproduced"]],
            "pass": bool(all(abs(v["diff_pp"]) <= TAX_TOL_PP for v in tax[wn]["C0"].values()))}
        rp = arm_run(w_r2, CONTROL, o, c, ewn, 100_000.0, i0, i1, "pre_tax", ctx=ctx)
        rq = arm_run(w_r2, CONTROL, o, c, ewn, 100_000.0, i0, i1, "progressive", ctx=ctx)
        rq4 = arm_run(w_r2, CONTROL, o, c, ewn, 100_000.0, i0, i1, "progressive", m=M_STRESS, ctx=ctx)
        refk = TAX_ANCHOR_K[wn]
        k_anchors["after_tax_100k"][wn] = {
            "reference": list(refk), "reference_cost_m4": list(REF_K_COST_M4[wn]),
            "reproduced": [rp["cagr_usd_liquidated"], rq["cagr_usd_liquidated"], rq["max_dd_nav_usd"]],
            "reproduced_cost_m4": [rq4["cagr_usd_liquidated"], rq4["max_dd_nav_usd"]],
            "deferrals": [rq["blocked_band"], rq["deferred"]],
            "reference_deferrals": list(REF_K_DEFERRALS[wn]),
            "diff_pp": [(rp["cagr_usd_liquidated"] - refk[0]) * 100,
                        (rq["cagr_usd_liquidated"] - refk[1]) * 100,
                        (rq["max_dd_nav_usd"] - refk[2]) * 100],
            "pass": bool(all(abs((x - y) * 100) <= TAX_TOL_PP for x, y in
                             zip([rp["cagr_usd_liquidated"], rq["cagr_usd_liquidated"],
                                  rq["max_dd_nav_usd"]], refk))
                         and rq["blocked_band"] == REF_K_DEFERRALS[wn][0]
                         and rq["deferred"] == REF_K_DEFERRALS[wn][1])}
        if not k_anchors["after_tax_100k"][wn]["pass"]:
            bad += 1
    r2_anchors["pass"] = bool(
        all(abs(checks[f"C0_{md}_m{m}_{k}"]["diff"]) <= TOL for md in fe_config.FIN for m in (1, 2)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and all(r2_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not r2_anchors["pass"]:
        bad += 1
    k_anchors["pass"] = bool(
        all(abs(checks[f"K_{md}_m{m}_{k}"]["diff"]) <= TOL for md in fe_config.FIN for m in (1, 2, M_STRESS)
            for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and all(abs(checks[f"K_{md}_rusd_{k}"]["diff"]) <= TOL for md in fe_config.FIN
                for k in ("cagr_usd_unliquidated", "max_dd_nav_usd", "turnover_annual"))
        and all(k_anchors["after_tax_100k"][wn]["pass"] for wn in TAX_WINDOWS))
    if not k_anchors["pass"]:
        bad += 1
    # ---- 5. the anchor constants must be the published cells, not a transcription slip
    pub = json.loads(KREF.read_text())
    kref_bad = []
    for md in fe_config.FIN:
        for m in (1, 2, M_STRESS):
            c = pub["pre_tax_main"][md][f"m{m}"]["K"]
            for got, want in ((REF_K[(md, m)][0], c["cagr"]), (REF_K[(md, m)][1], c["max_dd"]),
                              (REF_K[(md, m)][2], c["turnover_annual"])):
                if abs(got - want) > 1e-9:
                    kref_bad.append(f"pre_tax_main.{md}.m{m}")
        cu = pub["pre_tax_main_r_usd"][md]["K"]
        for got, want in ((REF_K_RUSD[md][0], cu["cagr"]), (REF_K_RUSD[md][1], cu["max_dd"]),
                          (REF_K_RUSD[md][2], cu["turnover_annual"])):
            if abs(got - want) > 1e-9:
                kref_bad.append(f"pre_tax_main_r_usd.{md}")
    for wn in TAX_WINDOWS:
        c = pub["after_tax_windows"]["100k"][wn]["K"]
        for got, want in ((TAX_ANCHOR_K[wn][0], c["pre_tax_cagr"]),
                          (TAX_ANCHOR_K[wn][1], c["after_tax_cagr"]),
                          (TAX_ANCHOR_K[wn][2], c["max_dd_after_tax"])):
            if abs(got - want) > 1e-9:
                kref_bad.append(f"after_tax_windows.100k.{wn}")
        if (REF_K_DEFERRALS[wn][0], REF_K_DEFERRALS[wn][1]) != (c["blocked_band"], c["deferred"]):
            kref_bad.append(f"deferrals.{wn}")
        c4 = pub["cost_stress_m4"][wn]["K"]
        for got, want in ((REF_K_COST_M4[wn][0], c4["after_tax_cagr"]),
                          (REF_K_COST_M4[wn][1], c4["pre_tax_cagr"]),
                          (REF_K_COST_M4[wn][2], c4["max_dd_after_tax"])):
            if abs(got - want) > 1e-9:
                kref_bad.append(f"cost_stress_m4.{wn}")
    for mode in ("progressive", "flat"):
        for cl in ("100k", "1M"):
            c = pub["bootstrap_clean_seed20261011"]["tax"][cl][mode]["K"]["cagr_median"]
            if abs(BOOT_ANCHOR_K[(mode, cl)] - c) > 1e-9:
                kref_bad.append(f"bootstrap.{cl}.{mode}")
    checks["k_anchor_constants_match_published"] = {"n_mismatch": len(kref_bad),
                                                   "mismatch_labels": kref_bad[:10]}
    if kref_bad:
        bad += 1
    boot_control = {}
    if run_boot:
        b = boot(n_pre=TAX_BOOT_PATHS, n_tax=TAX_BOOT_PATHS, capitals=("100k",), arms=ARMS,
                 deltas=BOOT_DELTAS, baselines=(CONTROL, REFERENCE), quiet=True)
        for mode in ("progressive", "flat"):
            boot_control[mode] = {"R2": b["tax"]["100k"][mode][REFERENCE]["cagr_median"],
                                  "K": b["tax"]["100k"][mode][CONTROL]["cagr_median"],
                                  "E2": b["anchor_e2_100k"][mode]["cagr_median"]}
            for label, ref in (("R2", BOOT_ANCHOR[mode]["R2"]), ("K", BOOT_ANCHOR_K[(mode, "100k")]),
                               ("E2", BOOT_ANCHOR[mode]["E2"])):
                if abs(boot_control[mode][label] - ref) > 0.0010:
                    bad += 1
        boot_control["pre_base"] = {a: b["pre"]["base"][a]["cagr_median"] for a in ARMS}
        boot_control["pre_cal"] = {a: b["pre"]["calibrated"][a]["cagr_median"] for a in ARMS}
    out = {"reproduction_checks": checks, "n_outside_tolerance": bad, "after_tax_anchors": tax,
           "k_anchors": k_anchors, "r2_anchors": r2_anchors, "bootstrap_control": boot_control,
           "pass": bool(bad == 0), "timings": timings}
    if deep:
        out["deep"] = harness_deep()
        if not out["deep"]["pass"]:
            out["pass"] = False
    return out


def harness_deep():
    """The study's arms against the parent campaigns' published cells, plus the clean bootstrap
    (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths) that `run()` reuses."""
    pre, pre_usd = pre_tax_main(ARMS)
    win = after_tax_windows((GATE_CAPITAL,), ARMS)
    cost = cost_stress(ARMS)
    print(f"  deep harness: clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)",
          flush=True)
    boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                    capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=BOOT_DELTAS,
                    baselines=(CONTROL, REFERENCE), label="clean", quiet=True)
    ref = reference_checks({"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost,
                            "boot_ref": boot_ref}, full=False)
    ref["boot_ref"] = boot_ref
    return ref


# ----------------------------------------------------------------------------- gates
def gates_exit(res):
    """Every candidate arm's gate block against the control K (PREREGISTRATION.md section 6):
    the ewma_band_v1 pre-tax gates G1-G9 with K as the control, and the after-tax gates T1-T3
    ($100k: all three windows, then the two clean bootstraps with the 55 % share floor). No
    non-inferiority relaxation: these arms change exposure, so a DD or bootstrap loss is a loss.
    """
    pre, win = res["pre"], res["windows"][GATE_CAPITAL]
    bt = res["boot_ref"]
    g = {}
    for arm in CANDIDATES:
        r = {}
        for mkey in ("m1", "m2"):
            det, ok = {}, True
            for mdl in ("base", "calibrated"):
                a, k = pre[mdl][mkey][arm], pre[mdl][mkey][CONTROL]
                cagr_ok = bool(a["cagr"] >= k["cagr"])
                calmar_ok = bool(a["calmar"] >= G1_MIN_CALMAR_MULT * k["calmar"]
                                 and (a["cagr"] - k["cagr"]) * 100 >= G1_MIN_REL_CAGR_PP)
                det[mdl] = {"cagr": a["cagr"], "cagr_K": k["cagr"],
                            "delta_pp": (a["cagr"] - k["cagr"]) * 100,
                            "calmar": a["calmar"], "calmar_K": k["calmar"],
                            "pass": bool(cagr_ok or calmar_ok)}
                ok = ok and det[mdl]["pass"]
            r[f"G1_detail_{mkey}"] = det
            r["G6" if mkey == "m1" else "G1"] = bool(ok)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            a, k = pre[mdl]["m2"][arm], pre[mdl]["m2"][CONTROL]
            d = (a["max_dd"] - k["max_dd"]) * 100
            det[mdl] = {"max_dd": a["max_dd"], "max_dd_K": k["max_dd"], "delta_pp": d,
                        "pass": bool(d <= G2_MAX_DD_PP + 1e-9)}
            ok = ok and det[mdl]["pass"]
        r["G2_detail"] = det
        r["G2"] = bool(ok)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            a = pre[mdl]["m2"][arm]["2017_end"]["cagr"]
            k = pre[mdl]["m2"][CONTROL]["2017_end"]["cagr"]
            d = (a - k) * 100
            det[mdl] = {"cagr_2017": a, "cagr_2017_K": k, "delta_pp": d,
                        "pass": bool(d >= G3_MIN_CAGR_PP)}
            ok = ok and det[mdl]["pass"]
        r["G3_detail"] = det
        r["G3"] = bool(ok)
        det, n_win = {}, 0
        for nm in ("P1", "P2", "P3"):
            a = pre["base"]["m2"][arm]["subperiods"][nm]["cagr"]
            k = pre["base"]["m2"][CONTROL]["subperiods"][nm]["cagr"]
            d = (a - k) * 100
            det[nm] = {"cagr": a, "cagr_K": k, "delta_pp": d, "win": bool(d > 0)}
            n_win += int(d > 0)
        r["G4_detail"] = {"periods": det, "n_wins": n_win, "required": G4_MIN_WINS}
        r["G4"] = bool(n_win >= G4_MIN_WINS)
        det, ok = {}, True
        for mdl in ("base", "calibrated"):
            c = bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_pp"] >= G5_MIN_MEDIAN_PP)
            det[mdl] = {"median_delta_pp": c["median_delta_pp"], "mean_delta_pp": c["mean_delta_pp"],
                        "share_gt_0": c["share_gt_0"], "p05_pp": c["p05_pp"], "p95_pp": c["p95_pp"],
                        "cagr_median": bt["pre"][mdl][arm]["cagr_median"],
                        "cagr_median_K": bt["pre"][mdl][CONTROL]["cagr_median"], "pass": good}
            ok = ok and good
        r["G5_detail"] = det
        r["G5"] = bool(ok)
        d_c = {wn: res["oos"][wn][arm]["delta_cagr_pp"] for wn in OOS_WINDOWS}
        d_d = {wn: res["oos"][wn][arm]["delta_max_dd_pp"] for wn in OOS_WINDOWS}
        good_w = [wn for wn in OOS_WINDOWS if d_c[wn] >= G7_MIN_CAGR_PP and d_d[wn] <= G7_MAX_DD_PP]
        r["G7_detail"] = {"delta_cagr_pp": d_c, "delta_max_dd_pp": d_d,
                          "median_delta_pp": float(np.median(list(d_c.values()))),
                          "n_within": len(good_w), "required_windows": G7_MIN_WINDOWS,
                          "max_delta_max_dd_pp": float(max(d_d.values()))}
        r["G7"] = bool(np.median(list(d_c.values())) >= G7_MIN_MEDIAN_PP
                       and len(good_w) >= G7_MIN_WINDOWS
                       and max(d_d.values()) <= G7_MAX_DD_HARD_PP)
        pb = res["oos_boot"]["pooled"][arm]
        r["G8_detail"] = {"median_delta_pp": pb["median_delta_pp"], "mean_delta_pp": pb["mean_delta_pp"],
                          "share_gt_0": pb["share_gt_0"], "paths": pb["paths"]}
        r["G8"] = bool(pb["median_delta_pp"] >= G8_MIN_MEDIAN_PP)
        t_a = pre["base"]["m2"][arm]["turnover_annual"]
        t_k = pre["base"]["m2"][CONTROL]["turnover_annual"]
        r["G9_detail"] = {"turnover": t_a, "turnover_K": t_k, "ratio": t_a / t_k,
                          "ratio_le_1": bool(t_a / t_k <= 1.0 + 1e-9)}
        r["G9"] = bool(t_a / t_k <= G9_MAX_TURNOVER_MULT + 1e-9)
        t1 = {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                   "after_tax_cagr_K": win[wn][CONTROL]["after_tax_cagr"],
                   "delta_pp": (win[wn][arm]["after_tax_cagr"]
                                - win[wn][CONTROL]["after_tax_cagr"]) * 100,
                   "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"],
                   "max_dd_after_tax_K": win[wn][CONTROL]["max_dd_after_tax"],
                   "delta_max_dd_pp": (win[wn][arm]["max_dd_after_tax"]
                                       - win[wn][CONTROL]["max_dd_after_tax"]) * 100}
              for wn in TAX_WINDOWS}
        r["T1_detail"] = {"windows": t1,
                          "min_delta_pp": float(min(v["delta_pp"] for v in t1.values())),
                          "tax_usd": {wn: win[wn][arm]["tax_usd"] for wn in TAX_WINDOWS},
                          "tax_usd_K": {wn: win[wn][CONTROL]["tax_usd"] for wn in TAX_WINDOWS},
                          "sells_per_year": {wn: win[wn][arm]["sells_per_year"] for wn in TAX_WINDOWS},
                          "postponements": win[TAX_WINDOWS_LAST][arm]["postponements"],
                          "deferred_exit": win[TAX_WINDOWS_LAST][arm]["deferred_exit"],
                          "staged_events": win[TAX_WINDOWS_LAST][arm]["staged_events"],
                          "index_relief_try": win[TAX_WINDOWS_LAST][arm]["index_relief_try"],
                          "index_relief_try_K": win[TAX_WINDOWS_LAST][CONTROL]["index_relief_try"]}
        r["T1"] = bool(all(v["delta_pp"] >= T1_MIN_DELTA_PP for v in t1.values()))
        for mode, key in (("progressive", "T2"), ("flat", "T3")):
            c = bt["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            good = bool(c["median_delta_cagr_pp"] >= T2_MIN_MEDIAN_PP
                        and c["share_gt_0"] >= T2_MIN_SHARE)
            r[f"{key}_detail"] = {"mode": mode, "median_delta_pp": c["median_delta_cagr_pp"],
                                  "mean_delta_pp": c["mean_delta_cagr_pp"], "share_gt_0": c["share_gt_0"],
                                  "p05_pp": c["p05_delta_cagr_pp"], "p95_pp": c["p95_delta_cagr_pp"],
                                  "pass": good,
                                  "cagr_median": bt["tax"][GATE_CAPITAL][mode][arm]["cagr_median"],
                                  "cagr_median_K": bt["tax"][GATE_CAPITAL][mode][CONTROL]["cagr_median"]}
            r[key] = bool(good)
        r["vs_K"] = {
            "pre_tax": {mdl: {m_: {"cagr": pre[mdl][m_][arm]["cagr"],
                                   "max_dd": pre[mdl][m_][arm]["max_dd"],
                                   "turnover_annual": pre[mdl][m_][arm]["turnover_annual"],
                                   "delta_cagr_pp": (pre[mdl][m_][arm]["cagr"]
                                                     - pre[mdl][m_][CONTROL]["cagr"]) * 100,
                                   "delta_max_dd_pp": (pre[mdl][m_][arm]["max_dd"]
                                                       - pre[mdl][m_][CONTROL]["max_dd"]) * 100}
                               for m_ in ("m1", "m2", "m4")} for mdl in ("base", "calibrated")},
            "after_tax_100k": {wn: {"after_tax_cagr": win[wn][arm]["after_tax_cagr"],
                                    "delta_vs_K_pp": t1[wn]["delta_pp"],
                                    "pre_tax_cagr": win[wn][arm]["pre_tax_cagr"],
                                    "max_dd_after_tax": win[wn][arm]["max_dd_after_tax"],
                                    "tax_usd": win[wn][arm]["tax_usd"],
                                    "sells_per_year": win[wn][arm]["sells_per_year"],
                                    "deferred_exit": win[wn][arm]["deferred_exit"],
                                    "staged_events": win[wn][arm]["staged_events"],
                                    "staged_sessions": win[wn][arm]["staged_sessions"],
                                    "staged_release_cap": win[wn][arm]["staged_release_cap"],
                                    "staged_release_gate": win[wn][arm]["staged_release_gate"],
                                    "staged_release_target": win[wn][arm]["staged_release_target"],
                                    "realised_gain_try_by_reason": win[wn][arm]["realised_gain_try_by_reason"],
                                    "neg_gain_try_by_reason": win[wn][arm]["neg_gain_try_by_reason"]}
                               for wn in TAX_WINDOWS},
            "bootstrap_seed20261011": {mode: {cl: {
                "median_delta_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["median_delta_cagr_pp"],
                "share_gt_0": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["share_gt_0"],
                "p05_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["p05_delta_cagr_pp"],
                "p95_pp": bt["tax"][cl][mode][f"{arm}_vs_{CONTROL}"]["p95_delta_cagr_pp"]}
                for cl in ("100k", "1M")} for mode in ("progressive", "flat")},
            "pre_tax_bootstrap": {mdl: {"median_delta_pp": bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]["median_delta_pp"],
                                        "share_gt_0": bt["pre"][mdl][f"{arm}_vs_{CONTROL}"]["share_gt_0"]}
                                  for mdl in ("base", "calibrated")},
            "oos_median_delta_pp": float(np.median([d_c[wn] for wn in OOS_WINDOWS])),
            "e2_rolling_starts": {"median_delta_pp": float(np.median(
                [(res["e2"][str(y)][arm]["after_tax_cagr"] - res["e2"][str(y)][CONTROL]["after_tax_cagr"]) * 100
                 for y in E2_START_YEARS])),
                "n_ge_0": int(sum(1 for y in E2_START_YEARS
                                  if (res["e2"][str(y)][arm]["after_tax_cagr"]
                                      - res["e2"][str(y)][CONTROL]["after_tax_cagr"]) >= 0)),
                "n_starts": len(E2_START_YEARS)},
            "e1_21_etfs": {"median_delta_pp": float(np.median(
                [res["e1"][k][arm]["delta_after_tax_pp"] for k in INSTRUMENTS])),
                "n_gt_0": int(sum(1 for k in INSTRUMENTS if res["e1"][k][arm]["delta_after_tax_pp"] > 0)),
                "n_instruments": len(INSTRUMENTS),
                "worst_delta_max_dd_pp": float(max(
                    res["e1"][k][arm]["delta_max_dd_after_tax_pp"] for k in INSTRUMENTS))},
            "cost_stress_m4": {wn: {"after_tax_cagr": res["cost"][wn][arm]["after_tax_cagr"],
                                    "delta_vs_K_pp": (res["cost"][wn][arm]["after_tax_cagr"]
                                                      - res["cost"][wn][CONTROL]["after_tax_cagr"]) * 100}
                               for wn in TAX_WINDOWS},
        }
        r["adopted"] = bool(all(r[x] for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8",
                                               "G9", "T1", "T2", "T3")))
        g[arm] = r
    adopted = [a for a in CANDIDATES if g[a]["adopted"]]
    best = None
    if adopted:
        best = max(adopted, key=lambda a: win[TAX_WINDOW_MAIN][a]["after_tax_cagr"])
    failed = {a: [x for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "T1", "T2", "T3")
                  if not g[a][x]] for a in CANDIDATES}
    reason = ("adopt " + best + " (highest after-tax $100k CAGR among the arms that clear every gate)"
              if best else
              "no arm adopted; every candidate fails at least one pre-registered gate: "
              + "; ".join(f"{a}: {','.join(failed[a])}" for a in CANDIDATES))
    return {"arms": g, "adopted": best, "failed_gates": failed, "reason": reason, "gate": {
        "G1": f"CAGR >= K's, or Calmar >= {G1_MIN_CALMAR_MULT} x K's with CAGR >= K "
              f"{G1_MIN_REL_CAGR_PP} pp (m = 2, base and calibrated)",
        "G2": f"pre-tax max DD <= K + {G2_MAX_DD_PP} pp (m = 2, base and calibrated)",
        "G3": f"2017+ CAGR >= K {G3_MIN_CAGR_PP} pp (m = 2, base and calibrated)",
        "G4": f"CAGR above K in >= {G4_MIN_WINS} of 3 sub-periods P1/P2/P3",
        "G5": f"block-20 pre-tax bootstrap (seed {SEED}, {PRE_BOOT_PATHS} paths) median of arm - K "
              f">= {G5_MIN_MEDIAN_PP} pp (base and calibrated)",
        "G6": "G1 at m = 1 (base and calibrated)",
        "G7": f"six OOS windows: median delta CAGR >= {G7_MIN_MEDIAN_PP} pp, >= {G7_MIN_WINDOWS}/6 with "
              f"delta CAGR >= {G7_MIN_CAGR_PP} pp and delta DD <= +{G7_MAX_DD_PP} pp, delta DD never "
              f"above +{G7_MAX_DD_HARD_PP} pp",
        "G8": f"pooled OOS bootstrap (M1-M5, {OOS_PATHS_PER_MARKET} paths each) median >= {G8_MIN_MEDIAN_PP} pp",
        "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K",
        "T1": f"after tax at $100k: arm - K >= {T1_MIN_DELTA_PP} pp in all three windows",
        "T2": f"B1-recent (progressive) at $100k: median >= {T2_MIN_MEDIAN_PP} pp and share > 0 >= {T2_MIN_SHARE:.0%}",
        "T3": f"B2 (flat 20 %) at $100k: median >= {T2_MIN_MEDIAN_PP} pp and share > 0 >= {T2_MIN_SHARE:.0%}",
        "adopt": "candidates are adopted only if all of G1-G9 and T1-T3 pass; the selection among "
                 "survivors is the highest after-tax $100k CAGR on the main window"}}


# ----------------------------------------------------------------------------- run
def run(boot_ref=None) -> dict:
    print(f"pre-tax main window ({len(ARMS)} arms)", flush=True)
    pre, pre_usd = pre_tax_main(ARMS)
    print("after-tax windows (5 capitals)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(ARMS)
    print("episodes (drawdown reporting)", flush=True)
    ep = episode_stage(ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(ARMS)
    print("instruments (R-USD, pre-tax)", flush=True)
    inst, quality = instrument_stages_usd(ARMS)
    if boot_ref is None:
        print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax paths)", flush=True)
        boot_ref = boot(seed=SEED, block=BLOCK, arms=ARMS, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
                        capitals=("100k", "1M"), modes=("progressive", "flat"), deltas=BOOT_DELTAS,
                        baselines=(CONTROL, REFERENCE), label="clean", quiet=True)
    print("E1: after-tax 21 ETFs (R-INDEX)", flush=True)
    e1 = stage_e1(ARMS)
    print("E2: rolling January starts", flush=True)
    e2 = stage_e2(ARMS)
    print(f"E3: fresh bootstraps (seed {E3_SEED}, blocks {E3_BLOCKS})", flush=True)
    e3 = stage_e3(ARMS)
    print("G8: pooled OOS bootstrap (M1-M5)", flush=True)
    oos_boot = oos_bootstrap((CONTROL,) + CANDIDATES)
    print("E5: decision traces", flush=True)
    e5 = {"decisions": stage_e5_decisions(ARMS)}
    res = {"pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost, "oos": oos,
           "oos_boot": oos_boot, "episodes": ep, "instruments": inst, "quality": quality,
           "boot_ref": boot_ref, "e1": e1, "e2": e2, "e3": e3, "e5": e5}
    print("reference reproduction checks (A1u vs gain_band_v3, H2 vs tax_harvest_v2)", flush=True)
    res["reference_check"] = {k: v for k, v in reference_checks(res).items() if k != "boot_ref"}
    res["gates"] = gates_exit(res)
    return res


# ----------------------------------------------------------------------------- smoke
def trading_days(n, first="2016-01-04"):
    """`n` consecutive weekdays as ISO dates (synthetic calendars only; NYSE holidays are not
    modelled -- the X1 window is defined by the December sessions of the calendar)."""
    from datetime import date, timedelta

    d = date.fromisoformat(first)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def smoke_bars(n, seed=7, drift=0.0009, vol=0.010):
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1 + rng.normal(drift, vol, n))


def smoke():
    """Synthetic-only identities: the X options are inert when off, and each X device acts exactly
    once where it should (engine parity cells included)."""
    out, bad = {}, 0
    parity = engine_parity_cells()
    out["engine_parity_cells"] = parity
    if not parity["pass"]:
        bad += 1

    days = trading_days(760)
    n = len(days)
    c = smoke_bars(n)
    o = np.concatenate([[c[0]], c[:-1]])
    fx, ufe = fe_data.usd_market(days)
    i0, i1 = 253, n - 1  # 253 warm-up sessions: the engine's measured-window convention
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1, tail=DEC_TAIL)
    env_win = fe_sim.RunEnv(days, fx, ufe, i0, i1, tail=0)
    base_w = np.full(n, 0.98)
    w_early = np.full((1, n), 0.5)
    hold = np.ones((1, n), dtype=bool)

    def sim(wants, arm, e=env, ctx=None, **kw):
        ctx = {"w_early": w_early, "hold": hold} if ctx is None else ctx
        return arm_run(np.asarray(wants, float), arm, o, c, e, PRE_CAPITAL, i0, i1, ctx=ctx, **kw)

    # ---- X1: a December exit-to-zero is postponed by X1/X3 and not by K
    i_dec = [k for k, d in enumerate(days) if d[:7] == "2017-12"][-1]  # last December-2017 session
    w1 = base_w.copy()
    w1[i_dec - 1 :] = 0.0  # the exit is *decided* at the close of i_dec-1 and would fill at i_dec
    k1 = sim(w1, CONTROL)
    x1 = sim(w1, "X1")
    x1_win = sim(w1, "X1", e=env_win)
    x3 = sim(w1, "X3")
    d = {
        "december_session": days[i_dec],
        "in_window": bool(en.december_tail(days, i0, i1, DEC_TAIL)[i_dec]),
        "K_deferred_exit": int(k1["deferred_exit"]),
        "K_sells": int(k1["qld_sells"]),
        "X1_deferred_exit": int(x1["deferred_exit"]),
        "X1_nav_differs_from_K": float(np.max(np.abs(x1["_nav"] - k1["_nav"]))),
        "X1_no_window_deferred_exit": int(x1_win["deferred_exit"]),
        "X1_no_window_is_K": float(np.max(np.abs(x1_win["_nav"] - k1["_nav"]))),
        "X3_deferred_exit": int(x3["deferred_exit"]),
        "pass": bool(k1["deferred_exit"] == 0 and k1["qld_sells"] >= 1 and k1["blocked_band"] == 0
                     and x1["deferred_exit"] == 1 and x1["postponements"] == 1
                     and x1["_nav"].shape == k1["_nav"].shape
                     and float(np.max(np.abs(x1["_nav"] - k1["_nav"]))) > 1e-9
                     and x1_win["deferred_exit"] == 0
                     and float(np.max(np.abs(x1_win["_nav"] - k1["_nav"]))) == 0.0
                     and x3["deferred_exit"] == 1)}
    out["x1_december_exit"] = d
    if not d["pass"]:
        bad += 1

    # ---- X2: a gain-heavy exit-to-zero stages to the early weight and is released by the gate
    i_e = n // 2
    w2 = base_w.copy()
    w2[i_e:] = 0.0
    h2 = np.ones((1, n), dtype=bool)
    h2[0, i_e + 1] = False  # the early conditions fail at the next decision close

    k2 = sim(w2, CONTROL)
    x2g = sim(w2, "X2", ctx={"w_early": w_early, "hold": h2})
    x2_nogain = sim(w2, "X2", x2_gain=1e9)  # unreachable threshold -> inert
    hcap = np.ones((1, n), dtype=bool)
    x2cap = sim(w2, "X2", ctx={"w_early": w_early, "hold": hcap}, x2_cap=3)
    w2t = w2.copy()
    w2t[i_e + 1 :] = 0.98  # the ordinary rule turns positive again -> takeover
    x2t = sim(w2t, "X2", ctx={"w_early": w_early, "hold": hcap})
    d = {
        "K_sells": int(k2["qld_sells"]),
        "X2_staged_events": int(x2g["staged_events"]),
        "X2_staged_sessions": int(x2g["staged_sessions"]),
        "X2_release_gate": int(x2g["staged_release_gate"]),
        "X2_release_cap": int(x2g["staged_release_cap"]),
        "X2_nav_differs_from_K": float(np.max(np.abs(x2g["_nav"] - k2["_nav"]))),
        "unreachable_gain_staged_events": int(x2_nogain["staged_events"]),
        "unreachable_gain_is_K": float(np.max(np.abs(x2_nogain["_nav"] - k2["_nav"]))),
        "cap_release": int(x2cap["staged_release_cap"]),
        "takeover": int(x2t["staged_release_target"]),
        "pass": bool(k2["qld_sells"] >= 1 and x2g["staged_events"] == 1
                     and x2g["staged_release_gate"] == 1 and x2g["staged_release_cap"] == 0
                     and x2g["staged_sessions"] >= 1
                     and float(np.max(np.abs(x2g["_nav"] - k2["_nav"]))) > 1e-9
                     and x2_nogain["staged_events"] == 0
                     and float(np.max(np.abs(x2_nogain["_nav"] - k2["_nav"]))) == 0.0
                     and x2cap["staged_release_cap"] == 1 and x2cap["staged_release_gate"] == 0
                     and x2t["staged_release_target"] == 1)}
    out["x2_staging"] = d
    if not d["pass"]:
        bad += 1

    # ---- composition: X3 with X1 off is X2; X3 with X2 off is X1 (bit-for-bit)
    x2 = sim(w1, "X2")
    x3_no_x1 = sim(w1, "X3", dec_exit=False)
    x3_no_x2 = sim(w1, "X3", stage=False)
    d = {"X3_no_X1_is_X2": float(np.max(np.abs(x3_no_x1["_nav"] - x2["_nav"]))),
         "X3_no_X2_is_X1": float(np.max(np.abs(x3_no_x2["_nav"] - x1["_nav"]))),
         "pass": bool(np.max(np.abs(x3_no_x1["_nav"] - x2["_nav"])) == 0.0
                      and np.max(np.abs(x3_no_x2["_nav"] - x1["_nav"])) == 0.0)}
    out["x3_composition"] = d
    if not d["pass"]:
        bad += 1

    # ---- tax modes: the X arms run in progressive / flat (a 2024-2026 synthetic calendar)
    days2 = trading_days(600, "2024-01-02")
    n2 = len(days2)
    c2 = smoke_bars(n2, seed=11)
    o2 = np.concatenate([[c2[0]], c2[:-1]])
    fx2, ufe2 = fe_data.usd_market(days2)
    j0, j1 = 253, n2 - 1
    env2 = fe_sim.RunEnv(days2, fx2, ufe2, j0, j1, tail=DEC_TAIL)
    assert env2.scale_ok, "the synthetic calendar must cover the 2025-10 YI-UFE anchor"
    wA = np.full(n2, 0.98)
    wA[430:] = 0.0
    rec = {}
    for arm in ARMS:
        for mode in ("pre_tax", "progressive", "flat"):
            r = en.simulate_x(o2, c2, wA, env2, PRE_CAPITAL, j0, j1, arm=arm, mode=mode, m=M,
                              w_early=np.full((1, n2), 0.5), hold=np.ones((1, n2), dtype=bool))
            rec[f"{arm}/{mode}"] = float(r["cagr_usd_liquidated"])
    out["tax_modes_run"] = {"cagr": rec, "pass": bool(all(np.isfinite(list(rec.values()))))}
    if not out["tax_modes_run"]["pass"]:
        bad += 1

    out["pass"] = bool(bad == 0)
    out["n_failed"] = bad
    return out


# ----------------------------------------------------------------------------- report
def fmt_pct(x):
    return f"{x * 100:7.2f}%"


def summary_lines(hs, res, wall_s, parity_wall_s):
    p = fmt_pct
    g = res["gates"]["arms"]
    rc = res["reference_check"]
    pre, win, e1, e2, e3, boot_ref = res["pre"], res["windows"], res["e1"], res["e2"], res["e3"], res["boot_ref"]
    ctrl = win[GATE_CAPITAL]
    w = pre["window"]
    L = [f"exit_timing_v2 | exit-leg tax timing under K (idea_survey_v4 I4), Numba engine | main window "
         f"{w['start']} -> {w['end']} ({w['sessions']} sessions) | control = production K | "
         f"arms = K + C0(R2) + A1u + H2 + X1 + X2 + X3",
         f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance); "
         f"engine parity --quick {'PASS' if hs['reproduction_checks']['engine_parity_cli']['pass'] else 'FAIL'} "
         f"({parity_wall_s}s, PARITY.json restored); "
         f"X options off == fe_sim/fe_boot bit-for-bit "
         f"{'PASS' if hs['reproduction_checks']['x_options_off_equals_engine']['pass'] else 'FAIL'}; "
         f"control K anchors {'PASS' if hs['k_anchors']['pass'] else 'FAIL'}; "
         f"R2 reference anchor {'PASS' if hs['r2_anchors']['pass'] else 'FAIL'}",
         f"deep reference block: {rc['n_cells']} cells ({', '.join(rc['sections'])}), "
         f"{rc['n_mismatch']} mismatches (tol {rc['tolerance']}), max|diff| {rc['max_abs_diff']:.2e} -> "
         f"{'PASS' if rc['pass'] else 'FAIL'}",
         f"ADOPTED: {res['gates']['adopted']} -- {res['gates']['reason']}",
         f"wall clock of this --run: {wall_s:.1f} s", ""]
    L.append("per arm (pre-tax main window m = 2, R-INDEX; after tax at $100k, base, m = 2, liquidated; "
             "deltas vs the control K):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr | dAT2000  dAT2010  dAT2017 "
             "(pp) | boot B1 med/share | boot B2 med/share | OOS med d (pp) | T1 T2 T3")
    for arm in ARMS:
        a, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        ats = "".join(f"{(ctrl[wn][arm]['after_tax_cagr'] - ctrl[wn][CONTROL]['after_tax_cagr']) * 100:+9.3f}"
                      for wn in TAX_WINDOWS)
        if arm == CONTROL:
            bb = "        -       -  |         -       - "
        else:
            bb = " | ".join(
                f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['median_delta_cagr_pp']:+7.3f} "
                f"{boot_ref['tax'][GATE_CAPITAL][md][f'{arm}_vs_{CONTROL}']['share_gt_0'] * 100:6.1f}%"
                for md in ("progressive", "flat"))
        eo = float(np.median([res["oos"][wn][arm].get("delta_cagr_pp", 0.0) for wn in OOS_WINDOWS]))
        fl = ("    -    -    -" if arm not in CANDIDATES
              else "  ".join(f"{x}={'P' if g[arm][x] else 'F'}" for x in ("T1", "T2", "T3")))
        L.append(f"  {arm:4s} {p(a['cagr'])} {p(ac['cagr'])} {p(a['max_dd'])} {p(ac['max_dd'])} "
                 f"{a['turnover_annual']:9.2f} |{ats} | {bb} | {eo:+12.3f} | {fl}")
    L.append("")
    for arm in CANDIDATES:
        r = g[arm]
        L.append(f"  {arm} gates: " + " ".join(
            f"{x}={'PASS' if r[x] else 'FAIL'}" for x in ("G1", "G2", "G3", "G4", "G5", "G6", "G7",
                                                          "G8", "G9", "T1", "T2", "T3")))
        L.append(f"      G1 m2 base {r['G1_detail_m2']['base']['delta_pp']:+.3f}pp "
                 f"cal {r['G1_detail_m2']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G1 m1 base {r['G1_detail_m1']['base']['delta_pp']:+.3f}pp "
                 f"cal {r['G1_detail_m1']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G2 base {r['G2_detail']['base']['delta_pp']:+.2f}pp cal "
                 f"{r['G2_detail']['calibrated']['delta_pp']:+.2f}pp | "
                 f"G3 base {r['G3_detail']['base']['delta_pp']:+.3f}pp cal "
                 f"{r['G3_detail']['calibrated']['delta_pp']:+.3f}pp | "
                 f"G4 wins {r['G4_detail']['n_wins']}/3 | G9 ratio {r['G9_detail']['ratio']:.4f}")
        L.append(f"      G5 pre-tax boot med/base {r['G5_detail']['base']['median_delta_pp']:+.4f}pp "
                 f"(share>0 {r['G5_detail']['base']['share_gt_0'] * 100:.1f}%) cal "
                 f"{r['G5_detail']['calibrated']['median_delta_pp']:+.4f}pp "
                 f"(share>0 {r['G5_detail']['calibrated']['share_gt_0'] * 100:.1f}%) | "
                 f"G7 median {r['G7_detail']['median_delta_pp']:+.3f}pp within "
                 f"{r['G7_detail']['n_within']}/6 worst dDD {r['G7_detail']['max_delta_max_dd_pp']:+.2f}pp | "
                 f"G8 median {r['G8_detail']['median_delta_pp']:+.3f}pp")
        for x in ("T1", "T2", "T3"):
            d = r[f"{x}_detail"]
            if x == "T1":
                L.append("      T1 deltas "
                         + ", ".join(f"{wn} {d['windows'][wn]['delta_pp']:+.3f}pp" for wn in TAX_WINDOWS)
                         + " | dmaxDD "
                         + ", ".join(f"{wn} {d['windows'][wn]['delta_max_dd_pp']:+.2f}pp"
                                     for wn in TAX_WINDOWS))
            else:
                L.append(f"      {x} ({d['mode']}): median {d['median_delta_pp']:+.4f}pp mean "
                         f"{d['mean_delta_pp']:+.4f}pp share>0 {d['share_gt_0'] * 100:.1f}% "
                         f"p05/p95 {d['p05_pp']:+.2f}/{d['p95_pp']:+.2f} -> "
                         f"{'PASS' if d['pass'] else 'FAIL'}")
        L.append(f"      vs K report: E2 rolling starts median "
                 f"{r['vs_K']['e2_rolling_starts']['median_delta_pp']:+.3f}pp "
                 f"({r['vs_K']['e2_rolling_starts']['n_ge_0']}/21 >= 0) | E1 21 ETFs median "
                 f"{r['vs_K']['e1_21_etfs']['median_delta_pp']:+.3f}pp "
                 f"({r['vs_K']['e1_21_etfs']['n_gt_0']}/21 > 0, worst dmaxDD "
                 f"{r['vs_K']['e1_21_etfs']['worst_delta_max_dd_pp']:+.2f}pp) | m=4 cost "
                 + ", ".join(f"{wn} {r['vs_K']['cost_stress_m4'][wn]['delta_vs_K_pp']:+.3f}pp"
                             for wn in TAX_WINDOWS))
    L.append("")
    L.append("postponement / staging counters (main window, m = 2, base, $100k, progressive):")
    for arm in ARMS:
        r = ctrl[TAX_WINDOW_MAIN][arm]
        L.append(f"  {arm:4s} postponements {r['postponements']:5d} (band {r['blocked_band']:5d}, "
                 f"December-resize {r['deferred']:4d}, December-exit {r['deferred_exit']:4d}) "
                 f"sells/yr {r['sells_per_year']:6.2f} staged events {r['staged_events']:3d} "
                 f"sessions {r['staged_sessions']:4d} (cap {r['staged_release_cap']}, "
                 f"gate {r['staged_release_gate']}, target-takeover {r['staged_release_target']})")
    L.append("")
    L.append("named episodes (main window, base financing, m = 2, pre-tax; delta vs K):")
    L.append("  episode              arm   total ret   d ret pp    max DD    d DD pp")
    for name, _a, _b in EPISODES:
        for arm in ARMS:
            e = res["episodes"]["episodes"][name][arm]
            L.append(f"  {name:20s} {arm:4s} {e['total_return'] * 100:+9.2f}% "
                     f"{e['delta_total_return_pp']:+9.3f} {e['max_dd'] * 100:8.2f}% "
                     f"{e['delta_max_dd_pp']:+9.3f}")
    L.append("")
    L.append("whole-path (main window, pre-tax, m = 2) max DD and the X counters:")
    for arm in ARMS:
        e = res["episodes"]["whole_path"][arm]
        L.append(f"  {arm:4s} cagr {e['cagr'] * 100:7.4f}% maxDD {e['max_dd'] * 100:6.3f}% "
                 f"(d vs K {e['max_dd_delta_vs_K_pp']:+.3f}pp) deferred-exit {e['deferred_exit']:3d} "
                 f"staged events {e['staged_events']:3d}")
    L.append("")
    L.append("after-tax counters at $100k (base, m = 2, liquidated):")
    L.append("  arm   tax USD 2017+    relief TRY 2017+   deferred-exit 2017+   staged 2017+   sells/yr 2017+")
    for arm in ARMS:
        r = ctrl[TAX_WINDOWS_LAST][arm]
        L.append(f"  {arm:4s} {r['tax_usd']:12.0f} {r['index_relief_try']:19.0f} "
                 f"{r['deferred_exit']:21d} {r['staged_events']:13d} {r['sells_per_year']:14.2f}")
    L.append("")
    L.append("E1 after tax on the 21 quality-passing ETFs (R-INDEX, $100k, calibrated, m = 2, "
             "liquidated) -- delta vs K (report only, not gated):")
    L.append("  instrument  window                     K AT     X1 d     X2 d     X3 d")
    for key in INSTRUMENTS:
        r = e1[key]
        L.append(f"  {key:10s}  {r['window']['start']}..{r['window']['end']} {p(r[CONTROL]['after_tax_cagr'])}"
                 f"  {r['X1']['delta_after_tax_pp']:+7.3f}  {r['X2']['delta_after_tax_pp']:+7.3f}  "
                 f"{r['X3']['delta_after_tax_pp']:+7.3f}")
    L.append("  median delta: " + ", ".join(
        f"{arm} {float(np.median([e1[k][arm]['delta_after_tax_pp'] for k in INSTRUMENTS])):+.3f}pp "
        f"({sum(1 for k in INSTRUMENTS if e1[k][arm]['delta_after_tax_pp'] > 0)}/21 > 0)"
        for arm in CANDIDATES))
    L.append("")
    L.append("E2 rolling January starts (after tax $100k, base, m = 2, to 2026-09-29) -- delta vs K:")
    L.append("  year   start        K AT      X1 d      X2 d      X3 d")
    for yr in E2_START_YEARS:
        r = e2[str(yr)]
        L.append(f"  {yr}   {r['start']}  {p(r[CONTROL]['after_tax_cagr'])} "
                 f"{r['X1']['delta_after_tax_pp']:+8.3f} {r['X2']['delta_after_tax_pp']:+8.3f} "
                 f"{r['X3']['delta_after_tax_pp']:+8.3f}")
    L.append("")
    L.append(f"E3 fresh after-tax bootstraps (seed {E3_SEED}, {E3_PATHS} paths, $100k, blocks "
             f"{E3_BLOCKS}) -- delta vs K (report only):")
    L.append("  run                    arm    median d  share>0     p05       p95")
    for key in sorted(e3):
        for arm in CANDIDATES:
            v = e3[key][f"{arm}_vs_{CONTROL}"]
            L.append(f"  {key:20s} {arm:4s} {v['median_delta_cagr_pp']:+10.3f} "
                     f"{v['share_gt_0'] * 100:7.1f}% {v['p05_delta_cagr_pp']:+9.2f} "
                     f"{v['p95_delta_cagr_pp']:+9.2f}")
    L.append("")
    L.append("E5 (report only): fill-decision differences on the main path (pre-tax, $100k, m = 2)")
    dec = res["e5"]["decisions"]
    for pair in ("X1_vs_K", "X2_vs_K", "X3_vs_K", "K_vs_C0", "K_vs_A1u", "H2_vs_C0"):
        a, b = pair.split("_vs_")
        d = dec[pair]
        L.append(f"  {pair:12s}: {d['sessions_decision_differs']:5d} sessions differ "
                 f"({d['share_decision_differs'] * 100:.2f}%); {a} sells where {b} holds "
                 f"{d[f'{a}_sells_{b}_holds']}, {a} holds where {b} sells {d[f'{a}_holds_{b}_sells']}")
    L.append("  sells (downward resizes+exits) per arm: "
             + ", ".join(f"{a} {dec['sells'][a]}" for a in ARMS))
    L.append("  postponements per arm: "
             + ", ".join(f"{a} {dec['postponements'][a]['band']}+{dec['postponements'][a]['deferred']}"
                         f"+{dec['postponements'][a]['exit']}+{dec['postponements'][a]['staged']}"
                         for a in ARMS))
    for cl in E5_CAPITALS:
        L.append(f"  after tax at ${cl} (progressive, liquidated, base, m = 2):")
        for wn in TAX_WINDOWS:
            c0 = win[cl][wn][CONTROL]["after_tax_cagr"]
            L.append(f"    {wn:20s} K {p(c0)} | " + " ".join(
                f"{arm} {p(win[cl][wn][arm]['after_tax_cagr'])} "
                f"{(win[cl][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("m = 4 cost stress, after tax at $100k (delta vs K):")
    for wn in TAX_WINDOWS:
        c0 = res["cost"][wn][CONTROL]["after_tax_cagr"]
        L.append(f"  {wn:20s} K {p(c0)} | " + " ".join(
            f"{arm} {p(res['cost'][wn][arm]['after_tax_cagr'])} "
            f"{(res['cost'][wn][arm]['after_tax_cagr'] - c0) * 100:+7.3f}pp" for arm in CANDIDATES))
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD rule) -- delta vs K:")
    L.append("  window            K CAGR    K maxDD     X1 d      X2 d      X3 d   (CAGR pp)")
    for wn_ in OOS_WINDOWS:
        r = res["oos"][wn_]
        L.append(f"  {wn_:16s} {p(r[CONTROL]['cagr'])}  {p(r[CONTROL]['max_dd'])}  "
                 f"{r['X1']['delta_cagr_pp']:+8.3f} {r['X2']['delta_cagr_pp']:+9.3f} "
                 f"{r['X3']['delta_cagr_pp']:+9.3f}")
    L.append("")
    L.append("G8 pooled OOS bootstrap (pre-tax, R-USD, M1-M5, 2,000 paths each) -- delta vs K:")
    L.append("  " + ", ".join(
        f"{a} median {res['oos_boot']['pooled'][a]['median_delta_pp']:+.3f}pp "
        f"(share>0 {res['oos_boot']['pooled'][a]['share_gt_0'] * 100:.1f}%)" for a in CANDIDATES))
    L.append("")
    L.append("realised / negative realised TRY gain by reason (main window, $100k, progressive, m = 2):")
    for arm in ("K", "H2") + CANDIDATES:
        r = ctrl[TAX_WINDOW_MAIN][arm]
        L.append(f"  {arm:4s} gain " + json.dumps({k: round(v) for k, v in
                                                    r["realised_gain_try_by_reason"].items()})
                 + " | neg " + json.dumps({k: round(v) for k, v in
                                           r["neg_gain_try_by_reason"].items()}))
    L.append("")
    L.append("reference reproduction: " + f"{rc['n_cells']} cells, {rc['n_mismatch']} mismatches, "
             f"max|diff| {rc['max_abs_diff']:.2e} -> {'PASS' if rc['pass'] else 'FAIL'}")
    return L


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--smoke", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    if mode == "--smoke":
        t0 = time.time()
        s = smoke()
        print(json.dumps({k: v for k, v in s.items() if k != "engine_parity_cells"}, indent=2, default=float))
        print(json.dumps(s["engine_parity_cells"], indent=2, default=float))
        print(f"smoke {'PASS' if s['pass'] else 'FAIL'} in {time.time() - t0:.1f}s")
        sys.exit(0 if s["pass"] else 1)

    hs = harness(run_boot=(mode == "--run"), deep=(mode in ("--harness", "--run")))
    for k in sorted(hs["reproduction_checks"]):
        v = hs["reproduction_checks"][k]
        if "reference" in v:
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff']:+.6f}")
        elif k == "engine_parity_cli":
            print(f"harness {k}: {'PASS' if v['pass'] else 'FAIL'} ({v.get('verdict', '')})")
        else:
            print(f"harness {k}: {v}")
    for wn, v in hs["after_tax_anchors"].items():
        for arm, x in v.items():
            print(f"tax anchor {wn} {arm}: pre {x['pre_tax']['diff_pp']:+.5f} "
                  f"prog {x['progressive']['diff_pp']:+.5f} dd {x['progressive_max_dd']['diff_pp']:+.5f} pp")
    if hs["bootstrap_control"]:
        for mode_ in ("progressive", "flat"):
            for label, ref in (("R2", BOOT_ANCHOR[mode_]["R2"]), ("K", BOOT_ANCHOR_K[(mode_, "100k")]),
                               ("E2", BOOT_ANCHOR[mode_]["E2"])):
                b = hs["bootstrap_control"][mode_][label]
                print(f"boot anchor {mode_} {label}: ref {ref:.6f} repro {b:.6f} "
                      f"diff {(b - ref) * 100:+.3f} pp")
    ka, ra = hs["k_anchors"], hs["r2_anchors"]
    for wn in TAX_WINDOWS:
        v = ka["after_tax_100k"][wn]
        print(f"K anchor after_tax {wn}: ref {[f'{x * 100:.3f}%' for x in v['reference']]} "
              f"repro {[f'{x * 100:.3f}%' for x in v['reproduced']]} deferrals {v['deferrals']} vs "
              f"{v['reference_deferrals']} -> {'PASS' if v['pass'] else 'FAIL'}")
    print(f"K anchor block (the control label the run uses): {'PASS' if ka['pass'] else 'FAIL'}")
    print(f"R2 anchor block (the reference label): {'PASS' if ra['pass'] else 'FAIL'}")
    if hs.get("deep"):
        d = hs["deep"]
        print(f"deep reference block: {d['n_cells']} cells ({', '.join(d['sections'])}), "
              f"{d['n_mismatch']} mismatches (tol {d['tolerance']}), max|diff| {d['max_abs_diff']:.2e}, "
              f"keys only in mine {d['n_keys_only_in_mine']} / only in v3 {d['n_keys_only_in_v3']} -> "
              f"{'PASS' if d['pass'] else 'FAIL'}")
        for m in d["mismatches"][:10]:
            print(f"  deep mismatch {m['cell']}: mine {m['mine']:.8f} ref {m['reference']:.8f} "
                  f"diff {m['diff']:+.2e}")
    print(f"harness {'PASS' if hs['pass'] else 'FAIL'} ({hs['n_outside_tolerance']} outside tolerance)")
    if mode == "--harness" or not hs["pass"]:
        sys.exit(0 if hs["pass"] else 1)

    parity_wall_s = hs.pop("timings", {}).get("engine_parity_cli_s", 0.0)
    t0 = time.time()
    res = run(boot_ref=hs["deep"]["boot_ref"])
    wall_s = time.time() - t0
    print(f"--run wall clock: {wall_s:.1f} s", flush=True)
    out = {
        "campaign": "exit_timing_v2",
        "engine": "research/fast_engine (Numba) + research/exit_timing_v2/engine.py (X1/X2 options)",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "reference_row": REFERENCE, "rule": RULE,
            "x2_gain_threshold": X2_GAIN, "x2_cap_sessions": X2_CAP_SESSIONS, "dec_tail": DEC_TAIL,
            "b_up": 0.005, "gain_tiers": [[0.01, 0.005], [0.05, 0.03], [None, 0.08]],
            "composition": ("every arm runs production R2's target series with K's execution layer "
                            "(H2's December window tested first, then A1u's gain band); X1 postpones "
                            "an exit to zero whose fill session is in the last DEC_TAIL December "
                            "sessions, X2 stages a gain-heavy exit to zero down to the early-leg "
                            "weight (0.5 x vt) for at most X2_CAP_SESSIONS sessions while the early "
                            "leg's own conditions stand, X3 = X1 + X2 with X1's window first. A "
                            "suppressed or postponed fill is never re-tried: the target is "
                            "re-evaluated at every close."),
            "signals": "production R2 (equity_leveraged_trend_v1 1.2.0): E2 2-of-3 on price closes "
                       "(SMA200, 252-session price return > 0, close > 126-session max/min midpoint) "
                       "+ early re-entry at 0.5 x vt while E2 off and close > SMA50, vol20 < its "
                       "252-session median and close > close 20 sessions ago; exit early below SMA50",
            "sizing": "vt = min(1, 0.35/vol20) on synthetic QLD*, cap 0.98",
            "instrument": "synthetic QLD* daily-reset 2x on QQQ total return, expense 0.95%/yr, "
                          "financing DTB3 + {base 0.0, calibrated 0.75%/yr}",
            "cost_per_side": en.COST_PER_SIDE, "cost_stress_m": M_STRESS,
            "main_window": pre_window(res), "start": START,
            "after_tax": {"engine": "fe_tax (the vt_level_tax_v1 corrected engine)",
                          "financing": "base", "m": M, "windows": TAX_WINDOWS,
                          "capitals": TAX_CAPITALS, "gate_capital": GATE_CAPITAL,
                          "clean_bootstrap": {"paths_pre": PRE_BOOT_PATHS, "paths_tax": TAX_BOOT_PATHS,
                                              "seed": SEED, "block": BLOCK},
                          "fresh_bootstrap": {"paths": E3_PATHS, "seed": E3_SEED,
                                              "blocks": list(E3_BLOCKS)}},
            "e1": {"rule": "R-INDEX", "start": E1_RINDEX_START, "financing": "calibrated", "m": M,
                   "capital": GATE_CAPITAL, "n_instruments": len(INSTRUMENTS),
                   "excluded": list(EXCLUDED_INSTRUMENTS)},
            "e2": {"starts": list(E2_START_YEARS), "capital": GATE_CAPITAL},
            "episodes": [list(x) for x in EPISODES],
            "oos_bootstrap": {"markets": list(MARKETS), "paths_per_market": OOS_PATHS_PER_MARKET,
                              "block": BLOCK, "seed": SEED, "treatment": "pre-tax, R-USD rule"},
            "gates": res["gates"]["gate"], "adopted": res["gates"]["adopted"],
            "reason": res["gates"]["reason"]},
        # deliberately no wall-clock field: RESULTS.json is the byte-identical artifact (the
        # --run wall clock is reported in SUMMARY.txt)
        "harness": {"pass": hs["pass"], "n_outside_tolerance": hs["n_outside_tolerance"],
                    "reproduction_checks": hs["reproduction_checks"], "k_anchors": hs["k_anchors"],
                    "r2_anchors": hs["r2_anchors"], "after_tax_anchors": hs["after_tax_anchors"],
                    "bootstrap_control_medians": hs["bootstrap_control"],
                    "deep_reference": {k: v for k, v in hs["deep"].items() if k != "boot_ref"}},
        "reference_check": res["reference_check"],
        "pre_tax_main": res["pre"],
        "pre_tax_main_r_usd": res["pre_usd"],
        "after_tax_windows": res["windows"],
        "cost_stress_m4": res["cost"],
        "episodes": res["episodes"],
        "oos": res["oos"],
        "oos_bootstrap": res["oos_boot"],
        "instruments": res["instruments"],
        "quality": res["quality"],
        "bootstrap_clean_seed20261011": res["boot_ref"],
        "E1_after_tax_etfs_rindex": res["e1"],
        "E2_rolling_starts": res["e2"],
        "E3_fresh_bootstraps": res["e3"],
        "E5": res["e5"],
        "gates": res["gates"],
        "status": "complete",
    }
    OUT.write_text(json.dumps(rnd(out), indent=2, sort_keys=True) + "\n")
    lines = summary_lines(hs, res, wall_s, parity_wall_s)
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def pre_window(res):
    return res["pre"]["window"]


if __name__ == "__main__":
    main()
