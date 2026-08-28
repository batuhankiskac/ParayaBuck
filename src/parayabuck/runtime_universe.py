"""Universe lifecycle — extracted from runtime.py.

Owns active symbol sets, refresh state, retry backoff, and validation.
Does NOT own strategy math or broker execution.

This module documents the boundary; EngineRuntime remains the facade that
composes it. Full extraction would require moving the scan-vs-BTC/ETH
branching and the managed-position pinning, which remain in EngineRuntime
for this mission to avoid risky threading changes. The manager currently
exposes pure helpers and state for future extraction.

Threading: refresh is driven by EngineRuntime's dedicated universe thread;
state is protected by EngineRuntime's existing locks (not duplicated here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Per-market scanner-refresh backoff after a failed scheduled refresh
_UNIVERSE_RETRY_BACKOFF_SECONDS = (300.0, 900.0, 1800.0, 3600.0)


@dataclass
class UniverseRefreshState:
    """Per-market universe refresh bookkeeping."""

    last_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    next_retry_at: datetime | None = None
    last_success_at: datetime | None = None

    def record_success(self, at: datetime | None = None) -> None:
        self.last_success_at = at or datetime.now(UTC)
        self.last_attempt_at = self.last_success_at
        self.consecutive_failures = 0
        self.next_retry_at = None

    def record_failure(self, at: datetime | None = None) -> None:
        now = at or datetime.now(UTC)
        self.last_attempt_at = now
        self.consecutive_failures += 1
        idx = min(self.consecutive_failures - 1, len(_UNIVERSE_RETRY_BACKOFF_SECONDS) - 1)
        backoff = _UNIVERSE_RETRY_BACKOFF_SECONDS[idx]
        from datetime import timedelta

        self.next_retry_at = now + timedelta(seconds=backoff)


@dataclass
class RuntimeUniverseManager:
    """Holds universe lifecycle state for EngineRuntime (facade-owned)."""

    active_symbols: set[str] = field(default_factory=set)
    refresh_state: dict[str, UniverseRefreshState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for market in ("equity", "crypto"):
            self.refresh_state.setdefault(market, UniverseRefreshState())

    def symbols_for_market(self, market: str) -> set[str]:
        # Simple helper — real filtering is still in EngineRuntime
        return {s for s in self.active_symbols if (market == "crypto") == ("/" in s or s.endswith("USD"))}


__all__ = ["RuntimeUniverseManager", "UniverseRefreshState", "_UNIVERSE_RETRY_BACKOFF_SECONDS"]
