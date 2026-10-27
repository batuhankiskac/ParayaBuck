"""gates_v2: items 28-34 on the fast engine (research/fast_engine). Pre-registered in PREREGISTRATION.md.

    python -m research.gates_v2.screen --harness   # control: K / R2 anchors + parity --quick (no study output)
    python -m research.gates_v2.screen --run       # the study -> RESULTS.json, SUMMARY.txt

Every simulation goes through fe_sim.simulate (numba). This file only builds target-weight arrays
(numpy), aggregates results and calls arch for SPA / MCS / Politis-White.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

import arch
from arch.bootstrap import MCS, SPA, optimal_block_length

from research.fast_engine import fe_config, fe_data, fe_signals, fe_sim, fe_study

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BH1_RESULTS = ROOT / "research/band_h2_combo_v1/RESULTS.json"
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

START = fe_config.START
CAP = fe_config.CAP
M = 2  # base cost multiplier (COST_PER_SIDE * 2)
CAPITAL = 100_000.0
CAL_SPREAD = fe_config.FIN["calibrated"]
WINDOWS = OrderedDict(fe_config.TAX_WINDOWS)  # 2000_03_to_2026_09, 2010_01_to_end, 2017_01_to_end
HALVES = OrderedDict([("pre_2020", ("2000-03-09", "2019-12-31")), ("post_2020", ("2020-01-01", "2026-09-29"))])
DEFAULT_VT = 0.35
DEFAULT_WIN = (252, 126)
VT_GRID = (0.25, 0.30, 0.35, 0.40, 0.45)
WIN_GRID = ((252, 126), (189, 126), (252, 63))  # (momentum TSM, Donchian DON)
EXP_FIRST_YEAR = 2005
SEED = 20261027
LAG_N, LAG_MIN = 1000, 252
SPA_REPS, MCS_REPS, MCS_ALPHA = 2000, 2000, 0.10
POWER_REPS, POWER_SPA_REPS = 200, 500
POWER_BP = (0.0, 0.5, 1.0, 2.0)  # known daily edge, basis points
FIXED_BLOCK = 20
TOL = 1e-4  # 0.01 pp


# ----------------------------------------------------------------------------- inputs
def daily(r):
    nav = r["_nav"]
    return nav[1:] / nav[:-1] - 1


def sharpe(x):
    x = np.asarray(x, dtype=float)
    return float(np.mean(x) / np.std(x, ddof=1) * np.sqrt(252))


def sized(on, factor, vol, target):
    """fe_signals.sized_wants with a free vol target: vt = min(1, target/vol) (vol<=0 or NaN -> 1),
    raw = on * factor * vt, total cap CAP. target = inf means no vol target."""
    vol = np.asarray(vol, dtype=float)
    pos = vol > 0
    vt = np.where(pos, np.minimum(1.0, target / np.where(pos, vol, 1.0)), 1.0)
    raw = np.asarray(on, dtype=float) * np.asarray(factor, dtype=float) * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def e2_vote(price, tsm, don):
    """E2 vote with parametric momentum (TSM) and Donchian (DON) windows; SMA200 unchanged."""
    s = fe_signals.sma_up(price, fe_config.SMA_N).astype(int)
    t = fe_signals.tsm_up_price(price, tsm).astype(int)
    d = fe_signals.don_up(price, don).astype(int)
    return ((s + t + d) >= 2)[0]


def build(spread):
    """fe_data.main_inputs(spread), plus the intermediate series the arms need."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_config.EXP_Q)
    price = q[1]
    vol = fe_signals.vol_n(c)[0]
    e2 = fe_signals.e2_up(price)[0]
    comps = fe_signals.early_components(price)
    w_e2 = fe_signals.arm_signals(price, e2, vol, "E2", comps)[0][0]
    w_r2 = fe_signals.arm_signals(price, e2, vol, "R2", comps)[0][0]
    return dict(days=days, arr=arr, o=o, c=c, price=price, vol=vol, e2=e2, comps=comps, w_e2=w_e2, w_r2=w_r2)


