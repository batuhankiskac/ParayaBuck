#!/usr/bin/env python3
"""E2 confirmation v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --windows  prints each market's data and measured window (no signal, no arm).
  --run      runs C0 and E2 on M1..M5 at m=2 and m=1, the pooled block-20
             bootstrap (K5), the gates K1-K5 and the report-only statistics.
             Writes RESULTS.json and SUMMARY.txt.

Engine (simulate_multi), synthetic 2x, SMA signal, vol, vt and metric helpers are
imported from research/trend_robustness_v2/screen.py (its main() is never run).
tsm_up, don_up, bill_level, round_trips and whipsaw are copied verbatim from
research/signal_ensemble_v1/screen.py (tag archive/research/signal-ensemble-v1-2026-10).
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

INDEX = ROOT / "data/historical/e2_confirmation_v1_index.csv"
TB3MS = ROOT / "data/historical/e2_confirmation_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
ARMS = ("C0", "E2")
SMA_N, TSM_N, DON_N = 200, 252, 126
CAP, BAND = trv2.CAP, trv2.BAND
COST_PER_SIDE = trv2.COST_PER_SIDE  # 4 bps per unit traded, x m
EXP = trv2.EXP_Q  # 0.95%/yr
EXTRA_FIN = 0.0075  # calibrated extra financing (leverage_frontier_v1 Amendment A1)
START = TSM_N + 1  # minimum first measured session index (decision close 252 has 252 returns)
MARKETS = {  # key: (symbol, first data date, first measured session on/after, last date)
    "M1": ("^IXIC", "1971-01-01", "1975-01-02", "1999-12-31"),
    "M2": ("^GSPC", "1934-01-01", None, "1956-12-31"),
    "M3": ("^N225", "1900-01-01", None, "2026-09-29"),
    "M4": ("^GDAXI", "1900-01-01", None, "2026-09-29"),
    "M5": ("^FTSE", "1900-01-01", None, "2026-09-29"),
}
SEED, PATHS_PER_MARKET, CHUNK, BLOCK = 20261011, 2000, 500, 20
MEASURED = 2520  # 10-year bootstrap paths (measured sessions)
BOOT_L = TSM_N + MEASURED  # returns per path; level has BOOT_L + 1 points; first measured session = START
DTB3_FIRST = "1954-01-04"

# signal_ensemble_v1 results (calibrated financing, m=2), copied from its RESULTS/doc; report-only
PRIOR = {
    "QQQ_TR_2000-03-09_2026-09-29": {"C0": {"cagr": 0.1283, "max_dd": 0.496}, "E2": {"cagr": 0.1425, "max_dd": 0.496}},
    "GSPC_price_1957-01-02_1999-12-31": {"C0": {"cagr": 0.0970, "max_dd": 0.391},
                                         "E2": {"cagr": 0.1026, "max_dd": 0.326}},
}


# ----------------------------------------------------------------------------- signals (copied from signal_ensemble_v1)
def bill_level(rate):
    """Cumulative DTB3 level aligned with the financing convention: return k accrues rate[k-1]/252."""
    g = 1 + rate[..., :-1] / 252
    return np.concatenate([np.ones(rate.shape[:-1] + (1,)), np.cumprod(g, axis=-1)], axis=-1)


def tsm_up(tr, bills, n=TSM_N):
    """up[t]: n-session total return of the series > cumulative DTB3 return over the same n sessions."""
    tr, bills = np.atleast_2d(tr), np.atleast_2d(bills)
    up = np.zeros(tr.shape, dtype=bool)
    up[:, n:] = (tr[:, n:] / tr[:, :-n]) > (bills[:, n:] / bills[:, :-n])
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)  # [P, T-n+1, n]
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def signals(close, bills):
    """All three signals on the index's price close (price-only data); TSM hurdle = T-bill level."""
    return {"SMA": trv2.sma_up(close, SMA_N), "TSM": tsm_up(close, bills), "DON": don_up(close)}


def build_wants(sig, vol, arm):
    """[P, 1, T] targets; capped at 0.98 as trend_robustness_v2.build_wants."""
    vt = trv2.vt_of(vol)
    s, t, d = (sig[k].astype(float) for k in ("SMA", "TSM", "DON"))
    if arm == "C0":
        raw = s * vt
    elif arm == "E2":
        raw = ((s + t + d) >= 2).astype(float) * vt
    else:
        raise ValueError(arm)
    raw = raw[:, None, :]
    tot = raw.sum(axis=1, keepdims=True)
    f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return raw * f


