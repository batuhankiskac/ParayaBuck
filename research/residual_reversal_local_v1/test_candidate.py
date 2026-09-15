"""Deterministic contract tests for the frozen research candidate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum import _aligned_returns
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import inverse_volatility_weights

from .candidate import (
    EquityShortTermResidualReversalV1,
    compose_shared_treatment_target,
    frozen_reversal_parameters,
    scale_sleeve_weights,
)


def _bar(timestamp: datetime, symbol: str, close: float, volume: float = 1_000_000.0) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, volume, Market.EQUITY)


def test_candidate_uses_exact_last_21_returns_and_zero_skip() -> None:
    candidate = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    inside = [-0.01] * 21
    expected = 0.99**21 - 1.0
    assert candidate._candidate_score([10.0, *inside], formation=21, skip=0) == pytest.approx(expected)
    assert candidate._candidate_score([-10.0, *inside], formation=21, skip=0) == pytest.approx(expected)
    assert candidate._candidate_score([0.01, *inside[:-1], -0.02], formation=21, skip=0) < expected
    assert candidate._candidate_passes_gate([], -0.2, formation=21, skip=0)


def test_negative_residual_performers_rank_before_positive_performers() -> None:
    candidate = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    original = candidate.__class__.__mro__[1]._score_candidates

    def stub(_self: object, _context: object):
        return ((0.10, "POSITIVE"), (-0.20, "NEGATIVE")), {"POSITIVE": 1.0, "NEGATIVE": 1.0}

    candidate.__class__.__mro__[1]._score_candidates = stub
    try:
        ranked, _vols = candidate._score_candidates(None)  # type: ignore[arg-type]
    finally:
        candidate.__class__.__mro__[1]._score_candidates = original
    assert ranked == ((-0.20, "NEGATIVE"), (0.10, "POSITIVE"))


def test_residual_alignment_is_inner_joined_by_timestamp() -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    stock = (
        _bar(start, "ABC", 100.0),
        _bar(start + timedelta(days=2), "ABC", 90.0),
        _bar(start + timedelta(days=3), "ABC", 99.0),
    )
    market = {
        start + timedelta(days=offset): close
        for offset, close in ((0, 100.0), (1, 101.0), (2, 102.0), (3, 103.0))
    }
    stock_returns, market_returns = _aligned_returns(stock, market, 3) or ([], [])
    assert stock_returns == pytest.approx([-0.10, 0.10])
    assert market_returns == pytest.approx([0.02, 103.0 / 102.0 - 1.0])


def test_weekly_policy_changes_only_at_iso_week_boundary() -> None:
    policy = RebalancePolicy(Market.EQUITY, "weekly")
    monday = datetime(2024, 1, 8, tzinfo=UTC)
    friday = datetime(2024, 1, 12, tzinfo=UTC)
    next_monday = datetime(2024, 1, 15, tzinfo=UTC)
    assert policy.is_period_start(None, monday)
    assert not policy.is_period_start(monday, friday)
    assert policy.is_period_start(friday, next_monday)


def test_inverse_volatility_weighting_is_used_with_a_real_cap() -> None:
    weights = dict(
        inverse_volatility_weights(
            ("LOW_VOL", "HIGH_VOL"),
            {"LOW_VOL": 0.10, "HIGH_VOL": 0.20},
            maximum_weight=0.80,
        )
    )
    assert weights["LOW_VOL"] == pytest.approx(2.0 / 3.0)
    assert weights["HIGH_VOL"] == pytest.approx(1.0 / 3.0)
    assert max(weights.values()) <= 0.80


def test_treatment_keeps_frozen_75_25_sleeves_without_upward_renormalization() -> None:
    timestamp = datetime(2024, 1, 8, tzinfo=UTC)
    context = PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    v3 = TargetPortfolio(
        "v3",
        "1",
        timestamp,
        (TargetWeight("AAA", 0.40), TargetWeight("BBB", 0.30)),
        0.30,
    )
    reversal = TargetPortfolio(
        "reversal",
        "1",
        timestamp,
        (TargetWeight("BBB", 0.20), TargetWeight("CCC", 0.10)),
        0.70,
    )
    scaled_v3 = scale_sleeve_weights(v3, sleeve_fraction=0.75)
    scaled_reversal = scale_sleeve_weights(reversal, sleeve_fraction=0.25)
    assert sum(scaled_v3.values()) == pytest.approx(0.4125)
    assert sum(scaled_reversal.values()) == pytest.approx(0.075)
    treatment = compose_shared_treatment_target(context, v3, reversal)
    assert treatment.invested_weight == pytest.approx(sum(scaled_v3.values()) + sum(scaled_reversal.values()))
    assert treatment.cash_weight == pytest.approx(1.0 - treatment.invested_weight)
    assert all(weight.rank == 0 for weight in treatment.weights)


class _FixedTargetStrategy(PortfolioStrategy):
    name = "research_fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1
    observed_latest: list[datetime] = []

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        latest = [bar.timestamp for bars in context.history.values() for bar in bars]
        assert latest and max(latest) <= context.decision_timestamp
        self.observed_latest.append(max(latest))
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            (TargetWeight("SPY", 0.20),),
            0.80,
            universe_version="test",
        )


def test_engine_caps_position_and_fills_only_after_completed_decision_bar() -> None:
    _FixedTargetStrategy.observed_latest = []
    start = datetime(2025, 1, 2, tzinfo=UTC)
    bars = [_bar(start + timedelta(days=index), "SPY", 100.0 + index) for index in range(3)]
    engine = PortfolioBacktestEngine(
        _FixedTargetStrategy(),
        RebalancePolicy(Market.EQUITY, "weekly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=2.0,
        risk_limits=PortfolioRiskLimits(
            maximum_gross=0.55,
            maximum_equity=0.55,
            maximum_equity_asset=0.10,
            minimum_cash=0.45,
            maximum_positions=6,
        ),
        maximum_allocation=0.55,
        execution_policy=None,
        allow_static_universe=True,
    )
    result = engine.run(bars)
    assert result.rebalances
    assert max(weight for _symbol, weight in result.rebalances[0].target_weights) <= 0.10
    assert result.fills
    assert _FixedTargetStrategy.observed_latest == [start]
    assert all(fill.decision_timestamp < fill.timestamp for fill in result.fills)
    assert result.fills[0].side == OrderSide.BUY
    assert result.fills[0].timestamp == start + timedelta(days=1)
