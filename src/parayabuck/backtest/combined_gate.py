"""Synchronized account-level gate for separately researched sleeves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult


@dataclass(frozen=True)
class CombinedPortfolioGateResult:
    passed: bool
    maximum_drawdown: float
    curve_dates: tuple[date, ...]
    equity_curve: tuple[float, ...]
    failures: tuple[str, ...]


def evaluate_combined_portfolio(
    sleeves: tuple[PortfolioBacktestResult, ...],
    *,
    maximum_drawdown: float = 0.10,
) -> CombinedPortfolioGateResult:
    if not sleeves:
        raise ValueError("Combined gate requires at least one sleeve")
    starting = sleeves[0].starting_equity
    if any(abs(result.starting_equity - starting) > 0.01 for result in sleeves):
        raise ValueError("Sleeves must use the same account starting equity")
    by_sleeve: list[dict[date, float]] = []
    calendar: set[date] = set()
    for result in sleeves:
        daily: dict[date, float] = {}
        for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
            daily[timestamp.date()] = equity
        if not daily:
            raise ValueError("Combined gate refuses an empty sleeve curve")
        by_sleeve.append(daily)
        calendar.update(daily)
    dates = tuple(sorted(calendar))
    last = [starting for _ in sleeves]
    combined: list[float] = []
    for day in dates:
        for index, daily in enumerate(by_sleeve):
            if day in daily:
                last[index] = daily[day]
        combined.append(starting + sum(value - starting for value in last))
    peak = combined[0]
    drawdown = 0.0
    for value in combined:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak > 0 else 1.0)
    failures = ("combined_max_drawdown_above_gate",) if drawdown > maximum_drawdown else ()
    return CombinedPortfolioGateResult(
        passed=not failures,
        maximum_drawdown=drawdown,
        curve_dates=dates,
        equity_curve=tuple(combined),
        failures=failures,
    )


__all__ = ["CombinedPortfolioGateResult", "evaluate_combined_portfolio"]
