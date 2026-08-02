"""Grid parameter sweep for the backtest engine.

Runs the same cached bars through every parameter combination and ranks the
results so the best strategy parameters can be picked without leaving the bot
running for days. Only the engine config (signal score, risk sizing, position
caps) changes between runs; data and market conditions stay identical.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar

DEFAULT_SCORES: tuple[float, ...] = (55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0)
DEFAULT_RISKS: tuple[float, ...] = (0.0025, 0.005, 0.0075, 0.01)
DEFAULT_POSITIONS: tuple[float, ...] = (0.05, 0.10, 0.15)
MIN_TRADES = 10


@dataclass(frozen=True)
class SweepPoint:
    minimum_signal_score: float
    risk_per_trade_pct: float
    max_position_pct: float
    result: BacktestResult

    @property
    def calmar(self) -> float:
        drawdown = self.result.max_drawdown
        if drawdown > 0:
            return self.result.cagr / drawdown
        return self.result.cagr if self.result.cagr > 0 else 0.0

    def to_dict(self) -> dict[str, object]:
        result = self.result
        return {
            "minimum_signal_score": self.minimum_signal_score,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_position_pct": self.max_position_pct,
            "total_return": result.total_return,
            "cagr": result.cagr,
            "benchmark_return": result.benchmark_return,
            "excess_return": result.excess_return,
            "max_drawdown": result.max_drawdown,
            "calmar": self.calmar,
            "sharpe": result.sharpe,
            "sortino": result.sortino,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "expectancy": result.expectancy,
        }


def run_sweep(
    bars: list[Bar],
    base_config: EngineConfig,
    engine_factory: Callable[[EngineConfig], BacktestEngine],
    *,
    scores: tuple[float, ...] = DEFAULT_SCORES,
    risks: tuple[float, ...] = DEFAULT_RISKS,
    positions: tuple[float, ...] = DEFAULT_POSITIONS,
    min_trades: int = MIN_TRADES,
) -> list[SweepPoint]:
    """Run every score x risk x position combination and rank by Calmar.

    Combinations whose realized trade count falls below ``min_trades`` are
    excluded; a strategy with two lucky trades tells nothing.

    Note: this sweep evaluates every combination on the *same* bars it ranks
    on, so it is an in-sample selector. Use
    ``parayabuck.backtest.validation.walk_forward_optimized`` to select on
    training windows and measure on out-of-sample windows.
    """
    if min_trades < 1:
        raise ValueError("min_trades must be positive")
    points: list[SweepPoint] = []
    for score in scores:
        for risk in risks:
            for position in positions:
                config = replace(
                    base_config,
                    minimum_signal_score=score,
                    risk=replace(
                        base_config.risk,
                        risk_per_trade_pct=risk,
                        max_position_pct=position,
                    ),
                )
                result = engine_factory(config).run(bars)
                if result.total_trades >= min_trades:
                    points.append(SweepPoint(score, risk, position, result))
    return sorted(
        points,
        key=lambda point: (
            point.calmar,
            point.result.total_trades,
            -point.result.max_drawdown,
        ),
        reverse=True,
    )


def write_sweep_csv(points: list[SweepPoint], output_dir: str) -> str:
    """Write the ranked sweep results to ``sweep.csv`` and return its path."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sweep.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(points[0].to_dict()))
        writer.writeheader()
        writer.writerows(point.to_dict() for point in points)
    return str(path)


def render_sweep_text(points: list[SweepPoint], top_n: int = 5) -> str:
    """Compact terminal table of the best combinations."""
    if not points:
        return "Sweep sonuçlandı: yeterli işlem sayısına ulaşan kombinasyon yok."
    header = (
        f"  {'Skor':<6}{'Risk':<8}{'Pozisyon':<10}{'Getiri':>9}{'CAGR':>8}"
        f"{'Sharpe':>8}{'MaxDD':>8}{'Calmar':>8}{'İşlem':>6}{'Win':>7}"
    )
    rows = [header]
    for point in points[:top_n]:
        result = point.result
        rows.append(
            f"  {point.minimum_signal_score:<6.0f}{point.risk_per_trade_pct:<8.4f}"
            f"{point.max_position_pct:<10.2f}{result.total_return:>8.1%}"
            f"{result.cagr:>7.1%}{result.sharpe:>8.2f}{result.max_drawdown:>7.1%}"
            f"{point.calmar:>8.2f}{result.total_trades:>6}{result.win_rate:>6.1%}"
        )
    if len(points) > top_n:
        rows.append(f"  ... ve {len(points) - top_n} kombinasyon daha (sweep.csv)")
    return "\n".join(rows)


__all__ = [
    "DEFAULT_POSITIONS",
    "DEFAULT_RISKS",
    "DEFAULT_SCORES",
    "MIN_TRADES",
    "SweepPoint",
    "render_sweep_text",
    "run_sweep",
    "write_sweep_csv",
]
