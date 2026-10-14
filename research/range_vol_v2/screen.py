#!/usr/bin/env python3
"""range_vol_v2 screen. See PREREGISTRATION.md."""

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

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = ROOT / "research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# constants
SMA_N, TSM_N, DON_N = 200, 252, 126
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1
TOL = 1e-4
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
DTB3_FIRST = "1954-01-04"
OOS_ETFS = ["XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY",
            "EWJ","EWG","EWU","EWC","EWA","EWH","EWS","EWL","EFA","EEM"]
OOS_PATHS_PER_ET = 2000
OOS_BOOT_L = TSM_N + MEASURED
C0_BAND = trv2.BAND

PLATEAU_WINDOWS = (10, 15, 30, 40)
ARMS_MAIN = ["C0", "C0q", "G1x", "G3x"] + [f"G1x_w{w}" for w in PLATEAU_WINDOWS] + [f"G3x_w{w}" for w in PLATEAU_WINDOWS]
CANDIDATES = ["G1x", "G3x"]
BOOT_ARMS = ["C0", "C0q", "G1x", "G3x"]

LN2 = np.log(2.0)


def arm_spec(arm):
    if arm == "C0":
        return "synth", 20, C0_BAND
    if arm == "C0q":
        return "cc", 20, C0_BAND
    if arm == "G1x":
        return "yz", 20, C0_BAND
    if arm == "G3x":
        return "park_scaled", 20, C0_BAND
    if arm.startswith("G1x_w"):
        return "yz", int(arm.split("w")[1]), C0_BAND
    if arm.startswith("G3x_w"):
        return "park_scaled", int(arm.split("w")[1]), C0_BAND
    raise ValueError(arm)


def vol_key(kind, n):
    return f"{kind}_{n}"


# signals
def tsm_up_price(c, n=TSM_N):
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def e2_up(price):
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


# range estimators
def _rmean(a, n):
    a = np.atleast_1d(a).astype(float)
    orig = a.shape
    a2 = a.reshape(-1, a.shape[-1])
    T = a2.shape[-1]
    z = np.zeros((a2.shape[0], 1))
    cum = np.concatenate([z, np.cumsum(a2, axis=1)], axis=1)
    out = np.full((a2.shape[0], T), np.nan)
    out[:, n - 1:] = (cum[:, n:] - cum[:, :-n]) / n
    return out.reshape(orig)


def _rvar(x, n):
    x = np.atleast_1d(x).astype(float)
    orig = x.shape
    x2 = x.reshape(-1, x.shape[-1])
    T = x2.shape[-1]
    z = np.zeros((x2.shape[0], 1))
    s = np.concatenate([z, np.cumsum(x2, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(x2 * x2, axis=1)], axis=1)
    out = np.full((x2.shape[0], T), np.nan)
    sx = s[:, n:] - s[:, :-n]
    sx2 = s2[:, n:] - s2[:, :-n]
    var = (sx2 - sx * sx / n) / (n - 1)
    var = np.where(var < 0, 0.0, var)
    out[:, n - 1:] = var
    return out.reshape(orig)


def range_vol_parkinson(high, low, n=20):
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    daily = (np.log(high / low) ** 2) / (4.0 * LN2)
    return np.sqrt(252.0 * _rmean(daily, n))


def range_vol_gk(high, low, opn, close, n=20):
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    hl = np.log(high / low)
    co = np.log(close / opn)
    daily = 0.5 * hl * hl - (2.0 * LN2 - 1.0) * co * co
    daily = np.where(daily < 0, 0.0, daily)
    return np.sqrt(252.0 * _rmean(daily, n))


def range_vol_yz(high, low, opn, close, n=20):
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    P, T = close.shape
    on = np.full((P, T), np.nan)
    on[:, 1:] = np.log(opn[:, 1:] / close[:, :-1])
    oc = np.log(close / opn)
    rs = np.log(high / close) * np.log(high / opn) + np.log(low / close) * np.log(low / opn)
    # on has a leading NaN at session 0; compute both variances on the aligned trailing slice
    # (sessions 1..T-1) and keep them aligned to trailing windows ending at sessions n..T-1.
    on_v = on[:, 1:]
    oc_v = oc[:, 1:]
    rs_v = rs[:, 1:]
    var_on = _rvar(on_v, n)[:, n - 1:]
    var_oc = _rvar(oc_v, n)[:, n - 1:]
    mean_rs = _rmean(rs_v, n)[:, n - 1:]
    k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0))
    var = var_on + k * var_oc + (1.0 - k) * mean_rs
    var = np.where(var < 0, 0.0, var)
    out = np.full((P, T), np.nan)
    out[:, n:] = np.sqrt(252.0 * var)
    return out