def round_trips(dates, nav, held, start):
    """In-market episodes (copied from signal_ensemble_v1)."""
    trips = []
    T = len(held)
    i = start
    while i < T:
        if held[i]:
            j = i
            while j + 1 < T and held[j + 1]:
                j += 1
            e = j + 1
            if e < T:
                r = nav[e - start + 1] / nav[i - start] - 1
                trips.append({"entry": dates[i - start + 1], "exit": dates[e - start + 1], "return": float(r)})
            i = e + 1
        else:
            i += 1
    return trips


def whipsaw(trips, years):
    losers = [t["return"] for t in trips if t["return"] < 0]
    return {
        "round_trips": len(trips),
        "round_trips_per_year": len(trips) / years,
        "share_losing": len(losers) / len(trips) if trips else None,
        "avg_loss_per_losing_trip": float(np.mean(losers)) if losers else None,
        "avg_return_per_trip": float(np.mean([t["return"] for t in trips])) if trips else None,
    }


# ----------------------------------------------------------------------------- data
def load_rates():
    """Daily rate lookup: DTB3 (forward-filled) from 1954-01-04; before that TB3MS of the month (forward-filled)."""
    d3 = []
    with DTB3.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                d3.append((r["observation_date"], float(r["DTB3"]) / 100))
    tb = []
    with TB3MS.open() as fh:
        for r in csv.DictReader(fh):
            if r["TB3MS"] not in (".", ""):
                tb.append((r["observation_date"], float(r["TB3MS"]) / 100))
    return d3, tb


def rate_on(dates, d3, tb):
    """y[k] = last DTB3 on or before dates[k]; before DTB3_FIRST, TB3MS whose observation_date <= dates[k]."""
    out, src = [], []
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
            src.append("DTB3")
        else:
            if lastt is None:
                raise RuntimeError(f"no T-bill rate on {d}")
            out.append(lastt)
            src.append("TB3MS")
    return np.array(out, dtype=float), src


def placeholder_open(o, h, lo, c):
    return (o is None or h is None or lo is None or o <= 0 or (o == h == lo == c)
            or o > h * (1 + 1e-9) or o < lo * (1 - 1e-9))


def load_market(key, prev_close_open_before=None, last_override=None):
    """prev_close_open_before / last_override are used only by the report-only M1 open-rule
    sensitivity (added after the pre-registration commit; no gate uses them)."""
    sym, first, meas_from, last = MARKETS[key]
    last = last_override or last
    rows = []
    with INDEX.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == sym and first <= r["date"] <= last:
                f = lambda x: None if x == "" else float(x)
                rows.append((r["date"], f(r["open"]), f(r["high"]), f(r["low"]), float(r["close"])))
    days = [r[0] for r in rows]
    c = np.array([r[4] for r in rows])
    o = np.empty_like(c)
    filled = 0
    filled_by_year = defaultdict(int)
    for k, (d, op, h, lo, cl) in enumerate(rows):
        if k == 0:
            o[k] = cl
            continue
        if placeholder_open(op, h, lo, cl) or (prev_close_open_before is not None and d < prev_close_open_before):
            o[k] = c[k - 1]
            filled += 1
            filled_by_year[d[:4]] += 1
        else:
            o[k] = op
    d3, tb = load_rates()
    y, src = rate_on(days, d3, tb)
    start = START if meas_from is None else next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START, f"{key}: warm-up incomplete"
    info = {"symbol": sym, "data_first": days[0], "data_last": days[-1], "rows": len(days),
            "first_measured_session": days[start], "last_measured_session": days[-1],
            "measured_sessions": len(days) - start, "decision_close_before_first": days[start - 1],
            "opens_filled_with_previous_close": filled,
            "rate_source_sessions": {"TB3MS": src.count("TB3MS"), "DTB3": src.count("DTB3")}}
    nf = 0  # filled opens inside the measured window
    for k in range(start, len(rows)):
        d, op, h, lo, cl = rows[k]
        if placeholder_open(op, h, lo, cl):
            nf += 1
    info["opens_filled_in_measured_window"] = nf
    info["opens_filled_by_year"] = dict(sorted(filled_by_year.items()))
    return days, o, c, y, start, info


