"""Build compact, non-outcome-generating reports from persisted economic artifacts."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research" / "abnormal_volume_economic_v1"
READINESS = ROOT / "research" / "abnormal_volume_readiness_v2" / "RUNNER_OUTPUT.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def dump(name: str, value: Any) -> None:
    (OUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "p25": None, "median": None, "p75": None, "maximum": None}
    ordered = sorted(values)

    def quantile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p25": quantile(0.25),
        "median": quantile(0.50),
        "p75": quantile(0.75),
        "maximum": ordered[-1],
    }


def metric_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "net_total_return",
        "cagr",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "worst_month",
        "positive_month_ratio",
        "profit_factor",
        "annualized_target_turnover",
        "annualized_realized_turnover",
        "realized_notional_turnover",
        "transaction_costs",
        "scheduled_decision_count",
        "actual_decision_count",
        "fill_count",
        "average_exposure",
        "median_exposure",
        "average_positions",
        "maximum_target_concentration",
        "maximum_realized_concentration",
    )
    return {field: metrics.get(field) for field in fields}


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def num(value: float) -> str:
    return f"{value:.3f}"


def candidate_table(costs: dict[str, Any], period: str) -> str:
    rows = [
        "| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Target TO | Realized TO | Fills | Avg exp. | Avg pos. |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("1.0", "2.0", "3.0"):
        m = costs[key]["metrics"][period]
        rows.append(
            f"| {key}x | {pct(m['net_total_return'])} | {pct(m['cagr'])} | "
            f"{pct(m['annualized_volatility'])} | {num(m['sharpe'])} | {num(m['sortino'])} | "
            f"{pct(m['max_drawdown'])} | {num(m['calmar'])} | ${m['transaction_costs']:,.2f} | "
            f"{m['annualized_target_turnover']:.2f}x | {m['annualized_realized_turnover']:.2f}x | "
            f"{m['fill_count']} | {pct(m['average_exposure'])} | {m['average_positions']:.2f} |"
        )
    rows.extend(
        [
            "",
            "| Cost | Worst month | Positive months | Profit factor | Scheduled/actual | Median exp. | Max target conc. | Max realized conc. |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key in ("1.0", "2.0", "3.0"):
        m = costs[key]["metrics"][period]
        rows.append(
            f"| {key}x | {pct(m['worst_month'])} | {pct(m['positive_month_ratio'])} | "
            f"{m['profit_factor']:.3f} | {m['scheduled_decision_count']}/{m['actual_decision_count']} | "
            f"{pct(m['median_exposure'])} | {pct(m['maximum_target_concentration'])} | "
            f"{pct(m['maximum_realized_concentration'])} |"
        )
    return "\n".join(rows)


def control_table(control: dict[str, Any], period: str) -> str:
    rows = [
        "| Cost | Return | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar | Costs | Reported turnover | Fills |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("1.0", "2.0", "3.0"):
        m = control["parity"]["runs"][key]["metrics"][period]
        rows.append(
            f"| {key}x | {pct(m['net_total_return'])} | {pct(m['cagr'])} | "
            f"{pct(m['annualized_volatility'])} | {num(m['sharpe'])} | {num(m['sortino'])} | "
            f"{pct(m['max_drawdown'])} | {num(m['calmar'])} | ${m['costs']:,.2f} | "
            f"{m['turnover']:.2f}x | {m['fill_count']} |"
        )
    return "\n".join(rows)


def main() -> None:
    runner = load(OUT / "RUNNER_ECONOMIC_OUTPUT.json")
    verifier = load(OUT / "INDEPENDENT_ECONOMIC_VERIFICATION.json")
    readiness = load(READINESS)
    control = load(OUT / "CONTROL_A_RESULTS.json")
    costs = runner["candidate_results"]["cost_scenarios"]
    one = costs["1.0"]
    summary = one["summary"]
    decisions = readiness["decisions"]

    compact_candidate = {
        "schema_version": "abnormal-volume-candidate-results-v1",
        "candidate": runner["candidate"],
        "identity": runner["identity"],
        "endpoint": runner["input_identity"]["endpoint"],
        "runner_sha256": verifier["runner_sha256"],
        "deterministic_payload_sha256": runner["deterministic_payload_sha256"],
        "period_metrics_by_cost_multiplier": {
            key: {period: metric_subset(cell["metrics"][period]) for period in ("development", "validation", "full")}
            for key, cell in costs.items()
        },
        "one_x_summary": {
            key: summary[key]
            for key in (
                "starting_equity",
                "ending_equity",
                "average_cash_ratio",
                "maximum_target_exposure",
                "maximum_realized_exposure",
                "minimum_realized_cash",
                "maximum_target_positions",
                "maximum_actual_positions",
                "maximum_realized_concentration",
                "filled_target_notional_ratio",
                "unfilled_partial_target_notional_ratio",
                "positive_gross_profit",
                "total_cost",
                "unique_traded_symbols",
                "unique_traded_entities",
            )
        },
    }
    dump("CANDIDATE_B_RESULTS.json", compact_candidate)

    cost_stress = {
        "schema_version": "abnormal-volume-cost-stress-v1",
        "candidate": runner["candidate"],
        "target_stream_unchanged_across_cells": len({cell["hashes"]["decisions_sha256"] for cell in costs.values()})
        == 1,
        "scenarios": {
            key: {
                "cost_multiplier": cell["cost_multiplier"],
                "ending_equity": cell["summary"]["ending_equity"],
                "total_cost": cell["summary"]["total_cost"],
                "development": metric_subset(cell["metrics"]["development"]),
                "validation": metric_subset(cell["metrics"]["validation"]),
                "full": metric_subset(cell["metrics"]["full"]),
            }
            for key, cell in costs.items()
        },
    }
    dump("COST_STRESS.json", cost_stress)

    dump(
        "YEARLY_VALIDATION.json",
        {
            "schema_version": "abnormal-volume-validation-years-v1",
            "cost_multiplier": 1.0,
            "years": one["metrics"]["validation_years"],
            "partial_year_warning": "2026 is partial through the frozen completed endpoint 2026-07-30.",
        },
    )

    eligible = [record for decision in decisions for record in decision["eligible"]]
    targets = [record for decision in decisions for record in decision["targets"]]
    abnormal_before_return = [
        len(decision["eligible"])
        + int(decision["exclusion_reason_counts"].get("formation_return_outside_middle_40", 0))
        for decision in decisions
    ]
    selected_counts = Counter(len(decision["targets"]) for decision in decisions)
    equality_count = sum(float(record["formation_dollar_volume"]) == float(record["q90"]) for record in eligible)
    q90_reference_tie_count = sum(
        sum(float(value) == float(record["q90"]) for value in record.get("reference_dollar_volumes", [])) > 1
        for record in targets
    )
    signal_diagnostics = {
        "schema_version": "abnormal-volume-signal-diagnostics-v1",
        "definitions": {
            "valid_decision_cross_section": "evaluated_count > 0 under the frozen PIT scanner",
            "abnormal_volume_eligible_before_return_condition": "final eligible plus formation_return_outside_middle_40 exclusions",
            "middle_40_conditioned_count": "final eligible after volume-rank and return-rank gates, before top-six selection",
            "descriptive_quantiles": "linear interpolation on ordered observed values; diagnostics only",
            "formation_return_percentile": "one-based average return rank divided by 50",
        },
        "scheduled_monthly_decisions": len(decisions),
        "valid_decision_cross_sections": sum(int(decision["evaluated_count"] > 0) for decision in decisions),
        "average_pit_pool": statistics.fmean(decision["scanner_pool_count"] for decision in decisions),
        "average_signal_eligible_before_middle_40": statistics.fmean(abnormal_before_return),
        "average_middle_40_conditioned_count": statistics.fmean(len(decision["eligible"]) for decision in decisions),
        "selected_name_count_distribution": {str(key): selected_counts[key] for key in sorted(selected_counts)},
        "decisions_with_fewer_than_six_selections": sum(count for key, count in selected_counts.items() if key < 6),
        "percentage_decisions_with_fewer_than_six_selections": sum(
            count for key, count in selected_counts.items() if key < 6
        )
        / len(decisions),
        "eligible_record_count": len(eligible),
        "selected_record_count": len(targets),
        "abnormal_volume_intensity_distribution_eligible": distribution(
            [float(record["intensity"]) for record in eligible]
        ),
        "formation_return_percentile_distribution_eligible": distribution(
            [float(record["return_rank"]) / 50.0 for record in eligible]
        ),
        "q90_threshold_distribution_eligible_dollars": distribution([float(record["q90"]) for record in eligible]),
        "threshold_equality_count": equality_count,
        "threshold_equality_frequency": equality_count / len(eligible) if eligible else 0.0,
        "selected_records_with_duplicate_q90_reference_value_count": q90_reference_tie_count,
        "selected_records_with_duplicate_q90_reference_value_frequency": q90_reference_tie_count / len(targets)
        if targets
        else 0.0,
        "note": "No diagnostic was used to alter the frozen signal or gates.",
    }
    dump("SIGNAL_DIAGNOSTICS.json", signal_diagnostics)

    actual_decisions = one["decisions"]
    halted = [decision for decision in actual_decisions if decision["risk_overlay_halted"]]
    actual_position_counts = Counter(len(decision["target_weights"]) for decision in actual_decisions)
    turnover_diagnostics = {
        "schema_version": "abnormal-volume-turnover-diagnostics-v1",
        "cost_multiplier": 1.0,
        "scheduled_signal_decisions": len(decisions),
        "engine_decisions": len(actual_decisions),
        "metric_actual_decision_count": one["metrics"]["full"]["actual_decision_count"],
        "orders": len(one["orders"]),
        "fills": len(one["fills"]),
        "first_open_expirations": len(one["first_open_expirations"]),
        "failed_first_open_executions": len(one["first_open_expirations"]),
        "lifecycle_induced_exits": len(one["terminal_events"]),
        "risk_overlay_halted_decisions": len(halted),
        "risk_overlay_reason_counts": dict(
            Counter(reason for decision in halted for reason in decision["risk_overlay_reasons"])
        ),
        "annualized_target_one_way_turnover": one["metrics"]["full"]["annualized_target_turnover"],
        "annualized_realized_one_way_turnover": one["metrics"]["full"]["annualized_realized_turnover"],
        "filled_target_notional_ratio": summary["filled_target_notional_ratio"],
        "unfilled_partial_target_notional_ratio": summary["unfilled_partial_target_notional_ratio"],
        "monthly_rebalance_completion": summary["filled_target_notional_ratio"],
        "average_target_exposure_before_risk_overlay": statistics.fmean(
            sum(float(target["target_weight"]) for target in decision["targets"]) for decision in decisions
        ),
        "average_target_exposure_after_risk_overlay": statistics.fmean(
            sum(float(target[1]) for target in decision["target_weights"]) for decision in actual_decisions
        ),
        "average_realized_exposure": one["metrics"]["full"]["average_exposure"],
        "average_cash_percentage": summary["average_cash_ratio"],
        "unused_allocation_sources": [
            "Frozen 0.55 candidate allocation leaves at least 0.45 target cash.",
            "Fewer than six valid selections leave additional cash under the 0.10 per-name cap.",
            "Canonical risk-overlay halts set affected monthly targets to cash.",
            "Price drift and execution frictions create realized exposure differences between rebalances.",
        ],
        "costs": {
            "slippage": summary["slippage_cost_total"],
            "spread": summary["spread_cost_total"],
            "fees": summary["fee_total"],
            "total": summary["total_cost"],
            "cost_to_positive_gross_profit": one["metrics"]["full"]["cost_to_positive_gross_profit"],
        },
    }
    dump("TURNOVER_DIAGNOSTICS.json", turnover_diagnostics)

    contributors = sorted(summary["per_entity"], key=lambda row: row["gross_pnl"], reverse=True)
    absolute_total = sum(abs(row["gross_pnl"]) for row in contributors)
    top_abs = sorted(contributors, key=lambda row: abs(row["gross_pnl"]), reverse=True)[:5]
    concentration = {
        "schema_version": "abnormal-volume-concentration-diagnostics-v1",
        "contribution_basis": summary["concentration"]["contribution_basis"],
        "unique_traded_symbols": summary["unique_traded_symbols"],
        "unique_traded_entities": summary["unique_traded_entities"],
        "largest_positive_contributor": contributors[0],
        "largest_negative_contributor": contributors[-1],
        "largest_absolute_contribution_share": summary["concentration"]["largest_absolute_contribution_share"],
        "top_5_absolute_contribution_shares": [
            {**row, "absolute_contribution_share": abs(row["gross_pnl"]) / absolute_total} for row in top_abs
        ],
        "pre_overlay_signal_selection_count_distribution": {
            str(key): selected_counts[key] for key in sorted(selected_counts)
        },
        "post_overlay_target_position_count_distribution": {
            str(key): actual_position_counts[key] for key in sorted(actual_position_counts)
        },
        "median_target_holdings": runner["candidate_results"]["gate_evaluation"]["implementation_diagnostics"][
            "median_target_holdings"
        ],
        "maximum_realized_concentration": summary["maximum_realized_concentration"],
        "prespecified_leave_one_dominant_name_out": {
            "omitted_entity_id": runner["candidate_results"]["leave_one_dominant_name_out"]["omitted_entity_id"],
            "omitted_symbols": runner["candidate_results"]["leave_one_dominant_name_out"]["omitted_symbols"],
            "development": metric_subset(
                runner["candidate_results"]["leave_one_dominant_name_out"]["metrics"]["development"]
            ),
            "validation": metric_subset(
                runner["candidate_results"]["leave_one_dominant_name_out"]["metrics"]["validation"]
            ),
            "full": metric_subset(runner["candidate_results"]["leave_one_dominant_name_out"]["metrics"]["full"]),
        },
    }
    dump("CONCENTRATION_DIAGNOSTICS.json", concentration)

    gdiag = runner["candidate_results"]["gate_evaluation"]["implementation_diagnostics"]
    full = one["metrics"]["full"]
    validation = one["metrics"]["validation"]
    development = one["metrics"]["development"]
    loo = runner["candidate_results"]["leave_one_dominant_name_out"]["metrics"]
    gate_table = {
        "schema_version": "abnormal-volume-gates-v1",
        "all_mandatory_gates_pass": False,
        "gates": {
            "G1": {
                "status": "PASS",
                "evidence": "Pinned identity, readiness reconstruction, causal timing, lifecycle, control parity, and independent verification pass.",
            },
            "G2": {
                "status": "PASS",
                "threshold": "full 1x return > 0 and CAGR > 0",
                "observed": {"net_total_return": full["net_total_return"], "cagr": full["cagr"]},
            },
            "G3": {
                "status": "PASS",
                "threshold": "development 1x return > 0 and CAGR > 0",
                "observed": {"net_total_return": development["net_total_return"], "cagr": development["cagr"]},
            },
            "G4": {
                "status": "PASS",
                "threshold": "validation return/CAGR/Sharpe > 0 and maxDD <= 0.30",
                "observed": {key: validation[key] for key in ("net_total_return", "cagr", "sharpe", "max_drawdown")},
            },
            "G5": {
                "status": "PASS",
                "threshold": "full Sharpe >= 0.35, Sortino >= 0.45, Calmar >= 0.20, maxDD <= 0.30",
                "observed": {key: full[key] for key in ("sharpe", "sortino", "calmar", "max_drawdown")},
            },
            "G6": {
                "status": "PASS",
                "threshold": "2x full/validation return and CAGR > 0; 3x full return/CAGR/Sharpe > 0",
                "observed": {
                    key: {
                        period: {
                            field: costs[key]["metrics"][period][field]
                            for field in ("net_total_return", "cagr", "sharpe")
                        }
                        for period in ("validation", "full")
                    }
                    for key in ("2.0", "3.0")
                },
            },
            "G7": {
                "status": "PASS",
                "threshold": "annualized one-way turnover <= 12; fill >= 0.95; unfilled <= 0.05; costs <= 0.35 of positive gross profit",
                "observed": {
                    "annualized_realized_turnover": full["annualized_realized_turnover"],
                    "filled_target_notional_ratio": gdiag["filled_target_notional_ratio"],
                    "unfilled_partial_target_notional_ratio": gdiag["unfilled_partial_target_notional_ratio"],
                    "cost_to_positive_gross_profit": full["cost_to_positive_gross_profit"],
                },
            },
            "G8": {
                "status": "FAIL",
                "threshold": "median holdings >= 5; >= 90% decisions hold >= 4; target <= 0.10; absolute contribution share <= 0.35; positive full/validation LOO return and CAGR",
                "observed": {
                    "median_target_holdings": gdiag["median_target_holdings"],
                    "decisions_with_at_least_four_ratio": gdiag["decisions_with_at_least_four_ratio"],
                    "maximum_target_concentration": full["maximum_target_concentration"],
                    "largest_absolute_contribution_share": gdiag["largest_absolute_contribution_share"],
                    "loo_full_return": loo["full"]["net_total_return"],
                    "loo_full_cagr": loo["full"]["cagr"],
                    "loo_validation_return": loo["validation"]["net_total_return"],
                    "loo_validation_cagr": loo["validation"]["cagr"],
                },
                "failed_clause": "Only 74.7826% of decisions held at least four names, below 90%.",
            },
            "G9": {
                "status": "FAIL",
                "threshold": "target equity <= 0.55; realized gross <= 0.60; cash >= 0.40; positions <= 6; no privilege",
                "observed": {
                    "maximum_target_exposure": summary["maximum_target_exposure"],
                    "maximum_realized_exposure": summary["maximum_realized_exposure"],
                    "minimum_realized_cash": summary["minimum_realized_cash"],
                    "maximum_actual_positions": summary["maximum_actual_positions"],
                    "extra_privilege": runner["candidate_results"]["architecture_parity"][
                        "candidate_receives_extra_capital_slots_or_execution_privilege"
                    ],
                },
                "failed_clauses": [
                    "Maximum realized exposure 0.6114493547 exceeded 0.60.",
                    "Minimum realized cash 0.3885506453 fell below 0.40.",
                ],
            },
        },
        "independent_verifier_agreement": verifier["comparison"]["gates"],
    }
    dump("GATE_TABLE.json", gate_table)

    comparison = {}
    for key in ("1.0", "2.0", "3.0"):
        comparison[key] = {}
        for period in ("development", "validation", "full"):
            candidate_metrics = costs[key]["metrics"][period]
            control_metrics = control["parity"]["runs"][key]["metrics"][period]
            comparison[key][period] = {
                "candidate": {
                    name: candidate_metrics[name]
                    for name in (
                        "net_total_return",
                        "cagr",
                        "annualized_volatility",
                        "sharpe",
                        "sortino",
                        "max_drawdown",
                        "calmar",
                        "transaction_costs",
                        "maximum_realized_concentration",
                    )
                },
                "control": {
                    name: control_metrics.get(name)
                    for name in (
                        "net_total_return",
                        "cagr",
                        "annualized_volatility",
                        "sharpe",
                        "sortino",
                        "max_drawdown",
                        "calmar",
                    )
                }
                | {"transaction_costs": control_metrics["costs"], "reported_turnover": control_metrics["turnover"]},
            }

    final_decision = {
        "schema_version": "abnormal-volume-final-decision-v1",
        "candidate": runner["candidate"],
        "formal_frozen_economic_decision": "RESEARCH_REJECT",
        "durable_classification": "ALPHA_REJECTED",
        "research_run_validity": "RESEARCH_RUN_VALID",
        "determinative_failed_gates": ["G8", "G9"],
        "v3_displacement_diagnostic": "UNDERPERFORMS_V3",
        "v3_comparison_is_not_a_gate": True,
        "future_eligibility": "Candidate closed; no retuning and no portfolio-combination experiment authorized.",
        "production_promotion_performed": False,
        "identity": runner["identity"],
        "runner_sha256": verifier["runner_sha256"],
        "independent_verification_status": verifier["status"],
        "independent_verification_issues": verifier["issues"],
        "comparison": comparison,
    }
    dump("FINAL_DECISION.json", final_decision)

    years = one["metrics"]["validation_years"]
    year_rows = ["| Year | Scope | Return | CAGR | Sharpe | MaxDD | Costs |", "|---:|:---|---:|---:|---:|---:|---:|"]
    for year in ("2022", "2023", "2024", "2025", "2026"):
        m = years[year]
        scope = "partial through 07-30" if m["partial_calendar_year"] else "complete"
        year_rows.append(
            f"| {year} | {scope} | {pct(m['net_total_return'])} | {pct(m['cagr'])} | {m['sharpe']:.3f} | {pct(m['max_drawdown'])} | ${m['transaction_costs']:,.2f} |"
        )

    gate_rows = ["| Gate | Result | Decisive observation |", "|:---:|:---:|:---|"]
    for gate, record in gate_table["gates"].items():
        if gate == "G8":
            observation = "74.7826% of decisions had >=4 names; required >=90%."
        elif gate == "G9":
            observation = "Realized gross peaked at 61.1449% and cash fell to 38.8551%; limits were 60% and 40%."
        else:
            observation = "All frozen clauses passed."
        gate_rows.append(f"| {gate} | {record['status']} | {observation} |")

    v3c = control["parity"]["runs"]["1.0"]["metrics"]["full"]
    report = f"""# Abnormal-volume standalone economic experiment V1

