from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class RolloutStage(StrEnum):
    RESEARCH = "research"
    SHADOW = "shadow"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    HALTED = "halted"


@dataclass(frozen=True)
class RolloutPolicy:
    minimum_shadow_days: int = 30
    minimum_paper_days: int = 90
    minimum_paper_fills: int = 50
    maximum_slippage_ratio: float = 1.5
    initial_canary_risk_pct: float = 0.001
    normal_risk_pct: float = 0.0025
    minimum_canary_trades: int = 30
    minimum_rolling_profit_factor: float = 0.90
    maximum_drawdown_multiple: float = 1.5


@dataclass(frozen=True)
class RolloutState:
    stage: RolloutStage
    stage_started: date
    as_of: date
    fills: int = 0
    trades: int = 0
    operational_errors: int = 0
    expected_slippage_bps: float = 0.0
    realized_slippage_bps: float = 0.0
    rolling_50_profit_factor: float = 0.0
    realized_drawdown: float = 0.0
    validated_drawdown: float = 0.0

    @property
    def elapsed_days(self) -> int:
        return max(0, (self.as_of - self.stage_started).days)


@dataclass(frozen=True)
class RolloutDecision:
    eligible_for_next_stage: bool
    halt_new_entries: bool
    risk_per_trade_pct: float
    reasons: tuple[str, ...]


def evaluate_rollout(
    state: RolloutState, policy: RolloutPolicy = RolloutPolicy()
) -> RolloutDecision:
    reasons: list[str] = []
    cost_deviation = (
        state.expected_slippage_bps > 0
        and state.realized_slippage_bps
        > state.expected_slippage_bps * policy.maximum_slippage_ratio
    )
    drawdown_breach = (
        state.validated_drawdown > 0
        and state.realized_drawdown
        > state.validated_drawdown * policy.maximum_drawdown_multiple
    )
    rolling_breach = (
        state.trades >= 50
        and state.rolling_50_profit_factor < policy.minimum_rolling_profit_factor
    )
    if cost_deviation:
        reasons.append("realized_slippage_above_limit")
    if drawdown_breach:
        reasons.append("drawdown_above_validated_multiple")
    if rolling_breach:
        reasons.append("rolling_50_profit_factor_below_limit")
    halt = bool(reasons) or state.stage == RolloutStage.HALTED

    eligible = False
    if state.stage == RolloutStage.SHADOW:
        eligible = state.elapsed_days >= policy.minimum_shadow_days and not halt
    elif state.stage == RolloutStage.PAPER:
        eligible = (
            state.elapsed_days >= policy.minimum_paper_days
            and state.fills >= policy.minimum_paper_fills
            and not halt
        )
    elif state.stage == RolloutStage.CANARY:
        eligible = (
            state.trades >= policy.minimum_canary_trades
            and state.operational_errors == 0
            and not halt
        )
    risk = (
        policy.initial_canary_risk_pct
        if state.stage == RolloutStage.CANARY
        and state.trades < policy.minimum_canary_trades
        else policy.normal_risk_pct
    )
    return RolloutDecision(eligible, halt, risk, tuple(reasons))


__all__ = [
    "RolloutDecision",
    "RolloutPolicy",
    "RolloutStage",
    "RolloutState",
    "evaluate_rollout",
]
