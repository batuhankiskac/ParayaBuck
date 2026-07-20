"""Unit tests for performance metrics and benchmark comparison."""
from __future__ import annotations

from datetime import UTC, datetime

from parayabuck.analytics.metrics import calculate_metrics
from parayabuck.models import Trade


def _no_trades():
    return []


def test_metrics_zero_equity_curve():
    m = calculate_metrics([], trades=[], starting_balance=10000.0)
    assert m.total_return == 0.0
    assert m.sharpe_ratio == 0.0


def test_metrics_positive_return():
    eq = [10000.0, 10100.0, 10300.0, 10500.0]
    m = calculate_metrics(eq, trades=_no_trades(), starting_balance=10000.0, bars_per_year=252)
    assert m.total_return == 500.0
    assert m.total_return_pct == pytest_approx(0.05)
    assert m.annualized_return > 0


def pytest_approx(value, rel=1e-3):
    import pytest

    return pytest.approx(value, rel=rel)


def test_metrics_max_drawdown():
    # Peak at 11000, then drop to 9000 -> 2000 drawdown.
    eq = [10000.0, 11000.0, 9000.0, 9500.0]
    m = calculate_metrics(eq, trades=[], starting_balance=10000.0)
    assert m.max_drawdown == pytest_approx(2000.0)
    assert m.max_drawdown_pct > 0.18  # ~18%


def test_metrics_benchmark_comparison():
    """When a benchmark curve is provided, alpha/beta/excess_return are
    populated and excess_return = strat_return - benchmark_return."""
    eq = [10000.0, 10500.0, 11000.0]  # +10%
    bench = [10000.0, 10200.0, 10400.0]  # +4%
    m = calculate_metrics(
        eq, trades=[], starting_balance=10000.0,
        bars_per_year=252, benchmark_curve=bench,
    )
    assert m.benchmark_return == pytest_approx(0.04)
    assert m.excess_return == pytest_approx(0.06)
    # Beta for perfectly correlated returns should be near 1.
    assert m.beta > 0
    assert m.information_ratio != 0


def test_metrics_benchmark_short_curve_degrades_gracefully():
    """If the benchmark has only 1 point, alpha/beta stay at 0 (no crash)."""
    eq = [10000.0, 10500.0]
    bench = [10000.0]
    m = calculate_metrics(
        eq, trades=[], starting_balance=10000.0,
        benchmark_curve=bench,
    )
    # Single-point benchmark -> can't compute returns -> no alpha/beta.
    assert m.alpha == 0.0
    assert m.beta == 0.0


def test_metrics_no_benchmark_leaves_alpha_zero():
    eq = [10000.0, 10500.0]
    m = calculate_metrics(eq, trades=[], starting_balance=10000.0)
    assert m.alpha == 0.0
    assert m.benchmark_return == 0.0
    assert m.excess_return == pytest_approx(0.05)  # defaults to strat_return


def test_metrics_win_rate_from_trades():
    """Closing trades with positive pnl count as wins."""
    from parayabuck.models import Side

    ts = datetime(2025, 1, 1, tzinfo=UTC)
    trades = [
        Trade(ts, "TEST", Side.BUY, 10, 100.0, realized_pnl=50.0, closes_position=True),
        Trade(ts, "TEST", Side.SELL, 10, 101.0, realized_pnl=-30.0, closes_position=True),
        Trade(ts, "TEST", Side.BUY, 10, 100.0, realized_pnl=0.0, closes_position=False),  # entry
    ]
    eq = [10000.0, 10020.0]
    m = calculate_metrics(eq, trades=trades, starting_balance=10000.0)
    assert m.win_rate == pytest_approx(0.5)  # 1 win / 2 closing
    assert m.total_trades == 2
    assert m.avg_win == pytest_approx(50.0)
    assert m.avg_loss == pytest_approx(-30.0)
    assert m.profit_factor == pytest_approx(50.0 / 30.0)
