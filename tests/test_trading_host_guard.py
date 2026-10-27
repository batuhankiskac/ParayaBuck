"""Second-engine guard and engine-manager safety (audit F03/F34/F46).

* F03 — an engine start (auto or manual) requires an explicit
  ``PARAYABUCK_TRADING_HOST`` designation, so a second runner on the shared
  Alpaca PAPER account can never come up just because the credentials exist.
* F34 — a stop request issued while the engine is still starting aborts the
  startup sequence before its two order-producing steps run.
* F46 — ``live_prerequisites`` never authenticates against the LIVE endpoint
  while the live gates are closed (PAPER keys must not be sent there).

Offline throughout: no broker, no network, tmp_path settings only.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

import parayabuck.execution.alpaca as alpaca_module
from parayabuck import cli as cli_module
from parayabuck import runtime as runtime_module
from parayabuck.config import Config, EquityUniverseConfig, RuntimeConfig, RuntimeMode, UniverseConfig
from parayabuck.core.models import AccountSnapshot
from parayabuck.execution.alpaca_reconciliation import RECONCILIATION_CATEGORIES
from parayabuck.runtime import EngineRuntime, StartupCancelled
from parayabuck.trading_host import trading_host_authorized
from parayabuck.web import app as web_app
from parayabuck.web.managers import engine as engine_manager_module
from parayabuck.web.managers.engine import EngineManager

_PAPER_AUTO_START_CONFIG: dict[str, Any] = {
    "app": {"auto_start_engine": True},
    "runtime": {"mode": "paper"},
}


def _manager(root: Path) -> EngineManager:
    from tests.helpers import make_manager

    manager = make_manager(root)
    return manager


def _write_settings(root: Path, raw: dict[str, Any]) -> Path:
    path = root / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# F03: engine starts require the trading-host designation
# ---------------------------------------------------------------------------


def test_auto_start_refused_without_trading_host_designation(tmp_path: Path, monkeypatch: Any) -> None:
    _write_settings(tmp_path, _PAPER_AUTO_START_CONFIG)
    manager = _manager(tmp_path)
    manager.security_warning = None
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)

    assert manager.should_auto_start() is False


def test_auto_start_allowed_on_designated_host(tmp_path: Path, monkeypatch: Any) -> None:
    _write_settings(tmp_path, _PAPER_AUTO_START_CONFIG)
    manager = _manager(tmp_path)
    manager.security_warning = None
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")

    assert manager.should_auto_start() is True


def test_manual_start_refused_without_designation_and_leaves_engine_stopped(tmp_path: Path, monkeypatch: Any) -> None:
    _write_settings(tmp_path, _PAPER_AUTO_START_CONFIG)
    manager = _manager(tmp_path)
    manager.security_warning = None
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)
    # No backtest guard is bound, so a start that got past the designation
    # gate would fail later for an unrelated reason.
    manager._backtest_manager = None

    with pytest.raises(ValueError, match="PARAYABUCK_TRADING_HOST"):
        manager.start_engine()

    assert manager.engine_state == "stopped"
    assert manager.runtime is None
    assert manager.last_engine_error is None


def test_runtime_start_refused_without_designation(monkeypatch: Any) -> None:
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)
    runtime = EngineRuntime("key", "secret", Config(runtime=RuntimeConfig(mode=RuntimeMode.PAPER)))
    runtime._snapshot_writer = None

    with pytest.raises(RuntimeError, match="not designated as the trading host"):
        runtime.start()

    assert runtime._running is False


@pytest.mark.parametrize("value", ["", "0", "no", "off", "maybe"])
def test_non_truthy_designation_values_do_not_enable_trading(value: str, monkeypatch: Any) -> None:
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", value)

    assert trading_host_authorized() is False


# ---------------------------------------------------------------------------
# F03: the console honours --config instead of always reading CONFIG_PATH
# ---------------------------------------------------------------------------


def test_web_command_forwards_config_flag(tmp_path: Path, monkeypatch: Any) -> None:
    config_path = tmp_path / "settings.yaml"
    seen: dict[str, Any] = {}
    monkeypatch.setattr(web_app, "main", lambda config_path=None: seen.update(config_path=config_path))

    assert cli_module.main(["--config", str(config_path), "web"]) == 0
    assert seen["config_path"] == str(config_path)


def test_web_main_exports_named_config_path(tmp_path: Path, monkeypatch: Any) -> None:
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(yaml.safe_dump({"app": {"host": "127.0.0.1", "port": 8123}}), encoding="utf-8")
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPEN_BROWSER", raising=False)
    monkeypatch.setattr(web_app, "_probe_web_bind", lambda *_args, **_kwargs: None)
    served: dict[str, Any] = {}
    monkeypatch.setattr(
        web_app._ExitingServer,
        "run",
        lambda server: served.update(port=server.config.port),
    )

    web_app.main(config_path=str(config_path))

    assert os.environ["CONFIG_PATH"] == str(config_path)
    assert served["port"] == 8123


# ---------------------------------------------------------------------------
# F34: a stop during startup prevents every order submission
# ---------------------------------------------------------------------------


def _cancel_shell(cancel: threading.Event) -> EngineRuntime:
    """EngineRuntime shell wired only far enough to run the start sequence."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.PAPER  # type: ignore[attr-defined]
    runtime._running = False  # type: ignore[attr-defined]
    runtime._state = "stopped"  # type: ignore[attr-defined]
    runtime._stop_event = threading.Event()  # type: ignore[attr-defined]
    runtime._startup_cancel = cancel  # type: ignore[attr-defined]
    return runtime


