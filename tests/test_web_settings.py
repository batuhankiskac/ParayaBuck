from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import WebSocketDisconnect

from parayabuck import runtime as runtime_module
from parayabuck.web.lifespan import create_lifespan
from parayabuck.web.managers import websocket as ws_managers
from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import health as health_router
from parayabuck.web.routers import websocket as websocket_router
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services import status as status_service
from parayabuck.web.services.settings import SettingsService
from parayabuck.web.state import WebState
from tests.helpers import web_client

# Settings saves validate the full config with load_config, which requires
# at least one enabled market; fixtures carry the smallest loadable one.
_LOADABLE_MARKETS: dict[str, Any] = {
    "universe": {"equity": {"enabled": True}},
    "strategy": {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1"}},
}


class _CountingEngineManager(EngineManager):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.config_path = root / "settings.yaml"
        self.env_path = root / ".env"
        self.engine_state = "running"
        self.stop_calls = 0
        self.start_calls = 0

    def stop_engine(self) -> None:
        self.stop_calls += 1
        self.engine_state = "stopped"

    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "running"


class _RestartFailureManagerStub(_CountingEngineManager):
    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "error"
        raise TimeoutError("Alpaca timed out")


def test_control_token_and_index_are_not_cached() -> None:
    manager = EngineManager()
    client = web_client(manager)
    token_response = client.get("/api/control-token")
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.json()["control_token"] == manager.control_token

    index_response = client.get("/")
    assert index_response.headers["cache-control"] == "no-store"
    assert manager.control_token in index_response.text


class _RestartFailureManagerStub(_CountingEngineManager):
    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "error"
        raise TimeoutError("Alpaca timed out")


@pytest.mark.parametrize("state", ["warming_up", "degraded", "halted", "stop_failed"])
def test_engine_manager_refuses_second_runtime(state: str) -> None:
    manager = EngineManager()
    manager.engine_state = state
    manager.runtime = object()

    with pytest.raises(ValueError, match="Motor zaten"):
        manager.start_engine()


def test_concurrent_ws_ticket_issuance_never_drops_tickets() -> None:
    """issue_ws_ticket prunes by rebuilding the map while consume pops from
    it. Without the lock, two concurrent issuers could rebuild from the same
    snapshot and silently drop each other's fresh tickets (spurious 1008)."""
    manager = EngineManager()
    workers = 8
    per_worker = 25
    errors: list[Exception] = []

    def issue(batch: int) -> list[str]:
        try:
            return [manager.issue_ws_ticket("stream") for _ in range(per_worker)]
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)
            return []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        issued = [ticket for batch in pool.map(issue, range(workers)) for ticket in batch]

    assert not errors
    assert len(issued) == workers * per_worker
    # Every ticket survived the concurrent prune-rebuild cycles and consumes
    # exactly once; the store holds none of them afterwards.
    assert all(manager.consume_ws_ticket(ticket, "stream") for ticket in issued)
    store = manager._ticket_store or ws_managers.ticket_store
    assert not set(issued) & set(store.tickets)


def test_reset_safety_lock_does_not_deadlock_on_state_lock() -> None:
    """reset_safety_lock acquires _state_lock and then calls
    current_engine_state(), which re-acquires it. A non-reentrant lock would
    deadlock; the manager must complete the reset within a bounded time."""
    manager = EngineManager()
    manager.engine_state = "halted"
    manager.runtime = SimpleNamespace(
        health=lambda: SimpleNamespace(state="halted"),
        reset_safety_lock=lambda: None,
    )
    manager.broker = None

    result: dict[str, object] = {"done": False}

    def run() -> None:
        manager.reset_safety_lock()
        result["done"] = True

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=5.0)
    assert result["done"], "reset_safety_lock deadlocked on the state lock"


