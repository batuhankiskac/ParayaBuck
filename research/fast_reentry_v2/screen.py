#!/usr/bin/env python3
"""fast_reentry_v2 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Confirmation of fast_reentry_v1's R2 (production E2 plus a half-weight early re-entry path)
by two pre-registered tests: a 6-neighbour parameter plateau (gate P) and 21 independent
instruments with their own E2+R2 logic and their own synthetic 2x leg (gate I).

Modes
  --smoke    plumbing only, on synthetic random prices: every arm spec, the instrument path,
             the leave-one-out path and the after-tax engine run end to end and the arm
             invariants are asserted. Touches no real data.
  --harness  real data, controls only: C0 reproduces e2_integration_v1's production-E2
             deviation row to 0.01 pp (base/calibrated x m=1/m=2), the band simulator at
             0.5 pp equals trend_robustness_v2.simulate_multi bit for bit, the fast rolling
             median equals the v1 loop reference bit for bit, and the recomputed C0 / R2
             main-window rows equal research/fast_reentry_v1/RESULTS.json to 0.01 pp.
  --run      re-runs --harness, stops if it fails, then runs every arm (C0, R2 and the six
             plateau neighbours) on the main window, the main block-20 bootstrap, the
             out-of-sample windows (^GSPC 1957-1999 and e2_confirmation_v1's M1..M5), the
             pooled OOS bootstrap, the unchanged G1-G9 gates, the 21-instrument test (gate I),
             the leave-one-episode-out analysis, the v1 audit checks and the corrected
             after-tax engine at $100k. Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics, bootstrap, OOS loaders) are the
fast_reentry_v1 screen (itself built on research/trend_robustness_v2/screen.py, whose main()
is never run). The after-tax engine is research/fast_reentry_v2/tax_engine.py, a copy of
research/vt_level_tax_v1/screen.py's corrected engine.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)
_te_spec = importlib.util.spec_from_file_location("fr2_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = HERE / "E2_INTEGRATION_DEVIATION.json"
V1_RESULTS = ROOT / "research/fast_reentry_v1/RESULTS.json"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
LAG_FAST = 20
C0_BAND = trv2.BAND  # 0.005
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

# arms: spec = (SMA, vol-median window, early weight); None = production E2 control
ARM_SPEC = {
    "C0": None,
    "R2": {"sma": 50, "med": 252, "w": 0.5},
    "R2_s40": {"sma": 40, "med": 252, "w": 0.5},
    "R2_s60": {"sma": 60, "med": 252, "w": 0.5},
    "R2_m126": {"sma": 50, "med": 126, "w": 0.5},
    "R2_m504": {"sma": 50, "med": 504, "w": 0.5},
    "R2_w13": {"sma": 50, "med": 252, "w": 1.0 / 3.0},
    "R2_w23": {"sma": 50, "med": 252, "w": 2.0 / 3.0},
}
ARMS = tuple(ARM_SPEC)
NEIGHBOURS = ("R2_s40", "R2_s60", "R2_m126", "R2_m504", "R2_w13", "R2_w23")
CANDIDATES = ("R2",) + NEIGHBOURS
GATE_P_MIN_NEIGHBOURS = 5
GATE_P_MAX_DD_SLACK = 0.01
GATE_I_SHARE_POS = 0.60
GATE_I_DD_SLACK = 0.03
GATE_I_DD_SHARE = 0.80
GATE_I_PATHS = 2000

# instruments (gate I): 19 range_vol_v2 symbols + 3 range_vol_v1 symbols; EWC fails the
# range_vol_v2 quality rule and is excluded before simulation (see PREREGISTRATION.md).
INSTRUMENT_FILES = {
    "XLB": "range_vol_v2_xlb.csv", "XLE": "range_vol_v2_xle.csv", "XLF": "range_vol_v2_xlf.csv",
    "XLI": "range_vol_v2_xli.csv", "XLK": "range_vol_v2_xlk.csv", "XLP": "range_vol_v2_xlp.csv",
    "XLU": "range_vol_v2_xlu.csv", "XLV": "range_vol_v2_xlv.csv", "XLY": "range_vol_v2_xly.csv",
    "EWJ": "range_vol_v2_ewj.csv", "EWG": "range_vol_v2_ewg.csv", "EWU": "range_vol_v2_ewu.csv",
    "EWC": "range_vol_v2_ewc.csv", "EWA": "range_vol_v2_ewa.csv", "EWH": "range_vol_v2_ewh.csv",
    "EWS": "range_vol_v2_ews.csv", "EWL": "range_vol_v2_ewl.csv", "EFA": "range_vol_v2_efa.csv",
    "EEM": "range_vol_v2_eem.csv", "SPY": "range_vol_v1_spy.csv", "DIA": "range_vol_v1_dia.csv",
    "IWM": "range_vol_v1_iwm.csv",
}
INSTRUMENTS = tuple(sorted(INSTRUMENT_FILES))
QUALITY_MAX_BAD_FRAC = 0.05  # range_vol_v2's fixed rule
INSTRUMENT_MIN_SESSIONS = 500

# after-tax (report only): vt_level_tax_v1's windows at $100k
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
# vt_level_tax_v1 anchors for C0 (branch research/vt_level_tax_v1, RESULTS.json after_tax_windows)
TAX_ANCHORS = {
    "2000_03_to_2026_09": {"pre_tax": 0.146715, "progressive": 0.095340, "progressive_max_dd": 0.495643},
    "2010_01_to_end": {"pre_tax": 0.218852, "progressive": 0.144968, "progressive_max_dd": 0.446920},
    "2017_01_to_end": {"pre_tax": 0.322564, "progressive": 0.226932, "progressive_max_dd": 0.376052},
}
AUDIT_PROBES = 64
AUDIT_SEED = 20261014


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


# ----------------------------------------------------------------------------- production sizing
def wants_for(e2, vol):
    """[P, T] QLD* targets: E2 vote x vt(vol), total capped at 0.98 as trend_robustness_v2.build_wants."""
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


def _rolling_median_vol_ref(price_vol, window=252):
    """Reference (v1) trailing rolling median of the vol series; NaN until the window is full."""
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    with warnings.catch_warnings():  # all-NaN windows before the vol window fills -> NaN, guarded by cnt
        warnings.simplefilter("ignore", RuntimeWarning)
        for t in range(T):
            lo = max(0, t - window + 1)
            w = price_vol[:, lo:t + 1]
            cnt = np.sum(np.isfinite(w), axis=1)
            med = np.nanmedian(np.where(np.isfinite(w), w, np.nan), axis=1)
            out[:, t] = np.where(cnt >= window, med, np.nan)
    return out


def _rolling_median_vol(price_vol, window=252, block=32):
    """Same as _rolling_median_vol_ref, computed with sliding windows (bit-identical; harness-checked).

    Valid only when NaNs form a prefix per path (true for trv2.vol_n output); asserted, so a
    series with interior NaNs fails loudly instead of silently returning a wrong median.
    """
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    if T < window:
        return out
    for s in range(0, P, block):
        e = min(P, s + block)
        blk = price_vol[s:e]
        fin = np.isfinite(blk)
        first = np.argmax(fin, axis=1)
        for r in range(e - s):
            if np.any(np.diff(fin[r].astype(np.int8)) < 0):  # a finite value followed by a NaN
                raise ValueError("interior NaN in the vol series; use _rolling_median_vol_ref")
        j0 = int(first.min())
        if T - j0 < window:  # no session has a full trailing window yet
            continue
        out[s:e, j0 + window - 1:] = np.median(
            sliding_window_view(blk[:, j0:], window, axis=1), axis=2)
    return out


def early_components(price, sma_n, med_window):
    """Path-independent early-path inputs: SMA_n vote, vol20, trailing median of vol20, close > close[-20]."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma_up_v = trv2.sma_up(price, sma_n)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, med_window)
    close_lag = np.full_like(price, np.nan)
    close_lag[:, LAG_FAST:] = price[:, :-LAG_FAST]
    return sma_up_v, vol_p, med_p, price > close_lag


