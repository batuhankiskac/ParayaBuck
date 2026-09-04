#!/usr/bin/env python3
"""Isolated engine adapter for the low-beta C2 research candidate.

This module is intentionally a research adapter, not a product strategy
registration.  Callers must provide the already validated canonical PIT v5
bars, T1 universe, and corporate actions.  No data download, runtime service,
decision database, or PAPER/LIVE path is used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
SCRIPTS = REPO / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine, TerminalInfo
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy, require_product_universe
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalancePolicy, RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe

try:
    from scripts.research.equity_low_beta_candidate_2026_09 import (
        ALLOCATION,
        BETA_WINDOW,
        EXCLUDED_SYMBOLS,
        IDENTITY,
        MAX_SINGLE_WEIGHT,
        MIN_BETA_PRICES,
        TOP_N,
        equal_capped_weights,
        rank_dollar_volume,
        rank_low_beta,
    )
except ModuleNotFoundError:  # direct ``python scripts/research/...py`` execution
    from equity_low_beta_candidate_2026_09 import (
        ALLOCATION,
        BETA_WINDOW,
        EXCLUDED_SYMBOLS,
        IDENTITY,
        MAX_SINGLE_WEIGHT,
        MIN_BETA_PRICES,
        TOP_N,
        equal_capped_weights,
        rank_dollar_volume,
        rank_low_beta,
    )

SOURCE_HEAD = "ed11beba916202dcb4986853e848652af6e7a3ef"
DIRTY_PATHS = (
    "data/research/full_survivorship_2026_08/final_verdict_v5.json",
    "data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json",
    "data/research/full_survivorship_2026_08/parity_canary.json",
    "data/research/full_survivorship_2026_08/parity_manifest.json",
)


CANONICAL_INPUTS = {
    "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
    "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
    "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
}
MANIFEST = REPO / "data/research_manifests/equity_low_beta_candidate_2026_09.json"
DATA_PREFLIGHT_RECEIPT = REPO / "data/research_runs/equity_low_beta_candidate_2026_09/data_preflight.json"
FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
FULL_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
RECENT_START = datetime(2024, 7, 31, tzinfo=UTC)
LIQUIDITY_IDENTITY = "monthly_dollar_volume_top6_v1"


def require_frozen_inputs(
    manifest_path: Path = MANIFEST,
    preflight_path: Path = DATA_PREFLIGHT_RECEIPT,
) -> dict[str, object]:
    """Fail closed until the reviewed manifest and canonical preflight agree."""
    if not manifest_path.is_file() or not preflight_path.is_file():
        raise RuntimeError("BLOCKED_INPUTS_NOT_PREFLIGHTED")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "FROZEN" or manifest.get("manifest_sha256") in {None, "PENDING_FREEZE"}:
        raise RuntimeError("BLOCKED_MANIFEST_NOT_FROZEN")
    review = manifest.get("review", {})
    if not isinstance(review, dict) or review.get("frozen") is not True:
        raise RuntimeError("BLOCKED_MANIFEST_REVIEW_PENDING")
    inputs = manifest.get("inputs", {})
    if not isinstance(inputs, dict) or inputs.get("end") != FULL_END.isoformat():
        raise RuntimeError("BLOCKED_EVALUATION_END_MISMATCH")
    source = manifest.get("source", {})
    if not isinstance(source, dict) or source.get("head") != SOURCE_HEAD:
        raise RuntimeError("BLOCKED_SOURCE_HEAD_MISMATCH")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if preflight.get("status") == "PRE_ECONOMIC_PENDING_REVIEW":
        raise RuntimeError("BLOCKED_PREFLIGHT_REVIEW_PENDING")
    observed = preflight.get("observed_sha256", {})
    expected = {
        name: value.get("sha256")
        for name, value in inputs.items()
        if isinstance(value, dict) and "sha256" in value
    }
    if not isinstance(observed, dict) or any(observed.get(name) != digest for name, digest in expected.items()):
        raise RuntimeError("BLOCKED_PREFLIGHT_HASH_MISMATCH")
    if preflight.get("source", {}).get("head") != SOURCE_HEAD:
        raise RuntimeError("BLOCKED_PREFLIGHT_SOURCE_HEAD_MISMATCH")
    return manifest


class LowBetaMonthlyStrategy(PortfolioStrategy):
    """Candidate-owned beta ranking with a fixed six-name long-only sleeve."""

    name = IDENTITY
    version = "1.0.0"
    display_name = "Low Beta Equity (research only)"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "monthly"

    @property
    def minimum_history_bars(self) -> int:
        return MIN_BETA_PRICES

    def _validate_parameters(self) -> None:
        if self.param_int("beta_window", BETA_WINDOW) != BETA_WINDOW:
            raise ValueError("beta_window is frozen at 252 returns")
        if self.param_int("top_n", TOP_N) != TOP_N:
            raise ValueError("top_n is frozen at six")
        if self.param_float("allocation", ALLOCATION) != ALLOCATION:
            raise ValueError("allocation is frozen at 0.55")
        if self.param_float("max_single_weight", MAX_SINGLE_WEIGHT) != MAX_SINGLE_WEIGHT:
            raise ValueError("max_single_weight is frozen at 0.10")

    def _eligible_histories(
        self,
        context: PortfolioContext,
        *,
        require_universe: bool = True,
    ) -> tuple[dict[str, list[tuple[datetime, float]]], dict[datetime, float], tuple[str, ...]]:
        if require_universe:
            require_product_universe(context)
        eligible = tuple(
            symbol
            for symbol in sorted(context.history)
            if symbol not in EXCLUDED_SYMBOLS
            and (context.in_universe(symbol) if require_universe else True)
        )

        benchmark_bars = context.history.get(context.benchmark_symbol, ())
        benchmark = {bar.timestamp: bar.close for bar in benchmark_bars}
        histories = {
            symbol: [(bar.timestamp, bar.close) for bar in context.history[symbol]]
            for symbol in eligible
        }
        return histories, benchmark, eligible

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        # build_observation_set supplies a trusted scanner pool and an
        # intentionally permissive static context without a PIT universe.
        histories, benchmark, eligible = self._eligible_histories(context, require_universe=False)
        return tuple(item.symbol for item in rank_low_beta(histories, benchmark, eligible, as_of=context.decision_timestamp))

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        histories, benchmark, eligible = self._eligible_histories(context)
        ranked = rank_low_beta(histories, benchmark, eligible, as_of=context.decision_timestamp)
        if len(ranked) < TOP_N:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                data_timestamp=context.decision_timestamp,
                weights=(),
                cash_weight=1.0,
                rebalance_reason=RebalanceReason.RISK_OFF,
                universe_version=context.universe_version,
                universe_snapshot_id=context.universe_version,
                parameters=self.parameter_tuple(),
                metadata={"risk_off_detail": "coverage_insufficient", "qualified_count": len(ranked)},
            )
        selected = tuple(item.symbol for item in ranked[:TOP_N])
        weights = equal_capped_weights(selected)
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=tuple(
                TargetWeight(symbol, weights[symbol], reason="ascending_252d_beta", rank=rank)
                for rank, symbol in enumerate(selected, start=1)
            ),
            cash_weight=1.0 - sum(weights.values()),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_count": len(ranked),
                "selected_symbols": selected,
                "candidate_owned_signal": "rolling_252_return_OLS_beta_to_Benchmark",
                "cash_accrual": "zero; cash hurdle diagnostic only",
            },
        )


class MonthlyDollarVolumeStrategy(LowBetaMonthlyStrategy):
    """Candidate-valid-pool control using trailing median dollar volume."""

    name = LIQUIDITY_IDENTITY
    version = "1.0.0"
    display_name = "Monthly Dollar Volume Top 6 (research control)"

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        histories, _benchmark, eligible = self._eligible_histories(context, require_universe=False)
        volume_histories = {
            symbol: [(bar.timestamp, bar.close, bar.volume) for bar in context.history[symbol]]
            for symbol in eligible
        }
        return rank_dollar_volume(volume_histories, eligible)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        ranked = self.rank_candidates(context)
        if len(ranked) < TOP_N:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                data_timestamp=context.decision_timestamp,
                weights=(),
                cash_weight=1.0,
                rebalance_reason=RebalanceReason.RISK_OFF,
                universe_version=context.universe_version,
                universe_snapshot_id=context.universe_version,
                parameters=self.parameter_tuple(),
                metadata={"risk_off_detail": "coverage_insufficient", "qualified_count": len(ranked)},
            )
        selected = tuple(ranked[:TOP_N])
        weights = equal_capped_weights(selected)
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=tuple(
                TargetWeight(symbol, weights[symbol], reason="trailing_20d_median_dollar_volume", rank=rank)
                for rank, symbol in enumerate(selected, start=1)
            ),
            cash_weight=1.0 - sum(weights.values()),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"qualified_count": len(ranked), "selected_symbols": selected, "candidate_valid_pool": True},
        )


@dataclass(frozen=True)
class EngineCase:
    name: str
    strategy: PortfolioStrategy
    result: PortfolioBacktestResult
    universe_hash: str
    source_head: str
    coverage_status: str
    terminal_policy: str
    terminal_map_symbols: tuple[str, ...]
    terminal_event_count: int
    terminal_map_digest: str


def build_candidate_strategy() -> LowBetaMonthlyStrategy:
    return LowBetaMonthlyStrategy(
        {
            "beta_window": BETA_WINDOW,
            "top_n": TOP_N,
            "allocation": ALLOCATION,
            "max_single_weight": MAX_SINGLE_WEIGHT,
            "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        }
    )


def build_control_strategy(config: Config) -> EquityResidualMomentum1dV3:
    selection = config.strategy.equity
    return EquityResidualMomentum1dV3(dict(selection.parameters))


def build_liquidity_control_strategy() -> MonthlyDollarVolumeStrategy:
    return MonthlyDollarVolumeStrategy(
        {
            "beta_window": BETA_WINDOW,
            "top_n": TOP_N,
            "allocation": ALLOCATION,
            "max_single_weight": MAX_SINGLE_WEIGHT,
            "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        }
    )


def build_research_universe(
    config: Config,
    bars: Sequence[Bar],
    strategy: PortfolioStrategy,
    *,
    end: datetime | None = FULL_END,
) -> PointInTimeUniverse:
    """Build an explicit candidate/control observation basis from canonical bars.

    Scanner eligibility and T1 policy remain configured-product owned.  Only
    the streamed observation ranker is overridden, so candidate and control
    runs can prove their observation sets separately while sharing the same
    canonical source and endpoint.
    """
    if strategy.market != Market.EQUITY or strategy.timeframe != "1d":
        raise ValueError("research universe requires a daily equity strategy")
    return build_product_universe(
        config,
        list(bars),
        market=Market.EQUITY,
        end=end,
        timeframe="1d",
        observation_strategy_override=strategy,
    )


def build_engine(
    config: Config,
    strategy: PortfolioStrategy,
    universe: PointInTimeUniverse,
    *,
    cost_multiplier: float = 1.0,
    terminal_map: dict[str, TerminalInfo] | None = None,
    terminal_policy: str = "T1",
) -> PortfolioBacktestEngine:
    """Construct the shared research engine without registry/runtime side effects."""
    selection = config.strategy.equity
    cadence = getattr(strategy, "rebalance_policy_declared", "monthly")
    return TerminalAwareResearchEngine(
        strategy,
        RebalancePolicy(Market.EQUITY, cadence),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share * cost_multiplier,
        equity_taf_cap=config.backtest.equity_taf_cap * cost_multiplier,
        equity_cat_per_share=config.backtest.equity_cat_per_share * cost_multiplier,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate * cost_multiplier,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        allow_static_universe=False,
        terminal_map=terminal_map,
        policy=terminal_policy,
    )


def run_case(
    config: Config,
    bars: Sequence[Bar],
    universe: PointInTimeUniverse,
    strategy: PortfolioStrategy,
    *,
    start: datetime,
    end: datetime,
    corporate_actions: Sequence[CorporateAction] = (),
    cost_multiplier: float = 1.0,
    liquidate_at_end: bool = True,
    terminal_map: dict[str, TerminalInfo] | None = None,
    terminal_policy: str = "T1",
) -> EngineCase:
    """Run one bounded case; persistence and artifacts remain disabled."""
    if strategy.market != Market.EQUITY or strategy.timeframe != "1d":
        raise ValueError("C2 adapter accepts only daily equity strategies")
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be positive")
    selected = [bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == "1d" and start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == "1d" and bar.timestamp < start]
    if not selected:
        raise ValueError("no canonical equity bars in requested window")
    engine = build_engine(
        config,
        strategy,
        universe,
        cost_multiplier=cost_multiplier,
        terminal_map=terminal_map,
        terminal_policy=terminal_policy,
    )
    result = engine.run(
        selected,
        warmup=warmup,
        liquidate_at_end=liquidate_at_end,
        corporate_actions=list(corporate_actions),
    )
    fired = getattr(engine, "terminal_events_fired", ())
    map_payload = [
        {
            "symbol": symbol,
            "entity_id": info.entity_id,
            "last_bar": info.last_bar.isoformat(),
            "terminal_event_class": info.terminal_event_class,
            "evidence_class": info.evidence_class,
            "should_haircut": info.should_haircut,
        }
        for symbol, info in sorted((terminal_map or {}).items())
    ]
    terminal_digest = hashlib.sha256(
        json.dumps(map_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EngineCase(
        name=strategy.name,
        strategy=strategy,
        result=result,
        universe_hash=hash_point_in_time_universe(universe),
        source_head=SOURCE_HEAD,
        coverage_status=(
            "BLOCKED_COVERAGE_INSUFFICIENT"
            if strategy.name in {IDENTITY, LIQUIDITY_IDENTITY}
            and any(record.reason == RebalanceReason.RISK_OFF.value for record in result.rebalances)
            else "NOT_APPLICABLE_CONTROL" if strategy.name not in {IDENTITY, LIQUIDITY_IDENTITY}
            else "PASS_NO_COVERAGE_BLOCK"
        ),
        terminal_policy=terminal_policy,
        terminal_map_symbols=tuple(sorted((terminal_map or {}).keys())),
        terminal_event_count=len(fired),
        terminal_map_digest=terminal_digest,
    )


def case_windows() -> tuple[tuple[str, datetime, datetime], ...]:
    return (
        ("full_2017_2026_07_30", FULL_START, FULL_END),
        ("recent_last_24_months", RECENT_START, FULL_END),
    )


__all__ = [
    "CANONICAL_INPUTS",
    "DATA_PREFLIGHT_RECEIPT",
    "DIRTY_PATHS",
    "EngineCase",
    "LowBetaMonthlyStrategy",
    "MonthlyDollarVolumeStrategy",
    "build_candidate_strategy",
    "build_control_strategy",
    "build_liquidity_control_strategy",
    "build_engine",
    "build_research_universe",
    "case_windows",
    "require_frozen_inputs",
    "run_case",
    "SOURCE_HEAD",
]