def test_saving_runtime_settings_restarts_engine_without_restarting_web_app(tmp_path: Path, monkeypatch: Any) -> None:
    manager = _CountingEngineManager(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "app": {
                    "host": "127.0.0.1",
                    "port": 8000,
                    "open_browser": True,
                    "log_level": "INFO",
                },
                "runtime": {
                    "mode": "paper",
                    "decision_db_path": "data/runtime/parayabuck.sqlite3",
                },
                "risk": {
                    "daily_loss_limit_pct": 0.015,
                    "max_drawdown_pct": 0.10,
                },
                **_LOADABLE_MARKETS,
            }
        )
    )

    result = asyncio.run(
        SettingsService(manager).save_settings(
            SettingsPayload(
                paper_mode=True,
                daily_loss_limit_pct=2.0,
                restart_engine=True,
                max_drawdown_pct=12.0,
                host="127.0.0.1",
                port=8000,
                open_browser=True,
                log_level="INFO",
            )
        )
    )

    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["runtime"]["mode"] == "paper"
    assert "execution_timeframe" not in saved["runtime"]
    assert "engine" not in saved or "timeframe" not in saved["engine"]
    assert saved["risk"]["daily_loss_limit_pct"] == 0.02
    assert saved["risk"]["max_drawdown_pct"] == 0.12
    assert manager.stop_calls == 1
    assert manager.start_calls == 1
    assert result["engine_restarted"] is True
    assert result["app_restart_required"] is False


def test_settings_save_succeeds_when_automatic_restart_times_out(tmp_path: Path, monkeypatch: Any) -> None:
    manager = _RestartFailureManagerStub(tmp_path)
    manager.config_path.write_text(yaml.safe_dump({"runtime": {"mode": "paper"}, "risk": {}, **_LOADABLE_MARKETS}))

    result = asyncio.run(
        SettingsService(manager).save_settings(SettingsPayload(daily_loss_limit_pct=2.0, restart_engine=True))
    )

    assert result["status"] == "success"
    assert result["engine_restarted"] is False
    assert result["restart_error"] == "Alpaca timed out"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert "execution_timeframe" not in saved["runtime"]
    assert "timeframe" not in saved.get("engine", {})


