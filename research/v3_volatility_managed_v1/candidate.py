"""Research-only volatility overlay for the production equity V3.

The production strategy and registry are deliberately untouched.  This module
inherits the current V3 alpha and uses only its post-sizing extension seam.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskOverlay, RiskOverlayDecision
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import TargetPortfolio

CURRENT_RETURN_COUNT = 21
BASELINE_OBSERVATION_COUNT = 252
ANNUALIZATION_DAYS = 252
V3_MAXIMUM_ALLOCATION = 0.55


@dataclass(frozen=True)
class ScalerObservation:
    """One point-in-time, weekly-consumed volatility-scaler observation."""

    decision_timestamp: datetime
    current_vol_timestamp: datetime | None
    vol21: float | None
    basevol: float | None
    scale: float
    status: str
    baseline_count: int

    @property
    def managed_allocation(self) -> float:
        return V3_MAXIMUM_ALLOCATION * self.scale

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_timestamp": self.decision_timestamp.isoformat(),
            "current_vol_timestamp": self.current_vol_timestamp.isoformat()
            if self.current_vol_timestamp is not None
            else None,
            "vol21": self.vol21,
            "basevol": self.basevol,
            "scale": self.scale,
            "managed_allocation": self.managed_allocation,
            "status": self.status,
            "baseline_count": self.baseline_count,
        }


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def daily_spy_volatility_observations(spy_bars: Sequence[Bar]) -> tuple[tuple[datetime, float], ...]:
    """Return daily completed-bar VOL21 observations from SPY closes.

    The function consumes only bars supplied by the caller.  The caller is
    responsible for supplying bars whose timestamps are no later than the
    decision watermark; the engine does that for strategy contexts.
    """

    ordered = sorted(
        (bar for bar in spy_bars if bar.market == Market.EQUITY and bar.close > 0),
        key=lambda bar: bar.timestamp,
    )
    timestamps: list[datetime] = []
    closes: list[float] = []
    for bar in ordered:
        if timestamps and bar.timestamp == timestamps[-1]:
            if bar.close != closes[-1]:
                raise ValueError(f"Conflicting SPY bars at {bar.timestamp.isoformat()}")
            continue
        timestamps.append(bar.timestamp)
        closes.append(float(bar.close))
    observations: list[tuple[datetime, float]] = []
    for index in range(CURRENT_RETURN_COUNT, len(closes)):
        returns = [
            math.log(closes[offset] / closes[offset - 1])
            for offset in range(index - CURRENT_RETURN_COUNT + 1, index + 1)
            if closes[offset] > 0 and closes[offset - 1] > 0
        ]
        if len(returns) != CURRENT_RETURN_COUNT:
            continue
        vol = _sample_std(returns) * math.sqrt(ANNUALIZATION_DAYS)
        if math.isfinite(vol):
            observations.append((timestamps[index], vol))
    return tuple(observations)


def scaler_for_decision(
    decision_timestamp: datetime,
    volatility_observations: Sequence[tuple[datetime, float]],
) -> ScalerObservation:
    """Apply the frozen prior-252-observation median rule."""

    timestamps = [timestamp for timestamp, _vol in volatility_observations]
    current_index = bisect.bisect_right(timestamps, decision_timestamp) - 1
    if current_index < 0:
        return ScalerObservation(
            decision_timestamp,
            None,
            None,
            None,
            1.0,
            "insufficient_history_normal_v3",
            0,
        )
    current_timestamp, current_vol = volatility_observations[current_index]
    prior = volatility_observations[max(0, current_index - BASELINE_OBSERVATION_COUNT) : current_index]
    if len(prior) < BASELINE_OBSERVATION_COUNT or not math.isfinite(current_vol) or current_vol <= 0:
        return ScalerObservation(
            decision_timestamp,
            current_timestamp,
            float(current_vol) if math.isfinite(current_vol) else None,
            None,
            1.0,
            "insufficient_history_normal_v3" if len(prior) < BASELINE_OBSERVATION_COUNT else "nonpositive_current_vol_normal_v3",
            len(prior),
        )
    basevol = statistics.median(vol for _timestamp, vol in prior)
    if not math.isfinite(basevol) or basevol <= 0:
        return ScalerObservation(
            decision_timestamp,
            current_timestamp,
            float(current_vol),
            None,
            1.0,
            "invalid_baseline_normal_v3",
            len(prior),
        )
    scale = min(1.0, basevol / current_vol)
    if not math.isfinite(scale) or scale < 0:
        return ScalerObservation(
            decision_timestamp,
            current_timestamp,
            float(current_vol),
            float(basevol),
            1.0,
            "invalid_scale_normal_v3",
            len(prior),
        )
    return ScalerObservation(
        decision_timestamp,
        current_timestamp,
        float(current_vol),
        float(basevol),
        min(1.0, float(scale)),
        "scaled" if scale < 1.0 else "normal_or_low_vol",
        len(prior),
    )


def scaler_path_for_decisions(
    decision_timestamps: Sequence[datetime], spy_bars: Sequence[Bar]
) -> tuple[ScalerObservation, ...]:
    observations = daily_spy_volatility_observations(spy_bars)
    return tuple(scaler_for_decision(timestamp, observations) for timestamp in decision_timestamps)


class VolatilityManagedEquityV3(EquityResidualMomentum1dV3):
    """Current V3 plus the frozen post-sizing SPY volatility scaler."""

    name = "equity_v3_volatility_managed_v1"
    version = "1.0.0-research"
    display_name = "Research V3 volatility-managed"

    def __init__(
        self,
        parameters: dict[str, object] | None = None,
        *,
        scaler_by_decision: Mapping[datetime, ScalerObservation] | None = None,
    ) -> None:
        self._scaler_by_decision = dict(scaler_by_decision or {})
        self._audit_rows: list[dict[str, object]] = []
        self._active_overlay: dict[str, object] | None = None
        super().__init__(parameters)

    @property
    def audit_rows(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(row) for row in self._audit_rows)

    def scaler_for_context(self, context: PortfolioContext) -> ScalerObservation:
        cached = self._scaler_by_decision.get(context.decision_timestamp)
        if cached is not None:
            return cached
        benchmark = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        return scaler_for_decision(
            context.decision_timestamp,
            daily_spy_volatility_observations(context.history.get(benchmark, ())),
        )

    def _post_target_weights(self, weights: dict[str, float], context: PortfolioContext) -> dict[str, float]:
        observation = self.scaler_for_context(context)
        self._active_overlay = {
            "pre_scale_target_weights": {symbol: float(weight) for symbol, weight in sorted(weights.items())},
            "pre_scale_target_exposure": sum(weights.values()),
            "observation": observation,
        }
        return {symbol: float(weight) * observation.scale for symbol, weight in weights.items()}

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        observation = self.scaler_for_context(context)
        self._active_overlay = None
        target = super().decide(context)
        overlay = self._active_overlay or {
            "pre_scale_target_weights": {},
            "pre_scale_target_exposure": 0.0,
        }
        pre_scale = dict(overlay["pre_scale_target_weights"])
        post_scale = {weight.symbol: float(weight.weight) for weight in target.weights}
        metadata = {
            **target.metadata,
            "volatility_managed": True,
            "volatility_scaler": observation.to_dict(),
            "managed_allocation": observation.managed_allocation,
            "pre_scale_target_weights": pre_scale,
            "post_scale_target_weights": post_scale,
            "pre_scale_target_exposure": float(overlay["pre_scale_target_exposure"]),
            "post_scale_target_exposure": sum(post_scale.values()),
            "cash_target": target.cash_weight,
        }
        enriched = replace(target, metadata=metadata)
        explanations = metadata.get("explanations", {})
        selected_symbols = metadata.get("selected_symbols", ())
        ranks = {
            symbol: int(details["alpha_rank"])
            for symbol, details in explanations.items()
            if isinstance(details, Mapping) and "alpha_rank" in details
        }
        self._audit_rows.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "scaler": observation.to_dict(),
                "selected_symbols": list(selected_symbols) if isinstance(selected_symbols, (list, tuple)) else [],
                "alpha_ranks": ranks,
                "pre_scale_target_weights": pre_scale,
                "post_scale_target_weights": post_scale,
                "pre_scale_target_exposure": float(overlay["pre_scale_target_exposure"]),
                "post_scale_target_exposure": sum(post_scale.values()),
                "cash_target": target.cash_weight,
            }
        )
        return enriched


class AuditedControlV3(EquityResidualMomentum1dV3):
    """Research-only control wrapper that records pre-overlay decisions."""

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self._audit_rows: list[dict[str, object]] = []
        super().__init__(parameters)

    @property
    def audit_rows(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(row) for row in self._audit_rows)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        target = super().decide(context)
        metadata = target.metadata
        explanations = metadata.get("explanations", {})
        ranks = {
            symbol: int(details["alpha_rank"])
            for symbol, details in explanations.items()
            if isinstance(details, Mapping) and "alpha_rank" in details
        }
        self._audit_rows.append(
            {
                "decision_timestamp": context.decision_timestamp.isoformat(),
                "selected_symbols": list(metadata.get("selected_symbols", ())),
                "alpha_ranks": ranks,
                "pre_scale_target_weights": {
                    weight.symbol: float(weight.weight) for weight in target.weights
                },
                "pre_scale_target_exposure": target.invested_weight,
                "cash_target": target.cash_weight,
            }
        )
        return target


class DynamicAllocationRiskOverlay(PortfolioRiskOverlay):
    """Use the candidate's frozen allocation as the only changed risk input."""

    def apply(self, target: TargetPortfolio, account: Any, **kwargs: Any) -> RiskOverlayDecision:
        allocation = target.metadata.get("managed_allocation")
        if target.metadata.get("volatility_managed") and isinstance(allocation, (int, float)):
            if not math.isfinite(float(allocation)) or float(allocation) < 0:
                raise ValueError("volatility-managed allocation must be finite and non-negative")
            kwargs["maximum_allocation"] = float(allocation)
        return super().apply(target, account, **kwargs)


