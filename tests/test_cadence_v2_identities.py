"""Cadence-v2 strategy identities (2026-08-18 cadence mission).

v2 identities carry the SAME validated alpha math as v1; only the rebalance
cadence differs (crypto 96h). The cadence lives in the strategy parameters so
configuration digests, persisted decisions and the dashboard identity are
distinct from v1. v1 stays frozen and untouched.
"""

from __future__ import annotations

import pytest

from parayabuck.portfolio import (
    portfolio_strategy_from,
    rebalance_policy_for,
    supported_portfolio_strategies,
)

FROZEN_CRYPTO = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 168}


def test_crypto_v2_registered_and_96h() -> None:
    # default identity: constructed without rebalance_hours -> 96h
    v2 = portfolio_strategy_from("crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2})
    assert v2.name == "crypto_low_variance_v2"
    assert v2.version == "2.0.0"
    assert dict(v2.parameter_tuple())["rebalance_hours"] == 96
    assert rebalance_policy_for(v2).hours == 96
    assert v2.spec().rebalance_policy == "hours:96"
    # explicit override still wins (operator may pin a different cadence)
    pinned = portfolio_strategy_from("crypto_low_variance_v2", {**FROZEN_CRYPTO, "rebalance_hours": 168})
    assert rebalance_policy_for(pinned).hours == 168
    # v1 stays 168h
    v1 = portfolio_strategy_from("crypto_low_variance_v1", FROZEN_CRYPTO)
    assert dict(v1.parameter_tuple())["rebalance_hours"] == 168
    assert rebalance_policy_for(v1).hours == 168


def test_v2_identities_distinct_in_factory() -> None:
    names = supported_portfolio_strategies()
    assert "equity_leveraged_trend_v1" in names
    assert "crypto_low_variance_v1" in names
    assert "crypto_low_variance_v2" in names
    assert len({portfolio_strategy_from(n).name for n in names if n.endswith("_v1")}) >= 1


def test_v2_requires_no_alpha_parameter_changes() -> None:
    # construction with the frozen v1 parameter sets must succeed unchanged;
    # v2's own cadence default applies when the cadence parameter is absent
    crypto_v2 = portfolio_strategy_from("crypto_low_variance_v2", {"variance_lookback_days": 22, "top_n": 2})
    assert rebalance_policy_for(crypto_v2).hours == 96


def test_crypto_v2_rejects_invalid_hours_like_v1() -> None:
    with pytest.raises(ValueError, match="multiple of 24"):
        portfolio_strategy_from("crypto_low_variance_v2", {**FROZEN_CRYPTO, "rebalance_hours": 97})
