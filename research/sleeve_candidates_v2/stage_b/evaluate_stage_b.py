#!/usr/bin/env python3
"""Evaluate the frozen sleeve candidates v2 Stage B gates from shared-ledger runs.

    .venv/bin/python research/sleeve_candidates_v2/stage_b/evaluate_stage_b.py \
        --out research/sleeve_candidates_v2/stage_b/RESULTS_STAGE_B.json
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

from evaluate import DEVELOPMENT, FULL, STRESS_2022, VALIDATION, _metrics  # noqa: E402

RUNS = HERE / "runs"
ARCHIVE = RUNS / "v1_archive"
MULTIPLIERS = ("1.0", "2.0", "3.0")
FAMILIES = {
    "v2": {
        "risk_parity_v1": "S",
        "defensive_sectors_v1": "S",
        "tbill_sweep_v1": "X",
        # Declared post-hoc extension: added after defensive_sectors_v1 failed S1
        # (its ETFs overlap V3's own universe); next Stage A passers by Sharpe.
        "defensive_factor_v1": "S",
        "global_equity_v1": "S",
    },
    "v3": {
        "gold_v1": "S",
        "dividend_growth_v1": "S",
        # Declared post-hoc structural variant: gold_v1 cannot pass S1 (IAU is
        # inside V3's observed universe); OUNZ replaces IAU, exposure unchanged.
        "gold_v1b": "S",
    },
}
MAXIMUM_SLOTS = 8
MAXIMUM_GROSS = 0.60
TRUE_CASH_FLOOR = 0.20


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _returns(run: dict) -> list[tuple[date, float]]:
    rows = [(datetime.fromisoformat(ts).date(), float(nav)) for ts, nav in run["session_nav"]]
    return [(rows[i][0], rows[i][1] / rows[i - 1][1] - 1.0) for i in range(1, len(rows))]


def _summary(run: dict) -> dict:
    series = _returns(run)
    return {
        "full": _metrics(series, FULL),
        "development": _metrics(series, DEVELOPMENT),
        "validation": _metrics(series, VALIDATION),
        "year_2022": _metrics(series, STRESS_2022),
        "ending_nav": run["ending_nav"],
        "structural": run["structural"],
        "etf_pnl": run.get("etf_pnl", {}),
    }


def _accounting(run: dict) -> bool:
    inv = run["invariants"]
    return bool(
        inv["cash_conserved"]
        and inv["nav_matches_ledger"]
        and inv["position_quantities_reconciled"]
        and inv["duplicate_fill_count"] == 0
        and inv["unowned_fill_count"] == 0
        and inv["no_future_data_access"]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--campaign", default="v2", choices=tuple(FAMILIES))
    args = parser.parse_args()
    FAMILY = FAMILIES[args.campaign]
    candidate_runs = HERE.parents[1] / f"sleeve_candidates_{args.campaign}/stage_b/runs"

    fresh_a = _load(RUNS / "A_1.0.json.gz")
    archived = {f"{arm}|{m}": _load(ARCHIVE / f"{arm}_{m}.json.gz") for arm in ("A", "B") for m in MULTIPLIERS}
    reproduction = (
        fresh_a["hashes"] == archived["A|1.0"]["hashes"] and fresh_a["ending_nav"] == archived["A|1.0"]["ending_nav"]
    )
    controls = {key: _summary(run) for key, run in archived.items()}
    a, b = controls["A|1.0"], controls["B|1.0"]

    results: dict[str, object] = {
        "reproduction_of_v1_arm_A": reproduction,
        "controls": controls,
        "candidates": {},
    }
    for name, family in FAMILY.items():
        invariance_run = _load(candidate_runs / f"{name}_I_1.0.json.gz")
        runs = {m: _load(candidate_runs / f"{name}_C_{m}.json.gz") for m in MULTIPLIERS}
        arms = {m: _summary(run) for m, run in runs.items()}
        c = arms["1.0"]
        structural = {
            "S0_v1_reproduction": reproduction,
            "S1_invariance": invariance_run["hashes"] == fresh_a["hashes"]
            and invariance_run["ending_nav"] == fresh_a["ending_nav"],
            "S2_accounting": all(_accounting(run) for run in (*runs.values(), invariance_run)),
            "S3_non_sleeve_slots": all(
                run["structural"]["max_non_etf_slot_claims_in_violations"] <= MAXIMUM_SLOTS for run in runs.values()
            ),
            "S4_fills_after_creation": all(run["structural"]["fills_after_creation"] for run in runs.values()),
        }
        if family == "S":
            pnl = c["etf_pnl"]
            absolute_total = sum(abs(value) for value in pnl.values())
            largest_share = max(abs(value) for value in pnl.values()) / absolute_total if absolute_total else 1.0
            economic = {
                "G2_full_sharpe_and_drawdown": c["full"]["sharpe"] >= a["full"]["sharpe"] + 0.05
                and c["full"]["max_drawdown"] <= a["full"]["max_drawdown"],
                "G3_cagr_guard": c["full"]["cagr"] >= a["full"]["cagr"] - 0.005,
                "G4_validation": c["validation"]["sharpe"] >= a["validation"]["sharpe"]
                and c["validation"]["max_drawdown"] <= a["validation"]["max_drawdown"] + 0.01,
                "G5_development": c["development"]["sharpe"] >= a["development"]["sharpe"] - 0.05,
                "G6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"],
                "G7_costs_3x": arms["3.0"]["full"]["sharpe"] >= controls["A|3.0"]["full"]["sharpe"],
                "G8_concentration": largest_share <= 0.60,
                "G9a_sharpe_vs_cash": c["full"]["sharpe"] >= b["full"]["sharpe"] + 0.05,
                "G9b_calmar_vs_cash": c["full"]["calmar"] >= b["full"]["calmar"],
                "G9c_validation_sharpe_vs_cash": c["validation"]["sharpe"] >= b["validation"]["sharpe"],
            }
        else:
            largest_share = None
            structural["SX_non_sleeve_target_gross"] = all(
                run["structural"]["maximum_non_sleeve_target_gross"] <= MAXIMUM_GROSS + 1e-9 for run in runs.values()
            )
            structural["SX_true_cash_floor"] = all(
                run["structural"]["maximum_total_target_gross"] <= 1.0 - TRUE_CASH_FLOOR + 1e-9 for run in runs.values()
            )
            economic = {
                "XG2_cagr_uplift": c["full"]["cagr"] >= a["full"]["cagr"] + 0.0025,
                "XG3_drawdown": c["full"]["max_drawdown"] <= a["full"]["max_drawdown"] + 0.0025,
                "XG4_sharpe": c["full"]["sharpe"] >= a["full"]["sharpe"],
                "XG5_validation_cagr": c["validation"]["cagr"] >= a["validation"]["cagr"],
                "XG6_2022": c["year_2022"]["total_return"] >= a["year_2022"]["total_return"] - 0.0025,
                "XG7_costs_3x": arms["3.0"]["full"]["cagr"] >= controls["A|3.0"]["full"]["cagr"],
            }
        gates = {**structural, **economic}
        results["candidates"][name] = {
            "family": family,
            "arms": arms,
            "gates": gates,
            "passed": all(gates.values()),
            "largest_etf_pnl_share": largest_share,
        }
    results["verdict"] = {name: entry["passed"] for name, entry in results["candidates"].items()}
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"reproduction_of_v1_arm_A={reproduction}")
    print(f"{'arm':28} {'total':>7} {'cagr':>7} {'sharpe':>7} {'maxdd':>7} {'calmar':>7} {'valSh':>7} {'2022':>7}")
    rows = [("A", a), ("B", b)] + [(f"{name}", results["candidates"][name]["arms"]["1.0"]) for name in FAMILY]
    for label, row in rows:
        print(
            f"{label:28} {row['full']['total_return']:7.3f} {row['full']['cagr']:7.4f} {row['full']['sharpe']:7.3f} "
            f"{row['full']['max_drawdown']:7.4f} {row['full']['calmar']:7.3f} {row['validation']['sharpe']:7.3f} "
            f"{row['year_2022']['total_return']:7.4f}"
        )
    for name, entry in results["candidates"].items():
        failed = [gate for gate, ok in entry["gates"].items() if not ok]
        print(f"{name:28} pass={entry['passed']} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
