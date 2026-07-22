from __future__ import annotations

from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.features.indicators import atr, ema, rsi
from parayabuck.strategies.base import Strategy, StrategyContext


class StockTrendPullback(Strategy):
    name = "stock_trend_pullback"
    market = Market.EQUITY
    minimum_bars = 200

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if not self.supports(bars) or context.regime.regime != Regime.TREND_UP:
            return None
        if context.benchmark_regime not in {Regime.TREND_UP, Regime.UNKNOWN}:
            return None
        closes = [bar.close for bar in bars]
        fast = ema(closes, 20)
        slow = ema(closes, 50)
        rsi_value = rsi(closes, 14)
        atr_value = atr(bars, 14)
        if fast is None or slow is None or rsi_value is None or atr_value is None:
            return None
        current, previous = bars[-1], bars[-2]
        pullback_distance = abs(current.close - fast) / atr_value if atr_value > 0 else 99.0
        resumed = current.close > previous.close and current.close > fast
        if not (fast > slow and 40 <= rsi_value <= 68 and pullback_distance <= 1.2 and resumed):
            return None
        average_volume = sum(bar.volume for bar in bars[-20:]) / 20
        relative_volume = current.volume / average_volume if average_volume > 0 else 1.0
        score = 55 + min(15, context.regime.trend_strength / 2) + min(10, relative_volume * 5)
        if context.higher_timeframe_regime == Regime.TREND_UP:
            score += 10
        stop = current.close - max(2 * atr_value, current.close * 0.012)
        target = current.close + 2.2 * (current.close - stop)
        return Signal(
            symbol=current.symbol,
            market=self.market,
            direction=Direction.LONG,
            strategy=self.name,
            score=min(100, score),
            confidence=min(1.0, score / 100),
            entry_price=current.close,
            invalidation_price=stop,
            target_price=target,
            timestamp=current.timestamp,
            reasons=(
                "ema20>ema50",
                "pullback_reclaimed",
                f"rsi={rsi_value:.1f}",
                f"relative_volume={relative_volume:.2f}",
            ),
            regime=context.regime.regime,
        )