def test_settings_save_strips_obsolete_engine_keys_so_config_loads_again(tmp_path: Path, monkeypatch: Any) -> None:
    manager = _CountingEngineManager(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "runtime": {"mode": "paper"},
                "risk": {"daily_loss_limit_pct": 0.015, "max_drawdown_pct": 0.10},
                "engine": {"timeframe": "15m", "warmup_bars": 5},
                **_LOADABLE_MARKETS,
            }
        )
    )

    result = asyncio.run(
        SettingsService(manager).save_settings(
            SettingsPayload(
                paper_mode=True,
                daily_loss_limit_pct=2.0,
                restart_engine=True,
                max_drawdown_pct=12.0,
            )
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert "execution_timeframe" not in saved["runtime"]
    assert "timeframe" not in saved.get("engine", {})
    assert "warmup_bars" not in saved.get("engine", {})


def test_settings_save_strips_obsolete_risk_keys_so_config_loads_again(tmp_path: Path, monkeypatch: Any) -> None:
    manager = _CountingEngineManager(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "runtime": {"mode": "paper"},
                "risk": {
                    "daily_loss_limit_pct": 0.015,
                    "max_drawdown_pct": 0.10,
                    "volatility_targeting_enabled": True,
                    "risk_per_trade_pct": 0.01,
                },
                **_LOADABLE_MARKETS,
            }
        )
    )

    result = asyncio.run(
        SettingsService(manager).save_settings(
            SettingsPayload(
                paper_mode=True,
                daily_loss_limit_pct=2.0,
                restart_engine=True,
                max_drawdown_pct=12.0,
            )
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert "volatility_targeting_enabled" not in saved["risk"]
    assert "risk_per_trade_pct" not in saved["risk"]
    assert saved["risk"]["daily_loss_limit_pct"] == 0.02


def _credential_payload(**overrides: Any) -> SettingsPayload:
    """A save that replaces the stored credentials (forces the .env write)."""
    return SettingsPayload(api_key="newkey", secret_key="newsecret", restart_engine=True, **overrides)


def _prepare_credential_save(tmp_path: Path, monkeypatch: Any) -> _CountingEngineManager:
    manager = _CountingEngineManager(tmp_path)
    manager.config_path.write_text(yaml.safe_dump({"runtime": {"mode": "paper"}, "risk": {}, **_LOADABLE_MARKETS}))
    manager.env_path.write_text('ALPACA_API_KEY="oldkey"\nALPACA_SECRET_KEY="oldsecret"\n')
    monkeypatch.setenv("ALPACA_API_KEY", "oldkey")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "oldsecret")
    return manager


def test_credential_save_rejects_read_only_storage_before_stopping_the_engine(tmp_path: Path, monkeypatch: Any) -> None:
    """A read-only deployment must yield a 400 with the engine still trading."""
    from parayabuck.web.services.settings import SettingsService

    manager = _prepare_credential_save(tmp_path, monkeypatch)
    manager.env_path = tmp_path / "missing_dir" / ".env"

    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(SettingsService(manager).save_settings(_credential_payload()))

    assert manager.stop_calls == 0, "engine was stopped for a save that could never write"
    assert manager.start_calls == 0
    assert not manager.env_path.exists()
    assert yaml.safe_load(manager.config_path.read_text())["runtime"]["mode"] == "paper"


def test_failed_credential_write_restarts_the_engine_it_stopped(tmp_path: Path, monkeypatch: Any) -> None:
    """A write that fails after the stop must not leave the bot down."""
    from parayabuck.web.services import settings as settings_service
    from parayabuck.web.services.settings import SettingsService

    manager = _prepare_credential_save(tmp_path, monkeypatch)
    config_before = manager.config_path.read_text()
    env_before = manager.env_path.read_text()

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(settings_service, "_atomic_update_credentials", _boom)

    with pytest.raises(OSError):
        asyncio.run(SettingsService(manager).save_settings(_credential_payload()))

    assert manager.stop_calls == 1
    assert manager.start_calls == 1, "engine left stopped after a failed save"
    assert manager.engine_state == "running"
    assert manager.config_path.read_text() == config_before
    assert manager.env_path.read_text() == env_before


def test_credential_save_writes_env_and_restarts_engine(tmp_path: Path, monkeypatch: Any) -> None:
    from parayabuck.web.services.settings import SettingsService

    manager = _prepare_credential_save(tmp_path, monkeypatch)

    result = asyncio.run(SettingsService(manager).save_settings(_credential_payload()))

    assert result["engine_restarted"] is True
    assert manager.stop_calls == 1 and manager.start_calls == 1
    written = yaml.safe_load(manager.config_path.read_text())
    assert written["runtime"]["mode"] == "paper"
    assert 'ALPACA_API_KEY="newkey"' in manager.env_path.read_text()


def test_application_shutdown_stops_running_engine(monkeypatch: Any) -> None:
    class ShutdownManager:
        stop_calls = 0

        def stop_engine(self) -> None:
            self.stop_calls += 1

    manager = ShutdownManager()

    async def run_lifespan() -> None:
        async with create_lifespan(lambda: manager)(None):
            pass

    asyncio.run(run_lifespan())

    assert manager.stop_calls == 1


def test_application_lifespan_auto_starts_paper_engine(monkeypatch: Any) -> None:
    class AutoStartManager:
        start_calls = 0
        stop_calls = 0

        def should_auto_start(self) -> bool:
            return True

        async def auto_start_engine(self) -> None:
            self.start_engine()

        def start_engine(self) -> None:
            self.start_calls += 1

        def stop_engine(self) -> None:
            self.stop_calls += 1

        def _cancel_pending_retry(self) -> None:
            pass

    manager = AutoStartManager()

    async def run_lifespan() -> None:
        async with create_lifespan(lambda: manager)(None):
            await asyncio.sleep(0.01)

    asyncio.run(run_lifespan())

    assert manager.start_calls == 1
    assert manager.stop_calls == 1


def test_live_mode_is_never_eligible_for_auto_start(tmp_path: Path, monkeypatch: Any) -> None:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.security_warning = None
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "app": {"auto_start_engine": True},
                "alpaca": {"paper": False},
                "symbols": ["AAPL"],
            }
        )
    )

    assert manager.should_auto_start() is False

    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "app": {"auto_start_engine": True},
                "alpaca": {"paper": True},
                "symbols": ["AAPL"],
            }
        )
    )
    assert manager.should_auto_start() is True


@pytest.mark.parametrize(
    ("config_text", "expected_paper"),
    [
        ("runtime:\n", True),
        ("alpaca:\n  paper: true\nruntime:\n", True),
        ("alpaca:\n  paper: false\nruntime:\n", False),
    ],
)
def test_paper_mode_tolerates_an_empty_runtime_section(tmp_path: Path, config_text: str, expected_paper: bool) -> None:
    """`runtime:` with no value parses to None — the paper badge must still resolve."""
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(config_text, encoding="utf-8")

    assert manager.is_paper_mode() is expected_paper


