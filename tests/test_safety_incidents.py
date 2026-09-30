from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import RequestException

from parayabuck.config import Config, EngineConfig
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.risk.state import RiskState
from parayabuck.runtime import EngineRuntime

pytestmark = pytest.mark.paper_chaos

_CLEAN_RECONCILIATION = {
    "MATCH": [],
    "LOCAL_STALE": [],
    "BROKER_STALE": [],
    "QUANTITY_MISMATCH": [],
    "UNKNOWN_ORDER": [],
    "MISSING_LOCAL_ORDER": [],
    "MISSING_BROKER_ORDER": [],
}


def _runtime_with_ledger(ledger: ExecutionLedger) -> EngineRuntime:
    """A minimal EngineRuntime shell wired to a real ledger and fakes.

    Mirrors the construction used across test_hardening.py / test_loss_regressions.py:
    the methods under test only touch the attributes set here.
    """
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.ledger = ledger
    runtime.app_config = Config()
    runtime._risk_state_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._consecutive_losses = 0
    runtime._circuit_breaker_reason = None
    runtime._entry_risk_latch = {Market.EQUITY: None}
    return runtime


# --- PERSISTENT_INCIDENT: circuit-breaker reasons survive the day boundary ---


def test_ownership_drift_halt_survives_session_day_restart(tmp_path: Path) -> None:
    """ownership_drift on day N is recorded as an incident; a restart on day
    N+1 restores the halt from the incident table regardless of date."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = _runtime_with_ledger(ledger)

    runtime._halt_for_ownership_drift()

    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert runtime._state == "halted"
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["ownership_drift"]

    # A fresh runtime (restart) restores the halt from the incident even
    # though there is no TODAY session row carrying the reason.
    restarted = _runtime_with_ledger(ledger)
    restarted._restore_persistent_state()
    assert restarted._circuit_breaker_reason == "ownership_drift"
    ledger.close()


def test_drift_halt_rewrite_keeps_the_risk_state_peak_epoch(tmp_path: Path) -> None:
    """The halt's risk_state rewrite must not reset a post-reset peak epoch
    to 0 (the persisted-peak restore groups rows by epoch)."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=datetime.now(UTC).date(),
            day_start_equity=9_000.0,
            peak_equity=9_000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=3,
        )
    )
    runtime = _runtime_with_ledger(ledger)

    runtime._halt_for_ownership_drift()

    saved = ledger.load_risk_state("acct", datetime.now(UTC).date())
    assert saved is not None
    assert saved.circuit_breaker_reason == "ownership_drift"
    assert saved.peak_epoch == 3
    ledger.close()


def test_persistent_ownership_drift_alerts_only_on_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The account loop re-enters the drift halt every poll while drift
    persists: the halt state is re-asserted each time, but the operator
    alert and circuit-breaker metric fire only once."""
    import parayabuck.runtime as runtime_module

    emitted: list[str] = []
    recorded: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_: emitted.append(kind))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", recorded.append)
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = _runtime_with_ledger(ledger)

    for _ in range(3):
        runtime._halt_for_ownership_drift()
        runtime._state = "running"
        runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._halt_for_ownership_drift()

    assert emitted == ["circuit_breaker"]
    assert recorded == ["circuit_breaker"]
    assert runtime._state == "halted"
    assert set(runtime._entry_risk_latch.values()) == {"ownership_drift"}
    ledger.close()


class _FailingIncidentLedger:
    """A ledger whose incident write fails like a locked SQLite database."""

    def save_safety_incident(self, *_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")


def test_ownership_drift_alert_fires_even_when_persist_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Later polls see the reason already set and never alert, so a failed
    persist on the transition poll must not swallow the only alert."""
    import parayabuck.runtime as runtime_module

    emitted: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_: emitted.append(kind))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _kind: None)
    runtime = _runtime_with_ledger(_FailingIncidentLedger())  # type: ignore[arg-type]

    with pytest.raises(sqlite3.OperationalError):
        runtime._halt_for_ownership_drift()

    assert emitted == ["circuit_breaker"]
    assert runtime._state == "halted"
    assert runtime._circuit_breaker_reason == "ownership_drift"


