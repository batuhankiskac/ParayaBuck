"""Research-only SPY defensive overlay around unchanged production V3.

This module is deliberately absent from the product strategy registry.  It
owns only the frozen regime signal and the A/B/C target composition described
in ``FROZEN_PROTOCOL.md``; V3 scoring, ranking, and sizing are delegated to the
production ``EquityResidualMomentum1dV3`` instance unchanged.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

FROZEN_NORMAL_V3_ALLOCATION = 0.55
FROZEN_DEFENSIVE_V3_ALLOCATION = 0.45
FROZEN_SH_OVERLAY_ALLOCATION = 0.10
FROZEN_MAX_POSITIONS = 6
FROZEN_MAX_SINGLE_WEIGHT = 0.10
FROZEN_SPY_LOOKBACK_SESSIONS = 252
FROZEN_SPY_OBSERVATIONS = FROZEN_SPY_LOOKBACK_SESSIONS + 1


class OverlayArm(StrEnum):
    INCUMBENT = "A_incumbent"
    DEFENSIVE_CASH = "B_defensive_cash"
    DEFENSIVE_SH = "C_defensive_sh"


class SpyRegime(StrEnum):
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"


def spy_trend_return(context: PortfolioContext) -> float:
    """Return the frozen close[t] / close[t-252] - 1 signal.

    Only completed observations at or before the decision timestamp are
    considered.  The latest SPY observation must be the decision bar itself;
    otherwise the research decision fails closed instead of silently using a
    stale regime.
    """

    spy = canonical_symbol("SPY", Market.EQUITY)
    completed = tuple(bar for bar in context.history.get(spy, ()) if bar.timestamp <= context.decision_timestamp)
    if len(completed) < FROZEN_SPY_OBSERVATIONS:
        raise ValueError("SPY defensive overlay requires exactly 253 trailing completed observations")
    if completed[-1].timestamp != context.decision_timestamp:
        raise ValueError("SPY defensive overlay requires a completed SPY bar at the decision timestamp")
    start = float(completed[-FROZEN_SPY_OBSERVATIONS].close)
    end = float(completed[-1].close)
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end <= 0:
        raise ValueError("SPY defensive overlay requires finite positive closing prices")
    return end / start - 1.0


def regime_for_trend(trend_return: float) -> SpyRegime:
    if not math.isfinite(trend_return):
        raise ValueError("SPY trend return must be finite")
    return SpyRegime.NORMAL if trend_return > 0 else SpyRegime.DEFENSIVE


def compose_overlay_target(
    v3_target: TargetPortfolio,
    *,
    arm: OverlayArm,
    regime: SpyRegime,
    trend_return: float,
) -> TargetPortfolio:
    """Compose one frozen arm after the unchanged V3 decision.

    Position-count and gross/cash enforcement remain exclusively owned by the
    production risk overlay. Treatment C only gives SH first rank so that the
    subsequent six-slot enforcement retains SH plus at most five V3 names.
    """

    defensive = regime == SpyRegime.DEFENSIVE
    intended_v3 = (
        FROZEN_DEFENSIVE_V3_ALLOCATION if defensive and arm != OverlayArm.INCUMBENT else FROZEN_NORMAL_V3_ALLOCATION
    )
    scale = (
        FROZEN_DEFENSIVE_V3_ALLOCATION / FROZEN_NORMAL_V3_ALLOCATION
        if defensive and arm != OverlayArm.INCUMBENT
        else 1.0
    )
    scaled_v3 = tuple(replace(weight, weight=weight.weight * scale) for weight in v3_target.weights)
    v3_by_symbol = {weight.symbol: weight for weight in scaled_v3}
    source_sh = v3_by_symbol.get("SH")
    v3_owned_sh = source_sh.weight if source_sh is not None else 0.0
    intended_overlay_sh = FROZEN_SH_OVERLAY_ALLOCATION if defensive and arm == OverlayArm.DEFENSIVE_SH else 0.0
    overlay_owned_sh = min(intended_overlay_sh, max(0.0, FROZEN_MAX_SINGLE_WEIGHT - v3_owned_sh))

    final_by_symbol: dict[str, TargetWeight] = {}
    treatment = defensive and arm == OverlayArm.DEFENSIVE_SH
    for symbol, weight in v3_by_symbol.items():
        if symbol == "SH" and treatment:
            continue
        final_by_symbol[symbol] = replace(
            weight,
            rank=weight.rank + 1 if treatment and weight.rank > 0 else weight.rank,
        )
    if treatment:
        final_by_symbol["SH"] = TargetWeight(
            "SH",
            v3_owned_sh + overlay_owned_sh,
            reason="v3_plus_spy_defensive_overlay" if source_sh is not None else "spy_defensive_overlay",
            rank=1,
        )
    final_weights = tuple(sorted(final_by_symbol.values(), key=lambda weight: weight.symbol))
    realized_v3 = sum(weight.weight for weight in scaled_v3)
    total_exposure = sum(weight.weight for weight in final_weights)
    final_sh = final_by_symbol.get("SH")

    loss_reasons: list[str] = []
    if overlay_owned_sh < intended_overlay_sh - 1e-12:
        loss_reasons.append("sh_overlap_per_asset_cap")

    source_weights = [
        {"symbol": weight.symbol, "weight": weight.weight, "rank": weight.rank}
        for weight in sorted(v3_target.weights, key=lambda item: (item.rank or math.inf, item.symbol))
        if weight.weight > 0
    ]
    diagnostics = {
        "arm": arm.value,
        "regime": regime.value,
        "trend_return": trend_return,
        "signal_observations": FROZEN_SPY_OBSERVATIONS,
        "intended_v3_exposure": intended_v3,
        "pre_overlay_v3_exposure": realized_v3,
        "intended_overlay_sh_exposure": intended_overlay_sh,
        "realized_overlay_sh_exposure": overlay_owned_sh,
        "v3_owned_sh": v3_owned_sh,
        "total_sh_exposure": final_sh.weight if final_sh is not None else 0.0,
        "v3_name_count": len(scaled_v3),
        "non_sh_v3_name_count": sum(weight.symbol != "SH" for weight in scaled_v3),
        "total_positions": len(final_weights),
        "pre_overlay_total_exposure": total_exposure,
        "pre_overlay_cash_weight": max(0.0, 1.0 - total_exposure),
        "capacity_loss_reasons": loss_reasons,
        "dropped_v3_symbols": [],
        "v3_scale": scale,
        "source_v3_weights": source_weights,
        "production_maximum_positions": FROZEN_MAX_POSITIONS,
        "production_maximum_single_weight": FROZEN_MAX_SINGLE_WEIGHT,
        "sh_slot_reserved": treatment,
        "maximum_non_sh_v3_positions_after_overlay": FROZEN_MAX_POSITIONS - 1 if treatment else FROZEN_MAX_POSITIONS,
        "no_unused_allocation_reallocated": True,
    }
    metadata = dict(v3_target.metadata)
    metadata["spy_defensive_overlay"] = diagnostics
    return replace(
        v3_target,
        strategy_name="equity_spy_defensive_overlay_v1",
        strategy_version="1.0.0-research",
        weights=final_weights,
        cash_weight=v3_target.cash_weight if scale == 1.0 and not treatment else max(0.0, 1.0 - total_exposure),
        rebalance_reason=RebalanceReason.SCHEDULED if final_weights else RebalanceReason.RISK_OFF,
        parameters=(("arm", arm.value), ("rebalance_frequency", "weekly")),
        metadata=metadata,
    )


class EquitySpyDefensiveOverlayV1(PortfolioStrategy):
    """Frozen research wrapper around an exact production V3 instance."""

    name = "equity_spy_defensive_overlay_v1"
    version = "1.0.0-research"
    market = Market.EQUITY
    timeframe = "1d"
    supported_timeframes = ("1d",)
    display_name = "SPY Defensive Overlay v1 (research)"
    rebalance_policy_declared = "weekly"

    def __init__(self, arm: OverlayArm | str, v3: EquityResidualMomentum1dV3) -> None:
        self.arm = OverlayArm(arm)
        self.v3 = v3
        self.decision_diagnostics: dict[datetime, dict[str, object]] = {}
        super().__init__({"arm": self.arm.value, "rebalance_frequency": "weekly"})

    @property
    def minimum_history_bars(self) -> int:
        return int(max(self.v3.minimum_history_bars, FROZEN_SPY_OBSERVATIONS))

    def _validate_parameters(self) -> None:
        if type(self.v3) is not EquityResidualMomentum1dV3:
            raise ValueError("SPY defensive overlay must wrap the unchanged production V3 class")
        if self.v3.name != "equity_residual_momentum_v3" or self.v3.version != "3.0.0":
            raise ValueError("SPY defensive overlay must wrap production V3 identity 3.0.0")
        if self.parameters.get("rebalance_frequency") != "weekly":
            raise ValueError("SPY defensive overlay cadence is frozen at weekly")

    def supports(self, context: PortfolioContext) -> bool:
        if not self.v3.supports(context):
            return False
        try:
            trend = spy_trend_return(context)
        except ValueError:
            return False
        if self.arm == OverlayArm.DEFENSIVE_SH and regime_for_trend(trend) == SpyRegime.DEFENSIVE:
            sh_bars = context.history.get("SH", ())
            return bool(sh_bars) and sh_bars[-1].timestamp == context.decision_timestamp
        return True

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        return tuple(self.v3.rank_candidates(context))

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        trend = spy_trend_return(context)
        regime = regime_for_trend(trend)
        target = compose_overlay_target(
            self.v3.decide(context),
            arm=self.arm,
            regime=regime,
            trend_return=trend,
        )
        diagnostics = target.metadata.get("spy_defensive_overlay")
        if not isinstance(diagnostics, dict):
            raise RuntimeError("SPY defensive overlay diagnostics are missing")
        self.decision_diagnostics[context.decision_timestamp] = dict(diagnostics)
        return target


__all__ = [
    "EquitySpyDefensiveOverlayV1",
    "OverlayArm",
    "SpyRegime",
    "compose_overlay_target",
    "regime_for_trend",
    "spy_trend_return",
]
