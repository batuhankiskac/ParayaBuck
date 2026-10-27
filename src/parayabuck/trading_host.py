"""Trading-host designation — the single-runner guard for one Alpaca account.

The PAPER account is shared between the GCP VM (the trading host) and every
other machine that has the credentials. Two runners on one account corrupt
the shared ledger (double-run incident, 2026-09-25), so starting the engine
requires an explicit, machine-local opt-in that only the designated host
sets (``Environment=PARAYABUCK_TRADING_HOST=1`` in the systemd drop-in
``deploy/systemd/vm-dropin/trading-host.conf``; the template unit does not
set it).

Everything else keeps working without it: the web console, settings, the
backtest runner and all read-only operator views.
"""

from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path

TRADING_HOST_ENV = "PARAYABUCK_TRADING_HOST"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

TRADING_HOST_REFUSAL = (
    "Engine start refused: this machine is not designated as the trading host. "
    f"Only one runner may trade the shared Alpaca account; set {TRADING_HOST_ENV}=1 "
    "in the service environment on the designated host, through the systemd drop-in "
    "deploy/systemd/vm-dropin/trading-host.conf (not the template unit). "
    "The web console and backtests work without it."
)


def trading_host_authorized() -> bool:
    """True only where ``PARAYABUCK_TRADING_HOST`` opts this machine in."""
    return os.getenv(TRADING_HOST_ENV, "").strip().lower() in _TRUTHY


_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def trading_host_identity() -> str:
    """Stable, non-secret identity of this machine (hostname + machine id).

    A standing LIVE authorization is bound to it, so a copy of the signed
    file on another machine (a laptop, a rebuilt VM) never starts LIVE.
    """
    machine_id = ""
    for candidate in _MACHINE_ID_PATHS:
        try:
            machine_id = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if machine_id:
            break
    material = f"{socket.gethostname()}\0{machine_id}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


__all__ = [
    "TRADING_HOST_ENV",
    "TRADING_HOST_REFUSAL",
    "trading_host_authorized",
    "trading_host_identity",
]
