#!/usr/bin/env python3
"""r2_tbill_combo_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --smoke    synthetic random-walk prices only: run the whole path (two-leg pre-tax
             simulator, arm invariants, after-tax two-book runner) and assert it is finite
             and sane. Uses no market data. This is the only mode run before the
             pre-registration commit.
  --harness  C0/R2/S2 reproduction against published results plus the after-tax anchors.
             Simulates no candidate arm on real data beyond the reproduction checks.
  --run      re-runs the harness, stops if it fails, then runs every arm (C0, R2, S2, RS)
             on the main window, the main block-20 bootstrap, the six out-of-sample
             windows, the pooled OOS bootstrap, the gates, the after-tax stage and the two
             clean after-tax bootstraps. Writes RESULTS.json and SUMMARY.txt.

Engine helpers (synthetic 2x, SMA, vol20, vt, metrics) are imported from
research/trend_robustness_v2/screen.py (its main() is never run). The early re-entry arm
R2 and its per-episode machinery are copied from research/fast_reentry_v1/screen.py (tag
archive/research/fast-reentry-v1-2026-10); the two-leg T-bill simulator and the BIL* model
come from research/tbill_sweep_v1/screen.py (tag archive/research/tbill-sweep-v1-2026-10,
`simulate` mode "s2" and `bill_series`). The after-tax engine is
research/r2_tbill_combo_v1/tax_engine.py, a byte copy of
research/fast_reentry_v2/tax_engine.py, which is itself the byte-faithful copy of
research/vt_level_tax_v1/screen.py's corrected engine; the two-book (QLD* + BIL*) wrapper is
new here.
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
C0_BAND = trv2.BAND  # 0.005
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
ER_BIL = 0.001356  # BIL expense ratio (tbill_sweep_v1)
S2_BAND_X = 0.10  # S2_10 bill band
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

ARMS = ("C0", "R2", "S2", "RS")
CANDIDATE = "RS"
# QLD* leg of each arm: RS uses R2's early path, S2 uses production E2's QLD* leg.
QLD_ARM = {"C0": "C0", "R2": "R2", "S2": "C0", "RS": "R2"}
USE_BILL = {"C0": False, "R2": False, "S2": True, "RS": True}

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
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json, main_window
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
REF_S2 = {  # research/tbill_sweep_v1/RESULTS.json, historical (total turnover, both legs)
    ("base", 1): (0.154085, 0.453016, 11.422107),
    ("base", 2): (0.148820, 0.459942, 11.422107),
    ("calibrated", 1): (0.148343, 0.453943, 11.418331),
    ("calibrated", 2): (0.143106, 0.460857, 11.418331),
}


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


def _rolling_max(price, window=252):
    P, T = price.shape
    out = np.full((P, T), np.nan)
    for t in range(T):
        lo = max(0, t - window + 1)
        out[:, t] = np.nanmax(price[:, lo:t + 1], axis=1)
    return out


def early_components(price):
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    lag_cond = price > close_lag20
    don = don_up(price)
    rmax = _rolling_max(price, MED_WINDOW)
    return sma50_up, vol_p, med_p, lag_cond, don, rmax


def arm_signals(price, e2_on, vol_instr, arm, comps=None):
    """Return (target weights [P,T], early_active [P,T]) for a fast-reentry arm

    (copied from fast_reentry_v1/screen.py; arm "C0" returns plain production wants with no
    early path, arm "R2" is the half-weight early re-entry)."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    vol_instr = np.atleast_2d(np.asarray(vol_instr, dtype=float))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond, don, rmax = early_components(price) if comps is None else comps

    early_active = np.zeros(P, dtype=bool)
    eligible_until = np.full(P, -10 ** 9, dtype=int)
    prev_e2 = np.zeros(P, dtype=bool)

    on_eff = np.zeros((P, T), dtype=bool)
    early_hist = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    is_R2 = (arm == "R2")
    is_R4 = (arm == "R4")

    for t in range(T):
        e2t = e2_on[:, t].copy()
        if is_R4:
            exit_now = prev_e2 & (~e2t)
            if np.any(exit_now):
                ratio = price[:, t] / rmax[:, t]
                crash = exit_now & np.isfinite(rmax[:, t]) & (ratio <= 0.8)
                eligible_until = np.where(crash, t + 252, eligible_until)

        early_active = np.where(e2t, False, early_active)

        if arm in ("R1", "R2", "R4"):
            exit_cond = ~sma50_up[:, t]
        elif arm == "R3":
            exit_cond = (~don[:, t]) | (~sma50_up[:, t])
        else:
            exit_cond = np.zeros(P, dtype=bool)
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)

        if arm in ("R1", "R2", "R4"):
            entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                          & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        elif arm == "R3":
            entry_cond = don[:, t] & sma50_up[:, t]
        else:
            entry_cond = np.zeros(P, dtype=bool)

        if is_R4:
            entry_cond = entry_cond & (t <= eligible_until)

        can_enter = (~early_active) & (~e2t)
        if arm in ("R1", "R2", "R4"):
            can_enter = can_enter & np.isfinite(med_p[:, t])
        enter = can_enter & entry_cond
        early_active = np.where(enter, True, early_active)

        active = e2t | early_active
        f = np.where(e2t, 1.0, np.where(early_active, (0.5 if is_R2 else 1.0), 0.0))
        f = np.where(active, f, 0.0)

        on_eff[:, t] = active
        early_hist[:, t] = early_active
        factor[:, t] = f
        prev_e2 = e2t.copy()

    vt = trv2.vt_of(vol_instr)
    raw = on_eff.astype(float) * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    wants = raw * fcap
    return wants, early_hist


