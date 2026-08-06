from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

import pytest

from parayabuck.backtest.experiments import probability_backtest_overfitting
from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult
from parayabuck.backtest.portfolio_experiments import (
    PortfolioGate,
    PortfolioTrialResult,
    _build_portfolio_trial,
    _mark_portfolio_plateaus,
    _monthly_from_curve,
    _stitched_oos_series,
)
from parayabuck.core.models import Market
from parayabuck.portfolio.equity_dual_momentum import EquityEtfDualMomentumV1


def _fold(
    start: datetime,
    equity_values: list[float],
    *,
    sharpe: float = 1.0,
    calmar: float = 1.0,
    max_drawdown: float = 0.05,
    pnls: tuple[float, ...] = (10.0,),
) -> PortfolioBacktestResult:
    timestamps = [start + timedelta(days=index) for index in range(len(equity_values))]
    starting = 100_000.0
    ending = equity_values[-1]
    total_return = ending / starting - 1
    return PortfolioBacktestResult(
        starting_equity=starting,
        ending_equity=ending,
        total_return=total_return,
        benchmark_return=0.0,
        excess_return=total_return,
        gross_return=total_return,
        net_return=total_return,
        max_drawdown=max_drawdown,
        max_drawdown_duration_days=0,
        cagr=0.0,
        annualized_volatility=0.1,
        sharpe=sharpe,
        sortino=sharpe,
        calmar=calmar,
        duration_days=len(equity_values),
        start_date=timestamps[0].isoformat(),
        end_date=timestamps[-1].isoformat(),
        equity_curve=tuple(equity_values),
        curve_timestamps=tuple(timestamps),
        realized_pnls=pnls,
        realized_pnl_events=((timestamps[-1], "SPY", pnls[0] if pnls else 0.0),),
        rebalances=(),
        fills=(),
        weight_history=(),
        universe_history=(),
        benchmark_bars=(),
        benchmark_symbol="SPY",
        slippage_cost_total=0.0,
        spread_cost_total=0.0,
        fee_total=0.0,
        turnover=0.0,
        average_holdings_count=1.0,
        average_cash_ratio=0.0,
        rebalance_count=1,
        closed_positions=1,
        per_symbol=(),
        maximum_concentration=0.5,
        worst_month_return=-0.01,
        worst_year_return=-0.01,
        positive_month_ratio=0.5,
        state=__import__(
            "parayabuck.portfolio.models", fromlist=["PortfolioState"]
        ).PortfolioState(strategy_name="t", strategy_version="1"),
    )


def test_stitched_curve_compounds_across_folds() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    fold_one = _fold(start, [110_000.0])  # +10%
    fold_two = _fold(start + timedelta(days=40), [90_000.0])  # fold return -10%
    curve, timestamps, returns = _stitched_oos_series((fold_one, fold_two))
    # Compound: 100k * 1.10 * 0.90 = 99k -> -1%; no nominal-level chaining.
    assert curve[-1] == pytest.approx(99_000.0)
    assert len(returns) == 2
    assert returns[0] == pytest.approx(0.10)
    assert returns[1] == pytest.approx(-0.10)


def test_fold_restart_does_not_create_fake_monthly_returns() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    fold_one = _fold(start, [100_000.0, 100_000.0, 105_000.0])
    fold_two = _fold(start + timedelta(days=30), [100_000.0, 100_000.0, 100_000.0])
    curve, timestamps, _returns = _stitched_oos_series((fold_one, fold_two))
    monthly, serialized = _monthly_from_curve(curve, timestamps)
    # Fold two is flat; it must not produce a spurious jump because it
    # restarts at 100k while the stitched curve continues from 105k.
    values = [value for value in monthly.values()]
    assert sum(1 for value in values if value == 0.0) >= 1
    assert serialized[1][1] == pytest.approx(0.0)
    assert serialized[0][1] == pytest.approx(0.05)


def test_dsr_matches_analytic_reference_for_single_trial() -> None:
    from parayabuck.backtest.experiments import deflated_sharpe_probability

    observed_sharpe = 0.2
    observations = 101
    # With trials=1, skewness=0, kurtosis=3 the statistic is
    # SR*sqrt(n-1)/sqrt(1+(kurtosis-1)/4*SR^2) (Bailey/Lopez de Prado 2014):
    # the result must equal the normal CDF of that closed form exactly.
    denominator = math.sqrt(1 + ((3 - 1) / 4) * observed_sharpe**2)
    expected = NormalDist().cdf(observed_sharpe * 10.0 / denominator)
    assert deflated_sharpe_probability(observed_sharpe, observations, 1) == expected


