"""Round-9 Web fixes: shutdown during an in-flight auto-start, log handler
safety, and settings-save validation of configs without a universe block."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import HTTPException

from parayabuck.web import server_legacy
from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.managers import websocket as ws_manager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import settings as settings_router
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services.settings import SettingsService
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


def test_settings_save_validates_a_config_without_symbols_or_universe(tmp_path: Path) -> None:
    # No symbols/universe key: load_config rejects this config (no enabled
    # market), so the save must be refused instead of persisted unvalidated.
    original = yaml.safe_dump({"runtime": {"mode": "paper"}, "risk": {"daily_loss_limit_pct": 0.02}})
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(original, encoding="utf-8")
    manager.env_path = tmp_path / ".env"
    manager.get_credentials = lambda: ("key", "secret")  # type: ignore[method-assign]
    manager.engine_state = "stopped"

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(settings_router.save_settings(SettingsPayload(log_level="DEBUG"), SettingsService(manager)))

    assert excinfo.value.status_code == 400
    assert "enabled market symbol" in str(excinfo.value.detail)
    assert manager.config_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".settings.yaml.validate").exists()


def test_dashboard_anomalies_skip_crypto_dust_clear_bookkeeping(monkeypatch: Any) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE portfolio_ownership_adjustments (account_id TEXT, symbol TEXT, market TEXT, "
        "quantity REAL, kind TEXT, note TEXT, created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO portfolio_ownership_adjustments VALUES "
        "('acct', 'SPY', 'equity', 1.0, 'external_sell', 'real', '2026-09-01T00:00:00+00:00')"
    )
    # Newer dust rows would otherwise fill the LIMIT 10 window.
    for minute in range(12):
        connection.execute(
            "INSERT INTO portfolio_ownership_adjustments VALUES "
            f"('acct', 'BTC/USD', 'crypto', 1e-9, 'crypto_dust_clear', 'dust', '2026-09-02T00:{minute:02d}:00+00:00')"
        )
    monkeypatch.setattr(server_legacy, "_dashboard_readonly_connection", lambda: connection)
    monkeypatch.setattr(server_legacy, "_dashboard_account_id", lambda: "acct")

    anomalies = server_legacy._dashboard_execution()["reconciliation_anomalies"]

    assert [(row["symbol"], row["kind"]) for row in anomalies] == [("SPY", "external_sell")]
