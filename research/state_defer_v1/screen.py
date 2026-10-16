"""state_defer_v1 -- state-keyed realization deferral with a hard drift cap (idea_survey_v5 I3).

Control = production K (`equity_leveraged_trend_v1` 1.2.0 = E2 + R2 early re-entry + A1u + H2);
R2 is reported as a reference row. The four candidates replace K's calendar-keyed H2 deferral with
a deferral keyed on the *tax state* of the fill, and keep A1u and everything else of K:

  D1  a pending discretionary downsize that would fill in the last 10 sessions of any calendar
      month is postponed while the FIFO walk of that sale nets a positive *indexed-TRY* gain;
      one postponement episode lasts at most 21 sessions.
  D2  D1 plus a hard drift cap: never postpone while `held - target > 10 pp`.
  D3  no calendar at all: postpone while the fill year's marginal bracket rate times the sale's
      indexed-TRY gain exceeds 0.05 % of NAV; at most 21 sessions; the same 10 pp drift cap.
  D4  H2 with a 20-session December window and the same 10 pp drift cap.

Every arm shares production R2's target series bit for bit; only the fill decision changes, and an
overlay can only *suppress* a fill, never create one. See PREREGISTRATION.md for the fixed
parameters, the reading of the survey's arm text and the gates.

    python -m research.state_defer_v1.screen --smoke     # synthetic prices only
    python -m research.state_defer_v1.screen --parity    # the option-off parity cell
    python -m research.state_defer_v1.screen --harness   # anchors + parity + invariants
    python -m research.state_defer_v1.screen --run       # writes RESULTS.json / SUMMARY.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from research.state_defer_v1.engine import fe_boot, fe_data, fe_sim, fe_study
from research.state_defer_v1.engine.fe_config import (
    BLOCK,
    CANDIDATES,
    FIN,
    GATE_CAPITAL,
    OOS_WINDOWS,
    SEED,
    START,
    TAX_CAPITALS,
    TAX_WINDOWS,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ANCHORS = ROOT / "research/band_h2_combo_v1/RESULTS.json"
H2_ANCHORS = ROOT / "research/band_h2_combo_v1/H2_ANCHORS.json"

STUDY = ("K", "D1", "D2", "D3", "D4")
REPORT = STUDY + ("C0",)  # + R2 as the reference row
CONTROL = "K"
TOL = 1e-4  # 0.01 pp, the protocol's anchor tolerance

# the K / R2 anchors the protocol fixes (band_h2_combo_v1, base financing, m = 2, main window)
ANCHOR_K = {"cagr_base": 15.7649, "cagr_cal": 15.1785, "dd_base": 46.6698, "dd_cal": 46.7948}
ANCHOR_R2 = {"cagr_base": 15.4766, "cagr_cal": 14.8961, "dd_base": 46.7584, "dd_cal": 46.8797}
ANCHOR_TAX_K = {"2000_03_to_2026_09": 10.753, "2010_01_to_end": 15.235, "2017_01_to_end": 23.972}
ANCHOR_TAX_R2 = {"2000_03_to_2026_09": 10.160, "2010_01_to_end": 14.706, "2017_01_to_end": 22.886}


# ----------------------------------------------------------------------------- helpers
def rnd(x, nd=6):
    """`trend_robustness_v2.rnd`: every float in RESULTS.json is rounded, so runs are comparable."""
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, nd) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return rnd(float(x), nd) if isinstance(x, np.floating) else int(x)
    return x


def numeric_leaves(obj, prefix=""):
    """(path, float) for every numeric leaf, for the option-off parity cell."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from numeric_leaves(v, f"{prefix}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from numeric_leaves(v, f"{prefix}[{i}]")
    elif isinstance(obj, np.ndarray):
        yield prefix, float(np.max(np.abs(obj.astype(float)))) if obj.size else 0.0
    elif isinstance(obj, (int, float, np.integer, np.floating)):
        yield prefix, float(obj)


def main_inputs():
    return fe_data.main_inputs(0.0)


def env_main(i0=START, i1=None):
    days, o, c, w_e2, w_r2, e2 = main_inputs()
    fx, ufe = fe_study.try_market(days)
    return days, o, c, w_r2, fe_sim.RunEnv(days, fx, ufe, i0, len(days) - 1 if i1 is None else i1)


# ----------------------------------------------------------------------------- smoke (synthetic)
def synthetic_bars(n, seed):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.013, n)
    price = 100.0 * np.cumprod(1 + r)
    o = np.empty(n)
    o[0] = price[0]
    o[1:] = price[:-1] * (1 + rng.normal(0, 0.004, n - 1))
    return o, price


