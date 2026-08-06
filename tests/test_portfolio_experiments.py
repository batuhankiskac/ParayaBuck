from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.experiments import outer_holdout_start
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.portfolio_experiments import (
    PortfolioCandidateSpace,
    PortfolioExperimentSpec,
    PortfolioGate,
    run_portfolio_experiment,
)
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2025, 7, 1, tzinfo=UTC)


def _bars(days: int = 900) -> list[Bar]:
    bars: list[Bar] = []
    for index in range(days):
        timestamp = START + timedelta(days=index)
        for symbol, drift in (("SPY", 0.0004), ("QQQ", 0.0006), ("XLE", -0.0002)):
            close = 100 * (1 + drift) ** index
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * 0.999,
                    close * 1.001,
                    close * 0.998,
                    close,
                    5_000_000,
                    Market.EQUITY,
                )
            )
    return bars


def _spec(
    *, max_parameter_trials: int = 4, outer_holdout_months: int = 6
) -> PortfolioExperimentSpec:
    return PortfolioExperimentSpec(
        experiment_id="test_portfolio_exp",
        start=START,
        end=END,
        universe=("SPY", "QQQ", "XLE"),
        universe_version="permanent_etf_core_v1",
        candidate_spaces=(
            PortfolioCandidateSpace(
                "equity_etf_dual_momentum_v1",
                "1.0.0",
                Market.EQUITY,
                "1d",
                (("momentum_scheme", ("12_1", "6_1")), ("volatility_window", (20, 60))),
            ),
        ),
        train_months=9,
        validation_months=1,
        outer_holdout_months=outer_holdout_months,
        embargo_bars=5,
        max_mechanism_trials=30,
        max_parameter_trials=max_parameter_trials,
        max_holdout_finalists=2,
    )


def _factory(strategy: PortfolioStrategy, cost: object) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        slippage_bps=cost.slippage_bps,  # type: ignore[attr-defined]
        spread_bps=cost.spread_bps,  # type: ignore[attr-defined]
        crypto_taker_fee_bps=cost.taker_fee_bps,  # type: ignore[attr-defined]
        benchmark_symbol="SPY",
    )


def test_holdout_is_hidden_from_validation_folds_and_reports() -> None:
    spec = _spec()
    cutoff = outer_holdout_start(END, spec.outer_holdout_months)
    trials = run_portfolio_experiment(_bars(), spec, _factory)
    assert trials
    for trial in trials:
        assert trial.holdout_evaluations == 0
        assert all(fold.end_date <= cutoff.isoformat() for fold in trial.fold_results)


def _permissive_gate() -> PortfolioGate:
    """Gate that every synthetic trial passes; production policy stays strict."""
    return PortfolioGate(
        market=Market.EQUITY,
        minimum_oos_calendar_months=1,
        minimum_profit_factor=0.0,
        minimum_sharpe=0.0,
        maximum_drawdown=1.0,
        minimum_deflated_sharpe_probability=0.0,
        maximum_pbo=1.0,
        minimum_positive_fold_ratio=0.0,
        minimum_positive_month_ratio=0.0,
        minimum_stress_profit_factor=0.0,
        maximum_symbol_profit_concentration=1.0,
        maximum_single_asset_weight=1.0,
        require_parameter_plateau=False,
        require_regime_diversity=False,
    )


def test_holdout_opens_only_for_preregistered_frozen_candidate() -> None:

    from parayabuck.backtest.portfolio_experiments import trial_id_for

    strategies = _spec().candidate_spaces[0].iter_strategies()
    frozen_id = trial_id_for(strategies[0])
    unknown_id = "f" * 16
    assert frozen_id != unknown_id
    spec = replace(
        _spec(), max_holdout_finalists=1, frozen_candidate_trial_id=frozen_id
    )
    trials = run_portfolio_experiment(
        _bars(), spec, _factory, unlock_holdout=True, validation_gate=_permissive_gate()
    )
    evaluated = [trial for trial in trials if trial.holdout_evaluations == 1]
    assert len(evaluated) == 1
    assert evaluated[0].trial_id == frozen_id
    assert not evaluated[0].holdout_missing
    # A trial id that is not among the evaluated candidates is rejected.
    with pytest.raises(ValueError, match="not among"):
        run_portfolio_experiment(
            _bars(), replace(spec, frozen_candidate_trial_id=unknown_id), _factory,
            unlock_holdout=True, validation_gate=_permissive_gate(),
        )
    # A missing preregistration is rejected too.
    with pytest.raises(ValueError, match="preregistered"):
        run_portfolio_experiment(
            _bars(), replace(spec, frozen_candidate_trial_id=None), _factory,
            unlock_holdout=True, validation_gate=_permissive_gate(),
        )
    # Opening the holdout appends the holdout result only; validation folds
    # themselves must be byte-identical to the locked run.
    locked = run_portfolio_experiment(_bars(), spec, _factory, validation_gate=_permissive_gate())
    for trial in trials:
        locked_trial = next(t for t in locked if t.trial_id == trial.trial_id)
        assert locked_trial.fold_results == trial.fold_results[: len(locked_trial.fold_results)]