def test_account_loop_survives_a_failed_drift_persist() -> None:
    """The account poller is the only drift detector: a failed halt persist
    must be retried on the next poll, not kill the pb-account thread."""
    from parayabuck.execution.alpaca import OwnershipDriftError
    from parayabuck.runtime_health import WorkerHealth

    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    runtime._workers = {"pb-account": WorkerHealth(now, now)}
    runtime._running = True
    runtime.config = SimpleNamespace(account_poll_interval_seconds=0.0)
    runtime._account_snapshot_cache_state = lambda: (None, None, 0)  # type: ignore[method-assign]

    def drifted_account() -> AccountSnapshot:
        raise OwnershipDriftError("broker holds fewer shares than managed")

    runtime.broker = SimpleNamespace(account_id="acct", account=drifted_account)
    halts: list[int] = []

    def failing_halt() -> None:
        halts.append(1)
        raise sqlite3.OperationalError("database is locked")

    runtime._halt_for_ownership_drift = failing_halt  # type: ignore[method-assign]
    polls: list[float] = []

    def wait(timeout: float) -> bool:
        polls.append(timeout)
        if len(polls) >= 2:
            runtime._running = False
        return False

    runtime._stop_event = SimpleNamespace(wait=wait)  # type: ignore[assignment]

    runtime._account_loop()

    assert len(halts) == 2
    assert runtime._workers["pb-account"].last_error == "database is locked"


def test_operator_reset_refuses_while_a_shared_worker_is_stopped(tmp_path: Path) -> None:
    """decision_worker_crashed is resettable (for the restored incident after
    a restart), but in the crashing process the decision loop has exited: a
    reset would report "running" with nothing processing decisions."""
    from parayabuck.runtime_health import WorkerHealth

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "decision_worker_crashed")
    now = datetime.now(UTC)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "decision_worker_crashed"
    runtime._running = True
    runtime._state = "halted"
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=False),
        "pb-account": WorkerHealth(now, now, alive=True),
        "pb-stocks": WorkerHealth(now, now, alive=False),
    }
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime.market_state = SimpleNamespace()
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime.ledger = ledger

    with pytest.raises(ValueError, match="pb-decisions"):
        runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "decision_worker_crashed"
    assert runtime._state == "halted"
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["decision_worker_crashed"]
    ledger.close()


def test_persistent_incident_restores_after_midnight_restart(tmp_path: Path) -> None:
    """A halt persisted at 23:59 (ambiguous_order_state) is restored on a
    00:01 restart; restore reads incidents, not the today session row."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    yesterday = datetime.now(UTC) - timedelta(days=1)
    ledger.save_safety_incident(
        "acct",
        "ambiguous_order_state",
        activated_at=yesterday.replace(hour=23, minute=59),
    )

    runtime = _runtime_with_ledger(ledger)
    runtime._restore_persistent_state()

    assert runtime._circuit_breaker_reason == "ambiguous_order_state"
    ledger.close()


def test_protection_failure_persisted_by_risk_state_survives_restart(tmp_path: Path) -> None:
    """protection_failure set outside the drift path is still persisted: the
    generic _persist_risk_state writes the incident (authoritative) alongside
    the compat session-row reason, and restart restores it."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = _runtime_with_ledger(ledger)
    runtime._circuit_breaker_reason = "protection_failure"
    account = AccountSnapshot(cash=10_000, equity=10_000, peak_equity=10_000, daily_pnl=0)
    runtime._persist_risk_state(account)

    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["protection_failure"]
    row = ledger.load_risk_state("acct", datetime.now(UTC).date())
    assert row is not None and row.circuit_breaker_reason == "protection_failure"

    restarted = _runtime_with_ledger(ledger)
    restarted._restore_persistent_state()
    assert restarted._circuit_breaker_reason == "protection_failure"
    ledger.close()


# --- SESSION_SCOPED: daily-loss baselines keep session semantics ---


def test_daily_loss_breach_restored_on_same_day_restart(tmp_path: Path) -> None:
    """A daily-loss breach on day N is still blocked after a restart on the
    SAME day: consecutive_losses is restored from today's risk_state row."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    today = datetime.now(UTC).date()
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=today,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
            realized_pnl=-400.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=4,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
        )
    )

    runtime = _runtime_with_ledger(ledger)
    runtime._restore_persistent_state()

    assert runtime._consecutive_losses == 4
    assert runtime._circuit_breaker_reason is None
    ledger.close()


def test_corrupt_risk_state_row_is_ignored_not_raised(tmp_path: Path) -> None:
    """A risk_state row with an unparsable updated_at (or a payload missing
    fields) must not fail runtime startup: the row is treated as absent and
    the state is rebuilt from the account, per the fail-safe contract."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    today = datetime.now(UTC).date()
    with ledger.connection as connection:
        connection.execute(
            "INSERT INTO risk_state(account_id, session_date, payload, updated_at) VALUES ('acct', ?, ?, ?)",
            (today.isoformat(), '{"day_start_equity": 10000.0}', "not-a-timestamp"),
        )
    assert ledger.load_risk_state("acct", today) is None

    with ledger.connection as connection:
        connection.execute(
            "UPDATE risk_state SET updated_at = ?, payload = ? WHERE account_id = 'acct' AND session_date = ?",
            (
                datetime.now(UTC).isoformat(),
                '{"peak_equity": 1.0}',
                today.isoformat(),
            ),
        )
    # Valid timestamp but missing required payload fields: same treatment.
    assert ledger.load_risk_state("acct", today) is None
    ledger.close()


