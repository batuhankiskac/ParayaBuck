#!/usr/bin/env python3
"""VIX filter v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --validate  data checks only (coverage, alignment, FRED cross-check, ^GSPC
              placeholder opens). Writes VALIDATION.json. Simulates nothing.
  --harness   C0 only: C0 on the trend_robustness_v2 window (historical m=1/m=2)
              and C0's block-20 bootstrap must reproduce trend_robustness_v2's
              C0 to 0.01 pp. Simulates no other arm.
  --run       validate + harness (stops if the harness fails), then every arm
              (C0, X1, X2, X3, X4) under base and calibrated financing, the
              block-20 bootstraps on each arm's pool, the ^GSPC out-of-sample
              check (G8), gates, selection and report-only statistics. Writes
              RESULTS.json and SUMMARY.txt.

Engine, SMA, vol, synthetic 2x and metrics are imported from
research/trend_robustness_v2/screen.py (its main() is never run). Path
generation and the calibrated financing model follow signal_ensemble_v1 /
leverage_frontier_v1 Amendment A1 (code copied, not imported).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

DATA = ROOT / "data/historical"
F_VIX, F_VXN, F_VIX3M, F_GSPC, F_FRED = (DATA / f"vix_filter_v1_{k}.csv" for k in ("vix", "vxn", "vix3m", "gspc", "fred"))
TRV2_RESULTS = ROOT / "research/trend_robustness_v2/RESULTS.json"
OUT, SUMMARY, VALID = HERE / "RESULTS.json", HERE / "SUMMARY.txt", HERE / "VALIDATION.json"

# ----------------------------------------------------------------------------- pre-registered constants
ARMS = ("C0", "X1", "X2", "X3", "X4")
CANDIDATES = ("X1", "X2", "X3", "X4")
G8_ARMS = ("X1", "X2")  # X3/X4: no pre-2000 VIX3M/VXN -> G8 not available -> cannot pass
SMA_N = 200
VIX_LEVEL = 30.0          # X1: risk-off while VIX close > 30
SPIKE_N, SPIKE_X = 60, 1.5  # X2: risk-off while VIX / SMA60(VIX) > 1.5
IV_MULT = 2.0             # X4: vol_est = max(vol20, 2 * VXN / 100)
COST_PER_SIDE, EXP_Q, CAP = trv2.COST_PER_SIDE, trv2.EXP_Q, trv2.CAP
FIN = {"base": 0.0, "calibrated": 0.0075}  # leverage_frontier_v1 A1: +0.75%/yr x (L-1), L = 2
SEED, PATHS, CHUNK, BLOCK = trv2.SEED, trv2.PATHS, trv2.CHUNK, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
MAIN_START = trv2.SMA_MAX  # 250 -> 2000-03-06, trend_robustness_v2's common window
TOL = 1e-4  # 0.01 pp
G8_FIRST_DATA, G8_LAST = "1990-01-02", "1999-12-31"
G8_CAGR_TOL, G8_DD_TOL = 0.005, 0.03
REBOUND_N = 63  # sessions after the episode's index trough

EPISODES = {  # (from: last close <= a, to: last close <= b)
    "1998_08": ("1998-06-30", "1998-10-30"),   # G8 ^GSPC run only
    "2000_02": ("1999-12-31", "2002-12-31"),
    "2008": ("2007-12-31", "2008-12-31"),
    "2011_08": ("2011-06-30", "2011-10-31"),
    "2015_08": ("2015-06-30", "2016-02-29"),
    "2018_Q4": ("2018-09-28", "2018-12-31"),
    "2020_02_03": ("2020-01-31", "2020-03-31"),
    "2022": ("2021-12-31", "2022-12-30"),
    "2025_04": ("2025-01-31", "2025-04-30"),
}


# ----------------------------------------------------------------------------- data
def read_close(path):
    out = {}
    with path.open() as fh:
        for r in csv.DictReader(fh):
            out[r["date"]] = float(r["close"])
    return out


def align(levels: dict, days: list[str]):
    """Level at each day: same-date close, else forward-filled from the last earlier date; NaN before the first."""
    keys = sorted(levels)
    out = np.full(len(days), np.nan)
    j, last, filled = 0, np.nan, 0
    for i, d in enumerate(days):
        while j < len(keys) and keys[j] <= d:
            last = levels[keys[j]]
            j += 1
        out[i] = last
        if d not in levels and not np.isnan(last):
            filled += 1
    return out, filled


def load_main():
    days, arr, y = trv2.load()
    lv = {}
    for k, f in (("vix", F_VIX), ("vxn", F_VXN), ("vix3m", F_VIX3M)):
        lv[k] = align(read_close(f), days)[0]
    return days, arr, y, lv


def first_valid(a):
    return int(np.argmax(~np.isnan(a)))


# ----------------------------------------------------------------------------- features / wants
def sma(a, n):
    """n-session mean ending at t (inclusive); NaN unless all n values exist.

    NaN inputs (e.g. VIX before 1990-01-02 on the 1988-based ^GSPC calendar) are masked so they do not
    propagate through the cumulative sum. (Post-run fix: the first --run used a plain cumsum, which
    made every G8 VIX SMA60 NaN and X2 identical to C0 on ^GSPC; see the report.)"""
    a = np.atleast_2d(a).astype(float)
    ok = ~np.isnan(a)
    z = np.zeros((a.shape[0], 1))
    cs = np.concatenate([z, np.cumsum(np.where(ok, a, 0.0), axis=1)], axis=1)
    cn = np.concatenate([z, np.cumsum(ok, axis=1)], axis=1)
    out = np.full_like(a, np.nan)
    s = cs[:, n:] - cs[:, :-n]
    k = cn[:, n:] - cn[:, :-n]
    out[:, n - 1:] = np.where(k == n, s / n, np.nan)
    return out


def riskoff(arm, lv):
    """Risk-off flag on the completed close (NaN inputs -> False)."""
    vix = np.atleast_2d(lv["vix"])
    with np.errstate(invalid="ignore"):
        if arm == "X1":
            return vix > VIX_LEVEL
        if arm == "X2":
            return (vix / sma(vix, SPIKE_N)) > SPIKE_X
        if arm == "X3":
            return vix > np.atleast_2d(lv["vix3m"])
    raise ValueError(arm)


def build_wants(arm, up, vol, lv):
    """[P, 1, T] QLD* targets; C0 = SMA200 * vt(vol20); X1-X3 add a cash override; X4 uses implied vol in vt."""
    up = np.atleast_2d(up).astype(float)
    vol = np.atleast_2d(vol)
    if arm == "C0":
        raw = up * trv2.vt_of(vol)
    elif arm in ("X1", "X2", "X3"):
        raw = up * trv2.vt_of(vol) * (~riskoff(arm, lv)).astype(float)
    elif arm == "X4":
        iv = IV_MULT * np.atleast_2d(lv["vxn"]) / 100
        with np.errstate(invalid="ignore"):
            est = np.where(np.isnan(iv), vol, np.maximum(vol, iv))
        raw = up * trv2.vt_of(est)
    else:
        raise ValueError(arm)
    return np.minimum(raw, CAP)[:, None, :]


# ----------------------------------------------------------------------------- windows
def arm_start(arm, lv):
    """First measured session index: decision close start-1 has the arm's inputs; never before MAIN_START."""
    if arm == "X3":
        return max(MAIN_START, max(first_valid(lv["vix3m"]), first_valid(lv["vix"])) + 1)
    if arm == "X4":
        return max(MAIN_START, first_valid(lv["vxn"]) + 1)
    return max(MAIN_START, first_valid(lv["vix"]) + SPIKE_N)


