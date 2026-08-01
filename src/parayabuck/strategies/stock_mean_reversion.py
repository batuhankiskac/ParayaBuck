from __future__ import annotations

from parayabuck.core.models import Bar, Market, Regime, Signal
from parayabuck.features.indicators import atr, bollinger, rsi
from parayabuck.markets.sectors import is_inverse_equity
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)


class StockMeanReversion(Strategy):
    name = "stock_mean_reversion"
    market = Market.EQUITY
    minimum_bars = 50

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        supported = self.supports(bars)
        regime_ok = context.regime.regime == Regime.RANGE
        inverse = bool(bars and is_inverse_equity(bars[-1].symbol))
        benchmark_ok = (
            context.benchmark_regime in {Regime.TREND_DOWN, Regime.UNKNOWN}
            if inverse
            else context.benchmark_regime != Regime.TREND_DOWN
        )
        conditions = [
            ConditionEvaluation(
                "strategy_support",
                supported,
                {"bars": len(bars), "market": bars[-1].market.value if bars else None},
                f"bars>={self.minimum_bars} and market=equity",
                "Enough equity history is required.",
            ),
            ConditionEvaluation(
                "required_regime",
                regime_ok,
                context.regime.regime.value,
                "range",
                "Mean reversion is only allowed in a range regime.",
            ),
            ConditionEvaluation(
                "benchmark_regime",
                benchmark_ok,
                context.benchmark_regime.value,
                "trend_down or unknown" if inverse else "not trend_down",
                (
                    "An inverse ETF is only bought while the benchmark is bearish."
                    if inverse
                    else "Regular equity mean reversion is blocked in a bearish benchmark trend."
                ),
            ),
        ]
        if not (supported and regime_ok and benchmark_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = self.closes(bars)
        bands = bollinger(closes, 20, 2.0)
        rsi_value = rsi(closes, 14)
        atr_value = atr(bars, 14)
        indicators_ok = bands is not None and rsi_value is not None and atr_value is not None
        conditions.append(
            ConditionEvaluation(
                "indicators_available",
                indicators_ok,
                {"bollinger": bands is not None, "rsi14": rsi_value, "atr14": atr_value},
                "all indicators available",
                "Required indicators must be computable.",
            )
        )
        if not indicators_ok:
            return StrategyEvaluation(
                self.name, False, "Required indicators are unavailable.", tuple(conditions)
            )
        assert bands is not None and rsi_value is not None and atr_value is not None
        current, previous = bars[-1], bars[-2]
        previous_bands = bollinger(closes[:-1], 20, 2.0)
        reclaimed = (
            previous_bands is not None
            and previous.close <= previous_bands.lower
            and current.close > bands.lower
        )
        setup_checks = (
            ConditionEvaluation(
                "lower_band_reclaim",
                reclaimed,
                {
                    "previous_close": previous.close,
                    "previous_lower": previous_bands.lower if previous_bands else None,
                    "close": current.close,
                    "lower": bands.lower,
                },
                "previous close <= previous lower band and current close > lower band",
                "Price must reclaim the lower Bollinger band.",
            ),
            ConditionEvaluation(
                "rsi_ceiling",
                rsi_value <= 45,
                rsi_value,
                "<= 45",
                "RSI must still show a sufficiently oversold setup.",
            ),
        )
        conditions.extend(setup_checks)
        metrics = {
            "bollinger_lower": bands.lower,
            "bollinger_middle": bands.middle,
            "bollinger_zscore": bands.zscore,
            "rsi14": rsi_value,
            "atr14": atr_value,
        }
        if not all(item.passed for item in setup_checks):
            failed = next(item for item in setup_checks if not item.passed)
            return StrategyEvaluation(
                self.name, False, failed.explanation, tuple(conditions), metrics
            )
        relative_volume = self.relative_volume(current, bars[-21:-1])
        deviation_points = min(15, abs(bands.zscore) * 6)
        volume_points = min(10, relative_volume * 4)
        score = 60 + deviation_points + volume_points
        stop = min(previous.low, current.close - max(1.5 * atr_value, current.close * 0.01))
        target = max(bands.middle, current.close + 1.8 * (current.close - stop))
        signal = self.long_signal(
            current,
            score,
            stop,
            target,
            (
                "lower_band_reclaim",
                f"rsi={rsi_value:.1f}",
                f"relative_volume={relative_volume:.2f}",
            ),
            context.regime.regime,
        )
        metrics.update(
            {
                "relative_volume": relative_volume,
                "base_score": 60,
                "deviation_points": deviation_points,
                "volume_points": volume_points,
            }
        )
        return StrategyEvaluation(
            self.name,
            True,
            "Price reclaimed the lower band while RSI remained weak.",
            tuple(conditions),
            metrics,
            signal,
        )
