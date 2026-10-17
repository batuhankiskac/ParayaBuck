"""research/closeout_v1/ib38_report.py -- IB38/A (the regime matrix) and IB38/B (the split), outside
the ledger.

Both are **report-only** arms of `idea_bank_v1`: their variation is not a target-weight rule but the
*account* the same rule is run in (the tax regime, the capital, how many taxpayers) -- which the
plugin contract cannot express (`research/ideas/ib38.py`'s two `needs_kernel` reasons, quoted in
`PREREGISTRATION.md` §3). They are computed here with the engine's own after-tax machinery
(`fe_study` / `fe_tax` / `fe_sim.simulate`), are **not** registered as trials, are not gated and do
not enter the deflation.

    IB38/A  {R2 -> C0, A1u, H2, K} x {pre-tax (tau = 0), flat 20 %, progressive 2026}
            x {$10k, $100k, $1M} on the framework's three after-tax `TAX_WINDOWS`, plus the
            published main-window pre-tax row (base and calibrated) as the tau = 0 anchor.
            The bank's "six windows" cannot carry a tax regime: only the three `TAX_WINDOWS` have
            the TRY/YI-UFE calendar (the bank's own data note says so). Reduced set, pre-registered.
    IB38/B  K in two $50k accounts and three $33.33k accounts, one Turkish taxpayer.
            The engine's progressive mode applies `fe_tax.tariff_tax` to the *taxpayer's* annual
            net TRY gain (`fe_sim._assess`, `YR_DUE`), so with one taxpayer the accounts' annual
            bases aggregate -- the household's base per year is the sum, i.e. the control's own
            base, and the household outcome is exactly K at $100k (Delta = 0). The check that makes
            that an argument rather than an assertion is the pre-tax scale invariance
            (`nav@50k == nav@100k / 2`), so the two halves' bases really do sum to the whole.
            The separate-ladder variant (the bank's "multiplies the progressive ladder" arithmetic,
            what two independent *taxpayers* would pay) is reported from the engine's own runs at
            $50k and $33.33k, with the ladder's size read off the per-year assessed tax.

Writes `research/closeout_v1/ib38_report.json` and prints the two tables.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import FIN, GATE_CAPITAL, START, TAX_CAPITALS, TAX_WINDOWS
from research.fast_engine.fe_tax import MODES

HERE = Path(__file__).resolve().parent
OUT = HERE / "ib38_report.json"

#: the four execution layers the matrix varies (R2 = the same target series under C0's plain band)
RULES = ("R2", "A1u", "H2", "K")
#: the bank's three capitals for the matrix (a subset of `TAX_CAPITALS`)
CAPS = {"10k": 10_000.0, "100k": 100_000.0, "1M": 1_000_000.0}
#: the bank's two splits
SPLITS = {"two_50k": (2, 50_000.0), "three_33333k": (3, 100_000.0 / 3.0)}


def _cell(r: dict) -> dict:
    yrs = float(r["years"])
    return {
        # the campaign's two conventions: pre-tax rows are quoted as the un-liquidated `cagr`
        # (the published pre-tax cells), taxed rows as `cagr_usd_liquidated`
        "cagr_pct": float(r["cagr_usd_liquidated"]) * 100.0,
        "cagr_pre_liquidation_pct": float(r["cagr_usd_unliquidated"]) * 100.0,
        "max_dd_pct": float(r["max_dd_nav_usd"]) * 100.0,
        "terminal_usd": float(r["terminal_usd_liquidated"]),
        "tax_usd": float(r["tax_total_usd_at_payment_fx"]),
        "sells_per_year": float(r["sells"]) / yrs,
        "trades_per_year": float(r["strategy_trades"]) / yrs,
    }


def matrix() -> dict:
    """IB38/A: the regime switch table (after-tax windows) + the main-window pre-tax anchor."""
    days, o, c, _w_e2, w_r2, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    out: dict = {"windows": {}, "main_window_pre_tax": {}, "capitals": sorted(CAPS)}

    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        out["windows"][wn] = {"range": [days[i0], days[i1]], "cells": {}}
        for cap, capital in CAPS.items():
            cells = {}
            for mode in MODES:
                for arm in RULES:
                    r = fe_sim.simulate(o, c, w_r2, env, capital, i0, i1, arm=arm, mode=mode, m=2)
                    cells[f"{mode}|{arm}"] = _cell(r)
            out["windows"][wn]["cells"][cap] = cells

    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    for md, spread in FIN.items():
        _d, o_, c_, _we, wr_, _e = fe_data.main_inputs(spread)
        out["main_window_pre_tax"][md] = {
            arm: _cell(fe_sim.simulate(o_, c_, wr_, env, TAX_CAPITALS[GATE_CAPITAL], START, T,
                                       arm=arm, mode="pre_tax", m=2))
            for arm in RULES
        }
    return out


def split() -> dict:
    """IB38/B: one taxpayer's aggregate vs the separate-ladder arithmetic."""
    days, o, c, _w_e2, w_r2, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    out: dict = {"splits": {}, "scale_invariance": {}}

    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = fe_study.window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        # the aggregate case is the control's own run at $100k (one taxpayer's annual base)
        agg = fe_sim.simulate(o, c, w_r2, env, CAPS["100k"], i0, i1, arm="K",
                              mode="progressive", m=2)
        # the scale-invariance argument: the same rule pre-tax at $50k is $100k's path halved
        n50 = fe_sim.simulate(o, c, w_r2, env, 50_000.0, i0, i1, arm="K", mode="pre_tax", m=2)["_nav"]
        n100 = fe_sim.simulate(o, c, w_r2, env, CAPS["100k"], i0, i1, arm="K",
                               mode="pre_tax", m=2)["_nav"]
        rel = float(np.max(np.abs(2.0 * np.asarray(n50) - np.asarray(n100))
                           / np.maximum(np.abs(np.asarray(n100)), 1e-12)))
        out["scale_invariance"][wn] = {"max_rel_diff_2x50k_vs_100k": rel, "ok": bool(rel < 1e-9)}
        cells = {"aggregate_one_taxpayer": {"capital_per_account": CAPS["100k"],
                                            "accounts": 1, **_cell(agg)}}
        for name, (n_acct, cap) in SPLITS.items():
            r = fe_sim.simulate(o, c, w_r2, env, cap, i0, i1, arm="K", mode="progressive", m=2)
            household = _cell(r)
            household["accounts"] = int(n_acct)
            household["capital_per_account"] = float(cap)
            household["household_terminal_usd"] = float(r["terminal_usd_liquidated"]) * n_acct
            household["household_cagr_pct"] = household["cagr_pct"]
            household["delta_vs_K_100k_pp"] = household["cagr_pct"] - _cell(agg)["cagr_pct"]
            household["household_tax_usd_separate"] = float(r["tax_total_usd_at_payment_fx"]) * n_acct
            cells[name] = household
        cells["aggregate_one_taxpayer"]["delta_vs_K_100k_pp"] = 0.0
        cells["aggregate_one_taxpayer"]["household_tax_usd_aggregate"] = _cell(agg)["tax_usd"]
        # the ladder's size, per year: the tariff on the sum against the sum of the tariffs
        per_year = {}
        r50 = fe_sim.simulate(o, c, w_r2, env, 50_000.0, i0, i1, arm="K", mode="progressive", m=2)
        t100 = agg["tax_assessed_try_by_year"]
        t50 = r50["tax_assessed_try_by_year"]
        for y in sorted(set(t100) | set(t50)):
            a100, a50 = float(t100.get(y, 0.0)), float(t50.get(y, 0.0))
            if a100 > 0 or a50 > 0:
                per_year[str(y)] = {"assessed_100k_try": a100, "assessed_2x50k_try": 2.0 * a50,
                                    "ratio": (a100 / (2.0 * a50)) if a50 > 0 else None}
        cells["ladder_per_year"] = per_year
        cells["ladder_ratio_mean"] = float(np.mean([v["ratio"] for v in per_year.values()
                                                    if v["ratio"]]) or 1.0)
        cells["tax_total_try_100k"] = float(agg["tax_total_try"])
        cells["tax_total_try_2x50k"] = float(r50["tax_total_try"]) * 2.0
        out["splits"][wn] = cells
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.closeout_v1.ib38_report")
    ap.add_argument("--run", action="store_true", help="compute and write ib38_report.json")
    args = ap.parse_args(argv)
    rep = {"study": "closeout_v1", "arm": "IB38", "registered": False,
           "kind": "report-only (not a trial, outside the gates)",
           "A_regime_matrix": matrix(), "B_split": split()}
    if args.run:
        OUT.write_text(json.dumps(rep, indent=1, sort_keys=True))
        print(f"wrote {OUT}")
    for wn, blk in rep["A_regime_matrix"]["windows"].items():
        print(f"\nIB38/A -- {wn} ({blk['range'][0]} -> {blk['range'][1]}), CAGR % liquidated, m = 2")
        print(f"  {'mode':<11} {'cap':>6}  " + "  ".join(f"{r:>7}" for r in RULES)
              + "   |  max DD % (same mode)")
        for mode in MODES:
            for cap in sorted(blk["cells"]):
                c = blk["cells"][cap]
                key = "cagr_pre_liquidation_pct" if mode == "pre_tax" else "cagr_pct"
                line = "  ".join(f"{c[f'{mode}|{r}'][key]:7.2f}" for r in RULES)
                dd = "  ".join(f"{c[f'{mode}|{r}']['max_dd_pct']:7.2f}" for r in RULES)
                print(f"  {mode:<11} {cap:>6}  {line}   |  {dd}")
    print("\nIB38/A -- main window 2000-03-09 -> 2026-09-29, pre-tax (tau = 0, un-liquidated), m = 2")
    for md, blk in rep["A_regime_matrix"]["main_window_pre_tax"].items():
        line = "  ".join(f"{blk[r]['cagr_pre_liquidation_pct']:7.2f}" for r in RULES)
        dd = "  ".join(f"{blk[r]['max_dd_pct']:7.2f}" for r in RULES)
        print(f"  {md:<11} {'':>5}  {line}   |  {dd}")
    for wn, blk in rep["B_split"]["splits"].items():
        print(f"\nIB38/B -- {wn}")
        for k in ("aggregate_one_taxpayer", "two_50k", "three_33333k"):
            v = blk[k]
            print(f"  {k:>22}  CAGR {v['cagr_pct']:6.2f}%  delta vs K@100k "
                  f"{v.get('delta_vs_K_100k_pp', 0.0):+.2f} pp  accounts {v['accounts']}")
        si = rep["B_split"]["scale_invariance"][wn]
        print(f"  scale invariance: max rel diff {si['max_rel_diff_2x50k_vs_100k']:.2e} "
              f"(ok {si['ok']})")
        print(f"  ladder: tax(100k) {blk['tax_total_try_100k']:.0f} TRY vs 2x tax(50k) "
              f"{blk['tax_total_try_2x50k']:.0f} TRY  (mean per-year ratio "
              f"{blk['ladder_ratio_mean']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