def synthetic_days(n, y0=2005):
    from datetime import date, timedelta

    out, d = [], date(y0, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def smoke():
    """The whole rule path on synthetic random-walk prices; no market data touched."""
    n = 1800
    o, c = synthetic_bars(n, 7)
    days = synthetic_days(n)
    fx = np.linspace(1.0, 40.0, n)
    ufe = {f"{y}-{m:02d}": float(1000 + 12 * y + 5 * m) for y in range(2000, 2030) for m in range(1, 13)}
    i0, i1 = 260, n - 1
    w = 0.98 * (0.5 + 0.5 * np.sin(np.arange(n) / 13.0))
    w[:250] = 0.0
    env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
    rows = {}
    for arm in fe_sim.ARMS:
        for mode in ("pre_tax", "progressive", "flat"):
            r = fe_sim.simulate(o, c, w, env, 100_000.0, i0, i1, arm=arm, mode=mode, m=2, stats=True)
            assert np.isfinite(r["_nav"]).all(), f"{arm} {mode}: non-finite NAV"
            assert r["sells"] >= 0 and r["deferred"] >= 0
            if r["deferral_episodes"]:
                assert r["_dfr"] is not None and len(r["_dfr"]) == r["deferred"], f"{arm}: event count"
                assert r["_dfr"][:, 0].max() <= max(r["max_hold_above_target"], 1e-12) + 1e-12
            rows[f"{arm}/{mode}"] = {
                "sells": r["sells"],
                "deferred": r["deferred"],
                "blocked_band": r["blocked_band"],
                "deferral_episodes": r["deferral_episodes"],
                "max_episode_sessions": r["max_episode_sessions"],
            }
    print("smoke: all arms finite; deferral counters consistent")
    for k, v in rows.items():
        print(f"  {k:16s} {v}")
    return rows


# ----------------------------------------------------------------------------- parity (option off)
def parity():
    """The option-off parity cell: this fork with every deferral knob off == the shared engine.

    Compared on the *returned dicts* (every numeric leaf) and on the NAV path bit for bit, for the
    engine's four arms, all three modes, two capitals and two cost levels, on the real main window.
    """
    from research.fast_engine import fe_sim as eng

    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    _, _, _, _, env_eng = env_main()
    checks, worst, cells = {}, 0.0, 0
    for arm in ("C0", "A1u", "H2", "K"):
        for mode in ("pre_tax", "progressive", "flat"):
            for cap in (TAX_CAPITALS[GATE_CAPITAL], TAX_CAPITALS["1M"]):
                for m in (1, 2):
                    a = fe_sim.simulate(o, c, w_r2, env, cap, START, T, arm=arm, mode=mode, m=m, trace=True, stats=True)
                    b = eng.simulate(o, c, w_r2, env_eng, cap, START, T, arm=arm, mode=mode, m=m, trace=True)
                    d = np.max(np.abs(a["_nav"] - b["_nav"]))
                    dd = float(np.max(np.abs(a["_dec"].astype(float) - b["_dec"].astype(float))))
                    la = dict(numeric_leaves(a))
                    lb = dict(numeric_leaves(b))
                    diffs = {k: abs(la[k] - lb[k]) for k in lb}
                    mx = max([dd, d] + list(diffs.values()))
                    worst = max(worst, mx)
                    cells += 1 + len(diffs)
                    checks[f"{arm}/{mode}/{cap:g}/m{m}"] = {"nav": d, "dec": dd, "dict": max(diffs.values())}
    print(f"parity: {cells} cells, max|diff| {worst:.3e}  -> {'PASS' if worst == 0.0 else 'FAIL'}")
    return {"pass": bool(worst == 0.0), "cells": cells, "max_abs_diff": worst, "checks": checks}


# ----------------------------------------------------------------------------- harness
def shared_parity():
    """`python -m research.fast_engine.parity --quick` must PASS (the mandatory engine check)."""
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, "-m", "research.fast_engine.parity", "--quick"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = p.stdout.strip().splitlines()
    ok = p.returncode == 0 and "parity PASS" in p.stdout
    print(f"shared engine parity --quick: {'PASS' if ok else 'FAIL'} ({time.time() - t0:.1f}s)")
    if not ok:
        print("\n".join(out[-15:]))
        print(p.stderr[-2000:])
    # only the deterministic check lines are stored (the wall-clock lines are not reproducible)
    keep = [line.strip() for line in out if "-> PASS" in line or "-> FAIL" in line]
    return {"pass": bool(ok), "checks": keep}


def harness():
    """Control anchors + the option-off parity cell + the mechanism invariants."""
    checks = {}

    def chk(name, got, want, tol=TOL, pp=True):
        """TOL is 0.01 pp expressed as a fraction (1e-4); `diff_pp` is reported in pp."""
        scale = 100.0 if pp else 1.0
        ok = abs(got - want) <= tol * scale / 100.0
        checks[name] = {"got": got, "want": want, "diff_pp": (got - want) * scale, "pass": bool(ok)}
        return ok

    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    a = json.loads(ANCHORS.read_text())
    h2 = json.loads(H2_ANCHORS.read_text())

    # ---- K and R2 anchors, read out of the restored band_h2_combo_v1 RESULTS.json
    ref = a["pre_tax_main"]
    for arm, anchor in (("K", ANCHOR_K), ("C0", ANCHOR_R2)):
        for md in ("base", "calibrated"):
            r = ref[md]["m2"][arm]
            chk(f"{arm}_{md}_cagr", r["cagr"], anchor["cagr_base" if md == "base" else "cagr_cal"] / 100)
            chk(f"{arm}_{md}_maxdd", r["max_dd"], anchor["dd_base" if md == "base" else "dd_cal"] / 100)
    # ---- and my fork must reproduce that same published file, not just the protocol's summary
    for md, spread in FIN.items():
        days_m, o_m, c_m, _we, w_m, _e2 = fe_data.main_inputs(spread)
        fx_m, ufe_m = fe_study.try_market(days_m)
        T_m = len(days_m) - 1
        env_m = fe_sim.RunEnv(days_m, fx_m, ufe_m, START, T_m)
        for arm in ("K", "C0"):
            for m in ("m1", "m2", "m4"):
                rr = fe_sim.simulate(
                    o_m, c_m, w_m, env_m, TAX_CAPITALS[GATE_CAPITAL], START, T_m, arm=arm,
                    mode="pre_tax", m=int(m[1]),
                )
                st = fe_study.pre_stats(days_m, START, T_m, rr)
                chk(f"fork_{arm}_{md}_{m}_cagr", st["cagr"], ref[md][m][arm]["cagr"])
                chk(f"fork_{arm}_{md}_{m}_maxdd", st["max_dd"], ref[md][m][arm]["max_dd"])
                chk(f"fork_{arm}_{md}_{m}_turnover", st["turnover_annual"], ref[md][m][arm]["turnover_annual"])
                if arm == "K":
                    chk(f"fork_{arm}_{md}_{m}_deferred", float(st["deferred"]),
                        float(ref[md][m][arm]["deferred"]), tol=0.0)
    # ---- after-tax anchors at $100k
    for wn, (d0, d1) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, d0, d1)
        e = fe_sim.RunEnv(days, env.fx, env.ufe, i0, i1)
        for arm, anchor in (("K", ANCHOR_TAX_K), ("C0", ANCHOR_TAX_R2)):
            r = fe_sim.simulate(o, c, w_r2, e, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm=arm, mode="progressive")
            chk(f"tax_{arm}_{wn}", r["cagr_usd_liquidated"], anchor[wn] / 100)
            chk(f"tax_{arm}_{wn}_file", r["cagr_usd_liquidated"], a["after_tax_windows"]["100k"][wn][arm]["after_tax_cagr"])
    # ---- the H2 anchors (the deferral counter must match the parent campaign exactly)
    for wn, v in h2["after_tax_windows_100k"].items():
        d0, d1 = TAX_WINDOWS[wn]
        i0, i1 = fe_study.window_indices(days, d0, d1)
        e = fe_sim.RunEnv(days, env.fx, env.ufe, i0, i1)
        r = fe_sim.simulate(o, c, w_r2, e, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm="H2", mode="progressive")
        checks[f"h2_deferrals_{wn}"] = {"got": int(r["deferred"]), "want": int(v["H2"]["deferrals"]),
                                        "pass": int(r["deferred"]) == int(v["H2"]["deferrals"])}
    r = fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm="H2", mode="pre_tax", m=2)
    checks["h2_deferrals_pre"] = {"got": int(r["deferred"]), "want": 51, "pass": int(r["deferred"]) == 51}

    # ---- the option-off parity cell
    par = parity()
    checks["option_off_parity"] = {"got": par["max_abs_diff"], "want": 0.0, "pass": par["pass"]}

    # ---- mechanism invariants: each must hold on the real main path, and each is falsifiable
    traces = {
        arm: fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm,
                             mode="pre_tax", m=2, trace=True, stats=True)
        for arm in ("C0", "A1u", "H2", "K", "D1", "D2", "D3", "D4")
    }
    dd_, _, _, _, dd_env = env_main()
    inv = {}
    for arm in ("D1", "D2", "D3", "D4"):
        r = traces[arm]
        ev = r["_dfr"]
        sess = ev[:, fe_sim.DF_SESS].astype(np.int64)
        inv[f"{arm}_events_match_counter"] = len(ev) == r["deferred"]
        inv[f"{arm}_episodes_le_events"] = 0 < r["deferral_episodes"] <= r["deferred"]
        inv[f"{arm}_all_events_are_downsizes"] = bool(len(ev) == 0 or ev[:, fe_sim.DF_DRIFT].min() > 0)
        inv[f"{arm}_max_episode_capped"] = bool(
            r["max_episode_sessions"] <= (fe_sim.DFR_MAX_SESS - 1 if arm != "D4" else fe_sim.DEC_WIDE - 1)
        )
        inv[f"{arm}_event_episode_lengths_agree"] = bool(len(ev) == 0 or ev[:, fe_sim.DF_EPLEN].max() == r["max_episode_sessions"])
        if arm in ("D1", "D2"):
            inv[f"{arm}_events_in_month_tail"] = bool(len(ev) == 0 or dd_env.mtail[sess].all())
        if arm == "D4":
            inv[f"{arm}_events_in_december_tail"] = bool(len(ev) == 0 or dd_env.dec_wide[sess].all())
        if arm in ("D2", "D3", "D4"):
            # the drift cap is a decision-time test, so the *episode start* may not exceed it
            starts = ev[ev[:, fe_sim.DF_EPLEN] == 0]
            inv[f"{arm}_episode_starts_within_drift_cap"] = bool(
                len(starts) == 0 or starts[:, fe_sim.DF_DRIFT].max() <= fe_sim.DFR_DRIFT_CAP + 1e-9
            )
        inv[f"{arm}_sells_le_K"] = bool(r["sells"] <= traces["K"]["sells"])
    # K's own December deferrals (sessions where A1u sells and K does not) and D1's overlap
    k_def = int(np.sum((traces["A1u"]["_dec"] == 2) & (traces["K"]["_dec"] != 2)))
    d1_ov = int(np.sum((traces["A1u"]["_dec"] == 2) & (traces["D1"]["_dec"] != 2)))
    inv["K_deferral_sessions"] = k_def
    inv["D1_deferral_sessions_same_test"] = d1_ov
    inv["D1_covers_K_deferrals"] = bool(d1_ov >= k_def)
    checks["mechanism_invariants"] = {
        "pass": bool(all(v for k, v in inv.items() if isinstance(v, bool))), "detail": inv
    }

    # ---- the published H2-minus-R2 January-gap episodes, as a sanity check on the gap table
    gaps = january_gaps(days, traces)
    for yr, want in (("2016", -0.699), ("2019", 0.0), ("2025", -0.318)):
        got = (gaps["H2"][yr]["log_return"] - gaps["C0"][yr]["log_return"]) * 100
        checks[f"h2_r2_gap_{yr}"] = {"got": got, "want": want, "pass": abs(got - want) <= 0.01}

    shared = shared_parity()
    n_bad = sum(1 for v in checks.values() if not v.get("pass", False)) + (0 if shared["pass"] else 1)
    out = {
        "pass": n_bad == 0,
        "n_outside_tolerance": int(n_bad),
        "n_checks": len(checks),
        "checks": rnd(checks),
        "shared_engine_parity": shared,
        "mechanism_invariants": rnd(inv),
        "option_off_parity": {"pass": par["pass"], "cells": par["cells"], "max_abs_diff": par["max_abs_diff"]},
    }
    for k, v in checks.items():
        if not v.get("pass", False):
            print(f"  FAIL {k}: got {v['got']!r} want {v['want']!r}")
    print(f"harness: {len(checks)} checks, {n_bad} outside tolerance -> {'PASS' if out['pass'] else 'FAIL'}")
    return out


