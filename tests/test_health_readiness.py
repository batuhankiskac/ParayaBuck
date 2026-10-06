from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from parayabuck.config import AppConfig, Config
from parayabuck.web.routers import health as health_router
from tests.helpers import web_client

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
# Routes are served by an app composed around a fake manager (TestClient,
# no lifespan) with a monkeypatched config loader; no network or broker.


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
    client = web_client(_dead_engine_manager("error"))
    monkeypatch.setattr(health_router, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml")))

    result = client.get("/healthz").json()

    assert result == {"status": "ok"}


def test_alive_but_not_ready_healthz_200_readyz_503(monkeypatch: Any) -> None:
    """The canonical alive-but-not-ready state: auto-start was expected and
    the engine died. /healthz stays 200 (container stays up), /readyz is 503
    (deployment is not trading)."""
    client = web_client(_dead_engine_manager("error"))
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())

    health = client.get("/healthz").json()
    assert health == {"status": "ok"}

    ready = client.get("/readyz")
    payload = ready.json()
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
    client = web_client(_healthy_runtime_manager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml")))

    health = client.get("/healthz").json()
    assert health == {"status": "ok"}

    ready = client.get("/readyz")
    payload = ready.json()
    assert ready.status_code == 503
    assert payload["config_valid"] is False
    assert payload["process_alive"] is True
    assert payload["ready"] is False


def test_readyz_healthy_expected_runtime_is_200(monkeypatch: Any) -> None:
    """Config valid + trading expected + runtime healthy -> fully ready."""
    client = web_client(_healthy_runtime_manager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())

    ready = client.get("/readyz")
    payload = ready.json()
    assert ready.status_code == 200
    assert payload["ready"] is True
    assert payload["config_valid"] is True
    assert payload["trading_expected"] is True
    assert payload["trading_ready"] is True


def test_readyz_control_plane_ready_when_trading_not_expected(monkeypatch: Any) -> None:
    """auto_start_engine=false with an intentionally stopped engine: trading
    is not expected, so the control plane is ready even though no engine is
    running. The container health probe (/healthz) stays the liveness gate."""
    client = web_client(_stopped_manager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config(app=AppConfig(auto_start_engine=False)))

    ready = client.get("/readyz")
    payload = ready.json()
    assert ready.status_code == 200
    assert payload["ready"] is True
    assert payload["trading_expected"] is False
    assert payload["trading_ready"] is False


def _failing_start_manager(state: str, error: str | None, attempt: int) -> Any:
    class FailingStartManager:
        config_path = None
        last_engine_error = error
        _retry_attempt = attempt

        def current_engine_state(self) -> str:
            return state

        def has_active_runtime(self) -> bool:
            # 'starting' counts as active: the manager is mid start-up.
            return state in {"running", "starting"}

        @property
        def runtime(self) -> Any:
            return None

    return FailingStartManager()


def test_readyz_503_reports_the_start_failure(monkeypatch: Any) -> None:
    """A failed start must be visible to an out-of-process probe (/healthz
    stays 200 by design), including why it failed."""
    client = web_client(_failing_start_manager("error", "Alpaca rejected the API key (401)", 0))
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())

    ready = client.get("/readyz")
    payload = ready.json()
    assert ready.status_code == 503
    assert payload["ready"] is False
    assert payload["engine_state"] == "error"
    assert payload["engine_error"] == "Alpaca rejected the API key (401)"


def test_readyz_503_while_a_start_keeps_retrying(monkeypatch: Any) -> None:
    """An endless transient retry loop is not trading either: the state stays
    'starting', so the attempt count is the only progress signal."""
    client = web_client(_failing_start_manager("starting", "transient: timeout", 47))
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())

    ready = client.get("/readyz")
    payload = ready.json()
    assert ready.status_code == 503
    assert payload["trading_expected"] is True
    assert payload["engine_state"] == "starting"
    assert payload["start_retry_attempt"] == 47
