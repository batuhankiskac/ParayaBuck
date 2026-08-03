from __future__ import annotations

import math

from parayabuck.core.models import Bar, ExitPolicy, Market, Regime, Signal, TrailingStopType
from parayabuck.features.indicators import atr, ema
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.specs import CryptoTrendParams


class CryptoTrendBreakout(Strategy):
    name = "crypto_trend_breakout"
    version = "2.0.0"
    market = Market.CRYPTO
    minimum_bars = 200

    def __init__(
        self,
        min_volume_bar_ratio: float = 0.5,
        *,
        params: CryptoTrendParams | None = None,
        exit_policy: ExitPolicy | None = None,
    ) -> None:
        self.params = params or CryptoTrendParams(
            minimum_volume_bar_ratio=min_volume_bar_ratio
        )
        self.min_volume_bar_ratio = self.params.minimum_volume_bar_ratio
        self.exit_policy = exit_policy or ExitPolicy(
            policy_id="crypto_trend_chandelier_v1",
            break_even_trigger_r=1.0,
            trailing_type=TrailingStopType.CHANDELIER,
            trailing_atr_multiple=2.5,
            trailing_activation_r=1.0,
            max_holding_bars=48,
        )

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        supported = self.supports(bars)
        regime_ok = context.regime.regime == Regime.TREND_UP
        btc_regime_ok = context.benchmark_regime not in {
            Regime.TREND_DOWN,
            Regime.HIGH_VOLATILITY,
        }
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
            ConditionEvaluation(
                "btc_market_regime",
                btc_regime_ok,
                context.benchmark_regime.value,
                "not trend_down or high_volatility",
                "A bearish or stressed BTC regime blocks new crypto breakouts.",
            ),
        ]
        if not (supported and regime_ok and btc_regime_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = self.closes(bars)
        params = self.params
        fast = ema(closes, params.fast_ema_period)
        slow = ema(closes, params.slow_ema_period)
        atr_value = atr(bars, params.atr_period)
        volume_window = bars[-(params.donchian_period + 1) :]
        volume_gate_ok = self.nonzero_volume_gate(volume_window, self.min_volume_bar_ratio)
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
        prior_high = max(bar.high for bar in bars[-params.donchian_period - 1 : -1])
        setup_checks = (
            ConditionEvaluation(
                "donchian_breakout",
                current.close > prior_high,
                {"close": current.close, "prior_high": prior_high},
                f"close > prior {params.donchian_period}-bar high",
                "Price must close above the configured Donchian high.",
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
        relative_volume = self.relative_volume(
            current,
            bars[-params.donchian_period - 1 : -1],
            median=True,
            positive_only=True,
        )
        trend_points = min(17, context.regime.trend_strength / 2)
        # Kept in diagnostics for compatibility, but deliberately excluded from
        # score: Alpaca spot volume is a liquidity/data-quality gate only.
        volume_points = min(10, relative_volume * 4)
        higher_points = 10 if context.higher_timeframe_regime == Regime.TREND_UP else 0
        score = 58 + trend_points + higher_points
        stop = current.close - max(
            params.stop_atr_multiple * atr_value, current.close * params.minimum_stop_pct
        )
        target = current.close + params.safety_target_r_multiple * (current.close - stop)
        signal = self.long_signal(
            current,
            score,
            stop,
            target,
            (
                f"{params.donchian_period}_bar_breakout",
                "ema_fast>ema_slow",
                "volume_quality_gate",
            ),
            context.regime.regime,
            exit_policy=self.exit_policy,
            volatility_at_entry=atr_value,
            reentry_cooldown_bars=params.reentry_cooldown_bars,
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
            "Price broke the Donchian high with bullish EMA alignment.",
            tuple(conditions),
            metrics,
            signal,
        )