def spy_total_return(arr):
    """SPY total-return index on the main calendar (1x synthetic_2x with zero expense/financing)."""
    s = arr["SPY"]
    o, c, dv = s[0], s[1], np.nan_to_num(s[2])
    prev = c[:-1]
    r_cc = (c[1:] + dv[1:]) / prev - 1
    r_on = (o[1:] + dv[1:]) / prev - 1
    close = np.concatenate([[1.0], np.cumprod(1 + r_cc)])
    opn = np.concatenate([[1.0], close[:-1] * (1 + r_on)])
    return opn, close


# ----------------------------------------------------------------------------- arms
def arm_table(B):
    """name -> (target weights, fill rule). Rule C0 = plain 0.5 pp band (R2 production execution)."""
    n = len(B["days"])
    ones = np.ones(n)
    e2 = B["e2"].astype(float)
    vol = B["vol"]
    W = OrderedDict()
    W["a_const_2x"] = (np.full(n, CAP), "C0")
    W["b_vote_2x_noVT"] = (sized(e2, ones, vol, np.inf), "C0")
    W["c_VT_always_in"] = (sized(ones, ones, vol, DEFAULT_VT), "C0")
    W["d_E2"] = (B["w_e2"], "C0")
    W["e_R2"] = (B["w_r2"], "C0")
    W["f_K"] = (B["w_r2"], "K")
    return W


def tax_window(days, fx, ufe, o, c, w, arm, a, b):
    i0, i1 = fe_study.window_indices(days, a, b)
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    r = fe_sim.simulate(o, c, w, env, CAPITAL, i0, i1, arm=arm, mode="progressive", m=M)
    return {
        "after_tax_cagr": float(r["cagr_usd_liquidated"]),
        "max_dd_after_tax": float(r["max_dd_nav_usd"]),
        "tax_usd": float(r["tax_total_usd_at_payment_fx"]),
        "sells_per_year": float(r["sells"] / r["years"]),
    }


def evaluate(days, fx, ufe, o, c, w, arm, env_pre, taxed=True):
    T = len(days) - 1
    r = fe_sim.simulate(o, c, w, env_pre, CAPITAL, START, T, arm=arm, mode="pre_tax", m=M)
    st = fe_study.pre_stats(days, START, T, r)
    out = {"pre_tax": {k: float(st[k]) for k in ("cagr", "max_dd", "sells_per_year", "turnover_annual")}}
    if taxed:
        out["after_tax"] = {wn: tax_window(days, fx, ufe, o, c, w, arm, a, b) for wn, (a, b) in WINDOWS.items()}
        out["halves"] = {hn: tax_window(days, fx, ufe, o, c, w, arm, a, b) for hn, (a, b) in HALVES.items()}
    return out, daily(r)