def test_new_session_clears_session_scoped_state_but_keeps_persistent_incident(
    tmp_path: Path,
) -> None:
    """Day N+1 with a healthy session row: the session-scoped daily-loss
    baseline from day N does NOT carry over, while a persistent incident
    (ownership_drift) remains active. Both lifetimes coexist."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    now = datetime.now(UTC)
    today = now.date()
    yesterday = today - timedelta(days=1)
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=today,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,  # healthy new session
            circuit_breaker_reason=None,
            updated_at=now,
        )
    )
    # Stale day-N daily-loss baseline must NOT survive the boundary.
    ledger.save_risk_state(
        RiskState(
            account_id="acct",
            session_date=yesterday,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
            realized_pnl=-900.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=6,
            circuit_breaker_reason=None,
            updated_at=now - timedelta(days=1),
        )
    )
    # A day-N halt survives the boundary.
    ledger.save_safety_incident("acct", "ownership_drift", activated_at=now - timedelta(days=1))

    runtime = _runtime_with_ledger(ledger)
    runtime._restore_persistent_state()

    assert runtime._consecutive_losses == 0  # session-scoped: today's row only
    assert runtime._circuit_breaker_reason == "ownership_drift"  # persistent
    ledger.close()


# --- migration backfill ---


def test_v3_to_v4_migration_backfills_incident_and_restart_is_fail_closed(
    tmp_path: Path,
) -> None:
    """An old (v3) DB whose risk_state row carries a circuit_breaker_reason
    backfills a safety_incident on open; a restart then restores the halt
    (fail-closed). The backfill is stamped: reopening neither duplicates the
    incident nor resurrects an operator-cleared one."""
    path = tmp_path / "legacy.sqlite3"
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE risk_state (
            account_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(account_id, session_date)
        );
        INSERT INTO schema_migrations(version, applied_at) VALUES (3, CURRENT_TIMESTAMP);
        """
    )
    connection.execute(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "acct",
            yesterday.isoformat(),
            json.dumps(
                {
                    "day_start_equity": 10_000.0,
                    "peak_equity": 10_000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "fees": 0.0,
                    "consecutive_losses": 0,
                    "circuit_breaker_reason": "ownership_drift",
                },
                sort_keys=True,
            ),
            (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    ledger = ExecutionLedger(str(path))
    incidents = ledger.load_safety_incidents("acct")
    assert [i.reason for i in incidents] == ["ownership_drift"]

    # Fail-closed: a restart restores the halt from the backfilled incident.
    runtime = _runtime_with_ledger(ledger)
    runtime._restore_persistent_state()
    assert runtime._circuit_breaker_reason == "ownership_drift"
    ledger.close()

    # Idempotent: reopening does not duplicate the backfilled incident.
    reopened = ExecutionLedger(str(path))
    assert [i.reason for i in reopened.load_safety_incidents("acct")] == ["ownership_drift"]

    # The backfill is gated on the v4 stamp: an operator-cleared incident is
    # NOT resurrected from the historical risk_state row on reopen.
    reopened.clear_safety_incident("acct")
    assert reopened.load_safety_incidents("acct") == ()
    reopened.close()
    ExecutionLedger(str(path)).close()


def test_backfill_picks_latest_halted_session_when_later_session_is_healthy(
    tmp_path: Path,
) -> None:
    """A healthy (reason-less) LATEST session must not mask an older persisted
    halt: the backfill picks the latest row WITH a reason (fail-safe)."""
    path = tmp_path / "legacy.sqlite3"
    now = datetime.now(UTC)
    today = now.date()
    yesterday = today - timedelta(days=1)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE risk_state (
            account_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(account_id, session_date)
        );
        INSERT INTO schema_migrations(version, applied_at) VALUES (3, CURRENT_TIMESTAMP);
        """
    )
    healthy_payload = json.dumps(
        {
            "day_start_equity": 10_000.0,
            "peak_equity": 10_000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "fees": 0.0,
            "consecutive_losses": 0,
            "circuit_breaker_reason": None,
        },
        sort_keys=True,
    )
    halted_payload = json.dumps(
        {
            "day_start_equity": 10_000.0,
            "peak_equity": 10_000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "fees": 0.0,
            "consecutive_losses": 0,
            "circuit_breaker_reason": "protection_failure",
        },
        sort_keys=True,
    )
    connection.executemany(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("acct", today.isoformat(), healthy_payload, now.isoformat()),
            ("acct", yesterday.isoformat(), halted_payload, (now - timedelta(days=1)).isoformat()),
        ],
    )
    connection.commit()
    connection.close()

    ledger = ExecutionLedger(str(path))
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["protection_failure"]
    ledger.close()


# --- ledger incident API ---


def test_safety_incident_upsert_preserves_original_activation(tmp_path: Path) -> None:
    """Re-saving the same reason updates updated_at but preserves the original
    activated_at; clearing is per-reason and account-scoped."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    first = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    ledger.save_safety_incident("acct", "ownership_drift", activated_at=first)
    ledger.save_safety_incident("acct", "ownership_drift")  # re-save later

    incidents = ledger.load_safety_incidents("acct")
    assert len(incidents) == 1
    assert incidents[0].activated_at == first
    assert incidents[0].updated_at > first
    assert incidents[0].scope == "global"
    assert ledger.load_safety_incidents("other-account") == ()

    # Per-reason clear leaves other reasons untouched.
    ledger.save_safety_incident("acct", "protection_failure")
    ledger.clear_safety_incident("acct", "ownership_drift")
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["protection_failure"]

    # reason=None clears every incident for the account.
    ledger.clear_safety_incident("acct")
    assert ledger.load_safety_incidents("acct") == ()
    ledger.close()


# --- clearing semantics: operator reset vs verified reconcile ---


def test_operator_reset_clears_only_the_verified_incident(tmp_path: Path) -> None:
    """reset_safety_lock clears exactly the incident whose recovery condition
    was verified, never unrelated incidents."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")
    ledger.save_safety_incident("acct", "protection_failure")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
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

    assert runtime._circuit_breaker_reason is None
    assert {i.reason for i in ledger.load_safety_incidents("acct")} == {"protection_failure"}
    ledger.close()


def test_verified_reconcile_success_clears_only_reconciliation_incidents(tmp_path: Path) -> None:
    """A clean reconcile clears only incidents owned by reconciliation.

    Unrelated protection/ownership incidents retain their own recovery paths.
    """
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "reconciliation_failed")
    ledger.save_safety_incident("acct", "ownership_drift")
    ledger.save_safety_incident("acct", "protection_failure")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime.ledger = ledger
    runtime._reconcile_lock = threading.Lock()
    runtime._last_reconciliation_at = None
    runtime._last_account_at = None
    runtime._generic_reconcile_failures = 0
    runtime._circuit_breaker_reason = "reconciliation_failed"

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason is None
    assert {i.reason for i in ledger.load_safety_incidents("acct")} == {"ownership_drift", "protection_failure"}
    ledger.close()


def test_reconcile_failure_keeps_persisted_incidents(tmp_path: Path) -> None:
    """Transient reconcile failures block entries but only the Nth one
    persists a new incident: the halt must not outlive a single Alpaca
    timeout, and the incidents already on the ledger are never cleared."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")

    class _FlakyBroker:
        account_id = "acct"

        def reconcile(self) -> dict[str, list[str]]:
            raise RequestException("broker timeout")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = _FlakyBroker()
    runtime.ledger = ledger
    runtime.config = EngineConfig(reconcile_failure_threshold=3)
    runtime._reconcile_lock = threading.Lock()
    runtime._last_reconciliation_at = None
    runtime._last_account_at = None
    runtime._generic_reconcile_failures = 0
    runtime._transient_reconcile_failures = 0
    runtime._circuit_breaker_reason = None
    runtime._reconcile_failed = False

    runtime._reconcile_safely()
    runtime._reconcile_safely()

    # Below the threshold: entries blocked, but nothing persisted, no halt.
    assert runtime._circuit_breaker_reason is None
    assert runtime._reconcile_failed is True
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["ownership_drift"]

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == [
        "reconciliation_failed",
        "ownership_drift",
    ]
    ledger.close()


# --- DRAWDOWN OPERATIONAL INCIDENT + RISK-EPOCH RESET ----------------------


class _EpochBroker:
    """Fake broker for the risk-epoch reset: reconcile verifies, account()
    reflects the current (rebased) peak."""

    account_id = "acct"

    def __init__(self, equity: float, peak: float) -> None:
        self.equity = equity
        self.peak = peak
        self.reconcile_calls = 0
        self.reset_calls: list[float] = []

    def reconcile(self) -> dict[str, list[str]]:
        self.reconcile_calls += 1
        return dict(_CLEAN_RECONCILIATION)

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash=self.equity,
            equity=self.equity,
            peak_equity=self.peak,
            daily_pnl=0.0,
        )

    def reset_peak_equity(self, equity: float) -> None:
        self.reset_calls.append(equity)
        self.peak = equity


