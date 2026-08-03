from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist, median
from typing import Any, cast

import yaml

from parayabuck.backtest.engine import BacktestResult
from parayabuck.core.models import Bar, ExitPolicy, Market, TrailingStopType
from parayabuck.strategies.specs import (
    CryptoRangeParams,
    CryptoTrendParams,
    StockMeanReversionParams,
    StockTrendParams,
    StrategyParams,
    StrategySpec,
)


@dataclass(frozen=True)
class CostScenario:
    name: str
    market: Market
    slippage_bps: float
    spread_bps: float
    taker_fee_bps: float = 0.0

    def __post_init__(self) -> None:
        values = (self.slippage_bps, self.spread_bps, self.taker_fee_bps)
        if not self.name.strip() or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Invalid cost scenario")
        if self.market == Market.EQUITY and self.taker_fee_bps:
            raise ValueError("Equity scenarios do not use crypto taker fees")


EQUITY_BASE = CostScenario("equity_base", Market.EQUITY, 2.0, 4.0)
EQUITY_STRESS = CostScenario("equity_stress", Market.EQUITY, 5.0, 10.0)
CRYPTO_BASE = CostScenario("crypto_base", Market.CRYPTO, 2.0, 6.0, 5.0)
CRYPTO_STRESS = CostScenario("crypto_stress", Market.CRYPTO, 5.0, 15.0, 10.0)
DEFAULT_COST_SCENARIOS = (EQUITY_BASE, EQUITY_STRESS, CRYPTO_BASE, CRYPTO_STRESS)


@dataclass(frozen=True)
class StrategyCandidateSpace:
    name: str
    version: str
    market: Market
    timeframe: str
    parameter_grid: tuple[tuple[str, tuple[object, ...]], ...]
    exit_policies: tuple[ExitPolicy, ...] = (ExitPolicy(),)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip() or not self.timeframe.strip():
            raise ValueError("Candidate strategy identity is required")
        if any(not values for _, values in self.parameter_grid):
            raise ValueError("Candidate parameter values cannot be empty")
        if not self.exit_policies:
            raise ValueError("At least one exit policy is required")

    @property
    def combinations(self) -> int:
        total = len(self.exit_policies)
        for _, values in self.parameter_grid:
            total *= len(values)
        return total

    def iter_specs(self) -> tuple[StrategySpec, ...]:
        names = tuple(name for name, _ in self.parameter_grid)
        value_sets = tuple(values for _, values in self.parameter_grid)
        products = itertools.product(*value_sets) if value_sets else ((),)
        specs: list[StrategySpec] = []
        for values in products:
            parameters = _strategy_params(self.name, dict(zip(names, values, strict=True)))
            for policy in self.exit_policies:
                specs.append(
                    StrategySpec(
                        self.name,
                        self.version,
                        self.market,
                        self.timeframe,
                        parameters,
                        policy,
                    )
                )
        return tuple(specs)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    start: datetime
    end: datetime
    universe: tuple[str, ...]
    universe_version: str
    strategies: tuple[StrategySpec, ...] = ()
    candidate_spaces: tuple[StrategyCandidateSpace, ...] = ()
    train_months: int = 9
    validation_months: int = 1
    outer_holdout_months: int = 6
    embargo_bars: int = 72
    max_mechanism_trials: int = 30
    max_parameter_trials: int = 200
    max_holdout_finalists: int = 10
    alpha_risk_per_trade_pct: float = 0.0025
    cost_scenarios: tuple[CostScenario, ...] = DEFAULT_COST_SCENARIOS
    seed: int = 7

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Experiment dates must be timezone-aware and increasing")
        if not self.experiment_id.strip() or not self.universe_version.strip():
            raise ValueError("Experiment and universe versions are required")
        if not self.universe:
            raise ValueError("Experiment universe cannot be empty")
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
        if not 0 < self.alpha_risk_per_trade_pct <= 0.005:
            raise ValueError("Alpha research risk must be in (0, 0.5%]")
        if self.max_holdout_finalists > self.max_parameter_trials:
            raise ValueError("Holdout finalist budget cannot exceed parameter budget")
        mechanism_trials = (
            sum(len(space.exit_policies) for space in self.candidate_spaces)
            if self.candidate_spaces
            else len(self.strategies)
        )
        if mechanism_trials > self.max_mechanism_trials:
            raise ValueError("Candidate space exceeds the declared mechanism-trial budget")
        if sum(space.combinations for space in self.candidate_spaces) > self.max_parameter_trials:
            raise ValueError("Candidate space exceeds the declared parameter-trial budget")

    @classmethod
    def from_file(cls, path: str | Path) -> ExperimentSpec:
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
            embargo_bars=int(raw.get("embargo_bars", 72)),
            max_mechanism_trials=int(raw.get("max_mechanism_trials", 30)),
            max_parameter_trials=int(raw.get("max_parameter_trials", 200)),
            max_holdout_finalists=int(raw.get("max_holdout_finalists", 10)),
            alpha_risk_per_trade_pct=float(raw.get("alpha_risk_per_trade_pct", 0.0025)),
            cost_scenarios=costs or DEFAULT_COST_SCENARIOS,
            seed=int(raw.get("seed", 7)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "strategies": [strategy.to_dict() for strategy in self.strategies],
            "candidate_spaces": [asdict(space) for space in self.candidate_spaces],
        }