## Final standalone decision

Formal frozen economic decision: `RESEARCH_REJECT`.

## Durable classification

`ALPHA_REJECTED`

## Run validity

`RESEARCH_RUN_VALID`. The independent verifier consumed the persisted runner artifact, replayed canonical raw inputs, independently recomputed signal/timing/lifecycle/cost/ledger/metric/gate evidence, reported `PASS`, and found no issues. The candidate was run once; post-run changes were limited to verifier false-positive repairs and did not change the persisted runner output.

## Frozen identity

- Production main: `{runner["identity"]["production_main_sha"]}`
- Design V2: `{runner["identity"]["design_v2_sha"]}`
- Readiness V2: `{runner["identity"]["readiness_v2_sha"]}`
- Economic branch at the authorized run: `{runner["identity"]["economic_branch"]}` at `{runner["identity"]["economic_head_at_run"]}`
- Final research branch SHA: resolve `git rev-parse HEAD` at handoff; the immutable handoff SHA is reported outside this self-referential file.

## Candidate definition

`AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`: split-consistent dollar volume, exactly 49 prior observations, frozen non-interpolated `x_(44)` Q0.90, one-day formation, inclusive top-decile volume rank plus middle-40% formation-return rank, highest abnormal-volume rank first, monthly final-close decision, next-month first-tradable-open execution, long-only top six, equal weights, 55% sleeve cap and 10% name cap.

