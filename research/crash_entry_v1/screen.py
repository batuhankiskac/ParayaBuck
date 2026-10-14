#!/usr/bin/env python3
"""crash_entry_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Question: does a separate crash-recovery entry path -- active only while production R2's own
E2 vote and R2's early path are both off and QQQ sits at least 20% below its 252-session high --
recover part of the V-recovery that production R2 misses, without worsening drawdown?

Control (C0) is production R2 (`equity_leveraged_trend_v1` 1.2.0). Arms (fixed by the
pre-registration): K1/K2/K3, R2 plus the crash path at 0.5x/0.25x/0.5x production weight, with
the vol condition vol20 < 0.7/0.7/0.6 x the trailing 40-session vol20 maximum.

Modes
  --smoke    plumbing only, on synthetic random prices: every arm spec, the invariants and the
             after-tax engine run end to end. Touches no real data.
  --harness  real data, controls only: E2 reproduces e2_integration_v1's production-E2
             deviation row to 0.01 pp (base/calibrated x m=1/m=2) AND the recomputed E2 / R2
             main-window rows equal research/fast_reentry_v1/RESULTS.json to 0.01 pp; the band
             simulator at 0.5 pp equals trend_robustness_v2.simulate_multi bit for bit; the
             fast rolling median equals the v1 loop reference bit for bit; and the after-tax
             engine at $100k reproduces the published E2 and R2 rows.
  --run      re-runs --harness, stops if it fails, then runs C0/K1/K2/K3 on the main window,
             the main block-20 bootstrap, the out-of-sample windows (^GSPC 1957-1999 and
             e2_confirmation_v1's M1..M5), the pooled OOS bootstrap, the unchanged G1-G9 gates
             vs R2, the per-episode P&L of the crash path, the leave-one-episode-out analysis,
             the audit checks, the corrected after-tax engine at $100k and the clean after-tax
             bootstraps B1-recent (progressive) and B2 (flat 20%). Writes RESULTS.json and
             SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics, bootstrap, OOS loaders) are the
fast_reentry_v1/v2 screen, itself built on research/trend_robustness_v2/screen.py (whose
main() is never run). The after-tax engine is research/crash_entry_v1/tax_engine.py, a copy of
research/vt_level_tax_v1/screen.py's corrected engine (plus its flat-tax variant and the
run_strategy_flat session loop).
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

# arms: spec = None (production E2 1.1.0; harness reference only) or a dict with
#   "r2":    production R2's early path {sma, med, w}
#   "crash": the crash-recovery path {sma, lag, dd, high, volmax, w, vol_ratio} (optional)
R2_SPEC = {"sma": 50, "med": 252, "w": 0.5}  # production R2 early path (fast_reentry_v1/v2)
CRASH = {"sma": 20, "lag": 10, "dd": 0.20, "high": TSM_N, "volmax": 40}  # crash-path entry gate
ARM_SPEC = {
    "E2": None,
    "C0": {"r2": dict(R2_SPEC)},
    "K1": {"r2": dict(R2_SPEC), "crash": dict(CRASH, w=0.5, vol_ratio=0.7)},
    "K2": {"r2": dict(R2_SPEC), "crash": dict(CRASH, w=0.25, vol_ratio=0.7)},
    "K3": {"r2": dict(R2_SPEC), "crash": dict(CRASH, w=0.5, vol_ratio=0.6)},
}
ARMS = tuple(ARM_SPEC)
CANDIDATES = ("K1", "K2", "K3")

# after-tax (report only): vt_level_tax_v1's windows at $100k
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
# Published after-tax references (liquidated, Turkish progressive), used by --harness:
#   E2 = vt_level_tax_v1's C0 anchors (TAX_ANCHORS in that study's screen.py);
#   R2 = fast_reentry_v2's R2 row (its RESULTS.json after_tax_100k, tag
#        archive/research/fast-reentry-v2-2026-10), which the v2 report publishes as
#        10.16% / 14.71% / 22.89% after tax.
TAX_ANCHORS = {
    "2000_03_to_2026_09": {
        "E2": {"pre_tax": 0.146715, "progressive": 0.095340, "progressive_max_dd": 0.495643},
        "R2": {"pre_tax": 0.154732, "progressive": 0.101600, "progressive_max_dd": 0.467584},
    },
    "2010_01_to_end": {
        "E2": {"pre_tax": 0.218852, "progressive": 0.144968, "progressive_max_dd": 0.446920},
        "R2": {"pre_tax": 0.221587, "progressive": 0.147059, "progressive_max_dd": 0.437839},
    },
    "2017_01_to_end": {
        "E2": {"pre_tax": 0.322564, "progressive": 0.226932, "progressive_max_dd": 0.376052},
        "R2": {"pre_tax": 0.325330, "progressive": 0.228861, "progressive_max_dd": 0.362426},
    },
}
TAX_ANCHOR_TOL_PP = 0.02  # R2's published anchors are 2 dp, so 0.02 pp is the tightest safe bar

# clean after-tax bootstrap (B1-recent / B2): tax_asym_v2 design, calendar held real
TAX_BOOT_PATHS = 1000
TAX_BOOT_EXT, TAX_BOOT_WARM, TAX_BOOT_MEASURED = 3, 253, 10 * 252  # fixed_calendar 2776 sessions
TAX_BOOT_L = TAX_BOOT_WARM + TAX_BOOT_MEASURED
TAX_BOOT_I0 = TAX_BOOT_EXT + TAX_BOOT_WARM
TAX_BOOT_CAL = ("2015-09-01", "2026-09-30")
GATE_B_SHARE = 0.55  # share of paths with a positive delta
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


def _rolling_max_full(x, window):
    """Trailing max over `window` sessions, NaN unless every session in the window is finite."""
    x = np.atleast_2d(x)
    P, T = x.shape
    out = np.full((P, T), np.nan)
    if T < window:
        return out
    fin = np.isfinite(x)
    ok = sliding_window_view(fin, window, axis=1).all(axis=2)
    mx = sliding_window_view(x, window, axis=1).max(axis=2)
    out[:, window - 1:] = np.where(ok, mx, np.nan)
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


def crash_components(price, sma_n, lag, high_window, volmax_window):
    """Path-independent crash-path inputs: SMA_n vote, vol20, 252-session high of the close,
    trailing 40-session max of vol20, and close > close[-lag]."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma_up_v = trv2.sma_up(price, sma_n)
    vol_p = trv2.vol_n(price)
    rmax = _rolling_max_full(price, high_window)
    vmax = _rolling_max_full(vol_p, volmax_window)
    close_lag = np.full_like(price, np.nan)
    close_lag[:, lag:] = price[:, :-lag]
    return sma_up_v, vol_p, rmax, vmax, price > close_lag


