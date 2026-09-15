"""No-return and lifecycle invariants for the corrected replay mechanics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

from .corrected_mechanics import (
    FROZEN_MAX_POSITIONS,
    FROZEN_MAX_SINGLE_WEIGHT,
    FROZEN_REVERSAL_ALLOCATION,
    CorrectedReplayEngine,
    compose_sleeve_aware_treatment_target,
)


def _context() -> PortfolioContext:
    timestamp = datetime(2024, 1, 8, tzinfo=UTC)
    return PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="fixture",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _target(prefix: str, count: int, weight: float = 0.10) -> TargetPortfolio:
    timestamp = datetime(2024, 1, 8, tzinfo=UTC)
    return TargetPortfolio(
        prefix,
        "fixture",
        timestamp,
        tuple(TargetWeight(f"{prefix}{index}", weight, rank=index + 1) for index in range(count)),
        1.0 - count * weight,
    )


def test_disjoint_targets_retain_both_sleeves_under_six_slots() -> None:
    result = compose_sleeve_aware_treatment_target(_context(), _target("V", 8), _target("R", 6))
    attribution = result.metadata["treatment_attribution"]
    assert attribution["slot_quotas"] == {"v3": 4, "reversal": 2}
    assert len(result.weights) == FROZEN_MAX_POSITIONS
    assert attribution["post_position_cap_v3_exposure"] > 0
    assert attribution["post_position_cap_reversal_exposure"] > 0
    assert attribution["no_cross_sleeve_reallocation"] is True


def test_overlap_uses_one_slot_and_preserves_source_contributions() -> None:
    timestamp = datetime(2024, 1, 8, tzinfo=UTC)
    v3 = TargetPortfolio(
        "v3",
        "fixture",
        timestamp,
        (TargetWeight("AAA", 0.10, rank=1), TargetWeight("BBB", 0.10, rank=2)),
        0.80,
    )
    reversal = TargetPortfolio(
        "reversal",
        "fixture",
        timestamp,
        (TargetWeight("AAA", 0.10, rank=1), TargetWeight("CCC", 0.10, rank=2)),
        0.80,
    )
    result = compose_sleeve_aware_treatment_target(_context(), v3, reversal)
    attribution = result.metadata["treatment_attribution"]
    source = attribution["source_contributions"]["AAA"]
    assert attribution["overlap_symbols"] == ["AAA"]
    assert len(result.weights) == 3
    assert source["v3"] == pytest.approx(0.075)
    assert source["reversal"] == pytest.approx(0.025)
    assert result.weight_for("AAA", Market.EQUITY) == pytest.approx(FROZEN_MAX_SINGLE_WEIGHT)


def test_capacity_shortfall_stays_cash() -> None:
    result = compose_sleeve_aware_treatment_target(_context(), _target("V", 8), _target("R", 6))
    attribution = result.metadata["treatment_attribution"]
    assert attribution["allocation_capacity_shortfall"] is True
    assert attribution["unused_v3_allocation"] > 0
    assert attribution["unused_reversal_allocation"] > 0
    assert result.cash_weight == pytest.approx(1.0 - result.invested_weight)


def test_no_reversal_candidates_leave_reversal_budget_in_cash() -> None:
    empty = TargetPortfolio(
        "reversal",
        "fixture",
        datetime(2024, 1, 8, tzinfo=UTC),
        (),
        1.0,
    )
    result = compose_sleeve_aware_treatment_target(_context(), _target("V", 8), empty)
    attribution = result.metadata["treatment_attribution"]
    assert attribution["zero_reversal_exposure_reason"] == "no_reversal_candidates"
    assert attribution["post_position_cap_reversal_exposure"] == 0
    assert attribution["unused_reversal_allocation"] == pytest.approx(FROZEN_REVERSAL_ALLOCATION)


def _bar(timestamp: datetime, symbol: str, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000.0, Market.EQUITY)


class _TerminalFixtureStrategy(PortfolioStrategy):
    name = "research_terminal_fixture"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "weekly"

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = (TargetWeight("ESRX", 0.20, rank=1),) if "ESRX" in context.history else ()
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            weights,
            1.0 - sum(weight.weight for weight in weights),
            universe_version=context.universe_version,
        )


def test_terminal_map_closes_real_last_bar_and_unwedges_future_decisions() -> None:
    start = datetime(2024, 1, 8, tzinfo=UTC)
    terminal = start + timedelta(days=2)
    later = start + timedelta(days=7)
    bars = [
        _bar(start, "SPY", 100.0),
        _bar(start, "ESRX", 50.0),
        _bar(start + timedelta(days=1), "SPY", 101.0),
        _bar(start + timedelta(days=1), "ESRX", 51.0),
        _bar(terminal, "SPY", 102.0),
        _bar(terminal, "ESRX", 52.0),
        _bar(later, "SPY", 103.0),
    ]
    from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot

    universe = PointInTimeUniverse(
        Market.EQUITY,
        (
            UniverseSnapshot(
                effective_at=start,
                symbols=frozenset({"SPY", "ESRX"}),
                observation_set=("SPY", "ESRX"),
                maximum_stream_symbols=2,
                pinned_symbols=("SPY",),
            ),
        ),
        "terminal fixture",
    )
    engine = CorrectedReplayEngine(
        _TerminalFixtureStrategy(),
        RebalancePolicy(Market.EQUITY, "weekly"),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        risk_limits=PortfolioRiskLimits(
            maximum_gross=0.55,
            maximum_equity=0.55,
            maximum_equity_asset=0.10,
            minimum_cash=0.45,
            maximum_positions=6,
        ),
        maximum_allocation=0.55,
        execution_policy=None,
        universe=universe,
        universe_version="terminal-fixture",
        allow_static_universe=True,
        terminal_map={
            "ESRX": {
                "last_bar": terminal,
                "terminal_event_class": "POSSIBLE_DELISTING",
            }
        },
    )
    result = engine.run(bars, liquidate_at_end=False)
    assert len(result.rebalances) == 2
    assert all(item["decision_recorded"] for item in engine.coverage_attempts)
    assert result.state.holdings == {}
    assert len(result.terminal_events) == 1
    assert result.terminal_events[0]["policy"] == "TERMINAL_BAR"
    assert result.terminal_events[0]["last_close"] == pytest.approx(52.0)
    assert result.terminal_events[0]["terminal_event_class"] == "POSSIBLE_DELISTING"
