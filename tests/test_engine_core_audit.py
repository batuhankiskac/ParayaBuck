"""Regression tests for the 2026-10-06 engine-core audit fixes (EngineRuntime)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from parayabuck.core.models import AccountSnapshot
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth


def _account(equity: float = 100_000.0) -> AccountSnapshot:
    return AccountSnapshot(cash=equity, equity=equity, peak_equity=equity, daily_pnl=0.0)


def test_account_loop_refreshes_readiness_once_startup_committed() -> None:
    """The decision loop only wakes for the daily bar, so the periodic
    readiness refresh (warming_up -> running, warmup retry) rides the account
    poll instead of the deleted 5m pipeline."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    runtime._workers = {"pb-account": WorkerHealth(now, now)}
    runtime._running = True
    runtime._startup_committed = True
    runtime._warming_up = False
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime.broker = SimpleNamespace(account=_account, account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(update_high_water=lambda *_a: None)
    runtime._persist_risk_state = lambda _account: None
    runtime._maybe_run_intent_watchdog = lambda: None
    refreshed: list[bool] = []
    runtime._refresh_readiness = lambda: refreshed.append(True)

    def stop_after_one_poll(_seconds: float) -> bool:
        runtime._running = False
        return True

    runtime._stop_event = SimpleNamespace(wait=stop_after_one_poll)

    runtime._account_loop()

    assert refreshed == [True]


def test_account_loop_skips_readiness_before_startup_commits() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    runtime._workers = {"pb-account": WorkerHealth(now, now)}
    runtime._running = True
    runtime._startup_committed = False
    runtime._warming_up = False
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime.broker = SimpleNamespace(account=_account, account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(update_high_water=lambda *_a: None)
    runtime._persist_risk_state = lambda _account: None
    runtime._maybe_run_intent_watchdog = lambda: None
    refreshed: list[bool] = []
    runtime._refresh_readiness = lambda: refreshed.append(True)
    runtime._stop_event = SimpleNamespace(wait=lambda _s: setattr(runtime, "_running", False) or True)
    runtime._state_lock = threading.RLock()

    runtime._account_loop()

    assert refreshed == []
    assert runtime._workers["pb-account"].alive is False
