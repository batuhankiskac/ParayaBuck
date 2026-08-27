"""Minimal event-notification abstraction for runtime operators.

``emit`` is a fire-and-forget, never-raising notification sink: every event
is written to the ``parayabuck.monitoring.alerts`` logger (console/log sink
is always attached), and — when configured — POSTed as JSON to a generic
webhook URL. The webhook POST runs on a daemon thread with a short timeout;
any failure is logged, never raised into the caller.

Failure events are throttled per kind so a broken scanner or broker does not
spam the webhook: within the throttle window a repeat of the same kind is
suppressed (counted, logged at DEBUG) while the first occurrence is still
delivered. The 12 event kinds are the only ones emitted; unknown kinds are
rejected with a logged error.

Configuration precedence (first match wins):
1. ``configure_alerting(...)`` — explicit, called by the runtime at startup
   from ``Config.alerting``;
2. ``PARAYABUCK_ALERT_WEBHOOK_URL`` environment variable;
3. the ``alerting:`` block of ``config/settings.yaml`` (or ``CONFIG_PATH``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from parayabuck.monitoring.events import ALERT_KINDS
from parayabuck.monitoring.events import WARNING_KINDS as _WARNING_KINDS

log = logging.getLogger("parayabuck.monitoring.alerts")

_DEFAULT_THROTTLE_SECONDS = 60.0
_WEBHOOK_TIMEOUT = (3.0, 8.0)  # (connect, read) — never block the caller

_lock = threading.Lock()
_settings: dict[str, object] = {
    "enabled": True,
    "webhook_url": "",
    "events": tuple(sorted(ALERT_KINDS)),
    "throttle_seconds": _DEFAULT_THROTTLE_SECONDS,
}
_configured = False
_last_emitted_at: dict[str, float] = {}
_suppressed_count = 0
_delivered_counts: dict[str, int] = {}
_webhook_attempted = 0
_webhook_succeeded = 0
_webhook_failed = 0
# Legacy alias kept for backward compatibility
_webhook_count = 0
_config_sources: list[str] = []


def configure_alerting(
    *,
    enabled: bool | None = None,
    webhook_url: str | None = None,
    events: tuple[str, ...] | list[str] | None = None,
    throttle_seconds: float | None = None,
) -> None:
    """Explicitly configure the notification sink (idempotent, never raises).

    ``enabled=False`` keeps the console/log sink but disables the webhook.
    ``events`` restricts which kinds reach the webhook; the console/log sink
    still receives every accepted kind. ``webhook_url=""`` disables the
    webhook without changing ``enabled``.
    """
    global _configured
    try:
        with _lock:
            if enabled is not None:
                _settings["enabled"] = bool(enabled)
            if webhook_url is not None:
                _settings["webhook_url"] = str(webhook_url).strip()
            if events is not None:
                known = sorted(set(events) & ALERT_KINDS)
                _settings["events"] = tuple(known)
            if throttle_seconds is not None:
                _settings["throttle_seconds"] = max(0.0, float(throttle_seconds))
            _configured = True
    except Exception:  # pragma: no cover - defensive; never raise
        log.exception("Alert configuration ignored")


def _discover_settings() -> None:
    """Populate settings from env/config on first use when not configured."""
    global _configured
    if _configured:
        return
    try:
        with _lock:
            if _configured:
                return
            sources: list[str] = []
            webhook_url = os.getenv("PARAYABUCK_ALERT_WEBHOOK_URL", "").strip()
            if webhook_url:
                sources.append("env:PARAYABUCK_ALERT_WEBHOOK_URL")
            config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
            if config_path.exists():
                try:
                    import yaml

                    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                    alerting = dict(raw.get("alerting", {}))
                    if not webhook_url:
                        webhook_url = str(alerting.get("webhook_url", "")).strip()
                        if webhook_url:
                            sources.append("config:alerting.webhook_url")
                    if "enabled" in alerting:
                        _settings["enabled"] = bool(alerting["enabled"])
                    if "events" in alerting and isinstance(alerting["events"], list):
                        known = sorted(set(alerting["events"]) & ALERT_KINDS)
                        _settings["events"] = tuple(known)
                except Exception:
                    log.warning("Alerting config discovery failed; using defaults", exc_info=True)
            if webhook_url:
                _settings["webhook_url"] = webhook_url
            _config_sources[:] = sources
            _configured = True
    except Exception:  # pragma: no cover - defensive; never raise
        log.exception("Alert settings discovery failed")
        _configured = True


def _throttle_key(kind: str, fields: dict[str, object]) -> str:
    """Incident-style events throttle per distinct incident, not per kind."""
    if kind == "paper_incident":
        category = str(fields.get("category", "") or "").strip()
        incident_id = str(fields.get("incident_id", "") or "").strip()
        return f"{kind}:{category}:{incident_id}"
    return kind


def _severity(kind: str) -> int:
    return logging.WARNING if kind in _WARNING_KINDS else logging.INFO


def _post_webhook(url: str, payload: dict[str, object]) -> None:
    try:
        response = requests.post(url, json=payload, timeout=_WEBHOOK_TIMEOUT)
        response.raise_for_status()
        with _lock:
            global _webhook_succeeded
            _webhook_succeeded += 1
    except Exception as exc:
        with _lock:
            global _webhook_failed
            _webhook_failed += 1
        log.warning("Alert webhook delivery failed for kind=%s: %s", payload.get("kind"), exc)


def emit(kind: str, *, message: str, **fields: object) -> None:
    """Emit one alert event. Never raises.

    The event is always written to the local log sink. When alerting is
    enabled and ``kind`` is in the configured webhook event set, the event is
    also POSTed (fire-and-forget) to the configured webhook URL. Per-kind
    throttling coalesces repeat failures within the throttle window; for
    ``paper_incident`` the throttle is per (kind, category, incident_id) so
    two distinct incidents are both delivered.
    """
    try:
        if kind not in ALERT_KINDS:
            log.error("Ignored alert with unknown kind=%r", kind)
            return
        _discover_settings()
        throttle_key = _throttle_key(kind, dict(fields))
        with _lock:
            enabled = bool(_settings["enabled"])
            webhook_url = str(_settings.get("webhook_url", "")).strip()
            events_raw = _settings.get("events", ())
            events = tuple(events_raw) if isinstance(events_raw, (tuple, list)) else ()
            throttle_raw = _settings.get("throttle_seconds", _DEFAULT_THROTTLE_SECONDS)
            throttle = float(throttle_raw) if isinstance(throttle_raw, (int, float)) else 0.0
            now = time.monotonic()
            last = _last_emitted_at.get(throttle_key)
            if last is not None and throttle > 0.0 and now - last < throttle:
                global _suppressed_count
                _suppressed_count += 1
                log.debug("Alert %s suppressed (throttle window %.0fs)", kind, throttle)
                return
            _last_emitted_at[throttle_key] = now
        with _lock:
            _delivered_counts[kind] = _delivered_counts.get(kind, 0) + 1
        log.log(_severity(kind), "ALERT %s: %s fields=%s", kind, message, dict(fields))
        if not enabled or not webhook_url or kind not in events:
            return
        with _lock:
            global _webhook_count, _webhook_attempted
            _webhook_count += 1
            _webhook_attempted += 1
        payload: dict[str, object] = {
            "kind": kind,
            "message": message,
            "occurred_at": _now_iso(),
            "fields": dict(fields),
        }
        threading.Thread(
            target=_post_webhook,
            args=(webhook_url, payload),
            name="pb-alert-webhook",
            daemon=True,
        ).start()
    except Exception:  # pragma: no cover - the contract is to never raise
        log.exception("Alert emission failed for kind=%s", kind)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def emit_incident(
    category: str,
    severity: str,
    *,
    message: str,
    incident_id: str | None = None,
    account_id: str | None = None,
    **fields: object,
) -> None:
    """Emit a canonical incident record via the alert sink (never raises).

    Thin wrapper over emit() that guarantees incident_id/timestamp/category/
    severity are present as structured fields. Bounded throttling still applies
    per kind (paper_incident). Integrates with metrics via caller; this helper
    only drives the alert sink.
    """
    try:
        emit(
            "paper_incident",
            message=message,
            incident_id=incident_id or "",
            category=category,
            severity=severity,
            account_id=account_id or "unknown",
            **fields,
        )
    except Exception:
        log.warning("emit_incident failed for category=%s", category, exc_info=True)


def alert_stats() -> dict[str, object]:
    """Process-local delivery counters (webhook sends and throttled drops)."""
    with _lock:
        return {
            "suppressed": _suppressed_count,
            "delivered": dict(_delivered_counts),
            "webhook_sent": _webhook_count,
            "webhook_attempted": _webhook_attempted,
            "webhook_succeeded": _webhook_succeeded,
            "webhook_failed": _webhook_failed,
            "configured_from": list(_config_sources),
        }


def reset_for_tests() -> None:
    """Clear configuration, throttle state and counters (test helper only)."""
    global \
        _configured, \
        _suppressed_count, \
        _webhook_count, \
        _webhook_attempted, \
        _webhook_succeeded, \
        _webhook_failed, \
        _delivered_counts
    with _lock:
        _settings.update(
            {
                "enabled": True,
                "webhook_url": "",
                "events": tuple(sorted(ALERT_KINDS)),
                "throttle_seconds": _DEFAULT_THROTTLE_SECONDS,
            }
        )
        _configured = False
        _last_emitted_at.clear()
        _suppressed_count = 0
        _delivered_counts = {}
        _webhook_count = 0
        _webhook_attempted = 0
        _webhook_succeeded = 0
        _webhook_failed = 0
        _config_sources.clear()


__all__ = [
    "ALERT_KINDS",
    "alert_stats",
    "configure_alerting",
    "emit",
    "emit_incident",
    "reset_for_tests",
]
