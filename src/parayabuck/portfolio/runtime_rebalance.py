"""Two-phase rebalance execution phases.

Extracted from ``runtime_service.py`` to make the sell/buy lifecycle
explicit and reviewable. Provides the restart-safe phase predicates the
orchestrator advances on.

Phases:
    SELL_PENDING → BUY_READY → BUY_PENDING → COMPLETE
                      ↘ BLOCKED (any → BLOCKED on ambiguous/ error)

No generic state-machine library: plain constants + validation.
"""

from __future__ import annotations

from parayabuck.portfolio.runtime_models import (
    _AMBIGUOUS_ORDER_STATUSES,
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
    is_terminal_order_status,
)

# Re-export phase constants for callers that prefer this module
__all__ = [
    "REBALANCE_PHASE_BLOCKED",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_SELL_PENDING",
    "is_sell_phase_complete",
    "requires_ambiguous_block",
]


def is_sell_phase_complete(sell_statuses: list[str]) -> bool:
    """True when every required SELL is terminal and none is ambiguous."""
    if not sell_statuses:
        return False
    if any(s in _AMBIGUOUS_ORDER_STATUSES for s in sell_statuses):
        return False
    return all(is_terminal_order_status(s) for s in sell_statuses)


def requires_ambiguous_block(statuses: list[str]) -> bool:
    """Whether an ambiguous order state requires BLOCKED."""
    return any(s in _AMBIGUOUS_ORDER_STATUSES for s in statuses)
