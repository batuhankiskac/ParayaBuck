from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol

WEIGHT_TOLERANCE = 1e-6
MAX_WEIGHT = 1.0


def _canonical_key(symbol: str) -> str:
    """Best-effort canonical symbol key (unparseable symbols keep their raw form)."""
    try:
        return canonical_symbol(symbol)
    except ValueError:
        return symbol


def _float_mapping(raw: dict[str, Any], key: str) -> dict[str, float]:
    return {str(symbol): float(value) for symbol, value in dict(raw.get(key, {})).items()}


class RebalanceReason(StrEnum):
    INITIAL = "initial"
    SCHEDULED = "scheduled"
    RISK_OFF = "risk_off"
    MAX_VOLATILITY = "max_volatility"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class TargetWeight:
    """One symbol's target portfolio weight in [0, 1]."""

    symbol: str
    weight: float
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Target weight symbol cannot be empty")
        if not math.isfinite(self.weight) or not 0.0 <= self.weight <= MAX_WEIGHT:
            raise ValueError(f"Target weight must be in [0, 1]: {self.weight}")


@dataclass(frozen=True)
class TargetPortfolio:
    """Decision output of a portfolio strategy for one rebalance.

    Weights are the desired allocation at ``decision_timestamp``; the engine
    converts them into orders that only become effective on the next tradable
    bar. The sum of symbol weights plus ``cash_weight`` must not exceed 1 and
    every weight must be non-negative (long-only, no leverage).
    """

    strategy_name: str
    strategy_version: str
    decision_timestamp: datetime
    weights: tuple[TargetWeight, ...]
    cash_weight: float
    rebalance_reason: RebalanceReason = RebalanceReason.SCHEDULED
    data_timestamp: datetime | None = None
    universe_version: str = ""
    universe_snapshot_id: str = ""
    parameters: tuple[tuple[str, object], ...] = ()
    risk_multiplier: float = 1.0
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_name.strip() or not self.strategy_version.strip():
            raise ValueError("Strategy identity is required")
        if self.decision_timestamp.tzinfo is None:
            raise ValueError("Decision timestamps must be timezone-aware")
        if not math.isfinite(self.cash_weight) or not 0.0 <= self.cash_weight <= MAX_WEIGHT:
            raise ValueError("Cash weight must be in [0, 1]")
        if not math.isfinite(self.risk_multiplier) or self.risk_multiplier < 0:
            raise ValueError("Risk multiplier must be non-negative")
        if len({_canonical_key(weight.symbol) for weight in self.weights}) != len(self.weights):
            raise ValueError("Target portfolio contains duplicate symbols")
        total = self.cash_weight + sum(weight.weight for weight in self.weights)
        if total > 1.0 + WEIGHT_TOLERANCE:
            raise ValueError(f"Target weights exceed 1: {total:.6f}")

    @property
    def invested_weight(self) -> float:
        return sum(weight.weight for weight in self.weights)

    def weight_for(self, symbol: str, market: Market | None = None) -> float:
        try:
            canonical = canonical_symbol(symbol, market)
        except ValueError:
            return 0.0
        for weight in self.weights:
            try:
                candidate = canonical_symbol(weight.symbol, market)
            except ValueError:
                continue
            if candidate == canonical:
                return weight.weight
        return 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "decision_timestamp": self.decision_timestamp.isoformat(),
            "data_timestamp": self.data_timestamp.isoformat() if self.data_timestamp else None,
            "weights": [
                {"symbol": weight.symbol, "weight": weight.weight, "reason": weight.reason}
                for weight in self.weights
            ],
            "cash_weight": self.cash_weight,
            "rebalance_reason": self.rebalance_reason.value,
            "universe_version": self.universe_version,
            "universe_snapshot_id": self.universe_snapshot_id,
            "parameters": dict(self.parameters),
            "risk_multiplier": self.risk_multiplier,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TargetPortfolio:
        """Reconstruct a persisted decision target (used by rebalance recovery)."""
        weights = tuple(
            TargetWeight(
                symbol=str(weight["symbol"]),
                weight=float(weight["weight"]),
                reason=str(weight.get("reason", "")),
            )
            for weight in dict(raw).get("weights", ())
        )
        raw_timestamp = raw.get("decision_timestamp") or raw.get("timestamp")
        if raw_timestamp is None:
            raise ValueError("Target portfolio dict is missing decision_timestamp")
        timestamp = datetime.fromisoformat(str(raw_timestamp))
        raw_data_timestamp = raw.get("data_timestamp")
        return cls(
            strategy_name=str(raw.get("strategy_name", "")),
            strategy_version=str(raw.get("strategy_version", "")),
            decision_timestamp=timestamp,
            weights=weights,
            cash_weight=float(raw.get("cash_weight", 0.0)),
            rebalance_reason=RebalanceReason(str(raw.get("rebalance_reason", "scheduled"))),
            data_timestamp=(
                datetime.fromisoformat(str(raw_data_timestamp)) if raw_data_timestamp else None
            ),
            universe_version=str(raw.get("universe_version", "")),
            universe_snapshot_id=str(raw.get("universe_snapshot_id", "")),
            parameters=tuple(
                (str(key), value) for key, value in dict(raw.get("parameters", {})).items()
            ),
            risk_multiplier=float(raw.get("risk_multiplier", 1.0)),
            metadata=dict(raw.get("metadata", {})),
        )


