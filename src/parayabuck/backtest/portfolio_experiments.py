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
    hash_bars,
    outer_holdout_start,
    probability_backtest_overfitting,
)
from parayabuck.backtest.holdout_registry import HoldoutFingerprint, HoldoutRegistry
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
    universe_snapshot_hash: str = ""
    source_provider: str = ""
    adjustment_policy: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    # Preregistered frozen candidate (deterministic trial id). The holdout may
    # only be unlocked for this candidate after its validation gate passes;
    # the top-sorted candidate is never implicitly chosen.
    frozen_candidate_trial_id: str | None = None
    # Annualized yield applied to the average cash ratio (T-bill proxy) so
    # reports separate capital-market return from cash carry.
    cash_rate_annualized: float = 0.0
    # Suspended experiments may never be evaluated: the flag makes a
    # deliberate research pause (e.g. insufficient data) explicit instead of
    # relying on dataset gates alone. Evaluation raises; the research script
    # skips them with a warning.
    suspended: bool = False

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
            universe_snapshot_hash=str(raw.get("universe_snapshot_hash", "")),
            source_provider=str(raw.get("source_provider", "")),
            adjustment_policy=str(raw.get("adjustment_policy", "")),
            metadata=dict(raw.get("metadata", {})),
            frozen_candidate_trial_id=(
                str(raw["frozen_candidate_trial_id"])
                if raw.get("frozen_candidate_trial_id")
                else None
            ),
            cash_rate_annualized=float(raw.get("cash_rate_annualized", 0.0)),
            suspended=bool(raw.get("suspended", False)),
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
            "universe_snapshot_hash": self.universe_snapshot_hash,
            "source_provider": self.source_provider,
            "adjustment_policy": self.adjustment_policy,
            "metadata": self.metadata,
            "frozen_candidate_trial_id": self.frozen_candidate_trial_id,
            "cash_rate_annualized": self.cash_rate_annualized,
            "suspended": self.suspended,
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
    # -- corrected OOS statistics (aggregate over the stitched OOS curve)
    median_fold_sharpe: float = 0.0
    aggregate_oos_sharpe: float = 0.0
    aggregate_oos_sortino: float = 0.0
    aggregate_oos_calmar: float = 0.0
    aggregate_oos_max_drawdown: float = 0.0
    aggregate_oos_return: float = 0.0
    positive_calendar_month_ratio: float = 0.0
    unique_oos_calendar_months: int = 0
    oos_daily_returns: tuple[float, ...] = ()
    oos_monthly_returns: tuple[tuple[str, float], ...] = ()
    rebalance_count: int = 0
    neighborhood_robust: bool = False
    neighborhood_report: tuple[dict[str, object], ...] = ()
    duplicate_series_trial_ids: tuple[str, ...] = ()
    holdout_evaluations: int = 0
    holdout_missing: bool = False
    holdout_result: PortfolioBacktestResult | None = None
    holdout_stress_result: PortfolioBacktestResult | None = None
    holdout_gate_decision: GateDecision | None = None
    cost_sensitivity: tuple[tuple[str, float], ...] = ()
    validation_gate_decision: GateDecision | None = None
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    information_ratio: float = 0.0
    cash_yield: float = 0.0

    @property
    def median_fold_calmar(self) -> float:
        return median(fold.calmar for fold in self.fold_results) if self.fold_results else 0.0


