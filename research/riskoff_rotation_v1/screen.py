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
ARMS = ("C0", "R1", "R2", "R3", "R4")


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
    pool = np.arange(lf.SMA_MAX + lf.EXT if hasattr(lf, "EXT") else 200, T)
    # follow ewma: pool arange(1,T)
    pool = np.arange(1, T)
    npool = len(pool)
    Lp = WARM + MEASURED
    start = START
    # precompute bond/gold hist factors arrays indexed by session
    # bond gross factors depend on y10 (DGS); map to sessions via load (already aligned days)
    # Use lf.bond10_gross(y10) already per session; as hist factor, overnight=1 (bond all intraday), intraday=gross
    bond_gross = lf.bond10_gross(y10)  # [T] gross per session (index0 nan)
    bond_on_arr = np.ones(T)
    bond_id_arr = bond_gross.copy()
    bond_id_arr[0] = 1.0
    gold_on_arr, gold_id_arr, _ = lf.gold_factors(days, arr)
    gold_on_arr = np.where(np.isnan(gold_on_arr), 1.0, gold_on_arr)
    gold_id_arr = np.where(np.isnan(gold_id_arr), 1.0, gold_id_arr)

    cg = {(md, a): [] for md in FIN for a in arms}
    for md in FIN:
        spread = FIN[md]
        for chunk in range(0, PATHS, CHUNK):
            P = min(CHUNK, PATHS - chunk)
            # positions
            pos = np.empty((P, Lp), dtype=np.int64)
            pos[:, 0] = rng.integers(0, npool, P)
            jump = rng.random((P, Lp)) < 1 / BLOCK
            fresh = rng.integers(0, npool, (P, Lp))
            for t in range(1, Lp):
                pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
            pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
            pos = np.concatenate([pre, pos], axis=1)
            idx = pool[pos]
            # y mapping by session index: y is per session; idx global session indices. need y array
            # y is dtb3 per day aligned; use y (already length T), indexed by idx-1 for previous close date
            yy = y[np.clip(idx - 1, 0, T - 1)]
            # QQQ leg
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
            # eq synthetic
            eq_o, eq_c = lf.synthetic_L(qo, qc, np.zeros_like(qc), yyp, 2, lf.EXPENSE[2.0], spread)
            eq_vol = trv2.vol_n(eq_c)
            # defensive closes via factors indexed by idx
            def build_def_close(on_arr, id_arr):
                g_on = on_arr[idx]
                g_id = id_arr[idx]
                g_on_p = np.concatenate([np.ones((P, 1)), g_on], axis=1)
                g_id_p = np.concatenate([np.ones((P, 1)), g_id], axis=1)
                return lf.levels(g_on_p, g_id_p)[1]  # close
            # Need gold_on_arr etc could have nan; fill
            bond_close = build_def_close(bond_on_arr, bond_id_arr)
            gold_close = build_def_close(gold_on_arr, gold_id_arr)
            # bills close
            bills_gross = 1 + (yy - 0.0010) / 252
            bills_close = np.concatenate([np.ones((P, 1)), np.cumprod(bills_gross, axis=1)], axis=1)

            for arm in arms:
                # build wants per path
                eq_target = up.astype(float) * np.minimum(CAP, trv2.vt_of(eq_vol))
                if arm == "C0":
                    wants = eq_target[:, None, :]
                    opens = eq_o[:, None, :]
                    closes = eq_c[:, None, :]
                elif arm == "R1":
                    bond_tr = trend_up(bond_close) if False else None
                    # compute trend per path
                    btr = trend_up_path(bond_close)
                    cond = btr
                    ro_w = np.where(~up & cond, 0.98, 0.0)
                    wants = np.stack([eq_target, ro_w], axis=1)
                    opens = np.stack([eq_o, build_def_open(bond_on_arr, bond_id_arr, idx)], axis=1)
                    closes = np.stack([eq_c, bond_close], axis=1)
                elif arm == "R2":
                    gtr = trend_up_path(gold_close)
                    cond = gtr
                    ro_w = np.where(~up & cond, 0.98, 0.0)
                    wants = np.stack([eq_target, ro_w], axis=1)
                    opens = np.stack([eq_o, build_def_open(gold_on_arr, gold_id_arr, idx)], axis=1)
                    closes = np.stack([eq_c, gold_close], axis=1)
                else:
                    # R3/R4
                    bond_ret = ret252_path(bond_close)
                    gold_ret = ret252_path(gold_close)
                    bills_ret = ret252_path(bills_close)
                    gold_nan = np.isnan(gold_ret)
                    bond_nan = np.isnan(bond_ret)
                    chosen_bond = np.where(gold_nan & ~bond_nan, True,
                                  np.where(bond_nan & ~gold_nan, False,
                                  np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
                    chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
                    cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)
                    upb = up
                    if arm == "R3":
                        w = 0.98
                    else:
                        bond_vol = trv2.vol_n(bond_close)
                        gold_vol = trv2.vol_n(gold_close)
                        bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol > 0, bond_vol, 1.0)), 0.0)
                        gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol > 0, gold_vol, 1.0)), 0.0)
                        w = None
                    bond_active = (~upb) & chosen_bond & cond
                    gold_active = (~upb) & (~chosen_bond) & cond
                    if arm == "R3":
                        bond_w = np.where(bond_active, 0.98, 0.0)
                        gold_w = np.where(gold_active, 0.98, 0.0)
                    else:
                        bond_w = np.where(bond_active, bond_vt_w, 0.0)
                        gold_w = np.where(gold_active, gold_vt_w, 0.0)
                    wants = np.stack([eq_target, bond_w, gold_w], axis=1)
                    opens = np.stack([eq_o, build_def_open(bond_on_arr, bond_id_arr, idx), build_def_open(gold_on_arr, gold_id_arr, idx)], axis=1)
                    closes = np.stack([eq_c, bond_close, gold_close], axis=1)
                # cap total
                wants = cap_total_path(wants)
                nav = trv2.simulate_multi(opens, closes, wants, COST_PER_SIDE * 2, start)[0]
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    # summarize
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
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        if mode == "--harness":
            sys.exit(0 if hc["pass"] else 1)
        # continue if run? only if harness pass
        if not hc["pass"]:
            sys.exit(1)

    print("running main window...")
    main_res = run_main(ARMS, extras=True)
    print("running bootstrap...")
    boot = boot_main(ARMS)

    gates = {}
    for arm in ("R1", "R2", "R3", "R4"):
        g = {}
        # G1 both financings
        for md in FIN:
            a2 = main_res[md]["m2"][arm]; c2 = main_res[md]["m2"]["C0"]
            a1 = main_res[md]["m1"][arm]; c1 = main_res[md]["m1"]["C0"]
            g[f"G1_{md}"] = bool(a2["cagr"] >= c2["cagr"])
            g[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
            g[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
            g[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
            g[f"G6_{md}"] = bool(a1["cagr"] >= c1["cagr"])
        # G4
        a2 = main_res["base"]["m2"][arm]; c2 = main_res["base"]["m2"]["C0"]
        wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
        g["G4"] = bool(len(wins) >= 2)
        g["G4_wins"] = wins
        g["G9"] = bool(a2["turnover_annual"] <= c2["turnover_annual"] * 2.0)
        g["G7"] = False  # no OOS run
        g["G8"] = False
        core = [f"{gg}_{md}" for gg in ("G1", "G2", "G3") for md in FIN]
        g["pass_all"] = bool(all(g[k] for k in ([f"{gg}_{md}" for gg in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G9"])))
        g["core_G1_G3"] = bool(all(g[k] for k in core))
        gates[arm] = g

    # Write results
    out = {
        "campaign": "riskoff_rotation_v1",
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "gates": gates,
        "status": "complete",
    }
    # make serializable (numpy -> python)
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
    L = [f"riskoff_rotation_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions)",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD 2017+ turn/yr | boot20 median diff")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            L.append(f"  {arm:3s} {a['cagr']*100:7.2f}% {a['max_dd']*100:6.2f}% {a['2017_end']['cagr']*100:7.2f}% {a['turnover_annual']:5.2f} | {b['cagr_median']*100:7.2f}% {b['diff_vs_C0_median']*100:+.3f}pp")
        L.append("")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