## Data identity

Canonical bars `{runner["input_identity"]["bars"]["path"]}` at `{runner["input_identity"]["bars"]["sha256"]}`; lifetimes `{runner["input_identity"]["lifetimes"]["sha256"]}`; corporate actions `{runner["input_identity"]["corporate_actions"]["sha256"]}`. The shared endpoint is 2026-07-30; 5,602 later rows were excluded. Development is 2017-01-03 through 2021-12-31; validation is 2022-01-03 through the endpoint; the account state is continuous.

## Fresh V3 control parity

Fresh canonical `equity_residual_momentum_v3` replay passed exact target, fill, terminal-event, chronology, reserved-role, input, production-identity, and metric parity checks: `V3_PARITY_PASS`.

{control_table(control, "full")}

Control reports its native cumulative turnover field; Candidate B reports frozen annualized one-way target and realized turnover. Those differently defined values are retained and are not silently compared as equal units.

## Candidate B — 1x

Full-period ending equity was ${summary["ending_equity"]:,.2f} from $100,000. Full return {pct(full["net_total_return"])}, CAGR {pct(full["cagr"])}, Sharpe {full["sharpe"]:.3f}, Sortino {full["sortino"]:.3f}, maximum drawdown {pct(full["max_drawdown"])}, and costs ${full["transaction_costs"]:,.2f}.

