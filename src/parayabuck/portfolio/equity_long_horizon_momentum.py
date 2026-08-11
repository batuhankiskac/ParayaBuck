from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    conservative_portfolio_volatility,
    inverse_volatility_weights,
    momentum_at,
    realized_volatility,
    simple_moving_average,
)


class EquityLongHorizonMomentum1dV2(PortfolioStrategy):
    """Long-horizon cross-sectional momentum with a time-series regime gate.

    Research candidate (NOT the live default). One literature-grounded
    hypothesis: classic 12-month formation / 1-month skip cross-sectional
    momentum (Jegadeesh & Titman 1993), each stock gated by its own 200-day
    absolute trend, the whole book gated by SPY's 200-day trend, rebalanced
    monthly, sized with the existing inverse-volatility / volatility-target
    machinery. Component flags exist only for research ablation (M0-M3) and
    default to the full specification.
    """

    name = "equity_long_horizon_momentum_1d_v2"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        return (
            max(
                self.param_int("trend_window", 200),
                self.param_int("momentum_window", 252) + self.param_int("momentum_skip", 21),
            )
            + 1
        )

    def _validate_parameters(self) -> None:
        trend = self.param_int("trend_window", 200)
        momentum = self.param_int("momentum_window", 252)
        skip = self.param_int("momentum_skip", 21)
        volatility = self.param_int("volatility_window", 20)
        top_n = self.param_int("top_n", 6)
        cap = self.param_float("max_single_weight", 0.10)
        target = self.param_float("annual_volatility_target", 0.10)
        correlation_floor = self.param_float("correlation_floor", 0.25)
        frequency = self.param_str("rebalance_frequency", "monthly")
        if trend < 50 or momentum < 60 or skip < 1 or volatility < 10:
            raise ValueError("V2 windows are too short")
        if momentum <= skip:
            raise ValueError("momentum_window must exceed momentum_skip")
        if not 1 <= top_n <= 20:
            raise ValueError("top_n must be between 1 and 20")
        if not 0 < cap <= 0.25:
            raise ValueError("max_single_weight must be in (0, 0.25]")
        if not 0 < target <= 0.25:
            raise ValueError("annual_volatility_target must be in (0, 0.25]")
        if not 0 <= correlation_floor <= 1:
            raise ValueError("correlation_floor must be in [0, 1]")
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("rebalance_frequency must be weekly or monthly")
        sizing = self.param_str("sizing", "inverse_vol")
        if sizing not in {"equal", "inverse_vol"}:
            raise ValueError("sizing must be equal or inverse_vol")
        for flag in ("use_stock_trend", "use_market_regime", "use_vol_target"):
            if not isinstance(self.parameters.get(flag, True), bool):
                raise ValueError(f"{flag} must be a bool")
        excluded = self.parameters.get("exclude_symbols", ["SPY"])
        if not isinstance(excluded, (list, tuple)) or not all(
            isinstance(symbol, str) for symbol in excluded
        ):
            raise ValueError("exclude_symbols must be a list of symbols")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        trend_window = self.param_int("trend_window", 200)
        momentum_window = self.param_int("momentum_window", 252)
        momentum_skip = self.param_int("momentum_skip", 21)
        volatility_window = self.param_int("volatility_window", 20)
        top_n = self.param_int("top_n", 6)
        cap = self.param_float("max_single_weight", 0.10)
        volatility_target = self.param_float("annual_volatility_target", 0.10)
        correlation_floor = self.param_float("correlation_floor", 0.25)
        sizing = self.param_str("sizing", "inverse_vol")
        use_stock_trend = bool(self.parameters.get("use_stock_trend", True))
        use_market_regime = bool(self.parameters.get("use_market_regime", True))
        use_vol_target = bool(self.parameters.get("use_vol_target", True))
        excluded_raw = self.parameters.get("exclude_symbols", [context.benchmark_symbol])
        if not isinstance(excluded_raw, (list, tuple)):
            raise ValueError("exclude_symbols must be a list of symbols")
        excluded = {str(symbol).strip().upper() for symbol in excluded_raw}

        # Time-series market regime: SPY above its own long-term trend
        # (point-in-time, using only past bars).
        regime_ok = True
        spy_closes = context.closes(context.benchmark_symbol)
        if use_market_regime:
            spy_trend = simple_moving_average(spy_closes, trend_window)
            regime_ok = bool(spy_trend is not None and spy_closes and spy_closes[-1] > spy_trend)

        ranked: list[tuple[float, str]] = []
        volatilities: dict[str, float] = {}
        for symbol in sorted(context.history):
            if symbol in excluded or not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            trend = simple_moving_average(closes, trend_window)
            score = momentum_at(closes, momentum_window, skip_bars=momentum_skip)
            volatility = annualize_period_volatility(
                realized_volatility(closes, volatility_window), 252
            )
            if (
                not math.isfinite(score)
                or score <= 0
                or not math.isfinite(volatility)
                or volatility <= 0
            ):
                continue
            if use_stock_trend and (trend is None or closes[-1] <= trend):
                continue
            ranked.append((score, symbol))
            volatilities[symbol] = volatility

        if not regime_ok:
            # Market regime off: no new longs; the empty target takes the
            # book to cash at the next fill (monthly cadence).
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
                risk_multiplier=0.0,
                metadata={
                    "qualified_symbols": len(ranked),
                    "selected_symbols": (),
                    "market_regime": False,
                    "hypothesis": "long-horizon momentum with SPY regime gate",
                },
            )

        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        if sizing == "equal":
            weights = {symbol: min(cap, 1.0 / len(selected)) for symbol in selected}
        else:
            weights = dict(inverse_volatility_weights(selected, volatilities, maximum_weight=cap))
        risk_multiplier = 1.0
        if use_vol_target and weights:
            portfolio_volatility = conservative_portfolio_volatility(
                weights,
                volatilities,
                correlation_floor=correlation_floor,
            )
            risk_multiplier = (
                min(1.0, volatility_target / portfolio_volatility)
                if portfolio_volatility > 0
                else 1.0
            )
            weights = {symbol: weight * risk_multiplier for symbol, weight in weights.items()}
        targets = tuple(
            TargetWeight(symbol, weight, "long_horizon_momentum")
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
                "market_regime": regime_ok,
                "hypothesis": "long-horizon cross-sectional momentum, SMA200 stock trend, "
                "SPY SMA200 regime gate, monthly",
                "volatility_estimator": "realized volatility with pairwise correlation floor",
                "correlation_floor": correlation_floor,
            },
        )

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return False


__all__ = ["EquityLongHorizonMomentum1dV2"]