# ----------------------------------------------------------------------------- harness (control)
def harness():
    ref = json.loads(BH1_RESULTS.read_text())
    B = build(0.0)
    days = B["days"]
    m_days, m_o, m_c, m_we2, m_wr2, m_e2 = fe_data.main_inputs(0.0)
    checks = {
        "build_eq_main_inputs_w_r2": bool(np.array_equal(B["w_r2"], m_wr2)),
        "build_eq_main_inputs_w_e2": bool(np.array_equal(B["w_e2"], m_we2)),
        "sized_default_eq_E2": bool(np.array_equal(sized(B["e2"].astype(float), np.ones(len(days)), B["vol"], DEFAULT_VT), m_we2)),
        "e2_vote_default_eq_e2_up": bool(np.array_equal(e2_vote(B["price"], *DEFAULT_WIN), B["e2"])),
    }
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env_pre = fe_sim.RunEnv(days, fx, ufe, START, T)
    anchors = []
    Bc = build(CAL_SPREAD)
    W = arm_table(B)
    for label, arm, w, ref_arm in (("K", "K", B["w_r2"], "K"), ("R2_ref_C0", "C0", B["w_r2"], "C0")):
        base, _ = evaluate(days, fx, ufe, B["o"], B["c"], w, arm, env_pre, taxed=True)
        ref_m2 = ref["pre_tax_main"]["base"]["m2"][ref_arm]
        anchors.append((f"{label} pre_tax_base_cagr", base["pre_tax"]["cagr"], ref_m2["cagr"]))
        anchors.append((f"{label} pre_tax_base_max_dd", base["pre_tax"]["max_dd"], ref_m2["max_dd"]))
        for wn in WINDOWS:
            anchors.append((f"{label} after_tax_{wn} cagr", base["after_tax"][wn]["after_tax_cagr"],
                            ref["after_tax_windows"]["100k"][wn][ref_arm]["after_tax_cagr"]))
            anchors.append((f"{label} after_tax_{wn} max_dd", base["after_tax"][wn]["max_dd_after_tax"],
                            ref["after_tax_windows"]["100k"][wn][ref_arm]["max_dd_after_tax"]))
    env_cal = fe_sim.RunEnv(Bc["days"], fx, ufe, START, T)
    calr = fe_sim.simulate(Bc["o"], Bc["c"], Bc["w_r2"], env_cal, CAPITAL, START, T, arm="K", mode="pre_tax", m=M)
    cst = fe_study.pre_stats(days, START, T, calr)
    anchors.append(("K pre_tax_cal_cagr", cst["cagr"], ref["pre_tax_main"]["calibrated"]["m2"]["K"]["cagr"]))
    anchors.append(("K pre_tax_cal_max_dd", cst["max_dd"], ref["pre_tax_main"]["calibrated"]["m2"]["K"]["max_dd"]))
    ok = True
    print("identity checks:", checks)
    ok &= all(checks.values())
    for name, mine, theirs in anchors:
        diff = abs(mine - theirs)
        passed = diff <= TOL
        ok &= passed
        print(f"  {name:40s} mine={mine:.6f} ref={theirs:.6f} diff_pp={diff * 100:.5f} {'PASS' if passed else 'FAIL'}")
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"], cwd=ROOT,
                       capture_output=True, text=True)
    parity_ok = p.returncode == 0 and "parity PASS" in p.stdout
    print("parity --quick:", "PASS" if parity_ok else "FAIL")
    ok &= parity_ok
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- study
def lag_test(B, env_pre):
    """Item 28a: circular shifts of K's target series against the returns (fixed seed)."""
    days, o, c = B["days"], B["o"], B["c"]
    T = len(days) - 1
    L = len(B["w_r2"])
    base = fe_sim.simulate(o, c, B["w_r2"], env_pre, CAPITAL, START, T, arm="K", mode="pre_tax", m=M)
    k_cagr = float(fe_study.pre_stats(days, START, T, base)["cagr"])
    k_sh = sharpe(daily(base))
    rng = np.random.default_rng(SEED)
    offs = rng.integers(LAG_MIN, L - LAG_MIN + 1, size=LAG_N)
    null_cagr = np.empty(LAG_N)
    null_sh = np.empty(LAG_N)
    for j, s in enumerate(offs):
        r = fe_sim.simulate(o, c, np.roll(B["w_r2"], int(s)), env_pre, CAPITAL, START, T, arm="K", mode="pre_tax", m=M)
        null_cagr[j] = fe_study.pre_stats(days, START, T, r)["cagr"]
        null_sh[j] = sharpe(daily(r))
    return {
        "seed": SEED, "n_shifts": LAG_N, "min_offset": LAG_MIN, "offset_range": [LAG_MIN, L - LAG_MIN],
        "K_cagr": k_cagr, "K_sharpe": k_sh,
        "null_cagr_median": float(np.median(null_cagr)), "null_cagr_p05": float(np.quantile(null_cagr, 0.05)),
        "null_cagr_p95": float(np.quantile(null_cagr, 0.95)),
        "null_sharpe_median": float(np.median(null_sh)), "null_sharpe_p05": float(np.quantile(null_sh, 0.05)),
        "null_sharpe_p95": float(np.quantile(null_sh, 0.95)),
        "K_cagr_percentile": float(100 * np.mean(null_cagr < k_cagr)),
        "K_sharpe_percentile": float(100 * np.mean(null_sh < k_sh)),
    }


