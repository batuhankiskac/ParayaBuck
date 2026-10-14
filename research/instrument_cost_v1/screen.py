#!/usr/bin/env python3
"""instrument_cost_v1 screen (see PREREGISTRATION.md). Offline except for the
one-time Yahoo download in download_data.py.

Question: with REAL fund data, is TQQQ (3x) held at 2/3 weight a cheaper way to
get production's QLD (2x) exposure? Arms, parameters, windows, gates and the
selection rule are fixed in PREREGISTRATION.md.

Modes
  --harness  no real-data arm is simulated. Three control reproductions:
             (1) fast_reentry_v1's own harness (C0 == e2_integration_v1
                 production E2, band simulator == simulate_multi) plus its
                 published C0/R2 rows on the synthetic QLD*,
             (2) the vendored Turkish after-tax engine on the synthetic QLD*
                 against vt_level_tax_v1's/vt R2's published after-tax anchors,
             (3) simulate_band start=1 == start=253 on the synthetic.
  --run      re-runs the harness, stops if it fails, then runs the arms on the
             real fund data (historical windows, tracking, liquidity, block-20
             bootstrap on real paired returns, after-tax windows, B1/B2 clean
             after-tax bootstraps), applies the pre-registered gates and writes
             RESULTS.json and SUMMARY.txt.

Pre-tax engine helpers (signals, early path, band simulator, metrics, loaders)
are imported from research/fast_reentry_v1/screen.py and
research/trend_robustness_v2/screen.py. The after-tax engine is a verbatim copy
of the block in research/vt_level_tax_v1/screen.py (lines ~905-1295 there),
which is research/tax_frontier_v1/screen.py's tax model plus the accounts of
research/tax_asym_v2/screen.py; the copy is checked against these studies'
published numbers in --harness.
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
fr = imp("fr", ROOT / "research/fast_reentry_v1/screen.py")

# ----------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = fr.SMA_N, fr.TSM_N, fr.DON_N
CAP, COST_PER_SIDE, BAND = trv2.CAP, trv2.COST_PER_SIDE, trv2.BAND
VOL_TARGET = trv2.VOL_TARGET  # 0.35
TQQQ_SHARE = 2.0 / 3.0        # target weight of TQQQ = 2/3 x the QLD-equivalent weight
M_GATED, M_REPORT = 2, 1
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED  # 250, 2520
EXT = TSM_N - trv2.SMA_MAX + 1             # 3 back-extension sessions
TOL = 1e-4                                 # 0.01 pp
END_DATE = "2026-09-29"
W0_START = "2010-02-11"                    # joint first session (TQQQ inception)
W17_START = "2017-01-03"
FUNDS = ("QQQ", "QLD", "TQQQ")
LEVER = {"QLD": 2.0, "TQQQ": 3.0}
ARMS = ("Q", "T", "Qstar", "T_alt")
CANDIDATES = ("T",)

DATA = {s: ROOT / f"data/historical/instrument_cost_v1_{s.lower()}.csv" for s in FUNDS}
DTB3_CSV = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
TANA = ROOT / "research/r2_tbill_combo_v1/RESULTS.json"   # published after-tax anchors (synthetic)
FRES = ROOT / "research/fast_reentry_v1/RESULTS.json"     # published pre-tax rows (synthetic)
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# after-tax constants (vt_level_tax_v1 / tax_frontier_v1)
M = 2
COST = COST_PER_SIDE * M
TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10
AT_CAPITAL = 100_000.0
AT_WINDOWS = {"2010_to_end": (None, END_DATE), "2017_to_end": (W17_START, END_DATE)}  # filled in main
FIXED_EXT, FIXED_WARM, FIXED_MEASURED = EXT, 253, MEASURED
FIXED_NEED = FIXED_EXT + FIXED_WARM + FIXED_MEASURED
FIXED_I0 = FIXED_EXT + FIXED_WARM
CAL_LO, CAL_HI = "2015-09-01", "2026-09-30"

# ----------------------------------------------------------------- data
def load_funds(end=END_DATE):
    """Real bars -> per-fund arrays on the joint session index, plus total-return levels.

    `tr_open`/`tr_close` reinvest each dividend at the ex-date open, so the fund's
    close-to-close total return is (close + dividend)/prev_close by construction.
    """
    raw = {}
    for s in FUNDS:
        d = {}
        with DATA[s].open() as fh:
            for r in csv.DictReader(fh):
                if r["date"] > end:
                    continue
                d[r["date"]] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                                float(r["volume"]), max(0.0, float(r["dividend"])))
        raw[s] = d
    days = sorted(set(raw[FUNDS[0]]) & set(raw[FUNDS[1]]) & set(raw[FUNDS[2]]))
    arr = {}
    for s in FUNDS:
        n = len(days)
        o, hi, lo, c, vo, dv = (np.empty(n) for _ in range(6))
        for i, d in enumerate(days):
            o[i], hi[i], lo[i], c[i], vo[i], dv[i] = raw[s][d]
        tro, trc = np.empty(n), np.empty(n)
        tro[0] = trc[0] = 1.0
        for i in range(1, n):
            tro[i] = trc[i - 1] * (o[i] + dv[i]) / c[i - 1]
            trc[i] = tro[i] * (c[i] / o[i])
        arr[s] = {"open": o, "high": hi, "low": lo, "close": c, "volume": vo, "dividend": dv,
                  "tr_open": tro, "tr_close": trc}
    return days, arr


def dtb3_on(days):
    """DTB3 (decimal) ffilled onto `days`; raises if a date precedes the first quote."""
    rows = {}
    with DTB3_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                rows[r["observation_date"]] = float(r["DTB3"]) / 100
    want = set(days)
    out, last = [], None
    for d in sorted(set(rows) | want):
        v = rows.get(d)
        if v is not None:
            last = v
        if d in want:
            if last is None:
                raise RuntimeError(f"no DTB3 on {d}")
            out.append(last)
    return np.array(out)


# ----------------------------------------------------------------- signals and targets
def compute_wants(price, vol_qld, vol_tqqq, comps, e2=None):
    """Targets for every arm from the production E2+R2 signals on the QQQ price close."""
    price = np.asarray(price, dtype=float)
    e2 = fr.e2_up(price) if e2 is None else e2
    w_q, early = fr.arm_signals(price, e2, vol_qld, "R2", comps)
    w_t, _ = fr.arm_signals(price, e2, vol_qld, "R2", comps)   # same vol convention, scaled below
    w_alt, _ = fr.arm_signals(price, e2, vol_tqqq, "R2", comps)
    wants = {
        "Q": w_q[0].copy(),
        "T": TQQQ_SHARE * w_t[0],
        "Qstar": w_q[0].copy(),
        "T_alt": w_alt[0].copy(),
    }
    return wants, early[0]


def arm_par(days, arr, name):
    """(opens, closes) of the instrument an arm holds, in the same session index."""
    if name == "Q":
        return arr["QLD"]["tr_open"], arr["QLD"]["tr_close"]
    if name in ("T", "T_alt"):
        return arr["TQQQ"]["tr_open"], arr["TQQQ"]["tr_close"]
    raise KeyError(name)


def run_arm(opens, closes, wants, cost, start=1, band=BAND):
    return fr.simulate_band(opens, closes, wants, cost, start, band)


def metrics(dates, nav, turn, ntr, codes, traded, wants, start):
    years = trv2.calendar_years(dates[0], dates[-1])
    st = trv2.curve_stats(dates, nav)
    st["calmar"] = trv2.calmar(st["cagr"], st["max_dd"])
    st["turnover_annual"] = float(turn / years)
    st["trades"] = int(ntr)
    st["events_full"] = fr.event_stats(dates, codes, traded)
    st["events_2017_on"] = fr.event_stats(dates, codes, traded, W17_START)
    tgt = np.asarray(wants)[start - 1:start - 1 + len(nav) - 1]
    st["time_in_market"] = float((tgt > 0).mean())
    st["window_2010"] = fr.seg(dates, nav, None, None)
    st["window_2017"] = fr.seg(dates, nav, W17_START, None)
    return st


def historical(days, arr, m):
    price = arr["QQQ"]["close"]
    vol_qld = trv2.vol_n(arr["QLD"]["tr_close"])[0]
    vol_tqqq = trv2.vol_n(arr["TQQQ"]["tr_close"])[0]
    comps = fr.early_components(price)
    wants, early = compute_wants(price, vol_qld, vol_tqqq, comps)
    q = arr["QQQ"]
    y = dtb3_on(days)
    syn_o, syn_c = trv2.synthetic_2x(q["open"], q["close"], q["dividend"], y, trv2.EXP_Q)
    start = TSM_N + 1  # production minimum_history_bars: first decision at session 253
    dates = days[start - 1:]
    out = {}
    for arm in ARMS:
        o, c = (syn_o, syn_c) if arm == "Qstar" else arm_par(days, arr, arm)
        nav, turn, ntr, codes, traded = run_arm(o, c, wants[arm], COST_PER_SIDE * m, start=start)
        out[arm] = metrics(dates, nav[0], turn[0], ntr[0], codes[0], traded[0], wants[arm], start)
    sel = wants["Q"] > 0
    return out, {"early_time_in_market": float(early[start - 1:].mean()),
                 "tqqq_share_realized": float(np.nanmean(wants["T"][sel] / wants["Q"][sel]))}


# ----------------------------------------------------------------- tracking (report only)
def corwin_schultz(hi, lo):
    """Corwin-Schultz (2012) two-day high-low spread estimate, one value per adjacent pair."""
    h, l = np.log(hi), np.log(lo)
    beta = (h[1:] - l[1:]) ** 2 + (h[:-1] - l[:-1]) ** 2
    gam = (np.maximum(h[1:], h[:-1]) - np.minimum(l[1:], l[:-1])) ** 2
    k = 3 - 2 * math.sqrt(2)
    a = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gam / k)
    s = 2 * (np.exp(a) - 1) / (1 + np.exp(a))
    return np.clip(s, 0.0, None)


def tracking(days, arr):
    qqq_tr = arr["QQQ"]["tr_close"]
    r = qqq_tr[1:] / qqq_tr[:-1]
    out = {"windows": {}, "by_year": {}, "liquidity": {}}
    for s in ("QLD", "TQQQ"):
        k = LEVER[s]
        bench = np.concatenate([[1.0], np.cumprod(1 + k * r)])
        out["windows"][s] = seg_track(days, arr[s]["tr_close"], bench, k)
        w = {}
        for yr in sorted({d[:4] for d in days[1:]}):
            idx = [i for i, d in enumerate(days) if d[:4] == yr]
            if len(idx) < 2:
                continue
            i0, i1 = idx[0], idx[-1]
            j0 = i0 - 1 if i0 > 0 else 0
            f = arr[s]["tr_close"][i1] / arr[s]["tr_close"][j0] - 1
            b = bench[i1] / bench[j0] - 1
            w[yr] = {"fund": float(f), "bench": float(b), "drag_pp": float((f - b) * 100),
                     "partial": bool(i0 == 0)}
        out["by_year"][s] = w
        out["liquidity"][s] = {
            "avg_dollar_volume": float(np.mean(arr[s]["close"] * arr[s]["volume"])),
            "median_dollar_volume": float(np.median(arr[s]["close"] * arr[s]["volume"])),
            "corwin_schultz_spread_mean": float(np.mean(corwin_schultz(arr[s]["high"], arr[s]["low"]))),
            "corwin_schultz_spread_median": float(np.median(corwin_schultz(arr[s]["high"], arr[s]["low"]))),
            "mean_daily_range_pct": float(np.mean((arr[s]["high"] - arr[s]["low"]) / arr[s]["close"]) * 100),
        }
    out["liquidity"]["QQQ"] = {
        "avg_dollar_volume": float(np.mean(arr["QQQ"]["close"] * arr["QQQ"]["volume"])),
        "corwin_schultz_spread_mean": float(np.mean(corwin_schultz(arr["QQQ"]["high"], arr["QQQ"]["low"]))),
        "mean_daily_range_pct": float(np.mean((arr["QQQ"]["high"] - arr["QQQ"]["low"]) / arr["QQQ"]["close"]) * 100),
    }
    # dividend yield (last 3 years), report only
    n = len(days)
    i0 = max(1, n - 3 * 252)
    for s in FUNDS:
        y = arr[s]["dividend"][i0:] / arr[s]["close"][i0:] * 252
        out.setdefault("dividend_yield_3y", {})[s] = float(np.mean(y))
    # assumed-financing comparison on the same real sessions
    syn_o, syn_c = {}, {}
    y = dtb3_on(days)
    for s in ("QLD", "TQQQ"):
        so, sc = trv2.synthetic_2x(arr["QQQ"]["open"], arr["QQQ"]["close"], arr["QQQ"]["dividend"], y, trv2.EXP_Q)
        syn_o[s], syn_c[s] = so, sc
    out["vs_synthetic"] = {}
    for s in ("QLD", "TQQQ"):
        # the synthetic is 2x; for TQQQ scale the *daily* gross by 3/2 via leverage-financed rebuild
        yrs = trv2.calendar_years(days[0], days[-1])
        cagr_real = trv2.cagr(1.0, arr[s]["tr_close"][-1] / arr[s]["tr_close"][0], yrs)
        if s == "QLD":
            cagr_syn = trv2.cagr(1.0, syn_c[s][-1] / syn_c[s][0], yrs)
        else:
            so3, sc3 = synthetic_L(arr["QQQ"]["open"], arr["QQQ"]["close"], arr["QQQ"]["dividend"], y, 3.0, 0.0084)
            cagr_syn = trv2.cagr(1.0, sc3[-1] / sc3[0], yrs)
        out["vs_synthetic"][s] = {"cagr_real": float(cagr_real), "cagr_synthetic": float(cagr_syn),
                                  "diff_pp": float((cagr_real - cagr_syn) * 100)}
    return out


def seg_track(days, fund_tr, bench, k):
    yrs = trv2.calendar_years(days[0], days[-1])
    f = fund_tr[-1] / fund_tr[0]
    b = bench[-1] / bench[0]
    return {"start": days[0], "end": days[-1], "k": k, "years": float(yrs),
            "fund_total": float(f - 1), "bench_total": float(b - 1),
            "fund_cagr": float(trv2.cagr(1.0, f, yrs)), "bench_cagr": float(trv2.cagr(1.0, b, yrs)),
            "drag_pp_per_year": float((trv2.cagr(1.0, f, yrs) - trv2.cagr(1.0, b, yrs)) * 100)}


def synthetic_L(q_open, q_close, q_div, y, L, expense, spread=0.0):
    """Daily-reset L x total return - expense - (L-1) x DTB3 financing (leverage_frontier_v1)."""
    prev = q_close[..., :-1]
    r_cc = (q_close[..., 1:] + q_div[..., 1:]) / prev - 1
    r_on = (q_open[..., 1:] + q_div[..., 1:]) / prev - 1
    fin = y[..., :-1] if spread == 0.0 else y[..., :-1] + spread
    g_cc = 1 + L * r_cc - (expense + (L - 1) * fin) / 252
    one = np.ones(q_close.shape[:-1] + (1,))
    close = np.concatenate([one, np.cumprod(g_cc, axis=-1)], axis=-1)
    opn = np.concatenate([one, close[..., :-1] * (1 + L * r_on)], axis=-1)
    return opn, close


# ----------------------------------------------------------------- pre-tax bootstrap (real paired returns)
def boot_paths(npool, rng, P, L):
    pos = np.empty((P, L), dtype=np.int64)
    pos[:, 0] = rng.integers(0, npool, P)
    jump = rng.random((P, L)) < 1 / BLOCK
    fresh = rng.integers(0, npool, (P, L))
    for t in range(1, L):
        pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
    pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT, 0, -1)], axis=1)
    return np.concatenate([pre, pos], axis=1)


def boot_main(days, arr, arms=("Q", "T", "T_alt"), n_paths=PATHS):
    """Stationary block bootstrap (mean block 20) over the REAL joint session pool.

    Each drawn session carries QQQ price overnight/intraday (for signals) and QLD/TQQQ
    overnight (dividend-inclusive) and intraday factors, so the three instruments are
    resampled jointly by date. Signals are recomputed on the resampled QQQ price level.
    """
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, len(days))
    npool = len(pool)
    L = WARM + MEASURED
    start = trv2.SMA_MAX + 1 + EXT
    q, l, t = arr["QQQ"], arr["QLD"], arr["TQQQ"]
    cg = {a: [] for a in arms}
    tv = {a: [] for a in arms}
    for chunk in range(0, n_paths, CHUNK):
        P = min(CHUNK, n_paths - chunk)
        idx = pool[boot_paths(npool, rng, P, L)]
        prev = q["close"][idx - 1]
        pc = np.concatenate([np.ones((P, 1)), np.cumprod((q["open"][idx] / prev) * (q["close"][idx] / q["open"][idx]), axis=1)], axis=1)
        paths = {}
        for s, a in (("QLD", l), ("TQQQ", t)):
            gon = (a["open"][idx] + a["dividend"][idx]) / a["close"][idx - 1]
            gid = a["close"][idx] / a["open"][idx]
            trc = np.concatenate([np.ones((P, 1)), np.cumprod(gon * gid, axis=1)], axis=1)
            tro = np.concatenate([np.ones((P, 1)), trc[:, :-1] * gon], axis=1)
            paths[s] = (tro, trc)
        vol_qld = trv2.vol_n(paths["QLD"][1])
        vol_tqqq = trv2.vol_n(paths["TQQQ"][1])
        comps = fr.early_components(pc)
        wants, _ = compute_wants(pc, vol_qld, vol_tqqq, comps)
        for arm in arms:
            o, c = paths["QLD"] if arm == "Q" else paths["TQQQ"]
            nav, turn, _, _, _ = run_arm(o, c, wants[arm], COST_PER_SIDE * 2, start)
            k = nav.shape[1] - 1
            cg[arm].append((nav[:, -1] / nav[:, 0]) ** (252 / k) - 1)
            tv[arm].append(turn / (k / 252))
    return ({a: np.concatenate(v) for a, v in cg.items()}, {a: np.concatenate(v) for a, v in tv.items()})


def boot_summary(cg, tv, arms):
    out = {}
    base = cg["Q"]
    for a in arms:
        d = cg[a] - base
        out[a] = {"cagr_median": float(np.median(cg[a])), "turnover_median": float(np.median(tv[a])),
                  "diff_vs_Q_median": float(np.median(d)), "diff_vs_Q_mean": float(np.mean(d)),
                  "diff_vs_Q_p05": float(np.percentile(d, 5)), "diff_vs_Q_p25": float(np.percentile(d, 25)),
                  "diff_vs_Q_p75": float(np.percentile(d, 75)), "diff_vs_Q_p95": float(np.percentile(d, 95)),
                  "share_ge_Q": float(np.mean(d >= 0))}
    return out


# ----------------------------------------------------------------- pre-tax harness
def harness_pretax():
    h = fr.harness()
    ref = json.loads(FRES.read_text())
    got = fr.run_main(("C0", "R2"), extras=False, m_list=(1, 2))
    checks = {}
    for md in ("base", "calibrated"):
        for m in (1, 2):
            for arm in ("C0", "R2"):
                r = ref["main_window"][md][f"m{m}"][arm]
                x = got[md][f"m{m}"][arm]
                for k in ("cagr", "max_dd"):
                    checks[f"{md}_m{m}_{arm}_{k}"] = {"reference": r[k], "reproduced": x[k],
                                                      "diff_pp": (x[k] - r[k]) * 100}
    # real-data wiring check: band simulator at 0.5 pp == trend_robustness_v2.simulate_multi on the
    # real QLD total-return series, from the production start session
    days_r, arr_r = load_funds()
    w_r = compute_wants(arr_r["QQQ"]["close"], trv2.vol_n(arr_r["QLD"]["tr_close"])[0],
                        trv2.vol_n(arr_r["TQQQ"]["tr_close"])[0], fr.early_components(arr_r["QQQ"]["close"]))[0]["Q"]
    o, c = arr_r["QLD"]["tr_open"], arr_r["QLD"]["tr_close"]
    start_r = TSM_N + 1
    nav_b = fr.simulate_band(o, c, w_r[None, :], COST_PER_SIDE * 2, start_r, BAND)[0]
    nav_m = trv2.simulate_multi(o, c, w_r[None, None, :], COST_PER_SIDE * 2, start_r)[0]
    real_equal = bool(np.array_equal(nav_b, nav_m))
    ok = bool(h["pass"] and real_equal and max(abs(v["diff_pp"]) for v in checks.values()) <= TOL * 100)
    return {"fast_reentry_harness_pass": bool(h["pass"]),
            "fast_reentry_band_sim_equals_simulate_multi": bool(h["band_simulator_equals_simulate_multi"]),
            "window": got["window"],
            "real_data_band_sim_equals_simulate_multi": real_equal,
            "checks": checks,
            "max_abs_diff_pp": max(abs(v["diff_pp"]) for v in checks.values()),
            "pass": ok}


# ================= after-tax engine (verbatim copy of vt_level_tax_v1 lines ~905-1295) =================
# Tax model and accounts: research/tax_frontier_v1 (progressive 2026 tariff, Yi-UFE
# indexation, USD/TRY CBRT buying) + research/tax_asym_v2 (FlatAccount, B2). DAYS,
# PRICES and TARGETS are module globals reassigned by the caller before each run,
# exactly as vt_level_tax_v1's after_tax_bootstrap_variant does.

DAYS = []
PRICES = {}
TARGETS = {}


class Market:
    def __init__(self):
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in DAYS])
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
        self.days = list(DAYS)

    def scale(self, year: int) -> float:
        key = f"{year - 1}-10"
        if key not in self.ufe:
            ks = sorted(k for k in self.ufe if k.endswith("-10"))
            key = ks[0] if ks else ANCHOR
        return self.ufe[key] / self.ufe[ANCHOR]


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def tariff_tax(base, scale):
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale if not math.isinf(hi) else math.inf
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


class Regime:
    def __init__(self, name, kind):
        self.name, self.kind = name, kind

    def tax(self, base, year, mkt):
        if self.kind == "none" or base <= 0:
            return 0.0
        return tariff_tax(base, mkt.scale(year))


class BaseAccount:
    def __init__(self, mkt, regime, cash, cashbox=None):
        self.mkt = mkt
        self.rg = regime
        if cashbox is None:
            self._cashbox = {"v": float(cash)}
        else:
            self._cashbox = cashbox
            self._cashbox["v"] = float(cash)
        self.units = 0.0
        self.lots = []
        self.year_gain = defaultdict(float)
        self.year_div = defaultdict(float)
        self.year_wht = defaultdict(float)
        self.gain_by_reason = defaultdict(float)
        self.pos_gain_by_reason = defaultdict(float)
        self.neg_gain_by_reason = defaultdict(float)
        self.index_relief_try = 0.0
        self.tax_due = {}
        self.tax_paid_try = defaultdict(float)
        self.tax_paid_usd = 0.0
        self.wht_usd = 0.0
        self.fees = 0.0

    @property
    def cash(self):
        return self._cashbox["v"]

    @cash.setter
    def cash(self, v):
        self._cashbox["v"] = float(v)

    def buy(self, i, price, usd, fee):
        if usd <= 0:
            return
        u = usd / price
        self.cash -= usd + fee
        self.fees += fee
        self.units += u
        self.lots.append([u, (usd + fee) / u, self.mkt.fx[i], self.mkt.days[i][:7]])

    def sell(self, i, price, units, fee, reason):
        units = min(units, self.units)
        if units <= 0:
            return 0.0
        gross = units * price
        self.cash += gross - fee
        self.fees += fee
        fx_s = self.mkt.fx[i]
        sale_month = self.mkt.days[i][:7]
        year = int(self.mkt.days[i][:4])
        per_unit = (gross - fee) / units
        left, gain = units, 0.0
        while left > 0 and self.lots:
            lot = self.lots[0]
            take = min(left, lot[0])
            proceeds = take * per_unit * fx_s
            cost = take * lot[1] * lot[2]
            g = proceeds - cost
            if self.rg.kind != "none" and g > 0:
                base_m = prev_month(lot[3])
                rise = self.mkt.ufe[prev_month(sale_month)] / self.mkt.ufe[base_m] - 1
                if rise >= INDEX_MIN_RISE:
                    gi = max(0.0, proceeds - cost * (1 + rise))
                    self.index_relief_try += g - gi
                    g = gi
            gain += g
            if g >= 0:
                self.pos_gain_by_reason[reason] += g
            else:
                self.neg_gain_by_reason[reason] += g
            lot[0] -= take
            left -= take
            if lot[0] <= take * 1e-12:
                self.lots.pop(0)
        self.units -= units
        if self.units <= 1e-12 * max(1.0, units) or not self.lots:
            self.units, self.lots = 0.0, []
        self.year_gain[year] += gain
        self.gain_by_reason[reason] += gain
        return gain

    def assess(self, year):
        g = max(0.0, self.year_gain.get(year, 0.0))
        d = self.year_div.get(year, 0.0)
        if self.rg.kind == "none":
            return 0.0
        if d <= DIV_THRESHOLD_2026 * self.mkt.scale(year):
            d = 0.0
        base = g + d
        tax = self.rg.tax(base, year, self.mkt)
        if d > 0 and base > 0:
            tax -= min(self.year_wht.get(year, 0.0), tax * d / base)
        return max(0.0, tax)

    def outstanding(self):
        return sum(v - self.tax_paid_try[y] for y, v in self.tax_due.items())


def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_strategy(arm, regime_name, capital, a, b, mkt):
    days_all = DAYS
    if isinstance(a, int):
        i0 = a
        i1 = b
    else:
        i0 = bisect.bisect_left(days_all, a)
        i1 = bisect.bisect_right(days_all, b) - 1
    if regime_name == "progressive":
        regime = Regime("progressive", "tariff")
    else:
        regime = Regime("pre_tax", "none")

    opens_arr, closes_arr = PRICES[arm]
    wants_arr = TARGETS[arm]

    cashbox = {"v": float(capital)}
    account = BaseAccount(mkt, regime, 0.0, cashbox)
    cashbox["v"] = float(capital)

    pays = instalments(days_all, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0

    prev_tgt = None

    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax

        for ty, part in pays.get(i, ()):
            if ty not in account.tax_due:
                account.tax_due[ty] = 0.0
            due = account.tax_due.get(ty, 0.0)
            amt_total = due / 2 if part == 0 else due - account.tax_paid_try.get(ty, 0.0)
            if amt_total <= 1e-9:
                continue
            usd = amt_total / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - COST)
                if account.units > 1e-12:
                    account.sell(i, float(opens_arr[i]), need / float(opens_arr[i]), need * COST, "tax")
                    tax_sales += 1
            cashbox["v"] -= usd
            account.tax_paid_try[ty] += amt_total
            account.tax_paid_usd += usd

        po = float(opens_arr[i])
        pc = float(closes_arr[i])
        held_units = account.units
        held_value = held_units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (held_units * po) / nav_open if nav_open > 0 else 0.0

        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if math.isnan(tgt) else tgt
        resize_target = tgt

        trade = abs(tgt - w) > BAND

        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(resize_target - w)
            dsum = drift
            fee_total = nav_open * COST * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            curr_units = account.units
            target_units = resize_target * nav_after_cost / po if po > 0 else 0.0
            du = target_units - curr_units
            leg_fee = nav_open * COST * drift if drift > 0 else 0.0
            if du > 1e-12:
                account.buy(i, po, du * po, leg_fee)
            elif du < -1e-12:
                account.sell(i, po, -du, leg_fee, "resize")
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + account.units * pc

        prev_tgt = tgt

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - liq_tax / fx1

    ending_nav = nav[-1]
    mtm = ending_nav

    fx0 = mkt.fx[i0 - 1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(account.tax_paid_try.values()) + liq_tax
    tax_total_usd = account.tax_paid_usd + liq_tax / fx1v

    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1
    all_reasons = dict(account.gain_by_reason)

    nav_arr = nav.reshape(1, -1)
    maxdd = float(trv2.max_dd(nav_arr)[0])

    return {
        "window": [days_all[i0], days_all[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1v),
        "ending_nav_usd": float(ending_nav),
        "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1v)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_unliquidated": float(cg(capital * fx0, mtm * fx1v)),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(tax_total_try),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(all_reasons.items())},
        "indexation_relief_try": float(account.index_relief_try),
        "_nav": nav,
    }


class FlatAccount(BaseAccount):
    """B2: 20% flat tax on annual net realized USD gain, losses carried forward."""

    def __init__(self, mkt, cash, cashbox=None):
        super().__init__(mkt, Regime("none", "none"), cash, cashbox)
        self.loss_cf = 0.0

    def assess(self, year):
        g = self.year_gain.get(year, 0.0)
        net = g + self.loss_cf
        if net > 0:
            tax = 0.20 * net
            self.loss_cf = 0.0
        else:
            tax = 0.0
            self.loss_cf = net
        return max(0.0, tax)


def run_strategy_flat(arm, capital, i0, i1, dates, opens_arr, closes_arr, wants_arr, mkt):
    days_all = dates
    opens_arr = np.asarray(opens_arr, float).flatten()
    closes_arr = np.asarray(closes_arr, float).flatten()
    wants_arr = np.asarray(wants_arr, float).flatten()
    cashbox = {"v": float(capital)}
    account = FlatAccount(mkt, 0.0, cashbox)
    cashbox["v"] = float(capital)
    pays = None
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0
    prev_tgt = None
    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax
            due = acct_tax
            if due > 1e-9:
                usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"])
                    if account.units > 1e-12:
                        price = float(closes_arr[i - 1])
                        units_sell = need / price if price > 0 else 0.0
                        account.sell(i - 1 if i - 1 >= 0 else i, price, units_sell, units_sell * price * COST, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                account.tax_paid_try[prevy] = account.tax_paid_try.get(prevy, 0.0) + due
                account.tax_paid_usd += usd

        po = float(opens_arr[i]); pc = float(closes_arr[i])
        held_value = account.units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (account.units * po) / nav_open if nav_open > 0 else 0.0
        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if (tgt != tgt) else tgt
        resize_target = tgt

        trade = abs(tgt - w) > BAND
        if account.units == 0 and tgt > 1e-12:
            trade = True
        if account.units > 1e-12 and tgt <= 0:
            trade = True
        resize_target = tgt

        if trade:
            drift = abs(resize_target - w)
            nav_after = nav_open
            fee = nav_open * COST * drift if drift > 0 else 0.0
            nav_after -= fee
            target_units = resize_target * nav_after / po if po > 0 else 0.0
            du = target_units - account.units
            if du > 1e-12:
                account.buy(i, po, du * po, fee if du > 0 else 0.0)
            elif du < -1e-12:
                account.sell(i, po, -du, fee if du < 0 else 0.0, "resize")
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + account.units * pc
        prev_tgt = tgt

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    mtm = nav[-1]
    fx0 = mkt.fx[i0 - 1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(account.tax_paid_try.values()) + liq_tax
    tax_total_usd = account.tax_paid_usd + (liq_tax / fx1v if fx1v != 0 else liq_tax)
    cg = lambda v0, v1: (v1 / v0) ** (1.0 / yrs) - 1 if yrs > 0 else float("nan")
    nav_arr = nav.reshape(1, -1)
    maxdd = float(trv2.max_dd(nav_arr)[0])
    return {
        "window": [days_all[i0], days_all[i1]],
        "years": float(yrs),
        "capital_usd": capital,
        "ending_nav_usd": float(mtm),
        "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "fees_usd": float(account.fees),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
    }


# ================= after-tax drivers =================
def set_context(days, prices, targets):
    global DAYS, PRICES, TARGETS
    DAYS = list(days)
    PRICES = dict(prices)
    TARGETS = dict(targets)


def run_window(prices, targets, days, names, a, b, tax_kind, capital=AT_CAPITAL):
    """Run `names` (instrument groups sharing a target array) over [a, b], $100k."""
    set_context(days, prices, targets)
    mkt = Market()
    res = {}
    for n in names:
        res[n] = run_strategy(n, "progressive" if tax_kind == "progressive" else "pre_tax", capital, a, b, mkt)
    return res


def harness_aftertax():
    """Reproduce vt_level_tax_v1's C0 and r2_tbill_combo_v1's R2 after-tax anchors on the synthetic QLD*."""
    ref = json.loads(TANA.read_text())["after_tax_100k"]["windows"]
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    price = q[1]
    syn_o, syn_c = trv2.synthetic_2x(q[0], q[1], q[2], y, trv2.EXP_Q)  # base financing
    vol = trv2.vol_n(syn_c)[0]
    comps = fr.early_components(price)
    e2 = fr.e2_up(price)
    w_c0 = fr.wants_for(e2[0], vol)
    w_r2 = fr.arm_signals(price, e2, vol, "R2", comps)[0][0]
    targets = {"C0": np.asarray(w_c0).flatten(), "R2": np.asarray(w_r2).flatten()}
    prices = {"C0": (syn_o, syn_c), "R2": (syn_o, syn_c)}
    checks = {}
    for wn, (a, b) in {"2000_03_to_2026_09": ("2000-03-09", END_DATE),
                       "2010_01_to_end": ("2010-01-01", END_DATE),
                       "2017_01_to_end": (W17_START, END_DATE)}.items():
        res = run_window(prices, targets, days, ("C0", "R2"), a, b, "progressive")
        for arm in ("C0", "R2"):
            r = ref[wn][arm]
            for key, got_v, ref_v in (("progressive_cagr", res[arm]["cagr_usd_liquidated"], r["progressive"]["cagr"]),
                                      ("progressive_max_dd", res[arm]["max_dd_nav_usd"], r["progressive"]["max_dd"])):
                checks[f"{wn}_{arm}_{key}"] = {"reference": ref_v, "reproduced": got_v, "diff_pp": (got_v - ref_v) * 100}
    mx = max(abs(v["diff_pp"]) for v in checks.values())
    return {"checks": checks, "max_abs_diff_pp": mx, "tolerance_pp": TOL * 100, "pass": bool(mx <= TOL * 100)}


# ================= clean after-tax bootstraps (B1/B2) =================
def fx_on(dates):
    fd, fv = [], []
    with FX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            fd.append(r["date"])
            fv.append(float(r["usdtry_forex_buying"]))
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates])


def fixed_calendar():
    days, _ = GLOBAL["data"]
    sub = [d for d in days if CAL_LO <= d <= CAL_HI]
    if len(sub) < FIXED_NEED:
        raise RuntimeError(f"fixed calendar has {len(sub)} sessions, need {FIXED_NEED}")
    return sub[:FIXED_NEED]


def after_tax_bootstrap(tax_kind, n_paths=1000, capital=AT_CAPITAL, seed=SEED):
    days, arr = GLOBAL["data"]
    fixed = fixed_calendar()
    fx = fx_on(fixed)
    rng = np.random.default_rng(seed)
    pool = np.arange(1, len(days))
    npool = len(pool)
    q, l, t = arr["QQQ"], arr["QLD"], arr["TQQQ"]
    out = {"Q": {"cagr": [], "dd": []}, "T": {"cagr": [], "dd": []}}
    for _ in range(n_paths):
        idx = pool[boot_paths(npool, rng, 1, FIXED_NEED - EXT)[0]]
        prev = q["close"][idx - 1]
        pc = np.concatenate([[1.0], np.cumprod((q["open"][idx] / prev) * (q["close"][idx] / q["open"][idx]))])
        paths = {}
        for s, a in (("Q", l), ("T", t)):
            gon = (a["open"][idx] + a["dividend"][idx]) / a["close"][idx - 1]
            gid = a["close"][idx] / a["open"][idx]
            trc = np.concatenate([[1.0], np.cumprod(gon * gid)])
            tro = np.concatenate([[1.0], trc[:-1] * gon])
            paths[s] = (tro, trc)
        wants, _ = compute_wants(pc, trv2.vol_n(paths["Q"][1]), trv2.vol_n(paths["T"][1]), fr.early_components(pc))
        set_context(fixed, paths, {"Q": wants["Q"], "T": wants["T"]})
        mkt = Market()
        mkt.days = list(fixed)
        mkt.fx = fx.copy()
        for arm in ("Q", "T"):
            if tax_kind == "flat":
                r = run_strategy_flat(arm, capital, FIXED_I0, FIXED_NEED - 1, fixed,
                                      paths[arm][0], paths[arm][1], wants[arm], mkt)
            else:
                r = run_strategy(arm, "progressive", capital, FIXED_I0, FIXED_NEED - 1, mkt)
            liq = r["terminal_usd_liquidated"]
            if not (liq == liq) or liq <= 0:
                raise RuntimeError(f"non-positive terminal USD {liq} ({tax_kind}, {arm})")
            out[arm]["cagr"].append(r["cagr_usd_liquidated"])
            out[arm]["dd"].append(r["max_dd_nav_usd"])
    d = np.array(out["T"]["cagr"]) - np.array(out["Q"]["cagr"])
    ddd = np.array(out["T"]["dd"]) - np.array(out["Q"]["dd"])
    return {
        "tax_kind": tax_kind, "n_paths": n_paths, "calendar_start": fixed[0], "calendar_end": fixed[-1],
        "calendar_days": len(fixed),
        "Q_median_cagr": float(np.median(out["Q"]["cagr"])), "T_median_cagr": float(np.median(out["T"]["cagr"])),
        "Q_median_max_dd": float(np.median(out["Q"]["dd"])), "T_median_max_dd": float(np.median(out["T"]["dd"])),
        "delta_cagr_median_pp": float(np.median(d) * 100), "delta_cagr_mean_pp": float(np.mean(d) * 100),
        "share_delta_gt_0": float(np.mean(d > 0)), "p05_pp": float(np.percentile(d, 5) * 100),
        "p95_pp": float(np.percentile(d, 95) * 100), "delta_maxdd_median_pp": float(np.median(ddd) * 100),
    }


# ================= gates =================
def gates_for(hist, boot, at, b1, b2, arm="T"):
    g = {}
    for m in (M_GATED,):
        a, q = hist[m][arm], hist[m]["Q"]
        for wn in ("window_2010", "window_2017"):
            g[f"GT1_{wn}_m{m}"] = bool(a[wn]["cagr"] >= q[wn]["cagr"])
            g[f"GT2_{wn}_m{m}"] = bool(a[wn]["max_dd"] <= q[wn]["max_dd"] + 0.01)
    g["GT3_bootstrap"] = bool(boot[arm]["diff_vs_Q_median"] >= 0)
    for wn in AT_WINDOWS:
        g[f"GT4_{wn}"] = bool(at[wn][arm]["progressive"]["cagr_usd_liquidated"]
                              > at[wn]["Q"]["progressive"]["cagr_usd_liquidated"])
    for label, res in (("B1", b1), ("B2", b2)):
        g[f"GT5_{label}_median"] = bool(res["delta_cagr_median_pp"] >= 0)
        g[f"GT5_{label}_share"] = bool(res["share_delta_gt_0"] >= 0.55)
    g["GT6_turnover"] = bool(hist[M_GATED][arm]["turnover_annual"] <= 2 * hist[M_GATED]["Q"]["turnover_annual"])
    g["pass_all"] = bool(all(v for k, v in g.items()))
    return g


# ================= main =================
GLOBAL = {}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("--harness", "--run"):
        print(__doc__)
        sys.exit(2)

    hp = harness_pretax()
    ha = harness_aftertax()
    print(f"harness pre-tax: fast_reentry harness pass {hp['fast_reentry_harness_pass']}, "
          f"band sim == simulate_multi {hp['fast_reentry_band_sim_equals_simulate_multi']}, "
          f"real-data band sim == simulate_multi {hp['real_data_band_sim_equals_simulate_multi']}, "
          f"max |diff| vs published C0/R2 {hp['max_abs_diff_pp']:.6f} pp")
    print(f"harness after-tax anchors: max |diff| {ha['max_abs_diff_pp']:.6f} pp")
    print(f"harness {'PASS' if (hp['pass'] and ha['pass']) else 'FAIL'}")
    if mode == "--harness" or not (hp["pass"] and ha["pass"]):
        sys.exit(0 if (hp["pass"] and ha["pass"]) else 1)

    days, arr = load_funds()
    GLOBAL["data"] = (days, arr)
    AT_WINDOWS["2010_to_end"] = (days[TSM_N + 1], END_DATE)
    hist = {}
    hist[M_GATED], diag = historical(days, arr, M_GATED)
    hist[M_REPORT] = historical(days, arr, M_REPORT)[0]
    print("historical done")
    cg, tv = boot_main(days, arr)
    boot = boot_summary(cg, tv, ARMS)
    print("bootstrap done")
    trk = tracking(days, arr)
    print("tracking done")

    # after-tax windows on real funds
    price = arr["QQQ"]["close"]
    wants, early = compute_wants(price, trv2.vol_n(arr["QLD"]["tr_close"])[0],
                                 trv2.vol_n(arr["TQQQ"]["tr_close"])[0], fr.early_components(price))
    prices = {"Q": (arr["QLD"]["tr_open"], arr["QLD"]["tr_close"]),
              "T": (arr["TQQQ"]["tr_open"], arr["TQQQ"]["tr_close"])}
    targets = {"Q": wants["Q"], "T": wants["T"]}
    at = {wn: run_window(prices, targets, days, ("Q", "T"), a, b, "progressive") for wn, (a, b) in AT_WINDOWS.items()}
    at_pretax = {wn: run_window(prices, targets, days, ("Q", "T"), a, b, "pre_tax") for wn, (a, b) in AT_WINDOWS.items()}
    print("after-tax windows done")
    b1 = after_tax_bootstrap("progressive")
    b2 = after_tax_bootstrap("flat")
    print("after-tax bootstraps done")

    gates = {arm: gates_for(hist, boot, at, b1, b2, arm) for arm in CANDIDATES}
    sel = [a for a in CANDIDATES if gates[a]["pass_all"]]
    selection = {"selected": sel[0] if sel else "none", "candidates": sel,
                 "rule": "T is selected iff GT1-GT6 all pass; otherwise Q (production QLD) stays"}

    def at_view(wn, arm):
        r, p = at[wn][arm], at_pretax[wn][arm]
        return {"progressive_cagr": r["cagr_usd_liquidated"], "progressive_max_dd": r["max_dd_nav_usd"],
                "pre_tax_cagr": p["cagr_usd_liquidated"], "terminal_usd": r["terminal_usd_liquidated"],
                "trades_per_year": r["strategy_trades"] / r["years"], "tax_usd": r["tax_total_usd_at_payment_fx"],
                "pre_tax_max_dd": p["max_dd_nav_usd"], "pre_tax_terminal_usd": p["terminal_usd_liquidated"]}

    out = {
        "campaign": "instrument_cost_v1",
        "params": {"arms": list(ARMS), "candidates": list(CANDIDATES), "sma_n": SMA_N, "tsm_n": TSM_N,
                   "don_n": DON_N, "sma_fast": fr.SMA_FAST, "momentum_lag": fr.LAG_FAST,
                   "vol_median_window": fr.MED_WINDOW, "vol_n": trv2.VOL_N, "vol_target": VOL_TARGET,
                   "cap": CAP, "band": BAND, "cost_per_side_m1": COST_PER_SIDE, "cost_multiplier": M_GATED,
                   "tqqq_share": TQQQ_SHARE, "expense_synthetic_2x": trv2.EXP_Q,
                   "window": {"start": days[0], "end": days[-1], "sessions": len(days)},
                   "measured_window_start": days[TSM_N + 1], "data_start": W0_START,
                   "w17_start": W17_START,
                   "bootstrap": {"seed": SEED, "paths": PATHS, "block": BLOCK, "warm": WARM,
                                 "measured": MEASURED, "back_extension": EXT, "m": M_GATED},
                   "at_bootstrap": {"paths": 1000, "seed": SEED, "fixed_calendar": [CAL_LO, CAL_HI],
                                    "need": FIXED_NEED, "i0": FIXED_I0, "capital": AT_CAPITAL, "m": M_GATED},
                   "signals": "production E2 1.1.0 + R2 early re-entry on the QQQ price close"},
        "harness_pretax": hp, "harness_aftertax": ha,
        "historical_m2": hist[M_GATED], "historical_m1": hist[M_REPORT],
        "bootstrap_main_block20_m2": boot,
        "bootstrap_raw": {"median_cagr": {a: float(np.median(cg[a])) for a in ARMS},
                          "median_turnover": {a: float(np.median(tv[a])) for a in ARMS}},
        "tracking": trk, "early_diag": diag,
        "after_tax_windows": {wn: {a: at_view(wn, a) for a in ("Q", "T")} for wn in AT_WINDOWS},
        "after_tax_bootstrap_B1_recent": b1, "after_tax_bootstrap_B2_flat": b2,
        "gates": gates, "selection": selection, "status": "complete",
    }
    OUT.write_text(json.dumps(trv2.rnd(out), indent=2, sort_keys=True) + "\n")

    p = lambda x: f"{x * 100:7.2f}%"
    g = gates["T"]
    L = [f"instrument_cost_v1 | real funds {days[0]} -> {days[-1]} ({len(days)} sessions); measured from "
         f"{days[TSM_N + 1]} | Q = production R2 on real QLD,",
         f"harness: pre-tax {'PASS' if hp['pass'] else 'FAIL'} (max |diff| {hp['max_abs_diff_pp']:.6f} pp vs published C0/R2), "
         f"after-tax anchors {'PASS' if ha['pass'] else 'FAIL'} (max |diff| {ha['max_abs_diff_pp']:.6f} pp)", ""]
    for m in (M_GATED, M_REPORT):
        L.append(f"[m={m}] historical: arm CAGR maxDD Calmar turn/yr trades/yr | 2010win CAGR/DD | 2017win CAGR/DD | boot20 median, median(arm-Q)")
        for a in ARMS:
            x, b = hist[m][a], boot[a]
            L.append(f"  {a:6s} {p(x['cagr'])} {p(x['max_dd'])} {x['calmar']:5.3f} {x['turnover_annual']:5.2f} "
                     f"{x['events_full']['trades_per_year']:6.1f} | {p(x['window_2010']['cagr']).strip()}/{p(x['window_2010']['max_dd']).strip()} "
                     f"| {p(x['window_2017']['cagr']).strip()}/{p(x['window_2017']['max_dd']).strip()} "
                     f"| {p(b['cagr_median']).strip()} {b['diff_vs_Q_median'] * 100:+.3f}pp")
        L.append("")
    L.append("tracking (full window): fund | k | fund CAGR | k x QQQ daily CAGR | drag pp/yr")
    for s, v in trk["windows"].items():
        L.append(f"  {s:5s} {v['k']:.0f} {p(v['fund_cagr'])} {p(v['bench_cagr'])} {v['drag_pp_per_year']:+7.3f}")
    L.append("vs synthetic model: fund | real CAGR | synthetic CAGR | diff pp")
    for s, v in trk["vs_synthetic"].items():
        L.append(f"  {s:5s} {p(v['cagr_real'])} {p(v['cagr_synthetic'])} {v['diff_pp']:+7.3f}")
    L.append("liquidity: fund | avg $vol | CS spread (mean) | mean daily range")
    for s, v in trk["liquidity"].items():
        cs = v.get("corwin_schultz_spread_mean", float("nan"))
        L.append(f"  {s:5s} {v['avg_dollar_volume'] / 1e6:12,.1f}M {cs * 1e4:8.2f}bps {v['mean_daily_range_pct']:6.2f}%")
    L.append("")
    L.append("after tax ($100k, progressive 2026 tariff, liquidated, m=2): window | arm | pre-tax -> after-tax CAGR (DD) | trades/yr | tax USD")
    for wn, d in out["after_tax_windows"].items():
        for a in ("Q", "T"):
            x = d[a]
            L.append(f"  {wn:12s} {a:2s} {p(x['pre_tax_cagr'])} -> {p(x['progressive_cagr'])} "
                     f"({p(x['progressive_max_dd'])}) {x['trades_per_year']:6.1f} {x['tax_usd']:12,.0f} "
                     f"terminal {x['terminal_usd']:12,.0f}")
    L.append("")
    for label, res in (("B1-recent progressive", b1), ("B2 flat 20%", b2)):
        L.append(f"{label}: Q median {res['Q_median_cagr'] * 100:5.2f}% T median {res['T_median_cagr'] * 100:5.2f}% "
                 f"median dCAGR {res['delta_cagr_median_pp']:+6.3f}pp share>0 {res['share_delta_gt_0']:.3f} "
                 f"p05 {res['p05_pp']:+6.3f} p95 {res['p95_pp']:+6.3f} median dDD {res['delta_maxdd_median_pp']:+6.2f}pp")
    L.append("")
    L.append("gates: " + " ".join(f"{k}={'P' if v else 'F'}" for k, v in g.items() if k != "pass_all")
             + f" -> {'PASS' if g['pass_all'] else 'FAIL'}")
    L.append(f"selected: {selection['selected']}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
