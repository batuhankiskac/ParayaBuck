from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from parayabuck.web import server


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
                    "poll_interval": 60,
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
                poll_interval=30,
                host="127.0.0.1",
                port=8000,
                open_browser=True,
                log_level="INFO",
            )
        )
    )

    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["app"]["timeframe"] == "5m"
    assert saved["app"]["poll_interval"] == 30
    assert manager.stop_calls == 1
    assert manager.start_calls == 1
    assert result["engine_restarted"] is True
    assert result["app_restart_required"] is False
