"""valuation_v1 — I5: valuation / ERP-conditioned exposure on top of K.

Usage (from the worktree root, research venv):
    python -m research.valuation_v1.screen --harness    engine parity + K/R2 anchors
    python -m research.valuation_v1.screen --v0         the pre-run signal gate (V0)
    python -m research.valuation_v1.screen --run        the full study (requires V0 to pass)

Every simulation, after-tax computation and bootstrap goes through `research.fast_engine`
(`fe_sim` / `fe_tax` / `fe_boot` / `fe_study`); this module only builds target-weight arrays with
vectorised numpy. The one kernel change the study needs — a per-arm target series in the bootstrap
— lives in `fe_arm_boot.py` (a copy) with its option-off parity cell.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_config import (
    BLOCK,
    EXP_Q,
    EXTRA_FIN,
    FIN,
    M,
    M_STRESS,
    SEED,
    START,
    TAX_CAPITALS,
    TAX_WINDOWS,
)
from research.valuation_v1 import fe_arm_boot as fab

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/historical"
SIG_CSV = HIST / "valuation_v1_shiller_monthly.csv"
NDX_CSV = HIST / "valuation_v1_ndx_daily.csv"
OUT_JSON = ROOT / "research/valuation_v1/RESULTS.json"
V0_JSON = ROOT / "research/valuation_v1/V0_RESULTS.json"
OUT_TXT = ROOT / "research/valuation_v1/SUMMARY.txt"

ARMS = ("K", "V1", "V2", "V3", "V4")
US_OOS = ("GSPC_1957_1999", "M1", "M2")
NON_US_OOS = ("M3", "M4", "M5")

# K / R2 reference anchors (research/band_h2_combo_v1/RESULTS.json, main window 2000-03-09 -> 2026-09-29)
REF = {
    "K": {"base_cagr": 0.157649, "cal_cagr": 0.151785, "base_dd": 0.466698, "cal_dd": 0.467948,
          "at": {"2000_03_to_2026_09": 0.107533, "2010_01_to_end": 0.152354, "2017_01_to_end": 0.239720}},
    "R2": {"base_cagr": 0.154766, "cal_cagr": 0.148961, "base_dd": 0.467584, "cal_dd": 0.468797,
           "at": {"2000_03_to_2026_09": 0.101600, "2010_01_to_end": 0.147059, "2017_01_to_end": 0.228861}},
}
REF_TOL_PP = 0.01


# ------------------------------------------------------------------------------------- signal
class Signal:
    """Shiller's monthly EY/CAPE, month-keyed, with the month-lagged forward-filled lookup."""

    def __init__(self, path: Path = SIG_CSV):
        months, ey, cape = [], [], []
        with path.open() as fh:
            for r in csv.DictReader(fh):
                months.append(r["month"])
                ey.append(float(r["EY"]) if r["EY"] else math.nan)
                cape.append(float(r["CAPE"]) if r["CAPE"] else math.nan)
        self.months = np.array(months)
        self.ey = np.array(ey)
        self.cape = np.array(cape)
        # expanding median / population sd of CAPE over the defined prefix, per month index
        med = np.full(len(cape), np.nan)
        sd = np.full(len(cape), np.nan)
        seen = []
        for i, v in enumerate(cape):
            if v == v:
                seen.append(v)
                med[i] = float(np.median(seen))
                sd[i] = float(np.std(seen))
        self.exp_med, self.exp_sd = med, sd

    def _idx(self, month: str) -> int:
        i = int(np.searchsorted(self.months, month, side="right")) - 1
        return max(i, 0)

    def lagged(self, dates) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(EY, CAPE, expanding-median/sd index) for the decision on each session date.

        The signal is the previous calendar month's published figure (never the current month's).
        """
        ey = np.empty(len(dates))
        cape = np.empty(len(dates))
        idx = np.empty(len(dates), dtype=np.int64)
        for k, d in enumerate(dates):
            y, m = int(d[:4]), int(d[5:7])
            key = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
            i = self._idx(key)
            ey[k], cape[k], idx[k] = self.ey[i], self.cape[i], i
        return ey, cape, idx

    def window_mask(self, lo: str, hi: str) -> np.ndarray:
        return (self.months >= lo) & (self.months <= hi)

    def constants(self) -> dict:
        m = self.window_mask("1950-01", "1999-12")
        ey = self.ey[m]
        ey = ey[ey == ey]
        c = self.cape[self.window_mask("1871-01", "1999-12")]
        c = c[c == c]
        return {"EY_REF": float(np.median(ey)), "CAPE_Q90": float(np.percentile(c, 90)),
                "EY_REF_n": int(ey.size), "CAPE_Q90_n": int(c.size)}


def multipliers(dates, sig: Signal, consts: dict) -> dict:
    """The per-session multiplier of each arm (K: 1.0); the signal is month-lagged."""
    ey, cape, idx = sig.lagged(dates)
    with np.errstate(invalid="ignore"):
        v1 = np.clip(ey / consts["EY_REF"], 0.70, 1.15)
    v2 = np.where(cape > consts["CAPE_Q90"], 0.75, 1.0)
    med, sd = sig.exp_med[idx], sig.exp_sd[idx]
    v3 = np.where(cape > med + sd, 0.75, np.where(cape < med - sd, 1.10, 1.0))
    for name, arr in (("V1", v1), ("V2", v2), ("V3", v3)):
        arr[~np.isfinite(arr)] = 1.0
    v4 = consts["V4_C"] * v2
    return {"K": np.ones(len(dates)), "V1": v1, "V2": v2, "V3": v3, "V4": v4}


def ndx_inputs(spread: float = 0.0):
    """The ^NDX calibration window: (dates, synthetic-2x open/close, on_eff, factor, vol)."""
    rows = []
    with NDX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["date"] <= "2000-03-08":
                rows.append((r["date"], float(r["open"]), float(r["close"])))
    dates = [r[0] for r in rows]
    o_raw = np.array([r[1] for r in rows])
    c_raw = np.array([r[2] for r in rows])
    d3, tb = fe_data.load_rates()
    y = fe_data.rate_on(dates, d3, tb)
    o, c = fe_signals.synthetic_2x(o_raw, c_raw, np.zeros_like(c_raw), y + spread, EXP_Q)
    e2 = fe_signals.e2_up(c_raw)[0]
    comps = fe_signals.early_components(c_raw)
    on_eff, factor = fe_signals.r2_state(c_raw, e2, comps)
    return dates, o, c, on_eff, factor, fe_signals.vol_n(c)[0]


def solve_v4_c(sig: Signal, consts: dict) -> dict:
    """V4_C: V2's tilt rescaled so its mean target weight equals K's on 1985-10-01 -> 2000-03-08."""
    dates, _o, _c, on_eff, factor, vol = ndx_inputs(0.0)
    mu2 = multipliers(dates, sig, consts)["V2"]
    w_k = fab.sized_mult(on_eff, factor, vol, None)
    target = float(np.mean(w_k[START:]))
    lo, hi = 0.2, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if float(np.mean(fab.sized_mult(on_eff, factor, vol, mid * mu2)[START:])) < target:
            lo = mid
        else:
            hi = mid
    c = (lo + hi) / 2
    got = float(np.mean(fab.sized_mult(on_eff, factor, vol, c * mu2)[START:]))
    return {
        "value": c,
        "window": {"start": dates[START], "end": dates[-1], "sessions": len(dates) - START},
        "k_mean_weight": target,
        "v2_mean_weight": float(np.mean(fab.sized_mult(on_eff, factor, vol, mu2)[START:])),
        "v4_mean_weight": got,
        "residual": got - target,
    }


# ------------------------------------------------------------------------------------- inputs
def main_arms(spread: float, sig: Signal, consts: dict):
    """(days, synthetic-2x o/c, {arm: target weights}) for the QQQ main window."""
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price = q[1]
    e2 = fe_signals.e2_up(price)[0]
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    vol = fe_signals.vol_n(c)[0]
    w_k = fab.sized_mult(on_eff, factor, vol, None)
    assert np.array_equal(w_k, fe_signals.sized_wants(on_eff, factor, vol)), "sized_mult off must be bit-equal"
    mult = multipliers(days, sig, consts)
    wants = {arm: (w_k if arm == "K" else fab.sized_mult(on_eff, factor, vol, mult[arm])) for arm in ARMS}
    return days, o, c, wants, mult, (on_eff, factor, vol)


# ------------------------------------------------------------------------------------- stages
def stage_pre_tax(sig: Signal, consts: dict) -> dict:
    pre = {}
    for md, spread in FIN.items():
        days, o, c, wants, _mult, _parts = main_arms(spread, sig, consts)
        T = len(days) - 1
        fx, ufe = fe_study.try_market(days)
        env = fe_sim.RunEnv(days, fx, ufe, START, T)
        env_usd = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, T)
        pre[md] = {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in ARMS:
                r = fe_sim.simulate(o, c, wants[arm], env, TAX_CAPITALS["100k"], START, T, arm="K",
                                    mode="pre_tax", m=m)
                pre[md][f"m{m}"][arm] = fe_study.pre_stats(days, START, T, r)
        pre[md]["r_usd"] = {}
        for arm in ARMS:
            r = fe_sim.simulate(o, c, wants[arm], env_usd, TAX_CAPITALS["100k"], START, T, arm="K",
                                mode="pre_tax", m=M, rule_index=False)
            pre[md]["r_usd"][arm] = fe_study.pre_stats(days, START, T, r)
    days, *_ = fe_data.main_inputs(0.0)
    pre["window"] = {"start": days[START], "end": days[-1], "sessions": T - START + 1,
                     "years": float(fe_sim.calendar_years(days[START - 1], days[-1]))}
    return pre


def stage_after_tax(sig: Signal, consts: dict, capitals=("10k", "100k", "1M")) -> dict:
    days, o, c, wants, _mult, _parts = main_arms(0.0, sig, consts)
    fx, ufe = fe_study.try_market(days)
    out = {}
    for cl in capitals:
        out[cl] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            i0, i1 = fe_study.window_indices(days, a, b)
            env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
            out[cl][wn] = {}
            for arm in ARMS:
                rq = fe_sim.simulate(o, c, wants[arm], env, TAX_CAPITALS[cl], i0, i1, arm="K",
                                     mode="progressive")
                rp = fe_sim.simulate(o, c, wants[arm], env, 1.0, i0, i1, arm="K", mode="pre_tax")
                out[cl][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "postponements": rq["postponements"],
                    "blocked_band": rq["blocked_band"],
                    "deferred": rq["deferred"],
                    "index_relief_try": rq["index_relief_try"],
                }
                out[cl][wn][arm]["delta_vs_K_pp"] = (
                    out[cl][wn][arm]["after_tax_cagr"] - out[cl][wn]["K"]["after_tax_cagr"]
                ) * 100
    return out


def stage_cost_stress(sig: Signal, consts: dict) -> dict:
    days, o, c, wants, _mult, _parts = main_arms(0.0, sig, consts)
    fx, ufe = fe_study.try_market(days)
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        out[wn] = {}
        for arm in ARMS:
            rq = fe_sim.simulate(o, c, wants[arm], env, TAX_CAPITALS["100k"], i0, i1, arm="K",
                                 mode="progressive", m=M_STRESS)
            rp = fe_sim.simulate(o, c, wants[arm], env, 1.0, i0, i1, arm="K", mode="pre_tax", m=M_STRESS)
            out[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"], "pre_tax_cagr": rp["cagr_usd_liquidated"],
                            "max_dd_after_tax": rq["max_dd_nav_usd"], "turnover_annual": rp["turnover_annual"]}
    return out


def oos_one(days, o, c, y, start, sig: Signal, consts: dict) -> dict:
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    on_eff, factor = fe_signals.r2_state(c, e2, comps)
    vol = fe_signals.vol_n(sc)[0]
    mult = multipliers(days, sig, consts)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {}
    for arm in ARMS:
        w = fab.sized_mult(on_eff, factor, vol, None) if arm == "K" else fab.sized_mult(on_eff, factor, vol, mult[arm])
        r = fe_sim.simulate(so, sc, w, env, 1.0, start, len(days) - 1, arm="K", mode="pre_tax", m=M,
                            rule_index=False)
        st = fe_study.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["mean_weight"] = float(np.mean(w[start : len(days)]))
        res[arm] = st
    for arm in ARMS:
        if arm == "K":
            continue
        res[arm]["delta_cagr_pp"] = (res[arm]["cagr"] - res["K"]["cagr"]) * 100
        res[arm]["delta_max_dd_pp"] = (res[arm]["max_dd"] - res["K"]["max_dd"]) * 100
    return res


def stage_oos(sig: Signal, consts: dict, keys) -> dict:
    out = {}
    for key in keys:
        if key == "GSPC_1957_1999":
            days, o, c, y, start = fe_data.load_gspc()
        else:
            days, o, c, y, start = fe_data.load_market(key)
        out[key] = oos_one(days, o, c, y, start, sig, consts)
    return out


def stage_bootstrap(sig: Signal, consts: dict, seed=SEED, block=BLOCK, n_pre=fe_config.PRE_BOOT_PATHS,
                    n_tax=fe_config.TAX_BOOT_PATHS, capitals=("100k", "1M"), label="clean") -> dict:
    win = fe_boot.BootWindow()
    mult = multipliers(win.dates, sig, consts)
    return fab.boot_arms(mult, ARMS, seed=seed, block=block, n_pre=n_pre, n_tax=n_tax,
                         capitals=capitals, modes=("progressive", "flat"), window=win, label=label)


# ------------------------------------------------------------------------------------- V0
def ols_hac(x: np.ndarray, y: np.ndarray, lags: int) -> dict:
    n = len(x)
    X = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    u = y - X @ beta
    xu = X * u[:, None]
    S = xu.T @ xu  # the sum, not the mean: V = (X'X)^-1 S (X'X)^-1
    for l in range(1, min(lags, n - 1) + 1):
        w = 1.0 - l / (lags + 1.0)
        G = xu[l:].T @ xu[:-l]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    se_hac = float(np.sqrt(V[1, 1]))
    ss_res = float(u @ u)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    s2 = ss_res / (n - 2) if n > 2 else math.nan
    se_ols = float(np.sqrt(s2 * xtx_inv[1, 1])) if n > 2 else math.nan
    return {"n": int(n), "slope": float(beta[1]), "intercept": float(beta[0]),
            "t_nw": float(beta[1] / se_hac) if se_hac > 0 else None,
            "t_ols": float(beta[1] / se_ols) if se_ols > 0 else None,
            "r2": 1.0 - ss_res / ss_tot if ss_tot else None,
            "nw_lags": int(min(lags, n - 1))}


def run_v0(sig: Signal) -> dict:
    """The pre-run signal gate: 1871-1949 in-sample, 1950-1999 and 2000-2014 reported as OOS."""
    out = {"spec": {
        "dependent": "forward 10-year annualized real S&P total return (Shiller P, D, CPI)",
        "samples": {"in_sample": ["1871-01", "1949-12"], "oos_1": ["1950-01", "1999-12"],
                    "oos_2": ["2000-01", "2014-06"]},
        "primary_t": "Newey-West HAC, 120 lags", "gate": "EY slope > 0 and |t_NW| >= 2 AND CAPE slope < 0 and |t_NW| >= 2 (1871-1949)",
    }}
    f10 = sig_f10()
    for label, x in (("EY", sig.ey), ("CAPE", sig.cape)):
        out[label] = {}
        for sname, (lo, hi) in (("in_sample_1871_1949", ("1871-01", "1949-12")),
                                ("oos_1950_1999", ("1950-01", "1999-12")),
                                ("oos_2000_2014", ("2000-01", "2014-06"))):
            m = sig.window_mask(lo, hi) & np.isfinite(x) & np.isfinite(f10)
            row = ols_hac(x[m], f10[m], 120)
            keep = np.zeros_like(m)
            keep[np.flatnonzero(m)[::120]] = True
            n_keep = int(np.count_nonzero(keep))
            row["non_overlapping"] = (
                ols_hac(x[keep], f10[keep], 12) if n_keep >= 3 else {"n": n_keep}
            )
            row["non_overlapping"]["note"] = "every 120th month of the sample (report only)"
            out[label][sname] = row
    ey = out["EY"]["in_sample_1871_1949"]
    cape = out["CAPE"]["in_sample_1871_1949"]
    c1 = ey["slope"] > 0 and ey["t_nw"] is not None and abs(ey["t_nw"]) >= 2
    c2 = cape["slope"] < 0 and cape["t_nw"] is not None and abs(cape["t_nw"]) >= 2
    out["verdict"] = {"EY_slope_positive_and_t2": bool(c1), "CAPE_slope_negative_and_t2": bool(c2),
                      "pass": bool(c1 and c2)}
    return out


_F10 = None


def sig_f10() -> np.ndarray:
    """The forward 10-year real total return per month, recomputed from the committed CSV."""
    global _F10
    if _F10 is None:
        vals = []
        with SIG_CSV.open() as fh:
            for r in csv.DictReader(fh):
                vals.append(float(r["F10"]) if r["F10"] else math.nan)
        _F10 = np.array(vals)
    return _F10


# ------------------------------------------------------------------------------------- harness
def run_harness() -> dict:
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                          cwd=str(ROOT), capture_output=True, text=True)
    parity_out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    parity_pass = proc.returncode == 0 and "parity PASS" in proc.stdout
    boot_parity = fab.parity_multi_arms()
    # K and R2 anchors through the engine (C0 of the harness is R2)
    sig = Signal()
    consts = dict(sig.constants())
    v4 = solve_v4_c(sig, consts)
    consts["V4_C"] = v4["value"]
    checks = {}
    for md, spread in FIN.items():
        d_, o_, c_, w_, _mu, _parts = main_arms(spread, sig, consts)
        T = len(d_) - 1
        fx, ufe = fe_study.try_market(d_)
        env = fe_sim.RunEnv(d_, fx, ufe, START, T)
        for label, ref, w in (("K", REF["K"], w_["K"]), ("R2", REF["R2"], fe_data.main_inputs(spread)[4])):
            r = fe_sim.simulate(o_, c_, w, env, TAX_CAPITALS["100k"], START, T, arm=("K" if label == "K" else "C0"),
                                mode="pre_tax", m=M)
            st = fe_study.pre_stats(d_, START, T, r)
            key = "base" if md == "base" else "cal"
            checks[f"{label}_{key}_cagr"] = {"reproduced": st["cagr"], "reference": ref[f"{'base' if md == 'base' else 'cal'}_cagr"]}
            checks[f"{label}_{key}_max_dd"] = {"reproduced": st["max_dd"], "reference": ref[f"{'base' if md == 'base' else 'cal'}_dd"]}
    d_, o_, c_, w_, _mu, _parts = main_arms(0.0, sig, consts)
    fx, ufe = fe_study.try_market(d_)
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(d_, a, b)
        env = fe_sim.RunEnv(d_, fx, ufe, i0, i1)
        for label, ref, w in (("K", REF["K"], w_["K"]), ("R2", REF["R2"], fe_data.main_inputs(0.0)[4])):
            r = fe_sim.simulate(o_, c_, w, env, TAX_CAPITALS["100k"], i0, i1,
                                arm=("K" if label == "K" else "C0"), mode="progressive")
            checks[f"{label}_{wn}_after_tax"] = {"reproduced": r["cagr_usd_liquidated"], "reference": ref["at"][wn]}
    for k, v in checks.items():
        v["diff_pp"] = (v["reproduced"] - v["reference"]) * 100
    ok = all(abs(v["diff_pp"]) <= REF_TOL_PP for v in checks.values())
    return {"parity_quick": {"pass": bool(parity_pass), "last_line": parity_out},
            "boot_arm_parity": boot_parity, "anchors": checks, "anchors_pass": bool(ok),
            "v4_calibration": v4, "seconds": time.time() - t0}


# ------------------------------------------------------------------------------------- gates
def gates(sig: Signal, consts: dict, pre: dict, at: dict, oos: dict, boot: dict,
          exposure: dict, ndx_mean: float) -> dict:
    k_base = pre["base"]["m2"]["K"]
    k_cal = pre["calibrated"]["m2"]["K"]
    out = {"gate_definitions": {
        "G1": "pre-tax m=2 CAGR >= K, or (Calmar >= 1.1x K and CAGR >= K - 0.5 pp); base and calibrated",
        "G2": "pre-tax m=2 max DD <= K + 1.0 pp; base and calibrated",
        "G3": "2017-01-03 -> end CAGR >= K - 1 pp; base and calibrated",
        "G4": "CAGR > K in >= 2 of 3 sub-periods (base)",
        "G5": "main block-20 bootstrap (seed 20261011, 5000 paths, m=2, both models): median (arm - K) >= 0",
        "G6": "G1 at m=1; base and calibrated",
        "G7": "3 US OOS windows (reduced): median (arm - K) CAGR >= 0, >= 2/3 within -0.5 pp, DD <= K+3 pp in >= 2/3 and never > K+8 pp",
        "G8": "unavailable: e2_confirmation_v1 K5 pooled OOS bootstrap is not ported into research/fast_engine (reduced gate, see PREREGISTRATION.md)",
        "G9": "turnover/yr <= 2 x K (m=2, base)",
        "tier": "exposure-neutral (+-1.0 pp of K's mean weight) -> G5 median >= -0.05 pp and G9 only",
        "after_tax": "beats K at $100k in all three windows AND B1-recent/B2-flat median >= 0 with share > 0 >= 55%",
    }}
    for arm in ARMS:
        if arm == "K":
            continue
        ab, ac = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        a1b, a1c = pre["base"]["m1"][arm], pre["calibrated"]["m1"][arm]
        g1 = ((ab["cagr"] >= k_base["cagr"] or (ab["calmar"] >= 1.1 * k_base["calmar"] and ab["cagr"] >= k_base["cagr"] - 0.005))
              and (ac["cagr"] >= k_cal["cagr"] or (ac["calmar"] >= 1.1 * k_cal["calmar"] and ac["cagr"] >= k_cal["cagr"] - 0.005)))
        g2 = ab["max_dd"] <= k_base["max_dd"] + 0.01 and ac["max_dd"] <= k_cal["max_dd"] + 0.01
        g3 = (ab["2017_end"]["cagr"] >= k_base["2017_end"]["cagr"] - 0.01
              and ac["2017_end"]["cagr"] >= k_cal["2017_end"]["cagr"] - 0.01)
        n_gt = sum(1 for p in ("P1", "P2", "P3") if ab["subperiods"][p]["cagr"] > k_base["subperiods"][p]["cagr"])
        g4 = n_gt >= 2
        g5 = (boot["pre"]["base"][arm]["diff_vs_K_median_pp"] >= 0
              and boot["pre"]["calibrated"][arm]["diff_vs_K_median_pp"] >= 0)
        g5_med = min(boot["pre"]["base"][arm]["diff_vs_K_median_pp"],
                     boot["pre"]["calibrated"][arm]["diff_vs_K_median_pp"])
        g6 = ((a1b["cagr"] >= pre["base"]["m1"]["K"]["cagr"]
               or (a1b["calmar"] >= 1.1 * pre["base"]["m1"]["K"]["calmar"] and a1b["cagr"] >= pre["base"]["m1"]["K"]["cagr"] - 0.005))
              and (a1c["cagr"] >= pre["calibrated"]["m1"]["K"]["cagr"]
                   or (a1c["calmar"] >= 1.1 * pre["calibrated"]["m1"]["K"]["calmar"] and a1c["cagr"] >= pre["calibrated"]["m1"]["K"]["cagr"] - 0.005)))
        us = [oos[key][arm] for key in US_OOS]
        med_oos = float(np.median([u["delta_cagr_pp"] for u in us]))
        g7 = (med_oos >= 0
              and sum(1 for u in us if u["delta_cagr_pp"] >= -0.5) >= 2
              and sum(1 for u in us if u["delta_max_dd_pp"] <= 3.0) >= 2
              and max(u["delta_max_dd_pp"] for u in us) <= 8.0)
        g9 = ab["turnover_annual"] <= 2.0 * k_base["turnover_annual"]
        at100 = at["100k"]
        wins = {wn: at100[wn][arm]["delta_vs_K_pp"] for wn in TAX_WINDOWS}
        b1 = boot["tax"]["100k"]["progressive"][f"{arm}_vs_K"]
        b2 = boot["tax"]["100k"]["flat"][f"{arm}_vs_K"]
        after_tax = (all(v > 0 for v in wins.values())
                     and b1["median_delta_cagr_pp"] >= 0 and b1["share_gt_0"] >= 0.55
                     and b2["median_delta_cagr_pp"] >= 0 and b2["share_gt_0"] >= 0.55)
        dm = (exposure[arm]["mean_weight"] - ndx_mean) * 100
        tier1 = abs(dm) <= 1.0
        out[arm] = {
            "tier": ("exposure_neutral" if tier1 else "level_change"),
            "mean_weight_delta_pp": dm,
            "G1": bool(g1), "G2": bool(g2), "G3": bool(g3), "G4": bool(g4), "G5": bool(g5),
            "G6": bool(g6), "G7": bool(g7), "G9": bool(g9),
            "G5_median_min_pp": float(g5_med),
            "G7_median_oos_pp": med_oos,
            "G4_subperiods_gt": int(n_gt),
            "after_tax": bool(after_tax),
            "after_tax_windows_pp": {k: float(v) for k, v in wins.items()},
            "B1_median_pp": b1["median_delta_cagr_pp"], "B1_share_gt_0": b1["share_gt_0"],
            "B2_median_pp": b2["median_delta_cagr_pp"], "B2_share_gt_0": b2["share_gt_0"],
            "exposure_normalised_cagr": (1 + ab["cagr"]) ** (1 / exposure[arm]["mean_weight"]) - 1,
            "pass": bool((((g5_med >= -0.05) and g9) if tier1 else (g1 and g2 and g3 and g4 and g5 and g6 and g7 and g9))
                         and after_tax),
        }
    return out


def exposure_stats(wants: dict, days) -> dict:
    out = {}
    for arm, w in wants.items():
        seg = w[START : len(days)]
        out[arm] = {
            "mean_weight": float(np.mean(seg)),
            "mean_abs_delta_w": float(np.mean(np.abs(np.diff(w[START - 1 : len(days)])))),
            "share_mult_ne_1": None,
            "derisked_share": None,
        }
    return out


# ------------------------------------------------------------------------------------- driver
def sanitize(obj):
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if not math.isfinite(v) else v
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    return obj


def build(v0: dict) -> dict:
    sig = Signal()
    consts = sig.constants()
    v4 = solve_v4_c(sig, consts)
    consts["V4_C"] = v4["value"]
    consts["note"] = ("EY_REF = median monthly E/P 1950-01..1999-12; CAPE_Q90 = 90th percentile of "
                      "monthly CAPE 1871-01..1999-12; V4_C matches V4's mean target weight to K's on "
                      "the ^NDX calibration window")
    days, _o, _c, wants, mult, _parts = main_arms(0.0, sig, consts)
    expo = exposure_stats(wants, days)
    for arm in ARMS:
        seg = mult[arm][START : len(days)]
        expo[arm]["share_mult_ne_1"] = float(np.mean(seg != 1.0))
        expo[arm]["derisked_share"] = float(np.mean(seg < 1.0))
        expo[arm]["mean_mult"] = float(np.mean(seg))
    pre = stage_pre_tax(sig, consts)
    at = stage_after_tax(sig, consts)
    cost = stage_cost_stress(sig, consts)
    oos = stage_oos(sig, consts, US_OOS + NON_US_OOS)
    boot = stage_bootstrap(sig, consts, label="clean")
    boot_fresh = {}
    for blk in fe_config.E3_BLOCKS:
        boot_fresh[f"block{blk}"] = stage_bootstrap(sig, consts, seed=fe_config.E3_SEED, block=blk,
                                                    n_pre=0, n_tax=fe_config.E3_PATHS,
                                                    capitals=("100k",), label=f"fresh block{blk}")
    g = gates(sig, consts, pre, at, oos, boot, expo, expo["K"]["mean_weight"])

    frozen = stage_frozen(sig, consts, mult, pre)
    return {"campaign": "valuation_v1", "control": "K", "arms": list(ARMS), "constants": consts,
            "v0": v0, "v4_calibration": v4, "exposure": expo, "pre_tax_main": pre,
            "after_tax_windows": at, "cost_stress_m4": cost, "oos": oos, "bootstrap_clean": boot,
            "bootstrap_fresh": boot_fresh, "gates": g, "frozen_signal_robustness": frozen,
            "status": "complete", "run_seconds": None}


def stage_frozen(sig: Signal, consts: dict, mult: dict, pre: dict) -> dict:
    """Report-only robustness for the post-2024-06 stale signal (never a gate)."""
    days, o, c, wants, _mu, parts = main_arms(0.0, sig, consts)
    on_eff, factor, vol = parts
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    k_cagr = pre["base"]["m2"]["K"]["cagr"]

    # (a) the full window with the multiplier set to 1.0 wherever the signal is stale
    mult_stale = {a: v.copy() for a, v in mult.items()}
    for k, d in enumerate(days):
        if d >= "2024-08-01":          # the first decision whose signal month (2024-07) is unpublished
            for a in ("V1", "V2", "V3", "V4"):
                mult_stale[a][k] = 1.0
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    no_tilt = {}
    for arm in ("V1", "V2", "V3", "V4"):
        w = fab.sized_mult(on_eff, factor, vol, mult_stale[arm])
        r = fe_sim.simulate(o, c, w, env, TAX_CAPITALS["100k"], START, T, arm="K", mode="pre_tax", m=M)
        st = fe_study.pre_stats(days, START, T, r)
        no_tilt[arm] = {"cagr_base_m2": st["cagr"], "delta_vs_K_pp": (st["cagr"] - k_cagr) * 100}

    # (b) the same arms on 2000-03-09 -> 2024-06-28 only (the fresh-signal window)
    i1 = max(k for k, d in enumerate(days) if d <= "2024-06-28")
    env_s = fe_sim.RunEnv(days, fx, ufe, START, i1)
    k_cagr_s = fe_study.pre_stats(
        days, START, i1,
        fe_sim.simulate(o, c, wants["K"], env_s, TAX_CAPITALS["100k"], START, i1, arm="K", mode="pre_tax", m=M),
    )["cagr"]
    short = {}
    for arm in ARMS:
        r = fe_sim.simulate(o, c, wants[arm], env_s, TAX_CAPITALS["100k"], START, i1, arm="K",
                            mode="pre_tax", m=M)
        st = fe_study.pre_stats(days, START, i1, r)
        short[arm] = {"cagr_base_m2": st["cagr"], "delta_vs_K_pp": (st["cagr"] - k_cagr_s) * 100,
                      "max_dd": st["max_dd"]}
    return {"note": "report only, not a gate: (a) no tilt where the published figure is stale "
                    "(decisions from 2024-08-01), (b) the fresh-signal window only",
            "no_tilt_on_stale_sessions": no_tilt,
            "fresh_window_2000_03_09_to_2024_06_28": short,
            "window": {"start": days[START], "end": days[i1], "K_cagr": k_cagr_s}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--v0", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)
    if args.harness:
        res = sanitize(run_harness())
        (ROOT / "research/valuation_v1/HARNESS.json").write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
        return 0 if (res["parity_quick"]["pass"] and res["anchors_pass"] and res["boot_arm_parity"]["pass"]) else 1
    if args.v0:
        sig = Signal()
        res = sanitize({"campaign": "valuation_v1", "stage": "V0", **sig.constants(), **run_v0(sig)})
        V0_JSON.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
        return 0 if res["verdict"]["pass"] else 2
    if args.run:
        t0 = time.time()
        v0 = json.loads(V0_JSON.read_text())
        out = sanitize(build(v0))
        out["run_seconds"] = round(time.time() - t0, 1)
        OUT_JSON.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT_JSON} in {out['run_seconds']} s")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
