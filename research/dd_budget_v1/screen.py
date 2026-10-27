"""dd_budget_v1: does a drawdown-based risk budget on the underlying (QQQ) signal improve K after Turkish tax?

Pre-registered in PREREGISTRATION.md (arms U1-U3 and every parameter fixed before any non-control arm ran).

    python -m research.dd_budget_v1.screen --harness   # K / R2 anchors, signal checks, parity
    python -m research.dd_budget_v1.screen --run       # arms, gates, bootstraps -> RESULTS.json, SUMMARY.txt

Simulation goes through fe_sim.simulate; the bootstrap driver is fe_boot's `_boot_chunk` kernel called once
per arm on identical block draws. This file and ddbudget.py only build target-weight arrays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

import numba
import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study, fe_tax
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PROGRESSIVE
from research.gates_v1.stats import dsr, sample_moments, var_trials_floor
from research.dd_budget_v1 import ddbudget

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BH1_RESULTS = ROOT / "research/band_h2_combo_v1/RESULTS.json"
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

START = fe_config.START
CAP = fe_config.CAP
M = 2
CAPITAL = 100_000.0
CAL_SPREAD = fe_config.FIN["calibrated"]
EXTRA_FIN = fe_config.EXTRA_FIN
WINDOWS = OrderedDict(fe_config.TAX_WINDOWS)
HALVES = OrderedDict([("pre_2020", ("2000-03-09", "2019-12-31")), ("post_2020", ("2020-01-01", "2026-09-29"))])
OOS_KEYS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
SEED = 20261011           # G5, G8 and the B1/B2 bootstraps (same seed as the fast_reentry gates)
BLOCK = 20
N_PRE, N_TAX = 5000, 1000
G8_REPS = 2000
N_ARMS_PREREG = 3         # U1-U3
N_LEDGER_BEFORE = 375     # trials already on the ledger, as instructed for this worker
N_TRIALS = N_LEDGER_BEFORE + N_ARMS_PREREG
DSR_BAR = 0.95            # gates_v1 bar, reported (informational)

ARMS = ("K", "R2", "U1", "U2", "U3")
RULE_OF = {"K": "K", "R2": "C0", "U1": "K", "U2": "K", "U3": "K"}
SARMS = ("U1", "U2", "U3")
INPUT_FILES = [fe_config.BARS, fe_config.INDEX_CSV, fe_config.GSPC_CSV, fe_config.FX_CSV, fe_config.UFE_CSV,
               fe_config.HIST / "stress_robustness_v1_dtb3.csv", fe_config.HIST / "ewma_band_v1_tb3ms.csv"]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- inputs
def daily(nav):
    nav = np.asarray(nav, dtype=float)
    return nav[1:] / nav[:-1] - 1


def month_index_of(days):
    return np.array([fe_tax.month_index(d) for d in days], dtype=np.int64)


def main_state(spread):
    """Main QQQ calendar, one financing. The U arms use the QQQ close `price` (financing-free) and K's vol20."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_config.EXP_Q)
    price = np.atleast_2d(q[1])
    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    on, factor = fe_signals.r2_state(price, e2, comps)
    c2 = np.atleast_2d(c)
    w_k = fe_signals.sized_wants(on, factor, fe_signals.vol_n(c2))
    fx, ufe = fe_study.try_market(days)
    return {"days": days, "fx": fx, "ufe": ufe, "o": o, "c": c, "c2": c2, "on2": on, "factor2": factor,
            "price": price, "mth": month_index_of(days),
            "on": np.asarray(on)[0], "factor": np.asarray(factor)[0], "w_k2": w_k, "w_k": np.asarray(w_k)[0]}


def prepare():
    """Base and calibrated main states with every arm's weights (no calibrated scalar: the budget is fixed)."""
    Sb = main_state(0.0)
    ratios = ddbudget.arm_ratios(Sb["price"], Sb["mth"])
    out = {}
    for fin, S in (("base", Sb), ("cal", main_state(CAL_SPREAD))):
        A = ddbudget.weights_from_ratios(ratios, S["c2"], S["on2"], S["factor2"], S["w_k2"])
        W = {"K": S["w_k"], "R2": S["w_k"]} | {a: np.asarray(A[a])[0] for a in SARMS}
        S["W"] = W
        S["ratios"] = ratios
        out[fin] = S
    return out["base"], out["cal"]


