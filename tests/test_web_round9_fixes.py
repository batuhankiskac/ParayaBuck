"""Round-9 Web fixes: shutdown during an in-flight auto-start, log handler safety."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import pytest

from parayabuck.web import server_legacy
from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.managers import websocket as ws_manager
from tests.test_auto_start_retry import _REAL_SLEEP, _FlakyRuntime, _manager_with


class _BlockingFlaky(_FlakyRuntime):
    """Transient-failing runtime whose start() blocks until released."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def start(self) -> None:
        self.entered.set()
        assert self.release.wait(5)
        super().start()


def test_shutdown_during_in_flight_transient_start_does_not_wait_on_retries(monkeypatch: Any) -> None:
    flaky = _BlockingFlaky(failures=999)
    manager = _manager_with(monkeypatch, flaky)
    monkeypatch.setattr(manager, "should_auto_start", lambda: True)
    lifespan = create_lifespan(lambda: manager)

    async def run() -> None:
        async with lifespan(None):
            while not flaky.entered.is_set():
                await _REAL_SLEEP(0.001)
            # Shutdown begins while the first attempt is still in flight; it
            # then fails transiently and schedules a 5s backoff sleep.
            threading.Timer(0.05, flaky.release.set).start()

    started = time.monotonic()
    asyncio.run(asyncio.wait_for(run(), 4))
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert flaky.start_calls == 1  # no retry fired during shutdown
    assert manager.engine_state == "stopped"
    assert manager.runtime is None
    assert manager._retry_task is None


@pytest.mark.parametrize(
    "handler_cls",
    [ws_manager.WebSocketLogHandler, server_legacy.WebSocketLogHandler],
    ids=["managers", "legacy"],
)
def test_log_handler_never_raises_into_the_logging_caller(monkeypatch: Any, handler_cls: Any) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", False)
    handler = handler_cls()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("round9.web.handler")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("value %s %s", 1)  # malformed: one arg for two slots
        logger.warning("fine %s", 1)
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
