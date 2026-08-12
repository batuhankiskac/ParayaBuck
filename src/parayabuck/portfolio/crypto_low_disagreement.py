from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

# Abnormal Volume / Investor Disagreement (low side), GKM 2001 50-day interval:
# ratio_t = mean(volume over t-5..t-1, nonzero bars only) / mean(volume over
# t-50..t-6, nonzero bars only). Unusually high volume proxies disagreement /
# attention and predicts LOWER subsequent returns, so the long-only strategy
# holds the LOWEST-abnormal-volume assets. ZERO-VOLUME RULE (GKM footnote 10):
# means are computed over NONZERO bars only and min-nonzero gates are enforced,
# so a no-trade day (volume == 0) can NEVER mechanically become the low-volume
# buy signal. The DECISION bar t's own volume is not part of the signal: the
# formation window ends at t-1.
_DEFAULT_VOLUME_SHORT_DAYS = 5
_DEFAULT_VOLUME_LONG_DAYS = 45
_DEFAULT_MIN_NONZERO_SHORT = 4
_DEFAULT_MIN_NONZERO_LONG = 40
_DEFAULT_MIN_HISTORY_DAYS = 51
_DEFAULT_TOP_N = 2
_DEFAULT_REBALANCE_HOURS = 168  # weekly decisions: slow turnover for 25 bps taker fees


class CryptoLowDisagreement1dV1(PortfolioStrategy):
    """Slow-cadence long-only crypto low-abnormal-volume strategy.

    On each weekly decision every candidate is scored by the GKM 50-day
    interval reproduction of abnormal volume over COMPLETED bars: the 5-bar
    formation window (``bars[-6:-1]``) vs the disjoint 45-bar reference window
    (``bars[-51:-6]``), both means computed over NONZERO bars only. The
    decision bar is ``bars[-1]`` and its volume NEVER enters the ratio.

    A symbol QUALIFIES only when it is a PIT pool member with at least
    ``min_history_days`` completed bars AND at least ``min_nonzero_short`` of
    the ``volume_short_days`` formation bars AND at least
    ``min_nonzero_long`` of the ``volume_long_days`` reference bars carry
    volume > 0. Zero-volume days are excluded from the means; they never
    manufacture a low-volume signal.

    Qualifiers are ranked ASCENDING by ratio (LOWEST abnormal volume first),
    tie-broken by symbol name for determinism, and the top ``top_n`` are held
    EQUAL weight, the remainder in cash.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` through the
    private ``_score_candidates`` so the live observation set (stream limit
    != opportunity universe) is selected by the same alpha the strategy
    trades.
    """

    name = "crypto_low_disagreement_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "Low Abnormal Volume v1"
    supported_timeframes = ("1d",)
    default_rebalance_hours = _DEFAULT_REBALANCE_HOURS
    rebalance_policy_declared = f"hours:{_DEFAULT_REBALANCE_HOURS}"

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("min_history_days", _DEFAULT_MIN_HISTORY_DAYS)

    def _validate_parameters(self) -> None:
        short = self.param_int("volume_short_days", _DEFAULT_VOLUME_SHORT_DAYS)
        long_ = self.param_int("volume_long_days", _DEFAULT_VOLUME_LONG_DAYS)
        min_short = self.param_int("min_nonzero_short", _DEFAULT_MIN_NONZERO_SHORT)
        min_long = self.param_int("min_nonzero_long", _DEFAULT_MIN_NONZERO_LONG)
        min_history = self.param_int("min_history_days", _DEFAULT_MIN_HISTORY_DAYS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)
        rebalance_hours = self.param_int("rebalance_hours", _DEFAULT_REBALANCE_HOURS)
        if short < 3:
            raise ValueError("volume_short_days must be at least 3")
        if long_ < 20:
            raise ValueError("volume_long_days must be at least 20")
        if min_history < short + long_ + 1:
            raise ValueError(
                "min_history_days must be at least volume_short_days + volume_long_days + 1"
            )
        if not 1 <= min_short <= short:
            raise ValueError("min_nonzero_short must be between 1 and volume_short_days")
        if not 1 <= min_long <= long_:
            raise ValueError("min_nonzero_long must be between 1 and volume_long_days")
        if not 1 <= top_n <= 5:
            raise ValueError("top_n must be between 1 and 5")
        if rebalance_hours <= 0 or rebalance_hours % 24:
            raise ValueError("rebalance_hours must be a positive multiple of 24")

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        """Low-abnormal-volume ranking shared by decide() and rank_candidates()
        so the observation set is selected by the same signal the strategy
        trades.

        Returns ONLY qualified symbols as (ratio, symbol) pairs, best first =
        LOWEST ratio first, tie-broken by symbol name.
        """
        short = self.param_int("volume_short_days", _DEFAULT_VOLUME_SHORT_DAYS)
        long_ = self.param_int("volume_long_days", _DEFAULT_VOLUME_LONG_DAYS)
        min_short = self.param_int("min_nonzero_short", _DEFAULT_MIN_NONZERO_SHORT)
        min_long = self.param_int("min_nonzero_long", _DEFAULT_MIN_NONZERO_LONG)
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            bars = context.history[symbol]
            if len(bars) < self.minimum_history_bars:
                continue
            # Formation = days t-5..t-1 (bars[-6:-1]); reference = days
            # t-50..t-6 (bars[-51:-6]). The decision bar bars[-1] is never
            # part of either window.
            formation = bars[-(short + 1) : -1]
            reference = bars[-(short + long_ + 1) : -(short + 1)]
            formation_nonzero = [bar.volume for bar in formation if bar.volume > 0]
            reference_nonzero = [bar.volume for bar in reference if bar.volume > 0]
            if len(formation_nonzero) < min_short or len(reference_nonzero) < min_long:
                continue
            short_mean = math.fsum(formation_nonzero) / len(formation_nonzero)
            long_mean = math.fsum(reference_nonzero) / len(reference_nonzero)
            if long_mean <= 0:
                # Unreachable given the nonzero gates, but a zero denominator
                # must never turn into an infinite score.
                continue
            ratio = short_mean / long_mean
            if not math.isfinite(ratio) or ratio <= 0:
                continue
            qualified.append((ratio, symbol))
        qualified.sort(key=lambda item: (item[0], item[1]))
        return tuple(qualified)

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        short = self.param_int("volume_short_days", _DEFAULT_VOLUME_SHORT_DAYS)
        long_ = self.param_int("volume_long_days", _DEFAULT_VOLUME_LONG_DAYS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)

        ranked = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_low_volume")
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        weight = 1.0 / len(selected)
        targets = tuple(
            TargetWeight(symbol, weight, reason="low_abnormal_volume")
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
                    f"low abnormal volume: hold the {top_n} LOWEST-abnormal-volume "
                    f"(formation {short}-day vs reference {long_}-day volume ratio, "
                    "nonzero bars only) assets, equal weight, weekly; zero-volume "
                    "days never produce a buy signal"
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


__all__ = ["CryptoLowDisagreement1dV1"]
