"""State left behind by the previous build must not decide the new engine.

Two leftovers of the deleted crypto build are migrated deterministically at
startup instead of halting the engine or pinning ``ops status`` forever:

* a halt row whose condition was in-process only (a crashed decision worker, a
  trade-update callback that raised, an event queue that overflowed) — this
  start re-arms all three, so the row is cleared with an audit record;
* a rebalance execution of a market the equity runtime no longer executes —
  nothing advances it, so it is not a stuck rebalance.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import parayabuck.runtime as runtime_module
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring.forensics import find_stuck_rebalances
from parayabuck.monitoring.health import HealthStatus, _check_execution
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.runtime import EngineRuntime
from parayabuck.storage import SQLiteStore

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def _restarting_runtime(ledger: ExecutionLedger) -> EngineRuntime:
    """The attributes ``_restore_persistent_state`` reads, and nothing else."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = ledger
    runtime._risk_state_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._consecutive_losses = 0
    runtime._circuit_breaker_reason = None
    runtime._entry_risk_latch = {Market.EQUITY: None}
    return runtime


def test_leftover_worker_halt_row_is_migrated_not_restored(tmp_path: Path, monkeypatch) -> None:
    emitted: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        runtime_module.alerts,
        "emit",
        lambda kind, *, message, **fields: emitted.append((kind, message, fields)),
    )
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "trade_update_callback_failure")

    _restarting_runtime(ledger)._restore_persistent_state()

    assert ledger.load_safety_incidents("acct") == ()
    assert [kind for kind, _message, _fields in emitted] == ["circuit_breaker"]
    assert emitted[0][2]["reasons"] == ["trade_update_callback_failure"]
    ledger.close()


def test_halt_that_still_needs_verification_survives_the_migration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_args, **_kwargs: None)
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "trade_update_callback_failure")
    ledger.save_safety_incident("acct", "ownership_drift")
    runtime = _restarting_runtime(ledger)

    runtime._restore_persistent_state()

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert [incident.reason for incident in ledger.load_safety_incidents("acct")] == ["ownership_drift"]
    ledger.close()


def test_operator_reset_also_clears_a_leftover_worker_halt_row(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "trade_update_callback_failure")
    ledger.save_safety_incident("acct", "protection_failure")
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime.market_state = SimpleNamespace()
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    runtime.ledger = ledger
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None}

    runtime.reset_safety_lock()

    assert ledger.load_safety_incidents("acct") == ()
    ledger.close()


def _rebalance(store: SQLiteStore, session: str, decision_id: str, market: str, phase: str) -> None:
    """A rebalance execution that has been in ``phase`` for two days."""
    stale = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    with store.connection as connection:
        connection.execute(
            "INSERT INTO portfolio_decisions (decision_id, session_id, account_id, configuration_digest, "
            "decision_timestamp, market, strategy_name, target_json, risk_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, session, "acct", "digest", stale, market, "test", "{}", "{}"),
        )
        connection.execute(
            "INSERT INTO portfolio_rebalance_executions "
            "(decision_id, account_id, market, phase, status, buy_orders_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (decision_id, "acct", market, phase, "active", "[]", stale),
        )


def test_stuck_rebalances_ignore_legacy_crypto_rows(tmp_path: Path) -> None:
    db = str(tmp_path / "runtime.sqlite3")
    store = SQLiteStore(db)
    session = PortfolioStateRepository(db, store=store).begin_session("acct", "digest", "paper")
    _rebalance(store, session, "crypto-legacy", "crypto", "SELL_PENDING")
    _rebalance(store, session, "equity-legacy", "equity", "BUY_PENDING")
    _rebalance(store, session, "equity-done", "equity", "COMPLETE")

    stuck = find_stuck_rebalances(db, threshold_seconds=3600)

    # A crypto row is never advanced by the equity runtime, so it is not a
    # stuck rebalance; the equity one is still reported and still DEGRADED.
    assert [row["decision_id"] for row in stuck] == ["equity-legacy"]
    assert _check_execution(db).status is HealthStatus.DEGRADED


def test_only_a_legacy_crypto_rebalance_row_keeps_execution_ok(tmp_path: Path) -> None:
    db = str(tmp_path / "runtime.sqlite3")
    store = SQLiteStore(db)
    session = PortfolioStateRepository(db, store=store).begin_session("acct", "digest", "paper")
    _rebalance(store, session, "crypto-legacy", "crypto", "BUY_PENDING")

    # History, not a live stall: ops status must not report DEGRADED for good.
    assert find_stuck_rebalances(db, threshold_seconds=3600) == []
    assert _check_execution(db).status is HealthStatus.OK
