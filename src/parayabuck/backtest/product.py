from __future__ import annotations

import csv
import itertools
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
)
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for


@dataclass(frozen=True)
class ProductBacktestResult:
    run_id: str
    result: PortfolioBacktestResult
    report: PortfolioReport
    universe: PointInTimeUniverse
    output_dir: str
    score: float


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
            timestamp = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            market = Market(str(raw.get("market") or default_market.value).lower())
            rows.append(
                Bar(
                    timestamp=timestamp,
                    symbol=str(raw["symbol"]),
                    timeframe=str(
                        raw.get("timeframe") or ("1h" if market == Market.CRYPTO else "1d")
                    ),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    market=market,
                )
            )
    return rows


def run_product_backtest(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    strategy_name: str | None = None,
    parameters: dict[str, object] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    initial_cash: float | None = None,
    output_dir: str | None = None,
    cost_multiplier: float = 1.0,
    persist: bool = True,
) -> ProductBacktestResult:
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be positive")
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy = portfolio_strategy_from(
        strategy_name or selection.name, parameters or selection.parameters
    )
    available = [
        bar
        for bar in bars
        if bar.market == market
        and bar.timeframe == strategy.timeframe
        and (end is None or bar.timestamp <= end)
    ]
    filtered = [bar for bar in available if start is None or bar.timestamp >= start]
    if not filtered:
        raise ValueError(f"No {market.value} {strategy.timeframe} bars in the requested period")
    if market == Market.EQUITY:
        equity_universe = config.universe.equity
        pinned = equity_universe.reference_symbols
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
    universe = build_trailing_point_in_time_universe(
        available,
        market=market,
        maximum_symbols=maximum_symbols,
        rebalance_frequency=config.backtest.scanner_rebalance,
        lookback_days=lookback_days,
        minimum_history_ratio=minimum_history_ratio,
        minimum_price=minimum_price,
        minimum_dollar_volume=minimum_dollar_volume,
        pinned_symbols=pinned,
    )
    risk = _risk_limits(config)
    benchmark = "SPY" if market == Market.EQUITY else "BTC/USD"
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=initial_cash or config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        benchmark_symbol=benchmark,
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=selection.maximum_allocation,
    )
    warmup = [bar for bar in available if start is not None and bar.timestamp < start]
    result = engine.run(filtered, warmup=warmup)
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
            "cost_multiplier": cost_multiplier,
            "universe_hash": hash_point_in_time_universe(universe),
            "universe_methodology": universe.methodology,
            "survivorship_bias_limitation": (
                "Candidate metadata may reflect currently supported Alpaca assets; historical "
                "scanner membership is point-in-time within the supplied bar set, but delisted "
                "assets cannot be reconstructed when the provider omits them."
            ),
        }
    )
    run_id = uuid4().hex
    destination = Path(output_dir or config.backtest.output_dir) / run_id
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
    score = ranking_score(result)
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
) -> dict[str, object]:
    if not grid or any(not values for values in grid.values()):
        raise ValueError("Optimization grid must contain at least one value per parameter")
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    baseline = dict(base_parameters or selection.parameters)
    keys = tuple(sorted(grid))
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
    payload: dict[str, object] = {"training_results": ranked, "ranking": "risk_adjusted"}
    if oos_start is not None and oos_end is not None:
        best = ranked[0]
        best_parameters = best["parameters"]
        if not isinstance(best_parameters, dict):
            raise TypeError("Optimization parameters must be a mapping")
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    payload["output"] = str(path)
    return payload


def _optimization_sort_key(item: dict[str, object]) -> tuple[float, str]:
    score = item.get("score")
    numeric_score = float(score) if isinstance(score, (int, float)) else float("-inf")
    parameters = item.get("parameters")
    return -numeric_score, json.dumps(parameters, sort_keys=True)


def ranking_score(result: PortfolioBacktestResult) -> float:
    trade_penalty = min(0.0, (len(result.closed_position_events) - 5) * 0.10)
    return (
        result.sharpe
        + 0.50 * result.sortino
        + 0.25 * result.calmar
        - 2.0 * result.max_drawdown
        + trade_penalty
    )


def _risk_limits(config: Config) -> PortfolioRiskLimits:
    risk = config.risk
    return PortfolioRiskLimits(
        daily_loss_limit=risk.daily_loss_limit_pct,
        drawdown_halt=risk.max_drawdown_pct,
        maximum_gross=risk.max_gross_exposure_pct,
        maximum_equity=risk.max_gross_exposure_pct,
        maximum_crypto=risk.max_crypto_exposure_pct,
        maximum_equity_asset=risk.max_position_pct,
        maximum_crypto_asset=risk.max_crypto_position_pct,
        minimum_cash=risk.cash_buffer_pct,
        maximum_positions=risk.max_positions,
        maximum_crypto_positions=risk.max_crypto_positions,
    )


def _persist_run(
    database_path: str,
    run_id: str,
    strategy_name: str,
    summary: dict[str, object],
    score: float,
) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
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
    "load_csv_bars",
    "optimize_grid",
    "ranking_score",
    "run_product_backtest",
]
