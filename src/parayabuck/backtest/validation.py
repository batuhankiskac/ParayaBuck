from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
from parayabuck.backtest.sweep import (
    DEFAULT_POSITIONS,
    DEFAULT_RISKS,
    DEFAULT_SCORES,
    MIN_TRADES,
    run_sweep,
)
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar


@dataclass(frozen=True)
class WalkForwardFold:
    train_bars: int
    test_bars: int
    result: BacktestResult
    train_start: datetime | None = None
    train_end: datetime | None = None
    test_start: datetime | None = None
    test_end: datetime | None = None


@dataclass(frozen=True)
class OptimizedFold:
    train_bars: int
    test_bars: int
    minimum_signal_score: float
    risk_per_trade_pct: float
    max_position_pct: float
    result: BacktestResult
    train_start: datetime | None = None
    train_end: datetime | None = None
    test_start: datetime | None = None
    test_end: datetime | None = None


@dataclass(frozen=True)
class CalendarFoldWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True)
class MonteCarloResult:
    median_ending_equity: float
    worst_5pct_ending_equity: float
    median_max_drawdown: float
    worst_5pct_max_drawdown: float


def walk_forward(
    bars: list[Bar],
    engine_factory: Callable[[], BacktestEngine],
    train_size: int,
    test_size: int,
) -> tuple[WalkForwardFold, ...]:
    """Backward-compatible alias for rolling_out_of_sample.

    No parameter optimization occurs, so this is intentionally not represented
    as trained walk-forward optimization in reports.
    """
    return rolling_out_of_sample(bars, engine_factory, train_size, test_size)


def rolling_out_of_sample(
    bars: list[Bar],
    engine_factory: Callable[[], BacktestEngine],
    train_size: int,
    test_size: int,
) -> tuple[WalkForwardFold, ...]:
    """Run timestamp-aligned rolling OOS folds; training only warms indicators."""
    if train_size <= 0 or test_size <= 0:
        raise ValueError("Walk-forward sizes must be positive")
    ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    timestamps = sorted({bar.timestamp for bar in ordered})
    folds: list[WalkForwardFold] = []
    offset = train_size
    while offset + test_size <= len(timestamps):
        warmup_times = set(timestamps[offset - train_size : offset])
        test_times = set(timestamps[offset : offset + test_size])
        warmup = [bar for bar in ordered if bar.timestamp in warmup_times]
        test = [bar for bar in ordered if bar.timestamp in test_times]
        result = engine_factory().run(test, warmup=warmup)
        folds.append(WalkForwardFold(len(warmup), len(test), result))
        offset += test_size
    return tuple(folds)


def walk_forward_optimized(
    bars: list[Bar],
    base_config: EngineConfig,
    engine_factory: Callable[[EngineConfig], BacktestEngine],
    train_size: int,
    test_size: int,
    *,
    scores: tuple[float, ...] = DEFAULT_SCORES,
    risks: tuple[float, ...] = DEFAULT_RISKS,
    positions: tuple[float, ...] = DEFAULT_POSITIONS,
    min_trades: int = MIN_TRADES,
) -> tuple[OptimizedFold, ...]:
    """Nested walk-forward: parameters are selected on each train window, then
    the selected configuration is evaluated on the following out-of-sample test
    window.

    Unlike ``run_sweep`` — which ranks combinations on the full data and is
    therefore in-sample — this splits selection (train) from measurement
    (test), so the reported OOS results are not contaminated by choosing the
    best parameters on the very data being measured.
    """
    if train_size <= 0 or test_size <= 0:
        raise ValueError("Walk-forward sizes must be positive")
    if min_trades < 1:
        raise ValueError("min_trades must be positive")
    ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    timestamps = sorted({bar.timestamp for bar in ordered})
    folds: list[OptimizedFold] = []
    offset = train_size
    while offset + test_size <= len(timestamps):
        warmup_times = set(timestamps[offset - train_size : offset])
        test_times = set(timestamps[offset : offset + test_size])
        warmup = [bar for bar in ordered if bar.timestamp in warmup_times]
        test = [bar for bar in ordered if bar.timestamp in test_times]
        points = run_sweep(
            warmup,
            base_config,
            engine_factory,
            scores=scores,
            risks=risks,
            positions=positions,
            min_trades=min_trades,
        )
        if not points:
            offset += test_size
            continue
        best = points[0]
        selected = replace(
            base_config,
            minimum_signal_score=best.minimum_signal_score,
            risk=replace(
                base_config.risk,
                risk_per_trade_pct=best.risk_per_trade_pct,
                max_position_pct=best.max_position_pct,
            ),
        )
        result = engine_factory(selected).run(test, warmup=warmup)
        folds.append(
            OptimizedFold(
                len(warmup),
                len(test),
                best.minimum_signal_score,
                best.risk_per_trade_pct,
                best.max_position_pct,
                result,
            )
        )
        offset += test_size
    return tuple(folds)