def test_stop_during_warmup_aborts_before_resume_and_catch_up(monkeypatch: Any) -> None:
    cancel = threading.Event()
    runtime = _cancel_shell(cancel)
    submitted: list[str] = []
    monkeypatch.setattr(runtime, "_resume_startup_rebalances", lambda **_kw: submitted.append("resume"))
    monkeypatch.setattr(runtime, "_run_startup_catch_up", lambda **_kw: submitted.append("catch_up"))

    # The startup sequence's checkpoints sit immediately before both
    # order-producing steps; without a stop request both are reached.
    runtime._raise_if_startup_cancelled()
    runtime._resume_startup_rebalances(account=None)  # type: ignore[arg-type]
    runtime._raise_if_startup_cancelled()
    runtime._run_startup_catch_up(account=None)  # type: ignore[arg-type]
    assert submitted == ["resume", "catch_up"]

    # A stop request during warmup aborts before the first order.
    submitted.clear()
    cancel.set()
    with pytest.raises(StartupCancelled, match="cancelled by a stop request"):
        runtime._raise_if_startup_cancelled()
    assert submitted == []


class _CleanBroker:
    """Broker whose startup reconciliation is clean and account is flat."""

    account_id = "acct"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.reconcile_calls = 0
        self.account_calls = 0

    def reconcile(self) -> dict[str, list[str]]:
        self.reconcile_calls += 1
        return {category: [] for category in RECONCILIATION_CATEGORIES}

    def account(self) -> AccountSnapshot:
        self.account_calls += 1
        return AccountSnapshot(cash=1_000.0, equity=1_000.0, peak_equity=1_000.0, daily_pnl=0.0, positions=())

    def portfolio_open_order_symbols(self) -> set[str]:
        return set()

    def close(self) -> None:
        pass


class _BlockingStream:
    """Stands in for a connected socket: run() blocks until stop()."""

    def __init__(self) -> None:
        self._stopped = threading.Event()

    def subscribe(self, *_args: object, **_kwargs: object) -> None:
        pass

    def run(self) -> None:
        self._stopped.wait(5)

    run_stock = run

    def stop(self) -> None:
        self._stopped.set()


def test_stop_requested_during_warmup_submits_no_orders(tmp_path: Path, monkeypatch: Any) -> None:
    """The real startup sequence: a stop during warmup aborts before orders."""
    cancel = threading.Event()
    monkeypatch.setattr(runtime_module, "AlpacaBroker", _CleanBroker)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    runtime = EngineRuntime(
        "key",
        "secret",
        Config(
            universe=UniverseConfig(equity=EquityUniverseConfig(symbols=("SPY",))),
            runtime=RuntimeConfig(decision_db_path=str(tmp_path / "runtime.sqlite3")),
        ),
        startup_cancel=cancel,
    )
    runtime._snapshot_writer = None
    runtime.stream = _BlockingStream()
    runtime.trading_updates = _BlockingStream()
    placed: list[str] = []
    monkeypatch.setattr(runtime, "_warmup", lambda: cancel.set())
    monkeypatch.setattr(runtime, "_resume_startup_rebalances", lambda **_kw: placed.append("resume"))
    monkeypatch.setattr(runtime, "_run_startup_catch_up", lambda **_kw: placed.append("catch_up"))
    monkeypatch.setattr(runtime, "_refresh_readiness", lambda: None)
    monkeypatch.setattr(runtime, "_decision_loop", runtime._stop_event.wait)
    monkeypatch.setattr(runtime, "_account_loop", runtime._stop_event.wait)

    with pytest.raises(StartupCancelled, match="cancelled by a stop request"):
        runtime.start()

    assert placed == []
    assert runtime._running is False


