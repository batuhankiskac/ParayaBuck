"""Crypto synthetic OCO — exit, failover, reprotection.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- ``process_crypto_price`` per-symbol ``threading.Lock`` (non-blocking,
  same-symbol serialized, different symbols not blocked, no global lock).
- Target / stop / emergency (``stop * (1 - 75 bps)``) / intrabar both-hit.
- ``claim_symbol_exit`` idempotent, ``_cancel_stop_then_exit`` (bounded
  5 s poll, 0.25 s interval, verify terminal, partial fill → cancel
  remainder, ambiguous → manual intervention).
- Reprotection of remainder, pending-exit restart recovery via
  ``reconcile``.
- Never duplicate exit, never leave unprotected remainder.
"""

from __future__ import annotations

__all__ = ["CryptoService"]


class CryptoService:  # pragma: no cover
    pass
