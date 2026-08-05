from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from parayabuck.backtest.experiments import (
    CRYPTO_BASE,
    CRYPTO_STRESS,
    EQUITY_BASE,
    EQUITY_STRESS,
    CostScenario,
    GateDecision,
    deflated_sharpe_probability,
    outer_holdout_start,
    probability_backtest_overfitting,
)
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.validation import calendar_fold_windows
from parayabuck.core.models import Bar, Market
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioStrategy

PortfolioEngineFactory = Callable[
    [PortfolioStrategy, CostScenario], PortfolioBacktestEngine
]


@dataclass(frozen=True)
class PortfolioCandidateSpace:
    """Small, pre-declared parameter grid for one portfolio strategy."""

    name: str
    version: str
    market: Market
    timeframe: str
    parameter_grid: tuple[tuple[str, tuple[object, ...]], ...]

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip() or not self.timeframe.strip():
            raise ValueError("Candidate strategy identity is required")
        if any(not values for _, values in self.parameter_grid):
            raise ValueError("Candidate parameter values cannot be empty")

    @property
    def combinations(self) -> int:
        total = 1
        for _, values in self.parameter_grid:
            total *= len(values)
        return total

    def iter_strategies(self) -> tuple[PortfolioStrategy, ...]:
        names = tuple(name for name, _ in self.parameter_grid)
        value_sets = tuple(values for _, values in self.parameter_grid)
        products = itertools.product(*value_sets) if value_sets else ((),)
        strategies: list[PortfolioStrategy] = []
        for values in products:
            parameters = dict(zip(names, values, strict=True))
            strategies.append(portfolio_strategy_from(self.name, parameters))
        return tuple(strategies)


