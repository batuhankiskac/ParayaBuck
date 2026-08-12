from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

# Anchoring / nearness-to-high (George & Hwang 2004, JF 59(5); Jia, Simkins,
# Yan, Zhang & Zhao 2026, JBF 182): crypto assets trading CLOSEST to their
# trailing 52-week price high earn stronger subsequent cross-sectional
# returns — the prior high acts as a behavioral anchor. Price data ONLY: no
# momentum/RSI/trend/volume filters, no regime conditioning.
_DEFAULT_HIGH_WINDOW_DAYS = 364  # 52 weeks x 7 days; decision bar included
_DEFAULT_TOP_N = 2
_DEFAULT_REBALANCE_HOURS = 168  # weekly decisions: slow turnover for 25 bps taker fees


class CryptoHighProximity1dV1(PortfolioStrategy):
    """Slow-cadence long-only crypto high-proximity (anchoring).

    On each weekly decision every candidate is scored by its proximity to the
    trailing ``high_window_days`` price high over COMPLETED bars (the
    decision bar is the last bar of ``context.closes``): ``high_t = max(...)``
    over the last ``high_window_days`` closes and ``score_t = close_t /
    high_t`` in (0, 1]. EVERY pool member with enough history qualifies; the
    highest scores (closest to the high) rank first, tie-broken by symbol
    name for determinism, and the top ``top_n`` are held EQUAL weight, the
    remainder in cash.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` through the
    private ``_score_candidates`` so the live observation set (stream limit
    != opportunity universe) is selected by the same alpha the strategy
    trades.
    """

    name = "crypto_high_proximity_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "High Proximity v1"
    supported_timeframes = ("1d",)
    default_rebalance_hours = _DEFAULT_REBALANCE_HOURS
    rebalance_policy_declared = f"hours:{_DEFAULT_REBALANCE_HOURS}"

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("high_window_days", _DEFAULT_HIGH_WINDOW_DAYS) + 1

    def _validate_parameters(self) -> None:
        window = self.param_int("high_window_days", _DEFAULT_HIGH_WINDOW_DAYS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)
        rebalance_hours = self.param_int("rebalance_hours", _DEFAULT_REBALANCE_HOURS)
        if window < 20:
            raise ValueError("high_window_days must be at least 20")
        if not 1 <= top_n <= 5:
            raise ValueError("top_n must be between 1 and 5")
        if rebalance_hours <= 0 or rebalance_hours % 24:
            raise ValueError("rebalance_hours must be a positive multiple of 24")

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        """High-proximity ranking shared by decide() and rank_candidates() so
        the observation set is selected by the same signal the strategy
        trades.

        Returns ONLY qualified pool members as (score, symbol) pairs, best
        first = highest proximity (closest to the trailing high), tie-broken
        by symbol name.
        """
        window = self.param_int("high_window_days", _DEFAULT_HIGH_WINDOW_DAYS)
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            high = max(closes[-window:])
            if high <= 0:
                continue
            score = closes[-1] / high
            if not math.isfinite(score):
                continue
            qualified.append((score, symbol))
        qualified.sort(key=lambda item: (-item[0], item[1]))
        return tuple(qualified)

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        window = self.param_int("high_window_days", _DEFAULT_HIGH_WINDOW_DAYS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)

        ranked = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_symbols")
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        weight = 1.0 / len(selected)
        targets = tuple(
            TargetWeight(symbol, weight, reason="high_proximity") for symbol in sorted(selected)
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
                    f"high proximity: buy the {top_n} assets closest to their trailing "
                    f"{window}-bar high (score = close/high), equal weight, weekly, "
                    "price-only, no filters or regime conditioning"
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


__all__ = ["CryptoHighProximity1dV1"]
