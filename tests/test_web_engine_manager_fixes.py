"""EngineManager: credential reads and the start-time broker hand-over."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
import parayabuck.web.managers.engine as engine_module
from parayabuck.web.managers.engine import EngineManager


def test_get_credentials_reads_only_the_two_keys_without_touching_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("ALPACA_API_KEY=file-key\nALPACA_SECRET_KEY=file-secret\nLOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ALPACA_API_KEY", "process-key")
    manager = EngineManager()
    manager.env_path = env_file

    assert manager.get_credentials() == ("file-key", "file-secret")
    # Nothing else in the file was pushed into the process environment
    assert os.environ["LOG_LEVEL"] == "WARNING"
    assert os.environ["ALPACA_API_KEY"] == "process-key"
    # A key missing from the file falls back to the process environment
    env_file.write_text("ALPACA_SECRET_KEY=file-secret\n", encoding="utf-8")
    assert manager.get_credentials() == ("process-key", "file-secret")


class _Broker:
    def __init__(self) -> None:
        self.closed = False
        self.account_id = "acct"

    def close(self) -> None:
        self.closed = True


def test_start_closes_an_account_broker_built_while_the_runtime_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An /api/account poll that lands after runtime.start() but before the
    runtime broker is installed lazily builds a broker; the hand-over must
    close it, never orphan it."""
    manager = EngineManager()
    lazy = _Broker()
    runtime_broker = _Broker()
    armed = {"poll": False}
    real_state_lock = manager._state_lock

    class _PollOnEnter:
        """State lock whose first acquisition after start simulates the poll."""

        def __enter__(self) -> Any:
            if armed["poll"]:
                armed["poll"] = False
                manager.broker = lazy
            return real_state_lock.__enter__()

        def __exit__(self, *exc: Any) -> Any:
            return real_state_lock.__exit__(*exc)

    class _Runtime:
        def __init__(self, **_kwargs: Any) -> None:
            self.broker = runtime_broker

        def start(self) -> None:
            armed["poll"] = True

        def stop(self) -> bool:
            return True

    manager._state_lock = _PollOnEnter()  # type: ignore[assignment]

    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setattr(runtime_module, "EngineRuntime", _Runtime)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda _root: None)
    monkeypatch.setattr(
        engine_module,
        "load_config",
        lambda _path: SimpleNamespace(runtime=SimpleNamespace(mode=SimpleNamespace(value="paper"))),
    )
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

    manager.start_engine()

    assert manager.engine_state == "running"
    assert manager.broker is runtime_broker
    assert lazy.closed is True
    assert runtime_broker.closed is False
