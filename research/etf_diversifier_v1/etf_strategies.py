"""Frozen research-only ETF sleeve candidates for ETF diversifier v1.

Not registered in the product strategy registry; used only by the offline
research runner. Definitions are frozen by PREREGISTRATION.md.
"""

from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

CASH_PROXY = "BIL"
TREND_LOOKBACK_BARS = 252


class _EtfSleeve(PortfolioStrategy):
    market = Market.EQUITY
    timeframe = "1d"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "monthly"

    def _validate_parameters(self) -> None:
        if self.parameters:
            raise ValueError(f"{self.name} is frozen and takes no parameters")

    def _portfolio(self, context: PortfolioContext, weights: dict[str, float], metadata: dict) -> TargetPortfolio:
        targets = tuple(
            TargetWeight(symbol, weight, reason="etf_sleeve")
            for symbol, weight in sorted(weights.items())
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(target.weight for target in targets)),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata=metadata,
        )


class EtfStaticDefensiveV1(_EtfSleeve):
    """C1: fixed SPY/TLT/GLD/BIL quarters, restored monthly."""

    name = "etf_static_defensive_v1"
    version = "1.0.0"
    display_name = "ETF static defensive v1"
    WEIGHTS = {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "BIL": 0.25}

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        available = {symbol: weight for symbol, weight in self.WEIGHTS.items() if context.closes(symbol)}
        return self._portfolio(context, available, {"rule": "static_quarters"})


class EtfTrend12mV1(_EtfSleeve):
    """C2: each risk asset holds 1/5 when its 252-bar return beats BIL's, else BIL."""

    name = "etf_trend_12m_v1"
    version = "1.0.0"
    display_name = "ETF 12-month trend v1"
    RISK_ASSETS = ("SPY", "TLT", "IEF", "GLD", "DBC")

    @property
    def minimum_history_bars(self) -> int:
        return TREND_LOOKBACK_BARS + 1

    @staticmethod
    def _trailing_return(closes: list[float]) -> float | None:
        if len(closes) < TREND_LOOKBACK_BARS + 1:
            return None
        start, end = closes[-(TREND_LOOKBACK_BARS + 1)], closes[-1]
        if start <= 0 or end <= 0:
            return None
        return end / start - 1.0

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        slot = 1.0 / len(self.RISK_ASSETS)
        hurdle = self._trailing_return(context.closes(CASH_PROXY))
        weights: dict[str, float] = {}
        signals: dict[str, object] = {"hurdle": hurdle}
        for symbol in self.RISK_ASSETS:
            momentum = self._trailing_return(context.closes(symbol))
            passes = momentum is not None and hurdle is not None and momentum > hurdle
            signals[symbol] = {"return_252": momentum, "in_trend": passes}
            destination = symbol if passes else CASH_PROXY
            weights[destination] = weights.get(destination, 0.0) + slot
        return self._portfolio(context, weights, {"rule": "trend_12m_vs_bil", "signals": signals})
