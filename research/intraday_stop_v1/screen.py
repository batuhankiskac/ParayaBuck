#!/usr/bin/env python3
"""intraday_stop_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --smoke    synthetic-data checks of the stop engine only (no real data, no arm).
  --harness  C0 = production R2 only. Reproduces research/fast_reentry_v1/RESULTS.json's R2
             row (base/calibrated x m=1/m=2 CAGR, max DD, turnover) to 0.01 pp, checks that the
             stop engine with the stop disabled equals the fast_reentry_v1 band simulator bit for
             bit, that the QQQ OHLC loader equals trend_robustness_v2.load() exactly, and
             reproduces research/r2_tbill_combo_v1/RESULTS.json's R2 after-tax anchors at $100k
             on all three windows plus the single-leg after-tax cross-check. Simulates no
             non-control arm on real data.
  --run      re-runs the harness, stops if it fails, then runs every arm (C0, T8, T8c, T12,
             T12c) on the main window, the OHLC-joint block-20 bootstrap, the 21-ETF reduced OOS
             and its pooled bootstrap, the gates and the report-only statistics (stop-outs per
             year, per-episode saves/whipsaws), then the after-Turkish-tax windows and the B1/B2
             clean after-tax bootstrap. Writes RESULTS.json and SUMMARY.txt.

Harness helpers are imported from the sibling studies (never their main()):
  trv2 = research/trend_robustness_v2/screen.py   (synthetic 2x, vol, metrics)
  fr1  = research/fast_reentry_v1/screen.py       (E2 + early path = production R2, band engine,
                                                   OOS loaders, gate definitions, references)
  te   = research/r2_tbill_combo_v1/tax_engine.py (Turkish after-tax engine, vt_level_tax_v1 copy)
"""

from __future__ import annotations

import bisect
import csv
import importlib.util
import json
import math
import sys
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
fr1 = imp("fr1", ROOT / "research/fast_reentry_v1/screen.py")
te = imp("te", ROOT / "research/r2_tbill_combo_v1/tax_engine.py")

BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
ETF_DIR = ROOT / "data/historical"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
C0_BAND = trv2.BAND
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1
TOL = 1e-4  # 0.01 pp harness tolerance
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
STAGE_B_WINDOW = ("2017-01-02", "2026-07-30")

# arms: stop X (fraction below the prior QLD* close) and cooldown (sessions held flat after a stop)
ARMS = ("C0", "T8", "T8c", "T12", "T12c")
CANDIDATES = ("T8", "T8c", "T12", "T12c")
STOP = {"C0": (None, 0), "T8": (0.08, 0), "T8c": (0.08, 5), "T12": (0.12, 0), "T12c": (0.12, 5)}
SLIP = 0.0010  # 10 bps on stop fills (gated arms)
SLIP_STRESS = 0.0030  # 30 bps, report-only

# reduced OOS: 21 ETFs (range_vol_v2 set minus EWC, plus range_vol_v1's DIA/IWM/SPY)
ETFS_V2 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
           "EWJ", "EWG", "EWU", "EWA", "EWH", "EWS", "EWL", "EFA", "EEM"]
ETFS_V1 = ["DIA", "IWM", "SPY"]
ETFS = ETFS_V2 + ETFS_V1
ETFS_EXCLUDED = ["EWC"]  # pre-excluded: 5.6% bad bars under the range_vol_v2 quality rule
EXTRA_FIN = 0.0075
OOS_PATHS_PER_ET = 2000
OOS_BOOT_L = TSM_N + MEASURED

# after tax (report/gate): vt_level_tax_v1's windows at $100k, base financing, m = 2
TAX_WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
TAX_CAPITAL = 100_000.0
TAX_BOOT_PATHS = 1000
TAX_BOOT_LO, TAX_BOOT_HI = "2015-09-01", "2026-09-30"
TAX_BOOT_L = 253 + 10 * 252  # 2773
TAX_BOOT_EXT = 3
TAX_BOOT_I0 = TAX_BOOT_EXT + 253

# published reproduction anchors
REF_R2 = {  # research/fast_reentry_v1/RESULTS.json main_window.{md}.{m}.R2 (cagr, max_dd, turnover)
    ("base", 1): (0.157538, 0.464143, 5.991522),
    ("base", 2): (0.154766, 0.467584, 5.991522),
    ("calibrated", 1): (0.151719, 0.465364, 5.991496),
    ("calibrated", 2): (0.148961, 0.468797, 5.991496),
}
REF_R2_TAX = {  # research/r2_tbill_combo_v1/RESULTS.json after_tax_100k.windows.*.R2
    "2000_03_to_2026_09": {"pre_tax": 0.154732, "progressive": 0.1016, "progressive_max_dd": 0.467584},
    "2010_01_to_end": {"pre_tax": 0.221587, "progressive": 0.147059, "progressive_max_dd": 0.437839},
    "2017_01_to_end": {"pre_tax": 0.325330, "progressive": 0.228861, "progressive_max_dd": 0.362426},
}
REF_R2_BOOT = {"progressive": 0.035155, "flat": 0.039678}  # after_tax_bootstrap.*.R2.cagr_median
REF_QLD_LOW = {"corr": 0.988, "n_below_8pct_synth": 88, "n_below_8pct_real": 86,
               "n_below_12pct_synth": 18, "n_below_12pct_real": 17, "real_first": "2006-06-21"}


# ----------------------------------------------------------------------------- data
def load_qqq_ohlc():
    """QQQ (open, high, low, close, dividend) on trend_robustness_v2.load()'s day grid.

    trend_robustness_v2.load() takes only open/close/dividend; `--harness` checks that the
    open/close/dividend rows here are identical to it."""
    raw = {}
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == "QQQ":
                raw[r["date"]] = tuple(float(r[k]) for k in ("open", "high", "low", "close", "dividend"))
    days, arr, y = trv2.load()
    keys = ("open", "high", "low", "close", "dividend")
    out = {k: np.full(len(days), np.nan) for k in keys}
    for i, d in enumerate(days):
        if d in raw:
            for k, v in zip(keys, raw[d]):
                out[k][i] = v
    return days, out, y, arr


def qld_low_quality_crosscheck():
    """Report-only: modelled QLD* low vs the real QLD bars in the same file (2006-06-21 ->)."""
    rows = defaultdict(dict)
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            rows[r["symbol"]][r["date"]] = r
    days = sorted(set(rows["QQQ"]) & set(rows["QLD"]))
    g = lambda s, k: np.array([float(rows[s][d][k]) for d in days])
    lo_q, c_q, dv = g("QQQ", "low"), g("QQQ", "close"), g("QQQ", "dividend")
    lo_l, c_l = g("QLD", "low"), g("QLD", "close")
    synth = 2 * ((lo_q[1:] + dv[1:]) / c_q[:-1] - 1)
    real = lo_l[1:] / c_l[:-1] - 1
    ok = np.isfinite(synth) & np.isfinite(real)
    return {"window": [days[0], days[-1]], "n": int(ok.sum()),
            "corr_low_return": float(np.corrcoef(synth[ok], real[ok])[0, 1]),
            "mean_abs_diff": float(np.mean(np.abs(synth[ok] - real[ok]))),
            "n_below_8pct_synth": int((synth <= -0.08).sum()), "n_below_8pct_real": int((real <= -0.08).sum()),
            "n_below_12pct_synth": int((synth <= -0.12).sum()), "n_below_12pct_real": int((real <= -0.12).sum())}