def arm_legs(price, e2_on, vol_instr, arm, comps=None):
    """(QLD* targets [1,T], effective-on [1,T]) of one arm.

    effective-on = production E2's vote OR the early path (only RS/R2 have one)."""
    w, ea = arm_signals(price, e2_on, vol_instr, QLD_ARM[arm], comps)
    return w, (np.atleast_2d(e2_on).astype(bool) | ea)


# ----------------------------------------------------------------------------- bill instrument
def bill_series(y, er=ER_BIL):
    """Synthetic T-bill ETF (tbill_sweep_v1.bill_series): session i accrues (rate[i-1] - er)/252
    between its open and close; open = previous close. y is the decimal rate [P, T]."""
    y = np.atleast_2d(y)
    g = 1 + (y[:, :-1] - er) / 252
    close = np.concatenate([np.ones((y.shape[0], 1)), np.cumprod(g, axis=1)], axis=1)
    opn = np.concatenate([np.ones((y.shape[0], 1)), close[:, :-1]], axis=1)
    return opn, close


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


# ----------------------------------------------------------------------------- two-leg engine
def simulate_two(qo, qc, wq, act, cost, start, band=C0_BAND, bo=None, bc=None, band_x=S2_BAND_X):
    """Two-leg portfolio: QLD* (target wq, production resize rule) + synthetic T-bill ETF.

    Per session i, after the overnight move: the QLD* leg trades to wq[i-1] when the position
    flips or the drift exceeds `band`; the bill leg follows tbill_sweep_v1's S2_10 rule --
    while the arm is off (`act[i-1]` false) the bill target is 0.98 and it trades on a 0.5 pp
    drift, while the arm is active the bill is kept in [0.98 - w - X, 0.98 - w] and reset to
    max(0, 0.98 - w - X/2) on a breach, where w is the QLD* target and X = band_x. Cost is
    4 bps x m per side, charged on the traded notional of both legs. Idle cash earns zero.
    Returns (nav [P, T-start+1], total turnover [P], qld turnover [P], bill turnover [P],
    qld event codes [P, T-start], traded |dw| [P, T-start], bill trades [P], bill sells [P],
    bill sessions [P])."""
    qo, qc, wq, act = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (qo, qc, wq, act))
    P, T = qc.shape
    use_bill = bo is not None
    if use_bill:
        bo, bc = np.atleast_2d(bo), np.atleast_2d(bc)
    nav = np.ones(P)
    w = np.zeros(P)
    wb = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    qturn = np.zeros(P)
    bturn = np.zeros(P)
    nq = np.zeros(P, dtype=np.int64)
    nb = np.zeros(P, dtype=np.int64)
    nbs = np.zeros(P, dtype=np.int64)
    bdays = np.zeros(P)
    codes = np.zeros((P, T - start), dtype=np.int8)
    traded = np.zeros((P, T - start))
    for i in range(start, T):
        g_on = qo[:, i] / qc[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        if use_bill:
            gb = bo[:, i] / bc[:, i - 1]
            vb = wb * gb
            tot = 1 - w - wb + va + vb
        else:
            tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        if use_bill:
            wb = vb / tot

        want = wq[:, i - 1]
        d = np.abs(want - w)
        flip = (w > 0) != (want > 0)
        qtrade = flip | (d > band)

        if use_bill:
            off = ~(act[:, i - 1] > 0.5)
            hi = CAP - want
            lo = hi - band_x
            bt = np.where(off, CAP, np.maximum(0.0, hi - band_x / 2))
            btrade = np.where(off, np.abs(CAP - wb) > band, (wb > hi + 1e-12) | (wb < lo))
            bd = np.where(btrade, np.abs(bt - wb), 0.0)
        else:
            btrade = np.zeros(P, dtype=bool)
            bd = np.zeros(P)

        qd = np.where(qtrade, d, 0.0)
        nav = nav * (1 - cost * (qd + bd))
        qturn = qturn + qd
        bturn = bturn + bd
        nq = nq + qtrade.astype(np.int64)
        nb = nb + btrade.astype(np.int64)
        nbs = nbs + (btrade & (wb > 0)).astype(np.int64)
        k = i - start
        codes[:, k] = np.where(~qtrade, 0, np.where(flip & (want > 0), 1, np.where(flip, 2, np.where(want > w, 3, 4))))
        traded[:, k] = qd
        w = np.where(qtrade, want, w)
        if use_bill:
            wb = np.where(btrade, bt, wb)
            bdays = bdays + (wb > 0)

        ga = qc[:, i] / qo[:, i]
        va = w * ga
        if use_bill:
            vb = wb * (bc[:, i] / bo[:, i])
            tot = 1 - w - wb + va + vb
        else:
            tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        if use_bill:
            wb = vb / tot
        out[:, k + 1] = nav
    return out, qturn + bturn, qturn, bturn, codes, traded, nb, nbs, bdays


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


def arm_stats(dates, nav, qturn, bturn, ntr, codes, traded, wants, start, extras, early_active=None,
              nb=None, nbs=None, bdays=None):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float((qturn + bturn) / years)
    st["qld_turnover_annual"] = float(qturn / years)
    st["bill_turnover_annual"] = float(bturn / years)
    st["trades"] = int(ntr)
    st["bill_trades"] = int(nb) if nb is not None else 0
    st["bill_sells"] = int(nbs) if nbs is not None else 0
    st["bill_sessions_share"] = float(bdays / (len(dates) - 1)) if bdays is not None else 0.0
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
    return days, arr, y, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0], q[1]


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, y, o, c, e2, v20, price = main_inputs(spread)
        dates = days[START - 1:]
        comps = early_components(price)
        legs = {arm: arm_legs(price, e2, v20, arm, comps) for arm in arms}
        bo, bc = bill_series(y, ER_BIL)
        res[md] = {}
        for m in m_list:
            hm = {}
            navs = {}
            for arm in arms:
                wq, act = legs[arm]
                bill = USE_BILL[arm]
                nav, turn, qt, bt, codes, traded, nb, nbs, bdays = simulate_two(
                    o[None, :], c[None, :], wq, act, COST_PER_SIDE * m, START,
                    band=C0_BAND, bo=bo if bill else None, bc=bc if bill else None)
                navs[arm] = nav[0]
                hm[arm] = arm_stats(dates, nav[0], qt[0], bt[0], 0, codes[0], traded[0], wq[0], START,
                                    extras and m == 2, early_active=(np.atleast_2d(act) & ~np.atleast_2d(e2)),
                                    nb=nb[0] if bill else None, nbs=nbs[0] if bill else None,
                                    bdays=bdays[0] if bill else None)
            if extras and m == 2 and "C0" in arms:
                for arm in arms:
                    if arm != "C0":
                        ea = (legs[arm][1] & ~np.atleast_2d(e2))
                        hm[arm]["early_episodes"] = early_episodes(dates, navs[arm], navs["C0"], ea[0][START - 1:])
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def harness():
    h = run_main(ARMS, extras=False, m_list=(1, 2))
    checks, bad = {}, 0
    refs = (("C0", REF_DEV), ("R2", REF_R2), ("S2", REF_S2))
    for arm, ref in refs:
        for (md, m), (cagr, dd, turn) in ref.items():
            x = h[md][f"m{m}"][arm]
            for k, r in (("cagr", cagr), ("max_dd", dd), ("turnover_annual", turn)):
                d = (x[k] - r) * (100 if k != "turnover_annual" else 1)
                checks[f"{arm}_{md}_m{m}_{k}"] = {"reference": r, "reproduced": x[k], "diff": d}
                if abs(d) > (TOL * 100 if k != "turnover_annual" else TOL):
                    bad += 1
    # two-leg simulator with an empty bill leg == trend_robustness_v2.simulate_multi, bit for bit
    days, arr, y, o, c, e2, v20, price = main_inputs(0.0)
    wq = wants_for(e2, v20)
    act = np.atleast_2d(e2)
    nav_a = simulate_two(o[None, :], c[None, :], wq, act, COST_PER_SIDE * 2, START)[0][0]
    nav_b = trv2.simulate_multi(o, c, wq[:, None, :], COST_PER_SIDE * 2, START)[0][0]
    nav_c = simulate_band(o, c, wq, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    sim_equal = bool(np.array_equal(nav_a, nav_b)) and bool(np.array_equal(nav_a, nav_c))
    ok = (bad == 0) and sim_equal
    return {"window": h["window"], "reproduction_checks": checks, "tolerance_pp": TOL * 100,
            "n_checks": len(checks), "n_outside_tolerance": bad,
            "two_leg_equals_simulate_multi_and_band": sim_equal, "pass": bool(ok)}


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        base = {c: cg[(md, c)] for c in ("C0", "R2")}
        for a in arms:
            c = cg[(md, a)]
            e = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                 "turnover_median": float(np.median(tv[(md, a)]))}
            for ctrl in ("C0", "R2"):
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
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = early_components(pc)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol_instr = trv2.vol_n(scl)
            bo, bc = bill_series(yyp, ER_BIL)
            for arm in arms:
                w, act = arm_legs(pc, e2, vol_instr, arm, comps)
                bill = USE_BILL[arm]
                nav, turn, _, _, _, _, _, _, _ = simulate_two(
                    so, scl, w, act, COST_PER_SIDE * 2, start, band=C0_BAND,
                    bo=bo if bill else None, bc=bc if bill else None)
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
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = e2_up(c)
    vol_instr = trv2.vol_n(sc)
    comps = early_components(c)
    bo, bc = bill_series(y[None, :], ER_BIL)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1},
           "mean_rate_off": float(y[start - 1:-1][~e2[0, start - 1:-1]].mean())}
    for arm in arms:
        w, act = arm_legs(c, e2, vol_instr, arm, comps)
        bill = USE_BILL[arm]
        nav, turn, qt, bt, codes, traded, nb, nbs, bdays = simulate_two(
            so[None, :], sc[None, :], w, act, COST_PER_SIDE * 2, start, band=C0_BAND,
            bo=bo if bill else None, bc=bc if bill else None)
        nav = nav[0]
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)), "max_dd": float(trv2.max_dd(nav)[0]),
                    "turnover_annual": float(turn[0] / years), "bill_turnover_annual": float(bt[0] / years),
                    "bill_trades": int(nb[0]) if bill else 0,
                    "round_trips_per_year": ev["round_trips_per_year"], "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_market(key, rng, arms):
    """e2_confirmation_v1.bootstrap_market draws (shared generator, markets in order), all arms, m = 2.
    The bill leg is built from the same resampled rate series the financing uses."""
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
        comps = early_components(cc)
        bo, bc = bill_series(yyp, ER_BIL)
        for arm in arms:
            w, act = arm_legs(cc, e2, vol_instr, arm, comps)
            bill = USE_BILL[arm]
            nav = simulate_two(so, sc, w, act, COST_PER_SIDE * 2, START, band=C0_BAND,
                               bo=bo if bill else None, bc=bc if bill else None)[0]
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
    lim = main["base"]["m2"]["C0"]["turnover_annual"] * 2.0
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    r["G9_limit"] = lim
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


