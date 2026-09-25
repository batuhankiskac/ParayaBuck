#!/usr/bin/env python3
"""Evaluate the frozen Stage B gates from shared-ledger run outputs.

    .venv/bin/python research/etf_diversifier_v1/stage_b/evaluate_stage_b.py \
        --out research/etf_diversifier_v1/stage_b/RESULTS_STAGE_B.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from evaluate import DEVELOPMENT, FULL, STRESS_2022, VALIDATION, _drawdown_days_over, _metrics  # noqa: E402

RUNS = HERE / "runs"
ARMS = ("A", "B", "C1", "C2")
CANDIDATES = ("C1", "C2")
MULTIPLIERS = ("1.0", "2.0", "3.0")
MAXIMUM_SLOTS = 8


def _load(name: str) -> dict:
    with gzip.open(RUNS / f"{name}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _returns(run: dict) -> list[tuple[date, float]]:
    rows = [(datetime.fromisoformat(ts).date(), float(nav)) for ts, nav in run["session_nav"]]
    if len({day for day, _ in rows}) != len(rows):
        raise ValueError(f"duplicate session dates in {run['arm']}")
    return [(rows[i][0], rows[i][1] / rows[i - 1][1] - 1.0) for i in range(1, len(rows))]


def _structural(run: dict) -> dict[str, bool]:
    inv, structural = run["invariants"], run["structural"]
    return {
        "S2_accounting": bool(
            inv["cash_conserved"]
            and inv["nav_matches_ledger"]
            and inv["position_quantities_reconciled"]
            and inv["duplicate_fill_count"] == 0
            and inv["unowned_fill_count"] == 0
            and inv["no_future_data_access"]
        ),
        "S3_non_etf_slots": structural["max_non_etf_slot_claims_in_violations"] <= MAXIMUM_SLOTS,
        "S4_fills_after_creation": bool(structural["fills_after_creation"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    runs = {f"{arm}|{m}": _load(f"{arm}_{m}") for arm in ARMS for m in MULTIPLIERS}
    invariance = _load("I1_1.0")
    s1 = invariance["hashes"] == runs["A|1.0"]["hashes"] and invariance["ending_nav"] == runs["A|1.0"]["ending_nav"]

    results: dict[str, object] = {"arms": {}, "structural": {}, "gates": {}}
    sessions = None
    for key, run in runs.items():
        series = _returns(run)
        days = [day for day, _ in series]
        if sessions is None:
            sessions = days
        elif days != sessions:
            raise ValueError(f"session calendar mismatch for {key}")
        results["arms"][key] = {
            "full": _metrics(series, FULL),
            "development": _metrics(series, DEVELOPMENT),
            "validation": _metrics(series, VALIDATION),
            "year_2022": _metrics(series, STRESS_2022),
            "days_in_drawdown_over_5pct": _drawdown_days_over(series, 0.05),
            "ending_nav": run["ending_nav"],
            "fills": run["counts"]["fills"],
            "costs": run["costs"],
            "etf_decision_count": run["structural"]["etf_decision_count"],
            "halt_counts": run["structural"]["halt_counts"],
            "slot_claim_violation_events": run["structural"]["slot_claim_violation_events"],
            "max_observed_slot_claim_count": run["invariants"]["max_observed_slot_claim_count"],
            "maximum_realized_gross_observed": run["invariants"]["maximum_realized_gross_observed"],
        }
        results["structural"][key] = _structural(run)
    structural_ok = s1 and all(all(gates.values()) for gates in results["structural"].values())
    results["structural_summary"] = {"S1_invariance": s1, "all_structural_gates": structural_ok}

    arms = results["arms"]
    a, b = arms["A|1.0"], arms["B|1.0"]
    for candidate in CANDIDATES:
        c = arms[f"{candidate}|1.0"]
        pnl = runs[f"{candidate}|1.0"]["etf_pnl"]
        absolute_total = sum(abs(value) for value in pnl.values())
        largest_share = max(abs(value) for value in pnl.values()) / absolute_total if absolute_total else 1.0
        gates = {
            "S1_to_S4_structural": structural_ok,
            "G2_full_sharpe_and_drawdown": c["full"]["sharpe"] >= a["full"]["sharpe"] + 0.05
            and c["full"]["max_drawdown"] <= a["full"]["max_drawdown"],
            "G3_cagr_guard": c["full"]["cagr"] >= a["full"]["cagr"] - 0.005,
            "G4_validation": c["validation"]["sharpe"] >= a["validation"]["sharpe"]
            and c["validation"]["max_drawdown"] <= a["validation"]["max_drawdown"] + 0.01,
            "G5_development": c["development"]["sharpe"] >= a["development"]["sharpe"] - 0.05,
            "G6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"],
            "G7_costs_3x": arms[f"{candidate}|3.0"]["full"]["sharpe"] >= arms["A|3.0"]["full"]["sharpe"],
            "G8_concentration": largest_share <= 0.60,
            "G9a_sharpe_vs_cash": c["full"]["sharpe"] >= b["full"]["sharpe"] + 0.05,
            "G9b_calmar_vs_cash": c["full"]["calmar"] >= b["full"]["calmar"],
            "G9c_validation_sharpe_vs_cash": c["validation"]["sharpe"] >= b["validation"]["sharpe"],
        }
        results["gates"][candidate] = {
            "gates": gates,
            "passed": all(gates.values()),
            "largest_etf_pnl_share": largest_share,
            "etf_pnl": pnl,
        }
    passing = [name for name in CANDIDATES if results["gates"][name]["passed"]]
    results["verdict"] = {
        "stage_b_pass": passing,
        "preferred": max(passing, key=lambda name: arms[f"{name}|1.0"]["full"]["sharpe"]) if passing else None,
        "inconclusive": not structural_ok,
    }
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        key: {metric: round(arms[key]["full"][metric], 4) for metric in ("sharpe", "cagr", "max_drawdown", "calmar")}
        | {"validation_sharpe": round(arms[key]["validation"]["sharpe"], 4)}
        for key in arms
    }
    print(json.dumps({"summary": summary, "structural": results["structural_summary"]}, indent=2))
    print(json.dumps({"gates": results["gates"], "verdict": results["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