def january_gaps(days, traces, i0=START):
    """The published January-gap episode, per tax year `y`: the log return from the **first**
    session of December `y-1` to the **last** session of January `y`.

    That is `tax_harvest_v2`'s convention (reproduced here: H2 minus R2 is -0.699 pp for 2016,
    -0.318 pp for 2025 and exactly 0.000 pp for 2019, when R2 was flat). `nav[k]` is the close of
    session `i0 + k - 1`, so the close of session `i` is `nav[i - i0 + 1]`.
    """
    out = {}
    for arm, r in traces.items():
        nav, rows = r["_nav"], {}
        for y in range(2001, 2027):
            dec = [i for i, d in enumerate(days) if d.startswith(f"{y - 1}-12")]
            jan = [i for i, d in enumerate(days) if d.startswith(f"{y}-01")]
            if not dec or not jan:
                continue
            di, ji = dec[0] - i0 + 1, jan[-1] - i0 + 1
            if 0 <= di < len(nav) and 0 <= ji < len(nav) and nav[di] > 0:
                rows[str(y)] = {"log_return": float(np.log(nav[ji] / nav[di]))}
        out[arm] = rows
    return out


# ----------------------------------------------------------------------------- stages
def stage_pre_tax(arms):
    pre, pre_usd = fe_study.pre_tax_main(arms=arms)
    return {"pre_tax_main": rnd(pre), "pre_tax_main_r_usd": rnd(pre_usd)}


