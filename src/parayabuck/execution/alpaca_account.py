"""Account snapshot and ownership classification.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 7).

Responsibility:
- Fetch ``get_account`` + ``get_all_positions`` → :class:`AccountSnapshot`.
- Fail-closed blocked checks: ``account_blocked`` / ``trading_blocked`` /
  ``trade_suspended_by_user`` → ``RuntimeError`` (never tradable).
- Market classification via ``asset_class`` / ``is_crypto``.
- Ownership: ``managed`` / ``mixed`` / ``external`` with
  ``managed_qty = min(|raw|, total_owned)``, ``external_qty``,
  ``qty_available``, dual-lineage fail-closed, shortfall with SELL
  exemption.
- Portfolio-owned quantity is ``ExecutionLedger.managed_for_account`` plus
  ``PortfolioStateRepository`` owned quantities; the facade supplies both.
"""

from __future__ import annotations

from typing import Any

from parayabuck.core.models import Market
from parayabuck.logger import get_logger

log = get_logger("parayabuck.execution.alpaca_account")

# This module is intentionally thin in Mission 7 Iteration 1: the full
# reconciliation-heavy account() implementation remains in the facade
# ``AlpacaBroker.account()`` (2871-line boundary). The service below
# documents the contract and is used by characterization tests; future
# iterations will move the method body here verbatim.

__all__ = ["AccountService"]


class AccountService:
    """Documents the account/ownership contract; facade delegates here."""

    @staticmethod
    def is_account_blocked(account: Any) -> bool:
        from parayabuck.utils.objects import field_value

        return bool(
            field_value(account, "account_blocked", False)
            or field_value(account, "trading_blocked", False)
            or field_value(account, "trade_suspended_by_user", False)
        )

    @staticmethod
    def classify_market(asset: Any, is_crypto_fn) -> Market:  # type: ignore[no-untyped-def]
        from parayabuck.core.models import Market

        raw_class = getattr(asset, "asset_class", None)
        if raw_class is not None:
            try:
                v = str(raw_class).lower()
                if "crypto" in v:
                    return Market.CRYPTO
                return Market.EQUITY
            except Exception:
                pass
        # Fallback to helper
        try:
            return Market.CRYPTO if is_crypto_fn(getattr(asset, "symbol", "")) else Market.EQUITY
        except Exception:
            return Market.EQUITY
