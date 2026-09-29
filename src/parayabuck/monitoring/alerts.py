"""Minimal event-notification abstraction for runtime operators.

``emit`` is a fire-and-forget, never-raising notification sink: every event
is written to the ``parayabuck.monitoring.alerts`` logger (console/log sink
is always attached), and — when configured — POSTed as JSON to a generic
webhook URL. The webhook POST runs via a bounded single-worker queue with a
short timeout; any failure is logged, never raised into the caller.

Failure events are throttled per kind so a broken data provider or broker does not
spam the webhook: within the throttle window a repeat of the same kind is
suppressed (counted, logged at DEBUG) while the first occurrence is still
delivered. The kinds in ``monitoring.events.ALERT_KINDS`` are the only ones
emitted; unknown kinds are rejected with a logged error. For incident-style kinds throttling is per
distinct incident/market/order to avoid suppressing actionable incidents.

Configuration precedence (first match wins):
1. ``configure_alerting(...)`` — explicit, called by the runtime at startup
   from ``Config.alerting``;
2. ``PARAYABUCK_ALERT_WEBHOOK_URL`` environment variable;
3. the ``alerting:`` block of ``config/settings.yaml`` (or ``CONFIG_PATH``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from parayabuck.monitoring.events import ALERT_KINDS
from parayabuck.monitoring.events import WARNING_KINDS as _WARNING_KINDS
from parayabuck.monitoring.operational_event import redact_fields

log = logging.getLogger("parayabuck.monitoring.alerts")

_DEFAULT_THROTTLE_SECONDS = 60.0
_WEBHOOK_TIMEOUT = (3.0, 8.0)  # (connect, read) — never block the caller
_WEBHOOK_QUEUE_SIZE = 256
_WEBHOOK_MAX_RETRIES = 3
_WEBHOOK_RETRY_BACKOFF = (0.5, 1.0, 2.0)
_THROTTLE_KEYS_MAX = 4096

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
_webhook_queued = 0
_webhook_dropped = 0
_webhook_last_success_at: str | None = None
_webhook_last_failure_at: str | None = None
# Legacy alias kept for backward compatibility
_webhook_count = 0
_config_sources: list[str] = []

# Bounded webhook delivery queue — single worker, never spawns per-emit threads
_webhook_queue: queue.Queue[tuple[str, dict[str, object]] | None] = queue.Queue(maxsize=_WEBHOOK_QUEUE_SIZE)
_webhook_worker: threading.Thread | None = None
_webhook_worker_running = False


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
                unknown = sorted(set(events) - ALERT_KINDS)
                if unknown:
                    log.warning("Alerting config ignored unknown events: %s", unknown)
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
    """Throttle per distinct actionable incident, not merely per kind.

    For paper_incident — per (category, incident_id) or message hash.
    For provider/reconciliation/order kinds — include market/order/decision dimensions
    so distinct actionable incidents are not collapsed.
    """
    if kind == "paper_incident":
        category = str(fields.get("category", "") or "").strip()
        incident_id = str(fields.get("incident_id", "") or "").strip()
        if incident_id:
            return f"{kind}:{category}:{incident_id}"
        msg = str(fields.get("message", "") or fields.get("detail", "") or "")
        digest = hashlib.sha256(msg.encode("utf-8")).hexdigest()[:16] if msg else "empty"
        return f"{kind}:{category}:{digest}"
    # For other kinds, include correlation dimensions if present
    parts: list[str] = [kind]
    for key in ("market", "decision_id", "client_order_id", "incident_id", "category", "symbol"):
        val = str(fields.get(key, "") or "").strip()
        if val:
            parts.append(val)
            # limit to first 2 dimensions to avoid key explosion but preserve distinction
            if len(parts) >= 3:
                break
    return ":".join(parts)


def _severity(kind: str) -> int:
    return logging.WARNING if kind in _WARNING_KINDS else logging.INFO


def _severity_str(kind: str) -> str:
    return "warning" if kind in _WARNING_KINDS else "info"


def _scrub_webhook_url(text: str, url: str) -> str:
    """Remove the webhook URL (and its secret-bearing path) from an error text.

    ``requests`` puts the URL into its exception messages — the full URL for
    HTTP errors, the path and query for connection errors — and a chat webhook's
    path *is* its credential, so it must never reach the log.
    """
    parts = urlsplit(url)
    scrubbed = text.replace(url, "<webhook>") if url else text
    # Connection errors print path *and* query; a query can carry the key/token
    if parts.query:
        scrubbed = scrubbed.replace(parts.query, "<webhook-query>")
    if parts.password:
        scrubbed = scrubbed.replace(parts.password, "<webhook-password>")
    if parts.path and parts.path != "/":
        scrubbed = scrubbed.replace(parts.path, "<webhook-path>")
    return scrubbed


def _ensure_webhook_worker() -> None:
    global _webhook_worker, _webhook_worker_running
    with _lock:
        if _webhook_worker_running and _webhook_worker is not None and _webhook_worker.is_alive():
            return
        _webhook_worker_running = True
        _webhook_worker = threading.Thread(target=_webhook_worker_loop, name="pb-alert-webhook-worker", daemon=True)
        _webhook_worker.start()


def _webhook_worker_loop() -> None:
    while True:
        try:
            item = _webhook_queue.get(timeout=0.5)
        except queue.Empty:
            with _lock:
                if not _webhook_worker_running:
                    break
            continue
        if item is None:
            _webhook_queue.task_done()
            break
        url, payload = item
        # Attempt with bounded retries, exponential backoff
        for attempt in range(_WEBHOOK_MAX_RETRIES):
            try:
                response = requests.post(url, json=payload, timeout=_WEBHOOK_TIMEOUT)
                response.raise_for_status()
                with _lock:
                    global _webhook_succeeded, _webhook_last_success_at
                    _webhook_succeeded += 1
                    _webhook_last_success_at = datetime.now(UTC).isoformat()
                break
            except Exception as exc:
                if attempt == _WEBHOOK_MAX_RETRIES - 1:
                    with _lock:
                        global _webhook_failed, _webhook_last_failure_at
                        _webhook_failed += 1
                        _webhook_last_failure_at = datetime.now(UTC).isoformat()
                    log.warning(
                        "Alert webhook delivery failed for kind=%s after %d attempts: %s",
                        payload.get("kind"),
                        _WEBHOOK_MAX_RETRIES,
                        _scrub_webhook_url(str(exc), url),
                    )
                else:
                    backoff = _WEBHOOK_RETRY_BACKOFF[min(attempt, len(_WEBHOOK_RETRY_BACKOFF) - 1)]
                    time.sleep(backoff)
        _webhook_queue.task_done()


def _post_webhook(url: str, payload: dict[str, object]) -> None:
    """Synchronous single-attempt webhook POST (backward compat for tests).

    The worker's retry logic lives in ``_webhook_worker_loop``; tests that
    call ``_post_webhook`` directly expect a single POST and immediate
    counter update without background thread involvement.
    """
    try:
        response = requests.post(url, json=payload, timeout=_WEBHOOK_TIMEOUT)
        response.raise_for_status()
        with _lock:
            global _webhook_succeeded, _webhook_last_success_at
            _webhook_succeeded += 1
            _webhook_last_success_at = datetime.now(UTC).isoformat()
    except Exception as exc:
        with _lock:
            global _webhook_failed, _webhook_last_failure_at
            _webhook_failed += 1
            _webhook_last_failure_at = datetime.now(UTC).isoformat()
        log.warning(
            "Alert webhook delivery failed for kind=%s: %s", payload.get("kind"), _scrub_webhook_url(str(exc), url)
        )


def _enqueue_webhook(url: str, payload: dict[str, object]) -> None:
    _ensure_webhook_worker()
    try:
        _webhook_queue.put_nowait((url, payload))
        with _lock:
            global _webhook_queued
            _webhook_queued += 1
    except queue.Full:
        with _lock:
            global _webhook_dropped
            _webhook_dropped += 1
        log.warning("Alert webhook queue full; dropping kind=%s", payload.get("kind"))


def emit(kind: str, *, message: str, **fields: object) -> None:
    """Emit one alert event. Never raises.

    The event is always written to the local log sink. When alerting is
    enabled and ``kind`` is in the configured webhook event set, the event is
    also POSTed (fire-and-forget) to the configured webhook URL via a bounded
    single-worker queue. Per-kind throttling coalesces repeat failures within
    the throttle window; for ``paper_incident`` the throttle is per
    (kind, category, incident_id) so two distinct incidents are both delivered.
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
            if len(_last_emitted_at) > _THROTTLE_KEYS_MAX:
                # Keys carry per-incident/order ids, so the map grows for the life
                # of the process; an entry past its window can never suppress again
                for key in [k for k, t in _last_emitted_at.items() if now - t >= throttle]:
                    del _last_emitted_at[key]
        with _lock:
            _delivered_counts[kind] = _delivered_counts.get(kind, 0) + 1
        # The journal redacts its copy; the log line and the external webhook
        # must never carry a secret-named field in the clear either
        safe_fields = redact_fields(fields)
        log.log(_severity(kind), "ALERT %s: %s fields=%s", kind, message, safe_fields)
        # Reuse event_id if supplied, else generate; include correlation from fields
        eid = str(fields.get("event_id") or "") or None
        # Durable operational journal — best effort, never raises, isolated from trading
        try:
            from parayabuck.monitoring.journal import emit_journal
            from parayabuck.monitoring.operational_event import create_event

            ev = create_event(
                event_id=eid,
                kind=kind,
                message=message,
                fields=dict(fields),
                session_id=str(fields.get("session_id") or "") or None,
                decision_id=str(fields.get("decision_id") or "") or None,
                client_order_id=str(fields.get("client_order_id") or "") or None,
                incident_id=str(fields.get("incident_id") or "") or None,
                account_id=str(fields.get("account_id") or "") or None,
                market=str(fields.get("market") or "") or None,
            )
            emit_journal(ev)
        except Exception:
            log.warning("journal emit failed for alert kind=%s", kind, exc_info=True)
        if not enabled or not webhook_url or kind not in events:
            return
        with _lock:
            global _webhook_count, _webhook_attempted
            _webhook_count += 1
            _webhook_attempted += 1
        # Build webhook payload with correlation fields and stable event_id
        event_id = eid or uuid.uuid4().hex
        payload: dict[str, object] = {
            "event_id": event_id,
            "kind": kind,
            "severity": _severity_str(kind),
            "message": message,
            # Chat-webhook compatibility: Slack/Mattermost render ``text``,
            # Discord renders ``content``; generic receivers use ``message``.
            "text": message,
            "content": message,
            "occurred_at": _now_iso(),
            "fields": safe_fields,
        }
        # Promote correlation fields to top-level for operator convenience
        for corr_key in (
            "session_id",
            "decision_id",
            "client_order_id",
            "broker_order_id",
            "incident_id",
            "correlation_id",
            "market",
            "strategy",
            "account_id",
        ):
            val = fields.get(corr_key)
            if isinstance(val, str) and val.strip():
                payload[corr_key] = val
        _enqueue_webhook(webhook_url, payload)
    except Exception:  # pragma: no cover - the contract is to never raise
        log.exception("Alert emission failed for kind=%s", kind)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
            "webhook_queued": _webhook_queued,
            "webhook_dropped": _webhook_dropped,
            "webhook_queue_depth": _webhook_queue.qsize(),
            "webhook_last_success_at": _webhook_last_success_at,
            "webhook_last_failure_at": _webhook_last_failure_at,
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
        _webhook_queued, \
        _webhook_dropped, \
        _webhook_last_success_at, \
        _webhook_last_failure_at, \
        _delivered_counts, \
        _webhook_worker_running
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
        _webhook_queued = 0
        _webhook_dropped = 0
        _webhook_last_success_at = None
        _webhook_last_failure_at = None
        _config_sources.clear()
        # Drain queue
        try:
            while not _webhook_queue.empty():
                try:
                    _webhook_queue.get_nowait()
                    _webhook_queue.task_done()
                except queue.Empty:
                    break
        except Exception:
            pass


__all__ = [
    "ALERT_KINDS",
    "alert_stats",
    "configure_alerting",
    "emit",
    "reset_for_tests",
]
