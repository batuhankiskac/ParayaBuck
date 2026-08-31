#!/usr/bin/env python3
"""Predeclared, research-only equity alpha roadmap after V3.

The runner compares every candidate with the frozen production V3 control in
the same run, at the current 0.55 equity allocation, on the symmetric PIT v5
dataset.  Candidate classes live here and are deliberately not registered as
product strategies.  No artifact produced by this file is read by runtime
code and no promotion is performed.

Usage::

    python scripts/research_v3_equity_roadmap.py --variants control,hysteresis,path

The default invocation runs the complete first wave (M0-M4A/B, M6, M11),
including 20 independent six-month folds, cost stress, jackknife, regime and
bootstrap diagnostics.  Generated JSON stays under the gitignored
``data/research/equity_v3_alpha_roadmap_2026_08`` directory.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from research_helpers import config_digest, git_head, sha256_of_file  # noqa: E402
from run_symmetric_v5_validation import (  # noqa: E402
    load_bars,
    load_corporate_actions_all,
    load_lifetimes,
)

from parayabuck.backtest.numeric_history import NumericHistoryView, build_numeric_history  # noqa: E402
from parayabuck.backtest.observation_parity import (  # noqa: E402
    assert_strategy_identity,
    canonical_digest,
    strategy_identity,
)
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult  # noqa: E402
from parayabuck.backtest.product import build_product_universe  # noqa: E402
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine, TerminalInfo  # noqa: E402
from parayabuck.backtest.universe import (  # noqa: E402
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.market_dataset import CorporateAction  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import (  # noqa: E402
    EquityResidualMomentum1dV3,
    portfolio_strategy_from,
    rebalance_policy_for,
    supported_portfolio_strategies,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum_v3 import (  # noqa: E402
    _compounded,
    multi_horizon_consensus,
    percentile_ranks,
)
from parayabuck.portfolio.research_alpha import hysteresis_selection, spy_regime_state  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATASET_V5 = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_V5 = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
CA_V5 = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
OUT_DIR = REPO / "data/research/equity_v3_alpha_roadmap_2026_08"
MANIFEST_PATH = REPO / "data/research_manifests/equity_v3_alpha_roadmap_2026_08.json"
RESULT_SCHEMA_VERSION = "1.0.0"

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
RECENT_START = datetime(2022, 1, 1, tzinfo=UTC)
CURRENT_EQUITY_ALLOCATION = 0.55
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_RESAMPLES = 2_000
BOOTSTRAP_SEED = 20260830
JACKKNIFE_TOP = 5
HORIZONS = (6 * 21, 9 * 21, 12 * 21)

FROZEN_PARAMETERS: dict[str, object] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


class _DecisionTelemetry:
    """Non-economic research telemetry shared by the candidate identities."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._decision_log: list[dict[str, object]] = []
        self._weight_diagnostics: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)  # type: ignore[misc]

    def decide(self, context: PortfolioContext):  # type: ignore[no-untyped-def]
        target = super().decide(context)  # type: ignore[misc]
        selected = tuple(target.metadata.get("selected_symbols", ()))
        self._decision_log.append(
            {
                "timestamp": context.decision_timestamp,
                "selected": selected,
                "qualified_count": int(target.metadata.get("qualified_symbols", 0)),
                "target_exposure": float(sum(weight.weight for weight in target.weights)),
            }
        )
        return target

    def _record_weight_diagnostic(
        self,
        selected: tuple[str, ...],
        weights: Mapping[str, float],
        *,
        method: str,
    ) -> None:
        cap = self.param_float("max_single_weight", 0.10)  # type: ignore[attr-defined]
        values = tuple(float(value) for value in weights.values() if value > 0)
        self._weight_diagnostics.append(
            {
                "selected_count": len(selected),
                "pre_overlay_sum": sum(values),
                "cap_binding_count": sum(1 for value in values if value >= cap - 1e-9),
                "cap_slack": max(0.0, CURRENT_EQUITY_ALLOCATION - sum(values)),
                "method": method,
            }
        )

    def _position_weights(self, selected: tuple[str, ...], residual_vols: dict[str, float]) -> dict[str, float]:
        weights = super()._position_weights(selected, residual_vols)  # type: ignore[misc]
        self._record_weight_diagnostic(selected, weights, method="inverse_volatility")
        return weights


class RoadmapControl(_DecisionTelemetry, EquityResidualMomentum1dV3):
    """Same V3 behavior, instrumented only for a research control audit."""

    name = "equity_residual_momentum_v3"
    version = "3.0.0"
    display_name = "V3 control (current 0.55 policy)"


class RoadmapHysteresis(RoadmapControl):
    """M1: V3 rank, entry <=8, held eligible rank <=12."""

    name = "equity_residual_momentum_hysteresis_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Hysteresis v1"

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._selection_events: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)

    def _select_names(
        self,
        ranked: tuple[tuple[float, str], ...],
        residual_vols: dict[str, float],
        context: PortfolioContext,
        top_n: int,
    ) -> tuple[str, ...]:
        del residual_vols
        selected = hysteresis_selection(
            ranked,
            context.holdings,
            top_n=top_n,
            entry_rank=top_n,
            hold_rank=top_n + 4,
        )
        ranks = {symbol: index for index, (_score, symbol) in enumerate(ranked, start=1)}
        held = {symbol for symbol, quantity in context.holdings.items() if quantity > 0}
        self._selection_events.append(
            {
                "timestamp": context.decision_timestamp,
                "selected": selected,
                "held_retained": tuple(symbol for symbol in selected if symbol in held),
                "new_entries": tuple(symbol for symbol in selected if symbol not in held),
                "ranked_boundary": tuple((symbol, ranks[symbol]) for symbol in selected),
            }
        )
        return selected

    def _target_rank_by_symbol(
        self,
        ranked: tuple[tuple[float, str], ...],
        selected: tuple[str, ...],
    ) -> dict[str, int]:
        ranks = {symbol: index for index, (_score, symbol) in enumerate(ranked, start=1)}
        return {symbol: ranks[symbol] for symbol in selected}


def information_discreteness(
    residuals: Sequence[float],
    *,
    formation: int,
    skip: int,
) -> float | None:
    """Return the predeclared residual-path ID, or None if undefined."""
    window = residuals[-formation - skip : -skip]
    pret = _compounded(window)
    if not math.isfinite(pret):
        return None
    n_up = sum(1 for value in window if value > 0)
    n_down = sum(1 for value in window if value < 0)
    denominator = n_down + n_up
    if denominator == 0:
        return None
    sign = 1.0 if pret > 0 else -1.0 if pret < 0 else 0.0
    return sign * (n_down - n_up) / denominator


