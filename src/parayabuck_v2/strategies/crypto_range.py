from __future__ import annotations

from parayabuck_v2.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck_v2.features.indicators import atr, bollinger, rsi
from parayabuck_v2.strategies.base import Strategy, StrategyContext


class CryptoRangeReversion(Strategy):
    name = "crypto_range_reversion"
    market = Market.CRYPTO
    minimum_bars = 50

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if not self.supports(bars) or context.regime.regime != Regime.RANGE:
            return None
        closes = [bar.close for bar in bars]
        bands = bollinger(closes, 20, 2.2)
        rsi_value = rsi(closes, 14)
        atr_value = atr(bars, 14)
        if bands is None or rsi_value is None or atr_value is None:
            return None
        current, previous = bars[-1], bars[-2]
        previous_bands = bollinger(closes[:-1], 20, 2.2)
        reclaimed = (
            previous_bands is not None
            and previous.close < previous_bands.lower
            and current.close > bands.lower
        )
        if not reclaimed or rsi_value > 43:
            return None
        stop = min(previous.low, current.close - max(1.8 * atr_value, current.close * 0.015))
        target = max(bands.middle, current.close + 1.9 * (current.close - stop))
        score = min(100.0, 65 + abs(bands.zscore) * 7 + (43 - rsi_value) / 2)
        return Signal(
            symbol=current.symbol,
            market=self.market,
            direction=Direction.LONG,
            strategy=self.name,
            score=score,
            confidence=min(1.0, score / 100),
            entry_price=current.close,
            invalidation_price=stop,
            target_price=target,
            timestamp=current.timestamp,
            reasons=("crypto_range", "lower_band_reclaim", f"rsi={rsi_value:.1f}"),
            regime=context.regime.regime,
        )
