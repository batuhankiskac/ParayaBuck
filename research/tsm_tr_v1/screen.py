#!/usr/bin/env python3
"""tsm_tr_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Question: on top of production R2, which production-feasible TSM/DON input variant is best,
pre-tax and after Turkish tax? The TSM/DON inputs are the only thing that varies; everything
else (SMA200, vol sizing, early re-entry path, cap, band, cost, financing) is production R2.

  P0 = production: TSM = 252-session PRICE return > 0, DON on the price close.
  P1 = TSM on the QQQ total-return level (dividends reinvested) > 0; DON on price.
  P2 = P1 + DON on the total-return level.
  P3 = TSM total return > a flat 2 %/yr hurdle; DON on price.
  P4 = research E2 (TSM total return > cumulative DTB3, DON on total return); report only,
       needs a rate series production does not have.
  C0 = production E2 1.1.0 without the early path (harness reference arm for the published
       E2 anchors; identical vote to P0, so P0 = C0 + the early re-entry path).

Modes
  --smoke    synthetic random-walk prices only: run the whole path (pre-tax simulator, arm
             invariants, after-tax runner) and assert it is finite and sane. Uses no market
             data. This is the only mode run before the pre-registration commit.
  --harness  reproduction only: C0 against e2_integration_v1's DEVIATION.json (production E2
             and the TR / flat-hurdle / DTB3 variants it published), P0 against
             research/fast_reentry_v1/RESULTS.json (R2), P0's after-tax windows against
             r2_tbill_combo_v1's R2, the vt_level_tax_v1 after-tax anchors and the
             single-leg engine cross-check.
  --run      re-runs the harness, stops if it fails, then runs every arm on the main window,
             the main block-20 bootstrap, the six out-of-sample windows, the pooled OOS
             bootstrap, the pre-tax gates (against P0), the after-tax stage and the two clean
             after-tax bootstraps. Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics) are imported from
research/trend_robustness_v2/screen.py (its main() is never run). The early re-entry path and
its per-episode machinery, the simulator and the gates are copied from
research/fast_reentry_v1/screen.py; the TSM/DON variant construction (tr_level, bill_level,
tsm_up, the tsm_series/hurdle/don_series naming) is copied from
research/e2_integration_v1/deviation.py (tag archive/research/e2-integration-v1-2026-10). The
after-tax engine is research/tsm_tr_v1/tax_engine.py, a byte copy of
research/r2_tbill_combo_v1/tax_engine.py, which is itself the byte-faithful copy of
research/vt_level_tax_v1/screen.py's corrected engine; the single-book wrapper is new here.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
import csv
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)
_te_spec = importlib.util.spec_from_file_location("r2tc_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
DEVIATION = HERE / "E2_INTEGRATION_DEVIATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
SMA_FAST, LAG_FAST, MED_WINDOW = 50, 20, 252  # early path: SMA, momentum lag, vol-median window
EARLY_WEIGHT = 0.5  # R2's early position is half the production weight
C0_BAND = trv2.BAND  # 0.005
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions (signal_ensemble_v1)
TOL = 1e-4  # 0.01 pp harness tolerance
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")
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

# Arm -> (tsm series, hurdle, don series), the e2_integration_v1/deviation.py naming. The SMA
# vote is always on the price close (production). C0 = the P0 vote with no early path.
VARIANTS = {
    "P0": ("price", 0.0, "price"),
    "P1": ("tr", 0.0, "price"),
    "P2": ("tr", 0.0, "tr"),
    "P3": ("tr", 0.02, "price"),
    "P4": ("tr", "dtb3", "tr"),
}
ARM_VARIANT = {"C0": "P0", "P0": "P0", "P1": "P1", "P2": "P2", "P3": "P3", "P4": "P4"}
ARM_EARLY = {"C0": False, "P0": True, "P1": True, "P2": True, "P3": True, "P4": True}
ARMS = ("C0", "P0", "P1", "P2", "P3", "P4")
CONTROL = "P0"          # production R2: every gate is measured against it
CANDIDATES = ("P1", "P2", "P3")   # implementable; P4 is report-only (needs a rate series)
REPORT_ONLY = ("P4",)
# Arms whose only change is on the total-return channel. The six OOS windows carry price
# indexes only (the harness has no dividend series for them), so total return == price there
# and these arms are numerically identical to P0: their OOS delta is 0 by construction and the
# OOS gate cannot discriminate. Pre-registered: G7/G8 are reported as non-identifying and
# waived for them; the decision rests on the main window and after tax.
OOS_IDENTIFYING = {"P1": False, "P2": False, "P3": True, "P4": True}

# after tax (report only): vt_level_tax_v1's windows at $100k
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
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2773 sessions + 3 back-extension = 2776
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253
# published reproduction anchors
REF_DEV = {  # e2_integration_v1 DEVIATION.json, E2_prod_price_h0 (cagr, max_dd, turnover)
    ("base", 1): (0.1494912807457318, 0.4924710732832198, 5.968939533490137),
    ("base", 2): (0.14674909060373587, 0.4956428339322504, 5.968939533490137),
    ("calibrated", 1): (0.14377164501317918, 0.49333140001226616, 5.968913400107268),
    ("calibrated", 2): (0.14104311139315073, 0.49649783199459774, 5.968913400107268),
}
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json, main_window, arm R2 (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
REF_R2_TAX = {  # research/r2_tbill_combo_v1/RESULTS.json after_tax_100k.windows[*].R2
    "2000_03_to_2026_09": (0.154732, 0.101600, 0.467584),
    "2010_01_to_end": (0.221587, 0.147059, 0.437839),
    "2017_01_to_end": (0.325330, 0.228861, 0.362426),
}
# research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json: E2-only variants (definition check)
REF_VARIANT_ARM = {"P0": "E2_prod_price_h0", "P2": "E2_tr_h0", "P4": "E2_research",
                   "X2": "E2_price_h2"}


# ----------------------------------------------------------------------------- signals
def tr_level(c, d):
    """Total-return level from closes and dividends (signal_ensemble_v1 copy); index 0 = 1."""
    c, d = np.atleast_2d(c), np.atleast_2d(d)
    g = (c[:, 1:] + d[:, 1:]) / c[:, :-1]
    return np.concatenate([np.ones((c.shape[0], 1)), np.cumprod(g, axis=1)], axis=1)


def bill_level(rate):
    """Cumulative bill level: return k accrues rate[k-1]/252 (signal_ensemble_v1 copy)."""
    rate = np.atleast_2d(rate)
    g = 1 + rate[:, :-1] / 252
    return np.concatenate([np.ones((rate.shape[0], 1)), np.cumprod(g, axis=1)], axis=1)


def flat_bills(n, annual):
    """Cumulative level of a flat `annual` rate over n sessions (deviation.py's flat_bill)."""
    return bill_level(np.full(n, float(annual)))


def tsm_up(series, bills, n=TSM_N):
    """up[t]: n-session return of `series` > the cumulative bill return over the same n sessions."""
    series, bills = np.atleast_2d(series), np.atleast_2d(bills)
    up = np.zeros(series.shape, dtype=bool)
    up[:, n:] = (series[:, n:] / series[:, :-n]) > (bills[:, n:] / bills[:, :-n])
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def variant_up(price, tr, rates, spec):
    """Production E2's 2-of-3 vote with substituted TSM/DON inputs.

    spec = (tsm_series, hurdle, don_series) as in e2_integration_v1/deviation.py: tsm_series is
    "price" or "tr", don_series is "price" or "tr", and `hurdle` is "dtb3" (the cumulative level
    of the `rates` series) or a flat annual rate (0.0, 0.02). SMA is always on the price close.
    TSM compares the n-session return of its series with the n-session bill return, so hurdle 0.0
    on a flat bill level is "return > 0"."""
    tsm_series, hurdle, don_series = spec
    T = np.atleast_2d(np.asarray(price, dtype=float)).shape[1]
    bills = (bill_level(rates) if hurdle == "dtb3" else flat_bills(T, float(hurdle)))
    tsm_in = tr if tsm_series == "tr" else price
    don_in = tr if don_series == "tr" else price
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up(tsm_in, bills).astype(int)
    d = don_up(don_in).astype(int)
    return (s + t + d) >= 2


def e2_up(price):
    """Production E2 vote on the price close: at least 2 of SMA200, TSM252 (> 0), DON126."""
    return variant_up(price, price, np.zeros(np.atleast_2d(np.asarray(price)).shape[1]), VARIANTS["P0"])


# ----------------------------------------------------------------------------- production sizing
def wants_for(e2, vol):
    """[P, T] QLD* targets: E2 vote x vt(vol), total capped at 0.98 (trend_robustness_v2.build_wants)."""
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


def _rolling_median_vol(price_vol, window=252):
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    for t in range(T):
        lo = max(0, t - window + 1)
        w = price_vol[:, lo:t + 1]
        cnt = np.sum(np.isfinite(w), axis=1)
        with warnings.catch_warnings():  # all-NaN rows before the vol window fills -> NaN, guarded by cnt
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(w, axis=1)
        out[:, t] = np.where(cnt >= window, med, np.nan)
    return out


def early_components(price):
    """Price-close inputs of production's early re-entry path (fast_reentry_v1 arm R2)."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    return sma50_up, vol_p, med_p, lag_cond


def early_path(price, e2_on, vol_instr, comps=None):
    """Production R2's early re-entry path (fast_reentry_v1 arm R2) on top of vote `e2_on`.

    While the vote is off, enter at half weight if close > SMA50 AND vol20 < its own trailing
    252-session median AND close > close 20 sessions ago; exit below SMA50; the vote overrides
    and ends the early position. Returns (target weights [P,T], early_active [P,T])."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vol_instr = np.atleast_2d(np.asarray(vol_instr, dtype=float))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = early_components(price) if comps is None else comps

    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)

    for t in range(T):
        e2t = e2_on[:, t].copy()
        early_active = np.where(e2t, False, early_active)               # the vote overrides
        early_active = np.where(early_active & (~sma50_up[:, t]) & (~e2t), False, early_active)
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t]) & entry_cond
        early_active = np.where(enter, True, early_active)

        active = e2t | early_active
        factor[:, t] = np.where(active, np.where(e2t, 1.0, EARLY_WEIGHT), 0.0)
        on_eff[:, t] = active
        early_hist[:, t] = early_active

    raw = on_eff.astype(float) * factor * trv2.vt_of(vol_instr)
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap, early_hist


