"""Structured operational event model — single source for observability journal.

Reuses canonical alert vocabulary from ``monitoring/events.py`` and extends it
with lifecycle-only kinds needed for durable operator forensics. Each event
is JSON-serializable, UTC-timestamped, and carries optional domain correlation
identifiers (session_id, decision_id, order IDs, incident_id).

Security: ``fields`` must never contain secrets (API keys, webhook URLs,
approval keys). ``_REDACTED_KEYS`` enforce allowlist/redaction when needed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from parayabuck.monitoring.events import WARNING_KINDS

# ---------------------------------------------------------------------------
# Severity — small stable vocabulary
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def severity_for_kind(kind: str) -> Severity:
    """Centralized severity classification.

    Canonical alert kinds use WARNING/INFO from events.py WARNING_KINDS.
    Lifecycle-only kinds default to INFO; critical observability failures
    map to WARNING/CRITICAL explicitly.
    """
    if kind in _CRITICAL_KINDS:
        return Severity.CRITICAL
    if kind in WARNING_KINDS or kind in _WARNING_KINDS:
        return Severity.WARNING
    return Severity.INFO


_CRITICAL_KINDS: frozenset[str] = frozenset(
    {
        "runtime_stopped_unexpected",
        "circuit_breaker",
        "drawdown_halt",
    }
)

_WARNING_KINDS: frozenset[str] = frozenset(
    {
        "account_became_stale",
        "reconciliation_failure",
        "runtime_health_degraded",
    }
)

# ---------------------------------------------------------------------------
# Secrets redaction
# ---------------------------------------------------------------------------

_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "password",
        "webhook_url",
        "approval_key",
        "authorization",
        "alpaca_api_key",
        "alpaca_secret_key",
        "live_approval_key",
        # Bearer/session credentials and the alert e-mail routing (addresses and
        # the SMTP relay) are operator-private: they never reach a journal row or
        # a diagnostic bundle in the clear.
        "token",
        "access_token",
        "credential",
        "credentials",
        "bearer",
        "bearer_token",
        "smtp_host",
        "from_address",
        "to_addresses",
        "to_address",
        "email_address",
        "recipients",
    }
)

# Account numbers are not secrets the trading path needs, but they identify the
# live account: the value is masked to its last four characters, never dropped.
_ACCOUNT_NUMBER_KEYS: frozenset[str] = frozenset({"account_number", "account_id", "expected_account_id"})


_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "webhook",
    "password",
    "approval_key",
    "api_key",
    "credential",
    "bearer",
    "token",
)


def _is_sensitive(key: str) -> bool:
    """True for redaction keys and for any key naming a secret or a webhook."""
    low = key.lower()
    return low in _REDACTED_KEYS or any(fragment in low for fragment in _SENSITIVE_SUBSTRINGS)


def mask_account_number(value: object) -> str:
    """``***`` plus the last four characters of an account number (``***`` alone when shorter)."""
    text = str(value or "")
    return f"***{text[-4:]}" if len(text) > 4 else ("***" if text else "")


def _redact_item(key: str, value: object) -> object:
    if _is_sensitive(key):
        return "***REDACTED***"
    if key.lower() in _ACCOUNT_NUMBER_KEYS and value not in (None, ""):
        return mask_account_number(value)
    return _redact_value(value)


def _redact_value(value: object) -> object:
    """Recursively redact sensitive keys inside nested mappings and lists."""
    if isinstance(value, Mapping):
        return {str(key): _redact_item(str(key), item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def redact_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Return copy with secret keys redacted, nested values included.

    Account numbers are masked to their last four characters and e-mail
    routing (addresses, SMTP host) is redacted. Diagnostic bundles pass the
    whole config through this function, so they share the same policy.
    """
    return {str(key): _redact_item(str(key), value) for key, value in fields.items()}


# ---------------------------------------------------------------------------
# Event ID generation
# ---------------------------------------------------------------------------


