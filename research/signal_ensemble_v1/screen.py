#!/usr/bin/env python3
"""Signal ensemble v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --harness  C0 only: C0 on the trend_robustness_v2 window (historical m=1/m=2)
             and C0's block-20 bootstrap (back-extended paths) must reproduce
             trend_robustness_v2's C0 to 0.01 pp. Simulates no other arm.
  --run      re-runs the harness, stops if it fails, then runs all arms
             (C0, E2, EP, T1, D1) under base and calibrated financing, the
             bootstraps (blocks 20/60/250), the ^GSPC out-of-sample check (G8),
             the gates and the report-only statistics. Writes RESULTS.json and
             SUMMARY.txt.

Engine, SMA signal, vol, metrics and bootstrap path generation are imported
from research/trend_robustness_v2/screen.py (its main() is never run).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

GSPC = ROOT / "data/historical/signal_ensemble_v1_gspc.csv"
TRV2_RESULTS = ROOT / "research/trend_robustness_v2/RESULTS.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
ARMS = ("C0", "E2", "EP", "T1", "D1")
CANDIDATES = ("E2", "EP", "T1", "D1")
SMA_N, TSM_N, DON_N = 200, 252, 126
VOL_N, CAP, BAND = trv2.VOL_N, trv2.CAP, trv2.BAND
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q  # 0.95%/yr
FIN = {"base": 0.0, "calibrated": 0.0075}  # leverage_frontier_v1 Amendment A1: +0.75%/yr x (L-1), L=2
SEED, PATHS, CHUNK = trv2.SEED, trv2.PATHS, trv2.CHUNK
BLOCKS = (20, 60, 250)
GATE_BLOCK = 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 sessions prepended to every bootstrap path (no extra draws)
START = TSM_N + 1  # first measured session index: decision close START-1 = 252 has 252 TR returns
TRV2_START = trv2.SMA_MAX  # 250 -> 2000-03-06 (harness window)
TOL = 1e-4  # 0.01 pp
OOS_FIRST = "1957-01-02"
OOS_LAST = "1999-12-31"
G8_CAGR_TOL, G8_DD_TOL = 0.005, 0.03


# ----------------------------------------------------------------------------- signals
def tr_level(c, d):
    """Total-return level from closes and dividends (time on last axis); index 0 = 1."""
    g = (c[..., 1:] + d[..., 1:]) / c[..., :-1]
    return np.concatenate([np.ones(c.shape[:-1] + (1,)), np.cumprod(g, axis=-1)], axis=-1)


def bill_level(rate):
    """Cumulative DTB3 level aligned with the financing convention: return k accrues rate[k-1]/252."""
    g = 1 + rate[..., :-1] / 252
    return np.concatenate([np.ones(rate.shape[:-1] + (1,)), np.cumprod(g, axis=-1)], axis=-1)


def tsm_up(tr, bills, n=TSM_N):
    """up[t]: n-session total return of the series > cumulative DTB3 return over the same n sessions."""
    tr, bills = np.atleast_2d(tr), np.atleast_2d(bills)
    up = np.zeros(tr.shape, dtype=bool)
    up[:, n:] = (tr[:, n:] / tr[:, :-n]) > (bills[:, n:] / bills[:, :-n])
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)  # [P, T-n+1, n]
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def signals(price_close, tr, bills):
    """SMA on the price-close series (as trend_robustness_v2 / VT35), TSM and DON on total return."""
    return {"SMA": trv2.sma_up(price_close, SMA_N), "TSM": tsm_up(tr, bills), "DON": don_up(tr)}


def build_wants(sig, vol, arm):
    """[P, 1, T] QLD* targets; capped at 0.98 exactly as trend_robustness_v2.build_wants."""
    vt = trv2.vt_of(vol)
    s, t, d = (sig[k].astype(float) for k in ("SMA", "TSM", "DON"))
    if arm == "C0":
        raw = s * vt
    elif arm == "E2":
        raw = ((s + t + d) >= 2).astype(float) * vt
    elif arm == "EP":
        raw = (s + t + d) / 3 * vt
    elif arm == "T1":
        raw = t * vt
    elif arm == "D1":
        raw = d * vt
    else:
        raise ValueError(arm)
    raw = raw[:, None, :]
    tot = raw.sum(axis=1, keepdims=True)
    f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return raw * f


# ----------------------------------------------------------------------------- historical
def hist_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    sig = signals(q[1], tr_level(q[1], q[2]), bill_level(y))
    return days, arr, y, o, c, trv2.vol_n(c), sig


def seg(dates, nav, a, b):
    """From the last close <= a (first bar if none) to the last close <= b: total return and max DD."""
    ia = 0 if dates[0] > a else max(k for k, d in enumerate(dates) if d <= a)
    ib = max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "max_dd": float(trv2.max_dd(s)[0])}


def round_trips(dates, nav, held, start):
    """In-market episodes: sessions i with held target > 0 (want[i-1] > 0).

    A round trip runs from the close before its first held session to the close of
    the first unheld session (the exit fill and its cost happen at that open).
    Episodes still open at the end are not counted."""
    trips = []
    T = len(held)
    i = start
    while i < T:
        if held[i]:
            j = i
            while j + 1 < T and held[j + 1]:
                j += 1
            e = j + 1
            if e < T:
                r = nav[e - start + 1] / nav[i - start] - 1
                trips.append({"entry": dates[i - start + 1], "exit": dates[e - start + 1], "return": float(r)})
            i = e + 1
        else:
            i += 1
    return trips


def whipsaw(trips, years):
    losers = [t["return"] for t in trips if t["return"] < 0]
    return {
        "round_trips": len(trips),
        "round_trips_per_year": len(trips) / years,
        "share_losing": len(losers) / len(trips) if trips else None,
        "avg_loss_per_losing_trip": float(np.mean(losers)) if losers else None,
        "avg_return_per_trip": float(np.mean([t["return"] for t in trips])) if trips else None,
        "sum_of_losing_trip_returns": float(np.sum(losers)) if losers else 0.0,
    }


def hist_run(spread, start, m_list=(1, 2), arms=ARMS, extras=False):
    days, arr, y, o, c, vol, sig = hist_inputs(spread)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    out = {}
    for m in m_list:
        hm = {}
        for arm in arms:
            w = build_wants(sig, vol, arm)
            nav, turn, ntr = trv2.simulate_multi(o, c, w, COST_PER_SIDE * m, start)
            nav = nav[0]
            st = trv2.curve_stats(dates, nav)
            st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
            st["sharpe"] = trv2.sharpe_daily(nav)
            st["turnover_annual"] = float(turn[0] / years)
            st["trades"] = int(ntr[0])
            if extras:
                tgt = w[0, 0]
                held = np.zeros(len(days), dtype=bool)
                held[1:] = tgt[:-1] > 0
                st["time_in_market"] = float(held[start:].mean())
                st["avg_target_exposure"] = float(np.r_[0.0, tgt[:-1]][start:].mean())
                st["whipsaw"] = whipsaw(round_trips(dates, nav, held, start), years)
                st["episodes"] = {
                    "2000_2002": seg(dates, nav, "1999-12-31", "2002-12-31"),
                    "2007_2009": seg(dates, nav, "2006-12-31", "2009-12-31"),
                    "2020_02_03": seg(dates, nav, "2020-01-31", "2020-03-31"),
                    "2022": seg(dates, nav, "2021-12-31", "2022-12-31"),
                }
            hm[arm] = st
        s = arr["SPY"]
        spy_tr = (s[1][start:] + s[2][start:]) / s[1][start - 1:-1]
        spy_nav = np.concatenate([[1.0], np.cumprod(spy_tr)])
        sp = trv2.curve_stats(dates, spy_nav)
        sp["calmar"] = trv2.calmar(sp["cagr"], sp["max_dd"])
        sp["sharpe"] = trv2.sharpe_daily(spy_nav)
        if extras:
            sp["episodes"] = {
                "2000_2002": seg(dates, spy_nav, "1999-12-31", "2002-12-31"),
                "2007_2009": seg(dates, spy_nav, "2006-12-31", "2009-12-31"),
                "2020_02_03": seg(dates, spy_nav, "2020-01-31", "2020-03-31"),
                "2022": seg(dates, spy_nav, "2021-12-31", "2022-12-31"),
            }
        hm["SPY"] = sp
        out[f"m{m}"] = hm
    agree = None
    if extras:
        dec = slice(start - 1, len(days) - 1)  # decision closes driving measured sessions
        sv = {k: v[0, dec] for k, v in sig.items()}
        agree = {
            "decisions": int(len(sv["SMA"])),
            "share_on": {k: float(v.mean()) for k, v in sv.items()},
            "SMA_TSM": float((sv["SMA"] == sv["TSM"]).mean()),
            "SMA_DON": float((sv["SMA"] == sv["DON"]).mean()),
            "TSM_DON": float((sv["TSM"] == sv["DON"]).mean()),
            "all_three": float(((sv["SMA"] == sv["TSM"]) & (sv["TSM"] == sv["DON"])).mean()),
            "count_on_distribution": {str(k): float(((sv["SMA"].astype(int) + sv["TSM"] + sv["DON"]) == k).mean())
                                      for k in range(4)},
            "flips_per_year": {k: float(np.sum(v[1:] != v[:-1]) / years) for k, v in sv.items()},
        }
    return {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}, "stats": out,
            "signal_agreement": agree}


# ----------------------------------------------------------------------------- bootstrap
def bootstrap(block, m, arms=ARMS, models=("base", "calibrated")):
    """Stationary block bootstrap; path draws identical to trend_robustness_v2 (seed, order, chunks).

    Each 2,770-session path is extended backward by EXT=3 sessions (the 3 pool
    sessions preceding its first session, cyclically) without extra random draws,
    so the 252-session TSM has full warm-up. The measured 2,520 sessions are the
    same as the precedent's; C0 (lookback <= 200) is unaffected by the extension."""
    _, arr, y = trv2.load()
    T = arr["QQQ"].shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    cost = COST_PER_SIDE * m
    start = trv2.SMA_MAX + 1 + EXT
    acc = {(md, a): {"cagr": [], "dd": []} for md in models for a in arms}
    spy_acc = {"cagr": [], "dd": []}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / block
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]
        yy = y[idx - 1]

        def leg(sym):
            prev = arr[sym][1][idx - 1]
            r_on = (arr[sym][0][idx] + arr[sym][2][idx]) / prev
            r_id = arr[sym][1][idx] / arr[sym][0][idx]
            cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
            oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
            return oo, cc

        qo, qc = leg("QQQ")
        _, sc = leg("SPY")
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        sig = signals(qc, qc, bill_level(yyp))  # resampled level is already total return
        for md in models:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol = trv2.vol_n(scl)
            for arm in arms:
                w = build_wants(sig, vol, arm)
                nav = trv2.simulate_multi(so[:, None, :], scl[:, None, :], w, cost, start)[0]
                k = nav.shape[1] - 1
                acc[(md, arm)]["cagr"].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                acc[(md, arm)]["dd"].append(trv2.max_dd(nav))
        spy = sc[:, start - 1:]
        spy_acc["cagr"].append((spy[:, -1] / spy[:, 0]) ** (252 / (spy.shape[1] - 1)) - 1)
        spy_acc["dd"].append(trv2.max_dd(spy))
    s_c = np.concatenate(spy_acc["cagr"])
    out = {"paths": PATHS, "seed": SEED, "mean_block": block, "warmup_sessions": WARM,
           "back_extension_sessions": EXT, "measured_sessions": MEASURED, "cost_multiplier_m": m}
    for md in models:
        out[md] = {}
        for a in arms:
            c, d = np.concatenate(acc[(md, a)]["cagr"]), np.concatenate(acc[(md, a)]["dd"])
            out[md][a] = {
                "cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                "p_cagr_below_0": float(np.mean(c < 0)), "max_dd_median": float(np.median(d)),
                "p_dd_gt_50": float(np.mean(d > 0.50)), "p_dd_gt_70": float(np.mean(d > 0.70)),
                "share_beats_spy": float(np.mean(c > s_c)),
            }
    sd = np.concatenate(spy_acc["dd"])
    out["SPY"] = {"cagr_median": float(np.median(s_c)), "cagr_p5": float(np.percentile(s_c, 5)),
                  "max_dd_median": float(np.median(sd))}
    return out


