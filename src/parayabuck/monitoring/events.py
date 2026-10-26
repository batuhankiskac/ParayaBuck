"""Single source of truth for alert/incident event vocabulary.

Defines the canonical set of alert kinds and their severity classification.
``parayabuck.monitoring.alerts`` and the operational event journal import
from here so the vocabulary cannot drift.

``ALERT_KINDS`` is the full vocabulary (including ``paper_incident``,
``paper_receipt`` and ``daily_summary``). Every accepted kind goes to the
local log and the operational event journal. Off-host delivery is the opt-in
e-mail channel only (``monitoring.email_alerts``, ``alerting.email``), which
forwards the subset in ``EMAIL_ALERT_KINDS``.
"""

from __future__ import annotations

ALERT_KINDS_ORDERED: tuple[str, ...] = (
    "runtime_started",
    "runtime_stopped_unexpected",
    # Engine start failed: permanent error, a startup guard (secret scan /
    # live arming) blocking the start, or N transient auto-start retries.
    "runtime_start_failed",
    # A verified backup could not be written/verified, or the newest backup is
    # older than the freshness bound (the backup timer stopped working).
    "backup_failed",
    # Warning-level data-provider outage (paper_incident.emit_incident_record
    # maps a non-critical PROVIDER_UNAVAILABLE incident here).
    "data_provider_unavailable",
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
    # A session-close decision was re-queued for a bounded retry because a
    # recoverable gate (breaker, reconcile failure, readiness, snapshot
    # lease, transient broker fetch) was active at decision time. Emitted
    # once per deferred session, never per retry attempt.
    "decision_deferred",
    # Market data: the effective historical feed stopped being the configured
    # one (SIP entitlement downgrade to IEX) and later recovered.
    "market_data_degraded",
    # A session closed with missing stream minutes: the incomplete daily bar
    # was withheld and the SIP daily bar is fetched for the decision.
    "session_close_bar_incomplete",
    # A broker FILL activity carried a side outside Alpaca's vocabulary: it is
    # skipped and reported, never fatal for reconciliation.
    "reconciliation_unexpected_fill_side",
    # A broker-rejected SELL that would exit or reduce the held position:
    # escalated beyond ``order_rejected`` (e-mailed when e-mail is enabled)
    # and retried once with a new deterministic client order id.
    "exit_sell_rejected",
    # LIVE is configured but the engine cannot start unattended: no valid
    # standing LIVE authorization, so the owner must re-arm.
    "live_rearm_required",
    # The standing LIVE authorization expires soon / has expired.
    "live_authorization_expiring",
    "live_authorization_expired",
    # An off-host delivery (e-mail) failed or was dropped; journal only.
    "alert_delivery_failed",
)

ALERT_KINDS: frozenset[str] = frozenset(ALERT_KINDS_ORDERED)

# Kinds that describe a failure/incident are logged at WARNING; routine
# lifecycle/recovery events at INFO. Kept next to the kinds so the severity
# table cannot drift from the event vocabulary.
WARNING_KINDS: frozenset[str] = frozenset(
    {
        "runtime_stopped_unexpected",
        "runtime_start_failed",
        "backup_failed",
        "data_provider_unavailable",
        "order_rejected",
        "ambiguous_order",
        "reconciliation_failure",
        "risk_incident",
        "drawdown_halt",
        "circuit_breaker",
        "paper_incident",
        "intent_alarm",
        "signal_source_mismatch",
        "decision_deferred",
        "market_data_degraded",
        "session_close_bar_incomplete",
        "reconciliation_unexpected_fill_side",
        "exit_sell_rejected",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        "alert_delivery_failed",
    }
)

# Kinds forwarded by the opt-in e-mail channel: the engine stopped, halted or
# failed to start, safety incidents and halts, rejected orders (a rejected
# exit SELL above all), intent/over-target alarms, reconciliation drift and
# the LIVE re-arm / standing-authorization notices, plus market-data
# degradation, provider outages and backup failures (the last two are the
# unattended-failure signals the owner would otherwise only find in the
# journal). ``reconciliation_failure`` already covers the reconciliation and
# ownership/protection halts (their reasons travel in the ``reason`` field).
# ``daily_summary`` is added only when ``alerting.email.daily_summary`` is true.
EMAIL_ALERT_KINDS: frozenset[str] = frozenset(
    {
        "runtime_stopped_unexpected",
        "runtime_start_failed",
        "circuit_breaker",
        "drawdown_halt",
        "risk_incident",
        "ambiguous_order",
        "paper_incident",
        "order_rejected",
        "exit_sell_rejected",
        "intent_alarm",
        "reconciliation_failure",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        # Market data fell back to a lower feed (and recovered), a provider
        # outage that did not halt the engine, and a backup that could not be
        # written, verified or copied off-host (the timer's unattended failure).
        "market_data_degraded",
        "data_provider_unavailable",
        "backup_failed",
    }
)

__all__ = [
    "EMAIL_ALERT_KINDS",
    "ALERT_KINDS",
    "ALERT_KINDS_ORDERED",
    "WARNING_KINDS",
]
