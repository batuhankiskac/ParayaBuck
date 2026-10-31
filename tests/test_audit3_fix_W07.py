"""Audit wave 3 (W07): engine state machine, lifespan, LIVE safety, config parsing.

Deterministic: tmp_path state only, no network, no credentials, no data/ paths.
"""

from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from parayabuck import config as config_module
from parayabuck import live_safety
from parayabuck.config import load_config, strict_bool
from parayabuck.live_safety import LiveApprovalError, StandingLiveAuthorization, check_approval_key
from parayabuck.runtime import TransientStartupError
from parayabuck.utils import io as io_utils
from parayabuck.web import app as web_app
from parayabuck.web import lifespan as lifespan_module
from parayabuck.web import security
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers import engine as engine_router


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "none.env"))
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "settings.yaml"))
    monkeypatch.delenv("PARAYABUCK_CONTAINER", raising=False)
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_text: str = "app:\n") -> EngineManager:
    monkeypatch.setattr(engine_module, "_tracked_secret_warning", lambda root: None)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    manager = EngineManager()
    manager.config_path = config_path
    manager.env_path = tmp_path / "none.env"
    marker_dir = tmp_path / "runtime"
    manager.operator_halt_marker = lambda: marker_dir / "OPERATOR_HALT"  # type: ignore[method-assign]
    return manager


def _alert_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    def record(*, reason: str, message: str, auto_start: bool, attempts: int | None = None) -> None:
        alerts.append({"reason": reason, "message": message, "auto_start": auto_start})

    monkeypatch.setattr(engine_module, "_alert_start_failure", record)
    return alerts


# --- D3-5-3 / R3-2-2: strict booleans -------------------------------------------------


def test_strict_bool_accepts_only_real_booleans() -> None:
    assert strict_bool(True, "x") is True
    assert strict_bool(False, "x") is False
    for bad in ("false", "no", "0", 0, 1, None):
        with pytest.raises(ValueError, match="must be a boolean"):
            strict_bool(bad, "x")


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("app", "auto_start_engine"),
        ("app", "open_browser"),
        ("alpaca", "data_delayed"),
        ("alerting", "daily_summary"),
        ("backtest", "equity_use_effective_dated_fees"),
        ("runtime", "stale_account_sell_only"),
        ("runtime", "per_symbol_drift_lock"),
        ("risk", "adjust_for_cash_flows"),
        ("intent_watchdog", "fail_readiness"),
    ],
)
def test_quoted_boolean_config_field_is_refused(tmp_path: Path, section: str, field: str) -> None:
    path = tmp_path / "quoted.yaml"
    path.write_text(f'{section}:\n  {field}: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_config(str(path))


_VALID_UNIVERSE = "universe:\n  equity:\n    enabled: true\n    symbols: [QQQ, QLD]\n"


def test_real_boolean_config_fields_still_load(tmp_path: Path) -> None:
    path = tmp_path / "bools.yaml"
    path.write_text(
        _VALID_UNIVERSE
        + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n"
        + "app:\n  auto_start_engine: false\n  open_browser: false\n"
        "alpaca:\n  data_delayed: true\n"
        "alerting:\n  daily_summary: false\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.app.auto_start_engine is False
    assert cfg.alpaca.data_delayed is True
    assert cfg.alerting.daily_summary is False


def test_alpaca_data_client_refuses_quoted_data_delayed() -> None:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    with pytest.raises(ValueError, match="data_delayed"):
        AlpacaHistoricalData("key", "secret", data_delayed="false")  # type: ignore[arg-type]


def test_quoted_auto_start_is_not_auto_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch, 'app:\n  auto_start_engine: "false"\nruntime:\n  mode: paper\n')
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "testsecret")
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    manager._operator_halt_refusal = lambda: None  # type: ignore[method-assign]
    assert manager.should_auto_start() is False


def test_boolean_auto_start_true_still_auto_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch, "app:\n  auto_start_engine: true\nruntime:\n  mode: paper\n")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "testsecret")
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    manager._operator_halt_refusal = lambda: None  # type: ignore[method-assign]
    assert manager.should_auto_start() is True


# --- S3-3-1: fail-closed marker presence ----------------------------------------------


