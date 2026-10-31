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
    # The previous engine process ended without a clean stop (SIGKILL, OOM, a
    # watchdog kill) and this start is the automatic restart that followed.
    "runtime_unclean_restart",
    # A LIVE engine runtime started (manual start or standing-authorization
    # auto-start). Mailed: an unattended LIVE start must never be silent.
    "live_started",
    "runtime_stopped_unexpected",
    # Engine start failed: permanent error, a startup guard (secret scan /
    # live arming) blocking the start, or N transient auto-start retries.
    "runtime_start_failed",
    # A verified backup could not be written/verified, or copied off-host.
    "backup_failed",
    # The newest automatic backup is past the freshness bound. Raised by the
    # engine's periodic pass, so it fires even when the backup timer is dead.
    "backup_stale",
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
    # The operator cleared a halt through reset_safety_lock after a verified
    # reconcile; the message lists any halts still in force.
    "safety_lock_reset",
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
    # The retry window of a session-close decision ended with the session NOT
    # decided: the session is lost, so this is mailed as critical.
    "decision_window_expired",
    # Market data: the effective historical feed stopped being the configured
    # one (SIP entitlement downgrade to IEX) and later recovered.
    "market_data_degraded",
    # A session closed with missing stream minutes: the incomplete daily bar
    # was withheld and the SIP daily bar is fetched for the decision.
    "session_close_bar_incomplete",
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
        "signal_adjustment_failure",
        "paper_incident",
        "intent_alarm",
        "signal_source_mismatch",
        "decision_deferred",
        "decision_window_expired",
        "runtime_unclean_restart",
        "market_data_degraded",
        "session_close_bar_incomplete",
        "exit_sell_rejected",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        "alert_delivery_failed",
        "backup_stale",
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
# NOTE: ``monitoring.journal.SAFETY_JOURNAL_KINDS`` is its own explicit set, not this
# one: adding a kind here does NOT make it synchronous. Add a kind to that set too
# when its journal row must be committed before the emitting call returns.
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
        # The recovery that closes an intent alarm, so the owner learns it cleared.
        "intent_recovered",
        "reconciliation_failure",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        # Safety-state changes an operator or an unattended start made: a halt
        # cleared by hand, a drawdown baseline rebased, a LIVE engine started,
        # and equity entries failing closed on an unverified split basis.
        "safety_lock_reset",
        "risk_epoch_reset",
        "live_started",
        "signal_adjustment_failure",
        # The recoveries that close the mailed failures above, so the owner learns
        # the condition cleared (the latch clear of a reconcile is one of them).
        "reconciliation_recovered",
        "signal_adjustment_recovery",
        # The engine restarted after an unclean exit (no clean stop was recorded).
        "runtime_unclean_restart",
        # A session-close decision whose retry window ended with the session not decided.
        "decision_window_expired",
        # Market data fell back to a lower feed (and recovered), a provider
        # outage that did not halt the engine, and a backup that could not be
        # written, verified or copied off-host (the timer's unattended failure).
        "market_data_degraded",
        "data_provider_unavailable",
        "backup_failed",
        # The backup set has gone stale: the timer is dead or failing silently.
        "backup_stale",
    }
)

# Kinds the e-mail hourly cap never drops in favour of a non-critical mail:
# non-critical kinds may use only ``max_per_hour`` minus a reserve (see
# ``monitoring.email_alerts``). A CRITICAL-severity incident of any kind is
# reserved as well (its ``severity`` field).
CRITICAL_EMAIL_KINDS: frozenset[str] = frozenset(
    {
        "drawdown_halt",
        "circuit_breaker",
        "runtime_stopped_unexpected",
        "runtime_start_failed",
        "risk_incident",
        "ambiguous_order",
        "exit_sell_rejected",
        "live_authorization_expired",
        "decision_window_expired",
    }
)

__all__ = [
    "CRITICAL_EMAIL_KINDS",
    "EMAIL_ALERT_KINDS",
    "ALERT_KINDS",
    "ALERT_KINDS_ORDERED",
    "WARNING_KINDS",
]
