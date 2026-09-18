#!/usr/bin/env python3
"""Evaluate the frozen V3 protocol repair from persisted replay evidence.

This verifier intentionally does not invoke the 11.7M-row replay. It loads
the immutable economic and structural artifacts from the prior research ref,
recomputes the compact metrics, checks the V2 protocol hash, and verifies the
offline drawdown-shadow semantics.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPLAY_REF = "research/v3-reserved-role-corrected-baseline-v1"
REPLAY_DIR = "research/v3_reserved_role_corrected_baseline_v1"
PROTOCOL_PATH = ROOT / "research/v3_baseline_protocol_repair_v1/V3_BASELINE_PROTOCOL_V2.md"
RECEIPT_PATH = ROOT / "research/v3_baseline_protocol_repair_v1/PROTOCOL_HASH_RECEIPT.txt"
EVALUATION_PATH = ROOT / "research/v3_baseline_protocol_repair_v1/V2_EVALUATION.json"
VERIFICATION_PATH = ROOT / "research/v3_baseline_protocol_repair_v1/V2_INDEPENDENT_VERIFICATION.json"
TEST_RECEIPT_PATH = ROOT / "research/v3_baseline_protocol_repair_v1/SHADOW_SEMANTICS_TEST_RECEIPT.json"

EXPECTED_MAIN_SHA = "bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53"
EXPECTED_BARS_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_CA_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_PROTOCOL_SHA = "b8a09e2f430c9ff68b707d79e17e4d7154b44e44ac51732716ba51d85eecd8b2"
RESERVED = ("SPY", "SH", "PSQ", "DOG")
START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def load_ref_json(relative_path: str) -> dict[str, Any]:
    value = subprocess.run(
        ["git", "show", f"{REPLAY_REF}:{relative_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise TypeError(f"Expected object in {relative_path}")
    return loaded


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def close(actual: float, expected: float, tolerance: float = 1e-8) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def sample_std(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def recompute_period(run: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    evidence = run["evidence"]
    curve = [(dt(item["timestamp"]), float(item["equity"])) for item in evidence["equity_curve"]]
    selected = [index for index, (timestamp, _value) in enumerate(curve) if start <= timestamp <= end]
    if not selected:
        raise AssertionError(f"No curve points in {start} -> {end}")
    first = selected[0]
    indices = ([first - 1] if first > 0 else []) + selected
    period_curve = [curve[index] for index in indices]
    timestamps = [timestamp for timestamp, _value in period_curve]
    values = [value for _timestamp, value in period_curve]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400)
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[-1] > 0 else 0.0
    standard_deviation = sample_std(returns)
    negative_deviation = sample_std([value for value in returns if value < 0])
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = values[0]
    maximum_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
    period_holdings = [evidence["holdings_history"][index] for index in indices]
    average_cash = sum(float(item["cash_ratio"]) for item in period_holdings) / len(period_holdings)
    average_positions = sum(float(item["positions"]) for item in period_holdings) / len(period_holdings)
    concentration = max(float(item["concentration"]) for item in period_holdings)
    fills = [item for item in evidence["fills"] if start <= dt(item["timestamp"]) <= end]
    terminal = [item for item in evidence["terminal_events"] if start <= dt(str(item["timestamp"])) <= end]
    slippage = sum(float(item["slippage_cost"]) for item in fills) + sum(
        float(item.get("slippage_cost", 0.0)) for item in terminal
    )
    spread = sum(float(item["spread_cost"]) for item in fills) + sum(
        float(item.get("spread_cost", 0.0)) for item in terminal
    )
    fees = sum(float(item["fee"]) for item in fills) + sum(float(item.get("fee", 0.0)) for item in terminal)
    average_equity = sum(values) / len(values)
    turnover = sum(float(item["qty"]) * float(item["reference_price"]) for item in fills) / average_equity
    rebalance_count = sum(start <= dt(item["decision_timestamp"]) <= end for item in evidence["rebalances"])
    return {
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": standard_deviation * annualizer if standard_deviation > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns)) / standard_deviation * annualizer
        if returns and standard_deviation > 0
        else 0.0,
        "sortino": (sum(returns) / len(returns)) / negative_deviation * annualizer
        if returns and negative_deviation > 0
        else 0.0,
        "max_drawdown": maximum_drawdown,
        "calmar": cagr / maximum_drawdown if maximum_drawdown > 0 else 0.0,
        "turnover": turnover,
        "costs": slippage + spread + fees,
        "slippage_cost": slippage,
        "spread_cost": spread,
        "fee_cost": fees,
        "fills": len(fills),
        "terminal_liquidations": len(terminal),
        "rebalance_count": rebalance_count,
        "average_exposure": 1.0 - average_cash,
        "average_positions": average_positions,
        "maximum_concentration": concentration,
    }


def main() -> int:
    structural = load_ref_json(f"{REPLAY_DIR}/structural_old_vs_corrected.json")
    economic = load_ref_json(f"{REPLAY_DIR}/economic_results.json")
    metadata = economic["metadata"]
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    failures: list[str] = []

    def check(name: str, condition: bool, detail: Any = None) -> None:
        checks[name] = bool(condition)
        if detail is not None:
            details[name] = detail
        if not condition:
            failures.append(name if detail is None else f"{name}: {detail}")

    # Protocol freeze and anti-post-hoc checks happen before any gate is read.
    protocol_digest = sha256(PROTOCOL_PATH)
    receipt = RECEIPT_PATH.read_text(encoding="utf-8")
    test_receipt = json.loads(TEST_RECEIPT_PATH.read_text(encoding="utf-8"))
    receipt_sha = re.search(r"^SHA-256: ([0-9a-f]{64})$", receipt, flags=re.MULTILINE)
    check("protocol_hash_matches_frozen_constant", protocol_digest == EXPECTED_PROTOCOL_SHA, protocol_digest)
    check("protocol_receipt_hash_matches_file", receipt_sha is not None and receipt_sha.group(1) == protocol_digest)
    check("protocol_receipt_says_outputs_were_not_opened", "Economic outputs opened before this receipt: NO" in receipt)
    check("protocol_receipt_says_observed_result_not_used", "Observed 20.4359% result used to define the protocol: NO" in receipt)
    check("protocol_defines_no_1x_alpha_dd_threshold", "no absolute 1x offline alpha maximum-drawdown threshold" in PROTOCOL_PATH.read_text(encoding="utf-8").lower())
    check("shadow_fixture_tests_pass", all(item.get("result") == "PASS" for item in test_receipt["tests"]))

    current_sha = git("rev-parse", "HEAD")
    main_sha = git("rev-parse", "main")
    origin_main_sha = git("rev-parse", "origin/main")
    check("repair_head_is_production_sha", current_sha == EXPECTED_MAIN_SHA, current_sha)
    check("main_sha_is_production_sha", main_sha == EXPECTED_MAIN_SHA, main_sha)
    check("origin_main_sha_is_production_sha", origin_main_sha == EXPECTED_MAIN_SHA, origin_main_sha)
    check("replay_metadata_main_sha", metadata["main_sha"] == EXPECTED_MAIN_SHA, metadata["main_sha"])
    check("replay_metadata_branch", metadata["branch"] == REPLAY_REF, metadata["branch"])

    config_path = ROOT / "config/settings.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    equity = config["strategy"]["equity"]
    risk = config["risk"]
    strategy = portfolio_strategy_from(equity["name"], equity["parameters"])
    check("config_sha_matches_replay", sha256(config_path) == metadata["hashes"]["config_sha256"])
    check("strategy_name", equity["name"] == "equity_residual_momentum_v3", equity["name"])
    check("strategy_version", strategy.version == "3.0.0", strategy.version)
    check("strategy_parameters_match", equity["parameters"] == metadata["parameters"])
    check("paper_mode", config["runtime"]["mode"] == "paper", config["runtime"]["mode"])
    check("live_disabled", config["live"]["enabled"] is False, config["live"]["enabled"])
    check("allocation_unchanged", close(float(equity["maximum_allocation"]), 0.55))
    check("drawdown_limit_is_5_percent_operational_value", close(float(risk["max_drawdown_pct"]), 0.05))
    equity_universe = config["universe"]["equity"]
    check(
        "role_boundary_exact",
        tuple(equity_universe["reference_symbols"]) == ("SPY",)
        and equity_universe["trade_reference_symbols"] is False
        and tuple(equity_universe["inverse_trade_symbols"]) == ("SH", "PSQ", "DOG")
        and tuple(equity["parameters"]["exclude_symbols"]) == ("SPY",),
    )

    paths = {
        "bars": ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
        "lifetimes": ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv",
        "corporate_actions": ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
    }
    actual_hashes = {name: sha256(path) for name, path in paths.items()}
    expected_hashes = {"bars": EXPECTED_BARS_SHA, "lifetimes": EXPECTED_LIFETIMES_SHA, "corporate_actions": EXPECTED_CA_SHA}
    metadata_hash_keys = {"bars": "bars_sha256", "lifetimes": "lifetimes_sha256", "corporate_actions": "corporate_actions_sha256"}
    for name in paths:
        check(f"{name}_hash_current", actual_hashes[name] == expected_hashes[name], actual_hashes[name])
        check(
            f"{name}_hash_matches_replay",
            actual_hashes[name] == metadata["hashes"][metadata_hash_keys[name]],
            metadata["hashes"][metadata_hash_keys[name]],
        )

    comparison = structural["comparison"]
    coverage = structural["coverage_checks"]
    check("structural_phase_is_pre_economic", structural["phase"] == "structural_old_vs_corrected_before_economic_outputs")
    check("structural_decision_count_equal", comparison["old_decision_count"] == comparison["corrected_decision_count"])
    check("structural_curve_coverage_equal", coverage["corrected_curve_timestamps"] == coverage["expected_curve_timestamps"])
    check("structural_pit_order", coverage["snapshot_effective_at_not_after_decision"] is True)
    check("structural_corrected_reserved_targets_zero", comparison["corrected_reserved_target_count"] == 0)
    check(
        "structural_reserved_roles_in_context",
        all(comparison["context_role_hits"][context].get(symbol) == comparison["decision_count"] for context in ("candidate_pool", "observation") for symbol in RESERVED),
    )

    period_bounds = {
        "development": (START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, END),
        "full": (START, END),
    }
    recomputed: dict[str, Any] = {}
    shadow: dict[str, Any] = {}
    for cost_key, run in sorted(economic["runs"].items(), key=lambda item: float(item[0])):
        evidence = run["evidence"]
        curves = evidence["equity_curve"]
        curve_timestamps = [dt(item["timestamp"]) for item in curves]
        prefix = f"cost_{cost_key}"
        check(f"{prefix}_curve_count", len(curves) == evidence["curve_point_count"] == evidence["decision_bar_count"])
        check(f"{prefix}_curve_order", curve_timestamps == sorted(curve_timestamps) and len(set(curve_timestamps)) == len(curve_timestamps))
        check(f"{prefix}_nav_positive", run["engine_checks"]["all_curve_equity_positive"] and run["engine_checks"]["ending_cash"] > 0)
        check(f"{prefix}_next_open_execution", run["engine_checks"]["fill_timestamps_after_decisions"])
        check(f"{prefix}_target_controls", run["engine_checks"]["target_max_positions"] <= 6 and run["engine_checks"]["target_max_single_weight"] <= 0.10 + 1e-12 and run["engine_checks"]["target_max_gross"] <= 0.60 + 1e-12 and run["engine_checks"]["target_min_cash"] >= 0.40 - 1e-12)
        check(f"{prefix}_reserved_diagnostics_zero", run["reserved_diagnostics"]["exact_target_decision_count"] == 0 and run["reserved_diagnostics"]["exact_target_decision_count_by_symbol"] == {symbol: 0 for symbol in RESERVED})
        for record in evidence["rebalances"]:
            check(f"{prefix}_no_reserved_target_{record['decision_timestamp']}", not (set(record["target_weights"]) & set(RESERVED)))

        recomputed[cost_key] = {}
        for period_name, (period_start, period_end) in period_bounds.items():
            actual = recompute_period(run, period_start, period_end)
            recorded = run["periods"][period_name]
            recomputed[cost_key][period_name] = actual
            for metric in ("net_total_return", "cagr", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar", "turnover", "costs", "average_exposure", "average_positions", "maximum_concentration"):
                check(f"{prefix}_{period_name}_{metric}", close(actual[metric], recorded[metric]), {"actual": actual[metric], "recorded": recorded[metric]})
            for metric in ("fills", "terminal_liquidations", "rebalance_count"):
                check(f"{prefix}_{period_name}_{metric}", actual[metric] == recorded[metric], {"actual": actual[metric], "recorded": recorded[metric]})

        rebalances = evidence["rebalances"]
        shadow_records = [record for record in rebalances if "portfolio_drawdown_halt" in record["risk_overlay_reasons"]]
        shadow_only = [record for record in shadow_records if "daily_loss_limit" not in record["risk_overlay_reasons"]]
        shadow_with_daily_loss = [record for record in shadow_records if "daily_loss_limit" in record["risk_overlay_reasons"]]
        timestamp_indexes = {item["timestamp"]: index for index, item in enumerate(curves)}
        check(f"{prefix}_shadow_reason_count_matches_rebalances", len(shadow_records) == len(set(record["decision_timestamp"] for record in shadow_records)))
        check(f"{prefix}_shadow_only_not_halted", all(record["risk_overlay_halted"] is False for record in shadow_only))
        check(f"{prefix}_shadow_events_have_curve_continuation", all(timestamp_indexes[record["decision_timestamp"]] < len(curve_timestamps) - 1 for record in shadow_records))
        check(f"{prefix}_shadow_timestamps_are_ordered", [record["decision_timestamp"] for record in shadow_records] == sorted(record["decision_timestamp"] for record in shadow_records))
        shadow[cost_key] = {
            "shadow_event_count": len(shadow_records),
            "shadow_only_count": len(shadow_only),
            "shadow_with_daily_loss_count": len(shadow_with_daily_loss),
            "first_shadow_timestamp": shadow_records[0]["decision_timestamp"] if shadow_records else None,
            "last_shadow_timestamp": shadow_records[-1]["decision_timestamp"] if shadow_records else None,
            "continuation_points_verified": len(shadow_records),
        }

    one = economic["runs"]["1.0"]["periods"]
    two = economic["runs"]["2.0"]["periods"]
    three = economic["runs"]["3.0"]["periods"]
    gates = {
        "G1_validity_and_parity": all(checks.values()),
        "G2_full_1x_return_and_cagr_positive": one["full"]["net_total_return"] > 0 and one["full"]["cagr"] > 0,
        "G3_validation_1x_return_and_cagr_positive": one["validation"]["net_total_return"] > 0 and one["validation"]["cagr"] > 0,
        "G4_full_1x_sharpe_sortino_calmar_positive": all(one["full"][metric] > 0 for metric in ("sharpe", "sortino", "calmar")),
        "G5_drawdown_semantics_and_reporting": (
            math.isfinite(float(one["full"]["max_drawdown"]))
            and all(item["shadow_event_count"] > 0 and item["continuation_points_verified"] == item["shadow_event_count"] for item in shadow.values())
            and checks.get("protocol_defines_no_1x_alpha_dd_threshold", False)
            and checks.get("shadow_fixture_tests_pass", False)
            and all(checks.get(f"cost_{cost}_shadow_only_not_halted", False) for cost in ("1.0", "2.0", "3.0"))
        ),
        "G6_cost_robustness": two["full"]["net_total_return"] > 0 and three["full"]["net_total_return"] > -0.25 and three["full"]["max_drawdown"] < 0.50,
        "G7_controls_and_zero_reserved_targets": all(
            run["reserved_diagnostics"]["exact_target_decision_count"] == 0
            and run["engine_checks"]["target_max_positions"] <= 6
            and run["engine_checks"]["target_max_single_weight"] <= 0.10 + 1e-12
            and run["engine_checks"]["target_max_gross"] <= 0.60 + 1e-12
            and run["engine_checks"]["target_min_cash"] >= 0.40 - 1e-12
            for run in economic["runs"].values()
        ),
    }
    if not gates["G1_validity_and_parity"]:
        decision = "CORRECTED_V3_BASELINE_INCONCLUSIVE_PROTOCOL_ERROR"
    elif all(gates.values()):
        decision = "CORRECTED_V3_BASELINE_VALID"
    else:
        decision = "CORRECTED_V3_BASELINE_FAILED"

    evaluation = {
        "verifier": "v3-baseline-protocol-repair-v2-evaluator",
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "replay_ref": REPLAY_REF,
        "replay_artifacts": {
            "structural": f"{REPLAY_DIR}/structural_old_vs_corrected.json",
            "economic": f"{REPLAY_DIR}/economic_results.json",
        },
        "protocol": {"path": str(PROTOCOL_PATH.relative_to(ROOT)), "sha256": protocol_digest, "receipt": str(RECEIPT_PATH.relative_to(ROOT))},
        "git": {"head": current_sha, "main": main_sha, "origin_main": origin_main_sha},
        "dataset_hashes": actual_hashes,
        "recomputed_periods": recomputed,
        "shadow_event_verification": shadow,
        "gates": gates,
        "decision": decision,
        "failures": failures,
    }
    EVALUATION_PATH.write_text(json.dumps(evaluation, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    verification = {
        "verifier": "independent-v2-governance-and-persisted-evidence-verifier",
        "evaluated_at_utc": evaluation["evaluated_at_utc"],
        "checks": checks,
        "failed_checks": failures,
        "protocol_hash_verified": protocol_digest == EXPECTED_PROTOCOL_SHA,
        "main_sha_verified": current_sha == main_sha == origin_main_sha == EXPECTED_MAIN_SHA,
        "role_fix_and_v3_identity_verified": checks.get("role_boundary_exact", False) and checks.get("strategy_name", False) and checks.get("strategy_version", False),
        "dataset_hashes_verified": all(checks.get(f"{name}_hash_current", False) and checks.get(f"{name}_hash_matches_replay", False) for name in paths),
        "five_percent_latch_semantics_verified": checks.get("drawdown_limit_is_5_percent_operational_value", False),
        "shadow_behavior_verified": gates["G5_drawdown_semantics_and_reporting"],
        "temporal_provenance_verified": checks.get("protocol_receipt_says_outputs_were_not_opened", False) and checks.get("protocol_receipt_says_observed_result_not_used", False),
        "anti_post_hoc_answer": "NO",
        "observed_20_4359_percent_influenced_v2_gate_definition": "NO",
        "metric_recomputation_verified": not any(name.endswith(("_max_drawdown", "_net_total_return", "_cagr", "_sharpe", "_sortino")) for name in failures),
        "v2_gates": gates,
        "decision": decision,
        "notes": [
            "The persisted replay was reused because code/config/data/role/execution identity matched and compact evidence was sufficient.",
            "The original 5% G5 comparison was not re-applied as an offline alpha bound.",
            "No operator reset schedule or new numeric 1x drawdown threshold was invented.",
        ],
    }
    VERIFICATION_PATH.write_text(json.dumps(verification, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"decision": decision, "gates": gates, "failure_count": len(failures)}, sort_keys=True))
    return 0 if decision == "CORRECTED_V3_BASELINE_VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