class CostStressPortfolioBacktestEngine(PortfolioBacktestEngine):
    """Research-only engine applying cost multipliers to all equity costs."""

    def __init__(self, *args: Any, cost_multiplier: float = 1.0, **kwargs: Any) -> None:
        if not math.isfinite(cost_multiplier) or cost_multiplier <= 0:
            raise ValueError("cost_multiplier must be positive")
        self.cost_multiplier = float(cost_multiplier)
        if "slippage_bps" in kwargs:
            kwargs["slippage_bps"] = float(kwargs["slippage_bps"]) * self.cost_multiplier
        if "spread_bps" in kwargs:
            kwargs["spread_bps"] = float(kwargs["spread_bps"]) * self.cost_multiplier
        super().__init__(*args, **kwargs)

    def _equity_fee(self, side: Any, quantity: float, price: float, trade_date: Any) -> float:
        return super()._equity_fee(side, quantity, price, trade_date) * self.cost_multiplier


__all__ = [
    "ANNUALIZATION_DAYS",
    "AuditedControlV3",
    "BASELINE_OBSERVATION_COUNT",
    "CURRENT_RETURN_COUNT",
    "CostStressPortfolioBacktestEngine",
    "DynamicAllocationRiskOverlay",
    "ScalerObservation",
    "V3_MAXIMUM_ALLOCATION",
    "VolatilityManagedEquityV3",
    "daily_spy_volatility_observations",
    "scaler_for_decision",
    "scaler_path_for_decisions",
]