def synthetic_low(q_low, q_div, q_close, qls_close):
    """QLD* session low: daily-reset 2x of the QQQ low's total return from the prior close.

    q_low/q_div/q_close are level arrays [..., T] on one time base, qls_close is the synthetic
    QLD* close on the same base; returns [..., T] with element 0 set to 1 like synthetic_2x."""
    prev = q_close[..., :-1]
    r_low = (q_low[..., 1:] + q_div[..., 1:]) / prev - 1
    return low_from_ratios(r_low, qls_close)


def low_from_ratios(r_low, qls_close):
    """Same construction as trv2.synthetic_2x's open, from precomputed low returns [..., T-1]."""
    ones = np.ones(qls_close.shape[:-1] + (1,))
    return np.concatenate([ones, qls_close[..., :-1] * (1 + 2 * r_low)], axis=-1)


def market_quality_ok(high, low, opn, close):
    """range_vol_v2's fixed rule: a bar is bad on high==low or an invalid open; >5% bad fails."""
    bad = n = 0
    for h, lo, o, cl in zip(high, low, opn, close):
        n += 1
        if h is None or lo is None or h == lo:
            bad += 1
            continue
        if o is None or o <= 0 or o < lo * (1 - 1e-9) or o > h * (1 + 1e-9):
            bad += 1
    return (bad / max(1, n)) <= 0.05, bad / max(1, n)


def load_etf(key):
    """Adjusted OHLC of one ETF: open/high/low x adjclose/close, close = adjclose."""
    for sub in ("v2", "v1"):
        path = ETF_DIR / f"range_vol_{sub}_{key.lower()}.csv"
        if path.exists():
            break
    else:
        raise FileNotFoundError(key)
    rows = []
    with path.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                         float(r["close"]), float(r["adjclose"])))
    dates = [r[0] for r in rows]
    o = np.empty(len(rows))
    h = np.empty(len(rows))
    lo = np.empty(len(rows))
    c = np.empty(len(rows))
    for k, (_, op, hi, lw, cl, adj) in enumerate(rows):
        f = adj / cl
        o[k], h[k], lo[k], c[k] = op * f, hi * f, lw * f, adj
    d3, tb = fr1.load_rates()
    y = fr1.rate_on(dates, d3, tb)
    assert START <= len(dates) - 1
    return dates, o, h, lo, c, np.array(y)


# ----------------------------------------------------------------------------- engine
def simulate_stop(opens, closes, lows, wants, cost, start, band, stop_x=None, cooldown=0, slip=0.0):
    """trend_robustness_v2 / fast_reentry_v1 mechanics plus a daily-re-armed intraday stop.

    Per session i: overnight growth; trade at the open to wants[i-1] (an arm in a cooldown is
    forced to 0); if a position is held after that trade and stop_x is set, S = (1-stop_x) *
    closes[i-1] and the position is sold at min(S, open) if the open is at or below S (gap
    through), else at S if low <= S, at fill * (1 - slip), with the usual cost on the exit
    notional; the rest of the session is in cash. A stop arms a cooldown of `cooldown` sessions
    (sessions i+1..i+cooldown are flat). Then intraday growth.

    Returns a dict of per-path arrays: nav [P, T-start+1], turn, ntr, codes [P, T-start]
    (0 none, 1 entry, 2 exit, 3 resize up, 4 resize down, 5 stop), traded, stop (bool),
    fill (effective stop fill price), w_trade (weight after the open trade), reentry (session
    index, relative to start, of the first session after each stop with w_trade > 0; -1 if none).
    With stop_x=None it equals fast_reentry_v1.simulate_band bit for bit."""
    opens, closes, lows, wants = (np.atleast_2d(np.asarray(x, dtype=float))
                                  for x in (opens, closes, lows, wants))
    P, T = closes.shape
    nsess = T - start
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, nsess + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    codes = np.zeros((P, nsess), dtype=np.int8)
    traded = np.zeros((P, nsess))
    stop_sess = np.zeros((P, nsess), dtype=bool)
    stop_fill = np.zeros((P, nsess))
    w_trade = np.zeros((P, nsess))
    suppressed_until = np.full(P, -10 ** 9, dtype=np.int64)
    n_stops = np.zeros(P, dtype=np.int64)
    for i in range(start, T):
        k = i - start
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = np.where(tot > 0, va / np.where(tot > 0, tot, 1.0), 0.0)
        want = wants[:, i - 1]
        want = np.where(np.isnan(want), 0.0, want)
        if stop_x is not None:
            want = np.where(i <= suppressed_until, 0.0, want)
        d = np.abs(want - w)
        held, tgt = w > 0, want > 0
        flip = held != tgt
        trade = flip | (d > band)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        turn = turn + np.where(trade, d, 0.0)
        ntr = ntr + trade.astype(np.int64)
        codes[:, k] = np.where(~trade, 0, np.where(flip & tgt, 1, np.where(flip, 2, np.where(want > w, 3, 4))))
        traded[:, k] = np.where(trade, d, 0.0)
        w = np.where(trade, want, w)
        w_trade[:, k] = w
        ga = closes[:, i] / opens[:, i]
        if stop_x is None:
            va = w * ga
            tot = 1 - w + va
            nav = nav * tot
            w = np.where(tot > 0, va / np.where(tot > 0, tot, 1.0), 0.0)
        else:
            S = (1 - stop_x) * closes[:, i - 1]
            gap = w > 0
            trg_gap = gap & (opens[:, i] <= S)
            trg_low = gap & (~trg_gap) & (lows[:, i] <= S)
            trig = trg_gap | trg_low
            fill = np.where(trg_gap, opens[:, i], S)
            ret = np.where(trig, fill * (1 - slip) / opens[:, i], ga)
            ex = np.where(trig, w, 0.0)
            nav = nav * (1 - cost * ex)
            turn = turn + ex
            ntr = ntr + trig.astype(np.int64)
            codes[:, k] = np.where(trig, 5, codes[:, k])
            traded[:, k] = np.where(trig, w, traded[:, k])
            stop_sess[:, k] = trig
            stop_fill[:, k] = np.where(trig, fill * (1 - slip), 0.0)
            n_stops = n_stops + trig.astype(np.int64)
            va = w * ret
            tot = 1 - w + va
            nav = nav * tot
            w = np.where(trig, 0.0, np.where(tot > 0, va / np.where(tot > 0, tot, 1.0), 0.0))
            suppressed_until = np.where(trig, i + cooldown, suppressed_until)
        out[:, k + 1] = nav

    reentry = np.full((P, nsess), -1, dtype=np.int64)
    for p in range(P):
        for k in np.flatnonzero(stop_sess[p]):
            nz = np.flatnonzero(w_trade[p, k + 1:])
            reentry[p, k] = int(k + 1 + nz[0]) if nz.size else -1
    return {"nav": out, "turn": turn, "ntr": ntr, "codes": codes, "traded": traded,
            "stop": stop_sess, "fill": stop_fill, "w_trade": w_trade, "reentry": reentry,
            "n_stops": n_stops}