def test_web_engine_manager_uses_runtime(monkeypatch: Any) -> None:
    calls: list[str] = []

    class FakeRuntime:
        def __init__(self, **kwargs: Any) -> None:
            calls.append("init")
            self.broker = object()
            self.is_running = True

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")
            self.is_running = False

    monkeypatch.setattr(runtime_module, "EngineRuntime", FakeRuntime)
    manager = EngineManager()
    manager.config_path = Path(__file__).resolve().parents[1] / "config/settings.yaml"
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

    manager.start_engine()
    assert manager.engine_state == "running"
    assert calls == ["init", "start"]

    manager.stop_engine()
    assert manager.engine_state == "stopped"
    assert calls == ["init", "start", "stop"]


def test_failed_runtime_start_does_not_leave_closed_runtime_exposed(
    monkeypatch: Any,
) -> None:
    class FailedRuntime:
        def __init__(self, **kwargs: Any) -> None:
            self.broker = object()
            self.stopped = False

        def start(self) -> None:
            raise RuntimeError("stream startup failed")

        def stop(self) -> bool:
            self.stopped = True
            return True

        def health(self) -> Any:
            return type("Health", (), {"state": "stopped"})()

    monkeypatch.setattr(runtime_module, "EngineRuntime", FailedRuntime)
    manager = EngineManager()
    manager.config_path = Path(__file__).resolve().parents[1] / "config/settings.yaml"
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))

    with pytest.raises(RuntimeError, match="stream startup failed"):
        manager.start_engine()

    assert manager.engine_state == "error"
    assert manager.last_engine_error == "stream startup failed"
    assert manager.current_engine_state() == "error"
    assert manager.runtime is None
    assert manager.broker is None


def test_stop_failure_returns_json_http_error_and_marks_manager_failed(
    monkeypatch: Any,
) -> None:
    class FailingRuntime:
        def stop(self) -> bool:
            raise RuntimeError("worker shutdown failed")

    manager = EngineManager()
    # The operator Stop record would land in the repo's data/runtime; keep it out of tests.
    manager._write_operator_stop_marker = lambda: None  # type: ignore[method-assign]
    manager.engine_state = "running"
    manager.runtime = FailingRuntime()
    response = web_client(manager).post("/api/engine/stop")

    assert response.status_code == 500
    assert response.json()["detail"] == "Engine could not stop cleanly: worker shutdown failed"
    assert manager.engine_state == "stop_failed"


class _DeadWebSocket:
    """Simulates a browser that vanished without a clean close handshake.

    Carries a composed ``app.state.web`` because that is where the router
    resolves its manager: without it the route falls back to the process-wide
    EngineManager singleton, whose ``fetch_account_snapshot`` builds a real
    AlpacaBroker from the repo .env and reads the account the VM trades.
    """

    def __init__(self, engine_manager: Any) -> None:
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.sent: list[Any] = []
        web_state = WebState(engine_manager=engine_manager, backtest_manager=None)
        self.app = SimpleNamespace(state=SimpleNamespace(web=web_state))

    async def accept(self) -> None:
        return

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        raise WebSocketDisconnect()


class _WebSocketManagerStub:
    def __init__(self) -> None:
        self.stream_connections: set[Any] = set()
        self.snapshot_fetches = 0

    def fetch_account_snapshot(self) -> dict[str, Any]:
        self.snapshot_fetches += 1
        return {"equity": 1.0}

    def current_engine_state(self) -> str:
        return "stopped"

    def is_paper_mode(self) -> bool:
        return True

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
        del kind
        return not ticket or ticket == "valid-ticket"


def _bare_engine_manager(state: str) -> EngineManager:
    engine = EngineManager.__new__(EngineManager)
    engine.config_path = None
    engine.engine_state = state
    engine.runtime = None
    engine._state_lock = threading.RLock()
    return engine


