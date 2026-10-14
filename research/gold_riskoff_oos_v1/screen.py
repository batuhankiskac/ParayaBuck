#!/usr/bin/env python3
"""Gold risk-off out-of-sample screen, 1975-1998 (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --validate  data validation only (coverage, gaps, flat opens, gold vs World Bank Pink
              Sheet monthly London fix, instrument code identity). Simulates no arm.
              Writes VALIDATION.json.
  --run       re-runs the validation, stops if the gold check fails (> 2% mean absolute
              monthly difference), then runs CASH / GOLD / BILLS on P (^IXIC), S1 (^NDX)
              and S2 (^GSPC), evaluates H1-H5 and writes RESULTS.json and SUMMARY.txt.

Engine, signal and metric primitives are imported from research/trend_robustness_v2/screen.py
(its main() is never run). The leveraged instrument, the risk-off weights, the gold
factor construction (open = previous close, 0.40%/yr drag) and the stationary block
bootstrap are adapted from leverage_frontier_v1/screen.py (tag
archive/research/leverage-frontier-v1-2026-10).
"""

from __future__ import annotations

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
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

INDEX_CSV = ROOT / "data/historical/gold_riskoff_oos_v1_index.csv"
GOLD_CSV = ROOT / "data/historical/gold_riskoff_oos_v1_gold.csv"
PINK_CSV = ROOT / "data/historical/gold_riskoff_oos_v1_pinksheet.csv"
DTB3_CSV = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
VALIDATION = HERE / "VALIDATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
INDICES = {"P": "^IXIC", "S1": "^NDX", "S2": "^GSPC"}
WINDOW_FIRST, WINDOW_END = "1975-01-02", "1998-12-30"
IXIC_PREV_CLOSE_OPEN_BEFORE = "1985-01-01"  # ^IXIC has no intraday data before 1985: open := previous close
L, EXPENSE, FIN_SPREAD = 2.0, 0.0095, 0.0075  # QLD-like; calibrated model adds 0.75%/yr x (L-1)
MODELS = (("calibrated", FIN_SPREAD), ("base", 0.0))
SMA_N, VOL_N, VT, CAP, BAND = 200, 20, 0.35, 0.98, 0.005
COST_PER_SIDE = 4.0 / 10_000
BILL_FEE = 0.0010  # BILLS: DTB3 - 0.10%/yr
GOLD_DRAG = 0.0040  # gold position cost, %/yr
OZ_PER_KG = 32.1507466
GOLD_MAX_CARRY, GOLD_LOG_GAP = 10, 3  # carry a missing fixing forward <= 10 sessions; log runs > 3
PINK_STOP = 0.02  # stop if mean |monthly diff| vs Pink Sheet > 2%
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = 250, 2520
ARMS = ("CASH", "GOLD", "BILLS")
SUBS = (("p1975_1980", None, "1980-12-31"), ("p1981_1998", "1980-12-31", None))
# leverage_frontier_v1 published Stage-2 GOLD result (docs/research/leverage_frontier_v1.md),
# window 2000-08-31 -> 2026-09-29 (nav[0] = 2000-08-30 close), m = 2. Report-only combination.
LF = {"window": ("2000-08-30", "2026-09-29"),
      "calibrated": {"GOLD": 0.1639, "CASH": 0.1412}, "base": {"GOLD": 0.1697, "CASH": 0.1469},
      "base_max_dd": {"GOLD": 0.459, "CASH": 0.416}}


# ----------------------------------------------------------------------------- data
def load_index(sym):
    rows = []
    with INDEX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == sym and r["date"] <= WINDOW_END:
                rows.append((r["date"], *(float(r[k]) for k in ("open", "high", "low", "close"))))
    rows.sort()
    days = [r[0] for r in rows]
    a = np.array([r[1:] for r in rows]).T  # open, high, low, close
    o = a[0].copy()
    if sym == "^IXIC":
        for i, d in enumerate(days):
            if d < IXIC_PREV_CLOSE_OPEN_BEFORE:
                o[i] = a[3][i - 1] if i > 0 else a[3][i]
    return days, {"open": o, "high": a[1], "low": a[2], "close": a[3], "raw_open": a[0]}


