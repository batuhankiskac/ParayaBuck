#!/usr/bin/env python3
"""Run the frozen corrected V3 baseline and persist compact audit evidence.

This is a research-branch harness.  It deliberately uses the production
scanner, strategy, portfolio engine, execution policy, lifecycle handling,
corporate-action accounting, and risk overlay without changing production
code or runtime state.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.portfolio_engine import (  # noqa: E402
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    TerminalLifecycle,
)
from parayabuck.backtest.portfolio_report import build_portfolio_report  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
)
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.timeframes import is_regular_equity_time  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402

BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
BARS_MANIFEST_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CA_MANIFEST_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json"
CONFIG_PATH = ROOT / "config/settings.yaml"
STRUCTURAL_PATH = ROOT / "research/v3_reserved_role_corrected_baseline_v1/structural_old_vs_corrected.json"
ECONOMIC_PATH = ROOT / "research/v3_reserved_role_corrected_baseline_v1/economic_results.json"

START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END_BOUND = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
RESERVED_ROLES = ("SPY", "SH", "PSQ", "DOG")
EXPECTED_BARS_SHA256 = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA256 = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_CA_SHA256 = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_STRATEGY = "equity_residual_momentum_v3"
EXPECTED_VERSION = "3.0.0"


def _iso(value: datetime) -> str:
    return value.isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_utc_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _load_canonical_bars(path: Path) -> tuple[list[Bar], dict[str, Any]]:
    """Load the canonical bytes with lossless provider-spelling normalization.

    v5 contains the same daily equity series under both ``1d`` and ``1Day``
    spellings and both lowercase/uppercase market values.  The production CSV
    loader intentionally accepts one normalized spelling; this research input
    adapter records and normalizes only those exact field spellings without
    changing timestamps, prices, volume, symbols, or row order.
    """
    bars: list[Bar] = []
    raw_timeframes: Counter[str] = Counter()
    raw_markets: Counter[str] = Counter()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "open", "high", "low", "close"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"Bars CSV is missing required columns: {sorted(required - set(reader.fieldnames or ()))}")
        for raw in reader:
            timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            raw_market = str(raw.get("market") or "equity")
            raw_timeframe = str(raw.get("timeframe") or "1d")
            raw_markets[raw_market] += 1
            raw_timeframes[raw_timeframe] += 1
            market = raw_market.lower()
            timeframe_key = raw_timeframe.lower()
            timeframe = "1d" if timeframe_key in {"1d", "1day"} else timeframe_key
            if market == "equity":
                if timestamp.weekday() >= 5:
                    raise ValueError(f"Equity CSV contains a weekend bar: {timestamp.isoformat()}")
                if timeframe != "1d" and not is_regular_equity_time(timestamp):
                    raise ValueError(f"Equity CSV contains an out-of-session bar: {timestamp.isoformat()}")
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=str(raw["symbol"]),
                    timeframe=timeframe,
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    market=Market(market),
                )
            )
    return bars, {
        "raw_market_values": dict(sorted(raw_markets.items())),
        "raw_timeframe_values": dict(sorted(raw_timeframes.items())),
        "normalized_market": "lowercase market enum",
        "normalized_timeframe": "1d for raw 1d and 1Day; no price/timestamp/row changes",
    }


def _load_lifecycles(bars: list[Bar]) -> tuple[dict[str, TerminalLifecycle], dict[str, Any]]:
    latest: dict[str, datetime] = {}
    first: dict[str, datetime] = {}
    for bar in bars:
        if bar.market != Market.EQUITY or bar.timeframe != "1d":
            continue
        latest[bar.symbol] = max(latest.get(bar.symbol, bar.timestamp), bar.timestamp)
        first[bar.symbol] = min(first.get(bar.symbol, bar.timestamp), bar.timestamp)

    lifecycles: dict[str, TerminalLifecycle] = {}
    statuses: Counter[str] = Counter()
    event_classes: Counter[str] = Counter()
    inactive_without_terminal_event: list[str] = []
    rows = 0
    lifetime_mismatches: list[dict[str, str]] = []
    seen: set[str] = set()
    with LIFETIMES_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            symbol = row["canonical_symbol"].strip().upper()
            if symbol in seen:
                raise ValueError(f"Duplicate lifetime canonical symbol: {symbol}")
            seen.add(symbol)
            status = row["current_status"].strip().lower()
            statuses[status] += 1
            event_class = row["terminal_event_class"].strip()
            event_classes[event_class or "<none>"] += 1
            row_first = _parse_utc_date(row["first_observed_bar"])
            row_last = _parse_utc_date(row["last_observed_bar"])
            if symbol not in first or symbol not in latest:
                lifetime_mismatches.append({"symbol": symbol, "reason": "missing bars"})
                continue
            if first[symbol] != row_first:
                lifetime_mismatches.append(
                    {"symbol": symbol, "reason": f"first {first[symbol].isoformat()} != {row_first.isoformat()}"}
                )
            if latest[symbol] != row_last:
                lifetime_mismatches.append(
                    {"symbol": symbol, "reason": f"last {latest[symbol].isoformat()} != {row_last.isoformat()}"}
                )
            if status == "inactive":
                if event_class:
                    lifecycles[symbol] = TerminalLifecycle(row_last, event_class)
                else:
                    # One v5 row is an inactive provider listing with
                    # FINAL_SURVIVOR evidence and no terminal event.  It is
                    # horizon-censored, not a synthetic delisting; retaining
                    # it outside the terminal map preserves that distinction.
                    inactive_without_terminal_event.append(symbol)

    if set(latest) != seen:
        missing = sorted(set(latest) - seen)
        extra = sorted(seen - set(latest))
        raise ValueError(f"Lifetime/bar symbol identity mismatch; missing={missing[:5]} extra={extra[:5]}")
    if lifetime_mismatches:
        raise ValueError(f"Lifetime final-bar mismatches: {lifetime_mismatches[:3]}")
    return lifecycles, {
        "row_count": rows,
        "symbol_count": len(seen),
        "status_counts": dict(sorted(statuses.items())),
        "event_class_counts": dict(sorted(event_classes.items())),
        "terminal_lifecycle_count": len(lifecycles),
        "inactive_without_terminal_event": inactive_without_terminal_event,
        "horizon_censored_count": len(inactive_without_terminal_event),
        "final_bar_match_count": len(seen),
        "semantics": "POSSIBLE_DELISTING entities close only on their real final observed bar; FINAL_SURVIVOR rows at the dataset horizon receive no synthetic terminal event",
    }


def _load_cash_dividends() -> tuple[list[CorporateAction], dict[str, Any]]:
    actions: list[CorporateAction] = []
    action_counts: Counter[str] = Counter()
    with CORPORATE_ACTIONS_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action_type = row["action_type"].strip()
            action_counts[action_type] += 1
            if action_type != "cash_dividends":
                continue
            ex_date = row["ex_date"].strip()
            rate = float(row["rate"])
            if not ex_date or not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"Invalid cash-dividend row: {row}")
            actions.append(
                CorporateAction(
                    _parse_utc_date(ex_date),
                    row["symbol"].strip().upper(),
                    CorporateActionType.DIVIDEND,
                    rate,
                )
            )
    return actions, {
        "row_count": sum(action_counts.values()),
        "action_counts": dict(sorted(action_counts.items())),
        "applied_cash_dividend_count": len(actions),
        "split_rows_not_applied": sum(count for kind, count in action_counts.items() if kind not in {"cash_dividends"}),
        "semantics": "split-adjusted bars; cash_dividends applied as explicit dividend cash flows; split/stock-dividend rows not re-applied",
    }


def _engine(
    config: Any,
    strategy: Any,
    universe: PointInTimeUniverse,
    cost_multiplier: float,
    ordinary_alpha_excluded_symbols: tuple[str, ...],
    lifecycles: dict[str, TerminalLifecycle],
) -> PortfolioBacktestEngine:
    selection = config.strategy.equity
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        ordinary_alpha_excluded_symbols=ordinary_alpha_excluded_symbols,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=lifecycles,
    )


def _run(
    config: Any,
    strategy: Any,
    universe: PointInTimeUniverse,
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]],
    numeric_history: Any,
    corporate_actions: list[CorporateAction],
    lifecycles: dict[str, TerminalLifecycle],
    cost_multiplier: float,
    ordinary_alpha_excluded_symbols: tuple[str, ...],
) -> tuple[PortfolioBacktestEngine, PortfolioBacktestResult]:
    engine = _engine(
        config,
        strategy,
        universe,
        cost_multiplier,
        ordinary_alpha_excluded_symbols,
        lifecycles,
    )
    filtered, warmup, prepared_inputs = prepared
    result = engine.run(
        filtered,
        warmup=warmup,
        prepared_inputs=prepared_inputs,
        numeric_history=numeric_history,
        corporate_actions=corporate_actions,
    )
    return engine, result


def _snapshot_at(universe: PointInTimeUniverse, timestamp: datetime) -> Any:
    index = bisect_right(universe.snapshots, timestamp, key=lambda item: item.effective_at)
    return universe.snapshots[index - 1] if index else None


def _target_map(record: Any) -> dict[str, float]:
    return {symbol: float(weight) for symbol, weight in record.target_weights}


def _structural_comparison(
    universe: PointInTimeUniverse,
    old: PortfolioBacktestResult,
    corrected: PortfolioBacktestResult,
) -> dict[str, Any]:
    old_by_ts = {record.decision_timestamp: record for record in old.rebalances}
    corrected_by_ts = {record.decision_timestamp: record for record in corrected.rebalances}
    if tuple(old_by_ts) != tuple(corrected_by_ts):
        raise ValueError("Old and corrected structural runs do not share the same decision timestamps")
    observation_by_ts = {timestamp: symbols for timestamp, symbols in corrected.universe_history}
    context_hits: dict[str, Counter[str]] = {
        "candidate_pool": Counter(),
        "observation": Counter(),
        "old_target": Counter(),
        "corrected_target": Counter(),
    }
    target_changes: list[dict[str, Any]] = []
    old_target_role_weight: Counter[str] = Counter()
    corrected_target_role_weight: Counter[str] = Counter()
    for timestamp in old_by_ts:
        old_record = old_by_ts[timestamp]
        corrected_record = corrected_by_ts[timestamp]
        snapshot = _snapshot_at(universe, timestamp)
        if snapshot is None or snapshot.effective_at > timestamp:
            raise ValueError(f"No PIT universe snapshot at decision {timestamp.isoformat()}")
        candidate_roles = sorted(set(snapshot.symbols) & set(RESERVED_ROLES))
        observation_roles = sorted(set(observation_by_ts.get(timestamp, ())) & set(RESERVED_ROLES))
        old_target = _target_map(old_record)
        corrected_target = _target_map(corrected_record)
        old_roles = sorted(set(old_target) & set(RESERVED_ROLES))
        corrected_roles = sorted(set(corrected_target) & set(RESERVED_ROLES))
        for symbol in candidate_roles:
            context_hits["candidate_pool"][symbol] += 1
        for symbol in observation_roles:
            context_hits["observation"][symbol] += 1
        for symbol in old_roles:
            context_hits["old_target"][symbol] += 1
            old_target_role_weight[symbol] += old_target[symbol]
        for symbol in corrected_roles:
            context_hits["corrected_target"][symbol] += 1
            corrected_target_role_weight[symbol] += corrected_target[symbol]
        if old_target != corrected_target:
            old_ordinary = set(old_target) - set(RESERVED_ROLES)
            corrected_ordinary = set(corrected_target) - set(RESERVED_ROLES)
            target_changes.append(
                {
                    "decision_timestamp": timestamp.isoformat(),
                    "old_reserved_targets": {symbol: old_target[symbol] for symbol in old_roles},
                    "corrected_reserved_targets": {symbol: corrected_target[symbol] for symbol in corrected_roles},
                    "removed_reserved_targets": sorted(set(old_roles) - set(corrected_roles)),
                    "ordinary_additions": sorted(corrected_ordinary - old_ordinary),
                    "ordinary_removals": sorted(old_ordinary - corrected_ordinary),
                    "old_target_weights": old_target,
                    "corrected_target_weights": corrected_target,
                }
            )

    return {
        "comparison": "same prepared data/universe/lifecycle/corporate-actions/engine path; only ordinary_alpha_excluded_symbols differs",
        "decision_count": len(corrected.rebalances),
        "old_decision_count": len(old.rebalances),
        "corrected_decision_count": len(corrected.rebalances),
        "context_role_hits": {key: dict(sorted(value.items())) for key, value in context_hits.items()},
        "old_target_role_weight_sum": dict(sorted(old_target_role_weight.items())),
        "corrected_target_role_weight_sum": dict(sorted(corrected_target_role_weight.items())),
        "target_change_count": len(target_changes),
        "affected_roles": sorted(set(context_hits["old_target"]) | set(context_hits["corrected_target"])),
        "target_changes": target_changes,
        "corrected_reserved_target_count": sum(context_hits["corrected_target"].values()),
        "pre_fix_raw_score_identity_test": "tests/test_equity_residual_momentum_v3.py::test_reserved_roles_are_observable_but_not_ordinary_alpha_targets",
        "pre_fix_receipt": "RESERVED_ROLE_DEFECT_RECEIPT.md",
    }


def _sample_stddev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _period_metrics(
    engine: PortfolioBacktestEngine,
    result: PortfolioBacktestResult,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    points = list(zip(result.curve_timestamps, result.equity_curve, strict=True))
    selected_indices = [index for index, (timestamp, _value) in enumerate(points) if start <= timestamp <= end]
    if not selected_indices:
        raise ValueError(f"No equity points in period {start.isoformat()} -> {end.isoformat()}")
    first = selected_indices[0]
    indices = ([first - 1] if first > 0 else []) + selected_indices
    period_points = [points[index] for index in indices]
    values = [float(value) for _timestamp, value in period_points]
    timestamps = [timestamp for timestamp, _value in period_points]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400)
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[-1] > 0 else 0.0
    std = _sample_stddev(returns)
    downside_std = _sample_stddev([value for value in returns if value < 0])
    periods_per_year = len(returns) / years if years > 0 else 0.0
    annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)

    holdings = getattr(engine, "_holdings_history", [])
    period_holdings = [holdings[index] for index in indices]
    average_cash = (
        sum(float(cash) for _count, cash, _concentration in period_holdings) / len(period_holdings)
        if period_holdings
        else 1.0
    )
    average_positions = (
        sum(float(count) for count, _cash, _concentration in period_holdings) / len(period_holdings)
        if period_holdings
        else 0.0
    )
    maximum_concentration = max(
        (float(concentration) for _count, _cash, concentration in period_holdings),
        default=0.0,
    )
    in_period_fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    in_period_terminal = [
        event for event in result.terminal_events if start <= datetime.fromisoformat(str(event["timestamp"])) <= end
    ]
    slippage = sum(fill.slippage_cost for fill in in_period_fills) + sum(
        float(event.get("slippage_cost", 0.0)) for event in in_period_terminal
    )
    spread = sum(fill.spread_cost for fill in in_period_fills) + sum(
        float(event.get("spread_cost", 0.0)) for event in in_period_terminal
    )
    fees = sum(fill.fee for fill in in_period_fills) + sum(float(event.get("fee", 0.0)) for event in in_period_terminal)
    average_equity = sum(values) / len(values) if values else values[0]
    turnover = (
        sum(fill.qty * fill.reference_price for fill in in_period_fills) / average_equity if average_equity > 0 else 0.0
    )
    rebalance_count = sum(start <= record.decision_timestamp <= end for record in result.rebalances)
    return {
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
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
        "fills": len(in_period_fills),
        "terminal_liquidations": len(in_period_terminal),
        "rebalance_count": rebalance_count,
        "average_exposure": 1.0 - average_cash,
        "average_positions": average_positions,
        "maximum_concentration": maximum_concentration,
        "average_equity": average_equity,
    }


def _compact_rebalances(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    return [
        {
            "decision_timestamp": record.decision_timestamp.isoformat(),
            "target_weights": dict(record.target_weights),
            "target_cash_weight": record.target_cash_weight,
            "risk_overlay_halted": record.risk_overlay_halted,
            "risk_overlay_reasons": list(record.risk_overlay_reasons),
        }
        for record in result.rebalances
    ]


def _compact_fills(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": fill.timestamp.isoformat(),
            "decision_timestamp": fill.decision_timestamp.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": fill.qty,
            "reference_price": fill.reference_price,
            "fill_price": fill.fill_price,
            "slippage_cost": fill.slippage_cost,
            "spread_cost": fill.spread_cost,
            "fee": fill.fee,
        }
        for fill in result.fills
    ]


def _economic_run(
    multiplier: float,
    engine: PortfolioBacktestEngine,
    result: PortfolioBacktestResult,
    strategy: Any,
) -> dict[str, Any]:
    report = build_portfolio_report(result, strategy_name=strategy.name, strategy_version=strategy.version)
    periods = {
        "development": _period_metrics(engine, result, START, DEVELOPMENT_END),
        "validation": _period_metrics(engine, result, VALIDATION_START, END_BOUND),
        "full": _period_metrics(engine, result, START, END_BOUND),
    }
    role_target_hits: Counter[str] = Counter()
    for record in result.rebalances:
        for symbol, _weight in record.target_weights:
            if symbol in RESERVED_ROLES:
                role_target_hits[symbol] += 1
    role_observation_hits = Counter()
    for _timestamp, symbols in result.universe_history:
        for symbol in set(symbols) & set(RESERVED_ROLES):
            role_observation_hits[symbol] += 1
    return {
        "cost_multiplier": multiplier,
        "full_summary": report.summary,
        "periods": periods,
        "reserved_diagnostics": {
            "exact_target_decision_count": sum(role_target_hits.values()),
            "exact_target_decision_count_by_symbol": dict(
                sorted((symbol, role_target_hits.get(symbol, 0)) for symbol in RESERVED_ROLES)
            ),
            "observation_decision_hits": dict(sorted(role_observation_hits.items())),
            "reserved_symbols": list(RESERVED_ROLES),
        },
        "evidence": {
            "equity_curve": [
                {"timestamp": timestamp.isoformat(), "equity": equity}
                for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
            ],
            "holdings_history": [
                {
                    "timestamp": timestamp.isoformat(),
                    "positions": count,
                    "cash_ratio": cash,
                    "concentration": concentration,
                }
                for timestamp, (count, cash, concentration) in zip(
                    result.curve_timestamps,
                    getattr(engine, "_holdings_history", []),
                    strict=True,
                )
            ],
            "fills": _compact_fills(result),
            "terminal_events": list(result.terminal_events),
            "rebalances": _compact_rebalances(result),
            "curve_point_count": len(result.equity_curve),
            "decision_bar_count": len(result.curve_timestamps),
        },
        "engine_checks": {
            "all_curve_equity_positive": all(value > 0 for value in result.equity_curve),
            "ending_cash": float(getattr(engine, "_cash", 0.0)),
            "fill_timestamps_after_decisions": all(fill.timestamp > fill.decision_timestamp for fill in result.fills),
            "target_max_positions": max(
                (len(record.target_weights) for record in result.rebalances),
                default=0,
            ),
            "target_max_single_weight": max(
                (
                    max((weight for _symbol, weight in record.target_weights), default=0.0)
                    for record in result.rebalances
                ),
                default=0.0,
            ),
            "target_max_gross": max(
                (sum(weight for _symbol, weight in record.target_weights) for record in result.rebalances),
                default=0.0,
            ),
            "target_min_cash": min(
                (record.target_cash_weight for record in result.rebalances),
                default=1.0,
            ),
        },
    }


def main() -> int:
    config = load_config(CONFIG_PATH)
    bars_manifest = json.loads(BARS_MANIFEST_PATH.read_text(encoding="utf-8"))
    ca_manifest = json.loads(CA_MANIFEST_PATH.read_text(encoding="utf-8"))
    hashes = {
        "bars_sha256": _sha256(BARS_PATH),
        "lifetimes_sha256": _sha256(LIFETIMES_PATH),
        "corporate_actions_sha256": _sha256(CORPORATE_ACTIONS_PATH),
        "config_sha256": _sha256(CONFIG_PATH),
    }
    expected_hashes = {
        "bars_sha256": EXPECTED_BARS_SHA256,
        "lifetimes_sha256": EXPECTED_LIFETIMES_SHA256,
        "corporate_actions_sha256": EXPECTED_CA_SHA256,
    }
    for name, expected in expected_hashes.items():
        if hashes[name] != expected:
            raise ValueError(f"{name} mismatch: {hashes[name]} != {expected}")

    bars, bar_normalization = _load_canonical_bars(BARS_PATH)
    lifecycles, lifecycle_summary = _load_lifecycles(bars)
    corporate_actions, corporate_action_summary = _load_cash_dividends()
    latest: dict[str, datetime] = {}
    for bar in bars:
        if bar.market == Market.EQUITY and bar.timeframe == "1d":
            latest[bar.symbol] = max(latest.get(bar.symbol, bar.timestamp), bar.timestamp)
    role_latest = {symbol: latest[symbol].isoformat() for symbol in RESERVED_ROLES}
    common_endpoint = min(latest[symbol] for symbol in RESERVED_ROLES)
    if common_endpoint.date() != END_BOUND.date():
        raise ValueError(f"Reserved common endpoint changed: {common_endpoint.isoformat()}")

    strategy_config = config.strategy.equity
    strategy = portfolio_strategy_from(
        strategy_config.name,
        strategy_config.parameters,
        timeframe=strategy_config.timeframe,
    )
    if strategy.name != EXPECTED_STRATEGY or strategy.version != EXPECTED_VERSION:
        raise ValueError(f"Unexpected strategy identity: {strategy.name} {strategy.version}")
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=END_BOUND,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    prepared = _prepared_window(bars, Market.EQUITY, strategy.timeframe, START, END_BOUND)
    _filtered, warmup, (ordered, _decision_bars) = prepared
    numeric_history = build_numeric_history(warmup + ordered, market=Market.EQUITY, timeframe=strategy.timeframe)

    # Structural phase.  Do not inspect or persist economic metrics until the
    # old-vs-corrected role comparison has been written to disk.
    old_engine, old_result = _run(
        config,
        strategy,
        universe,
        prepared,
        numeric_history,
        corporate_actions,
        lifecycles,
        1.0,
        (),
    )
    corrected_engine, corrected_result = _run(
        config,
        strategy,
        universe,
        prepared,
        numeric_history,
        corporate_actions,
        lifecycles,
        1.0,
        tuple(config.universe.equity.ordinary_alpha_excluded_symbols),
    )
    structural = {
        "phase": "structural_old_vs_corrected_before_economic_outputs",
        "metadata": {
            "main_sha": _git("rev-parse", "main"),
            "research_head_sha": _git("rev-parse", "HEAD"),
            "origin_main_sha": _git("rev-parse", "origin/main"),
            "branch": _git("branch", "--show-current"),
            "strategy": strategy.name,
            "strategy_version": strategy.version,
            "strategy_spec": strategy.spec().to_dict(),
            "parameters": dict(strategy_config.parameters),
            "role_boundary": list(config.universe.equity.ordinary_alpha_excluded_symbols),
            "reserved_roles": list(RESERVED_ROLES),
            "start": _iso(START),
            "development_end": _iso(DEVELOPMENT_END),
            "validation_start": _iso(VALIDATION_START),
            "end_bound": _iso(END_BOUND),
            "actual_common_endpoint": common_endpoint.isoformat(),
            "role_latest_bars": role_latest,
            "hashes": hashes,
            "bars_manifest": bars_manifest,
            "bar_normalization": bar_normalization,
            "corporate_actions_manifest": ca_manifest,
            "lifecycle_summary": lifecycle_summary,
            "corporate_action_summary": corporate_action_summary,
            "universe_hash": hash_point_in_time_universe(universe),
            "universe_snapshot_count": len(universe.snapshots),
            "prepared_warmup_bar_count": len(warmup),
            "prepared_evaluation_bar_count": len(ordered),
        },
        "comparison": _structural_comparison(universe, old_result, corrected_result),
        "coverage_checks": {
            "expected_curve_timestamps": len({bar.timestamp for bar in ordered}),
            "old_curve_timestamps": len(old_result.curve_timestamps),
            "corrected_curve_timestamps": len(corrected_result.curve_timestamps),
            "old_corrected_curve_timestamps_equal": old_result.curve_timestamps == corrected_result.curve_timestamps,
            "snapshot_effective_at_not_after_decision": all(
                (_snapshot_at(universe, record.decision_timestamp) is not None)
                and (_snapshot_at(universe, record.decision_timestamp).effective_at <= record.decision_timestamp)
                for record in corrected_result.rebalances
            ),
        },
    }
    STRUCTURAL_PATH.write_text(
        json.dumps(structural, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"STRUCTURAL_COMPARISON_PERSISTED={STRUCTURAL_PATH}")
    print(
        "STRUCTURAL_SUMMARY="
        + json.dumps(
            {
                "decisions": structural["comparison"]["decision_count"],
                "target_changes": structural["comparison"]["target_change_count"],
                "old_target_role_hits": structural["comparison"]["context_role_hits"]["old_target"],
                "corrected_target_role_hits": structural["comparison"]["context_role_hits"]["corrected_target"],
            },
            sort_keys=True,
        )
    )

    # Economic phase begins only after the structural artifact is durable.
    runs: dict[str, Any] = {}
    for multiplier, engine, result in (
        (1.0, corrected_engine, corrected_result),
        (
            2.0,
            *_run(
                config,
                strategy,
                universe,
                prepared,
                numeric_history,
                corporate_actions,
                lifecycles,
                2.0,
                tuple(config.universe.equity.ordinary_alpha_excluded_symbols),
            ),
        ),
        (
            3.0,
            *_run(
                config,
                strategy,
                universe,
                prepared,
                numeric_history,
                corporate_actions,
                lifecycles,
                3.0,
                tuple(config.universe.equity.ordinary_alpha_excluded_symbols),
            ),
        ),
    ):
        runs[str(multiplier)] = _economic_run(multiplier, engine, result, strategy)

    economic = {
        "phase": "corrected_economic_replay_after_structural_persistence",
        "metadata": structural["metadata"],
        "runs": runs,
        "frozen_gates": {
            "g5_max_drawdown": 0.05,
            "g6_3x_min_return": -0.25,
            "g6_3x_max_drawdown": 0.50,
        },
    }
    ECONOMIC_PATH.write_text(json.dumps(economic, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    for key in ("1.0", "2.0", "3.0"):
        full = economic["runs"][key]["periods"]["full"]
        print(
            "ECONOMIC_SUMMARY="
            + json.dumps(
                {
                    "cost_multiplier": key,
                    "full_net_return": full["net_total_return"],
                    "full_cagr": full["cagr"],
                    "full_sharpe": full["sharpe"],
                    "full_max_drawdown": full["max_drawdown"],
                    "validation_net_return": economic["runs"][key]["periods"]["validation"]["net_total_return"],
                    "target_reserved_count": economic["runs"][key]["reserved_diagnostics"][
                        "exact_target_decision_count"
                    ],
                },
                sort_keys=True,
            )
        )
    print(f"ECONOMIC_RESULTS_PERSISTED={ECONOMIC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
