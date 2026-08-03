from __future__ import annotations

from parayabuck.core.models import Bar, ExitPolicy, Market, Regime, Signal, TrailingStopType
from parayabuck.features.indicators import atr, ema, rsi
from parayabuck.markets.sectors import is_inverse_equity
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.specs import StockTrendParams


class StockTrendPullback(Strategy):
    name = "stock_trend_pullback"
    version = "2.0.0"
    market = Market.EQUITY
    minimum_bars = 200

    def __init__(
        self,
        params: StockTrendParams | None = None,
        exit_policy: ExitPolicy | None = None,
    ) -> None:
        self.params = params or StockTrendParams()
        self.exit_policy = exit_policy or ExitPolicy(
            policy_id="equity_trend_chandelier_v1",
            break_even_trigger_r=1.0,
            trailing_type=TrailingStopType.CHANDELIER,
            trailing_atr_multiple=2.5,
            trailing_activation_r=1.0,
            max_holding_bars=35,
        )

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
        params = self.params
        fast = ema(closes, params.fast_ema_period)
        slow = ema(closes, params.slow_ema_period)
        previous_fast = ema(closes[:-1], params.fast_ema_period)
        previous_slow = ema(closes[:-1], params.slow_ema_period)
        rsi_value = rsi(closes, params.rsi_period)
        atr_value = atr(bars, params.atr_period)
        indicators_ok = all(
            value is not None
            for value in (fast, slow, previous_fast, previous_slow, rsi_value, atr_value)
        )
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
        assert fast is not None and slow is not None
        assert previous_fast is not None and previous_slow is not None
        assert rsi_value is not None and atr_value is not None
        current, previous = bars[-1], bars[-2]
        pullback_distance = abs(current.close - fast) / atr_value if atr_value > 0 else 99.0
        pullback_start = max(0, len(bars) - params.pullback_lookback - 1)
        touched_fast = False
        for index in range(pullback_start, len(bars) - 1):
            historical_fast = ema(closes[: index + 1], params.fast_ema_period)
            historical_atr = atr(bars[: index + 1], params.atr_period)
            if (
                historical_fast is not None
                and historical_atr is not None
                and bars[index].low
                <= historical_fast + params.touch_tolerance_atr * historical_atr
            ):
                touched_fast = True
                break
        resumed = current.close > previous.high and current.close > fast
        slopes_positive = fast > previous_fast and slow >= previous_slow
        relative_strength_ok = (
            context.relative_strength is None or context.relative_strength >= 0
        )
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
                params.rsi_floor <= rsi_value <= params.rsi_ceiling,
                rsi_value,
                f"{params.rsi_floor:g} <= RSI <= {params.rsi_ceiling:g}",
                "Momentum must cool without becoming weak or overbought.",
            ),
            ConditionEvaluation(
                "pullback_distance",
                pullback_distance <= params.maximum_distance_atr,
                pullback_distance,
                f"<= {params.maximum_distance_atr:g} ATR",
                "Price must remain close enough to EMA20 to qualify as a pullback.",
            ),
            ConditionEvaluation(
                "recent_ema_touch",
                touched_fast,
                {"lookback": params.pullback_lookback, "ema": fast},
                "a recent low touches EMA20",
                "A real pullback must touch or briefly undercut the fast EMA.",
            ),
            ConditionEvaluation(
                "pullback_resumed",
                resumed,
                {"close": current.close, "previous_high": previous.high, "ema20": fast},
                "close > previous high and ema20",
                "EMA reclaim must also close through the prior bar high.",
            ),
            ConditionEvaluation(
                "ema_slopes",
                slopes_positive,
                {"fast_change": fast - previous_fast, "slow_change": slow - previous_slow},
                "both EMA slopes non-negative; fast strictly positive",
                "A flat or falling EMA structure blocks trend entries.",
            ),
            ConditionEvaluation(
                "relative_strength",
                relative_strength_ok,
                context.relative_strength,
                ">= 0 when benchmark return is available",
                "The symbol must not lag its benchmark over the comparison window.",
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
        volume_points = min(5, relative_volume * 2)
        higher_points = 10 if context.higher_timeframe_regime == Regime.TREND_UP else 0
        relative_strength_points = (
            min(10.0, max(0.0, context.relative_strength * 500))
            if context.relative_strength is not None
            else 0.0
        )
        score = 55 + trend_points + volume_points + higher_points + relative_strength_points
        stop = current.close - max(
            params.stop_atr_multiple * atr_value, current.close * params.minimum_stop_pct
        )
        target = current.close + params.target_r_multiple * (current.close - stop)
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
            exit_policy=self.exit_policy,
            volatility_at_entry=atr_value,
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
                "relative_strength": context.relative_strength,
                "relative_strength_points": relative_strength_points,
            },
            signal,
        )