def arm_legs(price, tr, rates, vol_instr, arm, comps=None):
    """(QLD* targets [1,T], effective-on [1,T], vote [1,T]) of one arm.

    The variant supplies the 2-of-3 vote; ARM_EARLY arms add production's early re-entry path,
    so effective-on = the variant's vote OR its early path."""
    vote = variant_up(price, tr, rates, VARIANTS[ARM_VARIANT[arm]])
    if not ARM_EARLY[arm]:
        return wants_for(vote, vol_instr), np.atleast_2d(vote), np.atleast_2d(vote)
    w, ea = early_path(price, vote, vol_instr, comps)
    return w, (np.atleast_2d(vote) | ea), np.atleast_2d(vote)


# ----------------------------------------------------------------------------- single-leg engine (harness identity)
def simulate_band(opens, closes, wants, cost, start, band):
    """Single-leg trend_robustness_v2 mechanics with a resize band (fast_reentry_v1 copy)."""
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
    """Trade counts per year between the sessions after close a and through close b."""
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


def early_episodes(dates, nav, nav_c0, early_aligned):
    """Per-episode P&L of the early positions (report-only, base financing, m = 2)."""
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


# ----------------------------------------------------------------------------- main window
def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return {"days": days, "arr": arr, "y": y, "o": o, "c": c, "price": q[1],
            "tr": tr_level(q[1], q[2])[0], "rates": y, "vol": trv2.vol_n(c)[0]}


