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
DEVIATION = HERE / "E2_INTEGRATION_DEVIATION.json"
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

ARMS = ("C0", "G1x", "G2x", "G3x", "G4x")
CANDIDATES = ("G1x", "G2x", "G3x", "G4x")


def arm_spec(arm):
    """(vol kind, resize band) of an arm. All range arms use the production 0.5 pp band."""
    if arm == "C0":
        return "vol20", C0_BAND
    if arm == "G1x":
        return "yz", C0_BAND
    if arm == "G2x":
        return "gk_gap", C0_BAND
    if arm == "G3x":
        return "park_scaled", C0_BAND
    if arm == "G4x":
        return "avg", C0_BAND
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


# ----------------------------------------------------------------------------- range volatility estimators (on QQQ adjusted OHLC)
LN2 = np.log(2.0)
K_YZ = None  # placeholder; k depends on n

def _rmean(a, n):
    a = np.atleast_1d(a).astype(float)
    # handle shape (...,T): operate last axis
    a = np.atleast_2d(a) if a.ndim == 1 else a
    # generic last-axis
    P, T = a.shape[0], a.shape[-1]
    a2 = a.reshape(-1, T)
    PP = a2.shape[0]
    z = np.zeros((PP, 1))
    cum = np.concatenate([z, np.cumsum(a2, axis=1)], axis=1)
    out = np.full((PP, T), np.nan)
    out[:, n-1:] = (cum[:, n:] - cum[:, :-n]) / n
    return out.reshape(a.shape)

def _rvar(x, n):
    x = np.atleast_1d(x).astype(float)
    orig_shape = x.shape
    x = np.atleast_2d(x) if x.ndim == 1 else x
    P = x.shape[0]; T = x.shape[-1]
    x2 = x.reshape(-1, T)
    PP = x2.shape[0]
    z = np.zeros((PP, 1))
    s = np.concatenate([z, np.cumsum(x2, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(x2 * x2, axis=1)], axis=1)
    out = np.full((PP, T), np.nan)
    sx = s[:, n:] - s[:, :-n]
    sx2 = s2[:, n:] - s2[:, :-n]
    var = (sx2 - sx * sx / n) / (n - 1)
    var = np.where(var < 0, 0.0, var)
    out[:, n-1:] = var
    return out.reshape(orig_shape)

def range_vol_parkinson(high, low, n=20):
    """Parkinson volatility, annualized, window n."""
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    daily = (np.log(high / low) ** 2) / (4.0 * LN2)
    return np.sqrt(252.0 * _rmean(daily, n))

def range_vol_gk(high, low, opn, close, n=20):
    """Garman-Klass volatility, annualized, window n (intraday)."""
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    hl = np.log(high / low)
    co = np.log(close / opn)
    daily = 0.5 * hl * hl - (2.0 * LN2 - 1.0) * co * co
    daily = np.where(daily < 0, 0.0, daily)
    return np.sqrt(252.0 * _rmean(daily, n))

def range_vol_yz(high, low, opn, close, n=20):
    """Yang-Zhang volatility, annualized, window n (includes overnight gaps)."""
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    P, T = close.shape
    # price levels per path; overnight return log(open_t / close_{t-1})
    on = np.full((P, T), np.nan)
    on[:, 1:] = np.log(opn[:, 1:] / close[:, :-1])
    oc = np.log(close / opn)
    rs = np.log(high / close) * np.log(high / opn) + np.log(low / close) * np.log(low / opn)
    var_on = _rvar(on, n)
    var_oc = _rvar(oc, n)
    mean_rs = _rmean(rs, n)
    k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0))
    var = var_on + k * var_oc + (1.0 - k) * mean_rs
    var = np.where(var < 0, 0.0, var)
    return np.sqrt(252.0 * var)

