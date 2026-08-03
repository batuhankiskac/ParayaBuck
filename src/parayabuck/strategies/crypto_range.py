from __future__ import annotations

import math

from parayabuck.core.models import Bar, ExitPolicy, Market, Regime, Signal
from parayabuck.features.indicators import adx, atr, bollinger, rsi
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.specs import CryptoRangeParams


class CryptoRangeReversion(Strategy):
    name = "crypto_range_reversion"
    version = "2.0.0"
    market = Market.CRYPTO
    minimum_bars = 50

    def __init__(
        self,
        min_volume_bar_ratio: float = 0.5,
        *,
        params: CryptoRangeParams | None = None,
        exit_policy: ExitPolicy | None = None,
    ) -> None:
        self.params = params or CryptoRangeParams(
            minimum_volume_bar_ratio=min_volume_bar_ratio
        )
        self.min_volume_bar_ratio = self.params.minimum_volume_bar_ratio
        self.exit_policy = exit_policy or ExitPolicy(
            policy_id="crypto_range_time_v1",
            break_even_trigger_r=1.0,
            max_holding_bars=48,
        )

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        return self.evaluate_with_trace(bars, context).signal

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        supported = self.supports(bars)
        regime_ok = context.regime.regime == Regime.RANGE
        btc_regime_ok = context.benchmark_regime not in {
            Regime.TREND_UP,
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
                "range",
                "Mean reversion is only allowed in a range regime.",
            ),
            ConditionEvaluation(
                "btc_breakout_filter",
                btc_regime_ok,
                context.benchmark_regime.value,
                "range or unknown",
                "A directional or stressed BTC regime blocks crypto range entries.",
            ),
        ]
        if not (supported and regime_ok and btc_regime_ok):
            failed = next(item for item in conditions if not item.passed)
            return StrategyEvaluation(self.name, False, failed.explanation, tuple(conditions))
        closes = self.closes(bars)
        params = self.params
        bands = bollinger(closes, params.bollinger_period, params.bollinger_deviations)
        rsi_value = rsi(closes, params.rsi_period)
        atr_value = atr(bars, params.atr_period)
        adx_value = adx(bars, params.atr_period)
        volume_window = bars[-params.bollinger_period :]
        volume_gate_ok = self.nonzero_volume_gate(volume_window, self.min_volume_bar_ratio)
        indicators_ok = (
            bands is not None
            and rsi_value is not None
            and atr_value is not None
            and math.isfinite(atr_value)
            and atr_value > 0
            and volume_gate_ok
        )
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
        previous_bands = bollinger(
            closes[:-1], params.bollinger_period, params.bollinger_deviations
        )
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
                rsi_value <= params.rsi_ceiling,
                rsi_value,
                f"<= {params.rsi_ceiling:g}",
                "RSI must remain sufficiently weak for a range reversal.",
            ),
            ConditionEvaluation(
                "range_strength_filter",
                adx_value is None or adx_value <= params.maximum_adx,
                adx_value,
                f"ADX <= {params.maximum_adx:g}",
                "A rising directional regime is not treated as a range.",
            ),
            ConditionEvaluation(
                "volatility_filter",
                atr_value / current.close <= params.maximum_atr_pct,
                atr_value / current.close,
                f"ATR/price <= {params.maximum_atr_pct:g}",
                "Volatility shocks are excluded from range entries.",
            ),
        )
        conditions.extend(setup_checks)
        metrics: dict[str, object] = {
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
        stop = min(
            previous.low,
            current.close
            - max(params.stop_atr_multiple * atr_value, current.close * params.minimum_stop_pct),
        )
        target = bands.middle
        middle_band_rr = (target - current.close) / (current.close - stop)
        rr_ok = target > current.close and middle_band_rr >= params.minimum_middle_band_rr
        rr_condition = ConditionEvaluation(
            "middle_band_risk_reward",
            rr_ok,
            middle_band_rr,
            f">= {params.minimum_middle_band_rr:g}",
            "The Bollinger middle band must justify the initial risk.",
        )
        conditions.append(rr_condition)
        if not rr_ok:
            return StrategyEvaluation(
                self.name,
                False,
                rr_condition.explanation,
                tuple(conditions),
                {**metrics, "middle_band_risk_reward": middle_band_rr},
            )
        score = min(
            100.0,
            65 + abs(bands.zscore) * 7 + (params.rsi_ceiling - rsi_value) / 2,
        )
        signal = self.long_signal(
            current,
            score,
            stop,
            target,
            ("crypto_range", "lower_band_reclaim", f"rsi={rsi_value:.1f}"),
            context.regime.regime,
            exit_policy=self.exit_policy,
            volatility_at_entry=atr_value,
            minimum_risk_reward=params.minimum_middle_band_rr,
            reentry_cooldown_bars=params.reentry_cooldown_bars,
        )
        metrics.update(
            {
                "base_score": 65,
                "deviation_points": abs(bands.zscore) * 7,
                "rsi_points": (params.rsi_ceiling - rsi_value) / 2,
                "middle_band_risk_reward": middle_band_rr,
                "adx": adx_value,
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
