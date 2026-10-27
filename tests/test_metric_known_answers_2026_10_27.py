"""Known-answer checks for the backtest metrics.

The engine's metrics are computed in ``PortfolioBacktestEngine._result`` (plus
``_monthly_returns`` and ``_worst_year``) from a daily equity curve. Each test
builds a tiny hand-made curve, so the expected values come from either literal
arithmetic in the test or the explicit formulas in ``_reference_metrics``.
The annualiser is the engine's own convention: periods per year equal the
number of returns divided by the calendar span in years (days / 365).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.stats import sample_stddev
from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio

EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


class _CashStrategy(PortfolioStrategy):
    """Never invests: the metric functions only need a constructed engine."""

    name = "cash_only"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context):  # type: ignore[no-untyped-def]
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
            universe_version="test",
        )


def _at(days: float) -> datetime:
    return EPOCH + timedelta(days=days)


def _metrics(curve: list[float], stamps: list[datetime], starting: float = 100.0):  # type: ignore[no-untyped-def]
    engine = PortfolioBacktestEngine(
        _CashStrategy(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=starting,
    )
    return engine._result(curve, stamps, 0.0, ())


def _reference_metrics(curve: list[float], stamps: list[datetime], starting: float) -> dict[str, float]:
    """The metric formulas written out, independent of the engine code."""
    returns = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    count = len(returns)
    years = (stamps[-1] - stamps[0]).days / 365.0
    periods_per_year = count / years
    annualizer = math.sqrt(periods_per_year)
    mean = sum(returns) / count
    std = math.sqrt(sum((value - mean) ** 2 for value in returns) / (count - 1))
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / count)
    cagr = (curve[-1] / starting) ** (1 / years) - 1
    peak = starting
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak)
    return {
        "total_return": curve[-1] / starting - 1,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "annualized_volatility": std * annualizer,
        "sharpe": mean / std * annualizer,
        "sortino": mean / downside * annualizer,
        "calmar": cagr / max_drawdown,
    }


def _assert_matches(result, expected: dict[str, float]) -> None:  # type: ignore[no-untyped-def]
    for name, value in expected.items():
        assert getattr(result, name) == pytest.approx(value, rel=1e-9, abs=1e-12), name


def test_sample_stddev_is_the_n_minus_one_estimator() -> None:
    # Mean 5, squared deviations sum to 32: sample variance 32 / 7. The
    # population estimator would give exactly 2.0.
    assert sample_stddev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(math.sqrt(32 / 7))
    assert sample_stddev([3.0]) == 0.0
    assert sample_stddev([]) == 0.0


def test_hand_curve_with_a_drawdown_has_literal_and_formula_metrics() -> None:
    # Peak 110 on day 1, trough 99 (-10 %), still underwater on day 4 (108.9),
    # new high 111 on day 5.
    curve = [100.0, 110.0, 99.0, 99.0, 108.9, 111.0]
    stamps = [_at(day) for day in range(len(curve))]
    result = _metrics(curve, stamps)
    assert result.total_return == pytest.approx(0.11)
    assert result.max_drawdown == pytest.approx(0.10)
    # The underwater span runs from the day-1 peak to day 4: three days.
    assert result.max_drawdown_duration_days == 3
    assert result.worst_month_return == pytest.approx(0.11)
    assert result.positive_month_ratio == 1.0
    _assert_matches(result, _reference_metrics(curve, stamps, 100.0))


def test_five_daily_returns_match_the_sharpe_sortino_and_volatility_formulas() -> None:
    returns = [0.02, -0.01, 0.03, -0.02, 0.01]
    curve = [100.0]
    for value in returns:
        curve.append(curve[-1] * (1 + value))
    stamps = [_at(day) for day in range(len(curve))]
    result = _metrics(curve, stamps)
    # Mean 0.006; sample variance 0.00172 / 4 = 0.00043; five returns over five
    # days give 365 periods a year, so the annualiser is sqrt(365).
    assert result.annualized_volatility == pytest.approx(math.sqrt(0.00043) * math.sqrt(365))
    assert result.sharpe == pytest.approx(0.006 / math.sqrt(0.00043) * math.sqrt(365))
    # Downside deviation over all five periods: sqrt((0.01^2 + 0.02^2) / 5).
    assert result.sortino == pytest.approx(0.006 / math.sqrt((0.01**2 + 0.02**2) / 5) * math.sqrt(365))
    _assert_matches(result, _reference_metrics(curve, stamps, 100.0))


def test_a_full_year_gives_cagr_equal_to_the_total_return() -> None:
    # Exactly 365 days: years = 1, so CAGR is the total return.
    result = _metrics([100.0, 120.0], [_at(0), _at(365)])
    assert result.cagr == pytest.approx(0.20)
    assert result.total_return == pytest.approx(0.20)
    # One return has no sample deviation, so Sharpe and volatility are reported as zero.
    assert result.sharpe == 0.0
    assert result.annualized_volatility == 0.0


def test_two_year_cagr_and_calmar_are_known() -> None:
    # 100 -> 80 (-20 %) -> 121 over 730 days: CAGR sqrt(1.21) - 1 = 10 %,
    # max drawdown 20 %, so Calmar = 0.10 / 0.20 = 0.5.
    curve = [100.0, 80.0, 121.0]
    stamps = [_at(0), _at(365), _at(730)]
    result = _metrics(curve, stamps)
    assert result.cagr == pytest.approx(0.10)
    assert result.max_drawdown == pytest.approx(0.20)
    assert result.calmar == pytest.approx(0.50)
    _assert_matches(result, _reference_metrics(curve, stamps, 100.0))


def test_drawdown_is_measured_from_starting_equity_before_the_first_high() -> None:
    # The peak starts at the starting equity: 100 -> 95 -> 90 -> 97 is a 10 %
    # drawdown at day 2, even though the curve never made a high first.
    curve = [95.0, 90.0, 97.0]
    stamps = [_at(0), _at(1), _at(2)]
    result = _metrics(curve, stamps)
    assert result.max_drawdown == pytest.approx(0.10)
    assert result.max_drawdown_duration_days == 2


def test_monthly_and_worst_year_returns_use_month_and_year_end_values() -> None:
    # Month-end values: Dec 2025 -> 110, Jan 2026 -> 99, Feb 2026 -> 104.
    stamps = [
        datetime(2025, 12, 15, tzinfo=UTC),
        datetime(2025, 12, 31, tzinfo=UTC),
        datetime(2026, 1, 20, tzinfo=UTC),
        datetime(2026, 2, 10, tzinfo=UTC),
        datetime(2026, 2, 27, tzinfo=UTC),
    ]
    curve = [105.0, 110.0, 99.0, 103.0, 104.0]
    result = _metrics(curve, stamps)
    # Monthly: 110/100 - 1, 99/110 - 1, 104/99 - 1.
    monthly = [110 / 100 - 1, 99 / 110 - 1, 104 / 99 - 1]
    assert result.worst_month_return == pytest.approx(min(monthly))
    assert result.positive_month_ratio == pytest.approx(2 / 3)
    # Yearly: 2025 ends at 110 (+10 %), 2026 ends at 104 against 110 (-5.45 %).
    assert result.worst_year_return == pytest.approx(104 / 110 - 1)


def test_drawdown_clock_restarts_after_a_recovery_above_the_peak() -> None:
    # Peak 110 on day 1, dip to 90, new peak 111 on day 3, dip to 105 on day 4.
    # The second underwater span is one day, the first is one day.
    curve = [100.0, 110.0, 90.0, 111.0, 105.0]
    result = _metrics(curve, [_at(day) for day in range(len(curve))])
    assert result.max_drawdown_duration_days == 1


def test_drawdown_clock_restarts_when_equity_returns_exactly_to_the_peak() -> None:
    curve = [100.0, 110.0, 90.0, 110.0, 105.0]
    result = _metrics(curve, [_at(day) for day in range(len(curve))])
    assert result.max_drawdown == pytest.approx((110 - 90) / 110)
    assert result.max_drawdown_duration_days == 2