@dataclass(frozen=True)
class PortfolioGate:
    """Per-market production gate over corrected OOS statistics.

    All thresholds are fixed policy, never tuned to observed results. The
    equity gate uses the aggregate (stitched OOS) metrics and unique calendar
    months; the crypto gate counts closed OOS positions. The holdout is
    evaluated strictly separately from validation and the combined gate
    never absorbs holdout results.
    """

    market: Market
    minimum_oos_calendar_months: int = 60
    minimum_oos_positions: int = 100
    minimum_profit_factor: float = 1.15
    minimum_sharpe: float = 0.75
    maximum_drawdown: float = 0.10
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_pbo: float = 0.20
    minimum_positive_fold_ratio: float = 0.60
    minimum_positive_month_ratio: float = 0.60
    minimum_stress_profit_factor: float = 1.00
    maximum_symbol_profit_concentration: float = 0.30
    maximum_single_asset_weight: float = 0.50
    require_parameter_plateau: bool = True
    require_regime_diversity: bool = True

    @classmethod
    def for_market(cls, market: Market) -> PortfolioGate:
        if market == Market.EQUITY:
            return cls(
                market=market,
                minimum_oos_calendar_months=60,
                minimum_oos_positions=0,
                maximum_drawdown=0.10,
                minimum_positive_fold_ratio=0.60,
                minimum_positive_month_ratio=0.60,
                maximum_symbol_profit_concentration=0.30,
            )
        return cls(
            market=market,
            minimum_oos_calendar_months=0,
            minimum_oos_positions=100,
            maximum_drawdown=0.45,
            minimum_positive_fold_ratio=0.60,
            minimum_positive_month_ratio=0.60,
            maximum_symbol_profit_concentration=0.30,
        )

    def evaluate(
        self, trial: PortfolioTrialResult, *, include_holdout_requirements: bool = True
    ) -> GateDecision:
        """Validation gate over corrected OOS statistics.

        ``include_holdout_requirements`` is False for the validation-only
        gate (used before any holdout unlock decision): the holdout checks
        only apply once the holdout has actually been evaluated.
        """
        failures: list[str] = []
        sample_ok = (
            trial.unique_oos_calendar_months >= self.minimum_oos_calendar_months
            if self.market == Market.EQUITY
            else trial.closed_positions >= self.minimum_oos_positions
        )
        if not sample_ok:
            failures.append("insufficient_oos_sample")
        if self.market == Market.EQUITY:
            equity_checks = [
                (trial.profit_factor >= self.minimum_profit_factor, "profit_factor_below_gate"),
                (trial.aggregate_oos_sharpe >= self.minimum_sharpe, "sharpe_below_gate"),
                (
                    trial.aggregate_oos_max_drawdown <= self.maximum_drawdown,
                    "drawdown_above_gate",
                ),
                (
                    trial.deflated_sharpe_probability
                    >= self.minimum_deflated_sharpe_probability,
                    "deflated_sharpe_below_gate",
                ),
                (trial.probability_backtest_overfitting <= self.maximum_pbo, "pbo_above_gate"),
                (
                    trial.positive_fold_ratio >= self.minimum_positive_fold_ratio,
                    "positive_fold_ratio_below_gate",
                ),
                (
                    trial.positive_month_ratio >= self.minimum_positive_month_ratio,
                    "positive_month_ratio_below_gate",
                ),
                (trial.stress_expectancy >= 0, "negative_stress_expectancy"),
                (
                    trial.stress_profit_factor >= self.minimum_stress_profit_factor,
                    "stress_profit_factor_below_gate",
                ),
                (
                    trial.maximum_concentration <= self.maximum_single_asset_weight,
                    "single_asset_concentration",
                ),
            ]
            if include_holdout_requirements:
                equity_checks.extend(
                    (
                        (not trial.holdout_missing, "holdout_missing"),
                        (trial.holdout_evaluations == 1, "holdout_not_evaluated_exactly_once"),
                    )
                )
            if self.require_parameter_plateau:
                equity_checks.append(
                    (trial.neighborhood_robust, "parameter_neighborhood_not_robust")
                )
            if self.require_regime_diversity:
                equity_checks.append((trial.regime_diverse, "insufficient_regime_diversity"))
        else:
            crypto_checks: list[tuple[bool, str]] = [
                (trial.profit_factor >= self.minimum_profit_factor, "profit_factor_below_gate"),
                (trial.aggregate_oos_sharpe >= self.minimum_sharpe, "sharpe_below_gate"),
                (
                    trial.aggregate_oos_max_drawdown <= self.maximum_drawdown,
                    "drawdown_above_gate",
                ),
                (
                    trial.deflated_sharpe_probability
                    >= self.minimum_deflated_sharpe_probability,
                    "deflated_sharpe_below_gate",
                ),
                (trial.probability_backtest_overfitting <= self.maximum_pbo, "pbo_above_gate"),
                (
                    trial.positive_fold_ratio >= self.minimum_positive_fold_ratio,
                    "positive_fold_ratio_below_gate",
                ),
                (
                    trial.positive_month_ratio >= self.minimum_positive_month_ratio,
                    "positive_month_ratio_below_gate",
                ),
                (
                    trial.stress_profit_factor >= self.minimum_stress_profit_factor,
                    "stress_profit_factor_below_gate",
                ),
                (trial.stress_expectancy >= 0, "negative_stress_expectancy"),
                (trial.neighborhood_robust, "parameter_neighborhood_not_robust"),
                (trial.regime_diverse, "insufficient_regime_diversity"),
                (
                    trial.maximum_concentration <= self.maximum_single_asset_weight,
                    "single_asset_concentration",
                ),
            ]
            if include_holdout_requirements:
                crypto_checks.extend(
                    (
                        (not trial.holdout_missing, "holdout_missing"),
                        (trial.holdout_evaluations == 1, "holdout_not_evaluated_exactly_once"),
                    )
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

    def evaluate_holdout(self, trial: PortfolioTrialResult) -> GateDecision:
        """Standalone holdout gate: holdout results never mix with validation.

        A candidate whose holdout fails is rejected even if the combined
        validation+holdout metrics look good.
        """
        if trial.holdout_missing or trial.holdout_result is None:
            return GateDecision(False, ("holdout_missing",))
        holdout = trial.holdout_result
        stress = trial.holdout_stress_result
        stress_pnls = tuple(stress.realized_pnls) if stress is not None else ()
        stress_expectancy = sum(stress_pnls) / len(stress_pnls) if stress_pnls else 0.0
        stress_pf = _profit_factor(stress_pnls)
        checks = (
            (holdout.sharpe >= self.minimum_sharpe, "holdout_sharpe_below_gate"),
            (holdout.max_drawdown <= self.maximum_drawdown, "holdout_drawdown_above_gate"),
            (stress_expectancy >= 0, "holdout_negative_stress_expectancy"),
            (
                stress_pf >= self.minimum_stress_profit_factor,
                "holdout_stress_profit_factor_below_gate",
            ),
        )
        failures = [reason for passed, reason in checks if not passed]
        return GateDecision(not failures, tuple(failures))


def run_portfolio_experiment(
    bars: list[Bar],
    experiment: PortfolioExperimentSpec,
    engine_factory: PortfolioEngineFactory,
    *,
    unlock_holdout: bool = False,
    universe_hash_verifier: Callable[[], None] | None = None,
    holdout_registry: HoldoutRegistry | None = None,
    trial_count_override: int | None = None,
    validation_gate: PortfolioGate | None = None,
) -> tuple[PortfolioTrialResult, ...]:
    """Evaluate portfolio candidates on a single continuous OOS window.

    Methodology (corrected):

    * The candidate is run ONCE over the whole validation window with the
      full pre-validation history as indicator warmup (no embargo purge: the
      strategies do not fit on train data, so trimming the last visible bars
      only destroys feature history) and NO forced month-end liquidation: the
      portfolio is the same continuous book the strategy would actually run.
    * Independent monthly fold metrics (positive fold ratio, per-fold
      diagnostics, PBO return blocks) are partitioned FROM that continuous
      run, so they reflect the real strategy instead of monthly buy/sell
      churn.
    * The holdout is locked unless the preregistered frozen candidate passed
      the validation gate; the top-sorted trial is never implicitly chosen.
    """
    if experiment.suspended:
        raise ValueError(
            f"Experiment {experiment.experiment_id!r} is suspended "
            "(suspended=True); evaluation is forbidden"
        )
    if universe_hash_verifier is not None:
        universe_hash_verifier()
    bounded = [bar for bar in bars if experiment.start <= bar.timestamp <= experiment.end]
    candidates = tuple(
        strategy
        for space in experiment.candidate_spaces
        for strategy in space.iter_strategies()
    )[: experiment.max_parameter_trials]
    if not candidates:
        raise ValueError("Experiment has no concrete strategy candidates")
    market = experiment.candidate_spaces[0].market
    for space in experiment.candidate_spaces:
        for strategy in space.iter_strategies():
            # The manifest's market/timeframe must match what was actually
            # run; a mismatch would silently evaluate the wrong contract.
            if strategy.market != space.market or strategy.timeframe != space.timeframe:
                raise ValueError(
                    f"Candidate strategy {strategy.name!r} does not match its "
                    f"candidate space ({space.market.value}, {space.timeframe})"
                )
    candidate_bars = [bar for bar in bounded if bar.market == market]
    holdout_start = outer_holdout_start(experiment.end, experiment.outer_holdout_months)
    windows = calendar_fold_windows(
        candidate_bars,
        train_months=experiment.train_months,
        test_months=experiment.validation_months,
        outer_holdout_months=experiment.outer_holdout_months,
        embargo=timedelta(0),
        holdout_start=holdout_start,
    )
    if not windows:
        raise ValueError("Experiment has no validation window before the holdout")
    validation_start = windows[0].test_start
    fingerprint: HoldoutFingerprint | None = None
    holdout_bars: list[Bar] = []
    if unlock_holdout:
        if holdout_registry is not None:
            fingerprint = HoldoutFingerprint(
                market=market,
                timeframe=experiment.candidate_spaces[0].timeframe,
                universe_snapshot_hash=experiment.universe_snapshot_hash,
                holdout_start=holdout_start,
                holdout_end=experiment.end,
                adjustment=experiment.adjustment_policy,
                source_provider=experiment.source_provider,
            )
            # Registry preflight MUST precede any access to the holdout bars.
            holdout_registry.preflight(fingerprint, experiment.experiment_id)
        holdout_bars = [
            bar for bar in candidate_bars if holdout_start <= bar.timestamp <= experiment.end
        ]
        if fingerprint is not None:
            fingerprint = fingerprint.with_bar_hash(hash_bars(holdout_bars))
            assert holdout_registry is not None
            holdout_registry.begin_evaluation(fingerprint, experiment.experiment_id)
    base_cost, stress_cost = _costs_for(market, experiment)
    evaluated: list[PortfolioTrialResult] = []
    block_returns: list[list[float]] = []
    oos_bars = [bar for bar in candidate_bars if validation_start <= bar.timestamp < holdout_start]
    warmup = [bar for bar in candidate_bars if bar.timestamp < validation_start]
    if not warmup or not oos_bars:
        raise ValueError("Experiment has no usable validation data")
    benchmark_bars = [bar for bar in oos_bars if bar.symbol == "SPY"]
    for strategy in candidates:
        base_result = engine_factory(strategy, base_cost).run(
            oos_bars,
            warmup=warmup,
            liquidate_at_end=False,
            cash_rate_annualized=experiment.cash_rate_annualized,
        )
        stress_result = engine_factory(strategy, stress_cost).run(
            oos_bars,
            warmup=warmup,
            liquidate_at_end=False,
            cash_rate_annualized=experiment.cash_rate_annualized,
        )
        folds = _partition_monthly_folds(base_result, strategy.timeframe, market)
        stress_folds = _partition_monthly_folds(stress_result, strategy.timeframe, market)
        gross_profit_by_symbol = {
            contribution.symbol: contribution.realized_pnl
            for contribution in base_result.per_symbol
            if contribution.realized_pnl > 0
        }
        trial = _build_portfolio_trial(
            strategy,
            folds,
            stress_folds,
            trials=trial_count_override
            if trial_count_override is not None
            else len(candidates),
            market=market,
            gross_profit_by_symbol=tuple(sorted(gross_profit_by_symbol.items())),
            base_result=base_result,
            stress_result=stress_result,
        )
        evaluated.append(replace(trial, regime_diverse=_regime_diversity(benchmark_bars)))
        block_returns.append([fold.total_return for fold in folds])
    common_width = min((len(row) for row in block_returns), default=0)
    pbo = probability_backtest_overfitting(
        [row[:common_width] for row in block_returns if common_width],
        seed=experiment.seed,
    )
    duplicate_map = _duplicate_return_series(block_returns, evaluated)
    evaluated = [
        replace(
            trial,
            probability_backtest_overfitting=pbo,
            duplicate_series_trial_ids=duplicate_map.get(trial.trial_id, ()),
        )
        for trial in evaluated
    ]
    mechanism_parameter = (
        experiment.candidate_spaces[0].parameter_grid[0][0]
        if experiment.candidate_spaces[0].parameter_grid
        else ""
    )
    evaluated = _mark_portfolio_plateaus(evaluated, mechanism_parameter)
    evaluated.sort(
        key=lambda trial: (
            trial.neighborhood_robust,
            trial.median_fold_calmar,
            trial.expectancy,
        ),
        reverse=True,
    )
    validation_gate = validation_gate or PortfolioGate.for_market(market)
    evaluated = [
        replace(
            trial,
            validation_gate_decision=validation_gate.evaluate(
                trial, include_holdout_requirements=False
            ),
        )
        for trial in evaluated
    ]
    holdout_attempted = fingerprint is not None
    holdout_missing_any = False
    if unlock_holdout:
        frozen_id = experiment.frozen_candidate_trial_id
        if not frozen_id:
            raise ValueError(
                "unlock_holdout requires a preregistered frozen_candidate_trial_id"
            )
        if experiment.max_holdout_finalists != 1:
            raise ValueError(
                "unlock_holdout requires max_holdout_finalists == 1 "
                "(exactly one frozen finalist)"
            )
        frozen_trial = next(
            (trial for trial in evaluated if trial.trial_id == frozen_id), None
        )
        if frozen_trial is None:
            raise ValueError(
                f"frozen_candidate_trial_id {frozen_id!r} was not among the "
                "evaluated candidates"
            )
        frozen_decision = frozen_trial.validation_gate_decision
        if frozen_decision is None or not frozen_decision.passed:
            raise ValueError(
                "Frozen candidate failed the validation gate; the holdout "
                "stays locked: " + ", ".join(frozen_decision.failures if frozen_decision else ())
            )
        frozen_strategy = next(
            (item for item in candidates if trial_id_for(item) == frozen_id), None
        )
        if frozen_strategy is None:
            raise ValueError(f"No strategy matches frozen candidate {frozen_id!r}")
        try:
            finalist_trial = _evaluate_portfolio_holdout(
                frozen_trial,
                frozen_strategy,
                candidate_bars,
                experiment,
                holdout_start,
                base_cost,
                stress_cost,
                engine_factory,
                holdout_bars=holdout_bars,
            )
        except Exception as exc:
            if fingerprint is not None and holdout_registry is not None:
                holdout_registry.fail_evaluation(
                    fingerprint,
                    experiment.experiment_id,
                    f"holdout_evaluation_error: {exc}",
                )
            raise
        holdout_missing_any = finalist_trial.holdout_missing
        evaluated = [
            finalist_trial if trial.trial_id == frozen_id else trial for trial in evaluated
        ]
    if holdout_attempted:
        assert holdout_registry is not None
        assert fingerprint is not None
        if holdout_missing_any:
            holdout_registry.fail_evaluation(
                fingerprint,
                experiment.experiment_id,
                "holdout_bars_missing; dataset does not cover the locked window",
            )
        else:
            holdout_registry.complete_evaluation(
                fingerprint,
                experiment.experiment_id,
                hash_bars(bars),
            )
    holdout_gate = validation_gate
    evaluated = [
        replace(
            trial,
            holdout_gate_decision=(
                holdout_gate.evaluate_holdout(trial)
                if trial.holdout_result is not None and not trial.holdout_missing
                else None
            ),
        )
        for trial in evaluated
    ]
    return tuple(evaluated)


def _stitched_oos_series(
    folds: tuple[PortfolioBacktestResult, ...],
    starting_equity: float = 100_000.0,
) -> tuple[list[float], list[datetime], list[float]]:
    """Chronological stitched OOS equity from normalized fold return series.

    Every validation fold restarts at its own starting equity; chaining raw
    nominal fold levels would inject fake jumps at fold boundaries. Instead
    each fold's per-bar returns are chained multiplicatively onto the running
    stitched curve, so fold two continues from fold one's end value.
    """
    curve: list[float] = [starting_equity]
    timestamps: list[datetime] = []
    returns: list[float] = []
    for fold in folds:
        previous_value = starting_equity
        for timestamp, equity in zip(
            fold.curve_timestamps, fold.equity_curve, strict=True
        ):
            if previous_value > 0:
                returns.append(equity / previous_value - 1)
            curve.append(curve[-1] * (equity / previous_value) if previous_value > 0 else 1.0)
            timestamps.append(timestamp)
            previous_value = equity
    return curve, timestamps, returns


def _monthly_from_curve(
    curve: list[float], timestamps: list[datetime]
) -> tuple[dict[tuple[int, int], float], tuple[tuple[str, float], ...]]:
    """Monthly returns from an equity curve.

    Supports both the stitched shape (curve has one leading starting value,
    timestamps are per-return) and the direct continuous shape (curve and
    timestamps are aligned 1:1).
    """
    if len(curve) == len(timestamps) + 1:
        leading = curve[0]
        points = [(timestamp, curve[index + 1]) for index, timestamp in enumerate(timestamps)]
    else:
        leading = curve[0] if curve else 0.0
        points = [(timestamp, curve[index]) for index, timestamp in enumerate(timestamps)]
    last_index_by_month: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        last_index_by_month[(timestamp.year, timestamp.month)] = value
    monthly: dict[tuple[int, int], float] = {}
    previous = leading
    for month_key in sorted(last_index_by_month):
        value = last_index_by_month[month_key]
        monthly[month_key] = value / previous - 1 if previous > 0 else 0.0
        previous = value
    serialized = tuple(
        (f"{year:04d}-{month:02d}", monthly[month_key])
        for month_key in sorted(monthly)
        for year, month in [month_key]
    )
    return monthly, serialized


def _annualizer_for(market: Market, timeframe: str) -> float:
    if market == Market.EQUITY:
        return math.sqrt(252)
    minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
    return math.sqrt(365 * 24 * 60 / max(1, minutes))


def _partition_monthly_folds(
    result: PortfolioBacktestResult, timeframe: str, market: Market
) -> tuple[PortfolioBacktestResult, ...]:
    """Independent monthly fold diagnostics from ONE continuous OOS run.

    Each month is a fold view of the same continuous portfolio: there is no
    forced month-end liquidation, so turnover, costs, expectancy and PnL
    reflect the real strategy semantics. Used for fold-level diagnostics and
    PBO return blocks only; aggregate metrics come from the continuous run.
    """
    buckets: dict[tuple[int, int], list[int]] = {}
    for index, timestamp in enumerate(result.curve_timestamps):
        buckets.setdefault((timestamp.year, timestamp.month), []).append(index)
    annualizer = _annualizer_for(market, timeframe)
    folds: list[PortfolioBacktestResult] = []
    for (year, month) in sorted(buckets):
        indices = buckets[(year, month)]
        # Pure intra-month partition of the continuous curve: the fold's
        # first bar IS its starting equity. No boundary value is prepended,
        # so folds never contain phantom zero returns from duplicated
        # boundary points.
        month_curve = [result.equity_curve[index] for index in indices]
        month_ts = [result.curve_timestamps[index] for index in indices]
        total_return = month_curve[-1] / month_curve[0] - 1 if month_curve[0] > 0 else 0.0
        returns = [
            month_curve[index] / month_curve[index - 1] - 1
            for index in range(1, len(month_curve))
            if month_curve[index - 1] > 0
        ]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        std = _stddev(returns)
        downside = [value for value in returns if value < 0]
        downside_std = _stddev(downside)
        peak = month_curve[0]
        max_drawdown = 0.0
        for equity in month_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        days = int((month_ts[-1] - month_ts[0]).total_seconds() / 86_400)
        years = max(days, 1) / 365.0
        cagr = (
            ((month_curve[-1] / month_curve[0]) ** (1 / years) - 1)
            if month_curve[0] > 0 and month_curve[-1] > 0
            else 0.0
        )
        sharpe = mean_return / std * annualizer if std > 0 else 0.0
        sortino = mean_return / downside_std * annualizer if downside_std > 0 else 0.0
        rebalances = tuple(
            record
            for record in result.rebalances
            if (record.decision_timestamp.year, record.decision_timestamp.month)
            == (year, month)
        )
        events = tuple(
            event
            for event in result.realized_pnl_events
            if (event[0].year, event[0].month) == (year, month)
        )
        folds.append(
            PortfolioBacktestResult(
                starting_equity=month_curve[0],
                ending_equity=month_curve[-1],
                total_return=total_return,
                benchmark_return=0.0,
                excess_return=0.0,
                gross_return=total_return,
                net_return=total_return,
                max_drawdown=max_drawdown,
                max_drawdown_duration_days=0,
                cagr=cagr,
                annualized_volatility=std * annualizer if std > 0 else 0.0,
                sharpe=sharpe,
                sortino=sortino,
                calmar=cagr / max_drawdown if max_drawdown > 0 else 0.0,
                duration_days=days,
                start_date=month_ts[0].isoformat(),
                end_date=month_ts[-1].isoformat(),
                equity_curve=tuple(month_curve),
                curve_timestamps=tuple(month_ts),
                realized_pnls=tuple(event[2] for event in events),
                realized_pnl_events=events,
                rebalances=rebalances,
                fills=(),
                weight_history=(),
                universe_history=(),
                benchmark_bars=(),
                benchmark_symbol=result.benchmark_symbol,
                slippage_cost_total=0.0,
                spread_cost_total=0.0,
                fee_total=0.0,
                turnover=0.0,
                average_holdings_count=0.0,
                average_cash_ratio=0.0,
                rebalance_count=len(rebalances),
                closed_positions=len(events),
                per_symbol=(),
                maximum_concentration=0.0,
                worst_month_return=0.0,
                worst_year_return=0.0,
                positive_month_ratio=0.0,
                state=result.state,
            )
        )
    return tuple(folds)


def _build_portfolio_trial(
    strategy: PortfolioStrategy,
    folds: tuple[PortfolioBacktestResult, ...],
    stress_folds: tuple[PortfolioBacktestResult, ...],
    *,
    trials: int,
    market: Market,
    holdout_evaluations: int = 0,
    gross_profit_by_symbol: tuple[tuple[str, float], ...] = (),
    base_result: PortfolioBacktestResult | None = None,
    stress_result: PortfolioBacktestResult | None = None,
) -> PortfolioTrialResult:
    pnls = tuple(pnl for fold in folds for pnl in fold.realized_pnls)
    stress_pnls = tuple(pnl for fold in stress_folds for pnl in fold.realized_pnls)
    profit_factor = _profit_factor(pnls)
    stress_pf = _profit_factor(stress_pnls)
    median_fold_sharpe = median(fold.sharpe for fold in folds) if folds else 0.0
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0
    stress_expectancy = sum(stress_pnls) / len(stress_pnls) if stress_pnls else 0.0
    if base_result is not None and stress_result is not None:
        # Continuous-OOS path: the aggregate curve IS the single continuous
        # run; the monthly folds are diagnostics partitioned from it.
        curve = list(base_result.equity_curve)
        timestamps = list(base_result.curve_timestamps)
        daily_returns = [
            curve[i] / curve[i - 1] - 1
            for i in range(1, len(curve))
            if curve[i - 1] > 0
        ]
        aggregate_return = base_result.total_return
        max_drawdown = base_result.max_drawdown
        start_ts = base_result.curve_timestamps[0] if base_result.curve_timestamps else None
        end_ts = base_result.curve_timestamps[-1] if base_result.curve_timestamps else None
        duration_days = base_result.duration_days
        annualizer = _annualizer_for(market, strategy.timeframe)
        mean_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
        std = _stddev(daily_returns)
        downside = [value for value in daily_returns if value < 0]
        downside_std = _stddev(downside)
        aggregate_sharpe = mean_return / std * annualizer if std > 0 else 0.0
        aggregate_sortino = mean_return / downside_std * annualizer if downside_std > 0 else 0.0
        years = duration_days / 365.0
        cagr = (
            ((curve[-1] / curve[0]) ** (1 / years) - 1)
            if years > 0 and curve and curve[0] > 0 and curve[-1] > 0
            else 0.0
        )
        aggregate_calmar = cagr / max_drawdown if max_drawdown > 0 else 0.0
        rebalance_count = base_result.rebalance_count
        closed_positions = base_result.closed_positions
        maximum_concentration = base_result.maximum_concentration
    else:
        # Legacy stitching path (kept for direct unit tests).
        curve, timestamps, daily_returns = _stitched_oos_series(folds)
        aggregate_return = curve[-1] / curve[0] - 1 if curve and curve[0] > 0 else 0.0
        mean_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
        std = _stddev(daily_returns)
        downside = [value for value in daily_returns if value < 0]
        downside_std = _stddev(downside)
        peak = curve[0]
        max_drawdown = 0.0
        for equity in curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        start_ts = timestamps[0] if timestamps else None
        end_ts = timestamps[-1] if timestamps else None
        duration_days = (
            int((end_ts - start_ts).total_seconds() / 86_400) if start_ts and end_ts else 0
        )
        years = duration_days / 365.0
        annualizer = _annualizer_for(market, strategy.timeframe)
        aggregate_sharpe = mean_return / std * annualizer if std > 0 else 0.0
        aggregate_sortino = mean_return / downside_std * annualizer if downside_std > 0 else 0.0
        cagr = (
            ((curve[-1] / curve[0]) ** (1 / years) - 1)
            if years > 0 and curve and curve[0] > 0 and curve[-1] > 0
            else 0.0
        )
        aggregate_calmar = cagr / max_drawdown if max_drawdown > 0 else 0.0
        rebalance_count = sum(fold.rebalance_count for fold in folds)
        closed_positions = sum(fold.closed_positions for fold in folds)
        maximum_concentration = max(
            (fold.maximum_concentration for fold in folds), default=0.0
        )
    monthly, monthly_serialized = _monthly_from_curve(curve, timestamps)
    unique_months = len(monthly)
    # DSR uses the actual chronological OOS return series: skewness/kurtosis
    # come from the series itself, the observation count is the real number of
    # trading-day (equity) or hourly (crypto) returns, and the observed Sharpe
    # is the non-annualized one, consistent with Bailey/de Prado.
    observed_sharpe = mean_return / std if std > 0 else 0.0
    skewness = _skewness(daily_returns) if len(daily_returns) >= 8 else 0.0
    kurtosis = _kurtosis(daily_returns) if len(daily_returns) >= 8 else 3.0
    positive_fold_ratio = (
        sum(fold.total_return > 0 for fold in folds) / len(folds) if folds else 0.0
    )
    positive_month_ratio = (
        sum(1 for value in monthly.values() if value > 0) / len(monthly) if monthly else 0.0
    )
    return PortfolioTrialResult(
        trial_id=trial_id_for(strategy),
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        parameters=strategy.parameter_tuple(),
        fold_results=folds,
        market=market,
        total_rebalance_months=rebalance_count,
        closed_positions=closed_positions,
        profit_factor=profit_factor,
        sharpe=median_fold_sharpe,
        calmar=median(fold.calmar for fold in folds) if folds else 0.0,
        expectancy=expectancy,
        deflated_sharpe_probability=deflated_sharpe_probability(
            observed_sharpe,
            len(daily_returns),
            trials,
            skewness=skewness,
            kurtosis=kurtosis,
        ),
        probability_backtest_overfitting=0.0,
        max_drawdown=max_drawdown,
        positive_fold_ratio=positive_fold_ratio,
        positive_month_ratio=positive_month_ratio,
        stress_profit_factor=stress_pf,
        stress_expectancy=stress_expectancy,
        stress_realized_pnls=stress_pnls,
        gross_profit_by_symbol=gross_profit_by_symbol,
        maximum_concentration=maximum_concentration,
        regime_diverse=False,
        median_fold_sharpe=median_fold_sharpe,
        aggregate_oos_sharpe=aggregate_sharpe,
        aggregate_oos_sortino=aggregate_sortino,
        aggregate_oos_calmar=aggregate_calmar,
        aggregate_oos_max_drawdown=max_drawdown,
        aggregate_oos_return=aggregate_return,
        positive_calendar_month_ratio=positive_month_ratio,
        unique_oos_calendar_months=unique_months,
        oos_daily_returns=tuple(daily_returns),
        oos_monthly_returns=monthly_serialized,
        rebalance_count=rebalance_count,
        holdout_evaluations=holdout_evaluations,
        cost_sensitivity=(
            ("base_expectancy", expectancy),
            ("stress_expectancy", stress_expectancy),
        ),
        benchmark_return=base_result.benchmark_return if base_result is not None else 0.0,
        excess_return=base_result.excess_return if base_result is not None else 0.0,
        information_ratio=(
            base_result.information_ratio if base_result is not None else 0.0
        ),
        cash_yield=base_result.cash_yield if base_result is not None else 0.0,
    )


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _skewness(values: list[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    if variance <= 0:
        return 0.0
    std = math.sqrt(variance)
    return sum((value - mean) ** 3 for value in values) / (len(values) * std**3)


def _kurtosis(values: list[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    if variance <= 0:
        return 3.0
    std = math.sqrt(variance)
    return sum((value - mean) ** 4 for value in values) / (len(values) * std**4)


def _evaluate_portfolio_holdout(
    trial: PortfolioTrialResult,
    strategy: PortfolioStrategy,
    bars: list[Bar],
    experiment: PortfolioExperimentSpec,
    holdout_start: datetime,
    base_cost: CostScenario,
    stress_cost: CostScenario,
    engine_factory: PortfolioEngineFactory,
    holdout_bars: list[Bar] | None = None,
) -> PortfolioTrialResult:
    # Warmup is the FULL pre-holdout history: indicators need every visible
    # bar, there is no model fitting that would require a purge/embargo.
    warmup = [bar for bar in bars if bar.timestamp < holdout_start]
    holdout = holdout_bars if holdout_bars is not None else [
        bar for bar in bars if holdout_start <= bar.timestamp <= experiment.end
    ]
    if not warmup or not holdout:
        return replace(trial, holdout_evaluations=1, holdout_missing=True)
    base_result = engine_factory(strategy, base_cost).run(
        holdout,
        warmup=warmup,
        liquidate_at_end=False,
        cash_rate_annualized=experiment.cash_rate_annualized,
    )
    stress_result = engine_factory(strategy, stress_cost).run(
        holdout,
        warmup=warmup,
        liquidate_at_end=False,
        cash_rate_annualized=experiment.cash_rate_annualized,
    )
    # Holdout results are kept strictly separate from validation metrics:
    # validation fold metrics and gate decisions never absorb holdout data.
    return replace(
        trial,
        holdout_evaluations=1,
        holdout_missing=False,
        holdout_result=base_result,
        holdout_stress_result=stress_result,
    )


def _duplicate_return_series(
    block_returns: list[list[float]], trials: list[PortfolioTrialResult]
) -> dict[str, tuple[str, ...]]:
    """Map each trial to trial ids producing the identical economic return series.

    Candidates whose fold returns are numerically identical are economically
    duplicates; they must be reported so the multiple-testing count is
    transparent instead of silently inflating the trial count.
    """
    matches: dict[str, list[str]] = {}
    for index, trial in enumerate(trials):
        for other_index, other in enumerate(trials):
            if index == other_index:
                continue
            if len(block_returns[index]) == len(block_returns[other_index]) and all(
                abs(left - right) <= 1e-12
                for left, right in zip(
                    block_returns[index], block_returns[other_index], strict=True
                )
            ):
                matches.setdefault(trial.trial_id, []).append(other.trial_id)
    return {trial_id: tuple(duplicates) for trial_id, duplicates in matches.items()}


def _mark_portfolio_plateaus(
    trials: list[PortfolioTrialResult], mechanism_parameter: str
) -> list[PortfolioTrialResult]:
    """Mark candidates with a real parameter plateau.

    A candidate's neighborhood is made of OTHER candidates (never itself)
    within Hamming distance 1. The plateau passes only when at least five
    real neighbors satisfy positive expectancy and profit factor > 1.05; the
    neighborhood detail is reported per candidate so the whole grid can never
    silently count as a plateau.
    """
    robust_ids: set[str] = set()
    reports: dict[str, list[dict[str, object]]] = {}
    for trial in trials:
        qualifying: list[dict[str, object]] = []
        for candidate in trials:
            if candidate.trial_id == trial.trial_id:
                continue
            distance = _hamming_distance(trial.parameters, candidate.parameters)
            if distance > 1:
                continue
            if candidate.expectancy <= 0 or candidate.profit_factor <= 1.05:
                continue
            mechanism_difference = False
            if mechanism_parameter:
                mechanism_difference = (
                    dict(candidate.parameters).get(mechanism_parameter)
                    != dict(trial.parameters).get(mechanism_parameter)
                )
            qualifying.append(
                {
                    "trial_id": candidate.trial_id,
                    "parameters": dict(candidate.parameters),
                    "hamming_distance": distance,
                    "expectancy": candidate.expectancy,
                    "profit_factor": candidate.profit_factor,
                    "mechanism_difference": mechanism_difference,
                }
            )
        if len(qualifying) >= 5:
            robust_ids.add(trial.trial_id)
        reports[trial.trial_id] = qualifying
    return [
        replace(
            trial,
            neighborhood_robust=trial.trial_id in robust_ids,
            neighborhood_report=tuple(reports[trial.trial_id]),
        )
        for trial in trials
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


def _profit_factor(pnls: tuple[float, ...]) -> float:
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def trial_id_for(strategy: PortfolioStrategy) -> str:
    """Deterministic trial id: hash of strategy identity + parameters.

    Two runs over the same strategy parameters always produce the same trial
    id, which is what makes the preregistered frozen candidate enforceable.
    """
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