@dataclass(frozen=True)
class RebalancePolicy:
    """When the portfolio strategy is allowed to produce new target weights.

    ``frequency`` is ``monthly`` (first bar of each calendar month),
    ``weekly`` (first bar of each ISO week) or ``hours:N`` (first bar of every
    N-hour block). Decisions always happen on completed bars and orders become
    effective on the next tradable bar.
    """

    market: Market
    frequency: str = "monthly"

    def __post_init__(self) -> None:
        if (
            self.frequency != "monthly"
            and self.frequency != "weekly"
            and not (self.frequency.startswith("hours:"))
        ):
            raise ValueError(f"Unsupported rebalance frequency: {self.frequency}")
        if self.frequency.startswith("hours:"):
            hours = self.frequency.removeprefix("hours:")
            if not hours.isdigit() or int(hours) <= 0:
                raise ValueError("hours:N rebalance requires a positive N")
        elif self.market == Market.CRYPTO:
            raise ValueError("Crypto rebalancing requires an hours:N frequency")

    @property
    def hours(self) -> int | None:
        if not self.frequency.startswith("hours:"):
            return None
        return int(self.frequency.removeprefix("hours:"))

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        if previous is None:
            return True
        if self.frequency == "monthly":
            return (previous.year, previous.month) != (current.year, current.month)
        if self.frequency == "weekly":
            return previous.isocalendar()[:2] != current.isocalendar()[:2]
        hours = self.hours
        assert hours is not None
        previous_epoch_hours = int(previous.timestamp()) // (hours * 3600)
        current_epoch_hours = int(current.timestamp()) // (hours * 3600)
        return previous_epoch_hours != current_epoch_hours