def stage_after_tax(arms):
    return {"after_tax_windows": rnd(fe_study.after_tax_windows(("10k", "100k", "1M"), arms=arms))}


def stage_cost(arms):
    return {"cost_stress_m4": rnd(fe_study.cost_stress(arms=arms))}


def stage_oos(arms):
    return {"oos": rnd(fe_study.oos_stages(arms=arms))}


def stage_oos_boot(arms):
    return {"oos_bootstrap": rnd(fe_study.oos_bootstrap(arms=arms, control=CONTROL))}


def stage_clean_boot(arms):
    b = fe_boot.boot(
        seed=SEED,
        block=BLOCK,
        arms=arms,
        n_pre=5000,
        n_tax=1000,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        anchor_e2=True,
        deltas=tuple(a for a in arms if a != CONTROL),
        baselines=(CONTROL,),
        control=CONTROL,
        label="clean",
    )
    return {"bootstrap_clean_seed20261011": rnd(b)}


def stage_e3(arms):
    return {"E3_fresh_bootstraps": rnd(fe_study.stage_e3(arms=arms))}


def stage_reporting(arms):
    inst, quality = fe_study.instrument_stages_usd(arms=arms)
    return {
        "instruments": rnd(inst),
        "quality": rnd(quality),
        "E1_after_tax_etfs_rindex": rnd(fe_study.stage_e1(arms=arms)),
        "E2_rolling_starts": rnd(fe_study.stage_e2(arms=arms)),
    }