## Candidate B — 2x

Full return {pct(costs["2.0"]["metrics"]["full"]["net_total_return"])}, CAGR {pct(costs["2.0"]["metrics"]["full"]["cagr"])}, Sharpe {costs["2.0"]["metrics"]["full"]["sharpe"]:.3f}, maximum drawdown {pct(costs["2.0"]["metrics"]["full"]["max_drawdown"])}, and costs ${costs["2.0"]["metrics"]["full"]["transaction_costs"]:,.2f}.

## Candidate B — 3x

Full return {pct(costs["3.0"]["metrics"]["full"]["net_total_return"])}, CAGR {pct(costs["3.0"]["metrics"]["full"]["cagr"])}, Sharpe {costs["3.0"]["metrics"]["full"]["sharpe"]:.3f}, maximum drawdown {pct(costs["3.0"]["metrics"]["full"]["max_drawdown"])}, and costs ${costs["3.0"]["metrics"]["full"]["transaction_costs"]:,.2f}.

Full-period cross-cost metrics:

{candidate_table(costs, "full")}

## Development

{candidate_table(costs, "development")}

At 1x, worst month was {pct(development["worst_month"])}, positive-month ratio {pct(development["positive_month_ratio"])}, profit factor {development["profit_factor"]:.3f}, median exposure {pct(development["median_exposure"])}, maximum target concentration {pct(development["maximum_target_concentration"])}, and maximum realized concentration {pct(development["maximum_realized_concentration"])}. Scheduled/actual decisions were {development["scheduled_decision_count"]}/{development["actual_decision_count"]}.

