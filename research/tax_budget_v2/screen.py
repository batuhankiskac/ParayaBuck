"""tax_budget_v2 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Idea I3 of `docs/research/idea_survey_v4.md`: annual tax-budget execution on top of production K.
Control = production **K** (`equity_leveraged_trend_v1` 1.2.0 = E2 + R2 early re-entry + A1u
gain-scaled downsize band + H2 December deferral), on the mandatory Numba engine
`research/fast_engine/` (tag `archive/research/fast-engine-v1-2026-10`), forked into
`research/tax_budget_v2/fe/` for the two new execution mechanisms this study needs.

Arms (pre-registered; every parameter fixed in PREREGISTRATION.md section 4):
  K    production K, verbatim (the control).
  C0   production **R2** (K's execution layer OFF) -- the report-only reference row (== R2).
  Y1   loss-pool fill: at the last session of each calendar year fully inside the window, if the
       account's YTD realised net TRY gain is negative, sell-and-rebuy the smallest quantity that
       brings the year to ~0, capped at 25 % of NAV; both legs fill at that close.
  Y2   bracket-headroom fill: same session, fire while YTD < 25 % of the year's deflated top
       bracket ceiling, fill up to that ceiling (same cap).
  Y3   no-new-trade year reallocation: a pending non-exit downsize whose fill session is one of
       the last 10 December or the first 10 January sessions fills only when the session's own tax
       year is the cheaper of the two adjacent years (state, not calendar); A1u's band then applies
       as in K.
  Y4   Y1 + Y3 (report-only upper bound).

Stages (pre-registered): pre (main window, base/calibrated x m = 1/2/4 + the R-USD row), AT (after
tax at $100k and $1M on the three windows), cost (m = 4 stress), B12 (clean bootstrap, seed
20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, B1-recent and B2 flat), OOS (the six
ewma_band_v1 windows), OOSB (gate G8's pooled M1-M5 bootstrap), starts (21 rolling January
starts), pool (the destroyed-loss pool measurement from the control only), log (the arms' events).

Gates: G1-G9 of `ewma_band_v1`/the worker protocol against K, with **G5 pre-registered as
non-inferiority** (pre-tax bootstrap median delta >= -0.10 pp), plus A1/A2 after tax; the
selection rule is fixed in PREREGISTRATION.md section 7.

Modes
  --smoke    synthetic random-walk prices only, no market data: the fork's option-off bit identity
             against the parent engine, the Y1/Y2 trigger and cap arithmetic vs the engine's own
             booked gains, the round trip's unit/NAV invariants, Y3's state test and window, and
             finiteness/after-tax <= pre-tax on a synthetic window.
  --harness  `python -m research.fast_engine.parity --quick` (must PASS) and, on real data, the
             fork-vs-parent option-off bit identity plus every published K and C0 cell of
             research/band_h2_combo_v1/RESULTS.json (pre_tax_main, pre_tax_main_r_usd,
             after_tax_windows, cost_stress_m4, oos, bootstrap_clean_seed20261011) and the literal
             brief anchors.
  --run      harness, then every stage, the gates and the selection rule; writes RESULTS.json and
             SUMMARY.txt (RESULTS.json is byte-identical across runs).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.fast_engine import fe_sim as psim
from research.tax_budget_v2 import fe

ROOT = Path(__file__).resolve().parents[2]
OUTDIR = Path(__file__).resolve().parent
EVIDENCE = ROOT / "research/band_h2_combo_v1/RESULTS.json"

ARMS = ("C0", "K", "Y1", "Y2", "Y3", "Y4")
GATED = ("Y1", "Y2", "Y3")
REPORT_ONLY = ("Y4",)
CONTROL = "K"
REFERENCE = "C0"  # production R2 (RULE alias), the report-only reference row
TOL = 1e-6  # RESULTS.json stores round(x, 6); parity's published cells differ by <= 5e-07
NAV_CAP_FRAC = fe.fe_sim.NAV_CAP_FRAC
HEADROOM_TRIGGER_FRAC = fe.fe_sim.HEADROOM_TRIGGER_FRAC
G5_MARGIN_PP = -0.10
WINDOW_KEYS = tuple(fe.fe_config.TAX_WINDOWS)


def rnd(x, nd=6):
    """`trend_robustness_v2.rnd`, so RESULTS.json's floats are rounded the published way."""
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, nd) for v in x]
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return round(f, nd) if math.isfinite(f) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.ndarray):
        return rnd(x.tolist(), nd)
    return x


def main_inputs(spread=0.0):
    return fe.fe_data.main_inputs(spread)


def try_market(days):
    return fe.fe_data.fx_at(days), fe.fe_data.load_ufe()


def _delta_stats(d):
    """The parent bootstrap's delta block, computed from the kept per-path arrays."""
    return {
        "median_pp": float(np.median(d) * 100),
        "mean_pp": float(np.mean(d) * 100),
        "share_gt_0": float(np.mean(d > 0)),
        "p05_pp": float(np.percentile(d, 5) * 100),
        "p95_pp": float(np.percentile(d, 95) * 100),
        "paths": int(d.shape[0]),
    }


# ----------------------------------------------------------------------------- stages
def pre_tax_main(arms=ARMS):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD row."""
    return fe.fe_study.pre_tax_main(arms)


def after_tax_windows(capitals=("100k", "1M"), arms=ARMS):
    """After tax (progressive, liquidated, base financing, m = 2) on the three windows."""
    return fe.fe_study.after_tax_windows(capitals, arms)


def cost_stress(arms=ARMS):
    """m = 4 cost stress, after tax at $100k on the three windows (report-only)."""
    return fe.fe_study.cost_stress(arms)


def rolling_starts(arms=ARMS):
    """Report-only: 21 rolling January starts (2001..2021), after tax at $100k, base, m = 2."""
    out = fe.fe_study.stage_e2(arms)
    for _yr, rec in out.items():
        for a in arms:
            if a != CONTROL:
                rec[a]["delta_vs_K_pp"] = (rec[a]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
    return out


def oos_stages(arms=ARMS):
    """Six OOS windows (calibrated, m = 2, R-USD) plus the delta-vs-K cells of gate G7."""
    out = fe.fe_study.oos_stages(arms)
    for wn in fe.fe_config.OOS_WINDOWS:
        for a in arms:
            if a != CONTROL:
                out[wn][a]["delta_vs_K_cagr_pp"] = (out[wn][a]["cagr"] - out[wn][CONTROL]["cagr"]) * 100
                out[wn][a]["delta_vs_K_max_dd_pp"] = (out[wn][a]["max_dd"] - out[wn][CONTROL]["max_dd"]) * 100
    return out


def oos_bootstrap(arms=ARMS):
    """Gate G8: the pooled M1-M5 OOS bootstrap (2,000 paths per market) vs K."""
    b = fe.fe_oos.oos_boot(arms)
    base = b["pooled"][CONTROL]
    out = {"per_market": {}, "pooled": {}}
    for key, per in b["per_market"].items():
        out["per_market"][key] = {a: _delta_stats(per[a] - per[CONTROL]) for a in arms}
    for a in arms:
        out["pooled"][a] = _delta_stats(b["pooled"][a] - base)
    out["meta"] = {
        "seed": fe.fe_config.SEED,
        "block": fe.fe_config.BLOCK,
        "paths_per_market": fe.fe_oos.OOS_PATHS_PER_MARKET,
        "markets": list(fe.fe_oos.OOS_MARKETS),
        "mode": "pre_tax",
        "financing": "calibrated (0.75 %)",
        "m": fe.fe_config.M,
        "rule_index": False,
    }
    return out


def boot_clean(arms=ARMS, n_pre=None, n_tax=None, quiet=False):
    """B12: the clean block bootstrap with the per-path arrays kept (deltas are computed vs K)."""
    cfg = fe.fe_config
    return fe.fe_boot.boot(
        seed=cfg.SEED,
        block=cfg.BLOCK,
        arms=arms,
        n_pre=cfg.PRE_BOOT_PATHS if n_pre is None else n_pre,
        n_tax=cfg.TAX_BOOT_PATHS if n_tax is None else n_tax,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        anchor_e2=False,
        deltas=tuple(a for a in arms if a != CONTROL),
        baselines=(CONTROL, REFERENCE),
        label="B12",
        quiet=quiet,
        keep_paths=True,
    )


def boot_delta_vs_K(b):
    """Every bootstrap cell of section 7's G5/A2, from the kept per-path arrays."""
    paths = b["paths"]
    out = {"pre": {}, "tax": {}}
    for mdi in sorted({k[0] for k in paths["pre"]}):
        base = paths["pre"][(mdi, CONTROL)]
        out["pre"][mdi] = {a: _delta_stats(paths["pre"][(mdi, a)] - base) for m0, a in paths["pre"] if m0 == mdi}
    for cl, modes in paths["tax"].items():
        out["tax"][cl] = {}
        for mode, block in modes.items():
            base = block[CONTROL]
            out["tax"][cl][mode] = {a: _delta_stats(arr - base) for a, arr in block.items()}
    return out


