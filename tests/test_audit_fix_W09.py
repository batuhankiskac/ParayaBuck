"""Regression tests for the 2026-10-27 audit fixes owned by worker W09.

Covers the engine manager, lifespan, app shutdown, engine/safety routers and the
settings service. Every test uses stubs and tmp_path: no broker, network,
credentials, .env or data/ path is touched.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dotenv import dotenv_values
from fastapi import HTTPException

import parayabuck.execution.alpaca as alpaca_module
import parayabuck.monitoring.email_alerts as email_alerts_module
import parayabuck.runtime as runtime_module
import parayabuck.web.app as app_module
from parayabuck.runtime import TransientStartupError
from parayabuck.web import lifespan as lifespan_module
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager, LiveArmToken, _atomic_update_credentials
from parayabuck.web.routers import engine as engine_router
from parayabuck.web.schemas.settings import SettingsPayload
from parayabuck.web.services import settings as settings_service

_KEY = "PKkey123456789"
_SECRET = "PKsecret12345"


class _StopRuntime:
    """Fake EngineRuntime: start() may raise, stop() returns a configurable result."""

    def __init__(self, *, start_error: BaseException | None = None, stop_result: bool | None = True, **_: Any) -> None:
        self.broker = SimpleNamespace(close=lambda: None)
        self.start_error = start_error
        self.stop_result = stop_result
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> bool | None:
        self.stop_calls += 1
        return self.stop_result

    def health(self) -> Any:
        return SimpleNamespace(state="running")


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: Any = None) -> EngineManager:
    """A paper EngineManager whose runtime is ``factory(**kwargs)`` and whose config is a stub."""
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    monkeypatch.setattr(
        engine_module,
        "load_config",
        lambda _path: SimpleNamespace(runtime=SimpleNamespace(mode=SimpleNamespace(value="paper"))),
    )
    monkeypatch.setattr(runtime_module, "EngineRuntime", lambda **kwargs: (factory or _StopRuntime)(**kwargs))
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.env_path = tmp_path / ".env"
    manager.security_warning = None
    monkeypatch.setattr(manager, "get_credentials", lambda: (_KEY, _SECRET))
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: (_KEY, _SECRET))
    monkeypatch.setattr(manager, "_operator_halt_refusal", lambda: None)
    # A start clears the operator Stop record: keep it out of the checkout's data/runtime.
    stop_marker = tmp_path / "runtime" / engine_module.OPERATOR_STOP_MARKER_FILENAME
    monkeypatch.setattr(manager, "_operator_stop_marker", lambda: stop_marker)
    return manager


# --- S8-1: a failed or interrupted start whose stop reports workers alive ---------------


def test_failed_start_whose_stop_reports_workers_alive_keeps_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _StopRuntime(start_error=RuntimeError("broker rejected"), stop_result=False)
    manager = _manager(tmp_path, monkeypatch, lambda **kwargs: runtime)

    with pytest.raises(RuntimeError, match="broker rejected"):
        manager.start_engine()

    assert manager.engine_state == "stop_failed"
    assert manager.runtime is runtime
    assert manager.has_active_runtime() is True
    with pytest.raises(ValueError, match="Motor zaten durdurma hatası"):
        manager.start_engine()
    assert runtime.start_calls == 1


def test_transient_start_whose_stop_reports_workers_alive_gets_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _StopRuntime(start_error=TransientStartupError("timeout"), stop_result=False)
    manager = _manager(tmp_path, monkeypatch, lambda **kwargs: runtime)

    with pytest.raises(TransientStartupError):
        manager.start_engine(auto_start=True)

    assert manager.engine_state == "stop_failed"
    assert manager.runtime is runtime


def test_failed_start_with_a_clean_stop_still_drops_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _StopRuntime(start_error=RuntimeError("boom"), stop_result=True)
    manager = _manager(tmp_path, monkeypatch, lambda **kwargs: runtime)

    with pytest.raises(RuntimeError, match="boom"):
        manager.start_engine()

    assert manager.engine_state == "error"
    assert manager.runtime is None


# --- Q9-10: reset/risk-epoch exclusion and the settings write window -------------------


def test_stop_waits_for_a_safety_reset_that_holds_the_ops_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    runtime = _StopRuntime()
    manager.runtime = runtime
    manager.engine_state = "running"

    manager._runtime_ops_lock.acquire()
    worker = threading.Thread(target=manager.stop_engine)
    worker.start()
    try:
        time.sleep(0.1)
        assert runtime.stop_calls == 0
    finally:
        manager._runtime_ops_lock.release()
    worker.join(5)

    assert runtime.stop_calls == 1
    assert manager.runtime is None


def test_starts_are_refused_inside_a_settings_write_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    manager.begin_settings_write()
    try:
        with pytest.raises(ValueError, match="ayar kaydı sürüyor"):
            manager.start_engine()
        assert manager.engine_state == "stopped"
    finally:
        manager.end_settings_write()

    manager.start_engine()
    assert manager.engine_state == "running"


# --- S8-2 / Q9-1: the mode write is re-checked under the state lock --------------------


def test_mode_write_is_refused_while_the_engine_state_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    writes: list[bool] = []
    manager.engine_state = "starting"

    with pytest.raises(ValueError, match="işlem modu değiştirilemez"):
        manager.write_mode_if_idle(False, lambda _path, paper: writes.append(paper))
    assert writes == []

    manager.engine_state = "stopped"
    manager.write_mode_if_idle(True, lambda _path, paper: writes.append(paper))
    assert writes == [True]


# --- I-3 / R7-4: watchdog gate ---------------------------------------------------------


def test_watchdog_stops_pinging_when_a_start_attempt_hangs_past_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    manager._start_attempt_monotonic = time.monotonic() - (engine_module._WATCHDOG_STARTUP_GRACE_SECONDS + 1)
    assert manager.watchdog_healthy() is False

    manager._start_attempt_monotonic = time.monotonic()
    assert manager.watchdog_healthy() is True

    manager._start_attempt_monotonic = None
    assert manager.watchdog_healthy() is True


def test_watchdog_stops_pinging_when_the_decision_worker_is_wedged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=engine_module._WATCHDOG_DECISION_LOOP_MAX_AGE_SECONDS + 5)
    runtime = SimpleNamespace(
        _running=True,
        last_account_loop_pass_at=now,
        _workers={"pb-decisions": SimpleNamespace(last_heartbeat=stale)},
    )
    manager.runtime = runtime
    assert manager.watchdog_healthy(now=now) is False

    runtime._workers["pb-decisions"].last_heartbeat = now
    assert manager.watchdog_healthy(now=now) is True


# --- CC-7 / perf 3: account refresh lock and negative cache ----------------------------


def test_stop_does_not_hold_the_account_cache_lock_while_the_runtime_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    seen: dict[str, bool] = {}

    class _Runtime(_StopRuntime):
        def stop(self) -> bool:
            seen["account_cache_lock_held"] = manager._account_lock.locked()
            seen["refresh_lock_held"] = manager._account_refresh_lock.locked()
            return True

    manager.runtime = _Runtime()
    manager.engine_state = "running"
    manager.stop_engine()

    assert seen == {"account_cache_lock_held": False, "refresh_lock_held": True}


def test_poll_answers_from_the_last_snapshot_while_a_refresh_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    manager.last_account = {"equity": 5.0, "source": "broker"}
    manager._last_account_monotonic = time.monotonic() - 999

    assert manager._account_refresh_lock.acquire(blocking=False)
    try:
        started = time.monotonic()
        snapshot = manager.fetch_account_snapshot()
        elapsed = time.monotonic() - started
    finally:
        manager._account_refresh_lock.release()

    assert snapshot["stale"] is True
    assert snapshot["equity"] == 5.0
    assert elapsed < 1.0


def test_failed_account_refresh_is_not_retried_inside_the_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    calls: list[int] = []

    class _DownBroker:
        def account(self) -> Any:
            calls.append(1)
            raise RuntimeError("broker unreachable")

    manager.broker = _DownBroker()
    first = manager.fetch_account_snapshot()
    second = manager.fetch_account_snapshot()

    assert len(calls) == 1
    assert first["source"] == second["source"] == "unavailable"

    manager._account_failed_monotonic = time.monotonic() - (engine_module._ACCOUNT_FAILURE_BACKOFF_SECONDS + 1)
    manager.fetch_account_snapshot()
    assert len(calls) == 2


# --- Q-9 / I-8: one mode read per refresh -----------------------------------------------


def test_account_refresh_reads_the_mode_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    reads: list[int] = []

    def mode_read() -> bool:
        reads.append(1)
        return True  # PAPER

    seen: dict[str, Any] = {}

    class _Broker:
        def __init__(self, key: str, secret: str, *, paper: bool) -> None:
            seen.update(key=key, paper=paper)

        def account(self) -> Any:
            raise RuntimeError("no account in test")

    monkeypatch.setattr(manager, "_read_paper_mode", mode_read)
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaper", "paper-secret"))
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _Broker)

    manager._refresh_account_snapshot()

    assert len(reads) == 1
    assert seen == {"key": "PKpaper", "paper": True}


# --- S8-3: the LIVE display poll needs the host and environment gates -------------------


def test_live_display_poll_builds_no_live_client_without_the_live_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    built: list[Any] = []
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE", raising=False)
    monkeypatch.setattr(manager, "_read_paper_mode", lambda: False)
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlive123456", "live-secret"))
    monkeypatch.setattr(alpaca_module, "AlpacaBroker", lambda *a, **k: built.append(k) or SimpleNamespace())

    snapshot = manager.fetch_account_snapshot()

    assert built == []
    assert snapshot["source"] == "unavailable"


def test_live_display_snapshot_is_refused_when_the_account_is_not_the_live_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setattr(manager, "_read_paper_mode", lambda: False)
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlive123456", "live-secret"))
    monkeypatch.setattr(
        engine_module,
        "load_config",
        lambda _path: SimpleNamespace(broker=SimpleNamespace(expected_account_id="")),
    )

    class _PaperAccountBroker:
        account_number = "PA1234567"
        account_id = "acct-paper"
        account_suffix = "4567"

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def account(self) -> Any:
            return SimpleNamespace()

    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _PaperAccountBroker)

    snapshot = manager.fetch_account_snapshot()

    assert snapshot["source"] == "unavailable"
    assert manager.last_account is None


# --- S6-1: credentials read stripped; start-failure text scrubbed -----------------------


def test_credentials_are_read_stripped_from_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "CREDENTIALS_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text('ALPACA_API_KEY="  PKkeyvalue123\n"\nALPACA_SECRET_KEY=PKsecret1234\n', encoding="utf-8")
    manager = _manager(tmp_path, monkeypatch)
    manager.env_path = env

    assert manager._read_credentials(live=False) == ("PKkeyvalue123", "PKsecret1234")


def test_start_failure_text_never_carries_the_configured_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(engine_module, "_alert_start_failure", lambda **kwargs: alerts.append(kwargs))
    runtime = _StopRuntime(start_error=RuntimeError(f"Invalid header value {_SECRET}"), stop_result=True)
    manager = _manager(tmp_path, monkeypatch, lambda **kwargs: runtime)

    with pytest.raises(RuntimeError):
        manager.start_engine()

    assert _SECRET not in str(manager.last_engine_error)
    assert all(_SECRET not in alert["message"] for alert in alerts)
    assert alerts and "[redacted]" in alerts[0]["message"]


def test_redact_secrets_scrubs_both_mode_pairs_and_skips_short_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaperkey12", "x"))
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlivekey123", None))

    text = manager.redact_secrets("PKpaperkey12 and AKlivekey123 and x")

    assert text == "[redacted] and [redacted] and x"


# --- R7-6: LIVE enable flag follows the .env file ---------------------------------------


def test_live_enable_flag_follows_the_env_file_once_it_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    env = tmp_path / "live.env"
    env.write_text("ALPACA_API_KEY=PKkey\n", encoding="utf-8")
    manager.env_path = env
    assert manager._live_env_enabled() is False  # removed from the file: disarmed despite the inherited env

    env.write_text("PARAYABUCK_ENABLE_LIVE=YES\n", encoding="utf-8")
    assert manager._live_env_enabled() is True

    manager.env_path = tmp_path / "absent.env"
    assert manager._live_env_enabled() is True  # no file: the process environment decides


# --- D7-10: halt marker refusal alerts ------------------------------------------------


def test_auto_start_refused_by_the_halt_marker_raises_an_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(engine_module, "_alert_start_failure", lambda **kwargs: alerts.append(kwargs))
    monkeypatch.setattr(manager, "_operator_halt_refusal", lambda: "Engine start refused: operator halt is in force")

    assert manager.should_auto_start() is False
    assert [alert["reason"] for alert in alerts] == ["operator_halt"]


# --- S8-6: doctor's LIVE gates on every LIVE start --------------------------------------


def _live_cfg(tmp_path: Path, **live_overrides: Any) -> SimpleNamespace:
    live = {
        "enabled": True,
        "hard_dollar_cap": 1000.0,
        "operator_approval_path": str(tmp_path / "approval.json"),
        "operator_approval_key_env": "PARAYABUCK_OPERATOR_APPROVAL_KEY",
    }
    live.update(live_overrides)
    return SimpleNamespace(
        live=SimpleNamespace(**live),
        alerting=SimpleNamespace(email=SimpleNamespace(enabled=True)),
        runtime=SimpleNamespace(decision_db_path=str(tmp_path / "live.sqlite3")),
    )


def test_live_start_gates_refuse_a_configuration_doctor_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaperkey12", "PKpapersecret"))
    monkeypatch.setattr(email_alerts_module, "validate_email_setup", lambda settings, env=None: [])

    assert manager._live_doctor_refusal(_live_cfg(tmp_path), "AKlive123456", "AKlivesecret") is None
    assert "live.enabled" in manager._live_doctor_refusal(
        _live_cfg(tmp_path, enabled=False), "AKlive123456", "AKlivesecret"
    )
    assert "hard_dollar_cap" in manager._live_doctor_refusal(
        _live_cfg(tmp_path, hard_dollar_cap=0), "AKlive123456", "AKlivesecret"
    )
    assert "share a key" in manager._live_doctor_refusal(_live_cfg(tmp_path), "PKpaperkey12", "AKlivesecret")

    cfg = _live_cfg(tmp_path)
    cfg.alerting.email.enabled = False
    assert "alerting.email.enabled" in manager._live_doctor_refusal(cfg, "AKlive123456", "AKlivesecret")


def test_live_start_gates_refuse_a_short_approval_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "short")
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaperkey12", "PKpapersecret"))

    refusal = manager._live_doctor_refusal(_live_cfg(tmp_path), "AKlive123456", "AKlivesecret")

    assert refusal is not None and "PARAYABUCK_OPERATOR_APPROVAL_KEY" in refusal


# --- S-3: approval file validation ----------------------------------------------------


def _approval_items(manager: EngineManager, live_cfg: dict[str, Any]) -> tuple[Any, dict[str, str | None]]:
    items: dict[str, str | None] = {}

    def item(item_id: str, label: str, satisfied: bool, missing: str | None = None, state: str | None = None) -> None:
        items[item_id] = missing

    approval, _error, _key, _path = manager._check_approval_signature(item, live_cfg)
    return approval, items


def test_oversized_approval_file_is_refused_before_it_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "0123456789abcdef0123456789abcdef")
    path = tmp_path / "approval.json"
    path.write_text("{" + " " * engine_module._APPROVAL_FILE_MAX_BYTES + "}", encoding="utf-8")

    approval, items = _approval_items(
        manager,
        {"operator_approval_path": str(path), "operator_approval_key_env": "PARAYABUCK_OPERATOR_APPROVAL_KEY"},
    )

    assert approval is None
    assert "bayttan büyük" in str(items["approval_signature"])


def test_approval_parse_error_does_not_echo_the_file_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "0123456789abcdef0123456789abcdef")
    path = tmp_path / "approval.json"
    path.write_text('{"approved_at": "not-a-date-marker-7741"}', encoding="utf-8")

    approval, items = _approval_items(
        manager,
        {"operator_approval_path": str(path), "operator_approval_key_env": "PARAYABUCK_OPERATOR_APPROVAL_KEY"},
    )

    assert approval is None
    assert "marker-7741" not in str(items["approval_signature"])
    assert "okunamıyor veya geçersiz" in str(items["approval_signature"])


# --- S-7: the .env writer escapes backslashes -------------------------------------------


def test_env_writer_round_trips_backslashes_and_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    key = 'PK\\"tail\\'
    secret = "sec\\\\ret"

    _atomic_update_credentials(env, key, secret)

    values = dotenv_values(env)
    assert values["ALPACA_API_KEY"] == key
    assert values["ALPACA_SECRET_KEY"] == secret


# --- P7-7: a failed config write restores the rotated .env -----------------------------


def test_failed_config_write_restores_the_previous_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    previous = 'ALPACA_API_KEY="OLDKEY123456"\nALPACA_SECRET_KEY="OLDSECRET1234"\n'
    env.write_text(previous, encoding="utf-8")
    real_atomic_write = settings_service.atomic_write

    def failing_config_write(path: Path, content: str, mode: int | None = None) -> None:
        if path.name == "settings.yaml":
            raise OSError("disk full")
        real_atomic_write(path, content, mode)

    monkeypatch.setattr(settings_service, "atomic_write", failing_config_write)
    manager = SimpleNamespace(
        env_path=env,
        config_path=tmp_path / "settings.yaml",
        reset_broker=lambda: None,
    )

    with pytest.raises(OSError, match="disk full"):
        settings_service._write_settings_files(manager, {"runtime": {}}, "NEWKEY123456", "NEWSECRET1234", True)

    assert env.read_text(encoding="utf-8") == previous


# --- R7-2 / R7-3: shutdown lifecycle ----------------------------------------------------


class _GatedRuntime(_StopRuntime):
    """Runtime whose start() waits on an event and records whether the cancel flag was set."""

    def __init__(self, *, startup_cancel: threading.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.startup_cancel = startup_cancel
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_seen: bool | None = None

    def start(self) -> None:
        self.entered.set()
        assert self.release.wait(5)
        self.cancel_seen = self.startup_cancel.is_set()


def _gated_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[EngineManager, list[_GatedRuntime]]:
    made: list[_GatedRuntime] = []

    def factory(**kwargs: Any) -> _GatedRuntime:
        runtime = _GatedRuntime(**kwargs)
        made.append(runtime)
        return runtime

    manager = _manager(tmp_path, monkeypatch, factory)
    monkeypatch.setattr(manager, "should_auto_start", lambda: True)
    return manager, made


def test_shutdown_cancels_an_in_flight_start_before_waiting_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, made = _gated_manager(tmp_path, monkeypatch)
    lifespan = lifespan_module.create_lifespan(lambda: manager)

    async def run() -> None:
        async with lifespan(None):
            for _ in range(500):
                if made and made[0].entered.is_set():
                    break
                await asyncio.sleep(0.01)

            # Release the gated start only after the shutdown has raised its
            # cancel flag, so the ordering does not depend on a wall-clock delay.
            def release_after_cancel() -> None:
                if made[0].startup_cancel.wait(5):
                    made[0].release.set()

            threading.Thread(target=release_after_cancel, daemon=True).start()

    asyncio.run(asyncio.wait_for(run(), 10))

    assert made[0].cancel_seen is True
    assert manager.engine_state == "stopped"


def test_shutdown_wait_for_a_stuck_start_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifespan_module, "_SHUTDOWN_START_WAIT_SECONDS", 0.3)
    manager, made = _gated_manager(tmp_path, monkeypatch)
    lifespan = lifespan_module.create_lifespan(lambda: manager)
    exit_elapsed: list[float] = []

    async def run() -> None:
        exit_started: list[float] = []
        async with lifespan(None):
            for _ in range(500):
                if made and made[0].entered.is_set():
                    break
                await asyncio.sleep(0.01)
            threading.Timer(1.5, made[0].release.set).start()
            # Only the shutdown is timed: the wait for the start to enter is not part of it.
            exit_started.append(time.monotonic())
        exit_elapsed.append(time.monotonic() - exit_started[0])

    asyncio.run(asyncio.wait_for(run(), 10))

    assert exit_elapsed[0] < 1.2  # did not wait for the stuck start
    # The attempt runs on a daemon thread that asyncio.run no longer joins (R3-4-3):
    # wait for it, bounded, to finish the stop it was interrupted into.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and (
        manager.engine_state != "stopped"
        or any(t.name == "pb-auto-start" and t.is_alive() for t in threading.enumerate())
    ):
        time.sleep(0.02)
    assert manager.engine_state == "stopped"  # the late attempt was interrupted and stopped


def test_lifespan_records_a_stop_failure_on_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Manager:
        engine_state = "stopped"

        def should_auto_start(self) -> bool:
            return False

        def stop_engine(self) -> None:
            raise RuntimeError("workers did not stop")

    app = SimpleNamespace(state=SimpleNamespace())

    async def run() -> None:
        async with lifespan_module.create_lifespan(lambda: _Manager())(app):
            pass

    asyncio.run(run())

    assert app.state.engine_stop_failed is True


def _run_main_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stop_failed: bool) -> tuple[Any, dict[str, Any]]:
    fake_app = SimpleNamespace(state=SimpleNamespace(engine_stop_failed=stop_failed))
    fake_server = types.ModuleType("parayabuck.web.server")
    fake_server.app = fake_app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "parayabuck.web.server", fake_server)
    seen: dict[str, Any] = {}

    def fake_run(server: Any) -> None:
        # The web entry point serves through its own uvicorn Server subclass
        # (R2-4-1); the fake records the config it would have served with.
        seen.update(timeout_graceful_shutdown=server.config.timeout_graceful_shutdown)

    monkeypatch.setattr(app_module._ExitingServer, "run", fake_run)
    monkeypatch.setattr(app_module, "_probe_web_bind", lambda host, port: None)
    monkeypatch.setattr(app_module, "setup_logging", lambda **_kwargs: None)
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing-settings.yaml"))
    monkeypatch.setenv("OPEN_BROWSER", "0")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8765")
    outcome: Any = None
    try:
        app_module.main()
    except SystemExit as exc:
        outcome = exc
    return outcome, seen


def test_main_exits_non_zero_when_the_engine_workers_did_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome, seen = _run_main_with(monkeypatch, tmp_path, stop_failed=True)

    assert isinstance(outcome, SystemExit)
    assert outcome.code  # a message string: exit status 1
    assert 0 < seen["timeout_graceful_shutdown"] < 120


def test_main_exits_cleanly_when_the_engine_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome, seen = _run_main_with(monkeypatch, tmp_path, stop_failed=False)

    assert outcome is None
    assert 0 < seen["timeout_graceful_shutdown"] < 120


# --- Q9-7 / Q9-8 / Q9-3: HTTP mapping, phrase compare, probe text -----------------------


def test_start_route_maps_refusals_to_400_and_other_failures_to_a_generic_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Refusing:
        def start_engine(self, **_kwargs: object) -> None:
            raise ValueError("Engine is already running")

    class _Crashing:
        def start_engine(self, **_kwargs: object) -> None:
            raise RuntimeError("secret-leak-marker from the SDK")

    manager: Any = _Refusing()
    monkeypatch.setattr(engine_router, "get_engine_manager", lambda request: manager)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(engine_router.start_engine(request=None))  # type: ignore[arg-type]
    assert refused.value.status_code == 400
    assert refused.value.detail == "Engine is already running"

    manager = _Crashing()
    with pytest.raises(HTTPException) as crashed:
        asyncio.run(engine_router.start_engine(request=None))  # type: ignore[arg-type]
    assert crashed.value.status_code == 500
    assert "secret-leak-marker" not in str(crashed.value.detail)


def test_arming_phrase_with_non_ascii_text_is_refused_as_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "get_live_credentials", lambda: (_KEY, _SECRET))
    now = datetime.now(UTC)
    manager._pending_live_arm = LiveArmToken(
        account_id="acct-live",
        account_suffix="1234",
        credential_fingerprint=manager._credential_fingerprint(_KEY, _SECRET),
        config_digest=manager._config_digest(paper=False),
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
    )

    with pytest.raises(ValueError, match="eşleşmiyor"):
        manager.confirm_live_arming("LIVE 1234é")  # not a TypeError (HTTP 500)


def test_live_probe_failure_text_is_not_reflected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = _manager(tmp_path, monkeypatch)

    class _FailingBroker:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def account(self) -> Any:
            raise RuntimeError("request to the LIVE endpoint failed with marker PKxyz-9")

        def close(self) -> None:
            pass

    monkeypatch.setattr(alpaca_module, "AlpacaBroker", _FailingBroker)

    account_id, error = manager._probe_live_account("AKlive123456", "AKlivesecret")

    assert account_id is None
    assert error is not None and "PKxyz-9" not in error


# --- S6-1 / I-1 / P7-7 settings-side: unknown mode fails closed -------------------------


def test_live_save_with_an_unknown_current_mode_still_needs_the_live_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, monkeypatch)  # config file missing: the current mode is unknown
    monkeypatch.setattr(manager, "get_live_credentials", lambda: (_KEY, _SECRET))
    payload = SettingsPayload(paper_mode=False, restart_engine=False)

    with pytest.raises(ValueError, match="yeni bir canlı onay"):
        manager._validated_settings_payload(payload)
