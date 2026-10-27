"""Audit wave W07: halt reasons, reset ordering, reconcile freshness, stop() joins
and the latch alerts (R-1, R8-7, R9-2, R9-3, R9-4, R9-6, R9-9, C9-3, C9-6,
CC-5, CC-11, D7-3, D7-5, D7-6, Q-8).

Each test builds a small EngineRuntime shell with a real ExecutionLedger in
tmp_path and stubs only what the method under test does not reach.
"""

from __future__ import annotations

import queue
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config, EngineConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.live_safety import clear_operator_halt, operator_halt_marker_path, write_operator_halt
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.monitoring.health import HealthStatus, _check_reconciliation
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_health import WorkerHealth

CLEAN = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}
ACCOUNT = AccountSnapshot(10_000, 10_000, 10_000, 0)
SESSION_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
SESSION_CLOSE = datetime(2026, 9, 29, 16, 0, tzinfo=NEW_YORK).astimezone(UTC)
SYMBOLS = ("SPY", "QQQ", "QLD")


@pytest.fixture(autouse=True)
def alert_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    return events


def _runtime(tmp_path: Path, ledger: ExecutionLedger | None = None) -> tuple[EngineRuntime, ExecutionLedger]:
    """A running runtime shell whose reset path passes against a clean broker."""
    ledger = ledger or ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(CLEAN),
        account=lambda: ACCOUNT,
    )
    runtime.ledger = ledger
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(decision_db_path=str(tmp_path / "decisions.sqlite3")))
    runtime.config = EngineConfig()
    runtime._state_lock = threading.RLock()
    runtime._risk_state_lock = threading.Lock()
    runtime._reconcile_lock = threading.Lock()
    runtime._circuit_breaker_reason = None
    runtime._halt_reasons = []
    runtime._state = "running"
    runtime._running = True
    runtime._warming_up = False
    runtime._workers = {}
    runtime._threads = []
    runtime._threads_lock = threading.Lock()
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._portfolio_runtimes = ()
    runtime._market_entry_halts = {}
    runtime._entry_risk_latch = {}
    runtime._reconcile_failed = False
    runtime._last_account_at = None
    runtime._last_reconciliation_at = None
    runtime._last_clean_reconciliation_at = None
    runtime._generic_reconcile_failures = 0
    runtime._transient_reconcile_failures = 0
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._deferred_portfolio_batches = {}
    runtime._publish_account_snapshot = lambda account: None  # type: ignore[method-assign]
    runtime._resume_startup_rebalances = lambda **_kwargs: None  # type: ignore[method-assign]
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]
    return runtime, ledger


def _incident_reasons(ledger: ExecutionLedger) -> set[str]:
    return {incident.reason for incident in ledger.load_safety_incidents("acct")}


# --------------------------------------------------------------------------- #
# R9-3 / R-1: every active halt is kept and persisted; a reset lifts only its own
# --------------------------------------------------------------------------- #


def test_every_raised_halt_is_kept_and_persisted_and_a_reset_lifts_only_its_own(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)

    runtime._raise_halt("ownership_drift")
    runtime._raise_halt("portfolio_runtime_failure")

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert runtime._active_halt_reasons() == ("ownership_drift", "portfolio_runtime_failure")
    # R-1: both persistent halts are on disk before any account pass runs.
    assert _incident_reasons(ledger) == {"ownership_drift", "portfolio_runtime_failure"}

    runtime.reset_safety_lock()

    # The first reset clears its own halt and row only; the runtime stays halted.
    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert runtime._state == "halted"
    assert _incident_reasons(ledger) == {"portfolio_runtime_failure"}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert _incident_reasons(ledger) == set()
    ledger.close()


def test_a_persistent_halt_is_written_before_raise_halt_returns(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)

    runtime._raise_halt("protection_failure")
    runtime._raise_halt("account_fetch_failures")  # not a persistent reason

    assert _incident_reasons(ledger) == {"protection_failure"}
    ledger.close()


def test_a_failing_halt_write_never_raises_and_the_halt_stays_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ledger = _runtime(tmp_path)

    def _disk_full(_account_id: str, _reason: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "save_safety_incident", _disk_full)

    runtime._raise_halt("ownership_drift")  # must not raise

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert runtime._state == "halted"
    ledger.close()


def test_restart_restores_every_persisted_halt_not_only_the_breaker(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "reconciliation_failed")
    ledger.save_safety_incident("acct", "ownership_drift")
    runtime, _ = _runtime(tmp_path, ledger)
    runtime._circuit_breaker_reason = None
    runtime._halt_reasons = []

    runtime._restore_persistent_state()

    # The non-reconciliation reason becomes the breaker, as before; the other
    # persisted halt is kept too, so resetting the breaker cannot lift it.
    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert set(runtime._active_halt_reasons()) == {"ownership_drift", "reconciliation_failed"}
    ledger.close()


