from __future__ import annotations

import math
from datetime import timedelta

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import realized_volatility

# Low Realized Variance / Defensive (Ang, Hodrick, Xing & Zhang 2006):
# assets with LOWER recent realized variance earn better subsequent
# risk-adjusted cross-sectional returns (the low-volatility anomaly). The
# variance measure itself is the ranking signal — NO inverse-vol weighting
# across all assets, NO trend/momentum/volume filters, no regime
# conditioning. The AHXZ monthly construction (sample std of the previous
# ~22 daily returns) is applied on 1d bars, and the 23 closes used must span
# exactly 22 calendar days so a symbol with a recent data hole is skipped
# until 22 consecutive complete days accumulate (handles SOL/XRP-style gaps).
_DEFAULT_VARIANCE_LOOKBACK = 22
_DEFAULT_TOP_N = 2
_DEFAULT_REBALANCE_HOURS = 168  # weekly decisions: slow turnover for 25 bps taker fees


class CryptoLowVariance1dV1(PortfolioStrategy):
    """Slow-cadence long-only crypto low-realized-variance selection.

    On each weekly decision every candidate is scored by the sample standard
    deviation (n-1) of the trailing ``variance_lookback_days`` daily simple
    returns over COMPLETED bars (the decision bar is the last bar of
    ``context.closes``). A symbol QUALIFIES only when it is a PIT pool
    member, has at least ``variance_lookback_days + 1`` completed bars whose
    last 23 span exactly 22 calendar days (no missing day inside the window),
    and every window close is positive and finite. Qualifiers are ranked
    ascending by realized volatility (LOWEST variance first, tie-broken by
    symbol name for determinism) and the top ``top_n`` are held EQUAL
    weight, the remainder in cash.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` through the
    private ``_score_candidates`` so the live observation set (stream limit
    != opportunity universe) is selected by the same alpha the strategy
    trades.
    """

    name = "crypto_low_variance_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "Low Realized Variance v1"
    supported_timeframes = ("1d",)
    default_rebalance_hours = _DEFAULT_REBALANCE_HOURS
    rebalance_policy_declared = f"hours:{_DEFAULT_REBALANCE_HOURS}"

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("variance_lookback_days", _DEFAULT_VARIANCE_LOOKBACK) + 1

    def _validate_parameters(self) -> None:
        lookback = self.param_int("variance_lookback_days", _DEFAULT_VARIANCE_LOOKBACK)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)
        rebalance_hours = self.param_int("rebalance_hours", _DEFAULT_REBALANCE_HOURS)
        if lookback < 20:
            raise ValueError("variance_lookback_days must be at least 20")
        if not 1 <= top_n <= 5:
            raise ValueError("top_n must be between 1 and 5")
        if rebalance_hours <= 0 or rebalance_hours % 24:
            raise ValueError("rebalance_hours must be a positive multiple of 24")

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        """Low-variance ranking shared by decide() and rank_candidates() so
        the observation set is selected by the same signal the strategy
        trades.

        Returns ONLY qualified symbols as (sigma, symbol) pairs, best first =
        LOWEST realized volatility first, tie-broken by symbol name.
        """
        lookback = self.param_int("variance_lookback_days", _DEFAULT_VARIANCE_LOOKBACK)
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            # Consecutive-days rule: the last 23 completed bars must span
            # exactly 22 calendar days, i.e. no missing day inside the
            # lookback window (handles SOL/XRP-style data holes).
            bars = context.history[symbol]
            window_bars = bars[-(lookback + 1) :]
            if window_bars[-1].timestamp - window_bars[0].timestamp != timedelta(days=lookback):
                continue
            window_closes = closes[-(lookback + 1) :]
            if not all(math.isfinite(close) and close > 0 for close in window_closes):
                continue
            sigma = realized_volatility(closes, lookback)
            if not math.isfinite(sigma):
                continue
            qualified.append((sigma, symbol))
        qualified.sort(key=lambda item: (item[0], item[1]))
        return tuple(qualified)

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        lookback = self.param_int("variance_lookback_days", _DEFAULT_VARIANCE_LOOKBACK)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)

        ranked = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_low_variance")
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        weight = 1.0 / len(selected)
        targets = tuple(
            TargetWeight(symbol, weight, reason="low_realized_variance")
            for symbol in sorted(selected)
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
                "hypothesis": (
                    f"low realized variance: buy the {top_n} lowest-variance "
                    f"(trailing {lookback}-day realized vol, AHXZ 2006) assets, "
                    "equal weight, weekly, no filters or regime conditioning"
                ),
                "explanations": {
                    symbol: {
                        "alpha_rank": rank,
                        "realized_vol_22d": sigma,
                        "target_weight": weight,
                        "current_weight": float(context.holdings.get(symbol, 0.0)),
                    }
                    for rank, (sigma, symbol) in enumerate(ranked[:top_n], start=1)
                },
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


__all__ = ["CryptoLowVariance1dV1"]