def comps_for(price, sma_n, med_window, cache):
    key = (sma_n, med_window)
    if key not in cache:
        cache[key] = early_components(price, sma_n, med_window)
    return cache[key]


def arm_signals(price, e2_on, vol_instr, spec, comps=None):
    """Return (target weights [P, T], early_active [P, T]) for one arm.

    spec is None for C0 (production E2) or {"sma", "med", "w"}. Early logic is stateful and
    evaluated at the close: while E2 is off, enter at weight w x vt if close > SMA and
    vol20 < trailing median of vol20 and close > close[-20]; exit if close < SMA; the early
    path ends the moment E2's own vote turns on (then plain production).
    """
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    P, T = price.shape
    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    if spec is None:
        on_eff[:, :] = e2_on
        factor[:, :] = e2_on.astype(float)
    else:
        if comps is None:
            comps = early_components(price, spec["sma"], spec["med"])
        sma_up_v, vol_p, med_p, lag_cond = comps
        active = np.zeros(P, dtype=bool)
        for t in range(T):
            e2t = e2_on[:, t]
            active = np.where(e2t, False, active)  # early path ends when E2 turns on
            active = np.where(active & (~sma_up_v[:, t]) & (~e2t), False, active)  # exit: close < SMA
            entry = sma_up_v[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
            can_enter = (~active) & (~e2t) & np.isfinite(med_p[:, t])
            active = np.where(can_enter & entry, True, active)
            on_eff[:, t] = e2t | active
            early_hist[:, t] = active
            factor[:, t] = np.where(e2t, 1.0, np.where(active, spec["w"], 0.0))
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap, early_hist


def wants_by_arm(price, e2, vol_instr, arms):
    """{arm: (wants, early_hist)} for a list of arms, sharing the per-(SMA, med) components."""
    cache = {}
    out = {}
    for arm in arms:
        spec = ARM_SPEC[arm]
        comps = None if spec is None else comps_for(price, spec["sma"], spec["med"], cache)
        out[arm] = arm_signals(price, e2, vol_instr, spec, comps)
    return out


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


def arm_stats(dates, nav, turn, ntr, codes, traded, wants, start, extras, early_active=None):
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
        if early_active is not None:
            ea = np.atleast_2d(early_active)
            sl = slice(start - 1, start - 1 + len(tgt))
            st["early_time_in_market"] = float(ea[0, sl].mean())
        st["sessions_target_below_10pp_in_trend"] = int(np.sum((tgt > 0) & (tgt < 0.10)))
    return st


# ----------------------------------------------------------------------------- main window
def early_episodes(dates, nav, nav_c0, early_aligned):
    """Per-episode P&L of the early positions (base financing, m = 2).

    early_aligned[k] is the early path's decision at the close of dates[k], so the episode holds the
    early position over the sessions k0+1 .. k1+1. The P&L span runs from the close of dates[k0] to
    the close of the reconciliation session (the next open after the early path stops driving the
    target), where the early-driven position is either sold to cash or merged into production E2's
    position. That includes the exit fill and the overnight gap the early position earned, so the
    product of (1 + delta) over episodes reproduces the arm's total relative gain."""
    ea = np.asarray(early_aligned, dtype=bool)
    n = min(len(ea), len(nav) - 1, len(nav_c0) - 1)
    idx = np.flatnonzero(ea[:n])
    eps = []
    if idx.size:
        cut = np.flatnonzero(np.diff(idx) > 1)
        for s, e in zip(np.concatenate([[0], cut + 1]), np.concatenate([cut, [idx.size - 1]])):
            k0, k1 = int(idx[s]), int(idx[e])
            i1 = min(k1 + 2, len(nav) - 1)
            ra = float(nav[i1] / nav[k0] - 1)
            rc = float(nav_c0[i1] / nav_c0[k0] - 1)
            eps.append({"entry_decision": dates[k0], "last_early_decision": dates[k1],
                        "first_fill_session": dates[k0 + 1], "last_held_session": dates[k1 + 1],
                        "reconciliation_session": dates[i1], "sessions_held": k1 + 1 - k0,
                        "arm_return": ra, "c0_return": rc, "delta": ra - rc})
    return eps


def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return days, arr, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0]


