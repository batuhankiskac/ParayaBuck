"""Fail-closed invariants of the runtime's halt, latch and incident paths.

Each test pins one consumer-visible safety property of
``EngineRuntime``'s fail-closed machinery:

* ``_market_entry_halts`` is only mutated with the state lock held, so a
  halt raised by one thread cannot be erased by another;
* provider-authority adoption of the signal history is serialized against
  ``process_closed_bars``, which appends to the same list objects;
* a trade-update callback failure pages like every other circuit breaker;
* a local database failure is classified identically at both fail-closed
  call sites, whatever the driver's message says.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck import runtime as runtime_module
from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring.incident import IncidentCategory
from parayabuck.runtime import EngineRuntime

# A clean broker reconciliation: every category empty, no drift.
_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}

# --- V1: market entry halts are state-lock serialised ---


class _TrackedRLock:
    """A real RLock that also reports whether the caller holds it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.depth = 0
        self.acquisitions = 0

    def __enter__(self) -> _TrackedRLock:
        self._lock.acquire()
        self.depth += 1
        self.acquisitions += 1
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.depth -= 1
        self._lock.release()
        return False

    @property
    def held(self) -> bool:
        return self.depth > 0


class _LockCheckedHalts(dict):  # type: ignore[type-arg]
    """``_market_entry_halts`` that refuses access outside the state lock.

    The dict is mutated from the stream workers, the rebalance advance path
    and the operator reset. An unguarded
    read-modify-write silently loses a halt, so the container itself
    enforces the invariant instead of the test trusting a comment.
    """

    def __init__(self, *args: object, lock: _TrackedRLock, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._lock = lock

    def _assert_locked(self) -> None:
        assert self._lock.held, "_market_entry_halts mutated without _state_lock held"

    def __getitem__(self, key: object) -> object:
        self._assert_locked()
        return super().__getitem__(key)

    def __setitem__(self, key: object, value: object) -> None:
        self._assert_locked()
        super().__setitem__(key, value)

    def get(self, key: object, default: object = None) -> object:
        self._assert_locked()
        return super().get(key, default)

    def items(self) -> Any:
        self._assert_locked()
        return super().items()


def _halt_runtime() -> tuple[EngineRuntime, _TrackedRLock]:
    runtime = EngineRuntime.__new__(EngineRuntime)
    state_lock = _TrackedRLock()
    runtime._state_lock = state_lock  # type: ignore[assignment]
    runtime._event_stats_lock = threading.Lock()
    runtime._market_entry_halts = _LockCheckedHalts(  # type: ignore[assignment]
        {Market.EQUITY: None},
        lock=state_lock,
    )
    return runtime, state_lock


def _set_halt(runtime: EngineRuntime, market: Market, reason: str | None) -> None:
    """Seed a halt the way production writers do: under the state lock."""
    with runtime._state_lock:  # type: ignore[attr-defined]
        runtime._market_entry_halts[market] = reason  # type: ignore[index]


def _get_halt(runtime: EngineRuntime, market: Market) -> str | None:
    with runtime._state_lock:  # type: ignore[attr-defined]
        return runtime._market_entry_halts.get(market)  # type: ignore[union-attr]


def test_verified_stream_reconnect_clears_only_its_own_halt_under_the_state_lock() -> None:
    """The check-then-clear in ``_mark_market_stream_recovered`` is atomic:
    a halt raised by another thread between the read and the write can no
    longer be erased by the recovery."""
    runtime, _ = _halt_runtime()
    now = datetime.now(UTC)
    runtime._workers = {"pb-stocks": runtime_module.WorkerHealth(started_at=now, last_heartbeat=now)}
    runtime._workers["pb-stocks"].last_error = "stream closed"
    runtime._workers["pb-stocks"].restart_count = 6
    _set_halt(runtime, Market.EQUITY, "rebalance_reconciliation_required")

    runtime._mark_market_stream_recovered(Market.EQUITY)

    # Another writer's halt is not the stream worker's to clear.
    assert _get_halt(runtime, Market.EQUITY) == "rebalance_reconciliation_required"

    runtime._workers["pb-stocks"].last_error = "stream closed"
    _set_halt(runtime, Market.EQUITY, "pb-stocks_restart_limit")

    runtime._mark_market_stream_recovered(Market.EQUITY)

    assert _get_halt(runtime, Market.EQUITY) is None


def test_operator_reset_verifies_market_halts_under_the_state_lock() -> None:
    """``reset_safety_lock`` clears a halt only through its verified
    recovery path, and every dict access in that path is state-locked so a
    concurrent halt raise is never overwritten by the verification."""
    runtime, _ = _halt_runtime()
    _set_halt(runtime, Market.EQUITY, "rebalance_reconciliation_required")
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._risk_state_lock = threading.Lock()
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account_id: (),
        clear_safety_incident=lambda *_args, **_kwargs: None,
    )
    runtime._persist_risk_state = lambda _account: None
    reconciled: list[Market] = []
    runtime._rebalance_state_reconciled = lambda market: reconciled.append(market) or True
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )

    runtime.reset_safety_lock()

    assert reconciled == [Market.EQUITY]
    assert _get_halt(runtime, Market.EQUITY) is None


