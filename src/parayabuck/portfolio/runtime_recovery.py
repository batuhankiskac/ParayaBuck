"""Restart / reconciliation helpers.

Extracted from ``runtime_service.py`` to isolate crash-recovery concerns:
decision idempotency, rebalance restart safety, and stuck-submission
resolution. No strategy or risk logic.
"""

from __future__ import annotations

from datetime import datetime


def is_decision_idempotent(
    existing_timestamp: datetime | None,
    new_timestamp: datetime,
) -> bool:
    """True when a decision for (account, digest, timestamp, market) already exists.

    Used by ``PortfolioStateRepository.has_decision`` callers to avoid
    duplicate persists across restarts.
    """
    return existing_timestamp is not None and existing_timestamp == new_timestamp


def should_refresh_account_before_buys(sell_statuses: list[str]) -> bool:
    """Whether the BUY phase must refresh the broker account first.

    Always True after sells are terminal: the remaining BUY exposure must be
    re-checked against the fresh post-sell equity/cash.
    """
    # Currently trivial but documents the safety step in the two-phase flow:
    # 1. sells terminal → 2. refresh account → 3. re-run risk → 4. submit buys
    return True


__all__ = ["is_decision_idempotent", "should_refresh_account_before_buys"]
