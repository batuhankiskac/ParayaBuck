"""carver_forecast_v1: continuous trend forecast arms vs K after Turkish tax (PREREGISTRATION.md).

    python -m research.carver_forecast_v1.screen --harness   # K / R2 anchors (gates_v2 harness, incl. parity --quick) + causality
    python -m research.carver_forecast_v1.screen --run       # study -> RESULTS.json, SUMMARY.txt

Simulation goes through fe_sim.simulate, tax through fe_sim/fe_tax, bootstraps through fe_boot.boot. This file only
builds target-weight arrays (numpy / pandas rolling) and aggregates results.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.gates_v1.stats import dsr, sample_moments, var_trials_floor
from research.gates_v2 import screen as g2

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

START = fe_config.START
CAP = fe_config.CAP
CAPITAL = 100_000.0
M = 2
CAL_SPREAD = fe_config.FIN["calibrated"]
WINDOWS = g2.WINDOWS
HALVES = g2.HALVES
ARM_NAMES = ("C1", "C2", "C3", "C4")
OOS_WIN = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")
OOS_POOL = ("M1", "M2", "M3", "M4", "M5")
N_TRIALS = 365 + len(ARM_NAMES)
MIN_OBS = 20
FC_CAP = 20.0
FC_SCALE = 10.0
EWMA_SPAN = 5
G8_SEED = 20261011
G8_PATHS = 2000
G8_LEN = 2520
G8_BLOCK = 20

_ORIG_CHUNK = fe_boot.chunk_legs  # captured before any monkeypatch


# ----------------------------------------------------------------------------- forecast (no look-ahead)
def raw_signals(price):
    """Three raw trend signals per path, each (P, T): SMA200 distance, 252-session return, 126-session channel."""
    p = np.atleast_2d(np.asarray(price, dtype=float))
    df = pd.DataFrame(p.T)
    sma = df.rolling(fe_config.SMA_N).mean()
    mx = df.rolling(fe_config.DON_N).max()
    mn = df.rolling(fe_config.DON_N).min()
    r1 = df / sma - 1.0
    r2 = df / df.shift(fe_config.TSM_N) - 1.0
    rng = mx - mn
    r3 = (2.0 * (df - mn) / rng - 1.0).mask(rng == 0.0, 0.0)
    return [x.to_numpy().T for x in (r1, r2, r3)]


def expanding_mean_abs(r):
    """Expanding-window mean of |r| over valid sessions <= t; undefined until MIN_OBS valid values exist."""
    a = np.abs(r)
    ok = np.isfinite(a)
    cnt = np.cumsum(ok, axis=1)
    tot = np.cumsum(np.where(ok, a, 0.0), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = tot / cnt
    return np.where(cnt >= MIN_OBS, m, np.nan)


def forecasts(price):
    """Combined forecast (P, T): mean of three capped, scaled forecasts; NaN until every rule has a scalar."""
    fs = []
    for r in raw_signals(price):
        with np.errstate(divide="ignore", invalid="ignore"):
            f = np.clip(r / expanding_mean_abs(r) * FC_SCALE, -FC_CAP, FC_CAP)
        fs.append(f)
    return np.mean(np.stack(fs), axis=0)


def weights(name, price, comb, vol, comps):
    """Target weights (P, T) of one arm under fill rule K. vol = vol_n of the synthetic 2x close."""
    vt = fe_signals.vt_of(vol)
    valid = np.isfinite(comb)
    fpos = np.where(valid, np.maximum(comb, 0.0), 0.0)
    if name == "C1":
        return np.minimum(CAP, fpos / 20.0 * vt)
    w2 = np.minimum(CAP, np.minimum(1.0, fpos / 10.0) * vt)
    if name == "C2":
        return w2
    if name == "C3":
        fs = pd.DataFrame(fpos.T).ewm(span=EWMA_SPAN, adjust=False).mean().to_numpy().T
        return np.minimum(CAP, np.minimum(1.0, fs / 10.0) * vt)
    if name == "C4":
        e2p = valid & (comb > 0.0)
        _on, factor = fe_signals.r2_state(price, e2p, comps)
        early = np.minimum(CAP, 0.5 * vt) * (factor == 0.5)
        return np.where(e2p, w2, early)
    raise ValueError(f"unknown arm {name!r}")


def arm_targets(price, cl_2x, comps):
    """price, cl_2x: (1, T) QQQ close and synthetic 2x close (sets vol20); returns name -> (T,) weights."""
    comb = forecasts(price)
    vol = fe_signals.vol_n(cl_2x)
    return {n: weights(n, price, comb, vol, comps)[0] for n in ARM_NAMES}


# ----------------------------------------------------------------------------- bootstrap wrapper
def _forecast_chunk(name):
    """Replacement for fe_boot.chunk_legs: same draws, forecast-arm targets computed on each resampled path."""

    def chunk(q, y, pos):
        legs, e2, w_e2 = _ORIG_CHUNK(q, y, pos)
        idx = pos + 1
        prev = q[1][idx - 1]
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        P = idx.shape[0]
        pc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        comb = forecasts(pc)
        comps = fe_signals.early_components(pc)
        new = {}
        for key, (so, sc, _w) in legs.items():
            new[key] = (so, sc, weights(name, pc, comb, fe_signals.vol_n(sc), comps))
        return new, e2, w_e2

    return chunk


def boot_run(name=None):
    """name None: unmodified K control (R2 target, rule K). Else forecast arm `name` on the same draws."""
    fe_boot.chunk_legs = _ORIG_CHUNK if name is None else _forecast_chunk(name)
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
    rng = np.random.default_rng(1)
    base = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, size=(1, 1500)), axis=1))
    comb_a = forecasts(base)
    pert = base.copy()
    pert[:, 900:] *= 1.7
    comb_b = forecasts(pert)
    causal = bool(np.array_equal(comb_a[:, :900], comb_b[:, :900], equal_nan=True))
    print("forecast causality (perturbing t>=900 leaves t<900 unchanged):", "PASS" if causal else "FAIL")
    ok = ok and causal
    print("HARNESS", "PASS" if ok else "FAIL")
    return ok


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


def oos_block(labels):
    """labels: label -> (source, rule); source 'R2' = production w_r2, else an arm name. Calibrated, m = 2, USD rule."""
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    markets = OrderedDict([("GSPC_1957_1999", (gd, go, gc, gy, gstart))])
    for key in OOS_POOL:
        markets[key] = fe_data.load_market(key)
    out, daily_by = OrderedDict(), OrderedDict()
    for wn, (days, o, c, y, start) in markets.items():
        so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + fe_config.EXTRA_FIN, fe_config.EXP_Q)
        e2 = fe_signals.e2_up(c)[0]
        comps = fe_signals.early_components(c)
        vol = fe_signals.vol_n(sc)[0]
        _we2, w_r2 = fe_signals.wants_of(c, e2, vol, comps)
        tg = arm_targets(c[None, :], sc[None, :], comps)
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
    return {"sr_per_day": mom["sr"], "dsr": d["dsr"], "sr0": d["sr0"], "T": mom["T"]}


def targets(B):
    tg = arm_targets(B["price"][None, :], B["c"][None, :], B["comps"])
    W = OrderedDict([("K", (B["w_r2"], "K")), ("R2", (B["w_r2"], "C0"))])
    for n in ARM_NAMES:
        W[n] = (tg[n], "K")
    return W


def run():
    t0 = time.time()
    B0 = g2.build(0.0)
    Bc = g2.build(CAL_SPREAD)
    days = B0["days"]
    T = len(days) - 1
    fx, ufe = fe_study.try_market(days)
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    W0, Wc = targets(B0), targets(Bc)
    pre_b2, rets_b = pre_block(B0, env, W0, M)
    pre_c2, _ = pre_block(Bc, env, Wc, M)
    pre_b1, _ = pre_block(B0, env, W0, 1)
    pre_c1, _ = pre_block(Bc, env, Wc, 1)
    tax = tax_block(B0, W0, days, fx, ufe)
    oos_labels = OrderedDict([("K", ("R2", "K")), ("R2", ("R2", "C0"))] + [(n, (n, "K")) for n in ARM_NAMES])
    oos, oos_daily = oos_block(oos_labels)
    g8 = pooled_oos_boot(oos_daily, list(oos_labels))
    boot = boot_block(boot_run(None))
    arms = OrderedDict()
    for lab in W0:
        arms[lab] = {
            "pre_tax_base_m2": pre_b2[lab], "pre_tax_cal_m2": pre_c2[lab],
            "pre_tax_base_m1": pre_b1[lab], "pre_tax_cal_m1": pre_c1[lab],
            "after_tax_100k": tax[lab],
            "oos_calibrated_m2": {wn: oos[wn][lab] for wn in OOS_WIN},
            "dsr_base_m2": dsr_of(rets_b[lab]),
        }
        if lab in ARM_NAMES:
            arms[lab]["bootstrap_vs_K"] = boot[lab]
            arms[lab]["g8_pooled_oos_median_delta_pp"] = g8[lab]
    gates = OrderedDict((lab, gate_table(lab, arms)) for lab in ARM_NAMES)
    out = {
        "study": "carver_forecast_v1",
        "window": {"start": days[START], "end": days[-1], "sessions": T - START + 1},
        "n_trials_dsr": N_TRIALS,
        "arms": arms,
        "gates": gates,
    }
    RESULTS.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    elapsed = time.time() - t0
    SUMMARY.write_text(summary_text(out, elapsed))
    print(f"wall clock {elapsed:.1f} s")
    return out


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
    b = A["bootstrap_vs_K"]["pre"]
    g["G5"] = b["base"]["median_delta_pp"] >= 0 and b["calibrated"]["median_delta_pp"] >= 0
    g["G6"] = _g1(R["pre_tax_base_m1"], K["pre_tax_base_m1"]) and _g1(R["pre_tax_cal_m1"], K["pre_tax_cal_m1"])
    dc = np.array([(R["oos_calibrated_m2"][w]["cagr"] - K["oos_calibrated_m2"][w]["cagr"]) * 100 for w in OOS_WIN])
    dd = np.array([(R["oos_calibrated_m2"][w]["max_dd"] - K["oos_calibrated_m2"][w]["max_dd"]) * 100 for w in OOS_WIN])
    g["G7"] = bool(np.median(dc) >= 0 and np.sum(dc >= -0.5) >= 5 and np.sum(dd <= 3.0) >= 5 and dd.max() <= 8.0)
    g["G8"] = A["g8_pooled_oos_median_delta_pp"] >= 0
    g["G9"] = R["pre_tax_base_m2"]["turnover_annual"] <= 2 * K["pre_tax_base_m2"]["turnover_annual"]
    tw = {w: (R["after_tax_100k"]["windows"][w]["after_tax_cagr"] - K["after_tax_100k"]["windows"][w]["after_tax_cagr"]) * 100
          for w in WINDOWS}
    g["AT_all_three_windows_beat_K"] = all(v > 0 for v in tw.values())
    bt = A["bootstrap_vs_K"]["tax"]
    g["AT_B1_B2"] = all(bt[t]["median_delta_pp"] >= 0 and bt[t]["share_gt_0"] >= 0.55 for t in ("B1", "B2"))
    hd = {h: (R["after_tax_100k"]["halves"][h]["after_tax_cagr"] - K["after_tax_100k"]["halves"][h]["after_tax_cagr"]) * 100
          for h in HALVES}
    g["REPORTED_halves_both_positive"] = all(v > 0 for v in hd.values())
    g["ALL_GATED_PASS"] = all(bool(g[k]) for k in g if not k.startswith("REPORTED"))
    out = {k: bool(v) for k, v in g.items()}
    out["after_tax_delta_pp"] = tw
    out["halves_delta_pp"] = hd
    return out


def summary_text(out, elapsed):
    lines = [
        f"carver_forecast_v1  wall clock {elapsed:.1f} s  main window {out['window']['start']} .. {out['window']['end']}",
        f"DSR trials N = {out['n_trials_dsr']}",
    ]
    for lab, A in out["arms"].items():
        b2, c2 = A["pre_tax_base_m2"], A["pre_tax_cal_m2"]
        at = A["after_tax_100k"]["windows"]
        lines.append(
            f"{lab:4s} pre base {b2['cagr'] * 100:6.2f}% cal {c2['cagr'] * 100:6.2f}% DD {b2['max_dd'] * 100:5.1f}% "
            f"turnover {b2['turnover_annual']:.2f}/yr sells(QLD) {b2['sells_per_year_qld']:.1f}/yr | "
            f"AT 2000 {at['2000_03_to_2026_09']['after_tax_cagr'] * 100:6.2f} 2010 {at['2010_01_to_end']['after_tax_cagr'] * 100:6.2f} "
            f"2017 {at['2017_01_to_end']['after_tax_cagr'] * 100:6.2f} sells(all)/yr {at['2000_03_to_2026_09']['sells_per_year']:.1f} "
            f"DSR {A['dsr_base_m2']['dsr']:.3f}"
        )
    for lab, G in out["gates"].items():
        lines.append(f"{lab} gates: " + " ".join(f"{k}={'PASS' if v else 'fail'}" for k, v in G.items() if isinstance(v, bool)))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.harness:
        raise SystemExit(0 if harness() else 1)
    if args.run:
        run()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