def range_vol_gk_gap(high, low, opn, close, n=20):
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    gk = range_vol_gk(high, low, opn, close, n)
    P, T = close.shape
    on = np.full((P, T), np.nan)
    on[:, 1:] = np.log(opn[:, 1:] / close[:, :-1])
    var_on = _rvar(on, n)
    gk_var = (gk ** 2) / 252.0
    total_var = gk_var + var_on
    total_var = np.where(total_var < 0, 0.0, total_var)
    return np.sqrt(252.0 * total_var)


def range_vol_park_scaled(high, low, close, n=20):
    high = np.atleast_2d(high); low = np.atleast_2d(low); close = np.atleast_2d(close)
    P, T = close.shape
    pv20 = range_vol_parkinson(high, low, n)
    park_daily = (np.log(high / low) ** 2) / (4.0 * LN2)
    r = np.full((P, T), np.nan)
    r[:, 1:] = close[:, 1:] / close[:, :-1] - 1.0
    rr = np.where(np.isnan(r), 0.0, r)
    rr2 = rr * rr
    park0 = np.where(np.isnan(park_daily), 0.0, park_daily)
    C_r = np.concatenate([np.zeros((P, 1)), np.cumsum(rr, axis=1)], axis=1)
    C_r2 = np.concatenate([np.zeros((P, 1)), np.cumsum(rr2, axis=1)], axis=1)
    C_p = np.concatenate([np.zeros((P, 1)), np.cumsum(park0, axis=1)], axis=1)
    ratio_prev = np.ones((P, T))
    for t in range(2, T):
        cnt = t
        sr = C_r[:, t]
        sr2 = C_r2[:, t]
        sp = C_p[:, t]
        var_cc = (sr2 - sr * sr / cnt) / (cnt - 1)
        var_cc = np.where(var_cc < 0, 0.0, var_cc)
        cc_vol = np.sqrt(252.0 * var_cc)
        park_mean = sp / cnt
        park_vol = np.sqrt(252.0 * park_mean)
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = np.where(park_vol > 0, cc_vol / park_vol, 1.0)
        ratio_prev[:, t] = ratio
    out = pv20 * ratio_prev
    return out


def wants_for(e2, vol):
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


# engine
def simulate_band(opens, closes, wants, cost, start, band):
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    codes = np.zeros((P, T - start), dtype=np.int8)
    traded = np.zeros((P, T - start))
    for i in range(start, T):
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i - 1]
        d = np.abs(want - w)
        held, tgt = w > 0, want > 0
        flip = held != tgt
        trade = flip | (d > band)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(trade, d, 0.0)
        ntr = ntr + trade.astype(np.int64)
        k = i - start
        codes[:, k] = np.where(~trade, 0, np.where(flip & tgt, 1, np.where(flip, 2, np.where(want > w, 3, 4))))
        traded[:, k] = np.where(trade, d, 0.0)
        w = np.where(trade, want, w)
        ga = closes[:, i] / opens[:, i]
        va = w * ga
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        out[:, k + 1] = nav
    return out, turn, ntr, codes, traded


