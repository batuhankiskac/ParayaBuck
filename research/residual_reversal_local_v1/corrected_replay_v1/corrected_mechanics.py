"""Research-only mechanics for the frozen corrected replay.

This module owns the two corrections that cannot be expressed by changing a
parameter: the strategy-specific observation stream and the treatment's
sleeve-aware position selection.  It also provides a research-only engine
subclass that wires the already validated lifetime metadata into the existing
historical event loop and records structural decision coverage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

from ..candidate import EquityShortTermResidualReversalV1

FROZEN_REFERENCE_ALLOCATION = 0.55
FROZEN_V3_FRACTION = 0.75
FROZEN_REVERSAL_FRACTION = 0.25
FROZEN_V3_ALLOCATION = FROZEN_REFERENCE_ALLOCATION * FROZEN_V3_FRACTION
FROZEN_REVERSAL_ALLOCATION = FROZEN_REFERENCE_ALLOCATION * FROZEN_REVERSAL_FRACTION
FROZEN_MAX_POSITIONS = 6
FROZEN_MAX_SINGLE_WEIGHT = 0.10
TREATMENT_OBSERVATION_NAME = "treatment_combined_observation_v1"
TREATMENT_OBSERVATION_VERSION = "1.0.0-corrected-replay"


def _positive_target_weights(target: TargetPortfolio) -> tuple[TargetWeight, ...]:
    return tuple(weight for weight in target.weights if weight.weight > 0.0)


def _scaled_entries(
    target: TargetPortfolio,
    *,
    sleeve: str,
    sleeve_fraction: float,
    reference_allocation: float,
) -> tuple[dict[str, Any], ...]:
    """Return source-preserving component contributions after sleeve scaling."""
    weights = _positive_target_weights(target)
    invested = sum(weight.weight for weight in weights)
    cap_scale = min(1.0, reference_allocation / invested) if invested > 0 else 1.0
    entries: list[dict[str, Any]] = []
    for fallback_rank, weight in enumerate(weights, start=1):
        symbol = canonical_symbol(weight.symbol, Market.EQUITY)
        entries.append(
            {
                "symbol": symbol,
                "sleeve": sleeve,
                "raw_weight": float(weight.weight),
                "weight": float(weight.weight) * cap_scale * sleeve_fraction,
                "rank": int(weight.rank) if weight.rank > 0 else fallback_rank,
                "source_rank": int(weight.rank),
            }
        )
    return tuple(entries)


def _rank_order(entries: tuple[dict[str, Any], ...], sleeve: str) -> tuple[str, ...]:
    best: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry["sleeve"] != sleeve:
            continue
        symbol = str(entry["symbol"])
        current = best.get(symbol)
        if current is None or (entry["rank"], symbol) < (current["rank"], symbol):
            best[symbol] = entry
    return tuple(
        symbol
        for symbol, _entry in sorted(best.items(), key=lambda item: (item[1]["rank"], item[0]))
    )


def _apportioned_slot_quotas(
    v3_symbols: tuple[str, ...],
    reversal_symbols: tuple[str, ...],
    *,
    capacity: int,
    v3_fraction: float,
    reversal_fraction: float,
) -> tuple[int, int]:
    """Largest-remainder six-slot quotas with a declared minority tie-break."""
    if capacity <= 0:
        return 0, 0
    if not v3_symbols:
        return 0, min(capacity, len(reversal_symbols))
    if not reversal_symbols:
        return min(capacity, len(v3_symbols)), 0
    raw_v3 = capacity * v3_fraction / (v3_fraction + reversal_fraction)
    raw_reversal = capacity * reversal_fraction / (v3_fraction + reversal_fraction)
    v3_quota = int(raw_v3 // 1)
    reversal_quota = int(raw_reversal // 1)
    remaining = capacity - v3_quota - reversal_quota
    # The 4.5/1.5 tie for six slots is assigned to reversal so the smaller
    # sleeve is never silently represented by zero or one slot by rounding.
    if remaining:
        if raw_reversal - reversal_quota >= raw_v3 - v3_quota:
            reversal_quota += 1
        else:
            v3_quota += 1
    v3_quota = min(v3_quota, len(v3_symbols))
    reversal_quota = min(reversal_quota, len(reversal_symbols))
    spare = capacity - v3_quota - reversal_quota
    if spare > 0 and len(v3_symbols) > v3_quota:
        add = min(spare, len(v3_symbols) - v3_quota)
        v3_quota += add
        spare -= add
    if spare > 0 and len(reversal_symbols) > reversal_quota:
        reversal_quota += min(spare, len(reversal_symbols) - reversal_quota)
    return v3_quota, reversal_quota


def _source_map(entries: tuple[dict[str, Any], ...]) -> dict[str, dict[str, Any]]:
    source: dict[str, dict[str, Any]] = {}
    for entry in entries:
        symbol = str(entry["symbol"])
        row = source.setdefault(
            symbol,
            {
                "v3": 0.0,
                "reversal": 0.0,
                "v3_rank": None,
                "reversal_rank": None,
                "v3_source_rank": None,
                "reversal_source_rank": None,
            },
        )
        sleeve = str(entry["sleeve"])
        row[sleeve] += float(entry["weight"])
        row[f"{sleeve}_rank"] = min(
            int(entry["rank"]), row[f"{sleeve}_rank"] or int(entry["rank"])
        )
        row[f"{sleeve}_source_rank"] = min(
            int(entry["source_rank"]), row[f"{sleeve}_source_rank"] or int(entry["source_rank"])
        )
    return source


def compose_sleeve_aware_treatment_target(
    context: PortfolioContext,
    v3_target: TargetPortfolio,
    reversal_target: TargetPortfolio,
    *,
    v3_fraction: float = FROZEN_V3_FRACTION,
    reversal_fraction: float = FROZEN_REVERSAL_FRACTION,
    reference_allocation: float = FROZEN_REFERENCE_ALLOCATION,
    maximum_positions: int = FROZEN_MAX_POSITIONS,
    maximum_single_weight: float = FROZEN_MAX_SINGLE_WEIGHT,
) -> TargetPortfolio:
    """Compose the frozen sleeves without letting target weight choose slots.

    The returned target has at most ``maximum_positions`` symbols, so the
    unchanged production risk overlay remains the final risk authority while
    the predeclared sleeve policy decides which source contributions survive.
    """
    if (v3_fraction, reversal_fraction, reference_allocation) != (
        FROZEN_V3_FRACTION,
        FROZEN_REVERSAL_FRACTION,
        FROZEN_REFERENCE_ALLOCATION,
    ):
        raise ValueError("Corrected treatment allocation is frozen at 75% / 25% of the 0.55 sleeve")
    if maximum_positions != FROZEN_MAX_POSITIONS or maximum_single_weight != FROZEN_MAX_SINGLE_WEIGHT:
        raise ValueError("Corrected treatment must retain the frozen six-position and 0.10 caps")

    v3_entries = _scaled_entries(
        v3_target,
        sleeve="v3",
        sleeve_fraction=v3_fraction,
        reference_allocation=reference_allocation,
    )
    reversal_entries = _scaled_entries(
        reversal_target,
        sleeve="reversal",
        sleeve_fraction=reversal_fraction,
        reference_allocation=reference_allocation,
    )
    entries = (*v3_entries, *reversal_entries)
    source = _source_map(entries)
    scaled_v3_exposure = sum(float(entry["weight"]) for entry in v3_entries)
    scaled_reversal_exposure = sum(float(entry["weight"]) for entry in reversal_entries)

    # Enforce the total per-symbol limit before slot selection. The source
    # split is reduced proportionally and remains explicit in ``source``.
    per_symbol_reductions: dict[str, dict[str, float]] = {}
    for symbol, row in source.items():
        combined = float(row["v3"]) + float(row["reversal"])
        if combined <= maximum_single_weight + 1e-12:
            continue
        factor = maximum_single_weight / combined
        before = {sleeve: float(row[sleeve]) for sleeve in ("v3", "reversal")}
        for sleeve in ("v3", "reversal"):
            row[sleeve] = before[sleeve] * factor
        per_symbol_reductions[symbol] = {
            sleeve: before[sleeve] - float(row[sleeve]) for sleeve in ("v3", "reversal")
        }

    v3_symbols = _rank_order(v3_entries, "v3")
    reversal_symbols = _rank_order(reversal_entries, "reversal")
    v3_quota, reversal_quota = _apportioned_slot_quotas(
        v3_symbols,
        reversal_symbols,
        capacity=maximum_positions,
        v3_fraction=v3_fraction,
        reversal_fraction=reversal_fraction,
    )

    selected: set[str] = set()
    selected.update(v3_symbols[:v3_quota])
    selected.update(reversal_symbols[:reversal_quota])
    v3_index = v3_quota
    reversal_index = reversal_quota
    next_sleeve = "reversal"
    while len(selected) < maximum_positions:
        if next_sleeve == "reversal":
            candidates = reversal_symbols
            index = reversal_index
        else:
            candidates = v3_symbols
            index = v3_index
        while index < len(candidates) and candidates[index] in selected:
            index += 1
        if index >= len(candidates):
            other = "v3" if next_sleeve == "reversal" else "reversal"
            candidates = v3_symbols if other == "v3" else reversal_symbols
            index = v3_index if other == "v3" else reversal_index
            while index < len(candidates) and candidates[index] in selected:
                index += 1
            if index >= len(candidates):
                break
            next_sleeve = other
        symbol = candidates[index]
        selected.add(symbol)
        if next_sleeve == "v3":
            v3_index = index + 1
            next_sleeve = "reversal"
        else:
            reversal_index = index + 1
            next_sleeve = "v3"

    pre_position_cap_v3 = sum(float(row["v3"]) for row in source.values())
    pre_position_cap_reversal = sum(float(row["reversal"]) for row in source.values())
    post_position_cap_v3 = sum(float(source[symbol]["v3"]) for symbol in selected)
    post_position_cap_reversal = sum(float(source[symbol]["reversal"]) for symbol in selected)
    dropped = {
        symbol: "position_capacity_sleeve_aware"
        for symbol in sorted(source)
        if symbol not in selected
    }
    source_ranks = {
        symbol: {
            "v3_rank": row["v3_rank"],
            "reversal_rank": row["reversal_rank"],
            "v3_source_rank": row["v3_source_rank"],
            "reversal_source_rank": row["reversal_source_rank"],
        }
        for symbol, row in sorted(source.items())
    }
    contribution_map = {
        symbol: {
            "v3": float(row["v3"]),
            "reversal": float(row["reversal"]),
        }
        for symbol, row in sorted(source.items())
    }
    target_weights = tuple(
        TargetWeight(
            symbol,
            float(source[symbol]["v3"]) + float(source[symbol]["reversal"]),
            reason="sleeve_aware_treatment",
            rank=min(
                rank
                for rank in (source[symbol]["v3_rank"], source[symbol]["reversal_rank"])
                if rank is not None
            ),
        )
        for symbol in sorted(selected)
        if float(source[symbol]["v3"]) + float(source[symbol]["reversal"]) > 0.0
    )
    invested = sum(weight.weight for weight in target_weights)
    overlap = sorted(set(v3_symbols).intersection(reversal_symbols))
    zero_reversal_reason = None
    if post_position_cap_reversal <= 1e-12:
        zero_reversal_reason = "no_reversal_candidates" if not reversal_entries else "position_capacity_sleeve_aware"
    attribution = {
        "policy_version": "sleeve_aware_quota_round_robin_v1",
        "raw_v3_target_symbols": list(v3_symbols),
        "raw_reversal_target_symbols": list(reversal_symbols),
        "overlap_symbols": overlap,
        "pre_cap_merged_symbols": sorted(source),
        "post_cap_symbols": sorted(selected),
        "dropped_symbols": dropped,
        "dropped_symbol_reasons": dict(dropped),
        "slot_capacity": maximum_positions,
        "slot_quotas": {"v3": v3_quota, "reversal": reversal_quota},
        "frozen_intended_v3_allocation": FROZEN_V3_ALLOCATION,
        "frozen_intended_reversal_allocation": FROZEN_REVERSAL_ALLOCATION,
        "scaled_component_v3_exposure": scaled_v3_exposure,
        "scaled_component_reversal_exposure": scaled_reversal_exposure,
        "pre_position_cap_v3_exposure": pre_position_cap_v3,
        "pre_position_cap_reversal_exposure": pre_position_cap_reversal,
        "post_position_cap_v3_exposure": post_position_cap_v3,
        "post_position_cap_reversal_exposure": post_position_cap_reversal,
        "unused_v3_allocation": max(0.0, FROZEN_V3_ALLOCATION - post_position_cap_v3),
        "unused_reversal_allocation": max(0.0, FROZEN_REVERSAL_ALLOCATION - post_position_cap_reversal),
        "capacity_shortfall_v3": max(0.0, pre_position_cap_v3 - post_position_cap_v3),
        "capacity_shortfall_reversal": max(0.0, pre_position_cap_reversal - post_position_cap_reversal),
        "allocation_capacity_shortfall": (
            post_position_cap_v3 < pre_position_cap_v3 - 1e-12
            or post_position_cap_reversal < pre_position_cap_reversal - 1e-12
        ),
        "zero_reversal_exposure_reason": zero_reversal_reason,
        "per_symbol_cap_reductions": per_symbol_reductions,
        "source_contributions": contribution_map,
        "source_ranks": source_ranks,
        "no_cross_sleeve_reallocation": True,
        "composite_rank_policy": "minimum positive source rank; source ranks remain in metadata",
    }
    return TargetPortfolio(
        strategy_name="equity_v3_plus_residual_reversal_treatment",
        strategy_version="1.0.0-corrected-replay",
        decision_timestamp=context.decision_timestamp,
        data_timestamp=context.decision_timestamp,
        weights=target_weights,
        cash_weight=max(0.0, 1.0 - invested),
        rebalance_reason=RebalanceReason.SCHEDULED if target_weights else RebalanceReason.RISK_OFF,
        universe_version=context.universe_version,
        universe_snapshot_id=context.universe_version,
        parameters=(
            ("v3_fraction", v3_fraction),
            ("reversal_fraction", reversal_fraction),
            ("reference_allocation", reference_allocation),
            ("maximum_positions", maximum_positions),
            ("maximum_single_weight", maximum_single_weight),
            ("rebalance_frequency", "weekly"),
        ),
        metadata={
            "composition_rule": "sleeve-aware quotas then unchanged production risk overlay",
            "treatment_attribution": attribution,
        },
    )


class TreatmentObservationRanker(PortfolioStrategy):
    """Ranks one treatment stream from separate V3 and reversal alpha lists."""

    name = TREATMENT_OBSERVATION_NAME
    version = TREATMENT_OBSERVATION_VERSION
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Corrected combined V3/reversal observation ranker"
    rebalance_policy_declared = "weekly"

    def __init__(self, v3: EquityResidualMomentum1dV3, reversal: EquityShortTermResidualReversalV1) -> None:
        self.v3 = v3
        self.reversal = reversal
        super().__init__({"policy": "round_robin_v3_reversal"})

    @property
    def minimum_history_bars(self) -> int:
        return max(self.v3.minimum_history_bars, self.reversal.minimum_history_bars)

    def _validate_parameters(self) -> None:
        if self.v3.market != Market.EQUITY or self.reversal.market != Market.EQUITY:
            raise ValueError("Treatment observation ranker is equity-only")

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        v3_ranked = tuple(self.v3.rank_candidates(context))
        reversal_ranked = tuple(self.reversal.rank_candidates(context))
        combined: list[str] = []
        v3_index = 0
        reversal_index = 0
        while v3_index < len(v3_ranked) or reversal_index < len(reversal_ranked):
            if v3_index < len(v3_ranked):
                combined.append(v3_ranked[v3_index])
                v3_index += 1
            if reversal_index < len(reversal_ranked):
                combined.append(reversal_ranked[reversal_index])
                reversal_index += 1
        return tuple(dict.fromkeys(canonical_symbol(symbol, Market.EQUITY) for symbol in combined))

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        raise RuntimeError("TreatmentObservationRanker is only valid for PIT observation construction")


class CorrectedTreatmentStrategy(PortfolioStrategy):
    """One-account treatment strategy using the frozen sleeve policy."""

    name = "equity_v3_plus_residual_reversal_treatment"
    version = "1.0.0-corrected-replay"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Corrected V3 plus residual reversal treatment"
    rebalance_policy_declared = "weekly"

    def __init__(self, v3: EquityResidualMomentum1dV3, reversal: EquityShortTermResidualReversalV1) -> None:
        self.v3 = v3
        self.reversal = reversal
        super().__init__(
            {
                "v3_fraction": FROZEN_V3_FRACTION,
                "reversal_fraction": FROZEN_REVERSAL_FRACTION,
                "reference_allocation": FROZEN_REFERENCE_ALLOCATION,
                "maximum_positions": FROZEN_MAX_POSITIONS,
                "maximum_single_weight": FROZEN_MAX_SINGLE_WEIGHT,
                "rebalance_frequency": "weekly",
            }
        )

    @property
    def minimum_history_bars(self) -> int:
        return max(self.v3.minimum_history_bars, self.reversal.minimum_history_bars)

    def _validate_parameters(self) -> None:
        if self.v3.name != "equity_residual_momentum_v3":
            raise ValueError("Corrected treatment must use unchanged V3")
        if self.reversal.name != "equity_short_term_residual_reversal_v1":
            raise ValueError("Corrected treatment must use the frozen reversal candidate")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return compose_sleeve_aware_treatment_target(context, self.v3.decide(context), self.reversal.decide(context))


class CorrectedReplayEngine(PortfolioBacktestEngine):
    """Research-only lifecycle/coverage instrumentation around the engine."""

    def __init__(self, *args: Any, terminal_map: dict[str, dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.terminal_map = terminal_map
        self.coverage_attempts: list[dict[str, Any]] = []
        self.decision_attributions: list[dict[str, Any]] = []
        self._terminal_bars: dict[tuple[str, datetime], Bar] = {}

    def run(self, bars: list[Bar], warmup: list[Bar] | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
        self._terminal_bars = {
            (canonical_symbol(bar.symbol, bar.market), bar.timestamp): bar
            for bar in (*tuple(warmup or ()), *tuple(bars))
        }
        return super().run(bars, warmup=warmup, **kwargs)

    def _coverage_preflight(self, timestamp: datetime) -> dict[str, Any]:
        context = self._build_context(timestamp)
        if not self.strategy.supports(context):
            return {"eligible": False, "skip_reason": "strategy_not_ready", "missing_symbols": []}
        expected = self._effective_observation_symbols(timestamp)
        missing = []
        for symbol in expected or ():
            if (
                symbol not in context.history
                or not context.history[symbol]
                or context.history[symbol][-1].timestamp != timestamp
            ):
                missing.append(symbol)
        if missing:
            return {"eligible": True, "skip_reason": "incomplete_observation", "missing_symbols": missing}
        return {"eligible": True, "skip_reason": None, "missing_symbols": []}

    def _decide(self, timestamp: datetime) -> None:
        market = self.strategy.market
        previous = self._previous_timestamp[market]
        scheduled = self.rebalance_policy.is_period_start(previous, timestamp)
        preflight = self._coverage_preflight(timestamp) if scheduled else None
        before = len(self._rebalances)
        super()._decide(timestamp)
        recorded = len(self._rebalances) > before
        if scheduled and preflight is not None:
            self.coverage_attempts.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "scheduled": True,
                    "eligible": preflight["eligible"],
                    "decision_recorded": recorded,
                    "skip_reason": None if recorded else preflight["skip_reason"] or "engine_no_decision",
                    "missing_symbols": list(preflight["missing_symbols"]),
                }
            )
        self._close_terminal_positions(timestamp)

    def _close_terminal_positions(self, timestamp: datetime) -> None:
        terminal_symbols = {
            symbol for symbol, terminal in self.terminal_map.items() if terminal.get("last_bar") == timestamp
        }
        if terminal_symbols:
            self._pending = [order for order in self._pending if order.symbol not in terminal_symbols]
        for position in list(self._positions.values()):
            terminal = self.terminal_map.get(position.symbol)
            if not terminal or terminal.get("last_bar") != timestamp:
                continue
            bar = self._terminal_bars.get((position.symbol, timestamp))
            if bar is None:
                raise RuntimeError(f"Terminal bar missing from corrected replay stream: {position.symbol} {timestamp.isoformat()}")
            self._close_position(position, bar)
            event = self._liquidation_events[-1]
            event["policy"] = "TERMINAL_BAR"
            event["effective_policy"] = "TERMINAL_BAR"
            event["terminal_event_class"] = terminal.get("terminal_event_class", "UNKNOWN")

    def _record_rebalance(self, target: TargetPortfolio) -> None:
        attribution = target.metadata.get("treatment_attribution")
        if isinstance(attribution, dict):
            row = dict(attribution)
            post_weights = {weight.symbol: float(weight.weight) for weight in target.weights}
            realized_v3 = 0.0
            realized_reversal = 0.0
            for symbol, contribution in dict(row.get("source_contributions", {})).items():
                source_total = float(contribution.get("v3", 0.0)) + float(contribution.get("reversal", 0.0))
                after = post_weights.get(symbol, 0.0)
                factor = after / source_total if source_total > 0 else 0.0
                realized_v3 += float(contribution.get("v3", 0.0)) * factor
                realized_reversal += float(contribution.get("reversal", 0.0)) * factor
            row.update(
                {
                    "decision_timestamp": target.decision_timestamp.isoformat(),
                    "post_overlay_target_weights": sorted(post_weights.items()),
                    "post_overlay_v3_exposure": realized_v3,
                    "post_overlay_reversal_exposure": realized_reversal,
                    "post_overlay_unused_v3_allocation": max(0.0, FROZEN_V3_ALLOCATION - realized_v3),
                    "post_overlay_unused_reversal_allocation": max(0.0, FROZEN_REVERSAL_ALLOCATION - realized_reversal),
                    "risk_overlay_reasons": list(target.metadata.get("risk_overlay_reasons", ())),
                }
            )
            self.decision_attributions.append(row)
        super()._record_rebalance(target)


__all__ = [
    "CorrectedReplayEngine",
    "CorrectedTreatmentStrategy",
    "FROZEN_MAX_POSITIONS",
    "FROZEN_MAX_SINGLE_WEIGHT",
    "FROZEN_REFERENCE_ALLOCATION",
    "FROZEN_REVERSAL_ALLOCATION",
    "FROZEN_V3_ALLOCATION",
    "TreatmentObservationRanker",
    "TREATMENT_OBSERVATION_NAME",
    "TREATMENT_OBSERVATION_VERSION",
    "compose_sleeve_aware_treatment_target",
]
