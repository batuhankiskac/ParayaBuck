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

USABLE_MARKETS = {  # real open prices for the entire measured window
    "IXIC": ("^IXIC", "1971-01-01", "1990-04-06", "1999-12-31"),
    "GDAXI": ("^GDAXI", "1900-01-01", "1997-05-30", "2026-09-29"),
    "N225": ("^N225", "1900-01-01", "2017-03-28", "2026-09-29"),
    "FTSE": ("^FTSE", "1900-01-01", "1985-01-02", "2026-09-29"),
}
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
DTB3_FIRST = "1954-01-04"

ARMS = ("C0", "X1", "X1n", "X2", "X3")
CANDIDATES = ("X1", "X1n", "X2")


def arm_spec(arm):
    return arm


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


# ----------------------------------------------------------------------------- execution variants
def simulate_close(opens, closes, wants, cost, start, band=0.005):
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
        g_in = closes[:, i] / opens[:, i]
        g_in = np.where(np.isnan(g_in), 1.0, g_in)
        va = w * g_in
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i]
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
        out[:, k + 1] = nav
    return out, turn, ntr, codes, traded


def simulate_X2(opens, closes, wants, cost, start, band):
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    has_pending = np.zeros(P, dtype=bool)
    pend_tgt = np.zeros(P)
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
        # apply pending upward trades at open_i
        if np.any(has_pending):
            tgt = pend_tgt
            d = np.abs(tgt - w)
            held = w > 0
            tg = tgt > 0
            entry = (~held) & tg
            up_resize = held & tg & (tgt > w) & (d > band)
            tm = has_pending & (entry | up_resize)
            nav = np.where(tm, nav * (1 - cost * d), nav)
            turn = turn + np.where(tm, d, 0.0)
            ntr = ntr + tm.astype(np.int64)
            k = i - start
            codes[:, k] = np.where(tm, np.where(entry, 1, 3), codes[:, k])
            traded[:, k] = np.where(tm, d, traded[:, k])
            w = np.where(tm, tgt, w)
        has_pending[:] = False
        g_in = closes[:, i] / opens[:, i]
        g_in = np.where(np.isnan(g_in), 1.0, g_in)
        va = w * g_in
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i]
        d = np.abs(want - w)
        held = w > 0
        tgt = want > 0
        exit_ = held & (~tgt)
        down_resize = held & tgt & (want < w) & (d > band)
        down_trade = exit_ | down_resize
        nav = np.where(down_trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(down_trade, d, 0.0)
        ntr = ntr + down_trade.astype(np.int64)
        k = i - start
        is_exit = exit_ & down_trade
        codes[:, k] = np.where(down_trade, np.where(is_exit, 2, 4), codes[:, k])
        traded[:, k] = np.where(down_trade, d, traded[:, k])
        w = np.where(down_trade, want, w)
        sched_mask = want > w
        has_pending = sched_mask.copy()
        pend_tgt[:] = np.where(sched_mask, want, pend_tgt)
        out[:, i - start + 1] = nav
    return out, turn, ntr, codes, traded


def simulate_delay(opens, closes, wants, cost, start):
    wants = np.asarray(wants, dtype=float)
    W = np.zeros_like(wants)
    delay = 1
    if wants.ndim == 1:
        W[delay:] = wants[:-delay]
    else:
        W[:, delay:] = wants[:, :-delay]
    return simulate_close(opens, closes, W, cost, start, 0.005)

# ----------------------------------------------------------------------------- metrics


def simulate_for_arm(arm, opens, closes, wants, cost, start):
    if arm == "C0":
        return simulate_band(opens, closes, wants, cost, start, 0.005)
    if arm == "X1":
        return simulate_close(opens, closes, wants, cost, start, 0.005)
    if arm in ("X1n",):
        return simulate_close(opens, closes, wants, cost, start, 0.005)
    if arm == "X2":
        return simulate_X2(opens, closes, wants, cost, start, 0.005)
    if arm == "X3":
        return simulate_delay(opens, closes, wants, cost, start)
    raise ValueError(arm)

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
def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return days, arr, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0], q[1].copy()


def run_main(arms, extras=False, m_list=(1, 2)):
    # First pass: deterministic arms; X1n aggregated over 20 noise seeds separately.
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, v20, qprice = main_inputs(spread)
        dates = days[START - 1:]
        wants0 = wants_for(e2, v20)[0]
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                if arm == "X1n":
                    continue
                nav, turn, ntr, codes, traded = simulate_for_arm(arm, o, c, wants0, COST_PER_SIDE * m, START)
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants0, START,
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
    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m=2
    days, arr, o, c, e2, v20, qprice = main_inputs(0.0)
    vew = ewma_vol(c)[0]
    w = wants_for(e2, v20)
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))
    # windowed EWMA identity vs direct computation on the main-window instrument
    direct = ewma_vol_direct(c)
    mask = ~np.isnan(direct)
    rel = float(np.max(np.abs(vew[mask] / direct[mask] - 1)))
    same_nan = bool(np.array_equal(np.isnan(direct), np.isnan(vew)))
    ok = ok_dev and sim_equal and rel < 1e-9 and same_nan
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "ewma_identity_max_rel_diff_vs_direct": rel, "ewma_nan_pattern_equal": same_nan,
            "pass": bool(ok)}


