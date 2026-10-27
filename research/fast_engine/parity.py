"""parity.py -- the old pure-Python research code and the numba engine, side by side.

Every quantity is checked twice:

  * side by side on real inputs -- `screen.engine_run` / `screen.boot` / the batch signal
    routines against `fe_sim.simulate` / `fe_boot.boot` / `fe_signals`, on the same arrays, with
    the NAV paths required to agree to 1e-9 relative (in practice they agree exactly: no
    reassociation, no fastmath, no FMA contraction -- see `RESULTS.json` in this directory);
  * against the published cells of `research/band_h2_combo_v1/RESULTS.json`, which the study
    wrote with the old code, at the precision that file stores (6 decimals).

Usage:  python -m research.fast_engine.parity [--quick]

`--quick` skips the full published clean bootstrap (5000 pre-tax / 1000 after-tax paths).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = ROOT / "research/band_h2_combo_v1"
OUT = HERE / "PARITY.json"

# the published-value tolerance: RESULTS.json stores round(x, 6), so half of the last digit
PUBLISHED_TOL = 5.0e-7
# the side-by-side tolerance on quantities built from the same float ops
EXACT_TOL = 1e-9


def _old_screen():
    spec = importlib.util.spec_from_file_location("old_screen", OLD / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm_keys(o):
    """Flatten to {dotted string path: numeric leaf}, stringifying dict keys (JSON keys)."""
    out = {}

    def walk(v, prefix):
        if isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, f"{prefix}{k}.")
        elif isinstance(v, bool) or v is None or isinstance(v, str):
            return
        elif isinstance(v, (list, tuple)):
            for i, vv in enumerate(v):
                walk(vv, f"{prefix}{i}.")
        elif isinstance(v, (int, float, np.integer, np.floating)):
            out[prefix[:-1]] = float(v)
        else:
            raise TypeError(f"{prefix}: {type(v)}")

    walk(o, "")
    return out


class Check:
    """One parity section."""

    def __init__(self, name):
        self.name = name
        self.cells = 0
        self.mismatch = []
        self.max_abs = 0.0
        self.tol = EXACT_TOL

    def add(self, label, mine, ref, tol, absolute=False):
        """`absolute` compares |diff| (the published cells are stored as round(x, 6), so half of
        the last decimal is the entire available precision); otherwise the relative diff."""
        self.tol = tol
        self.cells += 1
        d = abs(float(mine) - float(ref))
        rel = d / max(1e-30, abs(float(ref)))
        score = d if absolute else rel
        if d > self.max_abs:
            self.max_abs = d
        if score > tol:
            self.mismatch.append({"cell": label, "mine": float(mine), "ref": float(ref), "abs": d, "rel": rel})

    def compare(self, name, mine, ref, tol, absolute=False):
        """Compare two nested dicts leaf by leaf."""
        a, b = _norm_keys(mine), _norm_keys(ref)
        for k in sorted(set(a) | set(b)):
            if k not in a:
                self.mismatch.append({"cell": f"{name}.{k}", "mine": None, "ref": b[k], "abs": None, "rel": None})
                continue
            if k not in b:
                self.mismatch.append({"cell": f"{name}.{k}", "mine": a[k], "ref": None, "abs": None, "rel": None})
                continue
            self.add(f"{name}.{k}", a[k], b[k], tol, absolute)

    def result(self):
        return {
            "section": self.name,
            "cells": self.cells,
            "mismatches": len(self.mismatch),
            "max_abs_diff": self.max_abs,
            "tolerance": self.tol,
            "pass": not self.mismatch,
            "detail": self.mismatch[:20],
        }


def _arrays_equal(a, b, label, out):
    """Exact array identity, reported as a max |diff| (bools compare as 0/1)."""
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        out.mismatch.append({"cell": label, "mine": list(a.shape), "ref": list(b.shape), "abs": None, "rel": None})
        out.cells += 1
        return
    if a.dtype == bool or b.dtype == bool:
        d = 0.0 if np.array_equal(a, b) else 1.0
    elif a.size == 0:
        d = 0.0
    else:
        x, y = a.astype(np.float64), b.astype(np.float64)
        finite = np.isfinite(x) & np.isfinite(y)
        d = float(np.max(np.abs(x - y)[finite])) if finite.any() else 0.0
        if not np.array_equal(np.isnan(x), np.isnan(y)):
            d = float("inf")
    out.add(label, d, 0.0, EXACT_TOL)


# ----------------------------------------------------------------------------- sections
def check_engine_side_by_side(old, sections, arms):
    """`screen.engine_run` vs `fe_sim.simulate` on the main window, every arm/mode/capital."""
    from . import fe_sim

    c = Check("engine_side_by_side")
    days, o, c_, w_e2, w_r2, e2 = old.main_inputs(0.0)
    mkt = old.te.load_market(old.FX_CSV, old.UFE_CSV, days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, mkt.fx, mkt.ufe, old.START, T)
    keys = [
        "years",
        "ending_nav_usd",
        "max_dd_nav_usd",
        "cagr_usd_liquidated",
        "terminal_usd_liquidated",
        "cagr_usd_unliquidated",
        "terminal_usd_unliquidated",
        "tax_total_try",
        "tax_total_usd_at_payment_fx",
        "turnover_total",
        "turnover_annual",
        "fees_usd",
        "index_relief_try",
    ]
    ints = ["strategy_trades", "qld_sells", "tax_funding_sales", "sells", "postponements", "blocked_band", "deferred"]
    for mode in ("pre_tax", "progressive", "flat"):
        for cap in (1.0, 10_000.0, 100_000.0, 1_000_000.0):
            for arm in arms:
                a = old.engine_run(cap, old.START, T, days, mkt, o, c_, w_r2, e2, arm, mode)
                b = fe_sim.simulate(o, c_, w_r2, env, cap, old.START, T, arm=arm, mode=mode)
                _arrays_equal(a["_nav"], b["_nav"], f"nav.{mode}.{arm}.{cap:g}", c)
                for k in keys:
                    c.add(f"{k}.{mode}.{arm}.{cap:g}", b[k], a[k], EXACT_TOL)
                for k in ints:
                    if a[k] != b[k]:
                        c.add(f"{k}.{mode}.{arm}.{cap:g}", b[k] + 0.0, a[k] + 0.0, EXACT_TOL)
                    else:
                        c.cells += 1
                if a["tax_assessed_try_by_year"] != b["tax_assessed_try_by_year"]:
                    c.mismatch.append(
                        {
                            "cell": f"tax_by_year.{mode}.{arm}",
                            "mine": b["tax_assessed_try_by_year"],
                            "ref": a["tax_assessed_try_by_year"],
                            "abs": None,
                            "rel": None,
                        }
                    )
                if a["postponements_by_year"] != b["postponements_by_year"]:
                    c.mismatch.append(
                        {
                            "cell": f"post_by_year.{mode}.{arm}",
                            "mine": b["postponements_by_year"],
                            "ref": a["postponements_by_year"],
                            "abs": None,
                            "rel": None,
                        }
                    )
                if a["realised_gain_try_by_reason"] != b["realised_gain_try_by_reason"]:
                    c.mismatch.append(
                        {
                            "cell": f"gain_by_reason.{mode}.{arm}",
                            "mine": b["realised_gain_try_by_reason"],
                            "ref": a["realised_gain_try_by_reason"],
                            "abs": None,
                            "rel": None,
                        }
                    )
    # the C0 trace (E5's input)
    a = old.engine_run(100_000.0, old.START, T, days, mkt, o, c_, w_r2, e2, "K", "pre_tax", trace=True)
    b = fe_sim.simulate(o, c_, w_r2, env, 100_000.0, old.START, T, arm="K", mode="pre_tax", trace=True)
    _arrays_equal(a["_dec"], b["_dec"], "dec_trace.K", c)
    sections.append(c.result())


def check_signals(old, sections):
    """Batched `fe_signals` against the per-path Python calls (bitwise)."""
    from . import fe_data, fe_signals

    c = Check("signals")
    d0, o0, c0, we0, wr0, e20 = old.main_inputs(0.0)
    d1, o1, c1, we1, wr1, e21 = fe_data.main_inputs(0.0)
    for nm, x, y in (
        ("main_o", o0, o1),
        ("main_c", c0, c1),
        ("main_w_e2", we0, we1),
        ("main_w_r2", wr0, wr1),
        ("main_e2", e20, e21),
    ):
        _arrays_equal(x, y, f"main_inputs.{nm}", c)
    c.add("main_inputs.days", len(d0) == len(d1) and d0 == d1, 1.0, EXACT_TOL)
    days, arr, y = old.trv2.load()
    rng = np.random.default_rng(11)
    panel = np.array([100 * np.cumprod(1 + rng.normal(0.0003, 0.02, 900)) for _ in range(23)])
    e2b = fe_signals.e2_up(panel)
    comps = fe_signals.early_components(panel)
    oe, fa = fe_signals.r2_state(panel, e2b, comps)
    for p in range(panel.shape[0]):
        cp = old.early_components(panel[p : p + 1])
        _arrays_equal(fe_signals.e2_up(panel[p : p + 1])[0], e2b[p], f"e2.row{p}", c)
        for k, nm in enumerate(("sma50", "vol", "med", "lag")):
            _arrays_equal(cp[k][0], comps[k][p], f"comps.{nm}.row{p}", c)
        oep, fap = old.r2_state(panel[p : p + 1], e2b[p : p + 1], cp)
        _arrays_equal(oep[0], oe[p], f"r2.on_eff.row{p}", c)
        _arrays_equal(fap[0], fa[p], f"r2.factor.row{p}", c)
    # the rolling median: the reference (per-window nanmedian), the numpy port and the numba
    # running median must be bit-identical, on the real series and on random panels (odd/even
    # windows included)
    _arrays_equal(
        old._rolling_median_vol_ref(comps[1], 252),
        fe_signals._rolling_median_vol(comps[1], 252),
        "median_numba_vs_reference",
        c,
    )
    _arrays_equal(
        old._rolling_median_vol(comps[1], 252, 32),
        fe_signals.rolling_median_vol(comps[1], 252),
        "median_numba_vs_numpy",
        c,
    )
    for P, T, win in ((37, 700, 252), (9, 300, 20), (5, 60, 7), (3, 1000, 252)):
        v = np.abs(rng.normal(0.3, 0.1, (P, T)))
        v[:, :11] = np.nan
        _arrays_equal(
            old._rolling_median_vol_ref(v, win), fe_signals.rolling_median_vol(v, win), f"median_panel.{P}x{T}x{win}", c
        )
    sections.append(c.result())


def check_invariants(old, sections, arms):
    """Per-session account invariants, read out of the kernel's `dbg` buffer.

    The NAV path agreeing is necessary but not sufficient: `nav[i] = cash + units * close[i]` can
    hold while cash and units are individually wrong. This checks the two components separately,
    plus that the lot book never goes negative and never has more live lots than it ever appended.
    """
    from . import fe_data, fe_sim

    c = Check("invariants")
    days, o, cl, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    T = len(days) - 1
    i0 = old.START
    env = fe_sim.RunEnv(days, fe_data.fx_at(days), fe_data.load_ufe(), i0, T)
    nsess = T - i0 + 1
    for mode, cap in (("progressive", 100_000.0), ("flat", 100_000.0), ("progressive", 10_000.0)):
        for arm in arms:
            dbg = np.zeros((nsess, 6), dtype=np.float64)
            r = fe_sim.simulate(o[: T + 1], cl[: T + 1], w_r2[: T + 1], env, cap, i0, T, arm=arm, mode=mode, dbg=dbg)
            nav = r["_nav"]
            recon = dbg[:, 0] + dbg[:, 1] * cl[i0 : T + 1]
            _arrays_equal(nav[1:], recon, f"nav_recomposition.{mode}.{arm}.{cap:g}", c)
            c.add(f"units_nonneg.{mode}.{arm}.{cap:g}", float(dbg[:, 1].min()), 0.0, EXACT_TOL, absolute=True)
            c.add(f"lots_nonneg.{mode}.{arm}.{cap:g}", float(dbg[:, 3].min()), 0.0, EXACT_TOL, absolute=True)
            c.add(f"head_nonneg.{mode}.{arm}.{cap:g}", float(dbg[:, 2].min()), 0.0, EXACT_TOL, absolute=True)
    sections.append(c.result())


def check_cash_yield(old, sections):
    """The optional idle-cash yield: inert by default, and arithmetically what it says it is.

    Every published study runs with no yield, so this checks that (a) an env with no rate and an
    env with an all-zero rate are bit-identical, and (b) with a constant rate and no position the
    NAV is the compounded cash, session by session, exactly as a Python loop computes it.
    """
    from . import fe_data, fe_sim

    c = Check("cash_yield")
    days, o, cl, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    T = len(days) - 1
    i0, n = old.START, len(days)
    fx, ufe = fe_data.fx_at(days), fe_data.load_ufe()
    env0 = fe_sim.RunEnv(days, fx, ufe, i0, T)
    envz = fe_sim.RunEnv(days, fx, ufe, i0, T, cash_rate=np.zeros(n))
    a = fe_sim.simulate(o, cl, w_r2, env0, 100_000.0, i0, T, arm="K", mode="progressive")
    b = fe_sim.simulate(o, cl, w_r2, envz, 100_000.0, i0, T, arm="K", mode="progressive")
    _arrays_equal(a["_nav"], b["_nav"], "all_zero_rate_is_inert", c)
    r = 0.0001
    envr = fe_sim.RunEnv(days, fx, ufe, i0, T, cash_rate=np.full(n, r))
    zeros = np.zeros(T + 1)
    got = fe_sim.simulate(o, cl, zeros, envr, 100_000.0, i0, T, arm="C0", mode="pre_tax")["_nav"]
    cash, want = 100_000.0, np.empty(len(got))
    want[0] = cash
    for k in range(i0, T + 1):
        cash = cash + cash * r
        want[k - i0 + 1] = cash
    _arrays_equal(got, want, "constant_rate_compounds_the_cash", c)
    sections.append(c.result())


def check_boot_side_by_side(old, sections, arms, n_pre=600, n_tax=300):
    """`screen.boot` vs `fe_boot.boot` on the same seed/block, per path then aggregated."""
    from . import fe_boot

    c = Check("boot_side_by_side")
    t0 = time.time()
    a = old.boot(
        seed=old.SEED,
        block=old.BLOCK,
        arms=arms,
        n_pre=n_pre,
        n_tax=n_tax,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        deltas=old.CANDIDATES,
        baselines=(old.CONTROL, old.BASELINE),
        label="parity-old",
    )
    t_old = time.time() - t0
    t0 = time.time()
    b = fe_boot.boot(
        seed=old.SEED,
        block=old.BLOCK,
        arms=arms,
        n_pre=n_pre,
        n_tax=n_tax,
        capitals=("100k", "1M"),
        modes=("progressive", "flat"),
        deltas=old.CANDIDATES,
        baselines=(old.CONTROL, old.BASELINE),
        label="parity-new",
        chunk=128,
    )
    t_new = time.time() - t0
    c.compare("boot", {k: v for k, v in b.items()}, a, EXACT_TOL)
    sections.append(c.result())
    sections.append(
        {
            "section": "boot_side_by_side_wallclock",
            "paths": [n_pre, n_tax],
            "old_s": t_old,
            "new_s": t_new,
            "speedup": t_old / t_new,
        }
    )


def check_published(old, sections, arms, quick):
    """Every published cell of band_h2_combo_v1/RESULTS.json against the new engine."""
    from . import fe_boot, fe_study

    ref = json.loads((OLD / "RESULTS.json").read_text())

    def pub(name, mine, key, tol=PUBLISHED_TOL):
        c = Check(name)
        c.compare("", mine, ref[key], tol, absolute=True)
        sections.append(c.result())

    pre, pre_usd = fe_study.pre_tax_main(arms)
    pub("published.pre_tax_main", pre, "pre_tax_main")
    pub("published.pre_tax_main_r_usd", pre_usd, "pre_tax_main_r_usd")
    pub("published.after_tax_windows", fe_study.after_tax_windows(tuple(old.TAX_CAPITALS), arms), "after_tax_windows")
    pub("published.cost_stress_m4", fe_study.cost_stress(arms), "cost_stress_m4")
    pub("published.oos", fe_study.oos_stages(arms), "oos")
    inst, quality = fe_study.instrument_stages_usd(arms)
    pub("published.instruments", inst, "instruments")
    pub("published.quality", quality, "quality")
    pub("published.E1_after_tax_etfs_rindex", fe_study.stage_e1(arms), "E1_after_tax_etfs_rindex")
    pub("published.E2_rolling_starts", fe_study.stage_e2(arms), "E2_rolling_starts")
    pub("published.E5", {"decisions": fe_study.stage_e5_decisions()}, "E5")
    window = fe_boot.BootWindow()
    pub("published.E3_fresh_bootstraps", fe_study.stage_e3(arms, window=window), "E3_fresh_bootstraps")
    if not quick:
        t0 = time.time()
        b = fe_boot.boot(
            seed=old.SEED,
            block=old.BLOCK,
            arms=arms,
            n_pre=old.PRE_BOOT_PATHS,
            n_tax=old.TAX_BOOT_PATHS,
            capitals=("100k", "1M"),
            modes=("progressive", "flat"),
            deltas=old.CANDIDATES,
            baselines=(old.CONTROL, old.BASELINE),
            label="clean",
            window=window,
            quiet=True,
        )
        dt = time.time() - t0
        pub("published.bootstrap_clean_seed20261011", b, "bootstrap_clean_seed20261011")
        sections.append(
            {"section": "clean_bootstrap_wallclock", "paths": [old.PRE_BOOT_PATHS, old.TAX_BOOT_PATHS], "new_s": dt}
        )
        # the study's own control block: the R2 anchors the published harness carries
        c = Check("published.harness_reproduction_checks")
        for k, v in ref["harness"]["r2_anchors"]["pre_tax"].items():
            md, mm = k.split("_")  # "base_m2" -> the published md x m cell
            cell = pre[md][mm][old.CONTROL]
            c.add(f"r2_anchor.{k}.cagr", cell["cagr"], v["reference_cagr"], PUBLISHED_TOL, True)
            c.add(f"r2_anchor.{k}.max_dd", cell["max_dd"], v["reference_max_dd"], PUBLISHED_TOL, True)
            c.add(f"r2_anchor.{k}.turnover", cell["turnover_annual"], v["reference_turnover"], PUBLISHED_TOL, True)
        for wn, v in ref["harness"]["r2_anchors"]["after_tax_100k"].items():
            win = fe_study.after_tax_windows(("100k",), arms)["100k"][wn][old.CONTROL]
            c.add(f"r2_anchor_at.{wn}", win["after_tax_cagr"], v["reference"][1], PUBLISHED_TOL, True)
        sections.append(c.result())


def check_behaviour(old, sections):
    """The engine behaviour a future study must know about (documented in README.md).

    `rule_index` is inert: with `RULE["A1u"]`/`RULE["K"]`'s band on, `engine_run` always takes the
    `spec["usd_rule"]` branch of the band decision, so `rule_sale` (and its `rule_index` flag) is
    never consulted for a fill. This checks the claim on the numba engine.
    """
    from . import fe_data, fe_sim
    from .fe_config import START

    c = Check("behaviour")
    days, o, cl, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fe_data.fx_at(days), fe_data.load_ufe(), START, T)
    worst = 0.0
    for arm in ("C0", "A1u", "H2", "K"):
        a = fe_sim.simulate(o, cl, w_r2, env, 100_000.0, START, T, arm=arm, mode="pre_tax", rule_index=True)
        b = fe_sim.simulate(o, cl, w_r2, env, 100_000.0, START, T, arm=arm, mode="pre_tax", rule_index=False)
        worst = max(worst, float(np.max(np.abs(a["_nav"] - b["_nav"]))))
    c.add("rule_index_is_inert_on_the_band", worst, 0.0, EXACT_TOL)
    sections.append(c.result())


def run(quick=False, arms=None, side_by_side_only=False):
    old = _old_screen()
    arms = tuple(arms or old.ARMS)
    sections = []
    t0 = time.time()
    check_signals(old, sections)
    check_engine_side_by_side(old, sections, arms)
    check_invariants(old, sections, arms)
    check_cash_yield(old, sections)
    check_behaviour(old, sections)
    if not side_by_side_only:
        check_boot_side_by_side(old, sections, arms, n_pre=600 if not quick else 60, n_tax=300 if not quick else 30)
        check_published(old, sections, arms, quick)
    passed = all(s.get("pass", True) for s in sections if "pass" in s)
    out = {
        "generated_by": "research/fast_engine/parity.py",
        "quick": quick,
        "arms": list(arms),
        "wall_s": time.time() - t0,
        "pass": passed,
        "sections": sections,
    }
    OUT.write_text(json.dumps(out, indent=2, default=float) + "\n")
    for s in sections:
        if "pass" in s:
            print(
                f"  {s['section']:44s} {s['cells']:6d} cells  {s['mismatches']:4d} mismatches  "
                f"max|diff| {s['max_abs_diff']:.3e}  -> {'PASS' if s['pass'] else 'FAIL'}"
            )
        else:
            print(f"  {s['section']:44s} {json.dumps({k: v for k, v in s.items() if k != 'section'})}")
    print(f"parity {'PASS' if passed else 'FAIL'} in {out['wall_s']:.1f}s -> {OUT.name}")
    for s in sections:
        for m in s.get("detail", [])[:5]:
            print("   mismatch", m)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--side-by-side-only", action="store_true")
    args = ap.parse_args()
    out = run(quick=args.quick, side_by_side_only=args.side_by_side_only)
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