# ----------------------------------------------------------------------------- metrics
def dd_episodes(dates, nav, top=3):
    """Non-overlapping drawdown episodes (peak -> trough -> recovery), deepest first."""
    nav = np.asarray(nav)
    eps = []
    pk = 0
    k = 1
    n = len(nav)
    while k < n:
        if nav[k] >= nav[pk]:
            pk = k
            k += 1
            continue
        j = k
        tr = k
        while j < n and nav[j] < nav[pk]:
            if nav[j] < nav[tr]:
                tr = j
            j += 1
        eps.append({"peak": dates[pk], "trough": dates[tr], "recovered": dates[j] if j < n else None,
                    "depth": float(1 - nav[tr] / nav[pk]),
                    "years_peak_to_recovery": trv2.calendar_years(dates[pk], dates[j]) if j < n else None})
        if j < n:
            pk = j
        k = j + 1
    eps.sort(key=lambda e: -e["depth"])
    return eps[:top]


def instrument(o, c, y):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP)
    return so, sc


def hist_market(key, **kw):
    days, o, c, y, start, info = load_market(key, **kw)
    so, sc = instrument(o, c, y)
    vol = trv2.vol_n(sc)
    sig = signals(c, bill_level(y))
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": info, "years": years}
    for m in (2, 1):
        hm = {}
        for arm in ARMS:
            w = build_wants(sig, vol, arm)
            nav, turn, ntr = trv2.simulate_multi(so, sc, w, COST_PER_SIDE * m, start)
            nav = nav[0]
            cg = float(trv2.cagr(nav[0], nav[-1], years))
            dd = float(trv2.max_dd(nav)[0])
            st = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd), "ending_multiple": float(nav[-1]),
                  "turnover_annual": float(turn[0] / years), "trades": int(ntr[0])}
            if m == 2:
                tgt = w[0, 0]
                held = np.zeros(len(days), dtype=bool)
                held[1:] = tgt[:-1] > 0
                st["time_in_market"] = float(held[start:].mean())
                st["whipsaw"] = whipsaw(round_trips(dates, nav, held, start), years)
                st["worst_dd_episodes"] = dd_episodes(dates, nav)
                yr, prev, last_by = {}, nav[0], {}
                for d, v in zip(dates, nav):
                    last_by[d[:4]] = v
                for yy in sorted(last_by):
                    yr[yy] = float(last_by[yy] / prev - 1)
                    prev = last_by[yy]
                st["calendar_years"] = yr
            hm[arm] = st
        res[f"m{m}"] = hm
    bh = c[start - 1:] / c[start - 1]
    res["index_price_hold"] = {"cagr": float(trv2.cagr(1.0, bh[-1], years)), "max_dd": float(trv2.max_dd(bh)[0])}
    dec = slice(start - 1, len(days) - 1)
    sv = {k: v[0, dec] for k, v in sig.items()}
    res["signal_agreement"] = {
        "share_on": {k: float(v.mean()) for k, v in sv.items()},
        "SMA_TSM": float((sv["SMA"] == sv["TSM"]).mean()),
        "SMA_DON": float((sv["SMA"] == sv["DON"]).mean()),
        "TSM_DON": float((sv["TSM"] == sv["DON"]).mean()),
        "E2_vs_SMA_disagree": float((((sv["SMA"].astype(int) + sv["TSM"] + sv["DON"]) >= 2) != sv["SMA"]).mean()),
        "flips_per_year": {k: float(np.sum(v[1:] != v[:-1]) / years) for k, v in sv.items()},
    }
    return res


