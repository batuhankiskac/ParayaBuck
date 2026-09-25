"""Frozen research-only sleeve candidates for sleeve candidates v2.

Not registered in the product strategy registry; used only by the offline
research runners. Definitions are frozen by PREREGISTRATION.md.
"""

from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

CASH_PROXY = "BIL"
VOLATILITY_LOOKBACK_BARS = 63
SMA_LOOKBACK_BARS = 210


class _Sleeve(PortfolioStrategy):
    market = Market.EQUITY
    timeframe = "1d"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "monthly"
    family = "S"
    allocation = 0.15
    symbols: tuple[str, ...] = ()
    concentration_gate = True

    def _validate_parameters(self) -> None:
        if self.parameters:
            raise ValueError(f"{self.name} is frozen and takes no parameters")

    @property
    def minimum_history_bars(self) -> int:
        return 1

    def _portfolio(self, context: PortfolioContext, weights: dict[str, float], metadata: dict) -> TargetPortfolio:
        targets = tuple(
            TargetWeight(symbol, weight, reason="sleeve") for symbol, weight in sorted(weights.items()) if weight > 0
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


class _StaticSleeve(_Sleeve):
    WEIGHTS: dict[str, float] = {}

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        available = {symbol: weight for symbol, weight in self.WEIGHTS.items() if context.closes(symbol)}
        return self._portfolio(context, available, {"rule": "static"})


class PassiveUsCoreV1(_StaticSleeve):
    """S1: SPY and QQQ halves."""

    name = "passive_us_core_v1"
    version = "1.0.0"
    display_name = "Passive US core v1"
    WEIGHTS = {"SPY": 0.5, "QQQ": 0.5}
    symbols = tuple(WEIGHTS)
    concentration_gate = False


class DefensiveFactorV1(_StaticSleeve):
    """S2: minimum-volatility, quality and low-volatility factor ETFs."""

    name = "defensive_factor_v1"
    version = "1.0.0"
    display_name = "Defensive factor v1"
    WEIGHTS = {"USMV": 1 / 3, "QUAL": 1 / 3, "SPLV": 1 / 3}
    symbols = tuple(WEIGHTS)


class DefensiveSectorsV1(_StaticSleeve):
    """S5: staples, utilities and health care sector ETFs."""

    name = "defensive_sectors_v1"
    version = "1.0.0"
    display_name = "Defensive sectors v1"
    WEIGHTS = {"XLP": 1 / 3, "XLU": 1 / 3, "XLV": 1 / 3}
    symbols = tuple(WEIGHTS)


class GlobalEquityV1(_StaticSleeve):
    """S6: US, developed ex-US and emerging-market equity."""

    name = "global_equity_v1"
    version = "1.0.0"
    display_name = "Global equity v1"
    WEIGHTS = {"SPY": 0.5, "EFA": 0.25, "EEM": 0.25}
    symbols = tuple(WEIGHTS)


class TbillSweepV1(_StaticSleeve):
    """X1: idle-cash parking in T-bill ETFs (additive, cash-equivalent policy)."""

    name = "tbill_sweep_v1"
    version = "1.0.0"
    display_name = "T-bill sweep v1"
    family = "X"
    allocation = 0.20
    WEIGHTS = {"BIL": 0.5, "SHV": 0.5}
    symbols = tuple(WEIGHTS)


class RiskParityV1(_Sleeve):
    """S3: inverse 63-bar volatility across SPY/TLT/IEF/GLD/DBC."""

    name = "risk_parity_v1"
    version = "1.0.0"
    display_name = "Risk parity v1"
    symbols = ("SPY", "TLT", "IEF", "GLD", "DBC")

    @property
    def minimum_history_bars(self) -> int:
        return VOLATILITY_LOOKBACK_BARS + 1

    @staticmethod
    def _volatility(closes: list[float]) -> float | None:
        if len(closes) < VOLATILITY_LOOKBACK_BARS + 1:
            return None
        window = closes[-(VOLATILITY_LOOKBACK_BARS + 1) :]
        returns = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return math.sqrt(variance) if variance > 0 else None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        inverse = {}
        for symbol in self.symbols:
            volatility = self._volatility(context.closes(symbol))
            if volatility is not None:
                inverse[symbol] = 1.0 / volatility
        total = sum(inverse.values())
        weights = {symbol: value / total for symbol, value in inverse.items()} if total else {}
        return self._portfolio(context, weights, {"rule": "inverse_volatility_63"})


class Gtaa5SmaV1(_Sleeve):
    """S4: Faber GTAA-5; each asset 1/5 above its 210-bar SMA, else BIL."""

    name = "gtaa5_sma_v1"
    version = "1.0.0"
    display_name = "GTAA-5 SMA v1"
    RISK_ASSETS = ("SPY", "EFA", "IEF", "DBC", "VNQ")
    symbols = (*RISK_ASSETS, CASH_PROXY)

    @property
    def minimum_history_bars(self) -> int:
        return SMA_LOOKBACK_BARS

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        slot = 1.0 / len(self.RISK_ASSETS)
        weights: dict[str, float] = {}
        signals: dict[str, object] = {}
        for symbol in self.RISK_ASSETS:
            closes = context.closes(symbol)
            above = (
                len(closes) >= SMA_LOOKBACK_BARS and closes[-1] > sum(closes[-SMA_LOOKBACK_BARS:]) / SMA_LOOKBACK_BARS
            )
            signals[symbol] = above
            destination = symbol if above else CASH_PROXY
            weights[destination] = weights.get(destination, 0.0) + slot
        return self._portfolio(context, weights, {"rule": "sma_210_vs_bil", "signals": signals})


CANDIDATES: tuple[type[_Sleeve], ...] = (
    PassiveUsCoreV1,
    DefensiveFactorV1,
    RiskParityV1,
    Gtaa5SmaV1,
    DefensiveSectorsV1,
    GlobalEquityV1,
    TbillSweepV1,
)
