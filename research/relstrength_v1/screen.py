#!/usr/bin/env python3
"""relstrength_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# reuse trend_robustness_v2 engine helpers
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
DTB3F = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
E2DEV = ROOT / "research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json"

OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# pre-registered constants (identical to ewma_band_v1)
SMA_N, TSM_N, DON_N = 200, 252, 126
VOL_N, VOL_TARGET, BAND, CAP = 20, 0.35, 0.005, 0.98
COST_PER_SIDE = 4.0 / 10_000
EXP_Q, EXP_S = 0.0095, 0.0089
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1
TOL = 1e-4
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1975-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
DTB3_FIRST = "1954-01-04"
HYST = 0.02
GAP_SPLIT = 0.05
ARMS = ("C0", "Q1", "Q2", "Q3", "Q4")
CANDIDATES = ("Q1", "Q2", "Q3", "Q4")
OOS_PAIRS = (("IXIC_GSPC", "^IXIC", "^GSPC", "1975-01-02", "1999-12-31"),
              ("GDAXI_FTSE", "^GDAXI", "^FTSE", "1989-01-02", "2026-09-29"))


def seg(dates, nav, a, b):
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def e2_up(price):
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = np.zeros(np.atleast_2d(price).shape, dtype=int)
    t[:, TSM_N:] = (np.atleast_2d(price)[:, TSM_N:] / np.atleast_2d(price)[:, :-TSM_N]) > 1.0
    d = np.zeros(np.atleast_2d(price).shape, dtype=int)
    from numpy.lib.stride_tricks import sliding_window_view
    c = np.atleast_2d(price)
    win = sliding_window_view(c, DON_N, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    d[:, DON_N - 1:] = c[:, DON_N - 1:] > mid
    return (s + t + d) >= 2


def wants_for_single(e2, vol):
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


def load_main_raw():
    raw = defaultdict(dict)
    import csv
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            raw[r["symbol"]][r["date"]] = tuple(float(r[k]) for k in ("open", "close", "dividend"))
    days = sorted(set(raw["QQQ"]) & set(raw["SPY"]))
    # dtb3
    tb, last = {}, None
    with DTB3F.open() as fh:
        rows = {r["observation_date"]: r["DTB3"] for r in csv.DictReader(fh)}
    for d in sorted(set(rows) | set(days)):
        v = rows.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        tb[d] = last
    arr = {}
    for s in ("QQQ", "SPY"):
        a = np.full((3, len(days)), np.nan)
        for i, d in enumerate(days):
            if d in raw[s]:
                a[:, i] = raw[s][d]
        arr[s] = a
    y = np.array([tb[d] for d in days])
    return days, arr, y


def build_main(md):
    days, arr, y0 = load_main_raw()
    q = arr["QQQ"]; s = arr["SPY"]
    days = list(days)
    y = y0 + FIN[md]
    soq, scq = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q) if False else (None, None)
    # synthetic_2x signature needs expense separate; do properly:
    soq, scq = _synth(q[0], q[1], q[2], y, EXP_Q)
    sos, scs = _synth(s[0], s[1], s[2], y, EXP_S)
    T = len(days)
    eQ = e2_up(q[1])[0] if q[1].ndim == 1 else e2_up(q[1])[0]
    # ensure 1-D
    eQ = np.atleast_1d(e2_up(q[1])).ravel()
    eS = np.atleast_1d(e2_up(s[1])).ravel()
    # returns
    rQ126 = _relret(q[1], 126)
    rQ252 = _relret(q[1], 252)
    rS126 = _relret(s[1], 126)
    rS252 = _relret(s[1], 252)
    vQ = trv2.vol_n(scq)[0]
    vS = trv2.vol_n(scs)[0]
    # signal arrays 2-D
    eQ2 = eQ[None, :]; eS2 = eS[None, :]
    rQ126_2 = rQ126[None, :]; rS126_2 = rS126[None, :]
    rQ252_2 = rQ252[None, :]; rS252_2 = rS252[None, :]
    vQ2 = vQ[None, :]; vS2 = vS[None, :]
    # wants per arm
    C0w = wants_for_single(eQ2, vQ2)  # [1,T]
    C0w = C0w[:, None, :]  # [1,1,T]
    Q1w = _build_q1(eQ2, rQ126_2, rS126_2, vQ2, vS2)  # [1,2,T]
    Q2w = _build_q1(eQ2, rQ252_2, rS252_2, vQ2, vS2)
    Q3w = _build_q3(eQ2, eS2, rQ126_2, rS126_2, vQ2, vS2)
    Q4w = _build_q4(eQ2, eS2, rQ126_2, rS126_2, vQ2, vS2)
    # opens/closes stacked
    opens2 = np.stack([soq, sos], axis=0)[None, ...]  # [1,2,T]
    closes2 = np.stack([scq, scs], axis=0)[None, ...]
    opens1 = scq[None, None, :]  # dummy? need Q open for C0
    # C0 uses Q open/close only
    opens1 = soq[None, None, :]
    closes1 = scq[None, None, :]
    return days, opens1, closes1, opens2, closes2, C0w, Q1w, Q2w, Q3w, Q4w


def _synth(q_open, q_close, q_div, y, expense):
    # trv2 synthetic_2x with arrays 1-D
    o, c = trv2.synthetic_2x(q_open[None, :], q_close[None, :], q_div[None, :], y[None, :] if np.ndim(y) == 1 else y, expense)
    return o[0], c[0]


def _relret(price, n):
    price = np.asarray(price, float)
    T = len(price)
    out = np.full(T, np.nan)
    out[n:] = price[n:] / price[:-n]
    # return ratio; hysteresis compares ratios vs? We need return not ratio? Use ratio okay threshold 2pp on returns = ratio-1; adjust
    return out


def _ret(price, n):
    return _relret(price, n) - 1.0


def _build_q1(eQ, rQ, rS, volQ, volS):
    # eQ etc shape [P,T]; rQ/rS are return ratios? we'll pass ratios and compare with hysteresis on ratios or returns; use returns inside
    rQr = np.asarray(rQ, float) - 1.0
    rSr = np.asarray(rS, float) - 1.0
    P, T = eQ.shape
    hold = np.full(P, -1, dtype=int)
    out = np.zeros((P, 2, T))
    for t in range(T):
        eq = eQ[:, t].astype(bool)
        rq = rQr[:, t]; rs = rSr[:, t]
        hold = np.where(eq, hold, -1)
        # switches
        curQ = (hold == 0) & eq
        sw = curQ & (rs > rq + HYST)
        hold = np.where(sw, 1, hold)
        curS = (hold == 1) & eq
        sw2 = curS & (rq > rs + HYST)
        hold = np.where(sw2, 0, hold)
        # choose from cash
        none = (hold == -1) & eq
        hold = np.where(none & (rq >= rs), 0, hold)
        hold = np.where(none & (rq < rs), 1, hold)
        # cap
        vq = trv2.vt_of(volQ[:, t]); vs = trv2.vt_of(volS[:, t])
        wq = np.where(hold == 0, vq, 0.0)
        ws = np.where(hold == 1, vs, 0.0)
        tot = wq + ws
        scale = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
        out[:, 0, t] = wq * scale
        out[:, 1, t] = ws * scale
    return out


def _build_q3(eQ, eS, rQ, rS, volQ, volS):
    rQr = np.asarray(rQ, float) - 1.0
    rSr = np.asarray(rS, float) - 1.0
    P, T = eQ.shape
    out = np.zeros((P, 2, T))
    for t in range(T):
        onQ = eQ[:, t].astype(bool); onS = eS[:, t].astype(bool)
        rq = rQr[:, t]; rs = rSr[:, t]
        # selection
        selQ = onQ & (~onS | (rq >= rs))
        selS = onS & (~onQ | (rs > rq))
        vq = trv2.vt_of(volQ[:, t]); vs = trv2.vt_of(volS[:, t])
        wq = np.where(selQ, vq, 0.0)
        ws = np.where(selS, vs, 0.0)
        tot = wq + ws
        scale = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
        out[:, 0, t] = wq * scale
        out[:, 1, t] = ws * scale
    return out


def _build_q4(eQ, eS, rQ, rS, volQ, volS):
    rQr = np.asarray(rQ, float) - 1.0
    rSr = np.asarray(rS, float) - 1.0
    P, T = eQ.shape
    out = np.zeros((P, 2, T))
    for t in range(T):
        onQ = eQ[:, t].astype(bool); onS = eS[:, t].astype(bool)
        rq = rQr[:, t]; rs = rSr[:, t]
        both = onQ & onS
        split = both & (np.abs(rq - rs) < GAP_SPLIT)
        # base selection excluding split
        selQ = onQ & (~onS | ((~split) & (rq >= rs)))
        selS = onS & (~onQ | ((~split) & (rs > rq)))
        vq = trv2.vt_of(volQ[:, t]); vs = trv2.vt_of(volS[:, t])
        wq = np.where(split, 0.5 * vq, np.where(selQ, vq, 0.0))
        ws = np.where(split, 0.5 * vs, np.where(selS, vs, 0.0))
        tot = wq + ws
        scale = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
        out[:, 0, t] = wq * scale
        out[:, 1, t] = ws * scale
    return out


def arm_stats(dates, nav, turn, ntr, start):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    try:
        st["sharpe"] = trv2.sharpe_daily(nav)
    except Exception:
        pass
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    st["subperiods"] = {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    st["stage_b_window"] = seg(dates, nav, *STAGE_B_WINDOW)
    st["time_in_market"] = None
    return st


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md in FIN:
        for i, mdn in enumerate([md]):
            pass
        # build per financing
        days, o1, c1, o2, c2, wC0, wQ1, wQ2, wQ3, wQ4 = build_main(md)
        dates = days[START - 1:]
        wants_map = {
            "C0": wC0,
            "Q1": wQ1, "Q2": wQ2, "Q3": wQ3, "Q4": wQ4,
        }
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                if arm == "C0":
                    opens, closes, wants = o1, c1, wants_map[arm]
                else:
                    opens, closes, wants = o2, c2, wants_map[arm]
                nav, turn, ntr = trv2.simulate_multi(opens, closes, wants, COST_PER_SIDE * m, START)
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], START)
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def harness():
    h = run_main(("C0",), m_list=(1, 2))
    checks = {}
    # reference from deviation file if available
    ref = None
    try:
        ref = json.loads(E2DEV.read_text())
    except Exception:
        ref = None
    for md in FIN:
        for m in (1, 2):
            x = h[md][f"m{m}"]["C0"]
            if ref is not None:
                r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
                for k in ("cagr", "max_dd"):
                    checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff_pp": (x[k] - r[k]) * 100}
            else:
                exp_cagr = {"base": {1: 0.1495, 2: 0.1467}, "calibrated": {1: None, 2: 0.1410}}[md][m]
                exp_dd = 0.496
                if exp_cagr is not None:
                    checks[f"{md}_m{m}_cagr"] = {"reference": exp_cagr, "reproduced": x["cagr"], "diff_pp": (x["cagr"] - exp_cagr) * 100}
                checks[f"{md}_m{m}_max_dd"] = {"reference": exp_dd, "reproduced": x["max_dd"], "diff_pp": (x["max_dd"] - exp_dd) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100, "pass": bool(ok_dev)}


def load_rates():
    d3, tb = [], []
    with DTB3F.open() as fh:
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


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_pair(symQ, symS, meas_from, last_date):
    rowsQ = {}; rowsS = {}
    with INDEX.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symQ:
                rowsQ[r["date"]] = r
            if r["symbol"] == symS:
                rowsS[r["date"]] = r
    d3, tb = load_rates()
    days = sorted(set(rowsQ) & set(rowsS))
    days = [d for d in days if ("1900-01-01" <= d <= last_date)]
    # filter start not here; keep all for warmup
    cQ = np.array([float(rowsQ[d]["close"]) for d in days])
    cS = np.array([float(rowsS[d]["close"]) for d in days])
    oQ = np.empty_like(cQ); oS = np.empty_like(cS)
    for k, d in enumerate(days):
        r = rowsQ[d]
        f = lambda x: None if x == "" else float(x)
        op, h, lo, cl = f(r["open"]), f(r["high"]), f(r["low"]), float(r["close"])
        oQ[k] = cl if k == 0 else (cQ[k-1] if placeholder_open(op, h, lo, cl) else op)
    for k, d in enumerate(days):
        r = rowsS[d]
        f = lambda x: None if x == "" else float(x)
        op, h, lo, cl = f(r["open"]), f(r["high"]), f(r["low"]), float(r["close"])
        oS[k] = cl if k == 0 else (cS[k-1] if placeholder_open(op, h, lo, cl) else op)
    y = rate_on(days, d3, tb)
    start = next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START
    return days, oQ, cQ, oS, cS, y, start


def build_pair(md, symQ, symS, meas_from, last_date):
    days, oQ, cQ, oS, cS, y0, start = load_pair(symQ, symS, meas_from, last_date)
    y = y0 + EXTRA_FIN  # OOS uses calibrated extra? ewma OOS uses +EXTRA_FIN (0.0075)
    # for main OOS, ewma OOS oos_window uses y + EXTRA_FIN (calibrated). Yes.
    soq, scq = _synth(oQ, cQ, np.zeros_like(cQ), y, EXP_Q)
    sos, scs = _synth(oS, cS, np.zeros_like(cS), y, EXP_S)
    T = len(days)
    eQ = np.atleast_1d(e2_up(cQ)).ravel()
    eS = np.atleast_1d(e2_up(cS)).ravel()
    rQ126 = _ret(cQ, 126)
    rS126 = _ret(cS, 126)
    vQ = trv2.vol_n(scq)[0]
    vS = trv2.vol_n(scs)[0]
    # reshape
    eQ2 = eQ[None, :]; eS2 = eS[None, :]
    rQr = (rQ126 + 1)[None, :]; rSr = (rS126 + 1)[None, :]
    vQ2 = vQ[None, :]; vS2 = vS[None, :]
    C0w = wants_for_single(eQ2, vQ2)[:, None, :]
    Q1w = _build_q1(eQ2, rQr, rSr, vQ2, vS2)
    Q2w = _build_q1(eQ2, ( _relret(cQ,252))[None,:], (_relret(cS,252))[None,:], vQ2, vS2)
    # convert ret ratios: _relret returns price ratio; build expects ratio? We passed ret+1? Wait _build converts ratio-1. Let's standardize: pass price ratio arrays.
    # Redo Q1/Q2 ratio inputs consistently:
    Q1w = _build_q1(eQ2, _relret(cQ,126)[None,:], _relret(cS,126)[None,:], vQ2, vS2)
    Q2w = _build_q1(eQ2, _relret(cQ,252)[None,:], _relret(cS,252)[None,:], vQ2, vS2)
    Q3w = _build_q3(eQ2, eS2, _relret(cQ,126)[None,:], _relret(cS,126)[None,:], vQ2, vS2)
    Q4w = _build_q4(eQ2, eS2, _relret(cQ,126)[None,:], _relret(cS,126)[None,:], vQ2, vS2)
    opens2 = np.stack([soq, sos], axis=0)[None, ...]
    closes2 = np.stack([scq, scs], axis=0)[None, ...]
    opens1 = soq[None, None, :]
    closes1 = scq[None, None, :]
    wants_map = {"C0": C0w, "Q1": Q1w, "Q2": Q2w, "Q3": Q3w, "Q4": Q4w}
    return days, start, opens1, closes1, opens2, closes2, wants_map


def oos_window(days, start, opens1, closes1, opens2, closes2, wants_map, arms):
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        if arm == "C0":
            opens, closes, wants = opens1, closes1, wants_map[arm]
        else:
            opens, closes, wants = opens2, closes2, wants_map[arm]
        nav, turn, ntr = trv2.simulate_multi(opens, closes, wants, COST_PER_SIDE * 2, start)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
                    "turnover_annual": float(turn[0] / years)}
    return res


def boot_main(arms):
    # joint block bootstrap for QQQ + SPY
    raw = defaultdict(dict)
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            raw[r["symbol"]][r["date"]] = tuple(float(r[k]) for k in ("open", "close", "dividend"))
    days0 = sorted(set(raw["QQQ"]) & set(raw["SPY"]))
    # dtb3
    tb, last = {}, None
    with DTB3F.open() as fh:
        rows = {r["observation_date"]: r["DTB3"] for r in csv.DictReader(fh)}
    for d in sorted(set(rows) | set(days0)):
        v = rows.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        tb[d] = last
    q = np.full((3, len(days0)), np.nan)  # we only need closes etc; store as arrays
    arrq = defaultdict(list)
    # reuse trv2 load? It loads q/s but q shape (3,T). Need q/s/days/y
    days, arr, y0 = (None, None, None), None, None
    # call load_main_raw gives days/arr/y without financing
    daysA, arrA, yA = load_main_raw()
    days = daysA
    q = arrA["QQQ"]; s = arrA["SPY"]
    y = yA
    T = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
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
        idx = pool[pos]  # [P, L+EXT]
        # QQQ total return overnight/intraday
        prevq = q[1][idx - 1]
        r_on_tr_q = (q[0][idx] + q[2][idx]) / prevq
        r_id_q = q[1][idx] / q[0][idx]
        # price ratio
        r_on_p_q = q[0][idx] / prevq
        prevs = s[1][idx - 1]
        r_on_tr_s = (s[0][idx] + s[2][idx]) / prevs
        r_id_s = s[1][idx] / s[0][idx]
        r_on_p_s = s[0][idx] / prevs
        one = np.ones((P, 1))
        # total return closes
        qc = np.concatenate([one, np.cumprod(r_on_tr_q * r_id_q, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr_q], axis=1)
        sc = np.concatenate([one, np.cumprod(r_on_tr_s * r_id_s, axis=1)], axis=1)
        so = np.concatenate([one, sc[:, :-1] * r_on_tr_s], axis=1)
        # price closes
        pc = np.concatenate([one, np.cumprod(r_on_p_q * r_id_q, axis=1)], axis=1)
        ps = np.concatenate([one, np.cumprod(r_on_p_s * r_id_s, axis=1)], axis=1)
        eQ = e2_up(pc)
        eS = e2_up(ps)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        # relative returns on price levels
        rQ126 = _bootstrap_rel(pc, 126)
        rS126 = _bootstrap_rel(ps, 126)
        rQ252 = _bootstrap_rel(pc, 252)
        rS252 = _bootstrap_rel(ps, 252)
        for md in FIN:
            soq, scq = _synth_b(qo, qc, yyp + FIN[md], EXP_Q)
            sos, scs = _synth_b(so, sc, yyp + FIN[md], EXP_S)
            vQ = trv2.vol_n(scq)[0] if scq.ndim == 2 else None
            vS = trv2.vol_n(scs)[0] if scs.ndim == 2 else None
            # vol_n expects [P,T]
            vQ = trv2.vol_n(scq)
            vS = trv2.vol_n(scs)
            wmap = {}
            C0w = wants_for_single(eQ, vQ)[:, None, :]
            wmap["C0"] = C0w
            wmap["Q1"] = _build_q1(eQ, rQ126, rS126, vQ, vS)
            wmap["Q2"] = _build_q1(eQ, rQ252, rS252, vQ, vS)
            wmap["Q3"] = _build_q3(eQ, eS, rQ126, rS126, vQ, vS)
            wmap["Q4"] = _build_q4(eQ, eS, rQ126, rS126, vQ, vS)
            opens2 = np.stack([soq, sos], axis=1)  # [P,2,T]
            closes2 = np.stack([scq, scs], axis=1)
            opens1 = soq[:, None, :]
            closes1 = scq[:, None, :]
            for arm in arms:
                if arm == "C0":
                    oo, cc, ww = opens1, closes1, wmap[arm]
                else:
                    oo, cc, ww = opens2, closes2, wmap[arm]
                nav, turn, ntr = trv2.simulate_multi(oo, cc, ww, COST_PER_SIDE * 2, start)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()},
            {key: np.concatenate(v) for key, v in tv.items()})


def _bootstrap_rel(price, n):
    # returns price ratios [P,T] for lag n, NaN where invalid; align to price close
    P, T = price.shape
    out = np.full((P, T), np.nan)
    out[:, n:] = price[:, n:] / price[:, :-n]
    return out


def _synth_b(qo, qc, y, expense):
    # qo/qc shape [P,T]; y [P,T-1]? synthetic_2x expects q_div same shape maybe; provide zeros
    qdiv = np.zeros_like(qc)
    o, c = trv2.synthetic_2x(qo, qc, qdiv, y, expense)
    return o, c


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        c0 = cg[(md, "C0")]
        for a in arms:
            c = cg[(md, a)]
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "turnover_median": float(np.median(tv[(md, a)])),
                          "diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                          "diff_vs_C0_p25": float(np.percentile(d, 25)), "diff_vs_C0_p75": float(np.percentile(d, 75)),
                          "share_ge_C0": float(np.mean(d >= 0))}
    return out


def boot_oos_pair(symQ, symS, meas_from, last_date, rng, arms):
    days, oQ, cQ, oS, cS, y0, start0 = load_pair(symQ, symS, meas_from, last_date)
    # relative boot uses measured start? ewma uses load_market returns start; block samples from [start,len)
    start = start0
    pool = np.arange(start, len(days))
    npool = len(pool)
    acc = {a: [] for a in arms}
    for chunk in range(0, OOS_PATHS_PER_MARKET, CHUNK):
        P = min(CHUNK, OOS_PATHS_PER_MARKET - chunk)
        pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, OOS_BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
        for t in range(1, OOS_BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        # price/total overnight factors
        prevQ = cQ[idx - 1]
        prevS = cS[idx - 1]
        r_on_tr_Q = (oQ[idx] + 0)  # we need dividends? none in index; total return ~ price return
        # There are no dividend in index data; o is open close. Compute overnight gap open/prevclose
        r_on_Q = oQ[idx] / prevQ
        r_id_Q = cQ[idx] / oQ[idx]
        r_on_S = oS[idx] / prevS
        r_id_S = cS[idx] / oS[idx]
        ccQ = np.concatenate([np.ones((P, 1)), np.cumprod(r_on_Q * r_id_Q, axis=1)], axis=1)
        ooQ = np.concatenate([np.ones((P, 1)), ccQ[:, :-1] * r_on_Q], axis=1)
        ccS = np.concatenate([np.ones((P, 1)), np.cumprod(r_on_S * r_id_S, axis=1)], axis=1)
        ooS = np.concatenate([np.ones((P, 1)), ccS[:, :-1] * r_on_S], axis=1)
        yy = y0[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        # rates already per date included in y0; use as is
        eQ = e2_up(ccQ)
        eS = e2_up(ccS)
        soq, scq = _synth_b(ooQ, ccQ, yyp + EXTRA_FIN, EXP_Q)
        sos, scs = _synth_b(ooS, ccS, yyp + EXTRA_FIN, EXP_S)
        vQ = trv2.vol_n(scq); vS = trv2.vol_n(scs)
        rQ126 = _bootstrap_rel(ccQ, 126); rS126 = _bootstrap_rel(ccS, 126)
        rQ252 = _bootstrap_rel(ccQ, 252); rS252 = _bootstrap_rel(ccS, 252)
        wmap = {}
        wmap["C0"] = wants_for_single(eQ, vQ)[:, None, :]
        wmap["Q1"] = _build_q1(eQ, rQ126, rS126, vQ, vS)
        wmap["Q2"] = _build_q1(eQ, rQ252, rS252, vQ, vS)
        wmap["Q3"] = _build_q3(eQ, eS, rQ126, rS126, vQ, vS)
        wmap["Q4"] = _build_q4(eQ, eS, rQ126, rS126, vQ, vS)
        # simulate
        opens2 = np.stack([soq, sos], axis=1)
        closes2 = np.stack([scq, scs], axis=1)
        opens1 = soq[:, None, :]
        closes1 = scq[:, None, :]
        for arm in arms:
            if arm == "C0":
                oo, cc, ww = opens1, closes1, wmap[arm]
            else:
                oo, cc, ww = opens2, closes2, wmap[arm]
            nav = trv2.simulate_multi(oo, cc, ww, COST_PER_SIDE * 2, START)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


def historical_gates(main, boot, arm):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"]["C0"]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"]["C0"]
        r[f"G1_{md}"] = bool(a2["cagr"] >= c2["cagr"] or (a2["calmar"] >= 1.10 * c2["calmar"] and a2["cagr"] >= c2["cagr"] - 0.005))
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
        r[f"G6_{md}"] = bool(a1["cagr"] >= c1["cagr"] or (a1["calmar"] >= 1.10 * c1["calmar"] and a1["cagr"] >= c1["cagr"] - 0.005))
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"]["C0"]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    r["G9"] = bool(a2["turnover_annual"] <= c2["turnover_annual"] * 2.0)
    return r


def oos_gates(oos, oos_boot_pooled, arm):
    keys = list(oos.keys())
    dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= len(keys) and n_d >= len(keys) and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm]["diff_vs_C0_median"]}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    main_res = run_main(ARMS, extras=True)
    print("main window done")
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done")

    # OOS
    oos = {}
    oos = {}
    pairs_built = {}
    for key, sq, ss, meas, last in OOS_PAIRS:
        days, start, o1, c1, o2, c2, wm = _load_pair_full(sq, ss, meas, last)
        pairs_built[key] = (days, start, o1, c1, o2, c2, wm)
        oos[key] = oos_window(days, start, o1, c1, o2, c2, wm, ARMS)
    print("OOS windows done")
    # OOS bootstrap pooled
    rng = np.random.default_rng(SEED)
    per = {}
    for key, sq, ss, meas, last in OOS_PAIRS:
        per[key] = boot_oos_pair(sq, ss, meas, last, rng, ARMS)
    pooled = {}
    for a in ARMS:
        pooled[a] = np.concatenate([per[k][a] for k in per])
    oos_boot = {"per_pair": {}, "pooled": {}}
    for k in per:
        oos_boot["per_pair"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                      "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in (["G1_base", "G1_calibrated", "G2_base", "G2_calibrated",
                                                   "G3_base", "G3_calibrated", "G4", "G5_base", "G5_calibrated",
                                                   "G6_base", "G6_calibrated", "G7", "G8", "G9"])))
        gates[arm] = g

    out = {
        "campaign": "relstrength_v1",
        "params": {"arms": list(ARMS), "cap": CAP, "cost_per_side_m1": COST_PER_SIDE,
                   "expense": {"Q": EXP_Q, "S": EXP_S}, "financing_spread": FIN,
                   "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM, "measured": MEASURED},
                   "bootstrap_oos": {"seed": SEED, "paths_per_pair": OOS_PATHS_PER_MARKET, "block": BLOCK}},
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"relstrength_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | C0 = production E2 1.1.0",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ turn/yr | boot median diff | m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            L.append(f"  {arm:5s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} | {b['diff_vs_C0_median'] * 100:+.3f}pp | {p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("gates:")
    GATE_KEYS = ["G1_base", "G1_calibrated", "G2_base", "G2_calibrated", "G3_base", "G3_calibrated",
                 "G4", "G5_base", "G5_calibrated", "G6_base", "G6_calibrated", "G7", "G8", "G9"]
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:5s} " + " ".join(f"{k}={'P' if g.get(k) else 'F'}" for k in GATE_KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


# fix build_pair name exposure
def build_pair_version(symQ, symS, meas_from, last_date):
    return build_pair_params(symQ, symS, meas_from, last_date)


def build_pair_params(symQ, symS, meas_from, last_date):
    # wrapper to avoid name collision with build_pair used above
    return _load_pair_full(symQ, symS, meas_from, last_date)


def _load_pair_full(symQ, symS, meas_from, last_date):
    days, oQ, cQ, oS, cS, y0, start = load_pair(symQ, symS, meas_from, last_date)
    y = y0 + EXTRA_FIN
    soq, scq = _synth(oQ, cQ, np.zeros_like(cQ), y, EXP_Q)
    sos, scs = _synth(oS, cS, np.zeros_like(cS), y, EXP_S)
    # need simple arrays; but _synth returns 1-D using [None]
    eQ = np.atleast_1d(e2_up(cQ)).ravel()
    eS = np.atleast_1d(e2_up(cS)).ravel()
    vQ = trv2.vol_n(scq[None, :])[0] if scq.ndim == 1 else trv2.vol_n(scq)[0]
    vS = trv2.vol_n(scs[None, :])[0] if scs.ndim == 1 else trv2.vol_n(scs)[0]
    vQ = trv2.vol_n(scq.reshape(1, -1))[0]
    vS = trv2.vol_n(scs.reshape(1, -1))[0]
    eQ2 = eQ[None, :]; eS2 = eS[None, :]
    vQ2 = vQ[None, :]; vS2 = vS[None, :]
    C0w = wants_for_single(eQ2, vQ2)[:, None, :]
    Q1w = _build_q1(eQ2, _relret(cQ, 126)[None, :], _relret(cS, 126)[None, :], vQ2, vS2)
    Q2w = _build_q1(eQ2, _relret(cQ, 252)[None, :], _relret(cS, 252)[None, :], vQ2, vS2)
    Q3w = _build_q3(eQ2, eS2, _relret(cQ, 126)[None, :], _relret(cS, 126)[None, :], vQ2, vS2)
    Q4w = _build_q4(eQ2, eS2, _relret(cQ, 126)[None, :], _relret(cS, 126)[None, :], vQ2, vS2)
    wm = {"C0": C0w, "Q1": Q1w, "Q2": Q2w, "Q3": Q3w, "Q4": Q4w}
    opens2 = np.stack([soq, sos], axis=0)[None, ...]
    closes2 = np.stack([scq, scs], axis=0)[None, ...]
    opens1 = soq[None, None, :]
    closes1 = scq[None, None, :]
    return days, start, opens1, closes1, opens2, closes2, wm


# expose build_pair called by main
build_pair = _load_pair_full


if __name__ == "__main__":
    main()
