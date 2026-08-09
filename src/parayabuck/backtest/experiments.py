from __future__ import annotations

import hashlib
import json
import math
import random
from calendar import monthrange
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import NormalDist, median

from parayabuck.backtest.engine import BacktestResult
from parayabuck.core.models import Bar, Market


@dataclass(frozen=True)
class CostScenario:
    name: str
    market: Market
    slippage_bps: float
    spread_bps: float
    taker_fee_bps: float = 0.0
    equity_taf_per_share: float = 0.0
    equity_taf_cap: float = 0.0
    equity_cat_per_share: float = 0.0
    equity_sec_fee_rate: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.slippage_bps,
            self.spread_bps,
            self.taker_fee_bps,
            self.equity_taf_per_share,
            self.equity_taf_cap,
            self.equity_cat_per_share,
            self.equity_sec_fee_rate,
        )
        if not self.name.strip() or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Invalid cost scenario")
        if self.market == Market.EQUITY and self.taker_fee_bps:
            raise ValueError("Equity scenarios do not use crypto taker fees")


EQUITY_BASE = CostScenario(
    "equity_base",
    Market.EQUITY,
    2.0,
    4.0,
    0.0,
    0.000195,
    9.79,
    0.000003,
    0.00002060,
)
EQUITY_STRESS = CostScenario(
    "equity_stress",
    Market.EQUITY,
    5.0,
    10.0,
    0.0,
    0.000195,
    9.79,
    0.000003,
    0.00002060,
)
# Tier-1 Alpaca taker fee (0-100k USD trailing 30-day volume) is 25 bps.
# Stress doubles the fee and widens spread/slippage; a 5 bps base materially
# understated the cost of the intended small paper/live account.
CRYPTO_BASE = CostScenario("crypto_base", Market.CRYPTO, 2.0, 6.0, 25.0)
CRYPTO_STRESS = CostScenario("crypto_stress", Market.CRYPTO, 8.0, 25.0, 50.0)
DEFAULT_COST_SCENARIOS = (EQUITY_BASE, EQUITY_STRESS, CRYPTO_BASE, CRYPTO_STRESS)


def outer_holdout_start(end: datetime, months: int) -> datetime:
    """First timestamp that must remain invisible during research."""
    if end.tzinfo is None or months < 0:
        raise ValueError("Holdout requires a timezone-aware end and non-negative months")
    month_index = end.year * 12 + end.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return end.replace(
        year=year,
        month=month,
        day=min(end.day, monthrange(year, month)[1]),
    )


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
                trial.deflated_sharpe_probability >= self.minimum_deflated_sharpe_probability,
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
        if gross_total > 0 and max(gross) / gross_total > self.maximum_symbol_profit_concentration:
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


def _kth_combination(n: int, k: int, rank: int) -> list[int]:
    """Unrank the ``rank``-th (0-based) k-combination of n elements.

    Combinadic unranking: uniform sampling must never enumerate the whole
    combination space (C(52, 26) is astronomically large), so sampled ranks
    are converted directly into combinations in O(k*n).
    """
    result: list[int] = []
    start = 0
    remaining = rank
    for size in range(k, 0, -1):
        candidate = start
        while candidate < n:
            count = math.comb(n - candidate - 1, size - 1)
            if remaining < count:
                result.append(candidate)
                start = candidate + 1
                break
            remaining -= count
            candidate += 1
    return result


def probability_backtest_overfitting(
    trial_block_returns: list[list[float]],
    *,
    maximum_splits: int = 2_000,
    seed: int = 7,
) -> float:
    """Estimate CSCV/PBO from trial-by-block returns.

    Rows are parameter trials and columns are chronological, non-overlapping
    return blocks. Odd trailing blocks are ignored because CSCV uses equal
    in-sample and out-of-sample partitions.

    The combination space is evaluated exhaustively when manageable; when the
    number of combinations exceeds ``maximum_splits`` a deterministic,
    balanced uniform sample is used instead (rank-based unranking, so the
    sample is drawn from the whole space and never restricted to the first
    lexicographic combinations). Sampling is seeded: the same input and seed
    always reproduce the same PBO.
    """
    if len(trial_block_returns) < 2 or not trial_block_returns[0]:
        return 0.0
    width = min(len(row) for row in trial_block_returns)
    width -= width % 2
    if width < 2:
        return 0.0
    half = width // 2
    total = math.comb(width, half)
    rng = random.Random(seed)
    # A fixed deterministic column permutation removes the lexicographic
    # ordering bias from the sampled combinations.
    permutation = list(range(width))
    rng.shuffle(permutation)
    if total <= maximum_splits:
        ranks: range | list[int] = range(total)
    else:
        # random.sample(range(huge)) overflows on C ssize_t; draw ranks via
        # randrange (arbitrary precision) with set dedup instead.
        sampled: set[int] = set()
        while len(sampled) < maximum_splits:
            sampled.add(rng.randrange(total))
        ranks = sorted(sampled)
    overfit = 0
    evaluated = 0
    all_indexes = set(range(width))
    for rank in ranks:
        in_sample = {permutation[index] for index in _kth_combination(width, half, rank)}
        out_sample = all_indexes - in_sample
        train_scores = [
            sum(row[index] for index in in_sample) / half for row in trial_block_returns
        ]
        selected = max(range(len(train_scores)), key=train_scores.__getitem__)
        out_scores = [sum(row[index] for index in out_sample) / half for row in trial_block_returns]
        selected_score = out_scores[selected]
        rank_value = sum(score <= selected_score for score in out_scores) / len(out_scores)
        if rank_value <= 0.5:
            overfit += 1
        evaluated += 1
    return overfit / evaluated if evaluated else 0.0


def new_manifest(
    *,
    experiment_id: str,
    spec_dict: dict[str, object],
    universe_version: str,
    seed: int,
    bars: list[Bar],
    code_revision: str,
    metadata: dict[str, object] | None = None,
) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id=experiment_id,
        created_at=datetime.now(UTC),
        code_revision=code_revision,
        data_hash=hash_bars(bars),
        universe_version=universe_version,
        spec=spec_dict,
        seed=seed,
        metadata=metadata or {},
    )


__all__ = [
    "CRYPTO_BASE",
    "CRYPTO_STRESS",
    "DEFAULT_COST_SCENARIOS",
    "EQUITY_BASE",
    "EQUITY_STRESS",
    "CostScenario",
    "ExperimentManifest",
    "GateDecision",
    "ProductionGate",
    "TrialResult",
    "deflated_sharpe_probability",
    "hash_bars",
    "new_manifest",
    "outer_holdout_start",
    "probability_backtest_overfitting",
]