# --- V4: provider-authority adoption is serialized against bar appends ---


def test_signal_history_adoption_runs_under_the_portfolio_lock() -> None:
    """``adopt_authoritative_signal_history`` REPLACES the per-symbol list
    object. It must run inside the portfolio lock that
    ``process_closed_bars`` holds while appending, otherwise the completed
    bar is appended to an orphaned list and silently lost."""
    portfolio_lock = _TrackedRLock()
    held_during_adoption: list[bool] = []

    def adopt(_provider: dict[str, list[Bar]], _watermarks: dict[str, datetime]) -> int:
        held_during_adoption.append(portfolio_lock.held)
        return 1

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._portfolio_lock = portfolio_lock  # type: ignore[assignment]
    runtime._signal_verify_dedup = None
    runtime._record_signal_adjustment_success = lambda _watermarks: None
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            history={"AAA": []},
            symbols=("AAA",),
            strategy=SimpleNamespace(minimum_history_bars=1),
            adopt_authoritative_signal_history=adopt,
        ),
    )
    runtime._symbols_by_market = {Market.EQUITY: {"AAA"}}
    now = datetime.now(UTC)
    provider = {
        "AAA": [
            Bar(now - timedelta(days=offset), "AAA", "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.EQUITY)
            for offset in range(9, -1, -1)
        ]
    }
    runtime._historical = SimpleNamespace(get_bars_batch=lambda *_a, **_k: provider)

    assert runtime.verify_equity_signal_basis() is True
    assert held_during_adoption == [True]


# --- V5: a trade-update callback failure is a paged circuit breaker ---


def test_trade_update_callback_failure_emits_circuit_breaker(monkeypatch: Any) -> None:
    """Every sibling circuit-breaker path records a metric and emits an
    alert; the trade-update halt must too, or the halt is visible only in
    the dashboard."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._state_lock = threading.RLock()
    runtime._state = "running"
    runtime._circuit_breaker_reason = None

    def explode(_update: object) -> None:
        raise RuntimeError("ledger write failed")

    runtime._handle_trade_update = explode  # type: ignore[assignment]
    emitted: list[tuple[str, dict[str, object]]] = []
    recorded: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **kw: emitted.append((kind, kw)))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", recorded.append)
    update = SimpleNamespace(client_order_id="coid-1")

    asyncio.run(runtime._on_trade_update(update))  # type: ignore[arg-type]

    assert runtime._state == "halted"
    assert runtime._circuit_breaker_reason == "trade_update_callback_failure"
    assert recorded == ["circuit_breaker"]
    assert [kind for kind, _ in emitted] == ["circuit_breaker"]
    assert emitted[0][1]["reason"] == "trade_update_callback_failure"


# --- V6: one database-failure classification at both fail-closed sites ---


def test_account_fetch_classifies_a_sqlite_integrity_error_as_a_database_error(
    monkeypatch: Any,
) -> None:
    """``sqlite3.IntegrityError`` reads as "UNIQUE constraint failed" — no
    "database"/"locked"/"sqlite" token anywhere. A message scan alone routes
    it to BROKER_ACCOUNT_STALE instead of the fail-closed DB incident."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._account_snapshot_lock = threading.Lock()
    runtime._last_account_snapshot = None
    runtime._last_account_snapshot_at = None
    runtime._account_snapshot_generation = 0
    runtime.config = SimpleNamespace(maximum_account_age_for_entry_seconds=300)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        account=lambda: (_ for _ in ()).throw(sqlite3.IntegrityError("UNIQUE constraint failed: orders.client")),
    )
    categories: list[IncidentCategory] = []
    monkeypatch.setattr(
        runtime_module,
        "automate_incident_recovery",
        lambda category, **_kw: categories.append(category),
    )

    try:
        runtime._fresh_account_snapshot()
    except sqlite3.IntegrityError:
        pass
    else:  # pragma: no cover - the broker must propagate
        raise AssertionError("expected the broker error to propagate")

    assert categories == [IncidentCategory.DATABASE_ERROR]


