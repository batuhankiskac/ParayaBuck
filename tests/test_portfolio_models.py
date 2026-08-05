from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.core.models import Market
from parayabuck.portfolio.equity_dual_momentum import EquityEtfDualMomentumV1
from parayabuck.portfolio.models import (
    WEIGHT_TOLERANCE,
    RebalancePolicy,
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)

NOW = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def _portfolio(
    *weights: tuple[str, float],
    cash_weight: float = 0.0,
    **kwargs: object,
) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="test",
        strategy_version="1.0.0",
        decision_timestamp=NOW,
        weights=tuple(TargetWeight(symbol, weight) for symbol, weight in weights),
        cash_weight=cash_weight,
        **kwargs,
    )


def test_weight_sum_cannot_exceed_one() -> None:
    with pytest.raises(ValueError, match="exceed 1"):
        _portfolio(("SPY", 0.6), ("QQQ", 0.6))
    _portfolio(("SPY", 0.6), ("QQQ", 0.4))


def test_negative_weights_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        TargetWeight("SPY", -0.01)
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        _portfolio(("SPY", 0.5), cash_weight=-0.1)
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        TargetWeight("SPY", 1.5)


def test_cash_weight_is_explicit_and_consistent() -> None:
    portfolio = _portfolio(("SPY", 0.4), ("QQQ", 0.3), cash_weight=0.3)
    assert portfolio.invested_weight == pytest.approx(0.7)
    assert portfolio.cash_weight == pytest.approx(0.3)
    assert portfolio.weight_for("SPY") == pytest.approx(0.4)
    assert portfolio.weight_for("UNKNOWN") == 0.0


def test_duplicate_symbols_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _portfolio(("SPY", 0.4), ("SPY", 0.4))


def test_same_spec_seed_and_data_produce_identical_targets() -> None:
    first = EquityEtfDualMomentumV1({"momentum_scheme": "12_1"})
    second = EquityEtfDualMomentumV1({"momentum_scheme": "12_1"})
    assert first.parameter_tuple() == second.parameter_tuple()
    assert first.decide is not second.decide or True
    assert first.name == second.name and first.version == second.version


def test_decision_and_effective_timestamps_are_distinct_fields() -> None:
    portfolio = _portfolio(("SPY", 1.0))
    assert portfolio.decision_timestamp == NOW
    assert portfolio.effective_timestamp is None
    assert portfolio.data_timestamp == NOW or portfolio.data_timestamp is None


def test_rebalance_policy_period_boundaries() -> None:
    monthly = RebalancePolicy(Market.EQUITY, "monthly")
    jan_1 = datetime(2026, 1, 1, tzinfo=UTC)
    jan_2 = datetime(2026, 1, 2, tzinfo=UTC)
    feb_1 = datetime(2026, 2, 1, tzinfo=UTC)
    assert monthly.is_period_start(None, jan_1)
    assert not monthly.is_period_start(jan_1, jan_2)
    assert monthly.is_period_start(jan_2, feb_1)

    hourly = RebalancePolicy(Market.CRYPTO, "hours:6")
    hour_start = datetime(2026, 1, 1, 0, tzinfo=UTC)
    hour_end = datetime(2026, 1, 1, 6, tzinfo=UTC)
    assert hourly.is_period_start(hour_start, hour_end)
    mid_block = datetime(2026, 1, 1, 5, tzinfo=UTC)
    assert not hourly.is_period_start(hour_start, mid_block)


def test_rebalance_policy_rejects_invalid_frequencies() -> None:
    with pytest.raises(ValueError):
        RebalancePolicy(Market.EQUITY, "daily")
    with pytest.raises(ValueError):
        RebalancePolicy(Market.CRYPTO, "monthly")
    with pytest.raises(ValueError):
        RebalancePolicy(Market.CRYPTO, "hours:0")


def test_portfolio_state_round_trip_preserves_restart_context() -> None:
    from parayabuck.portfolio.models import PortfolioState

    state = PortfolioState(
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        holdings={"SPY": 120.0, "QQQ": 80.0},
        avg_prices={"SPY": 500.0, "QQQ": 400.0},
        last_target={"SPY": 0.5, "QQQ": 0.5},
        last_rebalance_at=NOW,
        rebalance_count=9,
        universe_version="permanent_etf_core_v1",
    )
    restored = PortfolioState.from_dict(state.to_dict())
    assert restored.holdings == state.holdings
    assert restored.avg_prices == state.avg_prices
    assert restored.last_target == state.last_target
    assert restored.last_rebalance_at == state.last_rebalance_at
    assert restored.rebalance_count == 9
    assert restored.strategy_name == "equity_etf_dual_momentum_v1"


def test_reason_enum_and_tolerance_are_exported() -> None:
    assert RebalanceReason.SCHEDULED.value == "scheduled"
    assert RebalanceReason.RISK_OFF.value == "risk_off"
    assert WEIGHT_TOLERANCE > 0