# --------------------------------------------------------------------------- #
# R9-6: refused resets keep the reconciliation incidents
# --------------------------------------------------------------------------- #


def test_refused_or_failed_reset_keeps_the_reconciliation_incidents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_reconciliation_breaker("reconciliation_failed")
    runtime._persist_reconciliation_incident("reconciliation_failed")
    monkeypatch.setattr(
        ledger,
        "managed_entries_for_account",
        lambda _account_id: [SimpleNamespace(status="manual_intervention_required")],
    )

    with pytest.raises(ValueError, match="manual intervention"):
        runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert _incident_reasons(ledger) == {"reconciliation_failed"}

    monkeypatch.setattr(ledger, "managed_entries_for_account", lambda _account_id: [])

    def _account_down() -> AccountSnapshot:
        raise RuntimeError("account endpoint down")

    runtime.broker.account = _account_down

    with pytest.raises(RuntimeError, match="account endpoint down"):
        runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert _incident_reasons(ledger) == {"reconciliation_failed"}
    ledger.close()


# --------------------------------------------------------------------------- #
# Q-8: a running engine honours the operator halt marker
# --------------------------------------------------------------------------- #


def test_running_engine_halts_when_the_operator_marker_appears_and_alerts_once(
    tmp_path: Path, alert_events: list[tuple[str, dict[str, object]]]
) -> None:
    runtime, ledger = _runtime(tmp_path)
    marker = operator_halt_marker_path(tmp_path)

    runtime._next_operator_halt_check = 0.0
    runtime._maybe_check_operator_halt()
    assert runtime._circuit_breaker_reason is None

    write_operator_halt(marker, reason="maintenance", user="test")
    runtime._next_operator_halt_check = 0.0
    runtime._maybe_check_operator_halt()

    assert runtime._circuit_breaker_reason == "operator_halt"
    assert runtime._state == "halted"
    assert _incident_reasons(ledger) == {"operator_halt"}

    runtime._next_operator_halt_check = 0.0
    runtime._maybe_check_operator_halt()

    breaker_alerts = [fields for kind, fields in alert_events if kind == "circuit_breaker"]
    assert [fields["reason"] for fields in breaker_alerts] == ["operator_halt"]
    ledger.close()


