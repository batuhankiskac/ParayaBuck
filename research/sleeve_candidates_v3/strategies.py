"""Frozen research-only sleeve candidates for sleeve candidates v3.

Not registered in the product strategy registry; used only by the offline
research runners. Definitions are frozen by PREREGISTRATION.md.
"""

from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

CASH_PROXY = "BIL"
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


class AllWeatherV1(_StaticSleeve):
    """S7: Dalio-style static all-weather mix."""

    name = "all_weather_v1"
    version = "1.0.0"
    display_name = "All-weather v1"
    WEIGHTS = {"SPY": 0.3, "TLT": 0.4, "IEF": 0.15, "GLD": 0.075, "DBC": 0.075}
    symbols = tuple(WEIGHTS)


class Balanced6040V1(_StaticSleeve):
    """S8: classic 60/40 US equity and aggregate bonds."""

    name = "balanced_60_40_v1"
    version = "1.0.0"
    display_name = "Balanced 60/40 v1"
    WEIGHTS = {"SPY": 0.6, "AGG": 0.4}
    symbols = tuple(WEIGHTS)


class GoldV1(_StaticSleeve):
    """S9: gold via two equivalent bullion ETFs (split only to respect the 0.10 per-name cap)."""

    name = "gold_v1"
    version = "1.0.0"
    display_name = "Gold v1"
    WEIGHTS = {"GLD": 0.5, "IAU": 0.5}
    symbols = tuple(WEIGHTS)
    concentration_gate = False


class GoldV1b(_StaticSleeve):
    """S9b (declared post-hoc variant of S9): same gold exposure with OUNZ instead of IAU.

    IAU is inside V3's own observed universe, so S9 cannot pass the Stage B
    invariance arm; OUNZ is not, which makes the structural check exact.
    """

    name = "gold_v1b"
    version = "1.0.0"
    display_name = "Gold v1b"
    WEIGHTS = {"GLD": 0.5, "OUNZ": 0.5}
    symbols = tuple(WEIGHTS)
    concentration_gate = False


class EqualWeightUsV1(_StaticSleeve):
    """S10: equal-weight US large caps via two equivalent ETFs (0.10 per-name cap)."""

    name = "equal_weight_us_v1"
    version = "1.0.0"
    display_name = "Equal-weight US v1"
    WEIGHTS = {"RSP": 0.5, "EUSA": 0.5}
    symbols = tuple(WEIGHTS)
    concentration_gate = False


class DividendGrowthV1(_StaticSleeve):
    """S11: dividend-growth equity ETFs."""

    name = "dividend_growth_v1"
    version = "1.0.0"
    display_name = "Dividend growth v1"
    WEIGHTS = {"SCHD": 1 / 3, "VIG": 1 / 3, "DGRO": 1 / 3}
    symbols = tuple(WEIGHTS)


class HedgedEquityV1(_StaticSleeve):
    """S12: SPY half plus the anti-beta market-neutral fund half."""

    name = "hedged_equity_v1"
    version = "1.0.0"
    display_name = "Hedged equity v1"
    WEIGHTS = {"SPY": 0.5, "BTAL": 0.5}
    symbols = tuple(WEIGHTS)


class CoveredCallV1(_StaticSleeve):
    """S13: S&P 500 and Nasdaq-100 buy-write ETFs."""

    name = "covered_call_v1"
    version = "1.0.0"
    display_name = "Covered call v1"
    WEIGHTS = {"XYLD": 0.5, "QYLD": 0.5}
    symbols = tuple(WEIGHTS)


class GlobalMinVolV1(_StaticSleeve):
    """S14: US, EAFE and EM minimum-volatility ETFs."""

    name = "global_min_vol_v1"
    version = "1.0.0"
    display_name = "Global minimum volatility v1"
    WEIGHTS = {"USMV": 1 / 3, "EFAV": 1 / 3, "EEMV": 1 / 3}
    symbols = tuple(WEIGHTS)


class MomentumQualityV1(_StaticSleeve):
    """S15: static momentum and quality factor ETFs (held, not rotated)."""

    name = "momentum_quality_v1"
    version = "1.0.0"
    display_name = "Momentum and quality v1"
    WEIGHTS = {"MTUM": 0.5, "QUAL": 0.5}
    symbols = tuple(WEIGHTS)


class SmallCapV1(_StaticSleeve):
    """S16: S&P 600 and Russell 2000."""

    name = "small_cap_v1"
    version = "1.0.0"
    display_name = "Small cap v1"
    WEIGHTS = {"IJR": 0.5, "IWM": 0.5}
    symbols = tuple(WEIGHTS)


class ShortTreasurySweepV1(_StaticSleeve):
    """X2: idle-cash parking in 1-3 year Treasury ETFs (additive, cash-equivalent policy)."""

    name = "short_treasury_sweep_v1"
    version = "1.0.0"
    display_name = "Short Treasury sweep v1"
    family = "X"
    allocation = 0.2
    WEIGHTS = {"SHY": 0.5, "VGSH": 0.5}
    symbols = tuple(WEIGHTS)


class TreasuryTrendV1(_Sleeve):
    """S17: long Treasuries (TLT/VGLT halves) while TLT is above its 210-bar SMA, otherwise BIL/SHV halves."""

    name = "treasury_trend_v1"
    version = "1.0.0"
    display_name = "Treasury trend v1"
    symbols = ("TLT", "VGLT", CASH_PROXY, "SHV")
    concentration_gate = False

    @property
    def minimum_history_bars(self) -> int:
        return SMA_LOOKBACK_BARS

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        closes = context.closes("TLT")
        above = len(closes) >= SMA_LOOKBACK_BARS and closes[-1] > sum(closes[-SMA_LOOKBACK_BARS:]) / SMA_LOOKBACK_BARS
        pair = ("TLT", "VGLT") if above else (CASH_PROXY, "SHV")
        return self._portfolio(context, {pair[0]: 0.5, pair[1]: 0.5}, {"rule": "tlt_sma_210_vs_bil", "above": above})


CANDIDATES: tuple[type[_Sleeve], ...] = (
    AllWeatherV1,
    Balanced6040V1,
    GoldV1,
    EqualWeightUsV1,
    DividendGrowthV1,
    HedgedEquityV1,
    CoveredCallV1,
    GlobalMinVolV1,
    MomentumQualityV1,
    SmallCapV1,
    TreasuryTrendV1,
    ShortTreasurySweepV1,
    GoldV1b,
)
