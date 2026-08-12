from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import WebSocketDisconnect
from starlette.requests import Request

from parayabuck import runtime as runtime_module
from parayabuck.web import server


class _CountingEngineManager(server.EngineManager):
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


class _RestartFailureManagerStub(_CountingEngineManager):
    def start_engine(self) -> None:
        self.start_calls += 1
        self.engine_state = "error"
        raise TimeoutError("Alpaca timed out")


@pytest.mark.parametrize("state", ["warming_up", "degraded", "halted", "stop_failed"])
def test_engine_manager_refuses_second_runtime(state: str) -> None:
    manager = server.EngineManager()
    manager.engine_state = state
    manager.runtime = object()

    with pytest.raises(ValueError, match=f"already {state}"):
        manager.start_engine()


def test_reset_safety_lock_does_not_deadlock_on_state_lock() -> None:
    """reset_safety_lock acquires _state_lock and then calls
    current_engine_state(), which re-acquires it. A non-reentrant lock would
    deadlock; the manager must complete the reset within a bounded time."""
    manager = server.EngineManager()
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


def test_saving_runtime_settings_restarts_engine_without_restarting_web_app(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
                    "execution_timeframe": "5m",
                    "market_state_timeframe": "5m",
                    "decision_db_path": "data/runtime/parayabuck.sqlite3",
                    "scanner_refresh_hours": 24,
                },
                "risk": {
                    "daily_loss_limit_pct": 0.015,
                    "max_drawdown_pct": 0.10,
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
                execution_timeframe="5m",
                host="127.0.0.1",
                port=8000,
                open_browser=True,
                log_level="INFO",
            )
        )
    )

    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["runtime"]["mode"] == "paper"
    assert saved["runtime"]["execution_timeframe"] == "5m"
    assert "engine" not in saved or "timeframe" not in saved["engine"]
    assert saved["risk"]["daily_loss_limit_pct"] == 0.02
    assert saved["risk"]["max_drawdown_pct"] == 0.12
    assert manager.stop_calls == 1
    assert manager.start_calls == 1
    assert result["engine_restarted"] is True
    assert result["app_restart_required"] is False


def test_settings_save_succeeds_when_automatic_restart_times_out(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = _RestartFailureManagerStub(tmp_path)
    manager.config_path.write_text(yaml.safe_dump({"runtime": {"mode": "paper"}, "risk": {}}))
    monkeypatch.setattr(server, "manager", manager)

    result = asyncio.run(server.save_settings(server.SettingsPayload(execution_timeframe="5m")))

    assert result["status"] == "success"
    assert result["engine_restarted"] is False
    assert result["restart_error"] == "Alpaca timed out"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["runtime"]["execution_timeframe"] == "5m"
    assert "timeframe" not in saved.get("engine", {})


def test_settings_save_strips_obsolete_engine_keys_so_config_loads_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = _CountingEngineManager(tmp_path)
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "runtime": {"mode": "paper"},
                "risk": {"daily_loss_limit_pct": 0.015, "max_drawdown_pct": 0.10},
                "engine": {"timeframe": "15m", "warmup_bars": 5},
                "strategy": {"equity": {"enabled": True}},
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
                execution_timeframe="5m",
            )
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert saved["runtime"]["execution_timeframe"] == "5m"
    assert "timeframe" not in saved.get("engine", {})
    assert "warmup_bars" not in saved.get("engine", {})


def test_settings_save_strips_obsolete_risk_keys_so_config_loads_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
                "strategy": {"equity": {"enabled": True}},
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
                execution_timeframe="5m",
            )
        )
    )

    assert result["status"] == "success"
    saved = yaml.safe_load(manager.config_path.read_text())
    assert "volatility_targeting_enabled" not in saved["risk"]
    assert "risk_per_trade_pct" not in saved["risk"]
    assert saved["risk"]["daily_loss_limit_pct"] == 0.02


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