def comps_for(price, key, cache):
    """Cache the path-independent inputs by their parameter tuple (arms share them)."""
    if key not in cache:
        if key[0] == "r2":
            _, sma_n, med_window = key
            cache[key] = early_components(price, sma_n, med_window)
        else:
            _, sma_n, lag, high_window, volmax_window = key
            cache[key] = crash_components(price, sma_n, lag, high_window, volmax_window)
    return cache[key]


def arm_signals(price, e2_on, vol_instr, spec, comps=None):
    """Return (target weights [P, T], early_active [P, T], crash_active [P, T]) for one arm.

    spec is None for production E2 1.1.0 or {"r2": {...}, "crash": {...}|absent}. All paths are
    stateful and evaluated at the close on the price series:

      R2 early path (production): while E2 is off, enter at w x vt if close > SMA(50) and
        vol20 < its trailing 252-session median and close > close[-20]; exit if close < SMA(50);
        the path ends the moment E2's own vote turns on.
      Crash path (K arms only, one state, shared by K1..K3 through their own spec): while E2 and
        the R2 early path are both off, enter at w x vt if close > SMA(20) and close is >= dd
        below its 252-session high and vol20 < vol_ratio x the trailing 40-session max of vol20
        and close > close[-10]; exit if close < SMA(20) or the moment E2 or the R2 early path
        takes over. The drawdown condition is an entry gate only (the pre-registration fixes
        those two exits).

    Priority of the weight factor: E2 (1.0) > R2 early (r2.w) > crash (crash.w) > 0.
    """
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vt = trv2.vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)))
    P, T = price.shape
    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    crash_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    if spec is None:
        on_eff[:, :] = e2_on
        factor[:, :] = e2_on.astype(float)
    else:
        r2 = spec["r2"]
        cr = spec.get("crash")
        if comps is None:
            comps = {"r2": early_components(price, r2["sma"], r2["med"])}
            if cr is not None:
                comps["crash"] = crash_components(price, cr["sma"], cr["lag"], cr["high"], cr["volmax"])
        sma_up_v, vol_p, med_p, lag_cond = comps["r2"]
        if cr is not None:
            csma, cvol, crmax, cvmax, clag = comps["crash"]
            crash_ready = (csma & np.isfinite(crmax) & (price <= (1.0 - cr["dd"]) * crmax)
                           & np.isfinite(cvmax) & (cvol < cr["vol_ratio"] * cvmax) & clag)
        early = np.zeros(P, dtype=bool)
        crash = np.zeros(P, dtype=bool)
        for t in range(T):
            e2t = e2_on[:, t]
            # --- production R2 early path ---
            early = np.where(e2t, False, early)
            early = np.where(early & (~sma_up_v[:, t]) & (~e2t), False, early)  # exit: close < SMA
            entry = sma_up_v[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
            can_enter = (~early) & (~e2t) & np.isfinite(med_p[:, t])
            early = np.where(can_enter & entry, True, early)
            # --- crash-recovery path (only while E2 and the early path are both off) ---
            if cr is not None:
                crash = np.where(e2t | early, False, crash)  # E2 or the early path takes over
                crash = np.where(crash & (~csma[:, t]), False, crash)  # exit: close < SMA20
                can_crash = (~crash) & (~e2t) & (~early)
                crash = np.where(can_crash & crash_ready[:, t], True, crash)
            on_eff[:, t] = e2t | early | crash
            early_hist[:, t] = early
            crash_hist[:, t] = crash
            factor[:, t] = np.where(e2t, 1.0, np.where(early, r2["w"],
                                                        np.where(crash, cr["w"] if cr is not None else 0.0, 0.0)))
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap, early_hist, crash_hist


def arm_wants(price, e2, vol_instr, arm, cache):
    """Target weights of one arm, sharing the path-input cache (per price series) across arms."""
    spec = ARM_SPEC[arm]
    comps = None
    if spec is not None:
        comps = {"r2": comps_for(price, ("r2", spec["r2"]["sma"], spec["r2"]["med"]), cache)}
        if "crash" in spec:
            c = spec["crash"]
            comps["crash"] = comps_for(price, ("crash", c["sma"], c["lag"], c["high"], c["volmax"]), cache)
    return arm_signals(price, e2, vol_instr, spec, comps)


def wants_by_arm(price, e2, vol_instr, arms):
    """{arm: (wants, early_hist, crash_hist)} sharing the per-parameter path inputs."""
    cache = {}
    return {arm: arm_wants(price, e2, vol_instr, arm, cache) for arm in arms}


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


def arm_stats(dates, nav, turn, ntr, codes, traded, wants, start, extras, early_active=None, crash_active=None):
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
        sl = slice(start - 1, start - 1 + len(tgt))
        if early_active is not None:
            st["early_time_in_market"] = float(np.atleast_2d(early_active)[0, sl].mean())
        if crash_active is not None:
            st["crash_time_in_market"] = float(np.atleast_2d(crash_active)[0, sl].mean())
        st["sessions_target_below_10pp_in_trend"] = int(np.sum((tgt > 0) & (tgt < 0.10)))
    return st


# ----------------------------------------------------------------------------- main window
def early_episodes(dates, nav, nav_c0, active_aligned):
    """Per-episode P&L of a re-entry path (report-only, base financing, m = 2).

    active_aligned[k] is the path's decision at the close of dates[k], so the episode holds the
    path's position over the sessions k0+1 .. k1+1. The P&L span runs from the close of dates[k0]
    to the close of the reconciliation session (the next open after the path stops driving the
    target), where the path-driven position is either sold to cash or merged into production E2's
    position. That includes the exit fill and the overnight gap the path position earned, so the
    product of (1 + delta) over episodes reproduces the arm's total relative gain. `c0_return` is
    the control's NAV return over the identical span."""
    ea = np.asarray(active_aligned, dtype=bool)
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
                w, ea, ca = got[arm]
                nav, turn, ntr, codes, traded = simulate_band(o, c, w, COST_PER_SIDE * m, START, C0_BAND)
                navs[arm] = nav[0]
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], w[0], START,
                                    extras and m == 2, early_active=ea, crash_active=ca)
            if extras and m == 2 and "C0" in arms:
                for arm in arms:
                    if arm != "C0":
                        hm[arm]["early_episodes"] = early_episodes(
                            dates, navs[arm], navs["C0"], got[arm][1][0][START - 1:])
                        hm[arm]["crash_episodes"] = early_episodes(
                            dates, navs[arm], navs["C0"], got[arm][2][0][START - 1:])
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
        ctx[md] = {"days": days, "o": o, "c": c, "dates": dates,
                   "wants": {a: got[a][0][0] for a in arms},
                   "early": {a: got[a][1][0] for a in arms},
                   "crash": {a: got[a][2][0] for a in arms}}
    return res, ctx