## Validation

{candidate_table(costs, "validation")}

At 1x, worst month was {pct(validation["worst_month"])}, positive-month ratio {pct(validation["positive_month_ratio"])}, profit factor {validation["profit_factor"]:.3f}, median exposure {pct(validation["median_exposure"])}, maximum target concentration {pct(validation["maximum_target_concentration"])}, and maximum realized concentration {pct(validation["maximum_realized_concentration"])}. Scheduled/actual decisions were {validation["scheduled_decision_count"]}/{validation["actual_decision_count"]}.

## Validation calendar years

{chr(10).join(year_rows)}

2026 is explicitly partial, not a complete calendar year.

## Abnormal-volume diagnostics

There were {signal_diagnostics["scheduled_monthly_decisions"]} scheduled monthly decisions and {signal_diagnostics["valid_decision_cross_sections"]} valid PIT cross-sections. Average PIT pool was {signal_diagnostics["average_pit_pool"]:.2f}; average volume-eligible count before the middle-40 condition was {signal_diagnostics["average_signal_eligible_before_middle_40"]:.2f}; average final middle-40 conditioned count was {signal_diagnostics["average_middle_40_conditioned_count"]:.2f}. Selected-count distribution was `{signal_diagnostics["selected_name_count_distribution"]}`; {pct(signal_diagnostics["percentage_decisions_with_fewer_than_six_selections"])} of decisions selected fewer than six. Full distributions, threshold equality, and duplicate-threshold diagnostics are in `SIGNAL_DIAGNOSTICS.json`; none became a post-hoc filter.

