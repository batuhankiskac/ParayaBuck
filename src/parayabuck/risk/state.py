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
    peak_epoch: int = 0


@dataclass(frozen=True)
class SafetyIncident:
    """A PERSISTENT_INCIDENT circuit-breaker record that survives the
    session-day boundary.

    Unlike the SESSION_SCOPED ``RiskState`` row (keyed by session date), an
    incident is keyed by ``(account_id, reason)`` and is restored regardless
    of date, so a halt persisted at 23:59 is still active after a 00:01
    restart until an operator verifies the recovery. ``scope`` is always
    ``"global"`` (the incident blocks entries for the whole account).
    """

    account_id: str
    reason: str
    scope: str
    activated_at: datetime
    updated_at: datetime


__all__ = ["RiskState", "SafetyIncident"]
