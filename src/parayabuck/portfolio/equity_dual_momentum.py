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
    simple_moving_average,
)
from parayabuck.portfolio.universes import (
    BROAD_MARKET_ETFS,
    EQUITY_ETF_UNIVERSE,
    EQUITY_UNIVERSE_VERSION,
)

MOMENTUM_SKIP_BARS = 21
BARS_PER_YEAR = 252
RISK_FREE_ANNUAL_DEFAULT = 0.02


def _momentum_score(closes: list[float], scheme: str) -> float:
    if scheme == "12_1":
        return momentum_at(closes, 252, MOMENTUM_SKIP_BARS)
    if scheme == "6_1":
        return momentum_at(closes, 126, MOMENTUM_SKIP_BARS)
    if scheme == "3_6_12":
        return composite_momentum(closes, (63, 126, 252), MOMENTUM_SKIP_BARS)
    raise ValueError(f"Unsupported momentum scheme: {scheme}")


def _absolute_filter_passes(
    closes: list[float],
    filter_name: str,
    *,
    risk_free_annual: float,
) -> bool:
    raw_momentum = momentum_at(closes, 252, MOMENTUM_SKIP_BARS)
    if not math.isfinite(raw_momentum):
        return False
    momentum = raw_momentum
    if filter_name == "positive_momentum":
        return momentum > 0
    if filter_name == "above_200dma":
        average = simple_moving_average(closes, 200)
        return average is not None and closes[-1] > average
    if filter_name == "excess_over_cash":
        days: float = 231.0
        hurdle: float = (1.0 + risk_free_annual) ** (days / 365.0) - 1.0
        return bool(momentum > hurdle)
    raise ValueError(f"Unsupported absolute filter: {filter_name}")


