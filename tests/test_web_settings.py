from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from parayabuck.web import server
from parayabuck_v2 import runtime as runtime_module


class _EngineManagerStub:
    def __init__(self, root: Path) -> None:
        self.config_path = root / "settings.yaml"
        self.env_path = root / ".env"
        self.engine_state = "running"
        self.broker: Any = object()
        self.stop_calls = 0
        self.start_calls = 0

    def get_credentials(self) -> tuple[str, str]:
        return "existing-key", "existing-secret"

    def stop_engine(self) -> None:
        self.stop_calls += 1
        self.engine_state = "stopped"

    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "running"


def test_saving_runtime_settings_restarts_engine_without_restarting_web_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = _EngineManagerStub(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "alpaca": {"paper": True},
                "risk": {"max_daily_loss": 300.0, "max_drawdown": 1500.0},
                "app": {
                    "host": "127.0.0.1",
                    "port": 8000,
                    "open_browser": True,
                    "log_level": "INFO",
                    "timeframe": "15m",
                },
            }
        )
    )
    monkeypatch.setattr(server, "manager", manager)

    result = asyncio.run(
        server.save_settings(
            server.SettingsPayload(
                paper_mode=True,
                max_daily_loss=300,
                max_drawdown=1500,
                timeframe="5m",
                host="127.0.0.1",
                port=8000,
                open_browser=True,
                log_level="INFO",
            )
        )
    )

    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["app"]["timeframe"] == "5m"
    assert manager.stop_calls == 1
    assert manager.start_calls == 1
    assert result["engine_restarted"] is True
    assert result["app_restart_required"] is False


def test_web_engine_manager_uses_v2_runtime(monkeypatch: Any) -> None:
    calls: list[str] = []

    class FakeRuntime:
        def __init__(self, **kwargs: Any) -> None:
            calls.append("init")
            self.engine = object()
            self.broker = object()
            self.is_running = True

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")
            self.is_running = False

    monkeypatch.setattr(runtime_module, "V2Runtime", FakeRuntime)
    manager = server.EngineManager()
    manager.config_path = Path("config/settings.yaml")
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

    manager.start_engine()
    assert manager.engine_state == "running"
    assert calls == ["init", "start"]

    manager.stop_engine()
    assert manager.engine_state == "stopped"
    assert calls == ["init", "start", "stop"]