def test_web_backtest_refused_while_engine_active(monkeypatch: Any) -> None:
    """A Web backtest must not start while the trading engine is active."""
    engine = _bare_engine_manager("running")
    backtests = BacktestManager(engine_state_provider=engine.current_engine_state)
    with pytest.raises(ValueError, match="stop the engine first"):
        backtests.start({"market": "equity"})
    # The backtest manager stays untouched by the refused start.
    assert backtests.state == "idle"


def test_web_backtest_allowed_when_engine_stopped(monkeypatch: Any) -> None:
    """With the engine stopped, the Web backtest starts normally."""
    engine = _bare_engine_manager("stopped")
    backtests = BacktestManager(engine_state_provider=engine.current_engine_state)

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, **extra) -> None:
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.started = False

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return False

    backtests._thread_factory = _FakeThread
    run_id = backtests.start({"market": "equity"})
    assert run_id.startswith("web-")
    assert backtests.state == "running"


def test_engine_start_refused_while_web_backtest_running(monkeypatch: Any) -> None:
    """The engine must not start while a Web backtest is running."""
    manager = EngineManager()
    monkeypatch.setattr(BacktestManager, "is_running", property(lambda self: True))
    with pytest.raises(ValueError, match="Web backtesti hâlâ çalışıyor"):
        manager.start_engine()
    assert manager.engine_state == "stopped"


def test_engine_start_allowed_when_web_backtest_idle(monkeypatch: Any) -> None:
    """An idle backtest manager does not block engine start (no credentials:
    the start fails for the credentials reason, not the backtest reason)."""
    manager = EngineManager()
    manager.env_path = Path("/nonexistent/.env")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setattr(BacktestManager, "is_running", property(lambda self: False))
    with pytest.raises(ValueError, match="Alpaca API anahtarı/secret eksik"):
        manager.start_engine()


def test_websocket_stream_detects_dead_browser_and_cleans_up(monkeypatch: Any) -> None:
    manager = _WebSocketManagerStub()
    websocket = _DeadWebSocket(manager)

    asyncio.run(websocket_router.websocket_stream(websocket))  # must exit quietly, not spin

    # The payload proves the router read the composed app's manager: the
    # process singleton would have answered from the broker (or raised).
    assert websocket.sent == [
        {"type": "update", "account": {"equity": 1.0}, "status": {"state": "stopped", "paper": True}}
    ]
    assert manager.snapshot_fetches == 1


def test_websocket_logs_detects_dead_browser_and_cleans_queue(monkeypatch: Any) -> None:
    manager = _WebSocketManagerStub()
    websocket = _DeadWebSocket(manager)
    before = ws_managers.get_log_queues_snapshot()

    asyncio.run(websocket_router.websocket_logs(websocket))  # must exit quietly, not spin

    # The shared module-level queue set is the single owner of log queues.
    assert ws_managers.get_log_queues_snapshot() == before


def test_status_reports_universe_counts_while_runtime_is_starting(tmp_path: Path, monkeypatch: Any) -> None:
    class StartingManager:
        config_path = tmp_path / "settings.yaml"
        runtime = None
        last_engine_error = None
        security_warning = None

        def get_credentials(self) -> tuple[str, str]:
            return "key", "secret"

        def current_engine_state(self) -> str:
            return "starting"

        def is_paper_mode(self) -> bool:
            return True

    equity_config = type(
        "EquityConfig",
        (),
        {"enabled": True, "symbols": ("AAPL", "MSFT")},
    )()
    config = type(
        "Config",
        (),
        {"universe": type("Universe", (), {"equity": equity_config})()},
    )()

    status_manager = StartingManager()
    monkeypatch.setattr(status_service, "_load_config", lambda _path: config)

    status = status_service.build_status_snapshot(status_manager)

    assert status["state"] == "starting"
    assert "active_crypto_symbol_count" not in status
    assert status["universe"] == {"equity": {"symbols": ["AAPL", "MSFT"]}}
    assert status["scanner"] == {"equity_status": "static"}


