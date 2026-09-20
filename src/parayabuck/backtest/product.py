from __future__ import annotations

import contextlib
import csv
import itertools
import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    TerminalLifecycle,
)
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
)
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES, is_regular_equity_time
from parayabuck.portfolio import PortfolioStrategy, portfolio_strategy_from, rebalance_policy_for

try:
    from parayabuck.backtest.numeric_history import NumericHistoryView, build_numeric_history
except ImportError:  # pragma: no cover
    NumericHistoryView = object  # type: ignore
    build_numeric_history = None  # type: ignore

_MAX_OPTIMIZATION_COMBINATIONS = 256
_CSV_TIMEFRAME_ALIASES = {"1d": "1d", "1Day": "1d"}
_LEGACY_ACTION_COLUMNS = {"timestamp", "symbol", "action_type", "value"}
_CANONICAL_ACTION_COLUMNS = {
    "action_type",
    "symbol",
    "ex_date",
    "record_date",
    "payable_date",
    "rate",
    "old_rate",
    "new_rate",
    "source_symbol",
    "target_symbol",
    "raw_id",
}
_CANONICAL_NON_CASH_ACTION_TYPES = {"stock_dividends", "forward_splits", "reverse_splits"}


@dataclass(frozen=True)
class ProductBacktestResult:
    run_id: str
    result: PortfolioBacktestResult
    report: PortfolioReport
    universe: PointInTimeUniverse
    output_dir: str
    score: float


def _canonical_csv_timeframe(raw_timeframe: object) -> str:
    timeframe = str(raw_timeframe)
    if timeframe in _CSV_TIMEFRAME_ALIASES:
        return _CSV_TIMEFRAME_ALIASES[timeframe]
    if timeframe in TIMEFRAME_MINUTES:
        return timeframe
    raise ValueError(f"Unsupported CSV timeframe: {timeframe}")


def load_csv_bars(path: str | Path, *, default_market: Market) -> list[Bar]:
    source = Path(path)
    rows: list[Bar] = []
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "open", "high", "low", "close"}
        if not required.issubset(reader.fieldnames or ()):
            missing = sorted(required - set(reader.fieldnames or ()))
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for raw in reader:
            timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            market = Market(str(raw.get("market") or default_market.value).lower())
            timeframe = _canonical_csv_timeframe(raw.get("timeframe") or ("1h" if market == Market.CRYPTO else "1d"))
            if market == Market.EQUITY:
                if timestamp.weekday() >= 5:
                    raise ValueError(f"Equity CSV contains a weekend bar: {timestamp.isoformat()}")
                if timeframe != "1d" and not is_regular_equity_time(timestamp):
                    raise ValueError(f"Equity CSV contains an out-of-session bar: {timestamp.isoformat()}")
            rows.append(
                Bar(
                    timestamp=timestamp,
                    symbol=str(raw["symbol"]),
                    timeframe=timeframe,
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    market=market,
                )
            )
    return rows


def _action_value(raw_value: object, *, row_number: int) -> Decimal:
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid value") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Corporate-action row {row_number} value must be finite and positive")
    return value


def _legacy_action_timestamp(raw_timestamp: object, *, row_number: int) -> datetime:
    try:
        timestamp = datetime.fromisoformat(str(raw_timestamp))
    except ValueError as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid timestamp") from exc
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def _canonical_action_timestamp(raw_ex_date: object, *, row_number: int) -> datetime:
    value = str(raw_ex_date)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid ex_date") from exc
    return parsed.replace(tzinfo=UTC)