def main_stage(arms, extras=False, m_list=(1, 2)):
    """Main window for every arm and both financing models; returns (results, context)."""
    res = {}
    ctx = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, v20 = main_inputs(spread)
        dates = days[START - 1:]
        price = arr["QQQ"][1]
        got = wants_by_arm(price, e2, v20, arms)
        res[md] = {}
        for m in m_list:
            hm = {}
            navs = {}
            for arm in arms:
                w, ea = got[arm]
                nav, turn, ntr, codes, traded = simulate_band(o, c, w, COST_PER_SIDE * m, START, C0_BAND)
                navs[arm] = nav[0]
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], w[0], START,
                                    extras and m == 2, early_active=ea)
            if extras and m == 2 and "C0" in arms:
                for arm in arms:
                    if arm != "C0":
                        hm[arm]["early_episodes"] = early_episodes(
                            dates, navs[arm], navs["C0"], got[arm][1][0][START - 1:])
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
        ctx[md] = {"days": days, "o": o, "c": c, "dates": dates, "wants": {a: got[a][0][0] for a in arms},
                   "early": {a: got[a][1][0] for a in arms}}
    return res, ctx


# ----------------------------------------------------------------------------- harness
def jload(path):
    return json.loads(Path(path).read_text())


def harness():
    ref = jload(DEVIATION)
    h, _ = main_stage(("C0", "R2"))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = h[md][f"m{m}"]["C0"]
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff_pp": (x[k] - r[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())

    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m = 2
    days, arr, o, c, e2, v20 = main_inputs(0.0)
    w = wants_for(e2, v20)
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))

    # fast rolling median == v1 loop reference (bit for bit), on the main window and on 3 paths
    vol_p = trv2.vol_n(arr["QQQ"][1])
    med_ref = _rolling_median_vol_ref(vol_p)
    med_fast = _rolling_median_vol(vol_p)
    med_equal_1d = bool(np.array_equal(med_ref, med_fast, equal_nan=True))
    rng = np.random.default_rng(SEED)
    pv = np.abs(rng.normal(0.3, 0.05, (3, 1500)))
    pv[:, :20] = np.nan
    med_equal_multi = bool(np.array_equal(_rolling_median_vol_ref(pv, 252), _rolling_median_vol(pv, 252),
                                          equal_nan=True))
    med_equal = med_equal_1d and med_equal_multi

    # recomputed C0 / R2 main-window rows vs fast_reentry_v1's RESULTS.json (0.01 pp)
    v1 = jload(V1_RESULTS)
    v1_checks = {}
    for md in FIN:
        for m in (1, 2):
            r1 = v1["main_window"][md][f"m{m}"]
            h2 = h[md][f"m{m}"]
            for arm in ("C0", "R2"):
                for k in ("cagr", "max_dd"):
                    d = (h2[arm][k] - r1[arm][k]) * 100
                    v1_checks[f"{md}_m{m}_{arm}_{k}"] = {"reference": r1[arm][k], "reproduced": h2[arm][k],
                                                         "diff_pp": d}
                if m == 2:
                    d = (h2[arm]["turnover_annual"] - r1[arm]["turnover_annual"])
                    v1_checks[f"{md}_m2_{arm}_turnover"] = {"reference": r1[arm]["turnover_annual"],
                                                            "reproduced": h2[arm]["turnover_annual"], "diff_pp": d}
    ok_v1 = all(abs(v["diff_pp"]) <= 0.01 for v in v1_checks.values())
    # v1's R2 bootstrap median (base) as a further anchor
    boot_check = None
    try:
        b1 = v1["bootstrap_main_block20_m2"]["base"]["R2"]["diff_vs_C0_median"]
        boot_check = {"v1_reference_pp": b1 * 100}
    except KeyError:
        pass
    ok = ok_dev and sim_equal and med_equal and ok_v1
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "rolling_median_fast_equals_v1_reference": med_equal,
            "v1_reproduction_checks": v1_checks, "v1_reproduction_pass": ok_v1,
            "v1_bootstrap_reference": boot_check, "pass": bool(ok)}