# ----------------------------------------------------------------------------- harness
def jload(path):
    return json.loads(Path(path).read_text())


def harness():
    ref = jload(DEVIATION)
    h, hctx = main_stage(("E2", "C0"))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = h[md][f"m{m}"]["E2"]
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

    # recomputed E2 / C0(=production R2) main-window rows vs fast_reentry_v1's RESULTS.json (0.01 pp)
    v1 = jload(V1_RESULTS)
    v1_checks = {}
    for md in FIN:
        for m in (1, 2):
            r1 = v1["main_window"][md][f"m{m}"]
            h2 = h[md][f"m{m}"]
            for arm, ref_arm in (("E2", "C0"), ("C0", "R2")):
                for k in ("cagr", "max_dd"):
                    d = (h2[arm][k] - r1[ref_arm][k]) * 100
                    v1_checks[f"{md}_m{m}_{arm}_{k}"] = {"reference": r1[ref_arm][k], "reproduced": h2[arm][k],
                                                         "diff_pp": d}
                if m == 2:
                    d = h2[arm]["turnover_annual"] - r1[ref_arm]["turnover_annual"]
                    v1_checks[f"{md}_m2_{arm}_turnover"] = {"reference": r1[ref_arm]["turnover_annual"],
                                                            "reproduced": h2[arm]["turnover_annual"], "diff_pp": d}
    ok_v1 = all(abs(v["diff_pp"]) <= 0.01 for v in v1_checks.values())
    # v1's R2 bootstrap median (base) as a further anchor
    boot_check = None
    try:
        b1 = v1["bootstrap_main_block20_m2"]["base"]["R2"]["diff_vs_C0_median"]
        boot_check = {"v1_reference_pp": b1 * 100}
    except KeyError:
        pass

    # after-tax anchors: E2 (vt_level_tax_v1) and R2 (fast_reentry_v2) at $100k, liquidated
    tax = after_tax_stage({"E2": hctx["base"]["wants"]["E2"], "C0": hctx["base"]["wants"]["C0"]},
                          dates_tax=("E2", "C0"))
    tax_checks = {}
    for wn in TAX_WINDOWS:
        got = tax["windows"][wn]
        for pub_arm, arm in (("E2", "E2"), ("R2", "C0")):
            ref_a = TAX_ANCHORS[wn][pub_arm]
            for key, got_key in (("pre_tax", ("pre_tax", "cagr")), ("progressive", ("progressive", "cagr")),
                                 ("progressive_max_dd", ("progressive", "max_dd"))):
                g = got[arm][got_key[0]][got_key[1]]
                tax_checks[f"{wn}_{pub_arm}_{key}"] = {"reference": ref_a[key], "reproduced": g,
                                                       "diff_pp": (g - ref_a[key]) * 100}
    ok_tax = all(abs(v["diff_pp"]) <= TAX_ANCHOR_TOL_PP for v in tax_checks.values())

    ok = ok_dev and sim_equal and med_equal and ok_v1 and ok_tax
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "rolling_median_fast_equals_v1_reference": med_equal,
            "v1_reproduction_checks": v1_checks, "v1_reproduction_pass": ok_v1,
            "after_tax_anchor_checks": tax_checks, "after_tax_anchor_tolerance_pp": TAX_ANCHOR_TOL_PP,
            "after_tax_anchor_pass": ok_tax,
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
                w = arm_wants(pc, e2, vol_instr, arm, cache)[0]
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