class RoadmapPath(RoadmapControl):
    """M2: V3 6/9/12 ranks plus a fixed 25% path-continuity rank."""

    name = "equity_residual_path_momentum_v1"
    version = "1.0.0"
    display_name = "Residual Path Momentum v1"

    def _transform_cross_sectional_scores_with_context(
        self,
        context: PortfolioContext,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        del context, residual_vols
        consensus = multi_horizon_consensus(residuals_by_symbol, HORIZONS, skip)
        path_raw = {
            symbol: -path_id
            for symbol, residuals in residuals_by_symbol.items()
            if (path_id := information_discreteness(residuals, formation=formation, skip=skip)) is not None
        }
        continuity = percentile_ranks(path_raw)
        return [
            ((3.0 * consensus[symbol] + continuity[symbol]) / 4.0, symbol)
            for _score, symbol in scored
            if symbol in consensus and symbol in continuity
        ]


class RoadmapBreadth(RoadmapControl):
    """M3: at least two positive residual horizons qualify."""

    name = "equity_residual_momentum_breadth_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Breadth v1"

    def _candidate_passes_gate(
        self,
        residuals: Sequence[float],
        score: float,
        *,
        formation: int,
        skip: int,
    ) -> bool:
        del score, formation
        positive = 0
        for horizon in HORIZONS:
            value = _compounded(residuals[-(horizon + skip) : -skip])
            positive += int(math.isfinite(value) and value > 0)
        return positive >= 2


def capped_waterfill(
    budgets: Mapping[str, float],
    *,
    total: float,
    cap: float,
) -> dict[str, float]:
    """Allocate ``min(c * budget, cap)`` to the declared total."""
    clean = {
        symbol: float(budget)
        for symbol, budget in budgets.items()
        if math.isfinite(float(budget)) and float(budget) > 0
    }
    if not clean or total <= 0 or cap <= 0:
        return {symbol: 0.0 for symbol in budgets}
    target = min(float(total), cap * len(clean))
    if target >= cap * len(clean) - 1e-12:
        return {symbol: cap if symbol in clean else 0.0 for symbol in budgets}
    low = 0.0
    high = max(cap / budget for budget in clean.values())
    for _ in range(100):
        middle = (low + high) / 2.0
        allocated = sum(min(middle * budget, cap) for budget in clean.values())
        if allocated < target:
            low = middle
        else:
            high = middle
    scale = (low + high) / 2.0
    return {symbol: min(scale * clean[symbol], cap) if symbol in clean else 0.0 for symbol in budgets}


class RoadmapCapAware(RoadmapControl):
    """M4A: same V3 names, clipped inverse-vol budget redistributed."""

    name = "equity_residual_momentum_cap_aware_ivol_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Cap-Aware IVOL v1"

    def _position_weights(self, selected: tuple[str, ...], residual_vols: dict[str, float]) -> dict[str, float]:
        cap = self.param_float("max_single_weight", 0.10)
        budgets = {symbol: 1.0 / residual_vols[symbol] for symbol in selected if residual_vols.get(symbol, 0) > 0}
        weights = capped_waterfill(budgets, total=CURRENT_EQUITY_ALLOCATION, cap=cap)
        self._record_weight_diagnostic(selected, weights, method="capped_inverse_volatility")
        return weights


class RoadmapScoreRisk(RoadmapControl):
    """M4B: V3 consensus percentile / IVOL capped water-filling."""

    name = "equity_residual_momentum_score_risk_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Score-Risk v1"

    def _position_weights_for_ranked(
        self,
        ranked: tuple[tuple[float, str], ...],
        selected: tuple[str, ...],
        residual_vols: dict[str, float],
        context: PortfolioContext,
    ) -> dict[str, float]:
        del context
        score_by_symbol = {symbol: score for score, symbol in ranked}
        budgets = {
            symbol: score_by_symbol[symbol] / residual_vols[symbol]
            for symbol in selected
            if symbol in score_by_symbol and residual_vols.get(symbol, 0) > 0 and score_by_symbol[symbol] > 0
        }
        weights = capped_waterfill(
            budgets,
            total=CURRENT_EQUITY_ALLOCATION,
            cap=self.param_float("max_single_weight", 0.10),
        )
        self._record_weight_diagnostic(selected, weights, method="score_percentile_over_ivol")
        return weights


class RoadmapDeployable6Equal(RoadmapControl):
    """M2 follow-up: exact V3 rank, top six, equal 10% pre-overlay targets.

    The production risk overlay remains authoritative: the six 10% strategy
    targets are scaled to the current 0.55 equity sleeve by the normal engine
    path.  Only portfolio construction differs from the frozen V3 control.
    """

    name = "equity_residual_momentum_deployable6_equal_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Deployable-Six Equal v1"

    def _position_weights(self, selected: tuple[str, ...], residual_vols: dict[str, float]) -> dict[str, float]:
        del residual_vols
        cap = self.param_float("max_single_weight", 0.10)
        weights = {symbol: cap for symbol in selected}
        self._record_weight_diagnostic(selected, weights, method="equal_weight_deployable_six")
        return weights


class Roadmap52WeekHigh(RoadmapControl):
    """M6: completed close divided by its trailing 252-bar high."""

    name = "equity_52week_high_proximity_v1"
    version = "1.0.0"
    display_name = "Equity 52-Week-High Proximity v1"

    def _candidate_passes_gate(
        self,
        residuals: Sequence[float],
        score: float,
        *,
        formation: int,
        skip: int,
    ) -> bool:
        del residuals, score, formation, skip
        return True

    def _transform_cross_sectional_scores_with_context(
        self,
        context: PortfolioContext,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        del residuals_by_symbol, residual_vols, formation, skip
        output: list[tuple[float, str]] = []
        for _old_score, symbol in scored:
            bars = context.history.get(symbol, ())
            completed = [bar for bar in bars if bar.timestamp <= context.decision_timestamp and bar.close > 0]
            closes = [bar.close for bar in completed[-252:]]
            if len(closes) < 252:
                continue
            high = max(closes)
            if high > 0:
                output.append((closes[-1] / high, symbol))
        return output


class RoadmapReversal(RoadmapControl):
    """M11: negative five-completed-bar residual return, weekly."""

    name = "equity_residual_short_term_reversal_v1"
    version = "1.0.0"
    display_name = "Residual Short-Term Reversal v1"

    def _candidate_score(self, residuals: Sequence[float], *, formation: int, skip: int) -> float:
        del formation, skip
        return -_compounded(residuals[-5:])

    def _transform_cross_sectional_scores(
        self,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        del residuals_by_symbol, residual_vols, formation, skip
        return scored


VARIANTS: dict[str, tuple[type[EquityResidualMomentum1dV3], dict[str, object]]] = {
    "control": (RoadmapControl, {}),
    "hysteresis": (RoadmapHysteresis, {}),
    "path": (RoadmapPath, {}),
    "breadth": (RoadmapBreadth, {}),
    "cap_aware": (RoadmapCapAware, {}),
    "score_risk": (RoadmapScoreRisk, {}),
    "deployable6_equal": (RoadmapDeployable6Equal, {"top_n": 6}),
    "52week": (Roadmap52WeekHigh, {}),
    "reversal": (RoadmapReversal, {}),
}


@dataclass(frozen=True)
class PreparedWindow:
    key: str
    start: datetime | None
    end: datetime
    available: list[Bar]
    filtered: list[Bar]
    warmup: list[Bar]
    prepared: tuple[list[Bar], list[Bar]]
    numeric_history: NumericHistoryView
    universe: PointInTimeUniverse
    universe_version: str
    observation_basis: str = "configured"
    observation_strategy_name: str = ""
    observation_strategy_version: str = ""
    observation_strategy_market: str = ""
    observation_strategy_timeframe: str = ""
    observation_strategy_parameter_digest: str = ""
    # Candidate-owned observers are stateful by design.  Retain the exact
    # instance used to build the PIT universe so the later engine boundary can
    # prove that execution receives a fresh, identity-matching instance.
    observation_strategy: PortfolioStrategy | None = field(default=None, repr=False, compare=False)


def fold_windows() -> list[tuple[str, datetime, datetime]]:
    folds: list[tuple[str, datetime, datetime]] = []
    for year in range(2017, 2027):
        first_start = datetime(year, 1, 1, tzinfo=UTC)
        first_end = min(datetime(year, 7, 1, tzinfo=UTC), EVAL_END)
        if first_start < EVAL_END:
            folds.append((f"{year}-H1", first_start, first_end))
        second_start = datetime(year, 7, 1, tzinfo=UTC)
        second_end = min(datetime(year + 1, 1, 1, tzinfo=UTC), EVAL_END)
        if second_start < EVAL_END:
            folds.append((f"{year}-H2", second_start, second_end))
    return folds[:20]


def annual_windows() -> list[tuple[str, datetime, datetime]]:
    windows: list[tuple[str, datetime, datetime]] = []
    for year in range(EVAL_START.year, EVAL_END.year + 1):
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = min(datetime(year + 1, 1, 1, tzinfo=UTC), EVAL_END)
        if start < EVAL_END:
            windows.append((str(year), start, end))
    return windows


def _bar_slice(bars: list[Bar], start: datetime | None, end: datetime) -> list[Bar]:
    left = bisect_left(bars, start, key=lambda bar: bar.timestamp) if start is not None else 0
    right = bisect_right(bars, end, key=lambda bar: bar.timestamp)
    return bars[left:right]


def prepare_window(
    config: Config,
    bars: list[Bar],
    *,
    key: str,
    start: datetime | None,
    end: datetime,
    observation_strategy_factory: Callable[[], PortfolioStrategy] | None = None,
    execution_strategy_factory: Callable[[], PortfolioStrategy] | None = None,
    observation_basis: str | None = None,
) -> PreparedWindow:
    available_all = _bar_slice(bars, None if start is None else start - timedelta(days=400), end)
    filtered = [bar for bar in available_all if start is None or bar.timestamp >= start]
    warmup = [bar for bar in available_all if start is not None and bar.timestamp < start]
    ordered = sorted(PortfolioBacktestEngine._deduplicate(filtered), key=lambda bar: (bar.timestamp, bar.symbol))
    warmup = sorted(PortfolioBacktestEngine._deduplicate(warmup), key=lambda bar: (bar.timestamp, bar.symbol))
    prepared = (ordered, ordered)
    numeric = build_numeric_history([*warmup, *ordered], market=Market.EQUITY, timeframe="1d")
    if observation_basis not in {None, "configured", "candidate_owned"}:
        raise ValueError("observation_basis must be configured or candidate_owned")
    observation_strategy: PortfolioStrategy | None = None
    identity: dict[str, object] | None = None
    if observation_strategy_factory is not None:
        if execution_strategy_factory is None:
            raise ValueError("candidate-owned observation preparation requires a separate execution_strategy_factory")
        observation_strategy = observation_strategy_factory()
        execution_strategy = execution_strategy_factory()
        assert_strategy_identity(observation_strategy, execution_strategy)
        identity = strategy_identity(observation_strategy)
        effective_basis = observation_basis or "candidate_owned"
        if effective_basis != "candidate_owned":
            raise ValueError("an observation_strategy_factory requires observation_basis='candidate_owned'")
    else:
        if execution_strategy_factory is not None:
            # X|C research is intentionally allowed: a caller may keep the
            # configured observation basis while executing an explicit
            # candidate. Identity parity is required only for X|X.
            if not isinstance(execution_strategy_factory(), PortfolioStrategy):
                raise TypeError("execution_strategy_factory must return a PortfolioStrategy")
        effective_basis = observation_basis or "configured"
        if effective_basis != "configured":
            raise ValueError("candidate_owned observation_basis requires an observation_strategy_factory")
    if observation_strategy is not None:
        universe = build_product_universe(
            config,
            [*warmup, *ordered],
            market=Market.EQUITY,
            end=end,
            timeframe="1d",
            observation_strategy_override=observation_strategy,
        )
    else:
        universe = build_product_universe(
            config,
            [*warmup, *ordered],
            market=Market.EQUITY,
            end=end,
            timeframe="1d",
        )
    version = hash_point_in_time_universe(universe)
    if not universe.snapshots:
        raise RuntimeError(f"PIT universe empty for window {key}")
    return PreparedWindow(
        key=key,
        start=start,
        end=end,
        available=available_all,
        filtered=filtered,
        warmup=warmup,
        prepared=prepared,
        numeric_history=numeric,
        universe=universe,
        universe_version=version,
        observation_basis=effective_basis,
        observation_strategy_name=str(identity["name"]) if identity is not None else "",
        observation_strategy_version=str(identity["version"]) if identity is not None else "",
        observation_strategy_market=str(identity["market"]) if identity is not None else "",
        observation_strategy_timeframe=str(identity["timeframe"]) if identity is not None else "",
        observation_strategy_parameter_digest=(
            str(identity["parameter_digest_sha256"]) if identity is not None else ""
        ),
        observation_strategy=observation_strategy,
    )


def make_engine(
    config: Config,
    strategy: EquityResidualMomentum1dV3,
    window: PreparedWindow,
    *,
    terminal_map: Mapping[str, TerminalInfo],
    cost_multiplier: float = 1.0,
) -> TerminalAwareResearchEngine:
    return TerminalAwareResearchEngine(
        strategy=strategy,
        rebalance_policy=rebalance_policy_for(strategy),
        terminal_map=dict(terminal_map),
        policy="T1",
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
        universe=window.universe,
        universe_version=window.universe_version,
    )


def build_strategy(variant_id: str) -> EquityResidualMomentum1dV3:
    cls, overrides = VARIANTS[variant_id]
    parameters = dict(FROZEN_PARAMETERS)
    parameters.update(overrides)
    return cls(parameters)


def run_case(
    config: Config,
    variant_id: str,
    window: PreparedWindow,
    *,
    terminal_map: Mapping[str, TerminalInfo],
    actions: Sequence[CorporateAction],
    cost_multiplier: float = 1.0,
    execution_strategy_factory: Callable[[], PortfolioStrategy] | None = None,
) -> tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3]:
    strategy = execution_strategy_factory() if execution_strategy_factory is not None else build_strategy(variant_id)
    if not isinstance(strategy, PortfolioStrategy):
        raise TypeError("execution_strategy_factory must return a PortfolioStrategy")
    if window.observation_basis == "candidate_owned":
        if window.observation_strategy is None:
            raise ValueError("candidate-owned window is missing its observation strategy instance")
        # Validate the object that is about to enter the engine, not only the
        # throwaway execution object created during preparation.  This checks
        # full identity (including market and parameters) and rejects reuse of
        # the stateful observer instance itself.
        assert_strategy_identity(window.observation_strategy, strategy)
        identity = strategy_identity(strategy)
        expected = {
            "name": window.observation_strategy_name,
            "version": window.observation_strategy_version,
            "market": window.observation_strategy_market,
            "timeframe": window.observation_strategy_timeframe,
            "parameter_digest_sha256": window.observation_strategy_parameter_digest,
        }
        for field, value in expected.items():
            if identity[field] != value:
                raise ValueError(f"Prepared candidate observation/execution {field} mismatch")
    engine = make_engine(config, strategy, window, terminal_map=terminal_map, cost_multiplier=cost_multiplier)
    result = engine.run(
        window.filtered,
        warmup=window.warmup,
        liquidate_at_end=True,
        corporate_actions=list(actions),
        prepared_inputs=window.prepared,
        numeric_history=window.numeric_history,
    )
    if not result.equity_curve:
        raise RuntimeError(f"empty result for {variant_id}/{window.key}")
    return result, strategy


def _profit_factor(result: PortfolioBacktestResult) -> float:
    gains = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    losses = -sum(pnl for pnl in result.realized_pnls if pnl < 0)
    if losses > 0:
        return gains / losses
    return float("inf") if gains > 0 else 0.0


def rebalance_stats(result: PortfolioBacktestResult) -> dict[str, float]:
    if not result.rebalances or len(result.equity_curve) < 2:
        return {"rebalance_win_rate": 0.0, "avg_winning_rebalance": 0.0, "avg_losing_rebalance": 0.0}
    index_by_time = {timestamp: index for index, timestamp in enumerate(result.curve_timestamps)}
    picks = sorted(
        {
            0,
            *(
                index_by_time[record.decision_timestamp]
                for record in result.rebalances
                if record.decision_timestamp in index_by_time
            ),
        }
    )
    values: list[float] = []
    for left, right in zip(picks, picks[1:]):
        if result.equity_curve[left] > 0 and result.equity_curve[right] > 0:
            values.append(result.equity_curve[right] / result.equity_curve[left] - 1.0)
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    return {
        "rebalance_win_rate": len(wins) / len(values) if values else 0.0,
        "avg_winning_rebalance": statistics.fmean(wins) if wins else 0.0,
        "avg_losing_rebalance": statistics.fmean(losses) if losses else 0.0,
    }


def full_stats(result: PortfolioBacktestResult) -> dict[str, object]:
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = statistics.fmean(result.equity_curve) if result.equity_curve else result.starting_equity
    gross_pnl = result.starting_equity * result.gross_return
    return {
        "return": round(result.net_return, 6),
        "gross_return": round(result.gross_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "max_drawdown_duration_days": result.max_drawdown_duration_days,
        "volatility": round(result.annualized_volatility, 6),
        "turnover": round(result.turnover, 6),
        "fees_total": round(total_cost, 2),
        "fees_pct_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_gross_pnl": round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None,
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "max_exposure": round(max((sum(weights.values()) for _ts, weights in result.weight_history), default=0.0), 4),
        "avg_holdings": round(result.average_holdings_count, 3),
        "max_holdings": max((len(weights) for _ts, weights in result.weight_history), default=0),
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "dividend_total": round(result.dividend_total, 2),
        "closed_position_events": len(result.closed_position_events),
        "profit_factor": round(_profit_factor(result), 6),
        **{key: round(value, 6) for key, value in rebalance_stats(result).items()},
    }


def yearly_returns(result: PortfolioBacktestResult) -> dict[str, float]:
    buckets: dict[int, float] = {}
    for equity, timestamp in zip(result.equity_curve, result.curve_timestamps):
        buckets.setdefault(timestamp.year, equity)
        buckets[timestamp.year] = equity
    previous = result.starting_equity
    output: dict[str, float] = {}
    for year in sorted(buckets):
        output[str(year)] = round(buckets[year] / previous - 1.0, 6) if previous > 0 else 0.0
        previous = buckets[year]
    return output


def concentration(result: PortfolioBacktestResult) -> dict[str, object]:
    contributions = sorted(
        ((item.symbol, item.realized_pnl) for item in result.per_symbol),
        key=lambda item: (-item[1], item[0]),
    )
    positive = sum(pnl for _symbol, pnl in contributions if pnl > 0)
    return {
        "top_symbols": [symbol for symbol, _pnl in contributions[:JACKKNIFE_TOP]],
        "top_symbols_pnl": [round(pnl, 2) for _symbol, pnl in contributions[:JACKKNIFE_TOP]],
        "top1_share_of_positive_pnl": round(contributions[0][1] / positive, 6)
        if contributions and positive > 0
        else 0.0,
        "top3_share_of_positive_pnl": round(sum(pnl for _symbol, pnl in contributions[:3]) / positive, 6)
        if positive > 0
        else 0.0,
        "top5_share_of_positive_pnl": round(sum(pnl for _symbol, pnl in contributions[:5]) / positive, 6)
        if positive > 0
        else 0.0,
    }


def universe_without(universe: PointInTimeUniverse, banned: frozenset[str]) -> PointInTimeUniverse:
    snapshots = tuple(
        UniverseSnapshot(
            effective_at=snapshot.effective_at,
            symbols=frozenset(snapshot.symbols - banned),
            scanner_version=snapshot.scanner_version,
            config_digest=snapshot.config_digest,
            candidate_count=snapshot.candidate_count,
            score_breakdown=snapshot.score_breakdown,
            eliminated=snapshot.eliminated,
            sector_metadata_coverage=snapshot.sector_metadata_coverage,
            observation_set=tuple(symbol for symbol in snapshot.observation_set if symbol not in banned),
            maximum_stream_symbols=snapshot.maximum_stream_symbols,
            pinned_symbols=tuple(symbol for symbol in snapshot.pinned_symbols if symbol not in banned),
            observation_strategy_name=snapshot.observation_strategy_name,
            observation_strategy_version=snapshot.observation_strategy_version,
        )
        for snapshot in universe.snapshots
    )
    return PointInTimeUniverse(market=universe.market, snapshots=snapshots, methodology=universe.methodology)


def fold_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    returns = [float(row["return"]) for row in rows]
    sharpes = [float(row["sharpe"]) for row in rows]
    return {
        "folds": rows,
        "fold_count": len(rows),
        "median_fold_return": round(statistics.median(returns), 6) if returns else 0.0,
        "mean_fold_return": round(statistics.fmean(returns), 6) if returns else 0.0,
        "fold_return_stdev": round(statistics.pstdev(returns), 6) if len(returns) > 1 else 0.0,
        "25th_percentile_fold_return": round(sorted(returns)[int(0.25 * (len(returns) - 1))], 6) if returns else 0.0,
        "worst_fold_return": round(min(returns), 6) if returns else None,
        "best_fold_return": round(max(returns), 6) if returns else None,
        "positive_fold_ratio": round(sum(value > 0 for value in returns) / len(returns), 6) if returns else 0.0,
        "median_fold_sharpe": round(statistics.median(sharpes), 6) if sharpes else 0.0,
    }


def spy_daily_regimes(bars: list[Bar]) -> dict[datetime, str]:
    spy = sorted(
        (bar for bar in bars if bar.symbol == "SPY" and bar.market == Market.EQUITY), key=lambda bar: bar.timestamp
    )
    closes: list[float] = []
    output: dict[datetime, str] = {}
    for bar in spy:
        closes.append(bar.close)
        output[bar.timestamp] = spy_regime_state(closes)[0]
    return output


def regime_pnl(result: PortfolioBacktestResult, regimes: Mapping[datetime, str]) -> dict[str, object]:
    buckets: dict[str, list[float]] = {"NORMAL": [], "ELEVATED": []}
    previous: float | None = None
    for equity, timestamp in zip(result.equity_curve, result.curve_timestamps):
        regime = regimes.get(timestamp)
        if regime in buckets and previous is not None and previous > 0:
            buckets[regime].append(equity / previous - 1.0)
        previous = equity
    return {
        state: {
            "days": len(values),
            "mean_daily_return": round(statistics.fmean(values), 6) if values else 0.0,
            "total_simple_return": round(sum(values), 6),
            "positive_day_ratio": round(sum(value > 0 for value in values) / len(values), 6) if values else 0.0,
        }
        for state, values in buckets.items()
    }


def _daily_returns(curve: Sequence[float]) -> list[float]:
    return [
        curve[index] / curve[index - 1] - 1.0
        for index in range(1, len(curve))
        if curve[index - 1] > 0 and curve[index] > 0
    ]


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    n = min(len(left), len(right))
    if n < 2:
        return 0.0
    x = list(left[:n])
    y = list(right[:n])
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y))
    return numerator / denominator if denominator > 0 else 0.0


def selection_overlap(
    candidate: EquityResidualMomentum1dV3, control: EquityResidualMomentum1dV3
) -> dict[str, float | int]:
    candidate_log = {item["timestamp"]: set(item["selected"]) for item in getattr(candidate, "_decision_log", ())}
    control_log = {item["timestamp"]: set(item["selected"]) for item in getattr(control, "_decision_log", ())}
    common = sorted(set(candidate_log) & set(control_log))
    if not common:
        return {"decisions": 0, "mean_intersection": 0.0, "mean_jaccard": 0.0}
    intersections = [len(candidate_log[ts] & control_log[ts]) for ts in common]
    jaccards = [
        len(candidate_log[ts] & control_log[ts]) / len(candidate_log[ts] | control_log[ts])
        if candidate_log[ts] | control_log[ts]
        else 1.0
        for ts in common
    ]
    return {
        "decisions": len(common),
        "mean_intersection": round(statistics.fmean(intersections), 6),
        "mean_jaccard": round(statistics.fmean(jaccards), 6),
    }


def turnover_attribution(strategy: EquityResidualMomentum1dV3) -> dict[str, object]:
    logs = sorted(getattr(strategy, "_decision_log", ()), key=lambda item: item["timestamp"])
    entries = exits = unchanged = 0
    previous: set[str] = set()
    for item in logs:
        selected = set(item["selected"])
        entries += len(selected - previous)
        exits += len(previous - selected)
        unchanged += len(selected & previous)
        previous = selected
    return {
        "decision_count": len(logs),
        "entries": entries,
        "exits": exits,
        "unchanged_name_decisions": unchanged,
        "selection_churn": entries + exits,
    }


def strategy_diagnostics(
    strategy: EquityResidualMomentum1dV3,
    result: PortfolioBacktestResult,
    control_strategy: EquityResidualMomentum1dV3,
    control_result: PortfolioBacktestResult,
) -> dict[str, object]:
    qualified = [int(item["qualified_count"]) for item in getattr(strategy, "_decision_log", ())]
    pre_overlay = [float(item["pre_overlay_sum"]) for item in getattr(strategy, "_weight_diagnostics", ())]
    cap_bindings = [int(item["cap_binding_count"]) for item in getattr(strategy, "_weight_diagnostics", ())]
    return {
        "selection_overlap_vs_control": selection_overlap(strategy, control_strategy),
        "weekly_active_return_correlation": round(
            _pearson(_daily_returns(result.equity_curve), _daily_returns(control_result.equity_curve)), 6
        ),
        "exposure": {
            "actual_avg_exposure": full_stats(result)["avg_exposure"],
            "actual_max_exposure": full_stats(result)["max_exposure"],
            "mean_pre_overlay_target": round(statistics.fmean(pre_overlay), 6) if pre_overlay else 0.0,
            "fraction_pre_overlay_below_0.55": round(
                sum(value < CURRENT_EQUITY_ALLOCATION - 1e-9 for value in pre_overlay) / len(pre_overlay), 6
            )
            if pre_overlay
            else 0.0,
        },
        "cap_binding": {
            "mean_bound_names": round(statistics.fmean(cap_bindings), 6) if cap_bindings else 0.0,
            "decisions_with_binding": sum(value > 0 for value in cap_bindings),
            "decision_count": len(cap_bindings),
        },
        "qualified_pool_breadth": {
            "mean": round(statistics.fmean(qualified), 6) if qualified else 0.0,
            "median": round(statistics.median(qualified), 6) if qualified else 0.0,
            "decisions": len(qualified),
            "at_least_six_fraction": round(sum(value >= 6 for value in qualified) / len(qualified), 6)
            if qualified
            else 0.0,
        },
        "turnover_attribution": turnover_attribution(strategy),
        "weight_methods": sorted({str(item["method"]) for item in getattr(strategy, "_weight_diagnostics", ())}),
    }


def block_bootstrap_deltas(
    control_curve: Sequence[float],
    candidate_curve: Sequence[float],
    *,
    block: int = BOOTSTRAP_BLOCK,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    control_returns = _daily_returns(control_curve)
    candidate_returns = _daily_returns(candidate_curve)
    n = min(len(control_returns), len(candidate_returns))
    if n < 2:
        return {"method": "20-day block bootstrap", "resamples": 0, "reason": "insufficient_curve"}
    control_returns = control_returns[:n]
    candidate_returns = candidate_returns[:n]

    def blocks(values: list[float]) -> list[list[float]]:
        return [values[index : index + block] for index in range(0, n, block)]

    control_blocks = blocks(control_returns)
    candidate_blocks = blocks(candidate_returns)

    def metrics(values: list[float]) -> tuple[float, float, float]:
        compounded = math.prod(1.0 + value for value in values)
        years = len(values) / 252.0
        cagr = compounded ** (1.0 / years) - 1.0 if years > 0 and compounded > 0 else 0.0
        mean = statistics.fmean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        sharpe = mean / stdev * math.sqrt(252.0) if stdev > 0 else 0.0
        running = peak = 1.0
        max_dd = 0.0
        for value in values:
            running *= 1.0 + value
            peak = max(peak, running)
            max_dd = max(max_dd, (peak - running) / peak if peak > 0 else 0.0)
        return cagr, sharpe, max_dd

    rng = random.Random(seed)
    delta_return: list[float] = []
    delta_sharpe: list[float] = []
    delta_dd: list[float] = []
    for _ in range(resamples):
        sampled_control: list[float] = []
        sampled_candidate: list[float] = []
        while len(sampled_control) < n:
            index = rng.randrange(len(control_blocks))
            sampled_control.extend(control_blocks[index])
            sampled_candidate.extend(candidate_blocks[index])
        cagr_c, sharpe_c, dd_c = metrics(sampled_control[:n])
        cagr_v, sharpe_v, dd_v = metrics(sampled_candidate[:n])
        delta_return.append(cagr_v - cagr_c)
        delta_sharpe.append(sharpe_v - sharpe_c)
        delta_dd.append(dd_v - dd_c)

    def interval(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        return {
            "median": round(ordered[len(ordered) // 2], 6),
            "p5": round(ordered[int(0.05 * (len(ordered) - 1))], 6),
            "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 6),
        }

    return {
        "method": f"{block}-day block bootstrap with replacement",
        "seed": seed,
        "resamples": resamples,
        "return_delta": interval(delta_return),
        "sharpe_delta": interval(delta_sharpe),
        "maxdd_delta": interval(delta_dd),
        "probability_return_above_control": round(sum(value > 0 for value in delta_return) / resamples, 6),
        "probability_sharpe_above_control": round(sum(value > 0 for value in delta_sharpe) / resamples, 6),
    }


def curve_without_year(result: PortfolioBacktestResult, excluded_year: int) -> dict[str, float]:
    returns = [
        equity / previous - 1.0
        for previous, equity, timestamp in zip(
            result.equity_curve,
            result.equity_curve[1:],
            result.curve_timestamps[1:],
        )
        if timestamp.year != excluded_year and previous > 0 and equity > 0
    ]
    if not returns:
        return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "days": 0}
    compounded = math.prod(1.0 + value for value in returns)
    mean = statistics.fmean(returns)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    running = peak = 1.0
    max_dd = 0.0
    for value in returns:
        running *= 1.0 + value
        peak = max(peak, running)
        max_dd = max(max_dd, (peak - running) / peak if peak > 0 else 0.0)
    return {
        "return": round(compounded - 1.0, 6),
        "sharpe": round(mean / stdev * math.sqrt(252.0), 6) if stdev > 0 else 0.0,
        "max_drawdown": round(max_dd, 6),
        "days": len(returns),
    }


def load_and_validate_manifest() -> dict[str, object]:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"missing predeclaration manifest: {MANIFEST_PATH}")
    manifest = json.loads(MANIFEST_PATH.read_text())
    if manifest.get("declared_before_results") is not True:
        raise SystemExit("manifest must declare declared_before_results=true")
    declared = manifest.get("variants", {})
    built_in = {
        variant_id: {"base": cls.__name__, "overrides": overrides} for variant_id, (cls, overrides) in VARIANTS.items()
    }
    if declared != built_in:
        raise SystemExit("research VARIANTS drifted from the committed predeclaration")
    if manifest.get("frozen_parameters") != FROZEN_PARAMETERS:
        raise SystemExit("frozen parameters drifted from the committed predeclaration")
    policy = manifest.get("current_policy", {})
    if policy.get("equity_maximum_allocation") != CURRENT_EQUITY_ALLOCATION:
        raise SystemExit("current 0.55 allocation is not declared in the manifest")
    if manifest.get("validation_contract", {}).get("walk_forward_folds") != 20:
        raise SystemExit("validation contract must declare 20 walk-forward folds")
    return manifest


def _configured_observation_strategy(config: Config) -> PortfolioStrategy:
    """Build the configured product observer for artifact provenance only."""
    selection = config.strategy.equity
    return portfolio_strategy_from(
        selection.name,
        selection.parameters,
        timeframe=selection.timeframe,
    )


def _artifact_observation_provenance(
    config: Config,
    full_window: PreparedWindow,
    full_strategy: PortfolioStrategy,
    control_window: PreparedWindow,
) -> dict[str, object]:
    """Return auditable observation/execution provenance for a result artifact.

    A candidate-owned run is only serializable when both sides of the
    preparation/execution boundary are present and complete.  Configured
    observation runs still record the configured strategy identity so every
    artifact has the same shape.
    """
    observation_strategy = full_window.observation_strategy
    if observation_strategy is None:
        if full_window.observation_basis == "candidate_owned":
            raise ValueError("candidate-owned artifact is missing its observation strategy instance")
        observation_strategy = _configured_observation_strategy(config)
    observation_identity = strategy_identity(observation_strategy)
    execution_identity = strategy_identity(full_strategy)
    provenance: dict[str, object] = {
        "observation_basis": full_window.observation_basis,
        "observation_strategy_name": observation_identity["name"],
        "observation_strategy_version": observation_identity["version"],
        "observation_strategy_market": observation_identity["market"],
        "observation_strategy_timeframe": observation_identity["timeframe"],
        "observation_strategy_parameter_digest": observation_identity["parameter_digest_sha256"],
        "observation_universe_hash": full_window.universe_version,
        "observation_universe_component_sha256": canonical_digest(full_window.universe.to_dict()),
        "execution_strategy_identity": execution_identity,
        "control_observation_basis": control_window.observation_basis,
        "control_observation_strategy_identity": (
            strategy_identity(control_window.observation_strategy)
            if control_window.observation_strategy is not None
            else strategy_identity(_configured_observation_strategy(config))
        ),
    }
    if full_window.observation_basis == "candidate_owned":
        required = (
            "observation_basis",
            "observation_strategy_name",
            "observation_strategy_version",
            "observation_strategy_market",
            "observation_strategy_timeframe",
            "observation_strategy_parameter_digest",
            "observation_universe_hash",
            "execution_strategy_identity",
            "control_observation_basis",
        )
        missing = [key for key in required if not provenance.get(key)]
        if missing:
            raise ValueError("candidate-owned artifact provenance is incomplete: " + ", ".join(missing))
    return provenance


def candidate_artifact(
    variant_id: str,
    config: Config,
    full: tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3],
    recent: tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3] | None,
    folds: list[dict[str, object]],
    annual: dict[str, dict[str, object]],
    stress: dict[str, object],
    jackknife: dict[str, object],
    control_full: tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3],
    regimes: Mapping[datetime, str],
    *,
    manifest: Mapping[str, object],
    full_window: PreparedWindow,
    control_window: PreparedWindow | None = None,
) -> dict[str, object]:
    full_result, full_strategy = full
    control_result, control_strategy = control_full
    stats = full_stats(full_result)
    if control_window is None:
        if full_window.observation_basis == "candidate_owned":
            raise ValueError("candidate-owned artifact requires an explicit configured control window")
        control_window = full_window
    observation_provenance = _artifact_observation_provenance(
        config,
        full_window,
        full_strategy,
        control_window,
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "variant": variant_id,
        "strategy_name": full_strategy.name,
        "strategy_class": type(full_strategy).__name__,
        "parameters": dict(full_strategy.parameter_tuple()),
        "full_period": stats,
        "concentration": concentration(full_result),
        "yearly_returns": yearly_returns(full_result),
        "annual_windows": annual,
        "walk_forward": fold_summary(folds),
        "recent_period": (
            {"start": RECENT_START.date().isoformat(), **full_stats(recent[0])}
            if recent is not None
            else {"status": "not_run", "reason": "scope_excludes_recent_window"}
        ),
        "cost_stress": stress,
        "jackknife": jackknife,
        "regime": regime_pnl(full_result, regimes),
        "2020_excluded_curve_diagnostic": curve_without_year(full_result, 2020),
        "diagnostics": strategy_diagnostics(full_strategy, full_result, control_strategy, control_result),
        "bootstrap_vs_control": (
            None
            if variant_id == "control"
            else block_bootstrap_deltas(control_result.equity_curve, full_result.equity_curve)
        ),
        "observation_provenance": observation_provenance,
        "identity": {
            "mission": manifest["mission"],
            "research_commit": git_head(),
            "python_version": platform.python_version(),
            "config_digest_sha256": config_digest(config),
            "dataset": manifest["dataset"],
            "generated_at": datetime.now(UTC).isoformat(),
            "eval_start": EVAL_START.date().isoformat(),
            "eval_end": EVAL_END.date().isoformat(),
            "current_equity_allocation": CURRENT_EQUITY_ALLOCATION,
            "survivorship_bias_limitation": True,
        },
    }


