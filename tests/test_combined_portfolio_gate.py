from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from parayabuck.backtest.combined_gate import evaluate_combined_portfolio
from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult


def _result(values: tuple[float, ...], hour: int) -> PortfolioBacktestResult:
    start = datetime(2026, 1, 1, hour, tzinfo=UTC)
    return cast(
        PortfolioBacktestResult,
        SimpleNamespace(
            starting_equity=100_000.0,
            equity_curve=values,
            curve_timestamps=tuple(start + timedelta(days=index) for index in range(len(values))),
        ),
    )


def test_combined_gate_synchronizes_daily_sleeves_and_rechecks_drawdown() -> None:
    equity = _result((100_000, 98_000, 96_000), 21)
    crypto = _result((100_000, 97_000, 94_000), 0)
    decision = evaluate_combined_portfolio((equity, crypto), maximum_drawdown=0.10)
    assert decision.equity_curve == (100_000, 95_000, 90_000)
    assert decision.maximum_drawdown == 0.10
    assert decision.passed

    failed = evaluate_combined_portfolio((equity, crypto), maximum_drawdown=0.09)
    assert not failed.passed
    assert failed.failures == ("combined_max_drawdown_above_gate",)
