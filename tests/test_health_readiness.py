from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from parayabuck.config import AppConfig, Config
from parayabuck.web import server

# Health vs readiness contract (documented in docs/operations.md):
#   /healthz — process liveness ONLY. Returns 200 {"status": "ok"} whenever
#              the web process can serve the route, regardless of config,
#              credentials, or engine state. This is the container health
#              probe: an alive control plane must never be restarted because
#              paper credentials are absent or the engine is intentionally
#              stopped.
#   /readyz  — layered deployment readiness. 503 unless BOTH the config is
#              valid AND (trading is not expected OR the expected runtime is
#              actually healthy). A process can be alive but not ready.
#
# Tests follow the FastAPI route-call pattern from tests/test_web_g9_g7.py:
# routes are invoked with asyncio.run() against a monkeypatched manager and
# config loader, with no network or broker access.


def _healthy_runtime_manager() -> Any:
    class HealthyManager:
        config_path = None

        def current_engine_state(self) -> str:
            return "running"

        def has_active_runtime(self) -> bool:
            return True

        @property
        def runtime(self) -> Any:
            health = SimpleNamespace(state="running", healthy=True)
            return SimpleNamespace(health=lambda: health)

    return HealthyManager()


def _stopped_manager() -> Any:
    class StoppedManager:
        config_path = None

        def current_engine_state(self) -> str:
            return "stopped"

        def has_active_runtime(self) -> bool:
            return False

        @property
        def runtime(self) -> Any:
            return None

    return StoppedManager()


def _dead_engine_manager(state: str) -> Any:
    class DeadEngineManager:
        config_path = None

        def __init__(self, engine_state: str) -> None:
            self._state = engine_state

        def current_engine_state(self) -> str:
            return self._state

        def has_active_runtime(self) -> bool:
            return self._state == "running"

        @property
        def runtime(self) -> Any:
            if self._state != "running":
                return None
            health = SimpleNamespace(state=self._state, healthy=False)
            return SimpleNamespace(health=lambda: health)

    return DeadEngineManager(state)


def test_healthz_is_unconditional_process_liveness(monkeypatch: Any) -> None:
    """/healthz returns ok even with a broken config and a dead engine: the
    probe answers 'is the process up', not 'is it ready to trade'."""
    monkeypatch.setattr(server, "manager", _dead_engine_manager("error"))
    monkeypatch.setattr(server, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml")))

    result = asyncio.run(server.healthz())

    assert result == {"status": "ok"}


def test_alive_but_not_ready_healthz_200_readyz_503(monkeypatch: Any) -> None:
    """The canonical alive-but-not-ready state: auto-start was expected and
    the engine died. /healthz stays 200 (container stays up), /readyz is 503
    (deployment is not trading)."""
    monkeypatch.setattr(server, "manager", _dead_engine_manager("error"))
    monkeypatch.setattr(server, "load_config", lambda _path: Config())

    health = asyncio.run(server.healthz())
    assert health == {"status": "ok"}

    ready = asyncio.run(server.readyz())
    payload = json.loads(ready.body)
    assert ready.status_code == 503
    assert payload["process_alive"] is True
    assert payload["config_valid"] is True
    assert payload["trading_expected"] is True
    assert payload["trading_ready"] is False
    assert payload["ready"] is False


def test_readyz_invalid_config_never_ready_even_when_engine_healthy(
    monkeypatch: Any,
) -> None:
    """Config validity is the first readiness layer: a healthy engine cannot
    compensate for a configuration that fails to load."""
    monkeypatch.setattr(server, "manager", _healthy_runtime_manager())
    monkeypatch.setattr(server, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml")))

    health = asyncio.run(server.healthz())
    assert health == {"status": "ok"}

    ready = asyncio.run(server.readyz())
    payload = json.loads(ready.body)
    assert ready.status_code == 503
    assert payload["config_valid"] is False
    assert payload["process_alive"] is True
    assert payload["ready"] is False


def test_readyz_healthy_expected_runtime_is_200(monkeypatch: Any) -> None:
    """Config valid + trading expected + runtime healthy -> fully ready."""
    monkeypatch.setattr(server, "manager", _healthy_runtime_manager())
    monkeypatch.setattr(server, "load_config", lambda _path: Config())

    ready = asyncio.run(server.readyz())
    payload = json.loads(ready.body)
    assert ready.status_code == 200
    assert payload["ready"] is True
    assert payload["config_valid"] is True
    assert payload["trading_expected"] is True
    assert payload["trading_ready"] is True


def test_readyz_control_plane_ready_when_trading_not_expected(monkeypatch: Any) -> None:
    """auto_start_engine=false with an intentionally stopped engine: trading
    is not expected, so the control plane is ready even though no engine is
    running. The container health probe (/healthz) stays the liveness gate."""
    monkeypatch.setattr(server, "manager", _stopped_manager())
    monkeypatch.setattr(server, "load_config", lambda _path: Config(app=AppConfig(auto_start_engine=False)))

    ready = asyncio.run(server.readyz())
    payload = json.loads(ready.body)
    assert ready.status_code == 200
    assert payload["ready"] is True
    assert payload["trading_expected"] is False
    assert payload["trading_ready"] is False
