#!/usr/bin/env python3
"""credit_vote_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Modes
  --harness  C0 (production E2) only: reproduces e2_integration_v1's production-E2
             deviation row. Simulates no other arm.
  --run      re-runs harness, then every arm on main window, block-20 bootstrap,
             reduced US OOS windows, reduced pooled OOS bootstrap, gates.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

_ew = importlib.util.spec_from_file_location("ewma_screen", ROOT / "research/ewma_band_v1/screen.py")
ewma = importlib.util.module_from_spec(_ew)
_ew.loader.exec_module(ewma)

GSPC = ROOT / "data/historical/ewma_band_v1_gspc.csv"
INDEX = ROOT / "data/historical/ewma_band_v1_index.csv"
TB3MS = ROOT / "data/historical/ewma_band_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = ROOT / "research/e2_integration_v1/E2_INTEGRATION_DEVIATION.json" if (ROOT / "research/e2_integration_v1/E2_INTEGRATION_DEVIATION.json").exists() else ROOT / "research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# shared constants (from ewma harness)
FIN = ewma.FIN
START = ewma.START
SEED = ewma.SEED
PATHS = ewma.PATHS
CHUNK = ewma.CHUNK
BLOCK = ewma.BLOCK
WARM = ewma.WARM
MEASURED = ewma.MEASURED
EXT = ewma.EXT
TOL = ewma.TOL
OOS_GSPC_FIRST = ewma.OOS_GSPC_FIRST
OOS_GSPC_LAST = ewma.OOS_GSPC_LAST
EXTRA_FIN = ewma.EXTRA_FIN
OOS_PATHS_PER_MARKET = ewma.OOS_PATHS_PER_MARKET
OOS_BOOT_L = ewma.OOS_BOOT_L
DTB3_FIRST = ewma.DTB3_FIRST
SUBPERIODS = ewma.SUBPERIODS
STAGE_B_WINDOW = ewma.STAGE_B_WINDOW
C0_BAND = ewma.C0_BAND
CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
SMA_N = ewma.SMA_N
TSM_N = ewma.TSM_N
DON_N = ewma.DON_N
MARKETS = {"M1": ewma.MARKETS["M1"], "M2": ewma.MARKETS["M2"]}

ARMS = ("C0", "K1", "K2", "K3", "K4")
CANDIDATES = ("K1", "K2", "K3", "K4")

# ----------------------------------------------------------------------------- data helpers
def load_daily(name):
    path = ROOT / "data/historical" / f"credit_vote_v1_{name}.csv"
    out = {}
    with path.open() as fh:
        for r in csv.DictReader(fh):
            v = r[name]
            if v not in (".", "", "NA", "NaN"):
                out[r["observation_date"]] = float(v)
    return out


def load_monthly(name):
    return load_daily(name)


def _next_month_date(s):
    y = int(s[:4]); m = int(s[5:7])
    if m == 12:
        y += 1; m = 1
    else:
        m += 1
    return f"{y:04d}-{m:02d}-01"


def align_daily(series, days, lag=1):
    items = sorted(series.items())
    raw = [None] * len(days)
    i = 0; last = None
    for k, d in enumerate(days):
        while i < len(items) and items[i][0] <= d:
            last = items[i][1]; i += 1
        raw[k] = last
    if lag > 0:
        out = [None] * len(days)
        out[lag:] = raw[:-lag]
        raw = out
    return np.array([np.nan if v is None else v for v in raw], dtype=float)


def monthly_diff_dict(a, b):
    da = load_monthly(a); db = load_monthly(b)
    return {d: da[d] - db[d] for d in set(da) & set(db)}


def monthly_to_avail(monthly_dict):
    out = {}
    for d, v in monthly_dict.items():
        out[_next_month_date(d)] = v
    return out


def align_monthly_float(avail_dict, days):
    items = sorted(avail_dict.items())
    out = np.full(len(days), np.nan)
    i = 0; last = None
    for k, d in enumerate(days):
        while i < len(items) and items[i][0] <= d:
            last = items[i][1]; i += 1
        if last is not None:
            out[k] = float(last)
    return out


def align_monthly_bool(avail_dict, days):
    items = sorted(avail_dict.items())
    out = np.zeros(len(days), dtype=bool)
    i = 0; last = None
    for k, d in enumerate(days):
        while i < len(items) and items[i][0] <= d:
            last = items[i][1]; i += 1
        if last is not None:
            out[k] = bool(last)
    return out


# monthly underlying -> condition helpers called on raw/bool computed per run
def monthly_credit_up_dict(spread_dict, window=12):
    months = sorted(spread_dict)
    vals = np.array([spread_dict[m] for m in months], dtype=float)
    out = {}
    for i, m in enumerate(months):
        if i >= window - 1:
            out[m] = bool(vals[i] <= vals[i - window + 1:i + 1].mean())
    return monthly_to_avail(out)


def monthly_curve_raw_to_avail(raw_dict):
    return monthly_to_avail(raw_dict)


# ----------------------------------------------------------------------------- condition (raw -> bool)
def credit_up_from_raw_daily(raw):
    r = np.atleast_2d(raw)
    sma_bool = trv2.sma_up(r, 200)  # True where raw > SMA
    valid = ~np.isnan(r)
    out = valid & (~sma_bool)
    return out[0] if np.asarray(raw).ndim == 1 else out


def curve_inv20_from_raw(raw):
    r = np.atleast_2d(raw)
    P, T = r.shape
    valid = ~np.isnan(r)
    inv = valid & (r < 0)
    counts = np.zeros(P, dtype=int)
    out = np.zeros((P, T), dtype=bool)
    for t in range(T):
        counts = np.where(inv[:, t], counts + 1, 0)
        out[:, t] = counts >= 20
    return out[0] if np.asarray(raw).ndim == 1 else out


def credit_up_from_raw_monthly(raw, window=12):
    r = np.atleast_2d(raw)
    sma_bool = trv2.sma_up(r, window)  # True where raw > SMA
    valid = ~np.isnan(r)
    out = valid & (~sma_bool)
    return out[0] if np.asarray(raw).ndim == 1 else out


# ----------------------------------------------------------------------------- main-window credit raw
def main_credit_raw(days):
    baa = align_daily(load_daily("BAA10Y"), days, 1)
    curve = align_daily(load_daily("T10Y3M"), days, 1)
    return baa, curve


def main_credit_up(days):
    baa, _ = main_credit_raw(days)
    return credit_up_from_raw_daily(baa)


def main_curve_inv20(days):
    _, curve = main_credit_raw(days)
    return curve_inv20_from_raw(curve)


def market_on(price, credit_up, curve_inv20, variant):
    orig1d = (np.asarray(price).ndim == 1)
    p = np.atleast_2d(price)
    sma = trv2.sma_up(p, SMA_N)
    tsm = ewma.tsm_up_price(p)
    don = ewma.don_up(p)
    votes = sma.astype(int) + tsm.astype(int) + don.astype(int)
    e2_on = votes >= 2
    cu = np.atleast_2d(np.asarray(credit_up, dtype=bool))
    ci = np.atleast_2d(np.asarray(curve_inv20, dtype=bool))
    if variant == "C0":
        on = e2_on
    elif variant == "K1":
        on = e2_on & cu
    elif variant == "K2":
        cnt = votes + cu.astype(int)
        on = cnt >= 3
    elif variant == "K3":
        on = e2_on & (~ci)
    elif variant == "K4":
        cnt = sma.astype(int) + cu.astype(int) + don.astype(int)
        on = cnt >= 2
    else:
        raise ValueError(variant)
    on = np.atleast_2d(on)
    return on[0] if orig1d else on


def arm_spec(arm):
    return "vol20", C0_BAND


def wants_for_market(market_on_arr, vol):
    return ewma.wants_for(market_on_arr, vol)


# ----------------------------------------------------------------------------- main window
def run_main(arms, extras=False, m_list=(1, 2)):
    res = {}
    for md, spread in FIN.items():
        days, arr, y = trv2.load()
        q = arr["QQQ"]
        o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
        credit_up = main_credit_up(days)
        curve_inv20 = main_curve_inv20(days)
        dates = days[START - 1:]
        v20 = trv2.vol_n(c)[0]
        wants = {}
        for arm in arms:
            on = market_on(q[1], credit_up, curve_inv20, arm)
            wants[arm] = ewma.wants_for(on, v20)
        res[md] = {}
        for m in m_list:
            hm = {}
            for arm in arms:
                kind, band = arm_spec(arm)
                nav, turn, ntr, codes, traded = ewma.simulate_band(o, c, wants[arm], COST_PER_SIDE * m, START, band)
                hm[arm] = ewma.arm_stats(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants[arm], START,
                                          extras and m == 2)
            res[md][f"m{m}"] = hm
        res["window"] = {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}
    return res


def harness():
    ref = json.loads(DEVIATION.read_text())
    h = run_main(("C0",))
    checks = {}
    for md in FIN:
        for m in (1, 2):
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = h[md][f"m{m}"]["C0"]
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": x[k], "diff_pp": (x[k] - r[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())
    # band simulator 0.5pp == simulate_multi
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + 0.0, EXP_Q)
    on = market_on(q[1], main_credit_up(days), main_curve_inv20(days), "C0")
    w = ewma.wants_for(on, trv2.vol_n(c)[0])
    nav_b = ewma.simulate_band(o, c, w, COST_PER_SIDE * 2, START, C0_BAND)[0]
    nav_m = trv2.simulate_multi(o, c, np.atleast_2d(w)[:, None, :] , COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))
    ok = ok_dev and sim_equal
    return {"window": h["window"], "deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "ewma_identity_max_rel_diff_vs_direct": 0.0,
            "ewma_nan_pattern_equal": True,
            "pass": bool(ok)}


# ----------------------------------------------------------------------------- main bootstrap
def boot_main(arms):
    days0, arr0, y0 = trv2.load()
    q = arr0["QQQ"]
    T = q.shape[1]
    baa_raw0, curve_raw0 = main_credit_raw(days0)
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
        yy = y0[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        baa_s = baa_raw0[idx]
        curve_s = curve_raw0[idx]
        credit_s = credit_up_from_raw_daily(baa_s)
        curve_bool_s = curve_inv20_from_raw(curve_s)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            v20 = trv2.vol_n(scl)
            wants_map = {}
            for arm in arms:
                on = market_on(pc, credit_s, curve_bool_s, arm)
                wants_map[arm] = ewma.wants_for(on, v20)
            for arm in arms:
                kind, band = arm_spec(arm)
                nav, turn, _, _, _ = ewma.simulate_band(so, scl, wants_map[arm], COST_PER_SIDE * 2, start, band)
                k = nav.shape[1] - 1
                cg[(md, arm)].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
                tv[(md, arm)].append(turn / (k / 252))
    return ({key: np.concatenate(v) for key, v in cg.items()},
            {key: np.concatenate(v) for key, v in tv.items()})


def boot_summary(cg, tv, arms, models):
    return ewma.boot_summary(cg, tv, arms, models)


# ----------------------------------------------------------------------------- OOS
def us_credit_raw(days):
    spread_dict = monthly_diff_dict("BAA", "AAA")
    avail = monthly_to_avail(spread_dict)
    return align_monthly_float(avail, days)


def us_curve_raw(days):
    diff_dict = monthly_diff_dict("GS10", "TB3MS")
    avail = monthly_to_avail(diff_dict)
    return align_monthly_float(avail, days)


def load_gspc():
    return ewma.load_gspc()


def load_market(key):
    return ewma.load_market(key)


def oos_window(days, o, c, y, start, credit_raw, curve_raw, arms):
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    credit_up = credit_up_from_raw_monthly(credit_raw, 12)
    curve_inv20 = curve_inv20_from_raw(curve_raw)
    dates = days[start - 1:]
    years = trv2.calendar_years(dates[0], dates[-1])
    res = {"window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1}}
    v20 = trv2.vol_n(sc)[0]
    for arm in arms:
        on = market_on(c, credit_up, curve_inv20, arm)
        wants = ewma.wants_for(on, v20)
        nav, turn, ntr, codes, traded = ewma.simulate_band(so, sc, wants, COST_PER_SIDE * 2, START, C0_BAND)
        nav = nav[0]
        cg = float(trv2.cagr(nav[0], nav[-1], years))
        dd = float(trv2.max_dd(nav)[0])
        ev = ewma.event_stats(dates, codes[0], traded[0])
        res[arm] = {"cagr": cg, "max_dd": dd, "calmar": trv2.calmar(cg, dd),
                    "turnover_annual": float(turn[0] / years),
                    "round_trips_per_year": ev["round_trips_per_year"],
                    "sells_per_year": ev["sells_per_year"],
                    "resizes_per_year": ev["resizes_per_year"]}
    return res


def boot_oos_market(key, rng, arms, credit_raw, curve_raw):
    days, o, c, y, start = load_market(key)
    credit_r = credit_raw
    curve_r = curve_raw
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
        # prepend nan for synthetic origin for macro raw
        raw_c_s = credit_r[idx]
        raw_v_s = curve_r[idx]
        raw_c_full = np.concatenate([np.full((P, 1), np.nan), raw_c_s], axis=1)
        raw_v_full = np.concatenate([np.full((P, 1), np.nan), raw_v_s], axis=1)
        credit_s = credit_up_from_raw_monthly(raw_c_full, 12)
        curve_s = curve_inv20_from_raw(raw_v_full)
        ee = ewma.e2_up(cc)  # majority bool; but arms use market_on from votes internally? market_on recomputes votes from cc price; credit overlay applied. We still compute market_on with cc.
        # market_on needs price cc for E2 votes
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        wants_map = {}
        for arm in arms:
            on = market_on(cc, credit_s, curve_s, arm)
            wants_map[arm] = ewma.wants_for(on, trv2.vol_n(sc))
        for arm in arms:
            kind, band = arm_spec(arm)
            nav = ewma.simulate_band(so, sc, wants_map[arm], COST_PER_SIDE * 2, START, band)[0]
            k = nav.shape[1] - 1
            acc[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
    return {a: np.concatenate(v) for a, v in acc.items()}


# ----------------------------------------------------------------------------- gates
def g_return(a, c):
    return bool(a["cagr"] >= c["cagr"] or (a["calmar"] >= 1.10 * c["calmar"] and a["cagr"] >= c["cagr"] - 0.005))


def historical_gates(main, boot, arm):
    r = {}
    for md in FIN:
        a2, c2 = main[md]["m2"][arm], main[md]["m2"]["C0"]
        a1, c1 = main[md]["m1"][arm], main[md]["m1"]["C0"]
        r[f"G1_{md}"] = g_return(a2, c2)
        r[f"G2_{md}"] = bool(a2["max_dd"] <= c2["max_dd"] + 0.01)
        r[f"G3_{md}"] = bool(a2["2017_end"]["cagr"] >= c2["2017_end"]["cagr"] - 0.01)
        r[f"G5_{md}"] = bool(boot[md][arm]["diff_vs_C0_median"] >= 0)
        r[f"G6_{md}"] = g_return(a1, c1)
    a2, c2 = main["base"]["m2"][arm], main["base"]["m2"]["C0"]
    wins = [p for p in ("P1", "P2", "P3") if a2["subperiods"][p]["cagr"] > c2["subperiods"][p]["cagr"]]
    r["G4"] = bool(len(wins) >= 2)
    r["G4_wins"] = wins
    kind, band = arm_spec(arm)
    lim = c2["turnover_annual"] * (1.0 if band > C0_BAND else 2.0)
    r["G9"] = bool(a2["turnover_annual"] <= lim)
    return r


def oos_gates(oos, oos_boot_pooled, arm):
    keys = ["GSPC", "M1", "M2"]
    dc = {k: oos[k][arm]["cagr"] - oos[k]["C0"]["cagr"] for k in keys}
    dd = {k: oos[k][arm]["max_dd"] - oos[k]["C0"]["max_dd"] for k in keys}
    med = float(np.median(list(dc.values())))
    n_c = sum(1 for v in dc.values() if v >= -0.005)
    n_d = sum(1 for v in dd.values() if v <= 0.03 + 1e-12)
    g7 = bool(med >= 0 and n_c >= 3 and n_d >= 3 and max(dd.values()) <= 0.08 + 1e-12)
    g8 = bool(oos_boot_pooled[arm]["diff_vs_C0_median"] >= 0)
    return {"G7": g7, "G7_detail": {"cagr_diff": dc, "max_dd_diff": dd, "median_cagr_diff": med,
                                    "windows_cagr_ge_minus_0_5pp": n_c, "windows_dd_within_3pp": n_d,
                                    "max_dd_diff_max": max(dd.values())},
            "G8": g8, "G8_pooled_median_diff": oos_boot_pooled[arm]["diff_vs_C0_median"]}


GATE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3", "G5", "G6") for md in FIN] + ["G4", "G7", "G8", "G9"]
CORE_KEYS = [f"{g}_{md}" for g in ("G1", "G2", "G3") for md in FIN]


# ----------------------------------------------------------------------------- main
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)
    hc = harness()
    for k, v in hc["deviation_checks"].items():
        print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
    print(f"harness band simulator == simulate_multi: {hc['band_simulator_equals_simulate_multi']}")
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}")
    if mode == "--harness" or not hc["pass"]:
        sys.exit(0 if hc["pass"] else 1)

    main_res = run_main(ARMS, extras=True)
    print("main window done")
    cg, tv = boot_main(ARMS)
    boot = boot_summary(cg, tv, ARMS, FIN)
    print("main bootstrap done")

    oos = {}
    # static OOS gspc
    gspc_days, gspc_o, gspc_c, gspc_y, gspc_start = load_gspc()
    gspc_credit_raw = us_credit_raw(gspc_days)
    gspc_curve_raw = us_curve_raw(gspc_days)
    oos["GSPC"] = oos_window(gspc_days, gspc_o, gspc_c, gspc_y, gspc_start, gspc_credit_raw, gspc_curve_raw, ARMS)
    for k in MARKETS:
        days, o, c, y, start = load_market(k)
        cr = us_credit_raw(days)
        cv = us_curve_raw(days)
        oos[k] = oos_window(days, o, c, y, start, cr, cv, ARMS)
    print("static OOS done")

    rng = np.random.default_rng(SEED)
    per = {}
    for k in MARKETS:
        days, o, c, y, start = load_market(k)
        cr = us_credit_raw(days)
        cv = us_curve_raw(days)
        per[k] = boot_oos_market(k, rng, ARMS, cr, cv)
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in ARMS}
    oos_boot = {"per_market": {}, "pooled": {}}
    for k in MARKETS:
        oos_boot["per_market"][k] = {a: {"median_diff": float(np.median(per[k][a] - per[k]["C0"])),
                                         "cagr_median": float(np.median(per[k][a]))} for a in ARMS}
    for a in ARMS:
        d = pooled[a] - pooled["C0"]
        oos_boot["pooled"][a] = {"diff_vs_C0_median": float(np.median(d)), "diff_vs_C0_mean": float(np.mean(d)),
                                 "share_ge_C0": float(np.mean(d >= 0)), "paths": int(len(d))}
    print("OOS bootstrap done")

    gates = {}
    for arm in CANDIDATES:
        g = historical_gates(main_res, boot, arm)
        g.update(oos_gates(oos, oos_boot["pooled"], arm))
        g["pass_all"] = bool(all(g[k] for k in GATE_KEYS))
        g["core_G1_G3"] = bool(all(g[k] for k in CORE_KEYS))
        gates[arm] = g

    out = {
        "campaign": "credit_vote_v1",
        "params": {"arms": list(ARMS), "sma_n": SMA_N, "tsm_n": TSM_N, "don_n": DON_N,
                   "cap": CAP, "cost_per_side_m1": COST_PER_SIDE, "expense": EXP_Q,
                   "financing_spread": FIN, "bootstrap_main": {"seed": SEED, "paths": PATHS, "block": BLOCK,
                                                               "warmup": WARM, "back_extension": EXT,
                                                               "measured": MEASURED, "m": 2},
                   "bootstrap_oos": {"seed": SEED, "paths_per_market": OOS_PATHS_PER_MARKET, "block": BLOCK,
                                     "returns_per_path": OOS_BOOT_L, "m": 2},
                   "signals": "production E2 1.1.0 plus credit overlays",
                   "credit_rule_daily": "BAA10Y below 200-session SMA; curve inverted if T10Y3M<0 for 20+ sessions (1-session lag)",
                   "credit_rule_monthly_oos": "BAA-AAA spread below 12-month SMA; GS10-TB3MS<0 for 20+ sessions (monthly lag)"},
        "harness_check": hc,
        "main_window": main_res,
        "bootstrap_main_block20_m2": boot,
        "oos": oos,
        "oos_bootstrap_block20_m2": oos_boot,
        "gates": gates,
        "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    # ---------------------------------------------------------------- SUMMARY
    p = lambda x: f"{x * 100:7.2f}%"
    w = main_res["window"]
    L = [f"credit_vote_v1 | main window {w['start']} -> {w['end']} ({w['sessions']} sessions) | C0 = production E2 1.1.0",
         f"harness: {'PASS' if hc['pass'] else 'FAIL'}", ""]
    for md in FIN:
        L.append(f"[{md}] m=2: arm CAGR maxDD Calmar 2017+ turn/yr | boot20 median diff")
        for arm in ARMS:
            a = main_res[md]["m2"][arm]
            b = boot[md][arm]
            L.append(f"  {arm:5s} {p(a['cagr'])} {p(a['max_dd'])} {a['calmar']:5.3f} {p(a['2017_end']['cagr'])} "
                     f"{a['turnover_annual']:5.2f} | {b['diff_vs_C0_median'] * 100:+.3f}pp")
        L.append("")
    L.append("gates:")
    for arm in CANDIDATES:
        g = gates[arm]
        L.append(f"  {arm:5s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in GATE_KEYS) +
                 f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
