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

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_file,
            maxBytes=_FILE_MAX_BYTES,
            backupCount=_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)

    _configured = True
    return logger


def get_logger(name: str = "parayabuck") -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
