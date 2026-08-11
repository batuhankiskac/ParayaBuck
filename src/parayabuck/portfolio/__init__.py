from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.crypto_cross_sectional import CryptoCrossSectionalMomentum1hV1
from parayabuck.portfolio.equity_dynamic_trend import EquityDynamicTrend1dV1
from parayabuck.portfolio.equity_long_horizon_momentum import EquityLongHorizonMomentum1dV2
from parayabuck.portfolio.models import (
    PortfolioState,
    RebalancePolicy,
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)

_PORTFOLIO_STRATEGY_FACTORIES: dict[str, type[PortfolioStrategy]] = {
    EquityDynamicTrend1dV1.name: EquityDynamicTrend1dV1,
    EquityLongHorizonMomentum1dV2.name: EquityLongHorizonMomentum1dV2,
    CryptoCrossSectionalMomentum1hV1.name: CryptoCrossSectionalMomentum1hV1,
}


def portfolio_strategy_from(
    name: str, parameters: dict[str, object] | None = None
) -> PortfolioStrategy:
    try:
        factory = _PORTFOLIO_STRATEGY_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported portfolio strategy: {name}") from exc
    return factory(parameters)


def supported_portfolio_strategies() -> tuple[str, ...]:
    return tuple(sorted(_PORTFOLIO_STRATEGY_FACTORIES))


def rebalance_policy_for(
    strategy: PortfolioStrategy, parameters: dict[str, object] | None = None
) -> RebalancePolicy:
    """Map a portfolio strategy to its rebalance policy.

    Crypto strategies encode their rebalance cadence in the ``rebalance_hours``
    parameter; equity strategies default to monthly unless the parameters ask
    for weekly.
    """
    if strategy.market == Market.CRYPTO:
        merged = dict(parameters or strategy.parameters)
        hours_value = merged.get(
            "rebalance_hours", getattr(strategy, "default_rebalance_hours", 24)
        )
        if not isinstance(hours_value, (int, float)):
            raise ValueError("rebalance_hours must be numeric")
        hours = int(hours_value)
        return RebalancePolicy(Market.CRYPTO, f"hours:{hours}")
    frequency_value = dict(parameters or strategy.parameters).get("rebalance_frequency", "monthly")
    if not isinstance(frequency_value, str):
        raise ValueError("rebalance_frequency must be a string")
    frequency = frequency_value
    return RebalancePolicy(Market.EQUITY, frequency)


__all__ = [
    "CryptoCrossSectionalMomentum1hV1",
    "EquityDynamicTrend1dV1",
    "EquityLongHorizonMomentum1dV2",
    "PortfolioContext",
    "PortfolioState",
    "PortfolioStrategy",
    "RebalancePolicy",
    "RebalanceReason",
    "TargetPortfolio",
    "TargetWeight",
    "portfolio_strategy_from",
    "rebalance_policy_for",
    "supported_portfolio_strategies",
]