def calendar_fold_windows(
    bars: list[Bar],
    *,
    train_months: int = 9,
    test_months: int = 1,
    outer_holdout_months: int = 6,
    embargo: timedelta = timedelta(0),
) -> tuple[CalendarFoldWindow, ...]:
    """Build fixed-length rolling calendar boundaries without counting timestamps."""
    if train_months <= 0 or test_months <= 0 or outer_holdout_months < 0:
        raise ValueError("Calendar fold sizes must be valid")
    if embargo < timedelta(0):
        raise ValueError("Embargo cannot be negative")
    if not bars:
        return ()
    timestamps = [bar.timestamp for bar in bars]
    start = _month_floor(min(timestamps))
    data_end = max(timestamps)
    holdout_start = _add_months(_month_floor(data_end), -outer_holdout_months + 1)
    validation_start = _add_months(start, train_months)
    windows: list[CalendarFoldWindow] = []
    while _add_months(validation_start, test_months) <= holdout_start:
        test_end = _add_months(validation_start, test_months)
        train_start = _add_months(validation_start, -train_months)
        train_end = validation_start - embargo
        if train_end > train_start:
            windows.append(
                CalendarFoldWindow(train_start, train_end, validation_start, test_end)
            )
        validation_start = _add_months(validation_start, test_months)
    return tuple(windows)


def calendar_rolling_out_of_sample(
    bars: list[Bar],
    engine_factory: Callable[[], BacktestEngine],
    *,
    train_months: int = 9,
    test_months: int = 1,
    outer_holdout_months: int = 6,
    embargo: timedelta = timedelta(0),
) -> tuple[WalkForwardFold, ...]:
    """Evaluate monthly OOS folds using calendar dates and a purge/embargo gap."""
    ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    folds: list[WalkForwardFold] = []
    for window in calendar_fold_windows(
        ordered,
        train_months=train_months,
        test_months=test_months,
        outer_holdout_months=outer_holdout_months,
        embargo=embargo,
    ):
        warmup = [
            bar for bar in ordered if window.train_start <= bar.timestamp < window.train_end
        ]
        test = [bar for bar in ordered if window.test_start <= bar.timestamp < window.test_end]
        if not warmup or not test:
            continue
        result = engine_factory().run(test, warmup=warmup)
        folds.append(
            WalkForwardFold(
                len(warmup),
                len(test),
                result,
                window.train_start,
                window.train_end,
                window.test_start,
                window.test_end,
            )
        )
    return tuple(folds)


def calendar_walk_forward_optimized(
    bars: list[Bar],
    base_config: EngineConfig,
    engine_factory: Callable[[EngineConfig], BacktestEngine],
    *,
    train_months: int = 9,
    test_months: int = 1,
    outer_holdout_months: int = 6,
    embargo: timedelta = timedelta(0),
    scores: tuple[float, ...] = DEFAULT_SCORES,
    positions: tuple[float, ...] = DEFAULT_POSITIONS,
    alpha_risk_per_trade_pct: float = 0.0025,
    min_trades: int = MIN_TRADES,
) -> tuple[OptimizedFold, ...]:
    """Calendar-based nested selection with strategy risk fixed during alpha research."""
    if not 0 < alpha_risk_per_trade_pct <= 0.005:
        raise ValueError("Alpha research risk must be in (0, 0.5%]")
    ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    folds: list[OptimizedFold] = []
    for window in calendar_fold_windows(
        ordered,
        train_months=train_months,
        test_months=test_months,
        outer_holdout_months=outer_holdout_months,
        embargo=embargo,
    ):
        train = [
            bar for bar in ordered if window.train_start <= bar.timestamp < window.train_end
        ]
        test = [bar for bar in ordered if window.test_start <= bar.timestamp < window.test_end]
        if not train or not test:
            continue
        points = run_sweep(
            train,
            base_config,
            engine_factory,
            scores=scores,
            risks=(alpha_risk_per_trade_pct,),
            positions=positions,
            min_trades=min_trades,
        )
        if not points:
            continue
        best = points[0]
        selected = replace(
            base_config,
            minimum_signal_score=best.minimum_signal_score,
            risk=replace(
                base_config.risk,
                risk_per_trade_pct=alpha_risk_per_trade_pct,
                max_position_pct=best.max_position_pct,
            ),
        )
        result = engine_factory(selected).run(test, warmup=train)
        folds.append(
            OptimizedFold(
                len(train),
                len(test),
                best.minimum_signal_score,
                alpha_risk_per_trade_pct,
                best.max_position_pct,
                result,
                window.train_start,
                window.train_end,
                window.test_start,
                window.test_end,
            )
        )
    return tuple(folds)


def monte_carlo_trades(
    realized_pnls: tuple[float, ...],
    starting_equity: float,
    simulations: int = 2_000,
    seed: int = 7,
) -> MonteCarloResult:
    """Estimate sequence risk by bootstrapping realized trades with replacement."""
    if not realized_pnls or simulations <= 0:
        raise ValueError("Trades and a positive simulation count are required")
    rng = random.Random(seed)
    endings: list[float] = []
    drawdowns: list[float] = []
    for _ in range(simulations):
        equity = starting_equity
        peak = starting_equity
        max_drawdown = 0.0
        for _ in realized_pnls:
            equity += rng.choice(realized_pnls)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak if peak > 0 else 1.0)
        endings.append(equity)
        drawdowns.append(max_drawdown)
    endings.sort()
    drawdowns.sort()
    return MonteCarloResult(
        median_ending_equity=_percentile(endings, 0.50),
        worst_5pct_ending_equity=_percentile(endings, 0.05),
        median_max_drawdown=_percentile(drawdowns, 0.50),
        worst_5pct_max_drawdown=_percentile(drawdowns, 0.95),
    )


def _percentile(values: list[float], quantile: float) -> float:
    index = min(len(values) - 1, max(0, round((len(values) - 1) * quantile)))
    return values[index]


def _month_floor(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    return value.replace(year=year, month=month_index + 1, day=1)