class _EpochRepository:
    def __init__(self) -> None:
        self.resets: list[tuple[str, float]] = []

    def reset_high_water(self, account_id: str, equity: float) -> None:
        self.resets.append((account_id, equity))


def _epoch_runtime(ledger: ExecutionLedger, broker: _EpochBroker) -> EngineRuntime:
    runtime = _runtime_with_ledger(ledger)
    runtime.broker = broker  # type: ignore[attr-defined]
    runtime._running = True  # type: ignore[attr-defined]
    runtime._portfolio_repository = _EpochRepository()  # type: ignore[attr-defined]
    return runtime


def test_drawdown_breach_persists_incident_and_epoch_reset_recovers(
    tmp_path: Path,
) -> None:
    """A >limit drawdown persists the portfolio_drawdown_halt operational
    incident; the operator risk-epoch reset rebases the peak to the current
    equity (broker + repository), clears the incident and unblocks entries."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _EpochBroker(equity=94_000, peak=100_000)  # 6% drawdown
    runtime = _epoch_runtime(ledger, broker)

    runtime._evaluate_entry_risk_latch(broker.account())
    assert runtime._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["portfolio_drawdown_halt"]

    baseline = runtime.start_new_risk_epoch()

    assert baseline == pytest.approx(94_000)
    assert broker.reset_calls == [94_000]
    assert broker.peak == pytest.approx(94_000)
    assert runtime._portfolio_repository.resets == [("acct", 94_000)]  # type: ignore[attr-defined]
    assert ledger.load_safety_incidents("acct") == ()
    # The latch re-evaluates against the rebased baseline and clears.
    assert runtime._entry_risk_latch[Market.EQUITY] is None
    ledger.close()


def test_risk_epoch_reset_evaluates_the_latch_once(tmp_path: Path) -> None:
    """``_persist_risk_state`` already re-evaluates the entry-risk latch
    against the rebased snapshot. A second pass over the SAME snapshot can
    not transition anything and only repeats two incident writes against
    SQLite on an operator action."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _EpochBroker(equity=94_000, peak=100_000)  # 6% drawdown
    runtime = _epoch_runtime(ledger, broker)
    runtime._evaluate_entry_risk_latch(broker.account())
    assert runtime._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"

    writes: list[str] = []
    real_clear = ledger.clear_safety_incident

    def counting_clear(account_id: str, reason: str | None = None) -> None:
        if reason == "portfolio_drawdown_halt":
            writes.append(reason)
        real_clear(account_id, reason)

    ledger.clear_safety_incident = counting_clear  # type: ignore[method-assign]

    runtime.start_new_risk_epoch()

    # One deliberate clear of the drawdown incident plus the single latch
    # evaluation inside _persist_risk_state — never three.
    assert writes == ["portfolio_drawdown_halt", "portfolio_drawdown_halt"]
    assert runtime._entry_risk_latch[Market.EQUITY] is None
    ledger.close()


