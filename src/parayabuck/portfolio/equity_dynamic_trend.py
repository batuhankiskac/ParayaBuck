from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    diagonal_portfolio_volatility,
    inverse_volatility_weights,
    momentum_at,
    realized_volatility,
    simple_moving_average,
)


class EquityDynamicTrend1dV1(PortfolioStrategy):
    """Long/cash trend and momentum strategy for a scanner-selected stock universe.

    The scanner decides what is liquid and operationally suitable. This strategy
    independently requires an absolute uptrend, ranks qualifying symbols by trailing
    momentum, selects a bounded number of positions, and sizes them by inverse volatility.
    """

    name = "equity_dynamic_trend_1d_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        return (
            max(
                self.param_int("trend_window", 150),
                self.param_int("momentum_window", 60) + 1,
                self.param_int("volatility_window", 20) + 1,
            )
            + 1
        )

    def _validate_parameters(self) -> None:
        trend = self.param_int("trend_window", 150)
        momentum = self.param_int("momentum_window", 60)
        volatility = self.param_int("volatility_window", 20)
        top_n = self.param_int("top_n", 6)
        cap = self.param_float("max_single_weight", 0.10)
        target = self.param_float("annual_volatility_target", 0.10)
        frequency = self.param_str("rebalance_frequency", "weekly")
        if trend < 20 or momentum < 10 or volatility < 10:
            raise ValueError("Equity trend, momentum, and volatility windows are too short")
        if not 1 <= top_n <= 20:
            raise ValueError("top_n must be between 1 and 20")
        if not 0 < cap <= 0.25:
            raise ValueError("max_single_weight must be in (0, 0.25]")
        if not 0 < target <= 0.25:
            raise ValueError("annual_volatility_target must be in (0, 0.25]")
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("rebalance_frequency must be weekly or monthly")
        excluded = self.parameters.get("exclude_symbols", ["SPY"])
        if not isinstance(excluded, (list, tuple)) or not all(
            isinstance(symbol, str) for symbol in excluded
        ):
            raise ValueError("exclude_symbols must be a list of symbols")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        trend_window = self.param_int("trend_window", 150)
        momentum_window = self.param_int("momentum_window", 60)
        volatility_window = self.param_int("volatility_window", 20)
        top_n = self.param_int("top_n", 6)
        cap = self.param_float("max_single_weight", 0.10)
        volatility_target = self.param_float("annual_volatility_target", 0.10)
        excluded_raw = self.parameters.get("exclude_symbols", [context.benchmark_symbol])
        if not isinstance(excluded_raw, (list, tuple)):
            raise ValueError("exclude_symbols must be a list of symbols")
        excluded = {str(symbol).strip().upper() for symbol in excluded_raw}

        ranked: list[tuple[float, str]] = []
        volatilities: dict[str, float] = {}
        for symbol in sorted(context.history):
            if symbol in excluded or not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            trend = simple_moving_average(closes, trend_window)
            score = momentum_at(closes, momentum_window, skip_bars=1)
            volatility = annualize_period_volatility(
                realized_volatility(closes, volatility_window), 252
            )
            if (
                trend is None
                or closes[-1] <= trend
                or not math.isfinite(score)
                or score <= 0
                or not math.isfinite(volatility)
                or volatility <= 0
            ):
                continue
            ranked.append((score, symbol))
            volatilities[symbol] = volatility

        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        weights = dict(inverse_volatility_weights(selected, volatilities, maximum_weight=cap))
        portfolio_volatility = diagonal_portfolio_volatility(weights, volatilities)
        risk_multiplier = (
            min(1.0, volatility_target / portfolio_volatility) if portfolio_volatility > 0 else 1.0
        )
        weights = {symbol: weight * risk_multiplier for symbol, weight in weights.items()}
        targets = tuple(
            TargetWeight(symbol, weight, "positive_trend_relative_momentum")
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
            rebalance_reason=RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            risk_multiplier=risk_multiplier,
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": selected,
                "hypothesis": "positive absolute trend plus relative momentum",
            },
        )

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        trend_window = self.param_int("trend_window", 150)
        for symbol, quantity in context.holdings.items():
            if quantity <= 0:
                continue
            closes = context.closes(symbol)
            trend = simple_moving_average(closes, trend_window)
            if trend is not None and closes[-1] <= trend:
                return True
        return False


__all__ = ["EquityDynamicTrend1dV1"]
