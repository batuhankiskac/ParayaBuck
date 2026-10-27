"""Canonical incident taxonomy and record for PAPER reliability.

Strict classification for 12 categories, no generic bucket for known cases.
Every incident defines severity, halt, retry, recovery, and canonical payload.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
        operator_action="Alpaca durumunu, ağı ve anahtarları kontrol edin; kod değişikliği gerekmez.",
    ),
    IncidentCategory.BROKER_ACCOUNT_STALE: IncidentSpec(
        category=IncidentCategory.BROKER_ACCOUNT_STALE,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(15,),
        bounded_attempts=None,
        operator_action="Alpaca bağlantısını ve sistem saatini kontrol edin; yeni anlık görüntüyle kendiliğinden temizlenir.",
    ),
    IncidentCategory.POSITION_DRIFT: IncidentSpec(
        category=IncidentCategory.POSITION_DRIFT,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Harici pozisyon sembollerini inceleyin, elle mutabakat yapın ve “Kilidi doğrula” ile kilidi açın.",
    ),
    IncidentCategory.ORDER_DRIFT: IncidentSpec(
        category=IncidentCategory.ORDER_DRIFT,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(60,),
        bounded_attempts=None,
        operator_action="Mutabakat aracını çalıştırın; bot durdurulmuşsa “Kilidi doğrula” ile kilidi açın.",
    ),
    IncidentCategory.PENDING_ORDER_UNKNOWN: IncidentSpec(
        category=IncidentCategory.PENDING_ORDER_UNKNOWN,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Alpaca'nın emir sonucunu kesinleştirmesini bekleyin, piyasa durdurmalarını doğrulayın ve “Kilidi doğrula” ile kilidi açın.",
    ),
    IncidentCategory.SIGNAL_HISTORY_UNVERIFIED: IncidentSpec(
        category=IncidentCategory.SIGNAL_HISTORY_UNVERIFIED,
        severity=IncidentSeverity.WARNING,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(3600,),
        bounded_attempts=None,
        operator_action="Veri sağlayıcısının yeterli kapsama ulaşmasını bekleyin; yapılandırma özetini ve evreni kontrol edin.",
    ),
    IncidentCategory.DATABASE_ERROR: IncidentSpec(
        category=IncidentCategory.DATABASE_ERROR,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(5,),
        bounded_attempts=1,
        operator_action="Disk alanını ve izinleri kontrol edin, yedeği doğrulayın ve WAL anlık görüntüsünü geri yükleyin.",
    ),
    IncidentCategory.STATE_RESTORE_FAILED: IncidentSpec(
        category=IncidentCategory.STATE_RESTORE_FAILED,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Veritabanı yedeğini geri yükleyin; mutabakattan sonra “Kilidi doğrula” ile kilidi açın.",
    ),
    IncidentCategory.DUPLICATE_DECISION: IncidentSpec(
        category=IncidentCategory.DUPLICATE_DECISION,
        severity=IncidentSeverity.WARNING,
        trading_halts=False,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Karar kayıtlarının sayısını dönem sayısıyla karşılaştırın.",
    ),
    IncidentCategory.DUPLICATE_ORDER_RISK: IncidentSpec(
        category=IncidentCategory.DUPLICATE_ORDER_RISK,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Mükerrer emir kimliklerini denetleyin.",
    ),
    IncidentCategory.CLOCK_OR_SESSION_MISMATCH: IncidentSpec(
        category=IncidentCategory.CLOCK_OR_SESSION_MISMATCH,
        severity=IncidentSeverity.WARNING,
        trading_halts=True,
        auto_retry=True,
        retry_interval_seconds=(2,),
        bounded_attempts=None,
        operator_action="Sistem saatini zaman sunucusuyla eşitleyin ve saat dilimini (New York) doğrulayın.",
    ),
    IncidentCategory.RESTART_RECOVERY_FAILED: IncidentSpec(
        category=IncidentCategory.RESTART_RECOVERY_FAILED,
        severity=IncidentSeverity.CRITICAL,
        trading_halts=True,
        auto_retry=False,
        retry_interval_seconds=None,
        bounded_attempts=None,
        operator_action="Onarılabilir nedenlerde “Kilidi doğrula”; zirveden düşüşte “Yeni risk dönemi” kullanın.",
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

    @property
    def episode_key(self) -> str:
        """Stable identity of the incident episode, for alert throttling.

        ``incident_id`` is a fresh uuid per record, so keying a throttle on it
        never coalesces a repeat. The episode is the category on one account,
        strategy and market: its repeats share one key.
        """
        return ":".join(str(part or "") for part in (self.category.value, self.account_id, self.strategy, self.market))


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
