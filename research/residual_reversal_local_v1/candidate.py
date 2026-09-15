"""Research-only frozen residual-reversal strategy and shared treatment.

The candidate subclasses the production residual-momentum implementation so
beta, timestamp alignment, residual volatility, ivol rejection, inverse-vol
sizing, and target construction remain shared. Only the frozen 21-session
zero-skip score and the absence of the incumbent positive-score gate differ.
"""

from __future__ import annotations

import math
from dataclasses import replace

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum import (
    EquityResidualMomentum1dV2,
    _compounded_return,
)
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

FROZEN_CANDIDATE_NAME = "equity_short_term_residual_reversal_v1"
FROZEN_CANDIDATE_VERSION = "1.0.0-research"
FROZEN_FORMATION = 21
FROZEN_SKIP = 0
FROZEN_BETA_WINDOW = 273
FROZEN_RESIDUAL_VOL_WINDOW = 63
FROZEN_TOP_N = 6
FROZEN_MAX_SINGLE_WEIGHT = 0.10
FROZEN_IVOL_QUANTILE = 0.70


class EquityShortTermResidualReversalV1(EquityResidualMomentum1dV2):
    """Long-only worst recent market-residual performers, research only."""

    name = FROZEN_CANDIDATE_NAME
    version = FROZEN_CANDIDATE_VERSION
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Short-term Residual Reversal v1 (research)"
    rebalance_policy_declared = "weekly"

    def _validate_parameters(self) -> None:
        formation = self.param_int("formation_bars", FROZEN_FORMATION)
        skip = self.param_int("skip_bars", FROZEN_SKIP)
        beta_window = self.param_int("beta_window_bars", FROZEN_BETA_WINDOW)
        vol_window = self.param_int("residual_vol_window", FROZEN_RESIDUAL_VOL_WINDOW)
        top_n = self.param_int("top_n", FROZEN_TOP_N)
        cap = self.param_float("max_single_weight", FROZEN_MAX_SINGLE_WEIGHT)
        ivol_quantile = self.param_float("ivol_reject_quantile", FROZEN_IVOL_QUANTILE)
        frequency = self.parameters.get("rebalance_frequency", "weekly")
        if formation != FROZEN_FORMATION:
            raise ValueError("Frozen reversal formation must be exactly 21 completed sessions")
        if skip != FROZEN_SKIP:
            raise ValueError("Frozen reversal skip must be exactly zero")
        if beta_window != FROZEN_BETA_WINDOW:
            raise ValueError("Frozen reversal beta window must match V3 at 273")
        if vol_window != FROZEN_RESIDUAL_VOL_WINDOW:
            raise ValueError("Frozen reversal residual-vol window must match V3 at 63")
        if top_n != FROZEN_TOP_N:
            raise ValueError("Frozen reversal top_n must be exactly six")
        if cap != FROZEN_MAX_SINGLE_WEIGHT:
            raise ValueError("Frozen reversal per-position cap must match production at 0.10")
        if ivol_quantile != FROZEN_IVOL_QUANTILE:
            raise ValueError("Frozen reversal ivol quantile must match V3 at 0.70")
        if frequency != "weekly":
            raise ValueError("Frozen reversal cadence must be weekly")
        excluded = self.parameters.get("exclude_symbols", ["SPY"])
        if not isinstance(excluded, (list, tuple)) or not all(isinstance(item, str) for item in excluded):
            raise ValueError("exclude_symbols must be a list of symbols")

    def _candidate_score(self, residuals: list[float], *, formation: int, skip: int) -> float:
        """Use exactly the latest ``formation`` completed residual returns."""
        del skip
        if len(residuals) < formation:
            return float("-inf")
        return _compounded_return(residuals[-formation:])

    def _candidate_passes_gate(
        self,
        residuals: list[float],
        score: float,
        *,
        formation: int,
        skip: int,
    ) -> bool:
        """The frozen reversal pool has no incumbent positive-score gate."""
        del residuals, formation, skip
        return math.isfinite(score)

    def _score_candidates(self, context: PortfolioContext):
        """Reuse the production pipeline but rank the most-negative scores first."""
        ranked, residual_vols = super()._score_candidates(context)
        ranked = tuple(sorted(ranked, key=lambda item: (item[0], item[1])))
        return ranked, residual_vols

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        target = super().decide(context)
        weights = tuple(
            replace(weight, reason="residual_reversal")
            for weight in target.weights
        )
        metadata = dict(target.metadata)
        metadata.update(
            {
                "hypothesis": (
                    "21 completed-session market-residual reversal with zero skip; "
                    "more-negative residual return ranks stronger, long-only"
                ),
                "score_window_bars": FROZEN_FORMATION,
                "score_skip_bars": FROZEN_SKIP,
                "positive_score_gate": False,
            }
        )
        return replace(
            target,
            strategy_name=self.name,
            strategy_version=self.version,
            weights=weights,
            metadata=metadata,
            parameters=self.parameter_tuple(),
        )


def frozen_reversal_parameters() -> dict[str, object]:
    """Return the one permitted candidate parameter set."""
    return {
        "formation_bars": FROZEN_FORMATION,
        "skip_bars": FROZEN_SKIP,
        "beta_window_bars": FROZEN_BETA_WINDOW,
        "residual_vol_window": FROZEN_RESIDUAL_VOL_WINDOW,
        "top_n": FROZEN_TOP_N,
        "max_single_weight": FROZEN_MAX_SINGLE_WEIGHT,
        "ivol_reject_quantile": FROZEN_IVOL_QUANTILE,
        "exclude_symbols": ["SPY"],
        "rebalance_frequency": "weekly",
    }