def test_application_lifespan_auto_starts_paper_engine(monkeypatch: Any) -> None:
    class AutoStartManager:
        start_calls = 0
        stop_calls = 0
        log_queues: set[Any] = set()
        _log_queues_lock = threading.Lock()

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
    monkeypatch.setattr(server, "manager", manager)

    async def run_lifespan() -> None:
        async with server.app_lifespan(server.app):
            await asyncio.sleep(0.01)

    asyncio.run(run_lifespan())

    assert manager.start_calls == 1
    assert manager.stop_calls == 1


def test_live_mode_is_never_eligible_for_auto_start(tmp_path: Path, monkeypatch: Any) -> None:
    manager = server.EngineManager()
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
    manager = server.EngineManager()
    manager.config_path = Path("config/settings.yaml")
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
    manager = server.EngineManager()
    manager.config_path = Path("config/settings.yaml")
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

    manager = server.EngineManager()
    manager.engine_state = "running"
    manager.runtime = FailingRuntime()
    monkeypatch.setattr(server, "manager", manager)

    with pytest.raises(server.HTTPException) as raised:
        asyncio.run(server.stop_engine())

    assert raised.value.status_code == 500
    assert raised.value.detail == "Engine could not stop cleanly: worker shutdown failed"
    assert manager.engine_state == "stop_failed"


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
        self._log_queues_lock = threading.Lock()
        self.stream_connections: set[Any] = set()

    def fetch_account_snapshot(self) -> dict[str, Any]:
        return {"equity": 1.0}

    def current_engine_state(self) -> str:
        return "stopped"

    def is_paper_mode(self) -> bool:
        return True

    def consume_ws_ticket(self, ticket: str, kind: str) -> bool:
        del kind
        return not ticket or ticket == "valid-ticket"


def _bare_engine_manager(state: str) -> server.EngineManager:
    engine = server.EngineManager.__new__(server.EngineManager)
    engine.config_path = None
    engine.engine_state = state
    engine.runtime = None
    engine._state_lock = threading.RLock()
    return engine


def test_web_backtest_refused_while_engine_active(monkeypatch: Any) -> None:
    """A Web backtest must not start while the trading engine is active."""
    engine = _bare_engine_manager("running")
    monkeypatch.setattr(server, "manager", engine)
    with pytest.raises(ValueError, match="stop the engine first"):
        server.backtest_manager.start({"market": "equity"})
    # The engine singleton stays untouched by the refused start.
    assert server.backtest_manager.state == "idle"


def test_web_backtest_allowed_when_engine_stopped(monkeypatch: Any) -> None:
    """With the engine stopped, the Web backtest starts normally."""
    engine = _bare_engine_manager("stopped")
    monkeypatch.setattr(server, "manager", engine)

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

    original_factory = server.backtest_manager._thread_factory
    try:
        server.backtest_manager._thread_factory = _FakeThread
        run_id = server.backtest_manager.start({"market": "equity"})
        assert run_id.startswith("web-")
        assert server.backtest_manager.state == "running"
    finally:
        server.backtest_manager._thread_factory = original_factory
        server.backtest_manager.state = "idle"
        server.backtest_manager._thread = None


def test_engine_start_refused_while_web_backtest_running(monkeypatch: Any) -> None:
    """The engine must not start while a Web backtest is running."""
    manager = server.EngineManager()
    monkeypatch.setattr(
        server.backtest_manager.__class__, "is_running", property(lambda self: True)
    )
    with pytest.raises(ValueError, match="Web backtest is still running"):
        manager.start_engine()
    assert manager.engine_state == "stopped"


def test_engine_start_allowed_when_web_backtest_idle(monkeypatch: Any) -> None:
    """An idle backtest manager does not block engine start (no credentials:
    the start fails for the credentials reason, not the backtest reason)."""
    manager = server.EngineManager()
    manager.env_path = Path("/nonexistent/.env")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        server.backtest_manager.__class__, "is_running", property(lambda self: False)
    )
    with pytest.raises(ValueError, match="Alpaca API key/secret missing"):
        manager.start_engine()


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


