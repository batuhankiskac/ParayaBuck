from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from statistics import median

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
from parayabuck.backtest.experiments import (
    CRYPTO_BASE,
    CRYPTO_STRESS,
    EQUITY_BASE,
    EQUITY_STRESS,
    CostScenario,
    ExperimentSpec,
    TrialResult,
    deflated_sharpe_probability,
    probability_backtest_overfitting,
)
from parayabuck.backtest.validation import calendar_fold_windows
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market
from parayabuck.markets.timeframes import timeframe_duration
from parayabuck.strategies.base import Strategy
from parayabuck.strategies.crypto_range import CryptoRangeReversion
from parayabuck.strategies.crypto_trend import CryptoTrendBreakout
from parayabuck.strategies.specs import (
    CryptoRangeParams,
    CryptoTrendParams,
    StockMeanReversionParams,
    StockTrendParams,
    StrategySpec,
)
from parayabuck.strategies.stock_mean_reversion import StockMeanReversion
from parayabuck.strategies.stock_trend import StockTrendPullback

ResearchEngineFactory = Callable[
    [EngineConfig, tuple[Strategy, ...], CostScenario], BacktestEngine
]


def strategy_from_spec(spec: StrategySpec) -> Strategy:
    if spec.name == "stock_trend_pullback" and isinstance(
        spec.parameters, StockTrendParams
    ):
        return StockTrendPullback(spec.parameters, spec.exit_policy)
    if spec.name == "stock_mean_reversion" and isinstance(
        spec.parameters, StockMeanReversionParams
    ):
        return StockMeanReversion(spec.parameters, spec.exit_policy)
    if spec.name == "crypto_trend_breakout" and isinstance(
        spec.parameters, CryptoTrendParams
    ):
        return CryptoTrendBreakout(params=spec.parameters, exit_policy=spec.exit_policy)
    if spec.name == "crypto_range_reversion" and isinstance(
        spec.parameters, CryptoRangeParams
    ):
        return CryptoRangeReversion(params=spec.parameters, exit_policy=spec.exit_policy)
    raise ValueError(f"Unsupported or mismatched strategy spec: {spec.name}")


def run_nested_experiment(
    bars: list[Bar],
    experiment: ExperimentSpec,
    base_config: EngineConfig,
    engine_factory: ResearchEngineFactory,
    *,
    unlock_holdout: bool = False,
) -> tuple[TrialResult, ...]:
    """Evaluate strategy/exit candidates on monthly validation folds.

    The last ``outer_holdout_months`` are excluded from every research fold.
    They are touched only when ``unlock_holdout`` is explicit, and only for the
    already-ranked finalist budget.
    """
    bounded = [bar for bar in bars if experiment.start <= bar.timestamp <= experiment.end]
    candidates = tuple(
        candidate
        for space in experiment.candidate_spaces
        for candidate in space.iter_specs()
    )[: experiment.max_parameter_trials]
    if not candidates:
        candidates = experiment.strategies[: experiment.max_parameter_trials]
    if not candidates:
        raise ValueError("Experiment has no concrete strategy candidates")

    base_config = replace(
        base_config,
        risk=replace(
            base_config.risk,
            risk_per_trade_pct=experiment.alpha_risk_per_trade_pct,
        ),
    )
    evaluated: list[TrialResult] = []
    block_returns: list[list[float]] = []
    for spec in candidates:
        candidate_bars = _candidate_bars(bounded, spec)
        embargo = _embargo_duration(
            candidate_bars, spec, experiment.embargo_bars
        )
        windows = calendar_fold_windows(
            candidate_bars,
            train_months=experiment.train_months,
            test_months=experiment.validation_months,
            outer_holdout_months=experiment.outer_holdout_months,
            embargo=embargo,
        )
        base_cost, stress_cost = _costs_for(spec.market, experiment)
        fold_results: list[BacktestResult] = []
        stress_results: list[BacktestResult] = []
        gross_profit_by_symbol: dict[str, float] = {}
        for window in windows:
            train = [
                bar
                for bar in candidate_bars
                if window.train_start <= bar.timestamp < window.train_end
            ]
            validation = [
                bar
                for bar in candidate_bars
                if window.test_start <= bar.timestamp < window.test_end
            ]
            if not train or not validation:
                continue
            config = replace(base_config, timeframe=spec.timeframe)
            strategy = strategy_from_spec(spec)
            candidate_engine = engine_factory(config, (strategy,), base_cost)
            fold_results.append(candidate_engine.run(validation, warmup=train))
            for trade in candidate_engine.broker.trades:
                if trade.pnl > 0:
                    gross_profit_by_symbol[trade.symbol] = (
                        gross_profit_by_symbol.get(trade.symbol, 0.0) + trade.pnl
                    )
            stress_results.append(
                engine_factory(
                    config,
                    (strategy_from_spec(spec),),
                    stress_cost,
                ).run(validation, warmup=train)
            )
        trial = _build_trial(
            spec,
            tuple(fold_results),
            tuple(stress_results),
            trials=len(candidates),
            gross_profit_by_symbol=tuple(sorted(gross_profit_by_symbol.items())),
        )
        evaluated.append(trial)
        block_returns.append([fold.total_return for fold in fold_results])

    common_width = min((len(row) for row in block_returns), default=0)
    pbo = probability_backtest_overfitting(
        [row[:common_width] for row in block_returns if common_width]
    )
    evaluated = [replace(trial, probability_backtest_overfitting=pbo) for trial in evaluated]
    evaluated = _mark_parameter_plateaus(evaluated)
    evaluated.sort(
        key=lambda trial: (
            trial.neighborhood_robust,
            trial.median_fold_calmar,
            trial.expectancy,
        ),
        reverse=True,
    )
    if unlock_holdout:
        finalists = evaluated[: experiment.max_holdout_finalists]
        replacements = {
            trial.trial_id: _evaluate_holdout(
                trial,
                next(spec for spec in candidates if _trial_id(spec) == trial.trial_id),
                bounded,
                experiment,
                base_config,
                engine_factory,
            )
            for trial in finalists
        }
        evaluated = [replacements.get(trial.trial_id, trial) for trial in evaluated]
    return tuple(evaluated)


