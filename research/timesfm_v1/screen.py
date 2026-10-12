#!/usr/bin/env python3
"""timesfm_v1 screen (see PREREGISTRATION.md). Offline and deterministic; numpy only.

Reads the committed data/historical/stress_robustness_v1_{daily,dtb3}.csv and the forecast cache
written by generate_forecasts.py (forecasts.csv, forecasts.meta.json, g9_verify.json). Never runs
the model. Data loading, the synthetic QLD*, signals, the simulator and the metrics are imported
from research/trend_robustness_v2/screen.py (its main() is not run).
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "research" / "trend_robustness_v2"))
import screen as trv2  # noqa: E402

FORECASTS = HERE / "forecasts.csv"
META = HERE / "forecasts.meta.json"
G9 = HERE / "g9_verify.json"
REF = ROOT / "research" / "trend_robustness_v2" / "RESULTS.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

ARMS = ("C0", "V1", "V0", "D1", "D2")
CANDIDATES = ("V1", "V0", "D1", "D2")
TFM_ARMS = ("V1", "D1", "D2")
FIRST_T = 269  # first forecast / signal close (2000-03-31)
START = FIRST_T + 1  # first measured session (2000-04-03)
LAMBDA = 0.94
SUBPERIODS = (("P1", "2000-04-03", "2008-12-31"), ("P2", "2009-01-02", "2016-12-30"),
              ("P3", "2017-01-03", "9999-12-31"))
HOLDOUT = ("2025-09-15", "9999-12-31")
TOL_HARNESS = 1e-4  # 0.01 pp
TOL_CACHE = 1e-12
QLIKE_FLOOR = 1e-4


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ----------------------------------------------------------------------------- inputs
def read_cache(T):
    vol_tfm, dir20, dir60 = (np.full(T, np.nan) for _ in range(3))
    c_vol20, c_logtr = np.full(T, np.nan), np.full(T, np.nan)
    dates = {}
    with FORECASTS.open() as fh:
        for r in csv.DictReader(fh):
            t = int(r["t"])
            dates[t] = r["date"]
            vol_tfm[t], dir20[t], dir60[t] = float(r["vol_tfm"]), float(r["dir_h20"]), float(r["dir_h60"])
            c_vol20[t], c_logtr[t] = float(r["vol20"]), float(r["logtr"])
    return dates, vol_tfm, dir20, dir60, c_vol20, c_logtr


def logtr_of(q):
    g = np.ones(q.shape[1])
    g[1:] = (q[1][1:] + q[2][1:]) / q[1][:-1]
    return np.log(q[1][0] * np.cumprod(g))


def ewma_vol(c, lam=LAMBDA, n=trv2.VOL_N):
    """RiskMetrics: seed at index n with mean of r(1..n)^2, then s2[t] = lam*s2[t-1] + (1-lam)*r[t]^2."""
    r = np.full(len(c), np.nan)
    r[1:] = c[1:] / c[:-1] - 1
    s2 = np.full(len(c), np.nan)
    s2[n] = np.mean(r[1 : n + 1] ** 2)
    for t in range(n + 1, len(c)):
        s2[t] = lam * s2[t - 1] + (1 - lam) * r[t] ** 2
    return np.sqrt(252 * s2)


def cap(raw):
    """Single-leg version of trv2.build_wants' cap: proportional scaling to 0.98 == min(raw, 0.98)."""
    raw = np.atleast_2d(raw)[:, None, :] if np.ndim(raw) == 1 else raw
    tot = raw.sum(axis=1, keepdims=True)
    f = np.where(tot > trv2.CAP, trv2.CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return raw * f


def wants_for(arm, up200, vol20, vol_tfm, vol_ewma, dir20, dir60, logtr):
    up = up200.astype(float)[0]
    if arm == "C0":
        return trv2.build_wants({200: up200}, {200: up200}, vol20, vol20, "C0")
    with np.errstate(invalid="ignore"):
        if arm == "V1":
            raw = up * trv2.vt_of(vol_tfm)
        elif arm == "V0":
            raw = up * trv2.vt_of(vol_ewma)
        elif arm == "D1":
            raw = (dir60 > logtr).astype(float) * trv2.vt_of(vol20)
        elif arm == "D2":
            raw = up * (dir20 > logtr).astype(float) * trv2.vt_of(vol20)
        else:
            raise ValueError(arm)
    return cap(np.atleast_2d(raw)[0])


# ----------------------------------------------------------------------------- stats
def seg(dates, nav, a, b):
    """From the last close before a to the last close <= b."""
    ia = max(k for k, d in enumerate(dates) if d < a)
    ib = max(k for k, d in enumerate(dates) if d <= b)
    return {"from_close": dates[ia], "to_close": dates[ib],
            "total_return": float(nav[ib] / nav[ia] - 1),
            "cagr": trv2.cagr(nav[ia], nav[ib], trv2.calendar_years(dates[ia], dates[ib])),
            "max_dd": float(trv2.max_dd(nav[ia : ib + 1])[0])}


def full_stats(dates, nav, turn, ntr, years):
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    st["subperiods"] = {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    st["holdout"] = seg(dates, nav, *HOLDOUT)
    return st


def vol_accuracy(ts, rv, fc):
    out = {"n": int(len(ts))}
    for name, f in fc.items():
        f = f[ts]
        y2, f2 = rv[ts] ** 2, f ** 2
        floored = f <= QLIKE_FLOOR
        fq2 = np.where(floored, QLIKE_FLOOR, f) ** 2
        ratio = y2 / fq2
        out[name] = {"mse_var": float(np.mean((y2 - f2) ** 2)),
                     "qlike": float(np.mean(ratio - np.log(ratio) - 1)),
                     "qlike_floored": int(floored.sum()),
                     "mean_forecast_vol": float(np.mean(f))}
    return out


def dir_accuracy(ts, logtr, fc, h):
    pred = fc[ts] > logtr[ts]
    real = logtr[ts + h] > logtr[ts]
    return {"n": int(len(ts)), "hit_rate": float(np.mean(pred == real)),
            "unconditional_up_rate": float(np.mean(real)), "share_up_calls": float(np.mean(pred)),
            "precision_up_calls": float(np.mean(real[pred])) if pred.any() else None}


# ----------------------------------------------------------------------------- main
def main() -> None:
    days, arr, y = trv2.load()
    T = len(days)
    q, s = arr["QQQ"], arr["SPY"]
    meta = json.loads(META.read_text())
    out: dict = {"params": {
        "arms": list(ARMS), "first_signal_t": FIRST_T, "vol_target": trv2.VOL_TARGET, "band": trv2.BAND,
        "total_cap": trv2.CAP, "cost_per_side_m1": trv2.COST_PER_SIDE, "cost_per_side_m2": 2 * trv2.COST_PER_SIDE,
        "expense_qld_star": trv2.EXP_Q, "ewma_lambda": LAMBDA, "subperiods": [list(x) for x in SUBPERIODS],
        "holdout": list(HOLDOUT), "cagr_basis": "calendar days / 365.25",
        "sharpe": "daily simple returns, rf=0, sample std, x sqrt(252)",
        "turnover": "sum over rebalances of |delta w|, annualized by calendar years",
        "execution": "decide at completed close, fill at next open; rebalance when drift > 0.5pp; cash earns zero",
        "vol_tfm": "TimesFM 2.5 median forecast of vol20 at h=20",
        "dir": "TimesFM 2.5 median forecast of log QQQ TR close at h=20 (D2) / h=60 (D1)",
    }}
    out["data_checks"] = {"sessions": T, "first": days[0], "last": days[-1]}
    out["forecast_meta"] = {k: meta[k] for k in ("model", "versions", "dates", "forecasts_csv_sha256", "inputs")}

    qsyn_o, qsyn_c = trv2.synthetic_2x(q[0], q[1], q[2], y, trv2.EXP_Q)
    up200 = trv2.sma_up(q[1], 200)
    vol20 = trv2.vol_n(qsyn_c)

    # --- harness check FIRST: C0 on trend_robustness_v2's window must reproduce its C0 at m=1 and m=2
    ref = json.loads(REF.read_text())["historical"]
    w = trv2.build_wants({200: up200}, {200: up200}, vol20, vol20, "C0")
    harness = {"window_start_index": trv2.SMA_MAX, "tolerance_pp": TOL_HARNESS * 100}
    ok = True
    for m in (1, 2):
        nav = trv2.simulate_multi(qsyn_o, qsyn_c, w, trv2.COST_PER_SIDE * m, trv2.SMA_MAX)[0][0]
        st = trv2.curve_stats(days[trv2.SMA_MAX - 1 :], nav)
        r = ref[f"m{m}"]["C0"]
        d_c, d_d = st["cagr"] - r["cagr"], st["max_dd"] - r["max_dd"]
        good = abs(d_c) <= TOL_HARNESS and abs(d_d) <= TOL_HARNESS
        ok = ok and good
        harness[f"m{m}"] = {"window": [st["start"], st["end"]], "ref_cagr": r["cagr"], "ref_max_dd": r["max_dd"],
                            "cagr": st["cagr"], "max_dd": st["max_dd"], "diff_cagr_pp": d_c * 100,
                            "diff_max_dd_pp": d_d * 100, "pass": bool(good)}
        print(f"harness m={m}: CAGR {st['cagr']*100:.4f}% vs {r['cagr']*100:.4f}%, "
              f"DD {st['max_dd']*100:.4f}% vs {r['max_dd']*100:.4f}% -> {'PASS' if good else 'FAIL'}")
    harness["pass"] = bool(ok)
    out["harness_check"] = harness
    if not ok:
        out["status"] = "harness_failed"
        OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")
        print("HARNESS FAILED - stopping before reading any arm result")
        sys.exit(1)

    # --- cache sanity check: cached inputs equal recomputation; cache unchanged since generation
    if sha256(FORECASTS) != meta["forecasts_csv_sha256"]:
        raise SystemExit("forecasts.csv does not match forecasts.meta.json")
    cdates, vol_tfm, dir20, dir60, c_vol20, c_logtr = read_cache(T)
    logtr = logtr_of(q)
    ts_all = np.array(sorted(cdates))
    rel_v = np.abs(c_vol20[ts_all] - vol20[0][ts_all]) / np.abs(vol20[0][ts_all])
    rel_l = np.abs(c_logtr[ts_all] - logtr[ts_all]) / np.abs(logtr[ts_all])
    cache_ok = (ts_all[0] == FIRST_T and ts_all[-1] == T - 1 and len(ts_all) == T - FIRST_T
                and all(cdates[t] == days[t] for t in ts_all)
                and rel_v.max() <= TOL_CACHE and rel_l.max() <= TOL_CACHE
                and np.isfinite(vol_tfm[ts_all]).all() and np.isfinite(dir20[ts_all]).all()
                and np.isfinite(dir60[ts_all]).all())
    out["cache_check"] = {"dates": len(ts_all), "first": days[ts_all[0]], "last": days[ts_all[-1]],
                          "max_rel_vol20": float(rel_v.max()), "max_rel_logtr": float(rel_l.max()),
                          "tolerance": TOL_CACHE, "pass": bool(cache_ok)}
    print(f"cache check: {len(ts_all)} dates, max rel vol20 {rel_v.max():.1e}, logtr {rel_l.max():.1e} "
          f"-> {'PASS' if cache_ok else 'FAIL'}")
    if not cache_ok:
        out["status"] = "cache_check_failed"
        OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")
        sys.exit(1)

    # --- G9 record
    g9 = json.loads(G9.read_text()) if G9.exists() else None
    g9_pass = bool(g9 and g9["pass"] and g9["forecasts_csv_sha256"] == meta["forecasts_csv_sha256"]
                   and g9["n"] == 50)
    out["g9_verify"] = {"present": g9 is not None, "pass": g9_pass,
                        **({k: g9[k] for k in ("n", "seed", "batch", "rtol", "max_rel_diff")} if g9 else {})}

    # --- arms on the common window
    vol_ewma = ewma_vol(qsyn_c)
    dates = days[START - 1 :]
    years = trv2.calendar_years(dates[0], dates[-1])
    out["common_window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    spy_tr = (s[1][START:] + s[2][START:]) / s[1][START - 1 : -1]
    spy_nav = np.concatenate([[1.0], np.cumprod(spy_tr)])
    hist = {}
    for m in (1, 2):
        hm = {}
        for arm in ARMS:
            w = wants_for(arm, up200, vol20, vol_tfm, vol_ewma, dir20, dir60, logtr)
            nav, turn, ntr = trv2.simulate_multi(qsyn_o, qsyn_c, w, trv2.COST_PER_SIDE * m, START)
            hm[arm] = full_stats(dates, nav[0], turn[0], ntr[0], years)
            sig = w[0, 0, START - 1 : T - 1]
            hm[arm]["mean_target_weight"] = float(sig.mean())
            hm[arm]["share_sessions_invested"] = float(np.mean(sig > 0))
        hm["SPY"] = full_stats(dates, spy_nav, 0.0, 0, years)
        hist[f"m{m}"] = hm
    out["historical"] = hist

    # --- gates
    h1, h2 = hist["m1"], hist["m2"]
    gates = {}
    for arm in CANDIDATES:
        a2, c2, a1, c1 = h2[arm], h2["C0"], h1[arm], h1["C0"]
        g = {}
        g["G1"] = {"pass": bool(a2["cagr"] >= c2["cagr"] - 0.005), "arm": a2["cagr"], "c0": c2["cagr"]}
        g["G2"] = {"pass": bool(a2["max_dd"] <= c2["max_dd"]), "arm": a2["max_dd"], "c0": c2["max_dd"]}
        g["G3"] = {"pass": bool(a2["cagr"] > c2["cagr"] or a2["calmar"] >= 1.10 * c2["calmar"]),
                   "arm_cagr": a2["cagr"], "c0_cagr": c2["cagr"], "arm_calmar": a2["calmar"], "c0_calmar": c2["calmar"]}
        g["G4"] = {"pass": bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.02),
                   "arm": a2["2017_end"]["cagr"], "c0": c2["2017_end"]["cagr"]}
        wins = {p: bool(a2["subperiods"][p]["cagr"] >= c2["subperiods"][p]["cagr"]) for p in ("P1", "P2", "P3")}
        g["G5p"] = {"pass": sum(wins.values()) >= 2, "wins": wins,
                    "arm": {p: a2["subperiods"][p]["cagr"] for p in wins},
                    "c0": {p: c2["subperiods"][p]["cagr"] for p in wins}}
        m1 = (bool(a1["cagr"] >= c1["cagr"] - 0.005), bool(a1["max_dd"] <= c1["max_dd"]),
              bool(a1["cagr"] > c1["cagr"] or a1["calmar"] >= 1.10 * c1["calmar"]))
        g["G6"] = {"pass": all(m1), "m1_G1": m1[0], "m1_G2": m1[1], "m1_G3": m1[2]}
        g["G7"] = {"pass": bool(a2["turnover_annual"] <= 2 * c2["turnover_annual"]),
                   "arm": a2["turnover_annual"], "c0": c2["turnover_annual"]}
        g["G8"] = {"pass": bool(a2["holdout"]["total_return"] >= c2["holdout"]["total_return"] - 0.02),
                   "arm": a2["holdout"]["total_return"], "c0": c2["holdout"]["total_return"]}
        g["G9"] = {"pass": g9_pass if arm in TFM_ARMS else None, "applies": arm in TFM_ARMS}
        keys = ("G1", "G2", "G3", "G4", "G5p", "G6", "G7", "G8") + (("G9",) if arm in TFM_ARMS else ())
        g["pass_all"] = all(g[k]["pass"] for k in keys)
        gates[arm] = g
    out["gates"] = gates
    v1, v0 = h2["V1"], h2["V0"]
    beats = bool(v1["cagr"] > v0["cagr"] or v1["calmar"] >= 1.10 * v0["calmar"])
    out["v1_vs_v0"] = {"v1_beats_v0": beats, "v1_cagr": v1["cagr"], "v0_cagr": v0["cagr"],
                       "v1_calmar": v1["calmar"], "v0_calmar": v0["calmar"],
                       "v1_eligible": bool(gates["V1"]["pass_all"] and beats)}
    eligible = [a for a in CANDIDATES if gates[a]["pass_all"] and (a != "V1" or beats)]
    sel = max(eligible, key=lambda a: (h2[a]["cagr"], h2[a]["calmar"])) if eligible else "none"
    out["selection"] = {"passing": [a for a in CANDIDATES if gates[a]["pass_all"]], "eligible": eligible,
                        "selected": sel,
                        "rule": "highest m=2 CAGR among eligible passing arms (tie: Calmar); V1 eligible only if it beats V0; else none (VT35 unchanged)"}

    # --- report-only forecast diagnostics
    sig_ts = np.arange(FIRST_T, T - 1)  # common-window signal closes
    hold = np.array([days[t] >= HOLDOUT[0] for t in range(T)])
    fc = {"vol20": vol20[0], "vol_ewma": vol_ewma, "vol_tfm": vol_tfm}
    rv = np.full(T, np.nan)
    rv[: T - 20] = vol20[0][20:]
    tv = sig_ts[sig_ts + 20 <= T - 1]
    out["vol_accuracy"] = {"target": "vol20(t+20) (returns t+1..t+20), losses on annualized variance",
                           "full": vol_accuracy(tv, rv, fc), "holdout": vol_accuracy(tv[hold[tv]], rv, fc)}
    da = {}
    for h, f in ((20, dir20), (60, dir60)):
        th = sig_ts[sig_ts + h <= T - 1]
        da[f"h{h}"] = {"full": dir_accuracy(th, logtr, f, h), "holdout": dir_accuracy(th[hold[th]], logtr, f, h)}
    out["dir_accuracy"] = da
    out["status"] = "complete"
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # --- SUMMARY.txt
    pct = lambda x: f"{x * 100:7.2f}%"  # noqa: E731
    L = [f"timesfm_v1 screen | common window {dates[1]} -> {dates[-1]} ({len(dates) - 1} sessions)",
         "harness C0 vs trend_robustness_v2 C0: " + " ".join(
             f"m{m} dCAGR {harness[f'm{m}']['diff_cagr_pp']:.4f}pp dDD {harness[f'm{m}']['diff_max_dd_pp']:.4f}pp"
             for m in (1, 2)) + " (tol 0.01pp) PASS",
         f"cache check PASS ({len(ts_all)} dates) | G9 {'PASS' if g9_pass else 'FAIL'}"
         + (f" (n={g9['n']}, max rel {g9['max_rel_diff']:.1e})" if g9 else " (no g9_verify.json)"), "",
         "m=2 historical:",
         f"{'arm':4s} {'CAGR':>8s} {'maxDD':>8s} {'Calmar':>6s} {'Sharpe':>6s} {'2017+':>8s} "
         f"{'P1':>8s} {'P2':>8s} {'P3':>8s} {'holdout':>8s} {'turnov':>6s} {'trades':>6s} {'invest':>6s}"]
    for arm in ARMS + ("SPY",):
        a = h2[arm]
        sp = a["subperiods"]
        inv = f"{a['share_sessions_invested'] * 100:5.1f}%" if "share_sessions_invested" in a else f"{'-':>6s}"
        L.append(f"{arm:4s} {pct(a['cagr'])} {pct(a['max_dd'])} {a['calmar']:6.2f} {a['sharpe']:6.2f} "
                 f"{pct(a['2017_end']['cagr'])} {pct(sp['P1']['cagr'])} {pct(sp['P2']['cagr'])} {pct(sp['P3']['cagr'])} "
                 f"{pct(a['holdout']['total_return'])} {a['turnover_annual']:6.2f} {a['trades']:6d} {inv}")
    L += ["", "m=1 key numbers (CAGR / maxDD / Calmar):"]
    for arm in ARMS:
        a = h1[arm]
        L.append(f"{arm:4s} {pct(a['cagr'])} {pct(a['max_dd'])} {a['calmar']:6.2f}")
    L += ["", "gates (m=2; G6 = G1-G3 at m=1; G9 only for TimesFM arms):"]
    for arm in CANDIDATES:
        gg = gates[arm]
        L.append(f"{arm}: " + " ".join(
            f"{k}={'n/a' if gg[k]['pass'] is None else ('PASS' if gg[k]['pass'] else 'FAIL')}"
            for k in ("G1", "G2", "G3", "G4", "G5p", "G6", "G7", "G8", "G9"))
            + f" -> {'PASS_ALL' if gg['pass_all'] else 'FAIL'}")
    vv = out["v1_vs_v0"]
    L.append(f"V1 vs V0 (m=2): CAGR {pct(vv['v1_cagr'])} vs {pct(vv['v0_cagr'])}, Calmar {vv['v1_calmar']:.3f} vs "
             f"{vv['v0_calmar']:.3f} -> V1 {'beats' if beats else 'does not beat'} V0")
    L.append(f"selected: {sel}")
    if sel == "none":
        L.append("no arm passes: VT35 stays unchanged, campaign closes")
    L += ["", "report-only: vol forecast accuracy vs vol20(t+20) (annualized variance)"]
    for part in ("full", "holdout"):
        va = out["vol_accuracy"][part]
        L.append(f"  {part} (n={va['n']}): " + " | ".join(
            f"{k} MSE {va[k]['mse_var']:.5f} QLIKE {va[k]['qlike']:.4f}" + (f" floored {va[k]['qlike_floored']}" if va[k]['qlike_floored'] else "")
            for k in ("vol20", "vol_ewma", "vol_tfm")))
    L.append("report-only: direction (log QQQ TR)")
    for h in ("h20", "h60"):
        for part in ("full", "holdout"):
            d = da[h][part]
            prec = "n/a" if d["precision_up_calls"] is None else f"{d['precision_up_calls'] * 100:.1f}%"
            L.append(f"  {h} {part} (n={d['n']}): hit {d['hit_rate'] * 100:.1f}% vs up-rate "
                     f"{d['unconditional_up_rate'] * 100:.1f}% | up-calls {d['share_up_calls'] * 100:.1f}% precision {prec}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