def pool_first(arm, lv):
    """First pool session for the arm's bootstrap: sessions whose own level(s) exist."""
    if arm == "X3":
        return max(1, first_valid(lv["vix3m"]), first_valid(lv["vix"]))
    if arm == "X4":
        return max(1, first_valid(lv["vxn"]))
    return max(1, first_valid(lv["vix"]))


# ----------------------------------------------------------------------------- historical
def seg(dates, nav, a, b):
    ia = 0 if dates[0] > a else max(k for k, d in enumerate(dates) if d <= a)
    ib = max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia:ib + 1]
    return ia, ib, {"from": dates[ia], "to": dates[ib], "total_return": float(s[-1] / s[0] - 1),
                    "max_dd": float(trv2.max_dd(s)[0])}


def episodes(dates, nav, held_m, index_close_m, keys):
    """held_m[k], index_close_m[k] aligned with nav[k] (k = 0 is the close before the first session)."""
    out = {}
    for name in keys:
        a, b = EPISODES[name]
        if b <= dates[0]:
            out[name] = None  # episode outside the window
            continue
        ia, ib, r = seg(dates, nav, a, b)
        r["partial"] = bool(a < dates[0])  # window starts inside the episode
        r["days_out"] = int((~held_m[ia + 1:ib + 1]).sum())
        r["sessions"] = int(ib - ia)
        tr = ia + int(np.argmin(index_close_m[ia:ib + 1]))
        te = min(tr + REBOUND_N, len(nav) - 1)
        r["rebound"] = {"from": dates[tr], "to": dates[te], "return": float(nav[te] / nav[tr] - 1),
                        "days_out": int((~held_m[tr + 1:te + 1]).sum())}
        out[name] = r
    return out


