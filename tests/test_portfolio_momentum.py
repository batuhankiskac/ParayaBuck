from __future__ import annotations

import pytest

from parayabuck.portfolio.momentum import (
    conservative_portfolio_volatility,
    diagonal_portfolio_volatility,
)


def test_positive_correlation_floor_prevents_diagonal_risk_understatement() -> None:
    weights = {"A": 0.5, "B": 0.5}
    volatilities = {"A": 0.20, "B": 0.20}

    diagonal = diagonal_portfolio_volatility(weights, volatilities)
    conservative = conservative_portfolio_volatility(weights, volatilities, correlation_floor=0.25)

    assert conservative > diagonal
    assert conservative == pytest.approx(0.158113883)
