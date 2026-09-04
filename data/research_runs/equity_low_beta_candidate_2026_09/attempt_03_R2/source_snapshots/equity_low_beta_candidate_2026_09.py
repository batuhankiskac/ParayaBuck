#!/usr/bin/env python3
"""Research-only low-beta equity candidate contract.

This module deliberately stops before economics until the pre-economic
manifest is frozen by the designer and root coordinator.  It never imports
runtime services, writes a cache/database, or registers a product strategy.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import math
import os
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
SETTINGS = REPO / "config/settings.yaml"
MANIFEST = REPO / "data/research_manifests/equity_low_beta_candidate_2026_09.json"
RUNS_DIR = REPO / "data/research_runs/equity_low_beta_candidate_2026_09"

IDENTITY = "equity_low_beta_252d_monthly_v1"
VERSION = "1.0.0"
TIMEFRAME = "1d"
EVAL_START = "2017-01-03T00:00:00+00:00"
# Date-level cutoff: admit every timestamp in the proven 2026-07-31 session,
# while rejecting any later bar.
EVAL_END = "2026-07-30T23:59:59+00:00"
ALLOCATION = 0.55
TOP_N = 6
MAX_SINGLE_WEIGHT = 0.10
BETA_WINDOW = 252
MIN_BETA_PRICES = BETA_WINDOW + 1
EXCLUDED_SYMBOLS = frozenset({"SPY", "SH", "PSQ", "DOG"})
CANONICAL_SHAS = {
    "bars": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "lifetimes": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "corporate_actions": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}


@dataclass(frozen=True)
class CandidateRank:
    symbol: str
    beta: float
    aligned_prices: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_observations(
    observations: Iterable[tuple[datetime, float]], as_of: datetime | None
) -> list[tuple[datetime, float]] | None:
    """Validate ordered UTC-positive observations after applying the as-of cut."""
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() != timedelta(0)):
        return None
    result: list[tuple[datetime, float]] = []
    previous: datetime | None = None
    for timestamp, close in observations:
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            return None
        if as_of is not None and timestamp > as_of:
            continue
        if previous is not None and timestamp <= previous:
            return None
        if not math.isfinite(close) or close <= 0:
            return None
        result.append((timestamp, close))
        previous = timestamp
    return result


def aligned_beta(
    stock: Sequence[tuple[datetime, float]],
    benchmark: Mapping[datetime, float],
    *,
    as_of: datetime | None = None,
) -> float | None:
    """Return OLS beta for the last 252 timestamp-aligned daily returns."""
    valid_stock = _valid_observations(stock, as_of)
    valid_benchmark = _valid_observations(benchmark.items(), as_of)
    if valid_stock is None or valid_benchmark is None:
        return None
    benchmark_by_timestamp = dict(valid_benchmark)
    common = [(timestamp, close, benchmark_by_timestamp[timestamp]) for timestamp, close in valid_stock if timestamp in benchmark_by_timestamp]
    if len(common) < MIN_BETA_PRICES:
        return None
    common = common[-MIN_BETA_PRICES:]
    stock_returns: list[float] = []
    market_returns: list[float] = []
    for (_left_ts, left_stock, left_market), (_right_ts, right_stock, right_market) in zip(common, common[1:]):
        stock_returns.append(right_stock / left_stock - 1.0)
        market_returns.append(right_market / left_market - 1.0)
    if len(stock_returns) != BETA_WINDOW or not all(math.isfinite(value) for value in stock_returns + market_returns):
        return None
    market_mean = statistics.fmean(market_returns)
    stock_mean = statistics.fmean(stock_returns)
    variance = sum((value - market_mean) ** 2 for value in market_returns)
    if variance <= 1e-12 or not math.isfinite(variance):
        return None
    beta = sum((stock - stock_mean) * (market - market_mean) for stock, market in zip(stock_returns, market_returns, strict=True)) / variance
    return beta if math.isfinite(beta) else None


def rank_low_beta(
    histories: Mapping[str, Sequence[tuple[datetime, float]]],
    benchmark: Mapping[datetime, float],
    eligible: Iterable[str],
    *,
    as_of: datetime | None = None,
) -> tuple[CandidateRank, ...]:
    """Rank eligible symbols by ascending 252-return beta, symbol tie-break."""
    ranked: list[CandidateRank] = []
    for symbol in sorted(set(eligible) - EXCLUDED_SYMBOLS):
        beta = aligned_beta(histories.get(symbol, ()), benchmark, as_of=as_of)
        if beta is not None:
            ranked.append(CandidateRank(symbol, beta, MIN_BETA_PRICES))
    return tuple(sorted(ranked, key=lambda item: (item.beta, item.symbol)))


def rank_dollar_volume(
    histories: Mapping[str, Sequence[tuple[datetime, float, float]]], eligible: Iterable[str], lookback: int = 20
) -> tuple[str, ...]:
    """Control ranking: trailing median dollar volume, deterministic symbol tie-break."""
    ranked: list[tuple[float, str]] = []
    for symbol in sorted(set(eligible) - EXCLUDED_SYMBOLS):
        bars = histories.get(symbol, ())[-lookback:]
        values = [close * volume for _timestamp, close, volume in bars if close > 0 and volume >= 0]
        if len(values) == lookback and all(math.isfinite(value) for value in values):
            ranked.append((statistics.median(values), symbol))
    return tuple(symbol for _volume, symbol in sorted(ranked, key=lambda item: (-item[0], item[1])))


def equal_capped_weights(symbols: Sequence[str]) -> dict[str, float]:
    """Build the fixed 0.55/6 target; fail closed if six names are unavailable."""
    selected = tuple(symbols[:TOP_N])
    if len(selected) < TOP_N:
        raise ValueError("coverage_insufficient: six valid names are required")
    weight = ALLOCATION / TOP_N
    if weight > MAX_SINGLE_WEIGHT:
        raise ValueError("candidate allocation exceeds per-name cap")
    return {symbol: weight for symbol in selected}


def _stream_bar_inventory() -> dict[str, object]:
    if not DATASET.exists():
        raise FileNotFoundError(DATASET)
    count = 0
    symbols: set[str] = set()
    first: str | None = None
    last: str | None = None
    post_cutoff_rows = 0
    previous_by_symbol: dict[str, datetime] = {}
    with DATASET.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = str(row["timestamp"])
            parsed = datetime.fromisoformat(timestamp)
            symbol = str(row["symbol"])
            prior = previous_by_symbol.get(symbol)
            if prior is not None and parsed <= prior:
                raise ValueError(f"duplicate_or_out_of_order_symbol_bar: {symbol} {timestamp}")
            previous_by_symbol[symbol] = parsed
            for field in ("open", "high", "low", "close", "volume"):
                value = float(row[field])
                if not math.isfinite(value) or (field != "volume" and value <= 0) or (field == "volume" and value < 0):
                    raise ValueError(f"invalid_source_value: {symbol} {timestamp} {field}")
            if str(row["timeframe"]).casefold() not in {"1d", "1day"} or str(row["market"]).casefold() != "equity":
                raise ValueError(f"unexpected_source_dimension: {symbol} {timestamp}")
            if first is None:
                first = timestamp
            last = timestamp
            symbols.add(symbol)
            count += 1
            if parsed.isoformat() > EVAL_END:
                post_cutoff_rows += 1
    return {"rows": count, "symbol_count": len(symbols), "first_timestamp": first, "last_timestamp": last, "post_cutoff_rows_unused": post_cutoff_rows}


def semantic_config_payload() -> dict[str, object]:
    return {
        "candidate": IDENTITY,
        "version": VERSION,
        "timeframe": TIMEFRAME,
        "beta_window_returns": BETA_WINDOW,
        "required_prices": MIN_BETA_PRICES,
        "cadence": "monthly:first_completed_session_then_next_tradable_bar",
        "allocation": ALLOCATION,
        "top_n": TOP_N,
        "max_single_weight": MAX_SINGLE_WEIGHT,
        "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        "cash_accrual": "zero",
        "controls": ["equity_residual_momentum_v3_same_run_same_source", "monthly_dollar_volume_top6_same_candidate_valid_universe"],
        "cost_multipliers": [1, 2, 3],
        "risk_semantics": "research_drawdown_shadow; production_entry_halt_not simulated as a research block",
    }


def build_manifest() -> dict[str, object]:
    dirty = [
        "data/research/full_survivorship_2026_08/final_verdict_v5.json",
        "data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json",
        "data/research/full_survivorship_2026_08/parity_canary.json",
        "data/research/full_survivorship_2026_08/parity_manifest.json",
        "docs/research/candidate_feasibility_2026_09_05.md",
        "docs/research/strategy_candidate_shortlist_2026_09_05.md",
    ]
    payload = semantic_config_payload()
    semantic_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "1.0.0",
        "status": "PRE_ECONOMIC_PENDING_REVIEW",
        "candidate": {"name": IDENTITY, "version": VERSION, "market": "equity", "timeframe": TIMEFRAME},
        "controls": [
            {"name": "equity_residual_momentum_v3", "role": "same_run_same_source_current_frozen_control"},
            {"name": "monthly_dollar_volume_top6_v1", "role": "same_candidate_valid_universe_equal_weight_control"},
        ],
        "inputs": {
            "bars": {"path": str(DATASET.relative_to(REPO)), "sha256": CANONICAL_SHAS["bars"]},
            "lifetimes": {"path": str(LIFETIMES.relative_to(REPO)), "sha256": CANONICAL_SHAS["lifetimes"]},
            "corporate_actions": {"path": str(CORPORATE_ACTIONS.relative_to(REPO)), "sha256": CANONICAL_SHAS["corporate_actions"]},
            "cutoff": EVAL_END,
        },
        "policy": payload,
        "config": {"settings_sha256": sha256_file(SETTINGS), "semantic_sha256": semantic_hash},
        "source": {
            "head": "ed11beba916202dcb4986853e848652af6e7a3ef",
            "dirty_paths": dirty,
            "file_sha256": {
                "scripts/research/equity_low_beta_candidate_2026_09.py": sha256_file(Path(__file__).resolve()),
                "scripts/research/equity_low_beta_engine_2026_09.py": sha256_file(REPO / "scripts/research/equity_low_beta_engine_2026_09.py"),
            },
        },
        "economic_gates": "PENDING_DESIGNER_AND_ROOT_REVIEW",
        "review": {"frozen": False, "markers": []},
    }


def preflight() -> dict[str, object]:
    expected = {"bars": DATASET, "lifetimes": LIFETIMES, "corporate_actions": CORPORATE_ACTIONS, "settings": SETTINGS}
    missing = [str(path.relative_to(REPO)) for path in expected.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    inventory = _stream_bar_inventory()
    manifest = build_manifest()
    manifest["inventory"] = inventory
    manifest["observed_sha256"] = {key: sha256_file(path) for key, path in expected.items()}
    if manifest["observed_sha256"]["bars"] != CANONICAL_SHAS["bars"]:
        raise ValueError("bars_sha256_mismatch: canonical PIT input changed")
    return manifest


def run_smoke() -> dict[str, object]:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    market = [(base + timedelta(days=index), 100.0 + index * 0.1) for index in range(MIN_BETA_PRICES)]
    histories = {symbol: [(timestamp, close * (1.0 + drift)) for timestamp, close in market] for symbol, drift in (("AAA", 0.0), ("BBB", 0.1), ("CCC", -0.1), ("DDD", 0.2), ("EEE", -0.2), ("FFF", 0.3))}
    ranked = rank_low_beta(histories, dict(market), histories)
    weights = equal_capped_weights([item.symbol for item in ranked])
    return {"status": "SMOKE_PASS", "ranked": [item.symbol for item in ranked], "weights": weights}


def _metric_record(case: object, *, window: str, cost_multiplier: float) -> dict[str, object]:
    result = case.result
    names = ("starting_equity", "start_date", "end_date", "cagr", "max_drawdown", "sharpe", "sortino", "total_return", "net_return", "gross_return", "turnover", "rebalance_count", "average_cash_ratio", "average_holdings_count", "slippage_cost_total", "spread_cost_total", "fee_total", "benchmark_return")
    metrics = {name: getattr(result, name) for name in names if hasattr(result, name)}
    return {
        "window": window,
        "cost_multiplier": cost_multiplier,
        "case": case.name,
        "metrics": metrics,
        "strategy_parameters": dict(case.strategy.parameter_tuple()),
        "universe_hash": case.universe_hash,
        "source_head": case.source_head,
        "coverage_status": case.coverage_status,
        "terminal_policy": case.terminal_policy,
        "terminal_map_symbol_count": len(case.terminal_map_symbols),
        "terminal_event_count": case.terminal_event_count,
        "terminal_map_digest": case.terminal_map_digest,
        "drawdown_semantics": "research shadow; production entry halt is not simulated as a research block",
    }


def evaluate_verdict(
    full_3x: Mapping[str, Mapping[str, float]],
    recent_24m: Mapping[str, Mapping[str, float]],
) -> dict[str, object]:
    """Apply the frozen scalar gates to decimal CAGR and positive drawdown metrics."""
    required = (
        ("full_3x", full_3x),
        ("recent_24m", recent_24m),
    )
    missing_or_nonfinite: list[str] = []
    for label, cells in required:
        for arm in (IDENTITY, "monthly_dollar_volume_top6_v1", "equity_residual_momentum_v3"):
            cell = cells.get(arm)
            if not isinstance(cell, Mapping):
                missing_or_nonfinite.append(f"{label}.{arm}")
                continue
            for field in ("cagr", "max_drawdown"):
                value = cell.get(field)
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    missing_or_nonfinite.append(f"{label}.{arm}.{field}")
    if missing_or_nonfinite:
        return {"status": "BLOCKED", "blocked_reason": "missing_or_nonfinite_gate_metrics", "fields": missing_or_nonfinite}
    candidate = full_3x[IDENTITY]
    liquidity = full_3x["monthly_dollar_volume_top6_v1"]
    v3 = full_3x["equity_residual_momentum_v3"]
    branch_a = candidate["cagr"] >= liquidity["cagr"] + 0.005 and candidate["max_drawdown"] <= liquidity["max_drawdown"] + 0.02
    branch_b = candidate["max_drawdown"] <= v3["max_drawdown"] - 0.03 and candidate["cagr"] >= v3["cagr"] - 0.01
    recent_candidate = recent_24m[IDENTITY]
    recent_liquidity = recent_24m["monthly_dollar_volume_top6_v1"]
    recent_guard = recent_candidate["cagr"] < recent_liquidity["cagr"] - 0.02 and recent_candidate["max_drawdown"] > recent_liquidity["max_drawdown"] - 0.05
    return {"status": "PASS" if (branch_a or branch_b) and not recent_guard else "REJECT", "full_3x_branch_a": branch_a, "full_3x_branch_b": branch_b, "recent_guard_failed": recent_guard}


def run_economic(manifest_path: Path = MANIFEST) -> None:
    from parayabuck.backtest.terminal_aware_engine import build_terminal_map
    from parayabuck.backtest.terminal_policy import classify_terminal_events
    from parayabuck.config import load_config
    from scripts.research.equity_low_beta_engine_2026_09 import (
        DATA_PREFLIGHT_RECEIPT, build_candidate_strategy, build_control_strategy,
        build_liquidity_control_strategy, build_research_universe, case_windows,
        require_frozen_inputs, run_case,
    )
    from scripts.run_symmetric_v5_validation import load_bars, load_corporate_actions_all

    manifest = require_frozen_inputs(manifest_path, DATA_PREFLIGHT_RECEIPT)
    pinned_files = manifest.get("source", {}).get("file_sha256", {})
    for relative, expected_hash in pinned_files.items():
        actual_hash = sha256_file(REPO / relative)
        if actual_hash != expected_hash:
            raise RuntimeError(f"BLOCKED_SOURCE_FILE_HASH_MISMATCH:{relative}")
    current = preflight()
    if current["observed_sha256"] != json.loads(DATA_PREFLIGHT_RECEIPT.read_text(encoding="utf-8"))["observed_sha256"]:
        raise RuntimeError("BLOCKED_PREFLIGHT_STALE_ACTUAL_HASHES")
    config = load_config(str(SETTINGS))
    bars = load_bars(DATASET)
    bars = [dataclasses.replace(bar, timeframe="1d") if bar.timeframe.casefold() in {"1day", "1d"} else bar for bar in bars]
    actions = load_corporate_actions_all()
    classifications = classify_terminal_events(LIFETIMES, dataset_end=datetime(2026, 7, 31, 4, tzinfo=UTC))
    terminal_map = build_terminal_map(classifications, datetime(2026, 7, 31, 4, tzinfo=UTC))
    strategies = (build_candidate_strategy(), build_control_strategy(config), build_liquidity_control_strategy())
    names = (IDENTITY, "equity_residual_momentum_v3", "monthly_dollar_volume_top6_v1")
    records: list[dict[str, object]] = []
    terminal_map_digest: str | None = None
    expected_cells = {(window, name, multiplier) for window, _start, _end in case_windows() for name in names for multiplier in (1.0, 2.0, 3.0)}

    def persist_blocked(reason: str) -> None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        target = RUNS_DIR / "economic_results.json"
        temporary = target.with_suffix(".tmp")
        payload = {"status": "BLOCKED", "equity_only": True, "blocked_reason": reason, "records": records, "economic_verdict": "BLOCKED"}
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    for window_name, start, end in case_windows():
        for prototype, name in zip(strategies, names):
            universe = build_research_universe(config, bars, prototype, end=end)
            for multiplier in (1.0, 2.0, 3.0):
                strategy = type(prototype)(dict(prototype.parameter_tuple())) if name != "equity_residual_momentum_v3" else build_control_strategy(config)
                try:
                    case = run_case(config, bars, universe, strategy, start=start, end=end, corporate_actions=actions, cost_multiplier=multiplier, liquidate_at_end=True, terminal_map=terminal_map, terminal_policy="T1")
                except Exception as exc:
                    persist_blocked(f"case_exception:{window_name}:{name}:{multiplier:g}x:{type(exc).__name__}:{exc}")
                    return
                if case.coverage_status == "BLOCKED_COVERAGE_INSUFFICIENT":
                    persist_blocked(f"coverage_insufficient:{window_name}:{name}:{multiplier:g}x")
                    return
                record = _metric_record(case, window=window_name, cost_multiplier=multiplier)
                metrics = record["metrics"]
                if not isinstance(record["universe_hash"], str) or not record["universe_hash"] or record["terminal_policy"] != "T1" or not isinstance(record["terminal_map_digest"], str) or not record["terminal_map_digest"]:
                    persist_blocked(f"invalid_case_identity:{window_name}:{name}:{multiplier:g}x")
                    return
                if terminal_map_digest is None:
                    terminal_map_digest = record["terminal_map_digest"]
                elif record["terminal_map_digest"] != terminal_map_digest:
                    persist_blocked(f"terminal_map_digest_mismatch:{window_name}:{name}:{multiplier:g}x")
                    return
                if not isinstance(metrics, dict) or any(field not in metrics or not math.isfinite(float(metrics[field])) for field in ("cagr", "max_drawdown", "total_return", "net_return", "gross_return", "turnover", "slippage_cost_total", "spread_cost_total", "fee_total", "benchmark_return")):
                    persist_blocked(f"missing_or_nonfinite_metrics:{window_name}:{name}:{multiplier:g}x")
                    return
                records.append(record)
    actual_cells = {(r["window"], r["case"], r["cost_multiplier"]) for r in records}
    if actual_cells != expected_cells or len(records) != 18:
        persist_blocked(f"incomplete_cell_matrix:expected=18:actual={len(records)}")
        return
    full_3x = {r["case"]: {"cagr": r["metrics"]["cagr"], "max_drawdown": r["metrics"]["max_drawdown"]} for r in records if r["window"] == "full_2017_2026_07_30" and r["cost_multiplier"] == 3.0}
    recent_24m = {r["case"]: {"cagr": r["metrics"]["cagr"], "max_drawdown": r["metrics"]["max_drawdown"]} for r in records if r["window"] == "recent_last_24_months" and r["cost_multiplier"] == 3.0}
    verdict = evaluate_verdict(full_3x, recent_24m)
    if verdict["status"] == "BLOCKED":
        persist_blocked(str(verdict["blocked_reason"]))
        return
    output = {"status": "COMPLETE", "equity_only": True, "manifest": manifest, "records": records, "economic_verdict": verdict}
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    target = RUNS_DIR / "economic_results.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.smoke:
        print(json.dumps(run_smoke(), indent=2, sort_keys=True))
        return 0
    if args.run:
        run_economic()
        return 0
    result = preflight()
    result["receipt"] = {
        "command": "python scripts/research/equity_low_beta_candidate_2026_09.py --preflight",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "DATA_READY_INPUT_IDENTITIES_VERIFIED_ADAPTER_COVERAGE_PENDING",
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "preflight.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