def vix_exits(dates, arm_t, c0_t, o_syn, c_syn, start, years):
    """VIX-triggered exits: decision close t >= start-1 with arm target 0, arm target[t-1] > 0, C0 target[t] > 0.

    Exit fills at open t+1; re-entry fills at the open after the next decision close with arm target > 0
    (or the last close if none). 'Avoided further loss' iff QLD* re-entry price < exit price."""
    T = len(arm_t)
    ev = []
    for t in range(start - 1, T - 1):
        if arm_t[t] == 0 and arm_t[t - 1] > 0 and c0_t[t] > 0:
            r = next((k for k in range(t + 1, T - 1) if arm_t[k] > 0), None)
            px_in = o_syn[r + 1] if r is not None else c_syn[-1]
            ret = float(px_in / o_syn[t + 1] - 1)
            ev.append({"decision": dates[t - start + 1], "out_sessions": int((r if r is not None else T - 1) - t),
                       "qld_return_while_out": ret})
    n = len(ev)
    return {"exits": n, "exits_per_year": n / years,
            "share_avoided_further_loss": float(np.mean([e["qld_return_while_out"] < 0 for e in ev])) if n else None,
            "mean_qld_return_while_out": float(np.mean([e["qld_return_while_out"] for e in ev])) if n else None,
            "median_out_sessions": float(np.median([e["out_sessions"] for e in ev])) if n else None,
            "events": ev}


def hist_run(model, arm, start, m_list=(1, 2), extras=False, data=None):
    days, arr, y, lv = data
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + FIN[model], EXP_Q)
    vol = trv2.vol_n(c)
    up = trv2.sma_up(q[1], SMA_N)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    w = build_wants(arm, up, vol, lv)
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    for m in m_list:
        nav, turn, ntr = trv2.simulate_multi(o, c, w, COST_PER_SIDE * m, start)
        nav = nav[0]
        st = trv2.curve_stats(dates, nav)
        st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
        st["sharpe"] = trv2.sharpe_daily(nav)
        st["turnover_annual"] = float(turn[0] / years)
        st["trades"] = int(ntr[0])
        if extras and m == 2:
            tgt = w[0, 0]
            held = np.zeros(len(days), dtype=bool)
            held[1:] = tgt[:-1] > 0
            st["time_in_market"] = float(held[start:].mean())
            st["episodes"] = episodes(dates, nav, held[start - 1:], q[1][start - 1:],
                                      [k for k in EPISODES if k != "1998_08"])
            if arm in ("X1", "X2", "X3"):
                c0t = build_wants("C0", up, vol, lv)[0, 0]
                st["vix_exits"] = vix_exits(dates, tgt, c0t, o, c, start, years)
                st["vix_exits"].pop("events")
        res[f"m{m}"] = st
    return res


def spy_hist(arr, days, start):
    s = arr["SPY"]
    tr = (s[1][start:] + s[2][start:]) / s[1][start - 1:-1]
    nav = np.concatenate([[1.0], np.cumprod(tr)])
    st = trv2.curve_stats(days[start - 1:], nav)
    return {"cagr": st["cagr"], "max_dd": st["max_dd"], "2017_end_cagr": st["2017_end"]["cagr"]}


