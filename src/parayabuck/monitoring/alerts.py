"""Local event-notification sink for runtime operators.

``emit`` is a never-raising notification sink: every accepted event is
written to the ``parayabuck.monitoring.alerts`` logger (journald on the VM)
and to the durable operational event journal; the operator reads alerts
through ``parayabuck ops events``, ``parayabuck ops status``, the dashboard
and ``journalctl -u parayabuck``. Outbound webhook delivery was removed on
2026-10-06; the only off-host channel is the opt-in e-mail sink
(``monitoring.email_alerts``, disabled unless ``alerting.email.enabled``),
which receives each accepted (not throttled) event after it is journaled and
delivers asynchronously, so it can never block or fail this call.

Repeats are throttled per distinct incident so a broken data provider or
broker does not flood the log/journal: within the throttle window a repeat of
the same key is suppressed (counted, logged at INFO with the running count)
while the first occurrence is still recorded. The kinds in
``monitoring.events.ALERT_KINDS`` are the only ones emitted; unknown kinds are
rejected with a logged error.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from parayabuck.monitoring.events import ALERT_KINDS
from parayabuck.monitoring.events import WARNING_KINDS as _WARNING_KINDS
from parayabuck.monitoring.operational_event import redact_fields

log = logging.getLogger("parayabuck.monitoring.alerts")

_DEFAULT_THROTTLE_SECONDS = 60.0
_THROTTLE_KEYS_MAX = 4096
# Alerts that end or start a reconciliation episode: observe re-journals the next clean run after them.
_RECONCILIATION_ALERT_KINDS = frozenset({"reconciliation_failure", "reconciliation_recovered"})
_DELIVERY_FAILED_KIND = "alert_delivery_failed"

_lock = threading.Lock()
_throttle_seconds = _DEFAULT_THROTTLE_SECONDS
_last_emitted_at: dict[str, float] = {}
_suppressed_by_key: dict[str, int] = {}
_suppressed_count = 0
_delivered_counts: dict[str, int] = {}


def _throttle_key(kind: str, fields: dict[str, object]) -> str:
    """Throttle per distinct actionable incident, not merely per kind.

    An explicit ``episode_key`` field names the incident episode and wins over
    every other dimension: callers pass a STABLE identity (category, account,
    check name), never a per-record uuid, so repeats of one episode coalesce
    and distinct episodes stay apart.
    For paper_incident without an episode — per (category, incident_id) or message hash.
    For alert_delivery_failed — per (failed alert kind, reason).
    For other kinds — up to two correlation dimensions (market/order/decision)
    plus ``reason`` and ``state``, so a different failure reason or a state
    transition of the same kind is never swallowed by an earlier one.
    """
    if kind == _DELIVERY_FAILED_KIND:
        alert_kind = str(fields.get("alert_kind", "") or "").strip()
        reason = str(fields.get("reason", "") or "").strip()
        return f"{kind}:{alert_kind}:reason={reason}"
    episode = str(fields.get("episode_key", "") or "").strip()
    if episode:
        parts: list[str] = [kind, f"episode={episode}"]
    elif kind == "paper_incident":
        category = str(fields.get("category", "") or "").strip()
        incident_id = str(fields.get("incident_id", "") or "").strip()
        if incident_id:
            return f"{kind}:{category}:{incident_id}"
        msg = str(fields.get("message", "") or fields.get("detail", "") or "")
        digest = hashlib.sha256(msg.encode("utf-8")).hexdigest()[:16] if msg else "empty"
        return f"{kind}:{category}:{digest}"
    else:
        parts = [kind]
        for key in ("market", "decision_id", "client_order_id", "incident_id", "category", "symbol"):
            val = str(fields.get(key, "") or "").strip()
            if val:
                parts.append(val)
                # limit to the first 2 dimensions to avoid key explosion
                if len(parts) >= 3:
                    break
    for key in ("reason", "state"):
        val = str(fields.get(key, "") or "").strip()
        if val:
            parts.append(f"{key}={val}")
    return ":".join(parts)


def _severity(kind: str, fields: dict[str, object] | None = None) -> int:
    # A CRITICAL incident (its ``severity`` field) logs at ERROR whatever its kind.
    if fields is not None and str(fields.get("severity", "") or "").strip().lower() == "critical":
        return logging.ERROR
    return logging.WARNING if kind in _WARNING_KINDS else logging.INFO


def emit(kind: str, *, message: str, **fields: object) -> None:
    """Emit one alert event to the log and the event journal. Never raises.

    Per-key throttling coalesces repeats within the throttle window. The key is
    (kind, ``episode_key``) when the caller passes one; ``paper_incident`` without
    an episode keys on (kind, category, incident_id) so two distinct incidents
    are both recorded.
    """
    global _suppressed_count
    try:
        if kind not in ALERT_KINDS:
            log.error("Ignored alert with unknown kind=%r", kind)
            return
        if kind in _RECONCILIATION_ALERT_KINDS:
            from parayabuck.monitoring import observe

            observe.note_reconciliation_alert()
        throttle_key = _throttle_key(kind, dict(fields))
        with _lock:
            throttle = _throttle_seconds
            now = time.monotonic()
            last = _last_emitted_at.get(throttle_key)
            if last is not None and throttle > 0.0 and now - last < throttle:
                _suppressed_count += 1
                repeats = _suppressed_by_key.get(throttle_key, 0) + 1
                _suppressed_by_key[throttle_key] = repeats
                log.info(
                    "Alert %s suppressed (throttle window %.0fs; %d repeat(s) of %s): %s",
                    kind,
                    throttle,
                    repeats,
                    throttle_key,
                    message,
                )
                return
            _last_emitted_at[throttle_key] = now
            _suppressed_by_key.pop(throttle_key, None)
            if len(_last_emitted_at) > _THROTTLE_KEYS_MAX:
                # Keys carry per-incident/order ids, so the map grows for the life
                # of the process; an entry past its window can never suppress again
                for key in [k for k, t in _last_emitted_at.items() if now - t >= throttle]:
                    del _last_emitted_at[key]
                    _suppressed_by_key.pop(key, None)
            _delivered_counts[kind] = _delivered_counts.get(kind, 0) + 1
        # The journal redacts its copy; the log line must never carry a
        # secret-named field in the clear either
        log.log(_severity(kind, fields), "ALERT %s: %s fields=%s", kind, message, redact_fields(fields))
        # Durable operational journal — best effort, never raises, isolated from trading
        try:
            from parayabuck.monitoring.journal import emit_journal
            from parayabuck.monitoring.operational_event import create_event

            ev = create_event(
                event_id=str(fields.get("event_id") or "") or None,
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
        # Opt-in off-host copy (e-mail): enqueue only, never blocks or raises.
        try:
            from parayabuck.monitoring import email_alerts

            email_alerts.notify(kind, message, dict(fields), throttle_key)
        except Exception:
            log.warning("e-mail alert hand-off failed for alert kind=%s", kind, exc_info=True)
    except Exception:  # pragma: no cover - the contract is to never raise
        log.exception("Alert emission failed for kind=%s", kind)


def alert_stats() -> dict[str, object]:
    """Process-local emission counters (accepted per kind, throttled repeats).

    When the e-mail channel is enabled, ``email`` carries its delivery counters
    (sent, failed, dropped, queued and the age of the last successful send). The
    engine writes this into its health snapshot, which ``ops status`` and the
    diagnostic bundle read.
    """
    with _lock:
        stats: dict[str, object] = {"suppressed": _suppressed_count, "delivered": dict(_delivered_counts)}
    try:
        from parayabuck.monitoring import email_alerts

        sink = email_alerts.current_sink()
        if sink is not None:
            stats["email"] = sink.delivery_status()
    except Exception:  # pragma: no cover - counters must never break a snapshot
        log.debug("e-mail delivery counters unavailable", exc_info=True)
    return stats


def reset_for_tests() -> None:
    """Clear throttle state and counters (test helper only)."""
    global _throttle_seconds, _suppressed_count
    with _lock:
        _throttle_seconds = _DEFAULT_THROTTLE_SECONDS
        _last_emitted_at.clear()
        _suppressed_by_key.clear()
        _suppressed_count = 0
        _delivered_counts.clear()


__all__ = [
    "ALERT_KINDS",
    "alert_stats",
    "emit",
    "reset_for_tests",
]
