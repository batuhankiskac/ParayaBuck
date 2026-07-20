from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from parayabuck.logger import get_logger

log = get_logger("parayabuck.gui.account_worker")


class AccountWorker(QThread):
    """Polls the Alpaca account in the background and emits snapshots.

    Runs its own loop (rather than QTimer) so the blocking
    ``broker.get_account()`` network call never freezes the UI thread. The
    poll interval is configurable; ``stop()`` flips a flag checked between
    polls so the thread exits within one interval.
    """

    account_snapshot = Signal(object)  # Account dataclass
    error = Signal(str)

    def __init__(self, broker: Any, interval_seconds: int = 10, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._broker = broker
        self._interval = interval_seconds
        self._running = True

    def run(self) -> None:
        while self._running:
            try:
                acct = self._broker.get_account()
                self.account_snapshot.emit(acct)
            except Exception as e:
                log.debug(f"Account poll failed: {e}")
                self.error.emit(str(e))
            # Use short sleeps so stop() responds quickly.
            elapsed = 0.0
            while elapsed < self._interval and self._running:
                self.msleep(200)
                elapsed += 0.2

    def stop(self) -> None:
        self._running = False
