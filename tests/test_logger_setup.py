"""setup_logging must apply an explicit level even after get_logger configured INFO."""

from __future__ import annotations

import logging

from parayabuck.logger import get_logger, setup_logging


def test_explicit_setup_reapplies_level_after_implicit_configuration() -> None:
    root = logging.getLogger("parayabuck")
    before_level = root.level
    try:
        get_logger("parayabuck.cli")  # what importing the CLI does
        setup_logging(level="DEBUG")
        assert root.level == logging.DEBUG
        setup_logging(level="WARNING")
        assert root.level == logging.WARNING
        # Re-applying never stacks a second stdout handler
        stream_handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
        assert len(stream_handlers) == 1
    finally:
        root.setLevel(before_level)