def decision_changes(dates, vote, on_eff, base_vote, base_on):
    """Report-only: how many decision closes differ from production P0, and when.

    `vote`/`on_eff`/`base_*` are 1-D over the decision closes (the last close of the data is
    not a decision) and `dates` are their session dates."""
    d = np.asarray(vote) != np.asarray(base_vote)
    e = np.asarray(on_eff) != np.asarray(base_on)
    idx = np.flatnonzero(d)
    years = defaultdict(int)
    for k in idx:
        years[dates[k][:4]] += 1
    blocks = []
    if idx.size:
        cut = np.flatnonzero(np.diff(idx) > 1)
        for s, z in zip(np.concatenate([[0], cut + 1]), np.concatenate([cut, [idx.size - 1]])):
            blocks.append([dates[int(idx[s])], dates[int(idx[z])], int(z - s + 1)])
    return {"vote_sessions": int(d.sum()), "vote_share": float(d.mean()),
            "effective_sessions": int(e.sum()), "effective_share": float(e.mean()),
            "vote_changed_by_year": dict(sorted(years.items())),
            "vote_blocks": blocks}


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        mi = main_inputs(spread)
        days, price, tr, rates, o, c = (mi["days"], mi["price"], mi["tr"], mi["rates"], mi["o"], mi["c"])
        v20 = mi["vol"]
        dates = days[START - 1:]
        comps = early_components(price)
        legs = {arm: arm_legs(price, tr, rates, v20, arm, comps) for arm in arms}
        res[md] = {}
        for m in m_list:
            hm, navs = {}, {}
            for arm in arms:
                wq, act, vote = legs[arm]
                nav, turn, ntr, codes, traded = simulate_band(
                    o, c, wq, COST_PER_SIDE * m, START, C0_BAND)
                navs[arm] = nav[0]
                hm[arm] = arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wq[0], START,
                                    extras and m == 2,
                                    early_active=(np.atleast_2d(act) & ~np.atleast_2d(vote)))
            if extras and m == 2:
                for arm in arms:
                    ea = (legs[arm][1] & ~legs[arm][2])
                    hm[arm]["early_episodes"] = early_episodes(
                        dates, navs[arm], navs[CONTROL], ea[0][START - 1:])
                    if arm != CONTROL:
                        hm[arm]["decisions_vs_control"] = decision_changes(
                            dates[:-1], legs[arm][2][0][START - 1:-1], legs[arm][1][0][START - 1:-1],
                            legs[CONTROL][2][0][START - 1:-1], legs[CONTROL][1][0][START - 1:-1])
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def e2_only_stats(m_list=(1, 2)):
    """E2 without the early path, per variant spec, both financings: for the DEVIATION.json check.

    Key "X2" is the price channel with a flat 2 % hurdle, the published E2_price_h2 arm: it
    exercises the flat-hurdle code path on the price series."""
    specs = {"P0": VARIANTS["P0"], "P2": VARIANTS["P2"], "P4": VARIANTS["P4"],
             "X2": ("price", 0.02, "price")}
    out = {}
    for md, spread in FIN.items():
        mi = main_inputs(spread)
        o, c, v20 = mi["o"], mi["c"], mi["vol"]
        for m in m_list:
            for name, spec in specs.items():
                vote = variant_up(mi["price"], mi["tr"], mi["rates"], spec)
                nav, turn, _, _, _ = simulate_band(o, c, wants_for(vote, v20), COST_PER_SIDE * m,
                                                   START, C0_BAND)
                years = trv2.calendar_years(mi["days"][START - 1], mi["days"][-1])
                out[(name, md, m)] = {"cagr": trv2.curve_stats(mi["days"][START - 1:], nav[0])["cagr"],
                                      "max_dd": float(trv2.max_dd(nav[0])[0]),
                                      "turnover_annual": float(turn[0] / years)}
    return out


def harness():
    h = run_main(ARMS, extras=False, m_list=(1, 2))
    checks, bad = {}, 0
    for arm, ref in (("C0", REF_DEV), ("P0", REF_R2)):
        for (md, m), (cagr, dd, turn) in ref.items():
            x = h[md][f"m{m}"][arm]
            for k, r in (("cagr", cagr), ("max_dd", dd), ("turnover_annual", turn)):
                d = (x[k] - r) * (100 if k != "turnover_annual" else 1)
                checks[f"{arm}_{md}_m{m}_{k}"] = {"reference": r, "reproduced": x[k], "diff": d}
                if abs(d) > (TOL * 100 if k != "turnover_annual" else TOL):
                    bad += 1
    # E2-only variant definitions against e2_integration_v1's DEVIATION.json
    dev = json.loads(DEVIATION.read_text())
    e2o = e2_only_stats((1, 2))
    for name, arm in REF_VARIANT_ARM.items():
        for (nm, md, m), x in ((k, v) for k, v in e2o.items() if k[0] == name):
            r = dev[f"{md}_m{m}"]["arms"][arm]
            for k in ("cagr", "max_dd", "turnover_annual"):
                d = (x[k] - r[k]) * (100 if k != "turnover_annual" else 1)
                checks[f"dev_{name}_{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff": d}
                if abs(d) > (TOL * 100 if k != "turnover_annual" else TOL):
                    bad += 1
    # production-vote identity: the variant machinery with P0's spec == the direct construction
    mi = main_inputs(0.0)
    vote_p0 = variant_up(mi["price"], mi["tr"], mi["rates"], VARIANTS["P0"])
    p2 = np.atleast_2d(mi["price"])
    t_price = np.zeros(p2.shape, dtype=bool)
    t_price[:, TSM_N:] = p2[:, TSM_N:] / p2[:, :-TSM_N] > 1.0
    vote_ref = (trv2.sma_up(mi["price"], SMA_N).astype(int) + t_price.astype(int)
                + don_up(mi["price"]).astype(int)) >= 2
    vote_id = bool(np.array_equal(vote_p0, vote_ref))
    # band simulator == trend_robustness_v2.simulate_multi, bit for bit
    wq = wants_for(vote_p0, mi["vol"])
    nav_b = trv2.simulate_multi(mi["o"], mi["c"], wq[:, None, :], COST_PER_SIDE * 2, START)[0][0]
    nav_c = simulate_band(mi["o"], mi["c"], wq, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    sim_equal = bool(np.array_equal(nav_b, nav_c))
    ok = (bad == 0) and sim_equal and vote_id
    return {"window": h["window"], "reproduction_checks": checks, "tolerance_pp": TOL * 100,
            "n_checks": len(checks), "n_outside_tolerance": bad,
            "p0_vote_identity": vote_id, "band_equals_simulate_multi": sim_equal, "pass": bool(ok)}


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        base = {c: cg[(md, c)] for c in (CONTROL, "C0")}
        for a in arms:
            c = cg[(md, a)]
            e = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                 "turnover_median": float(np.median(tv[(md, a)]))}
            for ctrl in (CONTROL, "C0"):
                d = c - base[ctrl]
                e[f"diff_vs_{ctrl}_median"] = float(np.median(d))
                e[f"diff_vs_{ctrl}_mean"] = float(np.mean(d))
                e[f"diff_vs_{ctrl}_p25"] = float(np.percentile(d, 25))
                e[f"diff_vs_{ctrl}_p75"] = float(np.percentile(d, 75))
                e[f"share_ge_{ctrl}"] = float(np.mean(d >= 0))
            out[md][a] = e
    return out