# ----------------------------------------------------------------------------- bootstrap (main window)
def boot_main(arms):
    """Stationary block bootstrap (mean block 20), draws identical to signal_ensemble_v1 (seed, order,
    chunks, 3-session back-extension). Signals on the resampled price level; instrument on the
    resampled total-return level. All arms on the same paths, m = 2."""
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
        cache = {}
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(scl)
            for arm in arms:
                spec = ARM_SPEC[arm]
                comps = None if spec is None else comps_for(pc, spec["sma"], spec["med"], cache)
                w, _ = arm_signals(pc, e2, vol_instr, spec, comps)
                nav, turn, _, _, _ = simulate_band(so, scl, w, COST_PER_SIDE * 2, start, C0_BAND)
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
    vol_instr = trv2.vol_n(sc)
    got = wants_by_arm(c, e2, vol_instr, arms)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        w = got[arm][0]
        nav, turn, ntr, codes, traded = simulate_band(so, sc, w, COST_PER_SIDE * 2, start, C0_BAND)
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
        vol_instr = trv2.vol_n(sc)
        got = wants_by_arm(cc, e2, vol_instr, arms)
        for arm in arms:
            nav = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates (ewma_band_v1, unchanged)
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


def gate_p(main):
    """Plateau gate: >= 5 of the 6 neighbours beat C0 on base m = 2 CAGR and none exceeds C0's max DD + 1 pp."""
    c0 = main["base"]["m2"]["C0"]
    detail = {}
    for arm in NEIGHBOURS:
        a = main["base"]["m2"][arm]
        detail[arm] = {"cagr": a["cagr"], "cagr_delta_vs_C0": a["cagr"] - c0["cagr"], "max_dd": a["max_dd"],
                       "max_dd_delta_vs_C0": a["max_dd"] - c0["max_dd"],
                       "beats_c0": bool(a["cagr"] > c0["cagr"]), "dd_ok": bool(a["max_dd"] <= c0["max_dd"] + GATE_P_MAX_DD_SLACK)}
    n_beats = sum(1 for v in detail.values() if v["beats_c0"])
    n_dd_ok = sum(1 for v in detail.values() if v["dd_ok"])
    return {"detail": detail, "neighbours_beating_C0": n_beats, "neighbours_needed": GATE_P_MIN_NEIGHBOURS,
            "neighbours_with_dd_ok": n_dd_ok,
            "pass": bool(n_beats >= GATE_P_MIN_NEIGHBOURS and n_dd_ok == len(NEIGHBOURS)),
            "rule": (f">= {GATE_P_MIN_NEIGHBOURS} of {len(NEIGHBOURS)} neighbours with base m=2 CAGR > C0 and no "
                     f"neighbour with max DD > C0 + {GATE_P_MAX_DD_SLACK * 100:.0f} pp")}


# ----------------------------------------------------------------------------- instruments (gate I)
def load_instrument(key):
    """Adjusted OHLC of one ETF: close := adjclose, open/high/low scaled by adjclose/close; DTB3 ffilled."""
    rows = []
    with (ROOT / "data/historical" / INSTRUMENT_FILES[key]).open() as fh:
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
    return dates, o, h, l, c, y


def quality_ok(high, low, opn, close):
    """range_vol_v2's fixed data-quality rule: > 5% of bars with high == low or an invalid open."""
    bad = 0
    for hi, lo, op, cl in zip(high, low, opn, close):
        if hi is None or lo is None or hi == lo:
            bad += 1
            continue
        if op is None or op <= 0 or op < lo * (1 - 1e-9) or op > hi * (1 + 1e-9):
            bad += 1
    frac = bad / max(1, len(high))
    return frac, bool(frac <= QUALITY_MAX_BAD_FRAC)


