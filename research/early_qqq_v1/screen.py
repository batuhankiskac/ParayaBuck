#!/usr/bin/env python3
"""early_qqq_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I5: deliver the early-re-entry leg of production R2 in the *unlevered* fund of the
same index (min(0.98, w) of QQQ) instead of 0.5*w of QLD, i.e. the same daily beta without
the embedded borrowing and with the cheaper fund's expense.

Modes
  --harness  C0/R2 reproduction only: the main-window pre-tax references of
             trend_robustness_v2 (`base`/`calibrated` x m=1/m=2), the multi-instrument
             simulator identity against trv2.simulate_multi, and the after-tax anchors at
             $100k (C0 = E2 and R2, progressive). Simulates no non-control arm.
  --run      re-runs the harness, stops if it fails, then runs every arm (C0, A1, A2, A3)
             on the main window, the main block-20 bootstrap, the six OOS windows, the
             pooled OOS bootstrap, the pre-tax gates G1-G9, the 21-ETF drawdown
             generalisation, the real-fund evaluation surface, the analytic bounds, the
             after-tax windows at $100k and the clean after-tax bootstraps B1-recent/B2,
             then applies the pre-registered selection rule. Writes RESULTS.json and
             SUMMARY.txt.

Pre-tax engine helpers (synthetic L-x, SMA, vol20, vt, metrics, bootstrap draws, OOS
loaders, gates) are copied from research/trend_robustness_v2/screen.py,
research/fast_reentry_v1/screen.py and research/r2_tbill_combo_v1/screen.py; the after-tax
model is the byte copy `tax_engine.py` of research/r2_tbill_combo_v1/tax_engine.py (itself
research/vt_level_tax_v1/screen.py's corrected engine).
"""

from __future__ import annotations

import bisect
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
_te_spec = importlib.util.spec_from_file_location("early_qqq_tax_engine", HERE / "tax_engine.py")
te = importlib.util.module_from_spec(_te_spec)
_te_spec.loader.exec_module(te)

BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
QQQ_REAL = ROOT / "data/historical/early_qqq_v1_qqq.csv"
QLD_REAL = ROOT / "data/historical/early_qqq_v1_qld.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
SMA_FAST, LAG_FAST, MED_WINDOW = 50, 20, 252
C0_BAND = trv2.BAND
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q, EXP_S = trv2.EXP_Q, trv2.EXP_S
QQQ_EXP, SPY_EXP = 0.0020, 0.000945
# instrument -> (leverage, expense, base symbol); base = QQQ/SPY bars of trv2.load()
INSTRUMENTS = {"QLD": (2.0, EXP_Q, "QQQ"), "QQQ": (1.0, QQQ_EXP, "QQQ"),
               "SPY": (1.0, SPY_EXP, "SPY"), "SSO": (2.0, EXP_S, "SPY")}