# ----------------------------------------------------------------------------- metrics
def event_stats(dates, codes, traded, a=None, b=None):
    """fast_reentry_v1.event_stats plus the stop code 5."""
    sess = dates[1:]
    lo = 0 if a is None else next(k for k, d in enumerate(sess) if d > a)
    hi = len(sess) if b is None else max(k for k, d in enumerate(sess) if d <= b) + 1
    c, tr = codes[lo:hi], traded[lo:hi]
    yrs = trv2.calendar_years(dates[lo], sess[hi - 1])
    n = {name: int(np.sum(c == v)) for name, v in
         (("entries", 1), ("exits", 2), ("resize_up", 3), ("resize_down", 4), ("stops", 5))}
    return {
        "years": yrs,
        "counts": n,
        "round_trips_per_year": n["exits"] / yrs,
        "trades_per_year": (n["entries"] + n["exits"] + n["resize_up"] + n["resize_down"]) / yrs,
        "resizes_per_year": (n["resize_up"] + n["resize_down"]) / yrs,
        "sells_per_year": (n["exits"] + n["resize_down"] + n["stops"]) / yrs,
        "stops_per_year": n["stops"] / yrs,
        "turnover_per_year": float(tr.sum() / yrs),
        "turnover_from_entries_exits_per_year": float(tr[(c == 1) | (c == 2)].sum() / yrs),
        "turnover_from_resizes_per_year": float(tr[(c == 3) | (c == 4)].sum() / yrs),
        "turnover_from_stops_per_year": float(tr[c == 5].sum() / yrs),
    }


def arm_stats(dates, res, wants, start, extras, early_active=None):
    years = trv2.calendar_years(dates[0], dates[-1])
    nav = res["nav"][0]
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(res["turn"][0] / years)
    st["trades"] = int(res["ntr"][0])
    st["stops"] = int(res["n_stops"][0])
    st["subperiods"] = {name: fr1.seg(dates, nav, a, b) for name, a, b in SUBPERIODS}
    if extras:
        st["stage_b_window"] = fr1.seg(dates, nav, *STAGE_B_WINDOW)
        st["events_full"] = event_stats(dates, res["codes"][0], res["traded"][0])
        st["events_2017_on"] = event_stats(dates, res["codes"][0], res["traded"][0], "2016-12-31")
        tgt = wants[start - 1:-1]
        st["time_in_market"] = float((tgt > 0).mean())
        if early_active is not None:
            ea = np.atleast_2d(early_active)
            sl = slice(start - 1, start - 1 + len(tgt))
            st["early_time_in_market"] = float(ea[0, sl].mean())
    return st


def stop_episodes(dates, nav_arm, nav_ctl, res, cooldown, opens):
    """Per-stop attribution (report-only, base financing, m = 2).

    A stop at session i = start+k is attributed over the closes [i-1, i+cooldown+1] (the stop
    fill, the flat sessions and the first session in which the arm is back on R2's target), as
    the difference between the arm's and the control's NAV return over that span."""
    stop, fill = res["stop"][0], res["fill"][0]
    reentry = res["reentry"][0]
    eps = []
    for k in np.flatnonzero(stop):
        k = int(k)
        j = min(k + cooldown + 2, len(nav_arm) - 1)
        ra = float(nav_arm[j] / nav_arm[k] - 1)
        rc = float(nav_ctl[j] / nav_ctl[k] - 1)
        rk = int(reentry[k])
        eps.append({"session": dates[k + 1], "stop_fill": float(fill[k]),
                    "reentry_session": dates[rk + 1] if rk >= 0 else None,
                    "reentry_open": float(opens[rk + 1]) if rk >= 0 else None,
                    "sessions_flat": (rk - k) if rk >= 0 else None,
                    "reentered_above": bool(rk >= 0 and float(opens[rk + 1]) > float(fill[k])),
                    "arm_return": ra, "r2_return": rc, "delta": ra - rc})
    return eps


def stop_summary(eps, dates):
    d = np.array([e["delta"] for e in eps]) if eps else np.array([0.0])
    yrs = trv2.calendar_years(dates[0], dates[-1])
    return {"n_stops": len(eps), "stops_per_year": len(eps) / yrs,
            "delta_sum": float(d.sum()), "delta_median": float(np.median(d)),
            "n_saves": int((d > 0).sum()), "saves_sum": float(d[d > 0].sum()),
            "n_whipsaws": int((d < 0).sum()), "whipsaw_sum": float(d[d < 0].sum()),
            "n_reentered_above_fill": int(sum(1 for e in eps if e["reentered_above"])),
            "biggest_saves": sorted(eps, key=lambda e: -e["delta"])[:5],
            "biggest_whipsaws": sorted(eps, key=lambda e: e["delta"])[:5]}


# ----------------------------------------------------------------------------- main window
def main_inputs(spread):
    days, ohlc, y, arr = load_qqq_ohlc()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    low = synthetic_low(ohlc["low"], ohlc["dividend"], ohlc["close"], c)
    e2 = fr1.e2_up(q[1])
    v20 = trv2.vol_n(c)
    return days, arr, o, c, low, e2, v20


