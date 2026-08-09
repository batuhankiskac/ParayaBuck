from __future__ import annotations

import math
from statistics import stdev

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import simple_moving_average
from parayabuck.portfolio.risk_budgeting import covariance_budget_weights

EQUITY_ALL_WEATHER_UNIVERSE = ("SPY", "QQQ", "IWM", "IEF", "SHY", "GLD")


class EquityCovarianceTrend1dV1(PortfolioStrategy):
    """Long/cash all-weather allocation; structurally distinct from RED ranks."""

    name = "equity_covariance_trend_1d_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        return max(
            self.param_int("trend_window", 200),
            self.param_int("covariance_window", 60) + 1,
        )

    def _validate_parameters(self) -> None:
        trend = self.param_int("trend_window", 200)
        covariance = self.param_int("covariance_window", 60)
        target = self.param_float("annual_volatility_target", 0.08)
        cap = self.param_float("max_single_weight", 0.25)
        if trend < 100 or covariance < 20:
            raise ValueError("Trend/covariance windows are too short")
        if not 0 < target <= 0.20:
            raise ValueError("annual_volatility_target must be in (0, 0.20]")
        if not 0 < cap <= 0.25:
            raise ValueError("max_single_weight must be in (0, 0.25]")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        trend_window = self.param_int("trend_window", 200)
        covariance_window = self.param_int("covariance_window", 60)
        closes_by_symbol: dict[str, list[float]] = {}
        for symbol in EQUITY_ALL_WEATHER_UNIVERSE:
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            average = simple_moving_average(closes, trend_window)
            if average is not None and closes[-1] > average:
                closes_by_symbol[symbol] = closes
        weights, scale = covariance_budget_weights(
            closes_by_symbol,
            window=covariance_window,
            max_weight=self.param_float("max_single_weight", 0.25),
            annual_volatility_target=self.param_float("annual_volatility_target", 0.08),
            periods_per_year=252,
        )
        targets = tuple(
            TargetWeight(symbol, weight, "absolute_trend_covariance_budget")
            for symbol, weight in sorted(weights.items())
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weights.values())),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version="equity_all_weather_v1",
            universe_snapshot_id="permanent_six_asset_v1",
            parameters=self.parameter_tuple(),
            risk_multiplier=scale,
            metadata={"eligible_assets": len(targets), "volatility_target": 0.08},
        )

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        held = {symbol for symbol, quantity in context.holdings.items() if quantity > 0}
        if not held:
            return False
        trend_window = self.param_int("trend_window", 200)
        target = self.param_float("annual_volatility_target", 0.08)
        for symbol in held:
            closes = context.closes(symbol)
            average = simple_moving_average(closes, trend_window)
            if average is not None and closes[-1] <= average:
                return True
            returns = [
                current / previous - 1.0
                for previous, current in zip(closes[-21:-1], closes[-20:], strict=True)
                if previous > 0
            ]
            if len(returns) >= 10:
                realized = stdev(returns) * math.sqrt(252)
                if realized > target * 1.5:
                    return True
        return False


__all__ = ["EQUITY_ALL_WEATHER_UNIVERSE", "EquityCovarianceTrend1dV1"]
