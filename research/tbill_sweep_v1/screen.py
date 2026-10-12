#!/usr/bin/env python3
"""tbill_sweep_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --harness  C0 only: production E2 with idle cash at zero must reproduce
             e2_integration_v1's DEVIATION.json numbers (E2_prod_price_h0,
             2000-03-09 -> 2026-09-29, base/calibrated x m=1/m=2) to 0.01 pp,
             and the two-leg simulator with an empty T-bill leg must equal the
             archived single-leg engine bit for bit. Simulates no other arm.
  --run      re-runs the harness, stops if it fails, then runs every arm
             (C0, S1, S2_5, S2_10, report-only S1_SGOV, A_OFF, A_ALL) on the
             main window, the block bootstraps, the out-of-sample markets, the
             real BIL/SGOV check, the gates and the report-only statistics.
             Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol, vt, metrics) come from the vendored
byte copy of research/trend_robustness_v2/screen.py (tag
archive/research/trend-robustness-v2-2026-10, blob c9637ee); its main() is
never run. tsm_up / don_up / tr_level / bill_level / build_wants follow
research/signal_ensemble_v1/screen.py (tag archive/research/signal-ensemble-v1-2026-10).
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
_spec = importlib.util.spec_from_file_location("trv2_screen", HERE / "vendor/trend_robustness_v2_screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)
trv2.BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
trv2.DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"

GSPC = ROOT / "data/historical/tbill_sweep_v1_gspc.csv"
INDEX = ROOT / "data/historical/tbill_sweep_v1_index.csv"
TB3MS = ROOT / "data/historical/tbill_sweep_v1_tb3ms.csv"
ETF_BARS = ROOT / "data/historical/tbill_sweep_v1_etf_daily_split_sip.csv"
ETF_ACTIONS = ROOT / "data/historical/tbill_sweep_v1_etf_corporate_actions.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
CANDIDATES = ("S1", "S2_5", "S2_10")
REPORT_ARMS = ("S1_SGOV", "A_OFF", "A_ALL")
ARMS = ("C0",) + CANDIDATES + REPORT_ARMS
SMA_N, TSM_N, DON_N = 200, 252, 126
CAP, BAND = trv2.CAP, trv2.BAND  # 0.98, 0.005
COST_PER_SIDE = trv2.COST_PER_SIDE  # 4 bps per unit traded, x m (both legs)
EXP_Q = trv2.EXP_Q  # QLD* 0.95%/yr
ER_BIL, ER_SGOV = 0.001356, 0.0009  # T-bill ETF expense ratios (gated: BIL)
ALPACA_SPREAD = 0.005  # report-only cash sweep: max(0, DTB3 - 0.5 pp), zero trading
FIN = {"base": 0.0, "calibrated": 0.0075}
SEED, PATHS, CHUNK = 20261011, 5000, 500
BLOCKS = (20, 60, 250)
GATE_BLOCK = 20
WARM, MEASURED = 250, 2520
EXT = TSM_N - 250 + 1  # 3 back-extension sessions (signal_ensemble_v1)
START = TSM_N + 1  # first measured session index -> 2000-03-09
TOL = 1e-4  # 0.01 pp
DD_TOL = 0.005  # +0.5 pp max-DD tolerance
OOS_FIRST, OOS_LAST = "1957-01-02", "1999-12-31"
DTB3_FIRST = "1954-01-04"
REAL_FIRST, REAL_LAST = "2017-01-03", "2026-07-30"
SGOV_FIRST = "2020-06-01"
S2_BANDS = {"S2_5": 0.05, "S2_10": 0.10}
SEL_PREMIUM = 0.001  # S2 must beat S1 by >= 0.10 pp main-window base m=2 CAGR to be selected over it
E2C_MARKETS = {  # e2_confirmation_v1 markets (report-only OOS)
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}


# ----------------------------------------------------------------------------- signals (production E2)
def bill_level(rate):
    g = 1 + rate[..., :-1] / 252
    return np.concatenate([np.ones(rate.shape[:-1] + (1,)), np.cumprod(g, axis=-1)], axis=-1)


def tsm_up(level, bills, n=TSM_N):
    level, bills = np.atleast_2d(level), np.atleast_2d(bills)
    up = np.zeros(level.shape, dtype=bool)
    up[:, n:] = (level[:, n:] / level[:, :-n]) > (bills[:, n:] / bills[:, :-n])
    return up


def don_up(c, n=DON_N):
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def prod_signals(price):
    """Production E2 (equity_leveraged_trend_v1 1.1.0): all votes on the split-adjusted price close,
    TSM against a zero hurdle."""
    price = np.atleast_2d(price)
    return {"SMA": trv2.sma_up(price, SMA_N), "TSM": tsm_up(price, np.ones_like(price)), "DON": don_up(price)}


def e2_on(sig):
    return (sig["SMA"].astype(int) + sig["TSM"] + sig["DON"]) >= 2


def qld_wants(sig, vol):
    """[P, T] QLD* targets: [votes >= 2] * vt, capped at 0.98 (signal_ensemble_v1.build_wants, one leg)."""
    vt = trv2.vt_of(vol)
    raw = e2_on(sig).astype(float) * vt
    return np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0) * raw


def bill_series(y, er):
    """Synthetic T-bill ETF: session i accrues (DTB3[i-1] - er)/252 between its open and close; open = prev close."""
    y = np.atleast_2d(y)
    g = 1 + (y[:, :-1] - er) / 252
    close = np.concatenate([np.ones((y.shape[0], 1)), np.cumprod(g, axis=1)], axis=1)
    opn = np.concatenate([np.ones((y.shape[0], 1)), close[:, :-1]], axis=1)
    return opn, close


# ----------------------------------------------------------------------------- simulator
def simulate(qo, qc, wq, on, cost, start, mode="none", band_x=None, bo=None, bc=None, cash_rate=None,
             cash_on_off_only=False):
    """Two legs (QLD*, T-bill ETF) plus cash. All arrays [P, T].

    Per session i: overnight growth of both legs; QLD* trades to wq[i-1] when it drifts > 0.5 pp
    (exactly trend_robustness_v2.simulate_multi for one leg); the bill leg trades to its own target
    under its own rule; cost = cost * (|dQ| + |dB|); intraday growth (cash accrues cash_rate[:, i]
    when given). mode: none (C0), s1 (bills = CAP while E2 off, else 0; 0.5 pp band), s2 (S1 while
    off; while on keep bills in [CAP - w* - X, CAP - w*], reset to max(0, CAP - w* - X/2) on breach).
    """
    P, T = qc.shape
    nav = np.ones(P)
    wcur = np.zeros(P)
    wb = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    bturn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    nbt = np.zeros(P, dtype=np.int64)
    bill_days = np.zeros(P)
    for i in range(start, T):
        g_on = qo[:, i] / qc[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = wcur * g_on
        if mode == "none":
            tot = 1 - wcur + va
            nav = nav * tot
            wcur = va / tot
        else:
            gb = bo[:, i] / bc[:, i - 1]
            vb = wb * gb
            tot = 1 - wcur - wb + va + vb
            nav = nav * tot
            wcur = va / tot
            wb = vb / tot
        want = wq[:, i - 1]
        drift = np.abs(want - wcur)
        trade = drift > BAND
        if mode == "none":
            dsum = drift
            nav = np.where(trade, nav * (1 - cost * dsum), nav)
            turn = turn + np.where(trade, dsum, 0.0)
            ntr = ntr + trade.astype(np.int64)
            wcur = np.where(trade, want, wcur)
        else:
            off = ~on[:, i - 1]
            if mode == "s1":
                bt = np.where(off, CAP, 0.0)
                btrade = np.abs(bt - wb) > BAND
            elif mode == "s2":
                hi = CAP - want
                lo = hi - band_x
                bt = np.where(off, CAP, np.maximum(0.0, hi - band_x / 2))
                btrade = np.where(off, np.abs(CAP - wb) > BAND, (wb > hi + 1e-12) | (wb < lo))
            else:
                raise ValueError(mode)
            bdelta = np.where(btrade, np.abs(bt - wb), 0.0)
            qdelta = np.where(trade, drift, 0.0)
            dsum = qdelta + bdelta
            anyt = trade | btrade
            nav = np.where(anyt, nav * (1 - cost * dsum), nav)
            turn = turn + dsum
            bturn = bturn + bdelta
            ntr = ntr + trade.astype(np.int64)
            nbt = nbt + btrade.astype(np.int64)
            wcur = np.where(trade, want, wcur)
            wb = np.where(btrade, bt, wb)
            bill_days = bill_days + (wb > 0)
        ga = qc[:, i] / qo[:, i]
        va = wcur * ga
        if mode == "none" and cash_rate is None:
            tot = 1 - wcur + va
            nav = nav * tot
            wcur = va / tot
        else:
            vb = wb * (bc[:, i] / bo[:, i]) if mode != "none" else 0.0
            cash = 1 - wcur - wb
            extra = 0.0
            if cash_rate is not None:
                cr = cash_rate[:, i]
                if cash_on_off_only:
                    cr = np.where(on[:, i - 1], 0.0, cr)
                extra = np.maximum(cash, 0.0) * cr
            tot = 1 - wcur - wb + va + vb + extra
            nav = nav * tot
            wcur = va / tot
            wb = vb / tot if mode != "none" else wb
        out[:, i - start + 1] = nav
    return out, {"turnover": turn, "bill_turnover": bturn, "qld_trades": ntr, "bill_trades": nbt,
                 "bill_sessions": bill_days}


def run_arm(arm, qo, qc, wq, on, y, cost, start):
    """Dispatch one arm on [P, T] inputs; y = T-bill rate [P, T] (decimal)."""
    if arm == "C0":
        return simulate(qo, qc, wq, on, cost, start)
    if arm in ("A_OFF", "A_ALL"):
        cr = np.zeros_like(y)
        cr[:, 1:] = np.maximum(0.0, y[:, :-1] - ALPACA_SPREAD) / 252
        return simulate(qo, qc, wq, on, cost, start, cash_rate=cr, cash_on_off_only=(arm == "A_OFF"))
    er = ER_SGOV if arm == "S1_SGOV" else ER_BIL
    bo, bc = bill_series(y, er)
    if arm in ("S1", "S1_SGOV"):
        return simulate(qo, qc, wq, on, cost, start, mode="s1", bo=bo, bc=bc)
    return simulate(qo, qc, wq, on, cost, start, mode="s2", band_x=S2_BANDS[arm], bo=bo, bc=bc)


# ----------------------------------------------------------------------------- historical main window
def hist_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    vol = trv2.vol_n(c)
    sig = prod_signals(q[1])
    return days, arr, y, o[None, :], c[None, :], qld_wants(sig, vol), e2_on(sig), sig


def seg(dates, nav, a, b):
    ia = 0 if dates[0] > a else max(k for k, d in enumerate(dates) if d <= a)
    ib = max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1) if yrs > 0 else None,
            "max_dd": float(trv2.max_dd(s)[0])}


SEGMENTS = {
    "2000_2008": ("2000-03-08", "2008-12-31"),
    "zirp_2009_2015": ("2008-12-31", "2015-12-31"),
    "2016_2019": ("2015-12-31", "2019-12-31"),
    "zirp_2020_2021": ("2019-12-31", "2021-12-31"),
    "2022_2026": ("2021-12-31", "2026-09-29"),
    "stage_b_2017_01_03_2026_07_30": ("2016-12-30", "2026-07-30"),
}


def hist_run(spread, start=START, m_list=(1, 2), arms=ARMS):
    days, arr, y, o, c, wq, on, sig = hist_inputs(spread)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    yy = y[None, :]
    out = {}
    navs = {}
    for m in m_list:
        hm = {}
        for arm in arms:
            nav, info = run_arm(arm, o, c, wq, on, yy, COST_PER_SIDE * m, start)
            nav = nav[0]
            navs[(m, arm)] = nav
            st = trv2.curve_stats(dates, nav)
            st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
            st["sharpe"] = trv2.sharpe_daily(nav)
            st["turnover_annual"] = float(info["turnover"][0] / years)
            st["bill_turnover_annual"] = float(info["bill_turnover"][0] / years)
            st["qld_trades"] = int(info["qld_trades"][0])
            st["bill_trades"] = int(info["bill_trades"][0])
            st["bill_sessions_share"] = float(info["bill_sessions"][0] / (len(dates) - 1))
            st["segments"] = {k: seg(dates, nav, a, b) for k, (a, b) in SEGMENTS.items()}
            hm[arm] = st
        out[f"m{m}"] = hm
    dec = slice(start - 1, len(days) - 1)
    ctx = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1},
           "e2_off_share_of_decisions": float((~on[0, dec]).mean()),
           "e2_flips_per_year": float(np.sum(on[0, dec][1:] != on[0, dec][:-1]) / years),
           "mean_dtb3_when_off": float(y[dec][~on[0, dec]].mean()),
           "mean_dtb3_all": float(y[dec].mean())}
    return out, ctx, navs, dates


# ----------------------------------------------------------------------------- harness
def harness():
    ref = {  # research/e2_integration_v1/DEVIATION.json (tag archive/research/e2-integration-v1-2026-10), E2_prod_price_h0
        ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
        ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
        ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
        ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
    }
    checks = {}
    for md, sp in FIN.items():
        h, _, navs, _ = hist_run(sp, arms=("C0",))
        for m in (1, 2):
            st = h[f"m{m}"]["C0"]
            for k, r in zip(("cagr", "max_dd", "turnover_annual"), ref[(md, m)]):
                x = st[k]
                checks[f"{md}_m{m}_{k}"] = {"reference": r, "reproduced": x,
                                            "diff_pp": (x - r) * (100 if k != "turnover_annual" else 1)}
    # simulator identity: two-leg simulator with mode none == archived single-leg engine
    days, arr, y, o, c, wq, on, sig = hist_inputs(0.0)
    a = simulate(o, c, wq, on, COST_PER_SIDE * 2, START)[0]
    b = trv2.simulate_multi(o[:, None, :], c[:, None, :], wq[:, None, :], COST_PER_SIDE * 2, START)[0]
    identical = bool(np.array_equal(a, b))
    ok = identical and all(abs(v["diff_pp"]) <= TOL * 100 + 1e-9 for v in checks.values())
    return {"checks": checks, "engine_identity_bitwise": identical, "tolerance_pp": TOL * 100, "pass": bool(ok)}


# ----------------------------------------------------------------------------- bootstrap
def bootstrap(block, m, models=("base", "calibrated"), arms=ARMS):
    """Stationary block bootstrap; path draws identical to signal_ensemble_v1 (seed, order, chunks, EXT)."""
    _, arr, y = trv2.load()
    T = arr["QQQ"].shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    cost = COST_PER_SIDE * m
    start = 250 + 1 + EXT
    acc = {(md, a): [] for md in models for a in arms}
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
        qa = arr["QQQ"]
        prev = qa[1][idx - 1]
        r_on_tr = (qa[0][idx] + qa[2][idx]) / prev
        r_on_px = qa[0][idx] / prev
        r_id = qa[1][idx] / qa[0][idx]
        ones = np.ones((P, 1))
        qc = np.concatenate([ones, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([ones, qc[:, :-1] * r_on_tr], axis=1)
        px = np.concatenate([ones, np.cumprod(r_on_px * r_id, axis=1)], axis=1)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        sig = prod_signals(px)
        on = e2_on(sig)
        for md in models:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            wq = qld_wants(sig, trv2.vol_n(scl))
            for arm in arms:
                nav = run_arm(arm, so, scl, wq, on, yyp, cost, start)[0]
                k = nav.shape[1] - 1
                acc[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    out = {"paths": PATHS, "seed": SEED, "mean_block": block, "warmup": WARM, "back_extension": EXT,
           "measured": MEASURED, "cost_multiplier_m": m}
    for md in models:
        c0 = np.concatenate(acc[(md, "C0")])
        res = {"C0": {"cagr_median": float(np.median(c0)), "cagr_p5": float(np.percentile(c0, 5))}}
        for a in arms:
            if a == "C0":
                continue
            ca = np.concatenate(acc[(md, a)])
            d = ca - c0
            res[a] = {"cagr_median": float(np.median(ca)), "cagr_p5": float(np.percentile(ca, 5)),
                      "diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                      "diff_vs_C0_p5": float(np.percentile(d, 5)), "diff_vs_C0_p95": float(np.percentile(d, 95)),
                      "share_beats_C0": float(np.mean(d > 0))}
        out[md] = res
    return out


# ----------------------------------------------------------------------------- out of sample
def load_rates():
    d3, tb = [], []
    with trv2.DTB3.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                d3.append((r["observation_date"], float(r["DTB3"]) / 100))
    with TB3MS.open() as fh:
        for r in csv.DictReader(fh):
            if r["TB3MS"] not in (".", ""):
                tb.append((r["observation_date"], float(r["TB3MS"]) / 100))
    return d3, tb


def rate_on(dates, d3, tb):
    """Last DTB3 on or before each date (from 1954-01-04); before that, TB3MS of the month (e2_confirmation_v1)."""
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


def load_gspc():
    """signal_ensemble_v1 G8: ^GSPC 1954-01-04 -> 1999-12-31, opens := previous close, DTB3 forward-filled."""
    rows = []
    with GSPC.open() as fh:
        for r in csv.DictReader(fh):
            if DTB3_FIRST <= r["date"] <= OOS_LAST:
                rows.append((r["date"], float(r["close"])))
    days = [r[0] for r in rows]
    c = np.array([r[1] for r in rows])
    o = np.concatenate([[c[0]], c[:-1]])
    d3, tb = load_rates()
    y = rate_on(days, d3, tb)
    start = next(i for i, d in enumerate(days) if d >= OOS_FIRST)
    assert start >= START
    return days, o, c, y, start


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_e2c_market(key):
    """e2_confirmation_v1 load_market: placeholder opens -> previous close; T-bill DTB3 / TB3MS."""
    sym, first, meas_from, last = E2C_MARKETS[key]
    rows = []
    with INDEX.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == sym and first <= r["date"] <= last:
                f = lambda x: None if x == "" else float(x)
                rows.append((r["date"], f(r["open"]), f(r["high"]), f(r["low"]), float(r["close"])))
    days = [r[0] for r in rows]
    c = np.array([r[4] for r in rows])
    o = np.empty_like(c)
    for k, (d, op, h, lo, cl) in enumerate(rows):
        o[k] = cl if k == 0 else (c[k - 1] if placeholder_open(op, h, lo, cl) else op)
    d3, tb = load_rates()
    y = rate_on(days, d3, tb)
    start = START if meas_from is None else next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START
    return days, o, c, y, start


def oos_run(days, o, c, y, start, m=2, spread=FIN["calibrated"], arms=ARMS):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + spread, EXP_Q)
    sig = prod_signals(c)
    on = e2_on(sig)
    wq = qld_wants(sig, trv2.vol_n(sc))
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    out = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1},
           "e2_off_share": float((~on[0, start - 1:-1]).mean()), "mean_rate_when_off":
               float(y[start - 1:-1][~on[0, start - 1:-1]].mean()), "cost_multiplier_m": m, "financing_spread": spread}
    for arm in arms:
        nav, info = run_arm(arm, so[None, :], sc[None, :], wq, on, y[None, :], COST_PER_SIDE * m, start)
        nav = nav[0]
        out[arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)), "max_dd": float(trv2.max_dd(nav)[0]),
                    "turnover_annual": float(info["turnover"][0] / years),
                    "bill_trades": int(info["bill_trades"][0])}
    return out


# ----------------------------------------------------------------------------- real BIL / SGOV check (report only)
def load_etf(symbol):
    bars, divs = {}, defaultdict(float)
    with ETF_BARS.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symbol:
                bars[r["timestamp"][:10]] = (float(r["open"]), float(r["close"]))
    with ETF_ACTIONS.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symbol and r["action_type"] == "cash_dividends":
                divs[r["ex_date"]] += float(r["rate"])
    return bars, divs


def real_etf_series(days, symbol, first, last):
    """Real ETF open/close aligned to days; dividend added to the ex-date open (rule_variants_v1 convention).
    Returns opn, close as a total-return index (ratios are what the simulator uses) and the index range."""
    bars, divs = load_etf(symbol)
    i0 = days.index(next(d for d in days if d >= first))
    i1 = max(k for k, d in enumerate(days) if d <= last)
    missing = [d for d in days[i0 - 1:i1 + 1] if d not in bars]
    assert not missing, f"{symbol}: missing bars {missing[:5]}"
    T = len(days)
    opn, close = np.ones(T), np.ones(T)
    lvl = 1.0
    for k in range(i0, i1 + 1):
        d, p = days[k], days[k - 1]
        g_on = (bars[d][0] + divs.get(d, 0.0)) / bars[p][1]
        opn[k] = lvl * g_on
        lvl = opn[k] * bars[d][1] / bars[d][0]
        close[k] = lvl
    close[i0 - 1] = 1.0
    return opn, close, i0, i1


def real_check():
    days, arr, y, o, c, wq, on, sig = hist_inputs(0.0)
    out = {}
    for sym, er, first in (("BIL", ER_BIL, REAL_FIRST), ("SGOV", ER_SGOV, SGOV_FIRST)):
        ro, rc, i0, i1 = real_etf_series(days, sym, first, REAL_LAST)
        so_, sc_ = bill_series(y[None, :], er)
        yrs = trv2.calendar_years(days[i0 - 1], days[i1])
        real_cagr = (rc[i1] / rc[i0 - 1]) ** (1 / yrs) - 1
        syn_cagr = (sc_[0, i1] / sc_[0, i0 - 1]) ** (1 / yrs) - 1
        rr = rc[i0:i1 + 1] / rc[i0 - 1:i1] - 1
        sr = sc_[0, i0:i1 + 1] / sc_[0, i0 - 1:i1] - 1
        res = {"window": [days[i0], days[i1]], "real_tr_cagr": float(real_cagr), "synthetic_cagr": float(syn_cagr),
               "tracking_diff_pp_per_year": float((real_cagr - syn_cagr) * 100),
               "daily_return_corr": float(np.corrcoef(rr, sr)[0, 1]),
               "real_daily_vol_annual": float(rr.std(ddof=1) * np.sqrt(252))}
        Tn = i1 + 1
        sl = slice(0, Tn)
        rows = {}
        for m in (1, 2):
            for name, bo_, bc_ in (("synthetic", so_[:, sl], sc_[:, sl]), ("real", ro[None, sl], rc[None, sl])):
                for arm in ("C0", "S1"):
                    if arm == "C0":
                        nav = simulate(o[:, sl], c[:, sl], wq[:, sl], on[:, sl], COST_PER_SIDE * m, i0)[0][0]
                    else:
                        nav = simulate(o[:, sl], c[:, sl], wq[:, sl], on[:, sl], COST_PER_SIDE * m, i0,
                                       mode="s1", bo=bo_, bc=bc_)[0][0]
                    rows[f"m{m}_{name}_{arm}"] = {"cagr": float((nav[-1] / nav[0]) ** (1 / yrs) - 1),
                                                  "max_dd": float(trv2.max_dd(nav)[0])}
        res["arms"] = rows
        out[sym] = res
    return out


# ----------------------------------------------------------------------------- gates
def gates_for(hist, boot20, oos):
    g = {}
    for arm in CANDIDATES:
        r = {}
        for md in FIN:
            h1, h2 = hist[md]["m1"], hist[md]["m2"]
            a2, c2, a1, c1 = h2[arm], h2["C0"], h1[arm], h1["C0"]
            r[md] = {
                "T1_cagr_m2": bool(a2["cagr"] >= c2["cagr"]),
                "T2_cagr_m1": bool(a1["cagr"] >= c1["cagr"]),
                "T3_dd": bool(a2["max_dd"] <= c2["max_dd"] + DD_TOL and a1["max_dd"] <= c1["max_dd"] + DD_TOL),
                "T4_2017": bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"]),
                "T5_boot20": bool(boot20[md][arm]["diff_vs_C0_median"] >= 0),
                "T7_turnover": bool(a2["turnover_annual"] <= 2 * c2["turnover_annual"]),
            }
        r["T6_oos_gspc"] = bool(oos[arm]["cagr"] >= oos["C0"]["cagr"]
                                and oos[arm]["max_dd"] <= oos["C0"]["max_dd"] + DD_TOL)
        r["pass_all"] = bool(r["T6_oos_gspc"] and all(all(v.values()) for md, v in r.items()
                                                       if md in FIN))
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
        print(f"harness {k}: ref {v['reference']:.6f} repro {v['reproduced']:.6f} diff {v['diff_pp']:+.6f}")
    print(f"harness engine identity bitwise: {hc['engine_identity_bitwise']}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    out = {"params": {
        "arms": list(ARMS), "candidates": list(CANDIDATES), "report_only": list(REPORT_ARMS),
        "cap": CAP, "band": BAND, "cost_per_side_m1": COST_PER_SIDE, "expense_qld_star": EXP_Q,
        "er_bil": ER_BIL, "er_sgov": ER_SGOV, "alpaca_spread": ALPACA_SPREAD, "s2_bands": S2_BANDS,
        "financing_spread": FIN, "dd_tolerance": DD_TOL, "selection_premium": SEL_PREMIUM,
        "bootstrap": {"paths": PATHS, "seed": SEED, "blocks": list(BLOCKS), "gate_block": GATE_BLOCK,
                      "warmup": WARM, "back_extension": EXT, "measured": MEASURED},
        "signals": "production E2: SMA200, 252-session price return > 0, close > 126-session channel midpoint, all on QQQ price close",
    }, "harness_check": hc}

    hist, ctx = {}, None
    navs_base = None
    for md, sp in FIN.items():
        h, cx, navs, dates = hist_run(sp)
        hist[md] = h
        if md == "base":
            ctx, navs_base, dates_base = cx, navs, dates
    out["context"] = ctx
    out["historical"] = hist
    # calendar-year differences vs C0 (base, m=2)
    yd = {}
    for arm in ARMS[1:]:
        yd[arm] = {y: hist["base"]["m2"][arm]["years"][y] - hist["base"]["m2"]["C0"]["years"][y]
                   for y in hist["base"]["m2"]["C0"]["years"]}
    out["year_diff_vs_C0_base_m2"] = yd
    print("historical done", flush=True)

    boot = {}
    for blk in BLOCKS:
        boot[f"block{blk}"] = bootstrap(blk, 2)
        print(f"bootstrap block {blk} done", flush=True)
    out["bootstrap_m2"] = boot

    oos = oos_run(*load_gspc())
    out["oos_gspc"] = oos
    e2c = {}
    for key in E2C_MARKETS:
        e2c[key] = oos_run(*load_e2c_market(key))
        e2c[key]["symbol"] = E2C_MARKETS[key][0]
    out["oos_e2_confirmation_markets"] = e2c
    print("oos done", flush=True)

    out["real_etf_check"] = real_check()

    gates = gates_for(hist, boot["block20"], oos)
    out["gates"] = gates
    passing = [a for a in CANDIDATES if gates[a]["pass_all"]]
    h2 = hist["base"]["m2"]
    sel = "none"
    if "S1" in passing:
        sel = "S1"
        s2p = [a for a in passing if a != "S1" and h2[a]["cagr"] >= h2["S1"]["cagr"] + SEL_PREMIUM]
        if s2p:
            sel = max(s2p, key=lambda a: h2[a]["cagr"])
    elif passing:
        sel = max(passing, key=lambda a: h2[a]["cagr"])
    out["selection"] = {"passing": passing, "selected": sel,
                        "rule": "S1 if it passes; an S2 arm replaces it only if it passes and beats S1's base m=2 CAGR by >= 0.10 pp; if S1 fails, highest base m=2 CAGR among passing S2 arms"}
    out["status"] = "complete"
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    L = [f"tbill_sweep_v1 | window {ctx['window']['start']} -> {ctx['window']['end']} ({ctx['window']['sessions']} sessions)",
         f"harness (C0 = production E2 vs e2_integration_v1 DEVIATION.json; engine identity): "
         f"{'PASS' if hc['pass'] else 'FAIL'} max |diff| {max(abs(v['diff_pp']) for v in hc['checks'].values()):.6f}",
         f"E2 off share {p(ctx['e2_off_share_of_decisions'])}, flips/yr {ctx['e2_flips_per_year']:.2f}, "
         f"mean DTB3 when off {p(ctx['mean_dtb3_when_off'])} (all {p(ctx['mean_dtb3_all'])})", ""]
    for md in FIN:
        b20, b60, b250 = (boot[f"block{k}"][md] for k in BLOCKS)
        L.append(f"[{md}] m=2: arm CAGR maxDD 2017+ turn/yr billTurn billTrades | m=1 CAGR maxDD | boot20 med dmed share>C0 | b60 dmed | b250 dmed")
        for arm in ARMS:
            a, a1 = hist[md]["m2"][arm], hist[md]["m1"][arm]
            if arm == "C0":
                bt = f"{p(b20['C0']['cagr_median'])} {'':>8s} {'':>7s} | {'':>8s} | {'':>8s}"
            else:
                bt = (f"{p(b20[arm]['cagr_median'])} {b20[arm]['diff_vs_C0_median'] * 100:+7.3f}pp "
                      f"{p(b20[arm]['share_beats_C0'])} | {b60[arm]['diff_vs_C0_median'] * 100:+7.3f}pp | "
                      f"{b250[arm]['diff_vs_C0_median'] * 100:+7.3f}pp")
            L.append(f"  {arm:7s} {p(a['cagr'])} {p(a['max_dd'])} {p(a['2017_end']['cagr'])} {a['turnover_annual']:6.2f} "
                     f"{a['bill_turnover_annual']:6.2f} {a['bill_trades']:5d} | {p(a1['cagr'])} {p(a1['max_dd'])} | {bt}")
        L.append("")
    L.append("segments (base, m=2) CAGR: " + " ".join(SEGMENTS))
    for arm in ARMS:
        s = hist["base"]["m2"][arm]["segments"]
        L.append(f"  {arm:7s} " + " ".join(p(s[k]['cagr']) for k in SEGMENTS))
    L.append("")
    L.append(f"OOS ^GSPC {oos['window']['start']} -> {oos['window']['end']} (calibrated, m=2, fills at previous close), "
             f"off share {p(oos['e2_off_share'])}, mean rate off {p(oos['mean_rate_when_off'])}:")
    for arm in ARMS:
        L.append(f"  {arm:7s} CAGR {p(oos[arm]['cagr'])} maxDD {p(oos[arm]['max_dd'])} turn {oos[arm]['turnover_annual']:5.2f}")
    L.append("OOS e2_confirmation markets (calibrated, m=2; M3-M5 use US T-bills as a USD proxy), CAGR / maxDD:")
    for key, r in e2c.items():
        L.append(f"  {key} {r['symbol']:7s} {r['window']['start']}->{r['window']['end']} off {p(r['e2_off_share'])}: " +
                 " ".join(f"{arm} {p(r[arm]['cagr'])}/{p(r[arm]['max_dd'])}" for arm in ("C0", "S1", "S2_5", "S2_10", "A_OFF")))
    L.append("")
    L.append("real ETF check (report only):")
    for sym, r in out["real_etf_check"].items():
        L.append(f"  {sym} {r['window'][0]}->{r['window'][1]}: real TR CAGR {p(r['real_tr_cagr'])} synthetic {p(r['synthetic_cagr'])} "
                 f"tracking {r['tracking_diff_pp_per_year']:+.3f} pp/yr corr {r['daily_return_corr']:.3f}")
        L.append("    " + " ".join(f"{k} {p(v['cagr'])}/{p(v['max_dd'])}" for k, v in r["arms"].items()))
    L.append("")
    L.append("gates (per financing model: T1 T2 T3 T4 T5 T7; T6 OOS):")
    for arm in CANDIDATES:
        gg = gates[arm]
        parts = [md[:4] + " " + " ".join(f"{k.split('_')[0]}={'P' if v else 'F'}" for k, v in gg[md].items()) for md in FIN]
        L.append(f"  {arm:6s} " + " | ".join(parts) + f" | T6={'P' if gg['T6_oos_gspc'] else 'F'} -> "
                 f"{'PASS' if gg['pass_all'] else 'FAIL'}")
    L.append(f"selected: {sel}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