def test_marker_present_fails_closed_on_lstat_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "OPERATOR_STOPPED"
    assert live_safety.marker_present(target) is False
    real_lstat = os.lstat

    def denied(path: Any, *args: Any, **kwargs: Any) -> Any:
        if os.fspath(path) == os.fspath(target):
            raise PermissionError("EACCES")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    assert live_safety.marker_present(target) is True


def test_operator_stop_marker_with_lstat_error_blocks_auto_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch, "app:\n  auto_start_engine: true\nruntime:\n  mode: paper\n")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "testsecret")
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    manager._operator_halt_refusal = lambda: None  # type: ignore[method-assign]
    stop_marker = manager._operator_stop_marker()
    real_lstat = os.lstat

    def denied(path: Any, *args: Any, **kwargs: Any) -> Any:
        if os.fspath(path) == os.fspath(stop_marker):
            raise PermissionError("EACCES")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    assert manager.should_auto_start() is False


# --- S3-3-6: directory fsync after clearing the halt marker ---------------------------


def test_clear_operator_halt_fsyncs_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "OPERATOR_HALT"
    marker.write_text("{}", encoding="utf-8")
    synced: list[Path] = []
    monkeypatch.setattr(io_utils, "_fsync_directory", lambda directory: synced.append(Path(directory)))
    assert live_safety.clear_operator_halt(marker) is True
    assert not marker.exists()
    assert synced == [tmp_path]


# --- S3-1-4: repeated-block approval keys ---------------------------------------------


def test_repeated_block_approval_key_is_refused() -> None:
    with pytest.raises(ValueError, match="repeated block"):
        check_approval_key("0123456789" * 4)


def test_random_looking_approval_key_is_accepted() -> None:
    check_approval_key("k7Qz!mP2xR9vB4nL0sT6yW8uH3jG5cD1a")


# --- C3-3-1: a Stop after a transient attempt settles 'stopping' -----------------------


def test_stop_after_transient_failure_settles_to_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    def attempt(*, auto_start: bool = False, settings_restart: bool = False) -> None:
        # A Stop lands after the attempt has ended and before the retry handler runs.
        manager.engine_state = "stopping"
        raise TransientStartupError("network unreachable")

    manager.start_engine = attempt  # type: ignore[method-assign]
    manager.engine_state = "starting"
    asyncio.run(manager.auto_start_engine())
    assert manager.engine_state == "stopped"
    assert manager.last_engine_error is None
    assert manager.runtime is None


# --- C3-3-2: an unreadable .env settles the state ------------------------------------


def test_unreadable_env_during_manual_start_settles_to_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    alerts = _alert_recorder(monkeypatch)
    monkeypatch.setattr(engine_module, "trading_host_authorized", lambda: True)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    manager._operator_halt_refusal = lambda: None  # type: ignore[method-assign]
    manager.bind_backtest_manager(SimpleNamespace(lock=threading.RLock(), is_running=False))

    def unreadable() -> tuple[str | None, str | None]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    manager.get_credentials = unreadable  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="okunamadı"):
        manager.start_engine()
    assert manager.engine_state == "error"
    assert alerts and alerts[0]["reason"] == "credentials_unreadable"


# --- C3-3-3: operator Stop marker is written under the state lock --------------------


def test_operator_stop_marker_is_written_under_state_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    seen_owned: list[bool] = []

    def write_marker() -> None:
        seen_owned.append(manager._state_lock._is_owned())  # type: ignore[attr-defined]

    monkeypatch.setattr(manager, "_write_operator_stop_marker", write_marker)
    assert manager.stop_engine(operator_request=True) == "stopped"
    assert seen_owned == [True]


def test_unrecordable_operator_stop_still_stops_and_alerts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    alerts = _alert_recorder(monkeypatch)

    def fail_write() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_write_operator_stop_marker", fail_write)
    assert manager.stop_engine(operator_request=True) == "stopped"
    assert [alert["reason"] for alert in alerts] == ["operator_stop_unrecorded"]


# --- C3-3-4: shutdown flags are set under the state lock -----------------------------


def test_request_shutdown_sets_cancel_under_state_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    owned: list[bool] = []

    class Recorder:
        def set(self) -> None:
            owned.append(manager._state_lock._is_owned())  # type: ignore[attr-defined]

    manager._start_cancel = Recorder()  # type: ignore[assignment]
    manager.request_shutdown()
    assert owned == [True]
    assert manager._shutdown_requested.is_set()


# --- C3-3-5: the stop clock starts once the stop holds the ops lock -----------------