def load_csv_corporate_actions(path: str | Path) -> list[CorporateAction]:
    """Load explicit dividend cash flows for split-adjusted equity price data."""
    actions: list[CorporateAction] = []
    seen: set[tuple[str, datetime, CorporateActionType, Decimal]] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        if _LEGACY_ACTION_COLUMNS.issubset(fieldnames):
            schema = "legacy"
        elif _CANONICAL_ACTION_COLUMNS.issubset(fieldnames):
            schema = "canonical_v5"
        elif {"ex_date", "rate"} & fieldnames:
            missing = sorted(_CANONICAL_ACTION_COLUMNS - fieldnames)
            raise ValueError(f"Canonical corporate-action CSV is missing required columns: {', '.join(missing)}")
        else:
            missing = sorted(_LEGACY_ACTION_COLUMNS - fieldnames)
            raise ValueError(f"Corporate-action CSV is missing required columns: {', '.join(missing)}")

        for row_number, raw in enumerate(reader, start=2):
            raw_action_type = str(raw["action_type"]).lower()
            if schema == "canonical_v5" and raw_action_type in _CANONICAL_NON_CASH_ACTION_TYPES:
                continue
            expected_action_type = "dividend" if schema == "legacy" else "cash_dividends"
            if raw_action_type != expected_action_type:
                raise ValueError(f"Unsupported corporate-action type on row {row_number}: {raw_action_type}")
            timestamp = (
                _legacy_action_timestamp(raw["timestamp"], row_number=row_number)
                if schema == "legacy"
                else _canonical_action_timestamp(raw["ex_date"], row_number=row_number)
            )
            value = _action_value(raw["value"] if schema == "legacy" else raw["rate"], row_number=row_number)
            raw_symbol = str(raw["symbol"])
            symbol = canonical_symbol(raw_symbol, Market.EQUITY)
            action_type = CorporateActionType.DIVIDEND
            key = (symbol, timestamp, action_type, value)
            if key in seen:
                continue
            seen.add(key)
            actions.append(
                CorporateAction(
                    timestamp,
                    raw_symbol if schema == "legacy" else symbol,
                    action_type,
                    float(value),
                )
            )
    return actions


def build_product_universe(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    end: datetime | None = None,
    timeframe: str | None = None,
    observation_strategy: PortfolioStrategy | None = None,
) -> PointInTimeUniverse:
    """Build the point-in-time scanner universe for a product backtest.

    Eligible-pool membership depends on the bar set and scanner policy. Its
    streamed observation membership is ranked by the configured product
    strategy, matching the product runtime. Optimizer trials deliberately
    reuse that configured observation basis rather than letting each parameter
    arm redefine the streamed universe.

    ``timeframe`` defaults to the configured strategy's timeframe and must be
    passed when a run overrides the strategy. ``observation_strategy`` may
    supply that already-resolved requested strategy; it changes only the
    observation-set ranker. Scanner eligibility, pins, capacity, costs, risk
    and execution remain owned by the configured product policy.
    """
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy = portfolio_strategy_from(
        selection.name,
        selection.parameters,
        # The configured strategy owns the observation-ranker timeframe. The
        # explicit ``timeframe`` argument filters the bars for a caller's
        # prepared run; passing it into the factory would make a synthetic
        # runtime override fail before the test/runtime can supply its own
        # validated strategy identity.
        timeframe=selection.timeframe,
    )
    selected_timeframe = timeframe or strategy.timeframe
    if observation_strategy is not None:
        if observation_strategy.market != market:
            raise ValueError(f"observation_strategy market must match the requested market ({market.value})")
        if observation_strategy.timeframe != selected_timeframe:
            raise ValueError(
                f"observation_strategy timeframe must match the selected backtest timeframe ({selected_timeframe})"
            )
    else:
        observation_strategy = strategy
    available = [
        bar
        for bar in bars
        if bar.market == market and bar.timeframe == selected_timeframe and (end is None or bar.timestamp <= end)
    ]
    if market == Market.EQUITY:
        equity_universe = config.universe.equity
        pinned = (*equity_universe.reference_symbols, *equity_universe.inverse_trade_symbols)
        maximum_symbols = equity_universe.maximum_stream_symbols
        lookback_days = 60
        minimum_price = equity_universe.scanner.minimum_price
        minimum_history_ratio = equity_universe.scanner.minimum_data_completeness
        minimum_dollar_volume = equity_universe.scanner.minimum_median_dollar_volume
    else:
        crypto_universe = config.universe.crypto
        pinned = crypto_universe.pinned_symbols
        maximum_symbols = crypto_universe.maximum_stream_symbols
        lookback_days = crypto_universe.scanner.lookback_days
        minimum_price = 0.0
        minimum_history_ratio = crypto_universe.scanner.minimum_data_completeness
        minimum_dollar_volume = crypto_universe.scanner.minimum_median_dollar_volume
    return build_trailing_point_in_time_universe(
        available,
        market=market,
        maximum_symbols=maximum_symbols,
        rebalance_frequency=config.backtest.scanner_rebalance,
        lookback_days=lookback_days,
        minimum_history_ratio=minimum_history_ratio,
        minimum_price=minimum_price,
        minimum_dollar_volume=minimum_dollar_volume,
        pinned_symbols=pinned,
        target_shortlist=(equity_universe.target_shortlist if market == Market.EQUITY else None),
        equity_config=(config.universe.equity if market == Market.EQUITY else None),
        crypto_config=(config.universe.crypto if market == Market.CRYPTO else None),
        observation_strategy=observation_strategy,
    )