def test_portfolio_failure_classifies_the_same_sqlite_error_identically(monkeypatch: Any) -> None:
    """The two fail-closed call sites used different token sets, so the same
    driver error took a different recovery path depending on where it
    surfaced. Both must now fail closed as a database error."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._last_portfolio_status = {}
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: {})
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime._fresh_account_snapshot = lambda: account  # type: ignore[assignment]

    def explode(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed: portfolio_orders.decision_id")

    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=("AAA",),
        strategy=SimpleNamespace(timeframe="1d", name="equity_test"),
        process_closed_bars=explode,
    )
    runtime._portfolio_runtimes = (portfolio_runtime,)
    categories: list[IncidentCategory] = []
    monkeypatch.setattr(
        runtime_module,
        "automate_incident_recovery",
        lambda category, **_kw: categories.append(category),
    )
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda _kind, **_kw: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _event: None)

    runtime._process_portfolio_timestamp(
        [Bar(datetime(2025, 1, 2, tzinfo=UTC), "AAA", "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.EQUITY)]
    )

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert categories == [IncidentCategory.DATABASE_ERROR]


def test_portfolio_provider_timeout_records_incident_without_a_doomed_retry(monkeypatch: Any) -> None:
    """The halted fail-closed path must not burn the provider retry backoff on
    the engine thread re-raising an exception that can never succeed: the
    provider incident is recorded at once with no retry action."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._warming_up = False
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False
    runtime._portfolio_lock = threading.RLock()
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._last_portfolio_status = {}
    runtime._entry_readiness_reasons = lambda _market: ()
    runtime._equity_signal_basis_latch = lambda _timestamp: None
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: {})
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime._fresh_account_snapshot = lambda: account  # type: ignore[assignment]

    def time_out(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("provider timeout")

    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("AAA",),
            strategy=SimpleNamespace(timeframe="1d", name="equity_test"),
            process_closed_bars=time_out,
        ),
    )
    calls: list[tuple[IncidentCategory, dict[str, Any]]] = []
    monkeypatch.setattr(
        runtime_module,
        "automate_incident_recovery",
        lambda category, **kw: calls.append((category, kw)),
    )
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda _kind, **_kw: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _event: None)

    runtime._process_portfolio_timestamp(
        [Bar(datetime(2025, 1, 2, tzinfo=UTC), "AAA", "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.EQUITY)]
    )

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert [category for category, _kw in calls] == [IncidentCategory.PROVIDER_UNAVAILABLE]
    assert "provider_action" not in calls[0][1]


# --- V17: warmup retries back off instead of running every 10 minutes ---


def _daily_bars(symbol: str) -> list[Bar]:
    return [
        Bar(datetime(2025, 1, day, tzinfo=UTC), symbol, "1d", 1.0, 1.0, 1.0, 1.0, 1.0, Market.EQUITY)
        for day in range(1, 6)
    ]


def _readiness_runtime(ready: bool) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._state = "warming_up"
    runtime._last_warmup_retry_at = time.monotonic()
    runtime._warmup_retry_attempt = 0
    runtime._warmup_retry_symbols = frozenset()
    runtime._not_ready_symbols = {"AAA"}
    # No historical client: the retry thread returns immediately instead of
    # fetching, so the scheduling cadence is the only thing under test.
    runtime._historical = None
    history = {"AAA": _daily_bars("AAA"), "SPY": _daily_bars("SPY")} if ready else {"AAA": [], "SPY": []}
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=("AAA",),
            strategy=SimpleNamespace(minimum_history_bars=5),
            history=dict(history),
        ),
    )
    return runtime