def politis_white(x):
    pw = optimal_block_length(np.asarray(x, dtype=float))
    return {"stationary": float(pw["stationary"].iloc[0]), "circular": float(pw["circular"].iloc[0]),
            "fixed_block_used_so_far": FIXED_BLOCK}


def _pv(series, key):
    return float(series[key]) if hasattr(series, "index") else float(series)


def run_spa_mcs(r_bench, models, names, bs):
    # arch's SPA and MCS take LOSSES (lower is better); the series here are returns, so pass -returns.
    # (Amendment A1: the first run passed returns to SPA, which inverts the test direction.)
    spa = SPA(-r_bench, -models, block_size=bs, reps=SPA_REPS, bootstrap="stationary", seed=SEED)
    spa.compute()
    pv = spa.pvalues
    spa_out = {k: float(pv[k]) for k in ("lower", "consistent", "upper")}
    losses = -np.column_stack([r_bench, models])
    cols = ["benchmark_K"] + list(names)
    import pandas as pd

    mcs = MCS(pd.DataFrame(losses, columns=cols), size=MCS_ALPHA, reps=MCS_REPS, block_size=bs, method="R",
              bootstrap="stationary", seed=SEED)
    mcs.compute()
    mp = mcs.pvalues
    mcs_out = {"alpha": MCS_ALPHA, "method": "R", "included": [str(x) for x in mcs.included],
               "pvalues": {str(k): float(mp.loc[k, "Pvalue"]) for k in mp.index}}
    return spa_out, mcs_out


def power_check(r_bench, sd_e, bs):
    rng = np.random.default_rng(SEED + 1)
    out = {}
    for bp in POWER_BP:
        rej = 0
        for rep in range(POWER_REPS):
            z = rng.standard_normal(len(r_bench))
            model = r_bench + bp * 1e-4 + sd_e * z
            s = SPA(-r_bench, -model.reshape(-1, 1), block_size=bs, reps=POWER_SPA_REPS, bootstrap="stationary", seed=rep)
            s.compute()
            rej += int(_pv(s.pvalues, "consistent") <= 0.10)
        out[f"edge_{bp}bp_per_day"] = {"rejection_rate_consistent_p_le_0.10": rej / POWER_REPS, "reps": POWER_REPS}
    return {"sd_noise_daily": float(sd_e), "reps": POWER_REPS, "spa_reps": POWER_SPA_REPS,
            "benchmark": "K pre-tax after-cost returns (observed)", "rows": out}


