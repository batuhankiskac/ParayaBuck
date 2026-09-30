"""Start-failure alerting: a failed engine start must never be silent.

Before this guard every start-failure path only logged. A permanent failure
left the engine in 'error' and a transient one retried forever, while
/healthz stayed 200 — so systemd's Restart=on-failure never fired, every
alarm that could report the failure runs inside a runtime that never
started, and the bot missed sessions until a human noticed.

These tests pin the operator-visible contract:
* every permanent failure path (missing credentials, an unimportable
  runtime, a blocked startup such as the tracked-secret scan) alerts;
* a manual transient failure alerts (no retry follows it);
* a retrying auto-start alerts once the retries stop looking transient, with
  the attempt count, and not on every attempt;
* a successful start never alerts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.monitoring.alerts as alerts
from parayabuck.runtime import TransientStartupError
from parayabuck.web.managers import engine as engine_module

_REAL_SLEEP = asyncio.sleep


@pytest.fixture(autouse=True)
def _alert_sink() -> Any:
    alerts.reset_for_tests()
    # Throttling is not the subject here: count every delivered alert.
    alerts.configure_alerting(
        enabled=True,
        webhook_url="",
        events=sorted(alerts.ALERT_KINDS),
        throttle_seconds=0.0,
    )
    yield
    alerts.reset_for_tests()


def _delivered(kind: str) -> int:
    delivered = alerts.alert_stats()["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


class _FailingRuntime:
    """Fake EngineRuntime that fails ``failures`` starts, then works."""

    def __init__(self, failures: int, *, transient: bool = True) -> None:
        self.failures = failures
        self.transient = transient
        self.broker = SimpleNamespace(account_id="acct")
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self.failures > 0:
            self.failures -= 1
            if self.transient:
                raise TransientStartupError("Alpaca API timeout")
            raise RuntimeError("Alpaca rejected the API key (401)")

    def stop(self) -> bool:
        return True

    def health(self) -> Any:
        return SimpleNamespace(state="running", healthy=True)


def _manager(monkeypatch: pytest.MonkeyPatch, flaky: _FailingRuntime) -> engine_module.EngineManager:
    monkeypatch.setattr("parayabuck.runtime.EngineRuntime", lambda **kwargs: flaky)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    manager = engine_module.EngineManager()
    manager.config_path = Path("config/settings.yaml")
    monkeypatch.setattr(manager, "get_credentials", lambda: ("key", "secret"))
    return manager


def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_delay: float) -> None:
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)


def test_successful_start_emits_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = _FailingRuntime(failures=0)
    manager = _manager(monkeypatch, flaky)

    manager.start_engine()

    assert manager.engine_state == "running"
    assert _delivered("runtime_start_failed") == 0


def test_permanent_start_failure_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-transient start error (rotated key, sqlite error, catch-up
    exception) leaves the engine in 'error'; the operator must be told."""
    flaky = _FailingRuntime(failures=1, transient=False)
    manager = _manager(monkeypatch, flaky)

    with pytest.raises(RuntimeError, match="401"):
        manager.start_engine(auto_start=True)

    assert manager.engine_state == "error"
    assert _delivered("runtime_start_failed") == 1


def test_auto_start_blocked_by_startup_guard_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tracked-secret scan refuses the start before any runtime exists;
    that refusal is a start failure the operator must see."""
    manager = _manager(monkeypatch, _FailingRuntime(failures=0))
    monkeypatch.setattr(
        engine_module,
        "_scan_tracked_secrets",
        lambda root: "Refusing to start: a tracked .env contains Alpaca credentials",
    )

    with pytest.raises(ValueError, match="tracked .env"):
        manager.start_engine(auto_start=True)

    assert manager.engine_state == "error"
    assert _delivered("runtime_start_failed") == 1


def test_missing_credentials_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch, _FailingRuntime(failures=0))
    monkeypatch.setattr(manager, "get_credentials", lambda: ("", ""))

    with pytest.raises(ValueError, match="key/secret missing"):
        manager.start_engine(auto_start=True)

    assert manager.engine_state == "error"
    assert _delivered("runtime_start_failed") == 1


def test_manual_transient_failure_alerts_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manual start has no retry behind it, so a transient failure is
    terminal and must alert immediately."""
    manager = _manager(monkeypatch, _FailingRuntime(failures=1))

    with pytest.raises(TransientStartupError):
        manager.start_engine()

    assert manager.engine_state == "error"
    assert _delivered("runtime_start_failed") == 1


def test_retrying_auto_start_alerts_only_past_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries are expected to clear a network blip: the alert waits until
    the retries stop looking transient, then repeats periodically with the
    attempt count instead of firing on every attempt."""
    flaky = _FailingRuntime(failures=5)
    manager = _manager(monkeypatch, flaky)
    _no_wait(monkeypatch)

    asyncio.run(manager.auto_start_engine())

    assert manager.engine_state == "running"
    assert flaky.start_calls == 6
    assert _delivered("runtime_start_failed") == 1


def test_long_retry_loop_repeats_the_alert_with_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = _FailingRuntime(failures=13)
    manager = _manager(monkeypatch, flaky)
    _no_wait(monkeypatch)

    asyncio.run(manager.auto_start_engine())

    assert flaky.start_calls == 14
    # Attempt 3 (first alerting attempt) and attempt 13 (next repeat).
    assert _delivered("runtime_start_failed") == 2