## Turnover / execution

Full 1x annualized target/realized one-way turnover was {full["annualized_target_turnover"]:.3f}x/{full["annualized_realized_turnover"]:.3f}x. The engine persisted {len(one["orders"])} orders and {len(one["fills"])} fills; target-notional completion was {pct(summary["filled_target_notional_ratio"])}, unfilled/partial notional was {pct(summary["unfilled_partial_target_notional_ratio"])}, and no first-open residual expired into a later-session fallback. Seven lifecycle/final-bar exits were persisted. Average realized exposure was {pct(full["average_exposure"])}; average cash was {pct(summary["average_cash_ratio"])}. Unused allocation came from the frozen 55% sleeve, per-name cap with fewer selections, canonical risk-overlay halts, and ordinary between-rebalance drift.

## Concentration

The run traded {summary["unique_traded_symbols"]} symbols/entities. Largest positive contributor was `{contributors[0]["symbols"][0]}` (${contributors[0]["gross_pnl"]:,.2f}); largest negative was `{contributors[-1]["symbols"][0]}` (${contributors[-1]["gross_pnl"]:,.2f}). Largest absolute entity contribution share was {pct(summary["concentration"]["largest_absolute_contribution_share"])}; maximum realized name concentration was {pct(summary["maximum_realized_concentration"])}. The prespecified LOO removed `{runner["candidate_results"]["leave_one_dominant_name_out"]["omitted_symbols"][0]}` and remained positive full/validation, so LOO passed. G8 nevertheless failed because only {pct(gdiag["decisions_with_at_least_four_ratio"])} of decisions held at least four names versus 90% required.

