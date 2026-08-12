from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    inverse_volatility_weights,
    momentum_at,
    realized_volatility,
    simple_moving_average,
)

# Time-series momentum (Moskowitz, Ooi & Pedersen 2012) applied per asset to
# its OWN history: an asset qualifies when its trailing return is positive and
# it sits above its own medium-term trend. This is deliberately NOT
# cross-sectional ("which coin beats the others?").
_DEFAULT_LOOKBACK_DAYS = 30
_DEFAULT_TREND_DAYS = 15
_DEFAULT_VOLATILITY_WINDOW_DAYS = 15
_DEFAULT_SKIP_BARS = 1
_DEFAULT_REBALANCE_HOURS = 168  # weekly decisions: slow turnover for 25 bps taker fees


class CryptoTimeSeriesMomentumV1(PortfolioStrategy):
    """Slow-cadence long-only crypto time-series momentum.

    The asset is evaluated only against its own history: a positive
    ``lookback_days`` return together with close > own SMA(``trend_days``)
    qualifies it, and qualifying assets are inverse-volatility sized with a
    per-asset cap. Turnover is deliberately low (weekly decisions) because the
    crypto cost model is expensive (Alpaca tier-1 taker ≈ 25 bps per side).

    ``supported_timeframes`` are the ONLY validated signal resolutions (4h and
    1d); the factory selects the variant and the lookbacks stay calendar-day
    equivalent on both, so the 4h vs 1d comparison is economically identical
    signal content at different sampling resolutions.
    """

    name = "crypto_time_series_momentum_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "Time Series Momentum v1"
    supported_timeframes = ("4h", "1d")
    default_rebalance_hours = _DEFAULT_REBALANCE_HOURS
    rebalance_policy_declared = f"hours:{_DEFAULT_REBALANCE_HOURS}"

    def _bars_per_day(self) -> int:
        minutes = TIMEFRAME_MINUTES[self.timeframe]
        return 1440 // minutes

    @property
    def minimum_history_bars(self) -> int:
        lookback = self.param_int("lookback_days", _DEFAULT_LOOKBACK_DAYS) * self._bars_per_day()
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        return lookback + skip + 1

    def _validate_parameters(self) -> None:
        lookback = self.param_int("lookback_days", _DEFAULT_LOOKBACK_DAYS)
        trend = self.param_int("trend_days", _DEFAULT_TREND_DAYS)
        volatility = self.param_int("volatility_window_days", _DEFAULT_VOLATILITY_WINDOW_DAYS)
        top_n = self.param_int("top_n", 3)
        cap = self.param_float("max_single_weight", 0.5)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        rebalance_hours = self.param_int("rebalance_hours", _DEFAULT_REBALANCE_HOURS)
        if lookback < 7 or trend < 3 or volatility < 3:
            raise ValueError("crypto TSM lookback/trend/volatility windows are too short")
        if trend >= lookback:
            raise ValueError("trend_days must be smaller than lookback_days")
        if skip < 1 or skip >= lookback:
            raise ValueError("skip_bars must be positive and smaller than lookback_days")
        if not 1 <= top_n <= 6:
            raise ValueError("top_n must be between 1 and 6")
        if not 0 < cap <= 1:
            raise ValueError("max_single_weight must be in (0, 1]")
        if rebalance_hours <= 0 or rebalance_hours % 24:
            raise ValueError("rebalance_hours must be a positive multiple of 24")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        lookback_days = self.param_int("lookback_days", _DEFAULT_LOOKBACK_DAYS)
        trend_days = self.param_int("trend_days", _DEFAULT_TREND_DAYS)
        volatility_days = self.param_int("volatility_window_days", _DEFAULT_VOLATILITY_WINDOW_DAYS)
        top_n = self.param_int("top_n", 3)
        cap = self.param_float("max_single_weight", 0.5)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        bars_per_day = self._bars_per_day()
        lookback_bars = lookback_days * bars_per_day
        trend_bars = trend_days * bars_per_day
        volatility_bars = volatility_days * bars_per_day
        bars_per_year = 365 * bars_per_day

        qualified: list[tuple[float, str]] = []
        volatilities: dict[str, float] = {}
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            score = momentum_at(closes, lookback_bars, skip_bars=skip)
            trend = simple_moving_average(closes, trend_bars)
            volatility = annualize_period_volatility(
                realized_volatility(closes, volatility_bars), bars_per_year
            )
            if not math.isfinite(score) or not math.isfinite(volatility):
                continue
            if score <= 0 or trend is None or closes[-1] <= trend or volatility <= 0:
                continue
            qualified.append((score, symbol))
            volatilities[symbol] = volatility

        if not qualified:
            return self._cash_portfolio(context, "no_positive_trend_assets")
        qualified.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(symbol for _score, symbol in qualified[:top_n])
        weights = dict(inverse_volatility_weights(selected, volatilities, maximum_weight=cap))
        targets = tuple(
            TargetWeight(symbol, weight, reason="time_series_momentum")
            for symbol, weight in sorted(weights.items())
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(qualified),
                "selected_symbols": selected,
                "timeframe": self.timeframe,
                "hypothesis": (
                    f"per-asset time-series momentum ({lookback_days}d return > 0 "
                    f"and close > own {trend_days}d SMA), inverse-vol sized, "
                    "weekly decisions"
                ),
            },
        )

    def _cash_portfolio(self, context: PortfolioContext, detail: str) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
            rebalance_reason=RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"risk_off_detail": detail},
        )


__all__ = ["CryptoTimeSeriesMomentumV1"]
