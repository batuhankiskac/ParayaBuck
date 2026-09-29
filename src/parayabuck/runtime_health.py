"""Runtime health models — extracted from runtime.py.

Preserves dashboard/API schema: existing consumers must receive identical fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkerHealth:
    started_at: datetime
    last_heartbeat: datetime
    last_event_at: datetime | None = None
    last_error: str | None = None
    restart_count: int = 0
    alive: bool = False


@dataclass(frozen=True)
class RuntimeHealth:
    state: str
    healthy: bool
    circuit_breaker_reason: str | None
    queue_size: int
    queue_age_seconds: float
    dropped_events: int
    coalesced_events: int
    workers: dict[str, dict[str, object]]
    markets: dict[str, dict[str, object]]
    strategy_status: str = "configured"
    strategies: tuple[dict[str, object], ...] = ()
    last_reconciliation_at: str | None = None
    universe_refresh: dict[str, object] = field(default_factory=dict)
    entry_risk_latch: dict[str, str | None] = field(default_factory=dict)
    signal_adjustment: dict[str, object] = field(default_factory=dict)
    # Intent-vs-reality watchdog: {"enabled", "evaluated_at", "alarms",
    # "degraded_reasons"}. Observability only; never part of ``healthy``.
    intent_watchdog: dict[str, object] = field(default_factory=dict)


def _portfolio_catch_up_health(runtime: object) -> dict[str, str | None]:
    """Health-only catch-up snapshot for one portfolio runtime (or none).

    ``status`` is the last startup_catch_up result status (None before the
    first attempt), ``at`` the UTC attempt timestamp, and
    ``next_boundary_after`` the next cadence boundary strictly after the last
    processed timestamp (None when no history has been processed yet).
    """
    if runtime is None:
        return {"status": None, "at": None, "next_boundary_after": None}
    catch_up_at = getattr(runtime, "startup_catch_up_at", None)
    previous_timestamp = getattr(runtime, "previous_timestamp", None)
    next_boundary: str | None = None
    if previous_timestamp is not None:
        policy = getattr(runtime, "policy", None)
        if policy is not None:
            next_boundary = policy.next_boundary_after(previous_timestamp).isoformat()
    return {
        "status": getattr(runtime, "startup_catch_up_status", None),
        "at": catch_up_at.isoformat() if catch_up_at is not None else None,
        "next_boundary_after": next_boundary,
    }


__all__ = ["RuntimeHealth", "WorkerHealth", "_portfolio_catch_up_health"]
