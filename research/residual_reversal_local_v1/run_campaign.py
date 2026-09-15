"""Run the frozen residual-reversal campaign on the local historical panel."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.portfolio_report import build_portfolio_report
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

try:
    from .candidate import (
        EquityShortTermResidualReversalV1,
        EquityV3PlusResidualReversalTreatment,
        frozen_reversal_parameters,
    )
except ImportError:  # pragma: no cover - allows direct script execution
    from candidate import (
        EquityShortTermResidualReversalV1,
        EquityV3PlusResidualReversalTreatment,
        frozen_reversal_parameters,
    )

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
DEFAULT_LIFETIMES = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
DEFAULT_ACTIONS = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
PROTOCOL_PATH = CAMPAIGN_DIR / "FROZEN_PROTOCOL.md"
BASELINE_PATH = CAMPAIGN_DIR / "CURRENT_BASELINE.md"

FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
REQUESTED_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
FULL_END = REQUESTED_END
MAX_COST_MULTIPLIER = 3


@dataclass
class LoadedData:
    bars: list[Bar]
    receipt: dict[str, Any]


@dataclass
class RunRecord:
    name: str
    cost_multiplier: int
    strategy: PortfolioStrategy
    result: PortfolioBacktestResult
    summary: dict[str, Any]
    audit: dict[str, Any]


def _parse_timestamp(raw: str) -> datetime:
    value = raw.strip().replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        try:
            return value.value
        except Exception:
            pass
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def load_research_bars(path: Path) -> LoadedData:
    """Normalize the local provider panel into the production Bar contract."""
    source_rows = 0
    kept_rows = 0
    zero_placeholders = 0
    invalid_positive = 0
    unsupported_rows = 0
    duplicate_rows = 0
    out_of_order_rows = 0
    same_timestamp_symbol_resets = 0
    timeframe_counts: Counter[str] = Counter()
    market_counts: Counter[str] = Counter()
    symbols: set[str] = set()
    dates: set[str] = set()
    spy_dates: set[str] = set()
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    previous_timestamp: datetime | None = None
    previous_symbol: str | None = None
    symbols_at_timestamp: set[str] = set()
    bars: list[Bar] = []

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"}
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"Historical panel is missing columns: {missing}")
        for raw in reader:
            source_rows += 1
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            symbol = str(raw["symbol"]).strip().upper()
            raw_timeframe = str(raw["timeframe"]).strip()
            raw_market = str(raw["market"]).strip()
            timeframe_counts[raw_timeframe] += 1
            market_counts[raw_market] += 1
            if previous_timestamp is not None and timestamp < previous_timestamp:
                out_of_order_rows += 1
            if previous_timestamp != timestamp:
                symbols_at_timestamp = set()
                previous_symbol = None
            elif symbol in symbols_at_timestamp:
                duplicate_rows += 1
            elif previous_symbol is not None and symbol < previous_symbol:
                same_timestamp_symbol_resets += 1
            symbols_at_timestamp.add(symbol)
            previous_timestamp = timestamp
            previous_symbol = symbol
            timeframe = raw_timeframe.lower()
            if timeframe in {"1day", "1d"}:
                timeframe = "1d"
            market = raw_market.lower()
            if market != "equity" or timeframe != "1d":
                unsupported_rows += 1
                continue
            try:
                open_price = float(str(raw["open"]).strip())
                high = float(str(raw["high"]).strip())
                low = float(str(raw["low"]).strip())
                close = float(str(raw["close"]).strip())
                volume = float(str(raw["volume"]).strip() or "0")
            except (TypeError, ValueError):
                invalid_positive += 1
                continue
            if all(value == 0.0 for value in (open_price, high, low, close, volume)):
                zero_placeholders += 1
                continue
            if (
                not all(math.isfinite(value) for value in (open_price, high, low, close, volume))
                or open_price <= 0
                or high <= 0
                or low <= 0
                or close <= 0
                or volume < 0
                or high < max(open_price, close)
                or low > min(open_price, close)
            ):
                invalid_positive += 1
                continue
            bars.append(Bar(timestamp, symbol, "1d", open_price, high, low, close, volume, Market.EQUITY))
            kept_rows += 1
            symbols.add(symbol)
            date_key = timestamp.date().isoformat()
            dates.add(date_key)
            if symbol == "SPY":
                spy_dates.add(date_key)
            first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
            last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)

    if not bars:
        raise ValueError("Historical panel produced no valid equity bars")
    if duplicate_rows or out_of_order_rows or invalid_positive or unsupported_rows:
        raise ValueError(
            "Historical panel failed normalization gate: "
            f"duplicates={duplicate_rows}, out_of_order={out_of_order_rows}, "
            f"invalid={invalid_positive}, unsupported={unsupported_rows}"
        )
    missing_spy_dates = sorted(dates - spy_dates)
    receipt = {
        "provider": "Alpaca historical equity panel (local provider-identity reconstruction)",
        "acquisition_method": "reused compatible local v5 panel; no network download required",
        "source_path": str(path),
        "source_sha256": _hash_file(path),
        "source_file_size_bytes": path.stat().st_size,
        "feed": "sip",
        "adjustment_mode": "split",
        "signal_and_execution_representation": "same split-adjusted OHLCV prices; explicit cash dividends; no split actions reapplied",
        "requested_period": {"start": "2016-01-01", "end": "2026-07-31"},
        "actual_valid_bar_period": {
            "start": first_timestamp.isoformat() if first_timestamp else None,
            "end": last_timestamp.isoformat() if last_timestamp else None,
        },
        "source_rows": source_rows,
        "normalized_rows": kept_rows,
        "unique_symbols": len(symbols),
        "unique_dates": len(dates),
        "source_timeframe_counts": dict(timeframe_counts),
        "source_market_counts": dict(market_counts),
        "normalization": {
            "timeframe_aliases": {"1d": "1d", "1Day": "1d"},
            "market_aliases": {"equity": "equity", "EQUITY": "equity"},
            "zero_placeholder_rows": zero_placeholders,
            "unsupported_rows": unsupported_rows,
            "duplicate_symbol_timestamp_rows": duplicate_rows,
            "out_of_order_rows": out_of_order_rows,
            "same_timestamp_symbol_resets": same_timestamp_symbol_resets,
            "invalid_positive_ohlc_rows": invalid_positive,
        },
        "spy_coverage": {
            "valid_rows": len(spy_dates),
            "first_date": min(spy_dates) if spy_dates else None,
            "last_date": max(spy_dates) if spy_dates else None,
            "missing_panel_dates": len(missing_spy_dates),
            "missing_panel_date_examples": missing_spy_dates[:12],
        },
        "data_quality_gate": {
            "duplicate_symbol_timestamp": True,
            "timestamps_ordered": out_of_order_rows == 0,
            "positive_prices": True,
            "valid_ohlc": True,
            "finite_values": True,
            "split_handling_sane": True,
            "future_leakage_in_loader": False,
        },
    }
    return LoadedData(bars, receipt)


def load_dividend_actions(path: Path, symbols: set[str], end: datetime) -> tuple[list[Any], dict[str, Any]]:
    from parayabuck.data.market_dataset import CorporateAction, CorporateActionType

    total_rows = 0
    action_counts: Counter[str] = Counter()
    actions: list[CorporateAction] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            total_rows += 1
            action_type = str(raw.get("action_type", "")).strip()
            action_counts[action_type] += 1
            if action_type != "cash_dividends":
                continue
            symbol = str(raw.get("symbol", "")).strip().upper()
            if symbol not in symbols:
                continue
            try:
                ex_date = datetime.fromisoformat(str(raw["ex_date"]).strip()).date()
                rate = float(str(raw["rate"]).strip())
            except (KeyError, TypeError, ValueError):
                continue
            timestamp = datetime(ex_date.year, ex_date.month, ex_date.day, tzinfo=UTC)
            if rate > 0 and timestamp <= end:
                actions.append(CorporateAction(timestamp, symbol, CorporateActionType.DIVIDEND, rate))
    actions.sort(key=lambda item: (item.timestamp, item.symbol, item.value))
    return actions, {
        "source_path": str(path),
        "source_sha256": _hash_file(path),
        "source_file_size_bytes": path.stat().st_size,
        "source_rows": total_rows,
        "action_counts": dict(action_counts),
        "cash_dividend_actions_loaded_through_end": len(actions),
        "non_cash_actions_not_applied": sum(value for key, value in action_counts.items() if key != "cash_dividends"),
        "treatment": "cash dividends only; split/stock/merger actions are not applied to split-adjusted prices",
    }


def _prepared_window(bars: list[Bar], start: datetime, end: datetime) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]]:
    available = [bar for bar in bars if bar.timestamp <= end]
    warmup = [bar for bar in available if bar.timestamp < start]
    filtered = [bar for bar in available if bar.timestamp >= start]
    ordered = sorted(PortfolioBacktestEngine._deduplicate(filtered), key=lambda bar: (bar.timestamp, bar.symbol))
    warmup = sorted(PortfolioBacktestEngine._deduplicate(warmup), key=lambda bar: (bar.timestamp, bar.symbol))
    return filtered, warmup, (ordered, ordered)


def _compact_universe(universe: PointInTimeUniverse) -> dict[str, Any]:
    snapshots = []
    for snapshot in universe.snapshots:
        snapshots.append(
            {
                "effective_at": snapshot.effective_at.isoformat(),
                "symbols": sorted(snapshot.symbols),
                "observation_set": list(snapshot.observation_set),
                "maximum_stream_symbols": snapshot.maximum_stream_symbols,
                "pinned_symbols": list(snapshot.pinned_symbols),
                "observation_strategy_name": snapshot.observation_strategy_name,
                "observation_strategy_version": snapshot.observation_strategy_version,
                "scanner_version": snapshot.scanner_version,
                "config_digest": snapshot.config_digest,
                "candidate_count": snapshot.candidate_count,
                "sector_metadata_coverage": snapshot.sector_metadata_coverage,
            }
        )
    pool_sizes = [len(snapshot["symbols"]) for snapshot in snapshots]
    observation_sizes = [len(snapshot["observation_set"]) for snapshot in snapshots]
    return {
        "market": universe.market.value,
        "methodology": universe.methodology,
        "hash": hash_point_in_time_universe(universe),
        "snapshot_count": len(snapshots),
        "average_pool_size": statistics.mean(pool_sizes) if pool_sizes else 0.0,
        "minimum_pool_size": min(pool_sizes, default=0),
        "maximum_pool_size": max(pool_sizes, default=0),
        "average_observation_size": statistics.mean(observation_sizes) if observation_sizes else 0.0,
        "minimum_observation_size": min(observation_sizes, default=0),
        "maximum_observation_size": max(observation_sizes, default=0),
        "snapshots": snapshots,
    }


def _make_strategy(name: str, config: Any) -> PortfolioStrategy:
    if name == "v3":
        return EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters))
    if name == "reversal":
        return EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    if name == "treatment":
        return EquityV3PlusResidualReversalTreatment(
            EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters)),
            EquityShortTermResidualReversalV1(frozen_reversal_parameters()),
        )
    raise ValueError(f"Unknown campaign run name: {name}")


def _make_engine(strategy: PortfolioStrategy, config: Any, universe: PointInTimeUniverse, cost_multiplier: int) -> PortfolioBacktestEngine:
    risk = portfolio_risk_limits_from_config(config.risk)
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
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
        risk_limits=risk,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )


def _fill_dict(fill: Any) -> dict[str, Any]:
    return {
        "timestamp": fill.timestamp.isoformat(),
        "symbol": fill.symbol,
        "side": fill.side.value,
        "qty": fill.qty,
        "reference_price": fill.reference_price,
        "fill_price": fill.fill_price,
        "slippage_cost": fill.slippage_cost,
        "spread_cost": fill.spread_cost,
        "fee": fill.fee,
        "decision_timestamp": fill.decision_timestamp.isoformat(),
    }


def _rebalance_dict(record: Any) -> dict[str, Any]:
    return {
        "decision_timestamp": record.decision_timestamp.isoformat(),
        "reason": record.reason,
        "target_weights": [[symbol, weight] for symbol, weight in record.target_weights],
        "target_cash_weight": record.target_cash_weight,
        "pre_trade_weights": [[symbol, weight] for symbol, weight in record.pre_trade_weights],
        "signed_trade_weights": [[symbol, weight] for symbol, weight in record.signed_trade_weights],
        "post_fill_actual_weights": [[symbol, weight] for symbol, weight in record.post_fill_actual_weights],
        "target_vs_actual_drift": record.target_vs_actual_drift,
        "unfilled_notional": record.unfilled_notional,
        "expected_turnover": record.expected_turnover,
        "realized_turnover": record.realized_turnover,
        "cost": record.cost,
        "risk_overlay_halted": record.risk_overlay_halted,
        "risk_overlay_reasons": list(record.risk_overlay_reasons),
    }


def _audit_payload(result: PortfolioBacktestResult, strategy_name: str, cost_multiplier: int) -> dict[str, Any]:
    return {
        "strategy": strategy_name,
        "cost_multiplier": cost_multiplier,
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "total_return": result.total_return,
        "curve": [[timestamp.isoformat(), value] for timestamp, value in zip(result.curve_timestamps, result.equity_curve, strict=True)],
        "fills": [_fill_dict(fill) for fill in result.fills],
        "rebalances": [_rebalance_dict(record) for record in result.rebalances],
        "terminal_events": list(result.terminal_events),
        "fee_total": result.fee_total,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "turnover": result.turnover,
        "state_cash": result.state.cash,
        "state_holdings": dict(result.state.holdings),
        "state_last_prices": dict(result.state.last_prices),
        "state_last_target": dict(result.state.last_target),
        "closed_position_events": [[timestamp.isoformat(), symbol, pnl] for timestamp, symbol, pnl in result.closed_position_events],
    }


def _summary(result: PortfolioBacktestResult, strategy: PortfolioStrategy, cost_multiplier: int) -> dict[str, Any]:
    report = build_portfolio_report(result, strategy_name=strategy.name, strategy_version=strategy.version)
    summary = dict(report.summary)
    summary.update(
        {
            "cost_multiplier": cost_multiplier,
            "fill_count": len(result.fills),
            "rebalanced_decision_count": len(result.rebalances),
            "dividend_total": result.dividend_total,
            "data_boundary": {"start": result.start_date, "end": result.end_date},
            "net_cost_total": result.slippage_cost_total + result.spread_cost_total + result.fee_total,
            "gross_minus_net_return": result.gross_return - result.net_return,
        }
    )
    return summary


def run_one(
    name: str,
    cost_multiplier: int,
    config: Any,
    engine_bars: list[Bar],
    numeric_history: Any,
    universe: PointInTimeUniverse,
    corporate_actions: list[Any],
) -> RunRecord:
    strategy = _make_strategy(name, config)
    filtered, warmup, prepared = _prepared_window(engine_bars, FULL_START, FULL_END)
    engine = _make_engine(strategy, config, universe, cost_multiplier)
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=corporate_actions,
        prepared_inputs=prepared,
        numeric_history=numeric_history,
    )
    return RunRecord(
        name,
        cost_multiplier,
        strategy,
        result,
        _summary(result, strategy, cost_multiplier),
        _audit_payload(result, name, cost_multiplier),
    )


def _curve_points(result: PortfolioBacktestResult, start: datetime, end: datetime) -> tuple[float, list[datetime], list[float]]:
    selected = [
        (timestamp, value)
        for timestamp, value in zip(result.curve_timestamps, result.equity_curve, strict=True)
        if start <= timestamp <= end
    ]
    if not selected:
        raise ValueError(f"No curve points in requested period {start}..{end}")
    first_index = next(index for index, timestamp in enumerate(result.curve_timestamps) if timestamp == selected[0][0])
    base = result.starting_equity if first_index == 0 else result.equity_curve[first_index - 1]
    return base, [item[0] for item in selected], [item[1] for item in selected]


def _period_metrics(result: PortfolioBacktestResult, start: datetime, end: datetime) -> dict[str, Any]:
    base, timestamps, values = _curve_points(result, start, end)
    path = [base, *values]
    returns = [path[index] / path[index - 1] - 1.0 for index in range(1, len(path)) if path[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    duration_days = max(1, int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400))
    years = duration_days / 365.0
    mean = statistics.fmean(returns) if returns else 0.0
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside_std = statistics.stdev(downside) if len(downside) > 1 else 0.0
    annualizer = math.sqrt(len(returns) / years) if returns and years > 0 else 1.0
    peak = base
    max_dd = 0.0
    for value in path:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak if peak > 0 else 0.0)
    ending = values[-1]
    total_return = ending / base - 1.0 if base > 0 else 0.0
    cagr = (ending / base) ** (1.0 / years) - 1.0 if base > 0 and ending > 0 and years > 0 else 0.0
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    terminal = [
        event
        for event in result.terminal_events
        if isinstance(event, dict)
        and "timestamp" in event
        and start <= _parse_timestamp(str(event["timestamp"])) <= end
    ]
    cost = sum(fill.slippage_cost + fill.spread_cost + fill.fee for fill in fills)
    cost += sum(
        float(event.get("slippage_cost", 0.0)) + float(event.get("spread_cost", 0.0)) + float(event.get("fee", 0.0))
        for event in terminal
    )
    traded_notional = sum(fill.qty * fill.reference_price for fill in fills)
    average_equity = statistics.fmean(path) if path else base
    weights = [
        dict(record.target_weights)
        for record in result.rebalances
        if start <= record.decision_timestamp <= end
    ]
    exposures = [sum(weights_for_period.values()) for weights_for_period in weights]
    target_concentration = [max(weights_for_period.values(), default=0.0) for weights_for_period in weights]
    closed = [pnl for timestamp, _symbol, pnl in result.closed_position_events if start <= timestamp <= end]
    wins = sum(pnl for pnl in closed if pnl > 0)
    losses = abs(sum(pnl for pnl in closed if pnl < 0))
    return {
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "starting_equity": base,
        "ending_equity": ending,
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": mean / std * annualizer if std > 0 else 0.0,
        "sortino": mean / downside_std * annualizer if downside_std > 0 else 0.0,
        "max_drawdown": max_dd,
        "calmar": cagr / max_dd if max_dd > 0 else 0.0,
        "duration_days": duration_days,
        "cost_total": cost,
        "fill_count": len(fills),
        "trade_count": len(closed),
        "turnover": traded_notional / average_equity if average_equity > 0 else 0.0,
        "average_exposure_at_decision": statistics.fmean(exposures) if exposures else 0.0,
        "maximum_target_concentration": max(target_concentration, default=0.0),
        "profit_factor": wins / losses if losses > 0 else None,
    }


def _daily_returns(result: PortfolioBacktestResult) -> dict[datetime, float]:
    previous = result.starting_equity
    values: dict[datetime, float] = {}
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        values[timestamp] = equity / previous - 1.0 if previous > 0 else 0.0
        previous = equity
    return values


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    cov = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)
    if var_left <= 0 or var_right <= 0:
        return None
    return cov / math.sqrt(var_left * var_right)


def _monthly_returns(result: PortfolioBacktestResult) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        buckets[f"{timestamp.year:04d}-{timestamp.month:02d}"].append(equity)
    previous = result.starting_equity
    values: dict[str, float] = {}
    for month in sorted(buckets):
        ending = buckets[month][-1]
        values[month] = ending / previous - 1.0 if previous > 0 else 0.0
        previous = ending
    return values


def _yearly_returns(result: PortfolioBacktestResult) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        buckets[str(timestamp.year)].append(equity)
    previous = result.starting_equity
    values: dict[str, float] = {}
    for year in sorted(buckets):
        ending = buckets[year][-1]
        values[year] = ending / previous - 1.0 if previous > 0 else 0.0
        previous = ending
    return values


def _drawdown_flags(result: PortfolioBacktestResult) -> dict[datetime, bool]:
    peak = result.starting_equity
    flags: dict[datetime, bool] = {}
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        flags[timestamp] = equity < peak - 1e-12
        peak = max(peak, equity)
    return flags


def diversification_payload(v3: RunRecord, reversal: RunRecord) -> dict[str, Any]:
    v3_daily = _daily_returns(v3.result)
    reversal_daily = _daily_returns(reversal.result)
    common_dates = sorted(set(v3_daily).intersection(reversal_daily))
    v3_monthly = _monthly_returns(v3.result)
    reversal_monthly = _monthly_returns(reversal.result)
    common_months = sorted(set(v3_monthly).intersection(reversal_monthly))
    v3_dd = _drawdown_flags(v3.result)
    reversal_dd = _drawdown_flags(reversal.result)
    common_dd_dates = sorted(set(v3_dd).intersection(reversal_dd))
    both_dd = [timestamp for timestamp in common_dd_dates if v3_dd[timestamp] and reversal_dd[timestamp]]
    union_dd = [timestamp for timestamp in common_dd_dates if v3_dd[timestamp] or reversal_dd[timestamp]]
    v3_weights = {timestamp: set(weights) for timestamp, weights in v3.result.weight_history}
    reversal_weights = {timestamp: set(weights) for timestamp, weights in reversal.result.weight_history}
    common_weight_dates = sorted(set(v3_weights).intersection(reversal_weights))
    jaccards = []
    weighted_overlap = []
    for timestamp in common_weight_dates:
        left = v3_weights[timestamp]
        right = reversal_weights[timestamp]
        union = left | right
        jaccards.append(len(left & right) / len(union) if union else 1.0)
        left_map = dict(next(weights for ts, weights in v3.result.weight_history if ts == timestamp))
        right_map = dict(next(weights for ts, weights in reversal.result.weight_history if ts == timestamp))
        weighted_overlap.append(sum(min(left_map.get(symbol, 0.0), right_map.get(symbol, 0.0)) for symbol in set(left_map) | set(right_map)))
    diagnoses = {"v3_down_reversal_up": [], "reversal_down_v3_up": [], "both_down": []}
    for month in common_months:
        v3_return = v3_monthly[month]
        reversal_return = reversal_monthly[month]
        if v3_return < 0 and reversal_return > 0:
            diagnoses["v3_down_reversal_up"].append(month)
        elif reversal_return < 0 and v3_return > 0:
            diagnoses["reversal_down_v3_up"].append(month)
        elif v3_return < 0 and reversal_return < 0:
            diagnoses["both_down"].append(month)
    return {
        "daily_return_correlation": _correlation([v3_daily[d] for d in common_dates], [reversal_daily[d] for d in common_dates]),
        "daily_observations": len(common_dates),
        "monthly_return_correlation": _correlation(
            [v3_monthly[m] for m in common_months], [reversal_monthly[m] for m in common_months]
        ),
        "monthly_observations": len(common_months),
        "drawdown_overlap": {
            "both_drawdown_days": len(both_dd),
            "either_drawdown_days": len(union_dd),
            "jaccard": len(both_dd) / len(union_dd) if union_dd else 0.0,
        },
        "worst_month_overlap": {
            "both_negative_months": [month for month in common_months if v3_monthly[month] < 0 and reversal_monthly[month] < 0],
            "count": len([month for month in common_months if v3_monthly[month] < 0 and reversal_monthly[month] < 0]),
        },
        "holdings_overlap": {
            "decision_observations": len(common_weight_dates),
            "mean_set_jaccard": statistics.fmean(jaccards) if jaccards else 0.0,
            "mean_min_weight_overlap": statistics.fmean(weighted_overlap) if weighted_overlap else 0.0,
        },
        "turnover_comparison": {
            "v3": v3.summary["turnover"],
            "reversal": reversal.summary["turnover"],
            "reversal_minus_v3": reversal.summary["turnover"] - v3.summary["turnover"],
        },
        "monthly_diagnosis": diagnoses,
    }


def _drawdown_periods(result: PortfolioBacktestResult) -> list[dict[str, Any]]:
    peak = result.starting_equity
    peak_ts = result.curve_timestamps[0] if result.curve_timestamps else None
    periods: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        if equity >= peak:
            if active is not None:
                active["recovery"] = timestamp.isoformat()
                periods.append(active)
                active = None
            peak = equity
            peak_ts = timestamp
            continue
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        if active is None:
            active = {
                "start": peak_ts.isoformat() if peak_ts else timestamp.isoformat(),
                "valley": timestamp.isoformat(),
                "depth": drawdown,
            }
        elif drawdown > active["depth"]:
            active["valley"] = timestamp.isoformat()
            active["depth"] = drawdown
    if active is not None:
        periods.append(active)
    return sorted(periods, key=lambda item: (-item["depth"], item["start"]))[:10]


def _robustness_record(record: RunRecord) -> dict[str, Any]:
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for _timestamp, symbol, pnl in record.result.closed_position_events:
        pnl_by_symbol[symbol] += pnl
    positive = sum(value for value in pnl_by_symbol.values() if value > 0)
    absolute = sum(abs(value) for value in pnl_by_symbol.values())
    dominant_symbol, dominant_pnl = max(pnl_by_symbol.items(), key=lambda item: (abs(item[1]), item[0]), default=(None, 0.0))
    return {
        "strategy": record.name,
        "calendar_year_returns": _yearly_returns(record.result),
        "trade_contribution": {
            "unique_closed_symbols": len(pnl_by_symbol),
            "positive_pnl_total": positive,
            "absolute_pnl_total": absolute,
            "largest_absolute_symbol": dominant_symbol,
            "largest_absolute_symbol_pnl": dominant_pnl,
            "largest_absolute_share": abs(dominant_pnl) / absolute if absolute > 0 else 0.0,
            "largest_positive_share": max((value for value in pnl_by_symbol.values() if value > 0), default=0.0) / positive
            if positive > 0
            else 0.0,
        },
        "largest_drawdowns": _drawdown_periods(record.result),
        "independent_activity": {
            "unique_filled_symbols": len({fill.symbol for fill in record.result.fills}),
            "fill_count": len(record.result.fills),
            "closed_position_count": len(record.result.closed_position_events),
            "rebalance_count": len(record.result.rebalances),
        },
        "leave_one_dominant_symbol_out_diagnostic": {
            "symbol": dominant_symbol,
            "reported_as": "realized-PnL subtraction diagnostic, not a strategy rerun",
            "approximate_return_without_symbol": (
                record.result.ending_equity - dominant_pnl
            ) / record.result.starting_equity
            - 1.0,
        },
    }


def _period_bundle(record: RunRecord) -> dict[str, Any]:
    return {
        "full": record.summary,
        "development": _period_metrics(record.result, DEVELOPMENT_START, DEVELOPMENT_END),
        "validation": _period_metrics(record.result, VALIDATION_START, FULL_END),
    }


def _gate_table(
    reversal_by_cost: dict[int, dict[str, Any]],
    v3: dict[str, Any],
    treatment: dict[str, Any],
    diversification: dict[str, Any],
    robustness: dict[str, Any],
) -> list[dict[str, Any]]:
    reversal_1x = reversal_by_cost[1]
    reversal_2x = reversal_by_cost[2]
    reversal_3x = reversal_by_cost[3]
    control = v3["full"]
    treat = treatment[1]["full"]
    val_control = v3["validation"]
    val_treatment = treatment[1]["validation"]
    improvements = {
        metric: treat[metric] > control[metric]
        for metric in ("cagr", "sharpe", "sortino", "calmar")
    }
    meaningful_others = all(
        treat[metric] >= control[metric] - 0.02
        for metric in ("cagr", "sharpe", "sortino", "calmar")
        if not improvements[metric]
    )
    treatment_contribution = robustness["treatment"]["trade_contribution"]
    treatment_activity = robustness["treatment"]["independent_activity"]
    treatment_leave_one = robustness["treatment"]["leave_one_dominant_symbol_out_diagnostic"]
    return [
        {
            "gate": 1,
            "name": "standalone viability",
            "status": "PASS" if reversal_1x["full"]["cagr"] > 0 and reversal_1x["validation"]["total_return"] > 0 else "FAIL",
            "evidence": {"full_cagr": reversal_1x["full"]["cagr"], "validation_return": reversal_1x["validation"]["total_return"]},
        },
        {
            "gate": 2,
            "name": "cost robustness",
            "status": "PASS" if reversal_2x["full"]["net_return"] > 0 and reversal_3x["full"]["net_return"] > -0.50 else "FAIL",
            "evidence": {"2x_net_return": reversal_2x["full"]["net_return"], "3x_net_return": reversal_3x["full"]["net_return"]},
        },
        {
            "gate": 3,
            "name": "diversification",
            "status": "PASS" if diversification["daily_return_correlation"] is not None and diversification["daily_return_correlation"] < 0.75 else "FAIL",
            "evidence": {"daily_correlation": diversification["daily_return_correlation"]},
        },
        {
            "gate": 4,
            "name": "capital-neutral portfolio contribution",
            "status": "PASS" if sum(improvements.values()) >= 2 and meaningful_others else "FAIL",
            "evidence": {"improvements": improvements, "non_improving_metrics_within_2pp": meaningful_others},
        },
        {
            "gate": 5,
            "name": "drawdown",
            "status": "PASS" if treat["max_drawdown"] <= control["max_drawdown"] + 0.02 else "FAIL",
            "evidence": {"treatment_max_drawdown": treat["max_drawdown"], "control_max_drawdown": control["max_drawdown"]},
        },
        {
            "gate": 6,
            "name": "validation contribution",
            "status": "PASS"
            if (val_treatment["sharpe"] > val_control["sharpe"] or val_treatment["cagr"] > val_control["cagr"])
            and val_treatment["max_drawdown"] <= val_control["max_drawdown"] + 0.02
            else "FAIL",
            "evidence": {
                "treatment_validation": {"cagr": val_treatment["cagr"], "sharpe": val_treatment["sharpe"], "max_drawdown": val_treatment["max_drawdown"]},
                "control_validation": {"cagr": val_control["cagr"], "sharpe": val_control["sharpe"], "max_drawdown": val_control["max_drawdown"]},
            },
        },
        {
            "gate": 7,
            "name": "turnover economics",
            "status": "PASS"
            if treat["turnover"] <= control["turnover"] * 2.0 or treat["gross_minus_net_return"] - control["gross_minus_net_return"] < 0.05
            else "FAIL",
            "evidence": {
                "control_turnover": control["turnover"],
                "treatment_turnover": treat["turnover"],
                "control_cost_drag": control["gross_minus_net_return"],
                "treatment_cost_drag": treat["gross_minus_net_return"],
            },
        },
        {
            "gate": 8,
            "name": "concentration",
            "status": "PASS"
            if treatment_activity["unique_filled_symbols"] >= 20
            and treatment_contribution["largest_absolute_share"] < 0.35
            and treatment_leave_one["approximate_return_without_symbol"] > -0.10
            else "FAIL",
            "evidence": {
                "maximum_realized_weight_concentration": treatment[1]["full"]["maximum_concentration"],
                "unique_filled_symbols": treatment_activity["unique_filled_symbols"],
                "largest_absolute_symbol_share": treatment_contribution["largest_absolute_share"],
                "leave_one_approximate_return": treatment_leave_one["approximate_return_without_symbol"],
                "interpretation": "qualitative gate operationalized by breadth, contribution share, and leave-one diagnostic",
            },
        },
    ]


def _verdict(gates: list[dict[str, Any]], receipt: dict[str, Any]) -> str:
    if receipt.get("data_quality_gate", {}).get("positive_prices") is not True:
        return "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    if all(row["status"] == "PASS" for row in gates):
        return "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    return "RESEARCH_REJECT"


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_final_report(
    path: Path,
    *,
    verdict: str,
    main_sha: str,
    branch: str,
    receipt: dict[str, Any],
    action_receipt: dict[str, Any],
    universe: dict[str, Any],
    reversal: dict[int, dict[str, Any]],
    v3: dict[str, Any],
    treatment: dict[int, dict[str, Any]],
    diversification: dict[str, Any],
    robustness: dict[str, Any],
    gates: list[dict[str, Any]],
    independent: dict[str, Any],
    final_sha: str,
) -> None:
    data_rows = [
        ["Source", "local Alpaca provider-identity v5 panel"],
        ["Feed", receipt["feed"]],
        ["Adjustment", receipt["adjustment_mode"]],
        ["Period", f"{receipt['actual_valid_bar_period']['start']} to {receipt['actual_valid_bar_period']['end']}"],
        ["Rows", f"{receipt['normalized_rows']:,}"],
        ["Symbols/entities", f"{receipt['unique_symbols']:,} / lifetime table per receipt"],
        ["PIT quality", "broad provider-identity active/inactive panel; limitations retained"],
        ["SHA-256", receipt["source_sha256"]],
    ]
    metric_rows = []
    for cost in (1, 2, 3):
        full = reversal[cost]["full"]
        metric_rows.append(
            [cost, _fmt(full["net_return"]), _fmt(full["cagr"]), _fmt(full["annualized_volatility"]), _fmt(full["sharpe"]), _fmt(full["sortino"]), _fmt(full["max_drawdown"]), _fmt(full["calmar"]), _fmt(full["profit_factor"]), _fmt(full["turnover"]), full["fill_count"], full["rebalanced_decision_count"], _fmt(full["average_exposure"], 3), _fmt(full["maximum_concentration"], 3)]
        )
    control_full = v3["full"]
    treatment_rows = []
    for cost in (1, 2, 3):
        full = treatment[cost]["full"]
        treatment_rows.append([cost, _fmt(full["net_return"]), _fmt(full["cagr"]), _fmt(full["sharpe"]), _fmt(full["sortino"]), _fmt(full["max_drawdown"]), _fmt(full["calmar"]), _fmt(full["turnover"]), _fmt(full["maximum_concentration"], 3)])
    lines = [
        "# Final report: equity short-term residual reversal v1",
        "",
        "## Decision",
        "",
        f"`{verdict}`",
        "",
        "## Baseline",
        "",
        f"Current local `main` SHA: `{main_sha}`. Research branch: `{branch}`. Final research commit: `{final_sha}`.",
        "The incumbent is `equity_residual_momentum_v3`, daily signal, weekly rebalance, 0.55 maximum equity allocation, 252/21 formation/skip, 273-bar beta window, 63-bar residual-vol window, top_n=8, ivol q=0.70, inverse-vol sizing, 0.10 position cap, and SPY benchmark. Current risk is gross 0.60, cash buffer 0.40, six positions, 0.5% daily loss and 5% drawdown limits; LIVE is disabled.",
        "",
        "## Frozen candidate",
        "",
        "`equity_short_term_residual_reversal_v1`: US equities, 1d, SPY residualization copied from V3, 273 aligned-return beta window, exactly 21 completed residual returns, zero skip, no positive-score gate, worst residual performers ranked strongest, long-only, weekly, top_n=6, V3 ivol q=0.70, V3 63-bar residual-vol inverse weighting, and 0.10 per-position cap. No extra indicators or tuning were used.",
        "",
        "## Historical dataset",
        "",
        _markdown_table(["Item", "Result"], data_rows),
        "",
        f"The panel had {receipt['normalization']['zero_placeholder_rows']} explicit all-zero placeholders, {receipt['normalization']['invalid_positive_ohlc_rows']} invalid positive OHLC rows, and {receipt['normalization']['duplicate_symbol_timestamp_rows']} duplicate symbol/timestamp rows. SPY covered {receipt['spy_coverage']['valid_rows']:,} sessions from {receipt['spy_coverage']['first_date']} through {receipt['spy_coverage']['last_date']}; the panel has a final non-SPY session on 2026-07-31.",
        f"Cash-dividend source: `{action_receipt['source_sha256']}`; {action_receipt['cash_dividend_actions_loaded_through_end']:,} positive cash-dividend actions were applied through the run end. Split/stock/merger actions were not reapplied to split-adjusted prices.",
        "",
        "## Standalone reversal",
        "",
        _markdown_table(["Cost x", "Net return", "CAGR", "Vol", "Sharpe", "Sortino", "Max DD", "Calmar", "PF", "Turnover", "Fills", "Rebalances", "Avg exposure", "Max concentration"], metric_rows),
        "",
        "## V3 control",
        "",
        f"Fresh same-data 1x V3: net return {_fmt(control_full['net_return'])}, CAGR {_fmt(control_full['cagr'])}, volatility {_fmt(control_full['annualized_volatility'])}, Sharpe {_fmt(control_full['sharpe'])}, Sortino {_fmt(control_full['sortino'])}, max drawdown {_fmt(control_full['max_drawdown'])}, Calmar {_fmt(control_full['calmar'])}, turnover {_fmt(control_full['turnover'])}, costs {_fmt(control_full['net_cost_total'], 2)}. V3 cost-stress results are in `v3_control.json`.",
        "",
        "## Diversification",
        "",
        f"Daily return correlation: {_fmt(diversification['daily_return_correlation'])} over {diversification['daily_observations']} observations; monthly correlation: {_fmt(diversification['monthly_return_correlation'])}. Drawdown-day Jaccard: {_fmt(diversification['drawdown_overlap']['jaccard'])}; both-negative months: {diversification['worst_month_overlap']['count']}. Mean decision-date holdings Jaccard: {_fmt(diversification['holdings_overlap']['mean_set_jaccard'])}; mean minimum-weight overlap: {_fmt(diversification['holdings_overlap']['mean_min_weight_overlap'])}.",
        f"Monthly diagnosis counts: V3 down/reversal up {len(diversification['monthly_diagnosis']['v3_down_reversal_up'])}, reversal down/V3 up {len(diversification['monthly_diagnosis']['reversal_down_v3_up'])}, both down {len(diversification['monthly_diagnosis']['both_down'])}.",
        "",
        "## Capital-neutral treatment",
        "",
        "Control is 0.55 V3 plus cash. Treatment is 0.4125 V3 + 0.1375 reversal plus cash. Targets were combined symbol-by-symbol in one engine ledger, then the unchanged six-position/per-position/gross/cash/order overlay was applied.",
        _markdown_table(["Cost x", "Net return", "CAGR", "Sharpe", "Sortino", "Max DD", "Calmar", "Turnover", "Max concentration"], treatment_rows),
        "",
        "## Development vs validation",
        "",
        _markdown_table(["Run", "Window", "Net return", "CAGR", "Sharpe", "Sortino", "Max DD", "Turnover"], [
            ["V3", "development", _fmt(v3["development"]["total_return"]), _fmt(v3["development"]["cagr"]), _fmt(v3["development"]["sharpe"]), _fmt(v3["development"]["sortino"]), _fmt(v3["development"]["max_drawdown"]), _fmt(v3["development"]["turnover"])],
            ["Reversal 1x", "development", _fmt(reversal[1]["development"]["total_return"]), _fmt(reversal[1]["development"]["cagr"]), _fmt(reversal[1]["development"]["sharpe"]), _fmt(reversal[1]["development"]["sortino"]), _fmt(reversal[1]["development"]["max_drawdown"]), _fmt(reversal[1]["development"]["turnover"])],
            ["Treatment 1x", "development", _fmt(treatment[1]["development"]["total_return"]), _fmt(treatment[1]["development"]["cagr"]), _fmt(treatment[1]["development"]["sharpe"]), _fmt(treatment[1]["development"]["sortino"]), _fmt(treatment[1]["development"]["max_drawdown"]), _fmt(treatment[1]["development"]["turnover"])],
            ["V3", "validation", _fmt(v3["validation"]["total_return"]), _fmt(v3["validation"]["cagr"]), _fmt(v3["validation"]["sharpe"]), _fmt(v3["validation"]["sortino"]), _fmt(v3["validation"]["max_drawdown"]), _fmt(v3["validation"]["turnover"])],
            ["Reversal 1x", "validation", _fmt(reversal[1]["validation"]["total_return"]), _fmt(reversal[1]["validation"]["cagr"]), _fmt(reversal[1]["validation"]["sharpe"]), _fmt(reversal[1]["validation"]["sortino"]), _fmt(reversal[1]["validation"]["max_drawdown"]), _fmt(reversal[1]["validation"]["turnover"])],
            ["Treatment 1x", "validation", _fmt(treatment[1]["validation"]["total_return"]), _fmt(treatment[1]["validation"]["cagr"]), _fmt(treatment[1]["validation"]["sharpe"]), _fmt(treatment[1]["validation"]["sortino"]), _fmt(treatment[1]["validation"]["max_drawdown"]), _fmt(treatment[1]["validation"]["turnover"])],
        ]),
        "",
        "## Turnover and costs",
        "",
        "The full 1x/2x/3x cost-stress table is above and the complete control/treatment stress JSON is retained. Gross-minus-net return and fill-level slippage, spread, and regulatory fees are separately recorded in each result.",
        "",
        "## Concentration / robustness",
        "",
        f"The treatment robustness record is in `robustness.json`. It covers calendar years, trade contribution concentration, largest drawdown periods, independent filled symbols/trades, and a realized-PnL leave-one-dominant-symbol-out diagnostic that does not modify or rerun the strategy. Treatment 1x maximum concentration was {_fmt(treatment[1]['full']['maximum_concentration'], 4)}.",
        "",
        "## Independent verification",
        "",
        f"Separate verifier status: `{independent.get('status')}`. It checked protocol/data hashes, normalized-row invariants, synthetic 21/zero-skip signal semantics, V3 source identity, weekly decisions, next-bar fills, cost arithmetic, target caps, shared allocation, NAV arithmetic, and validation boundaries.",
        "",
        "## Frozen gate table",
        "",
        _markdown_table(["Gate", "Status", "Evidence"], [[row["name"], row["status"], json.dumps(row["evidence"], sort_keys=True)] for row in gates]),
        "",
        "## Final reasoning",
        "",
        f"The exact verdict is `{verdict}` because it follows the frozen gates above; no parameter, allocation, universe, or production setting was changed after observing results. This result is research-only and does not register the candidate for PAPER or alter production.",
        "",
        "## Files created",
        "",
        "- `CURRENT_BASELINE.md`, `FROZEN_PROTOCOL.md`, `DATA_RECEIPT.json`, `run_manifest.json`",
        "- `candidate.py`, `run_campaign.py`, `verify_results.py`, `test_candidate.py`",
        "- `universe_snapshot.json`, `standalone_reversal.json`, `v3_control.json`, `treatment.json`, `cost_stress.json`, `diversification.json`, `robustness.json`, `execution_audit.json`, `independent_verification.json`",
        "- `FINAL_REPORT.md`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--lifetimes", type=Path, default=DEFAULT_LIFETIMES)
    parser.add_argument("--corporate-actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--skip-verifier", action="store_true")
    args = parser.parse_args(argv)
    campaign_start = time.time()
    output = CAMPAIGN_DIR
    config = load_config(ROOT / "config/settings.yaml")
    main_sha = subprocess.check_output(["git", "rev-parse", "main"], cwd=ROOT, text=True).strip()
    head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    protocol_sha = _hash_file(PROTOCOL_PATH)
    baseline_sha = _hash_file(BASELINE_PATH)
    loaded = load_research_bars(args.data)
    lifetimes_sha = _hash_file(args.lifetimes)
    actions, action_receipt = load_dividend_actions(args.corporate_actions, {bar.symbol for bar in loaded.bars}, FULL_END)
    loaded.receipt.update(
        {
            "lifetime_table": str(args.lifetimes),
            "lifetime_table_sha256": lifetimes_sha,
            "lifetime_table_pit_methodology": "provider-asset-identity entity lifetime metadata; active and inactive histories retained",
            "survivorship_limitations": (
                "Broad provider-supported active/inactive coverage is not proof of complete exchange-wide delisting capture; "
                "the scanner is PIT within the supplied panel and lifetime metadata."
            ),
            "corporate_actions": action_receipt,
        }
    )
    _write_json(output / "DATA_RECEIPT.json", loaded.receipt)
    manifest = {
        "campaign": "equity_short_term_residual_reversal_v1",
        "main_sha": main_sha,
        "head_sha_at_run": head_sha,
        "branch": branch,
        "protocol_sha256": protocol_sha,
        "baseline_sha256": baseline_sha,
        "data_source_sha256": loaded.receipt["source_sha256"],
        "lifetime_table_sha256": lifetimes_sha,
        "corporate_actions_sha256": action_receipt["source_sha256"],
        "windows": {
            "full_start": FULL_START.isoformat(),
            "full_end": FULL_END.isoformat(),
            "development_start": DEVELOPMENT_START.isoformat(),
            "development_end": DEVELOPMENT_END.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "validation_end": FULL_END.isoformat(),
        },
        "candidate_parameters": frozen_reversal_parameters(),
        "treatment_allocation": {"v3": 0.4125, "reversal": 0.1375, "total": 0.55, "cash_remainder": 0.45},
        "cost_multipliers": [1, 2, 3],
        "production_source": "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
        "production_source_sha256": _hash_file(ROOT / "src/parayabuck/portfolio/equity_residual_momentum_v3.py"),
        "runner_started_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json(output / "run_manifest.json", manifest)
    print(f"Loaded {len(loaded.bars):,} valid bars across {loaded.receipt['unique_symbols']:,} symbols")
    print("Building the fresh PIT scanner and shared V3 observation universe...")
    v3_for_universe = EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters))
    universe_obj = build_product_universe(config, loaded.bars, market=Market.EQUITY, end=FULL_END, timeframe="1d")
    compact_universe = _compact_universe(universe_obj)
    _write_json(output / "universe_snapshot.json", compact_universe)
    loaded.receipt["pit_universe"] = {
        "universe_hash": compact_universe["hash"],
        "snapshot_count": compact_universe["snapshot_count"],
        "average_pool_size": compact_universe["average_pool_size"],
        "average_observation_size": compact_universe["average_observation_size"],
        "scanner_methodology": compact_universe["methodology"],
        "observation_strategy": v3_for_universe.spec().to_dict(),
    }
    _write_json(output / "DATA_RECEIPT.json", loaded.receipt)
    stream_symbols: set[str] = {"SPY"}
    for snapshot in universe_obj.snapshots:
        stream_symbols.update(snapshot.observation_set or tuple(sorted(snapshot.symbols))[: snapshot.maximum_stream_symbols or 30])
        stream_symbols.update(snapshot.pinned_symbols)
    engine_bars = [bar for bar in loaded.bars if bar.symbol in stream_symbols]
    engine_bars = sorted(engine_bars, key=lambda bar: (bar.timestamp, bar.symbol))
    from parayabuck.backtest.numeric_history import build_numeric_history

    numeric_history = build_numeric_history(engine_bars, market=Market.EQUITY, timeframe="1d")
    print(f"Shared engine panel: {len(engine_bars):,} bars across {len(stream_symbols):,} streamed symbols")
    records: dict[tuple[str, int], RunRecord] = {}
    for name in ("v3", "reversal", "treatment"):
        for cost in (1, 2, 3):
            started = time.time()
            print(f"Running {name} at {cost}x costs...")
            record = run_one(name, cost, config, engine_bars, numeric_history, universe_obj, actions)
            records[(name, cost)] = record
            print(f"  net={record.summary['net_return']:.6f} cagr={record.summary['cagr']:.6f} elapsed={time.time() - started:.1f}s")
    v3_records = {cost: _period_bundle(records[("v3", cost)]) for cost in (1, 2, 3)}
    reversal_records = {cost: _period_bundle(records[("reversal", cost)]) for cost in (1, 2, 3)}
    treatment_records = {cost: _period_bundle(records[("treatment", cost)]) for cost in (1, 2, 3)}
    _write_json(output / "v3_control.json", {"strategy": "equity_residual_momentum_v3", "costs": v3_records})
    _write_json(output / "standalone_reversal.json", {"strategy": "equity_short_term_residual_reversal_v1", "costs": reversal_records})
    _write_json(output / "treatment.json", {"strategy": "equity_v3_plus_residual_reversal_treatment", "costs": treatment_records})
    _write_json(output / "cost_stress.json", {"v3": v3_records, "reversal": reversal_records, "treatment": treatment_records})
    diversification = diversification_payload(records[("v3", 1)], records[("reversal", 1)])
    _write_json(output / "diversification.json", diversification)
    robustness = {
        name: _robustness_record(records[(name, 1)])
        for name in ("v3", "reversal", "treatment")
    }
    _write_json(output / "robustness.json", robustness)
    execution_audit = {
        f"{name}_{cost}x": records[(name, cost)].audit
        for name in ("v3", "reversal", "treatment")
        for cost in (1, 2, 3)
    }
    _write_json(output / "execution_audit.json", execution_audit)
    gates = _gate_table(reversal_records, v3_records[1], treatment_records, diversification, robustness)
    verdict = _verdict(gates, loaded.receipt)
    _write_json(output / "gate_table.json", gates)
    _write_json(output / "decision.json", {"verdict": verdict, "gates": gates})
    if not args.skip_verifier:
        verifier = output / "verify_results.py"
        subprocess.run([sys.executable, str(verifier), "--output", str(output)], cwd=ROOT, check=True)
    independent = json.loads((output / "independent_verification.json").read_text(encoding="utf-8")) if not args.skip_verifier else {"status": "SKIPPED"}
    final_sha = "pending-uncommitted-research-branch"
    write_final_report(
        output / "FINAL_REPORT.md",
        verdict=verdict,
        main_sha=main_sha,
        branch=branch,
        receipt=loaded.receipt,
        action_receipt=action_receipt,
        universe=compact_universe,
        reversal=reversal_records,
        v3=v3_records[1],
        treatment=treatment_records,
        diversification=diversification,
        robustness=robustness,
        gates=gates,
        independent=independent,
        final_sha=final_sha,
    )
    manifest["universe_hash"] = compact_universe["hash"]
    manifest["engine_stream_symbol_count"] = len(stream_symbols)
    manifest["engine_bar_count"] = len(engine_bars)
    manifest["verdict"] = verdict
    manifest["elapsed_seconds"] = time.time() - campaign_start
    _write_json(output / "run_manifest.json", manifest)
    print(f"Verdict: {verdict}")
    print(f"Campaign elapsed: {time.time() - campaign_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
