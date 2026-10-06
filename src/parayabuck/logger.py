from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# 10 MB per file, five rotated backups: bounded disk use even under a
# firehose of DEBUG logs from a long-running paper runtime.
_FILE_MAX_BYTES = 10 * 1024 * 1024
_FILE_BACKUP_COUNT = 5

_configured = False


def _install_stdout_handler(logger: logging.Logger) -> None:
    global _configured
    if _configured:
        return
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(handler)
    _configured = True


def setup_logging(level: str = "INFO", log_file: str | None = None) -> logging.Logger:
    """Configure the ``parayabuck`` logger; an explicit call always applies.

    Importing the CLI configures INFO through ``get_logger`` before the web
    entry point reads ``app.log_level``/``LOG_LEVEL``, so the level (and an
    optional file sink) must be re-applied here instead of being ignored.
    """
    logger = logging.getLogger("parayabuck")
    _install_stdout_handler(logger)
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
            fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            logger.addHandler(fh)
    return logger


def get_logger(name: str = "parayabuck") -> logging.Logger:
    if not _configured:
        root = logging.getLogger("parayabuck")
        _install_stdout_handler(root)
        if root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
    return logging.getLogger(name)