def seg(dates, nav, a, b):
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def event_stats(dates, codes, traded, a=None, b=None):
    sess = dates[1:]
    lo = 0 if a is None else next(k for k, d in enumerate(sess) if d > a)
    hi = len(sess) if b is None else max(k for k, d in enumerate(sess) if d <= b) + 1
    c, tr = codes[lo:hi], traded[lo:hi]
    yrs = trv2.calendar_years(dates[lo], sess[hi - 1])
    n = {name: int(np.sum(c == v)) for name, v in (("entries", 1), ("exits", 2), ("resize_up", 3), ("resize_down", 4))}
    return {
        "years": yrs,
        "counts": n,
        "round_trips_per_year": n["exits"] / yrs,
        "trades_per_year": (n["entries"] + n["exits"] + n["resize_up"] + n["resize_down"]) / yrs,
        "resizes_per_year": (n["resize_up"] + n["resize_down"]) / yrs,
        "sells_per_year": (n["exits"] + n["resize_down"]) / yrs,
        "turnover_per_year": float(tr.sum() / yrs),
        "turnover_from_entries_exits_per_year": float(tr[(c == 1) | (c == 2)].sum() / yrs),
        "turnover_from_resizes_per_year": float(tr[(c == 3) | (c == 4)].sum() / yrs),
    }


def arm_stats(dates, nav, turn, ntr, codes, traded, wants, start, extras):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    st["subperiods"] = {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    if extras:
        st["stage_b_window"] = seg(dates, nav, *STAGE_B_WINDOW)
        st["events_full"] = event_stats(dates, codes, traded)
        st["events_2017_on"] = event_stats(dates, codes, traded, "2016-12-31")
        tgt = wants[start - 1:-1]
        st["time_in_market"] = float((tgt > 0).mean())
        st["sessions_target_below_10pp_in_trend"] = int(np.sum((tgt > 0) & (tgt < 0.10)))
    return st


# main data
def load_qqq_adjusted(days):
    raw = {}
    with (ROOT / "data/historical/stress_robustness_v1_daily.csv").open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] != "QQQ":
                continue
            raw[r["date"]] = r
    o = np.full(len(days), np.nan)
    h = np.full(len(days), np.nan)
    l = np.full(len(days), np.nan)
    c = np.full(len(days), np.nan)
    for i, d in enumerate(days):
        r = raw.get(d)
        if r is None:
            continue
        close = float(r["close"]); adj = float(r["adjclose"]); f = adj / close
        o[i] = float(r["open"]) * f
        h[i] = float(r["high"]) * f
        l[i] = float(r["low"]) * f
        c[i] = adj
    return o, h, l, c


def main_vol_arrays(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    e2 = e2_up(q[1])[0]
    qa_o, qa_h, qa_l, qa_c = load_qqq_adjusted(days)
    cc_vol = trv2.vol_n(qa_c[np.newaxis, :])[0] * 2.0
    vmap = {}
    vmap["synth_20"] = trv2.vol_n(c)[0]
    vmap["cc_20"] = cc_vol
    for n in set([20] + list(PLATEAU_WINDOWS)):
        vmap[f"yz_{n}"] = range_vol_yz(qa_h[np.newaxis, :], qa_l[np.newaxis, :], qa_o[np.newaxis, :], qa_c[np.newaxis, :], n)[0] * 2.0
        vmap[f"park_scaled_{n}"] = range_vol_park_scaled(qa_h[np.newaxis, :], qa_l[np.newaxis, :], qa_c[np.newaxis, :], n)[0] * 2.0
    wants = {k: wants_for(e2, v)[0] for k, v in vmap.items()}
    return days, arr, o, c, e2, vmap, wants


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, vmap, wants = main_vol_arrays(spread)
        dates = days[START - 1:]
        wants_arm = {arm: wants[vol_key(*arm_spec(arm)[:2])] for arm in arms}
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                kind, n, band = arm_spec(arm)
                key = vol_key(kind, n)
                nav, turn, ntr, codes, traded = simulate_band(o, c, wants_arm[arm], COST_PER_SIDE * m, START, band)
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants_arm[arm], START,
                                    extras and m == 2)
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def harness():
    ref = json.loads(DEVIATION.read_text())
    h = run_main(("C0",))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = h[md][f"m{m}"]["C0"]
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff_pp": (x[k] - r[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())
    days, arr, o, c, e2, vmap, wants = main_vol_arrays(0.0)
    w = wants["synth_20"]
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[None, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))
    ok = ok_dev and sim_equal
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "pass": bool(ok)}