def run_w(S, w, rule, mode, m=M, capital=CAPITAL, window=None):
    days = S["days"]
    if window is None:
        i0, i1 = START, len(days) - 1
    else:
        i0, i1 = fe_study.window_indices(days, *window)
    env = fe_sim.RunEnv(days, S["fx"], S["ufe"], i0, i1)
    return fe_sim.simulate(S["o"], S["c"], w, env, capital, i0, i1, arm=rule, mode=mode, m=m)


def pre_block(S, w, rule, m):
    r = run_w(S, w, rule, "pre_tax", m=m)
    st = fe_study.pre_stats(S["days"], START, len(S["days"]) - 1, r)
    out = {"cagr": st["cagr"], "max_dd": st["max_dd"], "calmar": st["calmar"],
           "sells_per_year": st["sells_per_year"], "turnover_annual": st["turnover_annual"],
           "sub": {k: v["cagr"] for k, v in st["subperiods"].items()}, "cagr_2017_end": st["2017_end"]["cagr"]}
    return out, daily(r["_nav"])


def pre_halves(S, w, rule):
    """Pre-tax CAGR of each half (its own run, same convention as the window CAGR)."""
    days = S["days"]
    out = {}
    for hn, win in HALVES.items():
        i0, i1 = fe_study.window_indices(days, *win)
        r = run_w(S, w, rule, "pre_tax", window=win)
        nav = np.asarray(r["_nav"], dtype=float)
        yrs = fe_study.calendar_years(days[i0 - 1], days[i1])
        out[hn] = float(fe_study.cagr(nav[0], nav[-1], yrs))
    return out


def tax_block(S, w, rule):
    out = {"windows": {}, "halves": {}}
    for wn, win in WINDOWS.items():
        r = run_w(S, w, rule, "progressive", window=win)
        out["windows"][wn] = {"after_tax_cagr": float(r["cagr_usd_liquidated"]), "max_dd_after_tax": float(r["max_dd_nav_usd"]),
                              "sells_per_year": float(r["sells"] / r["years"])}
    for hn, win in HALVES.items():
        r = run_w(S, w, rule, "progressive", window=win)
        out["halves"][hn] = {"after_tax_cagr": float(r["cagr_usd_liquidated"])}
    full = run_w(S, w, rule, "progressive")
    return out, daily(full["_nav"])


# ----------------------------------------------------------------------------- bootstrap
def chunk_states(q, y, pos):
    """fe_boot.chunk_legs with the QQQ path close kept (the U arms need the price path); same arithmetic."""
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
    comps = fe_signals.early_components(pc)
    on, factor = fe_signals.r2_state(pc, e2, comps)
    yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
    zeros = np.zeros_like(qc)
    so, sc = fe_signals.synthetic_2x(qo, qc, zeros, yyp + fe_config.FIN["base"], fe_config.EXP_Q)
    so_c, sc_c = fe_signals.synthetic_2x(qo, qc, zeros, yyp + fe_config.FIN["calibrated"], fe_config.EXP_Q)
    return {"pc": pc, "on": on, "factor": factor, "so": so, "sc": sc, "so_c": so_c, "sc_c": sc_c}