def stage_deferral(arms):
    """The survey's mandatory deferral reporting, on the main path (pre-tax and after tax)."""
    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    out = {}
    for arm in arms:
        rp = fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm,
                             mode="pre_tax", m=2, trace=True, stats=True)
        rq = fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm,
                             mode="progressive", m=2, trace=True, stats=True)
        ev = rp["_dfr"]
        out[arm] = {
            "deferrals": int(rp["deferred"]),
            "deferral_episodes": int(rp["deferral_episodes"]),
            "max_episode_sessions": int(rp["max_episode_sessions"]),
            "deferred_gain_try_events": float(rp["deferred_gain_try_events"]),
            "deferred_gain_try_episodes": float(rp["deferred_gain_try_episodes"]),
            "event_drift_max": float(ev[:, 0].max()) if len(ev) else 0.0,
            "event_drift_p95": float(np.percentile(ev[:, 0], 95)) if len(ev) else 0.0,
            "max_hold_above_target": float(rp["max_hold_above_target"]),
            "mean_held_weight": float(rp["mean_held_weight"]),
            "mean_abs_target_gap": float(rp["mean_abs_target_gap"]),
            "deferrals_by_year": rp["deferrals_by_year"],
            "sells_per_year": float(rp["sells"] / rp["years"]),
            "turnover_annual": float(rp["turnover_annual"]),
            "pre_tax_cagr": float(rp["cagr_usd_liquidated"]),
            "after_tax_cagr": float(rq["cagr_usd_liquidated"]),
            "tax_usd": float(rq["tax_total_usd_at_payment_fx"]),
            "index_relief_try": float(rq["index_relief_try"]),
            "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"],
            "after_tax_deferrals": int(rq["deferred"]),
            "after_tax_deferred_gain_try_events": float(rq["deferred_gain_try_events"]),
        }
    return {"deferral_report": rnd(out)}


def stage_gap_table(arms):
    """The January-gap episodes on the real (full-resolution) main path, per arm vs K."""
    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    traces = {
        arm: fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm, mode="pre_tax", m=2)
        for arm in arms
    }
    gaps = january_gaps(days, traces)
    base = gaps[CONTROL]
    for arm in arms:
        for y in gaps[arm]:
            gaps[arm][y]["delta_vs_K_pp"] = (gaps[arm][y]["log_return"] - base[y]["log_return"]) * 100
    return {"january_gaps": rnd(gaps)}


def stage_e5(arms):
    """Report only: where the arms' fill decisions differ from K's (pre-tax, m = 2)."""
    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    tr = {
        arm: fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm,
                             mode="pre_tax", m=2, trace=True)
        for arm in arms
    }
    out = {"window": {"start": days[START], "end": days[-1], "sessions": int(T - START + 1)}}
    for arm in arms:
        out[arm] = {"sells": int(np.sum(tr[arm]["_dec"] == 2)),
                    "postponements": int(tr[arm]["postponements"]),
                    "band_blocks": int(tr[arm]["blocked_band"]),
                    "deferrals": int(tr[arm]["deferred"])}
    for arm in arms:
        if arm == CONTROL:
            continue
        da, db = tr[arm]["_dec"], tr[CONTROL]["_dec"]
        out[f"{arm}_vs_K"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            "arm_sells_K_holds": int(np.sum((da == 2) & (db != 2))),
            "arm_holds_K_sells": int(np.sum((da != 2) & (db == 2))),
        }
    return {"E5": out}


def stage_lag_sensitivity(arms):
    """Report only: D3 with the M-2 YI-UFE convention in the first three sessions of a month."""
    days, o, c, w_r2, env = env_main()
    T = len(days) - 1
    name = "D3_lag2"
    fe_sim.RULE[name] = dict(fe_sim.RULE["D3"])
    fe_sim.RULE[name]["dfr_ufe_lag"] = 1
    out = {}
    for tag, arm in (("D3", "D3"), ("D3_lag2", name)):
        rp = fe_sim.simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm, mode="pre_tax", m=2)
        i0, i1 = fe_study.window_indices(days, *TAX_WINDOWS["2000_03_to_2026_09"])
        e = fe_sim.RunEnv(days, env.fx, env.ufe, i0, i1)
        rq = fe_sim.simulate(o, c, w_r2, e, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm=arm, mode="progressive")
        out[tag] = {
            "pre_tax_cagr": float(rp["cagr_usd_liquidated"]),
            "deferrals": int(rp["deferred"]),
            "after_tax_cagr_2000": float(rq["cagr_usd_liquidated"]),
        }
    del fe_sim.RULE[name]
    out["delta_lag2_minus_D3_pp"] = (out["D3_lag2"]["after_tax_cagr_2000"] - out["D3"]["after_tax_cagr_2000"]) * 100
    return {"lag_sensitivity": rnd(out)}


