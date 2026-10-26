"""Minimal systemd notification sender (sd_notify protocol, stdlib only).

systemd passes ``NOTIFY_SOCKET`` to a ``Type=notify`` service. A datagram sent
to that AF_UNIX socket carries ``READY=1`` (startup finished) or
``WATCHDOG=1`` (liveness ping for ``WatchdogSec=``). Outside systemd the
variable is unset and every call is a no-op returning False.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable

__all__ = ["WatchdogPinger", "notify", "ready", "watchdog"]


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
    """Feed the systemd watchdog once."""
    return notify("WATCHDOG=1")


class WatchdogPinger:
    """Background ``WATCHDOG=1`` sender gated by a health predicate.

    The predicate is evaluated on every tick; a ping is sent only while it
    returns True. A predicate that raises counts as unhealthy, so the pings
    stop and systemd restarts the unit once ``WatchdogSec`` elapses. Start
    and stop are idempotent. Outside systemd every ping is a no-op.
    """

    def __init__(
        self,
        healthy: Callable[[], bool],
        *,
        interval_seconds: float = 30.0,
        send: Callable[[str], bool] | None = None,
    ) -> None:
        self._healthy = healthy
        self._interval = float(interval_seconds)
        self._send = send if send is not None else notify
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def ping_once(self) -> bool:
        """Evaluate the gate once; send ``WATCHDOG=1`` when healthy."""
        try:
            healthy = bool(self._healthy())
        except Exception:
            healthy = False
        if not healthy:
            return False
        return self._send("WATCHDOG=1")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="pb-watchdog", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.ping_once()
            self._stop_event.wait(self._interval)
