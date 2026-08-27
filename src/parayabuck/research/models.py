"""Research runner data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from parayabuck.core.models import Market


class ResearchTier(StrEnum):
    SMOKE = "smoke"
    QUICK = "quick"
    ROBUSTNESS = "robustness"
    CANONICAL = "canonical"


@dataclass(frozen=True)
class ResearchTrial:
    """One independent research trial.

    Parameters are strategy-level overrides; cost_multiplier scales slippage/spread/fees.
    Label is human readable; id is stable index for deterministic ordering.
    """

    label: str
    parameters: dict[str, object] | None = None
    cost_multiplier: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)
    execution_overrides: dict[str, object] | None = None


@dataclass(frozen=True)
class ResearchJob:
    """Batch of trials sharing dataset/window/market/strategy identity."""

    market: Market = Market.EQUITY
    strategy_name: str | None = None
    dataset_path: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    base_parameters: dict[str, object] | None = None
    trials: tuple[ResearchTrial, ...] = ()
    tier: ResearchTier = ResearchTier.QUICK
    workers: int | str = 1
    # Tri-state: None = tier default, True = force on, False = explicit opt-out
    persist_artifacts: bool | None = None
    persist_db: bool | None = None
    output_dir: str | None = None
    fail_fast: bool = False
    corporate_actions_path: str | None = None
    initial_cash: float | None = None
    config_path: str | None = None


@dataclass(frozen=True)
class TrialResult:
    trial_index: int
    label: str
    status: str
    parameters: dict[str, object]
    cost_multiplier: float
    elapsed_s: float
    total_return: float | None = None
    cagr: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    calmar: float | None = None
    turnover: float | None = None
    costs: float | None = None
    fee_total: float | None = None
    trade_count: int | None = None
    fill_count: int | None = None
    rebalance_count: int | None = None
    final_equity: float | None = None
    error: str | None = None
    summary: dict[str, Any] | None = None
    metadata: dict[str, object] | None = None
    worker_pid: int | None = None
    worker_peak_rss_mb: float | None = None


@dataclass(frozen=True)
class ResearchBatchResult:
    job: ResearchJob
    results: tuple[TrialResult, ...]
    elapsed_s: float
    tier: ResearchTier
    workers: int
    prepared_reused: bool
    universe_reused: bool
