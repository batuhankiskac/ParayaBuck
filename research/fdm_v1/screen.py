"""fdm_v1: Carver's forecast diversification multiplier on the three K rules, versus K after Turkish tax.

    python -m research.fdm_v1.screen --harness   # K/R2 anchors (gates_v2, parity --quick) + FDM and arm checks
    python -m research.fdm_v1.screen --measure   # expanding yearly rho and FDM (diagnostic, prints only)
    python -m research.fdm_v1.screen --run       # study -> RESULTS.json, SUMMARY.txt

Simulation goes through fe_sim.simulate, tax through fe_sim / fe_tax, bootstraps through fe_boot.boot. This file only
builds target-weight arrays (numpy / pandas) and aggregates results. PREREGISTRATION.md is the binding spec.
Rule forecasts, the C3 map and the gate definitions are copied from research/carver_forecast_v1/screen.py.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from research.carver_forecast_v1 import screen as c3  # carver's rule forecasts / C3 weights: reference and parity only
from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.gates_v1.provenance import stamp
from research.gates_v1.stats import dsr, sample_moments, var_trials_floor
from research.gates_v2 import screen as g2

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
MANIFEST = HERE / "DATA_MANIFEST.json"

START = fe_config.START
CAP = fe_config.CAP
CAPITAL = 100_000.0
M = 2
CAL_SPREAD = fe_config.FIN["calibrated"]
WINDOWS = g2.WINDOWS
HALVES = g2.HALVES
ARM_NAMES = ("F1", "F2")
OOS_WIN = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
OOS_POOL = ("M1", "M2", "M3", "M4", "M5")
N_REGISTRY = 375
N_TRIALS = N_REGISTRY + len(ARM_NAMES)
FC_CAP = c3.FC_CAP  # 20
FC_SCALE = c3.FC_SCALE  # 10
MIN_OBS = c3.MIN_OBS  # 20
EWMA_SPAN = 5
FDM_MIN_JOINT = 252
FDM_CAP = 2.5
SYN_PERIOD = 252
W3 = np.full(3, 1.0 / 3.0)
SEALED_FROM = date(2026, 10, 30)  # docs/research/sealed_window_v1.md: no session at or after this date is read
G8_SEED = 20261011
G8_PATHS = 2000
G8_LEN = 2520
G8_BLOCK = 20

_ORIG_CHUNK = fe_boot.chunk_legs  # captured before any monkeypatch


def check_sealed(days):
    last = str(days[-1])[:10]
    assert last < SEALED_FROM.isoformat(), f"sealed window: series ends {last}"
    return last


# ----------------------------------------------------------------------------- forecasts, votes, FDM (no look-ahead)
def rule_forecasts(price):
    """(3, P, T): the three rule forecasts, each clip(r / expanding mean |r| * 10, -20, +20) (carver definitions)."""
    fs = []
    for r in c3.raw_signals(price):
        with np.errstate(divide="ignore", invalid="ignore"):
            fs.append(np.clip(r / c3.expanding_mean_abs(r) * FC_SCALE, -FC_CAP, FC_CAP))
    return np.stack(fs)


def votes(price):
    """(P, T) int in {0,1,2,3}: K's three binary votes (SMA200, 252-session return, DON126 midpoint)."""
    p = np.atleast_2d(np.asarray(price, dtype=float))
    return (
        fe_signals.sma_up(p, fe_config.SMA_N).astype(int)
        + fe_signals.tsm_up_price(p).astype(int)
        + fe_signals.don_up(p).astype(int)
    )


def real_checkpoints(days):
    """Index of the last session of each calendar year that has a following session."""
    yrs = [str(d)[:4] for d in days]
    return [i for i in range(len(yrs) - 1) if yrs[i] != yrs[i + 1]]


def synthetic_checkpoints(T):
    """Bootstrap paths have no calendar: sessions 251, 503, ... (every 252 sessions from the path start)."""
    return list(range(SYN_PERIOD - 1, T - 1, SYN_PERIOD))