# ----------------------------------------------------------------------------- gates
GATE_KEYS = (
    "G1_base", "G1_calibrated", "G2_base", "G2_calibrated", "G3_base", "G3_calibrated",
    "G4", "G5_base", "G5_calibrated", "G6_base", "G6_calibrated", "G7", "G8", "G9",
    "T1", "T2", "T3",
)


def gates(res, arms):
    pre = res["pre_tax_main"]
    k = {md: {m: pre[md][m][CONTROL] for m in ("m1", "m2", "m4")} for md in ("base", "calibrated")}
    boot = res["bootstrap_clean_seed20261011"]
    tax = res["after_tax_windows"]
    oos = res["oos"]
    oosb = res["oos_bootstrap"]["pooled"]
    out = {}
    for arm in arms:
        if arm == CONTROL:
            continue
        g = {}
        # G1 (m = 2, both financings)
        for md in ("base", "calibrated"):
            c, kk = pre[md]["m2"][arm], k[md]["m2"]
            g[f"G1_{md}"] = bool(
                c["cagr"] >= kk["cagr"] - 1e-12
                or (c["calmar"] >= 1.1 * kk["calmar"] and c["cagr"] >= kk["cagr"] - 0.005)
            )
            g[f"G2_{md}"] = bool(c["max_dd"] <= kk["max_dd"] + 0.01)
            g[f"G3_{md}"] = bool(c["2017_end"]["cagr"] >= kk["2017_end"]["cagr"] - 0.01)
            # G6 = G1 at m = 1
            c1, k1 = pre[md]["m1"][arm], k[md]["m1"]
            g[f"G6_{md}"] = bool(
                c1["cagr"] >= k1["cagr"] - 1e-12
                or (c1["calmar"] >= 1.1 * k1["calmar"] and c1["cagr"] >= k1["cagr"] - 0.005)
            )
        # G4 sub-periods
        wins = [n for n in ("P1", "P2", "P3")
                if pre["base"]["m2"][arm]["subperiods"][n]["cagr"] > pre["base"]["m2"][CONTROL]["subperiods"][n]["cagr"]]
        g["G4"] = len(wins) >= 2
        g["G4_wins"] = wins
        # G5 pre-tax bootstrap, base and calibrated
        for md in ("base", "calibrated"):
            g[f"G5_{md}"] = bool(boot["pre"][md][arm]["diff_vs_C0_median_pp"] >= 0)
        # G7 six OOS windows
        d = [oos[w][arm]["delta_cagr_pp"] for w in OOS_WINDOWS]
        dd = [oos[w][arm]["delta_max_dd_pp"] for w in OOS_WINDOWS]
        g["G7"] = bool(
            float(np.median(d)) >= 0
            and sum(1 for x in d if x >= -0.5) >= 5
            and sum(1 for x in dd if x <= 3.0) >= 5
            and max(dd) <= 8.0
        )
        g["G7_detail"] = {"median_dcagr_pp": float(np.median(d)), "n_dcagr_ge_-0.5": sum(1 for x in d if x >= -0.5),
                          "n_ddd_le_3": sum(1 for x in dd if x <= 3.0), "worst_ddd_pp": float(max(dd))}
        # G8 pooled OOS bootstrap
        g["G8"] = bool(oosb[arm]["diff_vs_control_median_pp"] >= 0)
        # G9 turnover
        g["G9"] = bool(pre["base"]["m2"][arm]["turnover_annual"] <= 2 * pre["base"]["m2"][CONTROL]["turnover_annual"])
        # T1-T3 after tax at $100k
        d3w = {wn: (tax["100k"][wn][arm]["after_tax_cagr"] - tax["100k"][wn][CONTROL]["after_tax_cagr"]) * 100
               for wn in TAX_WINDOWS}
        g["T1"] = bool(all(v > 0 for v in d3w.values()))
        g["T1_detail_pp"] = d3w
        b1 = boot["tax"]["100k"]["progressive"][f"{arm}_vs_{CONTROL}"]
        b2 = boot["tax"]["100k"]["flat"][f"{arm}_vs_{CONTROL}"]
        g["T2"] = bool(b1["median_delta_cagr_pp"] >= 0 and b1["share_gt_0"] >= 0.55)
        g["T3"] = bool(b2["median_delta_cagr_pp"] >= 0 and b2["share_gt_0"] >= 0.55)
        g["T2_detail"] = {"median_pp": b1["median_delta_cagr_pp"], "share": b1["share_gt_0"]}
        g["T3_detail"] = {"median_pp": b2["median_delta_cagr_pp"], "share": b2["share_gt_0"]}
        keys = [k_ for k_ in GATE_KEYS if k_ in g]
        g["verdict"] = "PASS" if all(g[k_] for k_ in keys) else "FAIL"
        g["failed"] = [k_ for k_ in keys if not g[k_]]
        out[arm] = g
    passing = [a for a in out if out[a]["verdict"] == "PASS"]
    adopted = None
    if passing:
        adopted = max(passing, key=lambda a: pre["base"]["m2"][a]["cagr"])
    return {"arms": out, "adopted": adopted, "reason": (
        "no arm passes every gate" if adopted is None else f"{adopted} passes every gate, highest base m = 2 CAGR")}