def test_queued_stop_does_not_start_the_watchdog_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)

    class FakeRuntime:
        broker = None

        def stop(self) -> bool:
            return True

    manager.runtime = FakeRuntime()  # type: ignore[assignment]
    manager.engine_state = "running"
    manager._runtime_ops_lock.acquire()
    worker = threading.Thread(target=manager.stop_engine, daemon=True)
    try:
        worker.start()
        deadline = time.monotonic() + 5.0
        while manager.engine_state != "stopping" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.engine_state == "stopping"
        # Queued behind the ops lock: the clock must not be running yet.
        assert manager._stop_started_monotonic is None
    finally:
        manager._runtime_ops_lock.release()
    worker.join(5.0)
    assert not worker.is_alive()
    assert manager.engine_state == "stopped"
    assert manager._stop_started_monotonic is not None


# --- R3-4-3: the auto-start attempt runs on a daemon thread ---------------------------


def test_auto_start_attempt_runs_on_a_daemon_thread() -> None:
    seen: dict[str, Any] = {}

    def attempt(*, auto_start: bool) -> str:
        seen["daemon"] = threading.current_thread().daemon
        seen["auto_start"] = auto_start
        return "ok"

    result = asyncio.run(engine_module._run_on_daemon_thread(attempt, auto_start=True))
    assert result == "ok"
    assert seen == {"daemon": True, "auto_start": True}


def test_daemon_thread_runner_propagates_exceptions() -> None:
    def attempt() -> None:
        raise TransientStartupError("boom")

    with pytest.raises(TransientStartupError):
        asyncio.run(engine_module._run_on_daemon_thread(attempt))


# --- D3-5-4: refusals name the next command ------------------------------------------


def test_standing_refusal_names_the_next_command() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    authorization = StandingLiveAuthorization(
        operator_id="op",
        account_id="acct",
        configuration_digests=("digest",),
        config_fingerprint="fp",
        maximum_dollar_exposure=1000.0,
        trading_host="host-a",
        approved_at=now,
        expires_at=now + timedelta(days=1),
        nonce="n1",
    )
    with pytest.raises(LiveApprovalError, match="authorize-live"):
        authorization.verify_binding(
            configuration_digests=("digest",),
            config_fingerprint="fp",
            hard_dollar_cap=1000.0,
            trading_host="host-b",
            now=now,
        )


# --- D3-5-5: LIVE approval refusals are 400, not a generic 500 -----------------------


def test_live_approval_refusal_maps_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    class Manager:
        def start_engine(self) -> None:
            raise LiveApprovalError("Live operator approval has expired")

    monkeypatch.setattr(engine_router, "get_engine_manager", lambda request: Manager())
    app = FastAPI()
    app.include_router(engine_router.router)
    response = TestClient(app).post("/api/engine/start")
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


# --- S3-1-1: container peers are trusted only from the bridge gateway ----------------


def test_gateway_address_is_read_from_the_default_route(tmp_path: Path) -> None:
    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n",
        encoding="ascii",
    )
    assert security._container_gateway_address(table) == "172.17.0.1"


def test_gateway_address_is_none_without_a_default_route(tmp_path: Path) -> None:
    table = tmp_path / "route"
    table.write_text("Iface\tDestination\tGateway\neth0\t000011AC\t00000000\n", encoding="ascii")
    assert security._container_gateway_address(table) is None
    assert security._container_gateway_address(tmp_path / "missing") is None


def test_only_the_gateway_is_trusted_in_container_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    monkeypatch.setattr(security, "_container_gateway_address", lambda: "172.17.0.1")
    assert security.is_trusted_client("172.17.0.1") is True
    assert security.is_trusted_client("172.17.0.9") is False


# --- R3-4-5: READY=1 only once the socket is bound -----------------------------------


async def _noop_asgi(scope: Any, receive: Any, send: Any) -> None:
    return None


def test_server_sends_ready_after_bind_not_in_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    ready_calls: list[str] = []
    monkeypatch.setattr(web_app.sd_notify, "ready", lambda: ready_calls.append("ready") or True)

    async def fake_startup(self: Any, sockets: Any = None) -> None:
        return None

    monkeypatch.setattr(uvicorn.Server, "startup", fake_startup)
    server = web_app._ExitingServer(uvicorn.Config(_noop_asgi))
    server.lifespan = SimpleNamespace(should_exit=False)  # type: ignore[assignment]
    asyncio.run(server.startup())
    assert ready_calls == ["ready"]

    ready_calls.clear()
    server.lifespan = SimpleNamespace(should_exit=True)  # type: ignore[assignment]
    asyncio.run(server.startup())
    assert ready_calls == []