def joint_corr(f, c):
    """Expanding Pearson matrix of the three rule forecasts over sessions <= c with all three finite.

    f: (3, P, T). Returns n (P,) joint sample sizes and R (P, 3, 3); NaN where undefined.
    """
    x = f[:, :, : c + 1]
    m = np.all(np.isfinite(x), axis=0)
    n = m.sum(axis=1).astype(float)
    nn = np.maximum(n, 1.0)
    mu = np.where(m, x, 0.0).sum(axis=2) / nn
    d = np.where(m, x - mu[:, :, None], 0.0)
    cov = np.einsum("ipt,jpt->pij", d, d)
    sd = np.sqrt(np.einsum("ipt,ipt->pi", d, d))
    with np.errstate(divide="ignore", invalid="ignore"):
        R = cov / (sd[:, :, None] * sd[:, None, :])
    return n, R


def fdm_at(f, c):
    """FDM estimated with data through session c: (P,) FDM (1.0 if n < 252 or undefined), n, R, q = w'Rw."""
    n, R = joint_corr(f, c)
    q = np.einsum("i,pij,j->p", W3, R, W3)
    ok = (n >= FDM_MIN_JOINT) & np.isfinite(q)
    with np.errstate(divide="ignore", invalid="ignore"):
        fdm = 1.0 / np.sqrt(np.maximum(q, 1.0 / FDM_CAP**2))
    return np.where(ok, np.minimum(fdm, FDM_CAP), 1.0), n, R, q


def fdm_series(f, checkpoints):
    """(P, T) FDM in force: the estimate at the latest checkpoint c < t, applied from session c + 1 on."""
    P, T = f.shape[1], f.shape[2]
    out = np.ones((P, T))
    cps = [c for c in checkpoints if 0 <= c < T - 1]
    for k, c in enumerate(cps):
        nxt = cps[k + 1] if k + 1 < len(cps) else T - 1
        val = fdm_at(f, c)[0]
        out[:, c + 1 : nxt + 1] = val[:, None]
    return out


# ----------------------------------------------------------------------------- arms
def f1_weight(comb, fdm, vt):
    """F1 = carver C3 on comb x FDM: fpos = max(clip(comb x FDM, +-20), 0), EWMA span 5, min(1, fs/10) x vt."""
    valid = np.isfinite(comb)
    fc = np.clip(comb * fdm, -FC_CAP, FC_CAP)
    fpos = np.where(valid, np.maximum(fc, 0.0), 0.0)
    fs = pd.DataFrame(fpos.T).ewm(span=EWMA_SPAN, adjust=False).mean().to_numpy().T
    return np.minimum(CAP, np.minimum(1.0, fs / 10.0) * vt)


def f2_weight(price, v, fdm, vt, comps):
    """F2: f2 = min(20, v/3 x 20 x FDM); while v >= 1 weight min(CAP, min(1, f2/10) x vt); else K's early re-entry."""
    f2 = np.minimum(20.0, v / 3.0 * 20.0 * fdm)
    e2p = v >= 1
    _on, factor = fe_signals.r2_state(price, e2p, comps)
    early = np.minimum(CAP, 0.5 * vt) * (factor == 0.5)
    on = np.minimum(CAP, np.minimum(1.0, f2 / 10.0) * vt)
    return np.where(e2p, on, early)


def arm_weights(price, f, vol, comps, checkpoints):
    """price (P,T) QQQ close; f (3,P,T) rule forecasts; vol (P,T) vol20 of the 2x close. -> {name: (P,T)}, fdm."""
    comb = f.mean(axis=0)
    fdm = fdm_series(f, checkpoints)
    vt = fe_signals.vt_of(vol)
    w = {"F1": f1_weight(comb, fdm, vt), "F2": f2_weight(price, votes(price), fdm, vt, comps)}
    return w, fdm


def arm_targets(price, cl_2x, comps, checkpoints):
    """price, cl_2x: (1, T). Returns name -> (T,) weights and the (T,) FDM in force."""
    f = rule_forecasts(price)
    w, fdm = arm_weights(price, f, fe_signals.vol_n(cl_2x), comps, checkpoints)
    return {n: w[n][0] for n in ARM_NAMES}, fdm[0]


def targets(B, cps):
    tg, fdm = arm_targets(B["price"][None, :], B["c"][None, :], B["comps"], cps)
    W = OrderedDict([("K", (B["w_r2"], "K")), ("R2", (B["w_r2"], "C0"))])
    for n in ARM_NAMES:
        W[n] = (tg[n], "K")
    return W, fdm