# ----------------------------------------------------------------------------- bootstrap
def bootstrap(pfirst, arms, models, data, m=2, block=BLOCK):
    """Stationary block bootstrap of whole sessions from pool [pfirst, T).

    Same generator, seed, draw order and 500-path chunks as trend_robustness_v2 (with pfirst = 1 the
    draws are identical). QQQ/SPY overnight+intraday returns, dividends, DTB3 and the VIX/VXN/VIX3M
    closes of the same session are resampled jointly; SMA200, vol20 and VIX SMA60 are recomputed on
    each path. Path column 0 (the close before the first sampled session) carries the first sampled
    session's levels (warm-up only)."""
    days, arr, y, lv = data
    T = arr["QQQ"].shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(pfirst, T)
    npool = len(pool)
    L = WARM + MEASURED
    cost = COST_PER_SIDE * m
    start = trv2.SMA_MAX + 1
    acc = {(md, a): {"cagr": [], "dd": []} for md in models for a in arms}
    spy_acc = {"cagr": [], "dd": []}
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / block
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        yy = y[idx - 1]

        def leg(sym):
            prev = arr[sym][1][idx - 1]
            r_on = (arr[sym][0][idx] + arr[sym][2][idx]) / prev
            r_id = arr[sym][1][idx] / arr[sym][0][idx]
            cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
            oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
            return oo, cc

        qo, qc = leg("QQQ")
        _, sc = leg("SPY")
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        plv = {k: np.concatenate([v[idx[:, :1]], v[idx]], axis=1) for k, v in lv.items()}
        up = trv2.sma_up(qc, SMA_N)
        for md in models:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol = trv2.vol_n(scl)
            for arm in arms:
                w = build_wants(arm, up, vol, plv)
                nav = trv2.simulate_multi(so[:, None, :], scl[:, None, :], w, cost, start)[0]
                k = nav.shape[1] - 1
                acc[(md, arm)]["cagr"].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                acc[(md, arm)]["dd"].append(trv2.max_dd(nav))
        spy = sc[:, start - 1:]
        spy_acc["cagr"].append((spy[:, -1] / spy[:, 0]) ** (252 / (spy.shape[1] - 1)) - 1)
        spy_acc["dd"].append(trv2.max_dd(spy))
    s_c = np.concatenate(spy_acc["cagr"])
    out = {"pool_first": days[pfirst], "pool_sessions": int(npool), "paths": PATHS, "seed": SEED,
           "mean_block": block, "warmup_sessions": WARM, "measured_sessions": MEASURED, "cost_multiplier_m": m}
    for md in models:
        out[md] = {}
        for a in arms:
            c, d = np.concatenate(acc[(md, a)]["cagr"]), np.concatenate(acc[(md, a)]["dd"])
            out[md][a] = {"cagr_median": float(np.median(c)), "cagr_p5": float(np.percentile(c, 5)),
                          "p_cagr_below_0": float(np.mean(c < 0)), "max_dd_median": float(np.median(d)),
                          "p_dd_gt_50": float(np.mean(d > 0.50)), "p_dd_gt_70": float(np.mean(d > 0.70)),
                          "share_beats_spy": float(np.mean(c > s_c))}
    sd = np.concatenate(spy_acc["dd"])
    out["SPY"] = {"cagr_median": float(np.median(s_c)), "cagr_p5": float(np.percentile(s_c, 5)),
                  "max_dd_median": float(np.median(sd))}
    return out


# ----------------------------------------------------------------------------- G8: ^GSPC + ^VIX, 1990-1999
def load_gspc():
    """^GSPC 1988-01-04 -> 1999-12-31 with DTB3 and ^VIX aligned (forward fill; VIX NaN before 1990-01-02).

    Placeholder opens (missing, <= 0, or within 0.01 points of the previous close) are set to the previous
    close, identically for every arm. Returns days, open, close, rate, levels, placeholder count."""
    rows = []
    with F_GSPC.open() as fh:
        for r in csv.DictReader(fh):
            if r["date"] <= G8_LAST:
                rows.append((r["date"], float(r["open"]) if r["open"] else float("nan"), float(r["close"])))
    days = [r[0] for r in rows]
    o_raw = np.array([r[1] for r in rows])
    c = np.array([r[2] for r in rows])
    prev = np.concatenate([[c[0]], c[:-1]])
    ph = np.isnan(o_raw) | (o_raw <= 0) | (np.abs(o_raw - prev) <= 0.01)
    ph[0] = True
    o = np.where(ph, prev, o_raw)
    tb = {}
    with trv2.DTB3.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                tb[r["observation_date"]] = float(r["DTB3"]) / 100
    y = align(tb, days)[0]
    vix = align({d: v for d, v in read_close(F_VIX).items() if d <= G8_LAST}, days)[0]
    return days, o, c, y, {"vix": vix}, ph


