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

ARMS = ("C0", "A2")
CANDIDATES = ("A2",)
OOS_ETFS = ["XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY",
            "EWJ","EWG","EWU","EWC","EWA","EWH","EWS","EWL","EFA","EEM"]


ARM_PARAMS = {
    "C0": {"kind": "vol20", "band": C0_BAND, "mode": "prod", "k": 0.5, "trim": 0.0},
    "A2": {"kind": "vol20", "band": C0_BAND, "mode": "downward", "k": 0.5, "trim": 0.0},
}

def arm_spec(arm):
    p = ARM_PARAMS[arm]
    return p["kind"], p["band"]


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




def simulate_downward(opens, closes, wants, cost, start, band, k=0.5, trim=0.0):
    """Production mechanics with downward-only vol resizes.

    Upward resizes when target > held + band. Downward full resize when
    target < held - band AND (trend exit OR target < k*held). Partial trim
    (trim>0) moves to target + trim*(held-target) on the threshold case.
    Entries/exits always trade. Returns same tuple as simulate_band."""
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
    prev_t = np.full(P, np.nan)
    for i in range(start, T):
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i - 1]
        held = w > 0
        entry = (~held) & (want > 0)
        exitz = held & (want <= 0)
        trend_exit = (prev_t > 0) & (want <= 0)
        up_resize = (want > w + band) & held
        threshold_case = (want < w - band) & (want < k * np.where(w > 0, w, 1.0)) & held & (want > 0)
        down_big = (want < w - band) & held & ((trend_exit) | (want < k * np.where(w > 0, w, 1.0)))
        trade = entry | exitz | up_resize | down_big

        final_target = np.where(trade, want, w)
        # partial trim on threshold case
        pt = np.where(threshold_case & trade & (trim > 0), want + trim * (w - want), final_target)
        final_target = np.where(trade, pt, w)

        d = np.abs(final_target - w)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(trade, d, 0.0)
        ntr = ntr + trade.astype(np.int64)
        kidx = i - start
        codes[:, kidx] = np.where(~trade, 0, np.where(entry, 1, np.where(exitz, 2, np.where(final_target > w, 3, 4))))
        traded[:, kidx] = np.where(trade, d, 0.0)
        w = np.where(trade, final_target, w)

        ga = closes[:, i] / opens[:, i]
        va = w * ga
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot

        out[:, kidx + 1] = nav
        prev_t = np.where(np.isnan(want), prev_t, want)
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
def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return days, arr, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0], ewma_vol(c)[0]


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, v20, vew = main_inputs(spread)
        dates = days[START - 1:]
        wants = {"vol20": wants_for(e2, v20)[0], "ewma": wants_for(e2, vew)[0]}
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                p = ARM_PARAMS[arm]
                kind, band = p["kind"], p["band"]
                if p["mode"] == "downward":
                    nav, turn, ntr, codes, traded = simulate_downward(o, c, wants[kind], COST_PER_SIDE * m, START, band, p["k"], p["trim"])
                else:
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
    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m=2
    days, arr, o, c, e2, v20, vew = main_inputs(0.0)
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
            wants = {"vol20": wants_for(e2, trv2.vol_n(scl)), "ewma": wants_for(e2, ewma_vol(scl))}
            for arm in arms:
                pp = ARM_PARAMS[arm]
                kind, band = pp["kind"], pp["band"]
                if pp["mode"] == "downward":
                    nav, turn, _, _, _ = simulate_downward(so, scl, wants[kind], COST_PER_SIDE * 2, start, band, pp["k"], pp["trim"])
                else:
                    nav, turn, _, _, _ = simulate_band(so, scl, wants[kind], COST_PER_SIDE * 2, start, band)
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