# ----------------------------------------------------------------------------- bootstrap wrapper
def _arm_chunk(name):
    """Replacement for fe_boot.chunk_legs: same draws, the arm's targets (incl. FDM, synthetic schedule) per path."""

    def chunk(q, y, pos):
        legs, e2, w_e2 = _ORIG_CHUNK(q, y, pos)
        idx = pos + 1
        prev = q[1][idx - 1]
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        P = idx.shape[0]
        pc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        f = rule_forecasts(pc)
        comps = fe_signals.early_components(pc)
        cps = synthetic_checkpoints(pc.shape[1])
        new = {}
        for key, (so, sc, _w) in legs.items():
            w, _fdm = arm_weights(pc, f, fe_signals.vol_n(sc), comps, cps)
            new[key] = (so, sc, w[name])
        return new, e2, w_e2

    return chunk


def boot_run(name=None):
    """name None: unmodified K control (R2 target, rule K). Else arm `name` on the same draws."""
    fe_boot.chunk_legs = _ORIG_CHUNK if name is None else _arm_chunk(name)
    try:
        return fe_boot.boot(
            seed=fe_config.SEED, block=fe_config.BLOCK, arms=("C0", "K"), n_pre=fe_config.PRE_BOOT_PATHS,
            n_tax=fe_config.TAX_BOOT_PATHS, capitals=("100k",), anchor_e2=False, deltas=("K",),
            baselines=("C0",), quiet=True, keep_paths=True,
        )
    finally:
        fe_boot.chunk_legs = _ORIG_CHUNK


# ----------------------------------------------------------------------------- harness
def harness():
    ok = g2.harness()  # K and R2 anchors + parity --quick
    B0 = g2.build(0.0)
    price = B0["price"][None, :]
    cl = B0["c"][None, :]
    comps = B0["comps"]
    cps = real_checkpoints(B0["days"])
    tg, _fdm = arm_targets(price, cl, comps, cps)

    ident_vote = bool(np.array_equal(votes(price)[0] >= 2, B0["e2"]))
    print("identity: votes >= 2 equals fe_signals.e2_up on the main series:", "PASS" if ident_vote else "FAIL")

    f = rule_forecasts(price)
    vt = fe_signals.vt_of(fe_signals.vol_n(cl))
    c3_ref = c3.weights("C3", price, c3.forecasts(price), fe_signals.vol_n(cl), comps)[0]
    f1_unit = f1_weight(f.mean(axis=0), np.ones_like(f[0]), vt)[0]
    par_c3 = bool(np.array_equal(f1_unit, c3_ref))
    print("parity: F1 with FDM = 1 equals carver C3 bit-for-bit:", "PASS" if par_c3 else "FAIL")

    on = votes(price)[0] >= 2
    f2_eq = bool(np.allclose(tg["F2"][on], B0["w_r2"][on], rtol=0.0, atol=1e-12))
    print("identity: F2 equals K's w_r2 on vote-on days:", "PASS" if f2_eq else "FAIL")

    rng = np.random.default_rng(1)
    base = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, size=(1, 1500)), axis=1))
    pert = base.copy()
    pert[:, 900:] *= 1.7

    def causal_outputs(p):
        ff = rule_forecasts(p)
        w, fdm = arm_weights(p, ff, fe_signals.vol_n(p), fe_signals.early_components(p), synthetic_checkpoints(p.shape[1]))
        return ff, fdm, w

    fa, da, wa = causal_outputs(base)
    fb, db, wb = causal_outputs(pert)
    causal = bool(
        np.array_equal(fa[:, :, :900], fb[:, :, :900], equal_nan=True)
        and np.array_equal(da[:, :900], db[:, :900])
        and all(np.array_equal(wa[n][:, :900], wb[n][:, :900]) for n in ARM_NAMES)
    )
    print("causality (perturbing t>=900 leaves rules, FDM and arms before 900 unchanged):", "PASS" if causal else "FAIL")
    ok = ok and ident_vote and par_c3 and f2_eq and causal
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


