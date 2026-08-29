"""Backtest ↔ engine mutual exclusion and race test.

Covers the critical rule from web/server.py:
* Web backtest cannot run while trading engine is active (starting/running/warming_up/degraded/halted/stopping/stop_failed)
* Trading engine cannot start while a Web backtest is running
* The check is atomic via backtest_manager.lock — no TOCTOU.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.state import WebState


def _wired_pair():
    mgr = EngineManager()
    bm = BacktestManager(engine_state_provider=mgr.current_engine_state)
    bm.bind_engine_state_provider(mgr.current_engine_state)
    # also bind the global that EngineManager.start_engine checks
    import parayabuck.web.managers.engine as eng_mod

    eng_mod.backtest_manager = bm
    state = WebState(engine_manager=mgr, backtest_manager=bm)
    state.bind()
    return mgr, bm, state


def test_backtest_rejected_while_engine_active():
    _, bm, _ = _wired_pair()

    # fake engine as running by patching current_engine_state
    def fake_running():
        return "running"

    bm.bind_engine_state_provider(fake_running)
    with pytest.raises(ValueError, match="trading engine is active"):
        bm.start({"strategy": "equity"})


def test_engine_rejected_while_backtest_running():
    mgr, bm, _ = _wired_pair()
    # Start a long-running backtest in background thread (stub the actual _run)
    original_run = bm._run

    def fake_long_run(argv):
        time.sleep(0.5)
        bm._finish(None)

    bm._run = fake_long_run  # type: ignore
    try:
        bm.start({"strategy": "equity"})
        assert bm.is_running
        # Engine start must fail while backtest holds the lock/is_running
        with pytest.raises(ValueError, match="Web backtest is still running"):
            mgr.start_engine()
        # Wait for backtest to finish, then engine can start (or at least not blocked by backtest)
        deadline = time.time() + 2.0
        while bm.is_running and time.time() < deadline:
            time.sleep(0.05)
        assert not bm.is_running
    finally:
        bm._run = original_run  # type: ignore
        # cleanup global
        import parayabuck.web.managers.engine as eng_mod

        eng_mod.backtest_manager = bm


def test_engine_backtest_race_only_one_wins():
    """Concurrent start_engine vs start_backtest: at most one succeeds (deterministic)."""
    mgr, bm, _ = _wired_pair()
    # Stub both to be slow and report success
    results: dict[str, Any] = {"engine": None, "backtest": None}

    def engine_start():
        try:
            mgr.start_engine()
            results["engine"] = "ok"
        except Exception as e:
            results["engine"] = str(e)

    # Make backtest's _run long so is_running stays True during race
    orig_run = bm._run

    def fake_run(argv):
        time.sleep(0.4)
        bm._finish(None)

    bm._run = fake_run  # type: ignore

    def backtest_start():
        try:
            bm.start({"strategy": "equity"})
            results["backtest"] = "ok"
        except Exception as e:
            results["backtest"] = str(e)

    # Patch engine to not actually start runtime (avoid side effects) — just check exclusion
    # EngineManager.start_engine already checks backtest_manager.is_running before touching runtime
    # So we don't need to mock runtime; the exclusion happens early.
    t1 = threading.Thread(target=engine_start)
    t2 = threading.Thread(target=backtest_start)
    t1.start()
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)
    # At most one should have succeeded
    successes = sum(1 for v in results.values() if v == "ok")
    assert successes <= 1, f"both won unexpectedly: {results}"
    # At least one should have run (or both failed due to engine already active etc.)
    # The point is no corruption and exclusion is enforced — if backtest won, engine is blocked
    # Clean up
    bm._run = orig_run  # type: ignore
    deadline = time.time() + 1.0
    while bm.is_running and time.time() < deadline:
        time.sleep(0.05)


def test_backtest_lock_is_rlock_and_reentrant():
    bm = BacktestManager()
    assert hasattr(bm.lock, "__enter__")
    # RLock can be acquired twice by same thread
    with bm.lock:
        with bm.lock:
            assert bm.lock._is_owned()  # type: ignore