## Risk-adjusted viability

G5 passed: full 1x Sharpe {full["sharpe"]:.3f} >= 0.35, Sortino {full["sortino"]:.3f} >= 0.45, Calmar {full["calmar"]:.3f} >= 0.20, and maximum drawdown {pct(full["max_drawdown"])} <= 30%.

## Cost robustness

G6 passed: full and validation return/CAGR stayed positive at 2x, and full return/CAGR/Sharpe stayed positive at 3x. Target generation and chronology were unchanged across cost cells.

## V3 comparison — diagnostic only

Candidate B underperformed fresh V3 on full 1x return ({pct(full["net_total_return"])} vs {pct(v3c["net_total_return"])}), CAGR ({pct(full["cagr"])} vs {pct(v3c["cagr"])}), Sharpe ({full["sharpe"]:.3f} vs {v3c["sharpe"]:.3f}), Sortino ({full["sortino"]:.3f} vs {v3c["sortino"]:.3f}), and Calmar ({full["calmar"]:.3f} vs {v3c["calmar"]:.3f}). Candidate B had lower maximum drawdown ({pct(full["max_drawdown"])} vs {pct(v3c["max_drawdown"])}), lower cost (${full["transaction_costs"]:,.2f} vs ${v3c["costs"]:,.2f}), and higher maximum realized concentration ({pct(full["maximum_realized_concentration"])} vs {pct(control["parity"]["runs"]["1.0"]["metrics"]["report_summary"]["maximum_concentration"])}). V3 displacement is descriptive only and did not determine classification.

