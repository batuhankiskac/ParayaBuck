from __future__ import annotations

from parayabuck.core.models import Bar, Market, Regime, Signal
from parayabuck.features.indicators import atr, ema, rsi
from parayabuck.markets.sectors import is_inverse_equity
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)


class StockTrendPullback(Strategy):
    name = "stock_trend_pullback"
    market = Market.EQUITY
    minimum_bars = 200

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        conditions: list[ConditionEvaluation] = []
        supported = self.supports(bars)
        conditions.append(
            ConditionEvaluation(
                "strategy_support",
                supported,
                {"bars": len(bars), "market": bars[-1].market.value if bars else None},
                f"bars>={self.minimum_bars} and market=equity",
                "Enough equity history is available."
                if supported
                else "Insufficient or incompatible history.",
            )
        )
        regime_ok = context.regime.regime == Regime.TREND_UP
        conditions.append(
            ConditionEvaluation(
                "required_regime",
                regime_ok,
                context.regime.regime.value,
                "trend_up",
                "The strategy only buys pullbacks inside an established uptrend.",
            )
        )
        inverse = bool(bars and is_inverse_equity(bars[-1].symbol))
        allowed_benchmark_regimes = (
            {Regime.TREND_DOWN, Regime.UNKNOWN} if inverse else {Regime.TREND_UP, Regime.UNKNOWN}
        )
        benchmark_ok = context.benchmark_regime in allowed_benchmark_regimes
        conditions.append(
            ConditionEvaluation(
                "benchmark_regime",
                benchmark_ok,
                context.benchmark_regime.value,
                "trend_down or unknown" if inverse else "trend_up or unknown",
                (
                    "An inverse ETF is only bought while the benchmark is bearish."
                    if inverse
                    else "A bearish benchmark blocks new long equity exposure."
                ),
            )
        )
        if not (supported and regime_ok and benchmark_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = self.closes(bars)
        fast = ema(closes, 20)
        slow = ema(closes, 50)
        rsi_value = rsi(closes, 14)
        atr_value = atr(bars, 14)
        indicators_ok = all(value is not None for value in (fast, slow, rsi_value, atr_value))
        conditions.append(
            ConditionEvaluation(
                "indicators_available",
                indicators_ok,
                {"ema20": fast, "ema50": slow, "rsi14": rsi_value, "atr14": atr_value},
                "all indicators available",
                "Required indicators must be computable.",
            )
        )
        if not indicators_ok:
            return StrategyEvaluation(
                self.name, False, "Required indicators are unavailable.", tuple(conditions)
            )
        assert (
            fast is not None
            and slow is not None
            and rsi_value is not None
            and atr_value is not None
        )
        current, previous = bars[-1], bars[-2]
        pullback_distance = abs(current.close - fast) / atr_value if atr_value > 0 else 99.0
        resumed = current.close > previous.close and current.close > fast
        setup_checks = (
            ConditionEvaluation(
                "ema_alignment",
                fast > slow,
                fast - slow,
                "ema20 > ema50",
                "Fast EMA must remain above slow EMA.",
            ),
            ConditionEvaluation(
                "rsi_window",
                40 <= rsi_value <= 68,
                rsi_value,
                "40 <= rsi14 <= 68",
                "Momentum must cool without becoming weak or overbought.",
            ),
            ConditionEvaluation(
                "pullback_distance",
                pullback_distance <= 1.2,
                pullback_distance,
                "<= 1.2 ATR",
                "Price must remain close enough to EMA20 to qualify as a pullback.",
            ),
            ConditionEvaluation(
                "pullback_resumed",
                resumed,
                {"close": current.close, "previous_close": previous.close, "ema20": fast},
                "close > previous close and ema20",
                "The current bar must confirm that the pullback is resuming upward.",
            ),
        )
        conditions.extend(setup_checks)
        if not all(item.passed for item in setup_checks):
            failed = next(item for item in setup_checks if not item.passed)
            return StrategyEvaluation(
                self.name,
                False,
                failed.explanation,
                tuple(conditions),
                {
                    "ema20": fast,
                    "ema50": slow,
                    "rsi14": rsi_value,
                    "atr14": atr_value,
                    "pullback_distance_atr": pullback_distance,
                },
            )
        relative_volume = self.relative_volume(current, bars[-21:-1])
        trend_points = min(15, context.regime.trend_strength / 2)
        volume_points = min(10, relative_volume * 5)
        higher_points = 10 if context.higher_timeframe_regime == Regime.TREND_UP else 0
        score = 55 + trend_points + volume_points
        if context.higher_timeframe_regime == Regime.TREND_UP:
            score += 10
        stop = current.close - max(2 * atr_value, current.close * 0.012)
        target = current.close + 2.2 * (current.close - stop)
        signal = self.long_signal(
            current,
            score,
            stop,
            target,
            (
                "ema20>ema50",
                "pullback_reclaimed",
                f"rsi={rsi_value:.1f}",
                f"relative_volume={relative_volume:.2f}",
            ),
            context.regime.regime,
        )
        return StrategyEvaluation(
            self.name,
            True,
            "Trend pullback resumed with acceptable momentum and EMA alignment.",
            tuple(conditions),
            {
                "ema20": fast,
                "ema50": slow,
                "rsi14": rsi_value,
                "atr14": atr_value,
                "relative_volume": relative_volume,
                "pullback_distance_atr": pullback_distance,
                "base_score": 55,
                "trend_points": trend_points,
                "volume_points": volume_points,
                "higher_timeframe_points": higher_points,
            },
            signal,
        )
