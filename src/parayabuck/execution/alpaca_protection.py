"""Protective-stop lifecycle — equity & bracket.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- Detect existing protection (batch ``get_orders`` once, fee-reserve
  tolerance, nested bracket via ``get_order_by_id(nested=True)``).
- Calculate ``required_qty`` (remaining effective, ``0.9975`` fee-reserve
  when ``qty_available == 0``).
- Under/over detection, cancel+confirm before replace, submit remainder
  only, re-protect after execution changes.
- Equity fractional ``DAY`` vs integral ``GTC``, crypto via separate
  module.
"""

from __future__ import annotations

__all__ = ["ProtectionService"]


class ProtectionService:  # pragma: no cover
    pass