def _call_kernel(win, rule, so, sc, wb, so_c, sc_c, wc, p0, W, n_pre, n_tax, out_pre, out_at):
    env = win.env
    band = np.array([fe_sim.RULE[rule]["band"]], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[rule]["usd_rule"]], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[rule]["defer"]], dtype=np.bool_)
    mode_ids = np.array([MODE_PROGRESSIVE, MODE_FLAT], dtype=np.int64)
    cap_at = np.array([fe_config.TAX_CAPITALS["100k"]], dtype=np.float64)
    out_anchor = np.empty((2, W), dtype=np.float64)
    fe_boot._boot_chunk(
        so, sc, wb, so_c, sc_c, wc, wb, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
        env.pay_ptr, env.pay_yr, env.pay_part, fe_config.TAX_CAPITALS["100k"], cap_at, win.i0, win.i1,
        fe_sim.COST_PER_SIDE * M, win.yrs, env.Y0, env.M_lo, band, usd_rule, defer, mode_ids, p0, n_pre, n_tax,
        False, out_pre, out_at, out_anchor, np.zeros((0, 6), dtype=np.float64))


def boot_all(win, seed=SEED, block=BLOCK, n_pre=N_PRE, n_tax=N_TAX, chunk=fe_boot.DEFAULT_CHUNK, arms=ARMS):
    """Common block draws (fe_boot.draw_positions) for every arm; per-arm weights rebuilt on each path.

    The U arms' budget ratios depend only on the QQQ path, so they are computed once per chunk and shared by
    both financings. The month index for U3 is the bootstrap calendar's (win.env.mth_of), the same table the
    tax engine reads for each path session."""
    n_paths = max(n_pre, n_tax)
    pre = {(f, a): np.empty(n_pre) for f in ("base", "cal") for a in arms}
    at = {(md, a): np.empty(n_tax) for md in ("progressive", "flat") for a in arms}
    rng = np.random.default_rng(seed)
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
        sta = chunk_states(win.q, win.y, pos)
        mth_cal = np.asarray(win.env.mth_of)
        T_path = sta["pc"].shape[1]        # fe_boot draws L + EXT positions, so paths are longer than the calendar
        mth = np.concatenate([mth_cal, np.full(max(0, T_path - mth_cal.shape[0]), mth_cal[-1])])[:T_path]
        ratios = ddbudget.arm_ratios(sta["pc"], mth)
        wk = {}
        for fin, sc in (("base", sta["sc"]), ("cal", sta["sc_c"])):  # on/factor are financing-free
            w_k = fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sc))
            A = ddbudget.weights_from_ratios(ratios, sc, sta["on"], sta["factor"], w_k)
            wk[fin] = {"K": w_k, "R2": w_k} | {a: A[a] for a in SARMS}
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for a in arms:
            out_pre = np.empty((2, 1, W))
            out_at = np.empty((1, 2, 1, W))
            _call_kernel(win, RULE_OF[a], sta["so"], sta["sc"], wk["base"][a], sta["so_c"], sta["sc_c"],
                         wk["cal"][a], p0, W, n_pre, n_tax, out_pre, out_at)
            if npre:
                pre[("base", a)][p0:p0 + npre] = out_pre[0, 0, :npre]
                pre[("cal", a)][p0:p0 + npre] = out_pre[1, 0, :npre]
            if ntax:
                at[("progressive", a)][p0:p0 + ntax] = out_at[0, 0, 0, :ntax]
                at[("flat", a)][p0:p0 + ntax] = out_at[0, 1, 0, :ntax]
    return pre, at


def delta_stats(a, b):
    d = np.asarray(a) - np.asarray(b)
    return {"median_delta_pp": float(np.median(d) * 100), "mean_delta_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)), "p05_pp": float(np.percentile(d, 5) * 100),
            "p95_pp": float(np.percentile(d, 95) * 100)}


