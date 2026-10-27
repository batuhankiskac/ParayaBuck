"""Alerting and runtime-metric contract tests.

* ``parayabuck.monitoring.alerts.emit`` never raises, delivers to the log
  sink (and the event journal), and throttles repeat failures;
* ``parayabuck.monitoring.metrics.runtime_metrics`` reads an empty or
  temp runtime ledger without raising and aggregates persisted state plus
  process-local counters.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest

from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import alerts, metrics
from parayabuck.risk.state import RiskState

# ---------------------------------------------------------------------------
# Alerts module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_observability() -> None:
    alerts.reset_for_tests()
    metrics.reset_for_tests()
    yield
    alerts.reset_for_tests()
    metrics.reset_for_tests()


def test_alerts_emit_never_raises_and_accepts_all_kinds() -> None:
    for kind in sorted(alerts.ALERT_KINDS):
        alerts.emit(kind, message=f"test {kind}", extra=1)
    # Unknown kinds are rejected without raising.
    alerts.emit("not_a_real_kind", message="ignored")
    assert alerts.alert_stats()["suppressed"] == 0


def test_alerts_log_carries_kind_message_and_fields(caplog: pytest.LogCaptureFixture) -> None:
    alerts._throttle_seconds = 0.0
    with caplog.at_level(logging.INFO, logger="parayabuck.monitoring.alerts"):
        alerts.emit("data_provider_unavailable", message="scanner down", market="equity", attempt=1)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ALERT ")]
    assert lines == ["ALERT data_provider_unavailable: scanner down fields={'market': 'equity', 'attempt': 1}"]
    assert alerts.alert_stats()["delivered"] == {"data_provider_unavailable": 1}


def test_alerts_throttle_coalesces_repeat_failures(caplog: pytest.LogCaptureFixture) -> None:
    alerts._throttle_seconds = 60.0
    with caplog.at_level(logging.INFO, logger="parayabuck.monitoring.alerts"):
        alerts.emit("reconciliation_failure", message="first")
        alerts.emit("reconciliation_failure", message="second, throttled")
    assert alerts.alert_stats()["suppressed"] == 1
    assert alerts.alert_stats()["delivered"] == {"reconciliation_failure": 1}
    # The suppression is visible at INFO with its running count, never DEBUG-only.
    suppressed = [r for r in caplog.records if "suppressed" in r.getMessage()]
    assert len(suppressed) == 1 and suppressed[0].levelno == logging.INFO
    assert "1 repeat(s)" in suppressed[0].getMessage()
    # A different kind is NOT throttled by the same window.
    alerts.emit("data_provider_unavailable", message="other kind")
    assert alerts.alert_stats()["delivered"]["data_provider_unavailable"] == 1


def test_alerts_throttle_keys_on_reason_and_state() -> None:
    """A new failure reason (or state) of the same kind must not be swallowed
    by an earlier, different one inside the throttle window."""
    alerts._throttle_seconds = 60.0
    alerts.emit("runtime_start_failed", message="a", reason="credentials_missing")
    alerts.emit("runtime_start_failed", message="b", reason="trading_host_unauthorized")
    alerts.emit("runtime_start_failed", message="c", reason="trading_host_unauthorized")
    alerts.emit("circuit_breaker", message="d", state="tripped")
    alerts.emit("circuit_breaker", message="e", state="cleared")
    assert alerts.alert_stats()["delivered"] == {"runtime_start_failed": 2, "circuit_breaker": 2}
    assert alerts.alert_stats()["suppressed"] == 1


# ---------------------------------------------------------------------------
# Runtime metrics
# ---------------------------------------------------------------------------


def test_runtime_metrics_empty_state_is_safe(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.sqlite3"
    snapshot = metrics.runtime_metrics(missing)
    assert snapshot["sessions"]["total"] == 0
    assert snapshot["restart_count"] == 0
    assert snapshot["incidents"]["count"] == 0
    assert snapshot["risk_state"]["latest"] == []
    for key in (
        "data_provider_failures",
        "reconciliation_failures",
        "daily_loss_incidents",
        "drawdown_incidents",
        "circuit_breaker_events",
    ):
        assert snapshot[key] == 0
    assert snapshot["entry_block_duration_seconds"] is None


def test_runtime_metrics_reads_persisted_state(tmp_path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "runtime.sqlite3"))
    try:
        session_a = ledger.record_runtime_session("account-1", "paper")
        session_b = ledger.record_runtime_session("account-1", "paper")
        ledger.end_runtime_session(session_a, "stopped")
        ledger.save_safety_incident("account-1", "portfolio_drawdown_halt")
        ledger.save_safety_incident("account-2", "ownership_drift")
        now = datetime.now(UTC)
        ledger.save_risk_state(
            RiskState(
                account_id="account-1",
                session_date=date(2026, 8, 12),
                day_start_equity=100_000.0,
                peak_equity=101_000.0,
                realized_pnl=-900.0,
                unrealized_pnl=-100.0,
                fees=3.5,
                circuit_breaker_reason=None,
                updated_at=now,
            )
        )
        ledger_path = str(tmp_path / "runtime.sqlite3")
        snapshot = metrics.runtime_metrics(ledger_path)
        assert snapshot["sessions"]["total"] == 2
        assert snapshot["sessions"]["completed"] == 1
        assert snapshot["sessions"]["unended"] == 1
        assert snapshot["restart_count"] == 1  # second start is the first restart
        assert snapshot["incidents"]["count"] == 2
        reasons = {item["reason"] for item in snapshot["incidents"]["active"]}
        assert reasons == {"portfolio_drawdown_halt", "ownership_drift"}
        assert snapshot["risk_state"]["latest"][0]["account_id"] == "account-1"
        assert snapshot["risk_state"]["latest"][0]["realized_pnl"] == -900.0
        assert snapshot["entry_block_duration_seconds"] is not None
        assert snapshot["entry_block_duration_seconds"] >= 0.0
        # account_id narrows the persisted queries.
        scoped = metrics.runtime_metrics(ledger_path, account_id="account-1")
        assert scoped["incidents"]["count"] == 1
        assert [item["reason"] for item in scoped["incidents"]["active"]] == ["portfolio_drawdown_halt"]
        assert session_b  # keep the second session id referenced
    finally:
        ledger.close()


def test_runtime_metrics_merges_process_local_counters(tmp_path) -> None:
    metrics.record_event("data_provider_unavailable")
    metrics.record_event("data_provider_unavailable")
    metrics.record_event("circuit_breaker")
    metrics.record_event("not_a_counter")  # ignored
    snapshot = metrics.runtime_metrics(tmp_path / "empty.sqlite3")
    counters = snapshot["counters"]
    assert counters["data_provider_unavailable"] == 2
    assert counters["circuit_breaker"] == 1
    assert counters["runtime_started"] == 0  # zero-filled, never missing
    assert snapshot["data_provider_failures"] == 2
    assert snapshot["circuit_breaker_events"] == 1