def range_vol_gk_gap(high, low, opn, close, n=20):
    """Garman-Klass intraday vol + overnight gap variance (annualized)."""
    high = np.atleast_2d(high); low = np.atleast_2d(low)
    opn = np.atleast_2d(opn); close = np.atleast_2d(close)
    gk = range_vol_gk(high, low, opn, close, n)
    P, T = close.shape
    on = np.full((P, T), np.nan)
    on[:, 1:] = np.log(opn[:, 1:] / close[:, :-1])
    var_on = _rvar(on, n)
    # combine variances: gk var + overnight var
    gk_var = (gk ** 2) / 252.0
    total_var = gk_var + var_on
    total_var = np.where(total_var < 0, 0.0, total_var)
    return np.sqrt(252.0 * total_var)



def range_vol_park_scaled(high, low, close, n=20):
    """Parkinson 20-session vol scaled by expanding (pre-date) ratio of close-to-close to
    Parkinson volatility (no look-ahead). Returns annualized QQQ vol."""
    high = np.atleast_2d(high); low = np.atleast_2d(low); close = np.atleast_2d(close)
    P, T = close.shape
    pv20 = range_vol_parkinson(high, low, n)  # [P,T]
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
    scaled_prev = np.ones((P, T))
    for t in range(2, T):
        cnt = t  # returns available through t-1
        sr = C_r[:, t]
        sr2 = C_r2[:, t]
        sp = C_p[:, t]
        var_cc = (sr2 - sr * sr / cnt) / (cnt - 1)
        var_cc = np.where(var_cc < 0, 0.0, var_cc)
        cc_vol = np.sqrt(252.0 * var_cc)
        park_mean = sp / cnt
        park_vol = np.sqrt(252.0 * park_mean)
        ratio = np.where(park_vol > 0, cc_vol / park_vol, 1.0)
        ratio_prev[:, t] = ratio
    out = pv20 * ratio_prev
    return out

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
    # QQQ vol (annualized) -> convert to QLD basis by x2
    cc_vol = trv2.vol_n(qa_c[np.newaxis, :])[0] * 2.0
    yz_vol = range_vol_yz(qa_h[np.newaxis, :], qa_l[np.newaxis, :], qa_o[np.newaxis, :], qa_c[np.newaxis, :])[0] * 2.0
    gk_gap_vol = range_vol_gk_gap(qa_h[np.newaxis, :], qa_l[np.newaxis, :], qa_o[np.newaxis, :], qa_c[np.newaxis, :])[0] * 2.0
    park_scaled_vol = range_vol_park_scaled(qa_h[np.newaxis, :], qa_l[np.newaxis, :], qa_c[np.newaxis, :])[0] * 2.0
    avg_vol = (cc_vol + yz_vol) / 2.0
    vmap = {
        "vol20": trv2.vol_n(c)[0],    # C0 on synth QLD* close
        "yz": yz_vol,
        "gk_gap": gk_gap_vol,
        "park_scaled": park_scaled_vol,
        "avg": avg_vol,
    }
    return days, arr, o, c, e2, vmap

def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, vmap = main_vol_arrays(spread)
        dates = days[START - 1:]
        wants = {k: wants_for(e2, v)[0] for k, v in vmap.items()}
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
    days, arr, o, c, e2, vmap = main_vol_arrays(0.0)
    w = wants_for(e2, vmap["vol20"])
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))
    ok = ok_dev and sim_equal
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "pass": bool(ok)}


def boot_main(arms):
    """Stationary block bootstrap. Carries resampled QQQ OHLC for range vol estimators."""
    _, arr, y = trv2.load()
    q = arr["QQQ"]
    # raw high/low aligned to trv2 days
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
            yz_vol = range_vol_yz(qh, ql, qo, qc) * 2.0
            gk_gap_vol = range_vol_gk_gap(qh, ql, qo, qc) * 2.0
            park_scaled_vol = range_vol_park_scaled(qh, ql, qc) * 2.0
            cc_vol = trv2.vol_n(qc) * 2.0
            avg_vol = (cc_vol + yz_vol) / 2.0
            wants = {
                "vol20": wants_for(e2, trv2.vol_n(scl)),
                "yz": wants_for(e2, yz_vol),
                "gk_gap": wants_for(e2, gk_gap_vol),
                "park_scaled": wants_for(e2, park_scaled_vol),
                "avg": wants_for(e2, avg_vol),
            }
            for arm in arms:
                kind, band = arm_spec(arm)
                nav, turn, _, _, _ = simulate_band(so, scl, wants[kind], COST_PER_SIDE * 2, start_bs, band)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


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


