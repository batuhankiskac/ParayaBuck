from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class RiskState:
    """Persisted account-level circuit-breaker state used during recovery."""

    account_id: str
    session_date: date
    day_start_equity: float
    peak_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    consecutive_losses: int
    circuit_breaker_reason: str | None
    updated_at: datetime


__all__ = ["RiskState"]