def _build_trial(
    spec: StrategySpec,
    folds: tuple[BacktestResult, ...],
    stress_folds: tuple[BacktestResult, ...],
    *,
    trials: int,
    holdout_evaluations: int = 0,
    gross_profit_by_symbol: tuple[tuple[str, float], ...] = (),
) -> TrialResult:
    pnls = tuple(pnl for fold in folds for pnl in fold.realized_pnls)
    stress_pnls = tuple(pnl for fold in stress_folds for pnl in fold.realized_pnls)
    pf = _profit_factor(pnls)
    stress_pf = _profit_factor(stress_pnls)
    total_trades = len(pnls)
    sharpe = median(fold.sharpe for fold in folds) if folds else 0.0
    expectancy = sum(pnls) / total_trades if total_trades else 0.0
    stress_expectancy = sum(stress_pnls) / len(stress_pnls) if stress_pnls else 0.0
    observations = sum(max(1, fold.duration_days) for fold in folds)
    parameters = {
        **asdict(spec.parameters),
        "exit_policy_id": spec.exit_policy.policy_id,
        "break_even_trigger_r": spec.exit_policy.break_even_trigger_r,
        "trailing_atr_multiple": spec.exit_policy.trailing_atr_multiple,
        "max_holding_bars": spec.exit_policy.max_holding_bars,
    }
    return TrialResult(
        trial_id=_trial_id(spec),
        strategy_name=spec.name,
        strategy_version=spec.version,
        parameters=tuple(sorted(parameters.items())),
        fold_results=folds,
        total_trades=total_trades,
        profit_factor=pf,
        sharpe=sharpe,
        calmar=median(fold.calmar for fold in folds) if folds else 0.0,
        expectancy=expectancy,
        deflated_sharpe_probability=deflated_sharpe_probability(
            sharpe / math.sqrt(365 if spec.market == Market.CRYPTO else 252),
            observations,
            trials,
        ),
        probability_backtest_overfitting=0.0,
        cost_sensitivity=(
            ("base_expectancy", expectancy),
            ("stress_expectancy", stress_expectancy),
        ),
        max_drawdown=max((fold.max_drawdown for fold in folds), default=0.0),
        positive_fold_ratio=(
            sum(fold.expectancy > 0 for fold in folds) / len(folds) if folds else 0.0
        ),
        stress_profit_factor=stress_pf,
        stress_expectancy=stress_expectancy,
        gross_profit_by_symbol=gross_profit_by_symbol,
        stress_realized_pnls=stress_pnls,
        holdout_evaluations=holdout_evaluations,
    )


def _mark_parameter_plateaus(trials: list[TrialResult]) -> list[TrialResult]:
    robust_ids: set[str] = set()
    for trial in trials:
        neighbors = [
            candidate
            for candidate in trials
            if candidate.strategy_name == trial.strategy_name
            and _hamming_distance(trial.parameters, candidate.parameters) <= 1
            and candidate.expectancy > 0
            and candidate.profit_factor > 1.05
        ]
        if len(neighbors) >= 5:
            robust_ids.add(trial.trial_id)
    return [
        replace(trial, neighborhood_robust=trial.trial_id in robust_ids) for trial in trials
    ]