# ----------------------------------------------------------------------------- leave-one-episode-out
def leave_one_episode_out(dates, o, c, wants_c0, wants_arm, episodes, n_top=3):
    """Re-simulate an arm with each of its largest path episodes replaced by C0's decisions."""
    c0_nav = simulate_band(o, c, wants_c0, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    c0_cagr = trv2.cagr(c0_nav[0], c0_nav[-1], trv2.calendar_years(dates[0], dates[-1]))
    out = []
    for e in sorted(episodes, key=lambda x: -x["delta"])[:n_top]:
        k0 = dates.index(e["entry_decision"])
        i1 = dates.index(e["reconciliation_session"])
        w = wants_arm.copy()
        w[START - 1 + k0:START - 1 + i1] = wants_c0[START - 1 + k0:START - 1 + i1]
        nav = simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
        cg = trv2.cagr(nav[0], nav[-1], trv2.calendar_years(dates[0], dates[-1]))
        out.append({"episode_entry": e["entry_decision"], "episode_last_held": e["last_held_session"],
                    "episode_delta_pp": e["delta"] * 100, "sessions_held": e["sessions_held"],
                    "cagr_with_episode_flat": float(cg), "cagr_delta_vs_C0": float(cg - c0_cagr)})
    return {"c0_cagr": float(c0_cagr), "episodes": out}


# ----------------------------------------------------------------------------- audit of the harness
def audit(days, o, c, price, e2, v20, wants_c0, early_c0, wants_k1, early_k1, crash_k1):
    """Look-ahead, fill-timing, state-machine and cost checks on the R2 and crash-path mechanics."""
    x = {}
    rng = np.random.default_rng(AUDIT_SEED)
    T = len(price)
    ts = sorted(rng.choice(np.arange(300, T), size=min(AUDIT_PROBES, T - 300), replace=False).tolist())

    # A1 truncation probes: the arms' decisions at t must not change when the future is removed
    for label, arm, wants, early_h, crash_h in (("C0", "C0", wants_c0, early_c0, None),
                                                ("K1", "K1", wants_k1, early_k1, crash_k1)):
        mism = []
        for t in ts:
            w_t, e_t, c_t = arm_signals(price[:t + 1], e2[:t + 1], v20[:t + 1], ARM_SPEC[arm])
            if not (w_t[0, -1] == wants[t] and e_t[0, -1] == early_h[t]
                    and (crash_h is None or c_t[0, -1] == crash_h[t])):
                mism.append(days[t])
        x[f"truncation_probe_{label}"] = {
            "probes": len(ts), "mismatches": mism, "pass": not mism,
            "checks": "target, early flag and crash flag at t recomputed from closes <= t"}

    # A2 direct recomputation of the path inputs at sampled sessions
    bad = []
    sma_n, med_w, lag_r2 = R2_SPEC["sma"], R2_SPEC["med"], LAG_FAST
    cr = ARM_SPEC["K1"]["crash"]
    vol_p = trv2.vol_n(price)[0]
    ec = early_components(price, sma_n, med_w)
    cc = crash_components(price, cr["sma"], cr["lag"], cr["high"], cr["volmax"])
    for t in ts:
        want_sma = bool(t >= sma_n and price[t] > price[t - sma_n + 1:t + 1].mean())
        want_lag = bool(t >= lag_r2 and price[t] > price[t - lag_r2])
        win = vol_p[t - med_w + 1:t + 1]
        want_med = float(np.median(win)) if (t >= med_w + 19 and np.all(np.isfinite(win))) else None
        if bool(ec[0][0, t]) != want_sma or bool(ec[3][0, t]) != want_lag:
            bad.append(days[t])
        got_med = float(ec[2][0, t])
        if want_med is None:
            if np.isfinite(got_med):
                bad.append(days[t])
        elif not (np.isfinite(got_med) and abs(got_med - want_med) < 1e-12):
            bad.append(days[t])
        # crash-path inputs: SMA20, 252-session high, 40-session vol max, 10-session lag
        csma, cvol, crmax, cvmax, clag = cc
        w_sma = bool(t >= cr["sma"] and price[t] > price[t - cr["sma"] + 1:t + 1].mean())
        w_high = float(price[t - cr["high"] + 1:t + 1].max()) if t >= cr["high"] - 1 else None
        w_vmax = (float(np.nanmax(vol_p[t - cr["volmax"] + 1:t + 1]))
                  if t >= cr["volmax"] - 1 and np.all(np.isfinite(vol_p[t - cr["volmax"] + 1:t + 1])) else None)
        w_lag = bool(t >= cr["lag"] and price[t] > price[t - cr["lag"]])
        if bool(csma[0, t]) != w_sma or bool(clag[0, t]) != w_lag:
            bad.append(days[t])
        g_high = float(crmax[0, t])
        if (w_high is None and np.isfinite(g_high)) or (w_high is not None and abs(g_high - w_high) > 1e-9):
            bad.append(days[t])
        g_vmax = float(cvmax[0, t])
        if (w_vmax is None and np.isfinite(g_vmax)) or (w_vmax is not None and abs(g_vmax - w_vmax) > 1e-12):
            bad.append(days[t])
    x["trailing_window_recompute"] = {"probes": len(ts), "mismatches": bad, "pass": not bad,
                                      "checks": "SMA50/vol-median/lag20 and SMA20/252-session high/"
                                                "40-session vol20 max/lag10 recomputed directly"}

    # A3 fill timing: the decision at the close of session i-1 is filled at the open of session i
    oo = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    cc_ = np.array([100.0, 100.0, 100.0, 200.0, 200.0])
    ww = np.array([0.0, 1.0, 1.0, 1.0, 1.0])
    nav = simulate_band(oo, cc_, ww, 0.0, 1, C0_BAND)[0][0]
    x["fill_timing"] = {"nav": float(nav[-1]), "expected_close_last_over_open_session2": float(cc_[-1] / oo[2]),
                        "pass": bool(abs(nav[-1] - cc_[-1] / oo[2]) < 1e-12)}

    # A4 cost linearity: nav drag roughly doubles when the per-side cost doubles (K1's target)
    nav0 = simulate_band(o, c, wants_k1, 0.0, START, C0_BAND)[0][0]
    nav1 = simulate_band(o, c, wants_k1, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    nav2 = simulate_band(o, c, wants_k1, COST_PER_SIDE * 4, START, C0_BAND)[0][0]
    d1 = float(1 - nav1[-1] / nav0[-1])
    d2 = float(1 - nav2[-1] / nav0[-1])
    x["cost_linearity"] = {"drag_4bps_m2": d1, "drag_8bps_m2": d2, "ratio": (d2 / d1 if d1 else None),
                           "pass": bool(d1 > 0 and 1.5 <= d2 / d1 <= 2.5)}

    # A5 state-machine invariants
    inv = {
        "no_early_or_crash_while_e2_on": not bool(np.any(early_c0 & e2)) and not bool(np.any(early_k1 & e2))
                                          and not bool(np.any(crash_k1 & e2)),
        "crash_only_when_early_off": not bool(np.any(crash_k1 & early_k1)),
        "targets_within_cap": bool(np.all(wants_c0 <= CAP + 1e-12) and np.all(wants_k1 <= CAP + 1e-12)),
        "targets_non_negative": bool(np.all(wants_c0 >= -1e-15) and np.all(wants_k1 >= -1e-15)),
    }
    x["invariants"] = inv
    x["pass"] = bool(x["truncation_probe_C0"]["pass"] and x["truncation_probe_K1"]["pass"]
                     and x["trailing_window_recompute"]["pass"] and x["fill_timing"]["pass"]
                     and x["cost_linearity"]["pass"] and all(inv.values()))
    return x


# ----------------------------------------------------------------------------- after tax (report only)
def after_tax_stage(wants, dates_tax=("E2", "C0"), published=None):
    """Corrected vt_level_tax_v1 engine at $100k on the three windows (base financing, m = 2).

    `published` maps a window to {published arm name -> arm in `dates_tax`} for the anchor
    report (--harness): E2 -> vt_level_tax_v1's C0 row, R2 -> fast_reentry_v2's R2 row.
    """
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
        out["anchors"][wn] = {}
        for pub_arm, arm in (published or {}).get(wn, {}).items():
            ref, got = TAX_ANCHORS[wn][pub_arm], out["windows"][wn][arm]
            for key, got_key in (("pre_tax", ("pre_tax", "cagr")), ("progressive", ("progressive", "cagr")),
                                 ("progressive_max_dd", ("progressive", "max_dd"))):
                g = got[got_key[0]][got_key[1]]
                out["anchors"][wn][f"{pub_arm}_{key}"] = {"reference": ref[key], "reproduced": g,
                                                          "diff_pp": (g - ref[key]) * 100}
    out["pass"] = bool(all(abs(v["diff_pp"]) <= TAX_ANCHOR_TOL_PP for wn in out["anchors"].values()
                       for v in wn.values())
                       and any(out["anchors"].values()))
    return out


# ----------------------------------------------------------------------------- clean after-tax bootstrap (B1/B2)
def _fixed_calendar(days, fx, cal_lo, cal_hi, need):
    """The real (CBDT USD/TRY) trading calendar, first `need` sessions of [cal_lo, cal_hi]."""
    idx = [i for i, d in enumerate(days) if cal_lo <= d <= cal_hi]
    if len(idx) < need:
        raise RuntimeError(f"fixed calendar {cal_lo}..{cal_hi} has only {len(idx)} sessions, need {need}")
    idx = idx[:need]
    return [days[i] for i in idx], np.array([fx[i] for i in idx], dtype=float)


def after_tax_bootstrap(arms, n_paths=TAX_BOOT_PATHS, capital=TAX_CAPITAL, seed=SEED):
    """Clean after-tax bootstrap (tax_asym_v2's design, corrected control).

    1,000 block-20 paths of ten measured years (2,520 sessions, 3-session back-extension) drawn
    from QQQ open/close/dividend and DTB3; the signal is re-computed on the resampled price level
    and the market returns are resampled while the *calendar* (and therefore USD/TRY and Yi-UFE)
    stays real on a fixed 2015-09 -> 2026-09 window. B1 = Turkish progressive tariff, B2 = flat
    20% on the annual net realised USD gain with loss carry-forward. Financing is base (DTB3),
    m = 2, as in the pre-tax window. All arms share each path.
    """
    days_all, arr, y = trv2.load()
    q = arr["QQQ"]
    T = q.shape[1]
    fx_all = te.load_fx(FX_CSV, days_all)
    ufe = te.load_ufe(UFE_CSV)
    fixed_days, fixed_fx = _fixed_calendar(days_all, fx_all, *TAX_BOOT_CAL, TAX_BOOT_EXT + TAX_BOOT_L)
    mkt = te.Market(fixed_days, fixed_fx, ufe)
    rng = np.random.default_rng(seed)
    pool = np.arange(1, T)
    npool = len(pool)
    out = {arm: {"b1": [], "b2": [], "b1_dd": [], "b2_dd": []} for arm in arms}
    for _ in range(n_paths):
        P, L, EXTn = 1, TAX_BOOT_L, TAX_BOOT_EXT
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXTn, 0, -1)], axis=1)
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
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp, EXP_Q)  # base financing
        vol_instr = trv2.vol_n(sc)
        cache = {}
        wants = {a: arm_wants(pc, e2, vol_instr, a, cache)[0][0] for a in arms}
        te.init(fixed_days, {a: (so[0], sc[0]) for a in arms}, wants)
        i1 = len(fixed_days) - 1
        for a in arms:
            for kind, key in (("progressive", "b1"), ("flat", "b2")):
                r = (te.run_strategy(a, kind, capital, TAX_BOOT_I0, i1, mkt) if kind == "progressive"
                     else te.run_strategy_flat(a, capital, TAX_BOOT_I0, i1, fixed_days,
                                               so[0], sc[0], wants[a], mkt))
                liq = r["terminal_usd_liquidated"]
                if not np.isfinite(liq) or liq <= 0:
                    raise RuntimeError(f"non-positive terminal USD on a path, arm {a}, {kind}: {liq}")
                out[a][key].append(r["cagr_usd_liquidated"])
                out[a][key + "_dd"].append(r["max_dd_nav_usd"])
    res = {"n_paths": n_paths, "seed": seed, "calendar": [fixed_days[0], fixed_days[-1]],
           "calendar_sessions": len(fixed_days), "measured_sessions": TAX_BOOT_MEASURED,
           "financing": "base (DTB3), m = 2", "control": "C0", "arms": {}}
    c0b1, c0b2 = np.array(out["C0"]["b1"]), np.array(out["C0"]["b2"])
    c0b1dd, c0b2dd = np.array(out["C0"]["b1_dd"]), np.array(out["C0"]["b2_dd"])
    for a in arms:
        b1, b2 = np.array(out[a]["b1"]), np.array(out[a]["b2"])
        d1, d2 = b1 - c0b1, b2 - c0b2
        res["arms"][a] = {
            "b1_cagr_median": float(np.median(b1)), "b2_cagr_median": float(np.median(b2)),
            "b1_median_delta": float(np.median(d1)), "b1_share_gt_0": float(np.mean(d1 > 0)),
            "b1_p05": float(np.percentile(d1, 5)), "b1_p95": float(np.percentile(d1, 95)),
            "b2_median_delta": float(np.median(d2)), "b2_share_gt_0": float(np.mean(d2 > 0)),
            "b2_p05": float(np.percentile(d2, 5)), "b2_p95": float(np.percentile(d2, 95)),
            "b1_median_dd": float(np.median(np.array(out[a]["b1_dd"]) - c0b1dd)),
            "b2_median_dd": float(np.median(np.array(out[a]["b2_dd"]) - c0b2dd)),
        }
    return res



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
        w, ea, ca = got[arm]
        assert w.shape == (1, len(price)), arm
        assert np.all(w <= CAP + 1e-12) and np.all(w >= 0), arm
        assert not np.any(ea[0] & e2), arm
        assert not np.any(ca[0] & (e2 | ea[0])), arm  # crash only while E2 and the early path are off
        spec = ARM_SPEC[arm]
        if spec is not None:
            vals = (0.0, 1.0, spec["r2"]["w"]) + ((spec["crash"]["w"],) if "crash" in spec else ())
            for t in np.flatnonzero(w[0] > 0):
                if abs(w[0, t] - CAP) < 1e-12:  # cap binds at full production weight
                    continue
                f = w[0, t] / trv2.vt_of(v20[t])
                assert min(abs(f - v) for v in vals) < 1e-9, (arm, int(t), float(f))
        checks[arm] = {"mean_target": float(w.mean()), "early_share": float(ea.mean()),
                       "crash_share": float(ca.mean())}
    stats = {}
    for arm in ("C0", "K1"):
        nav, turn, ntr, codes, traded = simulate_band(o, sc, got[arm][0], COST_PER_SIDE * 2, START, C0_BAND)
        assert np.all(np.isfinite(nav)) and np.all(nav > 0)
        stats[arm] = {"cagr": float(trv2.cagr(nav[0, 0], nav[0, -1], trv2.calendar_years(days[START - 1], days[-1]))),
                      "turnover": float(turn[0])}
    # after-tax paths (progressive and flat) on a synthetic market
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2027) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(len(days), 30.0), ufe)
    w_k1 = got["K1"][0][0]
    te.init(days, {"K1": (so, sc)}, {"K1": w_k1})
    tax_p = te.run_strategy("K1", "progressive", TAX_CAPITAL, 300, len(days) - 1, mkt)
    tax_f = te.run_strategy_flat("K1", TAX_CAPITAL, 300, len(days) - 1, days, so, sc, w_k1, mkt)
    out = {"checks": checks, "stats": stats,
           "after_tax_progressive_cagr": tax_p["cagr_usd_liquidated"],
           "after_tax_flat_cagr": tax_f["cagr_usd_liquidated"]}
    out["pass"] = bool(all(np.isfinite(list(stats[a].values())).all() for a in stats)
                       and np.isfinite(tax_p["cagr_usd_liquidated"])
                       and np.isfinite(tax_f["cagr_usd_liquidated"]))
    return out