def run_product_backtest(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    strategy_name: str | None = None,
    parameters: dict[str, object] | None = None,
    strategy_timeframe: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    initial_cash: float | None = None,
    output_dir: str | None = None,
    cost_multiplier: float = 1.0,
    persist: bool = True,
    persist_artifacts: bool = True,
    corporate_actions: list[CorporateAction] | None = None,
    universe: PointInTimeUniverse | None = None,
    data_feed: str | None = None,
    prepared_inputs: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]] | None = None,
    numeric_history: object | None = None,
    terminal_lifecycles: Mapping[str, TerminalLifecycle] | None = None,
) -> ProductBacktestResult:
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be positive")
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    # Explicit None-check: {} means "use requested strategy defaults", None means "use configured selection"
    # This keeps an explicitly requested strategy's defaults from inheriting
    # the configured selection parameters.
    strategy = portfolio_strategy_from(
        strategy_name or selection.name,
        parameters if parameters is not None else selection.parameters,
        timeframe=strategy_timeframe or selection.timeframe,
    )
    if prepared_inputs is None:
        available = [
            bar
            for bar in bars
            if bar.market == market and bar.timeframe == strategy.timeframe and (end is None or bar.timestamp <= end)
        ]
        filtered = [bar for bar in available if start is None or bar.timestamp >= start]
        if not filtered:
            raise ValueError(f"No {market.value} {strategy.timeframe} bars in the requested period")
        warmup = [bar for bar in available if start is not None and bar.timestamp < start]
        if universe is None:
            universe = build_product_universe(
                config,
                bars,
                market=market,
                end=end,
                timeframe=strategy.timeframe,
                observation_strategy=strategy,
            )
        prepared = None
        # Build numeric view once for this trial (will be reused if caller builds prepared)
        if numeric_history is None and build_numeric_history is not None:
            try:
                # Use deduplicated sorted available for view (PIT safe)
                deduped = PortfolioBacktestEngine._deduplicate(available)
                numeric_history = build_numeric_history(deduped, market=market, timeframe=strategy.timeframe)
            except Exception:
                numeric_history = None
    else:
        filtered, warmup, (ordered, decision_bars) = prepared_inputs
        if not filtered:
            raise ValueError(f"No {market.value} {strategy.timeframe} bars in the requested period")
        if universe is None:
            raise ValueError("prepared_inputs requires an explicit universe")
        prepared = (ordered, decision_bars)
        if numeric_history is None and build_numeric_history is not None:
            try:
                # Reuse warmup+ordered which are already deduped/sorted
                combined = (warmup or []) + ordered
                # Filter to market/timeframe (already) and deduplicate combined if needed
                # combined is already canonicalized/deduped per _prepared_window, so direct build
                numeric_history = build_numeric_history(combined, market=market, timeframe=strategy.timeframe)
            except Exception:
                numeric_history = None
    risk = portfolio_risk_limits_from_config(config.risk)
    benchmark = "SPY" if market == Market.EQUITY else "BTC/USD"
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=initial_cash or config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=cost_multiplier,
        benchmark_symbol=benchmark,
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=selection.maximum_allocation,
        ordinary_alpha_excluded_symbols=(
            config.universe.equity.ordinary_alpha_excluded_symbols if market == Market.EQUITY else ()
        ),
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles=terminal_lifecycles,
    )
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=corporate_actions,
        prepared_inputs=prepared,
        numeric_history=numeric_history,
    )
    report = build_portfolio_report(
        result,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
    )
    report.summary.update(
        {
            "market": market.value,
            "strategy_parameters": dict(strategy.parameter_tuple()),
            "fill_model": "decision on closed bar; next tradable bar open",
            "price_adjustment_model": (
                "split-adjusted execution/signal prices with explicit dividend cash flows; "
                "callers must not supply split actions for already split-adjusted bars"
                if market == Market.EQUITY
                else "raw crypto prices; no corporate actions"
            ),
            "cost_multiplier": cost_multiplier,
            "universe_hash": hash_point_in_time_universe(universe),
            "universe_methodology": universe.methodology,
            "sector_metadata_coverage": universe.to_dict()["sector_metadata_coverage"],
            "scanner_versions": sorted({snapshot.scanner_version for snapshot in universe.snapshots}),
            "data_feed": (data_feed or "offline csv (feed unknown; downloader used the configured historical feed)"),
            "data_feed_note": (
                "IEX historical data reflects one exchange's prints, not the "
                "consolidated SIP/NBBO tape; liquidity, spread and volume "
                "proxies derived from IEX understate full-market conditions."
                if (data_feed or "").lower() == "iex"
                else "SIP consolidates the national market system tape."
            ),
            "survivorship_bias_limitation": (
                "Candidate metadata may reflect currently supported Alpaca assets; historical "
                "scanner membership is point-in-time within the supplied bar set, but delisted "
                "assets cannot be reconstructed when the provider omits them."
            ),
            "regulatory_fee_model": {
                "equity_taf_per_share": config.backtest.equity_taf_per_share,
                "equity_taf_cap": config.backtest.equity_taf_cap,
                "equity_cat_per_share": config.backtest.equity_cat_per_share,
                "equity_sec_fee_rate": config.backtest.equity_sec_fee_rate,
                "equity_use_effective_dated_fees": config.backtest.equity_use_effective_dated_fees,
                "cost_multiplier": cost_multiplier,
                "note": (
                    "SEC Section 31, FINRA TAF and CAT fees are approximated per "
                    "trade date from the official rate schedules when "
                    "equity_use_effective_dated_fees is enabled; otherwise the "
                    "configured fixed current regulatory rates are used. Either "
                    "way this is a fixed-rate approximation, not exact "
                    "historical fees — verify the rates in force at the trade "
                    "date before using results as a cost proxy."
                ),
            },
            "drawdown_halt_shadow_events": {
                "count": len(result.drawdown_halt_shadow_events),
                "first": (
                    result.drawdown_halt_shadow_events[0][0].isoformat() if result.drawdown_halt_shadow_events else None
                ),
                "last": (
                    result.drawdown_halt_shadow_events[-1][0].isoformat()
                    if result.drawdown_halt_shadow_events
                    else None
                ),
                "note": (
                    "Offline backtest: entries were NOT blocked at these "
                    "points (the curve kept trading). Under the production "
                    "drawdown policy each event is a halt until an operator "
                    "starts a new risk epoch."
                ),
            },
        }
    )
    run_id = uuid4().hex
    destination = Path(output_dir or config.backtest.output_dir) / run_id
    if persist_artifacts:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "report.json").write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
        report.write_csvs(str(destination))
        (destination / "universe.json").write_text(
            json.dumps(universe.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    score = ranking_score(result, config.backtest.ranking_metric)
    if persist:
        _persist_run(config.runtime.decision_db_path, run_id, strategy.name, report.summary, score)
    return ProductBacktestResult(run_id, result, report, universe, str(destination), score)


def optimize_grid(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    grid: dict[str, list[object]],
    strategy_name: str | None = None,
    base_parameters: dict[str, object] | None = None,
    initial_cash: float | None = None,
    train_start: datetime | None = None,
    train_end: datetime | None = None,
    oos_start: datetime | None = None,
    oos_end: datetime | None = None,
    output_dir: str | None = None,
    corporate_actions: list[CorporateAction] | None = None,
) -> dict[str, object]:
    if not grid or any(not values for values in grid.values()):
        raise ValueError("Optimization grid must contain at least one value per parameter")
    _validate_optimization_windows(train_start, train_end, oos_start, oos_end)
    combination_count = math.prod(len(values) for values in grid.values())
    if combination_count > _MAX_OPTIMIZATION_COMBINATIONS:
        raise ValueError(
            f"Optimization grid has {combination_count} combinations; maximum is {_MAX_OPTIMIZATION_COMBINATIONS}"
        )
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    baseline = dict(base_parameters or selection.parameters)
    keys = tuple(sorted(grid))
    observation_strategy = portfolio_strategy_from(
        strategy_name or selection.name,
        parameters=baseline,
        timeframe=selection.timeframe,
    )
    strategy_timeframe = observation_strategy.timeframe
    # The eligible pool and requested baseline observation basis are
    # trial-invariant: build them once per window and reuse them so candidate
    # parameters cannot silently redefine streamed membership between trials.
    train_universe = build_product_universe(
        config,
        bars,
        market=market,
        end=train_end,
        timeframe=strategy_timeframe,
        observation_strategy=observation_strategy,
    )
    # The per-trial bar preprocessing (window filter, dedup, sort) is likewise
    # trial-invariant: prepare it once per window and reuse it.
    train_prepared = _prepared_window(bars, market, strategy_timeframe, train_start, train_end)
    ranked: list[dict[str, object]] = []
    for values in itertools.product(*(grid[key] for key in keys)):
        parameters = {**baseline, **dict(zip(keys, values, strict=True))}
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=parameters,
            start=train_start,
            end=train_end,
            initial_cash=initial_cash,
            output_dir=output_dir,
            persist=False,
            persist_artifacts=False,
            corporate_actions=corporate_actions,
            universe=train_universe,
            prepared_inputs=train_prepared,
        )
        result = run.result
        ranked.append(
            {
                "parameters": parameters,
                "score": run.score,
                "return": result.total_return,
                "cagr": result.cagr,
                "sharpe": result.sharpe,
                "sortino": result.sortino,
                "max_drawdown": result.max_drawdown,
                "profit_factor": run.report.summary["profit_factor"],
                "trade_count": run.report.summary["number_of_trades"],
                "run_id": run.run_id,
            }
        )
    ranked.sort(key=_optimization_sort_key)
    payload: dict[str, object] = {
        "training_results": ranked,
        "ranking": config.backtest.ranking_metric,
        "combination_count": combination_count,
    }
    if oos_start is not None and oos_end is not None:
        best = ranked[0]
        best_parameters = best["parameters"]
        if not isinstance(best_parameters, dict):
            raise TypeError("Optimization parameters must be a mapping")
        oos_universe = build_product_universe(
            config,
            bars,
            market=market,
            end=oos_end,
            timeframe=strategy_timeframe,
            observation_strategy=observation_strategy,
        )
        oos_prepared = _prepared_window(bars, market, strategy_timeframe, oos_start, oos_end)
        oos = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=dict(best_parameters),
            start=oos_start,
            end=oos_end,
            initial_cash=initial_cash,
            output_dir=output_dir,
            corporate_actions=corporate_actions,
            universe=oos_universe,
            prepared_inputs=oos_prepared,
        )
        payload["out_of_sample"] = {
            "parameters": best["parameters"],
            "run_id": oos.run_id,
            "summary": oos.report.summary,
            "output_dir": oos.output_dir,
        }
    destination = Path(output_dir or config.backtest.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "optimization.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    payload["output"] = str(path)
    return payload


def _prepared_window(
    bars: list[Bar],
    market: Market,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]]:
    """Precompute the per-window engine inputs for a backtest trial.

    Mirrors ``run_product_backtest``'s window filter plus the engine's
    deterministic dedup/sort, so every trial of an optimization grid can
    reuse one preparation. Returns ``(filtered, warmup, (ordered,
    decision_bars))`` exactly as ``engine.run(prepared_inputs=...)`` expects.
    """
    available = [
        bar
        for bar in bars
        if bar.market == market and bar.timeframe == timeframe and (end is None or bar.timestamp <= end)
    ]
    filtered = [bar for bar in available if start is None or bar.timestamp >= start]
    warmup = [bar for bar in available if start is not None and bar.timestamp < start]
    if warmup:
        from parayabuck.markets.symbols import canonical_symbol

        canon_warmup: list[Bar] = []
        for bar in warmup:
            can = canonical_symbol(bar.symbol, bar.market)
            if can != bar.symbol:
                bar = Bar(
                    timestamp=bar.timestamp,
                    symbol=can,
                    timeframe=bar.timeframe,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    market=bar.market,
                )
            canon_warmup.append(bar)
        warmup = sorted(canon_warmup, key=lambda b: (b.timestamp, b.symbol))
    ordered = decision_bars = sorted(
        PortfolioBacktestEngine._deduplicate(filtered),
        key=lambda bar: (bar.timestamp, bar.symbol),
    )
    return filtered, warmup, (ordered, decision_bars)


