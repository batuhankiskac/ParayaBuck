from __future__ import annotations

import threading

import pytest

from parayabuck.runtime import EngineRuntime


def _runtime(**attrs: object) -> EngineRuntime:
    runtime = object.__new__(EngineRuntime)
    runtime.strategy_status = "research_only_no_approved_portfolio_strategy"
    runtime._running = False  # type: ignore[attr-defined]
    runtime._state = "stopped"  # type: ignore[attr-defined]
    runtime._stop_event = threading.Event()  # type: ignore[attr-defined]
    for name, value in attrs.items():
        setattr(runtime, name, value)
    return runtime


def test_runtime_start_refused_without_approved_strategy() -> None:
    runtime = _runtime()
    with pytest.raises(RuntimeError, match="no approved portfolio strategy"):
        runtime.start()


def test_runtime_start_guard_uses_status_not_just_state() -> None:
    # The status field alone must never be a guard: start() must refuse.
    runtime = _runtime(strategy_status="research_only_no_approved_portfolio_strategy")
    with pytest.raises(RuntimeError, match="research_only"):
        runtime.start()


def test_runtime_start_allowed_when_strategy_approved() -> None:
    class FakeBroker:
        def reconcile(self) -> None:
            raise RuntimeError("reached broker reconcile (guard passed)")

    runtime = _runtime(
        strategy_status="approved:equity/shadow",
        approval_manifests=(object(),),
        broker=FakeBroker(),
    )
    with pytest.raises(RuntimeError, match="reached broker reconcile"):
        runtime.start()