def test_status_exposes_strategy_metadata_instead_of_global_timeframe(monkeypatch: Any) -> None:
    """The status payload surfaces each strategy's OWN signal timeframe and
    rebalance cadence (no global strategy-timeframe selector exists)."""

    class StaticManager:
        config_path = None
        runtime = None
        last_engine_error = None
        security_warning = None

        def get_credentials(self) -> tuple[str, str]:
            return "", ""

        def current_engine_state(self) -> str:
            return "stopped"

        def is_paper_mode(self) -> bool:
            return True

    selection = type(
        "Selection",
        (),
        {
            "enabled": True,
            "name": "equity_leveraged_trend_v1",
            "timeframe": "1d",
            "parameters": {},
        },
    )()
    strategy = type("Strategy", (), {"equity": selection})()
    universe = type(
        "Universe",
        (),
        {"equity": type("Equity", (), {"enabled": True, "symbols": ("A", "B", "C")})()},
    )()
    config = type("Config", (), {"strategy": strategy, "universe": universe})()

    status_manager = StaticManager()
    monkeypatch.setattr(status_service, "_load_config", lambda _path: config)
    status = status_service.build_status_snapshot(status_manager)
    meta = status["strategy_metadata"]
    assert meta["equity"]["name"] == "equity_leveraged_trend_v1"
    assert meta["equity"]["signal_timeframe"] == "1d"
    assert meta["equity"]["rebalance_cadence"] == "hours:24"
    # The static equity universe is its own pool and observation set.
    assert meta["equity"]["candidate_pool_size"] == 3
    assert meta["equity"]["observation_set_size"] == 3
    assert set(meta) == {"equity"}


def test_status_falls_back_when_config_load_fails(monkeypatch: Any) -> None:

    class StaticManager:
        config_path = None
        runtime = None
        last_engine_error = None
        security_warning = None

        def get_credentials(self) -> tuple[str, str]:
            return "", ""

        def current_engine_state(self) -> str:
            return "stopped"

        def is_paper_mode(self) -> bool:
            return True

    status_manager = StaticManager()

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise ValueError("boom")

    monkeypatch.setattr(status_service, "_load_config", _raise)
    status = status_service.build_status_snapshot(status_manager)
    assert "strategy_metadata" not in status
    assert status["scanner"] == {"equity_status": "error"}