# bootstrap main
def boot_main(arms):
    _, arr, y = trv2.load()
    q = arr["QQQ"]
    raw_h = {}; raw_l = {}
    with (ROOT / "data/historical/stress_robustness_v1_daily.csv").open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] != "QQQ":
                continue
            raw_h[r["date"]] = float(r["high"]); raw_l[r["date"]] = float(r["low"])
    days_all = list(trv2.load()[0])
    high_arr = np.array([raw_h.get(d, np.nan) for d in days_all], dtype=float)
    low_arr = np.array([raw_l.get(d, np.nan) for d in days_all], dtype=float)
    T = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start_bs = trv2.SMA_MAX + 1 + EXT
    cg = {(md, a): [] for md in FIN for a in arms}
    tv = {(md, a): [] for md in FIN for a in arms}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]
        yy = y[idx - 1]
        prev = q[1][idx - 1]
        src_high = high_arr[idx]
        src_low = low_arr[idx]
        src_close = q[1][idx]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)
        ratio_h = src_high / src_close
        ratio_l = src_low / src_close
        qh = np.concatenate([np.ones((P, 1)), qc[:, 1:] * ratio_h], axis=1)
        ql = np.concatenate([np.ones((P, 1)), qc[:, 1:] * ratio_l], axis=1)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            yz_vol = range_vol_yz(qh, ql, qo, qc, 20) * 2.0
            park_scaled_vol = range_vol_park_scaled(qh, ql, qc, 20) * 2.0
            cc_vol = trv2.vol_n(qc) * 2.0
            wants = {
                "synth_20": wants_for(e2, trv2.vol_n(scl)),
                "cc_20": wants_for(e2, cc_vol),
                "yz_20": wants_for(e2, yz_vol),
                "park_scaled_20": wants_for(e2, park_scaled_vol),
            }
            for arm in arms:
                kind, n, band = arm_spec(arm)
                key = vol_key(kind, n)
                nav, turn, _, _, _ = simulate_band(so, scl, wants[key], COST_PER_SIDE * 2, start_bs, band)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


def boot_summary(cg, tv, arms, models, control="C0"):
    out = {}
    for md in models:
        out[md] = {}
        c0 = cg[(md, control)]
        for a in arms:
            c = cg[(md, a)]
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "turnover_median": float(np.median(tv[(md, a)])),
                          "diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                          "diff_vs_C0_p25": float(np.percentile(d, 25)), "diff_vs_C0_p75": float(np.percentile(d, 75)),
                          "share_ge_C0": float(np.mean(d >= 0))}
    return out


# OOS loaders


# OOS loader for ETF
def load_etf(key):
    path = ROOT / "data/historical" / f"range_vol_v2_{key.lower()}.csv"
    rows = []
    with path.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r["adjclose"])))
    dates = [r[0] for r in rows]
    o = np.empty(len(rows)); h = np.empty(len(rows)); l = np.empty(len(rows)); c = np.empty(len(rows))
    for k, (d, op, hi, lo, cl, adj) in enumerate(rows):
        f = adj / cl
        o[k] = op * f; h[k] = hi * f; l[k] = lo * f; c[k] = adj
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    start = START
    assert start <= len(dates) - 1
    return days_all if False else dates, o, h, l, c, np.array(y), start


def load_rates():
    d3, tb = [], []
    with DTB3.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                d3.append((r["observation_date"], float(r["DTB3"]) / 100))
    with TB3MS.open() as fh:
        for r in csv.DictReader(fh):
            if r["TB3MS"] not in (".", ""):
                tb.append((r["observation_date"], float(r["TB3MS"]) / 100))
    return d3, tb


