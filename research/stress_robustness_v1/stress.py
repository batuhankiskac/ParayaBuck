#!/usr/bin/env python3
"""VT35 stress / robustness v1 (see PREREGISTRATION.md). Offline; reads data/historical only."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
OUT = Path(__file__).parent / "RESULTS.json"

SMA, VOL_N, VOL_TARGET, BAND = 200, 20, 0.35, 0.005
SCALE = 0.93
COST = 4.0 * 2 / 10_000  # 2x the 4 bps/side Stage A cost
EXPENSE = 0.0095
BILL_FEE = 0.0010
SEED, PATHS, YEARS_MC, BLOCK = 20260929, 5000, 10, 20
PCTS = (5, 25, 50, 75, 95)


# ----------------------------------------------------------------------------- data
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


def synthetic_2x(q_open, q_close, q_div, y):
    """Daily-reset 2x on total return; works on 1-D or [paths, T] arrays (time on last axis)."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    g_cc = 1 + 2 * r_cc - (EXPENSE + y[..., :-1]) / 252
    close = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([np.ones(q_close.shape[:-1] + (1,)), close[..., :-1] * (1 + 2 * r_on)], axis=-1)
    return opn, close


# ----------------------------------------------------------------------------- engine
def indicators(sig_close, asset_close):
    """up[t]: close[t] > SMA200 ending at t; vol[t]: sample vol of the 20 returns ending at t."""
    c = np.atleast_2d(sig_close)
    cs = np.cumsum(c, axis=1)
    sma = np.full_like(c, np.nan)
    sma[:, SMA - 1 :] = (cs[:, SMA - 1 :] - np.concatenate([np.zeros((c.shape[0], 1)), cs[:, :-SMA]], axis=1)) / SMA
    up = c > sma  # NaN -> False
    a = np.atleast_2d(asset_close)
    r = a[:, 1:] / a[:, :-1] - 1
    vol = np.full_like(a, np.nan)
    z = np.zeros((a.shape[0], 1))
    s1 = np.concatenate([z, np.cumsum(r, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(r * r, axis=1)], axis=1)
    w1 = s1[:, VOL_N:] - s1[:, :-VOL_N]  # sum of the 20 returns ending at close t (t >= VOL_N)
    w2 = s2[:, VOL_N:] - s2[:, :-VOL_N]
    vol[:, VOL_N:] = np.sqrt(np.maximum(w2 - w1 * w1 / VOL_N, 0) / (VOL_N - 1) * 252)
    return up, vol


def simulate(opn, close, div, up, vol, y, start, mode, scale, bil=False):
    """Stage A2 mechanics. Returns NAV [P, T-start+1]; column 0 is the close before `start`."""
    opn, close, div, y = (np.atleast_2d(x) for x in (opn, close, div, y))
    P, T = close.shape
    nav = np.ones(P)
    wa = np.zeros(P)
    wb = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    for i in range(start, T):
        g_on = (opn[:, i] + div[:, i]) / close[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = wa * g_on
        vb = wb * (1 + (y[:, i - 1] - BILL_FEE) / 252)
        tot = 1 - wa - wb + va + vb
        nav = nav * tot
        wa, wb = va / tot, vb / tot
        if mode == "hold":
            want = np.full(P, scale)
        else:
            v = vol[:, i - 1]
            w = np.where(v > 0, np.minimum(1.0, VOL_TARGET / np.where(v > 0, v, 1.0)), 1.0)
            want = np.where(up[:, i - 1] & ~np.isnan(v), scale * w, 0.0)
        trade = np.abs(want - wa) > BAND
        turnover = np.abs(want - wa) + (np.abs(scale - want - wb) if bil else 0.0)
        nav = np.where(trade, nav * (1 - COST * turnover), nav)
        wa = np.where(trade, want, wa)
        if bil:
            wb = np.where(trade, scale - want, wb)
        ga = close[:, i] / opn[:, i]
        va = wa * ga
        tot = 1 - wa + va
        nav = nav * tot
        wa, wb = va / tot, wb / tot
        out[:, i - start + 1] = nav
    return out


# ----------------------------------------------------------------------------- metrics
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
    res["worst_year"] = {"year": worst, "return": full_years[worst]}
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


def pct(x):
    return {f"p{p}": float(np.percentile(x, p)) for p in PCTS}


def monte_carlo(days, arr, y, block):
    """Stationary block bootstrap of whole sessions; VT35/SPY/QQQ on each path."""
    T = len(days)
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)  # sessions with a previous close
    npool = len(pool)
    L = SMA + YEARS_MC * 252  # sampled sessions per path
    mc = {
        "VT35": {"cagr": [], "dd": [], "worst_year": []},
        "SPY": {"cagr": [], "dd": [], "worst_year": []},
        "QQQ": {"cagr": [], "dd": [], "worst_year": []},
    }
    qp = lambda row, idx: arr["QQQ"][row][pool[idx]]  # noqa: E731
    for chunk in range(0, PATHS, 1000):
        P = min(1000, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / block
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        prevc = arr["QQQ"][1][pool[pos] - 1]
        r_on_p = qp(0, pos) / prevc  # overnight price ratio
        dy = qp(2, pos) / prevc
        r_id = qp(1, pos) / qp(0, pos)
        yy = y[pool[pos] - 1]
        # rebuild a QQQ price path starting at 1 (column 0 is the initial close)
        qc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        qo = np.concatenate([np.ones((P, 1)), qc[:, :-1] * r_on_p], axis=1)
        qd = np.concatenate([np.zeros((P, 1)), qc[:, :-1] * dy], axis=1)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)  # column t-1 holds the rate before session t
        so, sc_ = synthetic_2x(qo, qc, qd, yyp)
        upm, volm = indicators(qc, sc_)
        navs = {
            "VT35": simulate(so, sc_, np.zeros_like(sc_), upm, volm, yyp, SMA + 1, "vt", SCALE),
            "QQQ": simulate(qo, qc, qd, upm, volm, yyp, SMA + 1, "hold", 1.0),
        }
        sprev = arr["SPY"][1][pool[pos] - 1]
        s_on = (arr["SPY"][0][pool[pos]] + arr["SPY"][2][pool[pos]]) / sprev
        s_id = arr["SPY"][1][pool[pos]] / arr["SPY"][0][pool[pos]]
        sc2 = np.concatenate([np.ones((P, 1)), np.cumprod(s_on * s_id, axis=1)], axis=1)
        so2 = np.concatenate([np.ones((P, 1)), sc2[:, :-1] * s_on], axis=1)
        navs["SPY"] = simulate(so2, sc2, np.zeros_like(sc2), upm, volm, yyp, SMA + 1, "hold", 1.0)
        for arm, nv in navs.items():
            n = nv.shape[1] - 1  # == YEARS_MC*252
            mc[arm]["cagr"].append((nv[:, -1] / nv[:, 0]) ** (252 / n) - 1)
            mc[arm]["dd"].append(max_dd(nv))
            yrs_ret = nv[:, 252::252] / nv[:, 0:-1:252][:, : nv[:, 252::252].shape[1]] - 1
            mc[arm]["worst_year"].append(yrs_ret.min(axis=1))
    mco = {"paths": PATHS, "years": YEARS_MC, "mean_block": block, "seed": SEED, "pool": [days[1], days[-1]]}
    cat = {arm: {k: np.concatenate(v) for k, v in d.items()} for arm, d in mc.items()}
    for arm, d in cat.items():
        mco[arm] = {
            "cagr": pct(d["cagr"]),
            "max_dd": pct(d["dd"]),
            "worst_252d_year": pct(d["worst_year"]),
            "p_max_dd_gt_50": float(np.mean(d["dd"] > 0.5)),
            "mean_cagr": float(d["cagr"].mean()),
        }
    mco["share_vt35_beats_spy"] = float(np.mean(cat["VT35"]["cagr"] > cat["SPY"]["cagr"]))
    mco["share_vt35_dd_le_1.3x_spy"] = float(np.mean(cat["VT35"]["dd"] <= 1.3 * cat["SPY"]["dd"]))
    mco["share_vt35_negative_10y"] = float(np.mean(cat["VT35"]["cagr"] < 0))
    return mco


# ----------------------------------------------------------------------------- main
def main() -> None:
    days, arr, y = load()
    T = len(days)
    q, s, lq = arr["QQQ"], arr["SPY"], arr["QLD"]
    syn_open, syn_close = synthetic_2x(q[0], q[1], q[2], y)
    zero = np.zeros(T)
    out: dict = {
        "params": {
            "sma": SMA,
            "vol_window": VOL_N,
            "vol_target": VOL_TARGET,
            "band": BAND,
            "scale": SCALE,
            "cost_per_side": COST,
            "expense": EXPENSE,
            "bill_fee": BILL_FEE,
            "cagr_basis": "calendar days / 365.25",
            "execution": "decide at completed close, fill at next open (Yahoo opens)",
        }
    }
    open_eq_close = {
        sym: float(np.mean(arr[sym][0][~np.isnan(arr[sym][0])] == arr[sym][1][~np.isnan(arr[sym][0])])) for sym in arr
    }
    out["data_checks"] = {"sessions": T, "first": days[0], "last": days[-1], "open_equals_close_share": open_eq_close}

    # --- 2. synthetic QLD validation vs real QLD (total return, close-to-close)
    k0 = int(np.argmax(~np.isnan(lq[1])))
    real_tr = (lq[1, k0 + 1 :] + lq[2, k0 + 1 :]) / lq[1, k0:-1]
    syn_tr = syn_close[k0 + 1 :] / syn_close[k0:-1]
    diff = syn_tr - real_tr
    yrs = calendar_years(days[k0], days[-1])
    val = {
        "window": [days[k0], days[-1]],
        "tracking_error_ann": float(diff.std(ddof=1) * math.sqrt(252)),
        "mean_daily_diff_ann": float(diff.mean() * 252),
        "corr": float(np.corrcoef(syn_tr, real_tr)[0, 1]),
        "cagr_real": cagr(1, real_tr.prod(), yrs),
        "cagr_synthetic": cagr(1, syn_tr.prod(), yrs),
    }
    val["cagr_diff_syn_minus_real"] = val["cagr_synthetic"] - val["cagr_real"]
    by_year = {}
    for yv in sorted({d[:4] for d in days[k0 + 1 :]}):
        m = np.array([d[:4] == yv for d in days[k0 + 1 :]])
        by_year[yv] = {"real": float(real_tr[m].prod() - 1), "synthetic": float(syn_tr[m].prod() - 1)}
    val["by_year"] = by_year
    out["synthetic_qld_validation"] = val

    # --- 3/4. historical runs
    start = SMA  # first session with 200 prior QQQ closes
    up, vol = indicators(q[1], syn_close)
    dates = days[start - 1 :]
    runs = {
        "VT35": simulate(syn_open, syn_close, zero, up, vol, y, start, "vt", SCALE),
        "VT35_bil": simulate(syn_open, syn_close, zero, up, vol, y, start, "vt", SCALE, bil=True),
        "VT35_scale1": simulate(syn_open, syn_close, zero, up, vol, y, start, "vt", 1.0),
        "SPY": simulate(s[0], s[1], s[2], up, vol, y, start, "hold", 1.0),
        "QQQ": simulate(q[0], q[1], q[2], up, vol, y, start, "hold", 1.0),
        "2X_hold": simulate(syn_open, syn_close, zero, up, vol, y, start, "hold", SCALE),
    }
    runs = {k: v[0] for k, v in runs.items()}
    hist = {k: curve_stats(dates, v) for k, v in runs.items()}
    # time in market
    w = np.where(up[0, start - 1 : -1], np.minimum(1, VOL_TARGET / vol[0, start - 1 : -1]), 0)
    hist["VT35"]["mean_qld_weight_of_sleeve"] = float(w.mean())
    hist["VT35"]["share_sessions_in_uptrend"] = float(up[0, start - 1 : -1].mean())
    hist["VT35"]["trend_flips"] = int(np.sum(up[0, start:-1] != up[0, start - 1 : -2]))
    out["historical"] = hist
    b, a = hist["VT35_bil"], hist["VT35"]
    out["bil_overlay"] = {
        "incremental_cagr_full": b["cagr"] - a["cagr"],
        "incremental_cagr_2017_end": b["2017_end"]["cagr"] - a["2017_end"]["cagr"],
        "max_dd_vt35": a["max_dd"],
        "max_dd_vt35_bil": b["max_dd"],
        "adopted": bool(b["cagr"] > a["cagr"] and b["max_dd"] <= a["max_dd"] + 1e-12),
    }

    # cross-check on real QLD (window where real QLD has 21 closes)
    ks = k0 + VOL_N + 1
    upr, volr = indicators(q[1], np.where(np.isnan(lq[1]), 1.0, lq[1]))
    real_vt = simulate(lq[0], lq[1], np.nan_to_num(lq[2]), upr, volr, y, ks, "vt", SCALE)[0]
    syn_vt = simulate(syn_open, syn_close, zero, up, vol, y, ks, "vt", SCALE)[0]
    out["vt35_real_vs_synthetic"] = {
        "window": [days[ks - 1], days[-1]],
        "real": {k: v for k, v in curve_stats(days[ks - 1 :], real_vt).items() if k in ("cagr", "max_dd")},
        "synthetic": {k: v for k, v in curve_stats(days[ks - 1 :], syn_vt).items() if k in ("cagr", "max_dd")},
    }

    # decision rule
    spy = hist["SPY"]
    out["decision"] = {
        "vt35_max_dd": a["max_dd"],
        "spy_max_dd": spy["max_dd"],
        "dd_ratio": a["max_dd"] / spy["max_dd"],
        "vt35_cagr": a["cagr"],
        "spy_cagr": spy["cagr"],
        "acceptable_for_small_live": bool(a["max_dd"] <= 1.3 * spy["max_dd"] and a["cagr"] > spy["cagr"]),
    }

    # --- 5. random monthly start dates (sliced from the continuous runs)
    starts = [k for k in range(1, len(dates)) if dates[k][:7] != dates[k - 1][:7]]
    rs = {}
    for label, n in (("5y", 1260), ("10y", 2520)):
        rows = {"VT35": {"cagr": [], "dd": []}, "SPY": {"cagr": [], "dd": []}}
        win_dates = []
        for k in starts:
            if k - 1 + n >= len(dates):
                continue
            win_dates.append(dates[k - 1])
            for arm in rows:
                seg = runs[arm][k - 1 : k + n]
                rows[arm]["cagr"].append(cagr(seg[0], seg[-1], calendar_years(dates[k - 1], dates[k - 1 + n])))
                rows[arm]["dd"].append(float(max_dd(seg)[0]))
        vc, sc = np.array(rows["VT35"]["cagr"]), np.array(rows["SPY"]["cagr"])
        vd, sd = np.array(rows["VT35"]["dd"]), np.array(rows["SPY"]["dd"])
        rs[label] = {
            "windows": len(vc),
            "first_start": win_dates[0],
            "last_start": win_dates[-1],
            "vt35_cagr": pct(vc),
            "spy_cagr": pct(sc),
            "excess_cagr": pct(vc - sc),
            "vt35_max_dd": pct(vd),
            "spy_max_dd": pct(sd),
            "share_vt35_beats_spy": float(np.mean(vc > sc)),
            "share_vt35_dd_le_spy": float(np.mean(vd <= sd)),
            "worst_vt35": {"start": win_dates[int(np.argmin(vc))], "cagr": float(vc.min())},
            "worst_excess": {"start": win_dates[int(np.argmin(vc - sc))], "excess": float((vc - sc).min())},
        }
    out["random_start"] = rs

    # --- 6. Monte Carlo stationary block bootstrap
    out["monte_carlo"] = monte_carlo(days, arr, y, BLOCK)
    # post-hoc sensitivity (not pre-registered, reported only): longer blocks keep more trend persistence
    out["monte_carlo_block_sensitivity"] = {str(b): monte_carlo(days, arr, y, b) for b in (60, 250)}

    OUT.write_text(json.dumps(out, indent=2) + "\n")
    summarize(out)


def summarize(o):
    f = lambda x: f"{x * 100:6.1f}%"  # noqa: E731
    v = o["synthetic_qld_validation"]
    print(
        f"synthetic QLD: TE {f(v['tracking_error_ann'])}  CAGR syn {f(v['cagr_synthetic'])} real {f(v['cagr_real'])} diff {f(v['cagr_diff_syn_minus_real'])}"
    )
    for k, h in o["historical"].items():
        print(
            f"{k:12s} CAGR {f(h['cagr'])} DD {f(h['max_dd'])} worst {h['worst_year']['year']} {f(h['worst_year']['return'])} "
            f"00-02 {f(h['2000_2002']['total_return'])}/{f(h['2000_2002']['max_dd'])} 07-09 {f(h['2007_2009']['total_return'])}/{f(h['2007_2009']['max_dd'])} "
            f"17+ {f(h['2017_end']['cagr'])} rec {h['max_dd_episode']} long {h['longest_underwater']}"
        )
    print("real vs syn", o["vt35_real_vs_synthetic"])
    print("bil", o["bil_overlay"])
    print("decision", o["decision"])
    for k, r in o["random_start"].items():
        print(k, json.dumps(r))
    print(json.dumps(o["monte_carlo"], indent=1))


if __name__ == "__main__":
    main()