def test_status_reports_validated_universe_counts_while_runtime_is_starting(
    tmp_path: Path, monkeypatch: Any
) -> None:
    candidate_path = tmp_path / "candidates.json"
    equity_path = tmp_path / "active-equity.json"
    crypto_path = tmp_path / "active-crypto.json"
    candidate_path.write_text(json.dumps(["AAPL", "MSFT", "NVDA"]))
    equity_path.write_text(json.dumps({"selection_timestamp": "equity-time"}))
    crypto_path.write_text(json.dumps({"selection_timestamp": "crypto-time"}))

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
        {
            "candidate_universe_path": str(candidate_path),
            "active_universe_path": str(equity_path),
        },
    )()
    crypto_config = type("CryptoConfig", (), {"active_universe_path": str(crypto_path)})()
    config = type(
        "Config",
        (),
        {
            "universe": type(
                "Universe",
                (),
                {"equity": equity_config, "crypto": crypto_config},
            )()
        },
    )()

    monkeypatch.setattr(server, "manager", StartingManager())
    monkeypatch.setattr(server, "load_config", lambda _path: config)
    monkeypatch.setattr(
        server, "validate_active_universe", lambda _config: (("AAPL", "MSFT"), "success")
    )
    monkeypatch.setattr(
        server,
        "validate_active_crypto_universe",
        lambda _config: (("BTC/USD", "ETH/USD"), "success"),
    )

    status = asyncio.run(server.get_status())

    assert status["state"] == "starting"
    assert status["active_equity_symbol_count"] == 2
    assert status["active_crypto_symbol_count"] == 2
    assert status["candidate_universe_size"] == 3
    assert status["scanner"]["equity_status"] == "success"
    assert status["scanner"]["crypto_status"] == "success"


def test_status_reports_configured_strategy_state(monkeypatch: Any) -> None:
    from parayabuck.web import server

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

    def make_config(enabled: bool) -> object:
        selection = type(
            "Selection", (), {"enabled": enabled, "name": "equity_dynamic_trend_1d_v1"}
        )()
        disabled = type("Selection", (), {"enabled": False, "name": "crypto"})()
        strategy = type("Strategy", (), {"equity": selection, "crypto": disabled})()
        universe = type(
            "Universe",
            (),
            {
                "equity": type(
                    "Equity",
                    (),
                    {"candidate_universe_path": "c.json", "active_universe_path": "a.json"},
                )(),
                "crypto": type("Crypto", (), {"active_universe_path": "crypto.json"})(),
            },
        )()
        return type("Config", (), {"strategy": strategy, "universe": universe})()

    monkeypatch.setattr(server, "manager", StaticManager())
    monkeypatch.setattr(server, "load_config", lambda _path: make_config(True))
    configured = asyncio.run(server.get_status())
    assert configured["strategy_status"] == "configured:equity/equity_dynamic_trend_1d_v1"

    monkeypatch.setattr(server, "load_config", lambda _path: make_config(False))
    disabled = asyncio.run(server.get_status())
    assert disabled["strategy_status"] == "configured:"