def instrument_window(days, o, high, low, c, y, arms, start=START):
    """Own E2 + own early path on the instrument's adjusted close, own 2x synthetic leg (calibrated, m = 2)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    vol_instr = trv2.vol_n(sc)
    got = wants_by_arm(c, e2, vol_instr, arms)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        nav, turn, ntr, codes, traded = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, start, C0_BAND)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"]}
    return res


def boot_instrument(key, rng, arms):
    """Block-20 bootstrap on one instrument's own leg (calibrated, m = 2), 2,000 paths."""
    days, o, h, l, c, y = load_instrument(key)
    pool = np.arange(START, len(days))
    npool = len(pool)
    acc = {a: [] for a in arms}
    for chunk in range(0, GATE_I_PATHS, CHUNK):
        P = min(CHUNK, GATE_I_PATHS - chunk)
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
        vol_instr = trv2.vol_n(sc)
        got = wants_by_arm(cc, e2, vol_instr, arms)
        for arm in arms:
            nav = simulate_band(so, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


def instrument_stage(arms, keys):
    """Per-instrument windows + quality report; returns (res, quality, pooled bootstrap)."""
    res, quality = {}, {}
    for k in keys:
        days, o, h, l, c, y = load_instrument(k)
        frac, ok = quality_ok(h, l, o, c)
        quality[k] = {"bad_bar_fraction": frac, "sessions": len(days), "pass": ok,
                      "first": days[0], "last": days[-1]}
        if not ok or len(days) < INSTRUMENT_MIN_SESSIONS:
            continue
        res[k] = instrument_window(days, o, h, l, c, y, arms)
    rng = np.random.default_rng(SEED)
    per = {k: boot_instrument(k, rng, arms) for k in res}
    pooled = {a: np.concatenate([per[k][a] for k in res]) for a in arms}
    return res, quality, per, pooled


def gate_i(inst, pooled, arm="R2"):
    keys = sorted(inst)
    dc = {k: inst[k][arm]["cagr"] - inst[k]["C0"]["cagr"] for k in keys}
    dd = {k: inst[k][arm]["max_dd"] - inst[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    share_pos = float(np.mean([v > 0 for v in dc.values()]))
    share_dd = float(np.mean([v <= GATE_I_DD_SLACK + 1e-12 for v in dd.values()]))
    p = pooled[arm] - pooled["C0"]
    pooled_med = float(np.median(p))
    return {"arm": arm, "n_instruments": len(keys), "median_delta_cagr": med,
            "share_delta_cagr_gt_0": share_pos, "share_delta_dd_within_3pp": share_dd,
            "cagr_median": float(np.median([inst[k][arm]["cagr"] for k in keys])),
            "pooled_median_delta_cagr": pooled_med, "pooled_share_ge_0": float(np.mean(p >= 0)),
            "pooled_paths": int(len(p)),
            "pass_median": bool(med >= 0), "pass_share_pos": bool(share_pos >= GATE_I_SHARE_POS),
            "pass_share_dd": bool(share_dd >= GATE_I_DD_SHARE), "pass_pooled": bool(pooled_med >= 0),
            "deltas": dc, "dd_diffs": dd,
            "rule": (f"median dCAGR >= 0, share dCAGR > 0 >= {GATE_I_SHARE_POS:.0%}, "
                     f"share dDD <= +{GATE_I_DD_SLACK * 100:.0f} pp >= {GATE_I_DD_SHARE:.0%}, "
                     f"pooled bootstrap median d >= 0 ({GATE_I_PATHS} paths/instrument)")}


# ----------------------------------------------------------------------------- leave-one-episode-out
def leave_one_episode_out(dates, o, c, wants_c0, wants_r2, episodes, n_top=3):
    """Re-simulate R2 with each of its largest episodes' decisions replaced by C0's (episode flat)."""
    c0_nav = simulate_band(o, c, wants_c0, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    c0_cagr = trv2.cagr(c0_nav[0], c0_nav[-1], trv2.calendar_years(dates[0], dates[-1]))
    out = []
    for e in sorted(episodes, key=lambda x: -x["delta"])[:n_top]:
        k0 = dates.index(e["entry_decision"])
        i1 = dates.index(e["reconciliation_session"])
        w = wants_r2.copy()
        w[START - 1 + k0:START - 1 + i1] = wants_c0[START - 1 + k0:START - 1 + i1]
        nav = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
        cg = trv2.cagr(nav[0], nav[-1], trv2.calendar_years(dates[0], dates[-1]))
        out.append({"episode_entry": e["entry_decision"], "episode_last_held": e["last_held_session"],
                    "episode_delta_pp": e["delta"] * 100, "sessions_held": e["sessions_held"],
                    "cagr_with_episode_flat": float(cg), "cagr_delta_vs_C0": float(cg - c0_cagr)})
    return {"c0_cagr": float(c0_cagr), "episodes": out}


# ----------------------------------------------------------------------------- audit of fast_reentry_v1
def audit(days, o, c, price, e2, v20, wants, early_hist):
    """Look-ahead, fill-timing, state-machine and cost checks on the R2 mechanics."""
    x = {}
    # A1 truncation probe: the arm's decision at t must not change when the future is removed
    rng = np.random.default_rng(AUDIT_SEED)
    T = len(price)
    ts = sorted(rng.choice(np.arange(300, T), size=min(AUDIT_PROBES, T - 300), replace=False).tolist())
    mism = []
    for t in ts:
        w_t, ea_t = arm_signals(price[:t + 1], e2[:t + 1], v20[:t + 1], ARM_SPEC["R2"])
        if not (w_t[0, -1] == wants[t] and ea_t[0, -1] == early_hist[t]):
            mism.append(days[t])
    x["truncation_probe"] = {"probes": len(ts), "mismatches": mism, "pass": not mism,
                             "seasons": "R2 target and early flag at t from closes <= t"}
    # A2 direct recomputation of the early inputs at sampled sessions
    bad = []
    sma_n, med_w = ARM_SPEC["R2"]["sma"], ARM_SPEC["R2"]["med"]
    vol_p = trv2.vol_n(price)[0]
    comps = early_components(price, sma_n, med_w)
    for t in ts:
        want_sma = bool(t >= sma_n and price[t] > price[t - sma_n + 1:t + 1].mean())
        want_lag = bool(t >= LAG_FAST and price[t] > price[t - LAG_FAST])
        win = vol_p[t - med_w + 1:t + 1]
        want_med = float(np.median(win)) if (t >= med_w + 19 and np.all(np.isfinite(win))) else None
        if bool(comps[0][0, t]) != want_sma or bool(comps[3][0, t]) != want_lag:
            bad.append(days[t])
        got_med = float(comps[2][0, t])
        if want_med is None:
            if np.isfinite(got_med):
                bad.append(days[t])
        elif not (np.isfinite(got_med) and abs(got_med - want_med) < 1e-12):
            bad.append(days[t])
    x["trailing_window_recompute"] = {"probes": len(ts), "mismatches": bad, "pass": not bad}
    # A3 fill timing: the decision at the close of session i-1 is filled at the open of session i
    oo = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    cc = np.array([100.0, 100.0, 100.0, 200.0, 200.0])
    ww = np.array([0.0, 1.0, 1.0, 1.0, 1.0])
    nav = simulate_band(oo, cc, ww, 0.0, 1, C0_BAND)[0][0]
    x["fill_timing"] = {"nav": float(nav[-1]), "expected_close_last_over_open_session2": float(cc[-1] / oo[2]),
                        "pass": bool(abs(nav[-1] - cc[-1] / oo[2]) < 1e-12)}
    # A4 cost linearity: nav drag roughly doubles when the per-side cost doubles
    nav0 = simulate_band(o, c, wants, 0.0, START, C0_BAND)[0][0]
    nav1 = simulate_band(o, c, wants, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    nav2 = simulate_band(o, c, wants, COST_PER_SIDE * 4, START, C0_BAND)[0][0]
    d1 = float(1 - nav1[-1] / nav0[-1])
    d2 = float(1 - nav2[-1] / nav0[-1])
    x["cost_linearity"] = {"drag_4bps_m2": d1, "drag_8bps_m2": d2, "ratio": (d2 / d1 if d1 else None),
                           "pass": bool(d1 > 0 and 1.5 <= d2 / d1 <= 2.5)}
    # A5 state machine invariants
    inv = {
        "no_early_while_e2_on": not bool(np.any(early_hist & e2)),
        "early_only_when_e2_off": not bool(np.any(early_hist & e2)),
        "targets_within_cap": bool(np.all(wants <= CAP + 1e-12)),
        "targets_non_negative": bool(np.all(wants >= -1e-15)),
    }
    x["invariants"] = inv
    x["pass"] = bool(x["truncation_probe"]["pass"] and x["trailing_window_recompute"]["pass"]
                     and x["fill_timing"]["pass"] and x["cost_linearity"]["pass"] and all(inv.values()))
    return x


# ----------------------------------------------------------------------------- after tax (report only)
def after_tax_stage(wants, dates_tax=("C0", "R2")):
    """Corrected vt_level_tax_v1 engine at $100k on the three windows, C0 and R2."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing, as vt_level_tax_v1
    te.init(days, {a: (o, c) for a in dates_tax}, {a: wants[a] for a in dates_tax})
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"capital_usd": TAX_CAPITAL, "windows": {}, "anchors": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        out["windows"][wn] = {}
        for arm in dates_tax:
            pre = te.run_strategy(arm, "pre_tax", TAX_CAPITAL, a, b, mkt)
            prog = te.run_strategy(arm, "progressive", TAX_CAPITAL, a, b, mkt)
            out["windows"][wn][arm] = {
                "pre_tax": {"cagr": pre["cagr_usd_liquidated"], "max_dd": pre["max_dd_nav_usd"],
                            "trades_per_year": pre["strategy_trades"] / pre["years"]},
                "progressive": {"cagr": prog["cagr_usd_liquidated"], "max_dd": prog["max_dd_nav_usd"],
                                "tax_usd": prog["tax_total_usd_at_payment_fx"], "terminal_usd": prog["terminal_usd_liquidated"],
                                "trades_per_year": prog["strategy_trades"] / prog["years"]},
            }
        ref = TAX_ANCHORS[wn]
        got = out["windows"][wn]["C0"]
        out["anchors"][wn] = {
            "pre_tax": {"reference": ref["pre_tax"], "reproduced": got["pre_tax"]["cagr"],
                        "diff_pp": (got["pre_tax"]["cagr"] - ref["pre_tax"]) * 100},
            "progressive": {"reference": ref["progressive"], "reproduced": got["progressive"]["cagr"],
                            "diff_pp": (got["progressive"]["cagr"] - ref["progressive"]) * 100},
            "progressive_max_dd": {"reference": ref["progressive_max_dd"], "reproduced": got["progressive"]["max_dd"],
                                   "diff_pp": (got["progressive"]["max_dd"] - ref["progressive_max_dd"]) * 100},
        }
    out["pass"] = all(abs(v["diff_pp"]) <= 0.005 for wn in out["anchors"].values() for v in wn.values())
    return out


# ----------------------------------------------------------------------------- smoke (synthetic only)
def synthetic_bars(n, seed):
    """Random-walk synthetic bars for the smoke test (no real data)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    div = np.zeros(n)
    y = np.full(n, 0.01)
    d0 = date(2000, 1, 3)
    days = [(d0 + timedelta(days=k)).isoformat() for k in range(n)]
    return days, o, price, div, y


def smoke():
    """Run the whole path on synthetic data; assert the arm invariants."""
    days, o, price, div, y = synthetic_bars(1400, 7)
    so, sc = trv2.synthetic_2x(o, price, div, y, EXP_Q)
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(sc)[0]
    got = wants_by_arm(price, e2, v20, ARMS)
    checks = {}
    for arm in ARMS:
        w, ea = got[arm]
        assert w.shape == (1, len(price)), arm
        assert np.all(w <= CAP + 1e-12) and np.all(w >= 0), arm
        assert not np.any(ea[0] & e2), arm
        spec = ARM_SPEC[arm]
        if spec is not None:
            vals = (0.0, spec["w"], 1.0)
            for t in np.flatnonzero(w[0] > 0):
                if abs(w[0, t] - CAP) < 1e-12:  # cap binds at full production weight
                    continue
                f = w[0, t] / trv2.vt_of(v20[t])
                assert min(abs(f - v) for v in vals) < 1e-9, (arm, int(t), float(f))
        checks[arm] = {"mean_target": float(w.mean()), "early_share": float(ea.mean())}
    # main-window simulator + stats
    stats = {}
    for arm in ("C0", "R2"):
        nav, turn, ntr, codes, traded = simulate_band(o, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)
        assert np.all(np.isfinite(nav)) and np.all(nav > 0)
        stats[arm] = {"cagr": float(trv2.cagr(nav[0, 0], nav[0, -1], trv2.calendar_years(days[START - 1], days[-1]))),
                      "turnover": float(turn[0])}
    # instrument path on a second synthetic series
    _, o2, p2, _, _ = synthetic_bars(900, 11)
    h2 = np.maximum(o2, p2); l2 = np.minimum(o2, p2)
    frac, ok = quality_ok(h2, l2, o2, p2)
    iwin = instrument_window(days[:900], o2, h2, l2, p2, y[:900], ("C0", "R2"))
    # after-tax path on a synthetic market
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2027) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(len(days), 30.0), ufe)
    w_c0 = np.zeros(len(price)); w_c0[600:] = 0.5
    te.init(days, {"C0": (so, sc)}, {"C0": w_c0})
    tax = te.run_strategy("C0", "progressive", TAX_CAPITAL, days[300], days[-1], mkt)
    return {"checks": checks, "stats": stats, "instrument_quality": {"fraction": frac, "pass": ok},
            "instrument_window_cagr": {a: iwin[a]["cagr"] for a in ("C0", "R2")},
            "after_tax_cagr": tax["cagr_usd_liquidated"],
            "pass": bool(all(np.isfinite(list(stats[a].values())).all() for a in stats)
                         and np.isfinite(tax["cagr_usd_liquidated"]))}


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--smoke", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    if mode == "--smoke":
        s = smoke()
        print(json.dumps(s, indent=2, sort_keys=True))
        print("smoke PASS" if s["pass"] else "smoke FAIL")
        sys.exit(0 if s["pass"] else 1)

    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    n_bad = sum(1 for v in hc["v1_reproduction_checks"].values() if abs(v["diff_pp"]) > 0.01)
    print(f"harness v1 reproduction: {len(hc['v1_reproduction_checks'])} checks, {n_bad} outside 0.01 pp")
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}")
    print(f"harness rolling median fast == v1 reference: {hc['rolling_median_fast_equals_v1_reference']}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    main_res, ctx = main_stage(ARMS, extras=True, m_list=(1, 2, 4))
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

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g

    # ---- gate I: independent instruments ----
    inst, inst_quality, inst_boot_per, inst_boot_pooled = instrument_stage(("C0", "R2"), INSTRUMENTS)
    gi = gate_i(inst, inst_boot_pooled, "R2")
    excluded = sorted(k for k in INSTRUMENTS if k not in inst)
    print("instruments done")

    # ---- audit + report-only analyses ----
    days_b, arr_b, o_b, c_b, e2_b, v20_b = main_inputs(0.0)
    price_b = arr_b["QQQ"][1]
    aud = audit(days_b, o_b, c_b, price_b, e2_b, v20_b,
                ctx["base"]["wants"]["R2"], ctx["base"]["early"]["R2"])
    loo = leave_one_episode_out(ctx["base"]["dates"], o_b, c_b, ctx["base"]["wants"]["C0"],
                                ctx["base"]["wants"]["R2"],
                                main_res["base"]["m2"]["R2"]["early_episodes"])
    tax = after_tax_stage({"C0": ctx["base"]["wants"]["C0"], "R2": ctx["base"]["wants"]["R2"]})
    print("audit, leave-one-out and after-tax done")

    # ---- gates P and I, verdict ----
    gp = gate_p(main_res)
    verdict = "CONFIRMED" if (gp["pass"] and gi["pass_median"] and gi["pass_share_pos"]
                              and gi["pass_share_dd"] and gi["pass_pooled"] and hc["pass"]
                              and aud["pass"] and tax["pass"]) else "FAIL"
    gate_keys_i = ["pass_median", "pass_share_pos", "pass_share_dd", "pass_pooled"]
    gi["pass_all"] = bool(all(gi[k] for k in gate_keys_i))

    out = {
        "campaign": "fast_reentry_v2",
        "verdict": verdict,
        "params": {"arms": list(ARMS), "neighbours": list(NEIGHBOURS), "arm_spec": {a: ARM_SPEC[a] for a in ARMS},
                   "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "c0_band": C0_BAND, "cap": CAP,
                   "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q, "main_window_cost_multipliers": [1, 2, 4],
                   "financing_spread": FIN, "momentum_lag": LAG_FAST,
                   "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                                      "back_extension": EXT, "measured": MEASURED, "m": 2},
                   "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                                     "returns_per_path": OOS_BOOT_L, "m": 2},
                   "gate_i": {"paths_per_instrument": GATE_I_PATHS, "seed": SEED, "block": BLOCK,
                              "returns_per_path": OOS_BOOT_L, "financing_spread": EXTRA_FIN, "m": 2,
                              "instruments": list(INSTRUMENTS), "quality_rule_max_bad_fraction": QUALITY_MAX_BAD_FRAC},
                   "signals": "production E2 1.1.0 on price closes: SMA200, 252-session price return > 0, "
                              "close > 126-session max/min midpoint; 2 of 3"},
        "harness_check": hc,
        "audit": aud,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates_g1_g9": gates,
        "gate_P_plateau": gp,
        "gate_I_instruments": gi,
        "instruments": {"quality": inst_quality, "excluded": excluded, "windows": inst,
                        "bootstrap_median": {a: float(np.median(inst_boot_pooled[a])) for a in ("C0", "R2")}},
        "leave_one_episode_out": loo,
        "after_tax_100k": tax,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"fast_reentry_v2 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"C0 = production E2 1.1.0 | verdict {verdict}",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} (max |diff| "
         f"{max(abs(v['diff_pp']) for v in hc['deviation_checks'].values()):.6f} pp vs e2_integration_v1; "
         f"v1 reproduction {'PASS' if hc['v1_reproduction_pass'] else 'FAIL'}; "
         f"band sim == simulate_multi {hc['band_simulator_equals_simulate_multi']}; "
         f"median fast == ref {hc['rolling_median_fast_equals_v1_reference']})", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot20 median, median(arm-C0) | "
                 f"OOS median dCAGR | m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            d = np.median([oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in oos])
            L.append(f"  {arm:8s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+.3f}pp | {d * 100:+.3f}pp | "
                     f"{p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("gate P (plateau, base m=2): neighbour | dCAGR vs C0 | maxDD | dDD vs C0 | beats | dd_ok")
    for arm in NEIGHBOURS:
        v = gp["detail"][arm]
        L.append(f"  {arm:8s} {v['cagr_delta_vs_C0'] * 100:+7.3f}pp {p(v['max_dd'])} {v['max_dd_delta_vs_C0'] * 100:+7.3f}pp "
                 f"{'yes' if v['beats_c0'] else 'no ':>4s} {'ok' if v['dd_ok'] else 'BREACH'}")
    L.append(f"  -> {gp['neighbours_beating_C0']}/{len(NEIGHBOURS)} beat C0 (need {GATE_P_MIN_NEIGHBOURS}); "
             f"{gp['neighbours_with_dd_ok']}/{len(NEIGHBOURS)} within C0 + 1 pp -> "
             f"gate P {'PASS' if gp['pass'] else 'FAIL'}")
    L.append("")
    L.append(f"gate I (21 instruments, calibrated m=2): median dCAGR {gi['median_delta_cagr'] * 100:+.3f}pp | "
             f"share>0 {gi['share_delta_cagr_gt_0'] * 100:.1f}% | DD<=+3pp {gi['share_delta_dd_within_3pp'] * 100:.1f}% | "
             f"pooled boot median d {gi['pooled_median_delta_cagr'] * 100:+.3f}pp ({gi['pooled_paths']} paths) -> "
             f"gate I {'PASS' if gi['pass_all'] else 'FAIL'}")
    L.append("  excluded by quality rule: " + (", ".join(excluded) if excluded else "none"))
    L.append("")
    L.append("per-instrument dCAGR (R2 - its own C0), calibrated m=2, sorted:")
    for k in sorted(gi["deltas"], key=lambda z: -gi["deltas"][z]):
        L.append(f"  {k:4s} {gi['deltas'][k] * 100:+7.3f}pp  dDD {gi['dd_diffs'][k] * 100:+7.3f}pp")
    L.append("")
    L.append(f"trades (base, m=2): arm | round trips/yr, resizes/yr, sells/yr, turnover/yr | 2017+ turnover/yr")
    for arm in ARMS:
        e = main_res["base"]["m2"][arm]["events_full"]
        f = main_res["base"]["m2"][arm]["events_2017_on"]
        L.append(f"  {arm:8s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{e['turnover_per_year']:5.2f} | {f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap median(arm-C0): " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_C0_median'] * 100:+.3f}pp" for a in CANDIDATES))
    L.append("")
    L.append("gates G1-G9 (" + " ".join(GATE_KEYS) + "):")
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:8s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("")
    L.append("early path (base, m=2): arm | early share | episodes | sum(arm-C0) | median episode d")
    for arm in CANDIDATES:
        a = main_res["base"]["m2"][arm]
        eps = a["early_episodes"]
        d = np.array([e["delta"] for e in eps]) if eps else np.array([0.0])
        L.append(f"  {arm:8s} {a['early_time_in_market'] * 100:6.2f}% {len(eps):4d} {d.sum() * 100:+8.2f}pp "
                 f"{np.median(d) * 100:+8.2f}pp")
    L.append("")
    L.append("leave one episode out (base, m=2, R2 with the episode flat): episode | episode d | CAGR | dCAGR vs C0")
    L.append(f"  (C0 CAGR {loo['c0_cagr'] * 100:.2f}%, R2 CAGR "
             f"{main_res['base']['m2']['R2']['cagr'] * 100:.2f}%)")
    for e in loo["episodes"]:
        L.append(f"  {e['episode_entry']} -> {e['episode_last_held']} {e['episode_delta_pp']:+7.2f}pp "
                 f"{e['cagr_with_episode_flat'] * 100:6.2f}% {e['cagr_delta_vs_C0'] * 100:+7.3f}pp")
    L.append("")
    L.append("after tax ($100k, Turkish progressive, liquidated): window | arm | pre-tax CAGR -> after-tax CAGR, "
             "max DD, tax USD")
    for wn in TAX_WINDOWS:
        for arm in ("C0", "R2"):
            x = tax["windows"][wn][arm]
            L.append(f"  {wn:22s} {arm:3s} {x['pre_tax']['cagr'] * 100:6.2f}% -> {x['progressive']['cagr'] * 100:6.2f}%  "
                     f"DD {x['progressive']['max_dd'] * 100:5.2f}%  tax {x['progressive']['tax_usd']:>12,.0f} USD")
    L.append(f"  anchors vs vt_level_tax_v1: {'PASS' if tax['pass'] else 'FAIL'}")
    L.append("")
    L.append(f"audit: look-ahead {aud['truncation_probe']['pass']}, trailing-window recompute "
             f"{aud['trailing_window_recompute']['pass']}, fill timing {aud['fill_timing']['pass']}, "
             f"cost linearity {aud['cost_linearity']['pass']}, invariants {all(aud['invariants'].values())} "
             f"-> {'PASS' if aud['pass'] else 'FAIL'}")
    L.append("")
    L.append(f"VERDICT: {verdict} (gate P {'PASS' if gp['pass'] else 'FAIL'}, "
             f"gate I {'PASS' if gi['pass_all'] else 'FAIL'})")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    if verdict != "CONFIRMED":
        sys.exit(0)  # a clean FAIL is a result; the caller reads the verdict from RESULTS.json


if __name__ == "__main__":
    main()
