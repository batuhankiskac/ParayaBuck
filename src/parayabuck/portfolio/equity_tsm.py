from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import (
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    composite_momentum,
    diagonal_portfolio_volatility,
    inverse_volatility_weights,
    momentum_at,
    realized_volatility,
)
from parayabuck.portfolio.universes import EQUITY_ETF_UNIVERSE, EQUITY_UNIVERSE_VERSION

MOMENTUM_SKIP_BARS = 21
BARS_PER_YEAR = 252


def _trend_score(closes: list[float], scheme: str) -> float:
    if scheme == "3m":
        return momentum_at(closes, 63, MOMENTUM_SKIP_BARS)
    if scheme == "6m":
        return momentum_at(closes, 126, MOMENTUM_SKIP_BARS)
    if scheme == "12m":
        return momentum_at(closes, 252, MOMENTUM_SKIP_BARS)
    if scheme == "norm_3_6_12":
        return composite_momentum(closes, (63, 126, 252), MOMENTUM_SKIP_BARS)
    raise ValueError(f"Unsupported trend scheme: {scheme}")


class EquityTimeSeriesMomentum1dV1(PortfolioStrategy):
    """Daily long-or-cash trend following per ETF, independent of the relative
    momentum ranking used by the dual-momentum strategy.

    Each ETF enters only when its own trailing trend is positive; exposures
    are inverse-vol weighted, capped at 1x total with no leverage, and an
    optional diagonal volatility target scales the book down. Results are
    reported separately from dual momentum and never auto-combined.
    """

    name = "equity_time_series_momentum_1d_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        scheme = self.param_str("momentum_scheme", "12m")
        return {
            "3m": 92,
            "6m": 148,
            "12m": 274,
            "norm_3_6_12": 92,
        }[scheme]

    def _validate_parameters(self) -> None:
        allowed_schemes = {"3m", "6m", "12m", "norm_3_6_12"}
        scheme = self.param_str("momentum_scheme", "12m")
        if scheme not in allowed_schemes:
            raise ValueError(f"Unsupported trend scheme: {scheme}")
        rebalance_frequency = self.param_str("rebalance_frequency", "monthly")
        if rebalance_frequency not in {"monthly", "weekly"}:
            raise ValueError("rebalance_frequency must be monthly or weekly")
        volatility_window = self.parameters.get("volatility_window", 20)
        if not isinstance(volatility_window, int) or volatility_window < 10:
            raise ValueError("volatility_window must be an integer of at least 10 bars")
        volatility_target = self.param_optional_float("volatility_target")
        if volatility_target is not None and not isinstance(volatility_target, (int, float)):
            raise ValueError("volatility_target must be numeric or null")
        max_single = self.parameters.get("max_single_weight", 0.5)
        if not isinstance(max_single, (int, float)) or not 0 < float(max_single) <= 1:
            raise ValueError("max_single_weight must be in (0, 1]")

    @property
    def rebalance_frequency(self) -> str:
        return self.param_str("rebalance_frequency", "monthly")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        scheme = self.param_str("momentum_scheme", "12m")
        volatility_window = self.param_int("volatility_window", 20)
        volatility_target = self.param_optional_float("volatility_target")
        max_single_weight = self.param_float("max_single_weight", 0.5)

        selected: list[str] = []
        volatilities: dict[str, float] = {}
        for symbol in EQUITY_ETF_UNIVERSE:
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            trend = _trend_score(closes, scheme)
            if not math.isfinite(trend) or trend <= 0:
                continue
            selected.append(symbol)
            volatilities[symbol] = annualize_period_volatility(
                realized_volatility(closes, volatility_window), BARS_PER_YEAR
            )
        if not selected:
            return self._cash_portfolio(context)
        weights = dict(
            inverse_volatility_weights(
                tuple(selected), volatilities, maximum_weight=max_single_weight
            )
        )
        if not weights:
            return self._cash_portfolio(context)
        risk_multiplier = 1.0
        if volatility_target is not None:
            target = float(volatility_target)
            portfolio_volatility = diagonal_portfolio_volatility(weights, volatilities)
            risk_multiplier = (
                min(1.0, target / portfolio_volatility) if portfolio_volatility > 0 else 1.0
            )
            weights = {symbol: weight * risk_multiplier for symbol, weight in weights.items()}
        targets = tuple(
            TargetWeight(symbol, weight, reason="time_series_trend")
            for symbol, weight in weights.items()
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weights.values())),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version or EQUITY_UNIVERSE_VERSION,
            universe_snapshot_id="permanent_etf_v1",
            parameters=self.parameter_tuple(),
            risk_multiplier=risk_multiplier,
            metadata={"positive_trend_count": len(targets)},
        )

    def _cash_portfolio(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
            rebalance_reason=RebalanceReason.RISK_OFF,
            universe_version=context.universe_version or EQUITY_UNIVERSE_VERSION,
            universe_snapshot_id="permanent_etf_v1",
            parameters=self.parameter_tuple(),
            metadata={"risk_off_detail": "no_positive_trend"},
        )


__all__ = ["EquityTimeSeriesMomentum1dV1"]
