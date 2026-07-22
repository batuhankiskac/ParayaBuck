from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from parayabuck.models import Bar
from parayabuck.strategy.indicators import adx, rsi


class MarketRegime(StrEnum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


@dataclass
class RegimeState:
    regime: MarketRegime
    trend_strength: float  # ADX value
    momentum: float  # RSI value
    above_sma: bool  # price above long SMA
    confidence: float  # 0-1, how confident we are

    def allows_mean_reversion(self) -> bool:
        return self.regime in (MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY)

    def allows_momentum(self) -> bool:
        return self.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)

    def allows_new_entries(self) -> bool:
        return self.regime != MarketRegime.CRISIS and self.regime != MarketRegime.UNKNOWN


class RegimeDetector:
    """Detects current market regime from price history.

    Uses:
      - 200-period SMA for trend direction
      - ADX for trend strength (>25 = trending, <20 = ranging)
      - RSI for momentum context
      - Volatility for risk regime
    """

    def __init__(
        self,
        sma_period: int = 200,
        adx_period: int = 14,
        rsi_period: int = 14,
        adx_trend_threshold: float = 25.0,
        adx_range_threshold: float = 20.0,
        vol_lookback: int = 50,
        crisis_vol_threshold: float = 3.0,
    ) -> None:
        self.sma_period = sma_period
        self.adx_period = adx_period
        self.rsi_period = rsi_period
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_range_threshold = adx_range_threshold
        self.vol_lookback = vol_lookback
        self.crisis_vol_threshold = crisis_vol_threshold

    def detect(self, bars: list[Bar]) -> RegimeState:
        if len(bars) < max(self.sma_period, 50):
            return RegimeState(
                regime=MarketRegime.UNKNOWN,
                trend_strength=0,
                momentum=50,
                above_sma=False,
                confidence=0.0,
            )

        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        current_price = closes[-1]

        sma_val = sum(closes[-self.sma_period :]) / self.sma_period
        above_sma = current_price > sma_val

        adx_val = adx(highs, lows, closes, self.adx_period) or 0
        rsi_val = rsi(closes, self.rsi_period) or 50

        recent_vol = self._avg_volatility(bars)
        if len(bars) >= self.vol_lookback * 2:
            baseline_vol = self._avg_volatility(bars[-self.vol_lookback * 2 : -self.vol_lookback])
        else:
            baseline_vol = recent_vol
        vol_ratio = recent_vol / baseline_vol if baseline_vol > 0 else 1.0

        confidence = min(adx_val / 50, 1.0)

        if vol_ratio > self.crisis_vol_threshold:
            regime = MarketRegime.CRISIS
        elif vol_ratio > 2.0:
            regime = MarketRegime.HIGH_VOLATILITY
        elif adx_val >= self.adx_trend_threshold:
            regime = MarketRegime.TRENDING_UP if above_sma else MarketRegime.TRENDING_DOWN
        elif adx_val < self.adx_range_threshold:
            if vol_ratio < 0.8:
                regime = MarketRegime.LOW_VOLATILITY
            else:
                regime = MarketRegime.RANGING
        else:
            regime = MarketRegime.TRENDING_UP if above_sma else MarketRegime.TRENDING_DOWN

        return RegimeState(
            regime=regime,
            trend_strength=adx_val,
            momentum=rsi_val,
            above_sma=above_sma,
            confidence=confidence,
        )

    @staticmethod
    def _avg_volatility(bars: list[Bar]) -> float:
        if len(bars) < 2:
            return 0.0
        returns = [abs(bars[i].close / bars[i - 1].close - 1) for i in range(1, len(bars))]
        return sum(returns) / len(returns) if returns else 0.0


def suggest_strategy(state: RegimeState) -> str:
    """Suggest which strategy type to use given the current regime.

    Legacy backtest strategy suggestion. The active Web runtime uses the V2
    regime and signal-ranking pipeline.

    A long-only bot must NOT trade in downtrends or high-volatility regimes:
      - downtrend: catching oversold dips = catching falling knives
      - high volatility: ATR stops are too wide, R:R unfavorable
    """
    return REGIME_TO_STRATEGY.get(state.regime.value, "none")


# Canonical regime -> strategy map. Both the live engine and auto-pilot import
# this so they never drift apart (previously the auto-pilot routed
# ``trending_down`` to mean_reversion while the live engine routed it to
# ``none`` — long-only bot vs. falling-knife conflict).
REGIME_TO_STRATEGY: dict[str, str] = {
    "trending_up": "ema_crossover",
    # Long-only bot: downtrend = catching falling knives if we buy oversold dips.
    "trending_down": "none",
    "ranging": "mean_reversion",
    "low_volatility": "mean_reversion",
    # ATR stops become too wide vs. achievable TP -> R:R unfavourable.
    "high_volatility": "none",
    "crisis": "none",
    "unknown": "none",
}


class RegimeFilterStrategy:
    """Wrapper that filters another strategy's signals based on regime.

    If the regime doesn't allow the wrapped strategy's signals, returns None.
    """

    def __init__(
        self,
        regime_detector: RegimeDetector,
        allowed_regimes: list[MarketRegime] | None = None,
    ) -> None:
        self.detector = regime_detector
        self.allowed_regimes = allowed_regimes or [
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
            MarketRegime.RANGING,
            MarketRegime.LOW_VOLATILITY,
        ]
        self._bars: list[Bar] = []

    def update(self, bar: Bar) -> RegimeState | None:
        """Push a new bar and return updated regime state."""
        self._bars.append(bar)
        if len(self._bars) > 300:
            self._bars = self._bars[-300:]
        if len(self._bars) < 50:
            return None
        return self.detector.detect(self._bars)

    def is_allowed(self, state: RegimeState | None) -> bool:
        if state is None:
            return False
        return state.regime in self.allowed_regimes


__all__ = [
    "MarketRegime",
    "RegimeState",
    "RegimeDetector",
    "RegimeFilterStrategy",
    "suggest_strategy",
    "REGIME_TO_STRATEGY",
]