def expanding(B, fx, ufe, env_pre):
    """Item 34: each year Y >= 2005 choose (vol target, TSM/DON) with the best past after-cost Sharpe
    (pre-tax, K execution, sessions before Y only); years < 2005 use the fixed K configuration."""
    days = B["days"]
    n = len(days)
    T = n - 1
    cfgs = [(win, vt) for win in WIN_GRID for vt in VT_GRID]
    default_k = cfgs.index((DEFAULT_WIN, DEFAULT_VT))
    W = np.zeros((len(cfgs), n))
    for k, (win, vt) in enumerate(cfgs):
        e2k = e2_vote(B["price"], win[0], win[1])
        on, fac = fe_signals.r2_state(B["price"], e2k, B["comps"])
        W[k] = sized(on[0], fac[0], B["vol"], vt)
    rets = np.full((len(cfgs), n), np.nan)
    for k in range(len(cfgs)):
        r = fe_sim.simulate(B["o"], B["c"], W[k], env_pre, CAPITAL, START, T, arm="K", mode="pre_tax", m=M)
        rets[k, START:] = daily(r)
    years = np.array([int(d[:4]) for d in days])
    idx = np.arange(n)
    chosen = {}
    log = []
    for Y in range(EXP_FIRST_YEAR, int(years.max()) + 1):
        mask = (idx >= START) & (years < Y)
        past = [sharpe(rets[k, mask]) for k in range(len(cfgs))]
        k_star = int(np.argmax(past))
        chosen[Y] = k_star
        win, vt = cfgs[k_star]
        log.append({"year": Y, "vol_target": vt, "tsm": win[0], "don": win[1],
                    "past_sharpe": float(past[k_star]), "default_K_past_sharpe": float(past[default_k])})
    kidx = np.array([chosen.get(int(y), default_k) for y in years])
    W_sel = W[kidx, idx]
    r_sel = fe_sim.simulate(B["o"], B["c"], W_sel, env_pre, CAPITAL, START, T, arm="K", mode="pre_tax", m=M)
    pre_sel = fe_study.pre_stats(days, START, T, r_sel)
    fixed_pre = fe_study.pre_stats(days, START, T, fe_sim.simulate(B["o"], B["c"], B["w_r2"], env_pre, CAPITAL,
                                                                  START, T, arm="K", mode="pre_tax", m=M))
    tax_sel = {wn: tax_window(days, fx, ufe, B["o"], B["c"], W_sel, "K", a, b) for wn, (a, b) in WINDOWS.items()}
    tax_fix = {wn: tax_window(days, fx, ufe, B["o"], B["c"], B["w_r2"], "K", a, b) for wn, (a, b) in WINDOWS.items()}
    counts = {}
    for row in log:
        key = f"vt{row['vol_target']}_tsm{row['tsm']}_don{row['don']}"
        counts[key] = counts.get(key, 0) + 1
    switches = sum(1 for a_, b_ in zip(log[:-1], log[1:]) if (a_["vol_target"], a_["tsm"], a_["don"]) != (b_["vol_target"], b_["tsm"], b_["don"]))
    return {
        "selection_years": [EXP_FIRST_YEAR, int(years.max())],
        "default_before_2005": f"vt{DEFAULT_VT}_tsm{DEFAULT_WIN[0]}_don{DEFAULT_WIN[1]}",
        "selections": log,
        "selection_counts": counts,
        "config_switches": switches,
        "expanding": {"pre_tax": {"cagr": float(pre_sel["cagr"]), "max_dd": float(pre_sel["max_dd"])},
                      "after_tax": tax_sel},
        "fixed_K": {"pre_tax": {"cagr": float(fixed_pre["cagr"]), "max_dd": float(fixed_pre["max_dd"])},
                    "after_tax": tax_fix},
        "delta_expanding_minus_fixed": {
            "pre_tax_cagr_pp": float((pre_sel["cagr"] - fixed_pre["cagr"]) * 100),
            "after_tax_cagr_pp": {wn: float((tax_sel[wn]["after_tax_cagr"] - tax_fix[wn]["after_tax_cagr"]) * 100)
                                  for wn in WINDOWS},
        },
    }