def test_lifespan_no_longer_sends_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    ready_calls: list[str] = []
    monkeypatch.setattr(lifespan_module.sd_notify, "ready", lambda: ready_calls.append("ready") or True)
    monkeypatch.setattr(lifespan_module.sd_notify, "WatchdogPinger", _FakePinger)
    app_lifespan = lifespan_module.create_lifespan(lambda: None)

    async def run() -> None:
        async with app_lifespan(SimpleNamespace(state=SimpleNamespace())):
            pass

    asyncio.run(run())
    assert ready_calls == []


class _FakePinger:
    def __init__(self, gate: Any) -> None:
        self.gate = gate

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


# --- Q3-3-5: main() restores the caller's umask --------------------------------------


def test_main_restores_the_callers_umask(monkeypatch: pytest.MonkeyPatch) -> None:
    inside: list[int] = []

    def fake_serve(config_path: Any) -> None:
        current = os.umask(0o077)
        os.umask(current)
        inside.append(current)

    monkeypatch.setattr(web_app, "_serve", fake_serve)
    original = os.umask(0o022)
    try:
        web_app.main()
        after = os.umask(0o022)
        assert inside == [0o077]
        assert after == 0o022
    finally:
        os.umask(original)


def test_main_restores_umask_when_startup_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(config_path: Any) -> None:
        raise ValueError("refused")

    monkeypatch.setattr(web_app, "_serve", refuse)
    original = os.umask(0o022)
    try:
        with pytest.raises(ValueError, match="refused"):
            web_app.main()
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(original)


# --- (a) strategy defaults are logged -------------------------------------------------


class _RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple[Any, ...]]] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append((message, args))


def test_missing_strategy_parameters_logs_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLog()
    monkeypatch.setattr(config_module, "_config_log", recorder)
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_UNIVERSE + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n", encoding="utf-8")
    load_config(str(path))
    assert len(recorder.warnings) == 1
    assert "strategy.equity.parameters" in recorder.warnings[0][0]


def test_explicit_strategy_parameters_log_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLog()
    monkeypatch.setattr(config_module, "_config_log", recorder)
    path = tmp_path / "settings.yaml"
    path.write_text(
        _VALID_UNIVERSE
        + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n    parameters:\n      sma_bars: 200\n",
        encoding="utf-8",
    )
    load_config(str(path))
    assert recorder.warnings == []
    path.write_text(_VALID_UNIVERSE + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n", encoding="utf-8")
    load_config(str(path))
    warned = len(recorder.warnings)
    path.write_text(
        _VALID_UNIVERSE
        + "strategy:\n  equity:\n    name: equity_leveraged_trend_v1\n    parameters:\n      sma_bars: 200\n",
        encoding="utf-8",
    )
    load_config(str(path))
    assert len(recorder.warnings) == warned


# --- (b) a loose .env refuses a LIVE start --------------------------------------------


def test_loose_env_refuses_live_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text("ALPACA_API_KEY=PKTESTKEY\nALPACA_SECRET_KEY=testsecret\n", encoding="utf-8")
    os.chmod(env_path, 0o644)
    assert stat.S_IMODE(env_path.stat().st_mode) & 0o077
    manager.env_path = env_path
    import parayabuck.monitoring.email_alerts as email_alerts

    monkeypatch.setattr(email_alerts, "validate_email_setup", lambda email: [])
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6")
    cfg = SimpleNamespace(
        live=SimpleNamespace(
            enabled=True,
            hard_dollar_cap=1000.0,
            operator_approval_path="approval.json",
            operator_approval_key_env="PARAYABUCK_OPERATOR_APPROVAL_KEY",
        ),
        runtime=SimpleNamespace(decision_db_path="data/runtime/live/parayabuck.sqlite3"),
        alerting=SimpleNamespace(email=SimpleNamespace(enabled=True)),
    )
    refusal = manager._live_doctor_refusal(cfg, "AKLIVEKEY", "livesecret")
    assert refusal is not None
    assert "accessible by group/other" in refusal
