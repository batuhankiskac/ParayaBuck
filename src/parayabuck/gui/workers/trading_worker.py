from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from parayabuck.logger import get_logger

log = get_logger("parayabuck.gui.trading_worker")


class TradingWorker(QThread):
    """Runs :class:`LiveTradingEngine` in the background.

    The engine's ``run()`` method blocks on a ``while self._running`` loop and
    sleeps ``poll_interval`` seconds between cycles. We simply call it on the
    thread's ``run()`` override; calling :meth:`stop` flips the engine's
    ``_running`` flag and joins shortly after.
    """

    status = Signal(str)
    error = Signal(str)
    started_state = Signal()
    stopped_state = Signal()

    def __init__(self, engine: Any, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._engine = engine

    def run(self) -> None:
        try:
            self.started_state.emit()
            self._engine.run()
        except Exception as e:
            log.exception("Trading engine crashed")
            self.error.emit(f"Engine crashed: {e}")
        finally:
            self.stopped_state.emit()

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception as e:
            log.warning(f"Engine.stop() raised: {e}")