@dataclass(frozen=True)
class PortfolioExperimentSpec:
    experiment_id: str
    start: datetime
    end: datetime
    universe: tuple[str, ...]
    universe_version: str
    candidate_spaces: tuple[PortfolioCandidateSpace, ...] = ()
    train_months: int = 9
    validation_months: int = 1
    outer_holdout_months: int = 6
    embargo_bars: int = 5
    max_mechanism_trials: int = 30
    max_parameter_trials: int = 200
    max_holdout_finalists: int = 10
    cost_scenarios: tuple[CostScenario, ...] = (
        EQUITY_BASE,
        EQUITY_STRESS,
        CRYPTO_BASE,
        CRYPTO_STRESS,
    )
    seed: int = 7
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Experiment dates must be timezone-aware and increasing")
        if not self.experiment_id.strip() or not self.universe_version.strip():
            raise ValueError("Experiment and universe versions are required")
        if not self.universe:
            raise ValueError("Experiment universe cannot be empty")
        if not self.candidate_spaces:
            raise ValueError("Portfolio experiments require at least one candidate space")
        positive = (
            self.train_months,
            self.validation_months,
            self.outer_holdout_months,
            self.embargo_bars,
            self.max_mechanism_trials,
            self.max_parameter_trials,
            self.max_holdout_finalists,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Experiment fold sizes and budgets must be positive")
        if self.max_holdout_finalists > self.max_parameter_trials:
            raise ValueError("Holdout finalist budget cannot exceed parameter budget")
        # The first grid dimension is the mechanism/ablation dimension; the
        # remaining dimensions are parameter candidates within each mechanism.
        mechanism_trials = sum(
            len(space.parameter_grid[0][1]) if space.parameter_grid else 1
            for space in self.candidate_spaces
        )
        if mechanism_trials > self.max_mechanism_trials:
            raise ValueError("Candidate space exceeds the declared mechanism-trial budget")
        if sum(space.combinations for space in self.candidate_spaces) > self.max_parameter_trials:
            raise ValueError("Candidate space exceeds the declared parameter-trial budget")
        markets = {space.market for space in self.candidate_spaces}
        if len(markets) != 1:
            raise ValueError("Portfolio experiments require one shared market")

    @classmethod
    def from_file(cls, path: str | Path) -> PortfolioExperimentSpec:
        source = Path(path)
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Experiment spec must be a mapping")
        costs = tuple(
            CostScenario(
                name=str(item["name"]),
                market=Market(str(item["market"])),
                slippage_bps=float(item["slippage_bps"]),
                spread_bps=float(item["spread_bps"]),
                taker_fee_bps=float(item.get("taker_fee_bps", 0.0)),
            )
            for item in raw.get("cost_scenarios", ())
        )
        spaces = tuple(_candidate_space(item) for item in raw.get("candidate_spaces", ()))
        return cls(
            experiment_id=str(raw["experiment_id"]),
            start=_parse_datetime(raw["start"]),
            end=_parse_datetime(raw["end"]),
            universe=tuple(str(symbol) for symbol in raw["universe"]),
            universe_version=str(raw["universe_version"]),
            candidate_spaces=spaces,
            train_months=int(raw.get("train_months", 9)),
            validation_months=int(raw.get("validation_months", 1)),
            outer_holdout_months=int(raw.get("outer_holdout_months", 6)),
            embargo_bars=int(raw.get("embargo_bars", 5)),
            max_mechanism_trials=int(raw.get("max_mechanism_trials", 30)),
            max_parameter_trials=int(raw.get("max_parameter_trials", 200)),
            max_holdout_finalists=int(raw.get("max_holdout_finalists", 10)),
            cost_scenarios=costs or (EQUITY_BASE, EQUITY_STRESS, CRYPTO_BASE, CRYPTO_STRESS),
            seed=int(raw.get("seed", 7)),
            metadata=dict(raw.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "universe": list(self.universe),
            "universe_version": self.universe_version,
            "candidate_spaces": [
                {
                    "name": space.name,
                    "version": space.version,
                    "market": space.market.value,
                    "timeframe": space.timeframe,
                    "parameter_grid": {
                        name: list(values) for name, values in space.parameter_grid
                    },
                    "combinations": space.combinations,
                }
                for space in self.candidate_spaces
            ],
            "train_months": self.train_months,
            "validation_months": self.validation_months,
            "outer_holdout_months": self.outer_holdout_months,
            "embargo_bars": self.embargo_bars,
            "max_mechanism_trials": self.max_mechanism_trials,
            "max_parameter_trials": self.max_parameter_trials,
            "max_holdout_finalists": self.max_holdout_finalists,
            "cost_scenarios": [asdict(scenario) for scenario in self.cost_scenarios],
            "seed": self.seed,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PortfolioTrialResult:
    trial_id: str
    strategy_name: str
    strategy_version: str
    parameters: tuple[tuple[str, object], ...]
    fold_results: tuple[PortfolioBacktestResult, ...]
    market: Market
    total_rebalance_months: int
    closed_positions: int
    profit_factor: float
    sharpe: float
    calmar: float
    expectancy: float
    deflated_sharpe_probability: float
    probability_backtest_overfitting: float
    max_drawdown: float
    positive_fold_ratio: float
    positive_month_ratio: float
    stress_profit_factor: float
    stress_expectancy: float
    stress_realized_pnls: tuple[float, ...]
    gross_profit_by_symbol: tuple[tuple[str, float], ...]
    maximum_concentration: float
    regime_diverse: bool
    neighborhood_robust: bool = False
    holdout_evaluations: int = 0
    cost_sensitivity: tuple[tuple[str, float], ...] = ()

    @property
    def median_fold_calmar(self) -> float:
        return median(fold.calmar for fold in self.fold_results) if self.fold_results else 0.0


@dataclass(frozen=True)
class PortfolioGate:
    """Per-market production gate. Equity counts OOS rebalance months and
    regime diversity; crypto counts closed OOS positions and requires a
    minimum stress profit factor. Missing data fails the gate closed."""

    market: Market
    minimum_oos_rebalance_months: int = 60
    minimum_oos_positions: int = 100
    minimum_profit_factor: float = 1.15
    minimum_sharpe: float = 0.75
    maximum_drawdown: float = 0.35
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_pbo: float = 0.20
    minimum_positive_fold_ratio: float = 0.60
    minimum_positive_month_ratio: float = 0.60
    minimum_stress_profit_factor: float = 1.00
    maximum_symbol_profit_concentration: float = 0.40
    maximum_single_asset_weight: float = 0.50
    require_parameter_plateau: bool = True
    require_regime_diversity: bool = True

    @classmethod
    def for_market(cls, market: Market) -> PortfolioGate:
        if market == Market.EQUITY:
            return cls(
                market=market,
                minimum_oos_rebalance_months=60,
                minimum_oos_positions=0,
                maximum_drawdown=0.35,
                minimum_positive_month_ratio=0.0,
            )
        return cls(
            market=market,
            minimum_oos_rebalance_months=0,
            minimum_oos_positions=100,
            maximum_drawdown=0.45,
            minimum_positive_month_ratio=0.60,
        )

    def evaluate(self, trial: PortfolioTrialResult) -> GateDecision:
        failures: list[str] = []
        sample_ok = (
            trial.total_rebalance_months >= self.minimum_oos_rebalance_months
            if self.market == Market.EQUITY
            else trial.closed_positions >= self.minimum_oos_positions
        )
        if not sample_ok:
            failures.append("insufficient_oos_sample")
        if self.market == Market.EQUITY:
            equity_checks = (
                (trial.sharpe >= self.minimum_sharpe, "sharpe_below_gate"),
                (trial.max_drawdown <= self.maximum_drawdown, "drawdown_above_gate"),
                (
                    trial.deflated_sharpe_probability
                    >= self.minimum_deflated_sharpe_probability,
                    "deflated_sharpe_below_gate",
                ),
                (trial.probability_backtest_overfitting <= self.maximum_pbo, "pbo_above_gate"),
                (trial.stress_expectancy >= 0, "negative_stress_expectancy"),
                (trial.neighborhood_robust, "parameter_neighborhood_not_robust"),
                (trial.regime_diverse, "insufficient_regime_diversity"),
                (
                    trial.maximum_concentration <= self.maximum_single_asset_weight,
                    "single_asset_concentration",
                ),
                (trial.holdout_evaluations == 1, "holdout_not_evaluated_exactly_once"),
            )
        else:
            crypto_checks = (
                (trial.profit_factor >= self.minimum_profit_factor, "profit_factor_below_gate"),
                (trial.sharpe >= self.minimum_sharpe, "sharpe_below_gate"),
                (trial.max_drawdown <= self.maximum_drawdown, "drawdown_above_gate"),
                (
                    trial.deflated_sharpe_probability
                    >= self.minimum_deflated_sharpe_probability,
                    "deflated_sharpe_below_gate",
                ),
                (trial.probability_backtest_overfitting <= self.maximum_pbo, "pbo_above_gate"),
                (
                    trial.positive_month_ratio >= self.minimum_positive_month_ratio,
                    "positive_month_ratio_below_gate",
                ),
                (
                    trial.stress_profit_factor >= self.minimum_stress_profit_factor,
                    "stress_profit_factor_below_gate",
                ),
                (trial.neighborhood_robust, "parameter_neighborhood_not_robust"),
                (
                    trial.maximum_concentration <= self.maximum_single_asset_weight,
                    "single_asset_concentration",
                ),
                (trial.holdout_evaluations == 1, "holdout_not_evaluated_exactly_once"),
            )
        active_checks = equity_checks if self.market == Market.EQUITY else crypto_checks
        failures.extend(reason for passed, reason in active_checks if not passed)
        gross = [max(0.0, value) for _, value in trial.gross_profit_by_symbol]
        gross_total = sum(gross)
        if (
            gross_total > 0
            and max(gross) / gross_total > self.maximum_symbol_profit_concentration
        ):
            failures.append("single_symbol_profit_concentration")
        return GateDecision(not failures, tuple(failures))


def run_portfolio_experiment(
    bars: list[Bar],
    experiment: PortfolioExperimentSpec,
    engine_factory: PortfolioEngineFactory,
    *,
    unlock_holdout: bool = False,
) -> tuple[PortfolioTrialResult, ...]:
    """Evaluate portfolio candidates on monthly validation folds with a locked
    outer holdout; finalists touch the holdout only once per experiment."""
    bounded = [bar for bar in bars if experiment.start <= bar.timestamp <= experiment.end]
    candidates = tuple(
        strategy
        for space in experiment.candidate_spaces
        for strategy in space.iter_strategies()
    )[: experiment.max_parameter_trials]
    if not candidates:
        raise ValueError("Experiment has no concrete strategy candidates")
    market = experiment.candidate_spaces[0].market
    candidate_bars = [bar for bar in bounded if bar.market == market]
    embargo = _embargo_duration(experiment)
    holdout_start = outer_holdout_start(experiment.end, experiment.outer_holdout_months)
    windows = calendar_fold_windows(
        candidate_bars,
        train_months=experiment.train_months,
        test_months=experiment.validation_months,
        outer_holdout_months=experiment.outer_holdout_months,
        embargo=embargo,
        holdout_start=holdout_start,
    )
    base_cost, stress_cost = _costs_for(market, experiment)
    evaluated: list[PortfolioTrialResult] = []
    block_returns: list[list[float]] = []
    for strategy in candidates:
        fold_results: list[PortfolioBacktestResult] = []
        stress_results: list[PortfolioBacktestResult] = []
        gross_profit_by_symbol: dict[str, float] = {}
        benchmark_bars: list[Bar] = []
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
            result = engine_factory(strategy, base_cost).run(validation, warmup=train)
            fold_results.append(result)
            benchmark_bars.extend(result.benchmark_bars)
            for contribution in result.per_symbol:
                if contribution.realized_pnl > 0:
                    gross_profit_by_symbol[contribution.symbol] = (
                        gross_profit_by_symbol.get(contribution.symbol, 0.0)
                        + contribution.realized_pnl
                    )
            stress_results.append(
                engine_factory(strategy, stress_cost).run(validation, warmup=train)
            )
        trial = _build_portfolio_trial(
            strategy,
            tuple(fold_results),
            tuple(stress_results),
            trials=len(candidates),
            market=market,
            gross_profit_by_symbol=tuple(sorted(gross_profit_by_symbol.items())),
        )
        evaluated.append(replace(trial, regime_diverse=_regime_diversity(benchmark_bars)))
        block_returns.append([fold.total_return for fold in fold_results])
    common_width = min((len(row) for row in block_returns), default=0)
    pbo = probability_backtest_overfitting(
        [row[:common_width] for row in block_returns if common_width]
    )
    evaluated = [replace(trial, probability_backtest_overfitting=pbo) for trial in evaluated]
    evaluated = _mark_portfolio_plateaus(evaluated)
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
        replacements: dict[str, PortfolioTrialResult] = {}
        for trial in finalists:
            finalist_strategy = next(
                (item for item in candidates if _trial_id(item) == trial.trial_id), None
            )
            if finalist_strategy is None:
                continue
            replacements[trial.trial_id] = _evaluate_portfolio_holdout(
                trial,
                finalist_strategy,
                candidate_bars,
                experiment,
                holdout_start,
                embargo,
                base_cost,
                stress_cost,
                engine_factory,
            )
        evaluated = [replacements.get(trial.trial_id, trial) for trial in evaluated]
    return tuple(evaluated)


def _build_portfolio_trial(
    strategy: PortfolioStrategy,
    folds: tuple[PortfolioBacktestResult, ...],
    stress_folds: tuple[PortfolioBacktestResult, ...],
    *,
    trials: int,
    market: Market,
    holdout_evaluations: int = 0,
    gross_profit_by_symbol: tuple[tuple[str, float], ...] = (),
) -> PortfolioTrialResult:
    pnls = tuple(pnl for fold in folds for pnl in fold.realized_pnls)
    stress_pnls = tuple(pnl for fold in stress_folds for pnl in fold.realized_pnls)
    profit_factor = _profit_factor(pnls)
    stress_pf = _profit_factor(stress_pnls)
    sharpe = median(fold.sharpe for fold in folds) if folds else 0.0
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0
    stress_expectancy = sum(stress_pnls) / len(stress_pnls) if stress_pnls else 0.0
    observations = sum(max(1, fold.duration_days) for fold in folds)
    annualizer = math.sqrt(365 if market == Market.CRYPTO else 252)
    return PortfolioTrialResult(
        trial_id=_trial_id(strategy),
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        parameters=strategy.parameter_tuple(),
        fold_results=folds,
        market=market,
        total_rebalance_months=sum(len(fold.weight_history) for fold in folds),
        closed_positions=sum(fold.closed_positions for fold in folds),
        profit_factor=profit_factor,
        sharpe=sharpe,
        calmar=median(fold.calmar for fold in folds) if folds else 0.0,
        expectancy=expectancy,
        deflated_sharpe_probability=deflated_sharpe_probability(
            sharpe / annualizer, observations, trials
        ),
        probability_backtest_overfitting=0.0,
        max_drawdown=max((fold.max_drawdown for fold in folds), default=0.0),
        positive_fold_ratio=(
            sum(fold.total_return > 0 for fold in folds) / len(folds) if folds else 0.0
        ),
        positive_month_ratio=_weighted_positive_month_ratio(folds),
        stress_profit_factor=stress_pf,
        stress_expectancy=stress_expectancy,
        stress_realized_pnls=stress_pnls,
        gross_profit_by_symbol=gross_profit_by_symbol,
        maximum_concentration=max(
            (fold.maximum_concentration for fold in folds), default=0.0
        ),
        regime_diverse=False,
        holdout_evaluations=holdout_evaluations,
        cost_sensitivity=(
            ("base_expectancy", expectancy),
            ("stress_expectancy", stress_expectancy),
        ),
    )


def _weighted_positive_month_ratio(folds: tuple[PortfolioBacktestResult, ...]) -> float:
    """Fraction of calendar months across all folds with a positive return."""
    monthly: dict[tuple[int, int], float] = {}
    for fold in folds:
        for timestamp, equity in zip(
            fold.curve_timestamps, fold.equity_curve, strict=True
        ):
            month_key = (timestamp.year, timestamp.month)
            monthly[month_key] = equity
    previous = folds[0].starting_equity if folds else 0.0
    returns: list[float] = []
    for month_key in sorted(monthly):
        value = monthly[month_key]
        if previous > 0:
            returns.append(value / previous - 1)
        previous = value
    return sum(1 for value in returns if value > 0) / len(returns) if returns else 0.0


def _evaluate_portfolio_holdout(
    trial: PortfolioTrialResult,
    strategy: PortfolioStrategy,
    bars: list[Bar],
    experiment: PortfolioExperimentSpec,
    holdout_start: datetime,
    embargo: timedelta,
    base_cost: CostScenario,
    stress_cost: CostScenario,
    engine_factory: PortfolioEngineFactory,
) -> PortfolioTrialResult:
    train_end = holdout_start - embargo
    train_start = _add_months(holdout_start, -experiment.train_months)
    train = [bar for bar in bars if train_start <= bar.timestamp < train_end]
    holdout = [bar for bar in bars if holdout_start <= bar.timestamp <= experiment.end]
    if not train or not holdout:
        return replace(trial, holdout_evaluations=1)
    base_result = engine_factory(strategy, base_cost).run(holdout, warmup=train)
    stress_result = engine_factory(strategy, stress_cost).run(holdout, warmup=train)
    combined_stress = (*trial.stress_realized_pnls, *stress_result.realized_pnls)
    stress_proxy = replace(stress_result, realized_pnls=combined_stress)
    rebuilt = _build_portfolio_trial(
        strategy,
        (*trial.fold_results, base_result),
        (stress_proxy,),
        trials=1,
        market=trial.market,
        holdout_evaluations=1,
        gross_profit_by_symbol=_merge_gross_profit(
            trial.gross_profit_by_symbol,
            tuple(
                (contribution.symbol, contribution.realized_pnl)
                for contribution in base_result.per_symbol
                if contribution.realized_pnl > 0
            ),
        ),
    )
    return replace(
        rebuilt,
        probability_backtest_overfitting=trial.probability_backtest_overfitting,
        deflated_sharpe_probability=trial.deflated_sharpe_probability,
        neighborhood_robust=trial.neighborhood_robust,
        regime_diverse=trial.regime_diverse,
    )


def _mark_portfolio_plateaus(
    trials: list[PortfolioTrialResult],
) -> list[PortfolioTrialResult]:
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


def _regime_diversity(benchmark_bars: list[Bar]) -> bool:
    """At least 20% negative benchmark months plus at least one 6-month
    benchmark episode below -10% across the evaluation window."""
    if not benchmark_bars:
        return False
    ordered = sorted(benchmark_bars, key=lambda bar: bar.timestamp)
    months: dict[tuple[int, int], list[float]] = {}
    for bar in ordered:
        months.setdefault((bar.timestamp.year, bar.timestamp.month), []).append(bar.close)
    previous: float | None = None
    monthly_returns: list[float] = []
    for month_key in sorted(months):
        closes = months[month_key]
        if previous is not None and previous > 0:
            monthly_returns.append(closes[-1] / previous - 1)
        previous = closes[-1]
    negative_ratio = (
        sum(1 for value in monthly_returns if value < 0) / len(monthly_returns)
        if monthly_returns
        else 0.0
    )
    closes = [bar.close for bar in ordered]
    bear_episode = any(
        window[0] > 0 and window[-1] / window[0] - 1 < -0.10
        for window in (
            closes[index - 126 : index] for index in range(126, len(closes) + 1)
        )
    )
    return negative_ratio >= 0.20 and bear_episode


def _costs_for(
    market: Market, experiment: PortfolioExperimentSpec
) -> tuple[CostScenario, CostScenario]:
    scenarios = [scenario for scenario in experiment.cost_scenarios if scenario.market == market]
    base = next((scenario for scenario in scenarios if "base" in scenario.name), None)
    stress = next((scenario for scenario in scenarios if "stress" in scenario.name), None)
    return base or (EQUITY_BASE if market == Market.EQUITY else CRYPTO_BASE), stress or (
        EQUITY_STRESS if market == Market.EQUITY else CRYPTO_STRESS
    )


def _embargo_duration(experiment: PortfolioExperimentSpec) -> timedelta:
    timeframe = experiment.candidate_spaces[0].timeframe
    minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
    return timedelta(minutes=minutes * experiment.embargo_bars)


def _profit_factor(pnls: tuple[float, ...]) -> float:
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _trial_id(strategy: PortfolioStrategy) -> str:
    payload = json.dumps(
        {
            "name": strategy.name,
            "version": strategy.version,
            "parameters": dict(strategy.parameters),
        },
        sort_keys=True,
        default=str,
    ).encode()
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


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _candidate_space(raw: dict[str, Any]) -> PortfolioCandidateSpace:
    grid_raw = raw.get("parameter_grid", {})
    if not isinstance(grid_raw, dict):
        raise ValueError("Candidate parameter_grid must be a mapping")
    return PortfolioCandidateSpace(
        name=str(raw["name"]),
        version=str(raw.get("version", "1.0.0")),
        market=Market(str(raw["market"])),
        timeframe=str(raw.get("timeframe", "1d")),
        parameter_grid=tuple(
            (str(name), tuple(values if isinstance(values, list) else (values,)))
            for name, values in grid_raw.items()
        ),
    )


def _add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    return value.replace(year=year, month=month_index + 1, day=1)


__all__ = [
    "PortfolioCandidateSpace",
    "PortfolioEngineFactory",
    "PortfolioExperimentSpec",
    "PortfolioGate",
    "PortfolioTrialResult",
    "run_portfolio_experiment",
]
