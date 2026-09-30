"""Reconciliation — 7-bucket, idempotent, fail-closed.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

Responsibility:
- Orchestrate ``account()`` → fee/fill activity backfill (watermarked
  24 h, paginated, deduped by ``activity.id``) → ``managed =
  ledger.managed_for_account`` → holdings 7-bucket (``MATCH`` etc.) →
  ``_reconcile_stale_portfolio_ownership`` → ``_reconcile_stale_ledger_entries``
  → protective-stop (re)submission (equity ledger-managed/external positions
  only; foreign positions in the shared account are left alone).
- Idempotent: second call with same broker truth yields same buckets, no
  duplicate fills/stops.
- Foreign inventory in the shared account (a crypto holding, or an equity
  symbol outside the bot's tradable universe that no lineage claims) is
  excluded from the ownership buckets: the runtime classifies it, alerts once
  at startup and never trades or protects it. Foreign orders, and foreign
  activity on a symbol the bot does trade, stay fail-closed drift.
- Emits observability but never controls trading; monitoring failure does
  not affect reconciliation decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from parayabuck.core.models import (
    AccountSnapshot,
    ExecutionEvent,
    Market,
    OrderRole,
    OrderState,
    ProtectionState,
)
from parayabuck.execution.alpaca_models import ProtectionReconciliationError
from parayabuck.execution.ledger import FILL_ACTIVITY_RECOVERY_PREFIX
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.monitoring import alerts
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca_reconciliation")

# Every side Alpaca reports on a FILL activity. The reconciler only rebuilds
# order state for the two sides a long-only managed book produces; the rest
# are legitimately present on the shared account (a manual short, a crypto or
# bond leg) and must NOT block reconciliation. A side outside this vocabulary
# is recorded and alerted instead of being fatal.
_RECOVERABLE_FILL_SIDES = frozenset({"buy", "sell"})
_KNOWN_FILL_SIDES = frozenset({*_RECOVERABLE_FILL_SIDES, "buy_minus", "sell_plus", "sell_short", "sell_short_exempt"})

# Absolute floor for ledger-vs-broker position quantity agreement.
QUANTITY_MATCH_FLOOR = 1e-8

# A local portfolio order still ``submitting`` or ``reconciliation_required``
# whose client order id Alpaca definitively does not know (404) is closed as
# ``canceled`` once its last write is this old: far beyond any submit call,
# so an in-flight submit (reconcile does not hold the portfolio lock) can
# never be canceled under it. Younger rows keep failing closed as drift.
UNKNOWN_ORDER_CANCEL_AGE_SECONDS = 600.0
_UNKNOWN_ORDER_CANCELABLE_STATUSES = frozenset({"submitting", "reconciliation_required"})

RECONCILIATION_CATEGORIES: tuple[str, ...] = (
    "MATCH",
    "LOCAL_STALE",
    "BROKER_STALE",
    "QUANTITY_MISMATCH",
    "UNKNOWN_ORDER",
    "MISSING_LOCAL_ORDER",
    "MISSING_BROKER_ORDER",
)
RECONCILIATION_DRIFT_CATEGORIES: frozenset[str] = frozenset(
    category for category in RECONCILIATION_CATEGORIES if category != "MATCH"
)


class ReconciliationResultError(ValueError):
    """Raised when a broker reconciliation result cannot be trusted."""


@dataclass(frozen=True)
class ReconciliationReport:
    """Validated, immutable view of the broker reconciliation result."""

    categories: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def from_result(cls, result: object) -> ReconciliationReport:
        if not isinstance(result, Mapping):
            raise ReconciliationResultError("Reconciliation returned a non-mapping result")
        if set(result) != set(RECONCILIATION_CATEGORIES):
            raise ReconciliationResultError("Reconciliation result has an unexpected category shape")
        normalized: list[tuple[str, tuple[str, ...]]] = []
        for category in RECONCILIATION_CATEGORIES:
            values = result[category]
            if not isinstance(values, (list, tuple)):
                raise ReconciliationResultError(f"Reconciliation category {category} is not a list")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ReconciliationResultError(f"Reconciliation category {category} contains an invalid value")
            normalized.append((category, tuple(values)))
        return cls(tuple(normalized))

    def as_dict(self) -> dict[str, list[str]]:
        return {category: list(values) for category, values in self.categories}

    @property
    def drift_categories(self) -> dict[str, tuple[str, ...]]:
        return {
            category: values
            for category, values in self.categories
            if category in RECONCILIATION_DRIFT_CATEGORIES and values
        }

    @property
    def has_drift(self) -> bool:
        return bool(self.drift_categories)


__all__ = [
    "RECONCILIATION_CATEGORIES",
    "RECONCILIATION_DRIFT_CATEGORIES",
    "ReconciliationReport",
    "ReconciliationResultError",
    "ReconciliationService",
]


class ReconciliationService:
    """Reconciliation coordinator — production implementation."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    # -- helpers ---------------------------------------------------------

    def _field(self, value: Any, name: str, default: Any = None) -> Any:
        return self._broker._field(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return self._broker._normalized_field(value, name)  # type: ignore[no-any-return]

    def _reconcile_fill_activity_fees(self) -> bool:
        broker = self._broker
        adapter = getattr(broker, "_client_adapter", None)
        if adapter is not None:
            try:
                request = adapter.request
            except Exception:
                request = getattr(getattr(broker, "_client", None), "_request", None)
        else:
            request = getattr(getattr(broker, "_client", None), "_request", None)
        ledger = getattr(broker, "ledger", None)
        if request is None or ledger is None:
            return False
        account_id = getattr(broker, "_account_id", "") or "unknown"
        watermark = None
        watermark_getter = getattr(ledger, "get_reconcile_watermark", None)
        watermark_setter = getattr(ledger, "set_reconcile_watermark", None)
        if watermark_getter is not None:
            watermark = watermark_getter(account_id, "fill_activity")
        activities: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        seen_activity_ids: set[str] = set()
        try:
            while True:
                params: dict[str, object] = {"direction": "desc", "page_size": 100}
                if watermark is not None:
                    params["after"] = (watermark - timedelta(hours=24)).isoformat()
                if page_token is not None:
                    params["page_token"] = page_token
                payload = request("GET", "/account/activities/FILL", data=params)
                if not isinstance(payload, list) or len(payload) > 100:
                    raise ReconciliationResultError("Invalid FILL activity page")
                if not payload:
                    break
                page: list[dict[str, Any]] = []
                for item in payload:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                        raise ReconciliationResultError("FILL activity without a valid ID")
                    try:
                        side = str(item["side"]).lower()
                        qty = float(item["qty"])
                        price = float(item["price"])
                        occurred_at = datetime.fromisoformat(str(item["transaction_time"]))
                        broker_order_id = str(item["order_id"])
                        symbol = str(item["symbol"])
                        # Structural corruption is fatal. An unlinked or
                        # unexpected-side activity is NOT: it is skipped (and
                        # reported) further down, because a single manual short
                        # on the shared account used to pin the watermark and
                        # block reconciliation until an operator fixed it by
                        # hand.
                        if (
                            not isfinite(qty)
                            or qty <= 0
                            or not isfinite(price)
                            or price <= 0
                            or occurred_at.tzinfo is None
                        ):
                            raise ValueError("Invalid FILL activity fields")
                        for quantity_field in ("cum_qty", "leaves_qty"):
                            if quantity_field in item:
                                value = float(item[quantity_field])
                                if not isfinite(value) or value < 0:
                                    raise ValueError(f"Invalid {quantity_field}")
                        fee_raw = item.get("commission", item.get("fee", 0))
                        if fee_raw is not None and not isfinite(float(fee_raw)):
                            raise ValueError("Invalid FILL activity fee")
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ReconciliationResultError("Malformed FILL activity") from exc
                    page.append(item)
                next_token = page[-1]["id"]
                if next_token == page_token or next_token in seen_tokens:
                    raise ReconciliationResultError("FILL activity cursor did not advance")
                new_items: list[dict[str, Any]] = []
                for item in page:
                    if item["id"] not in seen_activity_ids:
                        new_items.append(item)
                        seen_activity_ids.add(item["id"])
                if not new_items:
                    raise ReconciliationResultError("FILL activity page made no progress")
                activities.extend(new_items)
                seen_tokens.add(next_token)
                page_token = next_token
        except Exception as exc:
            log.warning("Fill activity fee reconciliation deferred: %s", exc)
            return False
        activities.sort(key=lambda item: str(item.get("transaction_time") or item.get("date") or ""))
        recovered_buy_fills: dict[str, list[tuple[float, float, datetime, str, str]]] = {}
        # Running (quantity, notional) of the SELL fills seen per broker order
        # in time order: one order can fill in several activities, and the
        # ledger event is cumulative, so each activity must carry the sum.
        recovered_sell_totals: dict[str, tuple[float, float]] = {}
        applied_watermark: datetime | None = None
        # (activity_id, side, symbol, reason) for the activities this pass
        # deliberately did not apply. Reported, never fatal.
        skipped: list[tuple[str, str, str, str]] = []
        for activity in activities:
            fee_raw = activity.get("commission", activity.get("fee", 0))
            try:
                fee = abs(float(fee_raw or 0))
            except (TypeError, ValueError):
                continue
            timestamp_raw = activity.get("transaction_time") or activity.get("date") or datetime.now(UTC).isoformat()
            try:
                occurred_at = datetime.fromisoformat(str(timestamp_raw))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
            except ValueError:
                occurred_at = datetime.now(UTC)
            activity_id = str(activity.get("id", "") or "")
            broker_order_id = str(activity.get("order_id", "") or "")
            side = str(activity.get("side", "") or "").lower().split(".")[-1]
            symbol = str(activity.get("symbol", "") or "")
            try:
                qty = abs(float(activity.get("qty", 0) or 0))
                price = float(activity.get("price", 0) or 0)
            except (TypeError, ValueError):
                qty = 0.0
                price = 0.0
            # The cursor advances for EVERY well-formed activity, skipped ones
            # included: the watermark is what stops the next pass from
            # re-reading this page, so pinning it behind one odd activity is
            # what made reconciliation fail for good.
            if activity_id and (applied_watermark is None or occurred_at > applied_watermark):
                applied_watermark = occurred_at
            if not (activity_id and broker_order_id and symbol):
                skipped.append((activity_id, side, symbol, "unlinked"))
                continue
            if side not in _RECOVERABLE_FILL_SIDES:
                # Known-but-unmanaged (a manual short on the shared account) or
                # a side outside Alpaca's vocabulary: no order state is
                # rebuilt, and an unknown side is surfaced to the operator.
                skipped.append(
                    (activity_id, side, symbol, "unexpected_side" if side not in _KNOWN_FILL_SIDES else "side")
                )
                continue
            if side == "sell" and qty > 0 and price > 0:
                market = Market.CRYPTO if "/" in symbol else Market.EQUITY
                order_lookup = getattr(ledger, "order_for_broker_id", None)
                existing_client = (
                    order_lookup(broker._account_id or "unknown", broker_order_id) if order_lookup is not None else None
                )
                recovery_client_id = f"{FILL_ACTIVITY_RECOVERY_PREFIX}{broker_order_id}"
                previous_qty, previous_notional = recovered_sell_totals.get(broker_order_id, (0.0, 0.0))
                cumulative_qty = previous_qty + qty
                cumulative_notional = previous_notional + qty * price
                recovered_sell_totals[broker_order_id] = (cumulative_qty, cumulative_notional)
                if existing_client is None or existing_client == recovery_client_id:
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            account_id=broker._account_id or "unknown",
                            client_order_id=recovery_client_id,
                            broker_order_id=broker_order_id,
                            parent_order_id=None,
                            role=OrderRole.MANUAL_EXIT,
                            symbol=canonical_symbol(symbol, market),
                            market=market,
                            state=OrderState.FILLED,
                            requested_qty=cumulative_qty,
                            cumulative_filled_qty=cumulative_qty,
                            average_fill_price=cumulative_notional / cumulative_qty,
                            occurred_at=occurred_at,
                            fee=fee,
                            activity_id=activity_id,
                        )
                    )
            if side == "buy" and qty > 0 and price > 0:
                recovered_buy_fills.setdefault(broker_order_id, []).append(
                    (qty, price, occurred_at, activity_id, symbol)
                )
            applied = ledger.apply_fee_activity(
                broker._account_id or "unknown",
                activity_id=activity_id,
                broker_order_id=broker_order_id,
                fee=fee,
                occurred_at=occurred_at,
            )
            if applied:
                log.info("Fee activity applied: broker_order_id=%s fee=%s", activity.get("order_id"), fee)
        entry_lookup = getattr(ledger, "entry_order_for_broker_id", None)
        if entry_lookup is not None:
            for broker_order_id, fills in recovered_buy_fills.items():
                recovered = entry_lookup(broker._account_id or "unknown", broker_order_id)
                if recovered is None:
                    continue
                order, known_cumulative = recovered
                observed_qty = sum(item[0] for item in fills)
                cumulative = min(order.qty, max(known_cumulative, observed_qty))
                if cumulative <= known_cumulative:
                    continue
                observed_notional = sum(item[0] * item[1] for item in fills)
                average_price = observed_notional / observed_qty
                latest = max(fills, key=lambda item: item[2])
                state = (
                    OrderState.FILLED
                    if cumulative >= order.qty - max(1e-9, order.qty * 1e-8)
                    else OrderState.PARTIALLY_FILLED
                )
                ledger.apply_execution_event(
                    ExecutionEvent(
                        account_id=broker._account_id or "unknown",
                        client_order_id=order.client_order_id,
                        broker_order_id=broker_order_id,
                        parent_order_id=None,
                        role=OrderRole.ENTRY,
                        symbol=order.symbol,
                        market=order.market,
                        state=state,
                        requested_qty=order.qty,
                        cumulative_filled_qty=cumulative,
                        average_fill_price=average_price,
                        occurred_at=latest[2],
                        activity_id=latest[3],
                    )
                )
                log.warning(
                    "Entry fill recovered from account activity: market=%s symbol=%s entry_client_order_id=%s broker_order_id=%s cumulative_qty=%s",
                    order.market.value,
                    order.symbol,
                    order.client_order_id,
                    broker_order_id,
                    cumulative,
                )
        if skipped:
            unexpected = [item for item in skipped if item[3] == "unexpected_side"]
            log.warning(
                "FILL activities skipped without order-state recovery: %d (%d unlinked, %d side not recoverable)",
                len(skipped),
                sum(1 for item in skipped if item[3] == "unlinked"),
                len(skipped) - sum(1 for item in skipped if item[3] == "unlinked"),
            )
            if unexpected:
                # A side outside Alpaca's FILL vocabulary: the reconciler will
                # keep skipping it, so the operator is told rather than
                # discovering it through a permanently failing reconcile.
                try:
                    alerts.emit(
                        "reconciliation_unexpected_fill_side",
                        message=(
                            f"{len(unexpected)} broker FILL activity(ies) carry a side outside Alpaca's "
                            "vocabulary; they are skipped, not applied, and the reconcile cursor advances"
                        ),
                        count=len(unexpected),
                        activities=[
                            {"activity_id": activity_id, "side": side, "symbol": symbol}
                            for activity_id, side, symbol, _reason in unexpected[:20]
                        ],
                    )
                except Exception:  # observability must never break reconciliation
                    log.warning("Unexpected FILL side alert failed", exc_info=True)
        if watermark_setter is not None and applied_watermark is not None:
            watermark_setter(account_id, "fill_activity", applied_watermark)
        return True

    def _recover_portfolio_orders(self, broker_orders: Any) -> None:
        """Resolve durable nonterminal orders before comparing ownership."""
        broker = self._broker
        provider = getattr(broker, "portfolio_open_orders", None)
        if provider is None:
            return
        open_by_client: dict[str, Any] = {}
        for order in broker_orders:
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            if not client_id or client_id in open_by_client:
                raise ReconciliationResultError("Broker OPEN list has missing or duplicate client ID")
            open_by_client[client_id] = order
        lookup = getattr(broker, "_fetch_broker_order", None)
        callback = getattr(broker, "portfolio_fill_recovered", None)
        for local in provider() or ():
            client_id = str(getattr(local, "client_order_id", "") or "").strip()
            if not client_id:
                raise ReconciliationResultError("Local nonterminal order has no client ID")
            order = open_by_client.get(client_id)
            if order is None:
                if lookup is None:
                    continue  # The final OPEN-ID comparison still fails closed.
                order = lookup(client_id, str(getattr(local, "broker_order_id", "") or ""))
            if order is None:
                # A targeted 404 alone is not evidence of a terminal outcome;
                # only an aged, never-acknowledged row is closed.
                self._cancel_unknown_portfolio_order(local, client_id, lookup, callback)
                continue
            actual_client_id = str(self._field(order, "client_order_id", "") or "")
            broker_id = str(self._field(order, "id", "") or "")
            if actual_client_id != client_id or not broker_id:
                raise ReconciliationResultError(f"Broker order identity mismatch for {client_id}")
            persisted_id = str(getattr(local, "broker_order_id", "") or "")
            if persisted_id and persisted_id != broker_id:
                raise ReconciliationResultError(f"Broker order ID changed for {client_id}")
            symbol = canonical_symbol(str(self._field(order, "symbol", "") or ""), local.market)
            side = str(self._normalized_field(order, "side") or "").lower()
            if symbol != canonical_symbol(local.symbol, local.market) or side != str(local.side).lower():
                raise ReconciliationResultError(f"Broker order contract mismatch for {client_id}")
            status = str(self._normalized_field(order, "status") or "").lower()
            if status == "partially_filled":
                local_status = "partial_fill"
            elif status in {"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"}:
                local_status = status
            elif status in {
                "new",
                "accepted",
                "pending_new",
                "pending_cancel",
                "pending_replace",
                "accepted_for_bidding",
            }:
                local_status = status
            else:
                raise ReconciliationResultError(f"Unrecognized broker order status for {client_id}: {status}")
            quantity = float(self._field(order, "filled_qty", 0) or 0)
            average_raw = self._field(order, "filled_avg_price", None)
            average = float(average_raw) if average_raw is not None else None
            local_quantity = float(local.quantity)
            if isfinite(quantity) and quantity > local_quantity + max(1e-9, local_quantity * 1e-8):
                local_quantity = self._adopt_broker_order_quantity(broker, order, client_id, quantity, local_quantity)
            if (
                not isfinite(quantity)
                or (average is not None and not isfinite(average))
                or quantity < 0
                or quantity > local_quantity + max(1e-9, local_quantity * 1e-8)
            ):
                raise ReconciliationResultError(f"Invalid broker cumulative fill for {client_id}")
            if (quantity > 0 and (average is None or average <= 0)) or (status == "filled" and quantity <= 0):
                raise ReconciliationResultError(f"Incomplete broker fill for {client_id}")
            if callback is None:
                raise ReconciliationResultError(f"Portfolio order recovery unavailable for {client_id}")
            timestamp_raw = self._field(order, "filled_at", None) or self._field(order, "updated_at", None)
            timestamp = (
                timestamp_raw
                if isinstance(timestamp_raw, datetime)
                else datetime.fromisoformat(str(timestamp_raw))
                if timestamp_raw is not None
                else datetime.now(UTC)
            )
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            callback(
                client_id,
                broker_id,
                quantity,
                average,
                timestamp,
                f"{client_id}:recovered:{quantity}:{local_status}",
                None,
                status=local_status,
            )

    def _cancel_unknown_portfolio_order(self, local: Any, client_id: str, lookup: Any, callback: Any) -> None:
        """Close a never-accepted portfolio order the broker does not know.

        Called after the targeted lookup returned 404. Only a row still
        ``submitting`` or ``reconciliation_required``, whose last write is at
        least ``UNKNOWN_ORDER_CANCEL_AGE_SECONDS`` old, is booked ``canceled``
        with a zero fill, and only when the lookup went through a client-id
        query (a missing query proves nothing). Anything else is unchanged and
        the final OPEN-ID comparison still reports it as drift.
        """
        status = str(getattr(local, "status", "") or "").lower()
        updated_at = getattr(local, "last_updated_at", None)
        client = getattr(self._broker, "_client", None)
        if (
            status not in _UNKNOWN_ORDER_CANCELABLE_STATUSES
            or not isinstance(updated_at, datetime)
            or lookup is None
            or callback is None
            or getattr(client, "get_order_by_client_id", None) is None
        ):
            return
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - updated_at).total_seconds()
        if age < UNKNOWN_ORDER_CANCEL_AGE_SECONDS:
            return
        callback(
            client_id,
            str(getattr(local, "broker_order_id", "") or ""),
            0.0,
            None,
            datetime.now(UTC),
            f"{client_id}:unknown_at_broker:canceled",
            None,
            status="canceled",
        )
        log.warning(
            "Portfolio order unknown at the broker canceled: client_order_id=%s status=%s age_seconds=%d",
            client_id,
            status,
            round(age),
        )
        try:
            from parayabuck.monitoring import alerts

            # One incident per order: the row is terminal afterwards, and the
            # alert throttle keys paper_incident by its incident id.
            alerts.emit(
                "paper_incident",
                message="Portfolio order never accepted by the broker was canceled",
                category="unknown_order_canceled",
                incident_id=f"unknown_order_canceled:{client_id}",
                client_order_id=client_id,
                previous_status=status,
                age_seconds=round(age),
            )
        except Exception:
            log.warning("Alert emission failed for unknown order %s", client_id, exc_info=True)

    def _adopt_broker_order_quantity(
        self, broker: Any, order: Any, client_id: str, filled: float, local_quantity: float
    ) -> float:
        """Adopt the broker's ordered quantity for one of our own orders.

        A deterministic client ID can be recovered (Alpaca rejects the reused
        ID and the existing order is returned) after the plan was re-sized, so
        the local row may record less than the broker actually ordered. Identity,
        symbol and side were already verified; the broker is authoritative, so
        the local row takes the broker's ordered quantity when it covers the
        cumulative fill. Anything else stays fail-closed.
        """
        adopt = getattr(broker, "portfolio_order_quantity_adopted", None)
        ordered_raw = self._field(order, "qty", None)
        try:
            ordered = float(ordered_raw) if ordered_raw is not None else float("nan")
        except (TypeError, ValueError):
            ordered = float("nan")
        if adopt is None or not isfinite(ordered) or ordered <= 0 or filled > ordered + max(1e-9, ordered * 1e-8):
            return local_quantity
        adopt(client_id, ordered)
        log.warning(
            "Adopted broker ordered quantity for %s: local %.9g -> broker %.9g",
            client_id,
            local_quantity,
            ordered,
        )
        return ordered

    def _reconcile_stale_ledger_entries(
        self, ledger: Any, broker_symbols: set[str], fill_activities_reconciled: bool
    ) -> None:
        broker = self._broker
        for entry in ledger.managed_entries_for_account(broker._account_id or "unknown"):
            if entry.owned_qty <= 0 or entry.canonical_symbol in broker_symbols:
                continue
            if entry.market == Market.EQUITY and not fill_activities_reconciled:
                ledger.mark_manual_intervention(broker._account_id or "unknown", entry.entry_client_order_id)
                log.warning(
                    "Managed ledger close deferred: market=equity symbol=%s entry_client_order_id=%s recovery_action=await_fill_activity_reconciliation",
                    entry.canonical_symbol,
                    entry.entry_client_order_id,
                )
                continue
            ledger.mark_position_closed(broker._account_id or "unknown", entry.entry_client_order_id)
            log.warning(
                "Stale managed ledger entry closed: market=%s symbol=%s entry_client_order_id=%s managed_qty=%s recovery_action=broker_position_snapshot_confirmed_flat",
                entry.market.value,
                entry.canonical_symbol,
                entry.entry_client_order_id,
                entry.owned_qty,
            )

    def _bot_tradable_symbols(self) -> frozenset[str] | None:
        """Canonical symbols this bot may trade, or None when it cannot say.

        The brokerage account is shared, so the bot's own tradable universe is
        the boundary between its positions and someone else's. None (no
        provider, or an unreadable one) is fail-closed: every unowned equity
        position then stays drift, exactly as before.
        """
        provider = getattr(self._broker, "bot_tradable_symbols", None)
        if provider is None:
            return None
        try:
            return frozenset(canonical_symbol(str(symbol)) for symbol in provider() or ())
        except Exception:
            log.warning("Bot tradable symbols unavailable; equity positions stay fail-closed", exc_info=True)
            return None

    # -- public ----------------------------------------------------------

    def reconcile(self) -> dict[str, list[str]]:
        broker = self._broker
        categories: dict[str, list[str]] = {
            "MATCH": [],
            "LOCAL_STALE": [],
            "BROKER_STALE": [],
            "QUANTITY_MISMATCH": [],
            "UNKNOWN_ORDER": [],
            "MISSING_LOCAL_ORDER": [],
            "MISSING_BROKER_ORDER": [],
        }
        # Respect test monkeypatch of broker.account (e.g. fake AccountSnapshot) — call via broker so __dict__ override is observed.
        # When not monkeypatched, broker.account() delegates to AccountService anyway.
        # Bind account identity before reading account-scoped durable orders.
        # account() classifies ownership, so it must run after offline fills
        # have updated local ownership rather than rejecting an explained fill.
        if not getattr(broker, "_account_id", ""):
            raw_account = broker._client.get_account()
            broker._account_id = str(getattr(raw_account, "id", "") or getattr(raw_account, "account_number", ""))
            if not broker._account_id:
                raise ReconciliationResultError("Broker account has no identity")
        broker_orders = broker._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        self._recover_portfolio_orders(broker_orders)
        manage_external = bool(getattr(broker, "manage_external_positions", True))
        ledger = getattr(broker, "ledger", None)
        fill_activities_reconciled = True
        managed: dict[str, Any] = {}
        if ledger is not None:
            fill_activities_reconciled = self._reconcile_fill_activity_fees()
            if not fill_activities_reconciled:
                raise ReconciliationResultError("FILL activity backfill incomplete")
        account: AccountSnapshot = broker.account()
        portfolio_owned_provider = getattr(broker, "portfolio_owned_quantities", None)
        portfolio_owned = set(portfolio_owned_provider()) if portfolio_owned_provider is not None else set()
        if ledger is not None:
            managed = ledger.managed_for_account(broker._account_id or "unknown")
        broker_symbols = {canonical_symbol(p.symbol, p.market) for p in account.positions if not p.is_flat}
        # holdings 7-bucket
        _ledger_quantities: dict[str, float] = {}
        for _sym, _pos in (managed or {}).items():
            _ledger_quantities[_sym] = _ledger_quantities.get(_sym, 0.0) + float(getattr(_pos, "owned_qty", 0.0))
        _portfolio_quantities: dict[str, float] = {}
        if portfolio_owned:
            _owned_provider = getattr(broker, "portfolio_owned_quantities", None)
            if _owned_provider is not None:
                try:
                    _owned_map = _owned_provider() if callable(_owned_provider) else set()
                    if isinstance(_owned_map, dict):
                        _portfolio_quantities = {str(k): float(v) for k, v in _owned_map.items()}
                    elif isinstance(_owned_map, set):
                        for _s in _owned_map:
                            _portfolio_quantities[str(_s)] = _portfolio_quantities.get(str(_s), 0.0) + 1.0
                except Exception:
                    _portfolio_quantities = {str(s): 1.0 for s in portfolio_owned}
        _local_symbols = set(_ledger_quantities) | set(_portfolio_quantities) | set(portfolio_owned)
        _tradable_symbols = self._bot_tradable_symbols()
        _broker_quantities: dict[str, float] = {}
        # Foreign inventory — a crypto holding, or an equity symbol no bot
        # lineage claims that the strategy does not trade — is observed and
        # alerted once at startup, never ownership drift: the bot neither
        # sized nor protected it, so it can neither be reconciled nor
        # legitimately sold. It still counts toward account gross and slot
        # limits (AccountService), and it stays in ``broker_symbols`` below so
        # a legacy managed ledger row is never closed as flat while the
        # broker really holds it. Foreign activity on a symbol the bot DOES
        # trade keeps today's fail-closed drift semantics.
        _foreign_positions: dict[str, str] = {}
        for _p in account.positions:
            if getattr(_p, "is_flat", False):
                continue
            _market = getattr(_p, "market", Market.EQUITY)
            _canon = canonical_symbol(getattr(_p, "symbol", ""), _market)
            if _market != Market.EQUITY or (
                _tradable_symbols is not None and _canon not in _local_symbols and _canon not in _tradable_symbols
            ):
                _foreign_positions[_canon] = _market.value
                continue
            _broker_quantities[_canon] = _broker_quantities.get(_canon, 0.0) + abs(float(getattr(_p, "qty", 0.0)))
        if _foreign_positions:
            log.warning(
                "Foreign positions excluded from ownership reconciliation: %s",
                ", ".join(f"{symbol} ({market})" for symbol, market in sorted(_foreign_positions.items())),
            )
        _all_symbols = _local_symbols | set(_broker_quantities)
        for _sym in sorted(_all_symbols):
            if _sym in _ledger_quantities:
                _local_qty = float(_ledger_quantities[_sym])
            elif _sym in _portfolio_quantities:
                _local_qty = float(_portfolio_quantities[_sym])
            else:
                _local_qty = 0.0
            _broker_qty = float(_broker_quantities.get(_sym, 0.0))
            if _local_qty > 1e-12 and _broker_qty > 1e-12:
                # Alpaca reports quantities at 9 decimals, so ledger quantities
                # can differ from the broker by a few units of the 9th decimal.
                _tol = max(QUANTITY_MATCH_FLOOR, _local_qty * 1e-8)
                if abs(_local_qty - _broker_qty) <= _tol:
                    categories["MATCH"].append(_sym)
                else:
                    categories["QUANTITY_MISMATCH"].append(_sym)
            elif _local_qty > 1e-12 and _broker_qty <= 1e-12:
                categories["LOCAL_STALE"].append(_sym)
                categories["MISSING_BROKER_ORDER"].append(_sym)
            elif _broker_qty > 1e-12 and _local_qty <= 1e-12:
                categories["BROKER_STALE"].append(_sym)
                categories["MISSING_LOCAL_ORDER"].append(_sym)
        if categories["QUANTITY_MISMATCH"]:
            # A share count the runtime could not prove: a non-standard
            # corporate action, or a foreign trade on the shared key. Name
            # the symbols and the supported recovery — a bare bucket name
            # sends the operator to SQLite, which the runbook forbids.
            log.warning(
                "Ownership quantity mismatch needs an operator rebase: %s. Confirm the broker "
                "position, then `parayabuck ops rebase-ownership SYMBOL --broker-qty QTY --confirm`.",
                ", ".join(categories["QUANTITY_MISMATCH"]),
            )
        if portfolio_owned and (flat_reconciler := getattr(broker, "portfolio_flat_reconciled", None)):
            for symbol in portfolio_owned:
                if symbol in broker_symbols:
                    continue
                market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
                if broker._reconcile_stale_portfolio_ownership(symbol, market):
                    continue
                flat_reconciler(symbol, market)
        if ledger is not None:
            self._reconcile_stale_ledger_entries(ledger, broker_symbols, fill_activities_reconciled)
        # protection reconciliation — delegated
        open_orders: Any = broker_orders
        for position in account.positions:
            is_long = position.qty > 0
            if position.is_flat:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            if position.market != Market.EQUITY:
                # Crypto support was removed: a crypto holding in the shared
                # account is never protected or traded by this runtime.
                log.info("Non-equity position left unchanged during reconciliation: %s", symbol)
                continue
            ownership = managed.get(symbol)
            if ownership is None and not manage_external:
                log.info("External position left unchanged during reconciliation: %s", symbol)
                continue
            if ownership is not None:
                stop_price = getattr(ownership, "current_stop", None) or ownership.original_stop
                total_qty = min(abs(position.qty), ownership.owned_qty)
            else:
                total_qty = abs(position.qty)
                stop_price = position.avg_price * (1 - 0.04 if is_long else 1 + 0.04)
            available_qty = abs(position.qty_available) if position.qty_available is not None else total_qty
            required_qty = total_qty
            # use broker's has_protective check (delegates to ProtectionService when wired)
            has_protection = broker._has_protective_order(position.symbol, is_long, required_qty, open_orders)
            if not has_protection and ownership is not None and ownership.broker_order_id and available_qty <= 0:
                parent_order_ids: str | set[str] = ownership.broker_order_id
                if ledger is not None:
                    parent_order_ids = ledger.managed_entry_broker_order_ids(broker._account_id or "unknown", symbol)
                has_protection = broker._has_nested_bracket_protection(
                    parent_order_ids, position.symbol, is_long, required_qty
                )
            if has_protection:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        broker._account_id or "unknown", ownership.entry_client_order_id, ProtectionState.ACTIVE
                    )
                continue
            mismatch = getattr(broker, "_last_protection_mismatch", "missing")
            if mismatch in {"under", "over"}:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        broker._account_id or "unknown", ownership.entry_client_order_id, ProtectionState.REQUESTED
                    )
                if mismatch == "over":
                    broker._cancel_protective_orders(position.symbol, is_long)
            qty = min(total_qty, available_qty)
            if mismatch == "under":
                protected_qty = float(getattr(broker, "_last_protected_qty", 0.0))
                qty = min(qty, max(0.0, required_qty - protected_qty))
            if qty <= 0:
                raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            # unique stop id
            from uuid import uuid4

            protective_client_id = (
                f"{ownership.entry_client_order_id}-stop-{uuid4().hex[:8]}" if ownership is not None else None
            )
            accepted_id = broker._submit_stop(
                position.symbol, position.market, qty, stop_price, is_long, client_order_id=protective_client_id
            )
            if accepted_id is None:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        broker._account_id or "unknown", ownership.entry_client_order_id, ProtectionState.FAILED
                    )
                    ledger.mark_manual_intervention(broker._account_id or "unknown", ownership.entry_client_order_id)
                raise ProtectionReconciliationError(f"Could not restore managed protection for {position.symbol}")
            if ownership is not None and ledger is not None:
                ledger.set_protection_state(
                    broker._account_id or "unknown", ownership.entry_client_order_id, ProtectionState.ACTIVE
                )
        _local_open_ids: set[str] = set()
        _local_provider = getattr(broker, "portfolio_open_orders", None)
        if _local_provider is not None:
            for _o in _local_provider() or ():
                _cid = str(getattr(_o, "client_order_id", "") or "").strip()
                if not _cid:
                    raise ReconciliationResultError("Local nonterminal order has no client ID")
                _local_open_ids.add(_cid)
        # Protection recovery above may have submitted a new owned stop;
        # compare against a fresh exhaustive list, not the pre-repair page set.
        current_broker_orders = broker._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        _broker_open_ids: set[str] = set()
        for _bo in current_broker_orders or ():
            _bcid = str(getattr(_bo, "client_order_id", "") or self._field(_bo, "client_order_id", "")).strip()
            if not _bcid or _bcid in _broker_open_ids:
                raise ReconciliationResultError("Broker OPEN list has missing or duplicate client ID")
            _broker_open_ids.add(_bcid)
        for _cid in sorted(_broker_open_ids - _local_open_ids):
            categories["UNKNOWN_ORDER"].append(_cid)
        for _cid in sorted(_local_open_ids - _broker_open_ids):
            categories["MISSING_BROKER_ORDER"].append(_cid)
            # Documented compatibility contract: a local open id the broker no
            # longer lists is reported under both keys (see
            # the reconciliation buckets in docs/execution_architecture.md and
            # the recovery steps in docs/operations.md).
            categories["MISSING_LOCAL_ORDER"].append(_cid)
        return categories