# ============================================================================= after tax
def _share_books(qa, qb, taxable=True):
    """One Turkish tax base: the bill book's realised gains feed the same annual dicts as the QLD book.

    taxable=False is a report-only diagnostic (not a pre-registered arm): the bill book keeps its own
    year_gain/year_div/year_wht, so BIL*'s gains are recorded but never taxed -- the upper bound on
    what the sleeve would be worth if real BIL's monthly distributions were treated as dividends
    instead of a taxed-on-sale capital gain."""
    for k in ("gain_by_reason", "pos_gain_by_reason", "neg_gain_by_reason"):
        setattr(qb, k, getattr(qa, k))
    for k in ("year_gain", "year_div", "year_wht"):
        setattr(qb, k, getattr(qa, k) if taxable else defaultdict(float))


def _sell_pro_rata(need, idx, books, cashbox, cost, reason):
    """Sell `need` of gross notional at the books' given (index, price), pro rata to market value.
    Returns the number of sell events (tbill_sweep_v1's single-leg funding generalised)."""
    tot = sum(b.units * p for b, p, _ in books)
    if tot <= 0:
        return 0
    n = 0
    for b, p, ix in books:
        v = b.units * p
        if v <= 0:
            continue
        gross = need * (v / tot)
        units = gross / p
        b.sell(ix, p, units, gross * cost, reason)
        n += 1
    return n