def boot_main(arms):
    """Stationary block bootstrap (mean block 20), draws identical to signal_ensemble_v1.
    All arms on the same paths, m = 2, both financing models."""
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
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = early_components(pc)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(scl)
            for arm in arms:
                w, _, _ = arm_legs(pc, qc, yyp, vol_instr, arm, comps)
                nav, turn, _, _, _ = simulate_band(so, scl, w, COST_PER_SIDE * 2, start, C0_BAND)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


# ----------------------------------------------------------------------------- OOS data (copied loaders)
def load_gspc():
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
                def f(x):
                    return None if x == "" else float(x)

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
    """One out-of-sample window. These indexes carry prices only (no dividends anywhere in the
    harness for them), so the total-return level is the price level: tr = c. P1/P2 are therefore
    numerically identical to P0 here (OOS_IDENTIFYING false)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    vol_instr = trv2.vol_n(sc)
    comps = early_components(c)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    vote_p0 = variant_up(c, c, y, VARIANTS["P0"])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1},
           "mean_rate_off": float(y[start - 1:-1][~vote_p0[0, start - 1:-1]].mean())}
    for arm in arms:
        w, _, _ = arm_legs(c, c, y, vol_instr, arm, comps)
        nav, turn, _, codes, traded = simulate_band(so, sc, w, COST_PER_SIDE * 2, start, C0_BAND)
        nav = nav[0]
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)), "max_dd": float(trv2.max_dd(nav)[0]),
                    "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_market(key, rng, arms):
    """e2_confirmation_v1.bootstrap_market draws (shared generator, markets in order), all arms, m = 2.
    Price-only index: the total-return level equals the price level."""
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
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        vol_instr = trv2.vol_n(sc)
        comps = early_components(cc)
        for arm in arms:
            w, _, _ = arm_legs(cc, cc, yyp, vol_instr, arm, comps)
            nav = simulate_band(so, sc, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates
def g_return(a, c):
    """CAGR >= control, or (Calmar >= 1.1 x control and CAGR >= control - 0.5 pp)."""
    return bool(a["cagr"] >= c["cagr"] or (a["calmar"] >= 1.10 * c["calmar"] and a["cagr"] >= c["cagr"] - 0.005))


def historical_gates(main, boot, arm, ctrl):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"][ctrl]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"][ctrl]
        r[f"G1_{md}"] = g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot[md][arm][f"diff_vs_{ctrl}_median"] >= 0)
        r[f"G6_{md}"] = g_return(a1, c1)
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"][ctrl]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    lim = main["base"]["m2"][ctrl]["turnover_annual"] * 2.0  # G9: <= 2 x the control's turnover
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    r["G9_limit"] = lim
    return r


def oos_gates(oos, oos_boot_pooled, arm, ctrl):
    keys = ["GSPC"] + list(MARKETS)
    dc = {k: oos[k][arm]["cagr"] - oos[k][ctrl]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k][ctrl]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= 5 and n_d >= 5 and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm][f"diff_vs_{ctrl}_median"] >= 0)
    identifying = OOS_IDENTIFYING.get(arm, True)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm][f"diff_vs_{ctrl}_median"],
            "non_identifying": (not identifying),
            "note": ("" if identifying else
                     "OOS windows are price-only: total return == price, so this arm's vote equals "
                     "P0's and the OOS delta is 0 by construction (pre-registered as waived)")}


GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9"]
GATE_KEYS_IDENT = [k for k in GATE_KEYS if k not in ("G7", "G8")]
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ============================================================================= after tax
def _sell_for_cash(qa, price, idx, need, cashbox, cost):
    """Fund a tax payment by selling gross notional `need` at (idx, price) -- the single-leg
    funding rule of research/vt_level_tax_v1/screen.py's run_strategy. Returns sell events."""
    if qa.units <= 1e-12 or price <= 0:
        return 0
    qa.sell(idx, price, need / price, need * cost, "tax")
    return 1