def run(show_timing=True):
    t0 = time.time()
    B = build(0.0)
    days = B["days"]
    T = len(days) - 1
    fx, ufe = fe_study.try_market(days)
    env_pre = fe_sim.RunEnv(days, fx, ufe, START, T)
    spy_o, spy_c = spy_total_return(B["arr"])
    n = len(days)
    ones = np.ones(n)

    res = OrderedDict()
    res["meta"] = {"arch_version": arch.__version__, "start": days[START], "end": days[-1], "n_sessions": T - START + 1,
                   "cost_multiplier": M, "capital": CAPITAL, "seeds": {"lag": SEED, "spa_mcs": SEED, "power": SEED + 1}}

    # item 29 / 30 ablation arms + S&P buy-and-hold benchmark
    arms = OrderedDict()
    rets = OrderedDict()
    for name, (w, rule) in arm_table(B).items():
        arms[name], rets[name] = evaluate(days, fx, ufe, B["o"], B["c"], w, rule, env_pre)
    arms["SPX_buy_and_hold"], rets["SPX_buy_and_hold"] = evaluate(days, fx, ufe, spy_o, spy_c, ones, "C0", env_pre)
    # calibrated financing: pre-tax only
    Bc = build(CAL_SPREAD)
    env_cal = fe_sim.RunEnv(Bc["days"], fx, ufe, START, T)
    for name, (w, rule) in arm_table(Bc).items():
        pre_c, _ = evaluate(days, fx, ufe, Bc["o"], Bc["c"], w, rule, env_cal, taxed=False)
        arms[name]["pre_tax_calibrated"] = pre_c["pre_tax"]
    res["item29_ablation"] = {k: {kk: vv for kk, vv in v.items()} for k, v in arms.items()}

    # item 30 gate
    gate = OrderedDict()
    spx = arms["SPX_buy_and_hold"]["halves"]
    for name in arm_table(B):
        g = {}
        for bench_name, bench in (("E2", arms["d_E2"]["halves"]), ("SPX_buy_and_hold", spx)):
            d = {h: arms[name]["halves"][h]["after_tax_cagr"] - bench[h]["after_tax_cagr"] for h in HALVES}
            g[f"delta_vs_{bench_name}_pp"] = {h: float(v * 100) for h, v in d.items()}
            g[f"gate_vs_{bench_name}"] = bool(all(v > 0 for v in d.values()))
        gate[name] = g
    res["item30_gate"] = gate

    # item 28 a: circular lag ; b: Politis-White
    res["item28_circular_lag"] = lag_test(B, env_pre)
    r_K = rets["f_K"]
    pw = politis_white(r_K)
    res["item28_politis_white"] = pw
    bs = max(1, int(round(pw["stationary"])))
    res["item28_politis_white"]["block_used_for_spa_mcs_power"] = bs

    # item 33: SPA (benchmark K) and MCS over the same set
    _, rets_a1u = evaluate(days, fx, ufe, B["o"], B["c"], B["w_r2"], "A1u", env_pre, taxed=False)
    _, rets_h2 = evaluate(days, fx, ufe, B["o"], B["c"], B["w_r2"], "H2", env_pre, taxed=False)
    model_names = ["a_const_2x", "b_vote_2x_noVT", "c_VT_always_in", "d_E2", "e_R2", "A1u_band_h2", "H2_band_h2"]
    model_rets = [rets["a_const_2x"], rets["b_vote_2x_noVT"], rets["c_VT_always_in"], rets["d_E2"], rets["e_R2"],
                  rets_a1u, rets_h2]
    models = np.column_stack(model_rets)
    spa_out, mcs_out = run_spa_mcs(r_K, models, model_names, bs)
    res["item33_spa"] = {"benchmark": "f_K", "models": model_names, "block": bs, "reps": SPA_REPS, **spa_out}
    res["item33_mcs"] = mcs_out
    # Amendment A2: sensitivity at the fixed block 20 used in earlier studies (reported next to PW)
    spa20, mcs20 = run_spa_mcs(r_K, models, model_names, FIXED_BLOCK)
    res["item33_sensitivity_block20"] = {"block": FIXED_BLOCK, "spa": spa20, "mcs": mcs20}
    sd_e = float(np.std(rets["e_R2"] - r_K, ddof=1))
    res["item33_power"] = power_check(r_K, sd_e, bs)

    # item 34: expanding window
    res["item34_expanding"] = expanding(B, fx, ufe, env_pre)

    RESULTS.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable) + "\n")
    elapsed = time.time() - t0
    lines = [
        "gates_v2 SUMMARY (numbers from RESULTS.json)",
        f"wall_clock_seconds_run: {elapsed:.1f}",
        f"arch {arch.__version__}",
        f"K pre-tax CAGR {arms['f_K']['pre_tax']['cagr'] * 100:.3f}% max_dd {arms['f_K']['pre_tax']['max_dd'] * 100:.3f}%",
        f"lag K_cagr_percentile {res['item28_circular_lag']['K_cagr_percentile']:.2f}",
        f"PW stationary {pw['stationary']:.3f} circular {pw['circular']:.3f} (fixed 20)",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return res


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if hasattr(x, "to_dict"):
        return x.to_dict()
    raise TypeError(f"not serialisable: {type(x)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.harness:
        sys.exit(0 if harness() else 1)
    if a.run:
        run()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
