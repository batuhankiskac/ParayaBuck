"""Backtest ↔ engine mutual exclusion and race test.

Covers the critical rule from web/server.py:
* Web backtest cannot run while trading engine is active (starting/running/warming_up/degraded/halted/stopping/stop_failed)
* Trading engine cannot start while a Web backtest is running
* The check is atomic via backtest_manager.lock — no TOCTOU.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from parayabuck.web.managers.backtest import BacktestManager
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.state import WebState


def _wired_pair():
    mgr = EngineManager()
    bm = BacktestManager(engine_state_provider=mgr.current_engine_state)
    bm.bind_engine_state_provider(mgr.current_engine_state)
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

    release = threading.Event()

    def fake_long_run(argv):
        assert release.wait(timeout=5)
        bm._finish(None)

    bm._run = fake_long_run  # type: ignore
    try:
        bm.start({"strategy": "equity"})
        assert bm.is_running
        # Engine start must fail while backtest holds the lock/is_running
        with pytest.raises(ValueError, match="Web backtesti hâlâ çalışıyor"):
            mgr.start_engine()
        # Wait for backtest to finish, then engine can start (or at least not blocked by backtest)
        release.set()
        assert bm._thread is not None
        bm._thread.join(timeout=5)
        assert not bm._thread.is_alive()
        assert not bm.is_running
    finally:
        bm._run = original_run  # type: ignore


def test_engine_backtest_race_only_one_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Concurrent start_engine vs start_backtest: at most one succeeds (deterministic)."""
    # The test proves only the mutual-exclusion race.  Never let its engine
    # contender discover an operator dotenv/config path or create a runtime
    # database when it wins the race.
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("runtime:\n  mode: paper\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENV_PATH", str(tmp_path / ".env"))
    mgr, bm, _ = _wired_pair()
    monkeypatch.setattr(mgr, "get_credentials", lambda: (None, None))
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

    release = threading.Event()

    def fake_run(argv):
        assert release.wait(timeout=5)
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
    t1.join(timeout=5)
    t2.join(timeout=5)
    # A contender still running would leave its result unset, and the check
    # below would then pass vacuously, so both must have finished and reported.
    assert not t1.is_alive() and not t2.is_alive(), results
    assert results["engine"] is not None and results["backtest"] is not None, results
    # At most one should have succeeded
    successes = sum(1 for v in results.values() if v == "ok")
    assert successes <= 1, f"both won unexpectedly: {results}"
    # Clean up
    bm._run = orig_run  # type: ignore
    release.set()
    if bm._thread is not None:
        bm._thread.join(timeout=5)


def test_backtest_lock_is_rlock_and_reentrant():
    bm = BacktestManager()
    assert hasattr(bm.lock, "__enter__")
    # RLock can be acquired twice by same thread
    with bm.lock:
        with bm.lock:
            assert bm.lock._is_owned()  # type: ignore


@pytest.mark.parametrize(
    "provider",
    [lambda: "unknown", lambda: (_ for _ in ()).throw(RuntimeError("state probe failed"))],
)
def test_backtest_refuses_unverified_engine_state(provider):
    bm = BacktestManager(engine_state_provider=provider)
    with pytest.raises(ValueError, match="could not be verified"):
        bm.start({})
    assert bm.state == "idle"


def test_engine_start_uses_its_bound_backtest_guard(monkeypatch):
    local = BacktestManager(engine_state_provider=lambda: "stopped")
    mgr = EngineManager(backtest_manager=local)

    class AliveThread:
        def is_alive(self) -> bool:
            return True

    local._thread = AliveThread()
    local.state = "running"

    with pytest.raises(ValueError, match="Web backtesti hâlâ çalışıyor"):
        mgr.start_engine()
