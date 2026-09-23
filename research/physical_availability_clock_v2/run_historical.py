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
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
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
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1  # noqa: E402
from research.physical_availability_clock_v2.shared_replay import (  # noqa: E402
    MarketReplayInput,
    SharedAccountReplay,
    SharedReplayResult,
    _SharedEngine,
)
from research.physical_availability_clock_v2.timing import (  # noqa: E402
    equity_action_effective_open,
    equity_daily_available_at,
    equity_session_open,
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
REPLAY_DIR = ROOT / "research/physical_availability_clock_v2"
CRYPTO_CONTROL = ROOT / "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz"


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
            REPLAY_DIR / "shared_replay.py",
            REPLAY_DIR / "run_historical.py",
            REPLAY_DIR / "timing.py",
            CONFIG_PATH,
            REPLAY_DIR / "EQUITY_CANONICAL_CONTROL.json",
            REPLAY_DIR / "EQUITY_CANONICAL_CONTROL_COMPARISON.json",
            CRYPTO_CONTROL,
        )
    }


def _require_isolated_parity() -> None:
    source = _source_identity()
    for market in ("EQUITY", "CRYPTO"):
        path = REPLAY_DIR / f"{market}_PHYSICAL_PARITY_V2.json"
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


def _opening_event(source: Bar, at: datetime) -> Bar:
    return Bar(
        timestamp=at,
        symbol=source.symbol,
        timeframe="1d",
        open=source.open,
        high=source.open,
        low=source.open,
        close=source.open,
        volume=0.0,
        market=source.market,
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
    del filtered
    session_labels = {bar.timestamp for bar in ordered}
    signal_availability = {label: equity_daily_available_at(label.date()) for label in session_labels}
    execution_schedule = {label: equity_session_open(label.date()) for label in session_labels}
    action_availability = {
        action.timestamp: equity_action_effective_open(action.timestamp.date()) for action in corporate_actions
    }
    return (
        MarketReplayInput(
            engine,
            ordered,
            warmup,
            decision_bars,
            corporate_actions,
            signal_availability,
            action_availability,
            final_close_at=max(signal_availability.values()),
            execution_schedule=execution_schedule,
        ),
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
    del filtered
    control = json.loads(gzip.decompress(CRYPTO_CONTROL.read_bytes()))
    terminal_at = datetime.fromisoformat(control["terminal_at"])
    if _sha256_file(CRYPTO_HOURLY) != control["source_sha256"]:
        raise ValueError("Corrected crypto control source changed")
    signal_availability = {
        bar.timestamp: bar.timestamp + timedelta(days=1)
        for bar in ordered
        if bar.timestamp + timedelta(days=1) <= terminal_at
    }
    selected = [bar for bar in ordered if bar.timestamp in signal_availability]
    by_day: dict[tuple[str, datetime], list[Bar]] = defaultdict(list)
    for bar in hourly:
        day = bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if CRYPTO_START < bar.timestamp <= terminal_at:
            by_day[(bar.symbol, day)].append(bar)
    execution: list[Bar] = []
    for (_symbol, day), rows in by_day.items():
        if day == terminal_at:
            execution.extend(_opening_event(bar, bar.timestamp) for bar in rows if bar.timestamp == terminal_at)
        else:
            execution.extend(_opening_event(bar, bar.timestamp) for bar in rows if bar.timestamp > day)
    return (
        MarketReplayInput(engine, execution, warmup, selected, [], signal_availability, {}, terminal_at),
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


def _equity_session_label(timestamp: str, labels_by_date: dict[date, str]) -> str:
    """Project a physical XNYS event back to the source session's UTC date label."""
    return labels_by_date[datetime.fromisoformat(timestamp).date()]


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
    labels_by_date = {label.date(): label.isoformat() for label in replay_input.signal_availability}
    replay.add_market(replay_input)
    result = replay.run(liquidate_at_end=True)
    harness = _load_v3_harness()
    product = SimpleNamespace(result=result.market_results[Market.EQUITY], report=SimpleNamespace(summary={}))
    actual = harness._run_receipt(1.0, product)
    reference = canonical_control["parity"]["runs"]["1.0"]
    physical_fills = harness._fill_rows(product.result)
    for row in physical_fills:
        row["timestamp"] = _equity_session_label(row["timestamp"], labels_by_date)
    physical_terminals = harness._terminal_rows(product.result)
    for row in physical_terminals:
        row["timestamp"] = _equity_session_label(str(row["timestamp"]), labels_by_date)
    physical_chronology = harness._chronology_rows(product.result)
    physical_chronology["curve_timestamps"] = [
        _equity_session_label(value, labels_by_date) for value in physical_chronology["curve_timestamps"]
    ]
    physical_chronology["fills"] = [
        [row[0], _equity_session_label(row[1], labels_by_date), *row[2:]] for row in physical_chronology["fills"]
    ]
    physical_chronology["terminal_events"] = [
        [_equity_session_label(row[0], labels_by_date), *row[1:]] for row in physical_chronology["terminal_events"]
    ]
    normalized_hashes = {
        "targets_sha256": actual["hashes"]["targets_sha256"],
        "fills_sha256": harness._payload_sha256(physical_fills),
        "terminal_events_sha256": harness._payload_sha256(physical_terminals),
        "chronology_sha256": harness._payload_sha256(physical_chronology),
    }
    metric_differences: list[dict[str, Any]] = []
    period_names = ("development", "validation", "full")
    expected_metrics = {
        period: {
            key: value
            for key, value in reference["metrics"][period].items()
            if key not in {"start_timestamp", "end_timestamp"}
        }
        for period in period_names
    }
    actual_metrics = {
        period: {
            key: value
            for key, value in actual["metrics"][period].items()
            if key not in {"start_timestamp", "end_timestamp"}
        }
        for period in period_names
    }
    harness._compare_numbers(
        expected_metrics,
        actual_metrics,
        "metrics",
        metric_differences,
    )
    hashes_match = normalized_hashes == reference["hashes"]
    counts_match = actual["counts"] == reference["counts"]
    physical_chronology_checks = dict(actual["chronology_checks"])
    for key in ("first_curve_timestamp", "last_curve_timestamp"):
        physical_chronology_checks[key] = _equity_session_label(str(physical_chronology_checks[key]), labels_by_date)
    chronology_match = physical_chronology_checks == reference["chronology_checks"]
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
        "session_normalized_target_fill_terminal_chronology_hashes_match": hashes_match,
        "counts_match": counts_match,
        "chronology_checks_match": chronology_match,
        "reserved_role_observability_match": reserved_roles_match,
        "development_validation_full_economic_metrics_within_1e_12": not metric_differences,
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
        "physical_event_hashes": actual["hashes"],
        "session_normalized_hashes": normalized_hashes,
        "session_normalized_samples": {
            "first_fill": physical_fills[0] if physical_fills else None,
            "last_fill": physical_fills[-1] if physical_fills else None,
            "terminal_events": physical_terminals,
            "first_chronology_fill": physical_chronology["fills"][0] if physical_chronology["fills"] else None,
            "chronology_checks": physical_chronology_checks,
            "reference_chronology_checks": reference["chronology_checks"],
        },
        "matched_hashes": normalized_hashes
        if hashes_match
        else {"reference": reference["hashes"], "actual": normalized_hashes},
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
    decision_fields = ("history", "eligible_symbols", "holdings", "prices", "scores", "alpha_target", "target")
    decision_projection = [
        {"label_time": row["timestamp"], **{field: row[field] for field in decision_fields}} for row in decisions
    ]
    baseline_projection = [
        {"label_time": row["label_time"], **{field: row[field] for field in decision_fields}}
        for row in baseline["decisions"]
    ]
    snapshot_projection = [
        {"label_time": row["timestamp"], **{field: row[field] for field in ("nav", "cash", "positions")}}
        for row in snapshots
    ]
    baseline_snapshots = [
        {"label_time": row["label_time"], **{field: row[field] for field in ("nav", "cash", "positions")}}
        for row in baseline["snapshots"]
    ]
    actual_fills = _plain(actual_result.fills)
    actual_terminal = _plain(actual_result.terminal_events)
    actual_state = _plain(actual_result.state)
    actual_curve = _plain(actual_result.equity_curve)
    actual_timestamps = _plain(actual_result.curve_timestamps)
    source_orders = [event for event in result.events if event["event"] == "reservation_created"]
    order_projection = [
        {
            "client_order_id": event["client_order_id"],
            "symbol": event["symbol"],
            "side": event["side"],
            "qty": event["qty"],
            "created_at": event["timestamp"],
        }
        for event in source_orders
    ]
    baseline_orders = [
        {field: row[field] for field in ("client_order_id", "symbol", "side", "qty", "created_at")}
        for row in baseline["orders"]
    ]
    economic_state_fields = (
        "cash",
        "holdings",
        "avg_prices",
        "entry_fees",
        "pending_orders",
        "pending_target",
        "realized_pnl",
        "realized_pnl_accumulators",
        "rebalance_count",
        "last_target",
        "last_rebalance_at",
        "universe_version",
        "universe_snapshot_id",
    )
    state_differences = {
        key: {"actual": actual_state.get(key), "reference": baseline["final_state"].get(key)}
        for key in sorted(set(actual_state) | set(baseline["final_state"]))
        if _semantic_digest(actual_state.get(key)) != _semantic_digest(baseline["final_state"].get(key))
    }
    checks = {
        "daily_input_hash_match": input_identity["daily_bars_sha256"] == baseline["hashes"]["daily_bars"],
        "universe_hash_match": replay_input.engine.universe_version == baseline["universe_sha256"],
        "decision_sequence_match": _semantic_digest(decision_projection) == _semantic_digest(baseline_projection),
        "order_creation_match": _semantic_digest(sorted(order_projection, key=lambda row: row["client_order_id"]))
        == _semantic_digest(sorted(baseline_orders, key=lambda row: row["client_order_id"])),
        "fill_sequence_match": _semantic_digest(actual_fills) == _semantic_digest(baseline["fills"]),
        "snapshot_account_match": _semantic_digest(snapshot_projection) == _semantic_digest(baseline_snapshots),
        "curve_timestamps_match": actual_timestamps == baseline["curve_timestamps"],
        "equity_curve_match": _semantic_digest(actual_curve) == _semantic_digest(baseline["equity_curve"]),
        "terminal_events_match": _semantic_digest(actual_terminal) == _semantic_digest(baseline["terminal_events"]),
        "final_economic_state_match": all(field not in state_differences for field in economic_state_fields),
        "decision_fill_nav_counts_match": (len(decisions), len(actual_fills), len(actual_curve)) == (454, 406, 1945),
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
        "schema": "physical-clock-v2-corrected-crypto-isolated-parity",
        "source_identity": _source_identity(),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "reference_schema": baseline["schema"],
        "reference_commit": "b135d73743ec2652f79b8c840df50bd3f67166e8",
        "reference_gzip_sha256": hashlib.sha256(baseline_raw).hexdigest(),
        "input_identity": input_identity,
        "actual_hashes": {
            "decisions": _digest(decision_projection),
            "orders": _digest(order_projection),
            "fills": _digest(actual_fills),
            "snapshots": _digest(snapshot_projection),
            "curve": _digest(actual_curve),
            "terminal": _digest(actual_terminal),
            "final_state": _digest(actual_state),
        },
        "reference_hashes": baseline["hashes"],
        "first_trace_differences": {
            "decision": _first_difference(decision_projection, baseline_projection),
            "order": _first_difference(order_projection, baseline_orders),
            "snapshot": _first_difference(snapshot_projection, baseline_snapshots),
            "fill": _first_difference(actual_fills, baseline["fills"]),
            "terminal_event": _first_difference(actual_terminal, baseline["terminal_events"]),
            "equity_curve": _first_difference(actual_curve, baseline["equity_curve"]),
        },
        "order_event_sequence_differs": order_projection != baseline_orders,
        "final_state_metadata_differences": {
            key: value for key, value in state_differences.items() if key not in economic_state_fields
        },
        "final_economic_state_differences": {
            key: value for key, value in state_differences.items() if key in economic_state_fields
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


def _event_hashes(result: SharedReplayResult) -> dict[str, str]:
    return {
        kind: _digest([event for event in result.events if event["event"] in types])
        for kind, types in {
            "decisions": {"decision"},
            "orders": {"reservation_created"},
            "fills": {"fill"},
            "reservations": {"reservation_created", "reservation_released"},
            "positions": {"account_mark", "final_state"},
        }.items()
    } | {"cash_path": _digest(result.cash_path), "nav_path": _digest(result.nav_path)}


def _causality_receipt(replay: SharedAccountReplay, result: SharedReplayResult) -> dict[str, Any]:
    decision_clock: dict[tuple[str, str], str] = {}
    failures: list[str] = []
    checked = {"decisions": 0, "orders": 0, "fills": 0, "corporate_actions": 0}
    for event in result.events:
        kind = event["event"]
        if kind == "decision":
            checked["decisions"] += 1
            market = Market(str(event["market"]))
            label = datetime.fromisoformat(str(event["label_time"]))
            at = str(event["timestamp"])
            decision_clock[(market.value, label.isoformat())] = at
            if replay.inputs[market].signal_availability[label].isoformat() != at:
                failures.append(f"decision_availability:{market.value}:{label.isoformat()}")
        elif kind == "reservation_created":
            checked["orders"] += 1
            key = (str(event["market"]), str(event["decision_label_time"]))
            created = str(event["order_created_at"])
            decision = decision_clock.get(key)
            if decision is None or datetime.fromisoformat(created) < datetime.fromisoformat(decision):
                failures.append(f"order_before_decision:{event['client_order_id']}")
        elif kind == "fill":
            checked["fills"] += 1
            market = Market(str(event["market"]))
            fill_at = datetime.fromisoformat(str(event["source_price_event_time"]))
            order_at = datetime.fromisoformat(str(event["order_created_at"]))
            if fill_at <= order_at or fill_at.isoformat() != event["timestamp"]:
                failures.append(f"fill_before_order:{event['client_order_id']}")
            if market == Market.EQUITY and equity_session_open(fill_at.date()) != fill_at:
                failures.append(f"equity_fill_not_xnys_open:{event['client_order_id']}")
            if market == Market.CRYPTO and (fill_at.minute != 0 or fill_at.second != 0):
                failures.append(f"crypto_fill_not_source_hour:{event['client_order_id']}")
        elif kind == "corporate_action":
            checked["corporate_actions"] += 1
            market = Market(str(event["market"]))
            label = datetime.fromisoformat(str(event["source_ex_date_label"]))
            expected = replay.inputs[market].action_availability[label]
            if expected.isoformat() != event["timestamp"]:
                failures.append(f"corporate_action_clock:{event['symbol']}:{label.isoformat()}")
    return {
        "schema": "physical-clock-v2-causality",
        "status": "PASS" if not failures else "FAIL",
        "checked": checked,
        "all_consumed_history_available": bool(result.invariants["no_future_data_access"]),
        "history_checks": result.invariants["future_data_access_checks"],
        "failures": failures[:20],
        "failure_count": len(failures),
    }


def _cross_market_reordering(result: SharedReplayResult) -> dict[str, Any]:
    rows = [
        event
        for event in result.events
        if event["event"] in {"mark", "decision"}
        and event.get("market") in {"equity", "crypto"}
        and "label_time" in event
    ]
    equity = [event for event in rows if event["market"] == "equity"]
    crypto = [event for event in rows if event["market"] == "crypto"]
    changed_pairs = 0
    changed_decision_pairs = 0
    affected_equity: set[int] = set()
    affected_crypto: set[int] = set()
    for equity_index, left in enumerate(equity):
        for crypto_index, right in enumerate(crypto):
            old_before = str(left["label_time"]) < str(right["label_time"])
            new_before = str(left["timestamp"]) < str(right["timestamp"])
            if old_before == new_before:
                continue
            changed_pairs += 1
            affected_equity.add(equity_index)
            affected_crypto.add(crypto_index)
            if left["event"] == right["event"] == "decision":
                changed_decision_pairs += 1
    return {
        "schema": "physical-clock-v2-cross-market-reordering",
        "basis": "Same physical-run completed-bar marks and decisions ordered once by archived label-clock keys and once by physical UTC keys; no P&L attribution.",
        "source_events": {"equity": len(equity), "crypto": len(crypto)},
        "changed_cross_market_event_pairs": changed_pairs,
        "events_with_changed_relative_order": len(affected_equity) + len(affected_crypto),
        "changed_decision_order_pairs": changed_decision_pairs,
        "scarce_slot_first_claimant_changes": None,
        "scarce_slot_first_claimant_status": "UNMEASURABLE_FROM_ARCHIVED_V2_AGGREGATES",
        "fill_time_shifts_due_solely_to_clock": None,
        "fill_shift_status": "UNMEASURABLE_FROM_ARCHIVED_V2_AGGREGATES_AND_SUPERSEDED_CRYPTO_CONTROL",
        "counterfactual_returns_computed": False,
    }


def run_combined(config: Config) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    _require_isolated_parity()
    markets = (Market.EQUITY, Market.CRYPTO)
    first_replay, input_identity = _new_replay(config, markets)
    first = first_replay.run(liquidate_at_end=True)
    causality = _causality_receipt(first_replay, first)
    reordering = _cross_market_reordering(first)
    first_component_hashes = _event_hashes(first)
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
    second_component_hashes = _event_hashes(second)
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
            "decision_order_fill_reservation_position_cash_nav_hashes_identical": first_component_hashes
            == second_component_hashes,
            "causality_receipt_pass": causality["status"] == "PASS",
        }
    )
    determinism = {
        "schema": "shared-account-determinism-v2",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "first_event_sha256": first_event_sha256,
        "second_event_sha256": second_event_sha256,
        "first_component_hashes": first_component_hashes,
        "second_component_hashes": second_component_hashes,
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
        "over_cap_timestamp_count": invariants["slot_claim_violation_count"],
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
    return combined, determinism, causality, reordering


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
        control = args.control or REPLAY_DIR / "EQUITY_CANONICAL_CONTROL.json"
        receipt = run_equity_isolated(config, baseline, control)
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"EQUITY_ISOLATED={receipt['status']}")
        return 0 if receipt["status"] == "PASS" else 1
    if config.risk.max_positions != 6:
        raise ValueError("Replay V2 is restricted to current maximum_positions=6")
    if args.stage == "crypto-isolated":
        baseline = args.baseline or CRYPTO_CONTROL
        receipt = run_crypto_isolated(config, baseline)
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"CRYPTO_ISOLATED={receipt['status']}")
        return 0 if receipt["status"] == "PASS" else 1
    combined, determinism, causality, reordering = run_combined(config)
    args.out.write_text(json.dumps(combined, indent=2, sort_keys=True, allow_nan=False) + "\n")
    determinism_out = args.determinism_out or args.out.with_name("DETERMINISM_RECEIPT.json")
    determinism_out.write_text(json.dumps(determinism, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (REPLAY_DIR / "CLOCK_CAUSALITY_RECEIPT_V2.json").write_text(
        json.dumps(causality, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (REPLAY_DIR / "CROSS_MARKET_REORDERING_V2.json").write_text(
        json.dumps(reordering, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(f"COMBINED={combined['status']} DETERMINISM={determinism['status']}")
    return 0 if combined["status"] == "PASS" and determinism["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
