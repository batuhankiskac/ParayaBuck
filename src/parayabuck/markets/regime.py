from __future__ import annotations

from dataclasses import dataclass

from parayabuck.core.models import Bar, Market, Regime
from parayabuck.features.indicators import adx, atr, directional_indicators, ema


@dataclass(frozen=True)
class RegimeAssessment:
    regime: Regime
    confidence: float
    trend_strength: float
    volatility_pct: float
    reasons: tuple[str, ...]


class RegimeDetector:
    """Classify a market without automatically suppressing all strategies."""

    def __init__(self, fast_period: int = 50, slow_period: int = 200) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period

    def detect(self, bars: list[Bar]) -> RegimeAssessment:
        minimum = max(self.slow_period, 50)
        if len(bars) < minimum:
            return RegimeAssessment(Regime.UNKNOWN, 0.0, 0.0, 0.0, ("insufficient_history",))

        closes = [bar.close for bar in bars]
        fast = ema(closes, self.fast_period)
        slow = ema(closes, self.slow_period)
        trend_strength = adx(bars, 14) or 0.0
        atr_value = atr(bars, 14) or 0.0
        volatility_pct = atr_value / closes[-1] if closes[-1] > 0 else 0.0
        volatility_limit = 0.035 if bars[-1].market == Market.CRYPTO else 0.025
        reasons: tuple[str, ...]

        if volatility_pct >= volatility_limit:
            regime = Regime.HIGH_VOLATILITY
            reasons = (f"atr_pct={volatility_pct:.3%}", "volatility_above_limit")
        elif fast is not None and slow is not None and trend_strength >= 22:
            directional = directional_indicators(bars, 14)
            plus_di, minus_di = directional or (0.0, 0.0)
            if closes[-1] > fast > slow:
                regime = Regime.TREND_UP
                reasons = ("price>ema50>ema200", f"adx={trend_strength:.1f}")
            elif closes[-1] < fast < slow:
                regime = Regime.TREND_DOWN
                reasons = ("price<ema50<ema200", f"adx={trend_strength:.1f}")
            elif closes[-1] < fast and minus_di > plus_di:
                regime = Regime.TREND_DOWN
                reasons = (
                    "fresh_bearish_break_below_ema50",
                    f"-di={minus_di:.1f}>+di={plus_di:.1f}",
                    f"adx={trend_strength:.1f}",
                )
            elif closes[-1] > fast and plus_di > minus_di:
                regime = Regime.TREND_UP
                reasons = (
                    "fresh_bullish_break_above_ema50",
                    f"+di={plus_di:.1f}>-di={minus_di:.1f}",
                    f"adx={trend_strength:.1f}",
                )
            else:
                regime = Regime.RANGE
                reasons = (
                    "moving_averages_not_aligned",
                    f"+di={plus_di:.1f},-di={minus_di:.1f}",
                    f"adx={trend_strength:.1f}",
                )
        else:
            regime = Regime.RANGE
            reasons = (f"adx={trend_strength:.1f}", "weak_directional_trend")

        trend_confidence = min(1.0, trend_strength / 40.0)
        confidence = (
            trend_confidence
            if regime in {Regime.TREND_UP, Regime.TREND_DOWN}
            else 1 - trend_confidence / 2
        )
        return RegimeAssessment(
            regime, max(0.0, min(1.0, confidence)), trend_strength, volatility_pct, reasons
        )
