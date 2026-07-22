from __future__ import annotations

from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.features.indicators import atr, ema
from parayabuck.strategies.base import Strategy, StrategyContext


class CryptoTrendBreakout(Strategy):
    name = "crypto_trend_breakout"
    market = Market.CRYPTO
    minimum_bars = 200

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if not self.supports(bars) or context.regime.regime != Regime.TREND_UP:
            return None
        closes = [bar.close for bar in bars]
        fast = ema(closes, 20)
        slow = ema(closes, 100)
        atr_value = atr(bars, 14)
        if fast is None or slow is None or atr_value is None:
            return None
        current = bars[-1]
        prior_high = max(bar.high for bar in bars[-21:-1])
        if not (current.close > prior_high and fast > slow):
            return None
        average_volume = sum(bar.volume for bar in bars[-20:]) / 20
        relative_volume = current.volume / average_volume if average_volume > 0 else 1.0
        score = 58 + min(17, context.regime.trend_strength / 2) + min(10, relative_volume * 4)
        if context.higher_timeframe_regime == Regime.TREND_UP:
            score += 10
        stop = current.close - max(2.2 * atr_value, current.close * 0.018)
        target = current.close + 2.3 * (current.close - stop)
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
            reasons=("20_bar_breakout", "ema20>ema100", f"relative_volume={relative_volume:.2f}"),
            regime=context.regime.regime,
        )