def tax_run(capital, i0, i1, days, mkt, oq, cq, ob, cb, wq, act, use_bill, mode, bill_taxable=True):
    """Two-book after-tax strategy run (QLD* + optional BIL*).

    The session loop, trade rules, fee accounting, tax assessment, instalment funding and
    liquidation mirror research/vt_level_tax_v1/screen.py's `run_strategy` (progressive /
    pre_tax) and `run_strategy_flat` (flat 20 %); the only addition is the second lot book,
    whose gains enter the same annual net realised gain. mode in {"pre_tax", "progressive",
    "flat"}. Opened `days[i0-1]` must exist."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq, act = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq, act))
    ob = np.asarray(ob, dtype=float).flatten() if use_bill else np.zeros(len(days))
    cb = np.asarray(cb, dtype=float).flatten() if use_bill else np.ones(len(days))
    cost = COST_PER_SIDE * 2
    band = C0_BAND
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        qb = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        qb = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1)
    _share_books(qa, qb, bill_taxable)
    cashbox["v"] = float(capital)  # the account constructors reset the shared cashbox

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = b_sells = tax_sales = 0
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
                        books = [(qa, float(cq[i - 1]), i - 1), (qb, float(cb[i - 1]), i - 1)]
                        tax_sales += _sell_pro_rata(usd - cashbox["v"], i - 1, books, cashbox, cost, "tax")
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
                    books = [(qa, float(oq[i]), i), (qb, float(ob[i]), i)]
                    tax_sales += _sell_pro_rata(need, i, books, cashbox, cost, "tax")
                cashbox["v"] -= usd
                qa.tax_paid_try[ty] += amt
                qa.tax_paid_usd += usd

        poq, pcq = float(oq[i]), float(cq[i])
        pob, pcb = (float(ob[i]), float(cb[i])) if use_bill else (0.0, 0.0)
        held_q = qa.units * poq
        held_b = qb.units * pob
        nav_open = cashbox["v"] + held_q + held_b
        if nav_open <= 0:
            nav_open = cashbox["v"]
        wq_cur = held_q / nav_open if nav_open > 0 else 0.0
        wb_cur = held_b / nav_open if nav_open > 0 else 0.0

        tgt_q = float(wq[i - 1]) if i > 0 else float(wq[0])
        tgt_q = 0.0 if tgt_q != tgt_q else tgt_q
        trade_q = abs(tgt_q - wq_cur) > band
        if qa.units == 0 and tgt_q > 1e-12 and prev_tgt is None:
            trade_q = True
        if qa.units > 1e-12 and tgt_q <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade_q = True

        if use_bill:
            off = not bool(act[i - 1] > 0.5)
            hi = CAP - tgt_q
            lo = hi - S2_BAND_X
            if off:
                tgt_b = CAP
                trade_b = abs(CAP - wb_cur) > band
            else:
                tgt_b = max(0.0, hi - S2_BAND_X / 2)
                trade_b = (wb_cur > hi + 1e-12) or (wb_cur < lo)
        else:
            tgt_b, trade_b = 0.0, False

        d_q = abs(tgt_q - wq_cur) if trade_q else 0.0
        d_b = abs(tgt_b - wb_cur) if trade_b else 0.0
        fee = nav_open * cost * (d_q + d_b)
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
        if trade_b:
            target_units = tgt_b * nav_after / pob if pob > 0 else 0.0
            du = target_units - qb.units
            if du > 1e-12:
                qb.buy(i, pob, du * pob, nav_open * cost * d_b)
            elif du < -1e-12:
                qb.sell(i, pob, -du, nav_open * cost * d_b, "resize")
                b_sells += 1
            trades += 1
        nav[i - i0 + 1] = cashbox["v"] + qa.units * pcq + qb.units * pcb
        prev_tgt = tgt_q

    end_y = int(days[i1][:4])
    if qa.units > 0:
        qa.sell(i1, float(cq[i1]), qa.units, qa.units * float(cq[i1]) * cost, "liquidation")
    if qb.units > 0:
        qb.sell(i1, float(cb[i1]), qb.units, qb.units * float(cb[i1]) * cost, "liquidation")
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
        "strategy_trades": int(trades), "qld_sells": int(q_sells), "bill_sells": int(b_sells),
        "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + b_sells + tax_sales),
        "fees_usd": float(qa.fees + qb.fees),
        "indexation_relief_try": float(qa.index_relief_try + qb.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "_nav": nav,
    }


def tax_inputs():
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing, as vt_level_tax_v1
    price, e2, v20 = q[1], e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = early_components(price)
    legs = {a: arm_legs(price, e2, v20, a, comps) for a in ARMS}
    bo, bc = bill_series(y, ER_BIL)
    return days, o, c, legs, bo[0], bc[0]


def after_tax_stage(legs, o, c, bo, bc, days):
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"capital_usd": TAX_CAPITAL, "cost_multiplier_m": 2, "financing": "base",
           "windows": {}, "anchors": {}, "single_leg_cross_check": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        out["windows"][wn] = {}
        for arm in ARMS:
            w, act = legs[arm]
            r = {}
            for mode in ("pre_tax", "progressive"):
                res = tax_run(TAX_CAPITAL, a, b, days, mkt, o, c, bo, bc, w[0], act[0],
                              USE_BILL[arm], mode)
                r[mode] = {"cagr": res["cagr_usd_liquidated"], "max_dd": res["max_dd_nav_usd"],
                           "terminal_usd": res["terminal_usd_liquidated"], "tax_usd": res["tax_total_usd_at_payment_fx"],
                           "trades_per_year": res["strategy_trades"] / res["years"],
                           "sells_per_year": res["sells"] / res["years"],
                           "tax_funding_sales": res["tax_funding_sales"]}
            if USE_BILL[arm]:
                res = tax_run(TAX_CAPITAL, a, b, days, mkt, o, c, bo, bc, w[0], act[0],
                              True, "progressive", bill_taxable=False)
                r["progressive_bill_untaxed"] = {
                    "cagr": res["cagr_usd_liquidated"], "max_dd": res["max_dd_nav_usd"],
                    "tax_usd": res["tax_total_usd_at_payment_fx"], "note": "report-only diagnostic"}
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
    out["pass"] = all(abs(v["diff_pp"]) <= 0.005 for wn in out["anchors"].values() for v in wn.values())

    # cross-check: my two-book runner with an empty bill book == the vendored single-leg engine
    for wn, (a, b) in TAX_WINDOWS.items():
        w, act = legs["C0"]
        te.init(days, {"C0": (o, c)}, {"C0": w[0]})
        ref = te.run_strategy("C0", "pre_tax", TAX_CAPITAL, a, b, mkt)
        mine = tax_run(TAX_CAPITAL, a, b, days, mkt, o, c, bo, bc, w[0], act[0], False, "pre_tax")
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
    calendar with its fixed FX. Reports median delta CAGR and share > 0 for RS-R2 and RS-C0."""
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
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)
        vol = trv2.vol_n(sc)
        comps = early_components(pc)
        bo, bc = bill_series(yyp, ER_BIL)
        mkt = te.Market(fixed_dates, fixed_fx, ufe)
        for arm in ARMS:
            w, act = arm_legs(pc, e2, vol, arm, comps)
            for mode in ("progressive", "flat"):
                r = tax_run(capital, i0, i1, fixed_dates, mkt, so[0], sc[0], bo[0], bc[0],
                            w[0], act[0], USE_BILL[arm], mode)
                if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                    raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode}")
                res[mode][arm]["cagr"].append(r["cagr_usd_liquidated"])
                res[mode][arm]["maxdd"].append(r["max_dd_nav_usd"])
                res[mode][arm]["sells"].append(r["sells"] / r["years"])
    out = {}
    for mode in ("progressive", "flat"):
        e = {"tax_kind": mode, "n_paths": n_paths, "calendar_start": fixed_dates[0],
             "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK,
             "capital_usd": capital}
        for ctrl in ("C0", "R2"):
            d = np.array(res[mode]["RS"]["cagr"]) - np.array(res[mode][ctrl]["cagr"])
            e[f"RS_vs_{ctrl}"] = {
                "median_delta_cagr_pp": float(np.median(d) * 100), "mean_delta_cagr_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)), "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100),
                "cagr_median": float(np.median(res[mode]["RS"]["cagr"])),
                "ctrl_cagr_median": float(np.median(res[mode][ctrl]["cagr"]))}
        for a in ARMS:
            e[a] = {"cagr_median": float(np.median(res[mode][a]["cagr"])),
                    "maxdd_median": float(np.median(res[mode][a]["maxdd"])),
                    "sells_per_year_median": float(np.median(res[mode][a]["sells"]))}
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
    so, sc = trv2.synthetic_2x(o, price, np.zeros(n), y, EXP_Q)
    e2 = e2_up(price)
    v20 = trv2.vol_n(sc)[0]
    comps = early_components(price)
    bo, bc = bill_series(y[None, :], ER_BIL)
    checks, outs = {}, {}
    for arm in ARMS:
        w, act = arm_legs(price, e2, v20, arm, comps)
        assert w.shape == (1, n) and np.all(w <= CAP + 1e-12) and np.all(w >= 0), arm
        assert not np.any(np.atleast_2d(e2) & arm_signals(price, e2, v20, QLD_ARM[arm], comps)[1]), arm
        obj = simulate_two(so[None, :], sc[None, :], w, act, COST_PER_SIDE * 2, START, band=C0_BAND,
                           bo=bo if USE_BILL[arm] else None, bc=bc if USE_BILL[arm] else None)
        nav = obj[0][0]
        assert np.all(np.isfinite(nav)) and np.all(nav > 0), arm
        # gross exposure never above the cap by more than drift
        assert float(nav[-1]) > 0.5, arm
        checks[arm] = {"mean_target": float(w.mean()), "early_share": float(
            np.mean(np.atleast_2d(act) & ~np.atleast_2d(e2))), "nav_end": float(nav[-1]),
            "turnover": float(obj[1][0]), "bill_trades": int(obj[6][0])}
        outs[arm] = (w, act)
    # after-tax two-book path on a synthetic market
    ufe = {f"{yr}-{mo:02d}": float(1000 + 12 * yr + 5 * mo) for yr in range(1995, 2027) for mo in range(1, 13)}
    mkt = te.Market(days, np.full(n, 30.0), ufe)
    tax = {}
    for arm in ARMS:
        w, act = outs[arm]
        r_pre = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, bo[0], bc[0], w[0], act[0],
                        USE_BILL[arm], "pre_tax")
        r_prog = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, bo[0], bc[0], w[0], act[0],
                         USE_BILL[arm], "progressive")
        r_flat = tax_run(TAX_CAPITAL, 300, n - 1, days, mkt, so, sc, bo[0], bc[0], w[0], act[0],
                         USE_BILL[arm], "flat")
        for r in (r_pre, r_prog, r_flat):
            assert np.isfinite(r["cagr_usd_liquidated"]) and r["terminal_usd_liquidated"] > 0, arm
        assert r_prog["cagr_usd_liquidated"] <= r_pre["cagr_usd_liquidated"] + 1e-9, arm
        assert r_flat["cagr_usd_liquidated"] <= r_pre["cagr_usd_liquidated"] + 1e-9, arm
        tax[arm] = {"pre_tax": r_pre["cagr_usd_liquidated"], "progressive": r_prog["cagr_usd_liquidated"],
                    "flat": r_flat["cagr_usd_liquidated"], "sells_per_year": r_prog["sells"] / r_prog["years"]}
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
    print(f"harness two-leg == simulate_multi == band: {hc['two_leg_equals_simulate_multi_and_band']}")

    # after-tax anchors and cross-check (real data, but not a candidate arm)
    days, o, c, legs, bo, bc = tax_inputs()
    tax = after_tax_stage(legs, o, c, bo, bc, days)
    for wn, v in tax["anchors"].items():
        print(f"tax anchor {wn}: pre_tax {v['pre_tax']['diff_pp']:+.5f} progressive "
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
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("OOS done", flush=True)

    gates = {}
    for arm in ARMS[1:]:
        gates[arm] = historical_gates(main_res, boot, arm, "C0")
        gates[arm].update(oos_gates(oos, oos_boot["pooled"], arm))
        gates[arm]["pass_all"] = bool(all(gates[arm][k] for k in GATE_KEYS))
    gr = {md: g_return(main_res[md]["m2"]["RS"], main_res[md]["m2"]["R2"]) for md in FIN}
    g5r = {md: bool(boot[md]["RS"]["diff_vs_R2_median"] >= 0) for md in FIN}
    gates["RS"]["G1R_vs_R2"] = gr
    gates["RS"]["G1R_pass"] = bool(all(gr.values()))
    gates["RS"]["G5R_vs_R2"] = g5r
    gates["RS"]["G5R_median_pp"] = {md: boot[md]["RS"]["diff_vs_R2_median"] * 100 for md in FIN}
    gates["RS"]["G5R_pass"] = bool(all(g5r.values()))
    gates["RS"]["pass_all_with_R2"] = bool(gates["RS"]["pass_all"] and gates["RS"]["G1R_pass"] and gates["RS"]["G5R_pass"])
    print("gates done", flush=True)

    tax_boot = after_tax_bootstrap()
    print("after-tax bootstrap done", flush=True)

    wins = {wn: {a: tax["windows"][wn][a]["progressive"]["cagr"] for a in ARMS} for wn in TAX_WINDOWS}
    beats_r2 = {wn: bool(wins[wn]["RS"] > wins[wn]["R2"]) for wn in TAX_WINDOWS}
    b1, b2 = tax_boot["progressive"], tax_boot["flat"]
    after_tax_ok = bool(all(beats_r2.values())
                        and b1["RS_vs_R2"]["median_delta_cagr_pp"] >= 0 and b1["RS_vs_R2"]["share_gt_0"] >= 0.55
                        and b2["RS_vs_R2"]["median_delta_cagr_pp"] >= 0 and b2["RS_vs_R2"]["share_gt_0"] >= 0.55)
    worth = bool(gates["RS"]["pass_all_with_R2"] and after_tax_ok)

    out = {
        "campaign": "r2_tbill_combo_v1",
        "params": {
            "arms": list(ARMS), "candidate": CANDIDATE, "qld_arm_of": QLD_ARM, "use_bill": USE_BILL,
            "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "c0_band": C0_BAND, "cap": CAP,
            "early_path": {"sma_fast": SMA_FAST, "momentum_lag": LAG_FAST, "vol_median_window": MED_WINDOW,
                           "r2_weight": 0.5},
            "bill": {"er_bil": ER_BIL, "band_x": S2_BAND_X, "source": "tbill_sweep_v1 S2_10"},
            "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
            "main_window_cost_multipliers": [1, 2, 4], "financing_spread": FIN,
            "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                               "back_extension": EXT, "measured": MEASURED, "m": 2},
            "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                              "returns_per_path": OOS_BOOT_L, "m": 2},
            "after_tax": {"capital_usd": TAX_CAPITAL, "windows": TAX_WINDOWS, "m": 2,
                          "financing": "base", "engine": "vt_level_tax_v1 corrected (byte copy in tax_engine.py)",
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                        "modes": ["progressive (B1-recent)", "flat 20% (B2)"]}},
            "signals": "production E2 1.1.0 on price closes: SMA200, 252-session price return > 0, "
                       "close > 126-session max/min midpoint; 2 of 3",
        },
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "after_tax_100k": tax,
        "after_tax_bootstrap": tax_boot,
        "after_tax_beats_r2_all_windows": beats_r2,
        "after_tax_ok": after_tax_ok,
        "verdict_worth_integrating": worth,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    def p(x):
        return f"{x * 100:7.2f}%"

    w = main_res["window"]
    L = [f"r2_tbill_combo_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | C0 = production E2 1.1.0",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} ({hc['n_checks']} reproduction checks, "
         f"{hc['n_outside_tolerance']} outside 0.01 pp; two-leg == simulate_multi == band "
         f"{hc['two_leg_equals_simulate_multi_and_band']}); tax anchors max |diff| "
         f"{max(abs(v['diff_pp']) for wn in tax['anchors'].values() for v in wn.values()):.5f} pp; "
         f"tax single-leg cross-check {xchk:.6f} pp", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 totTurn qldTurn billTurn turn/yr | "
                 f"m=1 CAGR maxDD | boot20 med, dmed vs C0, share>=C0")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} {a['qld_turnover_annual']:5.2f} {a['bill_turnover_annual']:5.2f} "
                     f"{a['turnover_annual']:5.2f} | {p(main_res[md]['m1'][arm]['cagr'])} "
                     f"{p(main_res[md]['m1'][arm]['max_dd'])} | {p(b['cagr_median'])} "
                     f"{b['diff_vs_C0_median'] * 100:+7.3f}pp "
                     + (p(b['share_ge_C0']) if arm != 'C0' else '     --'))
        L.append("")
    L.append("trades (base, m=2): arm | full: round trips/yr, resizes/yr, sells/yr, bill trades, bill sells | 2017+")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        e, f = a["events_full"], a["events_2017_on"]
        L.append(f"  {arm:3s} {e['round_trips_per_year']:5.2f} {e['resizes_per_year']:6.2f} {e['sells_per_year']:6.2f} "
                 f"{a['bill_trades']:5d} {a['bill_sells']:5d} | 2017+ {f['round_trips_per_year']:5.2f} "
                 f"{f['sells_per_year']:6.2f} turn {f['turnover_per_year']:5.2f} bill sessions "
                 f"{p(a['bill_sessions_share'])}")
    L.append("")
    L.append("OOS (calibrated, m=2): window | arm CAGR / maxDD")
    for k, v in oos.items():
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in ARMS))
    L.append("OOS pooled bootstrap (M1..M5, 10,000 paths) median(arm-C0): " +
             "  ".join(f"{a} {oos_boot['pooled'][a]['diff_vs_C0_median'] * 100:+.3f}pp" for a in ARMS))
    L.append("")
    L.append("gates vs C0 (" + " ".join(GATE_KEYS) + "):")
    for arm in ARMS[1:]:
        g = gates[arm]
        L.append(f"  {arm:3s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("RS vs R2: G1 (both financings) " +
             " ".join(f"{md}={'P' if gr[md] else 'F'}" for md in FIN) +
             f" -> {'PASS' if gates['RS']['G1R_pass'] else 'FAIL'}; G5R median " +
             " ".join(f"{md} {gates['RS']['G5R_median_pp'][md]:+.3f}pp" for md in FIN) +
             f" -> {'PASS' if gates['RS']['G5R_pass'] else 'FAIL'}")
    L.append("")
    L.append("cost stress (report only, not gated): arm | m=1 / m=2 / m=4 base CAGR, m=4 max DD")
    for arm in ARMS:
        r = main_res["base"]
        L.append(f"  {arm:3s} {p(r['m1'][arm]['cagr']).strip()} / {p(r['m2'][arm]['cagr']).strip()} / "
                 f"{p(r['m4'][arm]['cagr']).strip()} | {p(r['m4'][arm]['max_dd']).strip()}")
    L.append("")
    L.append("early path (base, m=2): arm | share of sessions | episodes | sum(arm-C0) | median delta | best | worst")
    for arm in ("R2", "RS"):
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
    L.append("after tax ($100k, base financing, m=2, liquidation at the end; progressive 2026 tariff):")
    L.append("  window | arm | pre-tax CAGR | after-tax CAGR | maxDD | tax USD | trades/yr | sells/yr")
    for wn in TAX_WINDOWS:
        for arm in ARMS:
            r = tax["windows"][wn][arm]
            L.append(f"  {wn:20s} {arm:3s} {p(r['pre_tax']['cagr'])} {p(r['progressive']['cagr'])} "
                     f"{p(r['progressive']['max_dd'])} {r['progressive']['tax_usd']:12,.0f} "
                     f"{r['progressive']['trades_per_year']:8.2f} {r['progressive']['sells_per_year']:8.2f}")
    L.append("  RS beats R2 after tax in all three windows: " +
             " ".join(f"{wn}={'Y' if beats_r2[wn] else 'N'}" for wn in TAX_WINDOWS) + f" -> {after_tax_ok and all(beats_r2.values())}")
    L.append("  report-only diagnostic, after-tax CAGR with the BIL* book NOT taxed (upper bound for a"
             " dividend-treated bill sleeve): window | S2 untaxed | RS untaxed | C0 | R2 | R2 untaxed-bill vs R2")
    for wn in TAX_WINDOWS:
        w = tax["windows"][wn]
        L.append(f"    {wn:20s} S2 {p(w['S2']['progressive_bill_untaxed']['cagr'])} RS "
                 f"{p(w['RS']['progressive_bill_untaxed']['cagr'])} | C0 {p(w['C0']['progressive']['cagr'])} "
                 f"R2 {p(w['R2']['progressive']['cagr'])} | RS-R2 "
                 f"{(w['RS']['progressive_bill_untaxed']['cagr'] - w['R2']['progressive']['cagr']) * 100:+.2f}pp")
    L.append("")
    L.append(f"after-tax bootstrap (block-20, {TAX_BOOT_PATHS} paths, {TAX_BOOT_LO}..{TAX_BOOT_HI}, fixed FX, $100k):")
    for label, res in (("B1-recent progressive", b1), ("B2 flat 20%", b2)):
        L.append(f"  {label:22s} RS median {p(res['RS']['cagr_median']).strip()}, C0 {p(res['C0']['cagr_median']).strip()}, "
                 f"R2 {p(res['R2']['cagr_median']).strip()}, S2 {p(res['S2']['cagr_median']).strip()}")
        for ctrl in ("C0", "R2"):
            v = res[f"RS_vs_{ctrl}"]
            L.append(f"    RS - {ctrl}: median {v['median_delta_cagr_pp']:+.3f} pp, mean {v['mean_delta_cagr_pp']:+.3f}, "
                     f"share>0 {p(v['share_gt_0'])}, p05 {v['p05_delta_cagr_pp']:+.3f}, p95 {v['p95_delta_cagr_pp']:+.3f}")
    L.append("")
    L.append(f"VERDICT: RS {'WORTH INTEGRATING' if worth else 'FAIL'} "
             f"(gates vs C0 {'PASS' if gates['RS']['pass_all'] else 'FAIL'}, vs R2 "
             f"{'PASS' if (gates['RS']['G1R_pass'] and gates['RS']['G5R_pass']) else 'FAIL'}, "
             f"after tax {'PASS' if after_tax_ok else 'FAIL'})")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
