from __future__ import annotations

from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.features.indicators import atr, bollinger, rsi
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)


class CryptoRangeReversion(Strategy):
    name = "crypto_range_reversion"
    market = Market.CRYPTO
    minimum_bars = 50

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        supported = self.supports(bars)
        regime_ok = context.regime.regime == Regime.RANGE
        conditions = [
            ConditionEvaluation(
                "strategy_support",
                supported,
                {"bars": len(bars), "market": bars[-1].market.value if bars else None},
                f"bars>={self.minimum_bars} and market=crypto",
                "Enough crypto history is required.",
            ),
            ConditionEvaluation(
                "required_regime",
                regime_ok,
                context.regime.regime.value,
                "range",
                "Mean reversion is only allowed in a range regime.",
            ),
        ]
        if not (supported and regime_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = [bar.close for bar in bars]
        bands = bollinger(closes, 20, 2.2)
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
        previous_bands = bollinger(closes[:-1], 20, 2.2)
        reclaimed = (
            previous_bands is not None
            and previous.close < previous_bands.lower
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
                "previous close < previous lower band and current close > lower band",
                "Price must reclaim the lower crypto Bollinger band.",
            ),
            ConditionEvaluation(
                "rsi_ceiling",
                rsi_value <= 43,
                rsi_value,
                "<= 43",
                "RSI must remain sufficiently weak for a range reversal.",
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
        stop = min(previous.low, current.close - max(1.8 * atr_value, current.close * 0.015))
        target = max(bands.middle, current.close + 1.9 * (current.close - stop))
        score = min(100.0, 65 + abs(bands.zscore) * 7 + (43 - rsi_value) / 2)
        signal = Signal(
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
        metrics.update(
            {
                "base_score": 65,
                "deviation_points": abs(bands.zscore) * 7,
                "rsi_points": (43 - rsi_value) / 2,
            }
        )
        return StrategyEvaluation(
            self.name,
            True,
            "Price reclaimed the lower band with oversold momentum.",
            tuple(conditions),
            metrics,
            signal,
        )
