"""Order submission and normalization — safety-sensitive.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- ``PortfolioOrderRequest`` → validated broker request → ``submit`` →
  ``broker_order_id`` or :class:`AmbiguousOrderState`.
- Quantity flooring to ``quantity_increment``, notional guard,
  open-order guard (``_has_open_order``), ``MarketOrderRequest``
  (``GTC`` crypto else ``DAY``), ``client_order_id`` as recovery key.
- ``_submit_with_recovery``: on submit raise, try ``get_order_by_client_id``
  → terminal re-raise, working recover, 404 re-raise, lookup fail →
  :class:`AmbiguousOrderState` (no blind resubmit).
- ``order_state`` normalization to :class:`OrderState` (``filled``,
  ``canceled``, ``rejected``, ``expired``, ``unknown`` / ambiguous).
- ``filled_qty`` / ``avg_price`` / ``fees`` centralization.

Mission 7 Iteration 1: facade still owns the method body; this module
documents the contract for review and future extraction.
"""

from __future__ import annotations

__all__ = ["OrderService"]


class OrderService:  # pragma: no cover - contract doc
    """Order submission contract (see ``alpaca.py`` for current implementation)."""

    pass