# ----------------------------------------------------------------------------- reporting helpers
PERIODS = (("2000_2002_bear", "2000-01-01", "2003-06-30"),
           ("2008_2009", "2007-10-01", "2009-12-31"),
           ("2020_covid", "2020-01-01", "2020-12-31"),
           ("2022_bear", "2022-01-01", "2023-03-31"))


def period_report(episodes):
    """Group episodes by calendar period and pick the three worst by delta."""
    out = {}
    for name, a, b in PERIODS:
        sel = [e for e in episodes if a <= e["entry_decision"] <= b]
        d = np.array([e["delta"] for e in sel]) if sel else np.array([0.0])
        out[name] = {"episodes": len(sel), "sessions": int(sum(e["sessions_held"] for e in sel)),
                     "sum_delta": float(d.sum()), "median_delta": float(np.median(d)),
                     "worst": min(sel, key=lambda e: e["delta"]) if sel else None,
                     "best": max(sel, key=lambda e: e["delta"]) if sel else None}
    worst = sorted(episodes, key=lambda e: e["delta"])[:3]
    return {"periods": out, "worst3": worst, "episodes": len(episodes),
            "sum_delta": float(sum(e["delta"] for e in episodes)),
            "share_positive": float(np.mean([e["delta"] > 0 for e in episodes])) if episodes else 0.0}