def rate_on(dates, d3, tb):
    out = []
    i3 = it = 0
    last3 = lastt = None
    for d in dates:
        while i3 < len(d3) and d3[i3][0] <= d:
            last3 = d3[i3][1]
            i3 += 1
        while it < len(tb) and tb[it][0] <= d:
            lastt = tb[it][1]
            it += 1
        if d >= DTB3_FIRST:
            out.append(last3)
        else:
            if lastt is None:
                raise RuntimeError(f"no T-bill rate on {d}")
            out.append(lastt)
    return np.array(out, dtype=float)


def _market_quality_ok(high, low, opn, close):
    bad = 0; n = 0
    for h, lo, o, cl in zip(high, low, opn, close):
        n += 1
        if h is None or lo is None:
            bad += 1
            continue
        if h == lo:
            bad += 1
            continue
        if o is None or o <= 0 or o < lo * (1 - 1e-9) or o > h * (1 + 1e-9):
            bad += 1
    return (bad / max(1, n)) <= 0.05


def oos_window(days, o, high, low, c, y, start, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    vmap = {}
    vmap["synth_20"] = trv2.vol_n(sc)[0]
    vmap["cc_20"] = trv2.vol_n(c[np.newaxis, :])[0] * 2.0
    vmap["yz_20"] = range_vol_yz(high[np.newaxis, :], low[np.newaxis, :], o[np.newaxis, :], c[np.newaxis, :], 20)[0] * 2.0
    vmap["park_scaled_20"] = range_vol_park_scaled(high[np.newaxis, :], low[np.newaxis, :], c[np.newaxis, :], 20)[0] * 2.0
    wants = {}
    for arm in arms:
        kind, n, _ = arm_spec(arm)
        wants[arm] = wants_for(e2, vmap[vol_key(kind, n)])[0]
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        kind, n, band = arm_spec(arm)
        key = vol_key(kind, n)
        nav, turn, ntr, codes, traded = simulate_band(so, sc, wants[arm], COST_PER_SIDE * 2, start, band)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_etf(key, rng, arms):
    days, o, high, low, c, y, start = load_etf(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    acc = {a: [] for a in arms}
    for chunk in range(0, OOS_PATHS_PER_ET, CHUNK):
        P = min(CHUNK, OOS_PATHS_PER_ET - chunk)
        pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, OOS_BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
        for t in range(1, OOS_BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        src_h = high[idx]; src_c = c[idx]; src_l = low[idx]
        ratio_h = src_h / src_c; ratio_l = src_l / src_c
        qh = np.concatenate([np.ones((P, 1)), cc[:, 1:] * ratio_h], axis=1)
        ql = np.concatenate([np.ones((P, 1)), cc[:, 1:] * ratio_l], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        yz_vol = range_vol_yz(qh, ql, oo, cc, 20) * 2.0
        park_vol = range_vol_park_scaled(qh, ql, cc, 20) * 2.0
        cc_vol = trv2.vol_n(cc) * 2.0
        wants = {
            "synth_20": wants_for(e2, trv2.vol_n(sc)),
            "cc_20": wants_for(e2, cc_vol),
            "yz_20": wants_for(e2, yz_vol),
            "park_scaled_20": wants_for(e2, park_vol),
        }
        for arm in arms:
            kind, n, band = arm_spec(arm)
            key = vol_key(kind, n)
            nav = simulate_band(so, sc, wants[key], COST_PER_SIDE * 2, START, band)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# gate helpers
def historical_gates(main, boot_vs_control, arm, control):
    r = {}
    for md in FIN:
        a2 = main[md]["m2"][arm]; c2 = main[md]["m2"][control]
        r[f"G1_{md}"] = bool(a2["cagr"] >= c2["cagr"] or (a2["calmar"] >= 1.10 * c2["calmar"] and a2["cagr"] >= c2["cagr"] - 0.005))
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot_vs_control[md][arm]["diff_vs_C0_median"] >= 0)
        r[f"G6_{md}"] = bool(a2 if False else main[md]["m1"][arm]["cagr"] >= main[md]["m1"][control]["cagr"] or
                          (main[md]["m1"][arm]["calmar"] >= 1.10 * main[md]["m1"][control]["calmar"] and
                           main[md]["m1"][arm]["cagr"] >= main[md]["m1"][control]["cagr"] - 0.005))
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"][control]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    kind, n, band = arm_spec(arm)
    lim = c2["turnover_annual"] * (1.0 if band > C0_BAND else 2.0)
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    return r


def oos_new_stats(oos_new, arm, control):
    keys = sorted(oos_new)
    deltas = [oos_new[k][arm]["cagr"] - oos_new[k][control]["cagr"] for k in keys]
    dds = [oos_new[k][arm]["max_dd"] - oos_new[k][control]["max_dd"] for k in keys]
    med = float(np.median(deltas)) if deltas else 0.0
    share_pos = float(np.mean(np.array(deltas) > 0)) if deltas else 0.0
    share_dd = float(np.mean(np.array(dds) <= 0.03)) if dds else 0.0
    g = bool(med >= 0 and share_pos >= 0.6 and share_dd >= 0.8)
    return {"median_delta_cagr": med, "share_delta_gt_0": share_pos, "share_dd_within_3pp": share_dd,
            "max_dd_diff_max": float(max(dds)) if dds else 0.0, "n_instruments": len(keys),
            "pass": g, "deltas": {k: deltas[i] for i, k in enumerate(keys)},
            "dd_diffs": {k: dds[i] for i, k in enumerate(keys)}}


def oos_new_boot_stats(pooled, arm, control):
    d = pooled[arm] - pooled[control]
    med = float(np.median(d)) if len(d) else 0.0
    return {"median_delta_cagr": med, "share_ge_0": float(np.mean(d >= 0)) if len(d) else 0.0,
            "paths": int(len(d)), "pass": bool(med >= 0)}


# main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    print("running main")
    main_res = run_main(ARMS_MAIN, extras=True)
    print("main window done")
    cg, tv = boot_main(BOOT_ARMS)
    boot_c0 = boot_summary(cg, tv, BOOT_ARMS, FIN, control="C0")
    boot_c0q = boot_summary(cg, tv, BOOT_ARMS, FIN, control="C0q")
    print("main bootstrap done")

    # OOS etfs quality
    passing_etfs = []
    for kt in OOS_ETFS:
        days, o, h, l, c, y, start = load_etf(kt)
        if _market_quality_ok(h, l, o, c):
            passing_etfs.append(kt)
    print("OOS quality-passing ETFs:", passing_etfs, "failed:", [k for k in OOS_ETFS if k not in passing_etfs])

    oos_new = {}
    for k in passing_etfs:
        oos_new[k] = oos_window(*load_etf(k), CANDIDATES + ["C0", "C0q"])
    print("OOS windows done")

    rng = np.random.default_rng(SEED)
    per_etf = {k: boot_oos_etf(k, rng, CANDIDATES + ["C0", "C0q"]) for k in passing_etfs}
    pooled_etf = {a: np.concatenate([per_etf[k][a] for k in passing_etfs]) for a in (CANDIDATES + ["C0", "C0q"])}

    # gates
    gates = {}
    plateau_map = {"G1x": "G1x", "G3x": "G3x"}
    for arm in CANDIDATES:
        g = {}
        g["vs_C0"] = historical_gates(main_res, boot_c0, arm, "C0")
        g["vs_C0q"] = historical_gates(main_res, boot_c0q, arm, "C0q")
        hist_pass = all(g["vs_C0"][k] for k in g["vs_C0"] if isinstance(g["vs_C0"][k], bool)) and \
                    all(g["vs_C0q"][k] for k in g["vs_C0q"] if isinstance(g["vs_C0q"][k], bool))
        # plateau gate
        if arm == "G1x":
            neigh = [f"G1x_w15", f"G1x_w30"]
        else:
            neigh = [f"G3x_w15", f"G3x_w30"]
        c0_base = main_res["base"]["m2"]["C0"]["cagr"]
        plateau = all(main_res["base"]["m2"][a]["cagr"] > c0_base for a in neigh)
        g["plateau"] = bool(plateau)
        g["G7p_vs_C0"] = oos_new_stats(oos_new, arm, "C0")
        g["G7p_vs_C0q"] = oos_new_stats(oos_new, arm, "C0q")
        g["G8p_vs_C0"] = oos_new_boot_stats(pooled_etf, arm, "C0")
        g["G8p_vs_C0q"] = oos_new_boot_stats(pooled_etf, arm, "C0q")
        g7p_pass = g["G7p_vs_C0"]["pass"] and g["G7p_vs_C0q"]["pass"]
        g8p_pass = g["G8p_vs_C0"]["pass"] and g["G8p_vs_C0q"]["pass"]
        g["pass_all"] = bool(hist_pass and plateau and g7p_pass and g8p_pass)
        gates[arm] = g

    # selection among passing
    passing = [a for a in CANDIDATES if gates[a]["pass_all"]]
    if passing:
        medians = {a: gates[a]["G8p_vs_C0"]["median_delta_cagr"] for a in passing}
        best = max(passing, key=lambda a: medians[a])
        # tie within 0.02 pp prefer G1x
        for a in passing:
            if a != "G1x" and "G1x" in passing and abs(medians[a] - medians["G1x"]) <= 0.0002:
                best = "G1x"
        sel = best
    else:
        sel = "none"
    selection = {"selected": sel, "passing": passing, "medians_g8p_vs_C0": {a: gates[a]["G8p_vs_C0"]["median_delta_cagr"] for a in CANDIDATES}}

    out = {
        "campaign": "range_vol_v2",
        "params": {"arms_main": ARMS_MAIN, "candidates": CANDIDATES, "plateau_windows": list(PLATEAU_WINDOWS),
                   "oos_etfs": OOS_ETFS, "quality_passing_etfs": passing_etfs,
                   "excluded_etfs": [k for k in OOS_ETFS if k not in passing_etfs]},
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": {"vs_C0": boot_c0, "vs_C0q": boot_c0q},
        "oos_new": oos_new,
        "oos_new_bootstrap_block20_m2": {"per_market": {k: {a: {"median": float(np.median(per_etf[k][a]))} for a in (CANDIDATES + ["C0", "C0q"])} for k in passing_etfs},
                                        "pooled": {a: {"median": float(np.median(pooled_etf[a]))} for a in (CANDIDATES + ["C0", "C0q"])}},
        "plateau_main_cagr_base_m2": {arm: main_res["base"]["m2"][arm]["cagr"] for arm in ARMS_MAIN},
        "gates": gates,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"range_vol_v2 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions)"]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD 2017+ turn/yr | boot vs C0 medD | vs C0q medD")
        for arm in ARMS_MAIN:
            a = main_res[md]["m2"][arm]
            if arm in boot_c0[md]:
                b0 = f"{boot_c0[md][arm]['diff_vs_C0_median']*100:+.3f}pp"
                bq = f"{boot_c0q[md][arm]['diff_vs_C0_median']*100:+.3f}pp"
            else:
                b0 = "   --  "; bq = "   --  "
            L.append(f"  {arm:11s} {p(a['cagr'])} {p(a['max_dd'])} {p(a['2017_end']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{b0} | {bq}")
    L.append("")
    L.append("quality-passing ETFs: " + ", ".join(passing_etfs))
    for arm in CANDIDATES:
        s = g["G7p_vs_C0"] if False else gates[arm]["G7p_vs_C0"]
        L.append(f"{arm}: G7' vs C0 medD {s['median_delta_cagr']*100:+.3f}pp share>0 {s['share_delta_gt_0']:.2f} shareDD {s['share_dd_within_3pp']:.2f} pass={s['pass']}")
        L.append(f"   G8' vs C0 medD {gates[arm]['G8p_vs_C0']['median_delta_cagr']*100:+.3f}pp pass={gates[arm]['G8p_vs_C0']['pass']}")
    L.append(f"selected: {sel}; passing: {passing}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