def test_holdout_unlock_rejected_when_frozen_candidate_fails_gate() -> None:

    from parayabuck.backtest.portfolio_experiments import trial_id_for

    strategies = _spec().candidate_spaces[0].iter_strategies()
    spec = replace(
        _spec(),
        max_holdout_finalists=1,
        frozen_candidate_trial_id=trial_id_for(strategies[0]),
    )
    # The strict production gate fails every synthetic candidate on sample
    # size, so the holdout must stay locked and the run must refuse.
    with pytest.raises(ValueError, match="validation gate"):
        run_portfolio_experiment(_bars(), spec, _factory, unlock_holdout=True)


def test_all_trials_share_pbo_computation() -> None:
    spec = _spec()
    trials = run_portfolio_experiment(_bars(), spec, _factory)
    pbo_values = {trial.probability_backtest_overfitting for trial in trials}
    assert len(pbo_values) == 1


def test_same_data_spec_seed_produce_identical_trials() -> None:
    spec = _spec()
    first = run_portfolio_experiment(_bars(), spec, _factory)
    second = run_portfolio_experiment(_bars(), spec, _factory)
    assert [(t.trial_id, t.sharpe, t.calmar, t.total_rebalance_months) for t in first] == [
        (t.trial_id, t.sharpe, t.calmar, t.total_rebalance_months) for t in second
    ]


def test_embargo_never_exceeds_fold_boundary() -> None:
    spec = _spec()
    trials = run_portfolio_experiment(_bars(), spec, _factory)
    assert trials


def test_portfolio_gate_fails_closed_on_insufficient_evidence() -> None:
    gate = PortfolioGate.for_market(Market.EQUITY)
    from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult

    trial = PortfolioTrialResult(
        trial_id="x",
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        parameters=(),
        fold_results=(),
        market=Market.EQUITY,
        total_rebalance_months=20,
        unique_oos_calendar_months=20,
        closed_positions=0,
        profit_factor=1.0,
        sharpe=0.4,
        calmar=0.0,
        expectancy=0.0,
        deflated_sharpe_probability=0.5,
        probability_backtest_overfitting=0.5,
        max_drawdown=0.1,
        positive_fold_ratio=0.5,
        positive_month_ratio=0.5,
        stress_profit_factor=0.9,
        stress_expectancy=-1.0,
        stress_realized_pnls=(),
        gross_profit_by_symbol=(),
        maximum_concentration=0.7,
        regime_diverse=False,
    )
    decision = gate.evaluate(trial)
    assert not decision.passed
    assert "insufficient_oos_sample" in decision.failures
    assert "sharpe_below_gate" in decision.failures


def test_portfolio_gate_passes_with_full_evidence() -> None:
    gate = PortfolioGate.for_market(Market.EQUITY)
    from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult

    trial = PortfolioTrialResult(
        trial_id="ok",
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        parameters=(("momentum_scheme", "12_1"),),
        fold_results=(),
        market=Market.EQUITY,
        total_rebalance_months=70,
        unique_oos_calendar_months=70,
        closed_positions=0,
        profit_factor=1.3,
        sharpe=0.9,
        calmar=1.0,
        expectancy=5.0,
        deflated_sharpe_probability=0.96,
        probability_backtest_overfitting=0.1,
        max_drawdown=0.05,
        positive_fold_ratio=0.8,
        positive_month_ratio=0.7,
        stress_profit_factor=1.1,
        stress_expectancy=1.0,
        stress_realized_pnls=(1.0,),
        gross_profit_by_symbol=(("SPY", 100.0), ("QQQ", 100.0), ("XLE", 100.0), ("XLK", 100.0)),
        maximum_concentration=0.4,
        regime_diverse=True,
        aggregate_oos_sharpe=0.9,
        aggregate_oos_max_drawdown=0.05,
        aggregate_oos_return=0.3,
        neighborhood_robust=True,
        holdout_evaluations=1,
    )
    decision = gate.evaluate(trial)
    assert decision.passed, decision.failures


def test_equity_and_crypto_use_distinct_gate_thresholds() -> None:
    equity = PortfolioGate.for_market(Market.EQUITY)
    crypto = PortfolioGate.for_market(Market.CRYPTO)
    assert equity.minimum_oos_calendar_months == 60
    assert equity.minimum_oos_positions == 0
    assert crypto.minimum_oos_positions == 100
    assert crypto.minimum_positive_month_ratio == 0.60
    assert equity.minimum_positive_fold_ratio == 0.60
    assert equity.maximum_drawdown == 0.10
    assert equity.maximum_drawdown < crypto.maximum_drawdown
    assert equity.maximum_symbol_profit_concentration == 0.30


