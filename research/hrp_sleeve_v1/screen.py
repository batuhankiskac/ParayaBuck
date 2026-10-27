"""hrp_sleeve_v1 screen: does an HRP / HERC / inverse-vol diversifier sleeve on K's idle and
vol-target-freed capital beat K after Turkish tax?  Pre-registration: PREREGISTRATION.md.

  python -m research.hrp_sleeve_v1.screen --harness   controls only (K, R2, parity, data checks)
  python -m research.hrp_sleeve_v1.screen --run       arms D1-D3 vs K, writes RESULTS.json / SUMMARY.txt
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_config as C
from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine3 import fe3_sim
from research.gates_v1 import stats
from research.hrp_sleeve_v1 import alloc, hs_sim
from research.hrp_sleeve_v1 import sleeve_data as SD

NAME = "hrp_sleeve_v1"
HERE = Path(__file__).resolve().parent
CAP = C.CAP
LOOKBACK = 252
ARM_METHOD = {"D1": "hrp", "D2": "herc", "D3": "ivol"}
ARM_NAME = {"D1": "HRP sleeve", "D2": "HERC sleeve", "D3": "inverse-vol sleeve"}
SLEEVE = ("IEF", "GLD", "BIL")
SLEEVE_BAND = 0.02  # per-leg resize band of the sleeve legs; QLD keeps C0_BAND (0.5 pp)
N_PRIOR_TRIALS = 372  # registry count (gates_v1 / protocol); DSR N = 372 + 3 arms
N_TRIALS = N_PRIOR_TRIALS + len(ARM_METHOD)  # 375
MODE = "progressive"
PRE_POST = {"pre_2020": ("2000-03-09", "2019-12-31"), "post_2020": ("2020-01-01", "2026-09-29")}


def build_inputs():
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    ief, gld, bil = SD.sleeve_returns(days)
    R = np.column_stack([ief, gld, bil])
    P = np.column_stack([SD.prices_from_returns(R[:, k]) for k in range(3)])
    P = np.where(np.isnan(P), 1.0, P)  # weight is zero wherever a series is not yet in the universe
    return days, o, c, w_r2, fx, ufe, R, P


def monthly_weights(days, R, method):
    """Sleeve weights (T, 3) decided at each month-end close from the trailing 252 returns."""
    T = len(days)
    fn = {"hrp": alloc.hrp, "herc": alloc.herc, "ivol": alloc.ivol}[method]
    month_end = {i for i in range(T) if i == T - 1 or days[i][:7] != days[i + 1][:7]}
    W = np.zeros((T, 3))
    cur = np.zeros(3)
    for t in range(T):
        if t in month_end and t >= LOOKBACK:
            win = R[t - LOOKBACK + 1: t + 1]
            idx = np.where(~np.isnan(win).any(axis=0))[0]
            cur = np.zeros(3)
            if idx.size:
                cov = np.atleast_2d(np.cov(win[:, idx], rowvar=False, ddof=1))
                cur[idx] = fn(cov)
        W[t] = cur
    return W


def leg_panels(w_qld, Wm, o, c, P):
    """(opens, closes, wants) for legs A=QLD, B=IEF, C=GLD, D=BIL; sleeve = (0.98 - w_QLD) * pi."""
    s = np.maximum(CAP - w_qld, 0.0)
    wants = [w_qld] + [s * Wm[:, k] for k in range(3)]
    opens = [o] + [P[:, k] for k in range(3)]
    closes = [c] + [P[:, k] for k in range(3)]
    return opens, closes, wants


def run(opens, closes, wants, days, fx, ufe, i0, i1, capital, mode, band_legs, arm="K", m=2):
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    return hs_sim.simulate3(opens, closes, wants, env, capital, i0, i1, arm=arm, mode=mode, m=m,
                            band_legs=band_legs)


def summarise(r):
    out = {k: (float(v) if isinstance(v, (int, float, np.floating)) else None)
           for k, v in r.items() if isinstance(v, (int, float, np.floating))}
    return out


def harness(days, o, c, w_r2, fx, ufe, R, P):
    """K and R2 reproduced through the per-leg-band kernel, and bit-parity with fe3_sim."""
    T = len(days) - 1
    res = {}
    i0, i1 = fe_study.window_indices(days, *C.TAX_WINDOWS["2000_03_to_2026_09"])
    band1 = np.array([fe_sim.C0_BAND])
    for arm in ("K", "R2"):
        r_new = run([o], [c], [w_r2], days, fx, ufe, i0, i1, 100_000.0, MODE, band1, arm=arm)
        r_old = fe3_sim.simulate3([o], [c], [w_r2], fe_sim.RunEnv(days, fx, ufe, i0, i1), 100_000.0,
                                  i0, i1, arm=arm, mode=MODE, m=2)
        same = bool(np.array_equal(r_new["_nav"] if "_nav" in r_new else np.array([0.0]),
                                   r_old["_nav"] if "_nav" in r_old else np.array([0.0])))
        res[arm] = {"after_tax_2000_cagr": r_new["cagr_usd_liquidated"],
                    "max_dd_after_tax": r_new["max_dd_nav_usd"],
                    "parity_with_fe3_sim_scalars": all(
                        r_new[k] == r_old[k] for k in r_old
                        if isinstance(r_old[k], float) and k in r_new),
                    "nav_bitwise_equal": same}
    return res


def arms_run(days, o, c, w_r2, fx, ufe, R, P):
    out = {"windows": {}, "pre_post_2020": {}, "pre_tax_main": {}, "weights_last": {}, "weight_stats": {}}
    Wm = {arm: monthly_weights(days, R, ARM_METHOD[arm]) for arm in ARM_METHOD}
    for arm in ARM_METHOD:
        out["weights_last"][arm] = [float(x) for x in Wm[arm][-1]]
        live = Wm[arm][Wm[arm].sum(axis=1) > 0]
        out["weight_stats"][arm] = {
            "sessions_with_sleeve": int(live.shape[0]),
            "ief_mean": float(live[:, 0].mean()), "gld_mean": float(live[:, 1].mean()),
            "bil_mean": float(live[:, 2].mean()), "bil_min": float(live[:, 2].min()),
            "ief_max": float(live[:, 0].max()), "gld_max": float(live[:, 1].max())}
    band_k = np.array([fe_sim.C0_BAND])
    band_s = np.array([fe_sim.C0_BAND] + [SLEEVE_BAND] * 3)
    T = len(days) - 1
    # pre-tax main window (liquidation-free, m = 2) for K and each arm
    for label, bands in (("K", band_k),):
        r = run([o], [c], [w_r2], days, fx, ufe, fe_study.START, T, 100_000.0, "pre_tax", bands)
        out["pre_tax_main"][label] = summarise(r)
    for arm in ARM_METHOD:
        ops, cls, wts = leg_panels(w_r2, Wm[arm], o, c, P)
        r = run(ops, cls, wts, days, fx, ufe, fe_study.START, T, 100_000.0, "pre_tax", band_s)
        out["pre_tax_main"][arm] = summarise(r)
    # after-tax windows at $100k, liquidated, m = 2, progressive
    for wn, (a, b) in C.TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        out["windows"][wn] = {}
        rk = run([o], [c], [w_r2], days, fx, ufe, i0, i1, 100_000.0, MODE, band_k)
        out["windows"][wn]["K"] = _row(rk)
        for arm in ARM_METHOD:
            ops, cls, wts = leg_panels(w_r2, Wm[arm], o, c, P)
            r = run(ops, cls, wts, days, fx, ufe, i0, i1, 100_000.0, MODE, band_s)
            out["windows"][wn][arm] = _row(r, dsr_trials=N_TRIALS)
    # pre / post 2020 split of the after-tax $100k result
    for pw, (a, b) in PRE_POST.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        out["pre_post_2020"][pw] = {}
        rk = run([o], [c], [w_r2], days, fx, ufe, i0, i1, 100_000.0, MODE, band_k)
        out["pre_post_2020"][pw]["K"] = _row(rk)
        for arm in ARM_METHOD:
            ops, cls, wts = leg_panels(w_r2, Wm[arm], o, c, P)
            r = run(ops, cls, wts, days, fx, ufe, i0, i1, 100_000.0, MODE, band_s)
            out["pre_post_2020"][pw][arm] = _row(r)
    return out


def _row(r, dsr_trials=None):
    years = float(r["years"]) if "years" in r else float("nan")
    row = {"after_tax_cagr": float(r["cagr_usd_liquidated"]), "max_dd": float(r["max_dd_nav_usd"]),
           "sells_per_year": float(r["sells"]) / years if years else float("nan"),
           "trades_per_year": float(r["strategy_trades"]) / years if years else float("nan"),
           "tax_usd": float(r["tax_total_usd_at_payment_fx"])}
    legs = r.get("_legs", {})
    row["legs"] = {k: {kk: float(vv) for kk, vv in v.items() if isinstance(vv, (int, float, np.floating))}
                   for k, v in legs.items()} if isinstance(legs, dict) else {}
    if dsr_trials is not None:
        from scipy.stats import kurtosis, skew
        nav = np.asarray(r["_nav"], dtype=np.float64)
        rr = np.diff(np.log(nav))
        rr = rr[np.isfinite(rr)]
        sr = float(rr.mean() / rr.std(ddof=1))
        t = int(rr.size)
        row["dsr"] = stats.dsr(sr, t, float(skew(rr)), float(kurtosis(rr, fisher=False)),
                               dsr_trials, stats.var_trials_floor(t))
        row["daily_sr"] = sr
    return row


def main(argv):
    t0 = time.time()
    days, o, c, w_r2, fx, ufe, R, P = build_inputs()
    if "--harness" in argv:
        out = {"study": NAME, "harness": harness(days, o, c, w_r2, fx, ufe, R, P)}
        print(json.dumps(out["harness"], indent=1, default=str))
        return
    if "--run" in argv:
        from research.gates_v1 import provenance

        out = arms_run(days, o, c, w_r2, fx, ufe, R, P)
        out["study"] = NAME
        man = json.loads((HERE / "DATA_MANIFEST.json").read_text())
        out["provenance"] = provenance.stamp({k: str(C.ROOT / k) for k in man["inputs"]}, cwd=str(C.ROOT))
        out["control_K_anchor"] = "band_h2_combo_v1/RESULTS.json after_tax_windows.100k (see SUMMARY.txt)"
        (HERE / "RESULTS.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
        wall = time.time() - t0
        lines = [f"hrp_sleeve_v1 --run wall clock: {wall:.2f} s (includes data build and numba cache load)"]
        for a in ("K", "D1", "D2", "D3"):
            if a == "K":
                k = out["windows"]["2000_03_to_2026_09"]["K"]
                lines.append(f"K after-tax 2000/2010/2017: " + " / ".join(
                    f"{out['windows'][w]['K']['after_tax_cagr']*100:.2f}" for w in C.TAX_WINDOWS))
            else:
                lines.append(f"{a} after-tax 2000/2010/2017: " + " / ".join(
                    f"{out['windows'][w][a]['after_tax_cagr']*100:.2f}" for w in C.TAX_WINDOWS))
        (HERE / "SUMMARY.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