def pool_diagnostic(arm=CONTROL, capital=100_000.0, mode="progressive"):
    """Section 6: the destroyed-loss pool and the bracket arithmetic, from the control only."""
    days, o, c, _we, w_r2, _e2 = main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe.fe_sim.RunEnv(days, fx, ufe, fe.fe_config.START, T)
    r = fe.fe_sim.simulate(o, c, w_r2, env, capital, fe.fe_config.START, T, arm=arm, mode=mode)
    gy = r["realised_gain_try_by_year"]
    rows = []
    for i in range(fe.fe_config.START, T + 1):
        if i < T and int(days[i + 1][:4]) != int(days[i][:4]):
            y = int(days[i][:4])
            ytd = gy.get(y, 0.0)
            ceiling = fe.fe_tax.TARIFF_TOP * float(env.scale_y[y - env.Y0])
            nav = float(r["_nav"][i - fe.fe_config.START + 1])
            fxi = float(fx[i])
            cap_try = NAV_CAP_FRAC * nav * fxi
            headroom_gap = max(0.0, ceiling - ytd)
            rows.append(
                {
                    "year": y,
                    "session": days[i],
                    "ytd_try": float(ytd),
                    "pool_try": max(0.0, -ytd),
                    "loss_year": bool(ytd < 0.0),
                    "ceiling_try": ceiling,
                    "ytd_over_ceiling": float(ytd / ceiling) if ceiling else None,
                    "headroom_gap_try": headroom_gap,
                    "nav_usd": nav,
                    "fx": fxi,
                    "cap_25pct_try": cap_try,
                    "cap_binds_25pct": bool(cap_try < headroom_gap),
                    "y1_trigger": bool(ytd < 0.0),
                    "y2_trigger": bool(ytd < HEADROOM_TRIGGER_FRAC * ceiling),
                }
            )
    loss = [p for p in rows if p["loss_year"]]
    return {
        "arm": arm,
        "mode": mode,
        "capital_usd": capital,
        "window": {"start": r["window"][0], "end": r["window"][1]},
        "n_years": len(rows),
        "n_loss_years": len(loss),
        "loss_years": [p["year"] for p in loss],
        "pool_sum_try": float(sum(p["pool_try"] for p in rows)),
        "pool_sum_usd": float(sum(p["pool_try"] / p["fx"] for p in rows if p["fx"])),
        "n_years_y1_trigger": int(sum(1 for p in rows if p["y1_trigger"])),
        "n_years_y2_trigger": int(sum(1 for p in rows if p["y2_trigger"])),
        "n_years_cap_binds": int(sum(1 for p in rows if p["cap_binds_25pct"])),
        "per_year": rows,
    }


def event_log(arms=("Y1", "Y2", "Y3", "Y4", "K"), modes=("progressive", "pre_tax")):
    """The arms' year-end round trips and realloc holds on the main window at $100k, m = 2."""
    days, o, c, _we, w_r2, _e2 = main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe.fe_sim.RunEnv(days, fx, ufe, fe.fe_config.START, T)
    out = {}
    for mode in modes:
        out[mode] = {}
        for arm in arms:
            r = fe.fe_sim.simulate(
                o, c, w_r2, env, 100_000.0, fe.fe_config.START, T, arm=arm, mode=mode, m=2, trace=True
            )
            ye = r["_ye_events"]
            re_ = r["_re_events"]
            trips = [
                {
                    "session": days[fe.fe_config.START + k],
                    "ytd_try_before": float(ye[k, 1]),
                    "ytd_try_after": float(ye[k, 2]),
                    "units": float(ye[k, 3]),
                    "notional_usd": float(ye[k, 4]),
                    "wanted_units": float(ye[k, 5]),
                    "nav_usd": float(ye[k, 6]),
                    "ceiling_try": float(ye[k, 7]),
                    "units_before": float(ye[k, 8]),
                    "units_after": float(ye[k, 9]),
                    "cap_binding": bool(ye[k, 3] < ye[k, 5] - 1e-9),
                    "crossing_unreachable": bool(ye[k, 5] >= ye[k, 8] - 1e-12),
                    "frac_nav": float(ye[k, 4] / ye[k, 6]) if ye[k, 6] else None,
                }
                for k in range(ye.shape[0])
                if ye[k, 0]
            ]
            holds = [
                {
                    "session": days[fe.fe_config.START + k],
                    "month": days[fe.fe_config.START + k][5:7],
                    "ytd_cur_try": float(re_[k, 1]),
                    "ytd_other_try": float(re_[k, 2]),
                    "drift": float(re_[k, 3]),
                }
                for k in range(re_.shape[0])
                if re_[k, 0]
            ]
            by_month = {}
            for h in holds:
                by_month[h["month"]] = by_month.get(h["month"], 0) + 1
            booked = float(sum(t["ytd_try_after"] - t["ytd_try_before"] for t in trips))
            reason_gain = float(r["realised_gain_try_by_reason"].get("yearend", 0.0))
            out[mode][arm] = {
                "n_round_trips": len(trips),
                "n_cap_binding": int(sum(1 for t in trips if t["cap_binding"])),
                "n_crossing_unreachable": int(sum(1 for t in trips if t["crossing_unreachable"])),
                "round_trip_notional_usd": float(sum(t["notional_usd"] for t in trips)),
                "round_trips": trips,
                "booked_gain_try": booked,
                "reason_gain_yearend_try": reason_gain,
                "booked_matches_reason": bool(abs(booked - reason_gain) <= 1e-6 * max(1.0, abs(reason_gain))),
                "n_realloc_holds": len(holds),
                "realloc_holds_by_month": by_month,
                "realloc_holds": holds,
                "turnover_annual": float(r["turnover_annual"]),
                "cagr_usd_liquidated": float(r["cagr_usd_liquidated"]),
            }
    return out


