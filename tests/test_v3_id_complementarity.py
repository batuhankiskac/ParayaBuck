from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetWeight
from research.v3_id_complementarity_v1.combination_runner import (
    CORE_ALLOCATION,
    SATELLITE_ALLOCATION,
    CoreSatelliteStrategy,
    UnionEventPolicy,
)
from research.v3_id_complementarity_v1.frozen_candidate_strategy import (
    EquityInformationDiscretenessContinuous1dV1,
)


def _context(timestamp: datetime) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="synthetic",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _strategy(mode: str = "C") -> CoreSatelliteStrategy:
    return CoreSatelliteStrategy(
        mode=mode,
        monthly_schedule=(),
        weekly_schedule=(),
        v3_parameters={
            "formation_bars": 252,
            "skip_bars": 21,
            "beta_window_bars": 273,
            "residual_vol_window": 63,
            "top_n": 8,
            "max_single_weight": 0.10,
            "ivol_reject_quantile": 0.70,
            "exclude_symbols": ["SPY"],
            "rebalance_frequency": "weekly",
        },
    )


def test_union_policy_accepts_asynchronous_monthly_and_weekly_events() -> None:
    monthly = datetime(2022, 1, 31, tzinfo=UTC)
    weekly = datetime(2022, 2, 7, tzinfo=UTC)
    policy = UnionEventPolicy(frozenset({monthly, weekly}))
    assert policy.is_period_start(None, monthly)
    assert policy.is_period_start(monthly, weekly)
    assert not policy.is_period_start(weekly, datetime(2022, 2, 8, tzinfo=UTC))


def test_treatment_uses_one_shared_target_with_nonoverlapping_sources() -> None:
    strategy = _strategy("C")
    strategy.core_targets = {
        "AAA": TargetWeight("AAA", CORE_ALLOCATION / 5, "v3_core_residual_momentum", rank=1),
        "CCC": TargetWeight("CCC", CORE_ALLOCATION / 5, "v3_core_residual_momentum", rank=2),
    }
    strategy.reservation_symbol = "BBB"
    strategy.reservation_record = {"id": -0.1, "pret": 0.2, "rank": 1}
    target = strategy._target(_context(datetime(2022, 1, 31, tzinfo=UTC)), monthly=True, weekly=False)
    assert len(target.weights) == 3
    assert target.weight_for("BBB") == SATELLITE_ALLOCATION
    assert target.cash_weight > 0.0
    assert target.metadata["source_map"] == {"AAA": "v3_core", "BBB": "id_satellite", "CCC": "v3_core"}


def test_reservation_transition_prunes_only_the_newly_reserved_core_name() -> None:
    strategy = _strategy("B")
    strategy.core_targets = {
        "AAA": TargetWeight("AAA", 0.09, "v3_core_residual_momentum", rank=1),
        "BBB": TargetWeight("BBB", 0.09, "v3_core_residual_momentum", rank=2),
    }
    strategy.reservation_symbol = "BBB"
    strategy._prune_reserved_core()
    assert tuple(strategy.core_targets) == ("AAA",)


def test_same_timestamp_updates_reservation_before_weekly_core() -> None:
    timestamp = datetime(2022, 1, 31, tzinfo=UTC)
    strategy = CoreSatelliteStrategy(
        mode="B",
        monthly_schedule=(timestamp,),
        weekly_schedule=(timestamp,),
        v3_parameters={
            "formation_bars": 252,
            "skip_bars": 21,
            "beta_window_bars": 273,
            "residual_vol_window": 63,
            "top_n": 8,
            "max_single_weight": 0.10,
            "ivol_reject_quantile": 0.70,
            "exclude_symbols": ["SPY"],
            "rebalance_frequency": "weekly",
        },
    )
    calls: list[str] = []

    def reservation(_context: PortfolioContext) -> None:
        calls.append("monthly")
        strategy.reservation_symbol = "BBB"

    def core(_context: PortfolioContext) -> None:
        calls.append("weekly")
        strategy.core_targets = {"AAA": TargetWeight("AAA", 0.09, "v3_core_residual_momentum", rank=1)}

    with (
        patch.object(strategy, "_update_reservation", side_effect=reservation),
        patch.object(strategy, "_core_decision", side_effect=core),
    ):
        strategy.decide(_context(timestamp))
    assert calls == ["monthly", "weekly"]


def test_frozen_candidate_still_requires_exact_formation_and_skip_parameters() -> None:
    candidate = EquityInformationDiscretenessContinuous1dV1(
        {
            "formation_bars": 252,
            "skip_bars": 21,
            "top_n": 6,
            "max_single_weight": 0.10,
            "maximum_allocation": 0.55,
        }
    )
    assert candidate.minimum_history_bars == 274
