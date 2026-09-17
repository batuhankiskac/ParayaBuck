"""Apply the frozen acceptance gates to an existing campaign result."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from research.spy_defensive_overlay_v1.data import FROZEN_HASHES

OUTPUT_DIR = Path(__file__).resolve().parent
ALLOWED_VERDICTS = {
    "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION",
    "RESEARCH_REJECT",
    "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT",
}
ARMS = {"A": "A_incumbent", "B": "B_defensive_cash", "C": "C_defensive_sh"}
RISK_METRICS = ("sharpe", "sortino", "calmar")
DELTA_METRICS = (*RISK_METRICS, "cagr", "max_drawdown", "turnover", "transaction_costs")
EPSILON = 1e-12


def _number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _run(results: dict[str, Any], arm: str, cost: int) -> dict[str, Any]:
    value = results["runs"][f"{ARMS[arm]}_{cost}x"]
    if not isinstance(value, dict):
        raise ValueError("Campaign run must be an object")
    return value


def _period(results: dict[str, Any], arm: str, cost: int, window: str) -> dict[str, Any]:
    value = _run(results, arm, cost)["periods"][window]
    if not isinstance(value, dict):
        raise ValueError("Campaign period must be an object")
    return value


def _metric(results: dict[str, Any], arm: str, cost: int, window: str, metric: str) -> float:
    return _number(_period(results, arm, cost, window)[metric], f"{arm} {cost}x {window} {metric}")


def compute_deltas(results: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for cost in (1, 2, 3):
        cost_rows: dict[str, Any] = {}
        for window in ("full", "development", "validation"):
            comparisons: dict[str, Any] = {}
            for left, right, label in (
                ("B", "A", "B_minus_A"),
                ("C", "A", "C_minus_A"),
                ("C", "B", "C_minus_B"),
            ):
                row = {
                    metric: _metric(results, left, cost, window, metric) - _metric(results, right, cost, window, metric)
                    for metric in DELTA_METRICS
                }
                row["max_drawdown_reduction"] = -row["max_drawdown"]
                comparisons[label] = row
            cost_rows[window] = comparisons
        payload[f"{cost}x"] = cost_rows
    return payload


def _at_least(value: float, threshold: float) -> bool:
    return value + EPSILON >= threshold


def _at_most(value: float, threshold: float) -> bool:
    return value <= threshold + EPSILON


def _opposite_sign(left: float, right: float) -> bool:
    return (left > EPSILON and right < -EPSILON) or (left < -EPSILON and right > EPSILON)


def _core_gates(results: dict[str, Any], deltas: dict[str, Any], cost: int) -> dict[int, dict[str, Any]]:
    cost_delta = deltas[f"{cost}x"]
    full_ca = cost_delta["full"]["C_minus_A"]
    full_cb = cost_delta["full"]["C_minus_B"]
    validation_ca = cost_delta["validation"]["C_minus_A"]
    validation_cb = cost_delta["validation"]["C_minus_B"]
    development_ca = cost_delta["development"]["C_minus_A"]

    a_drawdown = _metric(results, "A", cost, "full", "max_drawdown")
    drawdown_reduction = full_ca["max_drawdown_reduction"]
    relative_reduction = drawdown_reduction / a_drawdown if a_drawdown > 0 else float("-inf")
    gate2 = _at_least(drawdown_reduction, 0.02) and _at_least(relative_reduction, 0.10)

    risk_full = {metric: float(full_ca[metric]) for metric in RISK_METRICS}
    gate3 = max(risk_full.values()) + EPSILON >= 0.10 and min(risk_full.values()) + EPSILON >= -0.05

    full_cagr_drag = -float(full_ca["cagr"])
    validation_cagr_drag = -float(validation_ca["cagr"])
    gate4 = _at_most(full_cagr_drag, 0.02) and _at_most(validation_cagr_drag, 0.03)

    incremental_directions: list[str] = []
    if _at_least(float(full_cb["max_drawdown_reduction"]), 0.01):
        incremental_directions.append("max_drawdown")
    incremental_directions.extend(metric for metric in RISK_METRICS if _at_least(float(full_cb[metric]), 0.05))
    validated_directions = [
        direction
        for direction in incremental_directions
        if (
            _at_least(float(validation_cb["max_drawdown_reduction"]), 0.005)
            if direction == "max_drawdown"
            else _at_least(float(validation_cb[direction]), 0.03)
        )
    ]
    gate5 = bool(validated_directions) and _at_most(-float(full_cb["cagr"]), 0.01)

    qualifying_validation_metrics = [metric for metric in RISK_METRICS if _at_least(float(validation_ca[metric]), 0.05)]
    drawdown_opposite = _opposite_sign(
        float(development_ca["max_drawdown_reduction"]),
        float(validation_ca["max_drawdown_reduction"]),
    )
    direction_consistent = any(
        not (drawdown_opposite and _opposite_sign(float(development_ca[metric]), float(validation_ca[metric])))
        for metric in qualifying_validation_metrics
    )
    gate6 = (
        _at_least(float(validation_ca["max_drawdown_reduction"]), 0.01)
        and bool(qualifying_validation_metrics)
        and gate4
        and gate5
        and direction_consistent
    )
    return {
        2: {
            "passed": gate2,
            "details": {
                "cost_multiplier": cost,
                "drawdown_reduction": drawdown_reduction,
                "relative_drawdown_reduction": relative_reduction,
                "thresholds": {"absolute": 0.02, "relative": 0.10},
            },
        },
        3: {
            "passed": gate3,
            "details": {"cost_multiplier": cost, "risk_adjusted_deltas": risk_full},
        },
        4: {
            "passed": gate4,
            "details": {
                "cost_multiplier": cost,
                "full_cagr_drag": full_cagr_drag,
                "validation_cagr_drag": validation_cagr_drag,
            },
        },
        5: {
            "passed": gate5,
            "details": {
                "cost_multiplier": cost,
                "full_qualifying_directions": incremental_directions,
                "validation_confirmed_directions": validated_directions,
                "full_cagr_drag_vs_cash": -float(full_cb["cagr"]),
            },
        },
        6: {
            "passed": gate6,
            "details": {
                "cost_multiplier": cost,
                "validation_drawdown_reduction": validation_ca["max_drawdown_reduction"],
                "qualifying_validation_metrics": qualifying_validation_metrics,
                "development_validation_direction_consistent": direction_consistent,
                "development_deltas": {
                    "max_drawdown_reduction": development_ca["max_drawdown_reduction"],
                    **{metric: development_ca[metric] for metric in qualifying_validation_metrics},
                },
                "validation_deltas": {
                    "max_drawdown_reduction": validation_ca["max_drawdown_reduction"],
                    **{metric: validation_ca[metric] for metric in qualifying_validation_metrics},
                },
            },
        },
    }


def _verification_status(results: dict[str, Any], verification: dict[str, Any] | None) -> str:
    candidate = verification if verification is not None else results.get("independent_verification")
    if not isinstance(candidate, dict):
        return "MISSING"
    status = candidate.get("status", candidate.get("verdict", candidate.get("result", "MISSING")))
    return str(status).upper()


def _gate1(results: dict[str, Any], verification_status: str) -> dict[str, Any]:
    hashes = results.get("data_hashes")
    hashes_valid = isinstance(hashes, dict) and all(
        hashes.get(path) == digest for path, digest in FROZEN_HASHES.items()
    )
    coverage_valid = True
    coverage_rows = 0
    try:
        for run in results["runs"].values():
            coverage = run["coverage"]
            coverage_rows += 1
            scheduled = int(coverage["scheduled_opportunities"])
            decisions = int(coverage["recorded_decisions"])
            skips = int(coverage["skipped_decisions"])
            skip_rows = coverage["skips"]
            coverage_valid = coverage_valid and bool(coverage["accounting_complete"])
            coverage_valid = coverage_valid and scheduled == decisions + skips == decisions + len(skip_rows)
            coverage_valid = coverage_valid and all(str(row.get("reason", "")).strip() for row in skip_rows)
            coverage_valid = coverage_valid and int(coverage["development_decisions"]) > 0
            coverage_valid = coverage_valid and int(coverage["validation_decisions"]) > 0
    except (KeyError, TypeError, ValueError):
        coverage_valid = False
    coverage_valid = coverage_valid and coverage_rows == 9
    passed = hashes_valid and coverage_valid and verification_status == "PASS"
    return {
        "passed": passed,
        "details": {
            "hashes_valid": hashes_valid,
            "coverage_valid": coverage_valid,
            "coverage_run_count": coverage_rows,
            "independent_verification": verification_status,
        },
    }


def _gate8(results: dict[str, Any]) -> dict[str, Any]:
    rows = _run(results, "C", 1)["capacity"]["rows"]
    if not isinstance(rows, list) or not rows:
        return {"passed": False, "details": {"reason": "no defensive treatment capacity rows"}}
    ratios: list[float] = []
    realized_threshold = 0
    constraints_valid = True
    unused: list[float] = []
    for row in rows:
        intended = _number(row["intended_sh_exposure"], "intended_sh_exposure")
        realized = _number(row["realized_overlay_sh_exposure"], "realized_overlay_sh_exposure")
        unused_value = _number(
            row.get("capacity_loss_unused_equity_allocation", row["unused_equity_allocation"]),
            "capacity_loss_unused_equity_allocation",
        )
        target_weights = row["target_weights"]
        weights = [_number(item[1], "target weight") for item in target_weights]
        gross = sum(weights)
        constraints_valid = constraints_valid and int(row["total_positions"]) <= 6
        constraints_valid = constraints_valid and all(
            _at_most(weight, 0.10) and weight >= -EPSILON for weight in weights
        )
        constraints_valid = constraints_valid and _at_most(gross, 0.55) and _at_most(gross, 0.60)
        constraints_valid = constraints_valid and _at_least(1.0 - gross, 0.40)
        reasons = row.get("capacity_loss_reasons", [])
        constraints_valid = constraints_valid and (unused_value <= EPSILON or bool(reasons))
        unused.append(unused_value)
        if intended > 0:
            ratios.append(realized / intended)
            realized_threshold += int(_at_least(realized, 0.075))
    if not ratios:
        return {"passed": False, "details": {"reason": "no positive intended overlay decisions"}}
    median_ratio = statistics.median(ratios)
    realization_rate = realized_threshold / len(ratios)
    average_unused = statistics.fmean(unused)
    passed = (
        constraints_valid
        and _at_least(median_ratio, 0.75)
        and _at_least(realization_rate, 0.80)
        and _at_most(average_unused, 0.05)
    )
    return {
        "passed": passed,
        "details": {
            "constraints_valid": constraints_valid,
            "positive_intended_decisions": len(ratios),
            "median_realized_to_intended_ratio": median_ratio,
            "realized_at_least_0p075_rate": realization_rate,
            "average_unused_equity_allocation": average_unused,
        },
    }


def evaluate_campaign(
    results: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verification_status = _verification_status(results, verification)
    gate1 = _gate1(results, verification_status)
    try:
        deltas = compute_deltas(results)
        one_x = _core_gates(results, deltas, 1)
        two_x = _core_gates(results, deltas, 2)
        three_x = deltas["3x"]
        three_full_ca = three_x["full"]["C_minus_A"]
        three_full_cb = three_x["full"]["C_minus_B"]
        three_passed = (
            _at_least(float(three_full_ca["max_drawdown_reduction"]), 0.0)
            and _at_most(-float(three_full_ca["cagr"]), 0.04)
            and not all(float(three_full_cb[metric]) < -EPSILON for metric in RISK_METRICS)
        )
        gate7 = {
            "passed": all(two_x[index]["passed"] for index in range(2, 7)) and three_passed,
            "details": {
                "two_x_gates_2_to_6": {str(index): two_x[index] for index in range(2, 7)},
                "three_x": {
                    "passed": three_passed,
                    "drawdown_reduction_vs_A": three_full_ca["max_drawdown_reduction"],
                    "cagr_drag_vs_A": -float(three_full_ca["cagr"]),
                    "risk_adjusted_deltas_vs_B": {metric: three_full_cb[metric] for metric in RISK_METRICS},
                },
            },
        }
        gate8 = _gate8(results)
        gates = {
            "1": {"name": "replay_data_validity", **gate1},
            **{
                str(index): {
                    "name": {
                        2: "drawdown_objective",
                        3: "risk_adjusted_improvement",
                        4: "return_cost",
                        5: "sh_incremental_value",
                        6: "validation",
                    }[index],
                    **one_x[index],
                }
                for index in range(2, 7)
            },
            "7": {"name": "cost_robustness", **gate7},
            "8": {"name": "deployability", **gate8},
        }
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        deltas = {}
        gates = {"1": {"name": "replay_data_validity", **gate1}}
        for index, name in (
            (2, "drawdown_objective"),
            (3, "risk_adjusted_improvement"),
            (4, "return_cost"),
            (5, "sh_incremental_value"),
            (6, "validation"),
            (7, "cost_robustness"),
            (8, "deployability"),
        ):
            gates[str(index)] = {"name": name, "passed": False, "details": {"not_evaluated": str(exc)}}
        gates["1"]["passed"] = False
        gates["1"]["details"]["result_schema_valid"] = False

    if not gates["1"]["passed"]:
        verdict = "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    elif all(gate["passed"] for gate in gates.values()):
        verdict = "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    else:
        verdict = "RESEARCH_REJECT"
    if verdict not in ALLOWED_VERDICTS:  # pragma: no cover - invariant
        raise AssertionError("Invalid research verdict")
    gate_table = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "deltas": deltas,
        "gates": gates,
    }
    decision = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "verdict": verdict,
        "all_gates_passed": all(gate["passed"] for gate in gates.values()),
        "failed_gates": [int(index) for index, gate in gates.items() if not gate["passed"]],
        "independent_verification": verification_status,
    }
    return gate_table, decision


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def evaluate_files(
    results_path: str | Path = OUTPUT_DIR / "campaign_results.json",
    *,
    verification_path: str | Path | None = None,
    output_dir: str | Path = OUTPUT_DIR,
) -> dict[str, Any]:
    source = Path(results_path)
    results = _read_object(source)
    candidate_verification = (
        Path(verification_path) if verification_path is not None else source.with_name("independent_verification.json")
    )
    verification = _read_object(candidate_verification) if candidate_verification.exists() else None
    gate_table, decision = evaluate_campaign(results, verification)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "gate_table.json").write_text(
        json.dumps(gate_table, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (destination / "decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default=str(OUTPUT_DIR / "campaign_results.json"))
    parser.add_argument("--verification")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args(argv)
    decision = evaluate_files(args.results, verification_path=args.verification, output_dir=args.output_dir)
    print(decision["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
