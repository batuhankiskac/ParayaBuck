from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class _QtLogHandler(logging.Handler):
    """Logging handler that emits records to a Qt signal.

    Qt signal is attached lazily via :func:`attach_qt_signal` so the logger
    module can be imported before QApplication exists.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        self._signal: Any = None

    def attach(self, signal: Any) -> None:
        self._signal = signal

    def detach(self) -> None:
        self._signal = None

    def emit(self, record: logging.LogRecord) -> None:
        if self._signal is None:
            return
        try:
            msg = self.format(record)
            # Booleans are emitted as severity + message so the UI can color
            # errors differently from info.
            self._signal.emit(record.levelno, msg)
        except Exception:
            pass


_qt_handler = _QtLogHandler()


def attach_qt_signal(signal: Any) -> None:
    """Attach a Qt signal that takes (level: int, message: str)."""
    _qt_handler.attach(signal)


def detach_qt_signal() -> None:
    _qt_handler.detach()


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    global _configured
    if _configured:
        return logging.getLogger("parayabuck")

    logger = logging.getLogger("parayabuck")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(handler)

    logger.addHandler(_qt_handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)

    _configured = True
    return logger


def get_logger(name: str = "parayabuck") -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
