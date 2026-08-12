from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import RequestException

from parayabuck.config import Config
from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.risk.state import RiskState
from parayabuck.runtime import EngineRuntime

pytestmark = pytest.mark.paper_chaos


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
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
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
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: None,
        account=lambda: account,
    )
    runtime.ledger = ledger
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert {i.reason for i in ledger.load_safety_incidents("acct")} == {"protection_failure"}
    ledger.close()


def test_verified_reconcile_success_clears_persisted_incidents(tmp_path: Path) -> None:
    """A successful reconcile proves every persisted halt resolved (reconcile
    raises on drift/protection issues), so all incidents are cleared."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")
    ledger.save_safety_incident("acct", "protection_failure")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: None)
    runtime.ledger = ledger
    runtime._reconcile_lock = threading.Lock()
    runtime._last_reconciliation_at = None
    runtime._last_account_at = None
    runtime._generic_reconcile_failures = 0
    runtime._circuit_breaker_reason = "reconciliation_failed"

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason is None
    assert ledger.load_safety_incidents("acct") == ()
    ledger.close()


def test_reconcile_failure_keeps_persisted_incidents(tmp_path: Path) -> None:
    """A transient reconcile failure must not clear incidents: the halt
    remains persisted until a verified success."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.save_safety_incident("acct", "ownership_drift")

    class _FlakyBroker:
        account_id = "acct"

        def reconcile(self) -> None:
            raise RequestException("broker timeout")

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.broker = _FlakyBroker()
    runtime.ledger = ledger
    runtime._reconcile_lock = threading.Lock()
    runtime._last_reconciliation_at = None
    runtime._last_account_at = None
    runtime._generic_reconcile_failures = 0
    runtime._circuit_breaker_reason = None

    runtime._reconcile_safely()

    assert runtime._circuit_breaker_reason == "reconciliation_failed"
    assert [i.reason for i in ledger.load_safety_incidents("acct")] == ["ownership_drift"]
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

    def reconcile(self) -> None:
        self.reconcile_calls += 1

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
    assert restarted._entry_risk_latch[Market.CRYPTO] == "portfolio_drawdown_halt"
    assert restarted._circuit_breaker_reason is None
    ledger.close()