# ----------------------------------------------------------------------------- diagnostics
def fdm_table(days, f):
    rows = []
    for c in real_checkpoints(days):
        val, n, R, q = fdm_at(f, c)
        rows.append(
            {
                "checkpoint": str(days[c])[:10],
                "n_joint": int(n[0]),
                "rho_12": float(R[0, 0, 1]),
                "rho_13": float(R[0, 0, 2]),
                "rho_23": float(R[0, 1, 2]),
                "w_rho_w": float(q[0]),
                "fdm_in_force_from_next_session": float(val[0]),
            }
        )
    T = len(days) - 1
    full_val, full_n, full_R, full_q = fdm_at(f, T - 1)
    full = {
        "note": "in-sample, uses sessions after each year-end: information only, no arm uses it",
        "n_joint": int(full_n[0]),
        "rho_12": float(full_R[0, 0, 1]),
        "rho_13": float(full_R[0, 0, 2]),
        "rho_23": float(full_R[0, 1, 2]),
        "w_rho_w": float(full_q[0]),
        "fdm": float(full_val[0]),
    }
    return rows, full


def measure():
    B0 = g2.build(0.0)
    days = B0["days"]
    check_sealed(days)
    f = rule_forecasts(B0["price"][None, :])
    rows, full = fdm_table(days, f)
    print(f"{'checkpoint':>10s} {'n':>5s} {'rho12':>7s} {'rho13':>7s} {'rho23':>7s} {'wRw':>7s} {'FDM':>6s}")
    for r in rows:
        print(
            f"{r['checkpoint']:>10s} {r['n_joint']:5d} {r['rho_12']:7.3f} {r['rho_13']:7.3f} {r['rho_23']:7.3f} "
            f"{r['w_rho_w']:7.3f} {r['fdm_in_force_from_next_session']:6.3f}"
        )
    print("full-sample (look-ahead, info only):", json.dumps(full, sort_keys=True))
    return rows, full


# ----------------------------------------------------------------------------- aggregation
def pre_block(B, env, W, m):
    days = B["days"]
    T = len(days) - 1
    out, rets = {}, {}
    for label, (w, arm) in W.items():
        r = fe_sim.simulate(B["o"], B["c"], w, env, CAPITAL, START, T, arm=arm, mode="pre_tax", m=m)
        st = fe_study.pre_stats(days, START, T, r)
        out[label] = {
            "cagr": st["cagr"], "max_dd": st["max_dd"], "calmar": st["calmar"],
            "cagr_2017": st["2017_end"]["cagr"],
            "subperiods": {k: v["cagr"] for k, v in st["subperiods"].items()},
            "sells_per_year_qld": st["sells_per_year"], "turnover_annual": st["turnover_annual"],
            "trades": st["trades"],
        }
        if m == M:
            rets[label] = g2.daily(r)
    return out, rets


def tax_block(B, W, days, fx, ufe):
    out = {}
    for label, (w, arm) in W.items():
        out[label] = {
            "windows": {wn: g2.tax_window(days, fx, ufe, B["o"], B["c"], w, arm, a, b) for wn, (a, b) in WINDOWS.items()},
            "halves": {hn: g2.tax_window(days, fx, ufe, B["o"], B["c"], w, arm, a, b) for hn, (a, b) in HALVES.items()},
        }
    return out


def exposure_block(W, v):
    """Mean QLD target weight from START on: all days, vote-on (v >= 2) days, other days; QQQ-equivalent = 2 x QLD."""
    out = {}
    on = v[START:] >= 2
    for label, (w, _arm) in W.items():
        x = np.asarray(w, dtype=float)[START:]
        out[label] = {
            "mean_qld_weight_all": float(x.mean()),
            "mean_qld_weight_vote_on": float(x[on].mean()),
            "mean_qld_weight_vote_off": float(x[~on].mean()),
            "qqq_equiv_vote_on": float(2.0 * x[on].mean()),
            "share_days_invested": float(np.mean(x > 0)),
        }
    return out


