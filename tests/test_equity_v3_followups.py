from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_v3_equity_followups as followups  # noqa: E402


def test_constant_correlation_shrinkage_is_finite_and_bounded() -> None:
    returns = np.asarray(
        [
            [0.01, 0.012, -0.002],
            [-0.005, -0.004, 0.006],
            [0.008, 0.009, 0.001],
            [0.003, 0.004, -0.003],
            [-0.002, -0.001, 0.002],
        ]
    )
    covariance, intensity = followups.constant_correlation_shrinkage(returns)
    assert covariance.shape == (3, 3)
    assert np.all(np.isfinite(covariance))
    assert np.allclose(covariance, covariance.T)
    assert 0.0 <= intensity <= 1.0


def test_equal_risk_contribution_is_long_only_and_normalized() -> None:
    covariance = np.asarray([[0.04, 0.006], [0.006, 0.09]])
    weights = followups.equal_risk_contribution(covariance)
    assert weights.sum() == pytest.approx(1.0)
    assert np.all(weights > 0)
    contributions = weights * (covariance @ weights)
    assert contributions[0] == pytest.approx(contributions[1], rel=1e-5)


def test_cluster_selection_is_deterministic_connected_components() -> None:
    symbols = ("A", "B", "C", "D")
    correlation = np.asarray(
        [
            [1.0, 0.8, 0.1, 0.0],
            [0.8, 1.0, 0.7, 0.0],
            [0.1, 0.7, 1.0, 0.2],
            [0.0, 0.0, 0.2, 1.0],
        ]
    )
    assert followups.clustered_selection(symbols, correlation, 0.65) == [["A", "B", "C"], ["D"]]


def test_twice_weekly_policy_has_two_holiday_tolerant_boundaries() -> None:
    policy = followups.TwiceWeeklyPolicy()
    monday = datetime(2026, 8, 24, tzinfo=UTC)
    tuesday = monday + timedelta(days=1)
    thursday = monday + timedelta(days=3)
    friday = monday + timedelta(days=4)
    next_monday = monday + timedelta(days=7)
    assert policy.is_period_start(None, monday)
    assert not policy.is_period_start(monday, tuesday)
    assert policy.is_period_start(tuesday, thursday)
    assert not policy.is_period_start(thursday, friday)
    assert policy.is_period_start(friday, next_monday)


def test_followup_identities_stay_out_of_product_registry() -> None:
    from parayabuck.portfolio import supported_portfolio_strategies

    registered = set(supported_portfolio_strategies())
    for cls in (
        followups.ShrinkageERC,
        followups.ClusteredV3,
        followups.DispersionV3,
        followups.FactorETFMomentum,
        followups.TwiceWeeklyV3,
    ):
        assert cls.name not in registered