def test_readyz_rejects_degraded_or_halted_runtime(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    class DegradedManager:
        config_path = None

        def __init__(self, state: str) -> None:
            self._state = state

        def has_active_runtime(self) -> bool:
            return True

        def current_engine_state(self) -> str:
            return self._state

        @property
        def runtime(self) -> Any:
            health = SimpleNamespace(state=self._state, healthy=False)
            return SimpleNamespace(health=lambda: health)

    client = web_client(DegradedManager("degraded"))
    monkeypatch.setattr(health_router, "load_config", lambda _path: object())

    response = client.get("/readyz")
    payload = response.json()
    assert payload["ready"] is False
    assert payload["runtime_expected"] is True
    assert response.status_code == 503

    client = web_client(DegradedManager("halted"))
    response = client.get("/readyz")
    assert response.json()["ready"] is False
    assert response.status_code == 503


def test_readyz_reports_ready_when_no_runtime_is_expected(monkeypatch: Any) -> None:
    class StoppedManager:
        config_path = None

        def has_active_runtime(self) -> bool:
            return False

        def current_engine_state(self) -> str:
            return "stopped"

        @property
        def runtime(self) -> Any:
            return None

    client = web_client(StoppedManager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: object())

    response = client.get("/readyz")
    payload = response.json()
    assert payload["ready"] is True
    assert payload["runtime_expected"] is False
    assert response.status_code == 200


def test_readyz_config_invalid_returns_503(monkeypatch: Any) -> None:
    """A configuration that fails to parse is never ready."""

    class BadConfigManager:
        config_path = None

        def current_engine_state(self) -> str:
            return "stopped"

        def has_active_runtime(self) -> bool:
            return False

        @property
        def runtime(self) -> Any:
            return None

    client = web_client(BadConfigManager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml")))
    response = client.get("/readyz")
    payload = response.json()
    assert payload["ready"] is False
    assert payload["config_valid"] is False
    assert payload["process_alive"] is True
    assert response.status_code == 503


def test_readyz_auto_start_failure_returns_503(monkeypatch: Any) -> None:
    """auto_start_engine=true + paper mode + engine ended in error: the config
    parses but the deployment is NOT trading, so /readyz is 503."""
    from parayabuck.config import Config

    class FailedStartManager:
        config_path = None

        def current_engine_state(self) -> str:
            return "error"

        def has_active_runtime(self) -> bool:
            return False

        @property
        def runtime(self) -> Any:
            return None

    client = web_client(FailedStartManager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())
    response = client.get("/readyz")
    payload = response.json()
    assert payload["config_valid"] is True
    assert payload["trading_expected"] is True
    assert payload["trading_ready"] is False
    assert payload["ready"] is False
    assert response.status_code == 503


def test_readyz_healthy_running_runtime_returns_200(monkeypatch: Any) -> None:
    from parayabuck.config import Config

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

    client = web_client(HealthyManager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config())
    response = client.get("/readyz")
    payload = response.json()
    assert payload["ready"] is True
    assert payload["trading_expected"] is True
    assert payload["trading_ready"] is True
    assert response.status_code == 200


def test_readyz_intentional_stop_control_plane_ready(monkeypatch: Any) -> None:
    """Control-plane-only mode (auto_start_engine=false, engine intentionally
    stopped): trading is NOT expected and the control plane is ready. This is
    an explicit semantic, never an accident of ``runtime is None``."""
    from parayabuck.config import AppConfig, Config

    class StoppedManager:
        config_path = None

        def current_engine_state(self) -> str:
            return "stopped"

        def has_active_runtime(self) -> bool:
            return False

        @property
        def runtime(self) -> Any:
            return None

    client = web_client(StoppedManager())
    monkeypatch.setattr(health_router, "load_config", lambda _path: Config(app=AppConfig(auto_start_engine=False)))
    response = client.get("/readyz")
    payload = response.json()
    assert payload["ready"] is True
    assert payload["trading_expected"] is False
    assert response.status_code == 200


def test_account_snapshot_exposes_mixed_ownership(monkeypatch: Any, tmp_path: Path) -> None:
    """The operator account snapshot exposes total/managed/external quantity
    and the ownership state for every position."""

    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.broker = SimpleNamespace(
        account_id="acct",
        account_suffix="1234",
        account=lambda: SimpleNamespace(
            equity=100000.0,
            cash=90000.0,
            last_equity=100000.0,
            id="acct",
        ),
    )
    from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot

    manager.runtime = SimpleNamespace(
        ledger=None,
    )
    broker = SimpleNamespace(
        account_id="acct",
        account_suffix="1234",
        account=lambda: AccountSnapshot(
            cash=88000.0,
            equity=100000.0,
            peak_equity=100000.0,
            daily_pnl=0.0,
            positions=(
                PositionSnapshot(
                    "AAPL",
                    120,
                    100,
                    100,
                    Market.EQUITY,
                    qty_available=120,
                    ownership="mixed",
                    managed_qty=20,
                ),
                PositionSnapshot(
                    "MSFT",
                    50,
                    100,
                    100,
                    Market.EQUITY,
                    qty_available=50,
                    ownership="managed",
                    managed_qty=50,
                ),
            ),
        ),
    )
    manager.broker = broker
    snapshot = manager._refresh_account_snapshot()
    aapl = next(p for p in snapshot["positions"] if p["symbol"] == "AAPL")
    assert aapl["total_qty"] == 120
    assert aapl["managed_qty"] == 20
    assert aapl["external_qty"] == 100
    assert aapl["ownership_state"] == "mixed"
    msft = next(p for p in snapshot["positions"] if p["symbol"] == "MSFT")
    assert msft["managed_qty"] == 50
    assert msft["external_qty"] == 0
    assert msft["ownership_state"] == "managed"


def test_operations_snapshot_surfaces_reconciliation_anomalies(tmp_path: Path) -> None:
    """Reconciliation anomalies (ownership adjustments) are operator-visible
    through the account-scoped operations snapshot."""
    from parayabuck.core.models import Market
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    repo.record_ownership_adjustment(
        "acct",
        "QLD",
        Market.EQUITY,
        0.5,
        kind="stale_ownership_clear",
        note="broker flat, no real fill found",
    )
    snapshot = repo.operations_snapshot("acct")
    anomalies = snapshot["reconciliation_anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["symbol"] == "QLD"
    assert anomalies[0]["outstanding_quantity"] == pytest.approx(0.5)
    assert anomalies[0]["kind"] == "stale_ownership_clear"
    # A different account never sees the anomaly.
    assert repo.operations_snapshot("other")["reconciliation_anomalies"] == []
