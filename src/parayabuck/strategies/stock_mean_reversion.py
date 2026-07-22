from __future__ import annotations

from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.features.indicators import atr, bollinger, rsi
from parayabuck.strategies.base import Strategy, StrategyContext


class StockMeanReversion(Strategy):
    name = "stock_mean_reversion"
    market = Market.EQUITY
    minimum_bars = 50

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if not self.supports(bars) or context.regime.regime != Regime.RANGE:
            return None
        closes = [bar.close for bar in bars]
        bands = bollinger(closes, 20, 2.0)
        rsi_value = rsi(closes, 14)
        atr_value = atr(bars, 14)
        if bands is None or rsi_value is None or atr_value is None:
            return None
        current, previous = bars[-1], bars[-2]
        previous_bands = bollinger(closes[:-1], 20, 2.0)
        reclaimed = (
            previous_bands is not None
            and previous.close <= previous_bands.lower
            and current.close > bands.lower
        )
        if not reclaimed or rsi_value > 45:
            return None
        average_volume = sum(bar.volume for bar in bars[-20:]) / 20
        relative_volume = current.volume / average_volume if average_volume > 0 else 1.0
        score = 60 + min(15, abs(bands.zscore) * 6) + min(10, relative_volume * 4)
        stop = min(previous.low, current.close - max(1.5 * atr_value, current.close * 0.01))
        target = max(bands.middle, current.close + 1.8 * (current.close - stop))
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
                "lower_band_reclaim",
                f"rsi={rsi_value:.1f}",
                f"relative_volume={relative_volume:.2f}",
            ),
            regime=context.regime.regime,
        )
