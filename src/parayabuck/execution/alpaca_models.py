"""Models, errors, and constants extracted from the Alpaca execution boundary."""

from __future__ import annotations

# The one canonical broker-terminal order vocabulary. Dead states can never
# fill again (``rejected`` never filled); terminal adds ``filled``.
DEAD_BROKER_ORDER_STATUSES = frozenset({"canceled", "cancelled", "expired", "rejected", "done_for_day"})
TERMINAL_BROKER_ORDER_STATUSES = DEAD_BROKER_ORDER_STATUSES | {"filled"}


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
    """Broker ownership could not be reconciled; the runtime fails closed."""


class OwnershipDriftError(ProtectionReconciliationError):
    """The broker holds fewer shares than the portfolio repository claims as
    managed. With mixed inventory the remaining shares cannot be attributed to
    the bot safely, so entries halt until an operator reconciles manually.

    ``symbols`` names the canonical symbols the drift was found on (empty when
    the raise site cannot attribute it). With ``runtime.per_symbol_drift_lock``
    off the runtime halts as before; the attribute is diagnostic only there.
    """

    def __init__(self, message: str = "", *, symbols: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.symbols: tuple[str, ...] = tuple(symbols)


class EmptyPositionsReadError(RuntimeError):
    """The broker returned no positions twice while the bot's ledger owns
    inventory. Treated as a failed snapshot (retried, and a reconcile is
    scheduled), not as proof that the account is flat."""


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
    "DEAD_BROKER_ORDER_STATUSES",
    "TERMINAL_BROKER_ORDER_STATUSES",
    "AmbiguousOrderState",
    "DefiniteOrderRejection",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "RiskStatePersistenceError",
]