@dataclass(frozen=True)
class TrialResult:
    trial_id: str
    strategy_name: str
    strategy_version: str
    parameters: tuple[tuple[str, object], ...]
    fold_results: tuple[BacktestResult, ...]
    total_trades: int
    profit_factor: float
    sharpe: float
    calmar: float
    expectancy: float
    deflated_sharpe_probability: float
    probability_backtest_overfitting: float
    cost_sensitivity: tuple[tuple[str, float], ...] = ()
    max_drawdown: float = 0.0
    positive_fold_ratio: float = 0.0
    stress_profit_factor: float = 0.0
    stress_expectancy: float = 0.0
    gross_profit_by_symbol: tuple[tuple[str, float], ...] = ()
    stress_realized_pnls: tuple[float, ...] = ()
    neighborhood_robust: bool = False
    holdout_evaluations: int = 0

    @property
    def median_fold_calmar(self) -> float:
        return median(fold.calmar for fold in self.fold_results) if self.fold_results else 0.0


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class ProductionGate:
    minimum_trades: int = 100
    minimum_profit_factor: float = 1.15
    minimum_sharpe: float = 0.75
    maximum_drawdown: float = 0.10
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_pbo: float = 0.20
    minimum_positive_fold_ratio: float = 0.60
    minimum_stress_profit_factor: float = 1.00
    maximum_symbol_profit_concentration: float = 0.30

    def evaluate(self, trial: TrialResult) -> GateDecision:
        failures: list[str] = []
        checks = (
            (trial.total_trades >= self.minimum_trades, "insufficient_oos_trades"),
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
                trial.positive_fold_ratio >= self.minimum_positive_fold_ratio,
                "positive_fold_ratio_below_gate",
            ),
            (trial.stress_expectancy >= 0, "negative_stress_expectancy"),
            (
                trial.stress_profit_factor >= self.minimum_stress_profit_factor,
                "stress_profit_factor_below_gate",
            ),
            (trial.neighborhood_robust, "parameter_neighborhood_not_robust"),
            (trial.holdout_evaluations == 1, "holdout_not_evaluated_exactly_once"),
        )
        failures.extend(reason for passed, reason in checks if not passed)
        gross = [max(0.0, value) for _, value in trial.gross_profit_by_symbol]
        gross_total = sum(gross)
        if (
            gross_total > 0
            and max(gross) / gross_total > self.maximum_symbol_profit_concentration
        ):
            failures.append("single_symbol_profit_concentration")
        return GateDecision(not failures, tuple(failures))


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    created_at: datetime
    code_revision: str
    data_hash: str
    universe_version: str
    spec: dict[str, object]
    seed: int
    metadata: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return json.dumps(payload, sort_keys=True, indent=2, default=str)