def run_main_full(arms, m_list=(1, 2, 4), slip=SLIP):
    """Main-window run; keeps the engine outputs needed for the episode attribution."""
    out = {}
    for md, spread in FIN.items():
        days, arr, o, c, low, e2, v20 = main_inputs(spread)
        dates = days[START - 1:]
        price = arr["QQQ"][1]
        comps = fr1.early_components(price)
        wants, early = fr1.arm_signals(price, e2[0], v20[0], "R2", comps)
        wants, early = wants[0], early
        md_out = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
        engines = {}
        for m in m_list:
            hm, runs = {}, {}
            for arm in arms:
                stop_x, cd = STOP[arm]
                r = simulate_stop(o, c, low, wants, COST_PER_SIDE * m, START, C0_BAND,
                                  stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
                runs[arm] = r
                hm[arm] = arm_stats(dates, r, wants, START, m == 2, early_active=early)
            engines[m] = runs
            md_out[f"m{m}"] = hm
        ctl = engines[2]["C0"]["nav"][0]
        for arm in arms:
            if arm == "C0":
                continue
            _, cd = STOP[arm]
            eps = stop_episodes(dates, engines[2][arm]["nav"][0], ctl, engines[2][arm], cd, o)
            md_out["m2"][arm]["stop_summary"] = stop_summary(eps, dates)
            md_out["m2"][arm]["stop_episodes"] = eps
        out[md] = md_out
    return out


def run_main(arms, m_list=(1, 2), slip=SLIP):
    """Light run for the harness (no extras)."""
    res = {}
    for md, spread in FIN.items():
        days, arr, o, c, low, e2, v20 = main_inputs(spread)
        dates = days[START - 1:]
        price = arr["QQQ"][1]
        comps = fr1.early_components(price)
        wants = fr1.arm_signals(price, e2[0], v20[0], "R2", comps)[0][0]
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                stop_x, cd = STOP[arm]
                r = simulate_stop(o, c, low, wants, COST_PER_SIDE * m, START, C0_BAND,
                                  stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
                hm[arm] = arm_stats(dates, r, wants, START, False)
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


# ----------------------------------------------------------------------------- bootstrap (OHLC joint)
def boot_main(arms, paths=PATHS, slip=SLIP):
    """Stationary block bootstrap (mean block 20), draws identical to signal_ensemble_v1.

    Each drawn session carries its QQQ (open, high, low, close, dividend) tuple and DTB3 jointly,
    so the modelled intraday low is drawn with the same index as the open and the close. Signals
    on the resampled price level, instrument on the resampled total-return level, m = 2."""
    days, ohlc, y, arr = load_qqq_ohlc()
    q_open, q_close, q_div, q_low = ohlc["open"], ohlc["close"], ohlc["dividend"], ohlc["low"]
    T = q_close.shape[0]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    cg = {(md, a): [] for md in FIN for a in arms}
    tv = {(md, a): [] for md in FIN for a in arms}
    st = {(md, a): [] for md in FIN for a in arms}
    for chunk in range(0, paths, CHUNK):
        P = min(CHUNK, paths - chunk)
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
        prev = q_close[idx - 1]
        r_on_tr = (q_open[idx] + q_div[idx]) / prev
        r_on_p = q_open[idx] / prev
        r_id = q_close[idx] / q_open[idx]
        r_low = (q_low[idx] + q_div[idx]) / prev - 1
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = fr1.e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = fr1.early_components(pc)
        for md in FIN:
            so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            sl = low_from_ratios(r_low, sc)
            vol_instr = trv2.vol_n(sc)
            wants = fr1.arm_signals(pc, e2, vol_instr, "R2", comps)[0]
            for arm in arms:
                stop_x, cd = STOP[arm]
                r = simulate_stop(so, sc, sl, wants, COST_PER_SIDE * 2, start, C0_BAND,
                                  stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
                nav = r["nav"]
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(r["turn"] / (k / 252))
                st[(md, arm)].append(r["n_stops"] / (k / 252))
    return ({k: np.concatenate(v) for k, v in cg.items()},
            {k: np.concatenate(v) for k, v in tv.items()},
            {k: np.concatenate(v) for k, v in st.items()})


def boot_summary(cg, tv, st, arms, models, ctrl="C0"):
    out = {}
    for md in models:
        out[md] = {}
        c0 = cg[(md, ctrl)]
        for a in arms:
            c = cg[(md, a)]
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "turnover_median": float(np.median(tv[(md, a)])),
                          "stops_per_year_median": float(np.median(st[(md, a)])),
                          "diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                          "diff_vs_C0_p25": float(np.percentile(d, 25)),
                          "diff_vs_C0_p75": float(np.percentile(d, 75)),
                          "share_ge_C0": float(np.mean(d >= 0))}
    return out


# ----------------------------------------------------------------------------- reduced OOS (ETFs)
def oos_window_etf(key, arms, slip=SLIP):
    dates, o, h, lo, c, y = load_etf(key)
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    sl = synthetic_low(lo, np.zeros_like(lo), c, sc)
    e2 = fr1.e2_up(c)
    vol_instr = trv2.vol_n(sc)
    comps = fr1.early_components(c)
    wants = fr1.arm_signals(c, e2, vol_instr, "R2", comps)[0]
    start = START
    dates_w = dates[start - 1:]
    years = trv2.calendar_years(dates_w[0], dates_w[-1])
    res = {"window": {"start": dates_w[1], "end": dates_w[-1], "sessions": len(dates_w) - 1},
           "loaded_bars": len(dates), "quality_ok": market_quality_ok(h, lo, o, c)[0],
           "bad_bar_share": market_quality_ok(h, lo, o, c)[1]}
    for arm in arms:
        stop_x, cd = STOP[arm]
        r = simulate_stop(so, sc, sl, wants, COST_PER_SIDE * 2, start, C0_BAND,
                          stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
        nav = r["nav"][0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = event_stats(dates_w, r["codes"][0], r["traded"][0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
                    "turnover_annual": float(r["turn"][0] / years),
                    "stops_per_year": ev["stops_per_year"], "sells_per_year": ev["sells_per_year"]}
    return res


def boot_oos_etf(key, rng, arms, paths=OOS_PATHS_PER_ET, slip=SLIP):
    dates, o, h, lo, c, y = load_etf(key)
    pool = np.arange(START, len(dates))
    npool = len(pool)
    acc = {a: [] for a in arms}
    for chunk in range(0, paths, CHUNK):
        P = min(CHUNK, paths - chunk)
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
        ll = np.concatenate([np.ones((P, 1)), cc[:, :-1] * lo[idx] / c[idx - 1]], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = fr1.e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        sl = synthetic_low(ll, np.zeros_like(ll), cc, sc)
        vol_instr = trv2.vol_n(sc)
        comps = fr1.early_components(cc)
        wants = fr1.arm_signals(cc, e2, vol_instr, "R2", comps)[0]
        for arm in arms:
            stop_x, cd = STOP[arm]
            r = simulate_stop(so, sc, sl, wants, COST_PER_SIDE * 2, START, C0_BAND,
                              stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
            nav = r["nav"]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates
def oos_etf_gates(oos, pooled, arm, ctrl="C0"):
    keys = sorted(oos)
    dc = [oos[k][arm]["cagr"] - oos[k][ctrl]["cagr"] for k in keys]
    dd = [oos[k][arm]["max_dd"] - oos[k][ctrl]["max_dd"] for k in keys]
    med = float(np.median(dc))
    share_pos = float(np.mean(np.array(dc) > 0))
    share_dd = float(np.mean(np.array(dd) <= 0.03 + 1e-12))
    d = pooled[arm] - pooled[ctrl]
    return {"G7p": bool(med >= 0 and share_pos >= 0.6 and share_dd >= 0.8),
            "G7p_detail": {"median_delta_cagr": med, "share_delta_gt_0": share_pos,
                           "n_delta_gt_0": int(np.sum(np.array(dc) > 0)), "n_instruments": len(keys),
                           "share_dd_within_3pp": share_dd,
                           "n_dd_within_3pp": int(np.sum(np.array(dd) <= 0.03 + 1e-12)),
                           "max_dd_diff_max": float(max(dd)), "deltas": dict(zip(keys, dc)),
                           "dd_diffs": dict(zip(keys, dd))},
            "G8p": bool(float(np.median(d)) >= 0),
            "G8p_detail": {"median_delta_cagr": float(np.median(d)), "mean_delta_cagr": float(np.mean(d)),
                           "share_ge_0": float(np.mean(d >= 0)), "paths": int(len(d))}}


def historical_gates(main, boot, arm, ctrl="C0"):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"][ctrl]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"][ctrl]
        r[f"G1_{md}"] = fr1.g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
        r[f"G6_{md}"] = fr1.g_return(a1, c1)
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"][ctrl]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    lim = main["base"]["m2"]["C0"]["turnover_annual"] * 2.0
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    r["G9_limit"] = lim
    return r


GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + \
            ["G4", "G7p", "G8p", "G9", "G10", "G11", "G12"]
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ============================================================================= after tax
def tax_run_stop(capital, a, b, days, mkt, o, c, low, wants, mode, stop_x=None, cooldown=0, slip=SLIP):
    """Single-book after-tax run: research/r2_tbill_combo_v1/tax_engine.py run_strategy (itself
    the vt_level_tax_v1 corrected engine) with the intraday stop from simulate_stop bolted on.

    mode in {"pre_tax", "progressive", "flat"}. With stop_x=None the NAV path is identical to
    te.run_strategy (checked in --harness)."""
    days_all = list(days)
    if isinstance(a, int):
        i0, i1 = a, b
    else:
        i0 = bisect.bisect_left(days_all, a)
        i1 = bisect.bisect_right(days_all, b) - 1
    cost = COST_PER_SIDE * 2
    band = C0_BAND
    cashbox = {"v": float(capital)}
    if mode == "flat":
        account = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        account = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days_all, i0, i1)
    cashbox["v"] = float(capital)

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = sells = tax_sales = n_stops = 0
    prev_tgt = None
    suppressed_until = -10 ** 9
    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            account.tax_due[prevy] = account.assess(prevy)
            if mode == "flat":
                due = account.tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                    if cashbox["v"] < usd and account.units > 1e-12:
                        need = (usd - cashbox["v"]) / (1 - cost)
                        account.sell(i - 1, float(c[i - 1]), need / float(c[i - 1]), need * cost, "tax")
                        tax_sales += 1
                    cashbox["v"] -= usd
                    account.tax_paid_try[prevy] = account.tax_paid_try.get(prevy, 0.0) + due
                    account.tax_paid_usd += usd

        if pays is not None:
            for ty, part in pays.get(i, ()):
                if ty not in account.tax_due:
                    account.tax_due[ty] = 0.0
                due = account.tax_due.get(ty, 0.0)
                amt = due / 2 if part == 0 else due - account.tax_paid_try.get(ty, 0.0)
                if amt <= 1e-9:
                    continue
                usd = amt / mkt.fx[i]
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"]) / (1 - cost)
                    if account.units > 1e-12:
                        account.sell(i, float(o[i]), need / float(o[i]), need * cost, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                account.tax_paid_try[ty] += amt
                account.tax_paid_usd += usd

        po, pc, pl = float(o[i]), float(c[i]), float(low[i])
        held_value = account.units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = held_value / nav_open if nav_open > 0 else 0.0

        tgt = float(wants[i - 1]) if i > 0 else float(wants[0])
        tgt = 0.0 if math.isnan(tgt) else tgt
        if stop_x is not None and i <= suppressed_until:
            tgt = 0.0

        trade = abs(tgt - w) > band
        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(tgt - w)
            fee_total = nav_open * cost * drift if drift > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            target_units = tgt * nav_after_cost / po if po > 0 else 0.0
            du = target_units - account.units
            leg_fee = nav_open * cost * drift if drift > 0 else 0.0
            if du > 1e-12:
                account.buy(i, po, du * po, leg_fee)
            elif du < -1e-12:
                account.sell(i, po, -du, leg_fee, "resize")
                sells += 1
            trades += 1

        if stop_x is not None and account.units > 1e-12:
            S = (1 - stop_x) * float(c[i - 1])
            if po <= S:
                fill = po
            elif pl <= S:
                fill = S
            else:
                fill = None
            if fill is not None:
                units = account.units
                px = fill * (1 - slip)
                account.sell(i, px, units, units * px * cost, "stop")
                sells += 1
                n_stops += 1
                suppressed_until = i + cooldown

        nav[i - i0 + 1] = cashbox["v"] + account.units * pc
        prev_tgt = tgt

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(c[i1]), account.units, account.units * float(c[i1]) * cost, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - liq_tax / fx1
    mtm = nav[-1]
    fx0 = mkt.fx[i0 - 1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(account.tax_paid_try.values()) + liq_tax
    tax_total_usd = account.tax_paid_usd + liq_tax / fx1

    def cg(v0, v1):
        return (v1 / v0) ** (1 / yrs) - 1

    return {
        "window": [days_all[i0], days_all[i1]], "years": yrs, "capital_usd": capital,
        "ending_nav_usd": float(mtm), "max_dd_nav_usd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
        "cagr_usd_liquidated": float(cg(capital, liq)), "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1)),
        "tax_total_try": float(tax_total_try), "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "strategy_trades": int(trades), "sells": int(sells), "stops": int(n_stops),
        "tax_funding_sales": int(tax_sales), "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(account.gain_by_reason.items())},
        "index_relief_try": float(account.index_relief_try),
        "_nav": nav,
    }


def tax_inputs():
    days, ohlc, y, arr = load_qqq_ohlc()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y, EXP_Q)  # base financing, as vt_level_tax_v1
    low = synthetic_low(ohlc["low"], ohlc["dividend"], ohlc["close"], c)
    price, e2, v20 = q[1], fr1.e2_up(q[1])[0], trv2.vol_n(c)[0]
    comps = fr1.early_components(price)
    wants = fr1.arm_signals(price, e2, v20, "R2", comps)[0][0]
    return days, o, c, low, wants


def after_tax_control_checks():
    """Control-only after-tax checks (no candidate arm): R2 anchors + single-leg cross-check."""
    days, o, c, low, wants = tax_inputs()
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"anchors": {}, "single_leg_cross_check": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        ref = REF_R2_TAX[wn]
        r = {}
        for mode in ("pre_tax", "progressive"):
            res = tax_run_stop(TAX_CAPITAL, a, b, days, mkt, o, c, low, wants, mode)
            r[mode] = {"reference": ref[mode], "reproduced": res["cagr_usd_liquidated"],
                       "diff_pp": (res["cagr_usd_liquidated"] - ref[mode]) * 100}
        res = tax_run_stop(TAX_CAPITAL, a, b, days, mkt, o, c, low, wants, "progressive")
        r["progressive_max_dd"] = {"reference": ref["progressive_max_dd"], "reproduced": res["max_dd_nav_usd"],
                                   "diff_pp": (res["max_dd_nav_usd"] - ref["progressive_max_dd"]) * 100}
        out["anchors"][wn] = r
        te.init(days, {"C0": (o, c)}, {"C0": wants})
        ref_eng = te.run_strategy("C0", "pre_tax", TAX_CAPITAL, a, b, mkt)
        mine = tax_run_stop(TAX_CAPITAL, a, b, days, mkt, o, c, low, wants, "pre_tax")
        out["single_leg_cross_check"][wn] = {
            "engine_cagr": ref_eng["cagr_usd_liquidated"], "mine_cagr": mine["cagr_usd_liquidated"],
            "diff_pp": (mine["cagr_usd_liquidated"] - ref_eng["cagr_usd_liquidated"]) * 100,
            "nav_path_equal": bool(np.array_equal(np.asarray(mine["_nav"]), np.asarray(ref_eng["_nav"])))}
    out["pass"] = all(abs(v["diff_pp"]) <= 0.005 for wn in out["anchors"].values() for v in wn.values()) and \
        all(v["nav_path_equal"] for v in out["single_leg_cross_check"].values())
    return out


def after_tax_stage(slip=SLIP):
    """After-tax windows ($100k, base financing, m = 2, liquidated) for every arm."""
    days, o, c, low, wants = tax_inputs()
    mkt = te.load_market(FX_CSV, UFE_CSV, days)
    out = {"capital_usd": TAX_CAPITAL, "cost_multiplier_m": 2, "financing": "base", "windows": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        out["windows"][wn] = {}
        for arm in ARMS:
            stop_x, cd = STOP[arm]
            r = {}
            for mode in ("pre_tax", "progressive"):
                res = tax_run_stop(TAX_CAPITAL, a, b, days, mkt, o, c, low, wants, mode,
                                   stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
                r[mode] = {"cagr": res["cagr_usd_liquidated"], "max_dd": res["max_dd_nav_usd"],
                           "terminal_usd": res["terminal_usd_liquidated"],
                           "tax_usd": res["tax_total_usd_at_payment_fx"],
                           "trades_per_year": res["strategy_trades"] / res["years"],
                           "sells_per_year": res["sells"] / res["years"],
                           "stops_per_year": res["stops"] / res["years"]}
            out["windows"][wn][arm] = r
    return out


def fx_at(dates):
    fd, fv = [], []
    with FX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            fd.append(r["date"])
            fv.append(float(r["usdtry_forex_buying"]))
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates], dtype=float)


def tax_boot_calendar():
    days, ohlc, y, arr = load_qqq_ohlc()
    sub = [d for d in days if TAX_BOOT_LO <= d <= TAX_BOOT_HI]
    need = TAX_BOOT_L + TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {TAX_BOOT_LO}..{TAX_BOOT_HI} has only {len(sub)} days, need {need}")
    return days, ohlc, y, list(sub[:need])


def after_tax_bootstrap(n_paths=TAX_BOOT_PATHS, capital=TAX_CAPITAL, slip=SLIP):
    """Clean after-tax bootstrap (vt_level_tax_v1 B1 = progressive, B2 = 20% flat with loss
    carry-forward): block-20, ten-year paths on the fixed 2015-09 -> 2026-09 calendar with its
    fixed FX. The drawn session carries the QQQ OHLC tuple and DTB3 jointly."""
    all_days, ohlc, y, fixed_dates = tax_boot_calendar()
    fixed_fx = fx_at(fixed_dates)
    base_mkt = te.load_market(FX_CSV, UFE_CSV, all_days)
    ufe = base_mkt.ufe
    q_open, q_close, q_div, q_low = ohlc["open"], ohlc["close"], ohlc["dividend"], ohlc["low"]
    Tsrc = q_close.shape[0]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = TAX_BOOT_L, TAX_BOOT_EXT
    i0, i1 = TAX_BOOT_I0, len(fixed_dates) - 1
    res = {mode: {a: {"cagr": [], "maxdd": [], "sells": [], "stops": []} for a in ARMS}
           for mode in ("progressive", "flat")}
    for _ in range(n_paths):
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
        prev = q_close[idx - 1]
        r_on_tr = (q_open[idx] + q_div[idx]) / prev
        r_on_p = q_open[idx] / prev
        r_id = q_close[idx] / q_open[idx]
        r_low = (q_low[idx] + q_div[idx]) / prev - 1
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = fr1.e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)
        sl = low_from_ratios(r_low, sc)
        vol = trv2.vol_n(sc)
        comps = fr1.early_components(pc)
        wants = fr1.arm_signals(pc, e2, vol, "R2", comps)[0]
        mkt = te.Market(fixed_dates, fixed_fx, ufe)
        for arm in ARMS:
            stop_x, cd = STOP[arm]
            for mode in ("progressive", "flat"):
                r = tax_run_stop(capital, i0, i1, fixed_dates, mkt, so[0], sc[0], sl[0], wants[0],
                                 mode, stop_x=stop_x, cooldown=cd, slip=slip if stop_x else 0.0)
                if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                    raise RuntimeError(f"non-positive/NaN terminal arm {arm} {mode}")
                res[mode][arm]["cagr"].append(r["cagr_usd_liquidated"])
                res[mode][arm]["maxdd"].append(r["max_dd_nav_usd"])
                res[mode][arm]["sells"].append(r["sells"] / r["years"])
                res[mode][arm]["stops"].append(r["stops"] / r["years"])
    out = {}
    for mode in ("progressive", "flat"):
        med = float(np.median(res[mode]["R2"]["cagr"]))
        e = {"tax_kind": mode, "n_paths": n_paths, "calendar_start": fixed_dates[0],
             "calendar_end": fixed_dates[-1], "seed": SEED, "block": BLOCK, "capital_usd": capital,
             "anchor_R2_cagr_median": {"reference": REF_R2_BOOT[mode], "reproduced": med,
                                       "diff_pp": (med - REF_R2_BOOT[mode]) * 100}}
        for a in ARMS:
            d = np.array(res[mode][a]["cagr"]) - np.array(res[mode]["C0"]["cagr"])
            e[a] = {"cagr_median": float(np.median(res[mode][a]["cagr"])),
                    "maxdd_median": float(np.median(res[mode][a]["maxdd"])),
                    "sells_per_year_median": float(np.median(res[mode][a]["sells"])),
                    "stops_per_year_median": float(np.median(res[mode][a]["stops"])),
                    "vs_C0_median_delta_cagr_pp": float(np.median(d) * 100),
                    "vs_C0_share_gt_0": float(np.mean(d > 0))}
            if a != "C0":
                e[a]["vs_C0_p05_delta_cagr_pp"] = float(np.percentile(d, 5) * 100)
                e[a]["vs_C0_p95_delta_cagr_pp"] = float(np.percentile(d, 95) * 100)
        out[mode] = e
    return out


# ----------------------------------------------------------------------------- harness
def harness():
    days_ref, arr_ref, y_ref = trv2.load()
    days, ohlc, y, arr = load_qqq_ohlc()
    loader_ok = {"days": days == days_ref,
                 "open": bool(np.array_equal(np.nan_to_num(ohlc["open"]), np.nan_to_num(arr_ref["QQQ"][0]))),
                 "close": bool(np.array_equal(np.nan_to_num(ohlc["close"]), np.nan_to_num(arr_ref["QQQ"][1]))),
                 "dividend": bool(np.array_equal(np.nan_to_num(ohlc["dividend"]), np.nan_to_num(arr_ref["QQQ"][2]))),
                 "dtb3": bool(np.array_equal(y, y_ref))}
    h = run_main(("C0",))
    checks, bad = {}, 0
    for (md, m), (cagr, dd, turn) in REF_R2.items():
        x = h[md][f"m{m}"]["C0"]
        for k, ref in (("cagr", cagr), ("max_dd", dd), ("turnover_annual", turn)):
            d = (x[k] - ref) * (100 if k != "turnover_annual" else 1)
            checks[f"C0_{md}_m{m}_{k}"] = {"reference": ref, "reproduced": x[k], "diff": d}
            if abs(d) > (TOL * 100 if k != "turnover_annual" else TOL):
                bad += 1
    days2, arr2, o, c, low, e2, v20 = main_inputs(0.0)
    w = fr1.wants_for(e2, v20)
    nav_a = simulate_stop(o, c, low, w, COST_PER_SIDE * 2, START, C0_BAND)["nav"][0]
    nav_b = fr1.simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0][0]
    nav_c = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0][0]
    sim_equal = bool(np.array_equal(nav_a, nav_b)) and bool(np.array_equal(nav_a, nav_c))
    tax = after_tax_control_checks()
    ok = (bad == 0) and sim_equal and all(loader_ok.values()) and bool(tax["pass"])
    return {"window": h["window"], "loader_checks": loader_ok, "reproduction_checks": checks,
            "tolerance_pp": TOL * 100, "n_outside_tolerance": bad,
            "stop_engine_equals_fast_reentry_band": sim_equal, "tax_control": tax, "pass": bool(ok)}


# ----------------------------------------------------------------------------- smoke (synthetic only)
def smoke():
    """Engine mechanics on hand-made bars; no real data, no arm."""
    n = 12
    c = np.array([100.0] * n)
    o = np.array([100.0] * n)
    low = np.array([100.0] * n)
    low[5] = 90.0  # session 5: no gap, a 10% intraday low
    o[8] = 80.0    # session 8: a 20% gap through both stops
    low[8] = 79.0
    c[8] = 85.0
    o, c, low = np.atleast_2d(o), np.atleast_2d(c), np.atleast_2d(low)
    wants = np.full((1, n), 0.98)
    start = 1
    res = []
    a0 = simulate_stop(o, c, low, wants, 0.0, start, C0_BAND)
    a1 = fr1.simulate_band(o, c, wants, 0.0, start, C0_BAND)
    res.append(("no_stop_equals_band", bool(np.array_equal(a0["nav"][0], a1[0][0]))))
    r8 = simulate_stop(o, c, low, wants, 0.0, start, C0_BAND, stop_x=0.08, cooldown=0, slip=0.0)
    res.append(("low_trigger_fill_at_S", bool(abs(r8["fill"][0, 4] - 92.0) < 1e-12)))
    res.append(("low_trigger_only_session5", bool(int(r8["stop"][0, :7].sum()) == 1 and r8["stop"][0, 4])))
    res.append(("back_on_target_at_close", bool(abs(r8["w_trade"][0, 5] - 0.98) < 1e-12)))
    r8s = simulate_stop(o, c, low, wants, 0.0, start, C0_BAND, stop_x=0.08, cooldown=0, slip=0.001)
    res.append(("gap_fill_at_open_minus_slip", bool(abs(r8s["fill"][0, 7] - 80.0 * 0.999) < 1e-12)))
    r12 = simulate_stop(o, c, low, wants, 0.0, start, C0_BAND, stop_x=0.12, cooldown=0, slip=0.0)
    res.append(("12pct_skips_10pct_low", bool((not r12["stop"][0, 4]) and r12["stop"][0, 7])))
    rc = simulate_stop(o, c, low, wants, 0.0, start, C0_BAND, stop_x=0.08, cooldown=5, slip=0.0)
    res.append(("cooldown_flat_5_sessions", bool((not rc["w_trade"][0, 5:10].any()) and rc["w_trade"][0, 10] > 0)))
    res.append(("reentry_index", bool(int(rc["reentry"][0, 4]) == 10 and int(r8["reentry"][0, 4]) == 5)))
    return {"checks": {k: v for k, v in res}, "pass": all(v for _, v in res)}


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
    print(f"harness loader checks: {hc['loader_checks']}")
    print(f"harness R2 anchors: {hc['n_outside_tolerance']} outside {hc['tolerance_pp']:.2f} pp")
    print(f"harness stop engine (stop off) == fast_reentry band == simulate_multi: "
          f"{hc['stop_engine_equals_fast_reentry_band']}")
    for wn, v in hc["tax_control"]["anchors"].items():
        print(f"tax anchor {wn}: pre_tax {v['pre_tax']['diff_pp']:+.5f} progressive "
              f"{v['progressive']['diff_pp']:+.5f} maxDD {v['progressive_max_dd']['diff_pp']:+.5f} pp")
    print("tax single-leg cross-check: " + " ".join(
        f"{wn} nav_equal={v['nav_path_equal']} diff={v['diff_pp']:+.6f}pp"
        for wn, v in hc["tax_control"]["single_leg_cross_check"].items()))
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    main_res = run_main_full(ARMS, m_list=(1, 2, 4))
    print("main window done", flush=True)
    cg, tv, stp = boot_main(ARMS)
    boot = boot_summary(cg, tv, stp, ARMS, FIN)
    print("main bootstrap done", flush=True)
    oos = {k: oos_window_etf(k, ARMS) for k in ETFS}
    print("ETF OOS done", flush=True)
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_etf(k, rng, ARMS) for k in ETFS}
    pooled = {a: np.concatenate([per[k][a] for k in ETFS]) for a in ARMS}
    oos_boot = {"per_etf": {}, "pooled": {}}
    for k in ETFS:
        oos_boot["per_etf"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                      "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("ETF bootstrap done", flush=True)
    slip_stress = {}
    for a in CANDIDATES:
        d = [oos_window_etf(k, (a,), slip=SLIP_STRESS)[a]["cagr"] - oos[k][a]["cagr"] for k in ETFS]
        slip_stress[a] = float(np.median(d))
    print("slip stress done", flush=True)

    tax = after_tax_stage()
    print("after-tax windows done", flush=True)
    tax_boot = after_tax_bootstrap()
    print("after-tax bootstrap done", flush=True)

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_etf_gates(oos, pooled, arm))
        wins = {wn: tax["windows"][wn][arm]["progressive"]["cagr"] > tax["windows"][wn]["C0"]["progressive"]["cagr"]
                for wn in TAX_WINDOWS}
        g["G10"] = bool(all(wins.values()))
        g["G10_windows"] = wins
        b1, b2 = tax_boot["progressive"], tax_boot["flat"]
        g["G11"] = bool(b1[arm]["vs_C0_median_delta_cagr_pp"] >= 0 and b1[arm]["vs_C0_share_gt_0"] >= 0.55)
        g["G12"] = bool(b2[arm]["vs_C0_median_delta_cagr_pp"] >= 0 and b2[arm]["vs_C0_share_gt_0"] >= 0.55)
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g
    h2 = main_res["base"]["m2"]
    eligible = [arm for arm in CANDIDATES if gates[arm]["pass_all"]]
    sel = max(eligible, key=lambda a: h2[a]["cagr"]) if eligible else "none"
    selection = {"eligible": eligible, "selected": sel,
                 "rule": "highest base m=2 CAGR among arms passing all gates (G1-G12)"}
    print("gates done", flush=True)

    out = {
        "campaign": "intraday_stop_v1",
        "params": {
            "arms": list(ARMS), "stop": {k: {"x": v[0], "cooldown": v[1]} for k, v in STOP.items()},
            "slip": SLIP, "slip_stress": SLIP_STRESS, "c0_band": C0_BAND, "cap": CAP,
            "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q, "financing_spread": FIN,
            "main_window_cost_multipliers": [1, 2, 4],
            "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warmup": WARM,
                               "back_extension": EXT, "measured": MEASURED, "m": 2,
                               "resampling": "QQQ (open, high, low, close, dividend) tuple drawn jointly"},
            "oos_etfs": ETFS, "oos_excluded": ETFS_EXCLUDED, "oos_paths_per_etf": OOS_PATHS_PER_ET,
            "after_tax": {"capital_usd": TAX_CAPITAL, "windows": TAX_WINDOWS, "m": 2,
                          "financing": "base",
                          "engine": "vt_level_tax_v1 corrected (r2_tbill_combo_v1 tax_engine)",
                          "bootstrap": {"paths": TAX_BOOT_PATHS, "seed": SEED, "block": BLOCK,
                                        "calendar_lo": TAX_BOOT_LO, "calendar_hi": TAX_BOOT_HI,
                                        "modes": ["progressive (B1-recent)", "flat 20% (B2)"]}},
            "sig": "production R2 (E2 1.1.0 + early path at half weight) on QQQ price closes",
            "qld_low_model": "low*_t = close*_{t-1} * (1 + 2*((low_t + div_t)/close_{t-1} - 1))",
        },
        "harness_check": hc,
        "qld_low_crosscheck_vs_real_QLD": qld_low_quality_crosscheck(),
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "slip_stress_30bp_median_cagr_delta_vs_10bp": slip_stress,
        "after_tax_100k": tax,
        "after_tax_bootstrap": tax_boot,
        "gates": gates,
        "selection": selection,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    def p(x):
        return f"{x * 100:7.2f}%"

    w = main_res["base"]["window"]
    L = [f"intraday_stop_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | "
         f"C0 = production R2 (fast_reentry_v1 R2)",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'} (R2 anchors {hc['n_outside_tolerance']} outside "
         f"0.01 pp; loader {all(hc['loader_checks'].values())}; stop-off engine == band "
         f"{hc['stop_engine_equals_fast_reentry_band']}; tax anchors max |diff| "
         f"{max(abs(v['diff_pp']) for wn in hc['tax_control']['anchors'].values() for v in wn.values()):.5f} pp)",
         ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ P1 P2 P3 turn/yr stops/yr | m=1 CAGR maxDD | "
                 f"boot20 med, dmed vs C0, share>=C0, stops/yr")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            sp = a["subperiods"]
            L.append(f"  {arm:4s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{p(sp['P1']['cagr'])} {p(sp['P2']['cagr'])} {p(sp['P3']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} {a['events_full']['stops_per_year']:8.2f} | "
                     f"{p(main_res[md]['m1'][arm]['cagr'])} {p(main_res[md]['m1'][arm]['max_dd'])} | "
                     f"{p(b['cagr_median'])} {b['diff_vs_C0_median'] * 100:+7.3f}pp "
                     f"{b['share_ge_C0']:.3f} {b['stops_per_year_median']:8.2f}")
        L.append("")
    L.append("stop attribution (base, m=2): arm | stops | stops/yr | sum(delta) | median | saves/whipsaws "
             "| re-entered above fill | 5 biggest saves | 5 biggest whipsaws")
    for arm in CANDIDATES:
        s = main_res["base"]["m2"][arm]["stop_summary"]
        fs = lambda e: f"{e['session']}({e['delta'] * 100:+.2f}pp)"
        L.append(f"  {arm:4s} {s['n_stops']:4d} {s['stops_per_year']:7.2f} {s['delta_sum'] * 100:+8.2f}pp "
                 f"{s['delta_median'] * 100:+7.2f}pp {s['n_saves']:4d}/{s['n_whipsaws']:4d} "
                 f"{s['n_reentered_above_fill']:5d} | " +
                 " ".join(fs(e) for e in s["biggest_saves"]) + " | " +
                 " ".join(fs(e) for e in s["biggest_whipsaws"]))
    L.append("")
    L.append("ETF OOS (calibrated, m=2, 21 instruments): instrument | C0 CAGR/DD | per arm CAGR/DD")
    for k in ETFS:
        v = oos[k]
        L.append(f"  {k:4s} {v['window']['start']}->{v['window']['end']} | {p(v['C0']['cagr']).strip()}/"
                 f"{p(v['C0']['max_dd']).strip()} | " +
                 "  ".join(f"{a} {p(v[a]['cagr']).strip()}/{p(v[a]['max_dd']).strip()}" for a in CANDIDATES))
    L.append("")
    L.append("gates (" + " ".join(GATE_KEYS) + "):")
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:4s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append("")
    L.append("after tax ($100k, base, m=2, liquidated, progressive): window | arm | pre-tax | after-tax | "
             "maxDD | tax USD | trades/yr | sells/yr | stops/yr")
    for wn in TAX_WINDOWS:
        for arm in ARMS:
            r = tax["windows"][wn][arm]
            L.append(f"  {wn:20s} {arm:4s} {p(r['pre_tax']['cagr'])} {p(r['progressive']['cagr'])} "
                     f"{p(r['progressive']['max_dd'])} {r['progressive']['tax_usd']:12,.0f} "
                     f"{r['progressive']['trades_per_year']:8.2f} {r['progressive']['sells_per_year']:8.2f} "
                     f"{r['progressive']['stops_per_year']:8.2f}")
        L.append("    after-tax delta vs C0: " + " ".join(
            f"{a} {(tax['windows'][wn][a]['progressive']['cagr'] - tax['windows'][wn]['C0']['progressive']['cagr']) * 100:+.2f}pp"
            for a in CANDIDATES))
    L.append("")
    L.append(f"after-tax bootstrap (block-20, {TAX_BOOT_PATHS} paths, {TAX_BOOT_LO}..{TAX_BOOT_HI}, $100k):")
    for label, res in (("B1-recent progressive", tax_boot["progressive"]), ("B2 flat 20%", tax_boot["flat"])):
        L.append(f"  {label:22s} C0 median {p(res['C0']['cagr_median']).strip()} "
                 f"(R2 anchor ref {p(res['anchor_R2_cagr_median']['reference']).strip()} "
                 f"repro {p(res['anchor_R2_cagr_median']['reproduced']).strip()})")
        for a in CANDIDATES:
            L.append(f"    {a:4s} median {p(res[a]['cagr_median']).strip()} delta "
                     f"{res[a]['vs_C0_median_delta_cagr_pp']:+7.3f}pp share>0 {res[a]['vs_C0_share_gt_0']:.3f} "
                     f"p05 {res[a]['vs_C0_p05_delta_cagr_pp']:+.3f} p95 {res[a]['vs_C0_p95_delta_cagr_pp']:+.3f} "
                     f"stops/yr {res[a]['stops_per_year_median']:6.2f}")
    L.append("")
    L.append("30 bp slip stress (report-only, median delta CAGR vs the 10 bp run over the 21 ETFs): " +
             " ".join(f"{a} {slip_stress[a] * 100:+.3f}pp" for a in CANDIDATES))
    L.append(f"eligible {eligible}; selected: {sel}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