def g8_start(days, lv):
    """First session whose decision close has 200 ^GSPC closes, 20 synthetic returns and 60 VIX closes (from 1990-01-02)."""
    return max(SMA_N, first_valid(lv["vix"]) + SPIKE_N)


def g8_run(arms=("C0",) + G8_ARMS, m=2):
    days, o, c, y, lv, ph = load_gspc()
    start = g8_start(days, lv)
    so, scl = trv2.synthetic_2x(o, c, np.zeros_like(c), y + FIN["calibrated"], EXP_Q)
    vol = trv2.vol_n(scl)
    up = trv2.sma_up(c, SMA_N)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    out = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1, "data_first": days[0]},
           "cost_multiplier_m": m, "financing": "calibrated",
           "placeholder_opens_in_window": int(ph[start:].sum())}
    c0t = build_wants("C0", up, vol, lv)[0, 0]
    for arm in arms:
        w = build_wants(arm, up, vol, lv)
        nav, turn, ntr = trv2.simulate_multi(so, scl, w, COST_PER_SIDE * m, start)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        tgt = w[0, 0]
        held = np.zeros(len(days), dtype=bool)
        held[1:] = tgt[:-1] > 0
        r = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
             "turnover_annual": float(turn[0] / years), "time_in_market": float(held[start:].mean()),
             "episodes": episodes(dates, nav, held[start - 1:], c[start - 1:], ["1998_08"])}
        if arm != "C0":
            ve = vix_exits(dates, tgt, c0t, so, scl, start, years)
            ve.pop("events")
            r["vix_exits"] = ve
        out[arm] = r
    bh = c[start - 1:] / c[start - 1]
    out["GSPC_price_hold"] = {"cagr": float(trv2.cagr(1.0, bh[-1], years)), "max_dd": float(trv2.max_dd(bh)[0])}
    return out


# ----------------------------------------------------------------------------- validation
def validate():
    days, arr, y = trv2.load()
    v = {"qqq_days": {"first": days[0], "last": days[-1], "n": len(days)}}
    fred = {}
    with F_FRED.open() as fh:
        for r in csv.DictReader(fh):
            fred[r["observation_date"]] = r
    for k, f, fid in (("vix", F_VIX, "VIXCLS"), ("vxn", F_VXN, "VXNCLS"), ("vix3m", F_VIX3M, None)):
        lv = read_close(f)
        a, filled = align(lv, days)
        fi = first_valid(a)
        vals = np.array(list(lv.values()))
        gaps = sorted(lv)
        from datetime import date
        maxgap = max((date.fromisoformat(gaps[i + 1]) - date.fromisoformat(gaps[i])).days for i in range(len(gaps) - 1))
        info = {"rows": len(lv), "first": min(lv), "last": max(lv), "min": float(vals.min()), "max": float(vals.max()),
                "nonpositive": int((vals <= 0).sum()), "max_calendar_gap_days": maxgap,
                "first_qqq_day_with_level": days[fi],
                "qqq_days_forward_filled": filled,
                "qqq_days_without_level_after_first": int(np.isnan(a[fi:]).sum()),
                "level_rows_on_non_qqq_days_after_qqq_start": int(sum(1 for d in lv if d >= days[0] and d not in set(days)))}
        if fid:
            diffs = [abs(lv[d] - float(fred[d][fid])) for d in lv if d in fred and fred[d][fid] not in ("", ".")]
            only_y = sum(1 for d in lv if d not in fred or fred[d][fid] in ("", "."))
            only_f = sum(1 for d, r in fred.items() if r[fid] not in ("", ".") and d not in lv)
            info["fred_cross_check"] = {"series": fid, "common_dates": len(diffs), "max_abs_diff": float(max(diffs)),
                                        "share_abs_diff_le_0p01": float(np.mean(np.array(diffs) <= 0.01)),
                                        "share_abs_diff_le_0p5": float(np.mean(np.array(diffs) <= 0.5)),
                                        "yahoo_dates_missing_in_fred": only_y, "fred_dates_missing_in_yahoo": only_f}
        v[k] = info
    gd, o, c, gy, glv, ph = load_gspc()
    st = g8_start(gd, glv)
    v["gspc"] = {"rows": len(gd), "first": gd[0], "last": gd[-1], "nonpositive_close": int((c <= 0).sum()),
                 "placeholder_opens_total": int(ph.sum()),
                 "placeholder_opens_in_g8_window": int(ph[st:].sum()), "g8_window_sessions": len(gd) - st,
                 "g8_first_measured_session": gd[st], "vix_days_forward_filled_in_window": int(
                     sum(1 for d in gd[st:] if d not in read_close(F_VIX))),
                 "dtb3_nan_in_window": int(np.isnan(gy[st:]).sum())}
    lv = load_main()[3]
    v["windows"] = {a: days[arm_start(a, lv)] for a in ARMS}
    v["bootstrap_pool_first"] = {a: days[pool_first(a, lv)] for a in ARMS}
    VALID.write_text(json.dumps(trv2.rnd(v), indent=2, sort_keys=True) + "\n")
    return v


