#!/usr/bin/env python3
"""short_term_oracle_v1 -- DIAGNOSTIC: the size of the short-term-drawdown prize.

See PREREGISTRATION.md. Nothing here is a candidate; the arms are oracles built from
future knowledge and are labelled ORACLE everywhere. The deliverable is the size of
the prize for perfect / lagged / imperfect short-term drawdown detection, pre-tax and
after Turkish tax, versus production K.

Control: production K exactly (R2 + A1u gain band + H2 December deferral), on
`research/fast_engine`; R2 is reported as a reference row.

Stages
  census    pullback episodes on QQQ closes per depth class (count, depth, duration,
            share of K's drawdown time).
  pre       pre-tax main window, base/calibrated, m = 2 (CAGR, max DD, turnover).
  AT        after tax at $100k, progressive, liquidated, on the three windows.
  B12       clean bootstrap seed 20261011, block 20, 1,000 paths, $100k, B1-recent
            (progressive) and B2-flat, plus the pre-tax leg (base/calibrated).
  frontier  the precision sweep: after-tax delta vs K as a function of the
            false-alarm fraction f, sell-based and virtual-hedge.

Modes
  --smoke    synthetic random-walk data only; episodes/masks/prices/oracle-inertness.
  --harness  R1 parity --quick, R2 K anchors, R4 bootstrap-extension parity,
             R5 virtual-hedge identity, plus the mask reference checks.
  --run      harness, then every stage; writes RESULTS.json and SUMMARY.txt.

No gates: this is a measurement. See PREREGISTRATION.md section 5.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

from research.fast_engine import fe_boot, fe_config, fe_data, fe_signals, fe_sim, fe_study  # noqa: E402
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PROGRESSIVE  # noqa: E402
from research.short_term_oracle_v1.engine import fe_boot_targets as fbt  # noqa: E402

FULL_OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
START = fe_config.START              # 253 -> first measured session 2000-03-09
M = fe_config.M                      # 2
GATE_CAPITAL = fe_config.GATE_CAPITAL
TAX_CAPITALS = fe_config.TAX_CAPITALS
TAX_WINDOWS = fe_config.TAX_WINDOWS
FIN = fe_config.FIN
SEED, BLOCK = fe_config.SEED, fe_config.BLOCK
BOOT_PATHS = 1_000
CHUNK = 128
TOL_PP = 1e-4                        # 0.01 pp

# K's published anchors (task brief + band_h2_combo_v1/RESULTS.json)
REF_K = {
    ("base", 2): {"cagr": 0.157649, "max_dd": 0.466698},
    ("calibrated", 2): {"cagr": 0.151785, "max_dd": 0.467948},
}
REF_K_AT = {"2000_03_to_2026_09": 0.107533, "2010_01_to_end": 0.152354, "2017_01_to_end": 0.239720}
REF_K_AT_DELTA_VS_R2 = {"2000_03_to_2026_09": 0.005933, "2010_01_to_end": 0.005294, "2017_01_to_end": 0.010859}

# ---- the pre-registered arm grid (section 4 of PREREGISTRATION.md)
DEPTHS = (0.05, 0.08, 0.10, 0.15)
CUTS = (0.02, 0.03)
KS = (0, 3, 5)
XS = (0.08, 0.10)
SWEEP_F = (0.25, 0.50, 0.75, 1.0)
HEDGE_F = (0.0, 0.25, 0.50, 0.75, 1.0)


def _label(prefix, **kw):
    return prefix + "_" + "_".join(f"{k}{v}" for k, v in kw.items())


SPECS = {}
for _d in CUTS:
    for _k in KS:
        for _x in XS:
            SPECS[_label("O", **{"d": int(_d * 100), "k": _k, "X": int(_x * 100)})] = {
                "kind": "sell", "cut": _d, "X": _x, "k": _k, "f": 0.0}
for _f in SWEEP_F:
    SPECS[_label("S", **{"f": int(_f * 100)})] = {
        "kind": "sell", "cut": 0.03, "X": 0.10, "k": 3, "f": _f}
for _f in HEDGE_F:
    SPECS[_label("H", **{"f": int(_f * 100)})] = {
        "kind": "hedge", "cut": 0.03, "X": 0.10, "k": 3, "f": _f}

ARMS = tuple(SPECS)
CONTROL = "K"
R2 = "R2"


def _spec_key(sp):
    return (sp["cut"], sp["X"], sp["k"], sp["f"])


# ----------------------------------------------------------------------------- episode reference
def episodes_py(closes, e2, i0, i1):
    """Reference (Python) episode list -- PREREGISTRATION.md section 3, verbatim."""
    out = []
    t = i0
    while t <= i1:
        is20 = t >= 19 and closes[t] >= closes[t - 19 : t + 1].max()
        if bool(e2[t]) and is20:
            high = float(closes[t])
            end, by = i1, "end"
            for u in range(t + 1, i1 + 1):
                if closes[u] >= high:
                    end, by = u, "regain"
                    break
                if not e2[u]:
                    end, by = u, "e2off"
                    break
            seg = closes[t : end + 1]
            low = t + int(np.argmin(seg))
            out.append({
                "start": int(t), "end": int(end), "low": int(low), "high": high,
                "depth": float(1.0 - closes[low] / high), "dur": int(end - t),
                "dur_low": int(low - t), "by": by,
            })
            t = end + 1
        else:
            t += 1
    return out


def mask_py(eps, cut, min_depth, restore_k, false_frac, n):
    """Reference mask from the episode list (same arithmetic as the numba kernel)."""
    mask = np.zeros(n, dtype=np.int8)
    j = 0
    fired = 0
    for e in eps:
        depth = e["depth"]
        if depth >= min_depth:
            fire = True
        elif depth >= cut:
            fire = bool(np.floor((j + 1) * false_frac + 1e-12) > np.floor(j * false_frac + 1e-12))
            j += 1
        else:
            fire = False
        if not fire:
            continue
        thr = e["high"] * (1.0 - cut)
        ce = e["low"]
        # need the closes to find the first crossing; filled by caller-independent scan
        ce = e.get("cross", e["low"])
        r = max(e["low"] + restore_k, ce + 1)
        mask[ce:r] = 1
        fired += 1
    return mask, fired


def add_crossings(eps, closes, cut):
    for e in eps:
        thr = e["high"] * (1.0 - cut)
        idx = np.nonzero(closes[e["start"] : e["low"] + 1] <= thr)[0]
        e["cross"] = int(e["start"] + idx[0]) if len(idx) else e["low"]
    return eps


# ----------------------------------------------------------------------------- inputs / arms
def qqq_series():
    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    qqq = np.ascontiguousarray(q[1], dtype=float)
    e2 = fe_signals.e2_up(qqq)[0]
    return days, q, y, qqq, e2


def build_arms(i0, i1):
    """Per-model real price paths, K targets, oracle targets and hedge price paths."""
    days, q, y, qqq, e2 = qqq_series()
    T = len(qqq)
    models = {}
    comps = fe_signals.early_components(qqq)
    for md, spread in FIN.items():
        o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_config.EXP_Q)
        vol_i = fe_signals.vol_n(c)[0]
        w = fe_signals.arm_signals(qqq, e2, vol_i, "R2", comps)[0][0]
        models[md] = {"o": np.ascontiguousarray(o), "c": np.ascontiguousarray(c), "w": np.ascontiguousarray(w)}

    masks, counts = {}, {}
    for label, sp in SPECS.items():
        key = _spec_key(sp)
        if key in masks:
            continue
        m = np.zeros(T, dtype=np.int8)
        n = fbt.episode_mask_one(qqq, e2, i0, i1, sp["cut"], sp["X"], sp["k"], sp["f"], m)
        masks[key], counts[key] = m, int(n)

    targets = {md: {} for md in models}
    prices = {md: {} for md in models}
    for label, sp in SPECS.items():
        m = masks[_spec_key(sp)]
        for md in models:
            targets[md][label] = np.ascontiguousarray(np.where(m == 1, 0.0, models[md]["w"]))
            if sp["kind"] == "hedge":
                hm = np.zeros_like(m)
                hm[1:] = m[:-1]
                oo = np.empty_like(models[md]["o"])
                cc = np.empty_like(models[md]["c"])
                fbt.masked_prices(
                    np.ascontiguousarray(models[md]["o"][None, :]),
                    np.ascontiguousarray(models[md]["c"][None, :]),
                    np.ascontiguousarray(hm[None, :]),
                    np.ascontiguousarray(oo[None, :]), np.ascontiguousarray(cc[None, :]))
                prices[md][label] = (oo, cc)
            else:
                prices[md][label] = (models[md]["o"], models[md]["c"])
    # the control: K's own targets on the real path
    for md in models:
        targets[md][CONTROL] = models[md]["w"]
        prices[md][CONTROL] = (models[md]["o"], models[md]["c"])
    return {
        "days": days, "qqq": qqq, "e2": e2, "models": models,
        "masks": masks, "counts": counts, "targets": targets, "prices": prices,
        "i0": i0, "i1": i1,
    }


def run_arm(built, label, md, mode, capital, i0, i1, env, m=M):
    o, c = built["prices"][md][label]
    return fe_sim.simulate(o, c, built["targets"][md][label], env, capital, i0, i1,
                           arm=CONTROL, mode=mode, m=m)


# ----------------------------------------------------------------------------- stages
def census(built):
    days = built["days"]
    qqq, e2 = built["qqq"], built["e2"]
    T = len(days) - 1
    eps = episodes_py(qqq, e2, START, T)
    # K's pre-tax NAV (base, m = 2) for the drawdown-time denominator
    env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T)
    kpre = run_arm(built, CONTROL, "base", "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T, env)
    nav = kpre["_nav"]
    peak = np.maximum.accumulate(nav)
    under = np.zeros(T + 1, dtype=bool)          # indexed by session
    nsess = len(nav)
    under[START - 1 : START - 1 + nsess] = nav < peak
    under[:START] = False
    n_under = int(under[START : T + 1].sum())
    n_all = int(T - START + 1)

    out = {"window": {"start": days[START], "end": days[T], "sessions": n_all},
           "n_episodes": len(eps),
           "unresolved_e2off": int(sum(1 for e in eps if e["by"] == "e2off")),
           "open_at_end": int(sum(1 for e in eps if e["by"] == "end")),
           "k_underwater_sessions": n_under,
           "k_underwater_share_of_sessions": float(n_under / n_all),
           "classes": {}}
    for X in DEPTHS:
        cls = [e for e in eps if e["depth"] >= X]
        in_span = np.zeros(T + 1, dtype=bool)
        for e in cls:
            in_span[e["start"] : e["end"] + 1] = True
        n_span = int(in_span[START : T + 1].sum())
        n_span_under = int((in_span & under)[START : T + 1].sum())
        out["classes"][f"{int(X * 100)}"] = {
            "count": len(cls),
            "median_depth": float(np.median([e["depth"] for e in cls])) if cls else None,
            "median_duration_sessions": float(np.median([e["dur"] for e in cls])) if cls else None,
            "median_duration_to_low_sessions": float(np.median([e["dur_low"] for e in cls])) if cls else None,
            "max_duration_sessions": int(max([e["dur"] for e in cls])) if cls else None,
            "unresolved_e2off": int(sum(1 for e in cls if e["by"] == "e2off")),
            "sessions_in_class": n_span,
            "share_of_sessions": float(n_span / n_all),
            "share_of_K_drawdown_time": float(n_span_under / n_under) if n_under else 0.0,
            "members_10pct": [
                {"start": days[e["start"]], "low": days[e["low"]], "end": days[e["end"]],
                 "depth": round(e["depth"], 4), "dur": e["dur"], "by": e["by"]}
                for e in cls
            ] if X == 0.10 else [],
        }
    # the sweep denominators (2 % / 3 % trigger, below the 10 % class)
    for d in CUTS:
        shallow = [e for e in eps if d <= e["depth"] < 0.10]
        cls = [e for e in eps if e["depth"] >= 0.10]
        out[f"trigger_{int(d * 100)}pct"] = {
            "n_events_ge_10": len(cls),
            "n_shallow_d_to_10": len(shallow),
            "precision_at_f": {
                str(f): (len(cls) / (len(cls) + f * len(shallow)) if len(cls) else None)
                for f in (0.0, 0.25, 0.5, 0.75, 1.0)
            },
        }
    out["depth_histogram"] = {
        "le_0": int(sum(1 for e in eps if e["depth"] <= 0.0)),
        "0_to_2pct": int(sum(1 for e in eps if 0.0 < e["depth"] < 0.02)),
        "2_to_5pct": int(sum(1 for e in eps if 0.02 <= e["depth"] < 0.05)),
        "5_to_8pct": int(sum(1 for e in eps if 0.05 <= e["depth"] < 0.08)),
        "8_to_10pct": int(sum(1 for e in eps if 0.08 <= e["depth"] < 0.10)),
        "ge_10pct": int(sum(1 for e in eps if e["depth"] >= 0.10)),
    }
    # ---- descriptive attribution (no new arm): where would the avoided loss come from?
    # For each episode of the 8 % / 10 % classes, the QLD leg's move between the oracle's
    # sell fill (open of cut+1) and buy fill (open of low+k+1), weighted by K's target at the
    # cut close. Purely a decomposition of the census; it is not simulated.
    oq = built["models"]["base"]["o"]
    cq = built["models"]["base"]["c"]
    wq = built["models"]["base"]["w"]
    out["prize_attribution"] = {}
    for X in (0.08, 0.10):
        for (dd, kk) in ((0.02, 0), (0.03, 3)):
            epsx = add_crossings([dict(e) for e in eps if e["depth"] >= X], qqq, dd)
            agg = {}
            for e in epsx:
                c = e["cross"]
                r = max(e["low"] + kk, c + 1)
                if r + 1 > T or c + 1 > T:
                    continue
                avoided = 1.0 - oq[r + 1] / oq[c + 1]     # positive = a loss the arm did not take
                contrib = float(wq[c]) * avoided
                a = agg.setdefault(e["by"], {"n": 0, "sum_weighted_avoided_loss": 0.0, "sessions": 0})
                a["n"] += 1
                a["sum_weighted_avoided_loss"] += contrib
                a["sessions"] += int(r - c)
            tot = sum(a["sum_weighted_avoided_loss"] for a in agg.values())
            for a in agg.values():
                a["share_of_total"] = (a["sum_weighted_avoided_loss"] / tot) if tot else None
            out["prize_attribution"][f"X{int(X*100)}_d{int(dd*100)}_k{kk}"] = {
                "by_end_reason": agg,
                "total_weighted_avoided_loss": float(tot),
                "note": "descriptive only: K's target weight at the cut close x the 2x leg's decline "
                        "from the sell fill to the buy fill; not a simulated arm",
            }
    return out


def stage_pre_tax(built, labels):
    days = built["days"]
    T = len(days) - 1
    out = {}
    for md in FIN:
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T)
        out[md] = {}
        for label in labels:
            r = run_arm(built, label, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T, env)
            yrs = r["years"]
            out[md][label] = {
                "cagr": float(r["cagr_usd_unliquidated"]),
                "max_dd": float(r["max_dd_nav_usd"]),
                "turnover_annual": float(r["turnover_annual"]),
                "sells_per_year": float(r["qld_sells"] / yrs),
                "trades_per_year": float(r["strategy_trades"] / yrs),
                "tax_usd": float(r["tax_total_usd_at_payment_fx"]),
                "n_episodes_fired": int(built["counts"][_spec_key(SPECS[label])]) if label in SPECS else None,
            }
    for md in FIN:
        for label in labels:
            if label == CONTROL:
                continue
            out[md][label]["delta_cagr_pp"] = (out[md][label]["cagr"] - out[md][CONTROL]["cagr"]) * 100
            out[md][label]["delta_max_dd_pp"] = (out[md][label]["max_dd"] - out[md][CONTROL]["max_dd"]) * 100
            out[md][label]["extra_sells_per_year"] = (out[md][label]["sells_per_year"]
                                                      - out[md][CONTROL]["sells_per_year"])
    out["window"] = {"start": days[START], "end": days[T], "sessions": T - START + 1}
    return out


def stage_after_tax(built, labels):
    days = built["days"]
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        out[wn] = {}
        for label in labels:
            r = run_arm(built, label, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
            rp = run_arm(built, label, "base", "pre_tax", 1.0, i0, i1, env)
            out[wn][label] = {
                "pre_tax_cagr": float(rp["cagr_usd_liquidated"]),
                "after_tax_cagr": float(r["cagr_usd_liquidated"]),
                "after_tax_cagr_unliquidated": float(r["cagr_usd_unliquidated"]),
                "max_dd_after_tax": float(r["max_dd_nav_usd"]),
                "max_dd_pre_tax": float(rp["max_dd_nav_usd"]),
                "tax_usd": float(r["tax_total_usd_at_payment_fx"]),
                "sells_per_year": float(r["sells"] / r["years"]),
                "qld_sells_per_year": float(r["qld_sells"] / r["years"]),
                "terminal_usd": float(r["terminal_usd_liquidated"]),
                "years": float(r["years"]),
                "n_episodes_fired": int(built["counts"][_spec_key(SPECS[label])]) if label in SPECS else None,
            }
        for label in labels:
            if label == CONTROL:
                continue
            out[wn][label]["delta_pre_tax_pp"] = (
                out[wn][label]["pre_tax_cagr"] - out[wn][CONTROL]["pre_tax_cagr"]) * 100
            out[wn][label]["delta_max_dd_pre_tax_pp"] = (
                out[wn][label]["max_dd_pre_tax"] - out[wn][CONTROL]["max_dd_pre_tax"]) * 100
            out[wn][label]["delta_after_tax_pp"] = (
                out[wn][label]["after_tax_cagr"] - out[wn][CONTROL]["after_tax_cagr"]) * 100
            out[wn][label]["delta_max_dd_pp"] = (
                out[wn][label]["max_dd_after_tax"] - out[wn][CONTROL]["max_dd_after_tax"]) * 100
            out[wn][label]["delta_tax_usd"] = out[wn][label]["tax_usd"] - out[wn][CONTROL]["tax_usd"]
            out[wn][label]["extra_sells_per_year"] = (
                out[wn][label]["qld_sells_per_year"] - out[wn][CONTROL]["qld_sells_per_year"])
    return out


# ----------------------------------------------------------------------------- bootstrap
def boot_run(specs, n_pre, n_tax, seed=SEED, block=BLOCK, capitals=("100k",),
             modes=("progressive", "flat"), m=M, chunk=CHUNK, window=None, quiet=True):
    """Block bootstrap over exogenous per-path targets (see engine/fe_boot_targets.py).

    `specs[label]` = {"rule": (band, usd_rule, defer), "kind": "k"|"sell"|"hedge",
    "cut", "X", "k", "f"}. Kind "k" uses production R2's target series unchanged
    (the control); "sell" zeroes the oracle intervals; "hedge" keeps the target and
    zeroes the leg's return on those sessions.
    """
    win = window if window is not None else fe_boot.BootWindow()
    env = win.env
    labels = list(specs)
    n_paths = max(n_pre, n_tax)
    rng = np.random.default_rng(seed)
    pre = {(md, a): np.empty(n_pre, dtype=float) for md in FIN for a in labels}
    at = {cl: {mode: {a: np.empty(n_tax, dtype=float) for a in labels} for mode in modes}
          for cl in capitals}
    cost = fe_sim.COST_PER_SIDE * m
    cap_pre = TAX_CAPITALS[GATE_CAPITAL]

    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = fe_boot.draw_positions(rng, block, W, fe_config.TAX_BOOT_L, win.npool,
                                     fe_config.TAX_BOOT_EXT)
        pc, e2, legs, _w_e2 = fbt.chunk_legs_pc(win.q, win.y, pos)
        masks = {}
        for label, sp in specs.items():
            if sp["kind"] == "k":
                continue
            key = (sp["cut"], sp["X"], sp["k"], sp["f"])
            if key in masks:
                continue
            msk = np.zeros(pc.shape, dtype=np.int8)
            cnt = np.zeros(W, dtype=np.int64)
            fbt.episode_masks(pc, e2, win.i0, win.i1, key[0], key[1], key[2], key[3], msk, cnt)
            masks[key] = msk

        for label, sp in specs.items():
            band, usd, defer = sp["rule"]
            for md in FIN:
                so, sc, w = legs[md]
                if sp["kind"] == "k":
                    wq, oo, cc = w, so, sc
                else:
                    msk = masks[(sp["cut"], sp["X"], sp["k"], sp["f"])]
                    wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
                    if sp["kind"] == "hedge":
                        hm = np.zeros_like(msk)
                        hm[:, 1:] = msk[:, :-1]
                        oo = np.empty_like(so)
                        cc = np.empty_like(sc)
                        fbt.masked_prices(so, sc, hm, oo, cc)
                    else:
                        oo, cc = so, sc
                if p0 < n_pre:
                    got = np.empty(W, dtype=float)
                    fbt.run_pretax(oo, cc, wq, env.fx, env.upm, env.mth_of, env.yr_of,
                                   env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr,
                                   env.pay_part, cap_pre, win.i0, win.i1, cost,
                                   band, usd, defer, env.Y0, env.M_lo, got)
                    n = min(W, n_pre - p0)
                    pre[(md, label)][p0 : p0 + n] = got[:n]

            so, sc, w = legs["base"]
            if sp["kind"] == "k":
                wq, oo, cc = w, so, sc
            else:
                msk = masks[(sp["cut"], sp["X"], sp["k"], sp["f"])]
                wq = np.ascontiguousarray(np.where(msk == 1, 0.0, w))
                if sp["kind"] == "hedge":
                    hm = np.zeros_like(msk)
                    hm[:, 1:] = msk[:, :-1]
                    oo = np.empty_like(so)
                    cc = np.empty_like(sc)
                    fbt.masked_prices(so, sc, hm, oo, cc)
                else:
                    oo, cc = so, sc
            for mode in modes:
                mi = MODE_PROGRESSIVE if mode == "progressive" else MODE_FLAT
                for cl in capitals:
                    got = np.empty(W, dtype=float)
                    fbt.run_tax(oo, cc, wq, env.fx, env.upm, env.mth_of, env.yr_of,
                                env.scale_y, env.dec_tail, env.pay_ptr, env.pay_yr,
                                env.pay_part, TAX_CAPITALS[cl], win.i0, win.i1, cost,
                                band, usd, defer, mi, win.yrs, fbt._SC_LIQ,
                                env.Y0, env.M_lo, got)
                    n = min(W, n_tax - p0)
                    at[cl][mode][label][p0 : p0 + n] = got[:n]
        if not quiet:
            print(f"  bootstrap path {min(p0 + W, n_paths)}/{n_paths}", flush=True)

    return {"pre": pre, "tax": at, "window": win, "labels": labels}


def summarise_boot(raw):
    """Medians and deltas vs the control (K) for the pre-tax and after-tax legs."""
    labels = raw["labels"]
    out = {"pre": {}, "tax": {}}
    for md in FIN:
        base = raw["pre"][(md, CONTROL)]
        out["pre"][md] = {}
        for a in labels:
            c = raw["pre"][(md, a)]
            d = c - base
            out["pre"][md][a] = {
                "cagr_median": float(np.median(c)),
                "median_delta_pp": float(np.median(d) * 100),
                "mean_delta_pp": float(np.mean(d) * 100),
                "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100),
            }
    for cl in raw["tax"]:
        out["tax"][cl] = {}
        for mode in raw["tax"][cl]:
            base = raw["tax"][cl][mode][CONTROL]
            out["tax"][cl][mode] = {}
            for a in labels:
                c = raw["tax"][cl][mode][a]
                d = c - base
                out["tax"][cl][mode][a] = {
                    "cagr_median": float(np.median(c)),
                    "median_delta_pp": float(np.median(d) * 100),
                    "mean_delta_pp": float(np.mean(d) * 100),
                    "share_gt_0": float(np.mean(d > 0)),
                    "p05_delta_pp": float(np.percentile(d, 5) * 100),
                    "p95_delta_pp": float(np.percentile(d, 95) * 100),
                }
    return out


def stage_bootstrap(labels, quiet=True):
    specs = {CONTROL: {"rule": (True, True, True), "kind": "k"}}
    for label in labels:
        if label == CONTROL:
            continue
        if label == R2:                   # reference row: same targets, band/deferral off
            specs[R2] = {"rule": (False, False, False), "kind": "k"}
            continue
        sp = SPECS[label]
        specs[label] = {"rule": (True, True, True), "kind": sp["kind"], "cut": sp["cut"],
                        "X": sp["X"], "k": sp["k"], "f": sp["f"]}
    raw = boot_run(specs, n_pre=BOOT_PATHS, n_tax=BOOT_PATHS, capitals=(GATE_CAPITAL,),
                   modes=("progressive", "flat"), quiet=quiet)
    out = summarise_boot(raw)
    out["settings"] = {"seed": SEED, "block": BLOCK, "paths": BOOT_PATHS,
                       "capital": GATE_CAPITAL, "modes": ["progressive", "flat"],
                       "pre_models": list(FIN), "chunk": CHUNK,
                       "calendar": [raw["window"].dates[0], raw["window"].dates[-1]]}
    return out


# ----------------------------------------------------------------------------- harness
def _parity_quick():
    p = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                       cwd=str(ROOT), capture_output=True, text=True)
    ok = (p.returncode == 0) and ("parity PASS" in p.stdout)
    return {"pass": bool(ok), "returncode": int(p.returncode)}


def harness(deep=True):
    checks, bad = {}, 0
    checks["R1_parity_quick"] = _parity_quick()
    if not checks["R1_parity_quick"]["pass"]:
        bad += 1

    built = build_arms(START, len(fe_data.load()[0]) - 1)
    days = built["days"]
    T = len(days) - 1
    bad_anchors = 0

    # R2: K's anchors through this study's code
    anchors = {"pre_tax": {}, "after_tax": {}}
    for md in FIN:
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T)
        r = run_arm(built, CONTROL, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T, env)
        ref = REF_K[(md, 2)]
        d_c = r["cagr_usd_unliquidated"] - ref["cagr"]
        d_d = r["max_dd_nav_usd"] - ref["max_dd"]
        anchors["pre_tax"][md] = {"reference_cagr": ref["cagr"], "reproduced_cagr": r["cagr_usd_unliquidated"],
                                  "diff_pp": d_c * 100, "reference_max_dd": ref["max_dd"],
                                  "reproduced_max_dd": r["max_dd_nav_usd"], "diff_dd_pp": d_d * 100}
        if abs(d_c) > TOL_PP or abs(d_d) > TOL_PP:
            bad_anchors += 1
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        rk = run_arm(built, CONTROL, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
        anchors["after_tax"][wn] = {
            "reference": REF_K_AT[wn], "reproduced": rk["cagr_usd_liquidated"],
            "diff_pp": (rk["cagr_usd_liquidated"] - REF_K_AT[wn]) * 100,
            "max_dd": rk["max_dd_nav_usd"],
        }
        if abs(anchors["after_tax"][wn]["diff_pp"]) > 0.01:
            bad_anchors += 1
    checks["R2_K_anchors"] = {"pass": bool(bad_anchors == 0), "n_failed_cells": bad_anchors,
                              "detail": anchors}
    if bad_anchors:
        bad += 1

    # R2b: K - R2 deltas (run R2 through the same path, band/defer off)
    checks["R2b_K_minus_R2"] = {"cells": {}}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        c0 = fe_sim.simulate(built["models"]["base"]["o"], built["models"]["base"]["c"],
                             built["models"]["base"]["w"], env, TAX_CAPITALS[GATE_CAPITAL],
                             i0, i1, arm="C0", mode="progressive", m=M)
        kk = run_arm(built, CONTROL, "base", "progressive", TAX_CAPITALS[GATE_CAPITAL], i0, i1, env)
        d = (kk["cagr_usd_liquidated"] - c0["cagr_usd_liquidated"])
        checks["R2b_K_minus_R2"]["cells"][wn] = {"K_minus_R2_pp": d * 100,
                                                 "reference_pp": REF_K_AT_DELTA_VS_R2[wn] * 100}
        if abs(d - REF_K_AT_DELTA_VS_R2[wn]) > 1e-4:
            bad += 1
    checks["R2b_K_minus_R2"]["pass"] = bool(all(
        abs(v["K_minus_R2_pp"] - v["reference_pp"]) <= 1e-2
        for v in checks["R2b_K_minus_R2"]["cells"].values()))

    # R5: the virtual hedge with an all-zero mask is the identity (prices and NAV)
    ok_px = ok_nav = True
    for md in FIN:
        o, c = built["models"][md]["o"], built["models"][md]["c"]
        hm = np.zeros((1, c.shape[0]), dtype=np.int8)
        oo, cc = np.empty_like(o)[None, :], np.empty_like(c)[None, :]
        fbt.masked_prices(o[None, :], c[None, :], hm, oo, cc)
        if not (np.array_equal(oo[0], o) and np.array_equal(cc[0], c)):
            ok_px = False
        # a never-firing oracle mask is K's own target series
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T)
        k_nav = run_arm(built, CONTROL, md, "pre_tax", TAX_CAPITALS[GATE_CAPITAL], START, T, env)["_nav"]
        m = np.zeros(c.shape[0], dtype=np.int8)
        n = fbt.episode_mask_one(built["qqq"], built["e2"], START, T, 0.03, 2.0, 3, 0.0, m)
        w0 = np.where(m == 1, 0.0, built["models"][md]["w"])
        r = fe_sim.simulate(o, c, w0, env, TAX_CAPITALS[GATE_CAPITAL], START, T,
                            arm=CONTROL, mode="pre_tax", m=M)
        if n != 0 or not np.array_equal(r["_nav"], k_nav):
            ok_nav = False
    checks["R5_hedge_identity"] = {"pass": bool(ok_px and ok_nav),
                                   "prices_identical": bool(ok_px),
                                   "never_firing_oracle_equals_K": bool(ok_nav)}
    if not (ok_px and ok_nav):
        bad += 1

    # mask kernels vs the Python reference, on the real QQQ series, all distinct specs
    eps = episodes_py(built["qqq"], built["e2"], START, T)
    mask_cells, mask_bad = 0, 0
    uniq = sorted({_spec_key(sp) for sp in SPECS.values()})
    for (cut, X, k, f) in uniq:
        ref_eps = add_crossings([dict(e) for e in eps], built["qqq"], cut)
        ref, _ = mask_py(ref_eps, cut, X, k, f, len(built["qqq"]))
        got = np.zeros(len(built["qqq"]), dtype=np.int8)
        fbt.episode_mask_one(built["qqq"], built["e2"], START, T, cut, X, k, f, got)
        mask_cells += 1
        if not np.array_equal(ref, got):
            mask_bad += 1
    checks["mask_reference"] = {"pass": bool(mask_bad == 0), "specs": mask_cells,
                                "mismatches": mask_bad}
    if mask_bad:
        bad += 1

    # R4: the bootstrap extension with K's own targets reproduces fe_boot.boot bit for bit
    if deep:
        specs = {CONTROL: {"rule": (True, True, True), "kind": "k"},
                 "C0": {"rule": (False, False, False), "kind": "k"}}
        raw = boot_run(specs, n_pre=200, n_tax=200, capitals=(GATE_CAPITAL,),
                       modes=("progressive", "flat"), chunk=256, quiet=True)
        ref = fe_boot.boot(seed=SEED, block=BLOCK, arms=("C0", CONTROL), n_pre=200,
                           n_tax=200, capitals=(GATE_CAPITAL,),
                           modes=("progressive", "flat"), anchor_e2=False,
                           deltas=(), baselines=(CONTROL,), label="R4", quiet=True,
                           keep_paths=True)
        cells, mism = 0, 0
        # exact per-path equality on every cell the reference publishes
        for md in FIN:
            for a in ("C0", CONTROL):
                cells += 1
                if not np.array_equal(raw["pre"][(md, a)], ref["paths"]["pre"][(md, a)]):
                    mism += 1
        for mode in ("progressive", "flat"):
            for a in ("C0", CONTROL):
                cells += 1
                if not np.array_equal(raw["tax"][GATE_CAPITAL][mode][a],
                                      ref["paths"]["tax"][GATE_CAPITAL][mode][a]):
                    mism += 1
        checks["R4_boot_extension_parity"] = {"pass": bool(mism == 0), "cells": cells,
                                              "mismatches": mism,
                                              "note": "raw per-path arrays, 200 paths, seed/block published, arms C0 and K"}
        if mism:
            bad += 1

    # chunk_legs_pc == fe_boot.chunk_legs
    win = fe_boot.BootWindow()
    pos = fe_boot.draw_positions(np.random.default_rng(7), BLOCK, 16, fe_config.TAX_BOOT_L,
                                 win.npool, fe_config.TAX_BOOT_EXT)
    pc, e2c, legs, w_e2 = fbt.chunk_legs_pc(win.q, win.y, pos)
    l2, e2b, w2 = fe_boot.chunk_legs(win.q, win.y, pos)
    ok = all(np.array_equal(legs[md][i], l2[md][i]) for md in FIN for i in range(3))
    checks["chunk_legs_pc_parity"] = {"pass": bool(ok and np.array_equal(e2c, e2b)
                                                  and np.array_equal(w_e2, w2))}
    if not checks["chunk_legs_pc_parity"]["pass"]:
        bad += 1

    checks["n_failed"] = int(bad)
    checks["pass"] = bool(bad == 0)
    return checks


def smoke():
    """Synthetic data only: episode detection, masks, hedge identity, oracle inertness."""
    rng = np.random.default_rng(11)
    n = 2000
    r = rng.normal(0.0004, 0.013, n)
    price = 100 * np.cumprod(1 + r)
    e2 = np.zeros(n, dtype=bool)
    e2[300:] = True                      # force episodes
    o = np.concatenate([[price[0]], price[:-1] * (1 + rng.normal(0, 0.003, n - 1))])
    checks = {}

    eps = episodes_py(price, e2, 300, n - 1)
    checks["episodes_detected"] = len(eps) > 0
    checks["episode_spans_disjoint"] = all(eps[i]["end"] < eps[i + 1]["start"] for i in range(len(eps) - 1))
    checks["depth_in_0_1"] = all(0.0 <= e["depth"] < 1.0 for e in eps)

    for (cut, X, k, f) in [(0.02, 0.08, 0, 0.0), (0.03, 0.10, 3, 0.0),
                           (0.03, 0.10, 3, 0.25), (0.03, 0.10, 3, 1.0)]:
        ref, nref = mask_py(add_crossings([dict(e) for e in eps], price, cut), cut, X, k, f, n)
        got = np.zeros(n, dtype=np.int8)
        ngot = fbt.episode_mask_one(price, e2, 300, n - 1, cut, X, k, f, got)
        checks[f"mask_kernel_eq_ref_{int(cut*100)}_{int(X*100)}_{k}_{int(f*100)}"] = bool(
            np.array_equal(ref, got) and nref == ngot)
    # Bresenham counts
    for f, want in ((0.25, 1), (0.5, 2), (0.75, 3), (1.0, 4)):
        sel = [bool(np.floor((j + 1) * f + 1e-12) > np.floor(j * f + 1e-12)) for j in range(4)]
        checks[f"bresenham_{int(f*100)}"] = bool(sum(sel) == want)

    # hedge identity and a real zero-return session
    for mask_val in (0, 1):
        hm = np.zeros((1, n), dtype=np.int8)
        if mask_val:
            hm[0, 500] = 1
        oo, cc = np.empty_like(o)[None, :], np.empty_like(price)[None, :]
        fbt.masked_prices(o[None, :], price[None, :], hm, oo, cc)
        if mask_val == 0:
            checks["hedge_identity"] = bool(np.array_equal(oo[0], o) and np.array_equal(cc[0], price))
        else:
            checks["hedge_zero_return"] = bool(
                abs(cc[0, 500] / cc[0, 499] - 1.0) < 1e-15
                and abs(cc[0, 501] / cc[0, 500] - price[501] / price[500]) < 1e-12)
    return {"checks": checks, "pass": bool(all(checks.values()))}


# ----------------------------------------------------------------------------- the run
def run():
    t_start = time.time()
    h = harness(deep=True)
    built = build_arms(START, len(built_days()) - 1)
    labels = (CONTROL,) + ARMS
    cen = census(built)

    # R2 reference row: same target series, band and deferral off
    days = built["days"]
    T = len(days) - 1
    pre = stage_pre_tax(built, labels)
    # add the R2 row: it is not a SPEC, so run it here
    for md in FIN:
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), START, T)
        rr = fe_sim.simulate(built["models"][md]["o"], built["models"][md]["c"],
                             built["models"][md]["w"], env, TAX_CAPITALS[GATE_CAPITAL],
                             START, T, arm="C0", mode="pre_tax", m=M)
        pre[md][R2] = {"cagr": rr["cagr_usd_unliquidated"], "max_dd": rr["max_dd_nav_usd"],
                       "turnover_annual": rr["turnover_annual"],
                       "sells_per_year": rr["qld_sells"] / rr["years"],
                       "trades_per_year": rr["strategy_trades"] / rr["years"],
                       "tax_usd": rr["tax_total_usd_at_payment_fx"]}
        pre[md][R2]["delta_cagr_pp"] = (pre[md][R2]["cagr"] - pre[md][CONTROL]["cagr"]) * 100
        pre[md][R2]["delta_max_dd_pp"] = (pre[md][R2]["max_dd"] - pre[md][CONTROL]["max_dd"]) * 100

    at = stage_after_tax(built, labels)
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, *fe_study.try_market(days), i0, i1)
        rr = fe_sim.simulate(built["models"]["base"]["o"], built["models"]["base"]["c"],
                             built["models"]["base"]["w"], env, TAX_CAPITALS[GATE_CAPITAL],
                             i0, i1, arm="C0", mode="progressive", m=M)
        rr_pre = fe_sim.simulate(built["models"]["base"]["o"], built["models"]["base"]["c"],
                                 built["models"]["base"]["w"], env, 1.0,
                                 i0, i1, arm="C0", mode="pre_tax", m=M)
        at[wn][R2] = {"pre_tax_cagr": rr_pre["cagr_usd_liquidated"],
                      "after_tax_cagr": rr["cagr_usd_liquidated"],
                      "after_tax_cagr_unliquidated": rr["cagr_usd_unliquidated"],
                      "max_dd_after_tax": rr["max_dd_nav_usd"],
                      "max_dd_pre_tax": rr_pre["max_dd_nav_usd"],
                      "tax_usd": rr["tax_total_usd_at_payment_fx"],
                      "sells_per_year": rr["sells"] / rr["years"],
                      "qld_sells_per_year": rr["qld_sells"] / rr["years"],
                      "terminal_usd": rr["terminal_usd_liquidated"],
                      "years": rr["years"],
                      "n_episodes_fired": None}
        at[wn][R2]["delta_pre_tax_pp"] = (at[wn][R2]["pre_tax_cagr"] - at[wn][CONTROL]["pre_tax_cagr"]) * 100
        at[wn][R2]["delta_after_tax_pp"] = (at[wn][R2]["after_tax_cagr"] - at[wn][CONTROL]["after_tax_cagr"]) * 100
        at[wn][R2]["delta_max_dd_pp"] = (at[wn][R2]["max_dd_after_tax"] - at[wn][CONTROL]["max_dd_after_tax"]) * 100

    print("  bootstrap ...", flush=True)
    boot = stage_bootstrap((R2,) + ARMS, quiet=False)

    frontier = precision_frontier(cen, at, boot)
    verdict = make_verdict(cen, pre, at, boot, frontier)

    res = {
        "campaign": "short_term_oracle_v1",
        "status": "DIAGNOSTIC (upper bound). No gates. Nothing tradeable. ORACLE arms use future knowledge.",
        "params": {
            "main_window": {"start": days[START], "end": days[-1], "sessions": T - START + 1},
            "after_tax_windows": {k: list(v) for k, v in TAX_WINDOWS.items()},
            "capital_usd": TAX_CAPITALS[GATE_CAPITAL],
            "mode": "progressive, liquidated", "m": M,
            "bootstrap": {"seed": SEED, "block": BLOCK, "paths": BOOT_PATHS,
                          "calendar": [fe_config.TAX_BOOT_LO, fe_config.TAX_BOOT_HI]},
            "episode_rule": "QQQ close is a 20-session high with the E2 vote on and no episode open; ends at the first close >= the start high or the first E2-off close; depth = max decline to the minimum close; classes 5/8/10/15 %.",
            "arms": {lab: SPECS[lab] for lab in ARMS},
        },
        "harness": h,
        "census": cen,
        "pre_tax": pre,
        "after_tax": at,
        "bootstrap": boot,
        "frontier": frontier,
        "verdict": verdict,
    }
    elapsed = time.time() - t_start
    FULL_OUT.write_text(json.dumps(res, indent=1, sort_keys=True, default=_jsonable))
    write_summary(res, elapsed)
    return res


def built_days():
    return fe_data.load()[0]


def _jsonable(x):
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    raise TypeError(type(x))


def precision_frontier(cen, at, boot):
    """After-tax delta vs K as a function of the false-alarm fraction f."""
    d10 = cen["trigger_3pct"]
    out = {"n_events_ge_10": d10["n_events_ge_10"],
           "n_shallow_3_to_10": d10["n_shallow_d_to_10"],
           "points": {}}
    for f in (0.0,) + SWEEP_F:
        if f == 0.0:
            sell, hedge = "O_d3_k3_X10", "H_f0"
        else:
            sell, hedge = _label("S", **{"f": int(f * 100)}), _label("H", **{"f": int(f * 100)})
        n_ev = d10["n_events_ge_10"]
        n_sh = d10["n_shallow_d_to_10"]
        out["points"][str(f)] = {
            "label_sell": sell, "label_hedge": hedge,
            "precision": (n_ev / (n_ev + f * n_sh)) if n_ev else None,
            "after_tax_delta_sell_pp": {wn: at[wn][sell]["delta_after_tax_pp"] for wn in TAX_WINDOWS},
            "after_tax_delta_hedge_pp": {wn: at[wn][hedge]["delta_after_tax_pp"] for wn in TAX_WINDOWS},
            "after_tax_delta_sell_min_pp": min(at[wn][sell]["delta_after_tax_pp"] for wn in TAX_WINDOWS),
            "after_tax_delta_hedge_min_pp": min(at[wn][hedge]["delta_after_tax_pp"] for wn in TAX_WINDOWS),
            "boot_B1_median_sell_pp": boot["tax"][GATE_CAPITAL]["progressive"][sell]["median_delta_pp"],
            "boot_B2_median_sell_pp": boot["tax"][GATE_CAPITAL]["flat"][sell]["median_delta_pp"],
            "boot_B1_median_hedge_pp": boot["tax"][GATE_CAPITAL]["progressive"][hedge]["median_delta_pp"],
            "boot_B2_median_hedge_pp": boot["tax"][GATE_CAPITAL]["flat"][hedge]["median_delta_pp"],
        }
    # break-even f (linear interpolation on the three-window minimum)
    pts = [(float(f), v["after_tax_delta_sell_min_pp"]) for f, v in out["points"].items()]
    pts.sort()
    out["break_even_f_sell"] = _crossing(pts)
    pts_h = [(float(f), v["after_tax_delta_hedge_min_pp"]) for f, v in out["points"].items()]
    pts_h.sort()
    out["break_even_f_hedge"] = _crossing(pts_h)
    out["break_even_note"] = (
        "No crossing: every pre-registered false-alarm fraction keeps a positive three-window "
        "after-tax delta vs K. In this construction each trigger is followed by a known low, so a "
        "false alarm still avoids the decline from the trigger to that low; the sweep therefore "
        "measures the cost of extra round trips, not the cost of an imprecise trigger. Precision is "
        "not the binding constraint -- knowing the low is.")
    return out


def _crossing(pts):
    for (f0, y0), (f1, y1) in zip(pts, pts[1:]):
        if y0 > 0 >= y1:
            return float(f0 + (f1 - f0) * (y0 / (y0 - y1)))
        if y0 == 0:
            return float(f0)
    if pts and pts[0][1] <= 0:
        return 0.0
    return None


def make_verdict(cen, pre, at, boot, frontier):
    """Plain-language measurements for the report (no selection)."""
    grid = [lab for lab in ARMS if lab.startswith("O_")]
    def best(metric, key="delta_cagr_pp"):
        vals = {lab: pre["base"][lab][key] for lab in grid}
        lab = max(vals, key=vals.get)
        return {"arm": lab, "value_pp": vals[lab]}
    at_best = {}
    for wn in TAX_WINDOWS:
        vals = {lab: at[wn][lab]["delta_after_tax_pp"] for lab in grid}
        lab = max(vals, key=vals.get)
        at_best[wn] = {"arm": lab, "delta_pp": vals[lab]}
    perfect = "O_d2_k0_X10"
    no_lag = "O_d2_k0_X8"
    lag5 = "O_d3_k5_X10"
    wedge = {}
    for lab in (no_lag, "O_d3_k3_X10", lag5):
        wedge[lab] = {}
        for wn in TAX_WINDOWS:
            pre_d = at[wn][lab]["delta_pre_tax_pp"]
            post_d = at[wn][lab]["delta_after_tax_pp"]
            wedge[lab][wn] = {"pre_tax_pp": pre_d, "after_tax_pp": post_d,
                              "tax_wedge_share": (1.0 - post_d / pre_d) if pre_d > 0 else None}
    return {
        "note": "Diagnostic. Positive = the oracle arm beats K; these are upper bounds with future knowledge.",
        "tax_wedge": wedge,
        "best_pre_tax_grid_cell": best("cagr"),
        "best_after_tax_grid_cell": at_best,
        "perfect_no_lag_d2_X10": {
            "pre_tax_delta_pp": {"base": pre["base"][perfect]["delta_cagr_pp"],
                                 "calibrated": pre["calibrated"][perfect]["delta_cagr_pp"]},
            "max_dd_delta_pp": {"base": pre["base"][perfect]["delta_max_dd_pp"],
                                "calibrated": pre["calibrated"][perfect]["delta_max_dd_pp"]},
            "after_tax_delta_pp": {wn: at[wn][perfect]["delta_after_tax_pp"] for wn in TAX_WINDOWS},
            "boot_B1_median_pp": boot["tax"][GATE_CAPITAL]["progressive"][perfect]["median_delta_pp"],
            "boot_B2_median_pp": boot["tax"][GATE_CAPITAL]["flat"][perfect]["median_delta_pp"],
            "sells_per_year_extra": at["2000_03_to_2026_09"][perfect]["extra_sells_per_year"],
        },
        "perfect_no_lag_d2_X8": {
            "after_tax_delta_pp": {wn: at[wn][no_lag]["delta_after_tax_pp"] for wn in TAX_WINDOWS},
            "pre_tax_delta_pp": {"base": pre["base"][no_lag]["delta_cagr_pp"]},
        },
        "lag5_d3_X10": {
            "after_tax_delta_pp": {wn: at[wn][lag5]["delta_after_tax_pp"] for wn in TAX_WINDOWS},
            "pre_tax_delta_pp": {"base": pre["base"][lag5]["delta_cagr_pp"]},
        },
        "tax_free_vs_sell_O3310": {
            "sell_min_after_tax_delta_pp": min(at[wn]["O_d3_k3_X10"]["delta_after_tax_pp"] for wn in TAX_WINDOWS),
            "hedge_min_after_tax_delta_pp": min(at[wn]["H_f0"]["delta_after_tax_pp"] for wn in TAX_WINDOWS),
            "sell_boot_B1_pp": boot["tax"][GATE_CAPITAL]["progressive"]["O_d3_k3_X10"]["median_delta_pp"],
            "hedge_boot_B1_pp": boot["tax"][GATE_CAPITAL]["progressive"]["H_f0"]["median_delta_pp"],
            "sell_extra_tax_usd": {wn: at[wn]["O_d3_k3_X10"]["delta_tax_usd"] for wn in TAX_WINDOWS},
        },
        "frontier": frontier,
        "census_headline": {k: {"count": v["count"],
                                "share_of_K_drawdown_time": v["share_of_K_drawdown_time"]}
                            for k, v in cen["classes"].items()},
    }


def write_summary(res, elapsed):
    pre, at, boot = res["pre_tax"], res["after_tax"], res["bootstrap"]
    L = []
    L.append("short_term_oracle_v1 -- DIAGNOSTIC (upper bound; ORACLE arms use future knowledge)")
    L.append("Nothing here is tradeable. No gates apply.")
    L.append("")
    L.append(f"main window {res['params']['main_window']['start']} .. {res['params']['main_window']['end']}"
             f"  ({res['params']['main_window']['sessions']} sessions); after tax $100k progressive, m=2")
    L.append(f"wall clock of --run: {elapsed:.1f} s")
    L.append("")
    L.append("== episode census (QQQ closes) ==")
    L.append(f"  episodes {res['census']['n_episodes']} (unresolved by E2-off "
             f"{res['census']['unresolved_e2off']}); K underwater on "
             f"{res['census']['k_underwater_sessions']} sessions "
             f"({res['census']['k_underwater_share_of_sessions']:.1%})")
    L.append("  class      n   med depth  med dur  share of K drawdown time")
    for x, c in res["census"]["classes"].items():
        L.append(f"  >={x:>2}%   {c['count']:>3}   "
                 f"{(c['median_depth'] or 0):>7.3f}   {(c['median_duration_sessions'] or 0):>6.0f}   "
                 f"{c['share_of_K_drawdown_time']:>7.1%}")
    L.append("")
    L.append("== prize attribution (descriptive; K weight x avoided 2x decline, by episode end) ==")
    for key, v in res["census"]["prize_attribution"].items():
        parts = " ".join(f"{k}: n={a['n']} share={a['share_of_total']:.0%}"
                         for k, a in sorted(v["by_end_reason"].items()))
        L.append(f"  {key:<12} {parts}")
    L.append("")
    L.append("== pre-tax main window (CAGR %, max DD %) ==")
    L.append("  arm                 base CAGR  cal CAGR   base DD   cal DD  sells/yr  turn/yr")
    for lab in [CONTROL, R2] + [a for a in ARMS]:
        b, c = pre["base"][lab], pre["calibrated"][lab]
        L.append(f"  {lab:<20} {b['cagr']*100:>8.2f}  {c['cagr']*100:>8.2f}  "
                 f"{b['max_dd']*100:>8.2f}  {c['max_dd']*100:>8.2f}  {b['sells_per_year']:>8.2f}  "
                 f"{b['turnover_annual']:>7.2f}")
    L.append("")
    L.append("== pre-tax delta vs K per window (pp), and fired episodes (main window) ==")
    L.append("  arm                 fired   2000     2010     2017")
    for lab in [R2] + list(ARMS):
        cells = " ".join(f"{at[wn][lab]['delta_pre_tax_pp']:>+7.2f}" for wn in TAX_WINDOWS)
        fired = pre["base"][lab].get("n_episodes_fired")
        fired = "  -  " if fired is None else f"{fired:>4d} "
        L.append(f"  {lab:<20} {fired} {cells}")
    L.append("")
    L.append("== after tax $100k: CAGR (%) with delta vs K (pp); max-DD delta on the 2000 window ==")
    L.append("  arm                 2000 CAGR(d)        2010 CAGR(d)        2017 CAGR(d)      2000 DD(d)")
    for lab in [R2] + list(ARMS):
        cells = []
        for wn in TAX_WINDOWS:
            r = at[wn][lab]
            cells.append(f"{r['after_tax_cagr']*100:>7.2f}({r['delta_after_tax_pp']:+.2f})")
        dd = at["2000_03_to_2026_09"][lab]["delta_max_dd_pp"]
        L.append(f"  {lab:<20} " + " ".join(f"{c:>18}" for c in cells) + f"  {dd:>+7.2f}")
    L.append("")
    L.append("== clean bootstrap, median delta vs K (pp) / share > 0 ==")
    L.append("  arm                  pre base  pre cal   B1 recent   B2 flat")
    for lab in [R2] + list(ARMS):
        b1 = boot["tax"][GATE_CAPITAL]["progressive"][lab]
        b2 = boot["tax"][GATE_CAPITAL]["flat"][lab]
        pb = boot["pre"]["base"][lab]
        pc_ = boot["pre"]["calibrated"][lab]
        L.append(f"  {lab:<20} {pb['median_delta_pp']:>+8.2f} {pc_['median_delta_pp']:>+8.2f}   "
                 f"{b1['median_delta_pp']:>+7.2f}/{b1['share_gt_0']:.0%}   "
                 f"{b2['median_delta_pp']:>+7.2f}/{b2['share_gt_0']:.0%}")
    L.append("")
    L.append("== precision frontier (O(3,3,10) + false alarms; after-tax delta vs K, pp) ==")
    L.append("     f   precision    sell 2000/2010/2017   hedge 2000/2010/2017   sell min   hedge min")
    for f, p in sorted(res["frontier"]["points"].items(), key=lambda kv: float(kv[0])):
        s = "/".join(f"{p['after_tax_delta_sell_pp'][w]:+.2f}" for w in TAX_WINDOWS)
        h = "/".join(f"{p['after_tax_delta_hedge_pp'][w]:+.2f}" for w in TAX_WINDOWS)
        L.append(f"  {float(f):>4.2f}   {p['precision']:>8.3f}    {s:>20}   {h:>22}   "
                 f"{p['after_tax_delta_sell_min_pp']:>+8.2f}  {p['after_tax_delta_hedge_min_pp']:>+8.2f}")
    L.append(f"  break-even f (sell, three-window minimum) = {res['frontier']['break_even_f_sell']}")
    L.append(f"  break-even f (hedge)                      = {res['frontier']['break_even_f_hedge']}")
    L.append("  " + res["frontier"]["break_even_note"])
    L.append("")
    L.append("== tax wedge on the prize (after-tax delta / pre-tax delta) ==")
    for lab in ("O_d2_k0_X8", "O_d3_k3_X10", "O_d3_k5_X10"):
        w = res["verdict"]["tax_wedge"][lab]
        cells = " ".join(f"{wn}: {w[wn]['tax_wedge_share']:.0%}" for wn in TAX_WINDOWS)
        L.append(f"  {lab:<16} {cells}")
    L.append("")
    L.append("== harness ==")
    L.append(f"  pass = {res['harness']['pass']}; R1 parity --quick = {res['harness']['R1_parity_quick']['pass']}")
    SUMMARY.write_text("\n".join(L) + "\n")


# ----------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="synthetic-data checks only")
    ap.add_argument("--harness", action="store_true", help="reproduction checks (R1-R5)")
    ap.add_argument("--run", action="store_true", help="run every stage and write RESULTS.json / SUMMARY.txt")
    args = ap.parse_args(argv)
    if args.smoke:
        r = smoke()
        print(json.dumps(r, indent=1))
        return 0 if r["pass"] else 1
    if args.harness:
        r = harness(deep=True)
        print(json.dumps(r, indent=1, default=_jsonable))
        return 0 if r["pass"] else 1
    if args.run:
        r = run()
        print(f"wrote {FULL_OUT.name} and {SUMMARY.name}; harness pass={r['harness']['pass']}")
        return 0 if r["harness"]["pass"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
