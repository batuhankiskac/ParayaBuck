"""Account snapshot and ownership classification.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8 — Finish the Boundary).

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
- Snapshot cache coordination (broker owns cache, service seeds it).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_models import CRYPTO_TAKER_FEE_RESERVE, OwnershipDriftError
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca_account")

__all__ = ["AccountService"]


class AccountService:
    """Account/ownership service — production implementation.

    The service holds a reference to the owning :class:`AlpacaBroker`
    facade (typed as ``Any`` to avoid a circular import). All broker
    state (``_client``, ``ledger``, ``portfolio_owned_quantities``,
    ``_account_id``, ``_risk_peak``) is accessed through that reference
    so ``broker._client = fake`` in tests is observed immediately.
    """

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    # -- blocked helpers -------------------------------------------------

    @staticmethod
    def is_account_blocked(account: Any) -> bool:
        from parayabuck.utils.objects import field_value

        return bool(
            field_value(account, "account_blocked", False)
            or field_value(account, "trading_blocked", False)
            or field_value(account, "trade_suspended_by_user", False)
        )

    @staticmethod
    def classify_market(asset: Any, is_crypto_fn: Any = is_crypto) -> Market:
        raw_class = getattr(asset, "asset_class", None)
        if raw_class is not None:
            try:
                v = str(raw_class).lower()
                if "crypto" in v:
                    return Market.CRYPTO
                return Market.EQUITY
            except Exception:
                pass
        try:
            return Market.CRYPTO if is_crypto_fn(getattr(asset, "symbol", "")) else Market.EQUITY
        except Exception:
            return Market.EQUITY

    # -- drift helpers (used by account) --------------------------------

    def _portfolio_open_order_map(self) -> dict[str, list[object]]:
        provider = getattr(self._broker, "portfolio_open_orders", None)
        if provider is None:
            return {}
        grouped: dict[str, list[object]] = {}
        for order in provider() or ():
            symbol = str(getattr(order, "symbol", ""))
            if not symbol or str(getattr(order, "side", "")).lower() != "sell":
                continue
            market = getattr(order, "market", None)
            canonical = canonical_symbol(symbol, market)
            grouped.setdefault(canonical, []).append(order)
        return grouped

    @staticmethod
    def _open_order_explains_drift(
        canonical: str,
        owned_qty: float,
        raw_qty: float,
        market: Market,
        orders_by_symbol: dict[str, list[object]],
    ) -> bool:
        fee_reserve = float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE) if market == Market.CRYPTO else 1.0
        shortfall = max(0.0, owned_qty * fee_reserve - abs(raw_qty))
        if shortfall <= 1e-12:
            return False
        for order in orders_by_symbol.get(canonical, ()):
            order_market = getattr(order, "market", None)
            if order_market is not None and order_market != market:
                continue
            quantity = float(getattr(order, "quantity", 0.0) or 0.0)
            filled = float(getattr(order, "cumulative_filled_quantity", 0.0) or 0.0)
            remaining = max(0.0, quantity - filled)
            if remaining >= shortfall - max(1e-9, shortfall * 1e-8):
                return True
        return False

    # -- snapshot cache --------------------------------------------------

    def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None:
        cached = getattr(self._broker, "_snapshot_cache", None)
        cached_at = getattr(self._broker, "_snapshot_cached_at", None)
        if cached is None or cached_at is None:
            return None
        if time.monotonic() - cached_at <= max_age_seconds:
            return cached  # type: ignore[no-any-return]
        return None

    # -- main entry ------------------------------------------------------

    def account(self) -> AccountSnapshot:
        broker = self._broker
        # Access via property so fake injection is observed
        client = broker._client
        account: Any = client.get_account()
        # Bind account id
        broker._account_id = str(getattr(account, "id", "") or getattr(account, "account_number", ""))
        if (
            bool(getattr(account, "account_blocked", False))
            or bool(getattr(account, "trading_blocked", False))
            or bool(getattr(account, "trade_suspended_by_user", False))
        ):
            raise RuntimeError("Alpaca account is blocked from trading")
        equity = float(account.equity or 0)
        previous_equity = float(account.last_equity or equity)
        positions: list[PositionSnapshot] = []
        ledger = getattr(broker, "ledger", None)
        managed_positions = ledger.managed_for_account(broker._account_id or "unknown") if ledger is not None else {}
        owned_quantities: dict[str, float] = {}
        portfolio_owned_provider = getattr(broker, "portfolio_owned_quantities", None)
        if portfolio_owned_provider is not None:
            owned_quantities = portfolio_owned_provider() or {}
        unrealized = 0.0
        intraday_unrealized = 0.0
        raw_positions: Any = client.get_all_positions()
        drift_candidates: list[tuple[str, float, float, Market]] = []
        for raw in raw_positions:
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            asset_class = str(getattr(raw, "asset_class", "")).lower()
            market = Market.CRYPTO if "crypto" in asset_class or is_crypto(str(raw.symbol)) else Market.EQUITY
            canonical = canonical_symbol(str(raw.symbol), market)
            managed = managed_positions.get(canonical)
            raw_qty = float(raw.qty)
            ledger_owned = float(managed.owned_qty) if managed is not None else 0.0
            repo_owned = float(owned_quantities.get(canonical, 0.0))
            if ledger_owned > 1e-12 and repo_owned > 1e-12:
                raise OwnershipDriftError(
                    "Dual ownership lineage for "
                    f"{canonical}: ledger managed qty={ledger_owned} and "
                    f"portfolio managed qty={repo_owned} are both positive; "
                    "reconciliation is required before trading resumes"
                )
            total_owned = ledger_owned + repo_owned
            fee_reserve = float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE) if market == Market.CRYPTO else 1.0
            if (
                managed is None
                and total_owned > 1e-12
                and abs(raw_qty) > 1e-12
                and abs(raw_qty) + max(1e-9, total_owned * 1e-8) < total_owned * fee_reserve
            ):
                drift_candidates.append((canonical, total_owned, raw_qty, market))
            managed_qty = min(abs(raw_qty), total_owned)
            external_qty = abs(raw_qty) - managed_qty
            ownership = (
                "managed"
                if managed_qty > 1e-12 and external_qty <= 1e-12
                else "mixed"
                if managed_qty > 1e-12
                else "external"
            )
            positions.append(
                PositionSnapshot(
                    symbol=canonical,
                    qty=raw_qty,
                    avg_price=float(raw.avg_entry_price),
                    current_price=current_price,
                    market=market,
                    qty_available=(
                        float(raw.qty_available) if getattr(raw, "qty_available", None) is not None else None
                    ),
                    ownership=ownership,
                    managed_qty=managed_qty,
                    stop_price=(managed.current_stop or managed.original_stop if managed is not None else None),
                    initial_risk=(
                        abs(float(raw.avg_entry_price) - managed.original_stop) if managed is not None else None
                    ),
                )
            )
        if drift_candidates:
            repo_orders = self._portfolio_open_order_map()
            for canonical, owned_qty, raw_qty, market in drift_candidates:
                if self._open_order_explains_drift(canonical, owned_qty, raw_qty, market, repo_orders):
                    continue
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: repository managed "
                    f"qty={owned_qty} exceeds broker position qty={raw_qty}"
                )
        # Account-scoped peak reconciliation
        broker._ensure_peak_reconciled(broker._account_id or "unknown", equity)
        old_peak = broker._peak_equity
        broker._peak_equity = max(broker._peak_equity, equity)
        if broker._peak_equity != old_peak:
            broker._save_peak_equity()
        daily_pnl = equity - previous_equity
        cash = float(account.cash or 0)
        crypto_buying_power_raw = getattr(account, "non_marginable_buying_power", None)
        crypto_status = str(getattr(account, "crypto_status", "")).upper().split(".")[-1]
        snapshot = AccountSnapshot(
            cash=cash,
            equity=equity,
            peak_equity=broker._peak_equity,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
            crypto_buying_power=(float(crypto_buying_power_raw) if crypto_buying_power_raw is not None else cash),
            crypto_trading_enabled=crypto_status == "ACTIVE",
            fees=(ledger.total_fees_for_account(broker._account_id or "unknown") if ledger is not None else 0.0),
        )
        broker._snapshot_cache = snapshot
        broker._snapshot_cached_at = time.monotonic()
        return snapshot