def oos_block(labels):
    """labels: label -> (source, rule); source 'R2' = production w_r2, else an arm name. Calibrated, m = 2, USD rule."""
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    markets = OrderedDict([("GSPC_1957_1999", (gd, go, gc, gy, gstart))])
    for key in OOS_POOL:
        markets[key] = fe_data.load_market(key)
    out, daily_by = OrderedDict(), OrderedDict()
    for wn, (days, o, c, y, start) in markets.items():
        check_sealed(days)
        so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + fe_config.EXTRA_FIN, fe_config.EXP_Q)
        e2 = fe_signals.e2_up(c)[0]
        comps = fe_signals.early_components(c)
        vol = fe_signals.vol_n(sc)[0]
        _we2, w_r2 = fe_signals.wants_of(c, e2, vol, comps)
        tg, _fdm = arm_targets(c[None, :], sc[None, :], comps, real_checkpoints(days))
        env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
        out[wn], daily_by[wn] = {}, {}
        for label, (src, arm) in labels.items():
            w = w_r2 if src == "R2" else tg[src]
            r = fe_sim.simulate(so, sc, w, env, 1.0, start, len(days) - 1, arm=arm, mode="pre_tax", m=M, rule_index=False)
            st = fe_study.window_stats(r["_nav"], days, start)
            out[wn][label] = {"cagr": st["cagr"], "max_dd": st["max_dd"], "turnover_annual": float(r["turnover_annual"])}
            daily_by[wn][label] = g2.daily(r)
    return out, daily_by


def pooled_oos_boot(daily_by, labels):
    """G8: block-20 resampling of M1-M5 daily NAV returns, paired across labels (2,000 paths per market)."""
    rng = np.random.default_rng(G8_SEED)
    n_blocks = G8_LEN // G8_BLOCK
    pooled = {lab: [] for lab in labels}
    for mk in OOS_POOL:
        n = len(daily_by[mk][labels[0]])
        starts = rng.integers(0, n - G8_BLOCK + 1, size=(G8_PATHS, n_blocks))
        idx = (starts[:, :, None] + np.arange(G8_BLOCK)).reshape(G8_PATHS, G8_LEN)
        for lab in labels:
            lr = np.log1p(daily_by[mk][lab])
            pooled[lab].append(np.expm1(lr[idx].sum(axis=1) * 252.0 / G8_LEN))
    pooled = {lab: np.concatenate(v) for lab, v in pooled.items()}
    return {lab: float(np.median(pooled[lab] - pooled["K"]) * 100) for lab in labels if lab != "K"}


def boot_block(ctrl):
    out = {}
    for name in ARM_NAMES:
        arm = boot_run(name)
        b = {"pre": {}, "tax": {}}
        for mdi in ("base", "calibrated"):
            d = (arm["paths"]["pre"][(mdi, "K")] - ctrl["paths"]["pre"][(mdi, "K")]) * 100
            b["pre"][mdi] = {"median_delta_pp": float(np.median(d)), "share_gt_0": float(np.mean(d > 0))}
        for mode, tag in (("progressive", "B1"), ("flat", "B2")):
            d = (arm["paths"]["tax"]["100k"][mode]["K"] - ctrl["paths"]["tax"]["100k"][mode]["K"]) * 100
            b["tax"][tag] = {"median_delta_pp": float(np.median(d)), "share_gt_0": float(np.mean(d > 0))}
        out[name] = b
    return out


def dsr_of(r):
    mom = sample_moments(r)
    d = dsr(mom["sr"], mom["T"], mom["skew"], mom["kurt"], N_TRIALS, var_trials_floor(mom["T"]))
    return {"sr_per_day": float(mom["sr"]), "dsr": float(d["dsr"]), "sr0": float(d["sr0"]), "T": int(mom["T"])}


