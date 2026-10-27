#!/usr/bin/env python3
"""Trend robustness v2 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Standalone file: every helper is copied from research/stress_robustness_v1/stress.py
and generalized (expense parameter, multi-SMA signals, multi-leg simulator).
Nothing is imported from research/stress_robustness_v1.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
REF = Path(__file__).parent.parent / "stress_robustness_v1" / "RESULTS.json"
OUT = Path(__file__).parent / "RESULTS.json"
SUMMARY = Path(__file__).parent / "SUMMARY.txt"

ENSEMBLE_NS = (100, 150, 200, 250)
SMA_MAX = 250
VOL_N, VOL_TARGET, BAND, CAP = 20, 0.35, 0.005, 0.98
COST_PER_SIDE = 4.0 / 10_000  # per unit of traded notional; multiplied by m
EXP_Q, EXP_S = 0.0095, 0.0089  # QLD* / SSO* expense ratios, as in stress.py
SEED, PATHS, BLOCK = 20261011, 5000, 20
WARM, MEASURED = 250, 2520
CHUNK = 500
ARMS = ("C0", "E1", "M1", "M2")
TOL = 1e-4  # 0.01 pp harness tolerance


# ----------------------------------------------------------------------------- data (copied from stress.py)
def load():
    raw = defaultdict(dict)
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            raw[r["symbol"]][r["date"]] = tuple(float(r[k]) for k in ("open", "close", "dividend"))
    days = sorted(set(raw["QQQ"]) & set(raw["SPY"]))
    tb, last = {}, None
    with DTB3.open() as fh:
        rows = {r["observation_date"]: r["DTB3"] for r in csv.DictReader(fh)}
    for d in sorted(set(rows) | set(days)):
        v = rows.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        tb[d] = last
    arr = {}
    for s in ("QQQ", "SPY", "QLD"):
        a = np.full((3, len(days)), np.nan)
        for i, d in enumerate(days):
            if d in raw[s]:
                a[:, i] = raw[s][d]
        arr[s] = a  # rows: open, close, dividend
    y = np.array([tb[d] for d in days])
    return days, arr, y


def synthetic_2x(q_open, q_close, q_div, y, expense):
    """Daily-reset 2x on total return; works on 1-D or [paths, T] arrays (time on last axis)."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    g_cc = 1 + 2 * r_cc - (expense + y[..., :-1]) / 252
    close = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), close[..., :-1] * (1 + 2 * r_on)], axis=-1)
    return opn, close


# ----------------------------------------------------------------------------- signals (copied logic, multi-window)
def sma_up(c, n):
    """up[t]: close[t] > n-session SMA ending at t. NaN -> False."""
    c = np.atleast_2d(c)
    cs = np.cumsum(c, axis=1)
    sma = np.full_like(c, np.nan)
    sma[:, n - 1 :] = (cs[:, n - 1 :] - np.concatenate([np.zeros((c.shape[0], 1)), cs[:, :-n]], axis=1)) / n
    return c > sma  # NaN -> False


def vol_n(a, n=VOL_N):
    """vol[t]: annualized sample vol of the n daily returns ending at t."""
    a = np.atleast_2d(a)
    r = a[:, 1:] / a[:, :-1] - 1
    vol = np.full_like(a, np.nan)
    z = np.zeros((a.shape[0], 1))
    s1 = np.concatenate([z, np.cumsum(r, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(r * r, axis=1)], axis=1)
    w1 = s1[:, n:] - s1[:, :-n]  # sum of the n returns ending at close t (t >= n)
    w2 = s2[:, n:] - s2[:, :-n]
    vol[:, n:] = np.sqrt(np.maximum(w2 - w1 * w1 / n, 0) / (n - 1) * 252)
    return vol


def vt_of(vol):
    """vt(vol) = min(1, 0.35/vol); same edge convention as stress.py (vol<=0 -> 1.0)."""
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, VOL_TARGET / np.where(v > 0, v, 1.0)), 1.0)


