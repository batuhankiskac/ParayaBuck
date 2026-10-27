"""review_calendar_v1: K's vol-target resizes reviewed on a fixed calendar (audit item 56).

Driver for PREREGISTRATION.md. Engine: research/review_calendar_v1/engine, a copy of
research/fast_engine with ONE added kernel option (the review-calendar gate in fe_sim.sim_one; with the
gate off it is bit-identical to the published engine, see the parity cells in --harness). Target weights
are K's own (the R2 target series, unchanged); only the fill decision of resizes changes.
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

from research.gates_v1.provenance import stamp
from research.gates_v1.stats import dsr, sample_moments, var_trials_floor
from research.review_calendar_v1.engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.review_calendar_v1.engine.fe_tax import MODE_FLAT, MODE_PROGRESSIVE

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
BH1_RESULTS = ROOT / "research/band_h2_combo_v1/RESULTS.json"

START = fe_config.START
M = 2
CAPITAL = 100_000.0
CAL_SPREAD = fe_config.FIN["calibrated"]
EXTRA_FIN = fe_config.EXTRA_FIN
WINDOWS = OrderedDict(fe_config.TAX_WINDOWS)
HALVES = OrderedDict([("pre_2020", ("2000-03-09", "2019-12-31")), ("post_2020", ("2020-01-01", "2026-09-29"))])
OOS_KEYS = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
SEED = 20261011
BLOCK = 20
N_PRE, N_TAX = 5000, 1000
G8_REPS = 2000
REGISTRY_COUNT = 375          # user-specified registry count
N_NEW_ARMS = 3                # R1, R2Q, R3
N_TRIALS = REGISTRY_COUNT + N_NEW_ARMS
DSR_BAR = 0.95

# arm -> fill rule of fe_sim.RULE. K-based arms use K's fill rule (A1u band + H2) plus the calendar gate.
ARMS = ("K", "R2ref", "R1", "R2Q", "R3")
NEW_ARMS = ("R1", "R2Q", "R3")
RULE_OF = {"K": "K", "R2ref": "C0", "R1": "K", "R2Q": "K", "R3": "K"}
# arm -> (review calendar, drift escape threshold; None = no escape)
GATE_OF = {"R1": ("month", None), "R2Q": ("quarter", None), "R3": ("quarter", 0.15)}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def daily(nav):
    nav = np.asarray(nav, dtype=float)
    return nav[1:] / nav[:-1] - 1


def review_gate(env, kind):
    """1 on review sessions (= fill sessions), 0 elsewhere. kind 'month': first session of each calendar
    month. kind 'quarter': first session of each calendar month that opens a quarter (Jan/Apr/Jul/Oct)."""
    mth = np.asarray(env.mth_of, dtype=np.int64)
    assert mth.shape[0] == len(env.days)
    first = np.ones(mth.shape[0], dtype=bool)
    first[1:] = mth[1:] != mth[:-1]
    if kind == "quarter":
        first &= (mth % 12) % 3 == 0
    return first.astype(np.int8)


def arm_kwargs(env, arm):
    if arm not in GATE_OF:
        return {"arm": RULE_OF[arm]}
    kind, esc = GATE_OF[arm]
    return {"arm": RULE_OF[arm], "gate": review_gate(env, kind),
            "gate_esc": float("inf") if esc is None else float(esc)}


# ----------------------------------------------------------------------------- inputs and states
def main_state(spread):
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
    w = np.asarray(w_k)[0]
    return {"days": days, "fx": fx, "ufe": ufe, "o": o, "c": c, "on": np.asarray(on)[0],
            "factor": np.asarray(factor)[0], "w_k": w, "W": {a: w for a in ARMS}}


def prepare():
    return main_state(0.0), main_state(CAL_SPREAD)


def run_w(S, w, arm, mode, m=M, capital=CAPITAL, window=None):
    days = S["days"]
    if window is None:
        i0, i1 = START, len(days) - 1
    else:
        i0, i1 = fe_study.window_indices(days, *window)
    env = fe_sim.RunEnv(days, S["fx"], S["ufe"], i0, i1)
    return fe_sim.simulate(S["o"], S["c"], w, env, capital, i0, i1, mode=mode, m=m, **arm_kwargs(env, arm))


def pre_block(S, w, arm, m):
    r = run_w(S, w, arm, "pre_tax", m=m)
    st = fe_study.pre_stats(S["days"], START, len(S["days"]) - 1, r)
    out = {"cagr": st["cagr"], "max_dd": st["max_dd"], "calmar": st["calmar"],
           "sells_per_year": st["sells_per_year"], "turnover_annual": st["turnover_annual"],
           "sub": {k: v["cagr"] for k, v in st["subperiods"].items()}, "cagr_2017_end": st["2017_end"]["cagr"]}
    return out, daily(r["_nav"])


def pre_halves(S, w, arm):
    days = S["days"]
    out = {}
    for hn, win in HALVES.items():
        i0, i1 = fe_study.window_indices(days, *win)
        r = run_w(S, w, arm, "pre_tax", window=win)
        nav = np.asarray(r["_nav"], dtype=float)
        yrs = fe_study.calendar_years(days[i0 - 1], days[i1])
        out[hn] = float(fe_study.cagr(nav[0], nav[-1], yrs))
    return out


def tax_block(S, w, arm):
    out = {"windows": {}, "halves": {}}
    for wn, win in WINDOWS.items():
        r = run_w(S, w, arm, "progressive", window=win)
        out["windows"][wn] = {"after_tax_cagr": float(r["cagr_usd_liquidated"]),
                              "max_dd_after_tax": float(r["max_dd_nav_usd"]),
                              "sells_per_year": float(r["sells"] / r["years"])}
    for hn, win in HALVES.items():
        r = run_w(S, w, arm, "progressive", window=win)
        out["halves"][hn] = {"after_tax_cagr": float(r["cagr_usd_liquidated"])}
    full = run_w(S, w, arm, "progressive")
    return out, daily(full["_nav"])


# ----------------------------------------------------------------------------- bootstrap
def chunk_states(q, y, pos):
    """fe_boot.chunk_legs with the QQQ path close kept; same arithmetic as slow_vol_blend_v1."""
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


def _call_kernel(win, arm, so, sc, wb, so_c, sc_c, wc, p0, W, n_pre, n_tax, out_pre, out_at):
    env = win.env
    rule = RULE_OF[arm]
    band = np.array([fe_sim.RULE[rule]["band"]], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[rule]["usd_rule"]], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[rule]["defer"]], dtype=np.bool_)
    mode_ids = np.array([MODE_PROGRESSIVE, MODE_FLAT], dtype=np.int64)
    cap_at = np.array([fe_config.TAX_CAPITALS["100k"]], dtype=np.float64)
    out_anchor = np.empty((2, W), dtype=np.float64)
    if arm in GATE_OF:
        kind, esc = GATE_OF[arm]
        gate = review_gate(env, kind)
        gate_on = np.array([True])
        gate_esc = np.array([float("inf") if esc is None else float(esc)])
    else:
        gate = np.zeros(0, dtype=np.int8)
        gate_on = np.array([False])
        gate_esc = np.array([0.0])
    fe_boot._boot_chunk(
        so, sc, wb, so_c, sc_c, wc, wb, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
        env.pay_ptr, env.pay_yr, env.pay_part, fe_config.TAX_CAPITALS["100k"], cap_at, win.i0, win.i1,
        fe_sim.COST_PER_SIDE * M, win.yrs, env.Y0, env.M_lo, band, usd_rule, defer, mode_ids, p0, n_pre, n_tax,
        False, out_pre, out_at, out_anchor, np.zeros((0, 6), dtype=np.float64), gate, gate_on, gate_esc)


def boot_all(win, seed=SEED, block=BLOCK, n_pre=N_PRE, n_tax=N_TAX, chunk=fe_boot.DEFAULT_CHUNK, arms=ARMS):
    """Common block draws (fe_boot.draw_positions) for every arm; the same target weights on every path."""
    n_paths = max(n_pre, n_tax)
    pre = {(f, a): np.empty(n_pre) for f in ("base", "cal") for a in arms}
    at = {(md, a): np.empty(n_tax) for md in ("progressive", "flat") for a in arms}
    rng = np.random.default_rng(seed)
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
        sta = chunk_states(win.q, win.y, pos)
        wk = {}
        for fin, sc in (("base", sta["sc"]), ("cal", sta["sc_c"])):  # on/factor are financing-free
            wk[fin] = fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sc))
        npre = max(0, min(W, n_pre - p0))
        ntax = max(0, min(W, n_tax - p0))
        for a in arms:
            out_pre = np.empty((2, 1, W))
            out_at = np.empty((1, 2, 1, W))
            _call_kernel(win, a, sta["so"], sta["sc"], wk["base"], sta["so_c"], sta["sc_c"], wk["cal"],
                         p0, W, n_pre, n_tax, out_pre, out_at)
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
    """Six OOS windows, pre-tax, calibrated financing, m = 2, capital 1, own E2/R2 state and vol history."""
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
        w_k = np.asarray(fe_signals.sized_wants(on, factor, fe_signals.vol_n(sc)))[0]
        env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
        res, navs = {}, {}
        for arm in arms:
            r = fe_sim.simulate(so, sc, w_k, env, 1.0, start, len(days) - 1, mode="pre_tax", m=M,
                                rule_index=False, **arm_kwargs(env, arm))
            st = fe_study.window_stats(r["_nav"], days, start)
            res[arm] = {"cagr": float(st["cagr"]), "max_dd": float(st["max_dd"])}
            navs[arm] = np.asarray(r["_nav"], dtype=float)
        out[key] = {"sessions": len(days) - start, "start": days[start], "arms": res, "_navs": navs}
    return out


def g8_pooled(oos):
    """Reduced G8 (pre-registered in vol_regime_leverage_v1): block-20 circular bootstrap of M1-M5 Deltas."""
    rng = np.random.default_rng(SEED)
    res = {}
    for arm in ARMS:
        if arm == "K":
            continue
        dd = {key: daily(oos[key]["_navs"][arm]) - daily(oos[key]["_navs"]["K"]) for key in ("M1", "M2", "M3", "M4", "M5")}
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
    t = np.arange(len(S["days"]))
    sel = S["on"] & (t >= START)
    if pre2010_only:
        sel &= np.array([d < "2010-01-01" for d in S["days"]])
    return float(np.mean(2.0 * np.asarray(w)[sel]))


# ----------------------------------------------------------------------------- harness (controls, parity)
def parity_cells(S):
    """Engine-option parity (control weights only): gate off == research/fast_engine bit for bit; all-ones gate == K."""
    from research.fast_engine import fe_sim as orig
    days = S["days"]
    i0, i1 = START, len(days) - 1
    env_n = fe_sim.RunEnv(days, S["fx"], S["ufe"], i0, i1)
    env_o = orig.RunEnv(days, S["fx"], S["ufe"], i0, i1)
    ones = np.ones(len(days), dtype=np.int8)
    cells = {}
    for arm in ("K", "R2ref"):
        for mode in ("pre_tax", "progressive", "flat"):
            rn = fe_sim.simulate(S["o"], S["c"], S["W"][arm], env_n, CAPITAL, i0, i1, arm=RULE_OF[arm], mode=mode, m=M)
            ro = orig.simulate(S["o"], S["c"], S["W"][arm], env_o, CAPITAL, i0, i1, arm=RULE_OF[arm], mode=mode, m=M)
            cells[f"gate_off_eq_fast_engine:{arm}:{mode}"] = bool(np.array_equal(np.asarray(rn["_nav"]), np.asarray(ro["_nav"])))
    for mode in ("pre_tax", "progressive"):
        rk = fe_sim.simulate(S["o"], S["c"], S["W"]["K"], env_n, CAPITAL, i0, i1, arm="K", mode=mode, m=M)
        rg = fe_sim.simulate(S["o"], S["c"], S["W"]["K"], env_n, CAPITAL, i0, i1, arm="K", mode=mode, m=M,
                             gate=ones, gate_esc=0.0)
        cells[f"gate_all_ones_eq_K:{mode}"] = bool(np.array_equal(np.asarray(rk["_nav"]), np.asarray(rg["_nav"])))
    return cells


def harness():
    ref = json.loads(BH1_RESULTS.read_text())
    Sb, Sc = prepare()
    days = Sb["days"]
    _m_days, _o, _c, _we2, m_wr2, _e2 = fe_data.main_inputs(0.0)
    checks = {}
    checks["K_weights_eq_main_inputs_R2"] = bool(np.array_equal(Sb["w_k"], np.asarray(m_wr2).reshape(-1)))
    checks["window_ends_2026_09_29"] = bool(days[-1] == "2026-09-29")
    rng = np.random.default_rng(1)
    win = fe_boot.BootWindow()
    pos = fe_boot.draw_positions(rng, BLOCK, 4, fe_config.TAX_BOOT_L, win.npool, fe_config.TAX_BOOT_EXT)
    legs, _e2b, _wb = fe_boot.chunk_legs(win.q, win.y, pos)
    sta = chunk_states(win.q, win.y, pos)
    checks["K_weights_eq_fe_boot_chunk_legs"] = bool(np.array_equal(
        fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc"])), legs["base"][2]))
    checks["K_weights_cal_eq_fe_boot_chunk_legs"] = bool(np.array_equal(
        fe_signals.sized_wants(sta["on"], sta["factor"], fe_signals.vol_n(sta["sc_c"])), legs["calibrated"][2]))
    ok = all(checks.values())
    print("identity checks:", checks)
    smoke_pre, smoke_at = boot_all(win, n_pre=8, n_tax=8, arms=("K",))
    smoke_ok = bool(np.all(np.isfinite(smoke_pre[("base", "K")])) and np.all(np.isfinite(smoke_at[("flat", "K")])))
    print("bootstrap kernel smoke (K, 8 paths, gate plumbing compiles):", smoke_ok)
    ok &= smoke_ok

    parity = parity_cells(Sb)
    print("engine-option parity cells:", parity)
    ok &= all(parity.values())

    anchors = []
    ctrl = {}
    for arm in ("K", "R2ref"):
        pre, _d = pre_block(Sb, Sb["W"][arm], arm, M)
        ctrl[arm] = {"pre": pre, "tax": tax_block(Sb, Sb["W"][arm], arm)[0]}
    cal_k, _ = pre_block(Sc, Sc["W"]["K"], "K", M)
    for arm in ("K", "R2ref"):
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
    print("parity --quick (published engine):", "PASS" if parity_ok else "FAIL")
    ok &= parity_ok
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- the study
def provenance():
    inputs = {"bars": fe_config.BARS, "index": fe_config.INDEX_CSV, "gspc": fe_config.GSPC_CSV, "fx": fe_config.FX_CSV,
              "ufe": fe_config.UFE_CSV, "dtb3": fe_config.HIST / "stress_robustness_v1_dtb3.csv",
              "tb3ms": fe_config.HIST / "ewma_band_v1_tb3ms.csv"}
    code = {n: sha256(HERE / n) for n in ("screen.py", "PREREGISTRATION.md")}
    code |= {f"engine/{p.name}": sha256(p) for p in sorted((HERE / "engine").glob("fe_*.py"))}
    return {"stamp": stamp(inputs, cwd=ROOT), "code_sha256": code, "numba": numba.__version__}


def run():
    t0 = time.time()
    Sb, Sc = prepare()
    S = {"base": Sb, "cal": Sc}
    win = fe_boot.BootWindow()
    res = OrderedDict()
    res["meta"] = {"start": Sb["days"][START], "end": Sb["days"][-1], "cost_multiplier": M, "capital": CAPITAL,
                   "seed": SEED, "block": BLOCK, "n_pre": N_PRE, "n_tax": N_TAX, "n_trials_dsr": N_TRIALS,
                   "registry_count": REGISTRY_COUNT, "new_arms": N_NEW_ARMS, "dsr_bar": DSR_BAR}
    res["provenance"] = provenance()

    pre, tax, daily_pre, daily_tax, halves_pre, expo = {}, {}, {}, {}, {}, {}
    for arm in ARMS:
        pre[arm] = {}
        for fin in ("base", "cal"):
            for m in (1, 2):
                b, dly = pre_block(S[fin], S[fin]["W"][arm], arm, m)
                pre[arm][f"{fin}_m{m}"] = b
                if fin == "base" and m == M:
                    daily_pre[arm] = dly
        tax[arm], daily_tax[arm] = tax_block(Sb, Sb["W"][arm], arm)
        halves_pre[arm] = pre_halves(Sb, Sb["W"][arm], arm)
        expo[arm] = {"mean_qqq_equiv_exposure_vote_on": exposure(Sb, Sb["W"][arm]),
                     "mean_qqq_equiv_exposure_all_days": float(np.mean(2.0 * np.asarray(Sb["W"][arm])[START:]))}
    res["controls_and_arms_pre_tax"] = pre
    res["controls_and_arms_after_tax_100k"] = tax
    res["pre_tax_halves_cagr"] = halves_pre
    res["exposure"] = expo

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
        }
    res["bootstrap_vs_K"] = boots
    res["bootstrap_medians_cagr"] = {a: {"pre_base": float(np.median(pre_boot[("base", a)])),
                                         "B1": float(np.median(at_boot[("progressive", a)])),
                                         "B2": float(np.median(at_boot[("flat", a)]))} for a in ARMS}

    oos = oos_run()
    res["oos_calibrated_m2"] = {k: {"start": v["start"], "sessions": v["sessions"], "arms": v["arms"]} for k, v in oos.items()}
    res["g8_pooled_oos_reduced"] = g8_pooled(oos)

    gates = OrderedDict()

    def g1_ok(a, k):
        return a["cagr"] >= k["cagr"] or (a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005)

    for arm in NEW_ARMS:
        g = {}
        g["G1"] = bool(all(g1_ok(pre[arm][f"{f}_m2"], pre["K"][f"{f}_m2"]) for f in ("base", "cal")))
        g["G2"] = bool(all(pre[arm][f"{f}_m2"]["max_dd"] <= pre["K"][f"{f}_m2"]["max_dd"] + 0.01 for f in ("base", "cal")))
        g["G3"] = bool(all(pre[arm][f"{f}_m2"]["cagr_2017_end"] >= pre["K"][f"{f}_m2"]["cagr_2017_end"] - 0.01 for f in ("base", "cal")))
        sub_wins = sum(1 for p_ in ("P1", "P2", "P3") if pre[arm]["base_m2"]["sub"][p_] > pre["K"]["base_m2"]["sub"][p_])
        g["G4"] = bool(sub_wins >= 2)
        g["G5"] = bool(boots[arm]["pre_base"]["median_delta_pp"] >= 0 and boots[arm]["pre_cal"]["median_delta_pp"] >= 0)
        g["G6"] = bool(all(g1_ok(pre[arm][f"{f}_m1"], pre["K"][f"{f}_m1"]) for f in ("base", "cal")))
        d_oos = [oos[k]["arms"][arm]["cagr"] - oos[k]["arms"]["K"]["cagr"] for k in OOS_KEYS]
        dd_oos = [oos[k]["arms"][arm]["max_dd"] - oos[k]["arms"]["K"]["max_dd"] for k in OOS_KEYS]
        g["G7"] = bool(np.median(d_oos) >= 0 and sum(1 for x in d_oos if x >= -0.005) >= 5
                       and sum(1 for x in dd_oos if x <= 0.03) >= 5 and max(dd_oos) <= 0.08)
        g["G8"] = bool(res["g8_pooled_oos_reduced"][arm]["median_annualised_delta_pp"] >= 0)
        g["G9"] = bool(pre[arm]["base_m2"]["turnover_annual"] <= 2 * pre["K"]["base_m2"]["turnover_annual"])
        at_d = {wn: tax[arm]["windows"][wn]["after_tax_cagr"] - tax["K"]["windows"][wn]["after_tax_cagr"] for wn in WINDOWS}
        g["A1_after_tax_all_windows_gt_K"] = bool(all(v > 0 for v in at_d.values()))
        g["A2_B1_median_ge_0_share_ge_55"] = bool(boots[arm]["B1_progressive"]["median_delta_pp"] >= 0 and boots[arm]["B1_progressive"]["share_gt_0"] >= 0.55)
        g["A3_B2_median_ge_0_share_ge_55"] = bool(boots[arm]["B2_flat20"]["median_delta_pp"] >= 0 and boots[arm]["B2_flat20"]["share_gt_0"] >= 0.55)
        hd = {h: tax[arm]["halves"][h]["after_tax_cagr"] - tax["K"]["halves"][h]["after_tax_cagr"] for h in HALVES}
        g["A4_pre_post_2020_both_gt_0"] = bool(all(v > 0 for v in hd.values()))
        gates[arm] = {"pass": bool(all(g.values())), **g,
                      "detail": {"after_tax_delta_pp": {k: v * 100 for k, v in at_d.items()},
                                 "halves_after_tax_delta_pp": {k: v * 100 for k, v in hd.items()},
                                 "halves_pre_tax_delta_pp": {h: (halves_pre[arm][h] - halves_pre["K"][h]) * 100 for h in HALVES},
                                 "oos_delta_cagr_pp": {k: v * 100 for k, v in zip(OOS_KEYS, d_oos)},
                                 "oos_delta_dd_pp": {k: v * 100 for k, v in zip(OOS_KEYS, dd_oos)},
                                 "sub_periods_beating_K": sub_wins}}
    res["gates"] = gates

    dsr_out = {}
    for arm in ARMS:
        sm = sample_moments(daily_pre[arm])
        sm_t = sample_moments(daily_tax[arm])
        dsr_out[arm] = {"pre_tax_T": sm["T"], "pre_tax_sr_per_period": sm["sr"],
                        f"dsr_pre_tax_N{N_TRIALS}": dsr(sm["sr"], sm["T"], sm["skew"], sm["kurt"], N_TRIALS, var_trials_floor(sm["T"]))["dsr"],
                        f"dsr_after_tax_progressive_N{N_TRIALS}": dsr(sm_t["sr"], sm_t["T"], sm_t["skew"], sm_t["kurt"], N_TRIALS, var_trials_floor(sm_t["T"]))["dsr"]}
    res["dsr"] = dsr_out

    passing = [a for a in gates if gates[a]["pass"]]
    res["verdict"] = {"passing_arms": passing, "production_change": False,
                      "rule": "an arm is a candidate only if every G and A gate passes; otherwise K stays"}
    RESULTS.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable) + "\n")
    elapsed = time.time() - t0
    lines = ["review_calendar_v1 SUMMARY (numbers from RESULTS.json)", f"wall_clock_seconds_run: {elapsed:.1f}",
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
