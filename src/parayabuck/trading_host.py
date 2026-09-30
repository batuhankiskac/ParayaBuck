"""Trading-host designation — the single-runner guard for one Alpaca account.

The PAPER account is shared between the GCP VM (the trading host) and every
other machine that has the credentials. Two runners on one account corrupt
the shared ledger (double-run incident, 2026-09-25), so starting the engine
requires an explicit, machine-local opt-in that only the designated host
sets (``Environment=PARAYABUCK_TRADING_HOST=1`` in the systemd unit).

Everything else keeps working without it: the web console, settings, the
backtest runner and all read-only operator views.
"""

from __future__ import annotations

import os

TRADING_HOST_ENV = "PARAYABUCK_TRADING_HOST"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

TRADING_HOST_REFUSAL = (
    "Engine start refused: this machine is not designated as the trading host. "
    f"Only one runner may trade the shared Alpaca account; set {TRADING_HOST_ENV}=1 "
    "in the service environment (deploy/systemd/parayabuck.service) on the designated "
    "host. The web console and backtests work without it."
)


def trading_host_authorized() -> bool:
    """True only where ``PARAYABUCK_TRADING_HOST`` opts this machine in."""
    return os.getenv(TRADING_HOST_ENV, "").strip().lower() in _TRUTHY


__all__ = [
    "TRADING_HOST_ENV",
    "TRADING_HOST_REFUSAL",
    "trading_host_authorized",
]