def load_dtb3(days):
    with DTB3_CSV.open() as fh:
        rows = {r["observation_date"]: r["DTB3"] for r in csv.DictReader(fh)}
    last, tb, stale, run = None, {}, {}, 0
    for d in sorted(set(rows) | set(days)):
        v = rows.get(d, ".")
        if v not in (".", ""):
            last, run = float(v) / 100, 0
        elif d in set(days):
            run += 1
        tb[d], stale[d] = last, run
    return np.array([tb[d] for d in days]), max(stale[d] for d in days)


def load_gold_fixings():
    """{Frankfurt date: USD per troy oz} = DM/kg / (DM per USD) / 32.1507466."""
    out = {}
    with GOLD_CSV.open() as fh:
        for r in csv.DictReader(fh):
            out[r["date"]] = float(r["frankfurt_dem_per_kg"]) / float(r["dem_per_usd"]) / OZ_PER_KG
    return out


def gold_on_calendar(days, fix):
    """Same-date gold level per equity session (latest fixing dated <= the session), the
    carry-forward run length per session, and the list of runs > GOLD_LOG_GAP sessions."""
    fd = sorted(fix)
    g = np.full(len(days), np.nan)
    carry = np.zeros(len(days), dtype=int)
    j, last, run = 0, np.nan, 0
    for i, d in enumerate(days):
        while j < len(fd) and fd[j] <= d:
            last = fix[fd[j]]
            j += 1
        if d in fix:
            run = 0
        elif not np.isnan(last):
            run += 1
        g[i], carry[i] = last, run
    runs, k = [], 0
    while k < len(days):
        if carry[k] > 0:
            e = k
            while e + 1 < len(days) and carry[e + 1] > 0:
                e += 1
            if e - k + 1 > GOLD_LOG_GAP:
                runs.append({"from": days[k], "to": days[e], "sessions": int(e - k + 1)})
            k = e + 1
        else:
            k += 1
    return g, carry, runs


def gold_level(days, fix, alignment="next"):
    """GOLD 'close' level per equity session.

    next (primary): the level of session t is the same-date level of session t+1 (the
      first Frankfurt fixing after the session's close, struck before the next NY open);
      the last session keeps its same-date level. A risk-off trade decided on the close
      of t-1 and filled 'at the next open' (= previous close for the gold leg) is then
      filled at the fixing struck on session t's morning, i.e. after the signal.
    same: the latest fixing dated <= the session (leverage_frontier_v1's GC=F convention;
      report-only sensitivity: the fill price predates the signal close by ~9-11 hours)."""
    g, _, _ = gold_on_calendar(days, fix)
    if alignment == "same":
        return g
    return np.concatenate([g[1:], g[-1:]])


def gold_factors(level):
    """Per-session (overnight, intraday) gross factors: overnight 1 (open = previous close),
    intraday = level ratio x (1 - 0.40%/252). Index 0 NaN."""
    on = np.full(level.shape, np.nan)
    idr = np.full(level.shape, np.nan)
    on[1:] = 1.0
    idr[1:] = level[1:] / level[:-1] * (1 - GOLD_DRAG / 252)
    return on, idr


def load_pink():
    with PINK_CSV.open() as fh:
        return {r["month"]: float(r["gold_usd_oz"]) for r in csv.DictReader(fh)}