def run():
    t0 = time.time()
    B0 = g2.build(0.0)
    Bc = g2.build(CAL_SPREAD)
    days = B0["days"]
    last = check_sealed(days)
    T = len(days) - 1
    cps = real_checkpoints(days)
    fx, ufe = fe_study.try_market(days)
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    W0, fdm0 = targets(B0, cps)
    Wc, _ = targets(Bc, cps)
    pre_b2, rets_b = pre_block(B0, env, W0, M)
    pre_c2, _ = pre_block(Bc, env, Wc, M)
    pre_b1, _ = pre_block(B0, env, W0, 1)
    pre_c1, _ = pre_block(Bc, env, Wc, 1)
    tax = tax_block(B0, W0, days, fx, ufe)
    v_main = votes(B0["price"][None, :])[0]
    expo = exposure_block(W0, v_main)
    oos_labels = OrderedDict([("K", ("R2", "K")), ("R2", ("R2", "C0"))] + [(n, (n, "K")) for n in ARM_NAMES])
    oos, oos_daily = oos_block(oos_labels)
    g8 = pooled_oos_boot(oos_daily, list(oos_labels))
    boot = boot_block(boot_run(None))
    rows, full = fdm_table(days, rule_forecasts(B0["price"][None, :]))
    arms = OrderedDict()
    for lab in W0:
        arms[lab] = {
            "pre_tax_base_m2": pre_b2[lab], "pre_tax_cal_m2": pre_c2[lab],
            "pre_tax_base_m1": pre_b1[lab], "pre_tax_cal_m1": pre_c1[lab],
            "after_tax_100k": tax[lab],
            "exposure": expo[lab],
            "oos_calibrated_m2": {wn: oos[wn][lab] for wn in OOS_WIN},
            "dsr_base_m2": dsr_of(rets_b[lab]),
        }
        if lab in ARM_NAMES:
            arms[lab]["bootstrap_vs_K"] = boot[lab]
            arms[lab]["g8_pooled_oos_median_delta_pp"] = g8[lab]
    gates = OrderedDict((lab, gate_table(lab, arms)) for lab in ARM_NAMES)
    inputs = json.loads(MANIFEST.read_text())["files_sha256"]
    out = {
        "study": "fdm_v1",
        "window": {"start": str(days[START])[:10], "end": str(days[-1])[:10], "sessions": T - START + 1},
        "sealed": {"sealed_from": SEALED_FROM.isoformat(), "last_session_loaded": last},
        "n_trials_dsr": N_TRIALS,
        "fdm_diagnostics": {"checkpoints": rows, "full_sample_lookahead": full,
                            "fdm_in_force_mean_main_window": float(fdm0[START:].mean()),
                            "fdm_in_force_max_main_window": float(fdm0[START:].max())},
        "arms": arms,
        "gates": gates,
        "provenance": stamp({k: ROOT / k for k in inputs}),
    }
    RESULTS.write_text(json.dumps(out, indent=2, sort_keys=True, default=_jsonable) + "\n")
    elapsed = time.time() - t0
    SUMMARY.write_text(summary_text(out, elapsed))
    print(f"wall clock {elapsed:.1f} s")
    return out


def _jsonable(x):
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    raise TypeError(f"not serialisable: {type(x)}")


# ----------------------------------------------------------------------------- gates and summary
def _g1(cur, ref):
    """ewma_band_v1 G1: CAGR >= reference, or Calmar >= 1.1 x reference with CAGR >= reference - 0.5 pp."""
    return cur["cagr"] >= ref["cagr"] or (cur["calmar"] >= 1.1 * ref["calmar"] and cur["cagr"] >= ref["cagr"] - 0.005)


