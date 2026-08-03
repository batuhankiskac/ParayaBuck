from __future__ import annotations

from parayabuck.core.models import Bar, ExitPolicy, Market, Regime, Signal
from parayabuck.features.indicators import adx, atr, bollinger, rsi
from parayabuck.markets.sectors import is_inverse_equity
from parayabuck.strategies.base import (
    ConditionEvaluation,
    Strategy,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.strategies.specs import StockMeanReversionParams


class StockMeanReversion(Strategy):
    name = "stock_mean_reversion"
    version = "2.0.0"
    market = Market.EQUITY
    minimum_bars = 50

    def __init__(
        self,
        params: StockMeanReversionParams | None = None,
        exit_policy: ExitPolicy | None = None,
    ) -> None:
        self.params = params or StockMeanReversionParams()
        self.exit_policy = exit_policy or ExitPolicy(
            policy_id="equity_mean_time_v1",
            break_even_trigger_r=1.0,
            max_holding_bars=35,
        )

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
        params = self.params
        bands = bollinger(closes, params.bollinger_period, params.bollinger_deviations)
        rsi_value = rsi(closes, params.rsi_period)
        atr_value = atr(bars, params.atr_period)
        adx_value = adx(bars, params.atr_period)
        previous_adx = adx(bars[:-1], params.atr_period)
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
        previous_bands = bollinger(
            closes[:-1], params.bollinger_period, params.bollinger_deviations
        )
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
                rsi_value <= params.rsi_ceiling,
                rsi_value,
                f"<= {params.rsi_ceiling:g}",
                "RSI must still show a sufficiently oversold setup.",
            ),
            ConditionEvaluation(
                "zscore_extreme",
                previous_bands is not None
                and abs(previous_bands.zscore) >= params.minimum_abs_zscore,
                previous_bands.zscore if previous_bands else None,
                f"absolute prior z-score >= {params.minimum_abs_zscore:g}",
                "The initial excursion must be economically meaningful.",
            ),
            ConditionEvaluation(
                "range_transition_filter",
                (adx_value is None or adx_value <= params.maximum_adx)
                and (
                    adx_value is None
                    or previous_adx is None
                    or adx_value - previous_adx <= params.maximum_adx_increase
                ),
                {
                    "adx": adx_value,
                    "adx_change": None
                    if previous_adx is None or adx_value is None
                    else adx_value - previous_adx,
                },
                "ADX low and not accelerating",
                "A strengthening trend blocks mean-reversion entries.",
            ),
            ConditionEvaluation(
                "volatility_filter",
                atr_value / current.close <= params.maximum_atr_pct,
                atr_value / current.close,
                f"ATR/price <= {params.maximum_atr_pct:g}",
                "Volatility shocks are excluded from the range model.",
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
        relative_volume = self.relative_volume(current, bars[-21:-1])
        deviation_points = min(15, abs(bands.zscore) * 6)
        volume_points = min(10, relative_volume * 4)
        score = 60 + deviation_points + volume_points
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
            "The economic middle-band target must justify the initial risk.",
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
            exit_policy=self.exit_policy,
            volatility_at_entry=atr_value,
            minimum_risk_reward=params.minimum_middle_band_rr,
        )
        metrics.update(
            {
                "relative_volume": relative_volume,
                "base_score": 60,
                "deviation_points": deviation_points,
                "volume_points": volume_points,
                "middle_band_risk_reward": middle_band_rr,
                "adx": adx_value,
                "adx_change": None
                if adx_value is None or previous_adx is None
                else adx_value - previous_adx,
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
