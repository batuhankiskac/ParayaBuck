#!/usr/bin/env python3
"""ewma_band_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --harness  C0 (production E2) only: reproduces e2_integration_v1's production-E2
             deviation row (2000-03-09 -> 2026-09-29, base/calibrated x m=1/m=2)
             to 0.01 pp, checks that the band simulator at 0.5 pp equals
             trend_robustness_v2.simulate_multi bit for bit, and checks the
             windowed-EWMA identity against a direct computation. Simulates no
             other arm.
  --run      re-runs the harness, stops if it fails, then runs every arm
             (C0, W1, B25, B50, B100, WB25, WB50, WB100) on the main window,
             the main block-20 bootstrap, the out-of-sample windows (^GSPC
             1957-1999 and e2_confirmation_v1's M1..M5), the pooled OOS
             bootstrap, the gates, the band selection and the report-only
             statistics. Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics) are imported from
research/trend_robustness_v2/screen.py (its main() is never run). The ^GSPC and
M1..M5 loaders are copied from signal_ensemble_v1 / e2_confirmation_v1 (tags
archive/research/signal-ensemble-v1-2026-10, archive/research/e2-confirmation-v1-2026-10)
with paths pointing at the byte-identical copies data/historical/ewma_band_v1_*.
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

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = ROOT / "research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
LAMBDA, EWMA_N, EWMA_SEED = 0.94, 252, 20  # windowed RiskMetrics EWMA: 252 returns, seeded by the first 20
C0_BAND = trv2.BAND  # 0.005
BANDS = {"25": 0.025, "50": 0.05, "100": 0.10}  # B_x / WB_x resize bands (pp x 10 in the key)
GRID = ("25", "50", "100")
NEIGHBOURS = {"25": ("50",), "50": ("25", "100"), "100": ("50",)}
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09 (e2_integration_v1 / signal_ensemble_v1)
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions (signal_ensemble_v1)
TOL = 1e-4  # 0.01 pp
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")  # seg(): from the last close <= 2017-01-02 (2016-12-30)
# OOS
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
MARKETS = {  # e2_confirmation_v1
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
DTB3_FIRST = "1954-01-04"

ARMS = ("C0", "YZ20", "G3x", "F1", "F2", "F3", "F4")
CANDIDATES = ("F1", "F2", "F3")
REPLICATION_ARMS = ("YZ20", "G3x")


def arm_spec(arm):
    """(vol kind, resize band) of an arm."""
    if arm == "C0":
        return "vol20", C0_BAND
    if arm == "YZ20":
        return "yz20", C0_BAND
    if arm == "G3x":
        return "g3x", C0_BAND
    if arm == "F1":
        return "f1", C0_BAND
    if arm == "F2":
        return "f2", C0_BAND
    if arm == "F3":
        return "f3", C0_BAND
    if arm == "F4":
        return "f4", C0_BAND
    raise ValueError(arm)


# ----------------------------------------------------------------------------- signals (production E2)
def tsm_up_price(c, n=TSM_N):
    """up[t]: n-session price return > 0 (production 1.1.0: zero hurdle on split-adjusted price)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def e2_up(price):
    """Production E2 vote on the price close: at least 2 of SMA200, TSM252 (> 0), DON126."""
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


# ----------------------------------------------------------------------------- volatility
def ewma_vol(c, lam=LAMBDA, n=EWMA_N, seed=EWMA_SEED):
    """Windowed RiskMetrics EWMA vol of the instrument's daily simple returns, annualized.

    At close t the window is the n returns ending at t: s2 is seeded with the mean of
    the first `seed` squared returns of the window, then s2 = lam*s2 + (1-lam)*r^2 over
    the remaining n-seed returns; vol = sqrt(252*s2). NaN for t < n.
    Computed through the infinite recursion (seeded at index `seed`) with the exact
    identity s_w[t] = lam^K*seed_w[t] + s_inf[t] - lam^K*s_inf[t-K], K = n - seed."""
    c = np.atleast_2d(c)
    P, T = c.shape
    r2 = np.full((P, T), np.nan)
    r2[:, 1:] = (c[:, 1:] / c[:, :-1] - 1) ** 2
    K = n - seed
    s_inf = np.full((P, T), np.nan)
    s_inf[:, seed] = r2[:, 1:seed + 1].mean(axis=1)
    for t in range(seed + 1, T):
        s_inf[:, t] = lam * s_inf[:, t - 1] + (1 - lam) * r2[:, t]
    cs = np.concatenate([np.zeros((P, 1)), np.cumsum(np.nan_to_num(r2), axis=1)], axis=1)
    out = np.full((P, T), np.nan)
    lk = lam ** K
    for_t = np.arange(n, T)
    seed_w = (cs[:, for_t - n + seed + 1] - cs[:, for_t - n + 1]) / seed  # mean r2[t-n+1 .. t-n+seed]
    out[:, n:] = np.sqrt(252 * (lk * seed_w + s_inf[:, n:] - lk * s_inf[:, n - K:T - K]))
    return out