# ----------------------------------------------------------------------------- run
def run():
    t0 = time.time()
    arms = REPORT
    print(f"pre-tax main ({len(arms)} arms)", flush=True)
    res = {"campaign": "state_defer_v1", "status": "complete"}
    res.update(stage_pre_tax(arms))
    print("harness", flush=True)
    res["harness"] = harness()
    print("after tax", flush=True)
    res.update(stage_after_tax(arms))
    print("cost stress", flush=True)
    res.update(stage_cost(arms))
    print("OOS windows", flush=True)
    res.update(stage_oos(arms))
    print("pooled OOS bootstrap (G8)", flush=True)
    res.update(stage_oos_boot(arms))
    print("clean bootstrap (seed 20261011)", flush=True)
    res.update(stage_clean_boot(arms))
    print("E3 fresh bootstraps", flush=True)
    res.update(stage_e3(arms))
    print("instruments / E1 / E2", flush=True)
    res.update(stage_reporting(arms))
    print("deferral report", flush=True)
    res.update(stage_deferral(arms))
    res.update(stage_gap_table(arms))
    print("E5 decisions + lag sensitivity", flush=True)
    res.update(stage_e5(arms))
    res.update(stage_lag_sensitivity(arms))
    res["gates"] = rnd(gates(res, arms))
    res["params"] = {
        "control": CONTROL,
        "candidates": list(CANDIDATES),
        "reference": "C0 = R2",
        "rule": {a: dict(fe_sim.RULE[a]) for a in arms},
        "d1_tail_window": fe_sim.MTAIL_TAIL,
        "d4_december_window": fe_sim.DEC_WIDE,
        "deferral_cap_sessions": fe_sim.DFR_MAX_SESS,
        "drift_cap": fe_sim.DFR_DRIFT_CAP,
        "d3_tax_fraction_of_nav": fe_sim.DFR_TAX_FRAC,
        "band": "A1u 0.5 / 3 / 8 pp at g_frac <= 1 % / <= 5 % / > 5 % (USD FIFO)",
        "main_window": res["pre_tax_main"]["window"],
        "seed": SEED, "block": BLOCK,
    }
    wall = round(time.time() - t0, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(rnd(res), indent=1, sort_keys=True))
    write_summary(res, wall)
    print(f"run: {wall}s -> RESULTS.json / SUMMARY.txt")
    return res