# ----------------------------------------------------------------------------- gates
def _g1(a, k):
    return bool(a["cagr"] >= k["cagr"] or (a["calmar"] >= 1.1 * k["calmar"] and a["cagr"] >= k["cagr"] - 0.005))


def pre_gates(pre, bd, oos, oosb, arm):
    """G1-G9 of section 7 for one arm, with the measured values beside every decision."""
    k2 = {mdi: pre[mdi]["m2"][CONTROL] for mdi in ("base", "calibrated")}
    k1 = {mdi: pre[mdi]["m1"][CONTROL] for mdi in ("base", "calibrated")}
    a2 = {mdi: pre[mdi]["m2"][arm] for mdi in ("base", "calibrated")}
    a1 = {mdi: pre[mdi]["m1"][arm] for mdi in ("base", "calibrated")}
    rec = {"G1": {}, "G2": {}, "G3": {}, "G5": {}, "G6": {}}
    for mdi in ("base", "calibrated"):
        rec["G1"][mdi] = {
            "cagr_arm": a2[mdi]["cagr"],
            "cagr_K": k2[mdi]["cagr"],
            "delta_pp": (a2[mdi]["cagr"] - k2[mdi]["cagr"]) * 100,
            "calmar_arm": a2[mdi]["calmar"],
            "calmar_K": k2[mdi]["calmar"],
            "pass": _g1(a2[mdi], k2[mdi]),
        }
        rec["G2"][mdi] = {
            "max_dd_arm": a2[mdi]["max_dd"],
            "max_dd_K": k2[mdi]["max_dd"],
            "delta_pp": (a2[mdi]["max_dd"] - k2[mdi]["max_dd"]) * 100,
            "pass": bool(a2[mdi]["max_dd"] <= k2[mdi]["max_dd"] + 0.01),
        }
        rec["G3"][mdi] = {
            "cagr_2017_arm": a2[mdi]["2017_end"]["cagr"],
            "cagr_2017_K": k2[mdi]["2017_end"]["cagr"],
            "delta_pp": (a2[mdi]["2017_end"]["cagr"] - k2[mdi]["2017_end"]["cagr"]) * 100,
            "pass": bool(a2[mdi]["2017_end"]["cagr"] >= k2[mdi]["2017_end"]["cagr"] - 0.01),
        }
        rec["G5"][mdi] = {
            "median_delta_pp": bd["pre"][mdi][arm]["median_pp"],
            "share_gt_0": bd["pre"][mdi][arm]["share_gt_0"],
            "pass": bool(bd["pre"][mdi][arm]["median_pp"] >= G5_MARGIN_PP),
        }
        rec["G6"][mdi] = {
            "cagr_arm": a1[mdi]["cagr"],
            "cagr_K": k1[mdi]["cagr"],
            "delta_pp": (a1[mdi]["cagr"] - k1[mdi]["cagr"]) * 100,
            "calmar_arm": a1[mdi]["calmar"],
            "calmar_K": k1[mdi]["calmar"],
            "pass": _g1(a1[mdi], k1[mdi]),
        }
    subs = ("P1", "P2", "P3")
    wins = [s for s in subs if a2["base"]["subperiods"][s]["cagr"] > k2["base"]["subperiods"][s]["cagr"]]
    rec["G4"] = {
        "subperiod_deltas_pp": {s: (a2["base"]["subperiods"][s]["cagr"] - k2["base"]["subperiods"][s]["cagr"]) * 100 for s in subs},
        "wins": wins,
        "pass": bool(len(wins) >= 2),
    }
    dc = []
    dd = []
    for wn in fe.fe_config.OOS_WINDOWS:
        dc.append(oos[wn][arm]["delta_vs_K_cagr_pp"])
        dd.append(oos[wn][arm]["delta_vs_K_max_dd_pp"])
    good = [k for k in range(len(dc)) if dc[k] >= -0.5 and dd[k] <= 3.0]
    rec["G7"] = {
        "delta_cagr_pp": {wn: dc[k] for k, wn in enumerate(fe.fe_config.OOS_WINDOWS)},
        "delta_max_dd_pp": {wn: dd[k] for k, wn in enumerate(fe.fe_config.OOS_WINDOWS)},
        "median_delta_cagr_pp": float(np.median(dc)),
        "n_windows_ok": len(good),
        "max_delta_max_dd_pp": float(max(dd)),
        "pass": bool(np.median(dc) >= 0.0 and len(good) >= 5 and max(dd) <= 8.0),
    }
    rec["G8"] = {
        "median_delta_pp": oosb["pooled"][arm]["median_pp"],
        "share_gt_0": oosb["pooled"][arm]["share_gt_0"],
        "paths": oosb["pooled"][arm]["paths"],
        "pass": bool(oosb["pooled"][arm]["median_pp"] >= 0.0),
    }
    to_a = a2["base"]["turnover_annual"]
    to_k = k2["base"]["turnover_annual"]
    rec["G9"] = {
        "turnover_arm": to_a,
        "turnover_K": to_k,
        "ratio": to_a / to_k if to_k else None,
        "pass": bool(to_a <= 2.0 * to_k),
    }
    rec["pass"] = bool(
        all(rec["G1"][m]["pass"] for m in ("base", "calibrated"))
        and all(rec["G2"][m]["pass"] for m in ("base", "calibrated"))
        and all(rec["G3"][m]["pass"] for m in ("base", "calibrated"))
        and rec["G4"]["pass"]
        and all(rec["G5"][m]["pass"] for m in ("base", "calibrated"))
        and all(rec["G6"][m]["pass"] for m in ("base", "calibrated"))
        and rec["G7"]["pass"]
        and rec["G8"]["pass"]
        and rec["G9"]["pass"]
    )
    return rec


def tax_gates(win, bd, arm):
    """A1 and A2 of section 7 for one arm at the gate capital."""
    rec = {"A1": {}, "A2": {}}
    for wn in WINDOW_KEYS:
        d = (win["100k"][wn][arm]["after_tax_cagr"] - win["100k"][wn][CONTROL]["after_tax_cagr"]) * 100
        rec["A1"][wn] = {
            "after_tax_arm": win["100k"][wn][arm]["after_tax_cagr"],
            "after_tax_K": win["100k"][wn][CONTROL]["after_tax_cagr"],
            "delta_pp": d,
            "pass": bool(d > 0.0),
        }
    rec["A1"]["pass"] = bool(all(rec["A1"][wn]["pass"] for wn in WINDOW_KEYS))
    for mode in ("progressive", "flat"):
        cell = bd["tax"]["100k"][mode][arm]
        rec["A2"][mode] = {
            "median_delta_pp": cell["median_pp"],
            "share_gt_0": cell["share_gt_0"],
            "pass": bool(cell["median_pp"] >= 0.0 and cell["share_gt_0"] >= 0.55),
        }
    rec["A2"]["pass"] = bool(all(rec["A2"][m]["pass"] for m in ("progressive", "flat")))
    rec["pass"] = bool(rec["A1"]["pass"] and rec["A2"]["pass"])
    return rec