def _optimization_sort_key(item: dict[str, object]) -> tuple[float, str]:
    score = item.get("score")
    numeric_score = float(score) if isinstance(score, (int, float)) else float("-inf")
    parameters = item.get("parameters")
    return -numeric_score, json.dumps(parameters, sort_keys=True)


def ranking_score(result: PortfolioBacktestResult, metric: str = "risk_adjusted") -> float:
    if metric == "sharpe":
        return result.sharpe
    if metric == "sortino":
        return result.sortino
    if metric == "calmar":
        return result.calmar
    if metric != "risk_adjusted":
        raise ValueError(f"Unsupported ranking metric: {metric}")
    trade_penalty = min(0.0, (len(result.closed_position_events) - 5) * 0.10)
    return result.sharpe + 0.50 * result.sortino + 0.25 * result.calmar - 2.0 * result.max_drawdown + trade_penalty


def _validate_optimization_windows(
    train_start: datetime | None,
    train_end: datetime | None,
    oos_start: datetime | None,
    oos_end: datetime | None,
) -> None:
    if (train_start is None) != (train_end is None):
        raise ValueError("TRAIN requires both start and end")
    if (oos_start is None) != (oos_end is None):
        raise ValueError("OOS requires both start and end")
    if oos_start is not None and (train_start is None or train_end is None):
        raise ValueError("OOS evaluation requires an explicit TRAIN window")
    if train_start is not None and train_end is not None and train_start >= train_end:
        raise ValueError("TRAIN start must be before TRAIN end")
    if oos_start is not None and oos_end is not None and oos_start >= oos_end:
        raise ValueError("OOS start must be before OOS end")
    if train_end is not None and oos_start is not None and train_end >= oos_start:
        raise ValueError("TRAIN and OOS windows must not overlap")


def _persist_run(
    database_path: str,
    run_id: str,
    strategy_name: str,
    summary: dict[str, object],
    score: float,
) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``with sqlite3.connect`` only manages transactions; closing is manual.
    with contextlib.closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, strategy_name TEXT NOT NULL,
                score REAL NOT NULL, summary_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                strategy_name,
                score,
                json.dumps(summary, sort_keys=True, default=str),
            ),
        )


__all__ = [
    "ProductBacktestResult",
    "build_product_universe",
    "load_csv_bars",
    "optimize_grid",
    "ranking_score",
    "run_product_backtest",
]