GAP_WINDOWS = {"2020_covid": ("2020-02-01", "2020-06-30"), "2008_crisis": ("2008-09-01", "2009-06-30")}


def crash_gap_report(dates, price, early_state, arm="K1", window=("2020-02-01", "2020-06-30")):
    """Why the crash path did or did not fire in a window, condition by condition.

    All arrays are aligned to `dates` (the measured sessions); `early_state` is production R2's
    own early-path state. Reports when the drawdown gate opens and closes, when each of the other
    entry conditions first holds, the first session on which ALL of them hold with E2 and the R2
    early path off (an actual crash entry), how many such sessions exist, and when E2's vote and
    the R2 early path first turn on (a false -> true transition).
    """
    cr = ARM_SPEC[arm]["crash"]
    csma, cvol, crmax, cvmax, clag = crash_components(price, cr["sma"], cr["lag"], cr["high"], cr["volmax"])
    e2 = e2_up(price)[0]
    ea = np.asarray(early_state, dtype=bool)
    dd_ok = np.isfinite(crmax[0]) & (price <= (1.0 - cr["dd"]) * crmax[0])
    vol_ok = np.isfinite(cvmax[0]) & (cvol[0] < cr["vol_ratio"] * cvmax[0])
    sma_ok, lag_ok = csma[0], clag[0]
    idle = (~e2) & (~ea)  # both production paths off: the crash path's context
    triggers = sma_ok & dd_ok & vol_ok & lag_ok
    idx = [i for i, d in enumerate(dates) if window[0] <= d <= window[1]]
    first = lambda m: next((dates[i] for i in idx if m[i]), None)
    last = lambda m: next((dates[i] for i in reversed(idx) if m[i]), None)

    def turn_on(flag):
        for i in idx:
            if flag[i] and (i == 0 or not flag[i - 1]):
                return dates[i]
        return None

    lows = price[idx]
    return {
        "window": list(window), "sessions": len(idx),
        "first_drawdown_ge_20pct": first(dd_ok), "last_drawdown_ge_20pct": last(dd_ok),
        "sessions_drawdown_ge_20pct": int(sum(1 for i in idx if dd_ok[i])),
        "first_vol_below_ratio": first(vol_ok),
        "first_vol_below_ratio_while_drawdown": first(vol_ok & dd_ok),
        "first_close_above_sma20_while_drawdown": first(sma_ok & dd_ok),
        "first_close_above_close10_while_drawdown": first(lag_ok & dd_ok),
        "first_crash_entry_conditions_met": first(triggers & idle),
        "sessions_crash_entry_conditions_met": int(sum(1 for i in idx if triggers[i])),
        "sessions_crash_entry_conditions_met_while_both_paths_off": int(sum(1 for i in idx if triggers[i] and idle[i])),
        "sessions_both_production_paths_off": int(sum(1 for i in idx if idle[i])),
        "e2_first_turn_on": turn_on(e2), "r2_early_first_turn_on": turn_on(ea),
        "min_close_in_window": float(lows.min()), "max_close_in_window": float(lows.max()),
    }