def tax_run(capital, i0, i1, days, mkt, oq, cq, wq, mode):
    """Single-book after-tax strategy run (synthetic QLD* only).

    The session loop, trade rules, fee accounting, tax assessment, instalment funding and
    liquidation mirror research/vt_level_tax_v1/screen.py's `run_strategy` (progressive /
    pre_tax) and `run_strategy_flat` (flat 20 %). mode in {"pre_tax", "progressive", "flat"}.
    Opened `days[i0-1]` must exist."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    cost = COST_PER_SIDE * 2
    band = C0_BAND
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1)
    cashbox["v"] = float(capital)  # the account constructor resets the shared cashbox

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = 0
    prev_tgt = None
    for i in range(i0, i1 + 1):
        if i > i0 and int(days[i][:4]) != int(days[i - 1][:4]):
            prevy = int(days[i][:4]) - 1
            qa.tax_due[prevy] = qa.assess(prevy)
            if mode == "flat":
                due = qa.tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                    if cashbox["v"] < usd:
                        tax_sales += _sell_for_cash(qa, float(cq[i - 1]), i - 1, usd - cashbox["v"],
                                                    cashbox, cost)
                    cashbox["v"] -= usd
                    qa.tax_paid_try[prevy] = qa.tax_paid_try.get(prevy, 0.0) + due
                    qa.tax_paid_usd += usd

        if pays is not None:
            for ty, part in pays.get(i, ()):
                if ty not in qa.tax_due:
                    qa.tax_due[ty] = 0.0
                due = qa.tax_due.get(ty, 0.0)
                amt = due / 2 if part == 0 else due - qa.tax_paid_try.get(ty, 0.0)
                if amt <= 1e-9:
                    continue
                usd = amt / mkt.fx[i]
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"]) / (1 - cost)
                    tax_sales += _sell_for_cash(qa, float(oq[i]), i, need, cashbox, cost)
                cashbox["v"] -= usd
                qa.tax_paid_try[ty] += amt
                qa.tax_paid_usd += usd

        poq, pcq = float(oq[i]), float(cq[i])
        held_q = qa.units * poq
        nav_open = cashbox["v"] + held_q
        if nav_open <= 0:
            nav_open = cashbox["v"]
        wq_cur = held_q / nav_open if nav_open > 0 else 0.0

        tgt_q = float(wq[i - 1]) if i > 0 else float(wq[0])
        tgt_q = 0.0 if tgt_q != tgt_q else tgt_q
        trade_q = abs(tgt_q - wq_cur) > band
        if qa.units == 0 and tgt_q > 1e-12 and prev_tgt is None:
            trade_q = True
        if qa.units > 1e-12 and tgt_q <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade_q = True

        d_q = abs(tgt_q - wq_cur) if trade_q else 0.0
        fee = nav_open * cost * d_q
        nav_after = nav_open - fee
        if trade_q:
            target_units = tgt_q * nav_after / poq if poq > 0 else 0.0
            du = target_units - qa.units
            if du > 1e-12:
                qa.buy(i, poq, du * poq, nav_open * cost * d_q)
            elif du < -1e-12:
                qa.sell(i, poq, -du, nav_open * cost * d_q, "resize")
                q_sells += 1
            trades += 1
        nav[i - i0 + 1] = cashbox["v"] + qa.units * pcq
        prev_tgt = tgt_q

    end_y = int(days[i1][:4])
    if qa.units > 0:
        qa.sell(i1, float(cq[i1]), qa.units, qa.units * float(cq[i1]) * cost, "liquidation")
    liq_tax = qa.assess(end_y) + qa.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    mtm = nav[-1]
    yrs = trv2.calendar_years(days[i0 - 1], days[i1])

    def cg(v0, v1):
        return (v1 / v0) ** (1 / yrs) - 1

    maxdd = float(trv2.max_dd(nav.reshape(1, -1))[0])
    return {
        "window": [days[i0], days[i1]], "years": float(yrs), "capital_usd": float(capital),
        "ending_nav_usd": float(mtm), "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "tax_total_try": float(sum(qa.tax_paid_try.values()) + liq_tax),
        "tax_total_usd_at_payment_fx": float(qa.tax_paid_usd + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "strategy_trades": int(trades), "qld_sells": int(q_sells),
        "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + tax_sales),
        "fees_usd": float(qa.fees),
        "indexation_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "_nav": nav,
    }


def tax_inputs():
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing, as vt_level_tax_v1
    price, v20 = q[1], trv2.vol_n(c)[0]
    tr = tr_level(q[1], q[2])[0]
    comps = early_components(price)
    legs = {a: arm_legs(price, tr, y, v20, a, comps) for a in ARMS}
    return days, o, c, legs


def after_tax_stage(legs, o, c, days):
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"capital_usd": TAX_CAPITAL, "cost_multiplier_m": 2, "financing": "base",
           "windows": {}, "anchors": {}, "reproduction": {}, "single_leg_cross_check": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        out["windows"][wn] = {}
        for arm in ARMS:
            w = legs[arm][0]
            r = {}
            for mode in ("pre_tax", "progressive"):
                res = tax_run(TAX_CAPITAL, a, b, days, mkt, o, c, w[0], mode)
                r[mode] = {"cagr": res["cagr_usd_liquidated"], "max_dd": res["max_dd_nav_usd"],
                           "terminal_usd": res["terminal_usd_liquidated"], "tax_usd": res["tax_total_usd_at_payment_fx"],
                           "trades_per_year": res["strategy_trades"] / res["years"],
                           "sells_per_year": res["sells"] / res["years"],
                           "tax_funding_sales": res["tax_funding_sales"]}
            out["windows"][wn][arm] = r
        # anchors: C0 single-leg must reproduce vt_level_tax_v1
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
        # control reproduction: P0 (= production R2) against r2_tbill_combo_v1's R2
        r0 = out["windows"][wn]["P0"]
        ref0 = REF_R2_TAX[wn]
        out["reproduction"][wn] = {
            "pre_tax": {"reference": ref0[0], "reproduced": r0["pre_tax"]["cagr"],
                        "diff_pp": (r0["pre_tax"]["cagr"] - ref0[0]) * 100},
            "progressive": {"reference": ref0[1], "reproduced": r0["progressive"]["cagr"],
                            "diff_pp": (r0["progressive"]["cagr"] - ref0[1]) * 100},
            "progressive_max_dd": {"reference": ref0[2], "reproduced": r0["progressive"]["max_dd"],
                                   "diff_pp": (r0["progressive"]["max_dd"] - ref0[2]) * 100},
        }
    out["pass"] = all(abs(v["diff_pp"]) <= 0.005 for wn in list(out["anchors"].values())
                      + list(out["reproduction"].values()) for v in wn.values())

    # cross-check: my single-book runner == the vendored single-leg engine, identical NAV path
    for wn, (a, b) in TAX_WINDOWS.items():
        w = legs["C0"][0]
        te.init(days, {"C0": (o, c)}, {"C0": w[0]})
        ref = te.run_strategy("C0", "pre_tax", TAX_CAPITAL, a, b, mkt)
        mine = tax_run(TAX_CAPITAL, a, b, days, mkt, o, c, w[0], "pre_tax")
        out["single_leg_cross_check"][wn] = {
            "engine_cagr": ref["cagr_usd_liquidated"], "mine_cagr": mine["cagr_usd_liquidated"],
            "diff_pp": (mine["cagr_usd_liquidated"] - ref["cagr_usd_liquidated"]) * 100,
            "engine_terminal": ref["terminal_usd_liquidated"], "mine_terminal": mine["terminal_usd_liquidated"],
            "nav_path_equal": bool(np.array_equal(np.asarray(mine["_nav"]), np.asarray(ref["_nav"]))),
        }
    return out


def tax_boot_calendar():
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    return all_days, arr, y, list(sub[:need])


def after_tax_bootstrap(n_paths=TAX_BOOT_PATHS, capital=TAX_CAPITAL):
    """Clean after-tax bootstrap of vt_level_tax_v1 (B1 = progressive, B2 = 20 % flat, losses
    carried forward): block-20 stationary bootstrap, 10-year paths on the fixed 2015-09 -> 2026-09
    calendar with its fixed FX, $100k, liquidated at the end. Reports, for every candidate, the
    median delta CAGR and the share of paths above 0 against the control P0 (= production R2)."""
    all_days, arr, y, fixed_dates = tax_boot_calendar()
    fixed_fx = fx_at(fixed_dates)
    base_mkt = te.load_market(FX_CSV, UFE_CSV, all_days)
    ufe = base_mkt.ufe
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    res = {mode: {a: {"cagr": [], "maxdd": [], "sells": []} for a in ARMS}
           for mode in ("progressive", "flat")}
    for pidx in range(n_paths):
        P = 1
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT_, 0, -1)], axis=1)
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
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)
        vol = trv2.vol_n(sc)
        comps = early_components(pc)
        mkt = te.Market(fixed_dates, fixed_fx, ufe)
        for arm in ARMS:
            w = arm_legs(pc, qc, yyp, vol, arm, comps)[0]
            for mode in ("progressive", "flat"):
                r = tax_run(capital, i0, i1, fixed_dates, mkt, so[0], sc[0], w[0], mode)
                if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                    raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode}")
                res[mode][arm]["cagr"].append(r["cagr_usd_liquidated"])
                res[mode][arm]["maxdd"].append(r["max_dd_nav_usd"])
                res[mode][arm]["sells"].append(r["sells"] / r["years"])
    out = {}
    for mode in ("progressive", "flat"):
        e = {"tax_kind": mode, "n_paths": n_paths, "calendar_start": fixed_dates[0],
             "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK,
             "capital_usd": capital, "control": CONTROL}
        for a in ARMS:
            d = np.array(res[mode][a]["cagr"]) - np.array(res[mode][CONTROL]["cagr"])
            e[a] = {"cagr_median": float(np.median(res[mode][a]["cagr"])),
                    "maxdd_median": float(np.median(res[mode][a]["maxdd"])),
                    "sells_per_year_median": float(np.median(res[mode][a]["sells"])),
                    "median_delta_cagr_pp": float(np.median(d) * 100),
                    "mean_delta_cagr_pp": float(np.mean(d) * 100),
                    "share_gt_0": float(np.mean(d > 0)),
                    "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                    "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
        out[mode] = e
    return out


_FX_CACHE = {}


def fx_at(dates):
    """CBRT USD/TRY forex buying, forward-filled to `dates` -- the same alignment te.load_market
    applies (bisect_right - 1). Identical to vt_level_tax_v1's fixed FX on its own calendar."""
    if not _FX_CACHE:
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        _FX_CACHE["dates"] = fd
        _FX_CACHE["fx"] = fv
    fd, fv = _FX_CACHE["dates"], _FX_CACHE["fx"]
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates], dtype=float)


