"""Canonical incident taxonomy and record for PAPER reliability.

Strict classification for 12 categories, no generic bucket for known cases.
Every incident defines severity, halt, retry, recovery, and canonical payload.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class IncidentCategory(StrEnum):
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    BROKER_ACCOUNT_STALE = "BROKER_ACCOUNT_STALE"
    POSITION_DRIFT = "POSITION_DRIFT"
    ORDER_DRIFT = "ORDER_DRIFT"
    PENDING_ORDER_UNKNOWN = "PENDING_ORDER_UNKNOWN"
    SIGNAL_HISTORY_UNVERIFIED = "SIGNAL_HISTORY_UNVERIFIED"
    DATABASE_ERROR = "DATABASE_ERROR"
    STATE_RESTORE_FAILED = "STATE_RESTORE_FAILED"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    DUPLICATE_ORDER_RISK = "DUPLICATE_ORDER_RISK"
    CLOCK_OR_SESSION_MISMATCH = "CLOCK_OR_SESSION_MISMATCH"
    RESTART_RECOVERY_FAILED = "RESTART_RECOVERY_FAILED"


class IncidentSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class IncidentSpec:
    category: IncidentCategory
    severity: IncidentSeverity
    trading_halts: bool
    auto_retry: bool
    retry_interval_seconds: tuple[int, ...] | None
    bounded_attempts: int | None
    recovery_condition: str
    operator_action: str


# Strict taxonomy — every row has all columns, no generic fallback for known cases.
INCIDENT_TAXONOMY: dict[IncidentCategory, IncidentSpec] = {
    IncidentCategory.PROVIDER_UNAVAILABLE: IncidentSpec(
        category=IncidentCategory.PROVIDER_UNAVAILABLE,
        severity=IncidentSeverity.WARNING,
        trading_halts=False,
        auto_retry=True,
        retry_interval_seconds=(2, 4, 8, 16, 16),
        bounded_attempts=5,
        recovery_condition="broker.reconcile() success + fresh account + health.healthy",
        operator_action="Check Alpaca status/network/creds; no code change",
    ),
    IncidentCategory.BROKER_ACCOUNT_STALE: IncidentSpec(
        category=IncidentCategory.BROKER_ACCOUNT_STALE,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(15,),
        bounded_attempts=None,
        recovery_condition="Fresh AccountSnapshot age <= maximum_account_age_for_entry_seconds",
        operator_action="Verify broker connectivity/clock; self-clears on fresh snapshot",
    ),
    IncidentCategory.POSITION_DRIFT: IncidentSpec(
        category=IncidentCategory.POSITION_DRIFT,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="broker.reconcile() no drift + reset_safety_lock",
        operator_action="Inspect PaperIncidentReport.external_position_symbols, manual reconcile, reset_safety_lock",
    ),
    IncidentCategory.ORDER_DRIFT: IncidentSpec(
        category=IncidentCategory.ORDER_DRIFT,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(60,),
        bounded_attempts=None,
        recovery_condition="broker.reconcile() success clears safety_incidents + fresh last_reconciliation_at",
        operator_action="Run reconcile_paper_incident.py, reset_safety_lock if halted",
    ),
    IncidentCategory.PENDING_ORDER_UNKNOWN: IncidentSpec(
        category=IncidentCategory.PENDING_ORDER_UNKNOWN,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="All SELLs terminal via is_terminal_order_status + get_order_by_client_id verified",
        operator_action="Wait for broker finality, _verify_market_halts, reset_safety_lock",
    ),
    IncidentCategory.SIGNAL_HISTORY_UNVERIFIED: IncidentSpec(
        category=IncidentCategory.SIGNAL_HISTORY_UNVERIFIED,
        severity=IncidentSeverity.WARNING,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(3600,),
        bounded_attempts=None,
        recovery_condition="healthy==true + verified_through_by_symbol >= decision_timestamp + last_success<=3600s",
        operator_action="Wait for provider adequate_coverage, check digest/universe",
    ),
    IncidentCategory.DATABASE_ERROR: IncidentSpec(
        category=IncidentCategory.DATABASE_ERROR,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(5,),
        bounded_attempts=1,
        recovery_condition="COMMIT + PRAGMA integrity_check==ok",
        operator_action="Check disk/permissions, backup.verify_backup, restore WAL snapshot",
    ),
    IncidentCategory.STATE_RESTORE_FAILED: IncidentSpec(
        category=IncidentCategory.STATE_RESTORE_FAILED,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="load_safety_incidents + load_risk_state parsable, schema v3 valid",
        operator_action="Restore backup_database, reset_safety_lock after reconcile",
    ),
    IncidentCategory.DUPLICATE_DECISION: IncidentSpec(
        category=IncidentCategory.DUPLICATE_DECISION,
        severity=IncidentSeverity.WARNING,
        trading_halts=False,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="Single canonical decision persisted, UNIQUE holds",
        operator_action="Verify portfolio_decisions count == periods",
    ),
    IncidentCategory.DUPLICATE_ORDER_RISK: IncidentSpec(
        category=IncidentCategory.DUPLICATE_ORDER_RISK,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="Broker dedup single fill stream or open_orders terminal; PK prevents double-insert",
        operator_action="Verify IdempotencyKeyFactory, audit duplicate_client_order_ids",
    ),
    IncidentCategory.CLOCK_OR_SESSION_MISMATCH: IncidentSpec(
        category=IncidentCategory.CLOCK_OR_SESSION_MISMATCH,
        severity=IncidentSeverity.WARNING,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(2,),
        bounded_attempts=None,
        recovery_condition="host_time vs broker Bar.timestamp within tolerance + queue_age<=threshold",
        operator_action="Sync NTP, verify NEW_YORK tz",
    ),
    IncidentCategory.RESTART_RECOVERY_FAILED: IncidentSpec(
        category=IncidentCategory.RESTART_RECOVERY_FAILED,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        recovery_condition="broker.reconcile() success + clear_safety_incident single-reason + runtime_sessions.ended_at + rebalance complete",
        operator_action="reset_safety_lock for recoverable reasons, start_new_risk_epoch for drawdown",
    ),
}


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    timestamp: str
    category: IncidentCategory
    severity: IncidentSeverity
    account_id: str | None = None
    strategy: str | None = None
    market: str | None = None
    current_state: dict[str, Any] = field(default_factory=dict)
    attempted_recovery: str | None = None
    recovery_result: str | None = None
    trading_halted: bool = False
    retry_attempt: int | None = None
    next_retry_at: str | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # StrEnum to str for JSON
        data["category"] = str(self.category)
        data["severity"] = str(self.severity)
        return data


def create_incident(
    category: IncidentCategory,
    *,
    account_id: str | None = None,
    strategy: str | None = None,
    market: str | None = None,
    error: str | None = None,
    current_state: dict[str, Any] | None = None,
    attempted_recovery: str | None = None,
    recovery_result: str | None = None,
    retry_attempt: int | None = None,
    next_retry_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> IncidentRecord:
    spec = INCIDENT_TAXONOMY[category]
    return IncidentRecord(
        incident_id=uuid.uuid4().hex,
        timestamp=datetime.now(UTC).isoformat(),
        category=category,
        severity=spec.severity,
        account_id=account_id,
        strategy=strategy,
        market=market,
        current_state=current_state or {},
        attempted_recovery=attempted_recovery,
        recovery_result=recovery_result,
        trading_halted=spec.trading_halts,
        retry_attempt=retry_attempt,
        next_retry_at=next_retry_at,
        error=error,
        payload=payload or {},
    )


__all__ = [
    "IncidentCategory",
    "IncidentSeverity",
    "IncidentSpec",
    "INCIDENT_TAXONOMY",
    "IncidentRecord",
    "create_incident",
]
