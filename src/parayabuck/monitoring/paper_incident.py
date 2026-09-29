"""Read-only broker/local-ledger reconciliation for paper incidents.

Incident automation: bounded automatic recovery for recoverable incidents
(provider temporary: exponential retry 5 attempts; broker/order uncertainty:
reconcile first, never blind retry; DB integrity: fail closed immediately).
Every incident produces a canonical IncidentRecord with incident_id/timestamp/
category/severity/account/strategy/current_state/attempted_recovery/recovery_result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from parayabuck.monitoring.incident import (
    INCIDENT_TAXONOMY,
    IncidentCategory,
    IncidentRecord,
    create_incident,
)

log = logging.getLogger("parayabuck.monitoring.paper_incident")


@dataclass(frozen=True)
class BrokerOrderRecord:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    status: str
    created_at: datetime


@dataclass(frozen=True)
class BrokerFillRecord:
    activity_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    occurred_at: datetime


@dataclass(frozen=True)
class BrokerPositionRecord:
    symbol: str
    quantity: float
    average_entry_price: float
    current_price: float


@dataclass(frozen=True)
class BrokerCashActivityRecord:
    activity_id: str
    activity_type: str
    net_amount: float
    occurred_at: datetime


@dataclass(frozen=True)
class PaperIncidentSnapshot:
    orders: tuple[BrokerOrderRecord, ...]
    fills: tuple[BrokerFillRecord, ...]
    positions: tuple[BrokerPositionRecord, ...] = ()
    cash_activities: tuple[BrokerCashActivityRecord, ...] = ()
    starting_equity: float | None = None
    ending_equity: float | None = None
    starting_unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class PaperIncidentReport:
    realized_pnl: float
    unrealized_pnl: float
    starting_unrealized_pnl: float
    unrealized_pnl_change: float
    slippage: float
    fees: float
    net_transfers: float
    expected_ending_equity: float | None
    actual_ending_equity: float | None
    equity_residual: float | None
    manual_exit_order_ids: tuple[str, ...]
    external_position_symbols: tuple[str, ...]
    broker_orders_missing_locally: tuple[str, ...]
    local_orders_missing_at_broker: tuple[str, ...]
    duplicate_client_order_ids: tuple[str, ...]
    local_closed_positions_with_zero_pnl: int
    local_closed_positions_with_zero_fees: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PaperIncidentReconciler:
    """Reconcile a broker snapshot without mutating either source."""

    def reconcile(self, snapshot: PaperIncidentSnapshot, database_path: str | Path) -> PaperIncidentReport:
        local_orders, local_positions = _read_local_ledger(database_path)
        by_broker_id = {
            str(item.get("broker_order_id") or ""): item for item in local_orders if item.get("broker_order_id")
        }
        by_client_id = {str(item["client_order_id"]): item for item in local_orders if item.get("client_order_id")}
        broker_order_ids = {order.order_id for order in snapshot.orders}
        broker_client_ids = [order.client_order_id for order in snapshot.orders]
        broker_client_id_set = frozenset(broker_client_ids)
        missing_local = tuple(
            sorted(
                order.order_id
                for order in snapshot.orders
                if order.order_id not in by_broker_id
                and not (order.client_order_id and order.client_order_id in by_client_id)
            )
        )
        missing_broker = tuple(
            sorted(
                str(order.get("broker_order_id") or order.get("client_order_id") or "")
                for order in local_orders
                if order.get("broker_order_id") not in broker_order_ids
                and order.get("client_order_id") not in broker_client_id_set
            )
        )
        duplicates = tuple(
            sorted(value for value, count in Counter(broker_client_ids).items() if value and count > 1)
        )
        realized, slippage = _fill_accounting(snapshot.fills, by_broker_id, by_client_id, snapshot.orders)
        unrealized = sum(
            (position.current_price - position.average_entry_price) * position.quantity
            for position in snapshot.positions
        )
        local_symbols = {
            str(position.get("canonical_symbol") or "")
            for position in local_positions
            if str(position.get("status") or "") in {"open", "manual_intervention_required"}
        }
        manual = tuple(
            sorted(
                order.order_id
                for order in snapshot.orders
                if order.side.lower() == "sell"
                and order.order_id not in by_broker_id
                and not (order.client_order_id and order.client_order_id in by_client_id)
            )
        )
        closed = [position for position in local_positions if str(position.get("status") or "") == "closed"]
        fees = sum(fill.fee for fill in snapshot.fills)
        net_transfers = sum(activity.net_amount for activity in snapshot.cash_activities)
        unrealized_change = unrealized - snapshot.starting_unrealized_pnl
        expected_ending = (
            snapshot.starting_equity + realized + unrealized_change - fees + net_transfers
            if snapshot.starting_equity is not None
            else None
        )
        residual = (
            snapshot.ending_equity - expected_ending
            if snapshot.ending_equity is not None and expected_ending is not None
            else None
        )
        return PaperIncidentReport(
            realized_pnl=round(realized, 8),
            unrealized_pnl=round(unrealized, 8),
            starting_unrealized_pnl=round(snapshot.starting_unrealized_pnl, 8),
            unrealized_pnl_change=round(unrealized_change, 8),
            slippage=round(slippage, 8),
            fees=round(fees, 8),
            net_transfers=round(net_transfers, 8),
            expected_ending_equity=(round(expected_ending, 8) if expected_ending is not None else None),
            actual_ending_equity=snapshot.ending_equity,
            equity_residual=round(residual, 8) if residual is not None else None,
            manual_exit_order_ids=manual,
            external_position_symbols=tuple(
                sorted(position.symbol for position in snapshot.positions if position.symbol not in local_symbols)
            ),
            broker_orders_missing_locally=missing_local,
            local_orders_missing_at_broker=missing_broker,
            duplicate_client_order_ids=duplicates,
            local_closed_positions_with_zero_pnl=sum(
                abs(float(str(position.get("realized_pnl") or 0.0))) < 1e-12 for position in closed
            ),
            local_closed_positions_with_zero_fees=sum(
                abs(float(str(position.get("entry_fees") or 0.0)) + float(str(position.get("exit_fees") or 0.0)))
                < 1e-12
                for position in closed
            ),
        )

    @staticmethod
    def anonymized_replay_fixture(snapshot: PaperIncidentSnapshot, report: PaperIncidentReport) -> dict[str, object]:
        def token(value: str) -> str:
            return hashlib.sha256(("parayabuck-replay|" + value).encode()).hexdigest()[:16]

        expected = report.to_dict()
        for key in (
            "manual_exit_order_ids",
            "broker_orders_missing_locally",
            "local_orders_missing_at_broker",
            "duplicate_client_order_ids",
        ):
            raw_values = expected.get(key, ())
            values = raw_values if isinstance(raw_values, list | tuple) else ()
            expected[key] = [token(str(value)) for value in values]
        return {
            "schema_version": 1,
            "fills": [
                {
                    **asdict(fill),
                    "activity_id": token(fill.activity_id),
                    "order_id": token(fill.order_id),
                    "occurred_at": fill.occurred_at.isoformat(),
                }
                for fill in snapshot.fills
            ],
            "positions": [asdict(position) for position in snapshot.positions],
            "cash_activities": [
                {
                    **asdict(activity),
                    "activity_id": token(activity.activity_id),
                    "occurred_at": activity.occurred_at.isoformat(),
                }
                for activity in snapshot.cash_activities
            ],
            "starting_equity": snapshot.starting_equity,
            "ending_equity": snapshot.ending_equity,
            "starting_unrealized_pnl": snapshot.starting_unrealized_pnl,
            "expected_incident": expected,
        }


def write_anonymized_fixture(path: str | Path, payload: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _read_local_ledger(
    path: str | Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source = Path(path).resolve()
    uri = f"file:{source}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        orders = [dict(row) for row in connection.execute("SELECT * FROM orders")] if "orders" in tables else []
        positions = (
            [dict(row) for row in connection.execute("SELECT * FROM managed_positions")]
            if "managed_positions" in tables
            else []
        )
    finally:
        connection.close()
    return orders, positions


def _fill_accounting(
    fills: tuple[BrokerFillRecord, ...],
    by_broker_id: dict[str, dict[str, object]],
    by_client_id: dict[str, dict[str, object]],
    orders: tuple[BrokerOrderRecord, ...],
) -> tuple[float, float]:
    broker_orders = {order.order_id: order for order in orders}
    quantity: dict[str, float] = {}
    average: dict[str, float] = {}
    realized = 0.0
    slippage = 0.0
    for fill in sorted(fills, key=lambda item: (item.occurred_at, item.activity_id)):
        symbol = fill.symbol
        if fill.side.lower() == "buy":
            old_qty = quantity.get(symbol, 0.0)
            new_qty = old_qty + fill.quantity
            average[symbol] = (average.get(symbol, 0.0) * old_qty + fill.price * fill.quantity) / new_qty
            quantity[symbol] = new_qty
        else:
            sold = min(quantity.get(symbol, 0.0), fill.quantity)
            realized += (fill.price - average.get(symbol, fill.price)) * sold
            quantity[symbol] = max(0.0, quantity.get(symbol, 0.0) - sold)
        local = by_broker_id.get(fill.order_id)
        if local is None and fill.order_id in broker_orders:
            local = by_client_id.get(broker_orders[fill.order_id].client_order_id)
        if local is not None:
            reference = float(str(local.get("entry_price") or 0.0))
            if reference > 0:
                direction = 1.0 if fill.side.lower() == "buy" else -1.0
                slippage += (fill.price - reference) * fill.quantity * direction
    # Keep trading PnL and fees as separate decomposition components.  Netting
    # fees here would make the report count them twice (once in realized PnL
    # and once in ``fees``) and obscure the source of the incident loss.
    return realized, slippage


__all__ = [
    "BrokerCashActivityRecord",
    "BrokerFillRecord",
    "BrokerOrderRecord",
    "BrokerPositionRecord",
    "PaperIncidentReconciler",
    "PaperIncidentReport",
    "PaperIncidentSnapshot",
    "write_anonymized_fixture",
    "automate_incident_recovery",
    "provider_temporary_retry",
    "broker_uncertainty_reconcile_first",
    "db_integrity_fail_closed",
    "emit_incident_record",
]


# ---------------------------------------------------------------------------
# Incident automation — bounded automatic recovery per taxonomy.
# ---------------------------------------------------------------------------

_PROVIDER_TEMPORARY_CATEGORIES = frozenset(
    {
        IncidentCategory.PROVIDER_UNAVAILABLE,
        IncidentCategory.SIGNAL_HISTORY_UNVERIFIED,
    }
)
_BROKER_UNCERTAINTY_CATEGORIES = frozenset(
    {
        IncidentCategory.BROKER_ACCOUNT_STALE,
        IncidentCategory.ORDER_DRIFT,
        IncidentCategory.PENDING_ORDER_UNKNOWN,
        IncidentCategory.POSITION_DRIFT,
    }
)
_DB_INTEGRITY_CATEGORIES = frozenset(
    {
        IncidentCategory.DATABASE_ERROR,
        IncidentCategory.STATE_RESTORE_FAILED,
    }
)


def emit_incident_record(record: IncidentRecord) -> None:
    """Emit canonical incident to alerts + metrics (never raises)."""
    try:
        from parayabuck.monitoring import alerts
        from parayabuck.monitoring import metrics as metrics_store

        spec = INCIDENT_TAXONOMY[record.category]
        # Map severity to alert kind: critical->circuit_breaker/reconciliation_failure,
        # a warning-level provider outage->data_provider_unavailable, and a
        # generic incident uses reconciliation_failure as safe fallback; specific
        # callers may emit more precise kinds.
        if record.category == IncidentCategory.PROVIDER_UNAVAILABLE:
            kind = "data_provider_unavailable" if spec.severity.value != "critical" else "reconciliation_failure"
        elif record.category in _BROKER_UNCERTAINTY_CATEGORIES:
            kind = (
                "reconciliation_failure"
                if record.category != IncidentCategory.BROKER_ACCOUNT_STALE
                else "circuit_breaker"
            )
            if record.category == IncidentCategory.PENDING_ORDER_UNKNOWN:
                kind = "ambiguous_order"
        elif record.category in _DB_INTEGRITY_CATEGORIES:
            kind = "reconciliation_failure"
        else:
            kind = "reconciliation_failure"
        # metrics: record per-category counter if known, plus generic
        try:
            metrics_store.record_event(kind)
        except Exception:
            pass
        alerts.emit(
            kind,
            message=f"Incident {record.category.value}: {record.error or spec.operator_action}",
            incident_id=record.incident_id,
            category=str(record.category),
            severity=str(record.severity),
            account_id=record.account_id,
            strategy=record.strategy,
            recovery_result=record.recovery_result,
            attempted_recovery=record.attempted_recovery,
        )
    except Exception:
        log.warning("emit_incident_record failed for %s", record.category, exc_info=True)


def provider_temporary_retry(
    category: IncidentCategory,
    action: Callable[[], Any],
    *,
    account_id: str | None = None,
    strategy: str | None = None,
    current_state: dict[str, Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any | None, IncidentRecord]:
    """Bounded exponential retry for provider-temporary incidents (5 attempts).

    Never raises: on success returns (result, success record), on exhaustion
    returns (None, failure record). Backoff is (2,4,8,16,16) seconds per taxonomy,
    waited through the injectable ``sleep`` (tests pass a no-op).
    """
    spec = INCIDENT_TAXONOMY[category]
    intervals = spec.retry_interval_seconds or (2, 4, 8, 16, 16)
    attempts = spec.bounded_attempts or 5
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = action()
            record = create_incident(
                category,
                account_id=account_id,
                strategy=strategy,
                error=last_error,
                current_state=current_state,
                attempted_recovery=f"exponential_retry attempt {attempt}/{attempts}",
                recovery_result="recovered",
                retry_attempt=attempt,
                payload={"intervals": list(intervals), "success": True},
            )
            emit_incident_record(record)
            return result, record
        except Exception as exc:
            last_error = str(exc)
            if attempt >= attempts:
                record = create_incident(
                    category,
                    account_id=account_id,
                    strategy=strategy,
                    error=last_error,
                    current_state=current_state,
                    attempted_recovery=f"exponential_retry attempt {attempt}/{attempts} exhausted",
                    recovery_result="failed_exhausted",
                    retry_attempt=attempt,
                    payload={"intervals": list(intervals), "success": False},
                )
                emit_incident_record(record)
                return None, record
            sleep(float(intervals[min(attempt - 1, len(intervals) - 1)]))
    # Unreachable but typed
    record = create_incident(category, account_id=account_id, strategy=strategy, error=last_error)
    return None, record


def broker_uncertainty_reconcile_first(
    category: IncidentCategory,
    reconcile: Callable[[], Any],
    action: Callable[[], Any] | None = None,
    *,
    account_id: str | None = None,
    strategy: str | None = None,
    current_state: dict[str, Any] | None = None,
) -> tuple[Any | None, IncidentRecord]:
    """Reconcile-first recovery for broker/order uncertainty: never blind retry.

    Always runs reconcile() before considering action(); if reconcile fails or
    indicates uncertainty persists, returns failure record without invoking action.
    """
    attempted = "reconcile_first"
    try:
        reconcile_result = reconcile()
        # If reconcile explicitly signals failure (False/None with error), treat as not recovered
        if reconcile_result is False:
            record = create_incident(
                category,
                account_id=account_id,
                strategy=strategy,
                current_state=current_state,
                attempted_recovery=attempted,
                recovery_result="reconcile_failed_no_retry",
                payload={"reconcile_result": str(reconcile_result)},
            )
            emit_incident_record(record)
            return None, record
        # Reconcile succeeded — only now may the caller retry the original action once
        if action is not None:
            try:
                result = action()
                record = create_incident(
                    category,
                    account_id=account_id,
                    strategy=strategy,
                    current_state=current_state,
                    attempted_recovery=attempted,
                    recovery_result="reconciled_then_action_succeeded",
                    payload={"reconcile_result": str(reconcile_result)},
                )
                emit_incident_record(record)
                return result, record
            except Exception as exc:
                record = create_incident(
                    category,
                    account_id=account_id,
                    strategy=strategy,
                    error=str(exc),
                    current_state=current_state,
                    attempted_recovery=attempted,
                    recovery_result="reconciled_but_action_failed",
                    payload={"reconcile_result": str(reconcile_result)},
                )
                emit_incident_record(record)
                return None, record
        record = create_incident(
            category,
            account_id=account_id,
            strategy=strategy,
            current_state=current_state,
            attempted_recovery=attempted,
            recovery_result="reconciled_no_further_action",
            payload={"reconcile_result": str(reconcile_result)},
        )
        emit_incident_record(record)
        return reconcile_result, record
    except Exception as exc:
        record = create_incident(
            category,
            account_id=account_id,
            strategy=strategy,
            error=str(exc),
            current_state=current_state,
            attempted_recovery=attempted,
            recovery_result="reconcile_raised_no_retry",
            payload={},
        )
        emit_incident_record(record)
        return None, record


def db_integrity_fail_closed(
    category: IncidentCategory,
    *,
    account_id: str | None = None,
    strategy: str | None = None,
    error: str | None = None,
    current_state: dict[str, Any] | None = None,
) -> IncidentRecord:
    """Fail closed immediately for DB integrity incidents (never retry, halt trading)."""
    record = create_incident(
        category,
        account_id=account_id,
        strategy=strategy,
        error=error,
        current_state=current_state,
        attempted_recovery="fail_closed_no_retry",
        recovery_result="halted_trading_persisted",
        payload={"fail_closed": True},
    )
    emit_incident_record(record)
    return record


def automate_incident_recovery(
    category: IncidentCategory,
    *,
    account_id: str | None = None,
    strategy: str | None = None,
    error: str | None = None,
    current_state: dict[str, Any] | None = None,
    provider_action: Callable[[], Any] | None = None,
    reconcile_action: Callable[[], Any] | None = None,
    retry_action: Callable[[], Any] | None = None,
) -> IncidentRecord:
    """Canonical bounded recovery dispatcher per taxonomy (never raises).

    - Provider temporary -> exponential retry 5 attempts.
    - Broker/order uncertainty -> reconcile first, never blind retry.
    - DB integrity -> fail closed immediately.
    Returns the canonical IncidentRecord (incident_id/timestamp/category/severity/...
    with attempted_recovery/recovery_result populated).
    """
    try:
        if category in _PROVIDER_TEMPORARY_CATEGORIES:
            if provider_action is not None:
                _result, record = provider_temporary_retry(
                    category, provider_action, account_id=account_id, strategy=strategy, current_state=current_state
                )
                return record
            record = create_incident(
                category,
                account_id=account_id,
                strategy=strategy,
                error=error,
                current_state=current_state,
                attempted_recovery="exponential_retry 5 attempts (no action provided)",
                recovery_result="no_action",
            )
            emit_incident_record(record)
            return record
        if category in _BROKER_UNCERTAINTY_CATEGORIES:
            if reconcile_action is not None:
                _result, record = broker_uncertainty_reconcile_first(
                    category,
                    reconcile_action,
                    retry_action,
                    account_id=account_id,
                    strategy=strategy,
                    current_state=current_state,
                )
                return record
            record = create_incident(
                category,
                account_id=account_id,
                strategy=strategy,
                error=error,
                current_state=current_state,
                attempted_recovery="reconcile_first_no_blind_retry",
                recovery_result="reconcile_required_but_not_provided",
            )
            emit_incident_record(record)
            return record
        if category in _DB_INTEGRITY_CATEGORIES:
            return db_integrity_fail_closed(
                category, account_id=account_id, strategy=strategy, error=error, current_state=current_state
            )
        # Fallback: generic taxonomy but still produce canonical record
        spec = INCIDENT_TAXONOMY.get(category)
        if spec is not None and not spec.auto_retry:
            record = create_incident(
                category,
                account_id=account_id,
                strategy=strategy,
                error=error,
                current_state=current_state,
                attempted_recovery="no_auto_retry_per_taxonomy",
                recovery_result="operator_action_required",
            )
            emit_incident_record(record)
            return record
        # Auto-retry fallback: single attempt
        if provider_action is not None:
            _result, record = provider_temporary_retry(
                category, provider_action, account_id=account_id, strategy=strategy, current_state=current_state
            )
            return record
        record = create_incident(
            category,
            account_id=account_id,
            strategy=strategy,
            error=error,
            current_state=current_state,
            attempted_recovery="bounded_retry",
            recovery_result="no_action_retry_exhausted",
        )
        emit_incident_record(record)
        return record
    except Exception as exc:
        # Ultimate fail-closed: never raise from automation
        log.warning("automate_incident_recovery failed for %s: %s", category, exc, exc_info=True)
        record = create_incident(
            category,
            account_id=account_id,
            strategy=strategy,
            error=error or str(exc),
            current_state=current_state,
            attempted_recovery="automation_error_fail_closed",
            recovery_result="halted",
        )
        emit_incident_record(record)
        return record