EARLY_INSTRUMENT = {"C0": "QLD", "A1": "QQQ", "A2": "SPY", "A3": "SSO"}
ARMS = ("C0", "A1", "A2", "A3")
CANDIDATES = ("A1", "A2")
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions
TOL = 1e-4
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")
OOS_GSPC_FIRST, OOS_GSPC_LAST = "1957-01-02", "1999-12-31"
EXTRA_FIN = 0.0075
MARKETS = {
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = TSM_N + MEASURED
DTB3_FIRST = "1954-01-04"
M = 2
COST = COST_PER_SIDE * M

# after tax (report/decision stage): vt_level_tax_v1's windows at $100k
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253

# ETF drawdown generalisation: every range_vol_v[12]_* instrument, range_vol_v2's quality rule
ETF_KEYS = ("DIA", "IWM", "SPY", "EEM", "EFA", "EWA", "EWC", "EWG", "EWH", "EWJ", "EWL", "EWS",
            "EWU", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
QUALITY_MAX_BAD = 0.05
REAL_WINDOW = ("2011-01-03", None)

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
TAX_ANCHORS = {  # vt_level_tax_v1 / r2_tbill_combo_v1 anchors at $100k, base financing, m=2
    "C0": {"2000_03_to_2026_09": (0.146715, 0.095340, 0.495643),
           "2010_01_to_end": (0.218852, 0.144968, 0.446920),
           "2017_01_to_end": (0.322564, 0.226932, 0.376052)},
    "R2": {"2000_03_to_2026_09": (0.154732, 0.101600, 0.467584),
           "2010_01_to_end": (0.221587, 0.147059, 0.437839),
           "2017_01_to_end": (0.325330, 0.228861, 0.362426)},
}


# ----------------------------------------------------------------------------- instruments and signals
def synthetic_L(q_open, q_close, q_div, y, L, expense, spread=0.0):
    """Daily-reset L x total return - expense - (L-1) x (DTB3 + spread); 1-D or [P, T] (time last)."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    fin = y[..., :-1] if spread == 0.0 else y[..., :-1] + spread
    g_cc = 1 + L * r_cc - (expense + (L - 1) * fin) / 252
    one = np.ones(q_close.shape[:-1] + (1,))
    close = np.concatenate([one, np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([one, close[..., :-1] * (1 + L * r_on)], axis=-1)
    return opn, close


def tsm_up_price(c, n=TSM_N):
    """up[t]: n-session price return > 0."""
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


def _rolling_median_vol(price_vol, window=MED_WINDOW):
    P, T = price_vol.shape
    out = np.full((P, T), np.nan)
    for t in range(T):
        lo = max(0, t - window + 1)
        w = price_vol[:, lo:t + 1]
        cnt = np.sum(np.isfinite(w), axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(w, axis=1)
        out[:, t] = np.where(cnt >= window, med, np.nan)
    return out


def early_components(price):
    price = np.atleast_2d(np.asarray(price, dtype=float))
    sma50_up = trv2.sma_up(price, SMA_FAST)
    vol_p = trv2.vol_n(price)
    med_p = _rolling_median_vol(vol_p, MED_WINDOW)
    close_lag20 = np.full_like(price, np.nan)
    close_lag20[:, LAG_FAST:] = price[:, :-LAG_FAST]
    return sma50_up, vol_p, med_p, price > close_lag20


def early_active_path(price, e2_on, arm="R2", comps=None):
    """[P, T] production early-re-entry state (fast_reentry_v1 arm R2), decided at each close."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = early_components(price) if comps is None else comps
    early = np.zeros(P, dtype=bool)
    hist = np.zeros((P, T), dtype=bool)
    for t in range(T):
        e2t = e2_on[:, t]
        early = np.where(e2t, False, early)                       # E2 overrides the early position
        early = np.where(early & (~sma50_up[:, t]) & (~e2t), False, early)
        entry = sma50_up[:, t] & np.isfinite(med_p[:, t]) & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]
        early = np.where((~early) & (~e2t) & np.isfinite(med_p[:, t]) & entry, True, early)
        hist[:, t] = early
    return hist


def arm_state(arm, price, e2_on, vol_instr, comps=None, early_inst=None, c0_weight=0.5):
    """({instrument: [P, T] targets}, early_active [P, T]) for one pre-registered arm.

    w_t = vt(vol_instr) (production: vol20 of QLD*); E2 leg = min(0.98, w_t) in QLD*;
    early leg = c0_weight * w_t (C0) or min(0.98, w_t) (A1/A2/A3) in the arm's early instrument."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    early = early_active_path(price, e2_on, "R2", comps)
    w = trv2.vt_of(np.atleast_2d(vol_instr))
    inst = early_inst or EARLY_INSTRUMENT[arm]
    e2_w = np.where(e2_on, np.minimum(CAP, w), 0.0)
    early_w = np.where(early & (~e2_on), np.minimum(CAP, w) if inst != "QLD" else c0_weight * w, 0.0)
    targets = {"QLD": e2_w + (early_w if inst == "QLD" else 0.0)}
    if inst != "QLD":
        targets[inst] = early_w
    total = sum(targets.values())
    assert float(total.max()) <= CAP + 1e-12, (arm, float(total.max()))
    return targets, early


# ----------------------------------------------------------------------------- pre-tax engine
def simulate_multi(opens, closes, targets, cost, start, band=C0_BAND):
    """Multi-instrument single-portfolio simulator, production mechanics, band on the total weight.

    opens/closes/targets: {instrument: [P, T]}; the target vector is targets[*][i-1] (decided on the
    completed close i-1). Per session i: each held instrument grows overnight at its own open; the
    portfolio trades to the full target vector when the position flips (0 <-> > 0), when an instrument
    switch is required (an instrument with a position has target 0 while the total target is > 0), or
    when |total target - total held| > band; cost = cost x sum_instrument |target - held|; then intraday
    growth at each instrument's own close. Idle cash earns zero.
    With a single instrument (and no switch) this is trend_robustness_v2.simulate_multi bit for bit.
    Returns nav [P, T-start+1], turnover [P], per-instrument turnover, trades [P], switches [P],
    event codes [P, T-start] (0 none, 1 entry, 2 exit, 3 resize up, 4 resize down, 5 switch),
    traded |dw| [P, T-start]."""
    syms = list(targets)
    P, T = closes[syms[0]].shape
    held = {s: np.zeros(P) for s in syms}
    nav = np.ones(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    turn_by = {s: np.zeros(P) for s in syms}
    ntr = np.zeros(P, dtype=np.int64)
    nsw = np.zeros(P, dtype=np.int64)
    codes = np.zeros((P, T - start), dtype=np.int8)
    traded = np.zeros((P, T - start))
    for i in range(start, T):
        old_sum = np.zeros(P)
        new_sum = np.zeros(P)
        for s in syms:
            g = opens[s][:, i] / closes[s][:, i - 1]
            g = np.where(np.isnan(g), 1.0, g)
            old_sum = old_sum + held[s]
            held[s] = held[s] * g
            new_sum = new_sum + held[s]
        tot = 1.0 - old_sum + new_sum
        nav = nav * tot
        for s in syms:
            held[s] = held[s] / tot

        tgt = {s: np.nan_to_num(targets[s][:, i - 1], nan=0.0) for s in syms}
        ht = sum(held.values())
        tt = sum(tgt.values())
        d = np.abs(tt - ht)
        flip = (ht > 0) != (tt > 0)
        switch = np.zeros(P, dtype=bool)
        for s in syms:
            switch = switch | ((held[s] > 1e-12) & (tgt[s] <= 0.0))
        switch = switch & (tt > 0)
        trade = flip | switch | (d > band)

        dsum = np.zeros(P)
        d_sym = {}
        for s in syms:
            d_sym[s] = np.abs(tgt[s] - held[s])
            dsum = dsum + d_sym[s]
        dsum = np.where(trade, dsum, 0.0)
        nav = nav * (1.0 - cost * dsum)
        turn = turn + dsum
        for s in syms:
            turn_by[s] = turn_by[s] + np.where(trade, d_sym[s], 0.0)
        ntr = ntr + trade.astype(np.int64)
        nsw = nsw + (trade & switch).astype(np.int64)
        k = i - start
        codes[:, k] = np.where(~trade, 0, np.where(flip & (tt > 0), 1,
                            np.where(flip, 2, np.where(switch, 5, np.where(tt > ht, 3, 4)))))
        traded[:, k] = dsum
        for s in syms:
            held[s] = np.where(trade, tgt[s], held[s])

        prev_w = np.zeros(P)
        agg = np.zeros(P)
        for s in syms:
            prev_w = prev_w + held[s]
        for s in syms:
            held[s] = held[s] * (closes[s][:, i] / opens[s][:, i])
            agg = agg + held[s]
        tot = 1.0 - prev_w + agg
        nav = nav * tot
        for s in syms:
            held[s] = held[s] / tot
        out[:, k + 1] = nav
    return out, turn, turn_by, ntr, nsw, codes, traded


# ----------------------------------------------------------------------------- metrics
def seg(dates, nav, a, b):
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
            "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1), "max_dd": float(trv2.max_dd(s)[0])}


def event_stats(dates, codes, traded, a=None, b=None):
    sess = dates[1:]
    lo = 0 if a is None else next(k for k, d in enumerate(sess) if d > a)
    hi = len(sess) if b is None else max(k for k, d in enumerate(sess) if d <= b) + 1
    c, tr = codes[lo:hi], traded[lo:hi]
    yrs = trv2.calendar_years(dates[lo], sess[hi - 1])
    n = {name: int(np.sum(c == v)) for name, v in
         (("entries", 1), ("exits", 2), ("resize_up", 3), ("resize_down", 4), ("switches", 5))}
    return {
        "years": yrs,
        "counts": n,
        "round_trips_per_year": n["exits"] / yrs,
        "trades_per_year": sum(n.values()) / yrs,
        "resizes_per_year": (n["resize_up"] + n["resize_down"]) / yrs,
        "sells_per_year": (n["exits"] + n["resize_down"] + n["switches"]) / yrs,
        "switches_per_year": n["switches"] / yrs,
        "turnover_per_year": float(tr.sum() / yrs),
        "turnover_from_entries_exits_per_year": float(tr[(c == 1) | (c == 2)].sum() / yrs),
        "turnover_from_resizes_per_year": float(tr[(c == 3) | (c == 4)].sum() / yrs),
        "turnover_from_switches_per_year": float(tr[c == 5].sum() / yrs),
    }


def arm_stats(dates, nav, turn, turn_by, ntr, nsw, codes, traded, targets, start, extras, early_active=None):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["turnover_annual_by_instrument"] = {s: float(v / years) for s, v in sorted(turn_by.items())}
    st["trades"] = int(ntr)
    st["instrument_switches"] = int(nsw)
    st["subperiods"] = {name: seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    if extras:
        st["stage_b_window"] = seg(dates, nav, *STAGE_B_WINDOW)
        st["events_full"] = event_stats(dates, codes, traded)
        st["events_2017_on"] = event_stats(dates, codes, traded, "2016-12-31")
        tgt = sum(targets.values())[start - 1:-1]
        st["time_in_market"] = float((tgt > 0).mean())
        if early_active is not None:
            ea = np.atleast_2d(early_active)
            sl = slice(start - 1, start - 1 + len(tgt))
            st["early_time_in_market"] = float(ea[0, sl].mean())
    return st


# ----------------------------------------------------------------------------- data (main + OOS)
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


def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    inst = {tag: synthetic_L(arr[base][0], arr[base][1], arr[base][2], y, L, exp, spread)
            for tag, (L, exp, base) in INSTRUMENTS.items()}
    price = q[1]
    return days, arr, y, inst, e2_up(price)[0], trv2.vol_n(inst["QLD"][1])[0], price


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


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_market(key):
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


def etf_path(key):
    """range_vol_v[12]_<key>.csv; returns adjusted OHLC, aligned DTB3 and the quality-rule verdict."""
    for pre in ("range_vol_v1_", "range_vol_v2_"):
        p = ROOT / "data/historical" / f"{pre}{key.lower()}.csv"
        if p.exists():
            break
    else:
        raise FileNotFoundError(key)
    rows = []
    with p.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                         float(r["close"]), float(r["adjclose"])))
    dates = [r[0] for r in rows]
    o, h, lo, c = (np.empty(len(rows)) for _ in range(4))
    for k, (d, op, hi, l, cl, adj) in enumerate(rows):
        f = adj / cl
        o[k], h[k], lo[k], c[k] = op * f, hi * f, l * f, adj
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    bad = n = 0
    for hi, l, op, cl in zip(h, lo, o, c):
        n += 1
        if hi == l or op is None or op <= 0 or op < l * (1 - 1e-9) or op > hi * (1 + 1e-9):
            bad += 1
    return dates, o, h, lo, c, y, {"bad_share": bad / max(1, n), "pass": (bad / max(1, n)) <= QUALITY_MAX_BAD}


def real_path():
    """Real QQQ/QLD adjusted closes on their common sessions (Yahoo chart API download)."""
    def rd(p):
        out = {}
        with p.open() as fh:
            for r in csv.DictReader(fh):
                out[r["date"]] = float(r["adjclose"])
        return out
    q, d = rd(QQQ_REAL), rd(QLD_REAL)
    days = sorted(set(q) & set(d))
    return days, np.array([q[x] for x in days]), np.array([d[x] for x in days])


# ----------------------------------------------------------------------------- main window and bootstrap
def run_main(arms, m_list=(1, 2), extras_m2=False):
    res = {}
    for md, spread in FIN.items():
        days, arr, y, inst, e2, v20, price = main_inputs(spread)
        dates = days[START - 1:]
        comps = early_components(price)
        legs = {a: arm_state(a, price, e2, v20, comps) for a in arms}
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                tgt, early = legs[arm]
                nav, turn, turn_by, ntr, nsw, codes, traded = simulate_multi(
                    {s: inst[s][0][None, :] for s in tgt},
                    {s: inst[s][1][None, :] for s in tgt}, {s: tgt[s] for s in tgt},
                    COST_PER_SIDE * m, START)
                hm[arm] = arm_stats(dates, nav[0], turn[0], {s: v[0] for s, v in turn_by.items()}, ntr[0],
                                    nsw[0], codes[0], traded[0], tgt, START, extras_m2 and m == 2,
                                    early_active=early & ~np.atleast_2d(e2))
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def _e2_only_targets(v20, e2):
    return {"QLD": np.where(e2, np.minimum(CAP, trv2.vt_of(v20)), 0.0)[None, :]}


def harness():
    """Control reproduction only: E2 (no early leg) against REF_DEV, production R2 (= arm C0, the
    half-weight QLD early leg) against REF_R2, the multi-instrument simulator against
    trv2.simulate_multi on a single leg, and the after-tax anchors at $100k. Only the control
    (arm C0) and the E2-only reference are simulated: no candidate arm is run here."""
    h = run_main(("C0",), m_list=(1, 2))
    checks, bad = {}, 0

    def add(key, ref, val, scale=100.0):
        nonlocal bad
        d = (val - ref) * scale
        checks[key] = {"reference": ref, "reproduced": val, "diff": d}
        if abs(d) > (TOL * 100.0 if scale == 100.0 else TOL):
            bad += 1

    for md, spread in FIN.items():
        days, arr, y, inst, e2, v20, price = main_inputs(spread)
        tgt = _e2_only_targets(v20, e2)
        for m in (1, 2):
            nav, turn, _, _, _, _, _ = simulate_multi(
                {"QLD": inst["QLD"][0][None, :]}, {"QLD": inst["QLD"][1][None, :]},
                tgt, COST_PER_SIDE * m, START)
            nav = nav[0]
            yrs = trv2.calendar_years(days[START - 1], days[-1])
            cagr, dd, turnr = REF_DEV[(md, m)]
            add(f"E2_{md}_m{m}_cagr", cagr, float(trv2.cagr(nav[0], nav[-1], yrs)))
            add(f"E2_{md}_m{m}_max_dd", dd, float(trv2.max_dd(nav)[0]))
            add(f"E2_{md}_m{m}_turnover_annual", turnr, float(turn[0] / yrs), scale=1.0)
        # the production R2 rule is arm C0 of this screen
        for m in (1, 2):
            x = h[md][f"m{m}"]["C0"]
            cagr, dd, turnr = REF_R2[(md, m)]
            add(f"R2_{md}_m{m}_cagr", cagr, x["cagr"])
            add(f"R2_{md}_m{m}_max_dd", dd, x["max_dd"])
            add(f"R2_{md}_m{m}_turnover_annual", turnr, x["turnover_annual"], scale=1.0)

    days, arr, y, inst, e2, v20, price = main_inputs(0.0)
    comps = early_components(price)
    tgt, _ = arm_state("C0", price, e2, v20, comps)
    nav_a = simulate_multi({s: inst[s][0][None, :] for s in tgt},
                           {s: inst[s][1][None, :] for s in tgt}, tgt, COST_PER_SIDE * 2, START)[0][0]
    nav_b = trv2.simulate_multi(inst["QLD"][0], inst["QLD"][1], tgt["QLD"][None, :, :],
                                COST_PER_SIDE * 2, START)[0][0]
    sim_equal = bool(np.array_equal(nav_a, nav_b))

    # after-tax anchors (real data, no candidate arm): E2 = no early leg, R2 = arm C0
    legs = {"C0": ({"QLD": (inst["QLD"][0], inst["QLD"][1])}, tgt)}
    legs["E2"] = ({"QLD": (inst["QLD"][0], inst["QLD"][1])}, {k: v[0] for k, v in _e2_only_targets(v20, e2).items()})
    tax = after_tax_stage(arms=("E2", "C0"), days=days, legs=legs)
    anchors, tax_bad = {}, 0
    for arm, key, wins in (("C0", "E2", TAX_ANCHORS["C0"]), ("R2", "C0", TAX_ANCHORS["R2"])):
        for wn, (pt, pg, dd) in wins.items():
            got = tax["windows"][wn][key]
            for lab, ref, val in (("pre_tax", pt, got["pre_tax"]["cagr"]),
                                  ("progressive", pg, got["progressive"]["cagr"]),
                                  ("progressive_max_dd", dd, got["progressive"]["max_dd"])):
                d = (val - ref) * 100
                anchors[f"{arm}_{wn}_{lab}"] = {"reference": ref, "reproduced": val, "diff_pp": d}
                if abs(d) > 0.01:
                    tax_bad += 1
    ok = (bad == 0) and sim_equal and (tax_bad == 0)
    return {"main_window": h["window"], "reproduction_checks": checks, "tolerance_pp": TOL * 100,
            "n_checks": len(checks), "n_outside_tolerance": bad,
            "multi_instrument_equals_simulate_multi_single_leg": sim_equal,
            "after_tax_anchors": anchors, "n_tax_anchors_outside_0.01pp": tax_bad,
            "pass": bool(ok)}


def boot_main(arms):
    """Stationary block bootstrap (mean block 20); all arms on the same paths, m = 2, both models."""
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
        bars = {"QQQ": (qo, qc, np.zeros_like(qc)), "SPY": (None, None, None)}
        # SPY total return resampled on the same path positions
        sp = arr["SPY"]
        sp_on = sp[0][idx] / sp[1][idx - 1]
        sp_tr = (sp[0][idx] + sp[2][idx]) / sp[1][idx - 1]
        sp_on = np.where(np.isnan(sp_on), 1.0, sp_on)
        sp_tr = np.where(np.isnan(sp_tr), 1.0, sp_tr)
        sp_c = np.concatenate([one, np.cumprod(sp_tr, axis=1)], axis=1)
        sp_o = np.concatenate([one, sp_c[:, :-1] * sp_on], axis=1)
        bars["SPY"] = (sp_o, sp_c, np.zeros_like(sp_c))
        for md in FIN:
            inst = {}
            for tag, (Lv, exp, base) in INSTRUMENTS.items():
                bo_, bc_, bd_ = bars[base]
                inst[tag] = synthetic_L(bo_, bc_, bd_, yyp + FIN[md], Lv, exp)
            vol_instr = trv2.vol_n(inst["QLD"][1])
            for arm in arms:
                tgt, _ = arm_state(arm, pc, e2, vol_instr, comps)
                nav, turn, _, _, _, _, _ = simulate_multi({s: inst[s][0] for s in tgt},
                                                          {s: inst[s][1] for s in tgt},
                                                          tgt, COST_PER_SIDE * 2, start)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()}, {key: np.concatenate(v) for key, v in tv.items()})


def boot_summary(cg, tv, arms, models):
    out = {}
    for md in models:
        out[md] = {}
        for a in arms:
            c = cg[(md, a)]
            e = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                 "turnover_median": float(np.median(tv[(md, a)]))}
            d = c - cg[(md, "C0")]
            e["diff_vs_C0_median"] = float(np.median(d))
            e["diff_vs_C0_mean"] = float(np.mean(d))
            e["share_ge_C0"] = float(np.mean(d >= 0))
            out[md][a] = e
    return out


# ----------------------------------------------------------------------------- OOS windows
def oos_window(days, o, c, y, start, arms):
    inst = {"QLD": synthetic_L(o, c, np.zeros_like(c), y + EXTRA_FIN, *INSTRUMENTS["QLD"][:2]),
            "QQQ": synthetic_L(o, c, np.zeros_like(c), y + EXTRA_FIN, *INSTRUMENTS["QQQ"][:2]),
            "SPY": synthetic_L(o, c, np.zeros_like(c), y + EXTRA_FIN, *INSTRUMENTS["SPY"][:2]),
            "SSO": synthetic_L(o, c, np.zeros_like(c), y + EXTRA_FIN, *INSTRUMENTS["SSO"][:2])}
    e2 = e2_up(c)
    vol_instr = trv2.vol_n(inst["QLD"][1])
    comps = early_components(c)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for arm in arms:
        tgt, _ = arm_state(arm, c, e2, vol_instr, comps)
        nav, turn, _, _, _, codes, traded = simulate_multi(
            {s: inst[s][0][None, :] for s in tgt},
            {s: inst[s][1][None, :] for s in tgt}, tgt, COST_PER_SIDE * 2, start)
        nav = nav[0]
        ev = event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)), "max_dd": float(trv2.max_dd(nav)[0]),
                    "turnover_annual": float(turn[0] / years), "sells_per_year": ev["sells_per_year"],
                    "switches_per_year": ev["switches_per_year"]}
    return res


def boot_oos_market(key, rng, arms):
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
        inst = {}
        for tag, (Lv, exp, base) in INSTRUMENTS.items():
            inst[tag] = synthetic_L(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, Lv, exp)
        vol_instr = trv2.vol_n(inst["QLD"][1])
        comps = early_components(cc)
        for arm in arms:
            tgt, _ = arm_state(arm, cc, e2, vol_instr, comps)
            nav = simulate_multi({s: inst[s][0] for s in tgt}, {s: inst[s][1] for s in tgt},
                                 tgt, COST_PER_SIDE * 2, START)[0]
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
        r[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
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


# ----------------------------------------------------------------------------- 21-ETF drawdown generalisation
def etf_stage(arms=("C0", "A1")):
    out = {"quality_rule": f"share of bars with high == low or an invalid open <= {QUALITY_MAX_BAD:.0%} "
                           f"(range_vol_v2)", "financing": "base", "m": 2, "instruments": {}}
    passing = []
    for key in ETF_KEYS:
        dates, o, h, lo, c, y, q = etf_path(key)
        entry = {"quality": q, "first_session": dates[0], "last_session": dates[-1], "sessions": len(dates)}
        if not q["pass"] or len(dates) <= START + 30:
            entry["excluded"] = True
            out["instruments"][key] = entry
            continue
        entry["excluded"] = False
        passing.append(key)
        two = synthetic_L(o, c, np.zeros_like(c), y, *INSTRUMENTS["QLD"][:2])
        one = (o, c)  # the ETF's own adjusted total return
        e2 = e2_up(c)
        vol_instr = trv2.vol_n(two[1])
        comps = early_components(c)
        for arm in arms:
            tgt, _ = arm_state(arm, c, e2, vol_instr, comps, early_inst="QQQ" if arm == "A1" else "QLD")
            inst = {"QLD": two, "QQQ": one}
            nav, turn, _, _, nsw, _, _ = simulate_multi(
                {s: inst[s][0][None, :] for s in tgt},
                {s: inst[s][1][None, :] for s in tgt}, tgt, COST_PER_SIDE * 2, START)
            nav = nav[0]
            years = trv2.calendar_years(dates[START - 1], dates[-1])
            entry[arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)),
                          "max_dd": float(trv2.max_dd(nav)[0]), "turnover_annual": float(turn[0] / years),
                          "switches": int(nsw[0]), "window": [dates[START - 1], dates[-1]]}
        out["instruments"][key] = entry
    dds = {k: out["instruments"][k]["A1"]["max_dd"] - out["instruments"][k]["C0"]["max_dd"] for k in passing}
    cgs = {k: out["instruments"][k]["A1"]["cagr"] - out["instruments"][k]["C0"]["cagr"] for k in passing}
    n = len(passing)
    share_dd = float(np.mean([v <= 0.03 + 1e-12 for v in dds.values()])) if n else float("nan")
    out["quality_passing"] = passing
    out["n_passing"] = n
    out["delta_max_dd"] = dds
    out["delta_cagr"] = cgs
    out["median_delta_cagr_pp"] = float(np.median(list(cgs.values())) * 100) if n else float("nan")
    out["share_delta_cagr_gt_0"] = float(np.mean([v > 0 for v in cgs.values()])) if n else float("nan")
    out["share_delta_max_dd_within_3pp"] = share_dd
    out["median_delta_max_dd_pp"] = float(np.median(list(dds.values())) * 100) if n else float("nan")
    out["E1"] = bool(n >= 21 and share_dd >= 0.80)
    return out


# ----------------------------------------------------------------------------- real-fund evaluation surface
def real_stage():
    days, qq, qd = real_path()
    i0 = max(bisect.bisect_left(days, REAL_WINDOW[0]), START - 1)
    if i0 < START - 1:
        raise RuntimeError("real window too short")
    price, e2, comps = qq, e2_up(qq[None, :])[0], early_components(qq[None, :])
    v20 = trv2.vol_n(qd[None, :])[0]  # production sizing input: the levered instrument's vol
    inst = {"QLD": (qd[None, :], qd[None, :]), "QQQ": (qq[None, :], qq[None, :])}
    out = {"window": [days[i0], days[-1]], "financing": "none (real funds)", "m": 2,
           "note": "real Yahoo adjusted closes; real expenses and real financing are inside the "
                   "prices; not a candidate arm", "arms": {}}
    tgtC, _ = arm_state("C0", price, e2, v20, comps)
    tgtA, _ = arm_state("A1", price, e2, v20, comps)
    for arm, tgt in (("C0", tgtC), ("A1", tgtA)):
        nav, turn, _, ntr, nsw, codes, traded = simulate_multi(
            {s: inst[s][0] for s in tgt}, {s: inst[s][1] for s in tgt}, tgt, COST_PER_SIDE * 2, i0)
        nav = nav[0]
        years = trv2.calendar_years(days[i0 - 1], days[-1])
        out["arms"][arm] = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)),
                            "max_dd": float(trv2.max_dd(nav)[0]), "turnover_annual": float(turn[0] / years),
                            "switches": int(nsw[0]), "sessions": len(days) - i0,
                            "events": event_stats(days[i0 - 1:], codes[0], traded[0])}
    out["delta_cagr_pp"] = float((out["arms"]["A1"]["cagr"] - out["arms"]["C0"]["cagr"]) * 100)
    out["delta_max_dd_pp"] = float((out["arms"]["A1"]["max_dd"] - out["arms"]["C0"]["max_dd"]) * 100)
    return out


# ----------------------------------------------------------------------------- analytic bounds (report only)
def analytic_bounds():
    days, arr, y, inst, e2, v20, price = main_inputs(0.0)
    comps = early_components(price)
    early = early_active_path(price, e2, "R2", comps)[0]
    sl = slice(START - 1, len(days))
    ea = early[sl]
    w = trv2.vt_of(v20)[sl]
    yy = y[sl]
    n_sess = int(ea.sum())
    yrs = trv2.calendar_years(days[START - 1], days[-1])
    rate = (yy + 0.0075 + EXP_Q) / 2 - QQQ_EXP  # per-year financing+expense saving of the swap, per unit weight
    log_total = float(np.sum(ea * w * rate) / 252)
    cagr_pp = ((1 + log_total) ** (1 / yrs) - 1) * 100
    return {
        "sessions_measured": int(sl.stop - sl.start),
        "years": float(yrs),
        "early_active_sessions": n_sess,
        "early_active_session_share": float(ea.mean()),
        "early_mean_w": float(w[ea].mean()) if n_sess else float("nan"),
        "early_share_w_eq_1": float((w[ea] >= 1 - 1e-12).mean()) if n_sess else float("nan"),
        "early_sessions_w_eq_1": int(np.sum(ea & (w >= 1 - 1e-12))),
        "early_mean_dtb3": float(yy[ea].mean()) if n_sess else float("nan"),
        "analytic_pre_tax_cagr_gain_pp": float(cagr_pp),
        "cap_bound": {
            "note": "survey A2 residual QLD: A1's beta is min(0.98, w) vs the exact w; the shortfall is "
                    "0.02 of beta on the early sessions with w = 1 (the only ones the 0.98 cap binds on)",
            "beta_shortfall": 0.02,
            "sessions_w_eq_1": int(np.sum(ea & (w >= 1 - 1e-12))),
            "analytic_extra_beta_cagr_pp_upper_bound": float(0.02 * np.sum(ea & (w >= 1 - 1e-12)) /
                                                             (sl.stop - sl.start)),
        },
        "reset_convexity_note": "0.5*w QLD compounds 2x daily on half the notional, so it earns more than "
                                "w QQQ when the index trends (and less when it chops); this term is "
                                "measured by the simulated delta, not by this arithmetic.",
    }


# ----------------------------------------------------------------------------- after tax
def _share_books(books):
    """One Turkish tax base: every book's realised gains feed the primary book's annual dicts."""
    primary = books[0]
    for b in books:
        for k in ("gain_by_reason", "pos_gain_by_reason", "neg_gain_by_reason",
                  "year_gain", "year_div", "year_wht"):
            setattr(b, k, getattr(primary, k))


def _sell_pro_rata(need, idx, books, cashbox, cost, reason):
    tot = sum(b.units * p for b, p, _ in books)
    if tot <= 0:
        return 0
    n = 0
    for b, p, ix in books:
        v = b.units * p
        if v <= 0:
            continue
        gross = need * (v / tot)
        b.sell(ix, p, gross / p, gross * cost, reason)
        n += 1
    return n


def tax_run_multi(capital, i0, i1, days, mkt, prices, targets, mode):
    """Multi-book after-tax run (QLD* + the arm's early instrument), mode in {pre_tax, progressive, flat}.

    The session loop, trade rules, fee accounting, tariff assessment, instalment funding and
    liquidation mirror research/vt_level_tax_v1/screen.py's `run_strategy` /
    `run_strategy_flat` (byte copy in tax_engine.py) and research/r2_tbill_combo_v1/screen.py's
    `tax_run`, generalised from two books to a dict of books. Instruments share one annual net
    realised gain (Turkish within-year netting). `days[i0-1]` must exist."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    syms = list(targets)
    O = {s: np.asarray(prices[s][0], dtype=float).flatten() for s in syms}
    C = {s: np.asarray(prices[s][1], dtype=float).flatten() for s in syms}
    W = {s: np.asarray(targets[s], dtype=float).flatten() for s in syms}
    cost = COST_PER_SIDE * 2
    band = C0_BAND
    cashbox = {"v": float(capital)}
    if mode == "flat":
        books = [te.FlatAccount(mkt, 0.0, cashbox) for _ in syms]
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        books = [te.BaseAccount(mkt, rg, 0.0, cashbox) for _ in syms]
        pays = te.instalments(days, i0, i1)
    book = dict(zip(syms, books))
    _share_books(books)
    cashbox["v"] = float(capital)

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = switches = 0
    sells_by = {s: 0 for s in syms}
    for i in range(i0, i1 + 1):
        if i > i0 and int(days[i][:4]) != int(days[i - 1][:4]):
            prevy = int(days[i][:4]) - 1
            books[0].tax_due[prevy] = books[0].assess(prevy)
            if mode == "flat":
                due = books[0].tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                    if cashbox["v"] < usd:
                        lst = [(book[s], float(C[s][i - 1]), i - 1) for s in syms]
                        tax_sales += _sell_pro_rata(usd - cashbox["v"], i - 1, lst, cashbox, cost, "tax")
                    cashbox["v"] -= usd
                    books[0].tax_paid_try[prevy] = books[0].tax_paid_try.get(prevy, 0.0) + due
                    books[0].tax_paid_usd += usd

        if pays is not None:
            for ty, part in pays.get(i, ()):
                if ty not in books[0].tax_due:
                    books[0].tax_due[ty] = 0.0
                due = books[0].tax_due.get(ty, 0.0)
                amt = due / 2 if part == 0 else due - books[0].tax_paid_try.get(ty, 0.0)
                if amt <= 1e-9:
                    continue
                usd = amt / mkt.fx[i]
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"]) / (1 - cost)
                    lst = [(book[s], float(O[s][i]), i) for s in syms]
                    tax_sales += _sell_pro_rata(need, i, lst, cashbox, cost, "tax")
                cashbox["v"] -= usd
                books[0].tax_paid_try[ty] += amt
                books[0].tax_paid_usd += usd

        po = {s: float(O[s][i]) for s in syms}
        pc = {s: float(C[s][i]) for s in syms}
        held = {s: book[s].units * po[s] for s in syms}
        nav_open = cashbox["v"] + sum(held.values())
        if nav_open <= 0:
            nav_open = cashbox["v"]
        wheld = {s: (held[s] / nav_open if nav_open > 0 else 0.0) for s in syms}
        tgt = {s: (float(W[s][i - 1]) if i > 0 else float(W[s][0])) for s in syms}
        tgt = {s: (0.0 if tgt[s] != tgt[s] else tgt[s]) for s in syms}
        tt, ht = sum(tgt.values()), sum(wheld.values())
        flip = (ht > 1e-12) != (tt > 1e-12)
        switch = bool(tt > 1e-12 and any(wheld[s] > 1e-12 and tgt[s] <= 0.0 for s in syms))
        trade = bool(flip or switch or abs(tt - ht) > band)

        if trade:
            d_sym = {s: abs(tgt[s] - wheld[s]) for s in syms}
            fee = nav_open * cost * sum(d_sym.values())
            nav_after = nav_open - fee
            for s in syms:
                tgt_units = tgt[s] * nav_after / po[s] if po[s] > 0 else 0.0
                du = tgt_units - book[s].units
                leg_fee = nav_open * cost * d_sym[s]
                if du > 1e-12:
                    book[s].buy(i, po[s], du * po[s], leg_fee)
                elif du < -1e-12:
                    book[s].sell(i, po[s], -du, leg_fee, "resize")
                    sells_by[s] += 1
                    q_sells += 1
            trades += 1
            switches += int(switch)

        nav[i - i0 + 1] = cashbox["v"] + sum(book[s].units * pc[s] for s in syms)

    end_y = int(days[i1][:4])
    for s in syms:
        if book[s].units > 0:
            book[s].sell(i1, float(C[s][i1]), book[s].units, book[s].units * float(C[s][i1]) * cost, "liquidation")
    liq_tax = books[0].assess(end_y) + books[0].outstanding()
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
        "cagr_usd_liquidated": float(cg(capital, liq)), "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "tax_total_try": float(sum(books[0].tax_paid_try.values()) + liq_tax),
        "tax_total_usd_at_payment_fx": float(books[0].tax_paid_usd + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "strategy_trades": int(trades), "instrument_switches": int(switches),
        "sells_by_instrument": sells_by, "sells": int(q_sells + tax_sales), "tax_funding_sales": int(tax_sales),
        "fees_usd": float(sum(b.fees for b in books)),
        "indexation_relief_try": float(sum(b.index_relief_try for b in books)),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(books[0].gain_by_reason.items())},
        "_nav": nav,
    }


def tax_legs(days, arr, y, comps, spread=0.0):
    price = arr["QQQ"][1]
    inst = {tag: synthetic_L(arr[base][0], arr[base][1], arr[base][2], y, L, exp, spread)
            for tag, (L, exp, base) in INSTRUMENTS.items()}
    e2 = e2_up(price)[0]
    v20 = trv2.vol_n(inst["QLD"][1])[0]
    legs = {}
    for arm in ARMS:
        tgt, _ = arm_state(arm, price, e2, v20, comps)
        legs[arm] = ({s: (inst[s][0], inst[s][1]) for s in tgt}, tgt)
    return legs


def after_tax_stage(arms=ARMS, days=None, legs=None, mkt=None):
    days_all, arr, y = trv2.load()
    days = days_all if days is None else days
    if legs is None:
        legs = tax_legs(days_all, arr, y, early_components(arr["QQQ"][1]))
    mkt = te.load_market(FX_CSV, UFE_CSV, days) if mkt is None else mkt
    out = {"capital_usd": TAX_CAPITAL, "cost_multiplier_m": 2, "financing": "base",
           "engine": "tax_engine.py (vt_level_tax_v1 corrected, multi-book)",
           "windows": {wn: {} for wn in TAX_WINDOWS}}
    for arm in arms:
        for wn, (a, b) in TAX_WINDOWS.items():
            prices, tgt = legs[arm]
            r = {}
            for mode in ("pre_tax", "progressive"):
                res = tax_run_multi(TAX_CAPITAL, a, b, days, mkt, prices, tgt, mode)
                r[mode] = {"cagr": res["cagr_usd_liquidated"], "max_dd": res["max_dd_nav_usd"],
                           "terminal_usd": res["terminal_usd_liquidated"],
                           "tax_usd": res["tax_total_usd_at_payment_fx"],
                           "trades_per_year": res["strategy_trades"] / res["years"],
                           "sells_per_year": res["sells"] / res["years"],
                           "switches": res["instrument_switches"],
                           "tax_funding_sales": res["tax_funding_sales"],
                           "indexation_relief_try": res["indexation_relief_try"]}
            out["windows"][wn][arm] = r
    return out


def tax_boot_calendar():
    _, arr, y = trv2.load()
    all_days = trv2.load()[0]
    sub = [d for d in all_days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    if len(sub) < TAX_BOOT_L:
        raise RuntimeError(f"fixed calendar has only {len(sub)} days, need {TAX_BOOT_L}")
    return all_days, arr, y, list(sub[:TAX_BOOT_L])


_FX_CACHE = {}


def fx_at(dates):
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


def after_tax_bootstrap(arms, n_paths=TAX_BOOT_PATHS, capital=TAX_CAPITAL):
    """Clean after-tax bootstrap (tax_asym_v2 / vt_level_tax_v1): B1 = progressive, B2 = 20 % flat,
    block-20 stationary bootstrap, 10-year paths on the fixed 2015-09 -> 2026-09 calendar."""
    all_days, arr, y, fixed_dates = tax_boot_calendar()
    fixed_fx = fx_at(fixed_dates)
    base_mkt = te.load_market(FX_CSV, UFE_CSV, all_days)
    ufe = base_mkt.ufe
    q = arr["QQQ"]
    sp = arr["SPY"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    res = {mode: {a: {"cagr": [], "maxdd": [], "sells": []} for a in arms} for mode in ("progressive", "flat")}
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
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod((q[0][idx] / prev) * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = early_components(pc)
        sp_on = sp[0][idx] / sp[1][idx - 1]
        sp_tr = (sp[0][idx] + sp[2][idx]) / sp[1][idx - 1]
        sp_on = np.where(np.isnan(sp_on), 1.0, sp_on)
        sp_tr = np.where(np.isnan(sp_tr), 1.0, sp_tr)
        sp_c = np.concatenate([one, np.cumprod(sp_tr, axis=1)], axis=1)
        sp_o = np.concatenate([one, sp_c[:, :-1] * sp_on], axis=1)
        bars = {"QQQ": (qo, qc, np.zeros_like(qc)), "SPY": (sp_o, sp_c, np.zeros_like(sp_c))}
        inst = {}
        for tag, (Lv, exp, base) in INSTRUMENTS.items():
            bo_, bc_, bd_ = bars[base]
            inst[tag] = synthetic_L(bo_, bc_, bd_, yyp + 0.0, Lv, exp)
        vol_instr = trv2.vol_n(inst["QLD"][1])
        mkt = te.Market(fixed_dates, fixed_fx, ufe)
        for arm in arms:
            tgt, _ = arm_state(arm, pc, e2, vol_instr, comps)
            prices = {s: (inst[s][0].flatten(), inst[s][1].flatten()) for s in tgt}
            tg = {s: tgt[s].flatten() for s in tgt}
            for mode in ("progressive", "flat"):
                r = tax_run_multi(capital, i0, i1, fixed_dates, mkt, prices, tg, mode)
                if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                    raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode}")
                res[mode][arm]["cagr"].append(r["cagr_usd_liquidated"])
                res[mode][arm]["maxdd"].append(r["max_dd_nav_usd"])
                res[mode][arm]["sells"].append(r["sells"] / r["years"])
    out = {}
    for mode in ("progressive", "flat"):
        e = {"tax_kind": mode, "n_paths": n_paths, "calendar_start": fixed_dates[0],
             "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK, "capital_usd": capital}
        for a in arms:
            e[a] = {"cagr_median": float(np.median(res[mode][a]["cagr"])),
                    "maxdd_median": float(np.median(res[mode][a]["maxdd"])),
                    "sells_per_year_median": float(np.median(res[mode][a]["sells"]))}
            d = np.array(res[mode][a]["cagr"]) - np.array(res[mode]["C0"]["cagr"])
            e[f"{a}_vs_C0"] = {"median_delta_cagr_pp": float(np.median(d) * 100),
                               "mean_delta_cagr_pp": float(np.mean(d) * 100),
                               "share_gt_0": float(np.mean(d > 0)),
                               "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                               "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100),
                               "ctrl_cagr_median": float(np.median(res[mode]["C0"]["cagr"]))}
        out[mode] = e
    return out


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    print(f"harness pre-tax: {hc['n_checks']} checks, {hc['n_outside_tolerance']} outside tolerance; "
          f"simulator identity {hc['multi_instrument_equals_simulate_multi_single_leg']}", flush=True)
    print(f"harness after-tax: {len(hc['after_tax_anchors'])} anchors, "
          f"{hc['n_tax_anchors_outside_0.01pp']} outside 0.01 pp; "
          f"max |diff| {max(abs(v['diff_pp']) for v in hc['after_tax_anchors'].values()):.5f} pp", flush=True)
    if not hc["pass"]:
        print("harness FAIL")
        OUT.write_text(json.dumps({"status": "harness_failed", "harness_check": hc}, indent=2, sort_keys=True) + "\n")
        sys.exit(1)
    print("harness PASS", flush=True)
    if mode == "--harness":
        sys.exit(0)

    main_res = run_main(ARMS, m_list=(1, 2), extras_m2=True)
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
    oos_boot = {"per_market": {k: {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                       "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
                               for k in MARKETS}, "pooled": {}}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("OOS done", flush=True)
    gates = {}
    for arm in CANDIDATES + ("A3",):
        gates[arm] = historical_gates(main_res, boot, arm, "C0")
        gates[arm].update(oos_gates(oos, oos_boot["pooled"], arm))
        gates[arm]["pass_all"] = bool(all(gates[arm][k] for k in GATE_KEYS))
        gates[arm]["candidate"] = bool(arm in CANDIDATES)
    print("gates done", flush=True)

    etf = etf_stage()
    print(f"ETF stage done: {etf['n_passing']} passing, E1={etf['E1']}", flush=True)
    real = real_stage()
    print("real-fund surface done", flush=True)
    bounds = analytic_bounds()

    tax = after_tax_stage()
    print("after-tax windows done", flush=True)
    tax_boot = after_tax_bootstrap(ARMS)
    print("after-tax bootstrap done", flush=True)

    beats = {wn: {a: bool(tax["windows"][wn][a]["progressive"]["cagr"] > tax["windows"][wn]["C0"]["progressive"]["cagr"])
                  for a in ARMS} for wn in TAX_WINDOWS}
    b1, b2 = tax_boot["progressive"], tax_boot["flat"]
    for a in ARMS:
        gates[a]["T1_beats_C0_all_windows"] = bool(all(beats[wn][a] for wn in TAX_WINDOWS))
        gates[a]["T2_B1_recent"] = bool(b1[f"{a}_vs_C0"]["median_delta_cagr_pp"] >= 0
                                        and b1[f"{a}_vs_C0"]["share_gt_0"] >= 0.55)
        gates[a]["T3_B2_flat"] = bool(b2[f"{a}_vs_C0"]["median_delta_cagr_pp"] >= 0
                                      and b2[f"{a}_vs_C0"]["share_gt_0"] >= 0.55)
        gates[a]["T1_detail"] = beats
        gates[a]["E1_etf_drawdown"] = bool(etf["E1"]) if a == "A1" else None
        gates[a]["pass_all_with_tax"] = bool(gates[a]["pass_all"] and gates[a]["T1_beats_C0_all_windows"]
                                             and gates[a]["T2_B1_recent"] and gates[a]["T3_B2_flat"])
        if a == "A1":
            gates[a]["pass_all_with_tax_and_etf"] = bool(gates[a]["pass_all_with_tax"] and etf["E1"])
    adopted = [a for a in CANDIDATES if gates[a].get("pass_all_with_tax_and_etf")]
    if len(adopted) > 1:
        adopted = [max(adopted, key=lambda a: b1[f"{a}_vs_C0"]["median_delta_cagr_pp"])]

    out = {
        "campaign": "early_qqq_v1",
        "params": {
            "arms": list(ARMS), "candidates": list(CANDIDATES), "early_instrument": EARLY_INSTRUMENT,
            "instruments": {k: {"leverage": v[0], "expense": v[1], "base": v[2]} for k, v in INSTRUMENTS.items()},
            "c0_weight_early": 0.5, "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N, "band": C0_BAND, "cap": CAP,
            "early_path": {"sma_fast": SMA_FAST, "momentum_lag": LAG_FAST, "vol_median_window": MED_WINDOW},
            "cost_per_side_m1": COST_PER_SIDE, "main_window_cost_multipliers": [1, 2], "financing_spread": FIN,
            "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM, "measured": MEASURED,
                               "back_extension": EXT, "m": 2},
            "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK},
            "after_tax": {"capital_usd": TAX_CAPITAL, "windows": TAX_WINDOWS, "m": 2, "financing": "base",
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar": [TAX_BOOT_LO, TAX_BOOT_HI], "modes": ["progressive", "flat"]}},
            "etf": {"keys": list(ETF_KEYS), "quality_max_bad": QUALITY_MAX_BAD, "gate": "share delta DD <= +3pp >= 80%"},
            "real_window": REAL_WINDOW,
        },
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "etf_drawdown": etf,
        "real_fund_surface": real,
        "analytic_bounds": bounds,
        "after_tax_100k": tax,
        "after_tax_bootstrap": tax_boot,
        "after_tax_beats_c0_all_windows": beats,
        "adopted_candidate": adopted[0] if adopted else None,
        "verdict_worth_integrating": bool(adopted),
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    def p(x):
        return f"{x * 100:7.2f}%"

    w = main_res["window"]
    L = [f"early_qqq_v1 | I5: deliver the early leg in the unlevered fund | main window {w['start']} -> {w['end']} "
         f"({w['sessions']} sessions) | C0 = production R2",
         f"harness: PASS ({hc['n_checks']} pre-tax checks, {hc['n_outside_tolerance']} outside 0.01 pp; "
         f"simulator identity {hc['multi_instrument_equals_simulate_multi_single_leg']}; "
         f"{len(hc['after_tax_anchors'])} after-tax anchors, {hc['n_tax_anchors_outside_0.01pp']} outside 0.01 pp)",
         f"early path: {bounds['early_active_sessions']} sessions of {bounds['sessions_measured']} "
         f"({bounds['early_active_session_share'] * 100:.2f}%), mean w {bounds['early_mean_w']:.3f}, "
         f"mean DTB3 {bounds['early_mean_dtb3'] * 100:.2f}%", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm  CAGR   maxDD  Calmar 2017+   P1     P2     P3   turn/yr switch/yr | "
                 f"m=1 CAGR maxDD | boot med, dmed vs C0, share>=C0")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:3s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:6.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} "
                     f"{a['turnover_annual']:7.2f} {a['instrument_switches'] / trv2.calendar_years(w['start'], w['end']):8.2f} | "
                     f"{p(main_res[md]['m1'][arm]['cagr'])} {p(main_res[md]['m1'][arm]['max_dd'])} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+8.3f}pp {b['share_ge_C0'] * 100:5.1f}%")
        L.append("")
    L.append("turnover split (base, m=2): arm | by instrument (per year) | full: entries, exits, resizes, switches per year")
    for arm in ARMS:
        a = main_res["base"]["m2"][arm]
        ev = a["events_full"]
        L.append(f"  {arm:3s} {a['turnover_annual_by_instrument']} | {ev['counts']['entries'] / ev['years']:.1f}, "
                 f"{ev['counts']['exits'] / ev['years']:.1f}, {ev['resizes_per_year']:.1f}, {ev['switches_per_year']:.2f}")
    L.append("")
    L.append(f"OOS (calibrated, m=2), six windows: arm | median dCAGR | windows dCAGR >= -0.5pp | "
             f"windows dDD <= +3pp | max dDD | pooled boot median dCAGR")
    for arm in ARMS:
        d = gates[arm]["G7_detail"] if arm in gates else None
        if d is None:
            continue
        L.append(f"  {arm:3s} | {d['median_cagr_diff'] * 100:+7.3f}pp | {d['windows_cagr_ge_minus_0_5pp']}/6 | "
                 f"{d['windows_dd_within_3pp']}/6 | {d['max_dd_diff_max'] * 100:+6.2f}pp | "
                 f"{oos_boot['pooled'][arm]['diff_vs_C0_median'] * 100:+7.3f}pp")
    L.append("")
    L.append("gates vs C0 (R2): arm | G1b G1c G2b G2c G3b G3c G4 G5b G5c G6b G6c G7 G8 G9 | T1 T2 T3 | E1 | all")
    for arm in CANDIDATES + ("A3",):
        g = gates[arm]
        def fl(k):
            return "P" if g.get(k) else "F"

        L.append(f"  {arm:3s} | " + " ".join(fl(f"{x}_{md}") for x in ("G1", "G2", "G3", "G5", "G6") for md in FIN)
                 + f" {fl('G4')} {fl('G7')} {fl('G8')} {fl('G9')} | {fl('T1_beats_C0_all_windows')} "
                 f"{fl('T2_B1_recent')} {fl('T3_B2_flat')} | "
                 f"{'P' if g['E1_etf_drawdown'] else ('F' if g['E1_etf_drawdown'] is not None else '-')} | "
                 f"{'PASS' if g['pass_all_with_tax'] else 'FAIL'}")
    L.append("")
    L.append(f"ETF drawdown generalisation (E1): {etf['n_passing']}/21 quality-passing "
             f"(excluded: {[k for k in etf['instruments'] if etf['instruments'][k].get('excluded')]}); "
             f"share dDD <= +3pp {etf['share_delta_max_dd_within_3pp'] * 100:.1f}%, median dDD "
             f"{etf['median_delta_max_dd_pp']:+.2f}pp, median dCAGR {etf['median_delta_cagr_pp']:+.3f}pp, "
             f"share dCAGR > 0 {etf['share_delta_cagr_gt_0'] * 100:.1f}% -> E1 "
             f"{'PASS' if etf['E1'] else 'FAIL'}")
    L.append(f"analytic bounds: pre-tax saving of the swap {bounds['analytic_pre_tax_cagr_gain_pp']:+.4f} pp/yr "
             f"(cap-side upper bound {bounds['cap_bound']['analytic_extra_beta_cagr_pp_upper_bound']:+.4f} pp on "
             f"{bounds['cap_bound']['sessions_w_eq_1']} sessions)")
    L.append("")
    L.append("after tax at $100k, liquidated, base financing, m=2 (progressive): arm | "
             "2000-2026 pre/prog/DD, 2010- pre/prog/DD, 2017- pre/prog/DD | d vs C0 (prog)")
    for arm in ARMS:
        row = []
        for wn in TAX_WINDOWS:
            r = tax["windows"][wn][arm]
            row.append(f"{p(r['pre_tax']['cagr'])}/{p(r['progressive']['cagr'])}/{p(r['progressive']['max_dd'])}")
        dl = " ".join(f"{(tax['windows'][wn][arm]['progressive']['cagr'] - tax['windows'][wn]['C0']['progressive']['cagr']) * 100:+.3f}pp"
                      for wn in TAX_WINDOWS)
        L.append(f"  {arm:3s} | " + " | ".join(row) + f" | {dl}")
    L.append("")
    for mode, lab in (("progressive", "B1-recent (progressive, fixed 2015-09..2026-09 TRY calendar)"),
                      ("flat", "B2 (flat 20%, losses carried forward)")):
        e = tax_boot[mode]
        L.append(f"{lab}: arm | median CAGR | median d vs C0 | share > 0")
        for arm in ARMS:
            v = e[f"{arm}_vs_C0"]
            L.append(f"  {arm:3s} | {p(e[arm]['cagr_median'])} | {v['median_delta_cagr_pp']:+8.3f}pp | "
                     f"{v['share_gt_0'] * 100:5.1f}%")
        L.append("")
    L.append(f"real funds (QQQ/QLD adjusted closes, {real['window'][0]} -> {real['window'][1]}): "
             f"C0 {p(real['arms']['C0']['cagr'])} CAGR {p(real['arms']['C0']['max_dd'])} DD, "
             f"A1 {p(real['arms']['A1']['cagr'])} CAGR {p(real['arms']['A1']['max_dd'])} DD -> "
             f"dCAGR {real['delta_cagr_pp']:+.3f}pp, dDD {real['delta_max_dd_pp']:+.2f}pp")
    L.append("")
    L.append(f"VERDICT: worth integrating = {bool(adopted)}"
             + (f" (adopted {adopted[0]})" if adopted else " (no candidate passes every gate)"))
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