def build_wants(upQ, upS, volQ, volS, arm):
    """Target weights [P, legs, T] for one arm; cap total at 0.98 by proportional scaling."""
    vtQ, vtS = vt_of(volQ), vt_of(volS)
    if arm == "C0":
        raw = [upQ[200].astype(float) * vtQ]
    elif arm == "E1":
        s = sum(upQ[n].astype(float) for n in ENSEMBLE_NS) / len(ENSEMBLE_NS)
        raw = [s * vtQ]
    elif arm == "M1":
        raw = [0.5 * upQ[200].astype(float) * vtQ, 0.5 * upS[200].astype(float) * vtS]
    elif arm == "M2":
        sQ = sum(upQ[n].astype(float) for n in ENSEMBLE_NS) / len(ENSEMBLE_NS)
        sS = sum(upS[n].astype(float) for n in ENSEMBLE_NS) / len(ENSEMBLE_NS)
        raw = [0.5 * sQ * vtQ, 0.5 * sS * vtS]
    else:
        raise ValueError(arm)
    raw = np.stack(raw, axis=1)
    tot = raw.sum(axis=1, keepdims=True)
    f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return raw * f


# ----------------------------------------------------------------------------- engine (stress.py mechanics, N legs)
def simulate_multi(opens, closes, wants, cost, start):
    """Stage A2 mechanics generalized to legs.

    opens/closes/wants: [legs, T] or [P, legs, T]; wants = target weight per leg
    for each session (targets for i < start unused). Per session: overnight leg
    growth, rebalance ALL legs to target when ANY leg drifts > BAND, cost =
    cost * sum |delta w|, then intraday leg growth. Idle cash earns zero.
    Dividends are embedded in the synthetic total-return construction.
    Returns NAV [P, T-start+1] (column 0 = close before `start`), turnover [P],
    trade count [P]. With one leg and the stress.py settings this reproduces
    stress.py simulate() exactly.
    """
    opens, closes, wants = (np.asarray(x) for x in (opens, closes, wants))
    if opens.ndim == 1:  # single leg, single path [T]
        opens, closes = opens[None, None, :], closes[None, None, :]
    elif opens.ndim == 2:  # [legs, T], single path
        opens, closes = opens[None, :, :], closes[None, :, :]
    if wants.ndim == 2:  # [legs, T], single path
        wants = wants[None, :, :]
    P, _, T = closes.shape
    nav = np.ones(P)
    w = np.zeros((P, wants.shape[1]))
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    turn = np.zeros(P)
    ntr = np.zeros(P, dtype=np.int64)
    for i in range(start, T):
        g_on = opens[:, :, i] / closes[:, :, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w.sum(axis=1) + va.sum(axis=1)
        nav = nav * tot
        w = va / tot[:, None]
        want = wants[:, :, i - 1]  # signal at the completed close before session i
        drift = np.abs(want - w)
        trade = drift.max(axis=1) > BAND
        dsum = drift.sum(axis=1)
        nav = np.where(trade, nav * (1 - cost * dsum), nav)
        turn = turn + np.where(trade, dsum, 0.0)
        ntr = ntr + trade.astype(np.int64)
        w = np.where(trade[:, None], want, w)
        ga = closes[:, :, i] / opens[:, :, i]
        va = w * ga
        tot = 1 - w.sum(axis=1) + va.sum(axis=1)
        nav = nav * tot
        w = va / tot[:, None]
        out[:, i - start + 1] = nav
    return out, turn, ntr


# ----------------------------------------------------------------------------- metrics (copied from stress.py)
def max_dd(x):
    x = np.atleast_2d(x)
    return 1 - (x / np.maximum.accumulate(x, axis=1)).min(axis=1)


def cagr(first, last, years):
    return (last / first) ** (1 / years) - 1


def calendar_years(d0: str, d1: str) -> float:
    from datetime import date

    return (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365.25


def curve_stats(dates, nav):
    """dates[k] is the date of nav[k] (nav[0] = close before the first simulated session)."""
    nav = np.asarray(nav)
    res = {
        "start": dates[0],
        "end": dates[-1],
        "cagr": cagr(nav[0], nav[-1], calendar_years(dates[0], dates[-1])),
        "max_dd": float(max_dd(nav)[0]),
        "ending_multiple": float(nav[-1] / nav[0]),
    }
    # calendar years
    yr = {}
    last_by_year = {}
    for d, v in zip(dates, nav):
        last_by_year[d[:4]] = v
    prev = nav[0]
    for y in sorted(last_by_year):
        yr[y] = float(last_by_year[y] / prev - 1)
        prev = last_by_year[y]
    counts = defaultdict(int)
    for d in dates[1:]:
        counts[d[:4]] += 1
    full_years = {y: r for y, r in yr.items() if counts[y] >= 240}  # excludes the partial first/last year
    res["years"] = yr
    worst = min(full_years, key=full_years.get)
    # sub-periods: from last close before a to last close <= b
    for name, a, b in (
        ("2000_2002", "2000-01-01", "2002-12-31"),
        ("2007_2009", "2007-01-01", "2009-12-31"),
        ("2017_end", "2017-01-01", dates[-1]),
    ):
        if b <= dates[0]:
            continue
        ia = max(k for k, d in enumerate(dates) if d < a) if dates[0] < a else 0
        ib = max(k for k, d in enumerate(dates) if d <= b)
        seg = nav[ia : ib + 1]
        res[name] = {
            "total_return": float(seg[-1] / seg[0] - 1),
            "max_dd": float(max_dd(seg)[0]),
            "cagr": cagr(seg[0], seg[-1], calendar_years(dates[ia], dates[ib])),
        }
    # recovery of the max drawdown and longest underwater spell
    peak = np.maximum.accumulate(nav)
    trough = int(np.argmin(nav / peak))
    pk = int(np.argmax(nav[: trough + 1]))
    rec = next((k for k in range(trough, len(nav)) if nav[k] >= nav[pk]), None)
    res["max_dd_episode"] = {
        "peak": dates[pk],
        "trough": dates[trough],
        "recovered": dates[rec] if rec is not None else None,
        "years_peak_to_recovery": calendar_years(dates[pk], dates[rec]) if rec is not None else None,
        "years_underwater_so_far": None if rec is not None else calendar_years(dates[pk], dates[-1]),
    }
    longest, s = (0.0, None, None), None
    for k in range(len(nav)):
        if nav[k] >= peak[k]:
            if s is not None:
                longest = max(longest, (calendar_years(dates[s], dates[k]), dates[s], dates[k]))
            s = k
    if s is not None and s < len(nav) - 1:
        longest = max(longest, (calendar_years(dates[s], dates[-1]), dates[s], "open"))
    res["longest_underwater"] = {"years": longest[0], "from_peak": longest[1], "to": longest[2]}
    return res


def sharpe_daily(nav):
    """Annualized Sharpe of daily simple returns, rf = 0, sample std (ddof=1)."""
    r = np.asarray(nav)[1:] / np.asarray(nav)[:-1] - 1
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(252)) if len(r) > 1 and sd > 0 else 0.0


def calmar(c, dd):
    return float(c / dd) if dd and dd > 1e-12 else None


# ----------------------------------------------------------------------------- bootstrap
def bootstrap(days, arr, y, m):
    """Stationary block bootstrap (mean block 20) of whole sessions.

    Jointly resamples QQQ/SPY overnight+intraday returns, dividends and DTB3.
    Each path: 250 warm-up + 2520 measured sessions. All arms + SPY run on the
    same paths. Returns per-arm median/p5 CAGR, median max DD, share beating SPY.
    """
    T = len(days)
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)  # sessions with a previous close
    npool = len(pool)
    L = WARM + MEASURED  # sampled sessions per path
    cost = COST_PER_SIDE * m
    start = SMA_MAX + 1
    acc = {a: {"cagr": [], "dd": []} for a in ARMS + ("SPY",)}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        yy = y[pool[pos] - 1]

        def leg(sym):
            prev = arr[sym][1][pool[pos] - 1]
            r_on = (arr[sym][0][pool[pos]] + arr[sym][2][pool[pos]]) / prev
            r_id = arr[sym][1][pool[pos]] / arr[sym][0][pool[pos]]
            c = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
            o = np.concatenate([np.ones((P, 1)), c[:, :-1] * r_on], axis=1)
            return o, c

        qo, qc = leg("QQQ")
        so, sc = leg("SPY")
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        qsyn_o, qsyn_c = synthetic_2x(qo, qc, np.zeros_like(qc), yyp, EXP_Q)
        ssyn_o, ssyn_c = synthetic_2x(so, sc, np.zeros_like(sc), yyp, EXP_S)
        upQ = {n: sma_up(qc, n) for n in ENSEMBLE_NS}
        upS = {n: sma_up(sc, n) for n in ENSEMBLE_NS}
        volQ, volS = vol_n(qsyn_c), vol_n(ssyn_c)
        for arm in ARMS:
            w = build_wants(upQ, upS, volQ, volS, arm)
            if arm in ("C0", "E1"):
                nav = simulate_multi(qsyn_o[:, None, :], qsyn_c[:, None, :], w, cost, start)[0]
            else:
                nav = simulate_multi(np.stack([qsyn_o, ssyn_o], axis=1),
                                     np.stack([qsyn_c, ssyn_c], axis=1), w, cost, start)[0]
            n = nav.shape[1] - 1  # == MEASURED
            acc[arm]["cagr"].append((nav[:, -1] / nav[:, 0]) ** (252 / n) - 1)
            acc[arm]["dd"].append(max_dd(nav))
        # SPY buy-and-hold total return on the same paths (no trading cost)
        spy = sc[:, start - 1 :]
        acc["SPY"]["cagr"].append((spy[:, -1] / spy[:, 0]) ** (252 / (spy.shape[1] - 1)) - 1)
        acc["SPY"]["dd"].append(max_dd(spy))
    cat = {a: {k: np.concatenate(v) for k, v in d.items()} for a, d in acc.items()}
    out = {"paths": PATHS, "seed": SEED, "mean_block": BLOCK,
           "warmup_sessions": WARM, "measured_sessions": MEASURED, "cost_multiplier_m": m}
    for a in ARMS:
        c, d, s = cat[a]["cagr"], cat[a]["dd"], cat["SPY"]["cagr"]
        out[a] = {
            "cagr_median": float(np.median(c)),
            "cagr_p5": float(np.percentile(c, 5)),
            "max_dd_median": float(np.median(d)),
            "share_beats_spy": float(np.mean(c > s)),
        }
    c, d = cat["SPY"]["cagr"], cat["SPY"]["dd"]
    out["SPY"] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                  "max_dd_median": float(np.median(d))}
    return out


