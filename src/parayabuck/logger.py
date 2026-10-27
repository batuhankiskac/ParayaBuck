from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from parayabuck.monitoring.operational_event import redact_text

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# 10 MB per file, five rotated backups: bounded disk use even under a
# firehose of DEBUG logs from a long-running paper runtime.
_FILE_MAX_BYTES = 10 * 1024 * 1024
_FILE_BACKUP_COUNT = 5

_configured = False


class RedactingFormatter(logging.Formatter):
    """Formatter that scrubs the complete formatted record, traceback included.

    The scrub runs at the sink, so a ``log.exception`` traceback or a ``%s`` of an
    exception text is redacted before stderr, the log file or journald sees it,
    whatever the call site logged.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _pin_library_loggers() -> None:
    """Keep the ``websockets.*`` loggers at WARNING.

    At DEBUG the library logs the Alpaca auth frame, which carries the secret key.
    Nothing in ParayaBuck wants those frames, so the level is pinned on the parent
    and on every child already created; later children inherit it.
    """
    names = [name for name in list(logging.Logger.manager.loggerDict) if name.startswith("websockets.")]
    for name in ("websockets", *names):
        logging.getLogger(name).setLevel(logging.WARNING)


def _install_stream_handler(logger: logging.Logger) -> None:
    """Send log lines to stderr (journald captures both streams under systemd).

    WARNING lines on stdout made ``--json`` output unparseable on the paths that
    warn (D2-6-1), so the console sink never shares stdout with command results.
    """
    global _configured
    _pin_library_loggers()
    if _configured:
        return
    logger.propagate = False
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(handler)
    _configured = True


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    """Configure the ``parayabuck`` logger; an explicit call always applies.

    Importing the CLI configures INFO through ``get_logger`` before the web
    entry point reads ``app.log_level``/``LOG_LEVEL``, so the level (and an
    optional file sink) must be re-applied here instead of being ignored.
    """
    logger = logging.getLogger("parayabuck")
    _install_stream_handler(logger)
    _pin_library_loggers()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if log_file:
        target = str(Path(log_file).resolve())
        has_file = any(
            isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == target for h in logger.handlers
        )
        if not has_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                log_file,
                maxBytes=_FILE_MAX_BYTES,
                backupCount=_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            fh.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
            logger.addHandler(fh)
    return logger


def get_logger(name: str = "parayabuck") -> logging.Logger:
    if not _configured:
        root = logging.getLogger("parayabuck")
        _install_stream_handler(root)
        if root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
    return logging.getLogger(name)