# ----------------------------------------------------------------------------- smoke (synthetic only)
def synthetic_bars(n, seed):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    return o, price, np.full(n, 0.01)


def smoke():
    """Whole run path on synthetic random-walk prices; no market data touched."""
    from datetime import date, timedelta
    n = 1600
    o, price, y = synthetic_bars(n, 7)
    d0 = date(2000, 1, 3)
    days = [(d0 + timedelta(days=k)).isoformat() for k in range(n)]
    div = np.where(np.arange(n) % 63 == 0, 0.002 * price, 0.0)  # quarterly synthetic dividends
    so, sc = trv2.synthetic_2x(o, price, div, y, EXP_Q)
    tr = tr_level(price, div)[0]
    v20 = trv2.vol_n(sc)[0]
    comps = early_components(price)
    checks, outs = {}, {}
    for arm in ARMS:
        w, act, vote = arm_legs(price, tr, y, v20, arm, comps)
        assert w.shape == (1, n) and np.all(w <= CAP + 1e-12) and np.all(w >= 0), arm
        assert not np.any(np.atleast_2d(vote) & np.atleast_2d(act) & ~np.atleast_2d(vote)), arm
        nav, turn, _, _, _ = simulate_band(so[None, :], sc[None, :], w, COST_PER_SIDE * 2, START, C0_BAND)
        nav = nav[0]
        assert np.all(np.isfinite(nav)) and np.all(nav > 0), arm
        assert float(nav[-1]) > 0.5, arm
        checks[arm] = {"mean_target": float(w.mean()),
                       "effective_share": float(np.atleast_2d(act).mean()),
                       "vote_share": float(np.atleast_2d(vote).mean()),
                       "early_share": float(np.mean(np.atleast_2d(act) & ~np.atleast_2d(vote))),
                       "nav_end": float(nav[-1]), "turnover": float(turn[0])}
        outs[arm] = w
    # after-tax single-book path on a synthetic market
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2027) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(n, 30.0), ufe)
    tax = {}
    for arm in ARMS:
        w = outs[arm]
        r_pre = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, w[0], "pre_tax")
        r_prog = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, w[0], "progressive")
        r_flat = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, w[0], "flat")
        for r in (r_pre, r_prog, r_flat):
            assert np.isfinite(r["cagr_usd_liquidated"]) and r["terminal_usd_liquidated"] > 0, arm
        assert r_prog["cagr_usd_liquidated"] <= r_pre["cagr_usd_liquidated"] + 1e-9, arm
        assert r_flat["cagr_usd_liquidated"] <= r_pre["cagr_usd_liquidated"] + 1e-9, arm
        tax[arm] = {"pre_tax": r_pre["cagr_usd_liquidated"], "progressive": r_prog["cagr_usd_liquidated"],
                    "flat": r_flat["cagr_usd_liquidated"], "sells_per_year": r_prog["sells"] / r_prog["years"]}
    # the flat 2 % hurdle must be a strictly tighter TSM test than hurdle 0 on the same series
    t0 = tsm_up(tr, flat_bills(n, 0.0))
    t2 = tsm_up(tr, flat_bills(n, 0.02))
    assert np.any(t0 & ~t2), "flat hurdle is not binding"
    return {"checks": checks, "after_tax": tax, "pass": True}


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
    for k in sorted(hc["reproduction_checks"]):
        v = hc["reproduction_checks"][k]
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff']:+.6f}")
    print(f"harness replication: {hc['n_checks']} checks, {hc['n_outside_tolerance']} outside tolerance")
    print(f"harness vote identity {hc['p0_vote_identity']}, band == simulate_multi "
          f"{hc['band_equals_simulate_multi']}")

    # after-tax anchors, control reproduction and cross-check (real data, before any candidate arm)
    days, o, c, legs = tax_inputs()
    tax = after_tax_stage(legs, o, c, days)
    for wn, v in tax["anchors"].items():
        print(f"tax anchor {wn}: pre_tax {v['pre_tax']['diff_pp']:+.5f} progressive "
              f"{v['progressive']['diff_pp']:+.5f} maxDD {v['progressive_max_dd']['diff_pp']:+.5f} pp")
    for wn, v in tax["reproduction"].items():
        print(f"tax P0 vs r2_tbill_combo R2 {wn}: pre_tax {v['pre_tax']['diff_pp']:+.5f} progressive "
              f"{v['progressive']['diff_pp']:+.5f} maxDD {v['progressive_max_dd']['diff_pp']:+.5f} pp")
    xchk = max(abs(v["diff_pp"]) for v in tax["single_leg_cross_check"].values())
    print(f"tax single-leg cross-check max |diff| {xchk:.6f} pp")
    tax_ok = tax["pass"] and xchk <= 0.005
    print(f"harness {'PASS' if (hc['pass'] and tax_ok) else 'FAIL'}")
    if mode == "--harness" or not (hc["pass"] and tax_ok):
        sys.exit(0 if (hc["pass"] and tax_ok) else 1)

    main_res = run_main(ARMS, extras=True, m_list=(1, 2, 4))
    print("main window done", flush=True)
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done", flush=True)
    oos = {"GSPC": oos_window(*load_gspc(), ARMS)}
    for k in MARKETS:
        oos[k] = oos_window(*load_market(k), ARMS)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, ARMS) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff_vs_P0": float(np.median(per[k][a] - per[k][CONTROL])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled[CONTROL]
        oos_boot["pooled"][a] = {"diff_vs_P0_median": float(np.median(d)), "diff_vs_P0_mean": float(np.mean(d)),
                                 "share_ge_P0": float(np.mean(d >= 0)), "paths": int(len(d)),
                                 "cagr_median": float(np.median(pooled[a]))}
    print("OOS done", flush=True)

    gates = {}
    for arm in CANDIDATES + REPORT_ONLY:
        g = historical_gates(main_res, boot, arm, CONTROL)
        g.update(oos_gates(oos, oos_boot["pooled"], arm, CONTROL))
        keys = GATE_KEYS if OOS_IDENTIFYING.get(arm, True) else GATE_KEYS_IDENT
        g["gate_keys_used"] = keys
        g["report_only"] = arm in REPORT_ONLY
        g["pass_all"] = bool(all(g[k] for k in keys))
        gates[arm] = g
    print("gates done", flush=True)

    tax_boot = after_tax_bootstrap()
    print("after-tax bootstrap done", flush=True)

    wins = {wn: {a: tax["windows"][wn][a]["progressive"]["cagr"] for a in ARMS} for wn in TAX_WINDOWS}
    beats = {arm: {wn: bool(wins[wn][arm] > wins[wn][CONTROL]) for wn in TAX_WINDOWS}
             for arm in CANDIDATES + REPORT_ONLY}
    b1, b2 = tax_boot["progressive"], tax_boot["flat"]
    after_tax_ok = {a: bool(all(beats[a].values())
                            and b1[a]["median_delta_cagr_pp"] >= 0 and b1[a]["share_gt_0"] >= 0.55
                            and b2[a]["median_delta_cagr_pp"] >= 0 and b2[a]["share_gt_0"] >= 0.55)
                    for a in CANDIDATES + REPORT_ONLY}
    eligible = [a for a in CANDIDATES if gates[a]["pass_all"] and after_tax_ok[a]]
    key = lambda a: (main_res["base"]["m2"][a]["cagr"], main_res["base"]["m2"][a]["calmar"])
    selected = max(eligible, key=key) if eligible else "none"
    selection = {"eligible": eligible, "selected": selected, "report_only": list(REPORT_ONLY),
                 "rule": "highest main-window m=2 base CAGR among candidates passing every pre-tax "
                         "gate (vs P0) and the after-tax condition (beats P0 at $100k in all three "
                         "windows and B1/B2 median delta >= 0 with share > 0 >= 55%)"}

    out = {
        "campaign": "tsm_tr_v1",
        "params": {
            "arms": list(ARMS), "control": CONTROL, "candidates": list(CANDIDATES),
            "report_only": list(REPORT_ONLY), "variants": {k: list(v) for k, v in VARIANTS.items()},
            "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "c0_band": C0_BAND, "cap": CAP,
            "early_path": {"sma_fast": SMA_FAST, "momentum_lag": LAG_FAST, "vol_median_window": MED_WINDOW,
                           "weight": EARLY_WEIGHT},
            "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
            "main_window_cost_multipliers": [1, 2, 4], "financing_spread": FIN,
            "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                               "back_extension": EXT, "measured": MEASURED, "m": 2},
            "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                              "returns_per_path": OOS_BOOT_L, "m": 2},
            "oos_identifying": OOS_IDENTIFYING,
            "after_tax": {"capital_usd": TAX_CAPITAL, "windows": TAX_WINDOWS, "m": 2,
                          "financing": "base",
                          "engine": "vt_level_tax_v1 corrected (tax_engine.py byte copy)",
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                        "modes": ["progressive (B1-recent)", "flat 20% (B2)"]}},
            "signals": "E2's 2-of-3 vote with substituted TSM/DON inputs; SMA200 always on the price "
                       "close; see VARIANTS",
        },
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "after_tax_100k": tax,
        "after_tax_bootstrap": tax_boot,
        "after_tax_beats_control_all_windows": beats,
        "after_tax_ok": after_tax_ok,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    def p(x):
        return f"{x * 100:7.2f}%"

    def pct(x):
        return f"{x * 100:6.2f}%"

    w = main_res["window"]

    def spec(a):
        return "" if a == "C0" else "/".join(str(x) for x in VARIANTS[ARM_VARIANT[a]])

    L = [f"tsm_tr_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"control P0 = production R2 (E2 + early re-entry); C0 = production E2 without the early path",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} ({hc['n_checks']} reproduction checks, "
         f"{hc['n_outside_tolerance']} outside 0.01 pp; P0 vote identity {hc['p0_vote_identity']}; "
         f"band == simulate_multi {hc['band_equals_simulate_multi']}); tax anchors + P0-vs-R2 reproduction "
         f"max |diff| {max(abs(v['diff_pp']) for d in (tax['anchors'], tax['reproduction']) for wn in d.values() for v in wn.values()):.5f} pp; "
         f"tax single-leg cross-check {xchk:.6f} pp",
         f"arms (tsm_series/hurdle/don_series): " +
         "  ".join(f"{a}={spec(a) or 'P0 spec, no early path'}" for a in ARMS), ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar Sharpe 2017+ P1 P2 P3 turn/yr | m=1 CAGR maxDD | "
                 f"boot20 median, dmed vs P0, share>=P0 | decision sessions != P0 (vote / effective)")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            dc = a.get("decisions_vs_control")
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {a['sharpe']:5.3f} "
                     f"{p(a['2017_end']['cagr'])} {p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} | {p(main_res[md]['m1'][arm]['cagr'])} "
                     f"{p(main_res[md]['m1'][arm]['max_dd'])} | {p(b['cagr_median'])} "
                     f"{b['diff_vs_P0_median'] * 100:+7.3f}pp "
                     + (p(b['share_ge_P0']) + f" {dc['vote_sessions']:4d} / {dc['effective_sessions']:4d}"
                        if dc else "     --"))
        L.append("")
    L.append("decision sessions that differ from P0 (base, m=2): arm | vote sessions | effective sessions | "
             "by year | vote-diff blocks (start, end, sessions)")
    for arm in CANDIDATES + REPORT_ONLY:
        dc = main_res["base"]["m2"][arm]["decisions_vs_control"]
        yrs = " ".join(f"{y}:{n}" for y, n in dc["vote_changed_by_year"].items())
        blocks = " ".join(f"{s}->{e}({n})" for s, e, n in dc["vote_blocks"][:12])
        L.append(f"  {arm} {dc['vote_sessions']:4d} ({dc['vote_share'] * 100:5.2f}%) "
                 f"{dc['effective_sessions']:4d} ({dc['effective_share'] * 100:5.2f}%) | {yrs} | {blocks}")
    L.append("")
    L.append("trades (base, m=2): arm | full window: round trips/yr, resizes/yr, sells/yr, turnover/yr | 2017+")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        e, f = a["events_full"], a["events_2017_on"]
        L.append(f"  {arm:3s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} "
                 f"{e['sells_per_year']:6.2f} {e['turnover_per_year']:5.2f} | 2017+ "
                 f"{f['round_trips_per_year']:5.2f} {f['sells_per_year']:6.2f} {f['turnover_per_year']:5.2f}")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD (P1 and P2 are identical to P0: these "
             "windows are price-only, so total return == price)")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap (M1..M5, 10,000 paths) median(arm-P0) [vs C0]: " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_P0_median'] * 100:+.3f}pp "
                       f"[{(oos_boot['pooled'][a]['cagr_median'] - oos_boot['pooled']['C0']['cagr_median']) * 100:+.3f}]"
                       for a in CANDIDATES + REPORT_ONLY))
    L.append("")
    L.append("gates vs P0 (" + " ".join(GATE_KEYS) + "):")
    for arm in CANDIDATES + REPORT_ONLY:
        g = gates[arm]
        L.append(f"  {arm:3s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in g["gate_keys_used"]) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}"
                 + ("  (report only)" if g["report_only"] else "")
                 + ("  [G7/G8 non-identifying: OOS is price-only, waived]" if g["non_identifying"] else ""))
    L.append("")
    L.append("cost stress (report only, not gated): arm | m=1 / m=2 / m=4 base CAGR, m=4 max DD")
    for arm in ARMS:
        r = main_res["base"]
        L.append(f"  {arm:3s} {p(r['m1'][arm]['cagr']).strip()} / {p(r['m2'][arm]['cagr']).strip()} / "
                 f"{p(r['m4'][arm]['cagr']).strip()} | {p(r['m4'][arm]['max_dd']).strip()}")
    L.append("")
    L.append("early path (base, m=2): arm | share of sessions | episodes | sum(arm-C0) | median delta | best | worst")
    for arm in CANDIDATES + REPORT_ONLY:
        a = main_res["base"]["m2"][arm]
        eps = a.get("early_episodes") or []
        d = np.array([e["delta"] for e in eps]) if eps else np.array([0.0])
        best = max(eps, key=lambda e: e["delta"]) if eps else None
        worst = min(eps, key=lambda e: e["delta"]) if eps else None

        def fmt(e):
            return f"{e['entry_decision']}->{e['last_held_session']} {e['delta'] * 100:+.2f}pp"

        L.append(f"  {arm:3s} {a['early_time_in_market'] * 100:6.2f}% {len(eps):4d} {d.sum() * 100:+8.2f}pp "
                 f"{np.median(d) * 100:+8.2f}pp | {fmt(best) if best else '-'} | {fmt(worst) if worst else '-'}")
    L.append("")
    L.append("after tax ($100k, base financing, m=2, liquidated at the end; progressive 2026 tariff): "
             "window | arm | pre-tax CAGR | after-tax CAGR | maxDD | tax USD | trades/yr | sells/yr | d vs P0")
    for wn in TAX_WINDOWS:
        for arm in ARMS:
            r = tax["windows"][wn][arm]
            d = r["progressive"]["cagr"] - tax["windows"][wn][CONTROL]["progressive"]["cagr"]
            L.append(f"  {wn:20s} {arm:3s} {pct(r['pre_tax']['cagr'])} {pct(r['progressive']['cagr'])} "
                     f"{pct(r['progressive']['max_dd'])} {r['progressive']['tax_usd']:12,.0f} "
                     f"{r['progressive']['trades_per_year']:8.2f} {r['progressive']['sells_per_year']:8.2f} "
                     f"{d * 100:+7.2f}pp")
    for arm in CANDIDATES + REPORT_ONLY:
        L.append(f"  {arm} beats P0 after tax in all three windows: " +
                 " ".join(f"{wn}={'Y' if beats[arm][wn] else 'N'}" for wn in TAX_WINDOWS) +
                 f" -> {'PASS' if all(beats[arm].values()) else 'FAIL'}")
    L.append("")
    L.append(f"after-tax bootstrap (block-20, {TAX_BOOT_PATHS} paths, {TAX_BOOT_LO}..{TAX_BOOT_HI}, "
             f"fixed FX + TRY calendar, $100k, liquidated):")
    for label, res in (("B1-recent progressive", b1), ("B2 flat 20%", b2)):
        L.append(f"  {label}: " + "  ".join(f"{a} median {pct(res[a]['cagr_median'])}" for a in ARMS))
        for a in CANDIDATES + REPORT_ONLY:
            L.append(f"    {a} - P0: median {res[a]['median_delta_cagr_pp']:+.3f} pp, "
                     f"mean {res[a]['mean_delta_cagr_pp']:+.3f}, share>0 {pct(res[a]['share_gt_0'])}, "
                     f"p05 {res[a]['p05_delta_cagr_pp']:+.3f}, p95 {res[a]['p95_delta_cagr_pp']:+.3f}")
    L.append("")
    L.append(f"selection: eligible {eligible}; selected {selected} "
             f"(P4 report-only, never selected)")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