def _load_market_index(key):
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
    hh = np.array([ (None if r[1] is None else float(r[1])) for r in rows ], dtype=object)
    ll = np.array([ (None if r[2] is None else float(r[2])) for r in rows ], dtype=object)
    o = np.empty_like(c)
    for k, (d, op, h, lo, cl) in enumerate(rows):
        o[k] = cl if k == 0 else (c[k - 1] if placeholder_open(op, h, lo, cl) else op)
    # replace None highs/lows with close for non-range use only
    hh = np.where(hh == None, c, hh).astype(float)
    ll = np.where(ll == None, c, ll).astype(float)
    d3, tb = load_rates()
    y = rate_on(days, d3, tb)
    start = START if meas_from is None else next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START
    return days, o, hh, ll, c, y, start


def _market_quality_ok(high, low, opn, close):
    bad = 0; n = 0
    for h, lo, o, cl in zip(high, low, opn, close):
        n += 1
        if h is None or lo is None or h == lo or o is None:
            bad += 1
    return (bad / max(1, n)) <= 0.05


def load_etf(key):
    """Load a single ETF (SPY/DIA/IWM) from downloaded Yahoo OHLC CSV."""
    path = ROOT / "data/historical" / f"range_vol_v1_{key.lower()}.csv"
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
    assert len(days_all) if False else True
    assert start <= len(dates) - 1
    return days, o, h, l, c, np.array(y), start

def load_market(key):
    if key in ("SPY", "DIA", "IWM"):
        return load_etf(key)
    return _load_market_index(key)