def deterministic_event_id(
    *,
    kind: str,
    occurred_at: datetime,
    session_id: str | None = None,
    decision_id: str | None = None,
    client_order_id: str | None = None,
    incident_id: str | None = None,
    correlation_id: str | None = None,
    extra: str = "",
) -> str:
    """Deterministic ID for events with stable identity.

    Uses SHA256 over stable components, truncated to 32 hex chars (128 bits).
    Avoids Python ``hash()`` (randomized per process).
    """
    parts = "|".join(
        [
            kind,
            occurred_at.isoformat(),
            session_id or "",
            decision_id or "",
            client_order_id or "",
            incident_id or "",
            correlation_id or "",
            extra,
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()[:32]


def random_event_id() -> str:
    """Collision-resistant runtime identifier (uuid4 hex)."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# OperationalEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationalEvent:
    """One durable operational observation.

    All timestamps are UTC. ``fields`` is a flat JSON-serializable mapping.
    Optional correlation IDs allow tracing runtime session → decision →
    rebalance → order → fill.
    """

    event_id: str
    occurred_at: datetime
    kind: str
    severity: str
    message: str
    # optional domain correlation
    session_id: str | None = None
    account_id: str | None = None
    mode: str | None = None
    market: str | None = None
    strategy: str | None = None
    decision_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    incident_id: str | None = None
    correlation_id: str | None = None
    fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (UTC)")
        for key, value in self.fields.items():
            if _is_sensitive(str(key)) and value != "***REDACTED***":
                raise ValueError(f"fields must not contain secret key: {key}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "session_id": self.session_id,
            "account_id": self.account_id,
            "mode": self.mode,
            "market": self.market,
            "strategy": self.strategy,
            "decision_id": self.decision_id,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "incident_id": self.incident_id,
            "correlation_id": self.correlation_id,
            "fields": dict(self.fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationalEvent:
        occurred_at_raw = data["occurred_at"]
        if isinstance(occurred_at_raw, str):
            occurred_at = datetime.fromisoformat(occurred_at_raw)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
        else:
            occurred_at = occurred_at_raw
        return cls(
            event_id=str(data["event_id"]),
            occurred_at=occurred_at,
            kind=str(data["kind"]),
            severity=str(data["severity"]),
            message=str(data["message"]),
            session_id=data.get("session_id"),
            account_id=data.get("account_id"),
            mode=data.get("mode"),
            market=data.get("market"),
            strategy=data.get("strategy"),
            decision_id=data.get("decision_id"),
            client_order_id=data.get("client_order_id"),
            broker_order_id=data.get("broker_order_id"),
            incident_id=data.get("incident_id"),
            correlation_id=data.get("correlation_id"),
            fields=redact_fields(dict(data.get("fields") or {})),
        )


def create_event(
    *,
    kind: str,
    message: str,
    severity: str | Severity | None = None,
    occurred_at: datetime | None = None,
    event_id: str | None = None,
    deterministic_extra: str = "",
    session_id: str | None = None,
    account_id: str | None = None,
    mode: str | None = None,
    market: str | None = None,
    strategy: str | None = None,
    decision_id: str | None = None,
    client_order_id: str | None = None,
    broker_order_id: str | None = None,
    incident_id: str | None = None,
    correlation_id: str | None = None,
    fields: Mapping[str, object] | None = None,
) -> OperationalEvent:
    """Factory with defaults: UTC now, auto severity, auto event_id."""
    ts = occurred_at or datetime.now(UTC)
    sev = str(severity) if severity is not None else str(severity_for_kind(kind))
    eid = event_id
    if eid is None:
        # Use deterministic ID when we have stable domain identity
        if decision_id or client_order_id or incident_id or correlation_id or deterministic_extra:
            eid = deterministic_event_id(
                kind=kind,
                occurred_at=ts,
                session_id=session_id,
                decision_id=decision_id,
                client_order_id=client_order_id,
                incident_id=incident_id,
                correlation_id=correlation_id,
                extra=deterministic_extra,
            )
        else:
            eid = random_event_id()
    redacted = redact_fields(dict(fields or {}))
    return OperationalEvent(
        event_id=eid,
        occurred_at=ts,
        kind=kind,
        severity=sev,
        message=message,
        session_id=session_id,
        account_id=account_id,
        mode=mode,
        market=market,
        strategy=strategy,
        decision_id=decision_id,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        incident_id=incident_id,
        correlation_id=correlation_id,
        fields=redacted,
    )


__all__ = [
    "OperationalEvent",
    "Severity",
    "create_event",
    "deterministic_event_id",
    "random_event_id",
    "redact_fields",
    "severity_for_kind",
]
