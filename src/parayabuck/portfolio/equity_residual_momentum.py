from __future__ import annotations

import math
import statistics

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    inverse_volatility_weights,
)

# Residual-momentum literature defaults: 12-month formation / 1-month skip
# (Jegadeesh & Titman 1993) applied to MARKET-RESIDUAL returns (Blitz, Huij &
# Martens 2011). The market component is estimated as a rolling beta against
# the benchmark (SPY), so the score isolates the idiosyncratic return stream
# that momentum is documented to predict.
_DEFAULT_FORMATION_BARS = 252
_DEFAULT_SKIP_BARS = 21
_DEFAULT_BETA_WINDOW_BARS = 273  # formation + skip, beta estimated on all of it
_DEFAULT_RESIDUAL_VOL_WINDOW = 63  # ~3 months, used for the ivol filter and sizing


class EquityResidualMomentum1dV1(PortfolioStrategy):
    """Long-horizon MARKET-RESIDUAL momentum with an idiosyncratic-risk filter.

    Hypothesis: after stripping the common market component (rolling CAPM-style
    beta against SPY), the residual return stream retains cross-sectional
    momentum predictability without the beta lottery exposure of raw momentum
    (Blitz, Huij & Martens 2011).

    Signal (1d, monthly rebalance):
        1. rolling OLS beta vs SPY over the trailing beta window;
        2. residual returns = own return - beta * market return;
        3. residual momentum score = compounded residuals over 12m formation,
           skipping the last month (no same-bar lookahead: the decision fires
           on a completed bar, and the score window ends before the skip);
        4. defensive filter: reject the top idiosyncratic-volatility names
           (lottery-like / extreme-idiosyncratic-risk stocks);
        5. rank by score, inverse-vol sized, per-asset weight cap.

    This is deliberately NOT an indicator soup: one alpha component
    (residual momentum) plus one risk-quality filter (idiosyncratic vol).
    """

    name = "equity_residual_momentum_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Residual Momentum v1"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "monthly"

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS) + 1

    def _validate_parameters(self) -> None:
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)
        vol_window = self.param_int("residual_vol_window", _DEFAULT_RESIDUAL_VOL_WINDOW)
        top_n = self.param_int("top_n", 8)
        cap = self.param_float("max_single_weight", 0.10)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        if formation < 60 or skip < 1:
            raise ValueError("formation_bars and skip_bars are too short")
        if skip >= formation:
            raise ValueError("skip_bars must be smaller than formation_bars")
        if beta_window < formation + skip:
            raise ValueError("beta_window_bars must cover formation plus skip")
        if vol_window < 20:
            raise ValueError("residual_vol_window must be at least 20 bars")
        if not 1 <= top_n <= 20:
            raise ValueError("top_n must be between 1 and 20")
        if not 0 < cap <= 0.25:
            raise ValueError("max_single_weight must be in (0, 0.25]")
        if not 0.5 < ivol_quantile < 0.95:
            raise ValueError("ivol_reject_quantile must be in (0.5, 0.95)")
        excluded = self.parameters.get("exclude_symbols", [])
        if not isinstance(excluded, (list, tuple)) or not all(
            isinstance(symbol, str) for symbol in excluded
        ):
            raise ValueError("exclude_symbols must be a list of symbols")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)
        vol_window = self.param_int("residual_vol_window", _DEFAULT_RESIDUAL_VOL_WINDOW)
        top_n = self.param_int("top_n", 8)
        cap = self.param_float("max_single_weight", 0.10)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        excluded_raw = self.parameters.get("exclude_symbols", [])
        if not isinstance(excluded_raw, (list, tuple)):
            raise ValueError("exclude_symbols must be a list of symbols")
        excluded = {
            str(symbol).strip().upper() for symbol in (*excluded_raw, context.benchmark_symbol)
        }

        # The market factor must be observable: SPY in the supplied history
        # with at least the beta window of completed bars.
        spy_closes = context.closes(context.benchmark_symbol)
        if len(spy_closes) < beta_window + 1:
            return self._cash_portfolio(context, "market_factor_unavailable")

        # Rolling CAPM-style beta against the benchmark (point-in-time: only
        # bars at or before the decision timestamp, and the score window ends
        # a full skip before the decision bar).
        market_returns = _simple_returns(spy_closes, beta_window)
        market_mean = statistics.fmean(market_returns)
        market_variance = _variance(market_returns, market_mean)
        beta_valid = market_variance > 1e-12

        scored: list[tuple[float, str]] = []
        residual_vols: dict[str, float] = {}
        pre_filter: list[str] = []
        for symbol in sorted(context.history):
            if symbol in excluded or not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            stock_returns = _simple_returns(closes, beta_window)
            if beta_valid:
                beta = _ols_beta(stock_returns, market_returns, market_mean, market_variance)
            else:
                # Flat market factor: no common component to strip.
                beta = 0.0
            residuals = [
                stock - beta * market for stock, market in zip(stock_returns, market_returns)
            ]
            score = _compounded_return(residuals[-formation - skip : -skip])
            residual_vol = _volatility(residuals[-vol_window:])
            if not math.isfinite(score) or not math.isfinite(residual_vol):
                continue
            if score <= 0 or residual_vol <= 0:
                continue
            pre_filter.append(symbol)
            scored.append((score, symbol))
            residual_vols[symbol] = annualize_period_volatility(residual_vol, 252)

        # Defensive filter: reject the extreme idiosyncratic-volatility names
        # (lottery-like tail) before ranking, not as a second alpha signal.
        if scored:
            sorted_vols = sorted(residual_vols[symbol] for _score, symbol in scored)
            cutoff = min(len(sorted_vols) - 1, int(ivol_quantile * len(sorted_vols)))
            threshold = sorted_vols[cutoff]
            scored = [
                (score, symbol) for score, symbol in scored if residual_vols[symbol] <= threshold
            ]
        if not scored:
            return self._cash_portfolio(context, "no_qualified_candidates")

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(symbol for _score, symbol in scored[:top_n])
        weights = dict(inverse_volatility_weights(selected, residual_vols, maximum_weight=cap))
        targets = tuple(
            TargetWeight(symbol, weight, reason="residual_momentum")
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
                "pre_filter_count": len(pre_filter),
                "qualified_symbols": len(scored),
                "selected_symbols": selected,
                "ivol_filter_quantile": ivol_quantile,
                "hypothesis": (
                    "12m/1m market-residual momentum (rolling beta vs SPY), "
                    "top idiosyncratic-volatility names rejected, monthly, "
                    "inverse-vol sized"
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


def _simple_returns(closes: list[float], window: int) -> list[float]:
    """Simple returns over the trailing ``window + 1`` closes (oldest first)."""
    segment = closes[-(window + 1) :]
    return [
        segment[index] / segment[index - 1] - 1
        for index in range(1, len(segment))
        if segment[index - 1] > 0
    ]


def _variance(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _ols_beta(
    stock_returns: list[float],
    market_returns: list[float],
    market_mean: float,
    market_variance: float,
) -> float:
    stock_mean = statistics.fmean(stock_returns)
    covariance = sum(
        (stock - stock_mean) * (market - market_mean)
        for stock, market in zip(stock_returns, market_returns)
    ) / (len(stock_returns) - 1)
    return covariance / market_variance


def _compounded_return(residuals: list[float]) -> float:
    if not residuals:
        return float("-inf")
    value = 1.0
    for residual in residuals:
        value *= 1.0 + residual
        if not math.isfinite(value) or value <= 0:
            return float("-inf")
    return value - 1.0


def _volatility(residuals: list[float]) -> float:
    if len(residuals) < 2:
        return 0.0
    mean = statistics.fmean(residuals)
    return math.sqrt(_variance(residuals, mean))


__all__ = ["EquityResidualMomentum1dV1"]