def write_summary(res, wall):
    pre, tax, gates_ = res["pre_tax_main"], res["after_tax_windows"], res["gates"]
    boot = res["bootstrap_clean_seed20261011"]
    oos = res["oos"]
    oosb = res["oos_bootstrap"]["pooled"]
    lines = []
    lines.append(f"state_defer_v1 -- {res['status']}  (wall clock of this --run: {wall}s)")
    lines.append(f"harness: {res['harness']['n_checks']} checks, {res['harness']['n_outside_tolerance']} outside tolerance, "
                 f"shared engine parity --quick {'PASS' if res['harness']['shared_engine_parity']['pass'] else 'FAIL'}")
    lines.append(f"adopted: {gates_['adopted']} ({gates_['reason']})")
    lines.append("")
    lines.append("arm   CAGR base  CAGR cal   maxDD base  maxDD cal   m1 base   2017+ base  turn/yr  sells/yr  defer  band")
    for arm in REPORT:
        b, c = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        lines.append(
            f"{arm:5s} {b['cagr']*100:9.4f}% {c['cagr']*100:9.4f}% {b['max_dd']*100:10.4f}% {c['max_dd']*100:10.4f}% "
            f"{pre['base']['m1'][arm]['cagr']*100:8.4f}% {b['2017_end']['cagr']*100:9.4f}% "
            f"{b['turnover_annual']:8.4f} {b['sells_per_year']:9.2f} {b['deferred']:6d} {b['blocked_band']:5d}"
        )
    lines.append("")
    lines.append("after tax, progressive, liquidated, base financing, m = 2 (gate capital $100k)")
    for cap in ("10k", "100k", "1M"):
        lines.append(f"  capital {cap}")
        for wn in TAX_WINDOWS:
            row = " ".join(f"{arm}={tax[cap][wn][arm]['after_tax_cagr']*100:.4f}" for arm in REPORT)
            d = " ".join(f"{arm}={((tax[cap][wn][arm]['after_tax_cagr']-tax[cap][wn][CONTROL]['after_tax_cagr'])*100):+.4f}"
                         for arm in REPORT if arm != CONTROL)
            lines.append(f"    {wn:22s} {row}   d-vs-K {d}")
    lines.append("")
    lines.append("clean bootstrap seed 20261011 block 20 ($100k)")
    for mode, key in (("B1-recent", "progressive"), ("B2-flat", "flat")):
        for arm in REPORT:
            if arm == CONTROL:
                lines.append(f"  {mode:10s} {arm:5s} median {boot['tax']['100k'][key][arm]['cagr_median']*100:8.4f}%")
                continue
            v = boot["tax"]["100k"][key][f"{arm}_vs_{CONTROL}"]
            lines.append(f"  {mode:10s} {arm:5s} median {boot['tax']['100k'][key][arm]['cagr_median']*100:8.4f}%  "
                         f"delta {v['median_delta_cagr_pp']:+.4f}pp share>0 {v['share_gt_0']*100:.1f}% "
                         f"p05 {v['p05_delta_cagr_pp']:+.4f} p95 {v['p95_delta_cagr_pp']:+.4f}")
    lines.append("")
    lines.append("pre-tax bootstrap (5,000 ten-year paths): median delta pp (share>0)")
    for md in ("base", "calibrated"):
        lines.append("  " + "  ".join(
            f"{arm}={boot['pre'][md][arm]['diff_vs_C0_median_pp']:+.4f}({boot['pre'][md][arm]['share_gt_0']*100:.1f}%)"
            for arm in REPORT if arm != CONTROL))
    lines.append("")
    lines.append("OOS windows: delta CAGR pp / delta maxDD pp (vs K), and the pooled bootstrap (G8)")
    lines.append("  " + " ".join(f"{w}" for w in OOS_WINDOWS))
    for arm in REPORT:
        if arm == CONTROL:
            continue
        lines.append(f"  {arm:5s} " + " ".join(
            f"{oos[w][arm]['delta_cagr_pp']:+.2f}/{oos[w][arm]['delta_max_dd_pp']:+.2f}" for w in OOS_WINDOWS)
            + f"   pooled {oosb[arm]['diff_vs_control_median_pp']:+.4f}pp ({oosb[arm]['share_gt_0']*100:.1f}%)")
    lines.append("")
    lines.append("gates (P = pass, F = fail)")
    for arm, g in gates_["arms"].items():
        keys = [k for k in GATE_KEYS if k in g]
        lines.append(f"  {arm:5s} " + " ".join(f"{k}={'P' if g[k] else 'F'}" for k in keys) + f"  -> {g['verdict']}")
        if g["failed"]:
            lines.append(f"        failed: {g['failed']}")
        lines.append(f"        G7 {g['G7_detail']}  T1 {g['T1_detail_pp']}  "
                     f"T2 {g['T2_detail']}  T3 {g['T3_detail']}")
    lines.append("")
    lines.append("deferral report (main window, pre-tax m = 2 / after-tax progressive $100k)")
    lines.append("  K/H2's deferral is the original H2 device: it has no fork episode/gain counter")
    lines.append("  (its `deferrals` counter is H2's own), so those columns are 0 for K and C0.")
    for arm in REPORT:
        d = res["deferral_report"][arm]
        lines.append(f"  {arm:5s} deferrals {d['deferrals']:5d} episodes {d['deferral_episodes']:4d} "
                     f"maxep {d['max_episode_sessions']:3d} defgain_events {d['deferred_gain_try_events']:14.0f} "
                     f"episodes {d['deferred_gain_try_episodes']:14.0f} "
                     f"drift max {d['event_drift_max']:.4f} p95 {d['event_drift_p95']:.4f} "
                     f"pathmax {d['max_hold_above_target']:.4f} sells/yr {d['sells_per_year']:.2f} "
                     f"taxUSD {d['tax_usd']:12.0f} meanW {d['mean_held_weight']:.4f} "
                     f"meanGap {d['mean_abs_target_gap']:.4f}")
    lines.append("")
    lines.append("January-gap episodes (log return from the first December close to the last")
    lines.append("January close, pre-tax; delta vs K in pp)")
    gaps = res["january_gaps"]
    for y in ("2016", "2019", "2025"):
        if y in gaps[CONTROL]:
            lines.append("  " + " ".join(
                f"{arm}={gaps[arm][y]['delta_vs_K_pp']:+.3f}" for arm in REPORT if y in gaps[arm]))
    (OUT / "SUMMARY.txt").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--parity", action="store_true")
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        smoke()
    elif args.parity:
        parity()
    elif args.harness:
        h = harness()
        sys.exit(0 if h["pass"] else 1)
    elif args.run:
        run()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