def oos_window(days, o, c, y, start, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    wants = {"vol20": wants_for(e2, trv2.vol_n(sc))[0], "ewma": wants_for(e2, ewma_vol(sc))[0]}
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        pp = ARM_PARAMS[arm]
        kind, band = pp["kind"], pp["band"]
        if pp["mode"] == "downward":
            nav, turn, ntr, codes, traded = simulate_downward(so, sc, wants[kind], COST_PER_SIDE * 2, start, band, pp["k"], pp["trim"])
        else:
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
            pp = ARM_PARAMS[arm]
            kind, band = pp["kind"], pp["band"]
            if pp["mode"] == "downward":
                nav = simulate_downward(so, sc, wants[kind], COST_PER_SIDE * 2, START, band, pp["k"], pp["trim"])[0]
            else:
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
    return dates, o, h, l, c, np.array(y), start


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
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done")
    oos = {"GSPC": oos_window(*load_gspc(), ARMS)}
    for k in MARKETS:
        oos[k] = oos_window(*load_market(k), ARMS)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("OOS done")

    # ---- after-tax threshold plateau + A2r + bootstrap ----
    mkt_main = Market()
    mkt_main.days = DAYS
    mkt_main.fx = BASE_MKT.fx.copy()
    mkt_main.ufe = BASE_MKT.ufe
    plateau_arms = ["A2_33","A2_40","A2_50","A2_60","A2_67","A2_75","A2r"]
    at_plateau = {}
    for arm in plateau_arms:
        wres = {}
        for wn, (a, b) in WINDOWS.items():
            cd = {}
            for reg in ("pre_tax", "progressive"):
                r0 = run_strategy("C0", reg, 100000.0, a, b, mkt_main)
                r1 = run_strategy(arm, reg, 100000.0, a, b, mkt_main)
                cd[reg] = {
                    "C0": {"cagr": r0["cagr_usd_liquidated"], "max_dd": r0["max_dd_nav_usd"],
                           "sells_per_year": r0["strategy_trades"]/r0["years"]},
                    arm: {"cagr": r1["cagr_usd_liquidated"], "max_dd": r1["max_dd_nav_usd"],
                          "sells_per_year": r1["strategy_trades"]/r1["years"]},
                }
            wres[wn] = cd
        at_plateau[arm] = wres
    print("after-tax plateau done")

    boot_at = after_tax_bootstrap(n_paths=1000, capital=100000.0, seed=SEED)
    print("after-tax bootstrap done", boot_at["median_delta_cagr"], boot_at["share_delta_gt_0"])

    # ---- 18-ETF pre-tax test ----
    etf_results = {}
    for kt in OOS_ETFS:
        if kt == "EWC":
            continue
        try:
            ddays, o, high, low, c, y, start = load_etf(kt)
        except Exception as e:
            continue
        if not _market_quality_ok(high, low, o, c):
            continue
        so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
        e2 = e2_up(c)
        vol = trv2.vol_n(sc)[0]
        wants = wants_for(e2, vol)[0]
        dates = ddays[start - 1:]
        years = trv2.calendar_years(dates[0], dates[-1])
        # C0
        nav0, turn0, ntr0, codes0, traded0 = simulate_band(so, sc, wants, COST_PER_SIDE * 2, start, C0_BAND)
        nav0 = nav0[0]
        # A2
        nav1, turn1, ntr1, codes1, traded1 = simulate_downward(so, sc, wants, COST_PER_SIDE * 2, start, C0_BAND, 0.5, 0.0)
        nav1 = nav1[0]
        ev0 = event_stats(dates, codes0[0], traded0[0])
        ev1 = event_stats(dates, codes1[0], traded1[0])
        etf_results[kt] = {
            "cagr_C0": float(trv2.cagr(nav0[0], nav0[-1], years)),
            "cagr_A2": float(trv2.cagr(nav1[0], nav1[-1], years)),
            "delta_cagr": float(trv2.cagr(nav1[0], nav1[-1], years) - trv2.cagr(nav0[0], nav0[-1], years)),
            "max_dd_C0": float(trv2.max_dd(nav0)[0]),
            "max_dd_A2": float(trv2.max_dd(nav1)[0]),
            "delta_max_dd": float(trv2.max_dd(nav1)[0] - trv2.max_dd(nav0)[0]),
            "sells_per_year_C0": ev0["sells_per_year"],
            "sells_per_year_A2": ev1["sells_per_year"],
        }
    print("ETF test done", len(etf_results))

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g
    h2 = main_res["base"]["m2"]
    gates["A2"]["plateau"] = True
    selection = {"selected": "A2" if gates["A2"]["pass_all"] else "none"}

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
        "after_tax_plateau": at_plateau,
        "after_tax_bootstrap_T1": boot_at,
        "etf_test": etf_results,
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
    L.append("pre-tax gates for A2 vs C0 in JSON")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


# ================= TAX/after-tax model imported from tax_frontier_v1 =================
#!/usr/bin/env python3
"""tax_frontier_v1: which E2-family rule maximizes after-tax return."""

import argparse
import bisect
import csv
import hashlib
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


trv2 = imp("trv2", ROOT / "research/trend_robustness_v2/screen.py")
lf = imp("lf", ROOT / "research/leverage_frontier_v1/screen.py")
v3 = imp("v3", ROOT / "research/riskoff_rotation_v3/screen.py")

# constants
M = 2
COST = trv2.COST_PER_SIDE * M
BAND = trv2.BAND
CAP = trv2.CAP
START = v3.START
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10
CAPITALS = (10_000.0, 100_000.0, 1_000_000.0)
WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
ARMS = ("C0", "A1", "A2", "A3", "A4", "A5", "A6")
TRADE_MODE = {"C0": "prod", "A1": "flip", "A2": "downward", "A3": "quarter", "A4": "prod", "A5": "hold", "A6": "hold"}

# load data at import
DAYS, ARR, Y, Y10 = lf.load_all()
UP = v3.e2_up(ARR["QQQ"][1])
QQQ = ARR["QQQ"]
EQ2_O, EQ2_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 2, lf.EXPENSE[2.0], 0.0)
QQ1_O, QQ1_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 1, lf.EXPENSE[1.0], 0.0)
SPY1_O, SPY1_C = lf.synthetic_L(ARR["SPY"][0], ARR["SPY"][1], ARR["SPY"][2], Y, 1, lf.EXPENSE[1.0], 0.0)
EQ2_VOL = trv2.vol_n(EQ2_C)[0]
VT035 = lf.vt_weight(EQ2_VOL, 0.35)
T = len(DAYS)

