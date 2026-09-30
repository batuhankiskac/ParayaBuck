"""Models, errors, and constants extracted from the Alpaca execution boundary."""

from __future__ import annotations

# Bounded wait for the broker to confirm a protective-order cancellation
# before a replacement is submitted.
STOP_CANCEL_TIMEOUT_SECONDS = 5.0
STOP_CANCEL_POLL_INTERVAL_SECONDS = 0.25


class RiskStatePersistenceError(RuntimeError):
    """Peak equity sidecar is present but unreadable or invalid.

    Raised when ``paper_equity_peak.json`` / ``live_equity_peak.json`` exists
    but is malformed, truncated, non-finite, negative, or unreadable. The
    caller must halt entries fail-closed rather than fall back to 0.0 and
    erase the drawdown watermark. Missing file bootstraps to 0.0.
    """


class AmbiguousOrderState(RuntimeError):
    pass


class ProtectionReconciliationError(RuntimeError):
    pass


class OwnershipDriftError(ProtectionReconciliationError):
    """The broker holds fewer shares than the portfolio repository claims as
    managed. With mixed inventory the remaining shares cannot be attributed to
    the bot safely, so entries halt until an operator reconciles manually."""


class DefiniteOrderRejection(RuntimeError):
    """The broker definitively refused the order; the outcome is KNOWN.

    A 4xx submit failure (for example 403 or 422) combined with a recovery
    lookup that proves the broker never accepted the client order id, or a
    broker-confirmed terminal status for that id, leaves no ambiguity: the
    order does not exist at the broker. The runtime books the row as
    ``rejected``, alerts ``order_rejected`` and keeps running — a rejection is
    a re-plannable outcome, not a safety incident.
    """


__all__ = [
    "STOP_CANCEL_POLL_INTERVAL_SECONDS",
    "STOP_CANCEL_TIMEOUT_SECONDS",
    "AmbiguousOrderState",
    "DefiniteOrderRejection",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "RiskStatePersistenceError",
]