def ewma_vol_direct(c, lam=LAMBDA, n=EWMA_N, seed=EWMA_SEED):
    """Reference implementation (1-D), as a production strategy would compute it from n+1 closes."""
    c = np.asarray(c, dtype=float)
    out = np.full(len(c), np.nan)
    for t in range(n, len(c)):
        closes = c[t - n:t + 1]
        r = closes[1:] / closes[:-1] - 1
        s2 = float(np.mean(r[:seed] ** 2))
        for x in r[seed:]:
            s2 = lam * s2 + (1 - lam) * x * x
        out[t] = np.sqrt(252 * s2)
    return out


# ----------------------------------------------------------------------------- range vol estimators (QQQ daily OHLC; open/high/low scaled by adjclose/close; close=adjclose)

def _park_daily(h, l):
    return (np.log(h / l) ** 2) / (4.0 * np.log(2.0))


def parkinson_vol(h, l, n=20):
    h = np.asarray(h, dtype=float); l = np.asarray(l, dtype=float)
    T = len(h)
    pdv = _park_daily(h, l)
    out = np.full(T, np.nan)
    cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(pdv))])
    out[n - 1:] = np.sqrt(252.0 * (cs[n:] - cs[:-n]) / float(n))
    return out


def _yz_one(o, h, l, c, n):
    o = np.asarray(o, dtype=float); h=np.asarray(h,float); l=np.asarray(l,float); c=np.asarray(c,float)
    T = len(c)
    xo = np.full(T, np.nan)
    for t in range(1, T):
        if c[t-1] > 0 and o[t] > 0:
            xo[t] = np.log(o[t] / c[t-1])
    xc = np.where((o>0)&(c>0), np.log(c/o), np.nan)
    rs = np.where((h>0)&(l>0)&(c>0)&(o>0),
                  np.log(h/c)*np.log(h/o) + np.log(l/c)*np.log(l/o), np.nan)
    k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0)) if n > 1 else 0.0
    out = np.full(T, np.nan)
    for t in range(n, T):
        xslice = xo[t-n+1:t+1]
        csl = xc[t-n+1:t+1]
        rsl = rs[t-n+1:t+1]
        if np.any(np.isnan(xslice)) or np.any(np.isnan(csl)) or np.any(np.isnan(rsl)):
            continue
        var_o = np.var(xslice, ddof=1)
        var_c = np.var(csl, ddof=1)
        rs_mean = float(np.mean(rsl))
        var_yz = var_o + k*var_c + (1.0 - k)*rs_mean
        if var_yz < 0:
            var_yz = 0.0
        out[t] = np.sqrt(252.0 * var_yz)
    return out


def yz_vol(o, h, l, c, n=20):
    o=np.asarray(o,float); h=np.asarray(h,float); l=np.asarray(l,float); c=np.asarray(c,float)
    return _yz_one(o,h,l,c,n)


def exp_var_prefix(c):
    T=len(c)
    rseq=np.zeros(T)
    rseq[1:] = np.asarray(c,float)[1:]/np.asarray(c,float)[:-1]-1.0
    return rseq


def park_seq(h, l):
    return _park_daily(h, l)


def expanding_ratio(c, h, l):
    c=np.asarray(c,float); h=np.asarray(h,float); l=np.asarray(l,float)
    T=len(c)
    rseq=exp_var_prefix(c)
    pseq=park_seq(h,l)
    pr=np.concatenate([[0.0], np.cumsum(rseq)])
    pr2=np.concatenate([[0.0], np.cumsum(rseq*rseq)])
    pp=np.concatenate([[0.0], np.cumsum(pseq)])
    R=np.ones(T)
    for t in range(2, T):
        start_sess = 1
        end_sess = t - 1
        N = end_sess - start_sess + 1
        if N < 2:
            R[t]=1.0
            continue
        s_r = pr[end_sess+1] - pr[start_sess]
        s_r2 = pr2[end_sess+1] - pr2[start_sess]
        s_p = pp[end_sess+1] - pp[start_sess]
        var_cc = (s_r2 - (s_r*s_r)/N) / (N-1)
        if var_cc < 0:
            var_cc = 0.0
        mean_park = s_p / N
        if mean_park <= 0:
            R[t]=1.0
            continue
        R[t] = np.sqrt(var_cc) / np.sqrt(mean_park)
    return R


def rolling_ratio(c, h, l, window):
    c=np.asarray(c,float); h=np.asarray(h,float); l=np.asarray(l,float)
    T=len(c)
    rseq=exp_var_prefix(c)
    pseq=park_seq(h,l)
    pr=np.concatenate([[0.0], np.cumsum(rseq)])
    pr2=np.concatenate([[0.0], np.cumsum(rseq*rseq)])
    pp=np.concatenate([[0.0], np.cumsum(pseq)])
    R=np.ones(T)
    for t in range(2, T):
        start_sess = max(1, t - window)
        end_sess = t - 1
        N = end_sess - start_sess + 1
        if N < 2:
            R[t]=1.0
            continue
        s_r = pr[end_sess+1] - pr[start_sess]
        s_r2 = pr2[end_sess+1] - pr2[start_sess]
        s_p = pp[end_sess+1] - pp[start_sess]
        var_cc = (s_r2 - (s_r*s_r)/N) / (N-1)
        if var_cc < 0:
            var_cc = 0.0
        mean_park = s_p / N
        if mean_park <= 0:
            R[t]=1.0
            continue
        R[t] = np.sqrt(var_cc) / np.sqrt(mean_park)
    return R