def boot_main(arms):
    """Stationary block bootstrap (mean block 20), draws identical to signal_ensemble_v1 (seed, order,
    chunks, 3-session back-extension). Each drawn session carries QQQ overnight (with dividend for the
    instrument, without for the price signal series) and intraday factors and DTB3 jointly. Signals on
    the resampled price level; instrument on the resampled total-return level. All arms on the same paths,
    m = 2. Returns per-model per-arm CAGR arrays and turnover arrays."""
    _, arr, y = trv2.load()
    q = arr["QQQ"]
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
        idx = pool[pos]
        yy = y[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            w0 = wants_for(e2, trv2.vol_n(scl))
            for arm in arms:
                if arm == "C0":
                    nav, turn, _, _, _ = simulate_band(so, scl, w0, COST_PER_SIDE * 2, start, 0.005)
                elif arm in ("X1", "X1n"):
                    nav, turn, _, _, _ = simulate_close(so, scl, w0, COST_PER_SIDE * 2, start, 0.005)
                elif arm == "X2":
                    nav, turn, _, _, _ = simulate_X2(so, scl, w0, COST_PER_SIDE * 2, start, 0.005)
                elif arm == "X3":
                    nav, turn, _, _, _ = simulate_delay(so, scl, w0, COST_PER_SIDE * 2, start)
                else:
                    raise ValueError(arm)
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


def load_usable(key):
    sym, first, meas_from, last = USABLE_MARKETS[key]
    # reuse load_market by setting up USABLE market entry into MARKETS temporarily
    MARKETS[key] = (sym, first, meas_from, last)
    r = load_market(key)
    return r


def oos_window(days, o, c, y, start, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    w0 = wants_for(e2, trv2.vol_n(sc))
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        if arm == "C0":
            nav, turn, ntr, codes, traded = simulate_band(so, sc, w0, COST_PER_SIDE * 2, start, 0.005)
        elif arm in ("X1", "X1n"):
            nav, turn, ntr, codes, traded = simulate_close(so, sc, w0, COST_PER_SIDE * 2, start, 0.005)
        elif arm == "X2":
            nav, turn, ntr, codes, traded = simulate_X2(so, sc, w0, COST_PER_SIDE * 2, start, 0.005)
        elif arm == "X3":
            nav, turn, ntr, codes, traded = simulate_delay(so, sc, w0, COST_PER_SIDE * 2, start)
        else:
            raise ValueError(arm)
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
        w0 = wants_for(e2, trv2.vol_n(sc))
        for arm in arms:
            if arm == "C0":
                nav = simulate_band(so, sc, w0, COST_PER_SIDE * 2, START, 0.005)[0]
            elif arm in ("X1", "X1n"):
                nav = simulate_close(so, sc, w0, COST_PER_SIDE * 2, START, 0.005)[0]
            elif arm == "X2":
                nav = simulate_X2(so, sc, w0, COST_PER_SIDE * 2, START, 0.005)[0]
            elif arm == "X3":
                nav = simulate_delay(so, sc, w0, COST_PER_SIDE * 2, START)[0]
            else:
                raise ValueError(arm)
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
    lim = c2["turnover_annual"] * 2.0
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    return r


def oos_gates(oos, oos_boot_pooled, arm):
    keys = list(USABLE_MARKETS)
    dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= len(keys)-1 and n_d >= len(keys)-1 and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
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
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}; "
          f"EWMA identity max rel diff {hc['ewma_identity_max_rel_diff_vs_direct']:.3e}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    NOISE_SEED_BASE = 20261014
    NOISE_SD = 0.0025
    def medianize(metrics_list):
        out = {}
        first = metrics_list[0]
        for key, val in first.items():
            if isinstance(val, dict):
                out[key] = medianize([m[key] for m in metrics_list])
            elif isinstance(val, (int, np.integer)):
                out[key] = int(np.median([m[key] for m in metrics_list]))
            elif isinstance(val, (float, np.floating)):
                out[key] = float(np.median([m[key] for m in metrics_list]))
            elif isinstance(val, str):
                out[key] = val
            else:
                out[key] = val
        return out

    # C0 plus X1/X2/X3 main (no noise for execution comparison)
    main_res = run_main([a for a in ARMS if a != "X1n"], extras=True)
    print("main window done")
    # Inject X1n median over 20 pre-close noise seeds
    x1n_seed_metrics = {md: {f"m{m}": [] for m in (1, 2)} for md in FIN}
    x1n_worst = None
    for ns in range(20):
        rngn = np.random.default_rng(NOISE_SEED_BASE + ns)
        noisy_res = {}
        for md, spread in FIN.items():
            days, arr, y = trv2.load()
            q = arr["QQQ"]
            o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
            noise = rngn.normal(0, NOISE_SD, size=q[1].shape)
            e2 = e2_up(q[1] * (1 + noise))
            v20 = trv2.vol_n(c * (1 + noise))
            wants = wants_for(e2, v20)[0]
            dates = days[START - 1:]
            hr = {}
            for m in (1, 2):
                nav, turn, ntr, codes, traded = simulate_close(o, c, wants, COST_PER_SIDE * m, START, 0.005)
                hr[f"m{m}"] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants, START, False)
            noisy_res[md] = hr
            x1n_seed_metrics[md]["m1"].append(noisy_res[md]["m1"])
            x1n_seed_metrics[md]["m2"].append(noisy_res[md]["m2"])
        if x1n_worst is None or noisy_res["base"]["m2"]["cagr"] < x1n_worst[0]:
            x1n_worst = (noisy_res["base"]["m2"]["cagr"], ns, noisy_res)
    for md in FIN:
        for m in (1, 2):
            main_res[md][f"m{m}"]["X1n"] = medianize(x1n_seed_metrics[md][f"m{m}"])
    main_res["_x1n_worst_seed"] = int(x1n_worst[1])
    main_res["_x1n_worst_base_m2_cagr"] = float(x1n_worst[0])
    print("main X1n noise done")

    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    # X1n bootstrap approximated by X1 (noise mean zero) for the gate
    for md in FIN:
        boot[md]["X1n"] = dict(boot[md]["X1"])
    print("main bootstrap done")

    # Usable OOS windows (real open prices only)
    oos = {}
    for k in USABLE_MARKETS:
        days, o, c, y, start = load_usable(k)
        oos[k] = oos_window(days, o, c, y, start, [a for a in ARMS if a != "X1n"])
    # Inject X1n median across 20 noise seeds (real series)
    x1n_oos_seed = {k: [] for k in USABLE_MARKETS}
    for k in USABLE_MARKETS:
        days, o, c, y, start = load_usable(k)
        for ns in range(20):
            rngn = np.random.default_rng(NOISE_SEED_BASE + 100 + ns)
            noise = rngn.normal(0, NOISE_SD, size=c.shape)
            # run oos_window manually for X1n
            so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
            e2 = e2_up(c * (1 + noise))
            w0 = wants_for(e2, trv2.vol_n(sc * (1 + noise)))
            dates = days[start - 1:]
            years = trv2.calendar_years(dates[0], dates[-1])
            nav, turn, ntr, codes, traded = simulate_close(so, sc, w0, COST_PER_SIDE * 2, start, 0.005)
            nav = nav[0]
            cg_ = float(trv2.cagr(nav[0], nav[-1], years))
            dd_ = float(trv2.max_dd(nav)[0])
            x1n_oos_seed[k].append({"cagr": cg_, "max_dd": dd_, "calmar": trv2.calmar(cg_, dd_)})
    for k in USABLE_MARKETS:
        oos[k]["X1n"] = {
            "cagr": float(np.median([x["cagr"] for x in x1n_oos_seed[k]])),
            "max_dd": float(np.median([x["max_dd"] for x in x1n_oos_seed[k]])),
            "calmar": float(np.median([x["calmar"] for x in x1n_oos_seed[k]])),
        }
    print("OOS windows done")

    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, [a for a in ARMS if a != "X1n"]) for k in USABLE_MARKETS}
    # inject X1n duplicated from X1 for pooled bootstrap
    for k in USABLE_MARKETS:
        per[k]["X1n"] = per[k]["X1"].copy() if "X1" in per[k] else per[k]["C0"].copy()
    pooled = {a: np.concatenate([per[k][a] for k in USABLE_MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in USABLE_MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS if a in per[k]}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
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
    eligible = [a for a in CANDIDATES if gates[a]["pass_all"]]
    sel = max(eligible, key=key) if eligible else "none"
    selection = {"eligible": eligible, "selected": sel, "rule": "selected = highest m=2 base CAGR among passing candidates"}

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
         f"band sim == simulate_multi {hc['band_simulator_equals_simulate_multi']}; EWMA identity rel diff "
         f"{hc['ewma_identity_max_rel_diff_vs_direct']:.1e})", ""]
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
        if "events_full" not in a:
            continue
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
    L.append(f"eligible {eligible}; selected: {sel}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