def test_equity_gate_requires_profit_factor() -> None:
    gate = PortfolioGate.for_market(Market.EQUITY)
    from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult

    trial = PortfolioTrialResult(
        trial_id="pf",
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        parameters=(),
        fold_results=(),
        market=Market.EQUITY,
        total_rebalance_months=70,
        unique_oos_calendar_months=70,
        closed_positions=0,
        profit_factor=1.1,  # below the 1.15 production threshold
        sharpe=0.9,
        calmar=1.0,
        expectancy=5.0,
        deflated_sharpe_probability=0.96,
        probability_backtest_overfitting=0.1,
        max_drawdown=0.05,
        positive_fold_ratio=0.8,
        positive_month_ratio=0.7,
        stress_profit_factor=1.1,
        stress_expectancy=1.0,
        stress_realized_pnls=(1.0,),
        gross_profit_by_symbol=(("SPY", 100.0), ("QQQ", 100.0)),
        maximum_concentration=0.4,
        regime_diverse=True,
        aggregate_oos_sharpe=0.9,
        aggregate_oos_max_drawdown=0.05,
        aggregate_oos_return=0.3,
        neighborhood_robust=True,
        holdout_evaluations=1,
    )
    decision = gate.evaluate(trial)
    assert not decision.passed
    assert "profit_factor_below_gate" in decision.failures


def test_crypto_gate_requires_fold_ratio_and_regime_diversity() -> None:
    gate = PortfolioGate.for_market(Market.CRYPTO)
    from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult

    trial = PortfolioTrialResult(
        trial_id="crypto",
        strategy_name="crypto_cross_sectional_momentum_1h_v1",
        strategy_version="1.0.0",
        parameters=(),
        fold_results=(),
        market=Market.CRYPTO,
        total_rebalance_months=0,
        unique_oos_calendar_months=0,
        closed_positions=150,
        profit_factor=1.3,
        sharpe=0.9,
        calmar=1.0,
        expectancy=5.0,
        deflated_sharpe_probability=0.96,
        probability_backtest_overfitting=0.1,
        max_drawdown=0.3,
        positive_fold_ratio=0.4,  # below 0.60
        positive_month_ratio=0.7,
        stress_profit_factor=1.1,
        stress_expectancy=1.0,
        stress_realized_pnls=(1.0,),
        gross_profit_by_symbol=(("BTC/USD", 100.0), ("ETH/USD", 100.0)),
        maximum_concentration=0.4,
        regime_diverse=False,  # regime diversity is required for crypto too
        aggregate_oos_sharpe=0.9,
        aggregate_oos_max_drawdown=0.3,
        aggregate_oos_return=0.3,
        neighborhood_robust=True,
        holdout_evaluations=1,
    )
    decision = gate.evaluate(trial)
    assert not decision.passed
    assert "positive_fold_ratio_below_gate" in decision.failures
    assert "insufficient_regime_diversity" in decision.failures


class _FixedTargetStrategy(PortfolioStrategy):
    """Holds 60% SPY from the first decision (deterministic fills)."""

    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("SPY", 0.6),),
            cash_weight=0.4,
            universe_version="test",
        )


def test_monthly_folds_are_contiguous_partitions_of_one_continuous_run() -> None:
    """Fold diagnostics must be exact calendar-month partitions of ONE
    continuous run: no forced liquidation, no gaps, no synthetic zero
    returns. A per-fold-liquidation design fails this: each fold would
    record its own final liquidation, so the fold totals would exceed the
    run's totals."""
    from parayabuck.backtest.portfolio_experiments import _partition_monthly_folds
    from parayabuck.portfolio import rebalance_policy_for

    strategy = _FixedTargetStrategy()
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="SPY",
    )
    result = engine.run(_bars(400), liquidate_at_end=False)
    folds = _partition_monthly_folds(result, "1d", Market.EQUITY)
    assert len(folds) >= 10
    # Calendar-month boundaries, strictly increasing, no overlaps or gaps.
    months = [(fold.start_date[:7], fold.end_date[:7]) for fold in folds]
    assert all(start <= end for start, end in months)
    assert all(
        previous[1] < current[0] for previous, current in zip(months, months[1:])
    )
    # Exact partition of the continuous book: the folds split the run's
    # rebalances and realized events without inventing any (a liquidation at
    # every fold end would add events and break this identity).
    assert sum(fold.rebalance_count for fold in folds) == result.rebalance_count
    assert sum(fold.closed_positions for fold in folds) == result.closed_positions
    # The first fold's leading return must be genuine: a duplicated boundary
    # point (the old implementation prepended the fold start value) created
    # a synthetic 0.0 as the first return even though the book is moving.
    first = folds[0]
    curve = list(first.equity_curve)
    returns = [
        curve[i] / curve[i - 1] - 1
        for i in range(1, len(curve))
        if curve[i - 1] > 0
    ]
    assert returns and returns[0] != 0.0


def test_suspended_experiment_is_never_evaluated() -> None:
    import pytest

    spec = replace(_spec(), suspended=True)
    with pytest.raises(ValueError, match="suspended"):
        run_portfolio_experiment(_bars(), spec, _factory)


def test_experiment_spec_budget_validation() -> None:
    spec = _spec()
    assert spec.max_parameter_trials >= sum(space.combinations for space in spec.candidate_spaces)
    with __import__("pytest").raises(ValueError):
        PortfolioExperimentSpec(
            experiment_id="bad",
            start=START,
            end=END,
            universe=("SPY",),
            universe_version="v1",
            candidate_spaces=_spec().candidate_spaces,
            max_parameter_trials=1,
        )