@dataclass
class PortfolioState:
    """Persistent portfolio bookkeeping that survives engine restarts.

    ``holdings`` maps canonical symbol to the quantity last executed, so a
    restarted engine resumes from the same allocation instead of re-deciding
    from scratch. Persisted as JSON; the backtest engine treats it as the
    single source of truth for current holdings across ``run`` calls.

    Schema v2 adds cash, realized PnL, entry fees, last prices, pending
    target/orders and the last-processed-bar watermark so a restart resumes
    economically complete state instead of re-deriving cash from the
    starting equity.
    """

    strategy_name: str
    strategy_version: str
    holdings: dict[str, float] = field(default_factory=dict)
    avg_prices: dict[str, float] = field(default_factory=dict)
    last_target: dict[str, float] = field(default_factory=dict)
    last_rebalance_at: datetime | None = None
    rebalance_count: int = 0
    universe_version: str = ""
    universe_snapshot_id: str = ""
    # None means "unknown (v1 state)": cash is derived from holdings once.
    cash: float | None = None
    realized_pnl: float = 0.0
    entry_fees: dict[str, float] = field(default_factory=dict)
    # Per-position realized accumulator: a partial exit credits realized PnL
    # immediately, and the remainder travels with the position so a restart
    # after a partial exit reports the same final realized events.
    realized_pnl_accumulators: dict[str, float] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)
    # Decision made but not yet filled; never dropped on restart.
    pending_target: dict[str, float] = field(default_factory=dict)
    pending_orders: tuple[dict[str, object], ...] = ()
    last_processed_bar: str | None = None
    # Schema v3: the explicit daily-loss/high-water risk lifecycle. A restart
    # must resume the same peak equity, day-start equity and session date so
    # drawdown halts and daily-loss behavior are identical to an uninterrupted
    # run. None means "unknown (pre-v3 state)": the engine seeds them from the
    # starting equity exactly once.
    peak_equity: float | None = None
    day_start_equity: float | None = None
    last_observed_equity: float | None = None
    risk_session_date: str | None = None
    schema_version: int = 3

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "holdings": self.holdings,
            "avg_prices": self.avg_prices,
            "last_target": self.last_target,
            "last_rebalance_at": (
                self.last_rebalance_at.isoformat() if self.last_rebalance_at else None
            ),
            "rebalance_count": self.rebalance_count,
            "universe_version": self.universe_version,
            "universe_snapshot_id": self.universe_snapshot_id,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "entry_fees": self.entry_fees,
            "realized_pnl_accumulators": self.realized_pnl_accumulators,
            "last_prices": self.last_prices,
            "pending_target": self.pending_target,
            "pending_orders": list(self.pending_orders),
            "last_processed_bar": self.last_processed_bar,
            "peak_equity": self.peak_equity,
            "day_start_equity": self.day_start_equity,
            "last_observed_equity": self.last_observed_equity,
            "risk_session_date": self.risk_session_date,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PortfolioState:
        holdings = _float_mapping(raw, "holdings")
        avg_prices = _float_mapping(raw, "avg_prices")
        last_target = _float_mapping(raw, "last_target")
        entry_fees = _float_mapping(raw, "entry_fees")
        last_prices = _float_mapping(raw, "last_prices")
        pending_target = _float_mapping(raw, "pending_target")
        raw_pending = raw.get("pending_orders", ())
        pending_orders = tuple(dict(item) for item in raw_pending if isinstance(item, dict))
        realized_pnl_accumulators = _float_mapping(raw, "realized_pnl_accumulators")
        last_rebalance = raw.get("last_rebalance_at")
        raw_cash = raw.get("cash")
        return cls(
            strategy_name=str(raw.get("strategy_name", "")),
            strategy_version=str(raw.get("strategy_version", "")),
            holdings=holdings,
            avg_prices=avg_prices,
            last_target=last_target,
            last_rebalance_at=(
                datetime.fromisoformat(str(last_rebalance)) if last_rebalance else None
            ),
            rebalance_count=int(raw.get("rebalance_count", 0)),
            universe_version=str(raw.get("universe_version", "")),
            universe_snapshot_id=str(raw.get("universe_snapshot_id", "")),
            cash=float(raw_cash) if raw_cash is not None else None,
            realized_pnl=float(raw.get("realized_pnl", 0.0)),
            entry_fees=entry_fees,
            realized_pnl_accumulators=realized_pnl_accumulators,
            last_prices=last_prices,
            pending_target=pending_target,
            pending_orders=pending_orders,
            last_processed_bar=(
                str(raw["last_processed_bar"]) if raw.get("last_processed_bar") else None
            ),
            peak_equity=(float(raw["peak_equity"]) if raw.get("peak_equity") is not None else None),
            day_start_equity=(
                float(raw["day_start_equity"]) if raw.get("day_start_equity") is not None else None
            ),
            last_observed_equity=(
                float(raw["last_observed_equity"])
                if raw.get("last_observed_equity") is not None
                else None
            ),
            risk_session_date=(
                str(raw["risk_session_date"]) if raw.get("risk_session_date") else None
            ),
            schema_version=int(raw.get("schema_version", 1)),
        )


__all__ = [
    "MAX_WEIGHT",
    "WEIGHT_TOLERANCE",
    "PortfolioState",
    "RebalancePolicy",
    "RebalanceReason",
    "TargetPortfolio",
    "TargetWeight",
]
