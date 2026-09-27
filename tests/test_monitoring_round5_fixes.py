"""Regression tests for the fifth monitoring-layer defect pass.

Health verdicts must match what the current runtime does: an incident row the
runtime never restores is no block, and the universe is judged against the
runtime's refresh cadence and relevance. All offline (tmp_path, no network).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.config import Config
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring.health import HealthStatus, _check_risk, _check_universe

_NOW = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


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


def test_universe_is_stale_only_past_the_refresh_cadence() -> None:
    # Defaults: 24 h scanner refresh, 12 h artifact max age. A healthy runtime's
    # last refresh is up to 24 h old between scheduled refreshes.
    def snapshot(age: timedelta) -> dict[str, object]:
        return {"universe_refresh": {"status": {"crypto": "success"}, "last_refresh_at": (_NOW - age).isoformat()}}

    assert _check_universe(snapshot(timedelta(hours=13)), _NOW, Config()).status == HealthStatus.OK
    assert _check_universe(snapshot(timedelta(hours=26)), _NOW, Config()).status == HealthStatus.DEGRADED


def test_scanner_failure_of_irrelevant_market_is_not_degraded() -> None:
    # Like the runtime's scanner_degraded: only an operationally relevant
    # market's scanner failure degrades health.
    snapshot: dict[str, object] = {
        "universe_refresh": {"status": {"crypto": "refresh_failed"}, "errors": {"crypto": "boom"}},
        "markets": {"crypto": {"scanner_critical": False}},
    }

    assert _check_universe(snapshot, _NOW, Config()).status == HealthStatus.OK
    snapshot["markets"] = {"crypto": {"scanner_critical": True}}
    assert _check_universe(snapshot, _NOW, Config()).status == HealthStatus.DEGRADED