def test_operator_halt_resets_only_after_the_marker_is_cleared(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    marker = operator_halt_marker_path(tmp_path)
    write_operator_halt(marker, reason="maintenance", user="test")
    runtime._raise_halt("operator_halt")

    with pytest.raises(ValueError, match="resume"):
        runtime.reset_safety_lock()
    assert runtime._circuit_breaker_reason == "operator_halt"

    clear_operator_halt(marker)
    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert _incident_reasons(ledger) == set()
    ledger.close()


# --------------------------------------------------------------------------- #
# R9-9: a reset respawns a dead decision worker
# --------------------------------------------------------------------------- #


def test_reset_respawns_a_dead_decision_worker(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_halt("decision_worker_crashed")
    runtime._state = "halted"
    runtime._workers = {"pb-decisions": WorkerHealth(datetime.now(UTC), alive=False)}
    ran = threading.Event()
    runtime._decision_loop = ran.set  # type: ignore[method-assign]

    runtime.reset_safety_lock()

    assert ran.wait(5)
    assert [thread.name for thread in runtime._threads] == ["pb-decisions"]
    runtime._threads[0].join(5)
    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    ledger.close()


def test_a_stopped_engine_does_not_respawn_the_decision_worker(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._running = False
    runtime._workers = {"pb-decisions": WorkerHealth(datetime.now(UTC), alive=False)}
    runtime._decision_loop = lambda: None  # type: ignore[method-assign]

    assert runtime._respawn_decision_worker() is False
    assert runtime._threads == []
    ledger.close()


# --------------------------------------------------------------------------- #
# R9-2 / B-1 and R8-7: a re-queued close batch is decided after a reset
# --------------------------------------------------------------------------- #


def _bars() -> list[Bar]:
    return [
        Bar(SESSION_LABEL, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in SYMBOLS
    ]


def test_a_reset_inside_the_window_decides_the_requeued_close_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ledger = _runtime(tmp_path)
    decided: list[int] = []

    def _process(bars: list[Bar], **_kwargs: Any) -> SimpleNamespace:
        decided.append(len(bars))
        return SimpleNamespace(status="submitted", decision_id="dec-1", target=None, orders=(), submitted_order_ids=())

    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            symbols=SYMBOLS,
            strategy=SimpleNamespace(timeframe="1d", name="vt35"),
            process_closed_bars=_process,
        ),
    )
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._portfolio_lock = threading.RLock()
    runtime._last_portfolio_status = {}
    runtime._last_account_at = SESSION_CLOSE
    runtime._entry_readiness_reasons = lambda *_args, **_kwargs: ()  # type: ignore[method-assign]
    runtime._signal_cross_check_step = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
    runtime._fresh_account_snapshot_lease = (  # type: ignore[method-assign]
        lambda **_kwargs: runtime_module._AccountSnapshotLease(ACCOUNT, None)
    )
    runtime._account_snapshot_lock = threading.Lock()
    runtime._account_snapshot_generation = 0
    runtime._last_account_snapshot = None
    runtime._last_account_snapshot_at = None
    runtime._account_snapshot_active_leases = set()
    runtime._account_snapshot_invalidation_pending = False

    class _Clock:
        now = SESSION_CLOSE + timedelta(seconds=2)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return _Clock.now.astimezone(tz) if tz is not None else _Clock.now

    monkeypatch.setattr(runtime_module, "datetime", _Frozen)

    assert runtime._process_portfolio_timestamp(_bars()) is False
    assert decided == []

    # The operator reset lifts the halt inside the 40-minute window.
    with runtime._state_lock:
        runtime._release_halt_reason("ownership_drift")
    _Clock.now = SESSION_CLOSE + timedelta(seconds=62)
    runtime._run_due_deferred_portfolio_batches(now=_Clock.now)

    assert decided == [len(SYMBOLS)]
    assert runtime._deferred_portfolio_batches == {}
    ledger.close()


# --------------------------------------------------------------------------- #
# R9-4: drift does not refresh the clean reconciliation stamp
# --------------------------------------------------------------------------- #


def test_a_drift_reconcile_does_not_refresh_the_clean_reconciliation_stamp(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime.broker.reconcile = lambda: {**CLEAN, "QUANTITY_MISMATCH": ["QLD"]}  # type: ignore[method-assign]

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert runtime._last_reconciliation_at is not None
    assert runtime._last_clean_reconciliation_at is None

    now = datetime.now(UTC)
    fresh_drift = {
        "last_reconciliation_at": now.isoformat(),
        "last_clean_reconciliation_at": None,
    }
    assert _check_reconciliation(fresh_drift, now, Config()).status is HealthStatus.UNKNOWN

    stale_clean = {
        "last_reconciliation_at": now.isoformat(),
        "last_clean_reconciliation_at": (now - timedelta(hours=1)).isoformat(),
    }
    assert _check_reconciliation(stale_clean, now, Config()).status is HealthStatus.CRITICAL

    legacy = {"last_reconciliation_at": now.isoformat()}
    assert _check_reconciliation(legacy, now, Config()).status is HealthStatus.OK
    ledger.close()


# --------------------------------------------------------------------------- #
# C9-6: the periodic clean reconcile clears incidents under the risk-state lock
# --------------------------------------------------------------------------- #


def test_periodic_clean_reconcile_clears_incidents_under_the_risk_state_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ledger = _runtime(tmp_path)
    runtime._raise_reconciliation_breaker("reconciliation_failed")
    runtime._persist_reconciliation_incident("reconciliation_failed")
    held_during_clear: list[bool] = []
    real_clear = ledger.clear_safety_incident

    def _spy(account_id: str, reason: str) -> None:
        held_during_clear.append(runtime._risk_state_lock.locked())
        real_clear(account_id, reason)

    monkeypatch.setattr(ledger, "clear_safety_incident", _spy)

    runtime._reconcile_safely()

    assert held_during_clear and all(held_during_clear)
    assert runtime._circuit_breaker_reason is None
    assert _incident_reasons(ledger) == set()
    ledger.close()


# --------------------------------------------------------------------------- #
# C9-3: a market halt raised during an unlocked verification is not erased
# --------------------------------------------------------------------------- #


def test_verification_does_not_clear_a_halt_that_changed_while_it_ran(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    halts: dict[Market, str | None] = {Market.EQUITY: "pb-stocks_restart_limit"}

    def _worker_back_but_new_halt(_market: Market) -> bool:
        halts[Market.EQUITY] = "rebalance_reconciliation_required"
        return True

    runtime._restart_stream_worker = _worker_back_but_new_halt  # type: ignore[method-assign]

    runtime._verify_market_halts(halts)

    assert halts[Market.EQUITY] == "rebalance_reconciliation_required"
    ledger.close()


def test_verification_clears_the_halt_it_verified(tmp_path: Path) -> None:
    runtime, ledger = _runtime(tmp_path)
    halts: dict[Market, str | None] = {Market.EQUITY: "pb-stocks_restart_limit"}
    runtime._restart_stream_worker = lambda _market: True  # type: ignore[method-assign]

    runtime._verify_market_halts(halts)

    assert halts[Market.EQUITY] is None
    ledger.close()


# --------------------------------------------------------------------------- #
# CC-5: stop() joins outside the spawn locks
# --------------------------------------------------------------------------- #


def test_stop_joins_its_spawned_threads_outside_the_spawn_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[tuple[str, bool, bool]] = []

    class _Thread:
        def __init__(self, name: str) -> None:
            self.name = name

        def join(self, timeout: float | None = None) -> None:
            observed.append(
                (
                    self.name,
                    EngineRuntime._reconcile_spawn_lock.locked(),
                    EngineRuntime._warmup_retry_spawn_lock.locked(),
                )
            )

        def is_alive(self) -> bool:
            return False

    class _Journal:
        def stop(self, timeout: float | None = None) -> None:
            return None

    monkeypatch.setattr("parayabuck.monitoring.journal.get_journal", lambda: _Journal())
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._state = "running"
    runtime._state_lock = threading.RLock()
    runtime._stop_event = threading.Event()
    runtime._events = queue.Queue()
    runtime._stream_workers_launched = False
    runtime._threads = [_Thread("pb-decisions")]
    runtime._threads_lock = threading.Lock()
    runtime._reconcile_thread = _Thread("pb-reconcile")
    runtime._warmup_retry_thread = _Thread("pb-warmup-retry")
    runtime._storage_closed = True

    assert runtime.stop() is True

    assert [name for name, _, _ in observed] == ["pb-decisions", "pb-reconcile", "pb-warmup-retry"]
    assert all(not reconcile_held and not warmup_held for _, reconcile_held, warmup_held in observed)
    assert runtime._state == "stopped"


# --------------------------------------------------------------------------- #
# CC-11: a stream worker outliving stop() does not set the state back to running
# --------------------------------------------------------------------------- #


def test_a_stream_worker_that_outlives_stop_does_not_restore_running(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._state = "running"
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._workers = {"pb-account": WorkerHealth(datetime.now(UTC))}
    runtime._portfolio_runtimes = ()
    runtime._not_ready_symbols = set()

    def _blip() -> None:
        raise RuntimeError("simulated blip")

    def _wait_while_stop_lands(_delay: float) -> bool:
        # stop() clears _running during the backoff; the restore must not follow it.
        runtime._running = False
        return False

    runtime._stop_event = SimpleNamespace(wait=_wait_while_stop_lands)

    runtime._run_stream_worker("pb-account", _blip)

    assert runtime._state == "degraded"


# --------------------------------------------------------------------------- #
# D7-5 / D7-6 / D7-3: latch alerts fire once per transition
# --------------------------------------------------------------------------- #


def test_setting_a_reconcile_pending_latch_alerts_once_per_reason(
    alert_events: list[tuple[str, dict[str, object]]],
) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._state_lock = threading.RLock()
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._running = False

    runtime._set_reconcile_pending("trade_feed_stale")
    runtime._set_reconcile_pending("trade_feed_stale")
    runtime._set_reconcile_pending("empty_positions_read")

    reasons = [fields["reason"] for kind, fields in alert_events if kind == "reconciliation_failure"]
    assert reasons == ["trade_feed_stale", "empty_positions_read"]
    assert runtime._reconcile_pending_seq == 3


def test_event_queue_stale_alerts_on_each_transition_only(
    alert_events: list[tuple[str, dict[str, object]]],
) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.config = SimpleNamespace(maximum_queue_age_seconds=5.0, maximum_account_age_for_entry_seconds=60.0)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._state = "running"
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._workers = {}
    runtime._market_entry_halts = {}
    runtime._last_account_at = datetime.now(UTC)

    def _reasons(queue_age: float) -> tuple[str, ...]:
        return runtime._entry_readiness_reasons(Market.EQUITY, health=SimpleNamespace(queue_age_seconds=queue_age))

    assert "event_queue_stale" in _reasons(10.0)
    assert "event_queue_stale" in _reasons(12.0)
    assert "event_queue_stale" not in _reasons(1.0)
    assert "event_queue_stale" in _reasons(9.0)

    stale_alerts = [fields for kind, fields in alert_events if fields.get("reason") == "event_queue_stale"]
    assert len(stale_alerts) == 2


def test_signal_basis_latch_alerts_once_per_entry_into_the_latch(
    alert_events: list[tuple[str, dict[str, object]]],
) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._state_lock = threading.RLock()
    answers = iter(
        ["signal_adjustment_unverified", "signal_adjustment_unverified", None, "signal_adjustment_unverified"]
    )
    runtime._equity_signal_basis_check = lambda _decision_timestamp=None: next(answers)  # type: ignore[method-assign]

    results = [runtime._equity_signal_basis_latch() for _ in range(4)]

    assert results == [
        "signal_adjustment_unverified",
        "signal_adjustment_unverified",
        None,
        "signal_adjustment_unverified",
    ]
    latched = [fields for kind, fields in alert_events if fields.get("reason") == "signal_adjustment_unverified"]
    assert len(latched) == 2