def test_stop_engine_signals_the_in_flight_start(monkeypatch: Any) -> None:
    manager = _manager(Path("."))
    manager.engine_state = "starting"

    manager.stop_engine()

    assert manager._start_cancel.is_set()
    assert manager.engine_state == "stopping"


def test_start_cancellation_ends_stopped_without_error(tmp_path: Path, monkeypatch: Any) -> None:
    """A cancelled startup is the outcome the operator asked for, not an error."""
    _write_settings(tmp_path, _PAPER_AUTO_START_CONFIG)

    class _CancellingRuntime:
        broker = None

        def __init__(self, **_kwargs: Any) -> None:
            self.start_calls = 0

        def start(self) -> None:
            self.start_calls += 1
            raise StartupCancelled("Engine startup was cancelled by a stop request")

        def stop(self) -> bool:
            return True

    class _BacktestGuard:
        lock = threading.RLock()
        is_running = False

    monkeypatch.setattr(runtime_module, "EngineRuntime", _CancellingRuntime)
    monkeypatch.setattr(
        engine_manager_module, "load_config", lambda _path: Config(runtime=RuntimeConfig(mode=RuntimeMode.PAPER))
    )
    monkeypatch.setattr(engine_manager_module, "_scan_tracked_secrets", lambda _root: None)
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    manager = _manager(tmp_path)
    manager.security_warning = None
    manager.bind_backtest_manager(_BacktestGuard())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="cancelled by a stop request"):
        manager.start_engine()

    assert manager.engine_state == "stopped"
    assert manager.last_engine_error is None
    assert manager.runtime is None


# ---------------------------------------------------------------------------
# F46: the live account probe stays offline while the live gates are closed
# ---------------------------------------------------------------------------


class _RecordingLiveBroker:
    """Stands in for the LIVE broker so any probe is observable offline."""

    constructed: list[Any] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        type(self).constructed.append(self)
        self.account_id = "acct-live-1"

    def account(self) -> object:
        return object()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _record_live_probes(monkeypatch: Any) -> None:
    _RecordingLiveBroker.constructed = []
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _RecordingLiveBroker)


def _prerequisites_with(live_enabled: bool, env_armed: bool, root: Path, monkeypatch: Any) -> dict[str, Any]:
    _write_settings(
        root,
        {
            "app": {"auto_start_engine": True},
            "runtime": {"mode": "paper"},
            "live": {"enabled": live_enabled, "hard_dollar_cap": 0, "operator_approval_path": ""},
        },
    )
    if env_armed:
        monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    else:
        monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE", raising=False)
    return _manager(root).live_prerequisites()


def test_paper_config_never_sends_credentials_to_the_live_endpoint(tmp_path: Path, monkeypatch: Any) -> None:
    result = _prerequisites_with(live_enabled=False, env_armed=False, root=tmp_path, monkeypatch=monkeypatch)

    assert _RecordingLiveBroker.constructed == []
    assert result["all_met"] is False


def test_live_enabled_without_env_still_never_probes(tmp_path: Path, monkeypatch: Any) -> None:
    _prerequisites_with(live_enabled=True, env_armed=False, root=tmp_path, monkeypatch=monkeypatch)

    assert _RecordingLiveBroker.constructed == []


def test_both_live_gates_open_probe_the_live_account(tmp_path: Path, monkeypatch: Any) -> None:
    _prerequisites_with(live_enabled=True, env_armed=True, root=tmp_path, monkeypatch=monkeypatch)

    assert len(_RecordingLiveBroker.constructed) == 1
