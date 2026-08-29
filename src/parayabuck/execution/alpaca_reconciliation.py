"""Reconciliation — 7-bucket, idempotent, fail-closed.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- Orchestrate ``account()`` → fee/fill activity backfill (watermarked
  24 h, paginated, deduped by ``activity.id``) → ``managed =
  ledger.managed_for_account`` → holdings 7-bucket (``MATCH`` etc.) →
  ``_reconcile_stale_portfolio_ownership`` → ``_reconcile_crypto_exit_state``
  → ``_reconcile_stale_ledger_entries`` → protective-stop (re)submission.
- Idempotent: second call with same broker truth yields same buckets, no
  duplicate fills/stops.
- Emits observability but never controls trading; monitoring failure does
  not affect reconciliation decisions.
"""

from __future__ import annotations

__all__ = ["ReconciliationService"]


class ReconciliationService:  # pragma: no cover - contract doc
    """Reconciliation contract (see ``alpaca.py:reconcile()``)."""

    pass
