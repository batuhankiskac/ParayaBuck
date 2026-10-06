"""Regression tests for the fifth monitoring-layer defect pass.

Health verdicts must match what the current runtime does: an incident row the
runtime never restores is no block, and a market entry halt is never OK. All offline (tmp_path, no network).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.config import Config
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import snapshot as snapshot_module
from parayabuck.monitoring.health import HealthStatus, _check_risk, evaluate_health
from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

# What the engine persists into the snapshot: its local alert counters.
_PERSISTED_ALERTS = {"suppressed": 0, "delivered": {"runtime_started": 1}}
_PERSISTED_JOURNAL = {"event_journal_written": 12, "event_journal_failed": 0, "event_journal_dropped": 0}


def test_unrestored_restart_limit_incident_is_not_a_risk_block(tmp_path: Path) -> None:
    # A pb-trade-updates restart-limit halt is persisted by _persist_risk_state
    # but never restored or cleared: after a process restart the runtime trades.
    db = tmp_path / "parayabuck.sqlite3"
    ledger = ExecutionLedger(str(db))
    ledger.save_safety_incident("acct", "pb-trade-updates_restart_limit")

    assert _check_risk(db, None).status == HealthStatus.OK

    ledger.save_safety_incident("acct", "ownership_drift")  # restored at startup: still a block
    ledger.close()
    check = _check_risk(db, None)
    assert check.status == HealthStatus.BLOCKED
    assert check.summary == "1 active safety incidents"


def test_market_entry_halt_makes_overall_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # the engine-reported alert/journal copies are not this test's subject
    monkeypatch.setattr(snapshot_module, "alert_stats", lambda: _PERSISTED_ALERTS)
    monkeypatch.setattr(snapshot_module, "journal_stats", lambda: _PERSISTED_JOURNAL)
    db = tmp_path / "parayabuck.sqlite3"
    ExecutionLedger(str(db)).close()
    PortfolioStateRepository(db).close()
    now = datetime.now(UTC)
    fresh = now.isoformat()
    worker = {"alive": True, "last_heartbeat": fresh, "last_event_at": fresh}
    runtime_health = {
        "state": "running",
        "healthy": True,
        "workers": {"pb-decisions": worker, "pb-account": worker},
        "markets": {
            "equity": {"enabled": True, "symbol_count": 3, "entry_halt_reason": None},
        },
        "last_reconciliation_at": fresh,
        "entry_risk_latch": {"equity": None},
        "signal_adjustment": {"healthy": True, "stale": False},
        "intent_watchdog": {"enabled": True, "evaluated_at": fresh, "alarms": [], "degraded_reasons": []},
        "signal_cross_check": {
            "enabled": True,
            "last": {"status": "agree", "session_date": "2026-09-28"},
            "pending": {},
        },
    }
    path = tmp_path / "health_snapshot.json"

    def evaluate() -> dict[str, object]:
        snap = build_snapshot(runtime_health=runtime_health, runtime_state="running")
        write_snapshot_atomic(path, snap)
        return evaluate_health(snapshot_path=path, database_path=db, config=Config(), now=now)

    healthy = evaluate()
    assert healthy["overall_health"] == "OK", healthy["checks"]
    runtime_health["markets"]["equity"]["entry_halt_reason"] = "pb-stocks_restart_limit"  # type: ignore[index]
    halted = evaluate()
    check = next(c for c in halted["checks"] if c["code"] == "market_halts")  # type: ignore[union-attr]
    assert halted["overall_health"] == "DEGRADED"
    assert check["status"] == "DEGRADED"
    assert "equity=pb-stocks_restart_limit" in check["summary"]
    assert halted["trading_state"] == "MARKET_HALTED"