# ----------------------------------------------------------------------------- instruments (from leverage_frontier_v1)
def synthetic_L(q_open, q_close, q_div, y, Lx, expense, spread=0.0):
    """Daily-reset L x return - expense - (L-1) x (DTB3 + spread) financing; 1-D or [P, T]."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    fin = y[..., :-1] if spread == 0.0 else y[..., :-1] + spread
    g_cc = 1 + Lx * r_cc - (expense + (Lx - 1) * fin) / 252
    one = np.ones(q_close.shape[:-1] + (1,))
    close = np.concatenate([one, np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([one, close[..., :-1] * (1 + Lx * r_on)], axis=-1)
    return opn, close


def levels(gross_on, gross_id):
    g = gross_on * gross_id
    g = np.where(np.isnan(g), 1.0, g)
    close = np.concatenate([np.ones(g.shape[:-1] + (1,)), np.cumprod(g[..., 1:], axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(g.shape[:-1] + (1,)),
                          close[..., :-1] * np.where(np.isnan(gross_on[..., 1:]), 1.0, gross_on[..., 1:])], axis=-1)
    return opn, close


def bills_factors(y):
    on = np.full(y.shape, np.nan)
    idr = np.full(y.shape, np.nan)
    on[..., 1:] = 1.0
    idr[..., 1:] = 1 + (y[..., :-1] - BILL_FEE) / 252
    return on, idr


def vt_weight(vol):
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, VT / np.where(v > 0, v, 1.0)), 1.0)


def wants_for(up, vol, risk_off):
    """Leg 0: 2x instrument at up x min(CAP, vt(vol)); leg 1 (GOLD/BILLS): (1 - up) x CAP."""
    up = np.atleast_2d(up).astype(float)
    eq = up * np.minimum(CAP, vt_weight(np.atleast_2d(vol)))
    if risk_off is None:
        return eq[:, None, :]
    return np.stack([eq, (1 - up) * CAP], axis=1)


# ----------------------------------------------------------------------------- statistics
def idx_le(dates, d):
    return max(k for k, x in enumerate(dates) if x <= d)


def seg(dates, nav, a, b):
    ia = 0 if a is None or dates[0] > a else idx_le(dates, a)
    ib = len(dates) - 1 if b is None else idx_le(dates, b)
    s = nav[ia:ib + 1]
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return {"from": dates[ia], "to": dates[ib], "cagr": float(trv2.cagr(s[0], s[-1], yrs)),
            "total_return": float(s[-1] / s[0] - 1), "max_dd": float(trv2.max_dd(s)[0])}


def stats(dates, nav, turn=0.0, ntr=0):
    nav = np.asarray(nav)
    yrs = trv2.calendar_years(dates[0], dates[-1])
    st = {"start": dates[0], "end": dates[-1], "years": yrs,
          "cagr": float(trv2.cagr(nav[0], nav[-1], yrs)), "max_dd": float(trv2.max_dd(nav)[0]),
          "ending_multiple": float(nav[-1] / nav[0]), "sharpe": trv2.sharpe_daily(nav),
          "turnover_annual": float(turn / yrs), "trades": int(ntr)}
    pk_all = np.maximum.accumulate(nav)
    tr = int(np.argmin(nav / pk_all))
    pk = int(np.argmax(nav[:tr + 1]))
    rec = next((k for k in range(tr, len(nav)) if nav[k] >= nav[pk]), None)
    st["max_dd_episode"] = {"peak": dates[pk], "trough": dates[tr], "recovered": dates[rec] if rec is not None else None}
    for name, a, b in SUBS:
        if b is None or b >= dates[0]:  # S1 starts in 1986: no 1975-1980 sub-period
            st[name] = seg(dates, nav, a, b)
    if dates[0] < "1987-01-01":
        st["y1987"] = seg(dates, nav, "1986-12-31", "1987-12-31")
        st["oct1987"] = seg(dates, nav, "1987-09-30", "1987-10-31")
    last, prev, yr = {}, nav[0], {}
    for d, v in zip(dates, nav):
        last[d[:4]] = v
    for y in sorted(last):
        yr[y] = float(last[y] / prev - 1)
        prev = last[y]
    st["calendar_years"] = yr
    return st


# ----------------------------------------------------------------------------- per-index setup
class Index:
    def __init__(self, key, fix):
        self.key, self.sym = key, INDICES[key]
        self.days, self.bars = load_index(self.sym)
        self.y, self.dtb3_max_stale = load_dtb3(self.days)
        T = len(self.days)
        self.start = next(i for i in range(SMA_N, T) if self.days[i] >= WINDOW_FIRST)
        self.zero = np.zeros(T)
        self.up = trv2.sma_up(self.bars["close"], SMA_N)[0]
        self.gold = {al: gold_level(self.days, fix, al) for al in ("next", "same")}
        _, self.gold_carry, self.gold_runs = gold_on_calendar(self.days, fix)

    def instrument(self, spread):
        b = self.bars
        return synthetic_L(b["open"], b["close"], self.zero, self.y, L, EXPENSE, spread)

    def risk_off_levels(self, name, alignment="next"):
        if name == "GOLD":
            return levels(*gold_factors(self.gold[alignment]))
        return levels(*bills_factors(self.y))

    def run(self, arm, spread, m, alignment="next"):
        o, c = self.instrument(spread)
        vol = trv2.vol_n(c, VOL_N)[0]
        cost = COST_PER_SIDE * m
        if arm == "CASH":
            w = wants_for(self.up, vol, None)
            nav, turn, ntr = trv2.simulate_multi(o[None, :], c[None, :], w, cost, self.start)
        else:
            ro, rc = self.risk_off_levels(arm, alignment)
            w = wants_for(self.up, vol, arm)
            nav, turn, ntr = trv2.simulate_multi(np.stack([o, ro]), np.stack([c, rc]), w, cost, self.start)
        dates = self.days[self.start - 1:]
        return dates, nav[0], stats(dates, nav[0], turn[0], ntr[0])

    def buy_and_hold(self):
        dates = self.days[self.start - 1:]
        c = self.bars["close"][self.start - 1:]
        return stats(dates, c / c[0])

    def episodes(self, navs):
        """Risk-off episodes in the measured window: maximal runs of sessions i with up[i-1] False."""
        g = self.gold["next"]
        cl = self.bars["close"]
        out, i, T = [], self.start, len(self.days)
        while i < T:
            if not self.up[i - 1]:
                e = i
                while e + 1 < T and not self.up[e]:
                    e += 1
                k0, k1 = i - 1 - (self.start - 1), e - (self.start - 1)  # nav indices: close before i .. close e
                ep = {"first_session": self.days[i], "last_session": self.days[e], "sessions": int(e - i + 1),
                      "gold_return": float(g[e] / g[i - 1] - 1), "index_return": float(cl[e] / cl[i - 1] - 1)}
                for a, nav in navs.items():
                    ep[f"{a}_return"] = float(nav[k1] / nav[k0] - 1)
                out.append(ep)
                i = e + 1
            else:
                i += 1
        return out


# ----------------------------------------------------------------------------- bootstrap (from leverage_frontier_v1)
def boot_paths(npool, rng, P):
    Lp = WARM + MEASURED
    pos = np.empty((P, Lp), dtype=np.int64)
    pos[:, 0] = rng.integers(0, npool, P)
    jump = rng.random((P, Lp)) < 1 / BLOCK
    fresh = rng.integers(0, npool, (P, Lp))
    for t in range(1, Lp):
        pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
    return pos


def bootstrap(ix: Index, spread, m):
    """5,000 paths of 250 warm-up + 2,520 measured sessions resampled (stationary blocks,
    mean 20) from the measured window's sessions; index overnight/intraday returns, the
    previous DTB3 and the GOLD session factor are resampled jointly. All arms (and index
    buy-and-hold) see the same paths."""
    b = ix.bars
    T = len(ix.days)
    rng = np.random.default_rng(SEED)
    pool = np.arange(ix.start, T)
    npool = len(pool)
    cost = COST_PER_SIDE * m
    start = WARM + 1
    g_on, g_id = gold_factors(ix.gold["next"])
    acc = {a: {"cagr": [], "dd": []} for a in ARMS + ("INDEX",)}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        idx = pool[boot_paths(npool, rng, P)]
        yy = ix.y[idx - 1]
        prev = b["close"][idx - 1]
        r_on = b["open"][idx] / prev
        r_id = b["close"][idx] / b["open"][idx]
        qc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        qo = np.concatenate([np.ones((P, 1)), qc[:, :-1] * r_on], axis=1)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        io, ic = synthetic_L(qo, qc, np.zeros_like(qc), yyp, L, EXPENSE, spread)
        vol = trv2.vol_n(ic, VOL_N)
        up = trv2.sma_up(qc, SMA_N)
        pad = np.full((P, 1), np.nan)
        ro = {"GOLD": levels(np.concatenate([pad, g_on[idx]], axis=1), np.concatenate([pad, g_id[idx]], axis=1)),
              "BILLS": levels(np.concatenate([pad, np.ones((P, idx.shape[1]))], axis=1),
                              np.concatenate([pad, 1 + (yy - BILL_FEE) / 252], axis=1))}
        for arm in ARMS:
            if arm == "CASH":
                nav = trv2.simulate_multi(io[:, None, :], ic[:, None, :], wants_for(up, vol, None), cost, start)[0]
            else:
                nav = trv2.simulate_multi(np.stack([io, ro[arm][0]], axis=1), np.stack([ic, ro[arm][1]], axis=1),
                                          wants_for(up, vol, arm), cost, start)[0]
            k = nav.shape[1] - 1
            acc[arm]["cagr"].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
            acc[arm]["dd"].append(trv2.max_dd(nav))
        bh = qc[:, start - 1:]
        acc["INDEX"]["cagr"].append((bh[:, -1] / bh[:, 0]) ** (252 / (bh.shape[1] - 1)) - 1)
        acc["INDEX"]["dd"].append(trv2.max_dd(bh))
    out = {"paths": PATHS, "seed": SEED, "mean_block": BLOCK, "pool": [ix.days[ix.start], ix.days[-1]],
           "pool_sessions": int(npool), "warmup_sessions": WARM, "measured_sessions": MEASURED, "m": m,
           "spread": spread}
    for a in acc:
        c, d = np.concatenate(acc[a]["cagr"]), np.concatenate(acc[a]["dd"])
        out[a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                  "cagr_p95": float(np.percentile(c, 95)), "p_cagr_below_0": float(np.mean(c < 0)),
                  "max_dd_median": float(np.median(d)), "p_dd_gt_50": float(np.mean(d > 0.50))}
    return out


# ----------------------------------------------------------------------------- validation (no arms)
def validate():
    fix = load_gold_fixings()
    pink = load_pink()
    out = {"indices": {}, "gold": {}}
    for key in INDICES:
        ix = Index(key, fix)
        b, d = ix.bars, ix.days
        by = defaultdict(lambda: {"rows": 0, "flat_bar": 0, "raw_open_eq_prev_close": 0})
        for i in range(len(d)):
            y = by[d[i][:4]]
            y["rows"] += 1
            y["flat_bar"] += int(b["raw_open"][i] == b["high"][i] == b["low"][i] == b["close"][i])
            y["raw_open_eq_prev_close"] += int(i > 0 and b["raw_open"][i] == b["close"][i - 1])
        r = b["close"][1:] / b["close"][:-1] - 1
        gaps = [(d[i - 1], d[i]) for i in range(1, len(d))
                if (np.datetime64(d[i]) - np.datetime64(d[i - 1])).astype(int) > 5]
        w = slice(ix.start, len(d))
        out["indices"][key] = {
            "symbol": ix.sym, "rows": len(d), "first_bar": d[0], "last_bar": d[-1],
            "first_measured_session": d[ix.start], "measured_sessions": len(d) - ix.start,
            "per_year": {k: v for k, v in sorted(by.items())},
            "calendar_gaps_gt_5_days": gaps,
            "largest_abs_daily_close_return": {"value": float(np.max(np.abs(r))), "date": d[int(np.argmax(np.abs(r))) + 1]},
            "dtb3_max_carry_sessions": int(ix.dtb3_max_stale),
            "dtb3_nan_in_window": int(np.isnan(ix.y[w]).sum()),
            "gold_carry_max_sessions_in_window": int(ix.gold_carry[ix.start - 1:].max()),
            "gold_carry_runs_gt_3": ix.gold_runs,
            "gold_nan_in_window": int(np.isnan(ix.gold["next"][ix.start - 1:]).sum()),
            "uptrend_share_not_computed": "signal state not inspected before pre-registration",
        }
        if key == "P":
            # instrument code identity: L=2, spread 0 equals trend_robustness_v2.synthetic_2x
            o2, c2 = synthetic_L(b["open"], b["close"], ix.zero, ix.y, 2.0, EXPENSE)
            po, pc = trv2.synthetic_2x(b["open"], b["close"], ix.zero, ix.y, EXPENSE)
            out["instrument_identity_L2_equals_trv2_synthetic_2x"] = bool(np.array_equal(o2, po) and np.array_equal(c2, pc))
    # gold vs World Bank Pink Sheet (monthly mean of Frankfurt-USD fixings vs monthly London PM average)
    mon = defaultdict(list)
    for dd, v in fix.items():
        if "1975-01" <= dd[:7] <= "1998-12":
            mon[dd[:7]].append(v)
    ms = sorted(mon)
    f = np.array([np.mean(mon[k]) for k in ms])
    p = np.array([pink[k] for k in ms])
    diff = f / p - 1
    rf, rp = f[1:] / f[:-1] - 1, p[1:] / p[:-1] - 1
    worst = int(np.argmax(np.abs(diff)))
    by_y = {y: float(np.mean(np.abs(diff[[k[:4] == y for k in ms]]))) for y in sorted({k[:4] for k in ms})}
    out["gold"]["pinksheet_check"] = {
        "months": len(ms), "window": [ms[0], ms[-1]],
        "mean_abs_pct_diff": float(np.mean(np.abs(diff))) * 100, "mean_signed_pct_diff": float(np.mean(diff)) * 100,
        "max_abs_pct_diff": float(np.abs(diff[worst])) * 100, "max_abs_month": ms[worst],
        "frankfurt_usd_at_max": float(f[worst]), "pinksheet_at_max": float(p[worst]),
        "corr_monthly_returns": float(np.corrcoef(rf, rp)[0, 1]),
        "mean_abs_pct_diff_by_year": {k: v * 100 for k, v in by_y.items()},
        "stop_threshold_pct": PINK_STOP * 100, "pass": bool(np.mean(np.abs(diff)) <= PINK_STOP),
        "total_return_1975_01_to_1998_12": {"frankfurt_monthly_mean": float(f[-1] / f[0] - 1),
                                            "pinksheet": float(p[-1] / p[0] - 1)},
    }
    fd = sorted(fix)
    out["gold"]["series"] = {"first_fix": fd[0], "last_fix": fd[-1], "fixings_1975_1998": sum(1 for x in fd if x >= "1975"),
                             "alignment": "next (primary): session t level = first fixing after the close of t"}
    return out


# ----------------------------------------------------------------------------- run
def run():
    v = validate()
    if not v["gold"]["pinksheet_check"]["pass"]:
        print("GOLD VALIDATION FAILED - stopping before any arm is simulated")
        sys.exit(1)
    if any(x["gold_carry_max_sessions_in_window"] > GOLD_MAX_CARRY or x["gold_nan_in_window"] for x in v["indices"].values()):
        print(f"GOLD CARRY-FORWARD > {GOLD_MAX_CARRY} SESSIONS IN A WINDOW - stopping before any arm is simulated")
        sys.exit(1)
    fix = load_gold_fixings()
    res = {"validation": v, "indices": {}}
    for key in INDICES:
        ix = Index(key, fix)
        r = {"symbol": ix.sym, "window": [ix.days[ix.start - 1], ix.days[-1]], "first_measured_session": ix.days[ix.start],
             "index_buy_and_hold_price": ix.buy_and_hold(), "historical": {}}
        navs = {}
        for model, spread in MODELS:
            for m in (2, 1):
                cell = {}
                for arm in ARMS:
                    _, nav, st = ix.run(arm, spread, m)
                    cell[arm] = st
                    if model == "calibrated" and m == 2:
                        navs[arm] = nav
                r["historical"][f"{model}_m{m}"] = cell
        r["gold_same_date_alignment_calibrated_m2"] = ix.run("GOLD", FIN_SPREAD, 2, "same")[2]
        eps = ix.episodes(navs)
        r["risk_off_episodes"] = {"count": len(eps), "sessions_risk_off": int(sum(e["sessions"] for e in eps)),
                                  "share_sessions_risk_off": sum(e["sessions"] for e in eps) / (len(ix.days) - ix.start),
                                  "gold_up_episodes": int(sum(e["gold_return"] > 0 for e in eps)),
                                  "episodes": eps}
        r["bootstrap"] = {"calibrated_m2": bootstrap(ix, FIN_SPREAD, 2)}
        if key == "P":
            r["bootstrap"]["base_m2"] = bootstrap(ix, 0.0, 2)
            r["bootstrap"]["calibrated_m1"] = bootstrap(ix, FIN_SPREAD, 1)
        res["indices"][key] = r
        print(key, "done", flush=True)

    h = {k: res["indices"][k]["historical"] for k in INDICES}
    P = h["P"]["calibrated_m2"]
    bP = res["indices"]["P"]["bootstrap"]["calibrated_m2"]
    gates = {
        "H1": {"pass": P["GOLD"]["cagr"] > P["CASH"]["cagr"], "gold": P["GOLD"]["cagr"], "cash": P["CASH"]["cagr"]},
        "H2": {"pass": P["GOLD"]["max_dd"] <= P["CASH"]["max_dd"] + 0.05, "gold": P["GOLD"]["max_dd"],
               "cash": P["CASH"]["max_dd"]},
        "H3": {"pass": bP["GOLD"]["cagr_median"] >= bP["CASH"]["cagr_median"], "gold": bP["GOLD"]["cagr_median"],
               "cash": bP["CASH"]["cagr_median"]},
        "H4": {k: {"pass": h[k]["calibrated_m2"]["GOLD"]["cagr"] >= h[k]["calibrated_m2"]["CASH"]["cagr"] - 0.005,
                   "gold": h[k]["calibrated_m2"]["GOLD"]["cagr"], "cash": h[k]["calibrated_m2"]["CASH"]["cagr"]}
               for k in ("S1", "S2")},
        "H5": {c: {"pass": h["P"][c]["GOLD"]["cagr"] > h["P"][c]["CASH"]["cagr"], "gold": h["P"][c]["GOLD"]["cagr"],
                   "cash": h["P"][c]["CASH"]["cagr"]} for c in ("calibrated_m1", "base_m2", "base_m1")},
    }
    gates["H4"]["pass"] = all(gates["H4"][k]["pass"] for k in ("S1", "S2"))
    gates["H5"]["pass"] = all(gates["H5"][c]["pass"] for c in ("calibrated_m1", "base_m2", "base_m1"))
    allp = all(gates[g]["pass"] for g in ("H1", "H2", "H3", "H4", "H5"))
    res["gates"] = gates
    res["decision"] = ("PASS: gold risk-off confirmed out of sample (production needs a separate Stage B replay + parity tests)"
                       if allp else "FAIL: gold risk-off closed, not revisited")
    # combined picture with leverage_frontier_v1 (report-only; chained CAGR over both windows)
    comb = {}
    lf_years = trv2.calendar_years(*LF["window"])
    for model in ("calibrated", "base"):
        here = h["P"][f"{model}_m2"]
        comb[model] = {}
        for arm in ("GOLD", "CASH"):
            y1 = here[arm]["years"]
            c = ((1 + here[arm]["cagr"]) ** y1 * (1 + LF[model][arm]) ** lf_years) ** (1 / (y1 + lf_years)) - 1
            comb[model][arm] = {"oos_1975_1998": here[arm]["cagr"], "lf_2000_2026": LF[model][arm], "chained_cagr": c}
    comb["note"] = ("chained CAGR of P (^IXIC price, 1975-1998) and leverage_frontier_v1 (QQQ total return, 2000-08 -> "
                    "2026-09); different index and dividend treatment, 1999-2000-08 not covered; report-only")
    res["combined_with_leverage_frontier_v1"] = comb
    res["status"] = "complete"
    OUT.write_text(json.dumps(trv2.rnd(res), indent=2, sort_keys=True) + "\n")
    write_summary(res)


def write_summary(res):
    f = lambda x: f"{x * 100:6.2f}%"
    lines = [f"gold_riskoff_oos_v1 | decision: {res['decision']}", ""]
    for key, r in res["indices"].items():
        lines.append(f"{key} {r['symbol']} window {r['window'][0]} -> {r['window'][1]} | "
                     f"index B&H {f(r['index_buy_and_hold_price']['cagr'])} / DD {f(r['index_buy_and_hold_price']['max_dd'])}")
        for c, cell in r["historical"].items():
            lines.append(f"  {c:14s} " + " | ".join(
                f"{a} {f(cell[a]['cagr'])} DD {f(cell[a]['max_dd'])}" for a in ARMS))
        for bk, b in r["bootstrap"].items():
            lines.append(f"  boot {bk:13s} " + " | ".join(
                f"{a} med {f(b[a]['cagr_median'])} P<0 {f(b[a]['p_cagr_below_0'])} P(DD>50) {f(b[a]['p_dd_gt_50'])}"
                for a in ARMS + ("INDEX",)))
        cm = r["historical"]["calibrated_m2"]
        lines.append("  subperiods (cal m2): " + " | ".join(
            f"{a} " + " ".join(f"{n[1:]} {f(cm[a][n]['cagr'])}" for n, _, _ in SUBS if n in cm[a]) for a in ARMS))
        if "y1987" in cm["CASH"]:
            lines.append("  1987 (cal m2): " + " | ".join(
                f"{a} yr {f(cm[a]['y1987']['total_return'])} Oct {f(cm[a]['oct1987']['total_return'])}" for a in ARMS))
        e = r["risk_off_episodes"]
        lines.append(f"  risk-off episodes {e['count']}, sessions share {f(e['share_sessions_risk_off'])}, "
                     f"gold up in {e['gold_up_episodes']}")
        lines.append(f"  GOLD same-date alignment (cal m2): {f(r['gold_same_date_alignment_calibrated_m2']['cagr'])} "
                     f"DD {f(r['gold_same_date_alignment_calibrated_m2']['max_dd'])}")
        lines.append("")
    for g, v in res["gates"].items():
        lines.append(f"{g}: {'PASS' if v['pass'] else 'fail'}  {json.dumps(trv2.rnd(v, 4))}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--validate"
    if mode == "--validate":
        v = validate()
        VALIDATION.write_text(json.dumps(trv2.rnd(v), indent=2, sort_keys=True) + "\n")
        print(json.dumps(trv2.rnd(v["gold"]["pinksheet_check"]), indent=1))
    elif mode == "--run":
        run()
    else:
        raise SystemExit("usage: screen.py --validate | --run")


if __name__ == "__main__":
    main()
