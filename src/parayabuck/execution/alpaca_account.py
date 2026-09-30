"""Account snapshot and ownership classification.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8 — Finish the Boundary).

Responsibility:
- Fetch ``get_account`` + ``get_all_positions`` → :class:`AccountSnapshot`.
- Fail-closed blocked checks: ``account_blocked`` / ``trading_blocked`` /
  ``trade_suspended_by_user`` → ``RuntimeError`` (never tradable).
- Market classification via ``asset_class`` / ``is_crypto``: a foreign
  crypto holding in the shared account is classified ``Market.CRYPTO`` so it
  is recognized and left alone (the bot trades equities only).
- Ownership: ``managed`` / ``mixed`` / ``external`` with
  ``managed_qty = min(|raw|, total_owned)``, ``external_qty``,
  ``qty_available``, dual-lineage fail-closed, shortfall with SELL
  exemption. A share count that moved without a fill of ours (stock split,
  foreign trade on the shared key) is first offered to
  ``broker.portfolio_ownership_resolved``, which records an audited
  adjustment; whatever it cannot prove keeps the fail-closed drift error.
- Portfolio-owned quantity is ``ExecutionLedger.managed_for_account`` plus
  ``PortfolioStateRepository`` owned quantities; the facade supplies both.
- Snapshot cache coordination (broker owns cache, service seeds it).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_models import OwnershipDriftError
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
        """CRYPTO when the asset class says so *or* the symbol says so.

        Alpaca populates ``asset_class`` inconsistently, so a crypto pair
        arriving as ``''`` or ``'us_equity'`` must still be recognized; a
        misread market makes ``canonical_symbol`` either raise or return the
        wrong ownership key.
        """
        raw_class = getattr(asset, "asset_class", None)
        if raw_class is not None:
            try:
                if "crypto" in str(raw_class).lower():
                    return Market.CRYPTO
            except Exception:
                pass
        try:
            return Market.CRYPTO if is_crypto_fn(str(getattr(asset, "symbol", ""))) else Market.EQUITY
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
        shortfall = max(0.0, owned_qty - abs(raw_qty))
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

    def _resolve_broker_quantity(
        self,
        canonical: str,
        market: Market,
        broker_qty: float,
        raw: Any,
        owned_before: float,
    ) -> float | None:
        """Rebase portfolio ownership onto a differing broker share count.

        A stock split, or another consumer of the shared Alpaca key, moves the
        broker's shares without a fill of ours. While ownership disagrees,
        every decision (exits included) is dropped and no safety reset can
        succeed, because a clean reconcile is impossible. The repository
        records the audited adjustment and refuses anything it cannot prove;
        a refusal leaves the fail-closed drift path in place. Returns the
        owned quantity after the rebase, or ``None`` when it was declined.
        """
        resolver = getattr(self._broker, "portfolio_ownership_resolved", None)
        if resolver is None:
            return None
        average_entry = getattr(raw, "avg_entry_price", None)
        resolved = resolver(
            canonical,
            market,
            broker_qty,
            float(average_entry) if average_entry is not None else None,
        )
        if resolved is None:
            return None
        self._alert_ownership_rebase(canonical, market, owned_before, broker_qty)
        return float(resolved)

    @staticmethod
    def _alert_ownership_rebase(canonical: str, market: Market, owned_before: float, broker_qty: float) -> None:
        """One alert per rebase episode; the audit row is the durable record."""
        try:
            from parayabuck.monitoring import alerts

            alerts.emit(
                "paper_incident",
                message=(
                    f"Ownership rebased onto the broker share count for {canonical}: "
                    f"owned {owned_before:g} -> broker {broker_qty:g}. Verify the corporate "
                    "action or the foreign trade on the broker; the adjustment is listed "
                    "as a reconciliation anomaly."
                ),
                category="ownership_rebased",
                incident_id=f"ownership_rebased:{market.value}:{canonical}:{datetime.now(UTC):%Y-%m-%d}",
                symbol=canonical,
                market=market.value,
                previous_owned_quantity=owned_before,
                broker_quantity=broker_qty,
            )
        except Exception:
            log.warning("Alert emission failed for the ownership rebase of %s", canonical, exc_info=True)

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
        if self.is_account_blocked(account):
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
        seen_position_keys: set[tuple[Market, str]] = set()
        open_sell_orders: dict[str, list[object]] | None = None

        def sell_orders() -> dict[str, list[object]]:
            """Our working SELL orders, read at most once per snapshot."""
            nonlocal open_sell_orders
            if open_sell_orders is None:
                open_sell_orders = self._portfolio_open_order_map()
            return open_sell_orders

        for raw in raw_positions:
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            market = self.classify_market(raw)
            canonical = canonical_symbol(str(raw.symbol), market)
            position_key = (market, canonical)
            if position_key in seen_position_keys:
                raise OwnershipDriftError(
                    f"Duplicate broker position for {canonical} ({market.value}); "
                    "reconciliation is required before trading resumes"
                )
            seen_position_keys.add(position_key)
            managed = managed_positions.get(canonical)
            raw_qty = float(raw.qty)
            broker_qty = abs(raw_qty)
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
            if (
                managed is None
                and total_owned > 1e-12
                and broker_qty > 1e-12
                and abs(broker_qty - total_owned) > max(1e-9, total_owned * 1e-8)
            ):
                # The share count moved without a fill of ours: a corporate
                # action, or another consumer of the shared Alpaca key. A
                # SELL of ours still working at the broker explains a
                # shortfall and keeps ownership until it completes.
                explained = broker_qty < total_owned and self._open_order_explains_drift(
                    canonical, total_owned, broker_qty, market, sell_orders()
                )
                if not explained:
                    resolved = self._resolve_broker_quantity(canonical, market, broker_qty, raw, total_owned)
                    if resolved is not None:
                        total_owned = resolved
                    elif broker_qty < total_owned:
                        drift_candidates.append((canonical, total_owned, raw_qty, market))
            managed_qty = min(broker_qty, total_owned)
            external_qty = broker_qty - managed_qty
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
            repo_orders = sell_orders()
            for canonical, owned_qty, raw_qty, market in drift_candidates:
                if self._open_order_explains_drift(canonical, owned_qty, raw_qty, market, repo_orders):
                    continue
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: repository managed "
                    f"qty={owned_qty} exceeds broker position qty={raw_qty}. "
                    "Confirm the broker position, then adopt it explicitly with "
                    "`parayabuck ops rebase-ownership " + canonical + " --broker-qty <qty> --confirm`"
                )
        # Account-scoped peak reconciliation
        broker._ensure_peak_reconciled(broker._account_id or "unknown", equity)
        old_peak = broker._peak_equity
        broker._peak_equity = max(broker._peak_equity, equity)
        if broker._peak_equity != old_peak:
            broker._save_peak_equity()
        daily_pnl = equity - previous_equity
        cash = float(account.cash or 0)
        snapshot = AccountSnapshot(
            cash=cash,
            equity=equity,
            peak_equity=broker._peak_equity,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
            fees=(ledger.total_fees_for_account(broker._account_id or "unknown") if ledger is not None else 0.0),
        )
        broker._snapshot_cache = snapshot
        broker._snapshot_cached_at = time.monotonic()
        return snapshot