class EquityEtfDualMomentumV1(PortfolioStrategy):
    """Monthly 12-1 relative momentum over a permanent ETF universe with an
    absolute-momentum risk-off gate, inverse-volatility weights and an
    optional diagonal volatility-targeting risk layer.

    Parameter space is intentionally small and mechanism-level: three
    momentum schemes, three absolute filters, two volatility windows and an
    optional 8% volatility target — never an open-ended lookback sweep.
    """

    name = "equity_etf_dual_momentum_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"

    @property
    def minimum_history_bars(self) -> int:
        scheme = self.param_str("momentum_scheme", "12_1")
        scheme_need = {
            "12_1": 252 + MOMENTUM_SKIP_BARS + 1,
            "6_1": 126 + MOMENTUM_SKIP_BARS + 1,
            "3_6_12": 252 + MOMENTUM_SKIP_BARS + 1,
        }[scheme]
        # Every absolute filter evaluates a raw 12-1 momentum
        # (252 + skip + 1 bars) and above_200dma needs 200 closes on top; the
        # common requirement is the 12-1 lookback, never less.
        filter_need = 252 + MOMENTUM_SKIP_BARS + 1
        return max(scheme_need, filter_need)

    def _validate_parameters(self) -> None:
        allowed_schemes = {"12_1", "6_1", "3_6_12"}
        allowed_filters = {"positive_momentum", "above_200dma", "excess_over_cash"}
        scheme = self.param_str("momentum_scheme", "12_1")
        filter_name = self.param_str("absolute_filter", "positive_momentum")
        if scheme not in allowed_schemes:
            raise ValueError(f"Unsupported momentum scheme: {scheme}")
        if filter_name not in allowed_filters:
            raise ValueError(f"Unsupported absolute filter: {filter_name}")
        volatility_window = self.parameters.get("volatility_window", 20)
        if not isinstance(volatility_window, int) or volatility_window < 10:
            raise ValueError("Volatility window must be an integer of at least 10 bars")
        top_n = self.parameters.get("top_n", 3)
        if not isinstance(top_n, int) or top_n <= 0:
            raise ValueError("top_n must be a positive integer")
        volatility_target = self.param_optional_float("volatility_target")
        if volatility_target is not None and not isinstance(volatility_target, (int, float)):
            raise ValueError("Volatility target must be numeric or null")
        for name in ("max_single_weight", "max_sector_weight"):
            value = self.parameters.get(name, 0.5 if name == "max_single_weight" else 0.40)
            if not isinstance(value, (int, float)) or not 0 < float(value) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        risk_free = self.parameters.get("risk_free_annual", RISK_FREE_ANNUAL_DEFAULT)
        if not isinstance(risk_free, (int, float)) or float(risk_free) < 0:
            raise ValueError("risk_free_annual must be non-negative")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        scheme = self.param_str("momentum_scheme", "12_1")
        filter_name = self.param_str("absolute_filter", "positive_momentum")
        volatility_window = self.param_int("volatility_window", 20)
        top_n = self.param_int("top_n", 3)
        max_single_weight = self.param_float("max_single_weight", 0.5)
        max_sector_weight = self.param_float("max_sector_weight", 0.40)
        volatility_target = self.param_optional_float("volatility_target")
        risk_free_annual = self.param_float("risk_free_annual", RISK_FREE_ANNUAL_DEFAULT)

        scored: list[tuple[float, str]] = []
        volatilities: dict[str, float] = {}
        for symbol in EQUITY_ETF_UNIVERSE:
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if not _absolute_filter_passes(
                closes, filter_name, risk_free_annual=risk_free_annual
            ):
                continue
            score = _momentum_score(closes, scheme)
            if not math.isfinite(score) or score <= 0:
                continue
            scored.append((score, symbol))
            volatilities[symbol] = annualize_period_volatility(
                realized_volatility(closes, volatility_window), BARS_PER_YEAR
            )
        scored.sort(reverse=True)
        selected = tuple(symbol for _, symbol in scored[:top_n])
        if not selected:
            return self._portfolio(context, (), {}, RebalanceReason.RISK_OFF)

        weights = dict(
            inverse_volatility_weights(
                selected,
                volatilities,
                maximum_weight=max_single_weight,
            )
        )
        sector_symbols = [symbol for symbol in selected if symbol not in BROAD_MARKET_ETFS]
        sector_total = sum(weights.get(symbol, 0.0) for symbol in sector_symbols)
        if sector_total > max_sector_weight and sector_symbols:
            scale = max_sector_weight / sector_total
            for symbol in sector_symbols:
                weights[symbol] = weights.get(symbol, 0.0) * scale
        if not weights:
            return self._portfolio(context, (), {}, RebalanceReason.RISK_OFF)

        if volatility_target is not None:
            target = float(volatility_target)
            portfolio_volatility = diagonal_portfolio_volatility(weights, volatilities)
            scale = min(1.0, target / portfolio_volatility) if portfolio_volatility > 0 else 1.0
            weights = {symbol: weight * scale for symbol, weight in weights.items()}
            return self._portfolio(
                context,
                tuple((symbol, weight) for symbol, weight in weights.items() if weight > 0),
                {symbol: weight for symbol, weight in weights.items()},
                RebalanceReason.SCHEDULED,
                risk_multiplier=scale,
            )
        return self._portfolio(
            context, tuple(weights.items()), weights, RebalanceReason.SCHEDULED
        )

    def _portfolio(
        self,
        context: PortfolioContext,
        weighted: tuple[tuple[str, float], ...],
        weights: dict[str, float],
        reason: RebalanceReason,
        *,
        risk_multiplier: float = 1.0,
    ) -> TargetPortfolio:
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        targets = tuple(
            TargetWeight(symbol, weight, reason="momentum_rank_inverse_vol")
            for symbol, weight in weighted
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=cash_weight,
            rebalance_reason=reason,
            universe_version=context.universe_version or EQUITY_UNIVERSE_VERSION,
            universe_snapshot_id="permanent_etf_v1",
            parameters=self.parameter_tuple(),
            risk_multiplier=risk_multiplier,
            metadata={"universe_size": len(EQUITY_ETF_UNIVERSE), "selected": len(targets)},
        )


__all__ = ["EquityEtfDualMomentumV1"]
