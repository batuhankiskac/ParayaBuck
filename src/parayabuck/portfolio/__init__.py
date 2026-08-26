from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy, StrategySpec
from parayabuck.portfolio.crypto_low_variance import (
    CryptoLowVariance1dV1,
    CryptoLowVariance1dV2,
)
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1
from parayabuck.portfolio.equity_residual_momentum import (
    EquityResidualMomentum1dV1,
    EquityResidualMomentum1dV2,
)
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import (
    PortfolioState,
    RebalancePolicy,
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)

_PORTFOLIO_STRATEGY_FACTORIES: dict[str, type[PortfolioStrategy]] = {
    EquityResidualMomentum1dV1.name: EquityResidualMomentum1dV1,
    EquityResidualMomentum1dV2.name: EquityResidualMomentum1dV2,
    # v3 (MH_RAW consensus) is the frozen 2026-08 PAPER promotion identity.
    EquityResidualMomentum1dV3.name: EquityResidualMomentum1dV3,
    CryptoLowVariance1dV1.name: CryptoLowVariance1dV1,
    CryptoLowVariance1dV2.name: CryptoLowVariance1dV2,
    CryptoLowVarianceHysteresis1dV1.name: CryptoLowVarianceHysteresis1dV1,
}

# The PRODUCT strategy surface: the identities the PAPER product supports
# (current validated selections plus frozen historical baselines). Rejected
# strategy families are archived in docs/rejected_strategies.md and are NOT
# constructible at runtime.
PRODUCT_STRATEGY_NAMES: tuple[str, ...] = (
    "equity_residual_momentum_v1",
    "equity_residual_momentum_v2",
    "equity_residual_momentum_v3",
    "crypto_low_variance_v1",
    "crypto_low_variance_v2",
    "crypto_low_variance_hysteresis_v1",
)


def portfolio_strategy_from(
    name: str,
    parameters: dict[str, object] | None = None,
    *,
    timeframe: str | None = None,
) -> PortfolioStrategy:
    """Build a strategy, optionally selecting one of its VALIDATED timeframes.

    The strategy owns its signal timeframe: ``timeframe`` must be the primary
    or one of ``supported_timeframes``, otherwise the construction fails
    loudly. There is no global strategy timeframe.
    """
    try:
        factory = _PORTFOLIO_STRATEGY_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported portfolio strategy: {name}") from exc
    merged = dict(parameters or {})
    if timeframe is None and "timeframe" in merged:
        # A strategy that explicitly supports multiple validated timeframe
        # variants may be selected through its own ``timeframe`` parameter;
        # it is popped so it never leaks into the signal parameter set.
        timeframe = str(merged.pop("timeframe"))
    strategy = factory(merged)
    if timeframe is not None and timeframe != strategy.timeframe:
        if not strategy.supports_timeframe(timeframe):
            raise ValueError(
                f"Strategy {name} cannot run on timeframe {timeframe}; supported: "
                f"{strategy.timeframe}, {', '.join(strategy.supported_timeframes)}"
            )
        strategy.timeframe = timeframe
    return strategy


def product_strategy_from(
    name: str,
    parameters: dict[str, object] | None = None,
    *,
    timeframe: str | None = None,
) -> PortfolioStrategy:
    """Product-gated factory: only PAPER-supported identities.

    Validates ``name`` against ``PRODUCT_STRATEGY_NAMES`` (the PAPER product
    surface) then delegates to ``portfolio_strategy_from`` for construction.
    Research-only factories (e.g. hysteresis_v1 before promotion) remain
    constructible via ``portfolio_strategy_from`` but are rejected here.
    """
    if name not in PRODUCT_STRATEGY_NAMES:
        raise ValueError(
            f"Unsupported product strategy: {name}; supported product strategies: "
            f"{', '.join(sorted(PRODUCT_STRATEGY_NAMES))}"
        )
    return portfolio_strategy_from(name, parameters, timeframe=timeframe)


def validate_strategy_timeframe(strategy: PortfolioStrategy, configured: str) -> None:
    """Fail loudly when a configured ``strategy.*.timeframe`` does not match
    the selected strategy's validated timeframes (never silently reinterpret)."""
    if not strategy.supports_timeframe(configured):
        raise ValueError(
            f"Configured timeframe {configured} is not supported by strategy "
            f"{strategy.name}; supported: {strategy.timeframe}, "
            f"{', '.join(strategy.supported_timeframes)}"
        )


def supported_portfolio_strategies() -> tuple[str, ...]:
    """Names the PAPER product supports: validated selections + frozen v1 baselines."""
    return tuple(sorted(PRODUCT_STRATEGY_NAMES))


def rebalance_policy_for(strategy: PortfolioStrategy, parameters: dict[str, object] | None = None) -> RebalancePolicy:
    """Map a portfolio strategy to its rebalance policy.

    Crypto strategies encode their rebalance cadence in the ``rebalance_hours``
    parameter; equity strategies default to monthly unless the parameters ask
    for weekly.
    """
    if strategy.market == Market.CRYPTO:
        merged = dict(parameters or strategy.parameters)
        hours_value = merged.get("rebalance_hours", getattr(strategy, "default_rebalance_hours", 24))
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
    "CryptoLowVariance1dV1",
    "CryptoLowVariance1dV2",
    "CryptoLowVarianceHysteresis1dV1",
    "EquityResidualMomentum1dV1",
    "EquityResidualMomentum1dV2",
    "EquityResidualMomentum1dV3",
    "PortfolioContext",
    "PortfolioState",
    "PortfolioStrategy",
    "PRODUCT_STRATEGY_NAMES",
    "RebalancePolicy",
    "RebalanceReason",
    "StrategySpec",
    "TargetPortfolio",
    "TargetWeight",
    "portfolio_strategy_from",
    "product_strategy_from",
    "rebalance_policy_for",
    "supported_portfolio_strategies",
    "validate_strategy_timeframe",
]
