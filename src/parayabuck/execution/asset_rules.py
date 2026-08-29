"""Asset trading-rules and cache — Own: tradable, qty steps, price steps.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- Own ``tradable`` / ``fractional`` / ``quantity_increment`` /
  ``min_order_size`` / ``quantity_increment`` / ``price_increment`` /
  ``minimum_order_notional`` and the ``_asset_cache``.
- No stale-correctness: cache is per-broker, not shared; TTL is not
  introduced (existing semantics: cache forever per process, invalidate
  only on broker restart).
- Used by ``OrderService`` for quantity flooring and notional guard; not
  mixed with submission.
"""

from __future__ import annotations

from parayabuck.execution.portfolio import AssetTradingRules

__all__ = ["AssetRulesService"]


class AssetRulesService:
    """Asset rules cache contract (see ``alpaca.py:asset_rules``)."""

    def __init__(self) -> None:
        self._cache: dict[str, AssetTradingRules] = {}

    def get(self, symbol: str) -> AssetTradingRules | None:
        return self._cache.get(symbol)

    def set(self, symbol: str, rules: AssetTradingRules) -> None:
        self._cache[symbol] = rules
