#!/usr/bin/env python3
"""Fresh canonical V3 replay and serializer-stable maintenance parity receipt.

This candidate-neutral harness is intentionally isolated from PAPER/LIVE state.
It reads the frozen historical inputs, constructs the production V3 strategy,
scanner, risk and execution objects, runs the in-memory backtest engine, and
writes only the explicitly requested JSON receipt.

Typical use::

    .venv/bin/python research/month_end_first_open_correctness_v1/replay_v3_parity.py \
        --out research/month_end_first_open_correctness_v1/V3_PRE_FIX_CONTROL.json

    .venv/bin/python research/month_end_first_open_correctness_v1/replay_v3_parity.py \
        --out research/month_end_first_open_correctness_v1/V3_POST_FIX_REPLAY.json \
        --compare research/month_end_first_open_correctness_v1/V3_PRE_FIX_CONTROL.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
    run_product_backtest,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402

BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"

START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
RESERVED_ROLES = ("SPY", "SH", "PSQ", "DOG")
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
SERIALIZER = "canonical-json-v1: sort_keys,separators-comma-colon,utf8,allow_nan=false,no-trailing-newline"
METRIC_REL_TOLERANCE = 1e-12
METRIC_ABS_TOLERANCE = 1e-12

CRYPTO_FOCUSED_TESTS = (
    "tests/test_strategy_spec_freeze.py",
    "tests/test_crypto_foundation_parity.py",
    "tests/test_crypto_low_variance.py",
    "tests/test_crypto_t1_freeze.py",
    "tests/test_t1_promotion_transition.py",
    "tests/test_cadence_v2_identities.py",
    "tests/test_dual_market_startup.py",
    "tests/test_release_v2_integration.py",
    "tests/test_runtime_universe_refresh.py",
    "tests/test_product_boundary.py",
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Parity receipt cannot serialize non-finite floats")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported parity value: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _target_rows(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    return [
        {
            "decision_timestamp": record.decision_timestamp.isoformat(),
            "target_weights": [[symbol, float(weight)] for symbol, weight in sorted(record.target_weights)],
            "target_cash_weight": float(record.target_cash_weight),
            "risk_overlay_halted": bool(record.risk_overlay_halted),
            "risk_overlay_reasons": list(record.risk_overlay_reasons),
        }
        for record in result.rebalances
    ]


def _fill_rows(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": fill.timestamp.isoformat(),
            "decision_timestamp": fill.decision_timestamp.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": float(fill.qty),
            "reference_price": float(fill.reference_price),
            "fill_price": float(fill.fill_price),
            "slippage_cost": float(fill.slippage_cost),
            "spread_cost": float(fill.spread_cost),
            "fee": float(fill.fee),
        }
        for fill in result.fills
    ]


def _terminal_rows(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    return [_jsonable(dict(event)) for event in result.terminal_events]


def _chronology_rows(result: PortfolioBacktestResult) -> dict[str, Any]:
    return {
        "curve_timestamps": [timestamp.isoformat() for timestamp in result.curve_timestamps],
        "decision_timestamps": [record.decision_timestamp.isoformat() for record in result.rebalances],
        "fills": [
            [fill.decision_timestamp.isoformat(), fill.timestamp.isoformat(), fill.symbol, fill.side.value]
            for fill in result.fills
        ],
        "terminal_events": [
            [str(event.get("timestamp", "")), str(event.get("symbol", "")), str(event.get("event_class", ""))]
            for event in result.terminal_events
        ],
    }


def _sample_std(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _period_metrics(
    result: PortfolioBacktestResult,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    points = list(zip(result.curve_timestamps, result.equity_curve, strict=True))
    selected = [index for index, (timestamp, _value) in enumerate(points) if start <= timestamp <= end]
    if not selected:
        raise ValueError(f"No curve points in {start.isoformat()} -> {end.isoformat()}")
    first = selected[0]
    indices = ([first - 1] if first > 0 else []) + selected
    period = [points[index] for index in indices]
    timestamps = [timestamp for timestamp, _value in period]
    values = [float(value) for _timestamp, value in period]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400)
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1.0 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0 if years > 0 and values[-1] > 0 else 0.0
    std = _sample_std(returns)
    downside_std = _sample_std([value for value in returns if value < 0])
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    terminals = [
        event for event in result.terminal_events if start <= datetime.fromisoformat(str(event["timestamp"])) <= end
    ]
    slippage = sum(float(fill.slippage_cost) for fill in fills) + sum(
        float(event.get("slippage_cost", 0.0)) for event in terminals
    )
    spread = sum(float(fill.spread_cost) for fill in fills) + sum(
        float(event.get("spread_cost", 0.0)) for event in terminals
    )
    fees = sum(float(fill.fee) for fill in fills) + sum(float(event.get("fee", 0.0)) for event in terminals)
    average_equity = sum(values) / len(values)
    turnover = (
        sum(float(fill.qty) * float(fill.reference_price) for fill in fills) / average_equity
        if average_equity > 0
        else 0.0
    )
    return {
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
        "curve_point_count": len(values),
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns)) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": ((sum(returns) / len(returns)) / downside_std * annualizer if returns and downside_std > 0 else 0.0),
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "turnover": turnover,
        "costs": slippage + spread + fees,
        "slippage_cost": slippage,
        "spread_cost": spread,
        "fee_cost": fees,
        "fill_count": len(fills),
        "terminal_event_count": len(terminals),
        "rebalance_count": sum(start <= record.decision_timestamp <= end for record in result.rebalances),
    }


def _run_receipt(multiplier: float, product: Any) -> dict[str, Any]:
    result = product.result
    targets = _target_rows(result)
    fills = _fill_rows(result)
    terminals = _terminal_rows(result)
    chronology = _chronology_rows(result)
    role_targets: Counter[str] = Counter()
    for target in targets:
        for symbol, _weight in target["target_weights"]:
            if symbol in RESERVED_ROLES:
                role_targets[symbol] += 1
    role_observations: Counter[str] = Counter()
    for _timestamp, symbols in result.universe_history:
        for symbol in set(symbols) & set(RESERVED_ROLES):
            role_observations[symbol] += 1
    return {
        "cost_multiplier": multiplier,
        "hashes": {
            "targets_sha256": _payload_sha256(targets),
            "fills_sha256": _payload_sha256(fills),
            "terminal_events_sha256": _payload_sha256(terminals),
            "chronology_sha256": _payload_sha256(chronology),
        },
        "counts": {
            "target_decision_count": len(targets),
            "target_entry_count": sum(len(target["target_weights"]) for target in targets),
            "fill_count": len(fills),
            "terminal_event_count": len(terminals),
            "curve_point_count": len(result.equity_curve),
            "universe_history_count": len(result.universe_history),
        },
        "chronology_checks": {
            "curve_strictly_ordered": list(result.curve_timestamps) == sorted(set(result.curve_timestamps)),
            "decisions_strictly_ordered": [record.decision_timestamp for record in result.rebalances]
            == sorted({record.decision_timestamp for record in result.rebalances}),
            "fills_after_decisions": all(fill.timestamp > fill.decision_timestamp for fill in result.fills),
            "first_curve_timestamp": result.curve_timestamps[0].isoformat(),
            "last_curve_timestamp": result.curve_timestamps[-1].isoformat(),
            "first_decision_timestamp": result.rebalances[0].decision_timestamp.isoformat(),
            "last_decision_timestamp": result.rebalances[-1].decision_timestamp.isoformat(),
        },
        "reserved_roles": {
            "ordinary_target_count_by_symbol": {symbol: role_targets.get(symbol, 0) for symbol in RESERVED_ROLES},
            "observation_decision_count_by_symbol": {
                symbol: role_observations.get(symbol, 0) for symbol in RESERVED_ROLES
            },
        },
        "metrics": {
            "report_summary": _jsonable(product.report.summary),
            "development": _period_metrics(result, START, DEVELOPMENT_END),
            "validation": _period_metrics(result, VALIDATION_START, END),
            "full": _period_metrics(result, START, END),
        },
    }


def _crypto_identity(config: Any) -> dict[str, Any]:
    selection = config.strategy.crypto
    strategy = portfolio_strategy_from(
        selection.name,
        selection.parameters,
        timeframe=selection.timeframe,
    )
    policy = rebalance_policy_for(strategy)
    return {
        "enabled": bool(selection.enabled),
        "strategy_name": strategy.name,
        "strategy_version": strategy.version,
        "timeframe": strategy.timeframe,
        "parameters": _jsonable(dict(selection.parameters)),
        "parameter_tuple": _jsonable(strategy.parameter_tuple()),
        "rebalance_policy": policy.frequency,
        "maximum_allocation": float(selection.maximum_allocation),
        "risk": {
            "max_crypto_exposure_pct": float(config.risk.max_crypto_exposure_pct),
            "max_crypto_position_pct": float(config.risk.max_crypto_position_pct),
            "max_crypto_positions": int(config.risk.max_crypto_positions),
        },
        "focused_test_command": ".venv/bin/python -m pytest -q " + " ".join(CRYPTO_FOCUSED_TESTS),
        "focused_test_files": list(CRYPTO_FOCUSED_TESTS),
        "tests_executed_by_this_harness": False,
    }


def collect() -> dict[str, Any]:
    config = load_config(CONFIG_PATH)
    bars = load_csv_bars(BARS_PATH, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(LIFETIMES_PATH)
    corporate_actions = load_csv_corporate_actions(CORPORATE_ACTIONS_PATH)
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(
        selection.name,
        selection.parameters,
        timeframe=selection.timeframe,
    )
    if strategy.name != "equity_residual_momentum_v3" or strategy.version != "3.0.0":
        raise ValueError(f"Unexpected canonical strategy: {strategy.name} {strategy.version}")
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    prepared = _prepared_window(bars, Market.EQUITY, strategy.timeframe, START, END)
    _filtered, warmup, (ordered, _decision_bars) = prepared
    numeric_history = build_numeric_history(
        warmup + ordered,
        market=Market.EQUITY,
        timeframe=strategy.timeframe,
    )
    runs: dict[str, Any] = {}
    for multiplier in COST_MULTIPLIERS:
        product = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=strategy.name,
            parameters=dict(selection.parameters),
            strategy_timeframe=strategy.timeframe,
            start=START,
            end=END,
            cost_multiplier=multiplier,
            persist=False,
            persist_artifacts=False,
            corporate_actions=corporate_actions,
            universe=universe,
            prepared_inputs=prepared,
            numeric_history=numeric_history,
            terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
        )
        runs[str(multiplier)] = _run_receipt(multiplier, product)
        print(
            f"V3 {multiplier:.1f}x: targets={runs[str(multiplier)]['counts']['target_decision_count']} "
            f"fills={runs[str(multiplier)]['counts']['fill_count']} "
            f"terminal={runs[str(multiplier)]['counts']['terminal_event_count']}",
            flush=True,
        )
    parity = {
        "serializer": SERIALIZER,
        "metric_rel_tolerance": METRIC_REL_TOLERANCE,
        "metric_abs_tolerance": METRIC_ABS_TOLERANCE,
        "input_identity": {
            "bars": {"path": str(BARS_PATH.relative_to(ROOT)), "sha256": _file_sha256(BARS_PATH)},
            "lifetimes": {
                "path": str(LIFETIMES_PATH.relative_to(ROOT)),
                "sha256": _file_sha256(LIFETIMES_PATH),
                "terminal_mapping_sha256": lifetimes.receipt.terminal_mapping_sha256,
                "terminal_mapping_count": len(lifetimes.explicit_terminals),
                "unresolved_count": len(lifetimes.unresolved),
            },
            "corporate_actions": {
                "path": str(CORPORATE_ACTIONS_PATH.relative_to(ROOT)),
                "sha256": _file_sha256(CORPORATE_ACTIONS_PATH),
            },
            "config": {"path": str(CONFIG_PATH.relative_to(ROOT)), "sha256": _file_sha256(CONFIG_PATH)},
            "universe_sha256": hash_point_in_time_universe(universe),
            "universe_snapshot_count": len(universe.snapshots),
        },
        "production_identity": {
            "runtime_mode": config.runtime.mode.value,
            "live_enabled": bool(config.live.enabled),
            "live_hard_dollar_cap": float(config.live.hard_dollar_cap),
            "equity": {
                "strategy_name": strategy.name,
                "strategy_version": strategy.version,
                "timeframe": strategy.timeframe,
                "parameters": _jsonable(dict(selection.parameters)),
                "rebalance_policy": rebalance_policy_for(strategy).frequency,
                "maximum_allocation": float(selection.maximum_allocation),
                "ordinary_alpha_excluded_symbols": list(config.universe.equity.ordinary_alpha_excluded_symbols),
            },
            "crypto": _crypto_identity(config),
        },
        "window": {
            "start": START.isoformat(),
            "development_end": DEVELOPMENT_END.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "end": END.isoformat(),
            "warmup_bar_count": len(warmup),
            "evaluation_bar_count": len(ordered),
        },
        "runs": runs,
    }
    return {
        "schema": "candidate-neutral-v3-maintenance-parity-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git": {
            "head": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "main": _git("rev-parse", "main"),
            "origin_main": _git("rev-parse", "origin/main"),
        },
        "scope": {
            "candidate_economics_run": False,
            "production_runtime_or_cache_write": False,
            "description": "Fresh canonical V3 maintenance parity only; no abnormal-volume candidate path is imported or executed.",
        },
        "parity": parity,
        "parity_sha256": _payload_sha256(parity),
    }


def _compare_numbers(reference: Any, current: Any, path: str, differences: list[dict[str, Any]]) -> None:
    if isinstance(reference, bool) or isinstance(current, bool):
        if reference != current:
            differences.append({"path": path, "reference": reference, "current": current})
        return
    if isinstance(reference, (int, float)) and isinstance(current, (int, float)):
        if not math.isclose(
            float(reference),
            float(current),
            rel_tol=METRIC_REL_TOLERANCE,
            abs_tol=METRIC_ABS_TOLERANCE,
        ):
            differences.append({"path": path, "reference": reference, "current": current})
        return
    if isinstance(reference, dict) and isinstance(current, dict):
        if set(reference) != set(current):
            differences.append({"path": path, "reference_keys": sorted(reference), "current_keys": sorted(current)})
            return
        for key in sorted(reference):
            _compare_numbers(reference[key], current[key], f"{path}.{key}", differences)
        return
    if isinstance(reference, list) and isinstance(current, list):
        if len(reference) != len(current):
            differences.append({"path": path, "reference_length": len(reference), "current_length": len(current)})
            return
        for index, (reference_item, current_item) in enumerate(zip(reference, current, strict=True)):
            _compare_numbers(reference_item, current_item, f"{path}[{index}]", differences)
        return
    if reference != current:
        differences.append({"path": path, "reference": reference, "current": current})


def compare(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    reference_parity = reference["parity"]
    current_parity = current["parity"]
    hash_paths = ("targets_sha256", "fills_sha256", "terminal_events_sha256", "chronology_sha256")
    exact: dict[str, bool] = {}
    for multiplier in ("1.0", "2.0", "3.0"):
        before = reference_parity["runs"][multiplier]
        after = current_parity["runs"][multiplier]
        for key in hash_paths:
            exact[f"{multiplier}.{key}"] = before["hashes"][key] == after["hashes"][key]
        exact[f"{multiplier}.counts"] = before["counts"] == after["counts"]
        exact[f"{multiplier}.chronology_checks"] = before["chronology_checks"] == after["chronology_checks"]
        exact[f"{multiplier}.reserved_roles"] = before["reserved_roles"] == after["reserved_roles"]
    exact["input_identity"] = reference_parity["input_identity"] == current_parity["input_identity"]
    exact["production_identity"] = reference_parity["production_identity"] == current_parity["production_identity"]
    exact["window"] = reference_parity["window"] == current_parity["window"]
    metric_differences: list[dict[str, Any]] = []
    _compare_numbers(
        {key: value["metrics"] for key, value in reference_parity["runs"].items()},
        {key: value["metrics"] for key, value in current_parity["runs"].items()},
        "runs.metrics",
        metric_differences,
    )
    return {
        "verdict": "V3_PARITY_PASS" if all(exact.values()) and not metric_differences else "V3_PARITY_FAIL",
        "reference_git": reference["git"],
        "current_git": current["git"],
        "exact_checks": exact,
        "failed_exact_checks": sorted(key for key, passed in exact.items() if not passed),
        "metric_tolerance": {
            "relative": METRIC_REL_TOLERANCE,
            "absolute": METRIC_ABS_TOLERANCE,
        },
        "metric_differences": metric_differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    research_root = (ROOT / "research/month_end_first_open_correctness_v1").resolve()
    if research_root not in output.parents:
        raise ValueError(f"Output must remain inside {research_root}")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = collect()
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"WROTE={output}", flush=True)
    if args.compare is None:
        return 0
    reference = json.loads(args.compare.read_text(encoding="utf-8"))
    comparison = compare(reference, receipt)
    comparison_path = output.with_name(f"{output.stem}_COMPARISON.json")
    comparison_path.write_text(
        json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"COMPARISON={comparison_path}", flush=True)
    print(f"VERDICT={comparison['verdict']}", flush=True)
    return 0 if comparison["verdict"] == "V3_PARITY_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