def hash_bars(bars: list[Bar]) -> str:
    digest = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol, item.timeframe)):
        row = (
            bar.timestamp.isoformat(),
            bar.symbol,
            bar.timeframe,
            f"{bar.open:.12g}",
            f"{bar.high:.12g}",
            f"{bar.low:.12g}",
            f"{bar.close:.12g}",
            f"{bar.volume:.12g}",
            bar.market.value if isinstance(bar.market, Market) else str(bar.market),
        )
        digest.update("|".join(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def deflated_sharpe_probability(
    observed_sharpe: float,
    observations: int,
    trials: int,
    *,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Bailey/Lopez de Prado probabilistic Sharpe adjusted for multiple trials."""
    if observations < 2 or trials < 1:
        return 0.0
    standard_error = 1 / math.sqrt(observations - 1)
    if trials == 1:
        expected_max = 0.0
    else:
        normal = NormalDist()
        euler_gamma = 0.5772156649015329
        expected_max = standard_error * (
            (1 - euler_gamma) * normal.inv_cdf(1 - 1 / trials)
            + euler_gamma * normal.inv_cdf(1 - 1 / (trials * math.e))
        )
    denominator = 1 - skewness * observed_sharpe
    denominator += ((kurtosis - 1) / 4) * observed_sharpe**2
    if denominator <= 0:
        return 0.0
    statistic = (observed_sharpe - expected_max) * math.sqrt(observations - 1)
    statistic /= math.sqrt(denominator)
    return NormalDist().cdf(statistic)


def probability_backtest_overfitting(
    trial_block_returns: list[list[float]],
    *,
    maximum_splits: int = 2_000,
) -> float:
    """Estimate CSCV/PBO from trial-by-block returns.

    Rows are parameter trials and columns are chronological, non-overlapping
    return blocks. Odd trailing blocks are ignored because CSCV uses equal
    in-sample and out-of-sample partitions.
    """
    if len(trial_block_returns) < 2 or not trial_block_returns[0]:
        return 0.0
    width = min(len(row) for row in trial_block_returns)
    width -= width % 2
    if width < 2:
        return 0.0
    half = width // 2
    combinations = itertools.combinations(range(width), half)
    overfit = 0
    evaluated = 0
    all_indexes = set(range(width))
    for in_sample_indexes in combinations:
        if evaluated >= maximum_splits:
            break
        in_sample = set(in_sample_indexes)
        out_sample = all_indexes - in_sample
        train_scores = [
            sum(row[index] for index in in_sample) / half for row in trial_block_returns
        ]
        selected = max(range(len(train_scores)), key=train_scores.__getitem__)
        out_scores = [
            sum(row[index] for index in out_sample) / half for row in trial_block_returns
        ]
        selected_score = out_scores[selected]
        rank = sum(score <= selected_score for score in out_scores) / len(out_scores)
        if rank <= 0.5:
            overfit += 1
        evaluated += 1
    return overfit / evaluated if evaluated else 0.0


def new_manifest(
    spec: ExperimentSpec,
    bars: list[Bar],
    *,
    code_revision: str,
    metadata: dict[str, object] | None = None,
) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id=spec.experiment_id,
        created_at=datetime.now(UTC),
        code_revision=code_revision,
        data_hash=hash_bars(bars),
        universe_version=spec.universe_version,
        spec=spec.to_dict(),
        seed=spec.seed,
        metadata=metadata or {},
    )


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _candidate_space(raw: dict[str, Any]) -> StrategyCandidateSpace:
    grid_raw = raw.get("parameter_grid", {})
    if not isinstance(grid_raw, dict):
        raise ValueError("Candidate parameter_grid must be a mapping")
    policies = tuple(_exit_policy(item) for item in raw.get("exit_policies", ({},)))
    return StrategyCandidateSpace(
        name=str(raw["name"]),
        version=str(raw.get("version", "2.0.0")),
        market=Market(str(raw["market"])),
        timeframe=str(raw.get("timeframe", "1h")),
        parameter_grid=tuple(
            (str(name), tuple(values if isinstance(values, list) else (values,)))
            for name, values in grid_raw.items()
        ),
        exit_policies=policies,
    )


def _exit_policy(raw: dict[str, Any]) -> ExitPolicy:
    trailing = TrailingStopType(str(raw.get("trailing_type", "none")))
    return ExitPolicy(
        policy_id=str(raw.get("policy_id", "fixed")),
        initial_stop_atr_multiple=_optional_float(raw.get("initial_stop_atr_multiple")),
        target_r_multiple=_optional_float(raw.get("target_r_multiple")),
        break_even_trigger_r=_optional_float(raw.get("break_even_trigger_r")),
        trailing_type=trailing,
        trailing_atr_multiple=_optional_float(raw.get("trailing_atr_multiple")),
        trailing_atr_period=int(raw.get("trailing_atr_period", 14)),
        trailing_activation_r=_optional_float(raw.get("trailing_activation_r")),
        max_holding_bars=(
            int(raw["max_holding_bars"]) if raw.get("max_holding_bars") is not None else None
        ),
        regime_exit=bool(raw.get("regime_exit", False)),
    )


def _strategy_params(name: str, values: dict[str, object]) -> StrategyParams:
    classes: dict[str, type[Any]] = {
        "stock_trend_pullback": StockTrendParams,
        "stock_mean_reversion": StockMeanReversionParams,
        "crypto_trend_breakout": CryptoTrendParams,
        "crypto_range_reversion": CryptoRangeParams,
    }
    try:
        cls = classes[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported strategy candidate: {name}") from exc
    return cast(StrategyParams, cls(**values))


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


__all__ = [
    "CRYPTO_BASE",
    "CRYPTO_STRESS",
    "DEFAULT_COST_SCENARIOS",
    "EQUITY_BASE",
    "EQUITY_STRESS",
    "CostScenario",
    "ExperimentManifest",
    "ExperimentSpec",
    "GateDecision",
    "ProductionGate",
    "StrategyCandidateSpace",
    "TrialResult",
    "deflated_sharpe_probability",
    "hash_bars",
    "new_manifest",
    "probability_backtest_overfitting",
]
