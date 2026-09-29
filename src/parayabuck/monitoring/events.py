"""Single source of truth for alert/incident event vocabulary.

Defines the canonical set of alert kinds and their severity classification.
Both ``parayabuck.config.AlertingConfig`` and ``parayabuck.monitoring.alerts``
import from here so the vocabulary cannot drift.

Webhook default choice
----------------------
``ALERT_KINDS`` is the full vocabulary (including ``paper_incident``,
``paper_receipt`` and ``daily_summary``). Any kind in this set is valid in
``alerting.events`` and will pass ``AlertingConfig`` validation.

``DEFAULT_ALERT_EVENTS`` is the webhook-enabled default. It equals
``ALERT_KINDS_ORDERED`` with ``paper_receipt`` excluded:

* ``paper_receipt`` is emitted on every scheduled weekly equity decision
  (``receipt.py:build_receipt`` / ``write``). It is routine, high-volume
  lifecycle telemetry, not an operator-actionable failure. Enabling it on the
  webhook by default would spam the operator channel.
* ``paper_incident`` is an incident/failure signal (reconciliation drift,
  DB integrity) and **is** webhook-enabled by default.

Operators who want receipt webhooks can explicitly add ``paper_receipt`` to
``alerting.events`` in ``config/settings.yaml`` or via
``configure_alerting(events=...)``. Excluding it by default preserves the
pre-existing operator semantics where ``config/settings.yaml`` listed 12-14
failure/lifecycle kinds without ``paper_receipt``, while still accepting
``paper_receipt`` as a valid configurable kind (drift test passes).
"""

from __future__ import annotations

ALERT_KINDS_ORDERED: tuple[str, ...] = (
    "runtime_started",
    "runtime_stopped_unexpected",
    # Warning-level provider incidents (paper_incident.emit_incident_record
    # maps PROVIDER_UNAVAILABLE here); the name predates the equity-only runtime.
    "scanner_refresh_failure",
    "order_rejected",
    "ambiguous_order",
    "reconciliation_failure",
    "reconciliation_recovered",
    "risk_incident",
    "drawdown_halt",
    "risk_epoch_reset",
    "circuit_breaker",
    "signal_adjustment_failure",
    "signal_adjustment_recovery",
    "paper_incident",
    "paper_receipt",
    # One-line operator summary after each equity session-close decision.
    "daily_summary",
    # Intent watchdog: a scheduled decision or its fills did not happen.
    "intent_alarm",
    "intent_recovered",
    # Signal cross-check: the IEX stream-built and SIP daily bars disagree on
    # the trend (position held), diverge in close, or the SIP bar was missing.
    "signal_source_mismatch",
)

ALERT_KINDS: frozenset[str] = frozenset(ALERT_KINDS_ORDERED)

# Kinds that describe a failure/incident are logged at WARNING; routine
# lifecycle/recovery events at INFO. Kept next to the kinds so the severity
# table cannot drift from the event vocabulary.
WARNING_KINDS: frozenset[str] = frozenset(
    {
        "runtime_stopped_unexpected",
        "scanner_refresh_failure",
        "order_rejected",
        "ambiguous_order",
        "reconciliation_failure",
        "risk_incident",
        "drawdown_halt",
        "circuit_breaker",
        "paper_incident",
        "intent_alarm",
        "signal_source_mismatch",
    }
)

# Webhook default: all ordered kinds except the high-volume receipt.
DEFAULT_ALERT_EVENTS: tuple[str, ...] = tuple(kind for kind in ALERT_KINDS_ORDERED if kind != "paper_receipt")

__all__ = [
    "ALERT_KINDS",
    "ALERT_KINDS_ORDERED",
    "DEFAULT_ALERT_EVENTS",
    "WARNING_KINDS",
]
