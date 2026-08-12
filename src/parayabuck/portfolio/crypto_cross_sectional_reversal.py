from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

# Pure cross-sectional reversal (Debondt & Thaler 1985, applied to crypto):
# assets that LOST the most over the trailing formation window are bought
# (contrarian), assets that rose are never touched. One thesis only — no
# RSI/MACD overlays, no volatility filters, no regime conditioning.
_DEFAULT_FORMATION_BARS = 70
_DEFAULT_TOP_N = 2
_DEFAULT_REBALANCE_HOURS = 168  # weekly decisions: slow turnover for 25 bps taker fees


class CryptoCrossSectionalReversal1dV1(PortfolioStrategy):
    """Slow-cadence long-only crypto cross-sectional reversal.

    On each weekly decision every candidate is scored by its trailing
    ``formation_bars`` simple return over COMPLETED bars (the decision bar is
    the last bar of ``context.closes``). A symbol QUALIFIES only when that
    return is strictly negative — the strategy never buys the least-positive
    winner. Qualifiers are ranked most-negative-first (tie-broken by symbol
    name for determinism) and the top ``top_n`` are held EQUAL weight, the
    remainder in cash.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` through the
    private ``_score_candidates`` so the live observation set (stream limit
    != opportunity universe) is selected by the same alpha the strategy
    trades.
    """

    name = "crypto_cross_sectional_reversal_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    display_name = "Cross-Sectional Reversal v1"
    supported_timeframes = ("1d",)
    default_rebalance_hours = _DEFAULT_REBALANCE_HOURS
    rebalance_policy_declared = f"hours:{_DEFAULT_REBALANCE_HOURS}"

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("formation_bars", _DEFAULT_FORMATION_BARS) + 1

    def _validate_parameters(self) -> None:
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)
        rebalance_hours = self.param_int("rebalance_hours", _DEFAULT_REBALANCE_HOURS)
        if formation < 20:
            raise ValueError("formation_bars must be at least 20")
        if not 1 <= top_n <= 5:
            raise ValueError("top_n must be between 1 and 5")
        if rebalance_hours <= 0 or rebalance_hours % 24:
            raise ValueError("rebalance_hours must be a positive multiple of 24")

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        """Cross-sectional reversal ranking shared by decide() and
        rank_candidates() so the observation set is selected by the same
        signal the strategy trades.

        Returns ONLY qualified (strictly negative trailing return) symbols as
        (return, symbol) pairs, best first = most-negative first, tie-broken
        by symbol name.
        """
        formation_bars = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                continue
            base = closes[-(formation_bars + 1)]
            if base <= 0:
                continue
            ret = closes[-1] / base - 1
            if not math.isfinite(ret):
                continue
            if ret < 0:
                qualified.append((ret, symbol))
        qualified.sort(key=lambda item: (item[0], item[1]))
        return tuple(qualified)

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        formation_bars = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        top_n = self.param_int("top_n", _DEFAULT_TOP_N)

        ranked = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_losers")
        selected = tuple(symbol for _score, symbol in ranked[:top_n])
        weight = 1.0 / len(selected)
        targets = tuple(
            TargetWeight(symbol, weight, reason="cross_sectional_reversal")
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
                    f"cross-sectional reversal: buy the {top_n} worst-performing "
                    f"(most negative trailing {formation_bars}-bar return) assets, "
                    "equal weight, weekly, no filters or regime conditioning"
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


__all__ = ["CryptoCrossSectionalReversal1dV1"]