# ----------------------------------------------------------------------------- harness
def harness(data):
    ref = json.loads(TRV2_RESULTS.read_text())
    h = hist_run("base", "C0", MAIN_START, data=data)
    b = bootstrap(1, ("C0",), ("base",), data)
    checks = {}
    for m in (1, 2):
        for k in ("cagr", "max_dd"):
            r, x = ref["historical"][f"m{m}"]["C0"][k], h[f"m{m}"][k]
            checks[f"hist_m{m}_{k}"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    for k in ("cagr_median", "cagr_p5", "max_dd_median", "share_beats_spy"):
        r, x = ref["bootstrap"]["m2"]["C0"][k], b["base"]["C0"][k]
        checks[f"boot20_m2_{k}"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    r, x = ref["bootstrap"]["m2"]["SPY"]["cagr_median"], b["SPY"]["cagr_median"]
    checks["boot20_SPY_cagr_median"] = {"reference": r, "reproduced": x, "diff_pp": (x - r) * 100}
    ok = all(abs(v["diff_pp"]) <= TOL * 100 + 1e-9 for v in checks.values())
    return {"window": h["window"], "reference_window": ref["common_window"], "checks": checks,
            "tolerance_pp": TOL * 100, "pass": bool(ok)}


# ----------------------------------------------------------------------------- gates
def gate(a1, c1, a2, c2, ba, bc):
    r = {"G1": bool(a2["cagr"] >= c2["cagr"] - 0.005),
         "G2": bool(a2["max_dd"] <= c2["max_dd"]),
         "G3": bool(a2["cagr"] > c2["cagr"] or a2["calmar"] >= 1.10 * c2["calmar"]),
         "G4": bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.02),
         "G5": bool(ba["cagr_median"] >= bc["cagr_median"] and ba["share_beats_spy"] >= bc["share_beats_spy"])}
    g1 = bool(a1["cagr"] >= c1["cagr"] - 0.005)
    g2 = bool(a1["max_dd"] <= c1["max_dd"])
    g3 = bool(a1["cagr"] > c1["cagr"] or a1["calmar"] >= 1.10 * c1["calmar"])
    r["G6"] = bool(g1 and g2 and g3)
    r["G6_detail"] = {"G1": g1, "G2": g2, "G3": g3}
    r["G7"] = bool(a2["turnover_annual"] <= 2 * c2["turnover_annual"])
    return r


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--validate", "--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    val = validate()
    print(json.dumps(trv2.rnd(val), indent=1, sort_keys=True))
    if mode == "--validate":
        return
    data = load_main()
    hc = harness(data)
    for k, v in hc["checks"].items():
        print(f"harness {k}: ref {v['reference']:.6f} repro {v['reproduced']:.6f} diff {v['diff_pp']:+.4f} pp")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    days, arr, y, lv = data
    out = {"params": {
        "arms": list(ARMS), "sma_n": SMA_N, "vix_level": VIX_LEVEL, "spike_n": SPIKE_N, "spike_x": SPIKE_X,
        "iv_mult": IV_MULT, "vol_window": trv2.VOL_N, "vol_target": trv2.VOL_TARGET, "band": trv2.BAND, "cap": CAP,
        "cost_per_side_m1": COST_PER_SIDE, "expense_qld_star": EXP_Q, "financing_spread": FIN,
        "bootstrap": {"paths": PATHS, "seed": SEED, "block": BLOCK, "warmup": WARM, "measured": MEASURED},
        "episodes": EPISODES, "rebound_sessions": REBOUND_N,
        "g8": "^GSPC price + ^VIX, placeholder opens := previous close, synthetic 2x 0.95% + DTB3 + 0.75%, m=2",
    }, "validation": val, "harness_check": hc}

    # windows: X1/X2 share C0's main window; X3/X4 have their own, each with C0 on the same window
    starts = {a: arm_start(a, lv) for a in ARMS}
    win_of = {"C0": "main", "X1": "main", "X2": "main", "X3": "x3", "X4": "x4"}
    wstart = {"main": starts["C0"], "x3": starts["X3"], "x4": starts["X4"]}
    assert starts["X1"] == starts["X2"] == starts["C0"] == MAIN_START
    hist = {}
    for md in FIN:
        hist[md] = {}
        for wn, st in wstart.items():
            arms = ("C0", "X1", "X2") if wn == "main" else ("C0", "X3" if wn == "x3" else "X4")
            hist[md][wn] = {a: hist_run(md, a, st, extras=True, data=data) for a in arms}
            hist[md][wn]["SPY"] = spy_hist(arr, days, st)
    out["historical"] = hist

    # report-only: X4 with FRED VXNCLS where available (Yahoo ^VXN elsewhere), C0 on the same window
    fred = {}
    with F_FRED.open() as fh:
        for r in csv.DictReader(fh):
            if r["VXNCLS"] not in ("", "."):
                fred[r["observation_date"]] = float(r["VXNCLS"])
    yv = read_close(F_VXN)
    lv_f = dict(lv)
    lv_f["vxn"] = align({**yv, **fred}, days)[0]
    xf = hist_run("base", "X4", wstart["x4"], m_list=(2,), data=(days, arr, y, lv_f))["m2"]
    out["x4_fred_vxn_sensitivity"] = {"note": "report only; base, m=2, x4 window",
                                      "X4_fred": {"cagr": xf["cagr"], "max_dd": xf["max_dd"]},
                                      "X4_yahoo": {k: hist["base"]["x4"]["X4"]["m2"][k] for k in ("cagr", "max_dd")}}
    out["windows"] = {wn: hist["base"][wn]["C0"]["window"] for wn in wstart}

    pools = {"main": pool_first("X1", lv), "x3": pool_first("X3", lv), "x4": pool_first("X4", lv)}
    boot = {}
    for wn, pf in pools.items():
        arms = ("C0", "X1", "X2") if wn == "main" else ("C0", "X3" if wn == "x3" else "X4")
        boot[wn] = bootstrap(pf, arms, tuple(FIN), data)
        print(f"bootstrap {wn} done")
    out["bootstrap_m2_block20"] = boot

    g8 = g8_run()
    out["oos_gspc"] = g8

    gates = {}
    for arm in CANDIDATES:
        wn = win_of[arm]
        g = {}
        for md in FIN:
            h = hist[md][wn]
            g[md] = gate(h[arm]["m1"], h["C0"]["m1"], h[arm]["m2"], h["C0"]["m2"], boot[wn][md][arm], boot[wn][md]["C0"])
        if arm in G8_ARMS:
            g["G8"] = bool(g8[arm]["cagr"] >= g8["C0"]["cagr"] - G8_CAGR_TOL
                           and g8[arm]["max_dd"] <= g8["C0"]["max_dd"] + G8_DD_TOL)
        else:
            g["G8"] = "not_available"
        g["pass_all"] = bool(all(g[md][k] for md in FIN for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
                             and g["G8"] is True)
        gates[arm] = g
    out["gates"] = gates
    passing = [a for a in CANDIDATES if gates[a]["pass_all"]]
    h2 = {a: hist["base"][win_of[a]][a]["m2"] for a in CANDIDATES}
    sel = max(passing, key=lambda a: (h2[a]["cagr"], h2[a]["calmar"])) if passing else "none"
    out["selection"] = {"passing": passing, "selected": sel,
                        "rule": "highest full-window m=2 base CAGR among passing arms (tie: higher Calmar); not promoted"}
    out["status"] = "complete"
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ------------------------------------------------------------------ SUMMARY
    p = lambda x: "   n/a" if x is None else f"{x * 100:6.2f}%"
    L = [f"vix_filter_v1 | windows: " + ", ".join(f"{k} {v['start']}->{v['end']}" for k, v in out["windows"].items()),
         f"harness (C0 vs trend_robustness_v2 C0, hist m1/m2 + boot20): {'PASS' if hc['pass'] else 'FAIL'} "
         f"max |diff| {max(abs(v['diff_pp']) for v in hc['checks'].values()):.6f} pp", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: window arm | CAGR maxDD Calmar 2017+ turn/yr in-mkt | b20 med p5 P(DD>70) >SPY | m1 CAGR")
        for wn in wstart:
            for arm, r in hist[md][wn].items():
                if arm == "SPY":
                    continue
                a, b = r["m2"], boot[wn][md][arm]
                L.append(f"  {wn:4s} {arm:3s} | {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.2f} {p(a['2017_end']['cagr'])} "
                         f"{a['turnover_annual']:5.2f} {p(a['time_in_market'])} | {p(b['cagr_median'])} {p(b['cagr_p5'])} "
                         f"{p(b['p_dd_gt_70'])} {p(b['share_beats_spy'])} | {p(r['m1']['cagr'])}")
            sp = hist[md][wn]["SPY"]
            L.append(f"  {wn:4s} SPY | {p(sp['cagr'])} {p(sp['max_dd'])}  boot20 median {p(boot[wn]['SPY']['cagr_median'])}")
        L.append("")
    L.append(f"G8 ^GSPC+^VIX {g8['window']['start']} -> {g8['window']['end']} (calibrated, m=2; "
             f"{g8['placeholder_opens_in_window']} placeholder opens filled at previous close):")
    for arm in ("C0",) + G8_ARMS:
        a = g8[arm]
        L.append(f"  {arm:3s} CAGR {p(a['cagr'])} maxDD {p(a['max_dd'])} turn {a['turnover_annual']:5.2f} in-mkt {p(a['time_in_market'])}")
    gh = g8["GSPC_price_hold"]
    L.append(f"  ^GSPC price hold CAGR {p(gh['cagr'])} maxDD {p(gh['max_dd'])}")
    L.append("")
    L.append("gates (G1-G7 per financing model; G8 OOS):")
    for arm in CANDIDATES:
        g = gates[arm]
        parts = [md[:4] + " " + " ".join(f"{k}={'P' if g[md][k] else 'F'}" for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
                 for md in FIN]
        g8s = "n/a" if g["G8"] == "not_available" else ("P" if g["G8"] else "F")
        L.append(f"  {arm}: " + " | ".join(parts) + f" | G8={g8s} -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append(f"selected: {sel}" + (" (VT35 unchanged)" if sel == "none" else " (not promoted by this campaign)"))
    L.append("")
    L.append("crash episodes (base, m=2): arm total return / max DD / days out / rebound return (days out)")
    for name in EPISODES:
        L.append(f"  {name}:")
        rows = []
        if name == "1998_08":
            for arm in ("C0",) + G8_ARMS:
                rows.append((f"G8 {arm}", g8[arm]["episodes"][name]))
        else:
            for wn in wstart:
                for arm, r in hist["base"][wn].items():
                    if arm != "SPY":
                        rows.append((f"{wn} {arm}", r["m2"]["episodes"][name]))
        for lab, e in rows:
            if e is None:
                L.append(f"    {lab:8s} outside window")
            else:
                L.append(f"    {lab:8s} {p(e['total_return'])} {p(e['max_dd'])} {e['days_out']:4d}/{e['sessions']:4d} | "
                         f"{p(e['rebound']['return'])} ({e['rebound']['days_out']})")
    L.append("")
    L.append("VIX-triggered exits (base, m=2): exits/yr, share avoided further loss, mean QLD* return while out")
    for arm in ("X1", "X2", "X3"):
        ve = hist["base"][win_of[arm]][arm]["m2"]["vix_exits"]
        L.append(f"  {arm} {ve['exits']:3d} exits {ve['exits_per_year']:5.2f}/yr avoided {p(ve['share_avoided_further_loss'])} "
                 f"mean {p(ve['mean_qld_return_while_out'])} median out {ve['median_out_sessions']}")
    for arm in G8_ARMS:
        ve = g8[arm]["vix_exits"]
        L.append(f"  G8 {arm} {ve['exits']:3d} exits {ve['exits_per_year']:5.2f}/yr avoided {p(ve['share_avoided_further_loss'])} "
                 f"mean {p(ve['mean_qld_return_while_out'])}")
    xs = out["x4_fred_vxn_sensitivity"]
    L.append(f"X4 VXN source sensitivity (base m=2): Yahoo {p(xs['X4_yahoo']['cagr'])}/{p(xs['X4_yahoo']['max_dd'])} "
             f"FRED {p(xs['X4_fred']['cagr'])}/{p(xs['X4_fred']['max_dd'])} (report only)")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
