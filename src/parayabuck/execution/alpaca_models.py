"""Models, errors, and constants extracted from the Alpaca execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

CRYPTO_TAKER_FEE_RESERVE = Decimal("0.0025")


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


class ReconciliationObservationError(RuntimeError):
    """Authoritative local or broker order state could not be observed."""


class OwnershipDriftError(ProtectionReconciliationError):
    """The broker holds fewer shares than the portfolio repository claims as
    managed. With mixed inventory the remaining shares cannot be attributed to
    the bot safely, so entries halt until an operator reconciles manually."""


@dataclass(frozen=True)
class CryptoPriceObservation:
    symbol: str
    high: float
    low: float
    source: str
    timestamp: datetime


@dataclass(frozen=True)
class QuantityResolution:
    verified: bool
    available_qty: float
    source: str


__all__ = [
    "CRYPTO_TAKER_FEE_RESERVE",
    "AmbiguousOrderState",
    "CryptoPriceObservation",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "QuantityResolution",
    "RiskStatePersistenceError",
]