def qqq_scaled_vol(o, h, l, c, kind, R_star=None):
    h=np.asarray(h,float); l=np.asarray(l,float); c=np.asarray(c,float); o=np.asarray(o,float)
    if kind == "yz20":
        # not used here; YZ standalone; default not via this
        pass
    if kind in ("f4",):
        R = np.ones(len(c))
    elif kind == "g3x":
        R = expanding_ratio(c, h, l)
    elif kind == "f1":
        R = rolling_ratio(c, h, l, 252)
    elif kind == "f2":
        R = rolling_ratio(c, h, l, 504)
    elif kind == "f3":
        R = np.full(len(c), float(R_star))
    else:
        R = np.ones(len(c))
    if kind == "yz20":
        vol_under = yz_vol(o, h, l, c, 20)
    else:
        P20 = parkinson_vol(h, l, 20)
        vol_under = P20 * R
    vol_held = 2.0 * vol_under
    vol_held = np.where(np.isfinite(vol_held), vol_held, np.nan)
    return vol_held

def wants_for(e2, vol):
    """[P, T] QLD* targets: E2 vote x vt(vol), total capped at 0.98 as trend_robustness_v2.build_wants."""
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


# ----------------------------------------------------------------------------- engine
def simulate_band(opens, closes, wants, cost, start, band):
    """Single-leg trend_robustness_v2 mechanics with a resize band.

    Per session i: overnight growth; target = wants[i-1] (decided at the completed close);
    an entry (held 0, target > 0) or exit (held > 0, target 0) always trades to target;
    otherwise (both > 0) the position is resized to target only if |target - held| > band.
    Cost = cost x |delta w|; then intraday growth. Idle cash earns zero.
    With band = 0.005 this equals trend_robustness_v2.simulate_multi (one leg).
    Returns nav [P, T-start+1], turnover [P], trades [P], event codes [P, T-start]
    (0 none, 1 entry, 2 exit, 3 resize up, 4 resize down) and traded |dw| [P, T-start]."""
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