# ----------------------------------------------------------------------------- bootstrap (K5)
def bootstrap_market(key, rng, **kw):
    """Stationary block bootstrap (mean block 20) over the market's measured-window sessions.

    A path draws BOOT_L sessions; each session contributes its overnight factor
    (open/prev close), intraday factor (close/open) and the T-bill rate of its
    previous close, jointly. Signals run on the resampled price level, the
    instrument on the resampled factors. Sessions START..BOOT_L are measured
    (2,520 = 10 years at 252/yr)."""
    days, o, c, y, start, _ = load_market(key, **kw)
    pool = np.arange(start, len(days))
    npool = len(pool)
    diffs, cagr_acc = [], {a: [] for a in ARMS}
    for chunk in range(0, PATHS_PER_MARKET, CHUNK):
        P = min(CHUNK, PATHS_PER_MARKET - chunk)
        pos = np.empty((P, BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, BOOT_L))
        for t in range(1, BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        sig = signals(cc, bill_level(yyp))
        so, sc = instrument(oo, cc, yyp)
        vol = trv2.vol_n(sc)
        res = {}
        for arm in ARMS:
            w = build_wants(sig, vol, arm)
            nav = trv2.simulate_multi(so[:, None, :], sc[:, None, :], w, COST_PER_SIDE * 2, START)[0]
            k = nav.shape[1] - 1
            res[arm] = (nav[:, -1] / nav[:, 0]) ** (252 / k) - 1
            cagr_acc[arm].append(res[arm])
        diffs.append(res["E2"] - res["C0"])
    d = np.concatenate(diffs)
    cg = {a: np.concatenate(v) for a, v in cagr_acc.items()}
    summ = {"paths": PATHS_PER_MARKET, "pool_sessions": int(npool), "median_diff": float(np.median(d)),
            "mean_diff": float(np.mean(d)), "p25_diff": float(np.percentile(d, 25)),
            "p75_diff": float(np.percentile(d, 75)), "share_E2_ge_C0": float(np.mean(d >= 0)),
            "share_E2_gt_C0": float(np.mean(d > 0)),
            "C0_cagr_median": float(np.median(cg["C0"])), "E2_cagr_median": float(np.median(cg["E2"]))}
    return d, summ


# ----------------------------------------------------------------------------- gates
def gates(hist, boot_median):
    keys = list(MARKETS)
    out = {}
    for m in (2, 1):
        diffs = {k: hist[k][f"m{m}"]["E2"]["cagr"] - hist[k][f"m{m}"]["C0"]["cagr"] for k in keys}
        n_ge = sum(1 for k in keys if diffs[k] >= 0)
        med = float(np.median(list(diffs.values())))
        out[f"m{m}"] = {"cagr_diff": diffs, "markets_E2_ge_C0": n_ge, "median_diff": med,
                        "K1": bool(n_ge >= 4), "K2": bool(med > 0)}
    ddd = {k: hist[k]["m2"]["E2"]["max_dd"] - hist[k]["m2"]["C0"]["max_dd"] for k in keys}
    n3 = sum(1 for k in keys if ddd[k] <= 0.03 + 1e-12)
    k3 = bool(n3 >= 4 and max(ddd.values()) <= 0.08 + 1e-12)
    g = {"K1": out["m2"]["K1"], "K2": out["m2"]["K2"],
         "K3": k3, "K3_detail": {"max_dd_diff": ddd, "markets_within_3pp": n3, "max_diff": max(ddd.values())},
         "K4": bool(out["m1"]["K1"] and out["m1"]["K2"]),
         "K5": bool(boot_median >= 0), "K5_pooled_median_diff": boot_median,
         "detail": out}
    g["pass_all"] = bool(g["K1"] and g["K2"] and g["K3"] and g["K4"] and g["K5"])
    g["decision"] = "E2 confirmed" if g["pass_all"] else "E2 rejected: closed, VT35 unchanged"
    return g


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--windows":
        for k in MARKETS:
            print(k, json.dumps(load_market(k)[5]))
        return
    if mode != "--run":
        print(__doc__)
        sys.exit(2)

    hist = {k: hist_market(k) for k in MARKETS}
    rng = np.random.default_rng(SEED)
    boot, all_d = {}, []
    for k in MARKETS:
        d, s = bootstrap_market(k, rng)
        boot[k] = s
        all_d.append(d)
        print(f"bootstrap {k} done: median diff {s['median_diff'] * 100:+.3f} pp")
    pooled = np.concatenate(all_d)
    boot["pooled"] = {"paths": int(len(pooled)), "median_diff": float(np.median(pooled)),
                      "mean_diff": float(np.mean(pooled)), "share_E2_ge_C0": float(np.mean(pooled >= 0)),
                      "share_E2_gt_C0": float(np.mean(pooled > 0)),
                      "p25_diff": float(np.percentile(pooled, 25)), "p75_diff": float(np.percentile(pooled, 75))}
    g = gates(hist, boot["pooled"]["median_diff"])

    # ---- report-only additions made after the pre-registration commit (no gate uses them)
    srng = np.random.default_rng(SEED + 1)
    meds = [float(np.median(pooled[srng.integers(0, len(pooled), len(pooled))])) for _ in range(1000)]
    boot["pooled"]["median_mc_se_report_only"] = float(np.std(meds, ddof=1))
    boot["pooled"]["median_mc_p5_p95_report_only"] = [float(np.percentile(meds, 5)), float(np.percentile(meds, 95))]
    alt = {"prev_close_open_before": "1985-01-01"}  # gold_riskoff_oos_v1's ^IXIC open rule
    sens = {"rule": "M1 ^IXIC opens before 1985-01-01 := previous close (gold_riskoff_oos_v1 convention)"}
    for name, kw in (("M1_full_window", alt), ("M1_1975_1998_gold_window", {**alt, "last_override": "1998-12-30"}),
                     ("M1_1975_1998_campaign_rule", {"last_override": "1998-12-30"})):
        h = hist_market("M1", **kw)
        sens[name] = {f"m{m}": {a: {"cagr": h[f"m{m}"][a]["cagr"], "max_dd": h[f"m{m}"][a]["max_dd"]} for a in ARMS}
                      for m in (2, 1)}
    rng2 = np.random.default_rng(SEED)
    alt_d = []
    for k in MARKETS:
        d, s = bootstrap_market(k, rng2, **(alt if k == "M1" else {}))
        alt_d.append(d)
        if k == "M1":
            sens["M1_bootstrap_median_diff"] = s["median_diff"]
    sens["pooled_bootstrap_median_diff"] = float(np.median(np.concatenate(alt_d)))
    out_sens = sens

    combined = {}
    for name, v in PRIOR.items():
        combined[name] = {**v, "source": "signal_ensemble_v1 (calibrated, m=2)"}
    for k in MARKETS:
        h = hist[k]["m2"]
        combined[f"{k}_{MARKETS[k][0]}"] = {a: {"cagr": h[a]["cagr"], "max_dd": h[a]["max_dd"]} for a in ARMS}
    nwin = len(combined)
    ge = sum(1 for v in combined.values() if v["E2"]["cagr"] >= v["C0"]["cagr"])
    combined_summary = {"windows": nwin, "E2_cagr_ge_C0": ge,
                        "median_cagr_diff": float(np.median([v["E2"]["cagr"] - v["C0"]["cagr"]
                                                             for v in combined.values()])),
                        "note": "report-only; the two signal_ensemble_v1 windows were seen before this campaign"}

    out = {
        "campaign": "e2_confirmation_v1",
        "params": {"arms": list(ARMS), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N,
                   "vol_window": trv2.VOL_N, "vol_target": trv2.VOL_TARGET, "band": BAND, "cap": CAP,
                   "cost_per_side_m1": COST_PER_SIDE, "expense": EXP, "extra_financing": EXTRA_FIN,
                   "markets": {k: list(v) for k, v in MARKETS.items()},
                   "bootstrap": {"seed": SEED, "paths_per_market": PATHS_PER_MARKET, "mean_block": BLOCK,
                                 "warmup_returns": TSM_N, "measured_sessions": MEASURED, "m": 2},
                   "signals": "SMA, TSM and DON on the index price close; TSM hurdle = cumulative T-bill "
                              "(DTB3, TB3MS before 1954-01-04)"},
        "historical": hist, "bootstrap_m2_block20": boot, "gates": g,
        "combined_with_signal_ensemble_v1": {"windows": combined, "summary": combined_summary},
        "m1_open_rule_sensitivity_report_only": out_sens,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    p = lambda x: f"{x * 100:7.2f}%"
    L = ["e2_confirmation_v1 | C0 (VT35 rule) vs E2 (2-of-3 SMA200/TSM252/DON126) | synthetic 2x, "
         "0.95% + T-bill + 0.75%", ""]
    L.append("market window | m=2: C0 CAGR maxDD Calmar | E2 CAGR maxDD Calmar | dCAGR dDD | m=1: C0 E2 CAGR")
    for k in MARKETS:
        h = hist[k]
        w = h["window"]
        a, b = h["m2"]["C0"], h["m2"]["E2"]
        L.append(f"  {k} {w['symbol']:7s} {w['first_measured_session']}->{w['last_measured_session']} | "
                 f"{p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.2f} | {p(b['cagr'])} {p(b['max_dd'])} "
                 f"{b['calmar']:5.2f} | {(b['cagr'] - a['cagr']) * 100:+6.2f} {(b['max_dd'] - a['max_dd']) * 100:+6.2f} "
                 f"| {p(h['m1']['C0']['cagr'])} {p(h['m1']['E2']['cagr'])}")
    L.append("")
    L.append("report-only (m=2): arm time-in-market trips/yr losing-share turnover/yr | index price hold")
    for k in MARKETS:
        h = hist[k]
        parts = []
        for arm in ARMS:
            s = h["m2"][arm]
            parts.append(f"{arm} {p(s['time_in_market'])} {s['whipsaw']['round_trips_per_year']:5.2f} "
                         f"{p(s['whipsaw']['share_losing'] or 0)} {s['turnover_annual']:5.2f}")
        L.append(f"  {k} " + " | ".join(parts) + f" | hold {p(h['index_price_hold']['cagr'])} "
                 f"{p(h['index_price_hold']['max_dd'])}")
    L.append("worst drawdown episodes (m=2): depth peak->trough (recovered)")
    for k in MARKETS:
        for arm in ARMS:
            eps = hist[k]["m2"][arm]["worst_dd_episodes"]
            L.append(f"  {k} {arm} " + "; ".join(f"{p(e['depth']).strip()} {e['peak']}->{e['trough']} "
                                                f"({e['recovered'] or 'open'})" for e in eps))
    L.append("")
    L.append("bootstrap block 20, 2,000 x 10-y paths per market, m=2: median(E2-C0) share(E2>=C0) | C0 / E2 medians")
    for k in MARKETS:
        s = boot[k]
        L.append(f"  {k} {s['median_diff'] * 100:+.3f} pp {p(s['share_E2_ge_C0'])} | "
                 f"{p(s['C0_cagr_median'])} {p(s['E2_cagr_median'])}")
    s = boot["pooled"]
    L.append(f"  pooled ({s['paths']} paths) median {s['median_diff'] * 100:+.3f} pp, mean {s['mean_diff'] * 100:+.3f} pp, "
             f"share E2>=C0 {p(s['share_E2_ge_C0'])}")
    L.append("")
    d2, d1 = g["detail"]["m2"], g["detail"]["m1"]
    L.append(f"K1 E2>=C0 CAGR in >=4/5 (m=2): {d2['markets_E2_ge_C0']}/5 -> {'PASS' if g['K1'] else 'FAIL'}")
    L.append(f"K2 median dCAGR > 0 (m=2): {d2['median_diff'] * 100:+.3f} pp -> {'PASS' if g['K2'] else 'FAIL'}")
    L.append(f"K3 dDD <= +3 pp in >=4/5 and never > +8 pp: {g['K3_detail']['markets_within_3pp']}/5, max "
             f"{g['K3_detail']['max_diff'] * 100:+.2f} pp -> {'PASS' if g['K3'] else 'FAIL'}")
    L.append(f"K4 K1 and K2 at m=1: {d1['markets_E2_ge_C0']}/5, median {d1['median_diff'] * 100:+.3f} pp -> "
             f"{'PASS' if g['K4'] else 'FAIL'}")
    L.append(f"K5 pooled bootstrap median >= 0: {g['K5_pooled_median_diff'] * 100:+.3f} pp -> "
             f"{'PASS' if g['K5'] else 'FAIL'}")
    L.append(f"decision: {g['decision']}")
    L.append("")
    L.append(f"combined with signal_ensemble_v1 (report-only): E2 >= C0 in {combined_summary['E2_cagr_ge_C0']}/"
             f"{nwin} windows, median dCAGR {combined_summary['median_cagr_diff'] * 100:+.2f} pp")
    L.append("")
    L.append("report-only, added after pre-registration:")
    s = boot["pooled"]
    L.append(f"  pooled median Monte Carlo s.e. {s['median_mc_se_report_only'] * 100:.3f} pp "
             f"(5-95%: {s['median_mc_p5_p95_report_only'][0] * 100:+.3f} .. {s['median_mc_p5_p95_report_only'][1] * 100:+.3f} pp)")
    L.append(f"  M1 open-rule sensitivity ({out_sens['rule']}):")
    for name in ("M1_full_window", "M1_1975_1998_gold_window", "M1_1975_1998_campaign_rule"):
        v = out_sens[name]["m2"]
        L.append(f"    {name}: m=2 C0 {p(v['C0']['cagr'])}/{p(v['C0']['max_dd'])}  E2 {p(v['E2']['cagr'])}/{p(v['E2']['max_dd'])}")
    L.append(f"    bootstrap with this rule: M1 median {out_sens['M1_bootstrap_median_diff'] * 100:+.3f} pp, "
             f"pooled {out_sens['pooled_bootstrap_median_diff'] * 100:+.3f} pp")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