def evaluate_variant(
    variant_id: str,
    config: Config,
    bars: list[Bar],
    terminal_map: Mapping[str, TerminalInfo],
    actions: list[CorporateAction],
    full_window: PreparedWindow,
    control_full: tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3],
    *,
    skip_jackknife: bool,
    all_windows_cache: dict[str, PreparedWindow],
    manifest: Mapping[str, object],
    regimes: Mapping[datetime, str],
    scope: str,
    observation_strategy_factory: Callable[[], PortfolioStrategy] | None = None,
    execution_strategy_factory: Callable[[], PortfolioStrategy] | None = None,
    observation_basis: str | None = None,
    control_window: PreparedWindow | None = None,
) -> dict[str, object]:
    effective_observation_basis = observation_basis
    if effective_observation_basis is None:
        effective_observation_basis = "candidate_owned" if observation_strategy_factory is not None else "configured"
    if full_window.observation_basis != effective_observation_basis:
        raise ValueError("full_window observation basis does not match the requested evaluate_variant factories")

    def window(key: str, start: datetime | None, end: datetime) -> PreparedWindow:
        if key not in all_windows_cache:
            all_windows_cache[key] = prepare_window(
                config,
                bars,
                key=key,
                start=start,
                end=end,
                observation_strategy_factory=observation_strategy_factory,
                execution_strategy_factory=execution_strategy_factory,
                observation_basis=effective_observation_basis,
            )
        elif all_windows_cache[key].observation_basis != effective_observation_basis:
            raise ValueError(f"window cache basis mismatch for {key}")
        return all_windows_cache[key]

    if variant_id == "control":
        full = control_full
    else:
        full = run_case(
            config,
            variant_id,
            full_window,
            terminal_map=terminal_map,
            actions=actions,
            execution_strategy_factory=execution_strategy_factory,
        )
    fold_rows: list[dict[str, object]] = []
    if scope in {"fold", "contract"}:
        for fold_id, start, end in fold_windows():
            fold_window = window(f"fold:{fold_id}", start, end)
            result, _strategy = run_case(
                config,
                variant_id,
                fold_window,
                terminal_map=terminal_map,
                actions=actions,
                execution_strategy_factory=execution_strategy_factory,
            )
            fold_rows.append(
                {
                    "fold_id": fold_id,
                    "start": start.date().isoformat(),
                    "end": end.date().isoformat(),
                    **{
                        key: value
                        for key, value in full_stats(result).items()
                        if key in {"return", "cagr", "sharpe", "max_drawdown", "turnover"}
                    },
                }
            )

    annual: dict[str, dict[str, object]] = {}
    recent: tuple[PortfolioBacktestResult, EquityResidualMomentum1dV3] | None = None
    if scope == "contract":
        for year, start, end in annual_windows():
            annual_window = window(f"annual:{year}", start, end)
            result, _strategy = run_case(
                config,
                variant_id,
                annual_window,
                terminal_map=terminal_map,
                actions=actions,
                execution_strategy_factory=execution_strategy_factory,
            )
            annual[year] = {
                key: full_stats(result)[key]
                for key in ("return", "cagr", "sharpe", "max_drawdown", "turnover", "avg_exposure")
            }

        recent_window = window("recent", RECENT_START, EVAL_END)
        recent = run_case(
            config,
            variant_id,
            recent_window,
            terminal_map=terminal_map,
            actions=actions,
            execution_strategy_factory=execution_strategy_factory,
        )

    stress: dict[str, object] = {"1x": full_stats(full[0])}
    if scope == "contract":
        for multiplier in (2.0, 3.0, 5.0):
            stressed, _strategy = run_case(
                config,
                variant_id,
                full_window,
                terminal_map=terminal_map,
                actions=actions,
                cost_multiplier=multiplier,
                execution_strategy_factory=execution_strategy_factory,
            )
            stress[f"{multiplier:g}x"] = {
                key: full_stats(stressed)[key]
                for key in ("return", "gross_return", "sharpe", "max_drawdown", "turnover", "fees_total")
            }

    jackknife: dict[str, object] = {}
    if scope == "contract" and not skip_jackknife:
        control_top = concentration(control_full[0])["top_symbols"]
        for symbol in list(control_top)[:JACKKNIFE_TOP]:
            reduced = replace(
                full_window,
                universe=universe_without(full_window.universe, frozenset({symbol})),
            )
            reduced = replace(reduced, universe_version=hash_point_in_time_universe(reduced.universe))
            result, _strategy = run_case(
                config,
                variant_id,
                reduced,
                terminal_map=terminal_map,
                actions=actions,
                execution_strategy_factory=execution_strategy_factory,
            )
            jackknife[f"drop_{symbol}"] = {key: full_stats(result)[key] for key in ("return", "sharpe", "max_drawdown")}
        banned = frozenset(list(control_top)[:3])
        reduced = replace(full_window, universe=universe_without(full_window.universe, banned))
        reduced = replace(reduced, universe_version=hash_point_in_time_universe(reduced.universe))
        result, _strategy = run_case(
            config,
            variant_id,
            reduced,
            terminal_map=terminal_map,
            actions=actions,
            execution_strategy_factory=execution_strategy_factory,
        )
        jackknife["drop_top3_combined"] = {key: full_stats(result)[key] for key in ("return", "sharpe", "max_drawdown")}

    artifact = candidate_artifact(
        variant_id,
        config,
        full,
        recent,
        fold_rows,
        annual,
        stress,
        jackknife,
        control_full,
        regimes,
        manifest=manifest,
        full_window=full_window,
        control_window=control_window,
    )
    artifact["validation_scope"] = scope
    return artifact


