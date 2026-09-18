#!/usr/bin/env python3
"""Independently verify the corrected V3 replay artifacts and frozen gates."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.config import load_config  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402

STRUCTURAL_PATH = ROOT / "research/v3_reserved_role_corrected_baseline_v1/structural_old_vs_corrected.json"
ECONOMIC_PATH = ROOT / "research/v3_reserved_role_corrected_baseline_v1/economic_results.json"
OUTPUT_PATH = ROOT / "research/v3_reserved_role_corrected_baseline_v1/independent_verification.json"
CONFIG_PATH = ROOT / "config/settings.yaml"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
CA_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
EXPECTED_MAIN_SHA = "bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53"
EXPECTED_BARS_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_CA_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
RESERVED = ("SPY", "SH", "PSQ", "DOG")
START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def close(actual: float, expected: float, tolerance: float = 1e-8) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def sample_std(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def recompute_period(run: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    evidence = run["evidence"]
    points = [(dt(item["timestamp"]), float(item["equity"])) for item in evidence["equity_curve"]]
    selected = [index for index, (timestamp, _value) in enumerate(points) if start <= timestamp <= end]
    if not selected:
        raise AssertionError(f"No curve points in {start} -> {end}")
    first = selected[0]
    indices = ([first - 1] if first > 0 else []) + selected
    period_points = [points[index] for index in indices]
    timestamps = [timestamp for timestamp, _value in period_points]
    values = [value for _timestamp, value in period_points]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400)
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[-1] > 0 else 0.0
    std = sample_std(returns)
    downside_std = sample_std([value for value in returns if value < 0])
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    holdings = evidence["holdings_history"]
    period_holdings = [holdings[index] for index in indices]
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
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns)) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": (sum(returns) / len(returns)) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
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
    structural = json.loads(STRUCTURAL_PATH.read_text(encoding="utf-8"))
    economic = json.loads(ECONOMIC_PATH.read_text(encoding="utf-8"))
    metadata = economic["metadata"]
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            failures.append(f"{name}{': ' + detail if detail else ''}")

    current_sha = git("rev-parse", "HEAD")
    main_sha = git("rev-parse", "main")
    origin_main_sha = git("rev-parse", "origin/main")
    check("corrected_head_sha", current_sha == EXPECTED_MAIN_SHA, f"{current_sha}")
    check("corrected_main_sha", main_sha == EXPECTED_MAIN_SHA, f"{main_sha}")
    check("corrected_origin_main_sha", origin_main_sha == EXPECTED_MAIN_SHA, f"{origin_main_sha}")

    config = load_config(CONFIG_PATH)
    strategy = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe=config.strategy.equity.timeframe,
    )
    check("strategy_name", strategy.name == "equity_residual_momentum_v3", strategy.name)
    check("strategy_version", strategy.version == "3.0.0", strategy.version)
    check("strategy_parameters", dict(config.strategy.equity.parameters) == metadata["parameters"])
    check("config_sha256", sha256(CONFIG_PATH) == metadata["hashes"]["config_sha256"])
    check("paper_mode", str(config.runtime.mode) == "paper", str(config.runtime.mode))
    check("live_disabled", config.live.enabled is False)
    check("allocation_unchanged", close(config.strategy.equity.maximum_allocation, 0.55))
    check("risk_limits_unchanged", config.risk.max_positions == 6 and close(config.risk.max_position_pct, 0.10))
    check("role_boundary_exact", config.universe.equity.ordinary_alpha_excluded_symbols == RESERVED)

    actual_hashes = {
        "bars_sha256": sha256(BARS_PATH),
        "lifetimes_sha256": sha256(LIFETIMES_PATH),
        "corporate_actions_sha256": sha256(CA_PATH),
    }
    check("bars_hash", actual_hashes["bars_sha256"] == EXPECTED_BARS_SHA)
    check("lifetimes_hash", actual_hashes["lifetimes_sha256"] == EXPECTED_LIFETIMES_SHA)
    check("corporate_actions_hash", actual_hashes["corporate_actions_sha256"] == EXPECTED_CA_SHA)
    check("artifact_hashes_match_runner", all(actual_hashes[key] == metadata["hashes"][key] for key in actual_hashes))

    lifecycle_counts: Counter[tuple[str, str, str]] = Counter()
    with LIFETIMES_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            lifecycle_counts[(row["current_status"], row["evidence_class"], row["terminal_event_class"])] += 1
    check("lifecycle_row_count", sum(lifecycle_counts.values()) == 9010)
    check("lifecycle_active_count", sum(v for (s, _e, _t), v in lifecycle_counts.items() if s == "active") == 6858)
    check("lifecycle_inactive_count", sum(v for (s, _e, _t), v in lifecycle_counts.items() if s == "inactive") == 2152)
    check(
        "lifecycle_terminal_count",
        sum(v for (_s, _e, t), v in lifecycle_counts.items() if t == "POSSIBLE_DELISTING") == 2151,
    )
    check("lifecycle_horizon_censored_count", lifecycle_counts[("inactive", "FINAL_SURVIVOR", "")] == 1)
    check("lifecycle_runner_summary", metadata["lifecycle_summary"]["final_bar_match_count"] == 9010)

    comparison = structural["comparison"]
    check("structural_phase_order", structural["phase"] == "structural_old_vs_corrected_before_economic_outputs")
    check("structural_decision_count_equal", comparison["old_decision_count"] == comparison["corrected_decision_count"])
    check(
        "structural_curve_coverage",
        structural["coverage_checks"]["corrected_curve_timestamps"]
        == structural["coverage_checks"]["expected_curve_timestamps"],
    )
    check("structural_snapshot_pit_order", structural["coverage_checks"]["snapshot_effective_at_not_after_decision"])
    check("structural_no_corrected_reserved_targets", comparison["corrected_reserved_target_count"] == 0)
    check(
        "structural_role_context_available",
        all(
            comparison["context_role_hits"]["candidate_pool"].get(symbol) == comparison["decision_count"]
            for symbol in RESERVED
        )
        and all(
            comparison["context_role_hits"]["observation"].get(symbol) == comparison["decision_count"]
            for symbol in RESERVED
        ),
    )

    for key, run in sorted(economic["runs"].items(), key=lambda item: float(item[0])):
        run_label = f"cost_{key}"
        evidence = run["evidence"]
        rebalances = evidence["rebalances"]
        curves = evidence["equity_curve"]
        checks_engine = run["engine_checks"]
        diagnostics = run["reserved_diagnostics"]
        check(f"{run_label}_role_target_total_zero", diagnostics["exact_target_decision_count"] == 0)
        check(
            f"{run_label}_role_target_each_zero",
            diagnostics["exact_target_decision_count_by_symbol"] == {symbol: 0 for symbol in RESERVED},
        )
        check(
            f"{run_label}_curve_count", len(curves) == evidence["curve_point_count"] == evidence["decision_bar_count"]
        )
        timestamps = [dt(item["timestamp"]) for item in curves]
        check(f"{run_label}_curve_order", timestamps == sorted(timestamps) and len(timestamps) == len(set(timestamps)))
        check(
            f"{run_label}_nav_positive", checks_engine["all_curve_equity_positive"] and checks_engine["ending_cash"] > 0
        )
        check(f"{run_label}_next_open_execution", checks_engine["fill_timestamps_after_decisions"])
        check(f"{run_label}_g7_positions", checks_engine["target_max_positions"] <= 6)
        check(f"{run_label}_g7_single_weight", checks_engine["target_max_single_weight"] <= 0.10 + 1e-12)
        check(f"{run_label}_g7_gross", checks_engine["target_max_gross"] <= 0.60 + 1e-12)
        check(f"{run_label}_g7_cash", checks_engine["target_min_cash"] >= 0.40 - 1e-12)
        for record in rebalances:
            check(
                f"{run_label}_no_reserved_target_{record['decision_timestamp']}",
                not (set(record["target_weights"]) & set(RESERVED)),
            )

        for period_name, (period_start, period_end) in {
            "development": (START, DEVELOPMENT_END),
            "validation": (VALIDATION_START, END),
            "full": (START, END),
        }.items():
            recomputed = recompute_period(run, period_start, period_end)
            recorded = run["periods"][period_name]
            for metric in (
                "net_total_return",
                "cagr",
                "annualized_volatility",
                "sharpe",
                "sortino",
                "max_drawdown",
                "calmar",
                "turnover",
                "costs",
                "average_exposure",
                "average_positions",
                "maximum_concentration",
            ):
                check(
                    f"{run_label}_{period_name}_{metric}",
                    close(recomputed[metric], recorded[metric], 1e-8),
                    f"{recomputed[metric]} != {recorded[metric]}",
                )
            for count_metric in ("fills", "terminal_liquidations", "rebalance_count"):
                check(
                    f"{run_label}_{period_name}_{count_metric}",
                    recomputed[count_metric] == recorded[count_metric],
                    f"{recomputed[count_metric]} != {recorded[count_metric]}",
                )
        full_summary = run["full_summary"]
        full = run["periods"]["full"]
        for summary_key, period_key in {
            "total_return": "net_total_return",
            "net_return": "net_total_return",
            "cagr": "cagr",
            "annualized_volatility": "annualized_volatility",
            "sharpe": "sharpe",
            "sortino": "sortino",
            "max_drawdown": "max_drawdown",
            "calmar": "calmar",
            "turnover": "turnover",
            "average_exposure": "average_exposure",
            "average_positions": "average_positions",
            "maximum_concentration": "maximum_concentration",
        }.items():
            check(
                f"{run_label}_full_summary_{summary_key}",
                close(full_summary[summary_key], full[period_key], 1e-8),
            )
        check(
            f"{run_label}_full_summary_costs",
            close(
                float(full_summary["slippage_cost_total"])
                + float(full_summary["spread_cost_total"])
                + float(full_summary["fee_total"]),
                full["costs"],
                1e-7,
            ),
        )

    one = economic["runs"]["1.0"]["periods"]
    two = economic["runs"]["2.0"]["periods"]
    three = economic["runs"]["3.0"]["periods"]
    gates = {
        "G1_validity_and_parity": all(checks.values()),
        "G2_full_1x_return_and_cagr_positive": one["full"]["net_total_return"] > 0 and one["full"]["cagr"] > 0,
        "G3_validation_1x_return_and_cagr_positive": one["validation"]["net_total_return"] > 0
        and one["validation"]["cagr"] > 0,
        "G4_full_1x_sharpe_sortino_calmar_positive": all(
            one["full"][metric] > 0 for metric in ("sharpe", "sortino", "calmar")
        ),
        "G5_full_1x_max_drawdown_le_5pct": one["full"]["max_drawdown"] <= 0.05,
        "G6_cost_robustness": two["full"]["net_total_return"] > 0
        and three["full"]["net_total_return"] > -0.25
        and three["full"]["max_drawdown"] < 0.50,
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
        decision = "CORRECTED_V3_BASELINE_INCONCLUSIVE"
    elif all(gates.values()):
        decision = "CORRECTED_V3_BASELINE_VALID"
    else:
        decision = "CORRECTED_V3_BASELINE_FAILED"
    payload = {
        "verifier": "independent_compact-replay-evidence-v1",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "artifact_paths": {
            "structural": str(STRUCTURAL_PATH.relative_to(ROOT)),
            "economic": str(ECONOMIC_PATH.relative_to(ROOT)),
        },
        "git": {"head": current_sha, "main": main_sha, "origin_main": origin_main_sha},
        "hashes": actual_hashes,
        "checks": checks,
        "failures": failures,
        "gates": gates,
        "decision": decision,
        "notes": [
            "Economic period metrics were recomputed from persisted equity curves, holdings history, fills, terminal events, and targets.",
            "G1 includes the compact evidence parity checks and canonical identity/lifecycle/hash checks above.",
            "The 5% drawdown gate is the frozen production risk threshold; it was not fitted to the observed baseline result.",
        ],
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"decision": decision, "gates": gates, "failure_count": len(failures)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