def test_risk_epoch_reset_reconciles_under_the_reconcile_lock(tmp_path: Path) -> None:
    """The operator epoch reset runs from a web thread while pb-reconcile may
    be mid-reconcile; its own broker reconcile must hold _reconcile_lock."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _EpochBroker(equity=94_000, peak=100_000)
    runtime = _epoch_runtime(ledger, broker)
    lock = threading.Lock()
    runtime._reconcile_lock = lock  # type: ignore[attr-defined]
    held: list[bool] = []
    real_reconcile = broker.reconcile

    def observing_reconcile() -> dict[str, list[object]]:
        held.append(lock.locked())
        return real_reconcile()

    broker.reconcile = observing_reconcile  # type: ignore[method-assign]

    runtime.start_new_risk_epoch()

    assert held == [True]
    assert not lock.locked()
    ledger.close()


def test_risk_epoch_reset_clears_only_drawdown_incident(tmp_path: Path) -> None:
    """The epoch reset never touches unrelated incidents (ownership drift
    keeps its own recovery flow)."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")
    ledger.save_safety_incident("acct", "portfolio_drawdown_halt")
    broker = _EpochBroker(equity=94_000, peak=100_000)
    runtime = _epoch_runtime(ledger, broker)
    runtime._circuit_breaker_reason = None  # type: ignore[attr-defined]

    runtime.start_new_risk_epoch()

    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["ownership_drift"]
    ledger.close()


