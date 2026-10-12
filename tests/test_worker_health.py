from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck import runtime as runtime_module
from parayabuck.runtime import EngineRuntime, WorkerHealth

NOW = datetime(2026, 9, 26, 5, 30, tzinfo=UTC)


def _healthy(name: str, heartbeat_age: timedelta) -> bool:
    runtime = EngineRuntime.__new__(EngineRuntime)
    workers = {name: WorkerHealth(NOW - heartbeat_age, alive=True)}
    return runtime._is_worker_healthy(workers, name, NOW, 45.0)


def test_signal_adjustment_monitor_is_healthy_between_hourly_checks() -> None:
    assert _healthy("pb-signal-adjustment", timedelta(minutes=10))
    assert _healthy("pb-signal-adjustment", timedelta(seconds=runtime_module._SIGNAL_ADJUSTMENT_CHECK_SECONDS + 60))


def test_signal_adjustment_monitor_missing_a_check_is_unhealthy() -> None:
    assert not _healthy("pb-signal-adjustment", timedelta(seconds=runtime_module._SIGNAL_ADJUSTMENT_CHECK_SECONDS * 2))


def test_polling_workers_keep_the_short_heartbeat_ttl() -> None:
    assert _healthy("pb-account", timedelta(seconds=30))
    assert not _healthy("pb-account", timedelta(minutes=10))
