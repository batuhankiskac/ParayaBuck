"""hmm_regime_v1: does a causal two-state Gaussian HMM regime overlay beat K after Turkish tax? (audit item 52)

Pre-registered in PREREGISTRATION.md (arms H1-H3, every parameter fixed before any non-control arm ran).

    python -m research.hmm_regime_v1.screen --harness   # controls K / R2 anchors + kernel and parity checks
    python -m research.hmm_regime_v1.screen --run       # arms, gates, bootstraps -> RESULTS.json, SUMMARY.txt

Simulation goes through fe_sim.simulate; the bootstrap driver is fe_boot's `_boot_chunk` kernel called
once per arm on identical block draws. This file and hmm.py only build P(turbulent) and target-weight arrays.
"""

from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PROGRESSIVE
from research.gates_v1 import provenance
from research.gates_v1.stats import dsr, sample_moments, var_trials_floor
from research.hmm_regime_v1 import hmm

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BH1_RESULTS = ROOT / "research/band_h2_combo_v1/RESULTS.json"
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

START = fe_config.START
CAPITAL = 100_000.0
M = 2
CAL_SPREAD = fe_config.FIN["calibrated"]
EXTRA_FIN = fe_config.EXTRA_FIN
WINDOWS = OrderedDict(fe_config.TAX_WINDOWS)
HALVES = OrderedDict([("pre_2020", ("2000-03-09", "2019-12-31")), ("post_2020", ("2020-01-01", "2026-09-29"))])
OOS_KEYS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
SEED = 20261011           # G5, G8 and the B1/B2 bootstraps (same seed as the fast_reentry gates)
BLOCK = 20
N_PRE, N_TAX = 5000, 1000
G8_REPS = 2000
N_ARMS_PREREG = 3         # H1-H3
N_TRIALS = 369 + N_ARMS_PREREG
DSR_BAR = 0.95            # gates_v1 bar, reported (informational)
TRAIN_FROM = "1990-01-01"  # main-path training data starts here (^IXIC prefix before QQQ's first session)
LAG_YEARS = 10             # first refit = training start year + 10 (i.e. 2000 on the main path)

ARMS = ("K", "R2", "H1", "H2", "H3")
RULE_OF = {"K": "K", "R2": "C0", "H1": "K", "H2": "K", "H3": "K"}
HARM = ("H1", "H2", "H3")
TAX_LABEL = {"progressive": "B1_progressive", "flat": "B2_flat20"}


# ----------------------------------------------------------------------------- inputs
def daily(nav):
    nav = np.asarray(nav, dtype=float)
    return nav[1:] / nav[:-1] - 1


def refit_days(days, first_year):
    """First session of each calendar year from first_year to the last session's year (the refit dates)."""
    out = []
    for y in range(first_year, int(days[-1][:4]) + 1):
        for d in days:
            if d[:4] == str(y):
                out.append(d)
                break
    return out


@lru_cache(maxsize=1)
def main_p():
    """Causal P(turbulent) on the QQQ calendar (index 0 NaN). Observations: ^IXIC log returns 1990 to the
    day before QQQ's first session (1999-03-10), then QQQ log returns. Yearly expanding refits from 2000."""
    days, arr, _y = fe_data.load()
    c = np.asarray(arr["QQQ"][1], dtype=float)
    md, _mo, mc, _my, _ms = fe_data.load_market("M1")
    mc = np.asarray(mc, dtype=float)
    lo_i = bisect.bisect_left(md, TRAIN_FROM)
    hi_i = bisect.bisect_left(md, days[0])
    idx = np.arange(max(lo_i, 1), hi_i)
    x_ix = np.log(mc[idx] / mc[idx - 1])
    d_ix = [md[i] for i in idx]
    x_q = np.log(c[1:] / c[:-1])
    d_q = list(days[1:])
    x = np.concatenate([x_ix, x_q])
    dates = d_ix + d_q
    if not np.isfinite(x).all():
        raise RuntimeError("non-finite observation in the HMM series")
    p_obs, fits = hmm.expanding_p(x, dates, TRAIN_FROM, refit_days(days, int(TRAIN_FROM[:4]) + LAG_YEARS))
    P = np.full(len(days), np.nan)
    P[1:] = p_obs[len(d_ix):]
    return P, fits, {"n_ixic_obs": len(d_ix), "n_qqq_obs": len(d_q), "obs_first": dates[0], "obs_last": dates[-1]}


