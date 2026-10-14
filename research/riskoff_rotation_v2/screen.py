#!/usr/bin/env python3
"""riskoff_rotation_v1 screen (PREREGISTRATION.md). Offline; reads data/historical only."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

_lf_spec = importlib.util.spec_from_file_location("lf_screen", ROOT / "research/leverage_frontier_v1/screen.py")
lf = importlib.util.module_from_spec(_lf_spec)
_lf_spec.loader.exec_module(lf)

_ewma_spec = importlib.util.spec_from_file_location("ewma_screen", ROOT / "research/ewma_band_v1/screen.py")
ewma = importlib.util.module_from_spec(_ewma_spec)
_ewma_spec.loader.exec_module(ewma)

_gro_spec = importlib.util.spec_from_file_location("gro_screen", ROOT / "research/gold_riskoff_oos_v1/screen.py")
gro = importlib.util.module_from_spec(_gro_spec)
_gro_spec.loader.exec_module(gro)

OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
DEVIATION = ROOT / "research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json"

SMA_N, TSM_N, DON_N = 200, 252, 126
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured 2000-03-09
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = lf.WARM, lf.MEASURED
EXT = 3
TOL = 1e-4
SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))
ARMS = ("C0", "R4")


def e2_up(price):
    c = np.atleast_2d(price).astype(float)
    s = trv2.sma_up(c, SMA_N).astype(int)[0]
    t = np.zeros(c.shape[1], dtype=int)
    if c.shape[1] > TSM_N:
        t[TSM_N:] = (c[0, TSM_N:] / c[0, :-TSM_N] > 1.0).astype(int)
    win = np.lib.stride_tricks.sliding_window_view(c, DON_N, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    d = np.zeros(c.shape[1], dtype=int)
    d[DON_N - 1:] = (c[0, DON_N - 1:] > mid[0]).astype(int)
    return ((s + t + d) >= 2)


def e2_up_nd(price):
    c = np.atleast_2d(price).astype(float)
    P, T = c.shape
    s = trv2.sma_up(c, SMA_N).astype(int)
    t = np.zeros((P, T), dtype=int)
    if T > TSM_N:
        t[:, TSM_N:] = (c[:, TSM_N:] / c[:, :-TSM_N] > 1.0).astype(int)
    win = np.lib.stride_tricks.sliding_window_view(c, DON_N, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    d = np.zeros((P, T), dtype=int)
    d[:, DON_N - 1:] = (c[:, DON_N - 1:] > mid).astype(int)
    return (s + t + d) >= 2


def ret252(close):
    c = np.asarray(close, dtype=float)
    out = np.full_like(c, np.nan, dtype=float)
    if c.ndim == 1:
        out[252:] = c[252:] / c[:-252] - 1
    else:
        out[:, 252:] = c[:, 252:] / c[:, :-252] - 1
    return out


def trend_up(close):
    r = ret252(close)
    su = trv2.sma_up(np.atleast_2d(close), SMA_N)[0]
    return (r > 0) & su & ~np.isnan(r)


def load_main():
    days, arr, y, y10 = lf.load_all()
    return days, arr, y, y10


def build_assets(days, arr, y, y10, spread):
    eq_o, eq_c = lf.synthetic_L(arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y, 2, lf.EXPENSE[2.0], spread)
    ro = {}
    b = lf.bills_gross(y)
    ro["BOND10"] = lf.levels(np.where(np.isnan(b), np.nan, 1.0), lf.bond10_gross(y10))
    on, idr, _ = lf.gold_factors(days, arr)
    ro["GOLD"] = lf.levels(on, idr)
    bb_o, bb_c = lf.levels(np.where(np.isnan(b), np.nan, 1.0), b)
    ro["BILLS"] = (bb_o, bb_c)
    return eq_o, eq_c, ro


def main_legs_and_wants(days, arr, y, y10, spread, arm):
    eq_o, eq_c, ro = build_assets(days, arr, y, y10, spread)
    up = e2_up(arr["QQQ"][1])
    eq_vol = trv2.vol_n(eq_c)[0]
    eq_target = up.astype(float) * np.minimum(CAP, trv2.vt_of(eq_vol))

    bond_close = ro["BOND10"][1]
    gold_close = ro["GOLD"][1]
    bills_close = ro["BILLS"][1]
    bond_vol = trv2.vol_n(bond_close)[0]
    gold_vol = trv2.vol_n(gold_close)[0]
    bond_trend = trend_up(bond_close)
    gold_trend = trend_up(gold_close)
    bond_ret = ret252(bond_close)
    gold_ret = ret252(gold_close)
    bills_ret = ret252(bills_close)

    if arm == "C0":
        opens = eq_o[None, :]
        closes = eq_c[None, :]
        wants = eq_target[None, :]
        return opens, closes, wants

    if arm == "R1":
        cond = bond_trend
        ro_w = np.where(~up & cond, 0.98, 0.0)
        opens = np.stack([eq_o, ro["BOND10"][0]], axis=0)
        closes = np.stack([eq_c, bond_close], axis=0)
        wants = np.stack([eq_target, ro_w], axis=0)
        return opens, closes, wants

    if arm == "R2":
        cond = gold_trend
        ro_w = np.where(~up & cond, 0.98, 0.0)
        opens = np.stack([eq_o, ro["GOLD"][0]], axis=0)
        closes = np.stack([eq_c, gold_close], axis=0)
        wants = np.stack([eq_target, ro_w], axis=0)
        return opens, closes, wants

    # R3 / R4
    gold_nan = np.isnan(gold_ret)
    bond_nan = np.isnan(bond_ret)
    chosen_bond = np.where(gold_nan & ~bond_nan, True,
                      np.where(bond_nan & ~gold_nan, False,
                      np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
    chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
    cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)

    if arm == "R3":
        w = 0.98
        bond_active = (~up) & chosen_bond & cond
        gold_active = (~up) & (~chosen_bond) & cond
        bond_w = np.where(bond_active, w, 0.0)
        gold_w = np.where(gold_active, w, 0.0)
    else:
        bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol > 0, bond_vol, 1.0)), 0.0)
        gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol > 0, gold_vol, 1.0)), 0.0)
        bond_active = (~up) & chosen_bond & cond
        gold_active = (~up) & (~chosen_bond) & cond
        bond_w = np.where(bond_active, bond_vt_w, 0.0)
        gold_w = np.where(gold_active, gold_vt_w, 0.0)

    opens = np.stack([eq_o, ro["BOND10"][0], ro["GOLD"][0]], axis=0)
    closes = np.stack([eq_c, bond_close, gold_close], axis=0)
    wants = np.stack([eq_target, bond_w, gold_w], axis=0)
    wants = cap_total(wants)
    eq_target_capped = None
    # cap total for all arms consistently
    wants = cap_total(np.stack([eq_target, (bond_w if arm in ("R3","R4") else np.zeros_like(eq_target)), (gold_w if arm in ("R3","R4") else np.zeros_like(eq_target))], axis=0)) if False else wants
    return opens, closes, wants


def cap_total(raw):
    arr = np.asarray(raw, dtype=float)
    if arr.ndim == 2:
        tot = arr.sum(axis=0, keepdims=True)
        f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
        return arr * f
    tot = arr.sum(axis=1, keepdims=True)
    f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return arr * f


def simple_stats(dates, nav, turn, ntr):
    st = trv2.curve_stats(dates, nav)
    years = trv2.calendar_years(dates[0], dates[-1])
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["sharpe"] = trv2.sharpe_daily(nav)
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    st["subperiods"] = {}
    for name, a, b in SUBPERIODS:
        ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
        ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
        yrs = trv2.calendar_years(dates[ia], dates[ib])
        st["subperiods"][name] = {"from": dates[ia], "to": dates[ib],
                                  "cagr": float(trv2.cagr(nav[ia], nav[ib], yrs)),
                                  "max_dd": float(trv2.max_dd(nav[ia:ib + 1])[0])}
    # 2017+ segment
    ia = max(k for k, d in enumerate(dates) if d <= "2016-12-31")
    ib = len(dates) - 1
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    st["2017_end"] = {"cagr": float(trv2.cagr(nav[ia], nav[ib], yrs)),
                      "max_dd": float(trv2.max_dd(nav[ia:ib + 1])[0])}
    return st


def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, y, y10 = load_main()
        up = e2_up(arr["QQQ"][1])
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                opens, closes, wants = main_legs_and_wants(days, arr, y, y10, spread, arm)
                wants = cap_total(wants)
                if wants.ndim == 2:
                    wants = wants[None, :, :] if wants.ndim == 2 else wants
                # simulate_multi expects [legs,T] or [P,legs,T]; make [1,legs,T]
                o = opens[None, :, :] if opens.ndim == 2 else opens
                c = closes[None, :, :] if closes.ndim == 2 else closes
                w = wants[None, :, :] if wants.ndim == 2 else wants
                nav, turn, ntr = trv2.simulate_multi(o, c, w, COST_PER_SIDE * m, START)
                dates = days[START - 1:]
                st = simple_stats(dates, nav[0], turn[0], ntr[0])
                hm[arm] = st
            res[md][f"m{m}"] = hm
        res["window"] = {"start": days[START], "end": days[-1], "sessions": len(days) - START}
    return res


def harness():
    # C0 harness vs ewma deviation (control) to 0.01pp
    ref = json.loads(DEVIATION.read_text())
    # run C0 at base only for harness check? Use run_main C0
    r = run_main(("C0",), m_list=(1, 2))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            rr = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = r[md][f"m{m}"]["C0"]
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": rr[k], "reproduced": x[k],
                                           "diff_pp": (x[k] - rr[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())
    return {"window": r["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100, "pass": bool(ok_dev)}


# ---- bootstrap (main window) adapted from leverage_frontier_v1
def boot_main(arms):
    days, arr, y, y10 = load_main()
    T = arr["QQQ"].shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = len(pool)
    Lp = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    # Factors per session (historical; carried into resampled paths by session index)
    bond_gross = lf.bond10_gross(y10)
    bond_on_arr = np.ones(T)
    bond_id_arr = bond_gross.copy()
    bond_id_arr[0] = 1.0
    gold_on_arr, gold_id_arr, _ = lf.gold_factors(days, arr)
    gold_on_arr = np.where(np.isnan(gold_on_arr), 1.0, gold_on_arr)
    gold_id_arr = np.where(np.isnan(gold_id_arr), 1.0, gold_id_arr)

    cg = {(md, a): [] for md in FIN for a in arms}

    # Draws: chunk-outer order, identical to ewma_band_v1 / signal_ensemble_v1 (seed, block-20, 3 back-extension).
    for chunk in range(0, PATHS, CHUNK):
        P = min(CHUNK, PATHS - chunk)
        pos = np.empty((P, Lp), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, Lp)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, Lp))
        for t in range(1, Lp):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]

        # base draws (QQQ price for E2 signal; QQQ total return for QLD* instrument)
        yy = y[np.clip(idx - 1, 0, T - 1)]
        prev = arr["QQQ"][1][idx - 1]
        r_on_tr = (arr["QQQ"][0][idx] + arr["QQQ"][2][idx]) / prev
        r_on_p = arr["QQQ"][0][idx] / prev
        r_id = arr["QQQ"][1][idx] / arr["QQQ"][0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        up = e2_up_nd(pc)
        up = np.atleast_2d(up)
        if up.shape[0] == 1 and P > 1:
            up = np.broadcast_to(up, (P, up.shape[1]))
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)

        eq_vol_cache = {}
        def eq_for(spread):
            eo, ec = lf.synthetic_L(qo, qc, np.zeros_like(qc), yyp + spread, 2, lf.EXPENSE[2.0], 0.0 if False else 0.0)
            # apply calibrated financing inside spread handled by caller passing yyp+spread to synthetic_L? synthetic_L expects spread param.
            return None

        # defensive factors resampled by session index
        def build_def_close(on_arr, id_arr):
            g_on = on_arr[idx]
            g_id = id_arr[idx]
            g_on_p = np.concatenate([np.ones((P, 1)), g_on], axis=1)
            g_id_p = np.concatenate([np.ones((P, 1)), g_id], axis=1)
            return lf.levels(g_on_p, g_id_p)[1]

        def build_def_open(on_arr, id_arr, idx_):
            g_on = on_arr[idx_]
            g_id = id_arr[idx_]
            g_on_p = np.concatenate([np.ones((P, 1)), g_on], axis=1)
            g_id_p = np.concatenate([np.ones((P, 1)), g_id], axis=1)
            return lf.levels(g_on_p, g_id_p)[0]

        bond_close = build_def_close(bond_on_arr, bond_id_arr)
        gold_close = build_def_close(gold_on_arr, gold_id_arr)

        for md in FIN:
            spread = FIN[md]
            eo, ec = lf.synthetic_L(qo, qc, np.zeros_like(qc), yyp, 2, lf.EXPENSE[2.0], spread)
            eq_vol = trv2.vol_n(ec)
            eq_target = up.astype(float) * np.minimum(CAP, trv2.vt_of(eq_vol))

            bond_vol = trv2.vol_n(bond_close)
            gold_vol = trv2.vol_n(gold_close)
            bond_ret = ret252_path(bond_close)
            gold_ret = ret252_path(gold_close)
            bills_gross = 1 + (yy - 0.0010) / 252
            bills_close = np.concatenate([np.ones((P, 1)), np.cumprod(bills_gross, axis=1)], axis=1)
            bills_ret = ret252_path(bills_close)

            bond_trend = trend_up_path_nd(bond_close)
            gold_trend = trend_up_path_nd(gold_close)

            gold_nan = np.isnan(gold_ret)
            bond_nan = np.isnan(bond_ret)
            chosen_bond = np.where(gold_nan & ~bond_nan, True,
                      np.where(bond_nan & ~gold_nan, False,
                      np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
            chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
            sel_cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)

            bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol > 0, bond_vol, 1.0)), 0.0)
            gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol > 0, gold_vol, 1.0)), 0.0)

            for arm in arms:
                if arm == "C0":
                    wants = eq_target[:, None, :]
                    opens = eo[:, None, :]
                    closes = ec[:, None, :]
                elif arm == "R1":
                    cond = bond_trend
                    ro_w = np.where(~up & cond, 0.98, 0.0)
                    wants = np.stack([eq_target, ro_w], axis=1)
                    opens = np.stack([eo, build_def_open(bond_on_arr, bond_id_arr, idx)], axis=1)
                    closes = np.stack([ec, bond_close], axis=1)
                elif arm == "R2":
                    cond = gold_trend
                    ro_w = np.where(~up & cond, 0.98, 0.0)
                    wants = np.stack([eq_target, ro_w], axis=1)
                    opens = np.stack([eo, build_def_open(gold_on_arr, gold_id_arr, idx)], axis=1)
                    closes = np.stack([ec, gold_close], axis=1)
                else:
                    bond_active = (~up) & chosen_bond & sel_cond
                    gold_active = (~up) & (~chosen_bond) & sel_cond
                    if arm == "R3":
                        bond_w = np.where(bond_active, 0.98, 0.0)
                        gold_w = np.where(gold_active, 0.98, 0.0)
                    else:
                        bond_w = np.where(bond_active, bond_vt_w, 0.0)
                        gold_w = np.where(gold_active, gold_vt_w, 0.0)
                    wants = np.stack([eq_target, bond_w, gold_w], axis=1)
                    opens = np.stack([eo, build_def_open(bond_on_arr, bond_id_arr, idx), build_def_open(gold_on_arr, gold_id_arr, idx)], axis=1)
                    closes = np.stack([ec, bond_close, gold_close], axis=1)
                wants = cap_total_path(wants)
                nav = trv2.simulate_multi(opens, closes, wants, COST_PER_SIDE * 2, start)[0]
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)

    out = {}
    for md in FIN:
        out[md] = {}
        c0 = np.concatenate(cg[(md, "C0")])
        for a in arms:
            c = np.concatenate(cg[(md, a)])
            d = c - c0
            out[md][a] = {"cagr_median": float(np.median(c)),
                          "diff_vs_C0_median": float(np.median(d)),
                          "share_ge_C0": float(np.mean(d >= 0)),
                          "paths": int(len(c))}
    return out


def trend_up_path_nd(close):
    # close [P,T]
    r = ret252_path(close)
    P, T = close.shape
    su = np.zeros_like(close, dtype=bool)
    for p in range(P):
        c = close[p]
        # rolling mean 200 (no look-ahead: window ending t)
        cs = np.cumsum(c)
        for t in range(199, T):
            m = (cs[t] - (cs[t-200] if t-200>=0 else 0)) / 200
            su[p, t] = c[t] > m
    return (r > 0) & su & ~np.isnan(r)




def trend_up_path(close):
    # close [P,T]
    r = ret252_path(close)
    su = np.zeros_like(close, dtype=bool)
    # sma200 up: close > rolling mean 200
    P, T = close.shape
    for p in range(P):
        c = close[p]
        for t in range(199, T):
            su[p, t] = c[t] > np.mean(c[t - 199:t + 1])
    return (r > 0) & su & ~np.isnan(r)


def ret252_path(close):
    c = np.asarray(close, dtype=float)
    out = np.full_like(c, np.nan, dtype=float)
    out[:, 252:] = c[:, 252:] / c[:, :-252] - 1
    return out


def build_def_open(on_arr, id_arr, idx):
    # returns open levels [P, Tpath]
    g_on = on_arr[idx]
    g_id = id_arr[idx]
    g_on_p = np.concatenate([np.ones((g_on.shape[0], 1)), g_on], axis=1)
    g_id_p = np.concatenate([np.ones((g_id.shape[0], 1)), g_id], axis=1)
    return lf.levels(g_on_p, g_id_p)[0]


def cap_total_path(raw):
    arr = np.asarray(raw, dtype=float)
    tot = arr.sum(axis=1, keepdims=True)
    f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
    return arr * f


def seg_cagr(dates, nav, a, b):
    ia = 0 if a is None or dates[0] > a else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    yrs = trv2.calendar_years(dates[ia], dates[ib])
    return float(trv2.cagr(nav[ia], nav[ib], yrs))


# ---- main



# ----------------------------------------------------------------------------- out-of-sample (reduced, pre-registered)
def _combined_y10_gspc(days):
    # DGS10 daily post-1962; GS10 monthly pre-1962 with 31-day publication lag
    raw_dgs = {}
    with open(ROOT / "data/historical/leverage_frontier_v1_dgs10.csv") as fh:
        for r in csv.DictReader(fh):
            if r["DGS10"] not in (".", ""):
                raw_dgs[r["observation_date"]] = float(r["DGS10"]) / 100.0
    gs_rows = []
    with open(ROOT / "data/historical/riskoff_rotation_v2_gs10.csv") as fh:
        for r in csv.DictReader(fh):
            if r["GS10"] not in (".", ""):
                gs_rows.append((r["observation_date"], float(r["GS10"]) / 100.0))
    # last available DGS with ffill
    last_dgs = None
    dgs_arr = [np.nan] * len(days)
    i = 0
    dgs_dates = sorted(raw_dgs)
    for idx, d in enumerate(days):
        while i < len(dgs_dates) and dgs_dates[i] <= d:
            last_dgs = raw_dgs[dgs_dates[i]]
            i += 1
        dgs_arr[idx] = last_dgs if last_dgs is not None else np.nan

    # GS lagged (monthly -> shift ~31 days)
    lagged = []
    for dstr, val in gs_rows:
        import datetime as _dt
        dt = _dt.date.fromisoformat(dstr) + _dt.timedelta(days=31)
        lagged.append((dt.isoformat(), val))
    gs_arr = [np.nan] * len(days)
    j = 0
    lagged_sorted = sorted(lagged)
    last_gs = None
    for idx, d in enumerate(days):
        while j < len(lagged_sorted) and lagged_sorted[j][0] <= d:
            last_gs = lagged_sorted[j][1]
            j += 1
        gs_arr[idx] = last_gs if last_gs is not None else np.nan

    return np.array([dgs_arr[i] if not np.isnan(dgs_arr[i]) else gs_arr[i] for i in range(len(days))], dtype=float)


def load_gspc_window():
    days, o, c, y, start = ewma.load_gspc()
    y10 = _combined_y10_gspc(days)
    return days, o, c, y, start, y10


def load_ixic_window():
    days, o, c, y, start = ewma.load_market("M1")
    # bond yields from DGS10 (ffill using leverage file, clipped to window)
    import csv as _csv
    raw = {}
    with open(ROOT / "data/historical/leverage_frontier_v1_dgs10.csv") as fh:
        for r in _csv.DictReader(fh):
            if r["DGS10"] not in (".", ""):
                raw[r["observation_date"]] = float(r["DGS10"]) / 100.0
    y10 = [np.nan] * len(days)
    last = None
    dates = sorted(raw)
    i = 0
    for idx, d in enumerate(days):
        while i < len(dates) and dates[i] <= d:
            last = raw[dates[i]]
            i += 1
        y10[idx] = last if last is not None else np.nan
    return days, o, c, y, start, np.array(y10, dtype=float)

def _monthly_yield_to_daily(days, path, col):
    import datetime as _dt
    import csv as _csv
    raw = {}
    with open(path, newline="") as fh:
        for r in _csv.DictReader(fh):
            v = r.get(col, r.get(col.upper()))
            if v in (None, "", "."):
                continue
            raw[r["observation_date"]] = float(v) / 100.0
    lagged = []
    for dstr, val in raw.items():
        dd = _dt.date.fromisoformat(dstr)
        y = dd.year + (1 if dd.month == 12 else 0)
        m = 1 if dd.month == 12 else dd.month + 1
        pub = _dt.date(y, m, 1).isoformat()
        lagged.append((pub, val))
    lagged.sort()
    out = [np.nan] * len(days)
    j = 0
    last = None
    for idx, d in enumerate(days):
        while j < len(lagged) and lagged[j][0] <= d:
            last = lagged[j][1]
            j += 1
        out[idx] = last if last is not None else np.nan
    return np.array(out, dtype=float)

def load_modern_market(key, yield_path, yield_col):
    days, o, c, y, start0 = ewma.load_market(key)
    y10 = _monthly_yield_to_daily(days, yield_path, yield_col)
    meas_from = "2001-01-02"
    try:
        start = next(i for i, d in enumerate(days) if d >= meas_from)
    except StopIteration:
        start = start0
    if start < start0:
        start = start0
    return days, o, c, y, start, y10

def _lf_arr_for_days(days):
    pos = {d: i for i, d in enumerate(days)}
    T = len(days)
    new = defaultdict(dict)
    with open(ROOT / "data/historical/leverage_frontier_v1_daily.csv") as fh:
        for r in csv.DictReader(fh):
            new[r["symbol"]][r["date"]] = (float(r["open"]), float(r["close"]), float(r["dividend"]))
    arr = {}
    for s in ("GLD", "GC=F"):
        a = np.full((3, T), np.nan)
        for d, v in new[s].items():
            if d in pos:
                a[:, pos[d]] = v
        arr[s] = a
    return arr

def _gold_splice(days):
    fix = gro.load_gold_fixings()
    gold_level_arr = gro.gold_level(days, fix, alignment="next")
    g_on0, g_id0 = gro.gold_factors(gold_level_arr)
    arr_lf = _lf_arr_for_days(days)
    g_on1, g_id1, _ = lf.gold_factors(days, arr_lf)
    g_on = np.where(np.isnan(g_on0), g_on1, g_on0)
    g_id = np.where(np.isnan(g_id0), g_id1, g_id0)
    return g_on, g_id, fix
def _prepare_window_arrays(days, o, c, y, y10):
    # GOLD factors
    g_on, g_id, fix = _gold_splice(days)
    # bond factors
    bond_gross = lf.bond10_gross(y10)
    bond_on = np.where(np.isnan(bond_gross), np.nan, 1.0)
    bond_id = bond_gross.copy()
    bond_id[0] = 1.0
    # bills
    bills_gross = lf.bills_gross(y)
    eq_o, eq_c = lf.synthetic_L(o, c, np.zeros_like(c), y + ewma.EXTRA_FIN, 2, lf.EXPENSE[2.0], 0.0)
    # GOLD fixed factor (play): make float arrays
    bond_opn, bond_close = lf.levels(bond_on, bond_id)
    gold_opn, gold_close = lf.levels(g_on, g_id)
    bills_opn, bills_close = lf.levels(np.where(np.isnan(bills_gross), np.nan, 1.0), bills_gross)
    return eq_o, eq_c, bond_opn, bond_close, gold_opn, gold_close, bills_opn, bills_close, fix


def run_oos_window(days, o, c, y, start, y10):
    eq_o, eq_c, bond_opn, bond_close, gold_opn, gold_close, bills_opn, bills_close, fix = _prepare_window_arrays(days, o, c, y, y10)
    eq_vol = trv2.vol_n(eq_c)
    up = ewma.e2_up(c)
    bond_vol = trv2.vol_n(bond_close)
    gold_vol = trv2.vol_n(gold_close)
    bond_ret = ret252(bond_close)
    gold_ret = ret252(gold_close)
    bills_ret = ret252(bills_close)
    bond_trend = trend_up(bond_close)
    gold_trend = trend_up(gold_close)

    eq_target = up.astype(float) * np.minimum(CAP, trv2.vt_of(eq_vol))

    gold_nan = np.isnan(gold_ret)
    bond_nan = np.isnan(bond_ret)
    chosen_bond = np.where(gold_nan & ~bond_nan, True,
                  np.where(bond_nan & ~gold_nan, False,
                  np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
    chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
    cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)

    bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol > 0, bond_vol, 1.0)), 0.0)
    gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol > 0, gold_vol, 1.0)), 0.0)
    bond_active = (~up) & chosen_bond & cond
    gold_active = (~up) & (~chosen_bond) & cond

    def sim_for(weights):
        # weights: target array shape [legs,T]
        return trv2.simulate_multi(opens, closes, weights, COST_PER_SIDE * 2, start)

    # C0
    opens_c0 = eq_o[None, :]
    closes_c0 = eq_c[None, :]
    wants_c0 = eq_target[None, :]
    nav_c0, _, _ = trv2.simulate_multi(opens_c0, closes_c0, wants_c0, COST_PER_SIDE * 2, start)
    # R4
    bond_w = np.where(bond_active, bond_vt_w, 0.0)
    gold_w = np.where(gold_active, gold_vt_w, 0.0)
    eq_target = np.ravel(eq_target)
    bond_w = np.ravel(bond_w)
    gold_w = np.ravel(gold_w)
    opens_r4 = np.stack([np.ravel(eq_o), np.ravel(bond_opn), np.ravel(gold_opn)], axis=0)
    closes_r4 = np.stack([np.ravel(eq_c), np.ravel(bond_close), np.ravel(gold_close)], axis=0)
    wants_r4 = np.stack([eq_target, bond_w, gold_w], axis=0)
    wants_r4 = cap_total(wants_r4)
    nav_r4, _, _ = trv2.simulate_multi(opens_r4[None, :, :], closes_r4[None, :, :], wants_r4[None, :, :], COST_PER_SIDE * 2, start)

    dates = days[start - 1:]
    def stats(nav):
        nav = nav[0] if nav.ndim == 2 else nav
        return {
            "cagr": float(trv2.cagr(nav[0], nav[-1], trv2.calendar_years(dates[0], dates[-1]))),
            "max_dd": float(trv2.max_dd(nav)[0]),
        }
    return {
        "window": {"start": dates[0], "end": dates[-1], "sessions": len(dates) - 1},
        "C0": stats(nav_c0),
        "R4": stats(nav_r4),
    }


def boot_window(days, o, c, y, start, y10, rng):
    # per-market block bootstrap for C0 and R4
    T = len(days)
    pool = np.arange(start, T)
    npool = len(pool)
    Lp = getattr(ewma, "OOS_BOOT_L", ewma.TSM_N + ewma.MEASURED)
    paths = getattr(ewma, "OOS_PATHS_PER_MARKET", 2000)
    chunk = CHUNK
    c0_acc = []
    r4_acc = []
    # factor arrays by session
    g_on_arr, g_id_arr, fix = _gold_splice(days)
    bond_gross = lf.bond10_gross(y10)
    bond_on_arr = np.where(np.isnan(bond_gross), np.nan, 1.0)
    bond_id_arr = bond_gross.copy()
    bond_id_arr[0] = 1.0
    bills_gross = lf.bills_gross(y)
    gold_on_arr = np.where(np.isnan(g_on_arr), 1.0, g_on_arr)
    gold_id_arr = np.where(np.isnan(g_id_arr), 1.0, g_id_arr)
    bond_on_arr = np.where(np.isnan(bond_on_arr), 1.0, bond_on_arr)
    bond_id_arr = np.where(np.isnan(bond_id_arr), 1.0, bond_id_arr)

    for ch in range(0, paths, chunk):
        P = min(chunk, paths - ch)
        pos = np.empty((P, Lp - 0), dtype=np.int64)
        # draw positions in pool with back-extension
        pos0 = np.empty((P, Lp), dtype=np.int64)
        pos0[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, Lp)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, Lp))
        for t in range(1, Lp):
            pos0[:, t] = np.where(jump[:, t], fresh[:, t], (pos0[:, t - 1] + 1) % npool)
        pre = np.stack([(pos0[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
        pos0 = np.concatenate([pre, pos0], axis=1)
        idx = pool[pos0]

        # index price factors
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        one = np.ones((P, 1))
        pc = np.concatenate([one, np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([one, pc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        up = ewma.e2_up(pc)
        up = np.atleast_2d(up)
        if up.shape[0] == 1 and P > 1:
            up = np.broadcast_to(up, (P, up.shape[1]))
        eq_o, eq_c = lf.synthetic_L(oo, pc, np.zeros_like(pc), yyp + ewma.EXTRA_FIN, 2, lf.EXPENSE[2.0], 0.0)

        eq_vol = trv2.vol_n(eq_c)
        eq_target = up.astype(float) * np.minimum(CAP, trv2.vt_of(eq_vol))

        # defensive factors resampled
        def build_close(on_arr, id_arr):
            g_on = on_arr[idx]
            g_id = id_arr[idx]
            g_on_p = np.concatenate([np.ones((P, 1)), g_on], axis=1)
            g_id_p = np.concatenate([np.ones((P, 1)), g_id], axis=1)
            return lf.levels(g_on_p, g_id_p)[1]

        def build_open(on_arr, id_arr):
            g_on = on_arr[idx]
            g_id = id_arr[idx]
            g_on_p = np.concatenate([np.ones((P, 1)), g_on], axis=1)
            g_id_p = np.concatenate([np.ones((P, 1)), g_id], axis=1)
            return lf.levels(g_on_p, g_id_p)[0]

        bond_close = build_close(bond_on_arr, bond_id_arr)
        gold_close = build_close(gold_on_arr, gold_id_arr)

        bond_ret = ret252_path(bond_close)
        gold_ret = ret252_path(gold_close)
        bills_gross = 1 + (yy - 0.0010) / 252
        bills_close = np.concatenate([np.ones((P, 1)), np.cumprod(bills_gross, axis=1)], axis=1)
        bills_ret = ret252_path(bills_close)

        bond_vol = trv2.vol_n(bond_close)
        gold_vol = trv2.vol_n(gold_close)
        bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol > 0, bond_vol, 1.0)), 0.0)
        gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol > 0, gold_vol, 1.0)), 0.0)

        gold_nan = np.isnan(gold_ret)
        bond_nan = np.isnan(bond_ret)
        chosen_bond = np.where(gold_nan & ~bond_nan, True,
                      np.where(bond_nan & ~gold_nan, False,
                      np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
        chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
        cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)

        bond_active = (~up) & chosen_bond & cond
        gold_active = (~up) & (~chosen_bond) & cond
        bond_w = np.where(bond_active, bond_vt_w, 0.0)
        gold_w = np.where(gold_active, gold_vt_w, 0.0)

        # C0 sim
        nav_c0 = trv2.simulate_multi(eq_o[:, None, :], eq_c[:, None, :], eq_target[:, None, :], COST_PER_SIDE * 2, START)[0]
        c0_acc.append((nav_c0[:, -1] / nav_c0[:, 0]) ** (252 / (nav_c0.shape[1] - 1)))

        # R4 sim
        opens_r4 = np.stack([eq_o, build_open(bond_on_arr, bond_id_arr), build_open(gold_on_arr, gold_id_arr)], axis=1)
        closes_r4 = np.stack([eq_c, bond_close, gold_close], axis=1)
        wants_r4 = np.stack([eq_target, bond_w, gold_w], axis=1)
        # cap total pathwise
        tot = wants_r4.sum(axis=1, keepdims=True)
        f = np.where(tot > CAP, CAP / np.where(tot > 0, tot, 1.0), 1.0)
        wants_r4 = wants_r4 * f
        nav_r4 = trv2.simulate_multi(opens_r4, closes_r4, wants_r4, COST_PER_SIDE * 2, START)[0]
        r4_acc.append((nav_r4[:, -1] / nav_r4[:, 0]) ** (252 / (nav_r4.shape[1] - 1)))

    c0 = np.concatenate(c0_acc)
    r4 = np.concatenate(r4_acc)
    return c0, r4

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        if mode == "--harness":
            sys.exit(0 if hc["pass"] else 1)
        if not hc["pass"]:
            sys.exit(1)

    print("running main window...")
    main_res = run_main(ARMS, extras=True)
    print("running bootstrap...")
    boot = boot_main(ARMS)

    # OOS
    print("running OOS GSPC...")
    oos_gspc = run_oos_window(*load_gspc_window()[:5], * (load_gspc_window()[5],)) if False else None
    # call cleanly:
    gd, go, gc, gy, gstart, gy10 = load_gspc_window()
    oos_gspc = run_oos_window(gd, go, gc, gy, gstart, gy10)
    print("running OOS IXIC...")
    idd, io, ic, iy, istart, iy10 = load_ixic_window()
    oos_ixic = run_oos_window(idd, io, ic, iy, istart, iy10)
    oos = {"GSPC": oos_gspc, "M1": oos_ixic}
    modern_boot_c0 = []
    modern_boot_r4 = []
    rng = np.random.default_rng(SEED)
    modern_specs = [
        ("M3", ROOT / "data/historical/riskoff_rotation_v2_irltlt_jpy.csv", "IRLTLT01JPM156N"),
        ("M4", ROOT / "data/historical/riskoff_rotation_v2_irltlt_dem.csv", "IRLTLT01DEM156N"),
        ("M5", ROOT / "data/historical/riskoff_rotation_v2_irltlt_gbp.csv", "IRLTLT01GBM156N"),
    ]
    for key, yp, yc in modern_specs:
        print(f"running OOS {key}...")
        md, mo, mc, my, mstart, my10 = load_modern_market(key, yp, yc)
        oos[key] = run_oos_window(md, mo, mc, my, mstart, my10)
        print(f"running OOS bootstrap {key}...")
        c0x, r4x = boot_window(md, mo, mc, my, mstart, my10, rng)
        modern_boot_c0.append(c0x)
        modern_boot_r4.append(r4x)
    # override oos (already includes modern above)
    print("running OOS bootstrap GSPC...")
    c0g, r4g = boot_window(gd, go, gc, gy, gstart, gy10, rng)
    print("running OOS bootstrap IXIC...")
    c0i, r4i = boot_window(idd, io, ic, iy, istart, iy10, rng)
    pooled_c0 = np.concatenate([c0g, c0i] + modern_boot_c0)
    pooled_r4 = np.concatenate([r4g, r4i] + modern_boot_r4)
    dd = pooled_r4 - pooled_c0

    oos_boot = {"pooled": {"R4": {"diff_vs_C0_median": float(np.median(dd)),
                                   "diff_vs_C0_mean": float(np.mean(dd)),
                                   "share_ge_C0": float(np.mean(dd >= 0)),
                                   "paths": int(len(dd))}}}
    # cost stress: R4 G1 under 8 bps on main window (reuse main weights? rerun quickly for candidate only)
    main_stress = {}
    for md in FIN:
        main_stress[md] = {}
        days, arr, y, y10 = load_main()
        # build R4 wants once
        opens, closes, wants = main_legs_and_wants(days, arr, y, y10, FIN[md], "R4")
        wants = cap_total(wants)
        o = opens[None, :, :] if opens.ndim == 2 else opens
        c = closes[None, :, :] if closes.ndim == 2 else closes
        w = wants[None, :, :] if wants.ndim == 2 else wants
        # C0 for comparison
        o0, c0_, w0 = main_legs_and_wants(days, arr, y, y10, FIN[md], "C0")
        w0 = cap_total(w0)
        o0 = o0[None, :, :] if o0.ndim == 2 else o0
        c0_ = c0_[None, :, :] if c0_.ndim == 2 else c0_
        w0 = w0[None, :, :] if w0.ndim == 2 else w0
        nav_r4_s, _, _ = trv2.simulate_multi(o, c, w, COST_PER_SIDE * 2 * 2, START)
        nav_c0_s, _, _ = trv2.simulate_multi(o0, c0_, w0, COST_PER_SIDE * 2 * 2, START)
        dates = days[START - 1:]
        # cagr both
        navr = nav_r4_s[0]; navc = nav_c0_s[0]
        yrs = trv2.calendar_years(dates[0], dates[-1])
        main_stress[md] = {"R4_cagr": float(trv2.cagr(navr[0], navr[-1], yrs)),
                          "C0_cagr": float(trv2.cagr(navc[0], navc[-1], yrs))}

    gates = {}
    for arm in ("R4",):
        g = {}
        for md in FIN:
            a2 = main_res[md]["m2"][arm]; c2 = main_res[md]["m2"]["C0"]
            a1 = main_res[md]["m1"][arm]; c1 = main_res[md]["m1"]["C0"]
            g[f"G1_{md}"] = bool(a2["cagr"] >= c2["cagr"])
            g[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
            g[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
            g[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
            g[f"G6_{md}"] = bool(a1["cagr"] >= c1["cagr"])
        a2 = main_res["base"]["m2"][arm]; c2 = main_res["base"]["m2"]["C0"]
        wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
        g["G4"] = bool(len(wins) >= 2)
        g["G4_wins"] = wins
        # G9 replaced by cost stress: R4 still G1 under doubled cost
        g["G9_cost_stress"] = bool(main_stress["base"]["R4_cagr"] >= main_stress["base"]["C0_cagr"] and
                                    main_stress["calibrated"]["R4_cagr"] >= main_stress["calibrated"]["C0_cagr"])
        # OOS G7
        dc = {k: oos[k]["R4"]["cagr"] - oos[k]["C0"]["cagr"] for k in oos}
        dd_ = {k: oos[k]["R4"]["max_dd"] - oos[k]["C0"]["max_dd"] for k in oos}
        med = float(np.median(list(dc.values())))
        n_c = sum(1 for v in dc.values() if v >= -0.005)
        n_d = sum(1 for v in dd_.values() if v <= 0.03 + 1e-12)
        g["G7"] = bool(med >= 0 and n_c >= len(oos) - 1 and n_d >= len(oos) - 1 and max(dd_.values()) <= 0.08 + 1e-12)
        g["G7_detail"] = {"cagr_diff": dc, "max_dd_diff": dd_, "median_cagr_diff": med,
                          "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                          "max_dd_diff_max": float(max(dd_.values()))}
        g["G8"] = bool(oos_boot["pooled"]["R4"]["diff_vs_C0_median"] >= 0)
        g["G8_pooled_median_diff"] = float(oos_boot["pooled"]["R4"]["diff_vs_C0_median"])
        g["cost_stress_detail"] = main_stress
        g["pass_all"] = bool(all(g[k] for k in (["G1_base","G1_calibrated","G2_base","G2_calibrated","G3_base","G3_calibrated","G5_base","G5_calibrated","G6_base","G6_calibrated"] + ["G4","G9_cost_stress","G7","G8"])))
        gates["R4"] = g

    # Also include main window results for only? keep ARMS subset? run_main already all arms
    out = {
        "campaign": "riskoff_rotation_v2",
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "status": "complete",
    }
    def conv(o):
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, (np.floating,)): return float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        if isinstance(o, dict): return {k: conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [conv(v) for v in o]
        return o
    OUT.write_text(json.dumps(conv(out), indent=2, sort_keys=True) + "\n")

    # summary
    w = main_res["window"]
    L = [f"riskoff_rotation_v2 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions)",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'}", ""]
    L.append("main: arm CAGR maxDD 2017+ turn/yr | boot20 median diff")
    for arm in ARMS:
        for md in FIN:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            L.append(f"  {md} {arm:3s} {a['cagr']*100:7.2f}% {a['max_dd']*100:6.2f}% {a['2017_end']['cagr']*100:7.2f}% {a['turnover_annual']:5.2f} | {b['cagr_median']*100:7.2f}% {b['diff_vs_C0_median']*100:+.3f}pp")
    L.append("")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))




if __name__ == "__main__":
    main()
