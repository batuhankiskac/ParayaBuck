from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

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
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)

    _configured = True
    return logger


def get_logger(name: str = "parayabuck") -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