def test_risk_epoch_reset_refused_with_circuit_breaker(tmp_path: Path) -> None:
    """A circuit breaker (e.g. ownership drift) blocks the epoch reset: the
    drawdown baseline must not be rebased while a serious incident is
    unresolved."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _EpochBroker(equity=94_000, peak=100_000)
    runtime = _epoch_runtime(ledger, broker)
    runtime._circuit_breaker_reason = "ownership_drift"  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="circuit breaker"):
        runtime.start_new_risk_epoch()

    assert broker.reset_calls == []
    assert broker.reconcile_calls == 0
    ledger.close()


def test_risk_epoch_reset_refused_when_not_running(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    broker = _EpochBroker(equity=94_000, peak=100_000)
    runtime = _epoch_runtime(ledger, broker)
    runtime._running = False  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="not running"):
        runtime.start_new_risk_epoch()

    assert broker.reset_calls == []
    ledger.close()


def test_restart_restores_drawdown_latch_from_incident(tmp_path: Path) -> None:
    """A drawdown breach persists as an incident; a restart restores the
    entry latch from it so the halt is visible before the first account
    poll re-evaluates."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "portfolio_drawdown_halt")

    restarted = _runtime_with_ledger(ledger)
    restarted._restore_persistent_state()

    assert restarted._entry_risk_latch[Market.EQUITY] == "portfolio_drawdown_halt"
    assert restarted._circuit_breaker_reason is None
    ledger.close()


# --- breaker precedence: reconciliation never masks a stronger halt ---

_STRONGER_REASONS = [
    "ownership_drift",
    "portfolio_runtime_failure",
    "decision_worker_crashed",
    "trade_update_callback_failure",
    "final_market_event_queue_overflow",
    "protection_failure",
]


def _reconcile_runtime(ledger: ExecutionLedger, reconcile: object, reason: str | None) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=reconcile)
    runtime.ledger = ledger
    runtime._reconcile_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._last_reconciliation_at = None
    runtime._last_account_at = None
    runtime._generic_reconcile_failures = 0
    runtime._circuit_breaker_reason = reason
    return runtime


def _raise(exc: BaseException) -> object:
    def reconcile() -> dict[str, list[str]]:
        raise exc

    return reconcile


@pytest.mark.parametrize("reason", _STRONGER_REASONS)
@pytest.mark.parametrize(
    "failure",
    ["request", "generic", "drift", "protection"],
)
def test_reconcile_failure_never_replaces_a_stronger_breaker(tmp_path: Path, reason: str, failure: str) -> None:
    """A failed reconcile must not overwrite a non-reconciliation reason: the
    next clean reconcile would clear the overwritten reason and drop the
    halt while its incident still demands operator-verified recovery."""
    from parayabuck.execution.alpaca import ProtectionReconciliationError

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", reason)
    drift = dict(_CLEAN_RECONCILIATION, UNKNOWN_ORDER=["x"])
    reconcile = {
        "request": _raise(RequestException("broker timeout")),
        "generic": _raise(RuntimeError("bug")),
        "drift": lambda: drift,
        "protection": _raise(ProtectionReconciliationError("stop missing")),
    }[failure]
    runtime = _reconcile_runtime(ledger, reconcile, reason)

    for _ in range(3):  # generic failures escalate on the third
        runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason == reason

    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason == reason
    assert reason in {i.reason for i in ledger.load_safety_incidents("acct")}
    ledger.close()


def test_reconcile_failures_still_raise_and_escalate_reconciliation_reasons(tmp_path: Path) -> None:
    """Within the reconciliation family the breaker still sets, escalates to
    bug_suspected, yields to protection_failure and clears on success."""
    from parayabuck.execution.alpaca import ProtectionReconciliationError

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = _reconcile_runtime(ledger, _raise(RuntimeError("bug")), None)

    runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    runtime._reconcile_safely()
    runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason == "reconciliation_bug_suspected"

    runtime.broker = SimpleNamespace(account_id="acct", reconcile=_raise(ProtectionReconciliationError("x")))
    runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason == "protection_failure"

    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: dict(_CLEAN_RECONCILIATION))
    runtime._reconcile_safely()
    assert runtime._circuit_breaker_reason is None
    ledger.close()


