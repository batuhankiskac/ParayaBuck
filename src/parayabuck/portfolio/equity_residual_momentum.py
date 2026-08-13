from __future__ import annotations

import math
import statistics
from datetime import datetime

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
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
# Minimum aligned return pairs for a stable beta estimate before a symbol
# qualifies for scoring (beta/residual is the strategy's core math).
_MIN_BETA_RETURNS = 60


class EquityResidualMomentum1dV1(PortfolioStrategy):
    """Long-horizon MARKET-RESIDUAL momentum with an idiosyncratic-risk filter.

    Hypothesis: after stripping the common market component (rolling CAPM-style
    beta against SPY), the residual return stream retains cross-sectional
    momentum predictability without the beta lottery exposure of raw momentum
    (Blitz, Huij & Martens 2011).

    Signal (1d, monthly rebalance):
        1. rolling OLS beta vs SPY over the trailing beta window, computed on
           TIMESTAMP-ALIGNED return pairs (stock and SPY bars are inner-joined
           on their timestamps, so a missing trading day can never misalign
           one series against the other);
        2. residual returns = own return - beta * market return;
        3. residual momentum score = compounded residuals over 12m formation,
           skipping the last month (no same-bar lookahead: the decision fires
           on a completed bar, and the score window ends before the skip);
        4. defensive filter: reject the top idiosyncratic-volatility names
           (lottery-like / extreme-idiosyncratic-risk stocks);
        5. rank by score, inverse-vol sized, per-asset weight cap.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` so the live
    observation set (stream limit != opportunity universe) is selected by the
    same alpha the strategy trades.
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

    def _score_candidates(
        self, context: PortfolioContext
    ) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        """Timestamp-aligned residual-momentum ranking of the candidate pool.

        Shared by ``decide`` (portfolio construction) and ``rank_candidates``
        (live observation-set selection) so BACKTEST, PAPER and LIVE score
        identically. Returns (post-ivol-filter ranked pairs, residual vols).
        """
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)
        vol_window = self.param_int("residual_vol_window", _DEFAULT_RESIDUAL_VOL_WINDOW)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        excluded_raw = self.parameters.get("exclude_symbols", [])
        if not isinstance(excluded_raw, (list, tuple)):
            raise ValueError("exclude_symbols must be a list of symbols")
        excluded = {
            str(symbol).strip().upper() for symbol in (*excluded_raw, context.benchmark_symbol)
        }
        benchmark_key = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        spy_bars = context.history.get(benchmark_key, ())
        if len(spy_bars) < beta_window + 1:
            return (), {}

        scored: list[tuple[float, str]] = []
        residual_vols: dict[str, float] = {}
        # The momentum window needs formation + skip aligned return pairs; the
        # volatility window and the beta floor are smaller by construction.
        minimum_aligned = max(formation + skip, vol_window + 1, _MIN_BETA_RETURNS)
        for symbol in sorted(context.history):
            if symbol in excluded or not context.in_universe(symbol):
                continue
            bars = context.history[symbol]
            if len(bars) < self.minimum_history_bars:
                continue
            aligned = _aligned_returns(bars, spy_bars, beta_window)
            if aligned is None or len(aligned[0]) < minimum_aligned:
                continue
            stock_returns, market_returns = aligned
            beta = _ols_beta(stock_returns, market_returns)
            residuals = [
                stock - beta * market for stock, market in zip(stock_returns, market_returns)
            ]
            score = _compounded_return(residuals[-formation - skip : -skip])
            residual_vol = _volatility(residuals[-vol_window:])
            if not math.isfinite(score) or not math.isfinite(residual_vol):
                continue
            if score <= 0 or residual_vol <= 0:
                continue
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
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(scored), residual_vols

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked, _vols = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 8)
        cap = self.param_float("max_single_weight", 0.10)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)

        benchmark_key = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        if len(context.history.get(benchmark_key, ())) < beta_window + 1:
            return self._cash_portfolio(context, "market_factor_unavailable")

        ranked, residual_vols = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_candidates")
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
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
                "qualified_symbols": len(ranked),
                "selected_symbols": selected,
                "ivol_filter_quantile": self.param_float("ivol_reject_quantile", 0.70),
                "hypothesis": (
                    "12m/1m market-residual momentum (rolling beta vs SPY, "
                    "timestamp-aligned returns), top idiosyncratic-volatility "
                    "names rejected, monthly, inverse-vol sized"
                ),
                "explanations": _equity_explanations(context, ranked, residual_vols, weights),
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


def _equity_explanations(
    context: PortfolioContext,
    ranked: tuple[tuple[float, str], ...],
    residual_vols: dict[str, float],
    weights: dict[str, float],
) -> dict[str, dict[str, float | int]]:
    """Concise per-holding decision reasons for the selected names.

    Ranking numbers are read straight from ``_score_candidates`` output
    (alpha rank is the 1-based position in the post-filter ranked pool; the
    momentum score and annualized idio-vol are the exact production values).
    ``current_weight`` is the holding recorded in ``context.holdings``
    (position quantity from the account snapshot; 0.0 when flat).
    """
    pool_vols = sorted(residual_vols[symbol] for _score, symbol in ranked)
    pool_size = max(1, len(pool_vols))
    explanations: dict[str, dict[str, float | int]] = {}
    for rank, (score, symbol) in enumerate(ranked, start=1):
        if symbol not in weights:
            continue
        below_or_equal = sum(1 for value in pool_vols if value <= residual_vols[symbol])
        explanations[symbol] = {
            "alpha_rank": rank,
            "residual_momentum_score": score,
            "idio_vol_percentile": round(below_or_equal / pool_size, 4),
            "target_weight": weights[symbol],
            "current_weight": float(context.holdings.get(symbol, 0.0)),
        }
    return explanations


def _aligned_returns(
    stock_bars: tuple[Bar, ...] | tuple[Bar, ...],
    market_bars: tuple[Bar, ...],
    window: int,
) -> tuple[list[float], list[float]] | None:
    """Timestamp-INNER-JOINed simple returns over the stock's trailing window.

    The stock's last ``window + 1`` bars are joined against the market series
    ON TIMESTAMP (never positionally): a stock that misses a trading day must
    not pair its Thursday return with the market's Wednesday return. Returns
    (stock_returns, market_returns) over the aligned pairs, or None when too
    few aligned pairs exist for a stable beta estimate.
    """
    stock = stock_bars[-(window + 1) :]
    market_by_timestamp: dict[datetime, float] = {
        bar.timestamp: bar.close for bar in market_bars if bar.close > 0
    }
    pairs: list[tuple[float, float]] = []
    for bar in stock:
        if bar.close <= 0:
            continue
        market_close = market_by_timestamp.get(bar.timestamp)
        if market_close is not None:
            pairs.append((bar.close, market_close))
    if len(pairs) < 3:
        return None
    stock_returns: list[float] = []
    market_returns: list[float] = []
    for index in range(1, len(pairs)):
        previous_stock, previous_market = pairs[index - 1]
        current_stock, current_market = pairs[index]
        if previous_stock > 0 and previous_market > 0:
            stock_returns.append(current_stock / previous_stock - 1)
            market_returns.append(current_market / previous_market - 1)
    if len(stock_returns) < 2:
        return None
    return stock_returns, market_returns


def _ols_beta(stock_returns: list[float], market_returns: list[float]) -> float:
    """OLS slope of stock returns on market returns (aligned pairs)."""
    market_mean = statistics.fmean(market_returns)
    stock_mean = statistics.fmean(stock_returns)
    variance = sum((value - market_mean) ** 2 for value in market_returns) / (
        len(market_returns) - 1
    )
    if variance <= 1e-12:
        return 0.0
    covariance = sum(
        (stock - stock_mean) * (market - market_mean)
        for stock, market in zip(stock_returns, market_returns)
    ) / (len(stock_returns) - 1)
    return covariance / variance


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
    return math.sqrt(sum((value - mean) ** 2 for value in residuals) / (len(residuals) - 1))


__all__ = ["EquityResidualMomentum1dV1"]
