"""The frozen ``equity_overnight_reversal_v1`` research strategy.

This module is deliberately outside ``src/parayabuck/portfolio``.  It is not
registered as a product strategy and cannot be selected by PAPER/LIVE config.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from parayabuck.backtest.portfolio_engine import session_date_for
from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

CANDIDATE_NAME = "equity_overnight_reversal_v1"
CANDIDATE_VERSION = "1.0.0-research"
TOP_N = 6
EQUITY_ALLOCATION = 0.55
FIXED_PER_NAME_WEIGHT = EQUITY_ALLOCATION / TOP_N
POSITION_CAP = 0.10
BENCHMARK = "SPY"

FROZEN_PARAMETERS: dict[str, object] = {
    "top_n": TOP_N,
    "per_name_weight": FIXED_PER_NAME_WEIGHT,
    "maximum_allocation": EQUITY_ALLOCATION,
    "max_single_weight": POSITION_CAP,
    "rebalance_frequency": "daily",
    "exclude_symbols": [BENCHMARK],
}


@dataclass(frozen=True)
class OvernightSignal:
    symbol: str
    decision_date: date
    prior_date: date | None
    prior_close: float | None
    current_open: float | None
    stock_overnight: float | None
    spy_overnight: float | None
    spy_excess: float | None
    score: float | None
    eligibility: str
    reason: str
    eligible_pool_rank: int | None = None
    observation_rank: int | None = None
    selected: bool = False
    selected_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "decision_date": self.decision_date.isoformat(),
            "prior_date": self.prior_date.isoformat() if self.prior_date else None,
            "prior_close": self.prior_close,
            "current_open": self.current_open,
            "stock_overnight": self.stock_overnight,
            "spy_overnight": self.spy_overnight,
            "spy_excess": self.spy_excess,
            "score": self.score,
            "eligibility": self.eligibility,
            "reason": self.reason,
            "eligible_pool_rank": self.eligible_pool_rank,
            "observation_rank": self.observation_rank,
            "selected": self.selected,
            "selected_reason": self.selected_reason,
        }


@dataclass(frozen=True)
class DailySessionRebalancePolicy:
    """Research-only daily policy matching one regular session per decision."""

    market: Market = Market.EQUITY
    frequency: str = "daily"

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        if previous is None:
            return True
        return bool(session_date_for(previous, self.market) != session_date_for(current, self.market))


def _session_map(bars: tuple[Bar, ...] | list[Bar]) -> dict[date, Bar]:
    result: dict[date, Bar] = {}
    for bar in bars:
        day = session_date_for(bar.timestamp, Market.EQUITY)
        if day in result:
            raise ValueError(f"duplicate session bar in strategy context: {bar.symbol} {day}")
        result[day] = bar
    return result


def _snapshot_symbols(context: PortfolioContext) -> tuple[str, ...]:
    snapshots = getattr(context.universe, "snapshots", ())
    if snapshots:
        index = bisect_right(snapshots, context.decision_timestamp, key=lambda item: item.effective_at)
        if index > 0:
            return tuple(sorted(snapshots[index - 1].symbols))
        return ()
    return tuple(sorted(context.history))


def _valid_pair_signal(
    symbol: str,
    decision_day: date,
    prior_day: date | None,
    bars: tuple[Bar, ...],
    spy_bars: tuple[Bar, ...],
) -> OvernightSignal:
    if prior_day is None:
        return OvernightSignal(
            symbol, decision_day, None, None, None, None, None, None, None, "ineligible", "no_prior_spy_session"
        )
    stock_by_day = _session_map(bars)
    spy_by_day = _session_map(spy_bars)
    current = stock_by_day.get(decision_day)
    prior = stock_by_day.get(prior_day)
    spy_current = spy_by_day.get(decision_day)
    spy_prior = spy_by_day.get(prior_day)
    if spy_current is None or spy_prior is None:
        return OvernightSignal(
            symbol, decision_day, prior_day, None, None, None, None, None, None, "ineligible", "missing_spy_reference"
        )
    if current is None:
        return OvernightSignal(
            symbol,
            decision_day,
            prior_day,
            prior.close if prior else None,
            None,
            None,
            None,
            None,
            None,
            "ineligible",
            "missing_provider_observation",
        )
    if prior is None:
        return OvernightSignal(
            symbol, decision_day, prior_day, None, current.open, None, None, None, None, "ineligible", "session_gap"
        )
    stock_overnight = current.open / prior.close - 1.0
    spy_overnight = spy_current.open / spy_prior.close - 1.0
    excess = stock_overnight - spy_overnight
    score = -excess
    return OvernightSignal(
        symbol=symbol,
        decision_date=decision_day,
        prior_date=prior_day,
        prior_close=prior.close,
        current_open=current.open,
        stock_overnight=stock_overnight,
        spy_overnight=spy_overnight,
        spy_excess=excess,
        score=score,
        eligibility="eligible",
        reason="valid_common_session_pair",
    )


def signal_from_bars(
    symbol: str,
    decision_day: date,
    prior_day: date | None,
    stock_by_day: dict[date, Bar],
    spy_by_day: dict[date, Bar],
) -> OvernightSignal:
    """Compute one signal from pre-indexed bars for the audit path."""
    if prior_day is None:
        return OvernightSignal(
            symbol, decision_day, None, None, None, None, None, None, None, "ineligible", "no_prior_spy_session"
        )
    current = stock_by_day.get(decision_day)
    prior = stock_by_day.get(prior_day)
    spy_current = spy_by_day.get(decision_day)
    spy_prior = spy_by_day.get(prior_day)
    if spy_current is None or spy_prior is None:
        return OvernightSignal(
            symbol, decision_day, prior_day, None, None, None, None, None, None, "ineligible", "missing_spy_reference"
        )
    if current is None:
        return OvernightSignal(
            symbol,
            decision_day,
            prior_day,
            prior.close if prior else None,
            None,
            None,
            None,
            None,
            None,
            "ineligible",
            "missing_provider_observation",
        )
    if prior is None:
        return OvernightSignal(
            symbol, decision_day, prior_day, None, current.open, None, None, None, None, "ineligible", "session_gap"
        )
    stock_overnight = current.open / prior.close - 1.0
    spy_overnight = spy_current.open / spy_prior.close - 1.0
    excess = stock_overnight - spy_overnight
    return OvernightSignal(
        symbol=symbol,
        decision_date=decision_day,
        prior_date=prior_day,
        prior_close=prior.close,
        current_open=current.open,
        stock_overnight=stock_overnight,
        spy_overnight=spy_overnight,
        spy_excess=excess,
        score=-excess,
        eligibility="eligible",
        reason="valid_common_session_pair",
    )


def score_context(context: PortfolioContext) -> tuple[OvernightSignal, ...]:
    """Return one deterministic signal row per visible pool symbol."""
    decision_day = session_date_for(context.decision_timestamp, Market.EQUITY)
    spy_symbol = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
    spy_bars = context.history.get(spy_symbol, ())
    spy_dates = sorted(_session_map(spy_bars))
    # The current completed bar is the decision input, never the overnight
    # prior.  Use a strict-left lookup so p(d) is the immediately preceding
    # common SPY session even when the current day's bar is already present.
    prior_index = bisect_left(spy_dates, decision_day) - 1
    prior_day = spy_dates[prior_index] if prior_index >= 0 else None
    pool = _snapshot_symbols(context)
    visible = set(context.history)
    rows: list[OvernightSignal] = []
    for symbol in pool:
        canonical = canonical_symbol(symbol, Market.EQUITY)
        if canonical == spy_symbol:
            continue
        if canonical not in visible:
            rows.append(
                OvernightSignal(
                    symbol=canonical,
                    decision_date=decision_day,
                    prior_date=prior_day,
                    prior_close=None,
                    current_open=None,
                    stock_overnight=None,
                    spy_overnight=None,
                    spy_excess=None,
                    score=None,
                    eligibility="ineligible",
                    reason="outside_observation_context",
                )
            )
            continue
        rows.append(_valid_pair_signal(canonical, decision_day, prior_day, context.history[canonical], spy_bars))
    valid = sorted(
        (row for row in rows if row.score is not None),
        key=lambda row: (-float(cast(float, row.score)), row.symbol),
    )
    rank_map = {row.symbol: index for index, row in enumerate(valid, start=1)}
    return tuple(
        OvernightSignal(
            symbol=row.symbol,
            decision_date=row.decision_date,
            prior_date=row.prior_date,
            prior_close=row.prior_close,
            current_open=row.current_open,
            stock_overnight=row.stock_overnight,
            spy_overnight=row.spy_overnight,
            spy_excess=row.spy_excess,
            score=row.score,
            eligibility=row.eligibility,
            reason=row.reason,
            eligible_pool_rank=rank_map.get(row.symbol),
            observation_rank=row.observation_rank,
            selected=row.selected,
            selected_reason=row.selected_reason,
        )
        for row in rows
    )


class OvernightReversalStrategy(PortfolioStrategy):
    """Frozen one-session SPY-excess overnight reversal ranker."""

    name = CANDIDATE_NAME
    version = CANDIDATE_VERSION
    display_name = "Overnight Reversal v1 (research only)"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "daily"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        merged = dict(FROZEN_PARAMETERS)
        if parameters is not None:
            merged.update(parameters)
        self.recorded_targets: list[TargetPortfolio] = []
        super().__init__(merged)

    @property
    def minimum_history_bars(self) -> int:
        return 2

    def _validate_parameters(self) -> None:
        if self.parameters != FROZEN_PARAMETERS:
            raise ValueError("Overnight reversal parameters are frozen and cannot be retuned")

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        rows = score_context(context)
        return tuple(
            row.symbol
            for row in sorted(
                (row for row in rows if row.score is not None),
                key=lambda row: (-float(cast(float, row.score)), row.symbol),
            )
        )

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        rows = score_context(context)
        ranked = sorted(
            (row for row in rows if row.score is not None),
            key=lambda row: (-float(cast(float, row.score)), row.symbol),
        )
        selected = ranked[:TOP_N]
        selected_names = {row.symbol for row in selected}
        observation_rank = {row.symbol: index for index, row in enumerate(selected, start=1)}
        enriched_rows = tuple(
            OvernightSignal(
                symbol=row.symbol,
                decision_date=row.decision_date,
                prior_date=row.prior_date,
                prior_close=row.prior_close,
                current_open=row.current_open,
                stock_overnight=row.stock_overnight,
                spy_overnight=row.spy_overnight,
                spy_excess=row.spy_excess,
                score=row.score,
                eligibility=row.eligibility,
                reason=row.reason,
                eligible_pool_rank=row.eligible_pool_rank,
                observation_rank=observation_rank.get(row.symbol),
                selected=row.symbol in selected_names,
                selected_reason=(
                    "top_n6"
                    if row.symbol in selected_names
                    else ("not_selected_rank" if row.score is not None else row.reason)
                ),
            )
            for row in rows
        )
        weights = tuple(
            TargetWeight(
                symbol=row.symbol,
                weight=FIXED_PER_NAME_WEIGHT,
                reason="overnight_reversal",
                rank=index,
            )
            for index, row in enumerate(selected, start=1)
        )
        target = TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - len(weights) * FIXED_PER_NAME_WEIGHT,
            rebalance_reason=RebalanceReason.SCHEDULED if weights else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": tuple(row.symbol for row in selected),
                "score_definition": "score = -(Open_i,d / Close_i,p(d) - 1 - (Open_SPY,d / Close_SPY,p(d) - 1))",
                "signal_records": [row.to_dict() for row in enriched_rows],
                "fixed_per_name_weight": FIXED_PER_NAME_WEIGHT,
                "unused_cash_weight": 1.0 - len(weights) * FIXED_PER_NAME_WEIGHT,
            },
        )
        self.recorded_targets.append(target)
        return target


__all__ = [
    "BENCHMARK",
    "CANDIDATE_NAME",
    "CANDIDATE_VERSION",
    "DailySessionRebalancePolicy",
    "FIXED_PER_NAME_WEIGHT",
    "FROZEN_PARAMETERS",
    "OvernightReversalStrategy",
    "OvernightSignal",
    "POSITION_CAP",
    "TOP_N",
    "score_context",
    "signal_from_bars",
]