def select(gates):
    """The pre-registered selection rule: highest B1-recent $100k median delta among passers."""
    ok = [a for a in GATED if gates[a]["pre"]["pass"] and gates[a]["tax"]["pass"]]
    if not ok:
        return {"selected": None, "reason": "no arm passed G1-G9 + A1 + A2", "passing": []}
    def key(a):
        return (
            gates[a]["boot_delta"]["tax"]["100k"]["progressive"]["median_pp"],
            gates[a]["tax"]["A1"]["2000_03_to_2026_09"]["delta_pp"],
        )

    ranked = sorted(ok, key=key, reverse=True)
    return {
        "selected": ranked[0],
        "passing": ranked,
        "rank_key": "B1-recent $100k after-tax bootstrap median delta vs K (tie-break: 2000 window delta)",
        "ranking": [
            {
                "arm": a,
                "b1_median_pp": gates[a]["boot_delta"]["tax"]["100k"]["progressive"]["median_pp"],
                "tie_break_pp": gates[a]["tax"]["A1"]["2000_03_to_2026_09"]["delta_pp"],
            }
            for a in ranked
        ],
    }


# ----------------------------------------------------------------------------- smoke / harness
def synthetic_days(n, start="2016-01-04"):
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def synthetic_market(n=1600, seed=7):
    """A deterministic synthetic window: a crash-and-exit year (loss realisations) followed by a
    recovery, so both the reachable-crossing and the whole-book fallback branches of Y1 run."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.012, n)
    c1 = n // 4
    r[c1 : c1 + 40] = -0.015  # year 2: a crash
    c = 100.0 * np.cumprod(1.0 + r)
    o = np.empty(n)
    o[0] = c[0]
    o[1:] = c[:-1] * (1.0 + rng.normal(0.0, 0.002, n - 1))
    days = synthetic_days(n)
    fx = np.ones(n)  # flat TRY price: TRY gains are the USD gains, so a crash can be a loss
    ymin, ymax = int(days[0][:4]) - 2, int(days[-1][:4]) + 2
    ufe = {}
    idx = 100.0
    for y in range(ymin, ymax + 1):
        for mo in range(1, 13):
            ufe[f"{y}-{mo:02d}"] = idx
            idx *= 1.01
    ufe["2025-10"] = 100.0 * 1.01 ** 120  # the progressive scale anchor (synthetic)
    return days, o, c, fx, ufe


def synthetic_wants(c, days, env, start):
    """Targets that force a pending downsize inside the December tail and the January head."""
    n = len(c)
    w = np.full(n, 0.9)
    masks = {"dec": env.dec_tail, "jan": env.jan_head}
    for name, mask in masks.items():
        idxs = [i for i in range(start, n) if mask[i]]
        for k in range(4, len(idxs), 6):
            w[idxs[k] : idxs[k] + 8] = 0.2
    # a full exit then re-entry around the crash, so a year's realised TRY gain goes negative
    cmid = n // 2
    w[cmid : cmid + 30] = 0.0
    return w


def smoke():
    days, o, c, fx, ufe = synthetic_market()
    n = len(days)
    start = 260
    env = fe.fe_sim.RunEnv(days, fx, ufe, start, n - 1)
    env_p = psim.RunEnv(days, fx, ufe, start, n - 1)
    w = synthetic_wants(c, days, env, start)
    checks = {}
    # (a) the fork's option-off path is the parent engine, bit for bit (synthetic window too)
    same = True
    for arm in ("C0", "A1u", "H2", "K"):
        for mode in ("pre_tax", "progressive", "flat"):
            for m in (1, 2):
                a = psim.simulate(o, c, w, env_p, 100_000.0, start, n - 1, arm=arm, mode=mode, m=m)
                b = fe.fe_sim.simulate(o, c, w, env, 100_000.0, start, n - 1, arm=arm, mode=mode, m=m)
                same &= bool(np.array_equal(a["_nav"], b["_nav"]))
                same &= all(a[k] == b[k] for k in a if isinstance(a[k], (int, float, str)))
    checks["fork_option_off_identity_synthetic"] = bool(same)
    # (b) the masks: the first/last 10 sessions of January/December per year
    per_year_dec = {}
    per_year_jan = {}
    for i, d in enumerate(days):
        if env.dec_tail[i]:
            per_year_dec[d[:4]] = per_year_dec.get(d[:4], 0) + 1
        if env.jan_head[i]:
            per_year_jan[d[:4]] = per_year_jan.get(d[:4], 0) + 1
    checks["dec_tail_is_10_per_year"] = bool(per_year_dec and all(v == 10 for v in per_year_dec.values()))
    checks["jan_head_is_10_per_year"] = bool(per_year_jan and all(v == 10 for v in per_year_jan.values()))
    # (c) Y1/Y2: triggers, cap, book, round-trip unit/NAV invariants, probe == booking
    logs = {}
    for arm in ("Y1", "Y2"):
        for mode in ("progressive", "pre_tax"):
            r = fe.fe_sim.simulate(o, c, w, env, 100_000.0, start, n - 1, arm=arm, mode=mode, m=2, trace=True)
            ye = r["_ye_events"]
            ev = ye[ye[:, 0] > 0]
            logs[(arm, mode)] = ev
            ok_trig = bool(np.all(ev[:, 1] < (0.0 if arm == "Y1" else HEADROOM_TRIGGER_FRAC * ev[:, 7])))
            ok_cap = bool(np.all(ev[:, 3] <= NAV_CAP_FRAC * ev[:, 6] / np.maximum(1e-12, _close_at(c, days, start, ye, env)) + 1e-6))
            ok_book = bool(np.all(ev[:, 3] <= ev[:, 8] * (1 + 1e-9)))
            ok_units = bool(np.all(np.abs(ev[:, 9] - ev[:, 8]) <= 1e-9 * np.maximum(1.0, ev[:, 8])))
            booked = float(np.sum(ev[:, 2] - ev[:, 1]))
            reason = float(r["realised_gain_try_by_reason"].get("yearend", 0.0))
            checks[f"{arm}_{mode}_trigger"] = ok_trig
            checks[f"{arm}_{mode}_cap"] = ok_cap
            checks[f"{arm}_{mode}_book"] = ok_book
            checks[f"{arm}_{mode}_round_trip_units"] = ok_units
            checks[f"{arm}_{mode}_probe_matches_booking"] = bool(abs(booked - reason) <= 1e-6 * max(1.0, abs(reason)))
    ev1 = logs[("Y1", "progressive")]
    checks["Y1_fires_with_a_nonempty_book"] = bool(len(ev1) > 0)
    checks["Y1_both_crossing_branches_exercised"] = bool(
        np.any(ev1[:, 5] < ev1[:, 8] - 1e-12) and np.any(ev1[:, 5] >= ev1[:, 8] - 1e-12)
    )
    # (d) Y3: the state test, the window and the months of the holds
    r3 = fe.fe_sim.simulate(o, c, w, env, 100_000.0, start, n - 1, arm="Y3", mode="progressive", m=2, trace=True)
    re_ = r3["_re_events"]
    holds = re_[re_[:, 0] > 0]
    checks["Y3_state_test_holds_only_when_dearer"] = bool(np.all(holds[:, 1] > holds[:, 2]))
    months = {days[start + int(k)][5:7] for k in np.nonzero(re_[:, 0] > 0)[0]}
    checks["Y3_window_covers_dec_and_jan"] = bool(months <= {"12", "01"})
    checks["Y3_holds_present"] = bool(len(holds) > 0)
    # (e) finiteness and after-tax <= pre-tax
    ok_fin = True
    for arm in ARMS:
        rp = fe.fe_sim.simulate(o, c, w, env, 100_000.0, start, n - 1, arm=arm, mode="pre_tax", m=2)
        rq = fe.fe_sim.simulate(o, c, w, env, 100_000.0, start, n - 1, arm=arm, mode="progressive", m=2)
        ok_fin &= bool(np.isfinite(rp["_nav"]).all() and np.isfinite(rq["_nav"]).all())
        ok_fin &= bool(rq["cagr_usd_liquidated"] <= rp["cagr_usd_liquidated"] + 1e-12)
    checks["finite_and_after_tax_le_pre_tax"] = bool(ok_fin)
    return {"checks": checks, "n_checks": len(checks), "n_failed": int(sum(1 for v in checks.values() if not v))}


def _close_at(c, days, start, ye, env):
    """The decision close of each traced session (used by the smoke cap check)."""
    rows = ye[ye[:, 0] > 0]
    if rows.shape[0] == 0:
        return np.zeros(0)
    idx = [k for k in range(ye.shape[0]) if ye[k, 0] > 0]
    return np.array([c[start + k] for k in idx])


def _walk(prefix, mine, theirs, out, tol=TOL):
    if isinstance(theirs, dict):
        md = {str(k): v for k, v in mine.items()} if isinstance(mine, dict) else {}
        for k, v in theirs.items():
            if str(k) not in md:
                out.append((f"{prefix}.{k}", None, v, True))
            else:
                _walk(f"{prefix}.{k}", md[str(k)], v, out, tol)
    elif isinstance(theirs, (list, tuple)):
        if not isinstance(mine, (list, tuple)) or len(mine) != len(theirs):
            out.append((prefix, mine, theirs, True))
        else:
            for k, v in enumerate(theirs):
                _walk(f"{prefix}[{k}]", mine[k], v, out, tol)
    elif isinstance(theirs, bool) or theirs is None or isinstance(theirs, str):
        out.append((prefix, mine, theirs, mine != theirs))
    else:
        try:
            d = abs(float(mine) - float(theirs))
        except (TypeError, ValueError):
            out.append((prefix, mine, theirs, True))
            return
        out.append((prefix, d, d, d > tol))


def _compare(name, mine, theirs, results, tol=TOL):
    out = []
    _walk(name, mine, theirs, out, tol)
    bad = [o for o in out if o[3] is True]
    maxd = max([o[1] for o in out if isinstance(o[1], float)] or [0.0])
    worst = sorted([o for o in out if isinstance(o[1], float)], key=lambda o: -o[1])[:5]
    results[name] = {
        "cells": len(out),
        "mismatches": len(bad) + 0,
        "max_abs_diff": f"{maxd:.3e}",
        "tolerance": tol,
        "worst": [{"cell": w[0], "diff": f"{w[1]:.3e}"} for w in worst],
        "pass": bool(len(bad) == 0 and maxd <= tol),
        "bad": [{"cell": b[0], "mine": rnd(b[1]), "theirs": rnd(b[2])} for b in bad[:10]],
    }


def parity_subprocess():
    """`python -m research.fast_engine.parity --quick`: the parent engine vs the Python harness."""
    proc = subprocess.run(
        [sys.executable, "-m", "research.fast_engine.parity", "--quick"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return {
        "command": "python -m research.fast_engine.parity --quick",
        "returncode": int(proc.returncode),
        "pass": bool(proc.returncode == 0 and "parity PASS" in proc.stdout),
        # the check rows only: the final "parity PASS in X s" line carries a wall clock, and
        # RESULTS.json has to be byte-identical across runs
        "tab": [ln.strip() for ln in proc.stdout.splitlines() if "->" in ln and "parity PASS" not in ln],
        "result": "PASS" if "parity PASS" in proc.stdout else "FAIL",
    }


def harness(deep=True, quiet=False):
    """The protocol's harness: parity --quick, the fork's option-off identity, the K anchors."""
    out = {"parity_subprocess": parity_subprocess()}
    days, o, c, _we, w_r2, _e2 = main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    S = fe.fe_config.START
    env = fe.fe_sim.RunEnv(days, fx, ufe, S, T)
    env_p = psim.RunEnv(days, fx, ufe, S, T)
    results = {}
    # 1. option-off bit identity against the parent engine, every mode / m / parent arm
    nav_ok, scalar_ok, cells = True, True, 0
    for arm in ("C0", "A1u", "H2", "K"):
        for mode in ("pre_tax", "progressive", "flat"):
            for m in (1, 2, 4):
                a = psim.simulate(o, c, w_r2, env_p, 100_000.0, S, T, arm=arm, mode=mode, m=m)
                b = fe.fe_sim.simulate(o, c, w_r2, env, 100_000.0, S, T, arm=arm, mode=mode, m=m)
                nav_ok &= bool(np.array_equal(a["_nav"], b["_nav"]))
                for k in a:
                    if k in ("_nav", "_dec"):
                        continue
                    scalar_ok &= bool(a[k] == b[k]) if isinstance(a[k], (int, float, str)) else True
                    cells += 1
    out["fork_option_off_identity"] = {
        "arms": ["C0", "A1u", "H2", "K"],
        "modes": ["pre_tax", "progressive", "flat"],
        "m": [1, 2, 4],
        "nav_paths_bit_identical": bool(nav_ok),
        "shared_scalars_identical": bool(scalar_ok),
        "cells": cells,
        "extra_keys_in_fork": sorted(set(fe.fe_sim.simulate(o, c, w_r2, env, 100_000.0, S, T, arm="K", mode="progressive").keys()) - set(psim.simulate(o, c, w_r2, env_p, 100_000.0, S, T, arm="K", mode="progressive").keys())),
        "pass": bool(nav_ok and scalar_ok),
    }
    if not deep:
        out["pass"] = bool(out["parity_subprocess"]["pass"] and out["fork_option_off_identity"]["pass"])
        return out
    ev = json.loads(EVIDENCE.read_text())
    arms4 = ("C0", "A1u", "H2", "K")
    # 2. published pre-tax cells (C0 and K are the two the study reports; all four are run)
    pre, pre_usd = fe.fe_study.pre_tax_main(arms4)
    for mdi in ("base", "calibrated"):
        for m in ("m1", "m2", "m4"):
            for arm in arms4:
                _compare(f"published.pre_tax_main.{mdi}.{m}.{arm}", pre[mdi][m][arm], ev["pre_tax_main"][mdi][m][arm], results)
        for arm in arms4:
            _compare(f"published.pre_tax_main_r_usd.{mdi}.{arm}", pre_usd[mdi][arm], ev["pre_tax_main_r_usd"][mdi][arm], results)
    # 3. published after-tax windows
    win = fe.fe_study.after_tax_windows(("10k", "30k", "100k", "300k", "1M"), arms4)
    for cl in ("10k", "30k", "100k", "300k", "1M"):
        for wn in WINDOW_KEYS:
            for arm in arms4:
                _compare(f"published.after_tax_windows.{cl}.{wn}.{arm}", win[cl][wn][arm], ev["after_tax_windows"][cl][wn][arm], results)
    # 4. cost stress at m = 4
    cost = fe.fe_study.cost_stress(arms4)
    for wn in WINDOW_KEYS:
        for arm in arms4:
            _compare(f"published.cost_stress_m4.{wn}.{arm}", cost[wn][arm], ev["cost_stress_m4"][wn][arm], results)
    # 5. the six OOS windows
    oos = fe.fe_study.oos_stages(arms4)
    for wn in fe.fe_config.OOS_WINDOWS:
        for arm in arms4:
            _compare(f"published.oos.{wn}.{arm}", oos[wn][arm], ev["oos"][wn][arm], results)
    # 6. the clean bootstrap, every published cell (arms/deltas/baselines exactly as published)
    b = fe.fe_boot.boot(
        seed=fe.fe_config.SEED,
        block=fe.fe_config.BLOCK,
        arms=arms4,
        n_pre=fe.fe_config.PRE_BOOT_PATHS,
        n_tax=fe.fe_config.TAX_BOOT_PATHS,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        anchor_e2=True,
        deltas=fe.fe_config.CANDIDATES,
        baselines=("C0", "A1u"),
        label="harness",
        quiet=True,
    )
    pub = ev["bootstrap_clean_seed20261011"]
    for k in ("n_paths_pre", "n_paths_tax", "seed", "block", "arms", "calendar_start", "calendar_end"):
        _compare(f"published.bootstrap.{k}", b.get(k), pub[k], results)
    for mdi in b["pre"]:
        for arm in arms4:
            _compare(f"published.bootstrap.pre.{mdi}.{arm}", b["pre"][mdi][arm], pub["pre"][mdi][arm], results)
    for cl in b["tax"]:
        for mode in b["tax"][cl]:
            for k, v in pub["tax"][cl][mode].items():
                _compare(f"published.bootstrap.tax.{cl}.{mode}.{k}", b["tax"][cl][mode][k], v, results)
    for mode in b["anchor_e2_100k"]:
        _compare(f"published.bootstrap.anchor_e2_100k.{mode}", b["anchor_e2_100k"][mode], pub["anchor_e2_100k"][mode], results)
    out["published_cells"] = results
    # 7. the literal brief anchors (the brief's own 0.01 pp tolerance; the published-cell checks
    #    above are the stricter ones)
    days0, o0, c0, w_e2_0, _wr0, _e2v0 = main_inputs(0.0)
    e2_base = fe.fe_sim.simulate(o0, c0, w_e2_0, env, 100_000.0, S, T, arm="E2", mode="pre_tax", m=2)
    days1, o1, c1, w_e2_1, _wr1, _e2v1 = main_inputs(fe.fe_config.FIN["calibrated"])
    e2_cal = fe.fe_sim.simulate(o1, c1, w_e2_1, env, 100_000.0, S, T, arm="E2", mode="pre_tax", m=2)
    brief_tol = 1e-4
    lit = {
        "K_pre_tax_cagr_base": (pre["base"]["m2"]["K"]["cagr"], 0.157649),
        "K_pre_tax_cagr_calibrated": (pre["calibrated"]["m2"]["K"]["cagr"], 0.151785),
        "K_pre_tax_max_dd_base": (pre["base"]["m2"]["K"]["max_dd"], 0.466698),
        "K_pre_tax_max_dd_calibrated": (pre["calibrated"]["m2"]["K"]["max_dd"], 0.467948),
        "K_pre_tax_turnover_base_m2": (pre["base"]["m2"]["K"]["turnover_annual"], 5.456136),
        "K_after_tax_100k_2000": (win["100k"]["2000_03_to_2026_09"]["K"]["after_tax_cagr"], 0.107533),
        "K_after_tax_100k_2010": (win["100k"]["2010_01_to_end"]["K"]["after_tax_cagr"], 0.152354),
        "K_after_tax_100k_2017": (win["100k"]["2017_01_to_end"]["K"]["after_tax_cagr"], 0.239720),
        "C0_after_tax_100k_2000": (win["100k"]["2000_03_to_2026_09"]["C0"]["after_tax_cagr"], 0.101600),
        "C0_after_tax_100k_2010": (win["100k"]["2010_01_to_end"]["C0"]["after_tax_cagr"], 0.147059),
        "C0_after_tax_100k_2017": (win["100k"]["2017_01_to_end"]["C0"]["after_tax_cagr"], 0.228861),
        "R2_pre_tax_cagr_base_m2": (pre["base"]["m2"]["C0"]["cagr"], 0.154766),
        "R2_pre_tax_cagr_calibrated_m2": (pre["calibrated"]["m2"]["C0"]["cagr"], 0.148961),
        "E2_pre_tax_cagr_base_m2": (e2_base["cagr_usd_unliquidated"], 0.146749),
        "E2_pre_tax_cagr_calibrated_m2": (e2_cal["cagr_usd_unliquidated"], 0.141000),
        "E2_pre_tax_max_dd_base_m2": (e2_base["max_dd_nav_usd"], 0.495643),
    }
    lit_out = {}
    bad = 0
    for k, (mine, want) in lit.items():
        d = abs(float(mine) - want)
        ok = d <= brief_tol
        bad += 0 if ok else 1
        lit_out[k] = {"mine": float(mine), "expected": want, "abs_diff": f"{d:.3e}", "tolerance": brief_tol, "pass": ok}
    out["literal_anchors"] = {"cells": lit_out, "mismatches": bad, "pass": bad == 0}
    out["pass"] = bool(
        out["parity_subprocess"]["pass"]
        and out["fork_option_off_identity"]["pass"]
        and all(v["pass"] for v in results.values())
        and out["literal_anchors"]["pass"]
    )
    out["summary"] = {
        "published_blocks": len(results),
        "published_cells": int(sum(v["cells"] for v in results.values())),
        "published_mismatches": int(sum(v["mismatches"] for v in results.values())),
        "worst_abs_diff": f"{max([float(v['max_abs_diff']) for v in results.values()] or [0.0]):.3e}",
    }
    return out


# ----------------------------------------------------------------------------- run
def run_all(quiet=False):
    t0 = time.time()
    res = {"campaign": "tax_budget_v2", "status": "run"}
    res["params"] = {
        "arms": list(ARMS),
        "gated": list(GATED),
        "report_only": list(REPORT_ONLY),
        "control": CONTROL,
        "reference": REFERENCE,
        "stages": ["pre", "AT", "cost", "B12", "OOS", "OOSB", "starts", "pool", "log"],
        "engine": {
            "base": "research/fast_engine (tag archive/research/fast-engine-v1-2026-10)",
            "fork": "research/tax_budget_v2/fe (yearend, realloc, fe_oos)",
        },
        "gate_g5": f"non-inferiority: pre-tax bootstrap median delta vs K >= {G5_MARGIN_PP} pp",
        "m": fe.fe_config.M,
        "m_stress": fe.fe_config.M_STRESS,
        "capitals": {"gate": "100k", "report": "1M"},
        "seed": fe.fe_config.SEED,
        "block": fe.fe_config.BLOCK,
        "pre_boot_paths": fe.fe_config.PRE_BOOT_PATHS,
        "tax_boot_paths": fe.fe_config.TAX_BOOT_PATHS,
        "oos_paths_per_market": fe.fe_oos.OOS_PATHS_PER_MARKET,
        "nav_cap_frac": NAV_CAP_FRAC,
        "headroom_trigger_frac": HEADROOM_TRIGGER_FRAC,
    }
    if not quiet:
        print("harness...", flush=True)
    res["harness"] = harness(deep=True, quiet=True)
    if not quiet:
        print("  parity", res["harness"]["parity_subprocess"]["pass"], "identity",
              res["harness"]["fork_option_off_identity"]["pass"], "published",
              res["harness"]["summary"]["published_mismatches"], "mismatch of",
              res["harness"]["summary"]["published_cells"], flush=True)
        print("pre-tax main...", flush=True)
    pre, pre_usd = pre_tax_main()
    res["pre_tax_main"], res["pre_tax_main_r_usd"] = pre, pre_usd
    if not quiet:
        print("after-tax windows...", flush=True)
    res["after_tax_windows"] = after_tax_windows()
    if not quiet:
        print("cost stress...", flush=True)
    res["cost_stress_m4"] = cost_stress()
    if not quiet:
        print("bootstrap B12 (5,000 / 1,000 paths)...", flush=True)
    b = boot_clean(quiet=True)
    bd = boot_delta_vs_K(b)
    # the raw per-path arrays are used for the delta-vs-K cells and then dropped (tuple keys and
    # ~40k floats per capital/mode); `paths` is restored from the seed when needed
    res["bootstrap_clean_seed20261011"] = {k: v for k, v in b.items() if k != "paths"}
    res["bootstrap_delta_vs_K"] = bd
    if not quiet:
        print("OOS windows...", flush=True)
    oos = oos_stages()
    res["oos"] = oos
    if not quiet:
        print("OOS pooled bootstrap (G8)...", flush=True)
    oosb = oos_bootstrap()
    res["oos_bootstrap"] = oosb
    if not quiet:
        print("rolling starts...", flush=True)
    res["rolling_starts"] = rolling_starts()
    if not quiet:
        print("pool diagnostic...", flush=True)
    res["pool"] = pool_diagnostic()
    if not quiet:
        print("event log...", flush=True)
    res["event_log"] = event_log()
    if not quiet:
        print("gates...", flush=True)
    gates = {}
    for arm in ARMS:
        if arm in (CONTROL, REFERENCE):
            continue
        gates[arm] = {
            "pre": pre_gates(pre, bd, oos, oosb, arm),
            "tax": tax_gates(res["after_tax_windows"], bd, arm),
            "boot_delta": {
                "pre": bd["pre"],
                "tax": bd["tax"],
            },
        }
    res["gates"] = gates
    res["selection"] = select(gates)
    res["wall_clock_s_unwritten"] = float(time.time() - t0)  # timing is SUMMARY.txt only
    return res


def _fmt_pp(x, nd=2):
    return f"{x:+.{nd}f}"


def write_summary(res, path, wall_s):
    def g(a, *ks):
        cur = res
        for k in ks:
            cur = cur[k]
        return cur

    lines = []
    lines.append("tax_budget_v2 -- annual tax-budget execution on top of K (I3 of idea_survey_v4)")
    lines.append(f"engine: {res['params']['engine']['fork']} on {res['params']['engine']['base']}")
    lines.append(f"wall clock of --run: {wall_s:.1f} s (SUMMARY.txt only: RESULTS.json is byte-identical across runs)")
    lines.append("")
    lines.append("HARNESS: parity --quick " + ("PASS" if res["harness"]["parity_subprocess"]["pass"] else "FAIL")
                 + "; fork-vs-parent option-off identity "
                 + ("PASS" if res["harness"]["fork_option_off_identity"]["pass"] else "FAIL")
                 + f" ({res['harness']['fork_option_off_identity']['cells']} cells, nav bit-identical "
                 + str(res["harness"]["fork_option_off_identity"]["nav_paths_bit_identical"]) + ")"
                 + f"; published cells {res['harness']['summary']['published_cells']} with "
                 + f"{res['harness']['summary']['published_mismatches']} mismatches (worst "
                 + f"{res['harness']['summary']['worst_abs_diff']})"
                 + f"; literal anchors {res['harness']['literal_anchors']['mismatches']} mismatches")
    lines.append("")
    lines.append("PRE-TAX (main window, m = 2; delta vs K in pp)")
    lines.append(f"{'arm':<5}{'CAGR base':>11}{'d':>8}{'CAGR cal':>11}{'d':>8}{'maxDD base':>12}{'d':>8}{'Calmar':>9}{'2017+':>9}{'turn/yr':>9}")
    for arm in ARMS:
        base = g(res, "pre_tax_main", "base", "m2", arm)
        cal = g(res, "pre_tax_main", "calibrated", "m2", arm)
        kb = g(res, "pre_tax_main", "base", "m2", CONTROL)
        kc = g(res, "pre_tax_main", "calibrated", "m2", CONTROL)
        lines.append(
            f"{arm:<5}{base['cagr']*100:>11.4f}{(base['cagr']-kb['cagr'])*100:>+8.3f}"
            f"{cal['cagr']*100:>11.4f}{(cal['cagr']-kc['cagr'])*100:>+8.3f}"
            f"{base['max_dd']*100:>12.3f}{(base['max_dd']-kb['max_dd'])*100:>+8.3f}"
            f"{base['calmar']:>9.4f}{base['2017_end']['cagr']*100:>9.2f}{base['turnover_annual']:>9.4f}"
        )
    lines.append("")
    lines.append("AFTER TAX at $100k (progressive, liquidated, m = 2; delta vs K in pp)")
    lines.append(f"{'arm':<5}" + "".join(f"{w[:11]:>13}" for w in WINDOW_KEYS))
    for arm in ARMS:
        row = f"{arm:<5}"
        for wn in WINDOW_KEYS:
            a = g(res, "after_tax_windows", "100k", wn, arm)["after_tax_cagr"]
            k = g(res, "after_tax_windows", "100k", wn, CONTROL)["after_tax_cagr"]
            row += f"{a*100:>8.4f}{(a-k)*100:>+6.2f}   "
        lines.append(row)
    lines.append("")
    lines.append("BOOTSTRAP (clean, seed 20261011, block 20; delta vs K in pp)")
    lines.append(f"{'arm':<5}{'pre base':>11}{'pre cal':>10}{'B1 med':>9}{'B1 >0':>8}{'B2 med':>9}{'B2 >0':>8}{'OOSB med':>10}")
    for arm in ARMS:
        if arm == CONTROL:
            lines.append(f"{arm:<5}{'--':>11}{'--':>10}{'--':>9}{'--':>8}{'--':>9}{'--':>8}{'--':>10}")
            continue
        pb = g(res, "bootstrap_delta_vs_K", "pre", "base", arm)
        pc = g(res, "bootstrap_delta_vs_K", "pre", "calibrated", arm)
        b1 = g(res, "bootstrap_delta_vs_K", "tax", "100k", "progressive", arm)
        b2 = g(res, "bootstrap_delta_vs_K", "tax", "100k", "flat", arm)
        ob = g(res, "oos_bootstrap", "pooled", arm)
        lines.append(
            f"{arm:<5}{pb['median_pp']:>+11.3f}{pc['median_pp']:>+10.3f}{b1['median_pp']:>+9.3f}"
            f"{b1['share_gt_0']*100:>7.1f}%{b2['median_pp']:>+9.3f}{b2['share_gt_0']*100:>7.1f}%{ob['median_pp']:>+10.3f}"
        )
    lines.append("")
    lines.append("GATES")
    hdr = f"{'arm':<5}" + "".join(f"{k:>7}" for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2"))
    lines.append(hdr)
    for arm in (*GATED, *REPORT_ONLY):
        rec = g(res, "gates", arm)
        cells = []
        for k in ("G1", "G2", "G3", "G5", "G6"):
            cells.append((k, "PASS" if all(rec["pre"][k][m]["pass"] for m in ("base", "calibrated")) else "FAIL"))
        for k in ("G4", "G7", "G8", "G9"):
            cells.append((k, "PASS" if rec["pre"][k]["pass"] else "FAIL"))
        for k in ("A1", "A2"):
            cells.append((k, "PASS" if rec["tax"][k]["pass"] else "FAIL"))
        cells.sort(key=lambda kv: ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2").index(kv[0]))
        lines.append(f"{arm:<5}" + "".join(f"{c:>7}" for _k, c in cells)
                     + ("   (report-only)" if arm in REPORT_ONLY else ""))
    lines.append("")
    sel = res["selection"]
    lines.append(f"SELECTION: {sel['selected'] if sel['selected'] else 'no arm selected'} -- {sel['reason'] if 'reason' in sel else sel['rank_key']}")
    for arm in (*GATED, *REPORT_ONLY):
        rec = g(res, "gates", arm)
        failed = []
        for k in ("G1", "G2", "G3", "G5", "G6"):
            if not all(rec["pre"][k][m]["pass"] for m in ("base", "calibrated")):
                failed.append(k)
        for k in ("G4", "G7", "G8", "G9"):
            if not rec["pre"][k]["pass"]:
                failed.append(k)
        for k in ("A1", "A2"):
            if not rec["tax"][k]["pass"]:
                failed.append(k)
        inert = [wn for wn in WINDOW_KEYS if abs(rec["tax"]["A1"][wn]["delta_pp"]) < 5e-7]
        lines.append(f"  {arm}: failed {' '.join(failed) if failed else '(none)'}"
                     + (f"; after-tax window(s) bit-identical to K (delta exactly 0): {', '.join(inert)}" if inert else ""))
    lines.append("")
    lines.append("POOL (control K, $100k progressive, main window): "
                 f"{g(res,'pool','n_loss_years')} loss years of {g(res,'pool','n_years')}, "
                 f"sum |min(0,ytd)| = {g(res,'pool','pool_sum_try'):,.0f} TRY "
                 f"(~${g(res,'pool','pool_sum_usd'):,.0f}); Y1 trigger available in "
                 f"{g(res,'pool','n_years_y1_trigger')} years, Y2 in {g(res,'pool','n_years_y2_trigger')}, "
                 f"25% cap binds in {g(res,'pool','n_years_cap_binds')}")
    for arm in ("Y1", "Y2", "Y4"):
        e = g(res, "event_log", "progressive", arm)
        lines.append(f"LOG {arm}: {e['n_round_trips']} round trips "
                     f"(${e['round_trip_notional_usd']:,.0f} notional, {e['n_cap_binding']} cap-bound, "
                     f"{e['n_crossing_unreachable']} whole-book fallback, "
                     f"{e['booked_gain_try']:,.0f} TRY booked)")
    e = g(res, "event_log", "progressive", "Y3")
    lines.append(f"LOG Y3: {e['n_realloc_holds']} realloc holds, months {e['realloc_holds_by_month']}")
    lines.append("")
    lines.append(f"RESULTS.json written; wall clock {wall_s:.1f} s")
    Path(path).write_text("\n".join(lines) + "\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="tax_budget_v2 (see PREREGISTRATION.md)")
    ap.add_argument("--smoke", action="store_true", help="synthetic checks only, no market data")
    ap.add_argument("--harness", action="store_true", help="parity + anchors on real data")
    ap.add_argument("--run", action="store_true", help="harness + every stage + gates; writes RESULTS.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        t0 = time.time()
        out = smoke()
        print(json.dumps({"checks": out["checks"], "n_checks": out["n_checks"], "n_failed": out["n_failed"],
                          "wall_s": round(time.time() - t0, 1)}, indent=1))
        print("smoke " + ("PASS" if out["n_failed"] == 0 else "FAIL"))
        return 0 if out["n_failed"] == 0 else 1
    if args.harness:
        t0 = time.time()
        out = harness(deep=True, quiet=args.quiet)
        print(json.dumps(rnd({k: v for k, v in out.items() if k != "published_cells"}), indent=1))
        print(json.dumps({k: {"cells": v["cells"], "mismatches": v["mismatches"], "max_abs_diff": v["max_abs_diff"],
                              "pass": v["pass"]} for k, v in out["published_cells"].items()}, indent=1))
        print(f"published blocks {out['summary']['published_blocks']}, cells {out['summary']['published_cells']}, "
              f"mismatches {out['summary']['published_mismatches']}, worst diff {out['summary']['worst_abs_diff']}")
        print(f"harness {'PASS' if out['pass'] else 'FAIL'} in {time.time() - t0:.1f} s")
        return 0 if out["pass"] else 1
    if args.run:
        t0 = time.time()
        res = run_all(quiet=args.quiet)
        wall = time.time() - t0
        payload = rnd({k: v for k, v in res.items() if k != "wall_clock_s_unwritten"})
        (OUTDIR / "RESULTS.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        text = write_summary(rnd(res), OUTDIR / "SUMMARY.txt", wall)
        print(text)
        ok = res["harness"]["pass"]
        print(f"--run {'PASS' if ok else 'FAIL'} (harness) in {wall:.1f} s")
        return 0 if ok else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