def _elapse(runtime: EngineRuntime, seconds: float) -> None:
    """Rewind the last-retry clock and let the readiness gate decide."""
    runtime._last_warmup_retry_at = time.monotonic() - seconds
    runtime._refresh_readiness()
    thread = getattr(runtime, "_warmup_retry_thread", None)
    if thread is not None:
        thread.join(timeout=5)


def test_warmup_retry_delay_doubles_per_consecutive_attempt() -> None:
    """A symbol whose last bar permanently lags its benchmark's must not
    drive a provider round-trip every 10 minutes for the life of the
    process: each consecutive attempt doubles the floor, up to a 30-minute
    ceiling so a recovered provider makes the engine ready the same session."""
    runtime = _readiness_runtime(ready=False)

    _elapse(runtime, 600)
    assert runtime._warmup_retry_attempt == 1
    assert runtime._warmup_retry_symbols == frozenset({"AAA"})

    _elapse(runtime, 599)  # the next floor is 1200s, so 599s is too early
    assert runtime._warmup_retry_attempt == 1
    _elapse(runtime, 1200)
    assert runtime._warmup_retry_attempt == 2

    _elapse(runtime, 1799)  # 2400s would be next, but the ceiling is 1800s
    assert runtime._warmup_retry_attempt == 2
    _elapse(runtime, 1800)
    assert runtime._warmup_retry_attempt == 3
    _elapse(runtime, 1800)
    assert runtime._warmup_retry_attempt == 4


def test_warmup_retry_backoff_resets_once_a_symbol_recovers() -> None:
    """Progress (the pending set empties) resets the backoff so a later
    regression is retried promptly instead of after hours."""
    runtime = _readiness_runtime(ready=False)

    for _ in range(3):
        _elapse(runtime, 100_000)
    assert runtime._warmup_retry_attempt == 3

    runtime._portfolio_runtimes[0].history = {"AAA": _daily_bars("AAA"), "SPY": _daily_bars("SPY")}
    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == set()
    assert runtime._warmup_retry_attempt == 0
    assert runtime._state == "running"


def test_warmup_retry_backoff_resets_when_a_new_symbol_goes_pending() -> None:
    """The counter tracks the pending SET, not the process: a symbol that
    has never been retried must be retried at the 600s floor even while a
    chronically lagging one has already saturated the backoff."""
    runtime = _readiness_runtime(ready=False)

    for _ in range(3):
        _elapse(runtime, 100_000)
    assert runtime._warmup_retry_attempt == 3
    assert runtime._warmup_retry_symbols == frozenset({"AAA"})

    # BBB joins with a history that never catches up to the benchmark.
    runtime._portfolio_runtimes[0].symbols = ("AAA", "BBB")
    runtime._portfolio_runtimes[0].history["BBB"] = _daily_bars("BBB")[:-1]

    _elapse(runtime, 600)

    assert runtime._warmup_retry_attempt == 1
    assert runtime._warmup_retry_symbols == frozenset({"AAA", "BBB"})


# --- V2: the restored reasons are exactly the operator-resettable ones ---


def _ledger_shell(
    ledger: ExecutionLedger,
    runtime_class: type[EngineRuntime] = EngineRuntime,
) -> EngineRuntime:
    """A minimal runtime wired to a real ledger (the persist/restore paths
    under test only touch the attributes set here)."""
    runtime = runtime_class.__new__(runtime_class)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = ledger
    runtime.app_config = Config()
    runtime._risk_state_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._portfolio_runtimes = ()
    return runtime