def _evaluate_holdout(
    trial: TrialResult,
    spec: StrategySpec,
    bars: list[Bar],
    experiment: ExperimentSpec,
    base_config: EngineConfig,
    engine_factory: ResearchEngineFactory,
) -> TrialResult:
    holdout_start = _add_months(_month_floor(experiment.end), -experiment.outer_holdout_months + 1)
    embargo = _embargo_duration(bars, spec, experiment.embargo_bars)
    train_end = holdout_start - embargo
    train_start = _add_months(holdout_start, -experiment.train_months)
    train = [bar for bar in bars if train_start <= bar.timestamp < train_end]
    holdout = [bar for bar in bars if holdout_start <= bar.timestamp <= experiment.end]
    if not train or not holdout:
        return replace(trial, holdout_evaluations=1)
    base_cost, stress_cost = _costs_for(spec.market, experiment)
    config = replace(base_config, timeframe=spec.timeframe)
    base_engine = engine_factory(config, (strategy_from_spec(spec),), base_cost)
    base_result = base_engine.run(holdout, warmup=train)
    stress_result = engine_factory(config, (strategy_from_spec(spec),), stress_cost).run(
        holdout, warmup=train
    )
    combined_stress_pnls = (*trial.stress_realized_pnls, *stress_result.realized_pnls)
    stress_proxy = replace(stress_result, realized_pnls=combined_stress_pnls)
    rebuilt = _build_trial(
        spec,
        (*trial.fold_results, base_result),
        (stress_proxy,),
        trials=1,
        holdout_evaluations=1,
        gross_profit_by_symbol=_merge_gross_profit(
            trial.gross_profit_by_symbol,
            tuple(
                (trade.symbol, trade.pnl)
                for trade in base_engine.broker.trades
                if trade.pnl > 0
            ),
        ),
    )
    return replace(
        rebuilt,
        probability_backtest_overfitting=trial.probability_backtest_overfitting,
        deflated_sharpe_probability=trial.deflated_sharpe_probability,
        neighborhood_robust=trial.neighborhood_robust,
    )


def _candidate_bars(bars: list[Bar], spec: StrategySpec) -> list[Bar]:
    return [bar for bar in bars if bar.market == spec.market]


def _costs_for(
    market: Market, experiment: ExperimentSpec
) -> tuple[CostScenario, CostScenario]:
    scenarios = [scenario for scenario in experiment.cost_scenarios if scenario.market == market]
    base = next((scenario for scenario in scenarios if "base" in scenario.name), None)
    stress = next((scenario for scenario in scenarios if "stress" in scenario.name), None)
    return base or (EQUITY_BASE if market == Market.EQUITY else CRYPTO_BASE), stress or (
        EQUITY_STRESS if market == Market.EQUITY else CRYPTO_STRESS
    )


def _profit_factor(pnls: tuple[float, ...]) -> float:
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _trial_id(spec: StrategySpec) -> str:
    payload = json.dumps(spec.to_dict(), sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _hamming_distance(
    left: tuple[tuple[str, object], ...], right: tuple[tuple[str, object], ...]
) -> int:
    left_dict = dict(left)
    right_dict = dict(right)
    keys = left_dict.keys() | right_dict.keys()
    return sum(left_dict.get(key) != right_dict.get(key) for key in keys)


def _merge_gross_profit(
    *groups: tuple[tuple[str, float], ...]
) -> tuple[tuple[str, float], ...]:
    merged: dict[str, float] = {}
    for group in groups:
        for symbol, value in group:
            merged[symbol] = merged.get(symbol, 0.0) + value
    return tuple(sorted(merged.items()))


def _month_floor(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    year, month = divmod(absolute, 12)
    return value.replace(year=year, month=month + 1, day=1)


def _embargo_duration(bars: list[Bar], spec: StrategySpec, embargo_bars: int) -> timedelta:
    if spec.market == Market.CRYPTO:
        return timeframe_duration(spec.timeframe) * embargo_bars
    counts: dict[date, int] = {}
    for bar in bars:
        if bar.market == spec.market and bar.timeframe == spec.timeframe:
            counts[bar.timestamp.date()] = counts.get(bar.timestamp.date(), 0) + 1
    typical_session_bars = max(1, round(median(counts.values()))) if counts else 6
    sessions = math.ceil(embargo_bars / typical_session_bars)
    calendar_days = math.ceil(sessions * 7 / 5)
    return timedelta(days=calendar_days)


__all__ = ["ResearchEngineFactory", "run_nested_experiment", "strategy_from_spec"]