def test_pbo_is_deterministic_for_same_seed() -> None:
    matrix = [
        [0.01, 0.02, -0.01, 0.03, 0.0, 0.02],
        [0.03, 0.01, 0.0, -0.02, 0.01, 0.01],
        [-0.01, 0.0, 0.02, 0.01, 0.03, -0.01],
        [0.02, -0.01, 0.03, 0.01, 0.0, 0.02],
        [0.0, 0.03, 0.01, -0.01, 0.02, 0.01],
        [0.01, 0.0, -0.02, 0.02, 0.01, 0.03],
        [0.02, 0.01, 0.01, 0.0, -0.01, 0.0],
        [0.0, 0.01, 0.03, 0.02, 0.01, -0.02],
    ]
    first = probability_backtest_overfitting(matrix, seed=7)
    second = probability_backtest_overfitting(matrix, seed=7)
    assert first == second


def test_pbo_exhaustive_path_is_seed_independent() -> None:
    # Width 4 -> C(4,2)=6 combinations <= maximum_splits: exhaustive, so the
    # result must not depend on the seed at all.
    matrix = [
        [0.01, -0.02, 0.03, -0.01],
        [0.02, 0.01, -0.01, 0.02],
        [-0.01, 0.03, 0.01, 0.0],
        [0.03, -0.01, 0.02, 0.01],
    ]
    values = {probability_backtest_overfitting(matrix, seed=s) for s in (1, 7, 42)}
    assert len(values) == 1


def test_pbo_overfit_matrix_scores_high_and_robust_low() -> None:
    # Single-block winner rows: the in-sample best row is always the worst
    # out-of-sample -> PBO must be high.
    overfit = [[-1.0] * 6 for _ in range(6)]
    for index in range(6):
        overfit[index][index] = 1.0
    # Flat, near-identical rows leave no room for selection overfit.
    robust = [[0.001] * 6 for _ in range(8)]
    assert probability_backtest_overfitting(overfit, seed=7) >= 0.5
    assert probability_backtest_overfitting(robust, seed=7) < 0.5


def test_plateau_excludes_self_and_requires_five_real_neighbors() -> None:
    def strategy(parameters: dict[str, object]) -> EquityEtfDualMomentumV1:
        return EquityEtfDualMomentumV1(parameters)

    def trial_with(
        parameters: dict[str, object], profitable: bool
    ) -> PortfolioTrialResult:
        fold = _fold(
            datetime(2024, 1, 1, tzinfo=UTC),
            [105_000.0] if profitable else [95_000.0],
            pnls=(100.0,) if profitable else (-100.0,),
        )
        stress = _fold(datetime(2024, 1, 1, tzinfo=UTC), [100_000.0])
        return _build_portfolio_trial(
            strategy(parameters), (fold,), (stress,), trials=1, market=Market.EQUITY
        )

    base = {"momentum_scheme": "6_1", "absolute_filter": "above_200dma", "top_n": 3}
    center = trial_with({**base, "volatility_window": 20}, profitable=True)
    # All five real distance-1 neighbors of the center:
    neighbors = [
        trial_with({**base, "momentum_scheme": "12_1", "volatility_window": 20}, True),
        trial_with({**base, "momentum_scheme": "3_6_12", "volatility_window": 20}, True),
        trial_with(
            {**base, "absolute_filter": "positive_momentum", "volatility_window": 20}, False
        ),
        trial_with(
            {**base, "absolute_filter": "excess_over_cash", "volatility_window": 20}, False
        ),
        trial_with({**base, "volatility_window": 60}, False),
    ]
    # A distant candidate (distance 3) must never count toward the plateau.
    distant = trial_with(
        {
            "momentum_scheme": "12_1",
            "absolute_filter": "positive_momentum",
            "top_n": 3,
            "volatility_window": 60,
        },
        True,
    )
    candidates = [center, *neighbors, distant]
    marked = _mark_portfolio_plateaus(candidates, "momentum_scheme")
    center_marked = next(item for item in marked if item.trial_id == center.trial_id)
    # Candidate itself is never in its own neighborhood report.
    assert all(
        item["trial_id"] != center.trial_id for item in center_marked.neighborhood_report
    )
    # Only two profitable neighbors: not a plateau (five needed).
    assert len(center_marked.neighborhood_report) == 2
    assert not center_marked.neighborhood_robust
    assert all(item["hamming_distance"] == 1 for item in center_marked.neighborhood_report)


