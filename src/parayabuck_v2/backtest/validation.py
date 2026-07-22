from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from parayabuck_v2.backtest.engine import BacktestEngine, BacktestResult
from parayabuck_v2.core.models import Bar


@dataclass(frozen=True)
class WalkForwardFold:
    train_bars: int
    test_bars: int
    result: BacktestResult


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
    """Run strictly out-of-sample rolling folds; training bars only warm indicators."""
    if train_size <= 0 or test_size <= 0:
        raise ValueError("Walk-forward sizes must be positive")
    folds: list[WalkForwardFold] = []
    offset = train_size
    while offset + test_size <= len(bars):
        warmup = bars[offset - train_size : offset]
        test = bars[offset : offset + test_size]
        result = engine_factory().run(test, warmup=warmup)
        folds.append(WalkForwardFold(len(warmup), len(test), result))
        offset += test_size
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