def test_reconcile_drift_alerts_only_on_the_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent drift re-asserts the halt and persist on every reconcile
    pass but alerts once, even when the first pass's persist raises."""
    import parayabuck.runtime as runtime_module
    from parayabuck.execution.alpaca import OwnershipDriftError

    emitted: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_k: emitted.append(kind))
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", events.append)
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = _reconcile_runtime(ledger, _raise(OwnershipDriftError("broker holds fewer shares")), None)
    runtime._state = "running"
    persists: list[str] = []

    def persist(reason: str) -> None:
        persists.append(reason)
        if len(persists) == 1:
            raise sqlite3.OperationalError("database is locked")

    runtime._persist_safety_state = persist  # type: ignore[method-assign]

    with pytest.raises(sqlite3.OperationalError):
        runtime._reconcile_safely()
    runtime._state = "running"  # re-assertion is observable on every pass
    runtime._reconcile_safely()
    runtime._reconcile_safely()

    assert persists == ["ownership_drift"] * 3
    assert runtime._state == "halted"
    assert runtime._circuit_breaker_reason == "ownership_drift"
    assert emitted == ["circuit_breaker"]
    assert events == ["circuit_breaker"]
    ledger.close()


def test_restore_prefers_a_non_reconciliation_incident(tmp_path: Path) -> None:
    """A newer reconciliation incident must not mask an older stronger one at
    restore: a clean reconcile would clear it and resume trading."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    now = datetime.now(UTC)
    ledger.save_safety_incident("acct", "ownership_drift", activated_at=now - timedelta(hours=1))
    ledger.save_safety_incident("acct", "reconciliation_failed", activated_at=now)

    runtime = _runtime_with_ledger(ledger)
    runtime._restore_persistent_state()

    assert runtime._circuit_breaker_reason == "ownership_drift"
    ledger.close()


# --- no check-then-set to "running" can overwrite a concurrent halt ---


class _StateProbeRuntime(EngineRuntime):
    """Runs ``on_read`` the first time ``_state`` is read as ``trigger`` —
    i.e. between a caller's state check and its write."""

    @property
    def _state(self) -> str:
        value = self.__dict__["_probe_state"]
        hook = self.__dict__.get("_probe_hook")
        if hook is not None and value == hook[0]:
            self.__dict__["_probe_hook"] = None
            hook[1]()
        return value

    @_state.setter
    def _state(self, value: str) -> None:
        self.__dict__["_probe_state"] = value


def _concurrent_trade_update_halt(runtime: EngineRuntime) -> None:
    """Start the real trade-update failure halt on another thread and give
    it a bounded chance to finish (it blocks while the caller holds the
    state lock)."""
    import asyncio

    async def failing(_update: object) -> None:
        raise RuntimeError("callback failed")

    runtime._handle_trade_update = failing  # type: ignore[method-assign]
    thread = threading.Thread(
        target=lambda: asyncio.run(runtime._on_trade_update(SimpleNamespace(client_order_id="c1"))),  # type: ignore[arg-type]
    )
    thread.start()
    thread.join(timeout=0.3)
    runtime.__dict__["_halter"] = thread


def test_warmup_promotion_never_overwrites_a_concurrent_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A halt raised between _refresh_readiness's warming_up check and its
    "running" write must survive: the promotion is atomic with the halt."""
    import parayabuck.runtime as runtime_module

    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _kind: None)
    runtime = _StateProbeRuntime.__new__(_StateProbeRuntime)
    runtime._state = "warming_up"
    runtime._running = True
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = None
    runtime._not_ready_symbols = set()
    runtime._portfolio_runtimes = ()
    runtime._has_ready_symbols = lambda: True  # type: ignore[method-assign]
    runtime.__dict__["_probe_hook"] = ("warming_up", lambda: _concurrent_trade_update_halt(runtime))

    runtime._refresh_readiness()
    runtime.__dict__["_halter"].join(timeout=5)

    assert runtime._circuit_breaker_reason == "trade_update_callback_failure"
    assert runtime._state == "halted"


def test_warmup_promotion_never_runs_past_a_breaker() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._state = "warming_up"
    runtime._running = True
    runtime._state_lock = threading.RLock()
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime._not_ready_symbols = set()
    runtime._portfolio_runtimes = ()
    runtime._has_ready_symbols = lambda: True  # type: ignore[method-assign]

    runtime._refresh_readiness()

    assert runtime._state == "warming_up"


def test_operator_reset_keeps_a_halt_raised_during_verification(tmp_path: Path) -> None:
    """The breaker is cleared before the market halts are verified; a halt
    another thread raises in that window must not be flipped to running."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "reconciliation_failed")
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime = _runtime_with_ledger(ledger)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime.market_state = SimpleNamespace()
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]

    def concurrent_halt(_halts: object) -> None:
        runtime._circuit_breaker_reason = "portfolio_runtime_failure"
        runtime._state = "halted"

    runtime._verify_market_halts = concurrent_halt  # type: ignore[method-assign]

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert runtime._state == "halted"
    ledger.close()