def test_plateau_needs_five_qualifying_neighbors() -> None:
    def strategy(parameters: dict[str, object]) -> EquityEtfDualMomentumV1:
        return EquityEtfDualMomentumV1(parameters)

    def trial_with(
        parameters: dict[str, object], profitable: bool
    ) -> PortfolioTrialResult:
        fold = _fold(
            datetime(2024, 1, 1, tzinfo=UTC),
            [105_000.0] if profitable else [95_000.0],
            pnls=(100.0,) if profitable else (-100.0,),
        )
        stress = _fold(datetime(2024, 1, 1, tzinfo=UTC), [100_000.0])
        return _build_portfolio_trial(
            strategy(parameters), (fold,), (stress,), trials=1, market=Market.EQUITY
        )

    base = {"momentum_scheme": "6_1", "absolute_filter": "above_200dma", "top_n": 3}
    center = trial_with({**base, "volatility_window": 20}, profitable=True)
    profitable_neighbors = [
        trial_with({**base, "momentum_scheme": "12_1", "volatility_window": 20}, True),
        trial_with({**base, "momentum_scheme": "3_6_12", "volatility_window": 20}, True),
        trial_with({**base, "absolute_filter": "positive_momentum", "volatility_window": 20}, True),
        trial_with({**base, "absolute_filter": "excess_over_cash", "volatility_window": 20}, True),
    ]
    # Four profitable neighbors: not a plateau.
    marked = _mark_portfolio_plateaus([center, *profitable_neighbors], "momentum_scheme")
    center_marked = next(item for item in marked if item.trial_id == center.trial_id)
    assert not center_marked.neighborhood_robust
    # With the fifth profitable neighbor the plateau passes.
    all_neighbors = [
        *profitable_neighbors,
        trial_with({**base, "volatility_window": 60}, True),
    ]
    marked_again = _mark_portfolio_plateaus([center, *all_neighbors], "momentum_scheme")
    center_again = next(item for item in marked_again if item.trial_id == center.trial_id)
    assert center_again.neighborhood_robust
    assert len(center_again.neighborhood_report) == 5
    assert all(item["hamming_distance"] >= 1 for item in center_again.neighborhood_report)


def test_gate_rejects_old_style_candidate_with_585_pct_fold_ratio() -> None:
    gate = PortfolioGate.for_market(Market.EQUITY)
    trial = PortfolioTrialResult(
        trial_id="6_1",
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        parameters=(("momentum_scheme", "6_1"), ("absolute_filter", "above_200dma")),
        fold_results=(),
        market=Market.EQUITY,
        total_rebalance_months=53,
        unique_oos_calendar_months=53,
        closed_positions=0,
        profit_factor=1.3,
        sharpe=1.77,
        calmar=1.0,
        expectancy=5.0,
        deflated_sharpe_probability=0.985,
        probability_backtest_overfitting=0.249,
        max_drawdown=0.117,
        positive_fold_ratio=0.585,
        positive_month_ratio=0.585,
        stress_profit_factor=1.1,
        stress_expectancy=1.0,
        stress_realized_pnls=(1.0,),
        gross_profit_by_symbol=(("SPY", 100.0), ("QQQ", 100.0), ("XLE", 100.0), ("XLK", 100.0)),
        maximum_concentration=0.444,
        regime_diverse=True,
        aggregate_oos_sharpe=1.77,
        aggregate_oos_max_drawdown=0.117,
        neighborhood_robust=True,
        holdout_evaluations=1,
    )
    decision = gate.evaluate(trial)
    assert not decision.passed
    assert "positive_fold_ratio_below_gate" in decision.failures
    assert "insufficient_oos_sample" in decision.failures
    assert "drawdown_above_gate" in decision.failures
    assert "pbo_above_gate" in decision.failures