def oos_p(days, c):
    """Same causal schedule on an OOS window: training starts at the window's first session, first refit
    10 years later, yearly expanding refits; NaN (K's weight) before that."""
    c = np.asarray(c, dtype=float)
    x = np.log(c[1:] / c[:-1])
    dates = list(days[1:])
    p_obs, fits = hmm.expanding_p(x, dates, days[0], refit_days(days, int(days[0][:4]) + LAG_YEARS))
    P = np.full(len(days), np.nan)
    P[1:] = p_obs
    return P, fits


def main_state(spread, with_hmm=True):
    """Main QQQ calendar: 2x leg, E2/R2 state, K weights and the H1-H3 arms (1-D per arm)."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_config.EXP_Q)
    price = np.atleast_2d(q[1])
    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    on, factor = fe_signals.r2_state(price, e2, comps)
    w_k = fe_signals.sized_wants(on, factor, fe_signals.vol_n(c))
    W = {"K": w_k[0], "R2": w_k[0]}
    extra = {}
    if with_hmm:
        P, fits, meta = main_p()
        h1, h2, h3, gate = hmm.hmm_arms(P[None, :], w_k, e2)
        W.update({"H1": h1[0], "H2": h2[0], "H3": h3[0]})
        extra = {"P": P, "gate": gate[0], "e2": e2[0], "fits": fits, "meta": meta}
    fx, ufe = fe_study.try_market(days)
    return {"days": days, "fx": fx, "ufe": ufe, "o": o, "c": c, "factor": factor, "W": W, **extra}


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
    """fe_boot.chunk_legs with the QQQ path close kept (the regime arms need it); same arithmetic."""
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
    return {"pc": pc, "e2": e2, "on": on, "factor": factor, "so": so, "sc": sc, "so_c": so_c, "sc_c": sc_c}


def final_fit():
    """Bootstrap parameters (pre-registered choice): the last yearly refit of the main path (fit on data
    through 2025-12-31), applied to every resampled path by causal forward filtering from the stationary
    distribution. Resampled paths have no history, so they are not refitted (see PREREGISTRATION.md)."""
    _P, fits, _m = main_p()
    f = fits[-1]
    return {"A": np.asarray(f["A"]), "mu": np.asarray(f["mu"]), "sig": np.asarray(f["sig"]), "refit_date": f["refit_date"],
            "train_to": f["train_to"]}


def path_p(pc, ff):
    """P(turbulent) for every path column (column 0 = stationary prior, then the causal filter)."""
    logr = np.log(pc[:, 1:] / pc[:, :-1])
    pi0 = hmm.stationary(ff["A"])
    filt = hmm.filter_2d(np.ascontiguousarray(logr), pi0, ff["A"], ff["mu"], ff["sig"])
    return np.concatenate([np.full((pc.shape[0], 1), pi0[1]), filt], axis=1)


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


def boot_all(win, ff, seed=SEED, block=BLOCK, n_pre=N_PRE, n_tax=N_TAX, chunk=fe_boot.DEFAULT_CHUNK, arms=ARMS):
    """Common block draws (fe_boot.draw_positions) for every arm; regime weights from hmm.hmm_arms."""
    n_paths = max(n_pre, n_tax)
    pre = {(f, a): np.empty(n_pre) for f in ("base", "cal") for a in arms}
    at = {(md, a): np.empty(n_tax) for md in ("progressive", "flat") for a in arms}
    rng = np.random.default_rng(seed)
    gate_share = []
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
        sta = chunk_states(win.q, win.y, pos)
        P = path_p(sta["pc"], ff)
        wk = {}
        for fin, sc in (("base", sta["sc"]), ("cal", sta["sc_c"])):  # on/factor and P are financing-free
            w_k = fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sc))
            h1, h2, h3, gate = hmm.hmm_arms(P, w_k, sta["e2"])
            wk[fin] = {"K": w_k, "R2": w_k, "H1": h1, "H2": h2, "H3": h3}
            if fin == "base":
                gate_share.append(gate.mean())
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
    return pre, at, float(np.mean(gate_share))


def delta_stats(a, b):
    d = np.asarray(a) - np.asarray(b)
    return {"median_delta_pp": float(np.median(d) * 100), "mean_delta_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)), "p05_pp": float(np.percentile(d, 5) * 100),
            "p95_pp": float(np.percentile(d, 95) * 100)}


# ----------------------------------------------------------------------------- OOS windows
def oos_run(arms=ARMS):
    """Six OOS windows, pre-tax, calibrated financing, m = 2, capital 1, own E2/R2 state and own causal HMM."""
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
        P, fits = oos_p(days, c)
        h1, h2, h3, _g = hmm.hmm_arms(P[None, :], w_k, e2)
        W = {"K": w_k, "R2": w_k, "H1": h1, "H2": h2, "H3": h3}
        env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
        res = {}
        navs = {}
        for arm in arms:
            r = fe_sim.simulate(so, sc, np.asarray(W[arm])[0], env, 1.0, start, len(days) - 1, arm=RULE_OF[arm],
                                mode="pre_tax", m=M, rule_index=False)
            st = fe_study.window_stats(r["_nav"], days, start)
            res[arm] = {"cagr": float(st["cagr"]), "max_dd": float(st["max_dd"])}
            navs[arm] = np.asarray(r["_nav"], dtype=float)
        out[key] = {"sessions": len(days) - start, "start": days[start], "arms": res, "refits": len(fits),
                    "first_refit": fits[0]["refit_date"] if fits else None, "_navs": navs}
    return out


# ----------------------------------------------------------------------------- harness (controls and kernels)
def brute_arms(P, wk, v):
    """Reference for hmm.hmm_arms written as an explicit per-close loop (one path)."""
    T = len(wk)
    g = False
    L = False
    prev_p = np.nan
    prev_v = False
    prev_w3 = 0.0
    out = {"H1": [], "H2": [], "H3": []}
    for t in range(T):
        p, k, vt = P[t], wk[t], bool(v[t])
        if np.isnan(p):
            out["H1"].append(k)
            out["H2"].append(k)
            out["H3"].append(k)
            g, L, prev_p, prev_v, prev_w3 = False, False, np.nan, vt, k
            continue
        if g and p < hmm.P_OFF:
            g = False
        elif (not g) and p > hmm.P_ON and prev_p > hmm.P_ON:
            g = True
        L = bool(vt and g and (L or (prev_v and prev_w3 > 0.0)))
        w3 = 0.0 if L else k
        out["H1"].append(k * (1.0 - p))
        out["H2"].append(0.0 if g else k)
        out["H3"].append(w3)
        prev_p, prev_v, prev_w3 = p, vt, w3
    return {a: np.asarray(b) for a, b in out.items()}


def harness():
    ref = json.loads(BH1_RESULTS.read_text())
    S = main_state(0.0, with_hmm=False)
    days = S["days"]
    m_days, _o, _c, _we2, m_wr2, _e2 = fe_data.main_inputs(0.0)
    checks = {"K_weights_eq_main_inputs_R2": bool(np.array_equal(S["W"]["K"], m_wr2[0] if m_wr2.ndim == 2 else m_wr2))}
    rng = np.random.default_rng(1)
    win = fe_boot.BootWindow()
    pos = fe_boot.draw_positions(rng, BLOCK, 4, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
    legs, _e2b, _wb = fe_boot.chunk_legs(win.q, win.y, pos)
    sta = chunk_states(win.q, win.y, pos)
    w_mine = fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc"]))
    checks["K_weights_eq_fe_boot_chunk_legs"] = bool(np.array_equal(w_mine, legs["base"][2]))
    checks["K_weights_cal_eq_fe_boot_chunk_legs"] = bool(np.array_equal(
        fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc_c"])), legs["calibrated"][2]))

    # HMM kernel checks on synthetic data only (no real-data fit before the pre-registration commit)
    srng = np.random.default_rng(3)
    T = 6000
    s = np.zeros(T, int)
    A0 = np.array([[0.98, 0.02], [0.05, 0.95]])
    for t in range(1, T):
        s[t] = srng.choice(2, p=A0[s[t - 1]])
    x = np.array([0.0005, -0.001])[s] + np.array([0.007, 0.025])[s] * srng.standard_normal(T)
    fit = hmm.fit_hmm(x)
    checks["hmm_fit_recovers_states_sd_order"] = bool(fit["sig"][1] > fit["sig"][0] and fit["converged"])
    checks["hmm_fit_recovers_sd"] = bool(abs(fit["sig"][0] - 0.007) < 0.001 and abs(fit["sig"][1] - 0.025) < 0.004)
    p_syn = hmm.filter_1d(x, fit)
    x2 = x.copy()
    x2[3000:] = 0.0
    checks["filter_is_causal"] = bool(np.array_equal(p_syn[:3000], hmm.filter_1d(x2, fit)[:3000]))
    checks["filter_accuracy_above_0.9"] = bool(np.mean((p_syn > 0.5) == s) > 0.9)
    ex = np.random.default_rng(11)
    N_, T_ = 4, 700
    Pm = ex.random((N_, T_))
    Pm[:, :15] = np.nan
    Pm[0, 40:60] = 0.95
    wkm = ex.random((N_, T_))
    vm = ex.random((N_, T_)) < 0.8
    h1, h2, h3, _g = hmm.hmm_arms(Pm, wkm, vm)
    kernel_ok = True
    for n in range(N_):
        b = brute_arms(Pm[n], wkm[n], vm[n])
        kernel_ok &= bool(np.allclose(b["H1"], h1[n]) and np.allclose(b["H2"], h2[n]) and np.allclose(b["H3"], h3[n]))
    checks["hmm_arms_eq_brute_reference"] = kernel_ok
    dates_s = [f"{2000 + i // 252}-{(i % 252) // 21 + 1:02d}-{(i % 21) + 1:02d}" for i in range(T)]
    dates_s = sorted(dates_s)
    pe, _f = hmm.expanding_p(x, dates_s, dates_s[0], [dates_s[1200], dates_s[3000]])
    pe2, _f2 = hmm.expanding_p(x[:4000], dates_s[:4000], dates_s[0], [dates_s[1200], dates_s[3000]])
    checks["expanding_schedule_causal"] = bool(np.array_equal(pe[:4000], pe2, equal_nan=True))
    checks["expanding_warmup_nan_before_first_refit"] = bool(np.isnan(pe[:1200]).all() and np.isfinite(pe[1200:]).all())
    ok = all(bool(x_) for x_ in checks.values())
    print("identity / kernel checks:", checks)

    # K and R2 anchors against the reference RESULTS.json (reproduce K to 0.01 pp)
    ctrl = {}
    for arm, rule in (("K", "K"), ("R2", "C0")):
        pre, _ = pre_block(S, S["W"][arm], rule, M)
        ctrl[arm] = {"pre": pre, "tax": tax_block(S, S["W"][arm], rule)[0]}
    Sc = main_state(CAL_SPREAD, with_hmm=False)
    cal_k, _ = pre_block(Sc, Sc["W"]["K"], "K", M)
    anchors = []
    for arm, ref_arm in (("K", "K"), ("R2", "C0")):
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
    _pre, dK = pre_block(S, S["W"]["K"], "K", M)
    sm = sample_moments(dK)
    d365 = dsr(sm["sr"], sm["T"], sm["skew"], sm["kurt"], 365, var_trials_floor(sm["T"]))["dsr"]
    print(f"  K daily T={sm['T']} (gates_v1: 6679) DSR(N=365)={d365:.4f} (gates_v1: 0.711)")
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"], cwd=ROOT,
                       capture_output=True, text=True)
    parity_ok = p.returncode == 0 and "parity PASS" in p.stdout
    print("parity --quick:", "PASS" if parity_ok else "FAIL")
    ok &= parity_ok
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- the study
def g8_pooled(oos):
    """Reduced G8 (pre-registered in vol_regime_leverage_v1 and kept): block-20 circular bootstrap of each
    M1-M5 daily Delta vs K, 2000 replications, statistic = mean over M1-M5 of 252 x mean daily Delta."""
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


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    raise TypeError(f"not serialisable: {type(x)}")


def run():
    t0 = time.time()
    S = {"base": main_state(0.0), "cal": main_state(CAL_SPREAD)}
    win = fe_boot.BootWindow()
    ff = final_fit()
    res = OrderedDict()
    res["provenance"] = provenance.stamp(
        {"bars": fe_config.BARS, "index": fe_config.INDEX_CSV, "gspc": fe_config.GSPC_CSV, "dtb3": fe_config.DTB3_CSV,
         "tb3ms": fe_config.TB3MS_CSV, "usdtry": fe_config.FX_CSV, "yiufe": fe_config.UFE_CSV}, cwd=ROOT)
    res["provenance"]["code_sha256"] = {"screen.py": provenance.sha256_file(HERE / "screen.py"),
                                        "hmm.py": provenance.sha256_file(HERE / "hmm.py"),
                                        "PREREGISTRATION.md": provenance.sha256_file(HERE / "PREREGISTRATION.md")}
    res["meta"] = {"start": S["base"]["days"][START], "end": S["base"]["days"][-1], "cost_multiplier": M,
                   "capital": CAPITAL, "seed": SEED, "block": BLOCK, "n_pre": N_PRE, "n_tax": N_TAX,
                   "n_trials_dsr": N_TRIALS, "dsr_bar": DSR_BAR, "hmm": {"states": 2, "P_ON": hmm.P_ON, "P_OFF": hmm.P_OFF,
                   "train_from": TRAIN_FROM, "first_refit": S["base"]["fits"][0]["refit_date"],
                   "refit_schedule": "yearly expanding, first session of each year; filtered (causal) probabilities",
                   "bootstrap_params": {"refit_date": ff["refit_date"], "train_to": ff["train_to"],
                                        "A": ff["A"].tolist(), "mu": ff["mu"].tolist(), "sig": ff["sig"].tolist()}}}
    fits_out = S["base"]["fits"]
    res["hmm_fits_main"] = [{k: v for k, v in f_.items()} for f_ in fits_out]
    res["hmm_meta_main"] = S["base"]["meta"]

    pre, tax, daily_pre, daily_tax = {}, {}, {}, {}
    for arm in ARMS:
        pre[arm] = {}
        for fin in ("base", "cal"):
            for m in (1, 2):
                b, dly = pre_block(S[fin], S[fin]["W"][arm], RULE_OF[arm], m)
                pre[arm][f"{fin}_m{m}"] = b
                if fin == "base" and m == M:
                    daily_pre[arm] = dly
        tax[arm], daily_tax[arm] = tax_block(S["base"], S["base"]["W"][arm], RULE_OF[arm])
    res["controls_and_arms_pre_tax"] = pre
    res["controls_and_arms_after_tax_100k"] = tax

    main_days = S["base"]["days"]
    win_mask = np.array([START <= i for i in range(len(main_days))])
    Pm = S["base"]["P"]
    gate = S["base"]["gate"]
    diag = {"share_days_P_gt_0.5": float(np.mean(Pm[win_mask] > 0.5)),
            "share_days_gate_on": float(np.mean(gate[win_mask])),
            "gate_on_episodes": int(np.sum(np.diff(gate[win_mask].astype(int)) == 1)),
            "share_days_P_nan": float(np.mean(np.isnan(Pm[win_mask])))}
    diag["H1_mean_weight_over_K_on_days"] = float(np.mean(S["base"]["W"]["H1"][win_mask][S["base"]["e2"][win_mask]]) /
                                                  max(1e-12, np.mean(S["base"]["W"]["K"][win_mask][S["base"]["e2"][win_mask]])))
    res["hmm_diagnostics_main_window"] = diag

    pre_boot, at_boot, gate_share_boot = boot_all(win, ff)
    res["hmm_diagnostics_bootstrap"] = {"gate_on_share_base_fin": gate_share_boot}
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
    res["oos_calibrated_m2"] = {k: {"start": v["start"], "sessions": v["sessions"], "arms": v["arms"],
                                    "refits": v["refits"], "first_refit": v["first_refit"]} for k, v in oos.items()}
    res["g8_pooled_oos_reduced"] = g8_pooled(oos)

    gates = OrderedDict()
    K = ARMS[0]
    for arm in ARMS[2:]:
        g = {}

        def g1_ok(a, k):
            return a["cagr"] >= k["cagr"] or (a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)

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
                                 "halves_delta_pp": {k: v * 100 for k, v in hd.items()},
                                 "oos_delta_cagr_pp": {k: v * 100 for k, v in zip(OOS_KEYS, d_oos)},
                                 "oos_delta_dd_pp": {k: v * 100 for k, v in zip(OOS_KEYS, dd_oos)},
                                 "sub_periods_beating_K": sub_wins}}
    res["gates"] = gates

    dsr_out = {}
    for arm in ARMS:
        sm = sample_moments(daily_pre[arm])
        sm_t = sample_moments(daily_tax[arm])
        dsr_out[arm] = {"pre_tax_T": sm["T"], "pre_tax_sr_per_period": sm["sr"],
                        "dsr_pre_tax_N372": dsr(sm["sr"], sm["T"], sm["skew"], sm["kurt"], N_TRIALS, var_trials_floor(sm["T"]))["dsr"],
                        "dsr_after_tax_progressive_N372": dsr(sm_t["sr"], sm_t["T"], sm_t["skew"], sm_t["kurt"], N_TRIALS, var_trials_floor(sm_t["T"]))["dsr"]}
    res["dsr"] = dsr_out

    passing = [a for a in gates if gates[a]["pass"]]
    res["verdict"] = {"passing_arms": passing, "production_change": False,
                      "rule": "an arm is a candidate only if every G and A gate passes; otherwise K stays"}
    RESULTS.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable) + "\n")
    elapsed = time.time() - t0
    lines = ["hmm_regime_v1 SUMMARY (numbers from RESULTS.json)", f"wall_clock_seconds_run: {elapsed:.1f}",
             f"passing arms: {passing if passing else 'none'}"]
    for arm in ARMS:
        p2 = pre[arm]["base_m2"]
        t = tax[arm]["windows"]
        lines.append(f"{arm}: pre CAGR base {p2['cagr'] * 100:.2f}% max DD {p2['max_dd'] * 100:.2f}% "
                     f"after tax 2000/2010/2017 {t['2000_03_to_2026_09']['after_tax_cagr'] * 100:.2f}/"
                     f"{t['2010_01_to_end']['after_tax_cagr'] * 100:.2f}/{t['2017_01_to_end']['after_tax_cagr'] * 100:.2f}% "
                     f"sells/yr {p2['sells_per_year']:.1f}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return res


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