def test_status_exposes_strategy_metadata_instead_of_global_timeframe(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """The status payload surfaces each strategy's OWN signal timeframe and
    rebalance cadence (no global strategy-timeframe selector exists)."""
    from parayabuck.web import server

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
            "name": "equity_residual_momentum_v1",
            "timeframe": "1d",
            "parameters": {},
        },
    )()
    disabled = type("Selection", (), {"enabled": False, "name": "crypto"})()
    strategy = type("Strategy", (), {"equity": selection, "crypto": disabled})()
    universe = type(
        "Universe",
        (),
        {
            "equity": type(
                "Equity",
                (),
                {
                    "candidate_universe_path": str(tmp_path / "c.json"),
                    "active_universe_path": str(tmp_path / "a.json"),
                    "dynamic_selection_enabled": True,
                },
            )(),
            "crypto": type(
                "Crypto",
                (),
                {
                    "active_universe_path": str(tmp_path / "crypto.json"),
                    "dynamic_selection_enabled": True,
                },
            )(),
        },
    )()
    config = type("Config", (), {"strategy": strategy, "universe": universe})()
    (tmp_path / "c.json").write_text(json.dumps({"shortlist_symbols": ["A", "B", "C"]}))
    # The streamed observation set is the alpha-ranked subset of the pool;
    # selected_symbols holds the broader pool.
    (tmp_path / "a.json").write_text(
        json.dumps({"selected_symbols": ["A", "B", "C"], "observation_set": ["A", "B"]})
    )
    (tmp_path / "crypto.json").write_text(json.dumps({"selected_symbols": []}))

    monkeypatch.setattr(server, "manager", StaticManager())
    monkeypatch.setattr(server, "load_config", lambda _path: config)
    status = asyncio.run(server.get_status())
    meta = status["strategy_metadata"]
    assert meta["equity"]["name"] == "equity_residual_momentum_v1"
    assert meta["equity"]["signal_timeframe"] == "1d"
    assert meta["equity"]["supported_timeframes"] == ["1d"]
    assert meta["equity"]["rebalance_cadence"] == "monthly"
    assert meta["equity"]["candidate_pool_size"] == 3
    assert meta["equity"]["observation_set_size"] == 2
    assert meta["crypto"] is None  # disabled strategy exposes no metadata


def test_status_falls_back_when_config_load_fails(monkeypatch: Any) -> None:
    from parayabuck.web import server

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

    monkeypatch.setattr(server, "manager", StaticManager())

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise ValueError("boom")

    monkeypatch.setattr(server, "load_config", _raise)
    status = asyncio.run(server.get_status())
    assert status["strategy_status"] == "configuration_error"
    assert status["scanner"] == {"last_run_at": None, "status": "error"}


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

    monkeypatch.setattr(server, "manager", DegradedManager("degraded"))
    monkeypatch.setattr(server, "load_config", lambda _path: object())

    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
    assert payload["ready"] is False
    assert payload["runtime_expected"] is True
    assert response.status_code == 503

    monkeypatch.setattr(server, "manager", DegradedManager("halted"))
    response = asyncio.run(server.readyz())
    assert json.loads(response.body)["ready"] is False
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

    monkeypatch.setattr(server, "manager", StoppedManager())
    monkeypatch.setattr(server, "load_config", lambda _path: object())

    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
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

    monkeypatch.setattr(server, "manager", BadConfigManager())
    monkeypatch.setattr(
        server, "load_config", lambda _path: (_ for _ in ()).throw(ValueError("bad yaml"))
    )
    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
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

    monkeypatch.setattr(server, "manager", FailedStartManager())
    monkeypatch.setattr(server, "load_config", lambda _path: Config())
    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
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

    monkeypatch.setattr(server, "manager", HealthyManager())
    monkeypatch.setattr(server, "load_config", lambda _path: Config())
    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
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

    monkeypatch.setattr(server, "manager", StoppedManager())
    monkeypatch.setattr(
        server, "load_config", lambda _path: Config(app=AppConfig(auto_start_engine=False))
    )
    response = asyncio.run(server.readyz())
    payload = json.loads(response.body)
    assert payload["ready"] is True
    assert payload["trading_expected"] is False
    assert response.status_code == 200


def test_account_snapshot_exposes_mixed_ownership(monkeypatch: Any, tmp_path: Path) -> None:
    """The operator account snapshot exposes total/managed/external quantity
    and the ownership state for every position."""
    from parayabuck.web.server import EngineManager

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
        "BTC/USD",
        Market.CRYPTO,
        0.5,
        kind="stale_ownership_clear",
        note="broker flat, no real fill found",
    )
    snapshot = repo.operations_snapshot("acct")
    anomalies = snapshot["reconciliation_anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["symbol"] == "BTC/USD"
    assert anomalies[0]["outstanding_quantity"] == pytest.approx(0.5)
    assert anomalies[0]["kind"] == "stale_ownership_clear"
    # A different account never sees the anomaly.
    assert repo.operations_snapshot("other")["reconciliation_anomalies"] == []