# ----------------------------------------------------------------------------- output helpers
def rnd(x, nd=6):
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, nd) for v in x]
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return round(f, nd) if math.isfinite(f) else f
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.ndarray):
        return rnd(x.tolist(), nd)
    return x


def main() -> None:
    days, arr, y = load()
    T = len(days)
    q, s = arr["QQQ"], arr["SPY"]
    out: dict = {"params": {
        "arms": list(ARMS),
        "ensemble_lengths": list(ENSEMBLE_NS),
        "vol_window": VOL_N,
        "vol_target": VOL_TARGET,
        "band": BAND,
        "total_cap": CAP,
        "cost_per_side_m1": COST_PER_SIDE * 1,
        "cost_per_side_m2": COST_PER_SIDE * 2,
        "expense_qld_star": EXP_Q,
        "expense_sso_star": EXP_S,
        "bootstrap": {"paths": PATHS, "seed": SEED, "mean_block": BLOCK,
                      "warmup_sessions": WARM, "measured_sessions": MEASURED},
        "cagr_basis": "calendar days / 365.25",
        "sharpe": "daily simple returns, rf=0, sample std, x sqrt(252)",
        "turnover": "sum over rebalances of sum_legs |delta w|, annualized by calendar years",
        "execution": "decide at completed close, fill at next open; rebalance all legs when any drifts > 0.5pp; cash earns zero",
        "window_rule": "first session with 250 prior QQQ/SPY closes and 20 synthetic returns, to last common bar",
        "note_2017": "curve_stats 2017_end segment runs from the last close before 2017 (2016-12-30) to end, i.e. the 2017-01-03 -> end window",
        "note_spy": "SPY benchmark is pure total-return buy-and-hold (no entry cost), identical at m=1/m=2",
    }}
    out["data_checks"] = {"sessions": T, "first": days[0], "last": days[-1]}

    # --- harness check FIRST: C0 on the stress_robustness_v1 window/settings
    ref = json.loads(REF.read_text())["historical"]["VT35"]
    qsyn_o, qsyn_c = synthetic_2x(q[0], q[1], q[2], y, EXP_Q)
    up200 = {200: sma_up(q[1], 200)}
    vol = vol_n(qsyn_c)
    w = build_wants(up200, {200: sma_up(s[1], 200)}, vol, vol, "C0")
    hnav = simulate_multi(qsyn_o[None, :], qsyn_c[None, :], w, COST_PER_SIDE * 2, 200)[0][0]
    hst = curve_stats(days[199:], hnav)
    harness = {
        "reference": {"window": [ref["start"], ref["end"]], "cagr": ref["cagr"], "max_dd": ref["max_dd"]},
        "reproduced": {"window": [hst["start"], hst["end"]], "cagr": hst["cagr"], "max_dd": hst["max_dd"]},
        "diff_cagr_pp": (hst["cagr"] - ref["cagr"]) * 100,
        "diff_max_dd_pp": (hst["max_dd"] - ref["max_dd"]) * 100,
        "tolerance_pp": TOL * 100,
    }
    harness["pass"] = abs(hst["cagr"] - ref["cagr"]) <= TOL and abs(hst["max_dd"] - ref["max_dd"]) <= TOL
    out["harness_check"] = harness
    print(f"harness: ref CAGR {ref['cagr']*100:.4f}% DD {ref['max_dd']*100:.4f}% | "
          f"repro CAGR {hst['cagr']*100:.4f}% DD {hst['max_dd']*100:.4f}% | "
          f"diff {harness['diff_cagr_pp']:.4f}pp / {harness['diff_max_dd_pp']:.4f}pp | "
          f"{'PASS' if harness['pass'] else 'FAIL'}")
    if not harness["pass"]:
        out["status"] = "harness_failed"
        OUT.write_text(json.dumps(rnd(out), indent=2, sort_keys=True) + "\n")
        print("HARNESS FAILED - stopping before reading any arm result")
        sys.exit(1)

    # --- common window: first session with full 250-close + 20-return warm-up
    start = SMA_MAX  # signal at close start-1 uses closes [start-250, start-1] and 20 returns
    dates = days[start - 1 :]
    out["common_window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    upQ = {n: sma_up(q[1], n) for n in ENSEMBLE_NS}
    upS = {n: sma_up(s[1], n) for n in ENSEMBLE_NS}
    qsyn_o, qsyn_c = synthetic_2x(q[0], q[1], q[2], y, EXP_Q)
    ssyn_o, ssyn_c = synthetic_2x(s[0], s[1], s[2], y, EXP_S)
    volQ, volS = vol_n(qsyn_c), vol_n(ssyn_c)
    years = calendar_years(dates[0], dates[-1])

    # SPY pure total-return buy-and-hold over the common window
    spy_tr = (s[1][start:] + s[2][start:]) / s[1][start - 1 : -1]
    spy_nav = np.concatenate([[1.0], np.cumprod(spy_tr)])

    hist = {}
    for m in (1, 2):
        hm = {}
        for arm in ARMS:
            w = build_wants(upQ, upS, volQ, volS, arm)
            if arm in ("C0", "E1"):
                nav, turn, ntr = simulate_multi(qsyn_o, qsyn_c, w, COST_PER_SIDE * m, start)
            else:
                nav, turn, ntr = simulate_multi(np.stack([qsyn_o, ssyn_o]),
                                                np.stack([qsyn_c, ssyn_c]), w, COST_PER_SIDE * m, start)
            nav = nav[0]
            st = curve_stats(dates, nav)
            st["calmar"] = calmar(st["cagr"], st["max_dd"])
            st["sharpe"] = sharpe_daily(nav)
            st["turnover_annual"] = float(turn[0] / years)
            st["trades"] = int(ntr[0])
            hm[arm] = st
        sp = curve_stats(dates, spy_nav)
        sp["calmar"] = calmar(sp["cagr"], sp["max_dd"])
        sp["sharpe"] = sharpe_daily(spy_nav)
        sp["turnover_annual"] = 0.0
        sp["trades"] = 0
        hm["SPY"] = sp
        hist[f"m{m}"] = hm
    out["historical"] = hist

    # --- synthetic SSO* sanity (report only; no real SSO bars exist)
    sso_tr = ssyn_c[start:] / ssyn_c[start - 1 : -1]
    r_spy = spy_tr - 1
    r_sso = sso_tr - 1
    out["sso_sanity"] = {
        "window": [dates[1], dates[-1]],
        "corr_sso_star_vs_2x_spy": float(np.corrcoef(r_sso, 2 * r_spy)[0, 1]),
        "cagr_sso_star": cagr(1.0, ssyn_c[-1] / ssyn_c[start - 1], years),
        "cagr_spy": hist["m2"]["SPY"]["cagr"],
    }

    # --- bootstrap at both cost levels
    out["bootstrap"] = {"m1": bootstrap(days, arr, y, 1), "m2": bootstrap(days, arr, y, 2)}

    # --- gates G1..G7 (decided on raw values) and selection
    h1, h2 = hist["m1"], hist["m2"]
    b2 = out["bootstrap"]["m2"]
    gates = {}
    for arm in ("E1", "M1", "M2"):
        a2, c2 = h2[arm], h2["C0"]
        a1, c1 = h1[arm], h1["C0"]
        g = {}
        g["G1"] = {"pass": bool(a2["cagr"] >= c2["cagr"] - 0.005),
                   "arm_cagr": a2["cagr"], "c0_cagr": c2["cagr"]}
        g["G2"] = {"pass": bool(a2["max_dd"] <= c2["max_dd"]),
                   "arm_dd": a2["max_dd"], "c0_dd": c2["max_dd"]}
        g["G3"] = {"pass": bool(a2["cagr"] > c2["cagr"] or a2["calmar"] >= 1.10 * c2["calmar"]),
                   "arm_cagr": a2["cagr"], "c0_cagr": c2["cagr"],
                   "arm_calmar": a2["calmar"], "c0_calmar": c2["calmar"]}
        g["G4"] = {"pass": bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.02),
                   "arm_cagr_2017": a2["2017_end"]["cagr"], "c0_cagr_2017": c2["2017_end"]["cagr"]}
        g["G5"] = {"pass": bool(b2[arm]["cagr_median"] >= b2["C0"]["cagr_median"]
                                  and b2[arm]["share_beats_spy"] >= b2["C0"]["share_beats_spy"]),
                   "arm_med": b2[arm]["cagr_median"], "c0_med": b2["C0"]["cagr_median"],
                   "arm_share": b2[arm]["share_beats_spy"], "c0_share": b2["C0"]["share_beats_spy"]}
        g1 = bool(a1["cagr"] >= c1["cagr"] - 0.005)
        g2 = bool(a1["max_dd"] <= c1["max_dd"])
        g3 = bool(a1["cagr"] > c1["cagr"] or a1["calmar"] >= 1.10 * c1["calmar"])
        g["G6"] = {"pass": bool(g1 and g2 and g3), "m1_G1": g1, "m1_G2": g2, "m1_G3": g3}
        g["G7"] = {"pass": bool(a2["turnover_annual"] <= 2 * c2["turnover_annual"]),
                   "arm_turnover": a2["turnover_annual"], "c0_turnover": c2["turnover_annual"]}
        g["pass_all"] = all(g[k]["pass"] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
        gates[arm] = g
    out["gates"] = gates
    passing = [a for a in ("E1", "M1", "M2") if gates[a]["pass_all"]]
    sel = max(passing, key=lambda a: (h2[a]["cagr"], h2[a]["calmar"])) if passing else "none"
    out["selection"] = {"passing": passing, "selected": sel,
                        "rule": "highest full-window m=2 CAGR among passing arms (tie: higher Calmar); else none (VT35 unchanged)"}
    out["status"] = "complete"

    OUT.write_text(json.dumps(rnd(out), indent=2, sort_keys=True) + "\n")

    # --- SUMMARY.txt
    f2 = lambda x: f"{x * 100:7.2f}%"
    lines = []
    lines.append(f"trend_robustness_v2 screen | common window {dates[1]} -> {dates[-1]}")
    lines.append(f"harness C0 vs stress VT35: dCAGR {harness['diff_cagr_pp']:.4f}pp dDD {harness['diff_max_dd_pp']:.4f}pp "
                 f"(tol 0.01pp) {'PASS' if harness['pass'] else 'FAIL'}")
    lines.append("")
    lines.append("m=2 historical:")
    lines.append(f"{'arm':4s} {'CAGR':>8s} {'maxDD':>8s} {'Calmar':>7s} {'Sharpe':>7s} "
                 f"{'2017+CAGR':>9s} {'turnov':>7s} {'trades':>7s} | {'bootMed':>8s} {'p5':>8s} {'bootDD':>8s} {'>SPY':>6s}")
    for arm in ARMS + ("SPY",):
        a, b = h2[arm], b2.get(arm, {})
        boot = f"{b.get('cagr_median', float('nan'))*100:7.2f}% {b.get('cagr_p5', float('nan'))*100:7.2f}% " \
               f"{b.get('max_dd_median', float('nan'))*100:7.2f}% {b.get('share_beats_spy', float('nan'))*100:5.1f}%" \
            if b else f"{'n/a':>8s} {'n/a':>8s} {'n/a':>8s} {'n/a':>6s}"
        lines.append(f"{arm:4s} {f2(a['cagr'])} {f2(a['max_dd'])} {a['calmar']:7.2f} {a['sharpe']:7.2f} "
                     f"{f2(a['2017_end']['cagr'])} {a['turnover_annual']:7.2f} {a['trades']:7d} | {boot}")
    lines.append("")
    lines.append("m=1 key numbers (CAGR / maxDD / Calmar):")
    for arm in ARMS:
        a = h1[arm]
        lines.append(f"{arm:4s} {f2(a['cagr'])} {f2(a['max_dd'])} {a['calmar']:7.2f}")
    lines.append("")
    lines.append("gates (m=2; G6 = G1-G3 at m=1):")
    for arm in ("E1", "M1", "M2"):
        gg = gates[arm]
        lines.append(f"{arm}: " + " ".join(f"{k}={'PASS' if gg[k]['pass'] else 'FAIL'}" for k in
                                           ("G1", "G2", "G3", "G4", "G5", "G6", "G7")) +
                     f" -> {'PASS_ALL' if gg['pass_all'] else 'FAIL'}")
    lines.append(f"selected: {sel}" + ("" if sel == "none" else ""))
    if sel == "none":
        lines.append("no arm passes: VT35 stays unchanged, campaign closes")
    ss = out["sso_sanity"]
    lines.append(f"SSO* sanity ({ss['window'][0]}->{ss['window'][1]}): corr(SSO*,2xSPY)={ss['corr_sso_star_vs_2x_spy']:.4f} "
                 f"CAGR SSO*={ss['cagr_sso_star']*100:.2f}% vs SPY={ss['cagr_spy']*100:.2f}% (report only)")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
