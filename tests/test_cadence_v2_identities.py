"""Cadence-v2 strategy identities (2026-08-18 cadence mission).

v2 identities carry the SAME validated alpha math as v1; only the rebalance
cadence differs (equity weekly, crypto 96h). The cadence lives in the
strategy parameters so configuration digests, persisted decisions and the
dashboard identity are distinct from v1. v1 stays frozen and untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parayabuck.core.models import Market
from parayabuck.portfolio import (
    portfolio_strategy_from,
    rebalance_policy_for,
    supported_portfolio_strategies,
)
from parayabuck.portfolio.base import PortfolioContext

FROZEN_EQUITY = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
FROZEN_CRYPTO = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 168}


def _context(strategy) -> PortfolioContext:
    return PortfolioContext(
        decision_timestamp=datetime(2024, 1, 15, tzinfo=UTC),
        market=strategy.market,
        timeframe=strategy.timeframe,
        history={},
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY" if strategy.market == Market.EQUITY else "BTC/USD",
    )


def test_equity_v2_registered_and_weekly() -> None:
    v2 = portfolio_strategy_from("equity_residual_momentum_v2", FROZEN_EQUITY)
    assert v2.name == "equity_residual_momentum_v2"
    assert v2.version == "2.0.0"
    assert dict(v2.parameter_tuple())["rebalance_frequency"] == "weekly"
    assert rebalance_policy_for(v2).frequency == "weekly"
    assert v2.spec().rebalance_policy == "weekly"
    # v1 stays monthly and carries no cadence parameter
    v1 = portfolio_strategy_from("equity_residual_momentum_v1", FROZEN_EQUITY)
    assert "rebalance_frequency" not in dict(v1.parameter_tuple())
    assert rebalance_policy_for(v1).frequency == "monthly"


def test_equity_v2_signal_math_identical_to_v1() -> None:
    v1 = portfolio_strategy_from("equity_residual_momentum_v1", FROZEN_EQUITY)
    v2 = portfolio_strategy_from("equity_residual_momentum_v2", FROZEN_EQUITY)
    assert v1.minimum_history_bars == v2.minimum_history_bars
    assert v2.parameter_tuple() == tuple(
        sorted({**FROZEN_EQUITY, "rebalance_frequency": "weekly"}.items())
    )
    # same context -> same scores (decision requires history; verify parameter
    # surface only here; full behavioral parity is covered by the backtest)
    for key, value in v1.parameter_tuple():
        assert dict(v2.parameter_tuple())[key] == value


def test_crypto_v2_registered_and_96h() -> None:
    # default identity: constructed without rebalance_hours -> 96h
    v2 = portfolio_strategy_from(
        "crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2}
    )
    assert v2.name == "crypto_low_variance_v2"
    assert v2.version == "2.0.0"
    assert dict(v2.parameter_tuple())["rebalance_hours"] == 96
    assert rebalance_policy_for(v2).hours == 96
    assert v2.spec().rebalance_policy == "hours:96"
    # explicit override still wins (operator may pin a different cadence)
    pinned = portfolio_strategy_from(
        "crypto_low_variance_v2", {**FROZEN_CRYPTO, "rebalance_hours": 168}
    )
    assert rebalance_policy_for(pinned).hours == 168
    # v1 stays 168h
    v1 = portfolio_strategy_from("crypto_low_variance_v1", FROZEN_CRYPTO)
    assert dict(v1.parameter_tuple())["rebalance_hours"] == 168
    assert rebalance_policy_for(v1).hours == 168


def test_v2_identities_distinct_in_factory() -> None:
    names = supported_portfolio_strategies()
    assert "equity_residual_momentum_v1" in names
    assert "equity_residual_momentum_v2" in names
    assert "crypto_low_variance_v1" in names
    assert "crypto_low_variance_v2" in names
    assert len({portfolio_strategy_from(n).name for n in names if n.endswith("_v1")}) >= 1


def test_v2_requires_no_alpha_parameter_changes() -> None:
    # construction with the frozen v1 parameter sets must succeed unchanged;
    # v2's own cadence default applies when the cadence parameter is absent
    equity_v2 = portfolio_strategy_from("equity_residual_momentum_v2", FROZEN_EQUITY)
    assert rebalance_policy_for(equity_v2).frequency == "weekly"
    crypto_v2 = portfolio_strategy_from(
        "crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2}
    )
    assert rebalance_policy_for(crypto_v2).hours == 96


def test_crypto_v2_rejects_invalid_hours_like_v1() -> None:
    with pytest.raises(ValueError, match="multiple of 24"):
        portfolio_strategy_from("crypto_low_variance_v2", {**FROZEN_CRYPTO, "rebalance_hours": 97})