@pytest.mark.parametrize(
    "reason",
    ["portfolio_runtime_failure", "ownership_drift", "protection_failure", "post_fill_risk_violation"],
)
def test_assigned_circuit_breaker_reasons_survive_a_restart(tmp_path: Path, reason: str) -> None:
    """A failure-path reason the restore allow-list keeps. If the allow-list
    omits one, the incident row exists but a restart silently resumes
    trading."""
    ledger = ExecutionLedger(str(tmp_path / f"{reason}.sqlite3"))
    ledger.save_safety_incident("acct", reason)

    runtime = _ledger_shell(ledger)
    runtime._restore_persistent_state()

    assert runtime._circuit_breaker_reason == reason
    ledger.close()


@pytest.mark.parametrize("reason", sorted(runtime_module._RESTART_REARMED_REASONS))
def test_rearmed_worker_halt_rows_are_migrated_not_restored(tmp_path: Path, reason: str) -> None:
    """A process-local halt row describes worker or queue state this start
    re-arms, and only a repeat failure can recreate — which halts again by
    itself. Restoring it would halt a healthy engine over a condition
    nothing can verify any more, so the row is migrated with an audit
    record instead of restored."""
    ledger = ExecutionLedger(str(tmp_path / f"{reason}.sqlite3"))
    ledger.save_safety_incident("acct", reason)

    runtime = _ledger_shell(ledger)
    runtime._restore_persistent_state()

    assert runtime._circuit_breaker_reason is None
    assert ledger.load_safety_incidents("acct") == ()
    ledger.close()


@pytest.mark.parametrize(
    "reason",
    ["portfolio_runtime_failure", "decision_worker_crashed", "trade_update_callback_failure"],
)
def test_assigned_circuit_breaker_reasons_are_operator_resettable(tmp_path: Path, reason: str) -> None:
    """A reason that is persisted but absent from the recoverable set leaves
    the operator with "Safety lock is not operator-resettable" and no way
    out but a manual SQL edit."""
    ledger = ExecutionLedger(str(tmp_path / f"{reason}.sqlite3"))
    ledger.save_safety_incident("acct", reason)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime = _ledger_shell(ledger)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
        _peak_epoch=0,
    )
    runtime._circuit_breaker_reason = reason
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert ledger.load_safety_incidents("acct") == ()
    ledger.close()


# --- V7: the entry-risk latch writers serialize against the evaluation ---


class _LatchWriteProbeRuntime(EngineRuntime):
    """Records whether ``_risk_state_lock`` was held at each
    ``_entry_risk_latch`` assignment.

    Counting lock acquisitions proves nothing here: the pre-existing
    ``_persist_safety_state`` and ``_persist_risk_state`` helpers take the
    same lock on these very call paths, so the old assertion passed with the
    fix removed. The assignment is the one observation unique to it.
    """

    @property
    def _entry_risk_latch(self) -> dict[Market, str | None]:
        return self.__dict__["_probe_latch"]

    @_entry_risk_latch.setter
    def _entry_risk_latch(self, latch: dict[Market, str | None]) -> None:
        lock = getattr(self, "_risk_state_lock", None)
        self.__dict__.setdefault("_latch_write_lock_states", []).append(bool(getattr(lock, "held", False)))
        self.__dict__["_probe_latch"] = latch

    def latch_write_lock_states(self) -> list[bool]:
        return self.__dict__.setdefault("_latch_write_lock_states", [])


def test_operator_latch_clear_takes_the_risk_state_lock(tmp_path: Path) -> None:
    """The operator reset clears the latch from the web thread; without the
    risk-state lock the account loop's rebuild can restore a stale latch."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime = _ledger_shell(ledger, _LatchWriteProbeRuntime)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
        _peak_epoch=0,
    )
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._entry_risk_latch = {Market.EQUITY: "daily_loss_limit"}
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    risk_state_lock = _TrackedRLock()
    runtime._risk_state_lock = risk_state_lock  # type: ignore[assignment]
    runtime.latch_write_lock_states().clear()  # drop the seeding write

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    # The first write is the operator's latch clear; the later ones are
    # _evaluate_entry_risk_latch inside _persist_risk_state, which the
    # pre-existing code already ran under the lock.
    assert runtime.latch_write_lock_states()[0] is True
    ledger.close()
