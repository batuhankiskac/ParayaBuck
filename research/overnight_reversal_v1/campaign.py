"""Single-run campaign runner for the frozen overnight-reversal experiment."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import stdev
from typing import Any, cast

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    PortfolioFill,
    RebalanceRecord,
)
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.markets.sectors import sector_of
from parayabuck.portfolio import PortfolioStrategy, portfolio_strategy_from
from parayabuck.portfolio.models import RebalancePolicy
from research.overnight_reversal_v1.candidate import (
    CANDIDATE_NAME,
    CANDIDATE_VERSION,
    FROZEN_PARAMETERS,
    DailySessionRebalancePolicy,
    OvernightReversalStrategy,
    signal_from_bars,
)
from research.overnight_reversal_v1.data import (
    EXPECTED_BAR_ROWS,
    EXPECTED_BAR_SHA256,
    EXPECTED_CORPORATE_ACTION_ROWS,
    EXPECTED_CORPORATE_ACTION_SHA256,
    EXPECTED_DIVIDEND_ROWS,
    EXPECTED_LIFETIME_ROWS,
    EXPECTED_LIFETIME_SHA256,
    EXPECTED_SPLIT_ROWS,
    NormalizedDataset,
    load_normalized_dataset,
    session_date,
    sha256_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = REPO_ROOT / "research" / "overnight_reversal_v1"
DESIGN_DIR = REPO_ROOT / "research" / "overnight_intraday_design_v1"
DATA_DIR = REPO_ROOT / "data" / "historical"
BAR_PATH = DATA_DIR / "equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_PATH = DATA_DIR / "equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTION_PATH = DATA_DIR / "equity_survivorship_full_corporate_actions_v5.csv"
DESIGN_COMMIT = "7ec53efecc4d37cab17d0d96f194389b4513a69b"
ECONOMIC_BRANCH = "research/overnight-reversal-v1"
DESIGN_BRANCH = "research/overnight-intraday-design-v1"
DESIGN_ARTIFACTS = (
    "CURRENT_CAPABILITY_AUDIT.md",
    "LITERATURE_REVIEW.md",
    "DATA_FEASIBILITY.json",
    "CAUSALITY_AUDIT.md",
    "CANDIDATE_DESIGN.md",
    "FUTURE_TEST_PROTOCOL.md",
    "DESIGN_RED_TEAM.md",
    "DESIGN_DECISION.json",
    "FINAL_DESIGN_REPORT.md",
)
DESIGN_HASHES = {
    "CANDIDATE_DESIGN.md": "0a9fb68d891dd049caa5efcb375fc70ffb4e2cc31e500419b446c5b103504930",
    "CAUSALITY_AUDIT.md": "56e4080b91665fae0f8ff2fb7fc007dabe7e0f0198866b464135eccc7e5c25f4",
    "CURRENT_CAPABILITY_AUDIT.md": "086a30f36449599c7972daba58de04c93d7c9b23b0ae2eb55648dcc2885085c5",
    "DATA_FEASIBILITY.json": "35beb7593aec129af531bdcebbbeaac0cfd3d42c86936f3303f16c52e575d693",
    "DESIGN_DECISION.json": "c0979ce4d9925f302cbd9e66ef39b86ba6c9fedb102541cdd95a16f58ae1869a",
    "DESIGN_RED_TEAM.md": "cc938e21d6720e0262a979c16a2f4ee52e917d4e746fd13c10fbf1ebea8eddd8",
    "FINAL_DESIGN_REPORT.md": "4059849b267ff8c227aa9afd696f28ab55609e0493a8ba7aa17933691b11973a",
    "FUTURE_TEST_PROTOCOL.md": "342c21f606e1a0247b81cca19e68379837f96aa58b134ca8cc987d9480fa4aea",
    "LITERATURE_REVIEW.md": "f77bf86981d4398507540e2300c2d029c2cef014677063960ff8c050b0b3b0d3",
}
WINDOWS = {
    "warmup": (date(2016, 1, 4), date(2016, 12, 30)),
    "development": (date(2017, 1, 3), date(2021, 12, 31)),
    "validation": (date(2022, 1, 3), date(2026, 7, 30)),
    "2022": (date(2022, 1, 3), date(2022, 12, 30)),
    "2023": (date(2023, 1, 3), date(2023, 12, 29)),
    "2024": (date(2024, 1, 2), date(2024, 12, 31)),
    "2025": (date(2025, 1, 2), date(2025, 12, 31)),
    "2026_partial": (date(2026, 1, 2), date(2026, 7, 30)),
    "full": (date(2017, 1, 3), date(2026, 7, 30)),
}
EVALUATION_START = datetime(2017, 1, 3, tzinfo=UTC)
EVALUATION_END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
COSTS = {"1x": 1.0, "2x": 2.0, "3x": 3.0}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_json_gz(path: Path, payload: object) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return raw


def _read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return raw


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _design_identity() -> dict[str, object]:
    current_branch = _git("branch", "--show-current")
    design_tip = _git("rev-parse", DESIGN_BRANCH)
    main_sha = _git("rev-parse", "main")
    origin_main_sha = _git("rev-parse", "origin/main")
    hashes = {name: sha256_file(DESIGN_DIR / name) for name in DESIGN_ARTIFACTS}
    if current_branch != ECONOMIC_BRANCH:
        raise RuntimeError(f"Economic run must execute on {ECONOMIC_BRANCH}, got {current_branch}")
    if design_tip != DESIGN_COMMIT:
        raise RuntimeError(f"Frozen design tip changed: {design_tip}")
    if main_sha != origin_main_sha:
        raise RuntimeError("main and origin/main diverged before the economic run")
    if hashes != DESIGN_HASHES:
        raise RuntimeError("Frozen design artifact hash changed")
    return {
        "repo_root": str(REPO_ROOT),
        "economic_branch": current_branch,
        "design_branch": DESIGN_BRANCH,
        "design_branch_sha": design_tip,
        "main_sha": main_sha,
        "origin_main_sha": origin_main_sha,
        "economic_branch_parent": DESIGN_COMMIT,
        "design_artifacts": {
            name: {"path": str(DESIGN_DIR / name), "sha256": hashes[name]} for name in DESIGN_ARTIFACTS
        },
    }


def _config_identity(config: Config) -> dict[str, object]:
    equity = config.strategy.equity
    scanner = config.universe.equity.scanner
    return {
        "path": str(REPO_ROOT / "config" / "settings.yaml"),
        "sha256": sha256_file(REPO_ROOT / "config" / "settings.yaml"),
        "runtime_mode": config.runtime.mode.value,
        "live_enabled": config.live.enabled,
        "live_hard_cap": config.live.hard_dollar_cap,
        "equity_strategy": {
            "name": equity.name,
            "parameters": dict(equity.parameters),
            "maximum_allocation": equity.maximum_allocation,
            "timeframe": equity.timeframe,
        },
        "risk": asdict(config.risk),
        "execution": asdict(config.execution),
        "backtest": asdict(config.backtest),
        "scanner": {
            "maximum_stream_symbols": config.universe.equity.maximum_stream_symbols,
            "target_shortlist": config.universe.equity.target_shortlist,
            "reference_symbols": list(config.universe.equity.reference_symbols),
            "inverse_trade_symbols": list(config.universe.equity.inverse_trade_symbols),
            "supported_exchanges": list(config.universe.equity.supported_exchanges),
            "minimum_price": scanner.minimum_price,
            "minimum_median_dollar_volume": scanner.minimum_median_dollar_volume,
            "minimum_data_completeness": scanner.minimum_data_completeness,
            "minimum_atr_pct": scanner.minimum_atr_pct,
            "maximum_atr_pct": scanner.maximum_atr_pct,
            "maximum_per_sector": scanner.maximum_per_sector,
            "spread_quality_ceiling_bps": scanner.spread_quality_ceiling_bps,
        },
        "production_change_guard": {
            "registry_changed": False,
            "config_changed": False,
            "paper_changed": False,
            "live_changed": False,
        },
    }


def _assert_data_feasibility(dataset: NormalizedDataset) -> None:
    bars = dataset.structural_audit["bars"]
    spy = dataset.structural_audit["spy"]
    lifecycle = dataset.structural_audit["lifecycle"]
    corporate = dataset.structural_audit["corporate_actions"]
    assert isinstance(bars, dict) and isinstance(spy, dict)
    assert isinstance(lifecycle, dict) and isinstance(corporate, dict)
    expected = _read_json(DESIGN_DIR / "DATA_FEASIBILITY.json")
    expected_dataset = expected["dataset"]
    expected_spy = expected["spy"]
    expected_lifecycle = expected["lifecycle_metadata"]
    expected_actions = expected["corporate_actions"]
    assert isinstance(expected_dataset, dict)
    assert isinstance(expected_spy, dict)
    assert isinstance(expected_lifecycle, dict)
    assert isinstance(expected_actions, dict)
    checks = (
        (bars.get("rows"), EXPECTED_BAR_ROWS),
        (bars.get("sha256"), EXPECTED_BAR_SHA256),
        (bars.get("unique_symbols"), expected_dataset.get("unique_symbols")),
        (spy.get("bars"), expected_spy.get("total_bars")),
        (lifecycle.get("rows"), EXPECTED_LIFETIME_ROWS),
        (lifecycle.get("sha256"), EXPECTED_LIFETIME_SHA256),
        (corporate.get("rows"), EXPECTED_CORPORATE_ACTION_ROWS),
        (corporate.get("sha256"), EXPECTED_CORPORATE_ACTION_SHA256),
        (corporate.get("cash_dividend_rows"), EXPECTED_DIVIDEND_ROWS),
        (corporate.get("split_action_rows"), EXPECTED_SPLIT_ROWS),
    )
    if any(left != right for left, right in checks):
        raise RuntimeError(f"Normalized data does not match DATA_FEASIBILITY.json: {checks}")
    if bars.get("duplicate_symbol_session_rows") != 0 or bars.get("conflicting_duplicate_rows") != 0:
        raise RuntimeError("Canonical duplicate/conflict detected")
    if (
        bars.get("nonfinite_rows") != 0
        or bars.get("nonpositive_open_count") != 0
        or bars.get("nonpositive_close_count") != 0
    ):
        raise RuntimeError("Invalid price quality detected")
    if float(spy.get("dataset_session_date_coverage_pct", 0.0)) < 99.0:
        raise RuntimeError("SPY session coverage is below the frozen 99% floor")


def _run_focused_tests() -> dict[str, object]:
    command = [sys.executable, "-m", "pytest", "tests/test_overnight_reversal_v1.py", "-q"]
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    output = (completed.stdout + "\n" + completed.stderr).strip()
    matches = re.findall(r"(\d+) passed", output)
    passed = int(matches[-1]) if matches else 0
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "passed_count": passed,
        "output_tail": output[-4000:],
        "status": "PASS" if completed.returncode == 0 and passed >= 20 else "FAIL",
    }


def preflight() -> dict[str, Any]:
    """Run the frozen pre-economic validation and write its receipt."""
    result_paths = (
        RESEARCH_DIR / "run_manifest.json",
        RESEARCH_DIR / "decision.json",
        RESEARCH_DIR / "gate_table.json",
    )
    if any(path.exists() for path in result_paths) or any(RESEARCH_DIR.glob("case_*.json.gz")):
        raise RuntimeError("Economic outputs already exist; the frozen run cannot be restarted or rescued")
    identity = _design_identity()
    config = load_config(str(REPO_ROOT / "config" / "settings.yaml"))
    tests = _run_focused_tests()
    if tests["status"] != "PASS":
        _write_json(RESEARCH_DIR / "PRE_RUN_VALIDATION.json", {"status": "FAIL", "tests": tests})
        raise RuntimeError("Focused pre-economic tests did not pass")
    dataset = load_normalized_dataset(BAR_PATH, LIFETIME_PATH, CORPORATE_ACTION_PATH)
    _assert_data_feasibility(dataset)
    receipt = {
        **identity,
        "status": "FROZEN_INPUTS_RECEIVED",
        "economic_outcomes_inspected_before_run": False,
        "backtest_run_before_run": False,
        "result_artifacts_present_before_run": False,
        "data": dataset.structural_audit,
        "data_paths": {
            "bars": str(BAR_PATH),
            "lifetimes": str(LIFETIME_PATH),
            "corporate_actions": str(CORPORATE_ACTION_PATH),
        },
        "config": _config_identity(config),
        "frozen_candidate": {
            "name": CANDIDATE_NAME,
            "version": CANDIDATE_VERSION,
            "parameters": FROZEN_PARAMETERS,
            "formula": "score = -(Open_i,d / Close_i,p(d) - 1 - (Open_SPY,d / Close_SPY,p(d) - 1))",
            "decision": "after completed regular daily bar d",
            "fill": "next tradable daily-bar provider-open proxy Open(n(d))",
        },
    }
    _write_json(RESEARCH_DIR / "FROZEN_INPUT_RECEIPT.json", receipt)
    pre_run = {
        "status": "PASS",
        "frozen_design": identity,
        "data_hashes": {
            "bars": sha256_file(BAR_PATH),
            "lifetimes": sha256_file(LIFETIME_PATH),
            "corporate_actions": sha256_file(CORPORATE_ACTION_PATH),
        },
        "normalized_row_counts": dataset.structural_audit,
        "spy_coverage": dataset.structural_audit["spy"],
        "ohlc_validity": dataset.structural_audit["bars"],
        "duplicate_conflict_counts": {
            "duplicates": dataset.structural_audit["bars"]["duplicate_symbol_session_rows"],
            "conflicts": dataset.structural_audit["bars"]["conflicting_duplicate_rows"],
        },
        "unit_contract_tests": tests,
        "no_prior_output_inspected": True,
        "economic_run_authorized": True,
    }
    _write_json(RESEARCH_DIR / "PRE_RUN_VALIDATION.json", pre_run)
    del dataset
    return pre_run


def _prepared_inputs(dataset: NormalizedDataset) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object]:
    available = [bar for bar in dataset.bars if bar.timestamp <= EVALUATION_END]
    filtered = [bar for bar in available if bar.timestamp >= EVALUATION_START]
    warmup = [bar for bar in available if bar.timestamp < EVALUATION_START]
    ordered = sorted(filtered, key=lambda item: (item.timestamp, item.symbol))
    warmup.sort(key=lambda item: (item.timestamp, item.symbol))
    from parayabuck.backtest.numeric_history import build_numeric_history

    numeric = build_numeric_history(available, market=Market.EQUITY, timeframe="1d")
    return filtered, warmup, (ordered, ordered), numeric


def _build_universes(config: Config, dataset: NormalizedDataset) -> tuple[PointInTimeUniverse, PointInTimeUniverse]:
    control = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe="1d",
    )
    candidate = OvernightReversalStrategy()
    common = {
        "config": config,
        "bars": dataset.bars,
        "market": Market.EQUITY,
        "end": EVALUATION_END,
        "timeframe": "1d",
    }
    universe_a = build_product_universe(**common, observation_strategy=control)
    universe_b = build_product_universe(**common, observation_strategy=candidate)
    if len(universe_a.snapshots) != len(universe_b.snapshots):
        raise RuntimeError("Control and candidate PIT snapshot counts differ")
    for left, right in zip(universe_a.snapshots, universe_b.snapshots, strict=True):
        if left.effective_at != right.effective_at or left.symbols != right.symbols:
            raise RuntimeError("Control and candidate eligible PIT pools differ")
    return universe_a, universe_b


def _eligible_pool_digest(universe: PointInTimeUniverse) -> str:
    digest = hashlib.sha256()
    for snapshot in universe.snapshots:
        digest.update(snapshot.effective_at.isoformat().encode())
        digest.update(b"|")
        digest.update("|".join(sorted(snapshot.symbols)).encode())
        digest.update(b"|")
        digest.update(snapshot.scanner_version.encode())
        digest.update(b"|")
        digest.update(snapshot.config_digest.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _write_universe_artifacts(universe_a: PointInTimeUniverse, universe_b: PointInTimeUniverse) -> dict[str, object]:
    _write_json_gz(RESEARCH_DIR / "universe_A.json.gz", universe_a.to_dict())
    _write_json_gz(RESEARCH_DIR / "universe_B.json.gz", universe_b.to_dict())
    with gzip.open(RESEARCH_DIR / "observation_set_audit.jsonl.gz", "wt", encoding="utf-8", newline="") as handle:
        for left, right in zip(universe_a.snapshots, universe_b.snapshots, strict=True):
            handle.write(
                json.dumps(
                    {
                        "effective_at": left.effective_at.isoformat(),
                        "eligible_pool_size": len(left.symbols),
                        "eligible_pool_hash": hashlib.sha256("|".join(sorted(left.symbols)).encode()).hexdigest(),
                        "control_A_observation_set": list(left.observation_set),
                        "candidate_B_observation_set": list(right.observation_set),
                        "control_A_observation_strategy": left.observation_strategy_name,
                        "candidate_B_observation_strategy": right.observation_strategy_name,
                        "control_A_observation_size": len(left.observation_set),
                        "candidate_B_observation_size": len(right.observation_set),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    return {
        "control_A_universe_hash": hash_point_in_time_universe(universe_a),
        "candidate_B_universe_hash": hash_point_in_time_universe(universe_b),
        "eligible_pool_hash": _eligible_pool_digest(universe_b),
        "snapshot_count": len(universe_b.snapshots),
        "control_A_file": "universe_A.json.gz",
        "candidate_B_file": "universe_B.json.gz",
        "observation_audit_file": "observation_set_audit.jsonl.gz",
    }


def _audit_signal_rows(dataset: NormalizedDataset, universe: PointInTimeUniverse) -> dict[str, Any]:
    """Persist the full candidate-pool signal reconstruction before P&L."""
    spy_bars = dataset.bars_by_symbol.get("SPY", ())
    spy_by_day = {session_date(bar.timestamp): bar for bar in spy_bars}
    pool_rows = 0
    valid_pairs = 0
    selected_count = 0
    decision_count = 0
    incomplete_decisions = 0
    eligible_counts: list[int] = []
    observation_counts: list[int] = []
    selected_counts: list[int] = []
    reason_counts: Counter[str] = Counter()
    fieldnames = [
        "decision_timestamp",
        "decision_date",
        "prior_date",
        "symbol",
        "prior_close",
        "current_open",
        "stock_overnight",
        "spy_overnight",
        "spy_excess",
        "score",
        "eligibility",
        "reason",
        "rank",
        "observation_rank",
        "observation_member",
        "decision_complete",
        "selected",
        "selected_reason",
    ]
    with gzip.open(RESEARCH_DIR / "signal_audit.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for snapshot in universe.snapshots:
            decision_count += 1
            decision_day = session_date(snapshot.effective_at)
            prior_day = dataset.previous_spy_date(decision_day)
            observation = tuple(snapshot.observation_set)
            observation_set = set(observation)
            decision_complete = all(dataset.bar_for(symbol, decision_day) is not None for symbol in observation)
            if not decision_complete:
                incomplete_decisions += 1
            rows = []
            for symbol in sorted(snapshot.symbols):
                if symbol == "SPY":
                    row = signal_from_bars("SPY", decision_day, prior_day, spy_by_day, spy_by_day)
                    row = replace(row, eligibility="reference_only", reason="reference_only")
                else:
                    stock_by_day = {session_date(bar.timestamp): bar for bar in dataset.bars_by_symbol.get(symbol, ())}
                    row = signal_from_bars(symbol, decision_day, prior_day, stock_by_day, spy_by_day)
                rows.append(row)
            valid = sorted(
                (row for row in rows if row.score is not None and row.symbol != "SPY"),
                key=lambda row: (-float(row.score), row.symbol),
            )
            rank = {row.symbol: index for index, row in enumerate(valid, start=1)}
            observed_valid = [row for row in valid if row.symbol in observation_set]
            obs_rank = {row.symbol: index for index, row in enumerate(observed_valid, start=1)}
            eligible_counts.append(len(valid))
            observation_counts.append(len(observation))
            selected_counts.append(0)
            for row in rows:
                is_observed = row.symbol in observation_set
                row_rank = rank.get(row.symbol)
                row_obs_rank = obs_rank.get(row.symbol)
                selected = False
                selected_reason = "reference_only" if row.symbol == "SPY" else row.reason
                if row.symbol != "SPY":
                    if not is_observed:
                        selected_reason = "outside_observation_set"
                    elif not decision_complete:
                        selected_reason = "decision_incomplete_observation_set"
                    elif row.score is not None and row_obs_rank is not None and row_obs_rank <= 6:
                        selected = True
                        selected_reason = "top_n6"
                        selected_count += 1
                        selected_counts[-1] += 1
                    elif row.score is not None:
                        selected_reason = "not_selected_rank"
                reason_counts[selected_reason] += 1
                pool_rows += 1
                if row.score is not None and row.symbol != "SPY":
                    valid_pairs += 1
                payload = row.to_dict()
                payload.update(
                    {
                        "decision_timestamp": snapshot.effective_at.isoformat(),
                        "rank": row_rank,
                        "observation_rank": row_obs_rank,
                        "observation_member": is_observed,
                        "decision_complete": decision_complete,
                        "selected": selected,
                        "selected_reason": selected_reason,
                    }
                )
                writer.writerow(payload)
    # ``total_non_reference`` is the full pool denominator, not the count of
    # valid scores; the audit reports every exclusion explicitly.
    total_non_reference = sum(
        len(snapshot.symbols) - (1 if "SPY" in snapshot.symbols else 0) for snapshot in universe.snapshots
    )
    return {
        "file": "signal_audit.csv.gz",
        "decision_count": decision_count,
        "pool_row_count": pool_rows,
        "non_reference_pool_observations": total_non_reference,
        "valid_common_pair_count": valid_pairs,
        "valid_common_pair_pct": valid_pairs / total_non_reference if total_non_reference else 0.0,
        "average_valid_eligible_names": sum(eligible_counts) / len(eligible_counts) if eligible_counts else 0.0,
        "average_observation_set_size": sum(observation_counts) / len(observation_counts)
        if observation_counts
        else 0.0,
        "average_selected_names": sum(selected_counts) / len(selected_counts) if selected_counts else 0.0,
        "selected_row_count": selected_count,
        "incomplete_observation_decisions": incomplete_decisions,
        "reason_counts": dict(sorted(reason_counts.items())),
    }


class FeeScaledPortfolioBacktestEngine(PortfolioBacktestEngine):
    """Research-only engine shim that scales effective-dated fees too."""

    def __init__(self, *args: object, fee_multiplier: float, **kwargs: object) -> None:
        if fee_multiplier <= 0 or not math.isfinite(fee_multiplier):
            raise ValueError("fee_multiplier must be positive and finite")
        self.fee_multiplier = fee_multiplier
        super().__init__(*args, **kwargs)

    def _equity_fee(self, side: OrderSide, quantity: float, price: float, trade_date: date) -> float:
        return float(super()._equity_fee(side, quantity, price, trade_date)) * self.fee_multiplier


def _fill_dict(fill: PortfolioFill) -> dict[str, object]:
    return {
        "timestamp": fill.timestamp.isoformat(),
        "decision_timestamp": fill.decision_timestamp.isoformat(),
        "symbol": fill.symbol,
        "market": fill.market.value,
        "side": fill.side.value,
        "qty": fill.qty,
        "reference_price": fill.reference_price,
        "fill_price": fill.fill_price,
        "slippage_cost": fill.slippage_cost,
        "spread_cost": fill.spread_cost,
        "fee": fill.fee,
        "client_order_id": fill.client_order_id,
    }


def _rebalance_dict(record: RebalanceRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["decision_timestamp"] = record.decision_timestamp.isoformat()
    return payload


def _holdings_dict(timestamp: datetime, values: tuple[int, float, float]) -> dict[str, object]:
    count, cash_ratio, concentration = values
    return {
        "timestamp": timestamp.isoformat(),
        "positions": count,
        "cash_ratio": cash_ratio,
        "concentration": concentration,
    }


def _case_payload(
    result: PortfolioBacktestResult,
    report: PortfolioReport,
    engine: FeeScaledPortfolioBacktestEngine,
    strategy: PortfolioStrategy,
    arm: str,
    cost_name: str,
    fee_multiplier: float,
) -> dict[str, Any]:
    result_fills = [_fill_dict(fill) for fill in result.fills]
    curves = [
        {"timestamp": timestamp.isoformat(), "equity": equity}
        for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
    ]
    holdings = [
        _holdings_dict(timestamp, values)
        for timestamp, values in zip(result.curve_timestamps, engine._holdings_history, strict=True)
    ]
    drawdowns: list[dict[str, object]] = []
    peak = result.starting_equity
    for point in curves:
        equity = float(point["equity"])
        peak = max(peak, equity)
        drawdowns.append({"timestamp": point["timestamp"], "drawdown": (peak - equity) / peak if peak > 0 else 0.0})
    benchmark = [{"timestamp": bar.timestamp.isoformat(), "close": bar.close} for bar in result.benchmark_bars]
    return {
        "arm": arm,
        "cost_scenario": cost_name,
        "cost_multiplier": fee_multiplier,
        "strategy_name": strategy.name,
        "strategy_version": strategy.version,
        "strategy_parameters": dict(strategy.parameter_tuple()),
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "summary": dict(report.summary),
        "rebalances": [_rebalance_dict(record) for record in result.rebalances],
        "fills": result_fills,
        "raw_targets": [target.to_dict() for target in getattr(strategy, "recorded_targets", ())],
        "weight_history": [
            {"timestamp": timestamp.isoformat(), "weights": weights} for timestamp, weights in result.weight_history
        ],
        "holdings_history": holdings,
        "equity_curve": curves,
        "drawdowns": drawdowns,
        "benchmark_bars": benchmark,
        "per_symbol": [asdict(contribution) for contribution in result.per_symbol],
        "realized_pnl_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": pnl}
            for timestamp, symbol, pnl in result.realized_pnl_events
        ],
        "closed_position_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": pnl}
            for timestamp, symbol, pnl in result.closed_position_events
        ],
        "terminal_events": list(result.terminal_events),
        "dividend_total": result.dividend_total,
        "cost_totals": {
            "slippage": result.slippage_cost_total,
            "spread": result.spread_cost_total,
            "fees": result.fee_total,
            "transaction_costs": result.slippage_cost_total + result.spread_cost_total + result.fee_total,
        },
        "universe_hash": result.rebalances[0].universe_version if result.rebalances else "",
        "fill_model": "decision on completed daily bar; next tradable provider-open proxy",
        "fee_model": {
            "fee_multiplier": fee_multiplier,
            "effective_dated_schedule": True,
            "commission": 0.0,
        },
    }


def _run_case(
    config: Config,
    dataset: NormalizedDataset,
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]], object],
    universe: PointInTimeUniverse,
    arm: str,
    cost_name: str,
    multiplier: float,
) -> dict[str, Any]:
    filtered, warmup, ordered_inputs, numeric = prepared
    if arm == "A":
        strategy = portfolio_strategy_from(
            config.strategy.equity.name,
            config.strategy.equity.parameters,
            timeframe="1d",
        )
        policy = RebalancePolicy(Market.EQUITY, "weekly")
    else:
        strategy = OvernightReversalStrategy()
        policy = DailySessionRebalancePolicy()
    engine = FeeScaledPortfolioBacktestEngine(
        strategy,
        policy,
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * multiplier,
        spread_bps=config.backtest.spread_bps * multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=dataset.terminal_lifecycles,
        fee_multiplier=multiplier,
    )
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=dataset.corporate_actions,
        prepared_inputs=ordered_inputs,
        numeric_history=(numeric if arm == "A" else None),
        liquidate_at_end=True,
    )
    report = build_portfolio_report(result, strategy_name=strategy.name, strategy_version=strategy.version)
    report.summary.update(
        {
            "cost_scenario": cost_name,
            "cost_multiplier": multiplier,
            "fee_multiplier": multiplier,
            "data_feed": "SIP",
            "data_feed_note": "SIP consolidated historical bars; provider daily open is a regular-sale open proxy, not an auction claim.",
            "price_adjustment_model": "split-adjusted OHLC; explicit cash dividends applied once; split actions not applied",
            "production_registry_used": False,
            "paper_or_live": False,
        }
    )
    payload = _case_payload(result, report, engine, strategy, arm, cost_name, multiplier)
    _write_json_gz(RESEARCH_DIR / f"case_{arm}_{cost_name}.json.gz", payload)
    return payload


def _curve_points(case: dict[str, Any]) -> list[tuple[datetime, float]]:
    raw = case["equity_curve"]
    if not isinstance(raw, list):
        raise ValueError("case equity_curve is not a list")
    return [(_parse_timestamp(str(point["timestamp"])), float(point["equity"])) for point in raw]


def _sample_std(values: list[float]) -> float:
    return stdev(values) if len(values) >= 2 else 0.0


def _metrics_for_window(case: dict[str, Any], start_day: date, end_day: date) -> dict[str, Any]:
    points = _curve_points(case)
    if not points:
        raise ValueError("case has no equity curve")
    prior = [(timestamp, equity) for timestamp, equity in points if session_date(timestamp) < start_day]
    in_window = [(timestamp, equity) for timestamp, equity in points if start_day <= session_date(timestamp) <= end_day]
    if not in_window:
        raise ValueError(f"case has no curve points in {start_day}..{end_day}")
    if prior:
        anchor = prior[-1]
    else:
        anchor = (_parse_timestamp(str(case["summary"]["start_date"])), float(case["starting_equity"]))
    series = [anchor] + [point for point in in_window if point[0] != anchor[0]]
    ending = series[-1][1]
    duration_days = max(1, int((series[-1][0] - series[0][0]).total_seconds() / 86_400))
    years = duration_days / 365.0
    total_return = ending / anchor[1] - 1.0 if anchor[1] > 0 else 0.0
    cagr = (ending / anchor[1]) ** (1 / years) - 1.0 if anchor[1] > 0 and ending > 0 else 0.0
    returns = [
        series[index][1] / series[index - 1][1] - 1.0 for index in range(1, len(series)) if series[index - 1][1] > 0
    ]
    volatility = _sample_std(returns)
    annualizer = math.sqrt(len(returns) / years) if returns and years > 0 else 1.0
    mean_return = sum(returns) / len(returns) if returns else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = _sample_std(downside)
    peak = anchor[1]
    max_drawdown = 0.0
    for _timestamp, equity in series:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    costs = {"slippage": 0.0, "spread": 0.0, "fees": 0.0}
    fill_cost_fields = {"slippage": "slippage_cost", "spread": "spread_cost", "fees": "fee"}
    fills = []
    raw_fills = case.get("fills", [])
    if isinstance(raw_fills, list):
        for raw in raw_fills:
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            if start_day <= session_date(timestamp) <= end_day:
                fills.append(raw)
                for key, field in fill_cost_fields.items():
                    costs[key] += float(raw.get(field, 0.0))
    terminal_costs = {"slippage": 0.0, "spread": 0.0, "fees": 0.0}
    raw_terminal = case.get("terminal_events", [])
    if isinstance(raw_terminal, list):
        for raw in raw_terminal:
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            if start_day <= session_date(timestamp) <= end_day:
                terminal_costs["slippage"] += float(raw.get("slippage_cost", 0.0))
                terminal_costs["spread"] += float(raw.get("spread_cost", 0.0))
                terminal_costs["fees"] += float(raw.get("fee", 0.0))
    for cost_key in costs:
        costs[cost_key] += terminal_costs[cost_key]
    transaction_costs = sum(costs.values())
    raw_rebalances = case.get("rebalances", [])
    rebalances = []
    if isinstance(raw_rebalances, list):
        rebalances = [
            raw
            for raw in raw_rebalances
            if start_day <= session_date(_parse_timestamp(str(raw["decision_timestamp"]))) <= end_day
        ]
    expected_turnover = sum(float(raw.get("expected_turnover", 0.0)) for raw in rebalances)
    realized_turnover_notional = sum(abs(float(raw["qty"]) * float(raw["reference_price"])) for raw in fills)
    average_equity = sum(equity for _timestamp, equity in series) / len(series)
    realized_turnover = realized_turnover_notional / average_equity if average_equity > 0 else 0.0
    annualized_target_turnover = expected_turnover / years if years > 0 else 0.0
    annualized_realized_turnover = realized_turnover / years if years > 0 else 0.0
    monthly_ends: dict[tuple[int, int], float] = {}
    for timestamp, equity in series[1:]:
        monthly_ends[(timestamp.year, timestamp.month)] = equity
    monthly_returns: dict[tuple[int, int], float] = {}
    previous = anchor[1]
    for month_key in sorted(monthly_ends):
        monthly_returns[month_key] = monthly_ends[month_key] / previous - 1.0 if previous > 0 else 0.0
        previous = monthly_ends[month_key]
    month_values = list(monthly_returns.values())
    raw_events = case.get("realized_pnl_events", [])
    realized_events = []
    if isinstance(raw_events, list):
        realized_events = [
            raw for raw in raw_events if start_day <= session_date(_parse_timestamp(str(raw["timestamp"]))) <= end_day
        ]
    pnl_values = [float(raw["pnl"]) for raw in realized_events]
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    holdings = case.get("holdings_history", [])
    holdings_in_window = []
    if isinstance(holdings, list):
        holdings_in_window = [
            raw for raw in holdings if start_day <= session_date(_parse_timestamp(str(raw["timestamp"]))) <= end_day
        ]
    average_cash = (
        sum(float(raw["cash_ratio"]) for raw in holdings_in_window) / len(holdings_in_window)
        if holdings_in_window
        else 1.0
    )
    average_positions = (
        sum(int(raw["positions"]) for raw in holdings_in_window) / len(holdings_in_window)
        if holdings_in_window
        else 0.0
    )
    max_concentration = max((float(raw["concentration"]) for raw in holdings_in_window), default=0.0)
    max_positions = max((int(raw["positions"]) for raw in holdings_in_window), default=0)
    max_target_weight = max(
        (float(weight) for raw in rebalances for _symbol, weight in raw.get("target_weights", ())),
        default=0.0,
    )
    benchmark = case.get("benchmark_bars", [])
    benchmark_points = []
    if isinstance(benchmark, list):
        benchmark_points = [(_parse_timestamp(str(raw["timestamp"])), float(raw["close"])) for raw in benchmark]
    benchmark_prior = [
        (timestamp, close) for timestamp, close in benchmark_points if session_date(timestamp) < start_day
    ]
    benchmark_window = [
        (timestamp, close) for timestamp, close in benchmark_points if start_day <= session_date(timestamp) <= end_day
    ]
    benchmark_return = 0.0
    if benchmark_window:
        benchmark_anchor = benchmark_prior[-1][1] if benchmark_prior else benchmark_window[0][1]
        benchmark_return = benchmark_window[-1][1] / benchmark_anchor - 1.0 if benchmark_anchor > 0 else 0.0
    return {
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "starting_equity": anchor[1],
        "ending_equity": ending,
        "net_total_return": total_return,
        "net_return": total_return,
        "benchmark_return": benchmark_return,
        "cagr": cagr,
        "annualized_volatility": volatility * annualizer if volatility > 0 else 0.0,
        "sharpe": mean_return / volatility * annualizer if volatility > 0 else 0.0,
        "sortino": mean_return / downside_std * annualizer if downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month_return": min(month_values, default=0.0),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "annualized_one_way_target_turnover": annualized_target_turnover,
        "annualized_one_way_realized_turnover": annualized_realized_turnover,
        "target_turnover": expected_turnover,
        "realized_turnover": realized_turnover,
        "gross_notional": sum(abs(float(raw["qty"]) * float(raw["reference_price"])) for raw in fills),
        "realized_transaction_costs": transaction_costs,
        "slippage_cost": costs["slippage"],
        "spread_cost": costs["spread"],
        "fee_cost": costs["fees"],
        "costs_pct_starting_capital": transaction_costs / anchor[1] if anchor[1] > 0 else 0.0,
        "costs_pct_gross_gain": transaction_costs / max(0.0, ending - anchor[1]) if ending > anchor[1] else None,
        "fill_count": len(fills),
        "rebalance_count": len(rebalances),
        "decision_count": len(rebalances),
        "average_exposure": max(0.0, 1.0 - average_cash),
        "average_positions": average_positions,
        "max_positions": max_positions,
        "max_concentration": max_concentration,
        "max_target_weight": max_target_weight,
        "positive_month_ratio": sum(value > 0 for value in month_values) / len(month_values) if month_values else 0.0,
        "duration_days": duration_days,
        "realized_pnl": sum(pnl_values),
        "dividend_total": float(case.get("dividend_total", 0.0)),
    }


def _metrics_all_cases(cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, case in cases.items():
        arm, cost = key.split("/", 1)
        by_period: dict[str, Any] = {}
        for period, (start, end) in WINDOWS.items():
            if period == "warmup":
                continue
            by_period[period] = _metrics_for_window(case, start, end)
        output[key] = {"arm": arm, "cost_scenario": cost, "periods": by_period}
    return output


def _snapshot_map(universe: PointInTimeUniverse) -> dict[date, Any]:
    return {session_date(snapshot.effective_at): snapshot for snapshot in universe.snapshots}


def _history_length(dataset: NormalizedDataset, symbol: str, day: date) -> int:
    from bisect import bisect_right

    dates = dataset.session_dates_by_symbol.get(symbol, ())
    return bisect_right(dates, day)


def _coverage(
    case: dict[str, Any],
    dataset: NormalizedDataset,
    universe: PointInTimeUniverse,
    arm: str,
    signal_summary: dict[str, Any],
) -> dict[str, Any]:
    start, end = WINDOWS["full"]
    scheduled_days = [day for day in dataset.spy_session_dates if start <= day <= end]
    expected_days: list[date] = []
    previous_period: tuple[int, ...] | None = None
    for day in scheduled_days:
        period = (day.year, day.month, day.day) if arm == "B" else (day.isocalendar().year, day.isocalendar().week)
        if period != previous_period:
            expected_days.append(day)
            previous_period = period
    raw_rebalances = case.get("rebalances", [])
    actual_days = (
        {session_date(_parse_timestamp(str(raw["decision_timestamp"]))) for raw in raw_rebalances}
        if isinstance(raw_rebalances, list)
        else set()
    )
    snapshots = _snapshot_map(universe)
    skipped: dict[str, int] = Counter()
    for day in expected_days:
        if day in actual_days:
            continue
        snapshot = snapshots.get(day)
        if snapshot is None:
            skipped["missing_pit_snapshot"] += 1
            continue
        observation = tuple(snapshot.observation_set)
        if any(dataset.bar_for(symbol, day) is None for symbol in observation):
            skipped["incomplete_observation_set"] += 1
            continue
        minimum = 2 if arm == "B" else 274
        if max((_history_length(dataset, symbol, day) for symbol in observation), default=0) < minimum:
            skipped["insufficient_strategy_history"] += 1
            continue
        skipped["unexplained"] += 1
    actual_sorted = sorted(actual_days)
    max_gap = 0
    for left, right in zip(actual_sorted, actual_sorted[1:]):
        max_gap = max(max_gap, (right - left).days)
    spy_dates = list(dataset.spy_session_dates)
    scheduled_notional = 0.0
    filled_within_two = 0.0
    explicit_outstanding = 0.0
    unexplained_notional = 0.0
    fill_completion_records = []
    if isinstance(raw_rebalances, list):
        raw_fills = case.get("fills", [])
        fills = raw_fills if isinstance(raw_fills, list) else []
        for record in raw_rebalances:
            decision_timestamp = _parse_timestamp(str(record["decision_timestamp"]))
            decision_day = session_date(decision_timestamp)
            expected_notional = float(record.get("expected_turnover", 0.0)) * float(
                record.get("equity_at_decision", 0.0)
            )
            same = [raw for raw in fills if str(raw["decision_timestamp"]) == str(record["decision_timestamp"])]
            future_dates = [day for day in spy_dates if day > decision_day]
            cutoff = future_dates[1] if len(future_dates) >= 2 else future_dates[-1] if future_dates else decision_day
            filled = sum(
                abs(float(raw["qty"]) * float(raw["reference_price"]))
                for raw in same
                if session_date(_parse_timestamp(str(raw["timestamp"]))) <= cutoff
            )
            outstanding = max(0.0, float(record.get("unfilled_notional", 0.0)))
            unexplained = max(0.0, expected_notional - filled - outstanding)
            scheduled_notional += expected_notional
            filled_within_two += min(expected_notional, filled)
            explicit_outstanding += min(max(0.0, expected_notional - filled), outstanding)
            unexplained_notional += unexplained
            fill_completion_records.append(
                {
                    "decision_timestamp": record["decision_timestamp"],
                    "scheduled_target_notional": expected_notional,
                    "filled_within_two_sessions": filled,
                    "explicit_outstanding": outstanding,
                    "unexplained_notional": unexplained,
                }
            )
    coverage_ratio = (filled_within_two + explicit_outstanding) / scheduled_notional if scheduled_notional > 0 else 1.0
    skipped_total = sum(skipped.values())
    return {
        "arm": arm,
        "period": "full",
        "scheduled_decisions": len(expected_days),
        "actual_decisions": len(actual_days),
        "skipped_decisions": skipped_total,
        "skipped_reasons": dict(sorted(skipped.items())),
        "unexplained_skip_count": skipped.get("unexplained", 0),
        "skip_ratio": skipped_total / len(expected_days) if expected_days else 0.0,
        "first_decision": min(actual_days).isoformat() if actual_days else None,
        "last_decision": max(actual_days).isoformat() if actual_days else None,
        "longest_actual_decision_gap_days": max_gap,
        "scheduled_target_notional": scheduled_notional,
        "filled_within_two_sessions_notional": filled_within_two,
        "explicit_outstanding_notional": explicit_outstanding,
        "unexplained_notional": unexplained_notional,
        "target_notional_coverage_ratio": coverage_ratio,
        "target_notional_within_two_ratio": filled_within_two / scheduled_notional if scheduled_notional > 0 else 1.0,
        "fill_completion_sample_count": len(fill_completion_records),
        "fill_completion_records": fill_completion_records,
        "valid_overnight_cross_sections": signal_summary.get("decision_count", 0),
        "average_valid_eligible_names": signal_summary.get("average_valid_eligible_names", 0.0),
        "average_observation_set_size": signal_summary.get("average_observation_set_size", 0.0),
        "average_selected_names": signal_summary.get("average_selected_names", 0.0),
    }


def _concentration(case: dict[str, Any], validation_metrics: dict[str, Any]) -> dict[str, Any]:
    events = case.get("realized_pnl_events", [])
    by_symbol: defaultdict[str, float] = defaultdict(float)
    if isinstance(events, list):
        for raw in events:
            day = session_date(_parse_timestamp(str(raw["timestamp"])))
            if WINDOWS["full"][0] <= day <= WINDOWS["full"][1]:
                by_symbol[str(raw["symbol"])] += float(raw["pnl"])
    total_abs = sum(abs(value) for value in by_symbol.values())
    dominant_symbol, dominant_pnl = max(
        by_symbol.items(), key=lambda item: (abs(item[1]), item[0]), default=(None, 0.0)
    )
    adjusted_end = float(validation_metrics["ending_equity"])
    if dominant_symbol is not None:
        val_pnl = 0.0
        if isinstance(events, list):
            for raw in events:
                day = session_date(_parse_timestamp(str(raw["timestamp"])))
                if (
                    WINDOWS["validation"][0] <= day <= WINDOWS["validation"][1]
                    and str(raw["symbol"]) == dominant_symbol
                ):
                    val_pnl += float(raw["pnl"])
        adjusted_end -= val_pnl
    years = float(validation_metrics["duration_days"]) / 365.0
    adjusted_cagr = (
        (adjusted_end / float(validation_metrics["starting_equity"])) ** (1 / years) - 1.0
        if years > 0 and adjusted_end > 0 and float(validation_metrics["starting_equity"]) > 0
        else 0.0
    )
    raw_rebalances = case.get("rebalances", [])
    max_sector_count = 0
    unique_symbols: set[str] = set(by_symbol)
    target_max_position = 0.0
    if isinstance(raw_rebalances, list):
        for record in raw_rebalances:
            sector_counts: Counter[str] = Counter()
            for symbol, weight in record.get("target_weights", ()):
                symbol_str = str(symbol)
                unique_symbols.add(symbol_str)
                target_max_position = max(target_max_position, float(weight))
                sector_counts[sector_of(symbol_str)] += 1
            max_sector_count = max(max_sector_count, max(sector_counts.values(), default=0))
    return {
        "unique_symbols": len(unique_symbols),
        "realized_pnl_by_symbol": dict(sorted(by_symbol.items())),
        "largest_absolute_realized_contributor": dominant_symbol,
        "largest_absolute_realized_pnl": dominant_pnl,
        "largest_absolute_contributor_share": abs(dominant_pnl) / total_abs if total_abs > 0 else 0.0,
        "target_max_position_weight": target_max_position,
        "max_sector_count_in_targets": max_sector_count,
        "sector_rule": "current scanner maximum five per sector",
        "leave_one_dominant_symbol_validation_cagr": adjusted_cagr,
        "leave_one_dominant_symbol_validation_ending_equity": adjusted_end,
        "leave_one_dominant_symbol": dominant_symbol,
    }


def _gate_table(
    metrics: dict[str, Any],
    coverage: dict[str, Any],
    signal_summary: dict[str, Any],
    concentration: dict[str, Any],
    preflight_payload: dict[str, Any],
) -> dict[str, Any]:
    def m(arm: str, cost: str, period: str) -> dict[str, Any]:
        return cast(dict[str, Any], metrics[f"{arm}/{cost}"]["periods"][period])

    a_full, b_full = m("A", "1x", "full"), m("B", "1x", "full")
    a_val, b_val = m("A", "1x", "validation"), m("B", "1x", "validation")
    b2_val, a3_val, b3_val = m("B", "2x", "validation"), m("A", "3x", "validation"), m("B", "3x", "validation")
    yearly = [m("B", "1x", str(year)) for year in (2022, 2023, 2024, 2025)]
    g3_full_values = [float(b_full[key]) - float(a_full[key]) for key in ("sharpe", "sortino", "calmar")]
    g3_val_values = [float(b_val[key]) - float(a_val[key]) for key in ("sharpe", "sortino", "calmar")]
    g3_full_pass = sum(value >= 0.05 for value in g3_full_values) >= 2 and all(
        value >= -0.05 for value in g3_full_values
    )
    g3_val_pass = sum(value >= 0.03 for value in g3_val_values) >= 1 and all(value >= -0.05 for value in g3_val_values)
    gates = {
        "G1": {
            "pass": preflight_payload.get("status") == "PASS"
            and float(signal_summary.get("valid_common_pair_pct", 0.0)) >= 0.95
            and int(coverage["B"].get("unexplained_skip_count", 1)) == 0
            and int(coverage["A"].get("unexplained_skip_count", 1)) == 0,
            "evidence": {
                "pre_run_status": preflight_payload.get("status"),
                "valid_common_pair_pct": signal_summary.get("valid_common_pair_pct"),
                "A_unexplained_skips": coverage["A"].get("unexplained_skip_count"),
                "B_unexplained_skips": coverage["B"].get("unexplained_skip_count"),
            },
        },
        "G2": {
            "pass": all(
                float(m("B", "1x", period)["cagr"]) > 0 and float(m("B", "1x", period)["net_total_return"]) > 0
                for period in ("full", "development", "validation")
            ),
            "evidence": {period: m("B", "1x", period) for period in ("full", "development", "validation")},
        },
        "G3": {
            "pass": g3_full_pass and g3_val_pass,
            "evidence": {"full_deltas": g3_full_values, "validation_deltas": g3_val_values},
        },
        "G4": {
            "pass": sum(float(item["net_total_return"]) > 0 for item in yearly) >= 3
            and all(float(item["net_total_return"]) >= -0.08 for item in yearly)
            and float(b_val["cagr"]) > 0,
            "evidence": {"years": yearly, "validation_cagr": b_val["cagr"]},
        },
        "G5": {
            "pass": float(b_full["max_drawdown"]) <= 0.10
            and float(b_val["max_drawdown"]) <= 0.10
            and float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) + 0.01
            and float(b_val["max_drawdown"]) <= float(a_val["max_drawdown"]) + 0.01,
            "evidence": {
                "full_B_vs_A": [b_full["max_drawdown"], a_full["max_drawdown"]],
                "validation_B_vs_A": [b_val["max_drawdown"], a_val["max_drawdown"]],
            },
        },
        "G6": {
            "pass": float(b2_val["cagr"]) > 0
            and float(b3_val["net_total_return"]) > -0.02
            and float(b3_val["max_drawdown"]) <= float(a3_val["max_drawdown"]) + 0.02,
            "evidence": {"B_2x_validation": b2_val, "B_3x_validation": b3_val, "A_3x_validation": a3_val},
        },
        "G7": {
            "pass": float(b_full["annualized_one_way_target_turnover"]) <= 20.0
            and float(m("B", "2x", "full")["annualized_one_way_target_turnover"])
            <= 1.50 * float(b_full["annualized_one_way_target_turnover"])
            and float(coverage["B"]["target_notional_coverage_ratio"]) >= 0.95
            and float(coverage["B"]["skip_ratio"]) <= 0.05,
            "evidence": {"B_1x_full": b_full, "B_2x_full": m("B", "2x", "full"), "coverage": coverage["B"]},
        },
        "G8": {
            "pass": float(concentration["target_max_position_weight"]) <= 0.10
            and float(concentration["largest_absolute_contributor_share"]) <= 0.20
            and float(concentration["leave_one_dominant_symbol_validation_cagr"]) > 0
            and int(concentration["max_sector_count_in_targets"]) <= 5,
            "evidence": concentration,
        },
        "G9": {
            "pass": all(
                float(metrics[f"{arm}/{cost}"]["periods"]["full"]["max_target_weight"]) <= 0.10
                and int(metrics[f"{arm}/{cost}"]["periods"]["full"]["max_positions"]) <= 6
                for arm in ("A", "B")
                for cost in COSTS
            ),
            "evidence": {
                "maximum_allocation": 0.55,
                "maximum_positions": 6,
                "combined_sleeve": False,
                "new_data_feed": False,
                "candidate_specific_liquidity_exception": False,
            },
        },
    }
    return {
        "gates": gates,
        "all_pass": all(bool(value["pass"]) for value in gates.values()),
        "near_miss_is_fail": True,
        "promotion_authorization": False,
    }


def run_economic() -> None:
    preflight_path = RESEARCH_DIR / "PRE_RUN_VALIDATION.json"
    if not preflight_path.exists() or _read_json(preflight_path).get("status") != "PASS":
        raise RuntimeError("PRE_RUN_VALIDATION.json must PASS before the economic run")
    if any(RESEARCH_DIR.glob("case_*.json.gz")) or (RESEARCH_DIR / "run_manifest.json").exists():
        raise RuntimeError("Economic outputs already exist; this frozen run is single-shot")
    config = load_config(str(REPO_ROOT / "config" / "settings.yaml"))
    dataset = load_normalized_dataset(BAR_PATH, LIFETIME_PATH, CORPORATE_ACTION_PATH)
    _assert_data_feasibility(dataset)
    preflight_payload = _read_json(preflight_path)
    identity = _design_identity()
    universe_a, universe_b = _build_universes(config, dataset)
    universe_identity = _write_universe_artifacts(universe_a, universe_b)
    signal_summary = _audit_signal_rows(dataset, universe_b)
    _write_json(RESEARCH_DIR / "signal_summary.json", signal_summary)
    prepared = _prepared_inputs(dataset)
    cases: dict[str, dict[str, Any]] = {}
    for arm, universe in (("A", universe_a), ("B", universe_b)):
        for cost_name, multiplier in COSTS.items():
            cases[f"{arm}/{cost_name}"] = _run_case(
                config,
                dataset,
                prepared,
                universe,
                arm,
                cost_name,
                multiplier,
            )
    metrics = _metrics_all_cases(cases)
    _write_json(
        RESEARCH_DIR / "v3_control_results.json", {key: value for key, value in metrics.items() if key.startswith("A/")}
    )
    _write_json(
        RESEARCH_DIR / "candidate_results.json", {key: value for key, value in metrics.items() if key.startswith("B/")}
    )
    coverage = {
        "A": _coverage(cases["A/1x"], dataset, universe_a, "A", signal_summary),
        "B": _coverage(cases["B/1x"], dataset, universe_b, "B", signal_summary),
    }
    _write_json(RESEARCH_DIR / "decision_coverage.json", coverage)
    turnover = {
        key: {
            period: {
                "annualized_one_way_target_turnover": value["annualized_one_way_target_turnover"],
                "annualized_one_way_realized_turnover": value["annualized_one_way_realized_turnover"],
                "gross_notional": value["gross_notional"],
                "realized_transaction_costs": value["realized_transaction_costs"],
                "costs_pct_starting_capital": value["costs_pct_starting_capital"],
                "costs_pct_gross_gain": value["costs_pct_gross_gain"],
                "fill_count": value["fill_count"],
                "rebalance_count": value["rebalance_count"],
            }
            for period, value in data["periods"].items()
        }
        for key, data in metrics.items()
    }
    _write_json(RESEARCH_DIR / "turnover_diagnostics.json", turnover)
    concentration = _concentration(cases["B/1x"], metrics["B/1x"]["periods"]["validation"])
    _write_json(RESEARCH_DIR / "concentration_diagnostics.json", concentration)
    yearly = {
        str(year): {
            "candidate_B_1x": metrics["B/1x"]["periods"][str(year)],
            "control_A_1x": metrics["A/1x"]["periods"][str(year)],
        }
        for year in (2022, 2023, 2024, 2025)
    }
    yearly["2026_partial"] = {
        "candidate_B_1x": metrics["B/1x"]["periods"]["2026_partial"],
        "control_A_1x": metrics["A/1x"]["periods"]["2026_partial"],
    }
    _write_json(RESEARCH_DIR / "yearly_validation.json", yearly)
    gate_table = _gate_table(metrics, coverage, signal_summary, concentration, preflight_payload)
    _write_json(RESEARCH_DIR / "gate_table.json", gate_table)
    manifest = {
        **identity,
        "status": "ECONOMIC_RUN_COMPLETE_PENDING_INDEPENDENT_VERIFICATION",
        "data": dataset.structural_audit,
        "data_hashes": {
            "bars": sha256_file(BAR_PATH),
            "lifetimes": sha256_file(LIFETIME_PATH),
            "corporate_actions": sha256_file(CORPORATE_ACTION_PATH),
        },
        "windows": {name: [start.isoformat(), end.isoformat()] for name, (start, end) in WINDOWS.items()},
        "arms": {
            "A": "current configured equity_residual_momentum_v3, weekly, unchanged production parameters",
            "B": "equity_overnight_reversal_v1, daily, fixed six-name 0.55/6 targets",
        },
        "cost_scenarios": {
            name: {
                "slippage_bps": config.backtest.slippage_bps * multiplier,
                "spread_bps": config.backtest.spread_bps * multiplier,
                "regulatory_fee_multiplier": multiplier,
                "commission": config.backtest.commission,
            }
            for name, multiplier in COSTS.items()
        },
        "universe": universe_identity,
        "signal_audit": signal_summary,
        "case_files": [f"case_{arm}_{cost}.json.gz" for arm in ("A", "B") for cost in COSTS],
        "no_parameter_search": True,
        "no_post_result_rescue": True,
        "production_registry_changed": False,
        "paper_live_changed": False,
        "runner_gate_status": gate_table,
    }
    _write_json(RESEARCH_DIR / "run_manifest.json", manifest)
    del cases, prepared, dataset
    verify = subprocess.run(
        [sys.executable, "-m", "research.overnight_reversal_v1.verify", "--output", str(RESEARCH_DIR)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(verify.stdout)
    sys.stderr.write(verify.stderr)
    if verify.returncode != 0:
        raise RuntimeError("Independent verifier did not complete successfully")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "run"))
    args = parser.parse_args()
    if args.command == "preflight":
        payload = preflight()
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        run_economic()


if __name__ == "__main__":
    main()
