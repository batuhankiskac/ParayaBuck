#!/usr/bin/env python3
"""Run isolated controls or the current-cap shared replay smoke test."""

from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, TerminalLifecycle  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1  # noqa: E402
from research.shared_account_replay_v2.replay import (  # noqa: E402
    MarketReplayInput,
    SharedAccountReplay,
    SharedReplayResult,
    _SharedEngine,
)

EQUITY_BARS = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
EQUITY_LIFETIMES = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
EQUITY_ACTIONS = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CRYPTO_HOURLY = ROOT / "data/historical/crypto_expanded_1h.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"
EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
CRYPTO_START = datetime(2021, 4, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
REPLAY_DIR = ROOT / "research/shared_account_replay_v2"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): _sha256_file(path)
        for path in (
            REPLAY_DIR / "replay.py",
            REPLAY_DIR / "run_historical.py",
            CONFIG_PATH,
            REPLAY_DIR / "controls/EQUITY_CANONICAL_CONTROL.json",
            REPLAY_DIR / "controls/EQUITY_CANONICAL_CONTROL_COMPARISON.json",
            REPLAY_DIR / "controls/CURRENT_HOURLY_REPLAY.json.gz",
        )
    }


def _require_isolated_parity() -> None:
    source = _source_identity()
    for market in ("EQUITY", "CRYPTO"):
        path = REPLAY_DIR / f"{market}_ISOLATED_PARITY_V2.json"
        if not path.exists():
            raise RuntimeError(f"Combined replay requires current {market.lower()} isolated parity")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("status") != "PASS" or receipt.get("source_identity") != source:
            raise RuntimeError(f"Combined replay requires current {market.lower()} isolated parity")


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        values = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_plain(item) for item in values]
    return value


