from __future__ import annotations

import threading

import pytest

from parayabuck.config import Config, LiveConfig, RuntimeConfig, RuntimeMode
from parayabuck.runtime import EngineRuntime


def _runtime(**attrs: object) -> EngineRuntime:
    runtime = object.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.PAPER  # type: ignore[attr-defined]
    runtime._running = False  # type: ignore[attr-defined]
    runtime._state = "stopped"  # type: ignore[attr-defined]
    runtime._stop_event = threading.Event()  # type: ignore[attr-defined]
    for name, value in attrs.items():
        setattr(runtime, name, value)
    return runtime


def test_runtime_start_refused_when_disabled() -> None:
    runtime = _runtime(mode=RuntimeMode.DISABLED)
    with pytest.raises(RuntimeError, match="runtime.mode is disabled"):
        runtime.start()


def test_paper_runtime_needs_no_research_approval() -> None:
    class FakeBroker:
        def reconcile(self) -> None:
            raise RuntimeError("reached broker reconcile")

    runtime = _runtime(broker=FakeBroker())
    with pytest.raises(RuntimeError, match="reached broker reconcile"):
        runtime.start()


def test_live_runtime_requires_environment_confirmation(monkeypatch) -> None:
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE", raising=False)
    config = Config(
        runtime=RuntimeConfig(mode=RuntimeMode.LIVE),
        live=LiveConfig(enabled=True, hard_dollar_cap=1_000, operator_approval_path="approval"),
    )
    runtime = _runtime(mode=RuntimeMode.LIVE, app_config=config)
    with pytest.raises(RuntimeError, match="PARAYABUCK_ENABLE_LIVE=YES"):
        runtime.start()


def test_live_runtime_start_requires_live_enabled() -> None:
    runtime = _runtime(mode=RuntimeMode.LIVE, app_config=Config())
    with pytest.raises(RuntimeError, match="live.enabled=true"):
        runtime.start()


def test_disabled_runtime_is_rejected_before_universe_or_broker(monkeypatch) -> None:
    called = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("parayabuck.runtime.ensure_active_universes", forbidden)
    with pytest.raises(RuntimeError, match="does not support runtime.mode=disabled"):
        EngineRuntime("key", "secret", Config(runtime=RuntimeConfig(mode=RuntimeMode.DISABLED)))
    assert not called
