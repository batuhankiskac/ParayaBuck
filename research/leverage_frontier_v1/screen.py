#!/usr/bin/env python3
"""Leverage frontier v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --phase1  harness check (C0 historical + C0 bootstrap must reproduce
            trend_robustness_v2 to 0.01 pp) and instrument validation
            (synthetic 2x vs QLD, synthetic 3x vs TQQQ, BOND10 vs IEF, gold
            proxy vs GLD). Simulates no arm other than C0. Writes VALIDATION.json.
  --phase2  re-runs the phase-1 checks, stops if the harness fails, then runs
            Stage 1 (25 cells, base and calibrated financing; Amendment A1)
            and Stage 2 (risk-off assets) and writes RESULTS.json and SUMMARY.txt.

Engine, signals and metrics are imported from research/trend_robustness_v2/screen.py
(its main() is never run). New here: the L-x synthetic Nasdaq instrument, the
risk-off instruments (BILLS, BOND10, GOLD), a parametric vol target, and the
multi-block bootstrap with risk-off assets resampled jointly by session.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

NEW_BARS = ROOT / "data/historical/leverage_frontier_v1_daily.csv"
DGS10_CSV = ROOT / "data/historical/leverage_frontier_v1_dgs10.csv"
TRV2_RESULTS = ROOT / "research/trend_robustness_v2/RESULTS.json"
VALIDATION = HERE / "VALIDATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
LS = (1.0, 1.5, 2.0, 2.5, 3.0)
VTS = (0.30, 0.35, 0.40, 0.50, None)  # None = no vol target (weight 1, capped); adjacent to 0.50
EXPENSE = {1.0: 0.0, 1.5: 0.0095, 2.0: 0.0095, 2.5: 0.0095, 3.0: 0.0084}  # L=1 is real QQQ (ER inside price)
C0_CELL = (2.0, 0.35)
SMA_N, VOL_N, CAP, BAND = 200, 20, 0.98, 0.005
COST_PER_SIDE = 4.0 / 10_000
BILL_FEE = 0.0010  # BILLS: DTB3 - 0.10%/yr (stress_robustness_v1 BIL convention)
BOND_ER = 0.0015  # BOND10: par-bond TR - 0.15%/yr (IEF-class ER)
GLD_ER = 0.0040  # pre-GLD gold proxy: GC=F price return - 0.40%/yr
SEED, PATHS, CHUNK = 20261011, 5000, 500
BLOCKS = (20, 60, 250)
GATE_BLOCK = 20
WARM, MEASURED = 250, 2520
START = 250  # first measured session index = trend_robustness_v2 common window (2000-03-06)
TOL = 1e-4  # 0.01 pp
SUB_PERIODS = (("p2000_2008", None, "2008-12-31"), ("p2009_2016", "2008-12-31", "2016-12-31"),
               ("p2017_end", "2016-12-31", None))
RISK_OFF = ("BILLS", "BOND10", "GOLD")
# Calibrated financing model (Amendment A1; fixed from the phase-1 validation: real QLD / TQQQ lag
# the DTB3-financed synthetic by ~0.69 / ~1.65 pp/yr, i.e. ~0.7-0.8 pp per unit of (L-1)).
FIN_SPREAD = 0.0075


# ----------------------------------------------------------------------------- data
def load_all():
    days, arr, y = trv2.load()  # QQQ/SPY/QLD [open, close, dividend] on QQQ∩SPY days; DTB3 (decimal, ffilled)
    T = len(days)
    pos = {d: i for i, d in enumerate(days)}
    new = defaultdict(dict)
    with NEW_BARS.open() as fh:
        for r in csv.DictReader(fh):
            new[r["symbol"]][r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    for s in ("TQQQ", "IEF", "GLD", "GC=F"):
        a = np.full((3, T), np.nan)
        for d, v in new[s].items():
            if d in pos:
                a[:, pos[d]] = v
        arr[s] = a
    arr["GC_first_date"] = min(new["GC=F"])
    arr["GC_unmatched_dates"] = sorted(d for d in new["GC=F"] if d not in pos and d >= days[0])
    raw10 = {}
    with DGS10_CSV.open() as fh:
        for r in csv.DictReader(fh):
            raw10[r["observation_date"]] = r["DGS10"]
    y10, last = np.full(T, np.nan), None
    for d in sorted(set(raw10) | set(days)):
        v = raw10.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in pos:
            y10[pos[d]] = last
    return days, arr, y, y10


# ----------------------------------------------------------------------------- instruments
def synthetic_L(q_open, q_close, q_div, y, L, expense, spread=0.0):
    """Daily-reset L x total return - expense - (L-1) x DTB3 financing; 1-D or [P, T] (time last).

    L=2 is bit-identical to trend_robustness_v2.synthetic_2x; L=1 with expense 0
    is QQQ's own total return (real QQQ, no synthetic leverage). `spread` (the calibrated
    financing model, Amendment A1) adds (L-1) x spread to the financing rate."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    fin = y[..., :-1] if spread == 0.0 else y[..., :-1] + spread
    g_cc = 1 + L * r_cc - (expense + (L - 1) * fin) / 252
    one = np.ones(q_close.shape[:-1] + (1,))
    close = np.concatenate([one, np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([one, close[..., :-1] * (1 + L * r_on)], axis=-1)
    return opn, close


def levels(gross_on, gross_id):
    """Open/close level arrays from per-session gross overnight/intraday factors (index 0 unused)."""
    g = gross_on * gross_id
    g = np.where(np.isnan(g), 1.0, g)
    close = np.concatenate([np.ones(g.shape[:-1] + (1,)), np.cumprod(g[..., 1:], axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(g.shape[:-1] + (1,)), close[..., :-1] * np.where(np.isnan(gross_on[..., 1:]), 1.0, gross_on[..., 1:])], axis=-1)
    return opn, close


def bills_gross(y):
    """Per-session gross return of BILLS: 1 + (DTB3[t-1] - fee)/252; all intraday (open = prev close)."""
    g = np.full(y.shape, np.nan)
    g[..., 1:] = 1 + (y[..., :-1] - BILL_FEE) / 252
    return g


def par_bond_dc(yv):
    """Modified duration and convexity (years, years^2) of a 10y semiannual par bond at yield yv."""
    yv = np.asarray(yv, dtype=float)
    k = np.arange(1, 21)[:, None]
    v = 1 + yv[None, :] / 2
    cf = np.where(k == 20, 1 + yv[None, :] / 2, yv[None, :] / 2)  # per 1 face; price = 1 at par
    d1 = (k / 2 * cf / v ** (k + 1)).sum(axis=0)  # -dP/dy
    d2 = (k * (k + 1) / 4 * cf / v ** (k + 2)).sum(axis=0)  # d2P/dy2
    return d1, d2


def bond10_gross(y10):
    """BOND10 per-session gross return: carry + duration x dy + convexity, minus 0.15%/yr; index 0 NaN."""
    y0, y1 = y10[:-1], y10[1:]
    dy = y1 - y0
    D, C = par_bond_dc(y0)
    r = y0 / 252 - D * dy + 0.5 * C * dy * dy - BOND_ER / 252
    return np.concatenate([[np.nan], 1 + r])


def gold_factors(days, arr):
    """GOLD per-session (overnight, intraday) gross factors.

    Sessions with real GLD bars on t-1 and t: GLD open/close (no dividends).
    Earlier sessions (and the splice session): GC=F close-to-close x (1 - 0.40%/252),
    all intraday (overnight factor 1); a GC=F bar missing on an equity day is
    carried forward (factor 1). NaN before the first GC=F bar + 1 session."""
    T = len(days)
    gld, gc = arr["GLD"], arr["GC=F"]
    gcc = gc[1].copy()
    last = np.nan
    for i in range(T):  # forward-fill GC=F closes onto equity days
        if not np.isnan(gcc[i]):
            last = gcc[i]
        gcc[i] = last
    on = np.full(T, np.nan)
    idr = np.full(T, np.nan)
    src = np.array([""] * T, dtype=object)
    for t in range(1, T):
        if not np.isnan(gld[1][t]) and not np.isnan(gld[1][t - 1]):
            on[t] = gld[0][t] / gld[1][t - 1]
            idr[t] = gld[1][t] / gld[0][t]
            src[t] = "GLD"
        elif not np.isnan(gcc[t - 1]) and not np.isnan(gcc[t]):
            on[t] = 1.0
            idr[t] = gcc[t] / gcc[t - 1] * (1 - GLD_ER / 252)
            src[t] = "GC=F"
    return on, idr, src


def gold_start(on):
    """First session index with a GOLD return (window/pool start for GOLD arms and their bases)."""
    return int(np.argmax(~np.isnan(on)))


# ----------------------------------------------------------------------------- weights
def vt_weight(vol, target):
    """min(1, target/vol) with trend_robustness_v2's edge convention (vol<=0 or NaN -> 1); None -> 1."""
    v = np.asarray(vol)
    if target is None:
        return np.ones_like(v)
    return np.where(v > 0, np.minimum(1.0, target / np.where(v > 0, v, 1.0)), 1.0)


def wants_for(up, vol, vt, risk_off):
    """[P, legs, T] targets. Leg 0: L-instrument at up x min(CAP, vt(vol)); leg 1 (if risk_off):
    the risk-off asset at (1 - up) x CAP. The unused remainder in the trend state stays zero cash."""
    up = np.atleast_2d(up).astype(float)
    eq = up * np.minimum(CAP, vt_weight(np.atleast_2d(vol), vt))
    if risk_off is None:
        return eq[:, None, :]
    return np.stack([eq, (1 - up) * CAP], axis=1)


# ----------------------------------------------------------------------------- arms
def arm_name(L, vt, risk_off=None):
    v = "none" if vt is None else f"{vt:.2f}"
    return f"L{L:.1f}_VT{v}" + (f"+{risk_off}" if risk_off else "")


def stage1_cells():
    return [(L, vt) for L in LS for vt in VTS]


def neighbours(cell):
    L, vt = cell
    li, vi = LS.index(L), VTS.index(vt)
    out = []
    for dl in (-1, 1):
        if 0 <= li + dl < len(LS):
            out.append((LS[li + dl], vt))
    for dv in (-1, 1):
        if 0 <= vi + dv < len(VTS):
            out.append((L, VTS[vi + dv]))
    return out


# ----------------------------------------------------------------------------- historical runs
def hist_instruments(days, arr, y, y10, spread=0.0):
    q = arr["QQQ"]
    inst = {L: synthetic_L(q[0], q[1], q[2], y, L, EXPENSE[L], spread) for L in LS}
    ro = {}
    b = bills_gross(y)
    ro["BILLS"] = levels(np.where(np.isnan(b), np.nan, 1.0), b)
    g = bond10_gross(y10)
    ro["BOND10"] = levels(np.where(np.isnan(g), np.nan, 1.0), g)
    on, idr, _ = gold_factors(days, arr)
    ro["GOLD"] = levels(on, idr)
    return inst, ro


def seg_cagr(dates, nav, a, b):
    """CAGR from the last close <= a (or the first bar) to the last close <= b (or the end)."""
    ia = 0 if a is None or dates[0] > a else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    return float(trv2.cagr(nav[ia], nav[ib], trv2.calendar_years(dates[ia], dates[ib])))


def full_stats(dates, nav, turn, ntr):
    nav = np.asarray(nav)
    st = trv2.curve_stats(dates, nav)
    years = trv2.calendar_years(dates[0], dates[-1])
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    cnt = defaultdict(int)
    for d in dates[1:]:
        cnt[d[:4]] += 1
    full = {k: v for k, v in st["years"].items() if cnt[k] >= 240}
    wy = min(full, key=full.get)
    st["worst_calendar_year"] = {"year": wy, "return": full[wy]}
    for name, a, b in SUB_PERIODS:
        st[name] = {"cagr": seg_cagr(dates, nav, a, b)}
    k0 = max(k for k, d in enumerate(dates) if d <= "2021-12-31")
    k1 = max(k for k, d in enumerate(dates) if d <= "2022-12-31")
    st["2022"] = {"total_return": float(nav[k1] / nav[k0] - 1), "max_dd": float(trv2.max_dd(nav[k0:k1 + 1])[0])}
    return st


def run_hist(days, inst, ro, up, vols, L, vt, risk_off, m, start):
    o, c = inst[L]
    if risk_off is None:
        w = wants_for(up, vols[L], vt, None)
        nav, turn, ntr = trv2.simulate_multi(o[None, :], c[None, :], w, COST_PER_SIDE * m, start)
    else:
        w = wants_for(up, vols[L], vt, risk_off)
        ro_o, ro_c = ro[risk_off]
        nav, turn, ntr = trv2.simulate_multi(np.stack([o, ro_o]), np.stack([c, ro_c]), w, COST_PER_SIDE * m, start)
    dates = days[start - 1:]
    return full_stats(dates, nav[0], turn[0], ntr[0])


# ----------------------------------------------------------------------------- bootstrap
def boot_paths(npool, block, rng, P):
    """Stationary block bootstrap positions; same draw order as trend_robustness_v2.bootstrap."""
    L = WARM + MEASURED
    pos = np.empty((P, L), dtype=np.int64)
    pos[:, 0] = rng.integers(0, npool, P)
    jump = rng.random((P, L)) < 1 / block
    fresh = rng.integers(0, npool, (P, L))
    for t in range(1, L):
        pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
    return pos


def bootstrap(arr, y, ro_hist_factors, arms, pool_start, block, m, spread=0.0):
    """Run `arms` [(L, vt, risk_off)] on 5,000 paths of sessions resampled from [pool_start, T).

    Whole sessions are resampled jointly: QQQ/SPY overnight+intraday returns and
    dividends, the previous DTB3, the BOND10 and GOLD session factors. Seed and draw
    order are fixed (SEED, CHUNK), so every arm on the same (pool, block) sees the
    same paths. Returns per-arm distribution statistics of 10-year (2,520-session) outcomes."""
    T = arr["QQQ"].shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(pool_start, T)
    npool = len(pool)
    cost = COST_PER_SIDE * m
    start = trv2.SMA_MAX + 1
    names = [arm_name(*a) for a in arms]
    acc = {n: {"cagr": [], "dd": [], "beat": []} for n in names}
    spy_acc = {"cagr": [], "dd": []}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = boot_paths(npool, block, rng, P)
        idx = pool[pos]
        yy = y[idx - 1]

        def leg(sym):
            prev = arr[sym][1][idx - 1]
            r_on = (arr[sym][0][idx] + arr[sym][2][idx]) / prev
            r_id = arr[sym][1][idx] / arr[sym][0][idx]
            c = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
            o = np.concatenate([np.ones((P, 1)), c[:, :-1] * r_on], axis=1)
            return o, c

        qo, qc = leg("QQQ")
        so, sc = leg("SPY")
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        up = trv2.sma_up(qc, SMA_N)
        need_L = sorted({a[0] for a in arms})
        inst, vols = {}, {}
        for L in need_L:
            inst[L] = synthetic_L(qo, qc, np.zeros_like(qc), yyp, L, EXPENSE[L], spread)
            vols[L] = trv2.vol_n(inst[L][1], VOL_N)
        ro = {}
        for name in {a[2] for a in arms if a[2]}:
            if name == "BILLS":
                g_on = np.ones((P, pos.shape[1]))
                g_id = 1 + (yy - BILL_FEE) / 252
            else:
                h_on, h_id = ro_hist_factors[name]
                g_on, g_id = h_on[idx], h_id[idx]
            pad = np.full((P, 1), np.nan)
            ro[name] = levels(np.concatenate([pad, g_on], axis=1), np.concatenate([pad, g_id], axis=1))
        for (L, vt, roff), n in zip(arms, names):
            w = wants_for(up, vols[L], vt, roff)
            if roff is None:
                nav = trv2.simulate_multi(inst[L][0][:, None, :], inst[L][1][:, None, :], w, cost, start)[0]
            else:
                nav = trv2.simulate_multi(np.stack([inst[L][0], ro[roff][0]], axis=1),
                                          np.stack([inst[L][1], ro[roff][1]], axis=1), w, cost, start)[0]
            k = nav.shape[1] - 1
            acc[n]["cagr"].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
            acc[n]["dd"].append(trv2.max_dd(nav))
        spy = sc[:, start - 1:]
        spy_acc["cagr"].append((spy[:, -1] / spy[:, 0]) ** (252 / (spy.shape[1] - 1)) - 1)
        spy_acc["dd"].append(trv2.max_dd(spy))
    s_c = np.concatenate(spy_acc["cagr"])
    out = {"paths": PATHS, "seed": SEED, "mean_block": block, "pool_start_index": int(pool_start),
           "warmup_sessions": WARM, "measured_sessions": MEASURED, "cost_multiplier_m": m}
    for n in names:
        c, d = np.concatenate(acc[n]["cagr"]), np.concatenate(acc[n]["dd"])
        out[n] = {
            "cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
            "cagr_p25": float(np.percentile(c, 25)), "cagr_p75": float(np.percentile(c, 75)),
            "cagr_p95": float(np.percentile(c, 95)), "p_cagr_below_0": float(np.mean(c < 0)),
            "max_dd_median": float(np.median(d)), "p_dd_gt_50": float(np.mean(d > 0.50)),
            "p_dd_gt_70": float(np.mean(d > 0.70)), "share_beats_spy": float(np.mean(c > s_c)),
        }
    sd = np.concatenate(spy_acc["dd"])
    out["SPY"] = {"cagr_median": float(np.median(s_c)), "cagr_p5": float(np.percentile(s_c, 5)),
                  "max_dd_median": float(np.median(sd))}
    return out


# ----------------------------------------------------------------------------- phase 1: harness + validation
def tr_returns(a, k0, k1):
    """Real-ETF total-return gross factors close-to-close for sessions k0+1..k1."""
    return (a[1][k0 + 1:k1 + 1] + a[2][k0 + 1:k1 + 1]) / a[1][k0:k1]


def compare(days, k0, k1, syn_g, real_g, label):
    """Tracking statistics of two per-session gross-return series over sessions k0+1..k1."""
    yrs = trv2.calendar_years(days[k0], days[k1])
    rs, rr = syn_g - 1, real_g - 1
    diff = rs - rr
    by_year = {}
    dd = days[k0 + 1:k1 + 1]
    for yv in sorted({d[:4] for d in dd}):
        msk = np.array([d[:4] == yv for d in dd])
        s_, r_ = float(syn_g[msk].prod() - 1), float(real_g[msk].prod() - 1)
        by_year[yv] = {"synthetic": s_, "real": r_, "diff_pp": (s_ - r_) * 100}
    full_years = [v["diff_pp"] for k, v in by_year.items() if k not in (dd[0][:4], dd[-1][:4])]
    beta = float(np.cov(rr, rs)[0, 1] / np.var(rs, ddof=1))
    return {
        "label": label, "window": [days[k0], days[k1]], "sessions": int(k1 - k0),
        "corr_daily": float(np.corrcoef(rs, rr)[0, 1]),
        "tracking_error_ann": float(diff.std(ddof=1) * math.sqrt(252)),
        "mean_diff_ann_pp": float(diff.mean() * 252 * 100),
        "cagr_synthetic": float(trv2.cagr(1, syn_g.prod(), yrs)),
        "cagr_real": float(trv2.cagr(1, real_g.prod(), yrs)),
        "cagr_diff_pp": float((trv2.cagr(1, syn_g.prod(), yrs) - trv2.cagr(1, real_g.prod(), yrs)) * 100),
        "vol_synthetic": float(rs.std(ddof=1) * math.sqrt(252)),
        "vol_real": float(rr.std(ddof=1) * math.sqrt(252)),
        "beta_real_on_synthetic": beta,
        "annual_diff_pp_full_years": {"mean": float(np.mean(full_years)), "median": float(np.median(full_years)),
                                      "min": float(np.min(full_years)), "max": float(np.max(full_years)),
                                      "abs_mean": float(np.mean(np.abs(full_years)))},
        "by_year": by_year,
    }


def phase1(days, arr, y, y10):
    out = {}
    ref = json.loads(TRV2_RESULTS.read_text())
    q = arr["QQQ"]
    # --- generalized instrument equals the precedent's synthetic_2x bit for bit
    o2, c2 = synthetic_L(q[0], q[1], q[2], y, 2.0, EXPENSE[2.0])
    p_o, p_c = trv2.synthetic_2x(q[0], q[1], q[2], y, trv2.EXP_Q)
    o1, c1 = synthetic_L(q[0], q[1], q[2], y, 1.0, 0.0)
    qtr = np.concatenate([[1.0], np.cumprod((q[1][1:] + q[2][1:]) / q[1][:-1])])
    out["instrument_identity"] = {
        "L2_equals_trv2_synthetic_2x": bool(np.array_equal(o2, p_o) and np.array_equal(c2, p_c)),
        "L1_close_max_rel_diff_vs_qqq_tr": float(np.max(np.abs(c1 / qtr - 1))),
    }
    # --- harness: C0 historical on the common window (m=1, m=2)
    inst = {2.0: (o2, c2)}
    up = trv2.sma_up(q[1], SMA_N)[0]
    vols = {2.0: trv2.vol_n(c2, VOL_N)[0]}
    h = {}
    ok = out["instrument_identity"]["L2_equals_trv2_synthetic_2x"]
    for m in (1, 2):
        st = run_hist(days, inst, {}, up, vols, 2.0, 0.35, None, m, START)
        r = ref["historical"][f"m{m}"]["C0"]
        d_c, d_d = (st["cagr"] - r["cagr"]) * 100, (st["max_dd"] - r["max_dd"]) * 100
        h[f"m{m}"] = {"window": [st["start"], st["end"]], "cagr": st["cagr"], "max_dd": st["max_dd"],
                      "ref_cagr": r["cagr"], "ref_max_dd": r["max_dd"], "diff_cagr_pp": d_c, "diff_max_dd_pp": d_d}
        ok = ok and abs(d_c) <= TOL * 100 and abs(d_d) <= TOL * 100
    # --- harness: C0 bootstrap (block 20, full pool) reproduces trend_robustness_v2's paths/results
    for m in (1, 2):
        b = bootstrap(arr, y, {}, [(2.0, 0.35, None)], 1, GATE_BLOCK, m)[arm_name(2.0, 0.35)]
        r = ref["bootstrap"][f"m{m}"]["C0"]
        diffs = {k: (b[k] - r[k]) * 100 for k in ("cagr_median", "cagr_p5", "max_dd_median")}
        h[f"bootstrap_m{m}"] = {"repro": {k: b[k] for k in diffs}, "ref": {k: r[k] for k in diffs}, "diff_pp": diffs}
        ok = ok and all(abs(v) <= TOL * 100 for v in diffs.values())
    h["tolerance_pp"] = TOL * 100
    h["pass"] = bool(ok)
    out["harness_check"] = h
    if not ok:
        return out

    # --- instrument validation (data checks; no arm simulated)
    val = {}
    qld = arr["QLD"]
    k0 = int(np.argmax(~np.isnan(qld[1])))
    k1 = len(days) - 1
    val["syn2x_vs_QLD"] = compare(days, k0, k1, c2[k0 + 1:] / c2[k0:-1], tr_returns(qld, k0, k1), "synthetic L=2 (0.95%) vs real QLD")
    o3, c3 = synthetic_L(q[0], q[1], q[2], y, 3.0, EXPENSE[3.0])
    tq = arr["TQQQ"]
    k0 = int(np.argmax(~np.isnan(tq[1])))
    val["syn3x_vs_TQQQ"] = compare(days, k0, k1, c3[k0 + 1:] / c3[k0:-1], tr_returns(tq, k0, k1), "synthetic L=3 (0.84%) vs real TQQQ")
    # implied all-in annual drag of the real fund relative to gross L x QQQ - (L-1) x DTB3 (report only)
    for key, L, a in (("syn2x_vs_QLD", 2.0, qld), ("syn3x_vs_TQQQ", 3.0, tq)):
        val[key]["implied_expense_pp_from_mean_diff"] = EXPENSE[L] * 100 + val[key]["mean_diff_ann_pp"]
    bg = bond10_gross(y10)
    ief = arr["IEF"]
    k0 = int(np.argmax(~np.isnan(ief[1])))
    val["BOND10_vs_IEF"] = compare(days, k0, k1, bg[k0 + 1:], tr_returns(ief, k0, k1), "synthetic 10y CMT par bond (-0.15%) vs real IEF")
    val["BOND10_vs_IEF"]["note"] = ("IEF holds 7-10y Treasuries (modified duration ~7-7.5) vs the 10y par bond's ~8-9; "
                                    "beta_real_on_synthetic < 1 measures that duration gap")
    on, idr, src = gold_factors(days, arr)
    gs = gold_start(on)
    gld = arr["GLD"]
    k0 = int(np.argmax(~np.isnan(gld[1])))
    gc = arr["GC=F"]
    # GC=F proxy vs GLD: GC=F closes forward-filled onto equity days (as gold_factors does pre-GLD)
    gcc = gc[1]
    msk = ~np.isnan(gcc[k0:k1]) & ~np.isnan(gcc[k0 + 1:k1 + 1])
    ff = gcc.copy()
    last = np.nan
    for i in range(len(ff)):
        if not np.isnan(ff[i]):
            last = ff[i]
        ff[i] = last
    proxy_ff = ff[k0 + 1:k1 + 1] / ff[k0:k1] * (1 - GLD_ER / 252)
    gld_g = tr_returns(gld, k0, k1)
    val["GOLDproxy_vs_GLD"] = compare(days, k0, k1, proxy_ff, gld_g, "GC=F close-to-close (ffilled, -0.40%) vs real GLD")
    val["GOLDproxy_vs_GLD"]["sessions_with_GC_missing_on_equity_day"] = int((~msk).sum())
    val["GOLD_series"] = {
        "first_session_with_return": days[gs], "first_GC_bar": arr["GC_first_date"],
        "sessions_from_GC=F": int(np.sum(src == "GC=F")), "sessions_from_GLD": int(np.sum(src == "GLD")),
        "GC_bars_not_on_equity_days": len(arr["GC_unmatched_dates"]),
    }
    nb = int(np.sum(np.isnan(y10[START - 1:])))
    val["BOND10_series"] = {"first_day_yield": days[0], "missing_yield_in_window_after_ffill": nb,
                            "duration_at_4pct": float(par_bond_dc([0.04])[0][0]),
                            "convexity_at_4pct": float(par_bond_dc([0.04])[1][0])}
    out["validation"] = val
    out["windows"] = {"full": {"start_index": START, "first_session": days[START], "end": days[-1]},
                      "gold": {"start_index": int(gs + 0), "first_session": days[gs], "end": days[-1]}}
    return out


# ----------------------------------------------------------------------------- phase 2
def gate_stage1(h1, h2, b):
    c0 = arm_name(*C0_CELL)
    gates = {}
    for cell in stage1_cells():
        n = arm_name(*cell)
        if n == c0:
            continue
        a2, k2, a1, k1, ba, bk = h2[n], h2[c0], h1[n], h1[c0], b[n], b[c0]
        g = {}
        g["G1"] = {"pass": bool(a2["cagr"] >= k2["cagr"] + 0.010), "cell": a2["cagr"], "c0": k2["cagr"]}
        g["G2"] = {"pass": bool(ba["cagr_median"] >= bk["cagr_median"] and ba["cagr_p5"] >= bk["cagr_p5"] - 0.010),
                   "cell_med": ba["cagr_median"], "c0_med": bk["cagr_median"], "cell_p5": ba["cagr_p5"], "c0_p5": bk["cagr_p5"]}
        g["G3"] = {"pass": bool(a2["max_dd"] <= 0.65 and ba["p_dd_gt_70"] <= bk["p_dd_gt_70"] + 0.05),
                   "cell_dd": a2["max_dd"], "cell_p_dd70": ba["p_dd_gt_70"], "c0_p_dd70": bk["p_dd_gt_70"]}
        g["G4"] = {"pass": bool(a2["2017_end"]["cagr"] >= k2["2017_end"]["cagr"] - 0.020),
                   "cell": a2["2017_end"]["cagr"], "c0": k2["2017_end"]["cagr"]}
        wins = sum(a2[p]["cagr"] >= k2[p]["cagr"] for p, _, _ in SUB_PERIODS)
        g["G5"] = {"pass": bool(wins >= 2), "subperiods_won": int(wins)}
        g["G6"] = {"pass": bool(a1["cagr"] >= k1["cagr"] + 0.010), "cell_m1": a1["cagr"], "c0_m1": k1["cagr"]}
        nb = [arm_name(*x) for x in neighbours(cell)]
        g["G7"] = {"pass": bool(all(h2[x]["cagr"] >= k2["cagr"] for x in nb)),
                   "neighbours": {x: h2[x]["cagr"] for x in nb}}
        g["eligible"] = all(g[k]["pass"] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
        gates[n] = g
    elig = [n for n in gates if gates[n]["eligible"]]
    sel = max(elig, key=lambda n: (b[n]["cagr_median"], b[n]["cagr_p5"])) if elig else None
    return gates, elig, sel


def gate_stage2(arm, base, h, b):
    """h[m][name], b[m][name] on the arm's own window/pool. Conditions at m=2 and again at m=1."""
    res = {}
    for m in ("m2", "m1"):
        a, k = h[m][arm], h[m][base]
        wins = sum(a[p]["cagr"] >= k[p]["cagr"] for p, _, _ in SUB_PERIODS)
        c = {
            "cagr": bool(a["cagr"] >= k["cagr"] + 0.003),
            "max_dd": bool(a["max_dd"] <= k["max_dd"] + 0.020),
            "boot_median": bool(b[m][arm]["cagr_median"] >= b[m][base]["cagr_median"]),
            "subperiods": bool(wins >= 2),
        }
        res[m] = {"conditions": c, "pass": all(c.values()), "subperiods_won": int(wins),
                  "arm_cagr": a["cagr"], "base_cagr": k["cagr"], "arm_dd": a["max_dd"], "base_dd": k["max_dd"],
                  "arm_boot_med": b[m][arm]["cagr_median"], "base_boot_med": b[m][base]["cagr_median"]}
    res["pass"] = res["m2"]["pass"] and res["m1"]["pass"]
    res["excess_boot_median_m2"] = b["m2"][arm]["cagr_median"] - b["m2"][base]["cagr_median"]
    return res


MODELS = (("base", 0.0), ("calibrated", FIN_SPREAD))  # Amendment A1


def phase2(days, arr, y, y10, p1):
    out = {"phase1": p1, "amendment": "A1: Stage-1 gates and selection under base AND calibrated financing"}
    q = arr["QQQ"]
    up = trv2.sma_up(q[1], SMA_N)[0]
    gs = p1["windows"]["gold"]["start_index"]
    gold_on, gold_id, _ = gold_factors(days, arr)
    bg = bond10_gross(y10)
    ro_f = {"BOND10": (np.ones_like(bg), bg), "GOLD": (gold_on, gold_id)}
    inst, vols, ro = {}, {}, None
    for model, spread in MODELS:
        inst[model], ro = hist_instruments(days, arr, y, y10, spread)
        vols[model] = {L: trv2.vol_n(inst[model][L][1], VOL_N)[0] for L in LS}

    # ---------------- Stage 1 under both financing models (like-for-like within each model)
    arms1 = [(L, vt, None) for L, vt in stage1_cells()]
    s1 = {}
    for model, spread in MODELS:
        h = {f"m{m}": {arm_name(L, vt): run_hist(days, inst[model], ro, up, vols[model], L, vt, None, m, START)
                       for L, vt in stage1_cells()} for m in (1, 2)}
        b = {blk: bootstrap(arr, y, ro_f, arms1, 1, blk, 2, spread) for blk in BLOCKS}
        gates, elig, _ = gate_stage1(h["m1"], h["m2"], b[GATE_BLOCK])
        s1[model] = {"spread_per_unit_L_minus_1": spread, "historical": h,
                     "bootstrap_m2": {str(k): v for k, v in b.items()}, "gates": gates, "eligible": elig}
    elig = [n for n in s1["base"]["eligible"] if n in s1["calibrated"]["eligible"]]
    bc = s1["calibrated"]["bootstrap_m2"][str(GATE_BLOCK)]
    sel = max(elig, key=lambda n: (bc[n]["cagr_median"], bc[n]["cagr_p5"])) if elig else None
    out["stage1"] = {"models": s1, "eligible_both": elig, "selected": sel,
                     "rule": "eligible = all of G1-G7 under base AND under calibrated financing; selection = "
                             "highest calibrated block-20 m=2 bootstrap median (tie: calibrated p5)"}

    # ---------------- Stage 2: gating model = calibrated for a selected cell with L > 2, else base;
    # the other model is computed and reported only.
    bases = [C0_CELL] + ([next(c for c in stage1_cells() if arm_name(*c) == sel)] if sel else [])
    s2 = {}
    for cell in bases:
        bname = arm_name(*cell)
        gating = "calibrated" if (cell != C0_CELL and cell[0] > 2.0) else "base"
        for roff in RISK_OFF:
            # GOLD: window and bootstrap pool start at gs, the first session with a GOLD return
            # (its previous close is the first GC=F bar); BILLS/BOND10: the full window and pool.
            start, pool = (max(gs, START), gs) if roff == "GOLD" else (START, 1)
            arm = arm_name(*cell, roff)
            per_model = {}
            for model, spread in MODELS:
                hh = {f"m{m}": {bname: run_hist(days, inst[model], ro, up, vols[model], cell[0], cell[1], None, m, start),
                                arm: run_hist(days, inst[model], ro, up, vols[model], cell[0], cell[1], roff, m, start)}
                      for m in (1, 2)}
                bb = {}
                for m in (1, 2):
                    for blk in (BLOCKS if m == 2 else (GATE_BLOCK,)):
                        bb[(m, blk)] = bootstrap(arr, y, ro_f, [(*cell, None), (*cell, roff)], pool, blk, m, spread)
                bgate = {"m2": bb[(2, GATE_BLOCK)], "m1": bb[(1, GATE_BLOCK)]}
                per_model[model] = {"historical": hh, "bootstrap": {f"m{m}_b{blk}": v for (m, blk), v in bb.items()},
                                    "gate": gate_stage2(arm, bname, hh, bgate)}
            s2[arm] = {"base": bname, "risk_off": roff, "window": [days[start], days[-1]], "pool_start": days[pool],
                       "gating_model": gating, "models": per_model, "gate": per_model[gating]["gate"]}
    out["stage2"] = s2

    # ---------------- decision (no promotion)
    def best_ro(cell):
        bname = arm_name(*cell)
        passing = [a for a, v in s2.items() if v["base"] == bname and v["gate"]["pass"]]
        return max(passing, key=lambda a: s2[a]["gate"]["excess_boot_median_m2"]) if passing else None

    if sel:
        cand_cell = next(c for c in stage1_cells() if arm_name(*c) == sel)
        cand = best_ro(cand_cell) or sel
    else:
        cand = best_ro(C0_CELL)
    out["decision"] = {"candidate": cand or "none",
                       "rule": "selected Stage-1 cell (with its best passing risk-off asset, if any); else C0's best "
                               "passing risk-off asset; else none (VT35 unchanged). No promotion in this campaign."}
    out["status"] = "complete"
    return out


def write_summary(res):
    f = lambda x: f"{x * 100:6.2f}%"
    s1 = res["stage1"]
    w = res["phase1"]["windows"]
    lines = [f"leverage_frontier_v1 | full window {w['full']['first_session']} -> {w['full']['end']} | "
             f"gold window from {w['gold']['first_session']}", ""]
    for model in ("base", "calibrated"):
        sm = s1["models"][model]
        lines.append(f"Stage 1, {model} financing (+{sm['spread_per_unit_L_minus_1'] * 100:.2f}% x (L-1)), m=2:")
        lines.append(f"{'cell':14s} {'CAGR':>7s} {'maxDD':>7s} {'2017+':>7s} {'bMed20':>7s} {'p5':>7s} "
                     f"{'P(DD>70)':>8s} {'CAGR_m1':>7s} gates")
        for L, vt in stage1_cells():
            n = arm_name(L, vt)
            a, bb, a1 = sm["historical"]["m2"][n], sm["bootstrap_m2"][str(GATE_BLOCK)][n], sm["historical"]["m1"][n]
            if (L, vt) == C0_CELL:
                g = "C0"
            else:
                gg = sm["gates"][n]
                g = "".join(k[1] if gg[k]["pass"] else "." for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
                g += " ELIGIBLE" if gg["eligible"] else ""
            lines.append(f"{n:14s} {f(a['cagr'])} {f(a['max_dd'])} {f(a['2017_end']['cagr'])} {f(bb['cagr_median'])} "
                         f"{f(bb['cagr_p5'])} {f(bb['p_dd_gt_70']):>8s} {f(a1['cagr'])} {g}")
        lines.append(f"eligible ({model}): {sm['eligible']}")
        lines.append("")
    lines.append(f"eligible under both: {s1['eligible_both']} | selected: {s1['selected']}")
    lines.append("")
    lines.append("Stage 2 (gating model, m=2): arm | window | CAGR arm/base | DD arm/base | bootMed arm/base | m2/m1 | pass")
    for a, v in res["stage2"].items():
        g = v["gate"]
        g2 = g["m2"]
        lines.append(f"{a:22s} {v['gating_model']:10s} {v['window'][0]} {f(g2['arm_cagr'])}/{f(g2['base_cagr'])} "
                     f"{f(g2['arm_dd'])}/{f(g2['base_dd'])} {f(g2['arm_boot_med'])}/{f(g2['base_boot_med'])} "
                     f"{'P' if g['m2']['pass'] else 'f'}/{'P' if g['m1']['pass'] else 'f'} "
                     f"{'PASS' if g['pass'] else 'fail'}")
        other = "calibrated" if v["gating_model"] == "base" else "base"
        o = v["models"][other]["gate"]
        lines.append(f"{'':22s} ({other}, report-only: CAGR {f(o['m2']['arm_cagr'])}/{f(o['m2']['base_cagr'])}, "
                     f"{'pass' if o['pass'] else 'fail'})")
    lines.append(f"decision candidate: {res['decision']['candidate']}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--phase1"
    if mode not in ("--phase1", "--phase2"):
        raise SystemExit("usage: screen.py --phase1 | --phase2")
    days, arr, y, y10 = load_all()
    p1 = phase1(days, arr, y, y10)
    if mode == "--phase1":
        VALIDATION.write_text(json.dumps(trv2.rnd(p1), indent=2, sort_keys=True) + "\n")
    h = p1["harness_check"]
    print(json.dumps(trv2.rnd({k: v for k, v in h.items()}), indent=1))
    if not h["pass"]:
        print("HARNESS FAILED - stopping before any arm is simulated")
        sys.exit(1)
    if mode == "--phase1":
        return
    res = phase2(days, arr, y, y10, p1)
    OUT.write_text(json.dumps(trv2.rnd(res), indent=2, sort_keys=True) + "\n")
    write_summary(res)


if __name__ == "__main__":
    main()
