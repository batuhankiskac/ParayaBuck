"""Minimal systemd notification sender (sd_notify protocol, stdlib only).

systemd passes ``NOTIFY_SOCKET`` to a ``Type=notify`` service. A datagram sent
to that AF_UNIX socket carries ``READY=1`` (startup finished) or
``WATCHDOG=1`` (liveness ping for ``WatchdogSec=``). Outside systemd the
variable is unset and every call is a no-op returning False.
"""

from __future__ import annotations

import os
import socket

__all__ = ["notify", "ready", "watchdog"]


def notify(state: str) -> bool:
    """Send one notification datagram; True when it was delivered.

    Never raises: a missing or unreachable socket must not take the trading
    loop down with it. A leading ``@`` names an abstract-namespace socket.
    """
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode("utf-8"))
    except OSError:
        return False
    return True


def ready() -> bool:
    """Tell systemd that startup has finished."""
    return notify("READY=1")


def watchdog() -> bool:
    """Feed the systemd watchdog; call only after a completed iteration."""
    return notify("WATCHDOG=1")