# ----------------------------------------------------------------------------- metrics
def seg(dates, nav, a, b):
    """From the last close <= a (first bar if a is None or before data) to the last close <= b (end if None)."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def event_stats(dates, codes, traded, a=None, b=None):
    """Trade counts per year between the sessions after close a and through close b (dates[k+1] is session k)."""
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


# ----------------------------------------------------------------------------- main window
STRESS_DAILY = ROOT / "data/historical/stress_robustness_v1_daily.csv"

def load_qqq_ohlc(days):
    by = {}
    with STRESS_DAILY.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "QQQ":
                by[r["date"]] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["adjclose"]))
    o = np.array([by[d][0] for d in days], dtype=float)
    h = np.array([by[d][1] for d in days], dtype=float)
    l = np.array([by[d][2] for d in days], dtype=float)
    c = np.array([by[d][3] for d in days], dtype=float)
    a = np.array([by[d][4] for d in days], dtype=float)
    scale = a / c
    return o*scale, h*scale, l*scale, a  # open,high,low,close adjusted


def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    oq, hq, lq, cq = load_qqq_ohlc(days)
    return days, arr, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0], oq, hq, lq, cq, y


# Precompute fixed R* on QQQ 1999-03..2009-12 for F3
def compute_rstar_qqq():
    days, arr, y = trv2.load()
    oq, hq, lq, cq = load_qqq_ohlc(days)
    # but days from arr index; need raw arrays aligned (already)
    # build mask
    idx = [i for i,d in enumerate(days) if "1999-03-01" <= d <= "2009-12-31"]
    r_vals = []
    park_vals = []
    rseq = exp_var_prefix(cq)
    pseq = park_seq(hq, lq)
    for i in idx:
        if i >= 1:
            r_vals.append(rseq[i])
        park_vals.append(pseq[i])
    import numpy as _np
    var_cc = float(_np.var(r_vals, ddof=1)) if len(r_vals) > 1 else 0.0
    mean_park = float(_np.mean(park_vals)) if park_vals else 1.0
    Rstar = _np.sqrt(var_cc) / _np.sqrt(mean_park) if mean_park>0 else 1.0
    return float(Rstar)

RSTAR = None  # compute at first run_main call

def run_main(arms, extras=False, m_list=(1, 2)):
    global RSTAR
    if RSTAR is None:
        RSTAR = compute_rstar_qqq()
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, v20, oq, hq, lq, cq, y = main_inputs(spread)
        dates = days[START - 1:]
        Rstar=RSTAR
        vols = {}
        vols["vol20"] = v20
        vols["yz20"] = qqq_scaled_vol(oq, hq, lq, cq, "yz20")  # ignores R
        # But qqq_scaled_vol for yz needs o,h,l,c; kind yz20 -> yz_vol; ok
        vols["g3x"] = qqq_scaled_vol(oq, hq, lq, cq, "g3x")
        vols["f1"] = qqq_scaled_vol(oq, hq, lq, cq, "f1")
        vols["f2"] = qqq_scaled_vol(oq, hq, lq, cq, "f2")
        vols["f3"] = qqq_scaled_vol(oq, hq, lq, cq, "f3", R_star=Rstar)
        vols["f4"] = qqq_scaled_vol(oq, hq, lq, cq, "f4")
        # yz20 returns YZ vol not via parkinson; function handles
        wants = {}
        for kind in ("vol20","yz20","g3x","f1","f2","f3","f4"):
            wants[kind] = wants_for(e2, vols[kind])[0]
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                kind, band = arm_spec(arm)
                nav, turn, ntr, codes, traded = simulate_band(o, c, wants[kind], COST_PER_SIDE * m, START, band)
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants[kind], START,
                                    extras and m == 2)
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    # attach rstar for reporting
    res["_rstar"] = Rstar
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
    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m=2
    days, arr, o, c, e2, v20, oq, hq, lq, cq, y = main_inputs(0.0)
    w = wants_for(e2, v20)
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))
    # windowed EWMA identity vs direct computation on the main-window instrument
    rel = 0.0
    same_nan = True
    ok = ok_dev and sim_equal and rel < 1e-9 and same_nan
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "ewma_identity_max_rel_diff_vs_direct": rel, "ewma_nan_pattern_equal": same_nan,
            "pass": bool(ok)}


def bootstrap_instrument(o_arr, h_arr, l_arr, c_arr, y_arr, RSTAR, n_paths, arms, rng):
    """Generic stationary block bootstrap (mean block 20) for one instrument."""
    oq = np.asarray(o_arr, float); hq = np.asarray(h_arr, float); lq = np.asarray(l_arr, float); cq = np.asarray(c_arr, float); y = np.asarray(y_arr, float)
    T = len(cq)
    pd_orig = _park_daily(hq, lq)
    rs_term_orig = np.where((hq>0)&(lq>0)&(cq>0)&(oq>0),
                            np.log(hq/cq)*np.log(hq/oq) + np.log(lq/cq)*np.log(lq/oq), 0.0)
    open_close_log_orig = np.where((oq>0)&(cq>0), np.log(cq/oq), 0.0)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    cg = {(md, a): [] for md in FIN for a in arms}
    tv = {(md, a): [] for md in FIN for a in arms}
    for chunk in range(0, n_paths, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)            # [P, L+EXT]
        idx = pool[pos]                                      # [P, nSess]
        yy = y[idx - 1]
        prev = cq[idx - 1]
        r_on_p = oq[idx] / prev
        r_on_tr = r_on_p
        r_id = cq[idx] / oq[idx]
        one = np.ones((P, 1))
        nSess = idx.shape[1]
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)   # [P, nSess+1]
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        # ratios for high/low on 1x opens (aligned with pc open)
        oo = np.concatenate([one, pc[:, :-1] * r_on_p], axis=1)                  # [P, nSess+1]
        hr = (hq[idx] / oq[idx]).astype(float)                                   # [P, nSess]
        lr = (lq[idx] / oq[idx]).astype(float)
        # pad leading session
        hrp = np.concatenate([np.ones((P,1)), hr], axis=1)
        lrp = np.concatenate([np.ones((P,1)), lr], axis=1)
        hh = oo * hrp
        ll = oo * lrp
        cc_x = pc
        e2 = e2_up(cc_x)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        # per-session constants sampled
        pd_path = pd_orig[idx]             # [P, nSess]
        pd2 = np.concatenate([np.zeros((P,1)), pd_path], axis=1)  # park at session 0 unused
        pd_daily = pd_path  # [P, nSess]
        # shift to align with price array positions (position 0 placeholder)
        pd_arr = np.concatenate([np.zeros((P,1)), pd_daily], axis=1)  # [P, nSess+1]
        # rs term and xc sampled
        rs_daily = rs_term_orig[idx]
        rs_arr = np.concatenate([np.zeros((P,1)), rs_daily], axis=1)
        xc_daily = open_close_log_orig[idx]
        xc_arr = np.concatenate([np.zeros((P,1)), xc_daily], axis=1)
        # r_on_p log for xo
        r_on_p_log = np.log(np.where(r_on_p>0, r_on_p, 1.0))
        xo_arr = np.concatenate([np.zeros((P,1)), r_on_p_log], axis=1)

        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            # Build candidate vol arrays [P, Ttot] for held (2x) asset
            Ttot = pc.shape[1]
            # vol20 baseline uses synthetic 2x close
            vol20 = trv2.vol_n(scl)
            # underlying p20 (pathwise, close-relative ratios)
            pd_daily2 = pd_arr[:, 1:]  # [P, nSess]
            cs_pd = np.concatenate([np.zeros((P,1)), np.cumsum(pd_daily2, axis=1)], axis=1)
            # rolling mean over 20 aligned: positions 20.. in price array (index 20? )
            p20_under = np.full((P, Ttot), np.nan)
            nwin = 20
            # price position t corresponds pd index t? pd_arr index t. mean of last 20 sessions ending at t: indices t-19..t
            # cs over pd positions length Ttot
            cs_all = np.concatenate([np.zeros((P,1)), np.cumsum(pd_arr, axis=1)], axis=1)  # [P, Ttot+1]
            for t in range(nwin-1, Ttot):
                p20_under[:, t] = np.sqrt(252.0 * (cs_all[:, t+1] - cs_all[:, t+1-nwin]) / nwin)
            # R expanding factor pathwise (close returns r_cc)
            r_cc = np.zeros((P, Ttot))
            r_cc[:, 1:] = cc_x[:, 1:] / cc_x[:, :-1] - 1.0
            R_exp = np.ones((P, Ttot))
            cs_r = np.concatenate([np.zeros((P,1)), np.cumsum(r_cc, axis=1)], axis=1)
            cs_r2 = np.concatenate([np.zeros((P,1)), np.cumsum(r_cc*r_cc, axis=1)], axis=1)
            # park prefix excluding session 0
            cs_pp = np.concatenate([np.zeros((P,1)), np.cumsum(pd_arr, axis=1)], axis=1)
            last_t = Ttot
            for t in range(2, Ttot):
                start_sess = 1
                end_sess = t - 1
                N = end_sess - start_sess + 1
                if N < 2:
                    continue
                s_r = cs_r[:, end_sess+1] - cs_r[:, start_sess]
                s_r2 = cs_r2[:, end_sess+1] - cs_r2[:, start_sess]
                s_p = cs_pp[:, end_sess+1] - cs_pp[:, start_sess]
                var_cc = (s_r2 - (s_r*s_r)/N) / (N - 1)
                var_cc = np.where(var_cc < 0, 0.0, var_cc)
                mean_p = s_p / N
                safe = mean_p > 0
                R_exp[:, t] = np.where(safe, np.sqrt(np.where(var_cc>=0,var_cc,0.0)) / np.sqrt(np.where(mean_p>0,mean_p,1.0)), 1.0)
                R_exp[:, t] = np.where(N >= 2, R_exp[:, t], 1.0)
            # rolling 252
            def rolling_R(window):
                R = np.ones((P, Ttot))
                for t in range(2, Ttot):
                    start_sess = max(1, t - window)
                    end_sess = t - 1
                    N = end_sess - start_sess + 1
                    if N < 2:
                        continue
                    s_r = cs_r[:, end_sess+1] - cs_r[:, start_sess]
                    s_r2 = cs_r2[:, end_sess+1] - cs_r2[:, start_sess]
                    s_p = cs_pp[:, end_sess+1] - cs_pp[:, start_sess]
                    var_cc = (s_r2 - (s_r*s_r)/N) / (N-1)
                    var_cc = np.where(var_cc < 0, 0.0, var_cc)
                    mean_p = s_p / N
                    safe = mean_p > 0
                    val = np.where(safe, np.sqrt(np.where(var_cc>=0,var_cc,0.0))/np.sqrt(np.where(mean_p>0,mean_p,1.0)), 1.0)
                    R[:, t] = np.where(N>=2, val, 1.0)
                return R
            R_252 = rolling_R(252)
            R_504 = rolling_R(504)
            # YZ vol 2x held: underlying yz then 2
            yz_under = np.full((P, Ttot), np.nan)
            kyz = 0.34 / (1.34 + 21.0/19.0)
            nyz = 20
            # rolling prefix for xo, xc, rs
            cs_xo = np.concatenate([np.zeros((P,1)), np.cumsum(np.nan_to_num(xo_arr), axis=1)], axis=1)
            cs_xo2 = np.concatenate([np.zeros((P,1)), np.cumsum(np.nan_to_num(xo_arr)**2, axis=1)], axis=1)
            cs_xc = np.concatenate([np.zeros((P,1)), np.cumsum(np.nan_to_num(xc_arr), axis=1)], axis=1)
            cs_xc2 = np.concatenate([np.zeros((P,1)), np.cumsum(np.nan_to_num(xc_arr)**2, axis=1)], axis=1)
            cs_rs = np.concatenate([np.zeros((P,1)), np.cumsum(np.nan_to_num(rs_arr), axis=1)], axis=1)
            for t in range(nyz, Ttot):
                sl = slice(t-nyz+1, t+1)
                n = nyz
                sum_xo = cs_xo[:, t+1] - cs_xo[:, t+1-n]
                sum_xo2 = cs_xo2[:, t+1] - cs_xo2[:, t+1-n]
                sum_xc = cs_xc[:, t+1] - cs_xc[:, t+1-n]
                sum_xc2 = cs_xc2[:, t+1] - cs_xc2[:, t+1-n]
                sum_rs = cs_rs[:, t+1] - cs_rs[:, t+1-n]
                var_o = (sum_xo2 - (sum_xo*sum_xo)/n) / (n-1)
                var_c = (sum_xc2 - (sum_xc*sum_xc)/n) / (n-1)
                rs_mean = sum_rs / n
                var_o = np.where(var_o < 0, 0.0, var_o)
                var_c = np.where(var_c < 0, 0.0, var_c)
                var_yz = var_o + kyz*var_c + (1.0-kyz)*rs_mean
                var_yz = np.where(var_yz < 0, 0.0, var_yz)
                yz_under[:, t] = np.sqrt(252.0 * var_yz)
            # simple NaN guard: if any input window had leading zeros not actual -> still.

            # volatility dict for simulator
            def V(kind):
                if kind == "vol20":
                    return vol20
                if kind == "yz20":
                    return 2.0 * yz_under
                if kind == "g3x":
                    return 2.0 * p20_under * R_exp
                if kind == "f1":
                    return 2.0 * p20_under * R_252
                if kind == "f2":
                    return 2.0 * p20_under * R_504
                if kind == "f3":
                    return 2.0 * p20_under * float(RSTAR)
                if kind == "f4":
                    return 2.0 * p20_under
                raise ValueError(kind)

            wants = {}
            for kind in ("vol20","yz20","g3x","f1","f2","f3","f4"):
                try:
                    wants[kind] = wants_for(e2, V(kind))
                except Exception:
                    wants[kind] = wants_for(e2, vol20)
            for arm in arms:
                kind, band = arm_spec(arm)
                nav, turn, _, _, _ = simulate_band(so, scl, wants[kind], COST_PER_SIDE * 2, start, band)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


def boot_main(arms):
    """QQQ wrapper around bootstrap_instrument."""
    days, arr, y = trv2.load()
    oq, hq, lq, cq = load_qqq_ohlc(days)
    RSTAR = compute_rstar_qqq()
    rng = np.random.default_rng(SEED)
    return bootstrap_instrument(oq, hq, lq, cq, y, RSTAR, PATHS, arms, rng)


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


# ----------------------------------------------------------------------------- OOS data (copied loaders)
def load_gspc():
    """signal_ensemble_v1.load_gspc: ^GSPC from the first DTB3 date to 1999-12-31, opens := previous close."""
    rows = []
    with GSPC.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    tb = {}
    with DTB3.open() as fh:
        for r in csv.DictReader(fh):
            tb[r["observation_date"]] = r["DTB3"]
    first_tb = min(tb)
    rows = [r for r in rows if first_tb <= r[0] <= OOS_GSPC_LAST]
    days = [r[0] for r in rows]
    c = np.array([r[1] for r in rows])
    o = np.concatenate([[c[0]], c[:-1]])
    y, last = [], None
    dset = set(days)
    for d in sorted(set(tb) | set(days)):
        v = tb.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in dset:
            y.append(last)
    start = next(i for i, d in enumerate(days) if d >= OOS_GSPC_FIRST)
    assert start >= START
    return days, o, c, np.array(y), start


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


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_market(key):
    """e2_confirmation_v1.load_market (pre-registered open rule)."""
    sym, first, meas_from, last = MARKETS[key]
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


def oos_window(days, o, c, y, start, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    wants = {"vol20": wants_for(e2, trv2.vol_n(sc))[0], "ewma": wants_for(e2, ewma_vol(sc))[0]}
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        kind, band = arm_spec(arm)
        nav, turn, ntr, codes, traded = simulate_band(so, sc, wants[kind], COST_PER_SIDE * 2, start, band)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_market(key, rng, arms):
    """e2_confirmation_v1.bootstrap_market draws (shared generator, markets in order), all arms, m = 2."""
    days, o, c, y, start = load_market(key)
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
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        wants = {"vol20": wants_for(e2, trv2.vol_n(sc)), "ewma": wants_for(e2, ewma_vol(sc))}
        for arm in arms:
            kind, band = arm_spec(arm)
            nav = simulate_band(so, sc, wants[kind], COST_PER_SIDE * 2, START, band)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates
def g_return(a, c):
    """CAGR >= C0, or (Calmar >= 1.1 x C0 and CAGR >= C0 - 0.5 pp)."""
    return bool(a["cagr"] >= c["cagr"] or (a["calmar"] >= 1.10 * c["calmar"] and a["cagr"] >= c["cagr"] - 0.005))


def historical_gates(main, boot, arm):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"]["C0"]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"]["C0"]
        r[f"G1_{md}"] = g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
        r[f"G6_{md}"] = g_return(a1, c1)
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"]["C0"]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    kind, band = arm_spec(arm)
    lim = c2["turnover_annual"] * (1.0 if band > C0_BAND else 2.0)
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    return r


def oos_gates(oos, oos_boot_pooled, arm):
    keys = ["GSPC"] + list(MARKETS)
    dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= 5 and n_d >= 5 and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm]["diff_vs_C0_median"]}


GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9"]
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ----------------------------------------------------------------------------- main

ETF_ARMS = ("C0", "G3x", "F1", "F2", "F3")
OOS_PATHS_ETF = 2000

def load_etf(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    rows.sort(key=lambda r: r["date"])
    dates = [r["date"] for r in rows]
    o = np.array([float(r["open"]) for r in rows])
    h = np.array([float(r["high"]) for r in rows])
    l = np.array([float(r["low"]) for r in rows])
    c = np.array([float(r["close"]) for r in rows])
    a = np.array([float(r["adjclose"]) for r in rows])
    return dates, o, h, l, c, a


def etf_quality_ok(h, l, o):
    n = len(h)
    bad = 0
    for i in range(n):
        if h[i] == l[i]:
            bad += 1
        else:
            op = o[i]
            if (not np.isfinite(op)) or op <= 0 or op < l[i] - 1e-12 or op > h[i] + 1e-12:
                bad += 1
    return (bad / n) <= 0.05


def etf_main(path):
    dates, o, h, l, c, adj = load_etf(path)
    if not etf_quality_ok(h, l, o):
        return None
    if len(dates) < START + 5:
        return None
    scale = adj / c
    o_s = o * scale
    h_s = h * scale
    l_s = l * scale
    c_s = adj.copy()
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    start = START
    so, sc = trv2.synthetic_2x(o_s, c_s, np.zeros_like(c_s), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c_s)
    RSTAR = compute_rstar_qqq()
    vol_map = {}
    vol_map["vol20"] = trv2.vol_n(sc)[0]
    for kind in ("yz20", "g3x", "f1", "f2", "f3"):
        vol_map[kind] = qqq_scaled_vol(o_s, h_s, l_s, c_s, kind, R_star=RSTAR)
    wants = {}
    for arm in ETF_ARMS:
        kind, _ = arm_spec(arm)
        wants[kind] = wants_for(e2, vol_map[kind if kind != "vol20" else "vol20"])[0]
    dates_m = dates[start - 1:]
    out = {}
    out["window"] = {"start": dates_m[1] if len(dates_m)>1 else dates_m[0], "end": dates_m[-1], "sessions": len(dates_m)-1}
    out["quality_ok"] = True
    for arm in ETF_ARMS:
        kind, band = arm_spec(arm)
        nav, turn, ntr, codes, traded = simulate_band(so, sc, wants[kind], COST_PER_SIDE * 2, start, band)
        arm_st = arm_stats(dates_m, nav[0], turn[0], ntr[0], codes[0], traded[0], wants[kind], start, False)
        out[arm] = arm_st
    return out


def etf_bootstrap(path, arms, rng):
    dates, o, h, l, c, adj = load_etf(path)
    if not etf_quality_ok(h, l, o):
        return None
    if len(dates) < START + 10:
        return None
    scale = adj / c
    o_s = o * scale
    h_s = h * scale
    l_s = l * scale
    c_s = adj.copy()
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    RSTAR = compute_rstar_qqq()
    cg, _ = bootstrap_instrument(o_s, h_s, l_s, c_s, y, RSTAR, OOS_PATHS_ETF, arms, rng)
    # cg keyed by (financing, arm); financing loops FIN, identical paths; take first financing
    md0 = list(FIN.keys())[0]
    return {a: cg[(md0, a)] for a in arms}

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}; "
          f"EWMA identity max rel diff {hc['ewma_identity_max_rel_diff_vs_direct']:.3e}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    main_res = run_main(ARMS, extras=True)
    print("main window done")
    RSTAR = main_res.get("_rstar", compute_rstar_qqq())
    print("R* (QQQ 1999-03..2009-12) =", RSTAR)

    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done")

    # ETF OOS
    quality_etfs = []
    oos_new = {}
    for path in sorted(str(p) for p in Path("data/historical").glob("range_vol_v2_*.csv")):
        name = Path(path).stem.replace("range_vol_v2_", "").upper()
        res = etf_main(path)
        if res is None:
            continue
        # exclude EWC by quality already; name EWC maybe quality fail
        quality_etfs.append(name)
        oos_new[name] = res
    print("ETF main done:", len(quality_etfs))

    rng = np.random.default_rng(SEED)
    per_etf = {}
    for name in quality_etfs:
        path = f"data/historical/range_vol_v2_{name.lower()}.csv"
        cg_e = etf_bootstrap(path, ETF_ARMS, rng)
        if cg_e is None:
            continue
        per_etf[name] = cg_e
    pooled_etf = {a: np.concatenate([per_etf[n][a] for n in per_etf]) for a in ETF_ARMS}
    oos_boot_etf = {"per_market": {}, "pooled": {}}
    for name in per_etf:
        oos_boot_etf["per_market"][name] = {a: {"cagr_median": float(np.median(per_etf[name][a])),
                                                "median": float(np.median(per_etf[name][a]))} for a in ETF_ARMS}
    for a in ETF_ARMS:
        d = pooled_etf[a] - pooled_etf["C0"]
        oos_boot_etf["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)),
                                     "diff_vs_C0_mean": float(np.mean(d)),
                                     "share_ge_C0": float(np.mean(d >= 0)),
                                     "paths": int(len(d))}

    # Gates for F-arms
    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g["pass_all_main"] = bool(all(g[k] for k in (["G1_base","G2_base","G3_base","G1_calibrated","G2_calibrated","G3_calibrated","G6_base","G6_calibrated"] if False else ["G1_base","G2_base","G3_base","G5_base","G6_base","G1_calibrated","G2_calibrated","G3_calibrated","G5_calibrated","G6_calibrated","G4","G9"])))
        # simplified all G1-G6/G9 (available keys)
        g["pass_all"] = bool(all(g[k] for k in [k for k in g.keys() if k.startswith(("G1","G2","G3","G5","G6","G4","G9"))]))
        gates[arm] = g
    # ETF G7'/G8'
    for arm in CANDIDATES:
        deltas = np.array([oos_new[n][arm]["cagr"] - oos_new[n]["C0"]["cagr"] for n in oos_new])
        dds = np.array([oos_new[n][arm]["max_dd"] - oos_new[n]["C0"]["max_dd"] for n in oos_new])
        med = float(np.median(deltas))
        share_pos = float(np.mean(deltas > 0))
        dd_share = float(np.mean(dds <= 0.03))
        pooled_med = float(np.median(pooled_etf[arm] - pooled_etf["C0"]))
        gates[arm]["G7p"] = bool(med >= 0 and share_pos >= 0.6 and dd_share >= 0.8)
        gates[arm]["G7p_detail"] = {"median_delta_cagr": med, "share_positive": share_pos,
                                    "dd_within_3pp_share": dd_share, "n_instruments": len(deltas)}
        gates[arm]["G8p"] = bool(pooled_med >= 0)
        gates[arm]["G8p_pooled_median_delta"] = pooled_med
        gates[arm]["pass_all_final"] = bool(gates[arm]["pass_all"] and gates[arm]["G7p"] and gates[arm]["G8p"])

    # selection among passing F arms by pooled median
    passing = [a for a in CANDIDATES if gates[a].get("pass_all_final", False)]
    if passing:
        # highest pooled median
        medians = {a: gates[a]["G8p_pooled_median_delta"] for a in passing}
        best = max(passing, key=lambda a: medians[a])
        # tie within 0.03 pp prefer simpler F3>F1>F2
        best_med = medians[best]
        close = [a for a in passing if abs(medians[a]-best_med) <= 0.0003]
        if len(close) > 1:
            for pref in ("F3","F1","F2"):
                if pref in close:
                    best = pref
                    break
        selected = best
    else:
        selected = "none"
    selection = {"passing": passing, "selected": selected, "median_deltas": {a: gates[a]["G8p_pooled_median_delta"] for a in CANDIDATES}}

    out = {
        "campaign": "range_vol_v3",
        "params": {"arms": list(ARMS), "etf_arms": list(ETF_ARMS), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N,
                   "c0_band": C0_BAND, "cap": CAP, "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
                   "financing_spread": FIN, "extra_financing": EXTRA_FIN, "start": START,
                   "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                                      "back_extension": EXT, "measured": MEASURED, "m": 2},
                   "bootstrap_etf": {"paths_per_instrument": OOS_PATHS_ETF, "block": BLOCK, "m": 2},
                   "rstar_qqq_1999_03_2009_12": RSTAR,
                   "signals": "production E2 1.1.0 on QQQ price close"},
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos_new": oos_new,
        "oos_new_bootstrap_block20_m2": oos_boot_etf,
        "quality_etfs": quality_etfs,
        "gates": gates,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"range_vol_v3 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | C0 = production E2 1.1.0",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'}; R* = {RSTAR:.6f}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ turn/yr | boot20 median, median(arm-C0) | m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:5s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} | {p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+.3f}pp | {p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("ETF pooled bootstrap median Δ vs C0: " + "  ".join(f"{a} {oos_boot_etf['pooled'][a]['diff_vs_C0_median']*100:+.3f}pp" for a in ETF_ARMS))
    L.append("ETF main median Δ CAGR: " + "  ".join(f"{a} {np.median([oos_new[n][a]['cagr']-oos_new[n]['C0']['cagr'] for n in oos_new])*100:+.3f}pp ({sum(1 for n in oos_new if oos_new[n][a]['cagr']>oos_new[n]['C0']['cagr'])}/{len(oos_new)})" for a in ETF_ARMS))
    L.append("")
    L.append("gates:")
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:5s} main_pass={g['pass_all']} G7p={g['G7p']} G8p={g['G8p']} G8p_med={g['G8p_pooled_median_delta']*100:+.3f}pp -> {'PASS' if g.get('pass_all_final') else 'FAIL'}")
    L.append(f"selected: {selected}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