def _dataset_identity() -> dict[str, object]:
    return {
        "bars_csv": str(DATASET_V5.relative_to(REPO)),
        "bars_sha256": sha256_of_file(DATASET_V5),
        "lifetimes_csv": str(LIFETIMES_V5.relative_to(REPO)),
        "lifetimes_sha256": sha256_of_file(LIFETIMES_V5),
        "corporate_actions_csv": str(CA_V5.relative_to(REPO)),
        "corporate_actions_sha256": sha256_of_file(CA_V5),
    }


def write_summary(artifacts: Mapping[str, Mapping[str, object]]) -> None:
    control = artifacts["control"]
    control_stats = control["full_period"]
    summary: dict[str, object] = {}
    for variant_id, artifact in artifacts.items():
        stats = artifact["full_period"]
        summary[variant_id] = {
            "strategy_name": artifact["strategy_name"],
            "return": stats["return"],
            "cagr": stats["cagr"],
            "sharpe": stats["sharpe"],
            "max_drawdown": stats["max_drawdown"],
            "turnover": stats["turnover"],
            "avg_exposure": stats["avg_exposure"],
            "delta_return_vs_control": round(float(stats["return"]) - float(control_stats["return"]), 6),
            "delta_sharpe_vs_control": round(float(stats["sharpe"]) - float(control_stats["sharpe"]), 6),
            "delta_max_drawdown_vs_control": round(
                float(stats["max_drawdown"]) - float(control_stats["max_drawdown"]), 6
            ),
            "validation_scope": artifact["validation_scope"],
            "median_fold_return": artifact["walk_forward"]["median_fold_return"]
            if artifact["walk_forward"]["fold_count"]
            else None,
            "positive_fold_ratio": artifact["walk_forward"]["positive_fold_ratio"]
            if artifact["walk_forward"]["fold_count"]
            else None,
            "recent_return": artifact["recent_period"].get("return"),
            "active_return_correlation": artifact["diagnostics"]["weekly_active_return_correlation"],
        }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


