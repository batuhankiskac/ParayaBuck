from __future__ import annotations

import random
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
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


def calendar_fold_windows(
    bars: list[Bar],
    *,
    train_months: int = 9,
    test_months: int = 1,
    outer_holdout_months: int = 6,
    embargo: timedelta = timedelta(0),
    holdout_start: datetime | None = None,
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
    locked_start = holdout_start or _shift_calendar_months(data_end, -outer_holdout_months)
    if locked_start.tzinfo != data_end.tzinfo:
        raise ValueError("Holdout start must use the same timezone as the bars")
    validation_start = _add_months(start, train_months)
    windows: list[CalendarFoldWindow] = []
    while _add_months(validation_start, test_months) <= locked_start:
        test_end = _add_months(validation_start, test_months)
        train_start = _add_months(validation_start, -train_months)
        train_end = validation_start - embargo
        if train_end > train_start:
            windows.append(CalendarFoldWindow(train_start, train_end, validation_start, test_end))
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
        warmup = [bar for bar in ordered if window.train_start <= bar.timestamp < window.train_end]
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


def _shift_calendar_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    return value.replace(
        year=year,
        month=month,
        day=min(value.day, monthrange(year, month)[1]),
    )


__all__ = [
    "CalendarFoldWindow",
    "MonteCarloResult",
    "WalkForwardFold",
    "calendar_fold_windows",
    "calendar_rolling_out_of_sample",
    "monte_carlo_trades",
    "rolling_out_of_sample",
]