def gate_table(lab, A):
    K, R = A["K"], A[lab]
    both = ("pre_tax_base_m2", "pre_tax_cal_m2")
    g = OrderedDict()
    g["G1"] = all(_g1(R[k], K[k]) for k in both)
    g["G2"] = all(R[k]["max_dd"] <= K[k]["max_dd"] + 0.01 for k in both)
    g["G3"] = all(R[k]["cagr_2017"] >= K[k]["cagr_2017"] - 0.01 for k in both)
    g["G4"] = all(
        sum(R[k]["subperiods"][p] > K[k]["subperiods"][p] for p in ("P1", "P2", "P3")) >= 2 for k in both
    )
    b = R["bootstrap_vs_K"]["pre"]
    g["G5"] = b["base"]["median_delta_pp"] >= 0 and b["calibrated"]["median_delta_pp"] >= 0
    g["G6"] = _g1(R["pre_tax_base_m1"], K["pre_tax_base_m1"]) and _g1(R["pre_tax_cal_m1"], K["pre_tax_cal_m1"])
    dc = np.array([(R["oos_calibrated_m2"][w]["cagr"] - K["oos_calibrated_m2"][w]["cagr"]) * 100 for w in OOS_WIN])
    dd = np.array([(R["oos_calibrated_m2"][w]["max_dd"] - K["oos_calibrated_m2"][w]["max_dd"]) * 100 for w in OOS_WIN])
    g["G7"] = bool(np.median(dc) >= 0 and np.sum(dc >= -0.5) >= 5 and np.sum(dd <= 3.0) >= 5 and dd.max() <= 8.0)
    g["G8"] = R["g8_pooled_oos_median_delta_pp"] >= 0
    g["G9"] = R["pre_tax_base_m2"]["turnover_annual"] <= 2 * K["pre_tax_base_m2"]["turnover_annual"]
    tw = {w: (R["after_tax_100k"]["windows"][w]["after_tax_cagr"] - K["after_tax_100k"]["windows"][w]["after_tax_cagr"]) * 100
          for w in WINDOWS}
    g["AT_all_three_windows_beat_K"] = all(v > 0 for v in tw.values())
    bt = R["bootstrap_vs_K"]["tax"]
    g["AT_B1_B2"] = all(bt[t]["median_delta_pp"] >= 0 and bt[t]["share_gt_0"] >= 0.55 for t in ("B1", "B2"))
    hd = {h: (R["after_tax_100k"]["halves"][h]["after_tax_cagr"] - K["after_tax_100k"]["halves"][h]["after_tax_cagr"]) * 100
          for h in HALVES}
    g["REPORTED_halves_both_positive"] = all(v > 0 for v in hd.values())
    g["ALL_GATED_PASS"] = all(bool(g[k]) for k in g if not k.startswith("REPORTED"))
    out = {k: bool(v) for k, v in g.items()}
    out["after_tax_delta_pp"] = {k: float(v) for k, v in tw.items()}
    out["halves_delta_pp"] = {k: float(v) for k, v in hd.items()}
    return out


def summary_text(out, elapsed):
    lines = [
        f"fdm_v1  wall clock {elapsed:.1f} s  main window {out['window']['start']} .. {out['window']['end']}  "
        f"last session loaded {out['sealed']['last_session_loaded']} (sealed from {out['sealed']['sealed_from']})",
        f"DSR trials N = {out['n_trials_dsr']}",
        "FDM in force, main window: mean {:.3f}, max {:.3f}".format(
            out["fdm_diagnostics"]["fdm_in_force_mean_main_window"], out["fdm_diagnostics"]["fdm_in_force_max_main_window"]
        ),
    ]
    for lab, A in out["arms"].items():
        b2, c2 = A["pre_tax_base_m2"], A["pre_tax_cal_m2"]
        at = A["after_tax_100k"]["windows"]
        ex = A["exposure"]
        lines.append(
            f"{lab:4s} pre base {b2['cagr'] * 100:6.2f}% cal {c2['cagr'] * 100:6.2f}% DD {b2['max_dd'] * 100:5.1f}% "
            f"turnover {b2['turnover_annual']:.2f}/yr sells(QLD) {b2['sells_per_year_qld']:.1f}/yr | "
            f"AT 2000 {at['2000_03_to_2026_09']['after_tax_cagr'] * 100:6.2f} 2010 {at['2010_01_to_end']['after_tax_cagr'] * 100:6.2f} "
            f"2017 {at['2017_01_to_end']['after_tax_cagr'] * 100:6.2f} sells(all)/yr {at['2000_03_to_2026_09']['sells_per_year']:.1f} "
            f"DSR {A['dsr_base_m2']['dsr']:.3f}"
        )
        lines.append(
            f"     exposure QLD mean all {ex['mean_qld_weight_all']:.3f} vote-on {ex['mean_qld_weight_vote_on']:.3f} "
            f"vote-off {ex['mean_qld_weight_vote_off']:.3f} | QQQ-equiv vote-on {ex['qqq_equiv_vote_on']:.3f}"
        )
    for lab, G in out["gates"].items():
        lines.append(f"{lab} gates: " + " ".join(f"{k}={'PASS' if v else 'fail'}" for k, v in G.items() if isinstance(v, bool)))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.harness:
        raise SystemExit(0 if harness() else 1)
    if args.measure:
        measure()
        return
    if args.run:
        run()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