# ----------------------------------------------------------------------------- G8: ^GSPC out of sample
def load_gspc():
    """^GSPC rows from the first DTB3 date (1954-01-04) to 1999-12-31; DTB3 forward-filled.

    Every open is set to the previous close (Yahoo's old ^GSPC opens are placeholders),
    so every G8 fill is at the decision close, identically for every arm."""
    rows = []
    with GSPC.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    tb = {}
    with trv2.DTB3.open() as fh:
        for r in csv.DictReader(fh):
            tb[r["observation_date"]] = r["DTB3"]
    first_tb = min(tb)
    rows = [r for r in rows if first_tb <= r[0] <= OOS_LAST]
    days = [r[0] for r in rows]
    c = np.array([r[1] for r in rows])
    o = np.concatenate([[c[0]], c[:-1]])
    y, last = [], None
    allk = sorted(set(tb) | set(days))
    dset = set(days)
    for d in allk:
        v = tb.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in dset:
            y.append(last)
    return days, o, c, np.array(y)


def g8_run(m=2, arms=ARMS):
    days, o, c, y = load_gspc()
    start = next(i for i, d in enumerate(days) if d >= OOS_FIRST)
    assert start >= START, "warm-up incomplete at 1957-01-02"
    so, scl = trv2.synthetic_2x(o, c, np.zeros_like(c), y + FIN["calibrated"], EXP_Q)
    vol = trv2.vol_n(scl)
    sig = signals(c, c, bill_level(y))  # price only: ^GSPC has no dividends
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    out = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1,
                      "data_first": days[0]}, "cost_multiplier_m": m, "financing": "calibrated"}
    for arm in arms:
        w = build_wants(sig, vol, arm)
        nav, turn, ntr = trv2.simulate_multi(so, scl, w, COST_PER_SIDE * m, start)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        tgt = w[0, 0]
        held = np.zeros(len(days), dtype=bool)
        held[1:] = tgt[:-1] > 0
        out[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
                    "turnover_annual": float(turn[0] / years), "time_in_market": float(held[start:].mean()),
                    "whipsaw": whipsaw(round_trips(dates, nav, held, start), years)}
    bh = c[start - 1:] / c[start - 1]
    out["GSPC_price_hold"] = {"cagr": float(trv2.cagr(1.0, bh[-1], years)), "max_dd": float(trv2.max_dd(bh)[0])}
    return out