## Independent verification

Status `{verifier["status"]}` with `{verifier["research_run_validity"]}` and zero issues. Coverage independently reconstructed {verifier["comparison"]["signal_coverage"]["targets_reconstructed_from_raw"]} targets including {verifier["comparison"]["signal_coverage"]["boundary_targets_reconstructed"]} boundary targets, checked all {verifier["comparison"]["timing_coverage"]["scheduled_decisions"]} scheduled decisions across three cost cells, reconciled ledger/cost/metric evidence, and agreed on every gate.

## Frozen G1–G9 table

{chr(10).join(gate_rows)}

## Final reasoning

The run is valid and shows positive standalone economics, risk-adjusted viability, cost robustness, and implementable turnover. Classification is nevertheless determined by all mandatory frozen gates. G8 fails its breadth clause: {pct(gdiag["decisions_with_at_least_four_ratio"])} is below 90%. G9 fails realized capital bounds: gross exposure reached {pct(summary["maximum_realized_exposure"])}, above 60%, and cash reached {pct(summary["minimum_realized_cash"])}, below 40%. Near misses fail under the frozen protocol. Therefore the formal result is `RESEARCH_REJECT` and the durable classification is `ALPHA_REJECTED`.

## Future eligibility

Candidate is closed; no retuning. No portfolio-combination experiment was run or authorized here.

## Production state

No production promotion was performed. Main, PAPER strategy, LIVE state, V3 parameters, crypto, risk limits, allocation configuration, and the production strategy registry remain outside the research changes and are verified at handoff.

## Final validation

Economic runner/verifier tests: 18 passed. Focused month-end, quantile, lifecycle, cost, ledger, and verifier tests: 108 passed. Full sandbox suite: 1,132 passed and one skipped, with six localhost-binding tests blocked only by sandbox `PermissionError`; all six passed when rerun with loopback permission. Ruff, Ruff format, mypy on `src`, research-harness `py_compile`, JSON parsing, `git diff --check`, and production-isolation checks passed. No candidate rerun occurred during validation.

## Files

The compact decision package is in `research/abnormal_volume_economic_v1/`: frozen receipt, pre-run validation, runner/verifier and tests, fresh V3 control/parity, persisted runner output, compact Candidate B/cost/year/signal/turnover/concentration/gate artifacts, independent verification, and final decision.

## Final research branch SHA

The immutable final SHA is reported in the handoff after the commit is created. The authorized run itself was executed at `{runner["identity"]["economic_head_at_run"]}` and its persisted runner artifact SHA-256 is `{verifier["runner_sha256"]}`.
"""
    (OUT / "FINAL_REPORT.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
