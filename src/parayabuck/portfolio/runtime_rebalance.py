"""Two-phase rebalance execution state machine.

Extracted from ``runtime_service.py`` to make the sell/buy lifecycle
explicit and reviewable. Documents allowed transitions and provides
helpers for restart-safe advancement.

Phases:
    SELL_PENDING → BUY_READY → BUY_PENDING → COMPLETE
                      ↘ BLOCKED (any → BLOCKED on ambiguous/ error)

No generic state-machine library: plain constants + validation.
"""

from __future__ import annotations

from parayabuck.portfolio.runtime_models import (
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
    is_allowed_rebalance_transition,
    is_terminal_order_status,
)

# Re-export phase constants for callers that prefer this module
__all__ = [
    "REBALANCE_PHASE_BLOCKED",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_SELL_PENDING",
    "is_allowed_rebalance_transition",
    "is_sell_phase_complete",
    "is_buy_phase_complete",
    "requires_ambiguous_block",
]

_AMBIGUOUS_ORDER_STATUSES = frozenset({"reconciliation_required", "manual_intervention_required"})


def is_sell_phase_complete(sell_statuses: list[str]) -> bool:
    """True when every required SELL is terminal and none is ambiguous."""
    if not sell_statuses:
        return False
    if any(s in _AMBIGUOUS_ORDER_STATUSES for s in sell_statuses):
        return False
    return all(is_terminal_order_status(s) for s in sell_statuses)


def is_buy_phase_complete(buy_statuses: list[str]) -> bool:
    """True when every deferred BUY is terminal."""
    if not buy_statuses:
        return True  # no buys → already complete
    return all(is_terminal_order_status(s) for s in buy_statuses)


def requires_ambiguous_block(statuses: list[str]) -> bool:
    """Whether an ambiguous order state requires BLOCKED."""
    return any(s in _AMBIGUOUS_ORDER_STATUSES for s in statuses)