def scale_sleeve_weights(
    target: TargetPortfolio,
    *,
    sleeve_fraction: float,
    reference_allocation: float = 0.55,
) -> dict[str, float]:
    """Scale one strategy target into its frozen share of the 0.55 sleeve.

    A thin/risk-off component is not renormalized upward. If the component's
    raw target exceeds its reference allocation, it is first capped at that
    allocation, matching the production allocation ceiling before the 75/25
    treatment split is applied.
    """
    if not 0.0 <= sleeve_fraction <= 1.0:
        raise ValueError("sleeve_fraction must be in [0, 1]")
    if not 0.0 < reference_allocation <= 1.0:
        raise ValueError("reference_allocation must be in (0, 1]")
    raw = {weight.symbol: weight.weight for weight in target.weights if weight.weight > 0}
    invested = sum(raw.values())
    cap_scale = min(1.0, reference_allocation / invested) if invested > 0 else 1.0
    return {symbol: weight * cap_scale * sleeve_fraction for symbol, weight in raw.items()}


def compose_shared_treatment_target(
    context: PortfolioContext,
    v3_target: TargetPortfolio,
    reversal_target: TargetPortfolio,
    *,
    v3_fraction: float = 0.75,
    reversal_fraction: float = 0.25,
    reference_allocation: float = 0.55,
) -> TargetPortfolio:
    """Merge sleeve targets into one account target before the risk overlay."""
    v3_weights = scale_sleeve_weights(
        v3_target,
        sleeve_fraction=v3_fraction,
        reference_allocation=reference_allocation,
    )
    reversal_weights = scale_sleeve_weights(
        reversal_target,
        sleeve_fraction=reversal_fraction,
        reference_allocation=reference_allocation,
    )
    merged: dict[str, float] = {}
    for symbol, weight in (*v3_weights.items(), *reversal_weights.items()):
        merged[symbol] = merged.get(symbol, 0.0) + weight
    targets = tuple(
        TargetWeight(symbol, weight, reason="v3_plus_residual_reversal", rank=0)
        for symbol, weight in sorted(merged.items())
        if weight > 0
    )
    invested = sum(weight.weight for weight in targets)
    if invested > 1.0 + 1e-9:
        raise ValueError(f"Shared treatment target exceeds account capital: {invested}")
    overlap = sorted(set(v3_weights).intersection(reversal_weights))
    return TargetPortfolio(
        strategy_name="equity_v3_plus_residual_reversal_treatment",
        strategy_version="1.0.0-research",
        decision_timestamp=context.decision_timestamp,
        data_timestamp=context.decision_timestamp,
        weights=targets,
        cash_weight=max(0.0, 1.0 - invested),
        rebalance_reason=RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF,
        universe_version=context.universe_version,
        universe_snapshot_id=context.universe_version,
        parameters=(
            ("v3_fraction", v3_fraction),
            ("reversal_fraction", reversal_fraction),
            ("reference_allocation", reference_allocation),
            ("rebalance_frequency", "weekly"),
        ),
        metadata={
            "composition_rule": "sum scaled V3/reversal targets, then shared existing risk overlay",
            "v3_fraction": v3_fraction,
            "reversal_fraction": reversal_fraction,
            "v3_scaled_invested": sum(v3_weights.values()),
            "reversal_scaled_invested": sum(reversal_weights.values()),
            "merged_invested": invested,
            "overlap_symbols": overlap,
            "unranked_for_shared_position_cap": True,
        },
    )


class EquityV3PlusResidualReversalTreatment(PortfolioStrategy):
    """Research-only composite with the frozen 75% / 25% capital split."""

    name = "equity_v3_plus_residual_reversal_treatment"
    version = "1.0.0-research"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "V3 plus residual reversal treatment (research)"
    rebalance_policy_declared = "weekly"

    def __init__(self, v3: PortfolioStrategy, reversal: EquityShortTermResidualReversalV1) -> None:
        self.v3 = v3
        self.reversal = reversal
        self.parameters = {
            "v3_fraction": 0.75,
            "reversal_fraction": 0.25,
            "reference_allocation": 0.55,
            "rebalance_frequency": "weekly",
        }
        self._validate_parameters()

    @property
    def minimum_history_bars(self) -> int:
        return max(self.v3.minimum_history_bars, self.reversal.minimum_history_bars)

    def _validate_parameters(self) -> None:
        if self.v3.name != "equity_residual_momentum_v3":
            raise ValueError("Treatment must use the current V3 control")
        if self.reversal.name != FROZEN_CANDIDATE_NAME:
            raise ValueError("Treatment must use the frozen residual reversal candidate")
        if self.parameters != {
            "v3_fraction": 0.75,
            "reversal_fraction": 0.25,
            "reference_allocation": 0.55,
            "rebalance_frequency": "weekly",
        }:
            raise ValueError("Treatment allocation is frozen at 75% / 25% of the 0.55 sleeve")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return compose_shared_treatment_target(context, self.v3.decide(context), self.reversal.decide(context))


__all__ = [
    "EquityShortTermResidualReversalV1",
    "EquityV3PlusResidualReversalTreatment",
    "FROZEN_CANDIDATE_NAME",
    "FROZEN_CANDIDATE_VERSION",
    "compose_shared_treatment_target",
    "frozen_reversal_parameters",
    "scale_sleeve_weights",
]
