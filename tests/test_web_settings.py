from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import WebSocketDisconnect
from starlette.requests import Request

from parayabuck import runtime as runtime_module
from parayabuck.web import server


class _EngineManagerStub:
    def __init__(self, root: Path) -> None:
        self.config_path = root / "settings.yaml"
        self.env_path = root / ".env"
        self.engine_state = "running"
        self.broker: Any = object()
        self.log_queues: set[Any] = set()
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


def test_control_token_and_index_are_not_cached() -> None:
    token_response = asyncio.run(server.get_control_token())
    assert token_response.headers["cache-control"] == "no-store"
    assert json.loads(token_response.body)["control_token"] == server.manager.control_token

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )
    index_response = asyncio.run(server.serve_index(request))
    assert index_response.headers["cache-control"] == "no-store"


class _RestartFailureManagerStub(_EngineManagerStub):
    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "error"
        raise TimeoutError("Alpaca timed out")


def test_saving_runtime_settings_restarts_engine_without_restarting_web_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = _EngineManagerStub(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "alpaca": {"paper": True},
                "app": {
                    "host": "127.0.0.1",
                    "port": 8000,
                    "open_browser": True,
                    "log_level": "INFO",
                },
                "engine": {
                    "timeframe": "15m",
                    "risk": {
                        "daily_loss_limit_pct": 0.015,
                        "max_drawdown_pct": 0.10,
                    },
                },
            }
        )
    )
    monkeypatch.setattr(server, "manager", manager)

    result = asyncio.run(
        server.save_settings(
            server.SettingsPayload(
                paper_mode=True,
                daily_loss_limit_pct=2.0,
                max_drawdown_pct=12.0,
                timeframe="5m",
                host="127.0.0.1",
                port=8000,
                open_browser=True,
                log_level="INFO",
            )
        )
    )

    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["engine"]["timeframe"] == "5m"
    assert saved["engine"]["risk"]["daily_loss_limit_pct"] == 0.02
    assert saved["engine"]["risk"]["max_drawdown_pct"] == 0.12
    assert manager.stop_calls == 1
    assert manager.start_calls == 1
    assert result["engine_restarted"] is True
    assert result["app_restart_required"] is False


def test_settings_save_succeeds_when_automatic_restart_times_out(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = _RestartFailureManagerStub(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump({"alpaca": {"paper": True}, "engine": {"risk": {}}})
    )
    monkeypatch.setattr(server, "manager", manager)

    result = asyncio.run(server.save_settings(server.SettingsPayload(timeframe="5m")))

    assert result["status"] == "success"
    assert result["engine_restarted"] is False
    assert result["restart_error"] == "Alpaca timed out"
    assert yaml.safe_load(manager.config_path.read_text())["engine"]["timeframe"] == "5m"


def test_application_shutdown_stops_running_engine(monkeypatch: Any) -> None:
    class ShutdownManager:
        stop_calls = 0

        def stop_engine(self) -> None:
            self.stop_calls += 1

    manager = ShutdownManager()
    monkeypatch.setattr(server, "manager", manager)

    async def run_lifespan() -> None:
        async with server.app_lifespan(server.app):
            pass

    asyncio.run(run_lifespan())

    assert manager.stop_calls == 1


def test_web_engine_manager_uses_runtime(monkeypatch: Any) -> None:
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

    monkeypatch.setattr(runtime_module, "EngineRuntime", FakeRuntime)
    manager = server.EngineManager()
    manager.config_path = Path("config/settings.yaml")
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

    manager.start_engine()
    assert manager.engine_state == "running"
    assert calls == ["init", "start"]

    manager.stop_engine()
    assert manager.engine_state == "stopped"
    assert calls == ["init", "start", "stop"]


class _DeadWebSocket:
    """Simulates a browser that vanished without a clean close handshake."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.sent: list[Any] = []

    async def accept(self) -> None:
        return

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        raise WebSocketDisconnect()


class _WebSocketManagerStub:
    def __init__(self) -> None:
        self.log_queues: set[Any] = set()
        self.stream_connections: set[Any] = set()

    def fetch_account_snapshot(self) -> dict[str, Any]:
        return {"equity": 1.0}

    def current_engine_state(self) -> str:
        return "stopped"

    def is_paper_mode(self) -> bool:
        return True


def test_websocket_stream_detects_dead_browser_and_cleans_up(monkeypatch: Any) -> None:
    manager = _WebSocketManagerStub()
    monkeypatch.setattr(server, "manager", manager)
    websocket = _DeadWebSocket()

    asyncio.run(server.websocket_stream(websocket))  # must exit quietly, not spin

    assert len(websocket.sent) == 1
    assert websocket not in manager.stream_connections


def test_websocket_logs_detects_dead_browser_and_cleans_queue(monkeypatch: Any) -> None:
    manager = _WebSocketManagerStub()
    monkeypatch.setattr(server, "manager", manager)
    websocket = _DeadWebSocket()

    asyncio.run(server.websocket_logs(websocket))  # must exit quietly, not spin

    assert manager.log_queues == set()