# ----------------------------------------------------------------------------- OOS windows
def oos_run(arms=ARMS):
    """Six OOS windows, pre-tax, calibrated financing, m = 2, capital 1, own E2/R2 state and own vol histories."""
    out = {}
    for key in OOS_KEYS:
        if key == "GSPC_1957_1999":
            days, o, c, y, start = fe_data.load_gspc()
        else:
            days, o, c, y, start = fe_data.load_market(key)
        so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, fe_config.EXP_Q)
        price = np.atleast_2d(c)
        e2 = fe_signals.e2_up(price)
        comps = fe_signals.early_components(price)
        on, factor = fe_signals.r2_state(price, e2, comps)
        w_k = fe_signals.sized_wants(on, factor, fe_signals.vol_n(sc))
        ratios = ddbudget.arm_ratios(price, month_index_of(days))
        A = ddbudget.weights_from_ratios(ratios, sc, on, factor, w_k)
        W = {"K": np.asarray(w_k)[0], "R2": np.asarray(w_k)[0]} | {a: np.asarray(A[a])[0] for a in SARMS}
        env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
        res = {}
        navs = {}
        for arm in arms:
            r = fe_sim.simulate(so, sc, np.asarray(W[arm]), env, 1.0, start, len(days) - 1, arm=RULE_OF[arm],
                                mode="pre_tax", m=M, rule_index=False)
            st = fe_study.window_stats(r["_nav"], days, start)
            res[arm] = {"cagr": float(st["cagr"]), "max_dd": float(st["max_dd"])}
            navs[arm] = np.asarray(r["_nav"], dtype=float)
        out[key] = {"sessions": len(days) - start, "start": days[start], "arms": res, "_navs": navs}
    return out


# ----------------------------------------------------------------------------- harness (controls and signal checks)
def _brute_budgets(p, win=ddbudget.WIN, tail=ddbudget.TAIL_N):
    ui = np.full(len(p), np.nan)
    cd = np.full(len(p), np.nan)
    for t in range(win - 1, len(p)):
        w = p[t - win + 1:t + 1]
        dep = 1.0 - w / np.maximum.accumulate(w)
        ui[t] = np.sqrt(np.mean(dep ** 2))
        cd[t] = np.mean(np.sort(dep)[win - tail:])
    return ui, cd