def oos_window(days, o, high, low, c, y, start, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    cc_vol = trv2.vol_n(c[np.newaxis, :])[0] * 2.0
    yz_vol = range_vol_yz(high[np.newaxis, :], low[np.newaxis, :], o[np.newaxis, :], c[np.newaxis, :])[0] * 2.0
    gk_gap_vol = range_vol_gk_gap(high[np.newaxis, :], low[np.newaxis, :], o[np.newaxis, :], c[np.newaxis, :])[0] * 2.0
    park_vol = range_vol_parkinson(high[np.newaxis, :], low[np.newaxis, :], 20)[0] * 2.0
    # park_scaled not computable expanding simply here; use parkinson as proxy (small sample note)
    avg_vol = (cc_vol + yz_vol) / 2.0
    wants = {
        "vol20": wants_for(e2, trv2.vol_n(sc))[0],
        "yz": wants_for(e2, yz_vol)[0],
        "gk_gap": wants_for(e2, gk_gap_vol)[0],
        "park_scaled": wants_for(e2, park_vol)[0],
        "avg": wants_for(e2, avg_vol)[0],
    }
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        kind, band = arm_spec(arm)
        if kind not in wants:
            continue
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
    days, o, high, low, c, y, start = load_market(key)
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
        src_h = high[idx]; src_c = c[idx]; src_l = low[idx]
        ratio_h = src_h / src_c; ratio_l = src_l / src_c
        qh = np.concatenate([np.ones((P, 1)), cc[:, 1:] * ratio_h], axis=1)
        ql = np.concatenate([np.ones((P, 1)), cc[:, 1:] * ratio_l], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        yz_vol = range_vol_yz(qh, ql, oo, cc) * 2.0
        gk_gap_vol = range_vol_gk_gap(qh, ql, oo, cc) * 2.0
        park_vol = range_vol_parkinson(qh, ql, 20) * 2.0
        avg_vol = (trv2.vol_n(cc) * 2.0 + yz_vol) / 2.0
        wants = {"vol20": wants_for(e2, trv2.vol_n(sc)),
                 "yz": wants_for(e2, yz_vol),
                 "gk_gap": wants_for(e2, gk_gap_vol),
                 "park_scaled": wants_for(e2, park_vol),
                 "avg": wants_for(e2, avg_vol)}
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
    keys = list(oos.keys()) if oos else []
    if keys:
        dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
        dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
        med = float(np.median(list(dc.values())))
        n_c = sum(1 for v in dc.values() if v >= -0.005)
        n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
        g7 = bool(med >= 0 and n_c == len(keys) and n_d >= len(keys)-1 and max(dd.values()) <= 0.08 + 1e-12)
    else:
        dc = {}; dd = {}; med = 0.0; n_c = 0; n_d = 0
        g7 = False
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_available": len(keys),
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": (max(dd.values()) if dd else 0.0)},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm]["diff_vs_C0_median"]}


GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9"]
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ----------------------------------------------------------------------------- main
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

    main_res = run_main(ARMS, extras=True)
    print("main window done")
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done")
    ALL_OOS = list(MARKETS.keys()) + ['SPY','DIA','IWM']
    oos_keys = []
    for k in ALL_OOS:
        days, o, high, low, c, y, start = load_market(k)
        if _market_quality_ok(high, low, o, c):
            oos_keys.append(k)
    print("OOS passing markets:", oos_keys)
    oos = {}
    for k in oos_keys:
        oos[k] = oos_window(*load_market(k), ARMS)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in oos_keys}
    pooled = {a: (np.concatenate([per[k][a] for k in oos_keys]) if oos_keys else np.array([])) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in oos_keys:
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        if len(pooled[a]):
            d = pooled[a] - pooled["C0"]
            oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                     "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
        else:
            oos_boot["pooled"][a] = {"diff_vs_C0_median": 0.0, "diff_vs_C0_mean": 0.0, "share_ge_C0": 0.0, "paths": 0}
    print("OOS done")

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g
    h2 = main_res["base"]["m2"]
    key = lambda a: (h2[a]["cagr"], h2[a]["calmar"])
    passing = [a for a in CANDIDATES if gates[a]["pass_all"]]
    sel = max(passing, key=key) if passing else "none"
    selection = {"selected": sel, "passing": passing,
                 "rule": "selected = highest base m=2 CAGR among arms passing all gates (tie Calmar); none -> C0"}

    out = {
        "campaign": "ewma_band_v1",
        "params": {"arms": list(ARMS), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "ewma_lambda": LAMBDA,
                   "ewma_window_returns": EWMA_N, "ewma_seed_returns": EWMA_SEED, "c0_band": C0_BAND,
                   "bands": BANDS, "cap": CAP, "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
                   "financing_spread": FIN, "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK,
                                                               "warmup": WARM, "back_extension": EXT,
                                                               "measured": MEASURED, "m": 2},
                   "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                                     "returns_per_path": OOS_BOOT_L, "m": 2},
                   "signals": "production E2 1.1.0 on price closes: SMA200, 252-session price return > 0, "
                              "close > 126-session max/min midpoint; 2 of 3"},
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"ewma_band_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | C0 = production E2 1.1.0",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} (max |diff| "
         f"{max(abs(v['diff_pp']) for v in hc['deviation_checks'].values()):.6f} pp vs e2_integration_v1 production E2; "
         f"band sim == simulate_multi {hc['band_simulator_equals_simulate_multi']})", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot20 median, median(arm-C0) | m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:5s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+.3f}pp | {p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("trades (base, m=2): arm | full window: round trips/yr, resizes/yr, sells/yr, turnover/yr "
             "(entries+exits / resizes) | 2017+: round trips/yr, sells/yr, turnover/yr")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        e, f = a["events_full"], a["events_2017_on"]
        L.append(f"  {arm:5s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{e['turnover_per_year']:5.2f} ({e['turnover_from_entries_exits_per_year']:4.2f} / "
                 f"{e['turnover_from_resizes_per_year']:4.2f}) | {f['round_trips_per_year']:5.2f} "
                 f"{f['sells_per_year']:6.2f} {f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap (M1..M5, 10,000 paths) median(arm-C0): " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_C0_median'] * 100:+.3f}pp" for a in CANDIDATES))
    L.append("")
    L.append("gates (" + " ".join(GATE_KEYS) + "):")
    for arm in CANDIDATES:
        g = gates[arm]
        pl = f" plateau={'P' if g.get('plateau') else 'F'}" if "plateau" in g else ""
        L.append(f"  {arm:5s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f"{pl} -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append(f"selected: {sel}; passing {passing}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
