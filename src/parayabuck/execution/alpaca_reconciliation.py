"""Reconciliation — 7-bucket, idempotent, fail-closed.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8).

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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca_reconciliation")

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
        try:
            while True:
                params: dict[str, object] = {"direction": "desc", "page_size": 100}
                if watermark is not None:
                    params["after"] = (watermark - timedelta(hours=24)).isoformat()
                if page_token is not None:
                    params["page_token"] = page_token
                payload = request("GET", "/account/activities/FILL", data=params)
                page = payload if isinstance(payload, list) else []
                activities.extend(item for item in page if isinstance(item, dict))
                if len(page) < 100:
                    break
                next_token = str(page[-1].get("id", "") or "")
                if not next_token or next_token in seen_tokens:
                    log.warning(
                        "Fill activity pagination stopped at %d activities: page cursor was not honored",
                        len(activities),
                    )
                    break
                seen_tokens.add(next_token)
                page_token = next_token
        except Exception as exc:
            log.warning("Fill activity fee reconciliation deferred: %s", exc)
            return False
        activities.sort(key=lambda item: str(item.get("transaction_time") or item.get("date") or ""))
        recovered_buy_fills: dict[str, list[tuple[float, float, datetime, str, str]]] = {}
        applied_watermark: datetime | None = None
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
            if activity_id and broker_order_id and symbol and side == "sell" and qty > 0 and price > 0:
                market = Market.CRYPTO if "/" in symbol else Market.EQUITY
                order_lookup = getattr(ledger, "order_for_broker_id", None)
                existing_client = (
                    order_lookup(broker._account_id or "unknown", broker_order_id) if order_lookup is not None else None
                )
                if existing_client is not None:
                    fee_recorder = getattr(ledger, "record_fee_activity", None)
                    if fee_recorder is not None:
                        fee_recorder(activity_id, existing_client, fee, occurred_at)
                else:
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            account_id=broker._account_id or "unknown",
                            client_order_id=f"alpaca-fill-activity-{broker_order_id}",
                            broker_order_id=broker_order_id,
                            parent_order_id=None,
                            role=OrderRole.MANUAL_EXIT,
                            symbol=canonical_symbol(symbol, market),
                            market=market,
                            state=OrderState.FILLED,
                            requested_qty=qty,
                            cumulative_filled_qty=qty,
                            average_fill_price=price,
                            occurred_at=occurred_at,
                            fee=fee,
                            activity_id=activity_id,
                        )
                    )
            if activity_id and broker_order_id and symbol and side == "buy" and qty > 0 and price > 0:
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
            if applied_watermark is None or occurred_at > applied_watermark:
                applied_watermark = occurred_at
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
        if watermark_setter is not None and applied_watermark is not None:
            watermark_setter(account_id, "fill_activity", applied_watermark)
        return True

    def _reconcile_crypto_exit(self, position: Any) -> None:
        broker = self._broker
        ledger = getattr(broker, "ledger", None)
        if ledger is None:
            raise ProtectionReconciliationError(
                f"Cannot recover crypto exit for {position.canonical_symbol}: no ledger"
            )
        get_by_client_id = getattr(broker._client, "get_order_by_client_id", None)
        if get_by_client_id is None or not getattr(position, "exit_client_order_id", None):
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(f"Cannot recover crypto exit for {position.canonical_symbol}")
        try:
            order = get_by_client_id(position.exit_client_order_id)
        except Exception as exc:
            ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(
                f"Crypto exit lookup is ambiguous for {position.canonical_symbol}"
            ) from exc
        status_name = self._normalized_field(order, "status")
        state = {
            "filled": OrderState.FILLED,
            "partially_filled": OrderState.PARTIALLY_FILLED,
            "canceled": OrderState.CANCELLED,
            "cancelled": OrderState.CANCELLED,
            "rejected": OrderState.REJECTED,
            "expired": OrderState.EXPIRED,
        }.get(status_name, OrderState.ACCEPTED)
        qty = float(self._field(order, "qty", position.owned_qty) or position.owned_qty)
        filled_qty = float(self._field(order, "filled_qty", 0) or 0)
        average = self._field(order, "filled_avg_price")
        role = OrderRole.TAKE_PROFIT if "-target-exit" in position.exit_client_order_id else OrderRole.EMERGENCY_EXIT
        if filled_qty > 0 and average is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    broker._account_id,
                    position.exit_client_order_id,
                    str(self._field(order, "id", "") or ""),
                    None,
                    role,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    filled_qty,
                    float(average),
                    datetime.now(UTC),
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                broker._account_id,
                position.entry_client_order_id,
                target_state=("target_exit_filled" if role == OrderRole.TAKE_PROFIT else "emergency_exit_filled"),
            )
            return
        if state in {OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}:
            broker._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(broker._account_id, position.entry_client_order_id)
        raise ProtectionReconciliationError(f"Crypto exit remains {state.value} for {position.canonical_symbol}")

    def _reconcile_crypto_exit_state(self, ledger: Any) -> None:
        broker = self._broker
        for entry in ledger.managed_entries_for_account(broker._account_id or "unknown"):
            if entry.market != Market.CRYPTO:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
            elif entry.stop_state == "cancel_requested" and entry.target_state in {
                "target_triggered",
                "stop_failover_triggered",
            }:
                broker._cancel_stop_then_exit(
                    entry, "target" if entry.target_state == "target_triggered" else "stop_failover"
                )

    def _reconcile_stale_ledger_entries(
        self, ledger: Any, broker_symbols: set[str], fill_activities_reconciled: bool
    ) -> None:
        broker = self._broker
        for entry in ledger.managed_entries_for_account(broker._account_id or "unknown"):
            if entry.owned_qty <= 0 or entry.canonical_symbol in broker_symbols:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
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
        account: AccountSnapshot = broker.account()
        manage_external = bool(getattr(broker, "manage_external_positions", True))
        portfolio_owned_provider = getattr(broker, "portfolio_owned_quantities", None)
        portfolio_owned = set(portfolio_owned_provider()) if portfolio_owned_provider is not None else set()
        ledger = getattr(broker, "ledger", None)
        fill_activities_reconciled = True
        if ledger is not None:
            fill_activities_reconciled = self._reconcile_fill_activity_fees()
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
        _broker_quantities: dict[str, float] = {}
        for _p in account.positions:
            if not getattr(_p, "is_flat", False):
                _canon = canonical_symbol(getattr(_p, "symbol", ""), getattr(_p, "market", Market.EQUITY))
                _broker_quantities[_canon] = _broker_quantities.get(_canon, 0.0) + abs(float(getattr(_p, "qty", 0.0)))
        _local_symbols = set(_ledger_quantities) | set(_portfolio_quantities) | set(portfolio_owned)
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
                _tol = max(1e-9, _local_qty * 1e-8)
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
        if portfolio_owned and (flat_reconciler := getattr(broker, "portfolio_flat_reconciled", None)):
            for symbol in portfolio_owned:
                if symbol in broker_symbols:
                    continue
                market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
                if broker._reconcile_stale_portfolio_ownership(symbol, market):
                    continue
                flat_reconciler(symbol, market)
        if ledger is not None:
            self._reconcile_crypto_exit_state(ledger)
            self._reconcile_stale_ledger_entries(ledger, broker_symbols, fill_activities_reconciled)
        # protection reconciliation — delegated
        open_orders: Any = None
        get_open_orders = getattr(broker, "_get_open_orders", None)
        for position in account.positions:
            is_long = position.qty > 0
            if position.is_flat:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            ownership = managed.get(symbol)
            portfolio_owned_position = position.market == Market.CRYPTO and symbol in portfolio_owned
            if ownership is None and not portfolio_owned_position and not manage_external:
                log.info("External position left unchanged during reconciliation: %s", symbol)
                continue
            broker_qty_is_fee_adjusted = False
            if ownership is not None:
                tolerance = max(1e-9, ownership.owned_qty * 1e-8)
                minimum_fee_adjusted_qty = ownership.owned_qty
                if position.market == Market.CRYPTO:
                    from parayabuck.execution.alpaca_models import CRYPTO_TAKER_FEE_RESERVE

                    minimum_fee_adjusted_qty *= float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE)
                if position.market == Market.CRYPTO and abs(position.qty) + tolerance < minimum_fee_adjusted_qty:
                    if ledger is not None:
                        ledger.mark_manual_intervention(
                            broker._account_id or "unknown", ownership.entry_client_order_id
                        )
                    raise ProtectionReconciliationError(
                        f"Broker quantity is below managed ledger quantity for {symbol}"
                    )
                if position.market == Market.CRYPTO and abs(position.qty) + tolerance < ownership.owned_qty:
                    broker_qty_is_fee_adjusted = True
                    log.info(
                        "Accepted fee-adjusted crypto ownership: symbol=%s ledger_qty=%s broker_qty=%s",
                        symbol,
                        ownership.owned_qty,
                        abs(position.qty),
                    )
                stop_price = getattr(ownership, "current_stop", None) or ownership.original_stop
                total_qty = min(abs(position.qty), ownership.owned_qty)
            else:
                if portfolio_owned_position:
                    stop_pct = broker.crypto_emergency_exit_bps / 10_000
                    total_qty = min(abs(position.qty), position.managed_qty)
                else:
                    stop_pct = 0.025 if position.market == Market.CRYPTO else 0.04
                    total_qty = abs(position.qty)
                stop_price = position.avg_price * (1 - stop_pct if is_long else 1 + stop_pct)
            available_qty = abs(position.qty_available) if position.qty_available is not None else total_qty
            required_qty = total_qty
            if position.market == Market.CRYPTO and position.qty_available == 0 and not broker_qty_is_fee_adjusted:
                from parayabuck.execution.alpaca_models import CRYPTO_TAKER_FEE_RESERVE

                required_qty = total_qty * float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE)
                required_qty = broker._round_qty_down(position.symbol, required_qty)
            if open_orders is None:
                if get_open_orders is None:
                    raise RuntimeError("Alpaca broker does not support exhaustive open-order retrieval")
                open_orders = get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
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
                    protected_qty = float(getattr(broker, "_last_protected_qty", 0.0))
                    if position.market == Market.CRYPTO and protected_qty <= total_qty + max(1e-9, total_qty * 1e-8):
                        if ownership is not None and ledger is not None:
                            ledger.set_protection_state(
                                broker._account_id or "unknown", ownership.entry_client_order_id, ProtectionState.ACTIVE
                            )
                        continue
                    broker._cancel_protective_orders(position.symbol, is_long)
            qty = min(total_qty, available_qty)
            if mismatch == "under":
                protected_qty = float(getattr(broker, "_last_protected_qty", 0.0))
                qty = min(qty, max(0.0, required_qty - protected_qty))
            if position.market == Market.CRYPTO:
                qty = broker._available_position_qty(position.symbol, qty, reserve_fee_on_lookup_failure=False)
                if qty <= 0:
                    raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            elif qty <= 0:
                raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            # unique stop id
            from uuid import uuid4

            protective_client_id = (
                f"{ownership.entry_client_order_id}-stop-{uuid4().hex[:8]}"
                if ownership is not None
                else f"parayabuck-stop-{uuid4().hex}"
                if portfolio_owned_position
                else None
            )
            accepted_id = broker._submit_stop(
                position.symbol, position.market, qty, stop_price, is_long, client_order_id=protective_client_id
            )
            if (
                accepted_id is not None
                and portfolio_owned_position
                and (stop_recorder := getattr(broker, "portfolio_stop_recorded", None)) is not None
            ):
                stop_recorder(position.symbol, position.market, qty, accepted_id, stop_price)
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
            try:
                for _o in _local_provider() or ():
                    _cid = str(getattr(_o, "client_order_id", "")).strip()
                    if _cid:
                        _local_open_ids.add(_cid)
            except Exception:
                pass
        _broker_orders = broker._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        _broker_open_ids: set[str] = set()
        for _bo in _broker_orders or ():
            _bcid = str(getattr(_bo, "client_order_id", "") or self._field(_bo, "client_order_id", "")).strip()
            if _bcid:
                _broker_open_ids.add(_bcid)
        for _cid in sorted(_broker_open_ids - _local_open_ids):
            categories["UNKNOWN_ORDER"].append(_cid)
        for _cid in sorted(_local_open_ids - _broker_open_ids):
            categories["MISSING_BROKER_ORDER"].append(_cid)
            categories["MISSING_LOCAL_ORDER"].append(_cid)
        return categories