# ----------------------------------------------------------------------------- harness
def harness():
    ref = json.loads(TRV2_RESULTS.read_text())
    h = hist_run(FIN["base"], TRV2_START, m_list=(1, 2), arms=("C0",))
    b = bootstrap(GATE_BLOCK, 2, arms=("C0",), models=("base",))
    checks = {}
    for m in (1, 2):
        for k in ("cagr", "max_dd"):
            r, x = ref["historical"][f"m{m}"]["C0"][k], h["stats"][f"m{m}"]["C0"][k]
            checks[f"hist_m{m}_{k}"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    for k in ("cagr_median", "cagr_p5", "max_dd_median", "share_beats_spy"):
        r, x = ref["bootstrap"]["m2"]["C0"][k], b["base"]["C0"][k]
        checks[f"boot20_m2_{k}"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    r, x = ref["bootstrap"]["m2"]["SPY"]["cagr_median"], b["SPY"]["cagr_median"]
    checks["boot20_SPY_cagr_median"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    ok = all(abs(v["diff_pp"]) <= TOL * 100 + 1e-9 for v in checks.values())
    return {"window": h["window"], "reference_window": ref["common_window"], "checks": checks,
            "tolerance_pp": TOL * 100, "pass": bool(ok)}


# ----------------------------------------------------------------------------- gates
def gates_for(h1, h2, b20):
    g = {}
    for arm in CANDIDATES:
        a2, c2, a1, c1 = h2[arm], h2["C0"], h1[arm], h1["C0"]
        r = {}
        r["G1"] = bool(a2["cagr"] >= c2["cagr"] - 0.005)
        r["G2"] = bool(a2["max_dd"] <= c2["max_dd"])
        r["G3"] = bool(a2["cagr"] > c2["cagr"] or a2["calmar"] >= 1.10 * c2["calmar"])
        r["G4"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.02)
        r["G5"] = bool(b20[arm]["cagr_median"] >= b20["C0"]["cagr_median"]
                       and b20[arm]["share_beats_spy"] >= b20["C0"]["share_beats_spy"])
        g1 = bool(a1["cagr"] >= c1["cagr"] - 0.005)
        g2 = bool(a1["max_dd"] <= c1["max_dd"])
        g3 = bool(a1["cagr"] > c1["cagr"] or a1["calmar"] >= 1.10 * c1["calmar"])
        r["G6"] = bool(g1 and g2 and g3)
        r["G6_detail"] = {"G1": g1, "G2": g2, "G3": g3}
        r["G7"] = bool(a2["turnover_annual"] <= 2 * c2["turnover_annual"])
        g[arm] = r
    return g


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    for k, v in hc["checks"].items():
        print(f"harness {k}: ref {v['reference']:.6f} repro {v['reproduced']:.6f} diff {v['diff_pp']:+.4f} pp")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    out = {"params": {
        "arms": list(ARMS), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "vol_window": VOL_N,
        "vol_target": trv2.VOL_TARGET, "band": BAND, "cap": CAP, "cost_per_side_m1": COST_PER_SIDE,
        "expense_qld_star": EXP_Q, "financing_spread": FIN, "bootstrap": {
            "paths": PATHS, "seed": SEED, "blocks": list(BLOCKS), "gate_block": GATE_BLOCK,
            "warmup": WARM, "back_extension": EXT, "measured": MEASURED},
        "signal_series": "SMA on QQQ price close (historical; as VT35/trend_robustness_v2); TSM and DON on QQQ total return; bootstrap: all on resampled TR level",
        "g8": "^GSPC price, opens := previous close, synthetic 2x 0.95% + DTB3 + 0.75%, m=2",
    }, "harness_check": hc}

    hist = {md: hist_run(FIN[md], START, extras=True) for md in FIN}
    out["common_window"] = hist["base"]["window"]
    out["historical"] = {md: hist[md]["stats"] for md in FIN}
    out["signal_agreement"] = hist["base"]["signal_agreement"]

    boot = {}
    for blk in BLOCKS:
        boot[f"block{blk}"] = bootstrap(blk, 2)
        print(f"bootstrap block {blk} done")
    out["bootstrap_m2"] = boot

    g8 = g8_run()
    out["oos_gspc"] = g8

    gates = {}
    for md in FIN:
        gates[md] = gates_for(out["historical"][md]["m1"], out["historical"][md]["m2"], boot["block20"][md])
    g8g = {}
    for arm in CANDIDATES:
        g8g[arm] = bool(g8[arm]["cagr"] >= g8["C0"]["cagr"] - G8_CAGR_TOL
                        and g8[arm]["max_dd"] <= g8["C0"]["max_dd"] + G8_DD_TOL)
    gates["G8"] = g8g
    pass_all = {}
    for arm in CANDIDATES:
        pass_all[arm] = bool(all(gates[md][arm][k] for md in FIN for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
                             and g8g[arm])
    gates["pass_all"] = pass_all
    out["gates"] = gates
    passing = [a for a in CANDIDATES if pass_all[a]]
    h2 = out["historical"]["base"]["m2"]
    sel = max(passing, key=lambda a: (h2[a]["cagr"], h2[a]["calmar"])) if passing else "none"
    out["selection"] = {"passing": passing, "selected": sel,
                        "rule": "highest full-window m=2 base CAGR among passing arms (tie: higher Calmar); not promoted"}
    out["status"] = "complete"
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    L = [f"signal_ensemble_v1 | window {out['common_window']['start']} -> {out['common_window']['end']} "
         f"({out['common_window']['sessions']} sessions)",
         f"harness (C0 vs trend_robustness_v2 C0, hist m1/m2 + boot20): {'PASS' if hc['pass'] else 'FAIL'} "
         f"max |diff| {max(abs(v['diff_pp']) for v in hc['checks'].values()):.6f} pp", ""]
    for md in FIN:
        b20, b60, b250 = (boot[f"block{k}"][md] for k in BLOCKS)
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ turn/yr | b20 med p5 P(DD>70) >SPY | b60 med | b250 med")
        for arm in ARMS:
            a = out["historical"][md]["m2"][arm]
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.2f} {p(a['2017_end']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} | {p(b20[arm]['cagr_median'])} {p(b20[arm]['cagr_p5'])} "
                     f"{p(b20[arm]['p_dd_gt_70'])} {p(b20[arm]['share_beats_spy'])} | {p(b60[arm]['cagr_median'])} "
                     f"| {p(b250[arm]['cagr_median'])}")
        L.append("  m=1 CAGR / maxDD: " + "  ".join(
            f"{a} {p(out['historical'][md]['m1'][a]['cagr'])}/{p(out['historical'][md]['m1'][a]['max_dd'])}" for a in ARMS))
        L.append("")
    sp = out["historical"]["base"]["m2"]["SPY"]
    L.append(f"SPY TR hold: CAGR {p(sp['cagr'])} maxDD {p(sp['max_dd'])}; boot20 median "
             f"{p(boot['block20']['SPY']['cagr_median'])}")
    L.append("")
    L.append(f"G8 ^GSPC {g8['window']['start']} -> {g8['window']['end']} (calibrated, m=2, fills at previous close):")
    for arm in ARMS:
        a = g8[arm]
        L.append(f"  {arm:3s} CAGR {p(a['cagr'])} maxDD {p(a['max_dd'])} turn {a['turnover_annual']:5.2f} "
                 f"in-mkt {p(a['time_in_market'])} trips/yr {a['whipsaw']['round_trips_per_year']:.2f}")
    gh = g8["GSPC_price_hold"]
    L.append(f"  ^GSPC price hold CAGR {p(gh['cagr'])} maxDD {p(gh['max_dd'])}")
    L.append("")
    L.append("gates (G1-G7 per financing model; G8 OOS):")
    for arm in CANDIDATES:
        parts = []
        for md in FIN:
            parts.append(md[:4] + " " + " ".join(f"{k}={'P' if gates[md][arm][k] else 'F'}"
                                                 for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7")))
        L.append(f"  {arm}: " + " | ".join(parts) + f" | G8={'P' if g8g[arm] else 'F'} -> "
                 f"{'PASS' if pass_all[arm] else 'FAIL'}")
    L.append(f"selected: {sel}" + (" (VT35 unchanged)" if sel == "none" else " (not promoted by this campaign)"))
    L.append("")
    L.append("whipsaw (base, m=2): trips/yr, share losing, avg loss per losing trip, time in market")
    for arm in ARMS:
        a = out["historical"]["base"]["m2"][arm]
        w = a["whipsaw"]
        L.append(f"  {arm:3s} {w['round_trips_per_year']:5.2f} {p(w['share_losing'] or 0)} "
                 f"{p(w['avg_loss_per_losing_trip'] or 0)} {p(a['time_in_market'])}")
    L.append("episodes (base, m=2): total return / max DD")
    for arm in ARMS + ("SPY",):
        e = out["historical"]["base"]["m2"][arm]["episodes"]
        L.append(f"  {arm:3s} " + "  ".join(f"{k} {p(v['total_return'])}/{p(v['max_dd'])}" for k, v in e.items()))
    sa = out["signal_agreement"]
    L.append(f"signal agreement: SMA-TSM {p(sa['SMA_TSM'])} SMA-DON {p(sa['SMA_DON'])} TSM-DON {p(sa['TSM_DON'])} "
             f"all {p(sa['all_three'])}; on-share " + " ".join(f"{k} {p(v)}" for k, v in sa["share_on"].items()))
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