def _digest(value: Any) -> str:
    canonical = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def _normalize_numeric_encoding(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {key: _normalize_numeric_encoding(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_numeric_encoding(item) for item in value]
    return value


def _semantic_digest(value: Any) -> str:
    canonical = json.dumps(
        _normalize_numeric_encoding(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _first_difference(actual: list[Any], expected: list[Any]) -> dict[str, Any] | None:
    for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=False)):
        if actual_item != expected_item:
            return {"index": index, "actual": actual_item, "expected": expected_item}
    if len(actual) != len(expected):
        return {
            "index": min(len(actual), len(expected)),
            "actual_length": len(actual),
            "expected_length": len(expected),
        }
    return None


def _decision_difference(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> dict[str, Any] | None:
    difference = _first_difference(actual, expected)
    if difference is None or "actual" not in difference or "expected" not in difference:
        return difference
    left = difference.pop("actual")
    right = difference.pop("expected")
    fields = sorted(set(left) | set(right))
    difference["differing_fields"] = {
        key: {"actual_sha256": _digest(left.get(key)), "expected_sha256": _digest(right.get(key))}
        for key in fields
        if left.get(key) != right.get(key)
    }
    difference["actual_summary"] = {
        "timestamp": left.get("timestamp"),
        "holdings": left.get("holdings"),
        "equity": left.get("equity"),
        "target_weights": left.get("target", {}).get("weights"),
        "target_cash_weight": left.get("target", {}).get("cash_weight"),
        "orders": left.get("orders"),
        "account_cash": left.get("account", {}).get("cash"),
        "account_equity": left.get("account", {}).get("equity"),
    }
    difference["expected_summary"] = {
        "timestamp": right.get("timestamp"),
        "holdings": right.get("holdings"),
        "equity": right.get("equity"),
        "target_weights": right.get("target", {}).get("weights"),
        "target_cash_weight": right.get("target", {}).get("cash_weight"),
        "orders": right.get("orders"),
        "account_cash": right.get("account", {}).get("cash"),
        "account_equity": right.get("account", {}).get("equity"),
    }
    return difference


def _engine(
    replay: SharedAccountReplay,
    config: Config,
    market: Market,
    universe: Any,
    universe_version: str,
    *,
    terminal_lifecycles: dict[str, TerminalLifecycle] | None = None,
) -> _SharedEngine:
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy = portfolio_strategy_from(
        selection.name,
        selection.parameters,
        timeframe=selection.timeframe,
    )
    return _SharedEngine(
        replay.account,
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=1.0,
        benchmark_symbol="SPY" if market == Market.EQUITY else "BTC/USD",
        universe=universe,
        universe_version=universe_version,
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        ordinary_alpha_excluded_symbols=(
            config.universe.equity.ordinary_alpha_excluded_symbols if market == Market.EQUITY else ()
        ),
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
        terminal_lifecycles=terminal_lifecycles,
    )


def _equity_input(config: Config, replay: SharedAccountReplay) -> tuple[MarketReplayInput, dict[str, Any]]:
    bars = load_csv_bars(EQUITY_BARS, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(EQUITY_LIFETIMES)
    corporate_actions = load_csv_corporate_actions(EQUITY_ACTIONS)
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    if strategy.name != "equity_residual_momentum_v3" or strategy.version != "3.0.0":
        raise ValueError(f"Unexpected equity incumbent {strategy.name} {strategy.version}")
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=EQUITY_END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    filtered, warmup, (ordered, decision_bars) = _prepared_window(
        bars, Market.EQUITY, strategy.timeframe, EQUITY_START, EQUITY_END
    )
    engine = _engine(
        replay,
        config,
        Market.EQUITY,
        universe,
        hash_point_in_time_universe(universe),
        terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
    )
    engine._numeric_history = build_numeric_history(
        warmup + ordered, market=Market.EQUITY, timeframe=strategy.timeframe
    )
    return (
        MarketReplayInput(engine, filtered, warmup, decision_bars, corporate_actions),
        {
            "bars_sha256": _sha256_file(EQUITY_BARS),
            "lifetimes_sha256": _sha256_file(EQUITY_LIFETIMES),
            "corporate_actions_sha256": _sha256_file(EQUITY_ACTIONS),
            "universe_sha256": hash_point_in_time_universe(universe),
            "warmup_bar_count": len(warmup),
            "evaluation_bar_count": len(ordered),
        },
    )


def _crypto_input(config: Config, replay: SharedAccountReplay) -> tuple[MarketReplayInput, dict[str, Any]]:
    hourly = load_csv_bars(CRYPTO_HOURLY, default_market=Market.CRYPTO)
    daily = aggregate_historical_bars(hourly, "1d", require_complete=True)
    end = CRYPTO_END
    universe = build_product_universe(
        config,
        hourly,
        market=Market.CRYPTO,
        end=end,
        timeframe="1h",
    )
    selection = config.strategy.crypto
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    if strategy.name != "crypto_low_variance_hysteresis_v1" or strategy.version != "1.0.0":
        raise ValueError(f"Unexpected crypto incumbent {strategy.name} {strategy.version}")
    filtered, warmup, (ordered, decision_bars) = _prepared_window(
        daily, Market.CRYPTO, strategy.timeframe, CRYPTO_START, CRYPTO_END
    )
    engine = _engine(
        replay,
        config,
        Market.CRYPTO,
        universe,
        hash_point_in_time_universe(universe),
    )
    engine._numeric_history = build_numeric_history(
        warmup + ordered,
        market=Market.CRYPTO,
        timeframe=strategy.timeframe,
    )
    return (
        MarketReplayInput(engine, filtered, warmup, decision_bars, []),
        {
            "hourly_bars_sha256": _sha256_file(CRYPTO_HOURLY),
            "daily_bars_sha256": _digest(daily),
            "universe_sha256": hash_point_in_time_universe(universe),
            "hourly_bar_count": len(hourly),
            "daily_bar_count": len(daily),
            "warmup_bar_count": len(warmup),
            "evaluation_bar_count": len(ordered),
        },
    )


def _load_v3_harness() -> Any:
    path = ROOT / "research/month_end_first_open_correctness_v1/replay_v3_parity.py"
    spec = importlib.util.spec_from_file_location("parayabuck_v3_parity_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the canonical V3 parity harness")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_equity_isolated(config: Config, baseline_path: Path, control_path: Path) -> dict[str, Any]:
    canonical_control = json.loads(control_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    control_comparison_path = control_path.with_name(f"{control_path.stem}_COMPARISON.json")
    control_comparison = json.loads(control_comparison_path.read_text(encoding="utf-8"))
    replay = SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
    )
    replay_input, input_identity = _equity_input(config, replay)
    replay.add_market(replay_input)
    result = replay.run(liquidate_at_end=True)
    harness = _load_v3_harness()
    product = SimpleNamespace(result=result.market_results[Market.EQUITY], report=SimpleNamespace(summary={}))
    actual = harness._run_receipt(1.0, product)
    reference = canonical_control["parity"]["runs"]["1.0"]
    metric_differences: list[dict[str, Any]] = []
    harness._compare_numbers(
        {key: reference["metrics"][key] for key in ("development", "validation", "full")},
        {key: actual["metrics"][key] for key in ("development", "validation", "full")},
        "metrics",
        metric_differences,
    )
    hashes_match = actual["hashes"] == reference["hashes"]
    counts_match = actual["counts"] == reference["counts"]
    chronology_match = actual["chronology_checks"] == reference["chronology_checks"]
    reserved_roles_match = actual["reserved_roles"] == reference["reserved_roles"]
    v3_pass = control_comparison.get("verdict") == "V3_PARITY_PASS"
    expected_inputs = canonical_control["parity"]["input_identity"]
    input_match = (
        input_identity["bars_sha256"] == expected_inputs["bars"]["sha256"]
        and input_identity["lifetimes_sha256"] == expected_inputs["lifetimes"]["sha256"]
        and input_identity["corporate_actions_sha256"] == expected_inputs["corporate_actions"]["sha256"]
        and input_identity["universe_sha256"] == expected_inputs["universe_sha256"]
    )
    checks = {
        "fresh_canonical_control_pass": v3_pass,
        "input_identity_match": input_match,
        "target_fill_terminal_chronology_hashes_match": hashes_match,
        "counts_match": counts_match,
        "chronology_checks_match": chronology_match,
        "reserved_role_observability_match": reserved_roles_match,
        "development_validation_full_metrics_within_1e_12": not metric_differences,
    }
    return {
        "schema": "shared-account-equity-isolated-parity-v2",
        "source_identity": _source_identity(),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "canonical_control_verdict": canonical_control.get("verdict"),
        "canonical_control_source_git": canonical_control.get("current_git"),
        "reference_source_git": canonical_control.get("git"),
        "frozen_reference_source_git": baseline.get("git"),
        "canonical_control_comparison": control_comparison,
        "shared_replay_source_git": {"branch": "research/shared-account-replay-v2"},
        "input_identity": input_identity,
        "matched_hashes": actual["hashes"]
        if hashes_match
        else {"reference": reference["hashes"], "actual": actual["hashes"]},
        "matched_counts": actual["counts"]
        if counts_match
        else {"reference": reference["counts"], "actual": actual["counts"]},
        "metric_tolerance": {"relative": 1e-12, "absolute": 1e-12},
        "metric_difference_count": len(metric_differences),
        "metric_difference_paths": [item["path"] for item in metric_differences],
        "combined_economics_run": False,
    }


def _capture_crypto_replay(
    replay: SharedAccountReplay,
) -> tuple[SharedReplayResult, list[dict[str, Any]], list[dict[str, Any]]]:
    strategy = replay.inputs[Market.CRYPTO].engine.strategy
    decisions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    original_decide = CryptoLowVarianceHysteresis1dV1.decide
    original_apply = PortfolioBacktestEngine._apply_target
    original_mark = PortfolioBacktestEngine._record_holdings_snapshot

    def decide(self: Any, context: Any) -> Any:
        target = original_decide(self, context)
        decisions.append(
            {
                "timestamp": context.decision_timestamp.isoformat(),
                "history": {symbol: _plain(list(bars[-23:])) for symbol, bars in sorted(context.history.items())},
                "eligible_symbols": [symbol for symbol in sorted(context.history) if context.in_universe(symbol)],
                "holdings": _plain(context.holdings),
                "prices": _plain(context.prices),
                "equity": context.equity,
                "scores": _plain(self._score_candidates(context)),
                "alpha_target": _plain(target),
            }
        )
        return target

    def apply(self: Any, target: Any) -> None:
        original_apply(self, target)
        decisions[-1]["target"] = _plain(target)
        decisions[-1]["orders"] = _plain(self._pending)
        decisions[-1]["account"] = _plain(self._account_snapshot())

    def mark(self: Any, timestamp: datetime) -> None:
        original_mark(self, timestamp)
        snapshots.append(
            {
                "timestamp": timestamp.isoformat(),
                "nav": self._equity(),
                "cash": self._cash,
                "positions": {
                    symbol: {
                        "qty": position.qty,
                        "avg_price": position.avg_price,
                        "entry_fee": position.entry_fee,
                        "mark": self._last_price.get(symbol, position.avg_price),
                    }
                    for symbol, position in sorted(self._positions.items())
                },
            }
        )

    del strategy  # The same class hook is used by the canonical frozen harness.
    with (
        patch.object(CryptoLowVarianceHysteresis1dV1, "decide", decide),
        patch.object(PortfolioBacktestEngine, "_apply_target", apply),
        patch.object(PortfolioBacktestEngine, "_record_holdings_snapshot", mark),
    ):
        result = replay.run(liquidate_at_end=True)
    return result, decisions, snapshots


def run_crypto_isolated(config: Config, baseline_path: Path) -> dict[str, Any]:
    baseline_raw = baseline_path.read_bytes()
    baseline = json.loads(gzip.decompress(baseline_raw) if baseline_path.suffix == ".gz" else baseline_raw)
    replay = SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
    )
    replay_input, input_identity = _crypto_input(config, replay)
    replay.add_market(replay_input)
    result, decisions, snapshots = _capture_crypto_replay(replay)
    actual_result = result.market_results[Market.CRYPTO]
    actual = {
        "decisions": decisions,
        "snapshots": snapshots,
        "fills": _plain(actual_result.fills),
        "terminal_events": _plain(actual_result.terminal_events),
        "curve_timestamps": _plain(actual_result.curve_timestamps),
        "equity_curve": _plain(actual_result.equity_curve),
        "final_state": _plain(actual_result.state),
        "universe_hash": replay_input.engine.universe_version,
    }
    actual_hashes = {
        key: _digest(actual[key]) for key in ("decisions", "snapshots", "fills", "terminal_events", "final_state")
    }
    actual_hashes["curve_timestamps"] = _digest(actual["curve_timestamps"])
    actual_hashes["equity_curve"] = _digest(actual["equity_curve"])
    expected_hashes = baseline["hashes"]
    comparable_collections = {
        "decisions": "decisions",
        "snapshots": "snapshots",
        "fills": "fills",
        "terminal_events": "terminal_events",
        "final_state": "final_state",
        "curve_timestamps": "curve_timestamps",
        "equity_curve": "equity_curve",
    }
    normalized_actual_hashes = {key: _semantic_digest(actual[key]) for key in comparable_collections.values()}
    normalized_reference_hashes = {
        key: _semantic_digest(baseline[value]) for key, value in comparable_collections.items()
    }
    checks = {
        "daily_input_hash_match": input_identity["daily_bars_sha256"] == expected_hashes["daily_bars"],
        "universe_hash_match": actual["universe_hash"] == baseline["universe_hash"],
        "decisions_hash_match": normalized_actual_hashes["decisions"] == normalized_reference_hashes["decisions"],
        "snapshots_hash_match": normalized_actual_hashes["snapshots"] == normalized_reference_hashes["snapshots"],
        "fills_hash_match": normalized_actual_hashes["fills"] == normalized_reference_hashes["fills"],
        "terminal_events_hash_match": normalized_actual_hashes["terminal_events"]
        == normalized_reference_hashes["terminal_events"],
        "final_state_hash_match": normalized_actual_hashes["final_state"] == normalized_reference_hashes["final_state"],
        "curve_timestamps_match": actual["curve_timestamps"] == baseline["curve_timestamps"],
        "equity_curve_hash_match": normalized_actual_hashes["equity_curve"]
        == normalized_reference_hashes["equity_curve"],
        "decision_fill_nav_counts_match": (
            len(decisions) == len(baseline["decisions"])
            and len(actual_result.fills) == len(baseline["fills"])
            and len(actual_result.equity_curve) == len(baseline["equity_curve"])
        ),
        "flat_final_state": not actual_result.state.holdings
        and pytest_approx(actual_result.ending_equity, actual_result.state.cash),
        "cash_and_position_accounting": all(
            (
                result.invariants["cash_conserved"],
                result.invariants["position_quantities_reconciled"],
                result.invariants["nav_matches_ledger"],
                result.invariants["unowned_fill_count"] == 0,
                result.invariants["duplicate_fill_count"] == 0,
            )
        ),
    }
    return {
        "schema": "shared-account-crypto-isolated-parity-v2",
        "source_identity": _source_identity(),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "reference_schema": baseline.get("schema"),
        "reference_source_commit": baseline.get("production_main_sha") or "00722a726b2afcaa86e8ffcb5acee3cdd481c938",
        "reference_source_tag": "archive/maintenance/crypto-replay-parity-v1-2026-09",
        "reference_harness_runner_sha256": "2a8ee66773c0d30f1b93f84387882217936d1dd44418d45ed89dd8f4264c3f49",
        "fresh_control_file_sha256": hashlib.sha256(baseline_raw).hexdigest(),
        "input_identity": input_identity,
        "actual_hashes": actual_hashes,
        "reference_hashes": expected_hashes,
        "numeric_encoding_normalized_hashes": {
            "actual": normalized_actual_hashes,
            "reference": normalized_reference_hashes,
            "normalization": "int and float JSON encodings map to the same exact numeric value",
            "numeric_tolerance": 0.0,
        },
        "first_trace_differences": {
            "decision": _decision_difference(decisions, baseline["decisions"]),
            "snapshot": _first_difference(snapshots, baseline["snapshots"]),
            "fill": _first_difference(actual["fills"], baseline["fills"]),
            "terminal_event": _first_difference(actual["terminal_events"], baseline["terminal_events"]),
            "equity_curve": _first_difference(actual["equity_curve"], baseline["equity_curve"]),
        },
        "target_count": len(decisions),
        "fill_count": len(actual_result.fills),
        "nav_observation_count": len(actual_result.equity_curve),
        "accounting_invariants": result.invariants,
        "performance_interpretation": False,
    }


def pytest_approx(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= max(1e-8, abs(expected) * 1e-10)


def _new_replay(config: Config, markets: tuple[Market, ...]) -> tuple[SharedAccountReplay, dict[str, Any]]:
    replay = SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
    )
    identity: dict[str, Any] = {}
    for market in markets:
        replay_input, market_identity = (
            _equity_input(config, replay) if market == Market.EQUITY else _crypto_input(config, replay)
        )
        replay.add_market(replay_input)
        identity[market.value] = market_identity
    return replay, identity


def run_combined(config: Config) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_isolated_parity()
    markets = (Market.EQUITY, Market.CRYPTO)
    first_replay, input_identity = _new_replay(config, markets)
    first = first_replay.run(liquidate_at_end=True)
    invariants = first.invariants
    first_event_sha256 = first.event_sha256
    first_nav_path = first.nav_path
    first_cash_path = first.cash_path
    first_positions = first.positions
    first_cash = first.cash
    first_ending_nav = first.ending_nav
    first_start_nav = first.start_nav
    first_start_timestamp = first.timestamps[0].isoformat() if first.timestamps else None
    first_event_count = len(first.events)
    counts: dict[str, int] = {}
    for event in first.events:
        key = str(event["event"])
        counts[key] = counts.get(key, 0) + 1
    first_final_state_sha256 = _digest({"cash": first_cash, "nav": first_ending_nav, "positions": first_positions})
    # The equity input has millions of bars. Keep only compact proof of the
    # first run before loading the second PIT universe and event stream.
    del first, first_replay
    gc.collect()
    second_replay, second_identity = _new_replay(config, markets)
    second = second_replay.run(liquidate_at_end=True)
    second_event_sha256 = second.event_sha256
    second_digest = {market: _digest(value) for market, value in second_identity.items()}
    required_true = (
        "cash_conserved",
        "nav_matches_ledger",
        "position_quantities_reconciled",
        "final_position_count_within_limit",
        "no_deferred_buy_intents_at_end",
        "slot_claims_within_limit",
        "target_gross_within_limit",
        "target_name_weights_within_limits",
        "sleeve_targets_within_limits",
        "all_reservations_released",
        "ending_cash_nonnegative",
        "no_future_data_access",
    )
    checks = {name: bool(invariants[name]) for name in required_true}
    checks["maximum_observed_position_count_within_limit"] = (
        invariants["max_observed_position_count"] <= invariants["maximum_positions"]
    )
    checks.update(
        {
            "no_unowned_fills": invariants["unowned_fill_count"] == 0,
            "no_duplicate_fill_application": invariants["duplicate_fill_count"] == 0,
            "input_identity_same_on_repeat": {market: _digest(value) for market, value in input_identity.items()}
            == second_digest,
            "event_log_identical_on_repeat": first_event_sha256 == second_event_sha256,
            "nav_path_identical_on_repeat": first_nav_path == second.nav_path,
            "cash_path_identical_on_repeat": first_cash_path == second.cash_path,
            "final_positions_identical_on_repeat": first_positions == second.positions,
            "final_cash_identical_on_repeat": first_cash == second.cash,
            "final_nav_identical_on_repeat": first_ending_nav == second.ending_nav,
        }
    )
    determinism = {
        "schema": "shared-account-determinism-v2",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "first_event_sha256": first_event_sha256,
        "second_event_sha256": second_event_sha256,
        "event_count": first_event_count,
        "event_counts_by_type": counts,
        "nav_observation_count": len(first_nav_path),
        "cash_observation_count": len(first_cash_path),
        "start_timestamp_utc": first_start_timestamp,
        "final_state_sha256": first_final_state_sha256,
        "repeat_final_state_sha256": _digest(
            {"cash": second.cash, "nav": second.ending_nav, "positions": second.positions}
        ),
        "current_cap_only": 6,
        "cap_8_arm_created": False,
    }
    combined = {
        "schema": "shared-account-combined-smoke-invariants-v2",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "starting_nav": first_start_nav,
        "starting_cash": first_start_nav,
        "start_timestamp_utc": first_start_timestamp,
        "initial_sleeve_state": {"equity": "flat", "crypto": "flat"},
        "initial_position_count": 0,
        "initial_pending_order_count": 0,
        "ending_cash": invariants["ending_cash"],
        "cash_reconciliation_error": invariants["cash_reconciliation_error"],
        "nav_cash_mark_reconciliation": invariants["nav_matches_ledger"],
        "position_quantity_reconciliation_error": invariants["position_quantity_reconciliation_error"],
        "position_count_maximum": invariants["maximum_positions"],
        "maximum_observed_position_count": invariants["max_observed_position_count"],
        "maximum_observed_slot_claim_count": invariants["max_observed_slot_claim_count"],
        "slot_claim_violation_count": invariants["slot_claim_violation_count"],
        "slot_claim_violation_samples": invariants["slot_claim_violation_samples"],
        "deferred_buy_intent_count_at_end": invariants["deferred_buy_intent_count"],
        "maximum_target_gross": invariants["maximum_target_gross_observed"],
        "maximum_realized_gross_observed": invariants["maximum_realized_gross_observed"],
        "maximum_name_target_weight": invariants["maximum_target_name_weight"],
        "reservation_created": invariants["reservation_create_count"],
        "reservation_released": invariants["reservation_release_count"],
        "dividend_cash_events": invariants["dividend_cash_event_count"],
        "fill_count": invariants["fill_count"],
        "unowned_fill_count": invariants["unowned_fill_count"],
        "duplicate_fill_count": invariants["duplicate_fill_count"],
        "future_data_access_checks": invariants["future_data_access_checks"],
        "event_order_sha256": first_event_sha256,
        "input_identity": input_identity,
        "performance_metrics_published": False,
        "performance_interpretation": False,
    }
    return combined, determinism


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("equity-isolated", "crypto-isolated", "combined"), required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--determinism-out", type=Path)
    args = parser.parse_args()
    config = load_config(str(CONFIG_PATH))
    if args.stage == "equity-isolated":
        baseline = args.baseline or ROOT / "research/month_end_first_open_correctness_v1/V3_POST_FIX_REPLAY.json"
        control = args.control or ROOT / "research/shared_account_replay_v2/controls/EQUITY_CANONICAL_CONTROL.json"
        receipt = run_equity_isolated(config, baseline, control)
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"EQUITY_ISOLATED={receipt['status']}")
        return 0 if receipt["status"] == "PASS" else 1
    if config.risk.max_positions != 6:
        raise ValueError("Replay V2 is restricted to current maximum_positions=6")
    if args.stage == "crypto-isolated":
        baseline = args.baseline or ROOT / "research/shared_account_replay_v2/controls/CURRENT_HOURLY_REPLAY.json.gz"
        receipt = run_crypto_isolated(config, baseline)
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"CRYPTO_ISOLATED={receipt['status']}")
        return 0 if receipt["status"] == "PASS" else 1
    combined, determinism = run_combined(config)
    args.out.write_text(json.dumps(combined, indent=2, sort_keys=True, allow_nan=False) + "\n")
    determinism_out = args.determinism_out or args.out.with_name("DETERMINISM_RECEIPT.json")
    determinism_out.write_text(json.dumps(determinism, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"COMBINED={combined['status']} DETERMINISM={determinism['status']}")
    return 0 if combined["status"] == "PASS" and determinism["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