def signal_checks():
    """Synthetic-data checks of ddbudget (no real data): kernels equal explicit definitions."""
    rng = np.random.default_rng(11)
    T = 700
    P = np.exp(np.cumsum(rng.normal(0.0004, 0.015, (3, T)), axis=1))
    ui, cd = ddbudget.drawdown_budgets(P)
    ok_dd = all(np.allclose(np.r_[np.full(ddbudget.WIN - 1, np.nan), _brute_budgets(P[r])[0][ddbudget.WIN - 1:]],
                            ui[r], equal_nan=True, rtol=1e-12, atol=0)
                and np.allclose(_brute_budgets(P[r])[1], cd[r], equal_nan=True, rtol=1e-12, atol=0) for r in range(3))
    X = np.where(rng.random((3, T)) < 0.05, np.nan, ui)
    em = ddbudget.expanding_median(X)
    ok_med = True
    for r in range(3):
        for t in range(T):
            v = X[r, :t + 1]
            v = v[np.isfinite(v)]
            ref = np.median(v) if (len(v) and np.isfinite(X[r, t])) else np.nan
            ok_med &= bool((np.isnan(ref) and np.isnan(em[r, t])) or abs(ref - em[r, t]) < 1e-12)
    mth = np.repeat(np.arange(T // 21 + 1), 21)[:T]
    x = rng.random((2, T))
    h = ddbudget.monthly_hold(x, mth)
    ok_mh = True
    for t in range(T):
        me = [s for s in range(t + 1) if s < T - 1 and mth[s] != mth[s + 1]]
        ok_mh &= bool(np.all(np.isnan(h[:, t]))) if not me else bool(np.array_equal(h[:, t], x[:, me[-1]]))
    c = P * 100.0
    on = rng.random((3, T)) < 0.8
    fac = np.where(on, rng.choice([0.5, 1.0], (3, T)), 0.0)
    wk = fe_signals.sized_wants(on, fac, fe_signals.vol_n(c))
    ok_size = bool(np.allclose(np.nan_to_num(wk, nan=-9), np.nan_to_num(
        ddbudget.sized_target(on, fac, fe_signals.vol_n(c), fe_config.VOL_TARGET), nan=-9)))
    A = ddbudget.weights_from_ratios(ddbudget.arm_ratios(P, np.arange(T)), c, on, fac, wk)
    ok_warm = bool(np.array_equal(A["U1"][:, :ddbudget.WARM], wk[:, :ddbudget.WARM]))
    ok_clip = bool(np.nanmin(A["U1"]) >= 0 and np.nanmax(A["U1"]) <= CAP + 1e-12)
    checks = {"drawdown_kernels_eq_explicit": ok_dd, "expanding_median_eq_np_median": ok_med,
              "monthly_hold_eq_explicit": ok_mh, "sized_target_eq_K_sizing_at_0.35": ok_size,
              "warm_up_eq_K_weight": ok_warm, "weights_in_0_cap": ok_clip}
    print("signal checks:", checks)
    return all(checks.values())


def harness():
    ref = json.loads(BH1_RESULTS.read_text())
    Sb, Sc = prepare()
    ok = signal_checks()
    m_days, _o, _c, _we2, m_wr2, _e2 = fe_data.main_inputs(0.0)
    checks = {}
    checks["K_weights_eq_main_inputs_R2"] = bool(np.array_equal(Sb["W"]["K"], np.asarray(m_wr2).reshape(-1)))
    rng = np.random.default_rng(1)
    win = fe_boot.BootWindow()
    pos = fe_boot.draw_positions(rng, BLOCK, 4, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
    legs, _e2b, _wb = fe_boot.chunk_legs(win.q, win.y, pos)
    sta = chunk_states(win.q, win.y, pos)
    checks["K_weights_eq_fe_boot_chunk_legs"] = bool(np.array_equal(
        fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc"])), legs["base"][2]))
    checks["K_weights_cal_eq_fe_boot_chunk_legs"] = bool(np.array_equal(
        fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc_c"])), legs["calibrated"][2]))
    ok &= all(checks.values())
    print("identity checks:", checks)

    ctrl = {}
    for arm, rule in (("K", "K"), ("R2", "C0")):
        pre, _ = pre_block(Sb, Sb["W"][arm], rule, M)
        ctrl[arm] = {"pre": pre, "tax": tax_block(Sb, Sb["W"][arm], rule)[0]}
    cal_k, _ = pre_block(Sc, Sc["W"]["K"], "K", M)
    anchors = []
    for arm in ("K", "R2"):
        ref_arm = "K" if arm == "K" else "C0"
        pre, tb = ctrl[arm]["pre"], ctrl[arm]["tax"]
        anchors.append((f"{arm} pre_tax_base_cagr", pre["cagr"], ref["pre_tax_main"]["base"]["m2"][ref_arm]["cagr"]))
        anchors.append((f"{arm} pre_tax_base_max_dd", pre["max_dd"], ref["pre_tax_main"]["base"]["m2"][ref_arm]["max_dd"]))
        for wn in WINDOWS:
            anchors.append((f"{arm} after_tax_{wn}", tb["windows"][wn]["after_tax_cagr"],
                            ref["after_tax_windows"]["100k"][wn][ref_arm]["after_tax_cagr"]))
    anchors.append(("K pre_tax_cal_cagr", cal_k["cagr"], ref["pre_tax_main"]["calibrated"]["m2"]["K"]["cagr"]))
    anchors.append(("K pre_tax_cal_max_dd", cal_k["max_dd"], ref["pre_tax_main"]["calibrated"]["m2"]["K"]["max_dd"]))
    for name, mine, theirs in anchors:
        diff = abs(mine - theirs)
        passed = diff <= 1e-4
        ok &= passed
        print(f"  {name:36s} mine={mine:.6f} ref={theirs:.6f} diff_pp={diff * 100:.5f} {'PASS' if passed else 'FAIL'}")
    _pre, dK = pre_block(Sb, Sb["W"]["K"], "K", M)
    sm = sample_moments(dK)
    d365 = dsr(sm["sr"], sm["T"], sm["skew"], sm["kurt"], 365, var_trials_floor(sm["T"]))["dsr"]
    print(f"  K daily T={sm['T']} (gates_v1: 6679) DSR(N=365)={d365:.4f} (gates_v1: 0.711)")
    ok &= sm["T"] == 6679 and abs(d365 - 0.711) < 0.001
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"], cwd=ROOT,
                       capture_output=True, text=True)
    parity_ok = p.returncode == 0 and "parity PASS" in p.stdout
    print("parity --quick:", "PASS" if parity_ok else "FAIL")
    ok &= parity_ok
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- the study
def g8_pooled(oos):
    """Reduced G8 (pre-registered): block-20 circular bootstrap of each M1-M5 daily Delta vs K."""
    rng = np.random.default_rng(SEED)
    res = {}
    for arm in ARMS:
        if arm == "K":
            continue
        dd = {}
        for key in ("M1", "M2", "M3", "M4", "M5"):
            dd[key] = daily(oos[key]["_navs"][arm]) - daily(oos[key]["_navs"]["K"])
        stats = np.empty(G8_REPS)
        for rep in range(G8_REPS):
            per = []
            for key in ("M1", "M2", "M3", "M4", "M5"):
                x = dd[key]
                n = x.shape[0]
                nb = -(-n // BLOCK)
                starts = rng.integers(0, n, nb)
                idx = (starts[:, None] + np.arange(BLOCK)[None, :]).ravel() % n
                per.append(np.mean(x[idx[:n]]) * 252)
            stats[rep] = np.mean(per)
        res[arm] = {"median_annualised_delta_pp": float(np.median(stats) * 100), "share_ge_0": float(np.mean(stats >= 0)),
                    "reps": G8_REPS, "statistic": "mean over M1..M5 of 252 x mean daily Delta (arithmetic, pp)"}
    return res


def exposure(S, w, pre2010_only=False):
    """Mean QQQ-equivalent exposure (2 x QLD weight) on vote-on days."""
    t = np.arange(len(S["days"]))
    sel = S["on"] & (t >= START)
    if pre2010_only:
        sel &= np.array([d < "2010-01-01" for d in S["days"]])
    return float(np.mean(2.0 * np.asarray(w)[sel]))


def run():
    t0 = time.time()
    Sb, Sc = prepare()
    S = {"base": Sb, "cal": Sc}
    win = fe_boot.BootWindow()
    res = OrderedDict()
    res["meta"] = {"start": Sb["days"][START], "end": Sb["days"][-1], "cost_multiplier": M,
                   "capital": CAPITAL, "seed": SEED, "block": BLOCK, "n_pre": N_PRE, "n_tax": N_TAX,
                   "n_trials_dsr": N_TRIALS, "n_trials_ledger_before": N_LEDGER_BEFORE, "dsr_bar": DSR_BAR,
                   "budget": {"window": ddbudget.WIN, "tail_n": ddbudget.TAIL_N, "warm": ddbudget.WARM,
                              "clip": [ddbudget.CLIP_LO, ddbudget.CLIP_HI], "target_base": fe_config.VOL_TARGET}}
    res["provenance"] = provenance()

    pre, tax, daily_pre, daily_tax, halves_pre, expo = {}, {}, {}, {}, {}, {}
    for arm in ARMS:
        pre[arm] = {}
        for fin in ("base", "cal"):
            for m in (1, 2):
                b, dly = pre_block(S[fin], S[fin]["W"][arm], RULE_OF[arm], m)
                pre[arm][f"{fin}_m{m}"] = b
                if fin == "base" and m == M:
                    daily_pre[arm] = dly
        tax[arm], daily_tax[arm] = tax_block(Sb, Sb["W"][arm], RULE_OF[arm])
        halves_pre[arm] = pre_halves(Sb, Sb["W"][arm], RULE_OF[arm])
        expo[arm] = {"mean_qqq_equiv_exposure_vote_on": exposure(Sb, Sb["W"][arm]),
                     "mean_qqq_equiv_exposure_vote_on_pre2010": exposure(Sb, Sb["W"][arm], pre2010_only=True),
                     "mean_qqq_equiv_exposure_all_days": float(np.mean(2.0 * np.asarray(Sb["W"][arm])[START:]))}
    res["controls_and_arms_pre_tax"] = pre
    res["controls_and_arms_after_tax_100k"] = tax
    res["pre_tax_halves_cagr"] = halves_pre
    res["exposure"] = expo
    res["budget_ratio_summary_main"] = {}
    for a in ddbudget.ARM_NAMES:
        rr = np.asarray(Sb["ratios"][a])[0][START:]
        res["budget_ratio_summary_main"][a] = {
            "mean": float(np.nanmean(rr)),
            "share_at_clip_low": float(np.nanmean(rr <= ddbudget.CLIP_LO + 1e-12)),
            "share_at_clip_high": float(np.nanmean(rr >= ddbudget.CLIP_HI - 1e-12))}

    pre_boot, at_boot = boot_all(win)
    boots = {}
    for arm in ARMS:
        if arm == "K":
            continue
        boots[arm] = {
            "pre_base": delta_stats(pre_boot[("base", arm)], pre_boot[("base", "K")]),
            "pre_cal": delta_stats(pre_boot[("cal", arm)], pre_boot[("cal", "K")]),
            "B1_progressive": delta_stats(at_boot[("progressive", arm)], at_boot[("progressive", "K")]),
            "B2_flat20": delta_stats(at_boot[("flat", arm)], at_boot[("flat", "K")]),
            "anchor_cagr_median": {"K_pre_base": float(np.median(pre_boot[("base", "K")])),
                                   "K_B1": float(np.median(at_boot[("progressive", "K")])),
                                   "K_B2": float(np.median(at_boot[("flat", "K")]))},
        }
    res["bootstrap_vs_K"] = boots

    oos = oos_run()
    res["oos_calibrated_m2"] = {k: {"start": v["start"], "sessions": v["sessions"], "arms": v["arms"]} for k, v in oos.items()}
    res["g8_pooled_oos_reduced"] = g8_pooled(oos)

    gates = OrderedDict()
    K = "K"

    def g1_ok(a, k):
        return a["cagr"] >= k["cagr"] or (a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)

    for arm in SARMS:
        g = {}
        g["G1"] = bool(all(g1_ok(pre[arm][f"{f}_m2"], pre[K][f"{f}_m2"]) for f in ("base", "cal")))
        g["G2"] = bool(all(pre[arm][f"{f}_m2"]["max_dd"] <= pre[K][f"{f}_m2"]["max_dd"] + 0.01 for f in ("base", "cal")))
        g["G3"] = bool(all(pre[arm][f"{f}_m2"]["cagr_2017_end"] >= pre[K][f"{f}_m2"]["cagr_2017_end"] - 0.01 for f in ("base", "cal")))
        sub_wins = sum(1 for p_ in ("P1", "P2", "P3") if pre[arm]["base_m2"]["sub"][p_] > pre[K]["base_m2"]["sub"][p_])
        g["G4"] = bool(sub_wins >= 2)
        g["G5"] = bool(boots[arm]["pre_base"]["median_delta_pp"] >= 0 and boots[arm]["pre_cal"]["median_delta_pp"] >= 0)
        g["G6"] = bool(all(g1_ok(pre[arm][f"{f}_m1"], pre[K][f"{f}_m1"]) for f in ("base", "cal")))
        d_oos = [oos[k]["arms"][arm]["cagr"] - oos[k]["arms"][K]["cagr"] for k in OOS_KEYS]
        dd_oos = [oos[k]["arms"][arm]["max_dd"] - oos[k]["arms"][K]["max_dd"] for k in OOS_KEYS]
        g["G7"] = bool(np.median(d_oos) >= 0 and sum(1 for x in d_oos if x >= -0.005) >= 5
                       and sum(1 for x in dd_oos if x <= 0.03) >= 5 and max(dd_oos) <= 0.08)
        g["G8"] = bool(res["g8_pooled_oos_reduced"][arm]["median_annualised_delta_pp"] >= 0)
        g["G9"] = bool(pre[arm]["base_m2"]["turnover_annual"] <= 2 * pre[K]["base_m2"]["turnover_annual"])
        at_d = {wn: tax[arm]["windows"][wn]["after_tax_cagr"] - tax[K]["windows"][wn]["after_tax_cagr"] for wn in WINDOWS}
        g["A1_after_tax_all_windows_gt_K"] = bool(all(v > 0 for v in at_d.values()))
        g["A2_B1_median_ge_0_share_ge_55"] = bool(boots[arm]["B1_progressive"]["median_delta_pp"] >= 0 and boots[arm]["B1_progressive"]["share_gt_0"] >= 0.55)
        g["A3_B2_median_ge_0_share_ge_55"] = bool(boots[arm]["B2_flat20"]["median_delta_pp"] >= 0 and boots[arm]["B2_flat20"]["share_gt_0"] >= 0.55)
        hd = {h: tax[arm]["halves"][h]["after_tax_cagr"] - tax[K]["halves"][h]["after_tax_cagr"] for h in HALVES}
        g["A4_pre_post_2020_both_gt_0"] = bool(all(v > 0 for v in hd.values()))
        gates[arm] = {"pass": bool(all(g.values())), **g,
                      "detail": {"after_tax_delta_pp": {k: v * 100 for k, v in at_d.items()},
                                 "halves_after_tax_delta_pp": {k: v * 100 for k, v in hd.items()},
                                 "halves_pre_tax_delta_pp": {h: (halves_pre[arm][h] - halves_pre[K][h]) * 100 for h in HALVES},
                                 "oos_delta_cagr_pp": {k: v * 100 for k, v in zip(OOS_KEYS, d_oos)},
                                 "oos_delta_dd_pp": {k: v * 100 for k, v in zip(OOS_KEYS, dd_oos)},
                                 "sub_periods_beating_K": sub_wins}}
    res["gates"] = gates

    dsr_out = {}
    for arm in ARMS:
        sm = sample_moments(daily_pre[arm])
        sm_t = sample_moments(daily_tax[arm])
        dsr_out[arm] = {"pre_tax_T": sm["T"], "pre_tax_sr_per_period": sm["sr"],
                        "dsr_pre_tax_N378": dsr(sm["sr"], sm["T"], sm["skew"], sm["kurt"], N_TRIALS, var_trials_floor(sm["T"]))["dsr"],
                        "dsr_after_tax_progressive_N378": dsr(sm_t["sr"], sm_t["T"], sm_t["skew"], sm_t["kurt"], N_TRIALS, var_trials_floor(sm_t["T"]))["dsr"]}
    res["dsr"] = dsr_out

    passing = [a for a in gates if gates[a]["pass"]]
    res["verdict"] = {"passing_arms": passing, "production_change": False,
                      "rule": "an arm is a candidate only if every G and A gate passes; otherwise K stays"}
    RESULTS.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable) + "\n")
    elapsed = time.time() - t0
    lines = ["dd_budget_v1 SUMMARY (numbers from RESULTS.json)", f"wall_clock_seconds_run: {elapsed:.1f}",
             f"passing arms: {passing if passing else 'none'}"]
    for arm in ARMS:
        p2 = pre[arm]["base_m2"]
        t = tax[arm]["windows"]
        lines.append(f"{arm}: pre CAGR base {p2['cagr'] * 100:.2f}% max DD {p2['max_dd'] * 100:.2f}% "
                     f"after tax 2000/2010/2017 {t['2000_03_to_2026_09']['after_tax_cagr'] * 100:.2f}/"
                     f"{t['2010_01_to_end']['after_tax_cagr'] * 100:.2f}/{t['2017_01_to_end']['after_tax_cagr'] * 100:.2f}% "
                     f"sells/yr {p2['sells_per_year']:.1f} exposure(vote-on) {expo[arm]['mean_qqq_equiv_exposure_vote_on']:.3f}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return res


def provenance():
    code = {n: sha256(HERE / n) for n in ("screen.py", "ddbudget.py", "PREREGISTRATION.md")}
    inputs = {p.name: sha256(p) for p in INPUT_FILES}
    return {"code_sha256": code, "input_sha256": inputs, "python": sys.version.split()[0],
            "numpy": np.__version__, "numba": numba.__version__}


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
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
