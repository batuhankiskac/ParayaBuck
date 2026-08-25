from __future__ import annotations

import pytest

from parayabuck.portfolio.momentum import (
    conservative_portfolio_volatility,
    diagonal_portfolio_volatility,
    reject_quantile_threshold,
)


def test_positive_correlation_floor_prevents_diagonal_risk_understatement() -> None:
    weights = {"A": 0.5, "B": 0.5}
    volatilities = {"A": 0.20, "B": 0.20}

    diagonal = diagonal_portfolio_volatility(weights, volatilities)
    conservative = conservative_portfolio_volatility(weights, volatilities, correlation_floor=0.25)

    assert conservative > diagonal
    assert conservative == pytest.approx(0.158113883)


def test_reject_quantile_threshold_retains_q_fraction_for_distinct_values() -> None:
    # n = 1..10 at q=0.70: keep floor(0.70 * (n - 1)) + 1 names. The legacy
    # int(q * n) index kept one name too many (8 of 10, i.e. 80% instead of
    # ~70%), and kept ALL names for n <= 3.
    for n in range(1, 11):
        values = [float(index + 1) for index in range(n)]
        expected_keep = int(0.70 * (n - 1)) + 1
        threshold = reject_quantile_threshold(values, 0.70)
        assert sum(value <= threshold for value in values) == expected_keep


def test_reject_quantile_threshold_edge_cases() -> None:
    # Single value is always kept.
    assert reject_quantile_threshold([3.0], 0.70) == 3.0
    # Identical values: ties are KEPT (threshold equals the common value).
    assert reject_quantile_threshold([2.0] * 5, 0.70) == 2.0
    assert sum(v <= reject_quantile_threshold([2.0] * 10, 0.99) for v in [2.0] * 10) == 10
    # Boundary quantiles.
    assert reject_quantile_threshold([4.0, 1.0, 3.0, 2.0], 0.0) == 1.0
    assert reject_quantile_threshold([4.0, 1.0, 3.0, 2.0], 1.0) == 4.0
    # Input order never matters (deterministic).
    assert reject_quantile_threshold([5.0, 1.0, 4.0], 0.70) == reject_quantile_threshold([1.0, 4.0, 5.0], 0.70)


def test_reject_quantile_threshold_validates_arguments() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        reject_quantile_threshold([], 0.70)
    with pytest.raises(ValueError, match="\\[0, 1\\]"):
        reject_quantile_threshold([1.0], 1.5)
    with pytest.raises(ValueError, match="\\[0, 1\\]"):
        reject_quantile_threshold([1.0], -0.1)