def test_operator_reset_keeps_a_halt_raised_during_broker_reconcile(tmp_path: Path) -> None:
    """The broker reconcile inside the reset is a slow network call; a halt
    another thread raises meanwhile must survive the breaker clear, which
    may only clear the reason being reset."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime = _runtime_with_ledger(ledger)

    def reconcile_while_another_thread_halts() -> dict[str, list[str]]:
        with runtime._state_lock:
            runtime._circuit_breaker_reason = "portfolio_runtime_failure"
            runtime._state = "halted"
        return dict(_CLEAN_RECONCILIATION)

    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=reconcile_while_another_thread_halts,
        account=lambda: account,
    )
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._state_lock = threading.RLock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime.market_state = SimpleNamespace()
    runtime._persist_risk_state = lambda _account: None  # type: ignore[method-assign]

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason == "portfolio_runtime_failure"
    assert runtime._state == "halted"
    ledger.close()


@pytest.mark.parametrize(("alive", "expected"), [(True, "running"), (False, "degraded")])
def test_operator_reset_restores_a_blip_degraded_runtime(tmp_path: Path, alive: bool, expected: str) -> None:
    """protection_failure gates entries without halting; a trade-update blip
    meanwhile leaves "degraded" (its restore never passes a breaker). The
    reset that clears the breaker must un-pin it, as a clean reconcile does
    for a reconciliation reason, but only while that worker is alive."""
    from parayabuck.runtime_health import WorkerHealth

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "protection_failure")
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime = _runtime_with_ledger(ledger)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: dict(_CLEAN_RECONCILIATION),
        account=lambda: account,
    )
    now = datetime.now(UTC)
    # A dead worker is refused up front; this models it exiting between that
    # check and the reset tail.
    worker = WorkerHealth(now, now, alive=True)
    runtime._workers = {"pb-trade-updates": worker}
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "degraded"
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime.market_state = SimpleNamespace()

    def persist(_account: AccountSnapshot) -> None:
        worker.alive = alive

    runtime._persist_risk_state = persist  # type: ignore[method-assign]

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._state == expected
    assert [incident.reason for incident in ledger.load_safety_incidents("acct")] == []
    ledger.close()


class _LockCheckedStateRuntime(EngineRuntime):
    """Records whether ``_state_lock`` was held at each ``_state`` write."""

    @property
    def _state(self) -> str:
        return self.__dict__["_probe_state"]

    @_state.setter
    def _state(self, value: str) -> None:
        lock = self.__dict__.get("_state_lock")
        held = bool(lock is not None and lock._is_owned())  # type: ignore[attr-defined]
        self.__dict__.setdefault("_state_writes", []).append((value, held))
        self.__dict__["_probe_state"] = value


def test_halt_writers_hold_the_state_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Queue overflow and the shared-worker restart limit write "halted"
    under the state lock, so no locked check-then-set can overwrite it."""
    import parayabuck.runtime as runtime_module
    from parayabuck.runtime_health import WorkerHealth

    monkeypatch.setattr(runtime_module.alerts, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.runtime_metrics_store, "record_event", lambda _kind: None)
    runtime = _LockCheckedStateRuntime.__new__(_LockCheckedStateRuntime)
    runtime.__dict__["_probe_state"] = "running"
    runtime._state_lock = threading.RLock()
    runtime._event_stats_lock = threading.Lock()
    runtime._circuit_breaker_reason = None
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._events = SimpleNamespace(put_market_event=lambda _event: "overflow")

    runtime._put_event("primary", SimpleNamespace())

    now = datetime.now(UTC)
    runtime._workers = {"pb-trade-updates": WorkerHealth(now, now, restart_count=5)}
    runtime._running = True
    runtime._stop_event = SimpleNamespace(wait=lambda _s: False)

    def crash() -> None:
        raise RuntimeError("down")

    runtime._run_stream_worker("pb-trade-updates", crash)

    halted = [held for value, held in runtime.__dict__["_state_writes"] if value == "halted"]
    assert halted == [True, True]
