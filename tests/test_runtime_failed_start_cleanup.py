"""Cleanup after a runtime start that aborts at startup reconciliation.

Production (2026-09-28): startup reconciliation found drift, and the
failed-start cleanup then called stop() on alpaca-py stream objects whose
run() never executed (their event loop is created only inside run()), so
the cleanup logged ``stream:AttributeError`` and left the runtime
``stop_failed``. Offline: fake broker, real SDK stream adapters (constructed
only, never connected), tmp_path SQLite.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import (
    Config,
    CryptoUniverseConfig,
    EngineConfig,
    EquityUniverseConfig,
    RuntimeConfig,
    StrategyConfig,
    UniverseConfig,
)
from parayabuck.core.models import AccountSnapshot
from parayabuck.execution.alpaca_reconciliation import (
    RECONCILIATION_CATEGORIES,
    RECONCILIATION_DRIFT_CATEGORIES,
    ReconciliationResultError,
)
from parayabuck.runtime import EngineRuntime


class _FakeBroker:
    account_id = "acct"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.drift = True
        self.closed = 0

    def reconcile(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {category: [] for category in RECONCILIATION_CATEGORIES}
        if self.drift:
            result[sorted(RECONCILIATION_DRIFT_CATEGORIES)[0]] = ["SPY"]
        return result

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(cash=1_000.0, equity=1_000.0, peak_equity=1_000.0, daily_pnl=0.0, positions=())

    def portfolio_open_order_symbols(self) -> set[str]:
        return set()

    def close(self) -> None:
        self.closed += 1


class _BlockingStream:
    """Stands in for a connected socket: run() blocks until stop()."""

    def __init__(self) -> None:
        self._stopped = threading.Event()

    def subscribe(self, *_args: object, **_kwargs: object) -> None:
        pass

    def run(self) -> None:
        self._stopped.wait(5)

    run_stock = run
    run_crypto = run

    def stop(self) -> None:
        self._stopped.set()


def _config(tmp_path: Path) -> Config:
    return Config(
        universe=UniverseConfig(
            equity=EquityUniverseConfig(symbols=("SPY",)),
            crypto=CryptoUniverseConfig(enabled=False),
        ),
        engine=EngineConfig(),
        runtime=RuntimeConfig(decision_db_path=str(tmp_path / "runtime.sqlite3")),
        strategy=StrategyConfig(crypto=replace(StrategyConfig().crypto, enabled=False)),
    )


def _runtime(tmp_path: Path) -> EngineRuntime:
    runtime = EngineRuntime("key", "secret", _config(tmp_path))
    runtime._snapshot_writer = None
    return runtime


def test_start_aborted_by_reconcile_drift_cleans_up_and_later_start_succeeds(
    tmp_path: Path, monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runtime_module, "AlpacaBroker", _FakeBroker)
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    caplog.set_level(logging.INFO, logger="parayabuck.runtime")

    failed = _runtime(tmp_path / "failed")
    with pytest.raises(ReconciliationResultError, match="Startup reconciliation found drift"):
        failed.start()
    # The web manager stops the discarded runtime once more after start().
    assert failed.stop() is True

    assert "Runtime cleanup did not complete" not in caplog.text
    assert "Runtime startup cleanup failed" not in caplog.text
    assert failed._state == "halted"
    assert failed.broker.closed == 2

    # A later start in the same process (fresh runtime, as the web auto-start
    # retry builds) still succeeds once reconciliation is clean.
    retried = _runtime(tmp_path / "retried")
    retried.broker.drift = False
    retried.stream = _BlockingStream()
    retried.trading_updates = _BlockingStream()
    for name in ("_warmup", "_resume_startup_rebalances", "_run_startup_catch_up", "_refresh_readiness"):
        monkeypatch.setattr(retried, name, lambda **_kwargs: None)
    monkeypatch.setattr(retried, "_decision_loop", retried._stop_event.wait)
    monkeypatch.setattr(retried, "_account_loop", retried._stop_event.wait)
    caplog.clear()

    retried.start()
    assert retried._running is True
    assert retried._state in {"running", "warming_up"}
    assert retried.stop() is True
    assert "Runtime cleanup did not complete" not in caplog.text
