from __future__ import annotations

import math
import statistics

from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.features.indicators import atr, ema
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)


class CryptoTrendBreakout(Strategy):
    name = "crypto_trend_breakout"
    market = Market.CRYPTO
    minimum_bars = 200

    def __init__(self, min_volume_bar_ratio: float = 0.5) -> None:
        self.min_volume_bar_ratio = min_volume_bar_ratio

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        supported = self.supports(bars)
        regime_ok = context.regime.regime == Regime.TREND_UP
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
                "trend_up",
                "Breakouts are only allowed in an uptrend.",
            ),
        ]
        if not (supported and regime_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = [bar.close for bar in bars]
        fast = ema(closes, 20)
        slow = ema(closes, 100)
        atr_value = atr(bars, 14)
        volume_window = bars[-21:]
        nonzero_volume_bars = sum(
            1 for bar in volume_window if math.isfinite(bar.volume) and bar.volume > 0
        )
        volume_gate_ok = nonzero_volume_bars >= max(
            1, int(self.min_volume_bar_ratio * len(volume_window))
        )
        indicators_ok = (
            fast is not None
            and slow is not None
            and atr_value is not None
            and math.isfinite(atr_value)
            and atr_value > 0
            and volume_gate_ok
        )
        conditions.append(
            ConditionEvaluation(
                "indicators_available",
                indicators_ok,
                {"ema20": fast, "ema100": slow, "atr14": atr_value},
                "all indicators available",
                "Required indicators must be computable.",
            )
        )
        if not indicators_ok:
            return StrategyEvaluation(
                self.name, False, "Required indicators are unavailable.", tuple(conditions)
            )
        assert fast is not None and slow is not None and atr_value is not None
        current = bars[-1]
        prior_high = max(bar.high for bar in bars[-21:-1])
        setup_checks = (
            ConditionEvaluation(
                "twenty_bar_breakout",
                current.close > prior_high,
                {"close": current.close, "prior_high": prior_high},
                "close > prior 20-bar high",
                "Price must close above the prior 20-bar high.",
            ),
            ConditionEvaluation(
                "ema_alignment",
                fast > slow,
                fast - slow,
                "ema20 > ema100",
                "Fast EMA must be above slow EMA.",
            ),
        )
        conditions.extend(setup_checks)
        metrics = {
            "ema20": fast,
            "ema100": slow,
            "atr14": atr_value,
            "prior_20_bar_high": prior_high,
        }
        if not all(item.passed for item in setup_checks):
            failed = next(item for item in setup_checks if not item.passed)
            return StrategyEvaluation(
                self.name, False, failed.explanation, tuple(conditions), metrics
            )
        baseline_volumes = [bar.volume for bar in bars[-21:-1] if bar.volume > 0]
        average_volume = statistics.median(baseline_volumes) if baseline_volumes else 0.0
        relative_volume = current.volume / average_volume if average_volume > 0 else 1.0
        trend_points = min(17, context.regime.trend_strength / 2)
        volume_points = min(10, relative_volume * 4)
        higher_points = 10 if context.higher_timeframe_regime == Regime.TREND_UP else 0
        score = 58 + trend_points + volume_points
        if context.higher_timeframe_regime == Regime.TREND_UP:
            score += 10
        stop = current.close - max(2.2 * atr_value, current.close * 0.018)
        target = current.close + 2.3 * (current.close - stop)
        signal = Signal(
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
        metrics.update(
            {
                "relative_volume": relative_volume,
                "base_score": 58,
                "trend_points": trend_points,
                "volume_points": volume_points,
                "higher_timeframe_points": higher_points,
            }
        )
        return StrategyEvaluation(
            self.name,
            True,
            "Price broke the 20-bar high with bullish EMA alignment.",
            tuple(conditions),
            metrics,
            signal,
        )