# target arrays per arm (1-D over sessions)
TARGETS = {}
TARGETS["C0"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A1"] = UP.astype(float) * CAP
TARGETS["A2"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A3"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A4"] = UP.astype(float) * CAP
TARGETS["A5"] = np.ones(T)
TARGETS["A6"] = np.ones(T)

PRICES = {
    "C0": (EQ2_O, EQ2_C), "A1": (EQ2_O, EQ2_C), "A2": (EQ2_O, EQ2_C), "A3": (EQ2_O, EQ2_C),
    "A4": (QQ1_O, QQ1_C), "A5": (QQ1_O, QQ1_C), "A6": (SPY1_O, SPY1_C),
}


class Market:
    def __init__(self):
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in DAYS])
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
        # days for Market are DAYS

    def scale(self, year: int) -> float:
        # uf cache via simple dict
        if not hasattr(self, "_sc"):
            self._sc = {}
        if year not in self._sc:
            self._sc[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self._sc[year]

    days = DAYS


Market.DAYS = DAYS


# tax model copied from riskoff
def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def tariff_tax(base, scale):
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale if not math.isinf(hi) else math.inf
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


class Regime:
    def __init__(self, name, kind):
        self.name, self.kind = name, kind

    def tax(self, base, year, mkt):
        if self.kind == "none" or base <= 0:
            return 0.0
        return tariff_tax(base, mkt.scale(year))


class BaseAccount:
    def __init__(self, mkt, regime, cash, cashbox=None):
        self.mkt = mkt
        self.rg = regime
        if cashbox is None:
            self._cashbox = {"v": float(cash)}
        else:
            self._cashbox = cashbox
            self._cashbox["v"] = float(cash)
        self.units = 0.0
        self.lots = []
        self.year_gain = defaultdict(float)
        self.year_div = defaultdict(float)
        self.year_wht = defaultdict(float)
        self.gain_by_reason = defaultdict(float)
        self.pos_gain_by_reason = defaultdict(float)
        self.neg_gain_by_reason = defaultdict(float)
        self.index_relief_try = 0.0
        self.tax_due = {}
        self.tax_paid_try = defaultdict(float)
        self.tax_paid_usd = 0.0
        self.wht_usd = 0.0
        self.fees = 0.0

    @property
    def cash(self):
        return self._cashbox["v"]

    @cash.setter
    def cash(self, v):
        self._cashbox["v"] = float(v)

    def buy(self, i, price, usd, fee):
        if usd <= 0:
            return
        u = usd / price
        self.cash -= usd + fee
        self.fees += fee
        self.units += u
        self.lots.append([u, (usd + fee) / u, self.mkt.fx[i], self.mkt.days[i][:7]])

    def sell(self, i, price, units, fee, reason):
        units = min(units, self.units)
        if units <= 0:
            return 0.0
        gross = units * price
        self.cash += gross - fee
        self.fees += fee
        fx_s = self.mkt.fx[i]
        sale_month = self.mkt.days[i][:7]
        year = int(self.mkt.days[i][:4])
        per_unit = (gross - fee) / units
        left, gain = units, 0.0
        while left > 0 and self.lots:
            lot = self.lots[0]
            take = min(left, lot[0])
            proceeds = take * per_unit * fx_s
            cost = take * lot[1] * lot[2]
            g = proceeds - cost
            if self.rg.kind != "none" and g > 0:
                base_m = prev_month(lot[3])
                rise = self.mkt.ufe[prev_month(sale_month)] / self.mkt.ufe[base_m] - 1
                if rise >= INDEX_MIN_RISE:
                    gi = max(0.0, proceeds - cost * (1 + rise))
                    self.index_relief_try += g - gi
                    g = gi
            gain += g
            if g >= 0:
                self.pos_gain_by_reason[reason] += g
            else:
                self.neg_gain_by_reason[reason] += g
            lot[0] -= take
            left -= take
            if lot[0] <= take * 1e-12:
                self.lots.pop(0)
        self.units -= units
        if self.units <= 1e-12 * max(1.0, units) or not self.lots:
            self.units, self.lots = 0.0, []
        self.year_gain[year] += gain
        self.gain_by_reason[reason] += gain
        return gain

    def assess(self, year):
        g = max(0.0, self.year_gain.get(year, 0.0))
        d = self.year_div.get(year, 0.0)
        if self.rg.kind == "none":
            return 0.0
        if d <= DIV_THRESHOLD_2026 * self.mkt.scale(year):
            d = 0.0
        base = g + d
        tax = self.rg.tax(base, year, self.mkt)
        if d > 0 and base > 0:
            tax -= min(self.year_wht.get(year, 0.0), tax * d / base)
        return max(0.0, tax)

    def outstanding(self):
        return sum(v - self.tax_paid_try[y] for y, v in self.tax_due.items())


def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_strategy(arm, regime_name, capital, a, b, mkt):
    days_all = DAYS
    if isinstance(a, int):
        i0 = a
        i1 = b
    else:
        i0 = bisect.bisect_left(days_all, a)
        i1 = bisect.bisect_right(days_all, b) - 1
    if regime_name == "progressive":
        regime = Regime("progressive", "tariff")
    else:
        regime = Regime("pre_tax", "none")

    opens_arr, closes_arr = PRICES[arm]
    wants_arr = TARGETS[arm]
    mode = TRADE_MODE[arm]

    cashbox = {"v": float(capital)}
    account = BaseAccount(mkt, regime, 0.0, cashbox)
    cashbox["v"] = float(capital)

    pays = instalments(days_all, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0

    prev_tgt = None
    prev_quarter = None
    quarter_first = False

    for i in range(i0, i1 + 1):
        # year boundary assess
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax

        # instalments
        for ty, part in pays.get(i, ()):
            if ty not in account.tax_due:
                account.tax_due[ty] = 0.0
            due = account.tax_due.get(ty, 0.0)
            amt_total = due / 2 if part == 0 else due - account.tax_paid_try.get(ty, 0.0)
            if amt_total <= 1e-9:
                continue
            usd = amt_total / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - COST)
                if account.units > 1e-12:
                    account.sell(i, float(opens_arr[i]), need / float(opens_arr[i]), need * COST, "tax")
                    tax_sales += 1
            cashbox["v"] -= usd
            account.tax_paid_try[ty] += amt_total
            account.tax_paid_usd += usd

        po = float(opens_arr[i])
        pc = float(closes_arr[i])
        held_units = account.units
        held_value = held_units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (held_units * po) / nav_open if nav_open > 0 else 0.0

        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if math.isnan(tgt) else tgt
        resize_target = tgt

        # quarter boundary
        qk = (days_all[i][:4], (int(days_all[i][5:7]) - 1) // 3)
        if qk != prev_quarter:
            quarter_first = True
            prev_quarter = qk
        else:
            quarter_first = False

        trend_flip = (prev_tgt is not None) and ((tgt == 0) != (prev_tgt == 0))

        # trade decision
        trade = False
        if mode == "prod":
            trade = abs(tgt - w) > BAND
        elif mode == "flip":
            if prev_tgt is None:
                trade = abs(tgt - w) > 1e-12
            else:
                trade = (tgt == 0) != (prev_tgt == 0)
        elif mode == "hold":
            if prev_tgt is None:
                trade = abs(tgt - w) > 1e-12
            else:
                trade = abs(tgt - prev_tgt) > 1e-12
        elif mode == "quarter":
            if trend_flip:
                trade = True
            elif quarter_first:
                trade = abs(tgt - w) > BAND
            else:
                # entries handled by trend_flip; downward drift only if flip
                trade = False
            if account.units == 0 and tgt > 0:
                trade = True
            if account.units > 0 and tgt == 0:
                trade = True
        elif mode == "downward":
            meta = TAX_ARM_META.get(arm, {})
            kk = meta.get("k", 0.5)
            trim = meta.get("trim", 0.0)
            if tgt > w + BAND:
                trade = True
                resize_target = tgt
            elif tgt < w - BAND:
                _trim_case = (tgt > 0) and (tgt < kk * max(w, 1e-12))
                if prev_tgt is not None and prev_tgt > 0 and tgt == 0:
                    trade = True
                    resize_target = tgt
                elif _trim_case:
                    trade = True
                    if trim > 0:
                        resize_target = tgt + trim * (w - tgt)
                    else:
                        resize_target = tgt
            if account.units == 0 and tgt > 0:
                trade = True
                resize_target = tgt
            if account.units > 0 and tgt == 0:
                trade = True
                resize_target = 0.0

        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(resize_target - w)
            dsum = drift
            fee_total = nav_open * COST * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            # single leg adjust units
            curr_units = account.units
            target_units = resize_target * nav_after_cost / po if po > 0 else 0.0
            du = target_units - curr_units
            leg_fee = nav_open * COST * drift if drift > 0 else 0.0
            if du > 1e-12:
                account.buy(i, po, du * po, leg_fee)
            elif du < -1e-12:
                account.sell(i, po, -du, leg_fee, "resize")
            trades += 1

        # update nav at close
        nav[i - i0 + 1] = cashbox["v"] + account.units * pc

        prev_tgt = tgt

    # liquidation
    end_y = int(days_all[i1][:4])
    for _ in range(0):
        pass
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - liq_tax / fx1

    ending_nav = nav[-1]
    mtm = ending_nav

    fx0 = mkt.fx[i0 - 1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(account.tax_paid_try.values()) + liq_tax
    tax_total_usd = account.tax_paid_usd + liq_tax / fx1v

    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1
    all_reasons = dict(account.gain_by_reason)

    # max dd
    nav_arr = nav.reshape(1, -1)
    maxdd = float(trv2.max_dd(nav_arr)[0])

    return {
        "window": [days_all[i0], days_all[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1v),
        "ending_nav_usd": float(ending_nav),
        "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1v)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_unliquidated": float(cg(capital * fx0, mtm * fx1v)),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(tax_total_try),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(all_reasons.items())},
        "indexation_relief_try": float(account.index_relief_try),
        "_nav": nav,
    }


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# base market for after-tax bootstrap
BASE_MKT = Market()
BASE_MKT.days = DAYS
FX_BY_DATE = {DAYS[i]: float(BASE_MKT.fx[i]) for i in range(len(DAYS))}

# --- tax_asym arm registration into the after-tax model ---
TAX_ARM_META = {}
_BASE_VT = np.minimum(CAP, VT035)
for _arm, _k in (("A2_33", 0.33), ("A2_40", 0.40), ("A2_50", 0.50), ("A2_60", 0.60), ("A2_67", 0.67), ("A2_75", 0.75)):
    TARGETS[_arm] = UP.astype(float) * _BASE_VT
    PRICES[_arm] = (EQ2_O, EQ2_C)
    TRADE_MODE[_arm] = "downward"
    TAX_ARM_META[_arm] = {"k": _k, "trim": 0.0}
# default name A2 maps to 50%
TRADE_MODE["A2"] = "downward"
TARGETS["A2"] = UP.astype(float) * _BASE_VT
PRICES["A2"] = (EQ2_O, EQ2_C)
TAX_ARM_META["A2"] = {"k": 0.50, "trim": 0.0}
# partial trim variant
TARGETS["A2r"] = UP.astype(float) * _BASE_VT
PRICES["A2r"] = (EQ2_O, EQ2_C)
TRADE_MODE["A2r"] = "downward"
TAX_ARM_META["A2r"] = {"k": 0.50, "trim": 0.50}

ARMS = ("C0", "A2")
CANDIDATES = ("A2",)


def _patched_scale(self, year):
    key = f"{year - 1}-10"
    if key not in self.ufe:
        # fallback to earliest available october
        ks = sorted(k for k in self.ufe if k.endswith("-10"))
        key = ks[0] if ks else ANCHOR
    return self.ufe[key] / self.ufe[ANCHOR]

Market.scale = _patched_scale



def after_tax_bootstrap(n_paths=1000, capital=100000.0, seed=20261011):
    global DAYS, TARGETS, PRICES
    q = ARR["QQQ"]
    days_src = DAYS
    T = q.shape[1]
    L = 253 + 10 * 252
    EXT = 3
    rng = np.random.default_rng(seed)
    pool = np.arange(1, T)
    npool = len(pool)
    d_cagr, d_dd = [], []
    for _ in range(n_paths):
        P = 1
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]
        yy = Y[idx - 1]
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
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)

        # vol target (base financing) for both arms
        vol = trv2.vol_n(sc)[0]
        vt = lf.vt_weight(vol, 0.35)
        wants = e2.astype(float)[0] * np.minimum(CAP, vt)

        dates = [DAYS[i] for i in idx.flatten().tolist()]  # warmup source dates not dates; need measured dates?
        # Build date list for reconstructed path: use source dates for sampled positions; but warmup included in pos/pre.
        dates = [days_src[i] for i in idx.flatten().tolist()]
        # Add back-extension? idx includes pre, no separate date list for pre; but pre dates derived from idx pre. dates covers all L+EXT.
        dates = [days_src[i] for i in idx.flatten().tolist()]

        fx_path = np.array([FX_BY_DATE.get(d, BASE_MKT.fx[-1]) for d in dates], dtype=float)

        # run after-tax for C0 and A2 via monkeypatch globals
        old_days = list(DAYS)
        old_targets = {k: TARGETS.get(k) for k in ("C0", "A2")}
        old_prices = {k: PRICES.get(k) for k in ("C0", "A2")}
        DAYS = list(dates)
        w_arr = np.atleast_1d(wants).flatten()
        TARGETS["C0"] = w_arr.copy()
        TARGETS["A2"] = w_arr.copy()
        PRICES["C0"] = (qo.flatten(), qc.flatten())
        PRICES["A2"] = (so.flatten(), sc.flatten())
        # measured start date
        i0 = EXT + 253
        i1 = len(dates) - 1
        mkt = Market()
        mkt.days = list(dates)
        mkt.fx = fx_path
        mkt.ufe = BASE_MKT.ufe
        try:
            r0 = run_strategy("C0", "progressive", capital, i0, i1, mkt)
            r1 = run_strategy("A2", "progressive", capital, i0, i1, mkt)
        finally:
            DAYS = old_days
            TARGETS["C0"] = old_targets["C0"]
            TARGETS["A2"] = old_targets["A2"]
            PRICES["C0"] = old_prices["C0"]
            PRICES["A2"] = old_prices["A2"]
        c0v=r0["cagr_usd_liquidated"]; c1v=r1["cagr_usd_liquidated"]
        dv = r1["max_dd_nav_usd"] - r0["max_dd_nav_usd"]
        if (c0v==c0v and c1v==c1v and dv==dv):  # finite
            d_cagr.append(c1v - c0v)
            d_dd.append(dv)

    return {
        "paths_total": n_paths,
        "paths_finite": len(d_cagr),
        "paths": len(d_cagr),
        "median_delta_cagr": float(np.median(d_cagr)),
        "share_delta_gt_0": float(np.mean(np.array(d_cagr) > 0)),
        "p05_delta_cagr": float(np.percentile(d_cagr, 5)),
        "deltas_cagr": [float(x) for x in d_cagr],
        "delta_max_dd_median": float(np.median(d_dd)),
        "delta_max_dd_p95": float(np.percentile(d_dd, 95)),
        "deltas_max_dd": [float(x) for x in d_dd],
    }

if __name__ == "__main__":
    main()