def write_fold_deltas(artifacts: Mapping[str, Mapping[str, object]]) -> None:
    """Write paired candidate-vs-control fold evidence when folds ran."""
    control_rows = {str(row["fold_id"]): row for row in artifacts["control"]["walk_forward"].get("folds", [])}
    output: dict[str, object] = {}
    for variant_id, artifact in artifacts.items():
        if variant_id == "control" or not artifact["walk_forward"].get("fold_count"):
            continue
        rows: list[dict[str, object]] = []
        for row in artifact["walk_forward"]["folds"]:
            control = control_rows.get(str(row["fold_id"]))
            if control is None:
                continue
            rows.append(
                {
                    "fold_id": row["fold_id"],
                    "return_delta": round(float(row["return"]) - float(control["return"]), 6),
                    "sharpe_delta": round(float(row["sharpe"]) - float(control["sharpe"]), 6),
                    "max_drawdown_delta": round(float(row["max_drawdown"]) - float(control["max_drawdown"]), 6),
                }
            )
        return_deltas = [float(row["return_delta"]) for row in rows]
        sharpe_deltas = [float(row["sharpe_delta"]) for row in rows]
        dd_deltas = [float(row["max_drawdown_delta"]) for row in rows]
        output[variant_id] = {
            "folds": rows,
            "fold_count": len(rows),
            "return_delta_median": round(statistics.median(return_deltas), 6) if return_deltas else 0.0,
            "return_wins": sum(value > 0 for value in return_deltas),
            "sharpe_delta_median": round(statistics.median(sharpe_deltas), 6) if sharpe_deltas else 0.0,
            "sharpe_wins": sum(value > 0 for value in sharpe_deltas),
            "max_drawdown_delta_median": round(statistics.median(dd_deltas), 6) if dd_deltas else 0.0,
            "max_drawdown_wins": sum(value < 0 for value in dd_deltas),
        }
    (OUT_DIR / "fold_deltas.json").write_text(json.dumps(output, indent=1) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", default=",".join(VARIANTS), help="comma-separated variant ids")
    parser.add_argument("--skip-jackknife", action="store_true")
    parser.add_argument(
        "--scope",
        choices=("headline", "fold", "contract"),
        default="contract",
        help="headline=full-window only; fold=full+20 folds; contract=all declared diagnostics",
    )
    args = parser.parse_args()
    manifest = load_and_validate_manifest()
    requested = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in requested if item not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variants: {unknown}")
    if "control" not in requested:
        requested.insert(0, "control")
    config = load_config(str(REPO / "config/settings.yaml"))
    actual_allocation = float(config.strategy.equity.maximum_allocation)
    if abs(actual_allocation - CURRENT_EQUITY_ALLOCATION) > 1e-12:
        raise SystemExit(f"config equity allocation is {actual_allocation}; expected current policy 0.55")
    if "equity_residual_momentum_v3" not in supported_portfolio_strategies():
        raise SystemExit("production V3 must remain registered as the control")
    if _dataset_identity() != manifest["dataset"]:
        raise SystemExit("canonical dataset hash differs from the predeclared manifest")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"loading canonical v5 bars: {DATASET_V5}", flush=True)
    bars = load_bars(DATASET_V5)
    terminal_map, _lifetimes = load_lifetimes()
    actions = load_corporate_actions_all()
    regimes = spy_daily_regimes(bars)
    cache: dict[str, PreparedWindow] = {}
    full_window = prepare_window(config, bars, key="full", start=None, end=EVAL_END)
    print(f"PIT universe ready: {len(full_window.universe.snapshots)} snapshots", flush=True)
    print("running same-run current-policy V3 control", flush=True)
    control_full = run_case(config, "control", full_window, terminal_map=terminal_map, actions=actions)

    artifacts: dict[str, dict[str, object]] = {}
    for variant_id in requested:
        started = datetime.now(UTC)
        print(f"=== {variant_id} ===", flush=True)
        artifact = evaluate_variant(
            variant_id,
            config,
            bars,
            terminal_map,
            actions,
            full_window,
            control_full,
            skip_jackknife=args.skip_jackknife,
            all_windows_cache=cache,
            manifest=manifest,
            regimes=regimes,
            scope=args.scope,
        )
        artifacts[variant_id] = artifact
        path = OUT_DIR / f"{variant_id}.json"
        path.write_text(json.dumps(artifact, indent=1, default=str) + "\n")
        stats = artifact["full_period"]
        print(
            f"{variant_id}: ret={float(stats['return']):+.4f} sharpe={float(stats['sharpe']):+.3f} "
            f"maxDD={float(stats['max_drawdown']):.4f} ({(datetime.now(UTC) - started).total_seconds():.0f}s)",
            flush=True,
        )

    write_summary(artifacts)
    write_fold_deltas(artifacts)
    manifest_out = json.loads(MANIFEST_PATH.read_text())
    manifest_out["results_sha256"] = {path.name: sha256_of_file(path) for path in sorted(OUT_DIR.glob("*.json"))}
    MANIFEST_PATH.write_text(json.dumps(manifest_out, indent=1) + "\n")
    print(f"wrote {len(artifacts)} candidate artifacts to {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