def after_tax_gates(tax, bootax, arm):
    """After-tax legs of the pre-registered rule: beat C0 in all three windows; B1/B2 median >= 0
    with a share of positive paths >= GATE_B_SHARE."""
    d = {wn: tax["windows"][wn][arm]["progressive"]["cagr"] - tax["windows"][wn]["C0"]["progressive"]["cagr"]
         for wn in TAX_WINDOWS}
    b = bootax["arms"][arm]
    return {"TAX_all_windows": bool(all(v > 0 for v in d.values())), "TAX_deltas": d,
            "TAX_B1": bool(b["b1_median_delta"] >= 0 and b["b1_share_gt_0"] >= GATE_B_SHARE),
            "TAX_B2": bool(b["b2_median_delta"] >= 0 and b["b2_share_gt_0"] >= GATE_B_SHARE)}


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
    print(f"harness R2/E2 reproduction vs fast_reentry_v1: {len(hc['v1_reproduction_checks'])} checks, "
          f"{n_bad} outside 0.01 pp")
    n_tax = sum(1 for v in hc["after_tax_anchor_checks"].values() if abs(v["diff_pp"]) > TAX_ANCHOR_TOL_PP)
    print(f"harness after-tax anchors (E2 vt_level_tax_v1, R2 fast_reentry_v2): "
          f"{len(hc['after_tax_anchor_checks'])} checks, {n_tax} outside {TAX_ANCHOR_TOL_PP} pp")
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

    # ---- audit of the control and the crash path ----
    days_b, arr_b, o_b, c_b, e2_b, v20_b = main_inputs(0.0)
    price_b = arr_b["QQQ"][1]
    aud = audit(days_b, o_b, c_b, price_b, e2_b, v20_b,
                ctx["base"]["wants"]["C0"], ctx["base"]["early"]["C0"],
                ctx["base"]["wants"]["K1"], ctx["base"]["early"]["K1"], ctx["base"]["crash"]["K1"])
    print("audit done")

    # ---- gates G1-G9 and the crash-path episode report ----
    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        gates[arm] = g
    crash_report = {a: period_report(main_res["base"]["m2"][a]["crash_episodes"]) for a in CANDIDATES}
    al = slice(START - 1, None)
    gap = {name: crash_gap_report(ctx["base"]["dates"], price_b[al], ctx["base"]["early"]["C0"][al], "K1", w)
           for name, w in GAP_WINDOWS.items()}
    loo = {a: leave_one_episode_out(ctx["base"]["dates"], o_b, c_b, ctx["base"]["wants"]["C0"],
                                    ctx["base"]["wants"][a], main_res["base"]["m2"][a]["crash_episodes"])
           for a in CANDIDATES}

    # ---- after tax: control, candidates and the E2 reference ----
    tax_arms = ("E2", "C0") + CANDIDATES
    tax = after_tax_stage({a: ctx["base"]["wants"][a] for a in tax_arms}, dates_tax=tax_arms,
                          published={wn: {"E2": "E2", "R2": "C0"} for wn in TAX_WINDOWS})
    print("after-tax windows done")
    bootax = after_tax_bootstrap(tax_arms)
    print("after-tax bootstrap done")
    for arm in CANDIDATES:
        gates[arm].update(after_tax_gates(tax, bootax, arm))
        gates[arm]["pass_all"] = bool(all(gates[arm][k] for k in GATE_KEYS)
                                      and gates[arm]["TAX_all_windows"] and gates[arm]["TAX_B1"]
                                      and gates[arm]["TAX_B2"])

    h2 = main_res["base"]["m2"]
    eligible = [a for a in CANDIDATES if gates[a]["pass_all"]]
    sel = max(eligible, key=lambda a: h2[a]["cagr"]) if eligible else "none"
    verdict = "PASS" if eligible else "FAIL"
    selection = {"eligible": eligible, "selected": sel,
                 "rule": "selected = candidate with the highest base m=2 CAGR among those passing G1-G9 "
                         "and the after-tax legs"}

    out = {
        "campaign": "crash_entry_v1",
        "verdict": verdict,
        "params": {"arms": list(ARMS), "candidates": list(CANDIDATES), "arm_spec": {a: ARM_SPEC[a] for a in ARMS},
                   "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "c0_band": C0_BAND, "cap": CAP,
                   "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q, "main_window_cost_multipliers": [1, 2, 4],
                   "financing_spread": FIN, "r2_momentum_lag": LAG_FAST,
                   "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                                      "back_extension": EXT, "measured": MEASURED, "m": 2},
                   "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                                     "returns_per_path": OOS_BOOT_L, "m": 2},
                   "bootstrap_after_tax": {"seed": SEED, "paths": TAX_BOOT_PATHS, "block": BLOCK,
                                           "calendar": TAX_BOOT_CAL, "measured_sessions": TAX_BOOT_MEASURED,
                                           "financing": "base (DTB3), m = 2"},
                   "after_tax_windows": {k: list(v) for k, v in TAX_WINDOWS.items()},
                   "after_tax_anchor_tolerance_pp": TAX_ANCHOR_TOL_PP,
                   "gate_b_share": GATE_B_SHARE,
                   "gap_windows": {k: list(v) for k, v in GAP_WINDOWS.items()},
                   "signals": "production E2 1.1.0 on price closes: SMA200, 252-session price return > 0, "
                              "close > 126-session max/min midpoint; 2 of 3"},
        "harness_check": hc,
        "audit": aud,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates_g1_g9_and_tax": gates,
        "crash_path_report": crash_report,
        "crash_gap_report": gap,
        "leave_one_episode_out": loo,
        "after_tax_100k": tax,
        "after_tax_bootstrap_b1_b2": bootax,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"crash_entry_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"C0 = production R2 1.2.0 | verdict {verdict}",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} (max |diff| "
         f"{max(abs(v['diff_pp']) for v in hc['deviation_checks'].values()):.6f} pp vs e2_integration_v1; "
         f"R2/E2 reproduction {'PASS' if hc['v1_reproduction_pass'] else 'FAIL'}; "
         f"after-tax anchors {'PASS' if hc['after_tax_anchor_pass'] else 'FAIL'}; "
         f"band sim {hc['band_simulator_equals_simulate_multi']}; median ref "
         f"{hc['rolling_median_fast_equals_v1_reference']})", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr | boot20 median, median(arm-C0) | "
                 f"m=1 CAGR")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} {a['turnover_annual']:5.2f} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+.3f}pp | "
                     f"{p(main_res[md]['m1'][arm]['cagr'])}")
        L.append("")
    L.append("time in market / trade counts (base, m=2): arm | total, early, crash share | round trips/yr, "
             "resizes/yr, sells/yr, turnover/yr | 2017+ round trips/sells/turnover")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        e, f = a["events_full"], a["events_2017_on"]
        er = f"{a.get('early_time_in_market', 0.0) * 100:.2f}%"
        cr_ = f"{a.get('crash_time_in_market', 0.0) * 100:.2f}%"
        L.append(f"  {arm:3s} {a['time_in_market'] * 100:6.2f}% {er:>7s} {cr_:>7s} | "
                 f"{e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{e['turnover_per_year']:5.2f} | {f['round_trips_per_year']:5.2f} {f['sells_per_year']:6.2f} "
                 f"{f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("crash path (base, m=2): arm | crash share | episodes | sum(arm-C0) | median | share>0 | "
             "worst3 entry->exit delta")
    for arm in CANDIDATES:
        a = main_res["base"]["m2"][arm]
        r = crash_report[arm]
        eps = a["crash_episodes"]
        d = np.array([e["delta"] for e in eps]) if eps else np.array([0.0])
        w3 = "  ".join(f"{e['entry_decision']}->{e['last_held_session']} {e['delta'] * 100:+.2f}pp" for e in r["worst3"])
        L.append(f"  {arm:3s} {a['crash_time_in_market'] * 100:6.2f}% {len(eps):4d} {d.sum() * 100:+8.2f}pp "
                 f"{np.median(d) * 100:+8.2f}pp {r['share_positive'] * 100:5.1f}% | {w3}")
    L.append("")
    L.append("crash episodes by period (arm | period: episodes, sessions, sum delta, best, worst)")
    for arm in CANDIDATES:
        for name, _, _ in PERIODS:
            z = crash_report[arm]["periods"][name]
            f = lambda e: "-" if e is None else f"{e['entry_decision']}->{e['last_held_session']} {e['delta'] * 100:+.2f}pp"
            L.append(f"  {arm:3s} {name:16s} {z['episodes']:2d} {z['sessions']:4d} {z['sum_delta'] * 100:+7.2f}pp "
                     f"best {f(z['best'])} worst {f(z['worst'])}")
    L.append("")
    L.append("why the crash path fired / did not fire (K1, measured sessions): window | dd>=20% first..last "
             "(sessions) | vol<ratio*vmax40 first (while dd>=20%) | first all entry conditions | sessions with "
             "all conditions (both production paths off) | E2 turn-on | R2 early turn-on")
    for name, z in gap.items():
        L.append(f"  {name:12s} {z['first_drawdown_ge_20pct']} .. {z['last_drawdown_ge_20pct']} "
                 f"({z['sessions_drawdown_ge_20pct']:3d}) | {z['first_vol_below_ratio']} "
                 f"({z['first_vol_below_ratio_while_drawdown']}) | {z['first_crash_entry_conditions_met']} | "
                 f"{z['sessions_crash_entry_conditions_met']:3d} "
                 f"({z['sessions_crash_entry_conditions_met_while_both_paths_off']}) | {z['e2_first_turn_on']} | "
                 f"{z['r2_early_first_turn_on']}")
    L.append("")
    L.append("leave one episode out (base, m=2, C0 CAGR "
             f"{loo['K1']['c0_cagr'] * 100:.2f}%): arm | episode | episode delta | CAGR with it flat | vs C0")
    for arm in CANDIDATES:
        for e in loo[arm]["episodes"]:
            L.append(f"  {arm:3s} {e['episode_entry']} -> {e['episode_last_held']} {e['episode_delta_pp']:+7.2f}pp "
                     f"{e['cagr_with_episode_flat'] * 100:6.2f}% {e['cagr_delta_vs_C0'] * 100:+7.3f}pp")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap median(arm-C0): " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_C0_median'] * 100:+.3f}pp" for a in ARMS))
    L.append("")
    L.append("gates G1-G9 + after tax (" + " ".join(GATE_KEYS) + " TAX_all_windows TAX_B1 TAX_B2):")
    for arm in CANDIDATES:
        g = gates[arm]
        keys = GATE_KEYS + ["TAX_all_windows", "TAX_B1", "TAX_B2"]
        L.append(f"  {arm:3s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in keys) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("")
    L.append("cost stress (report only, not gated): arm | m=1 / m=2 / m=4 base CAGR, m=4 max DD")
    for arm in ARMS:
        r = main_res["base"]
        L.append(f"  {arm:3s} {p(r['m1'][arm]['cagr']).strip()} / {p(r['m2'][arm]['cagr']).strip()} / "
                 f"{p(r['m4'][arm]['cagr']).strip()} | {p(r['m4'][arm]['max_dd']).strip()}")
    L.append("")
    L.append("after tax ($100k, Turkish progressive, liquidated): window | arm | pre-tax -> after-tax CAGR, "
             "max DD, tax USD, delta vs C0")
    for wn in TAX_WINDOWS:
        c0 = tax["windows"][wn]["C0"]["progressive"]["cagr"]
        for arm in ("E2", "C0") + CANDIDATES:
            x = tax["windows"][wn][arm]
            dl = "" if arm == "C0" else f"  delta {((x['progressive']['cagr'] - c0) * 100):+6.2f}pp"
            L.append(f"  {wn:22s} {arm:3s} {x['pre_tax']['cagr'] * 100:6.2f}% -> {x['progressive']['cagr'] * 100:6.2f}%  "
                     f"DD {x['progressive']['max_dd'] * 100:5.2f}%  tax {x['progressive']['tax_usd']:>12,.0f} USD{dl}")
    L.append(f"  anchors vs published references: {'PASS' if tax['pass'] else 'FAIL'}")
    L.append("")
    L.append("clean after-tax bootstrap B1 (progressive) / B2 (flat 20%), "
             f"{bootax['n_paths']} block-20 paths, calendar {bootax['calendar'][0]} -> {bootax['calendar'][1]}: "
             "arm | B1 median delta, share>0, p05/p95 | B2 median delta, share>0, p05/p95")
    for arm in tax_arms:
        b = bootax["arms"][arm]
        L.append(f"  {arm:3s} {b['b1_median_delta'] * 100:+7.3f}pp {b['b1_share_gt_0'] * 100:5.1f}% "
                 f"{b['b1_p05'] * 100:+.2f}/{b['b1_p95'] * 100:+.2f} | {b['b2_median_delta'] * 100:+7.3f}pp "
                 f"{b['b2_share_gt_0'] * 100:5.1f}% {b['b2_p05'] * 100:+.2f}/{b['b2_p95'] * 100:+.2f}")
    L.append(f"  (C0 B1 median after-tax CAGR {bootax['arms']['C0']['b1_cagr_median'] * 100:.2f}%, "
             f"B2 {bootax['arms']['C0']['b2_cagr_median'] * 100:.2f}%)")
    L.append("")
    L.append(f"audit: truncation {aud['truncation_probe_C0']['pass']}/{aud['truncation_probe_K1']['pass']}, "
             f"trailing-window recompute {aud['trailing_window_recompute']['pass']}, fill timing "
             f"{aud['fill_timing']['pass']}, cost linearity {aud['cost_linearity']['pass']}, invariants "
             f"{all(aud['invariants'].values())} -> {'PASS' if aud['pass'] else 'FAIL'}")
    L.append("")
    L.append(f"eligible {eligible}; selected: {sel}")
    L.append(f"VERDICT: {verdict}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
