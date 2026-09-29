"""Alerting and runtime-metric contract tests.

* ``parayabuck.monitoring.alerts.emit`` never raises, delivers to the log
  sink, POSTs to a configured webhook, and throttles repeat failures;
* ``parayabuck.monitoring.metrics.runtime_metrics`` reads an empty or
  temp runtime ledger without raising and aggregates persisted state plus
  process-local counters.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def test_alerts_webhook_delivery() -> None:
    received: queue.Queue[dict[str, object]] = queue.Queue()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server convention)
            length = int(self.headers.get("Content-Length", "0"))
            received.put(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        alerts.configure_alerting(
            webhook_url=f"http://127.0.0.1:{server.server_address[1]}/alerts",
            throttle_seconds=0.0,
        )
        alerts.emit(
            "data_provider_unavailable",
            message="scanner down",
            market="equity",
            attempt=1,
        )
        payload = received.get(timeout=5.0)
        assert payload["kind"] == "data_provider_unavailable"
        assert payload["message"] == "scanner down"
        assert payload["fields"] == {"market": "equity", "attempt": 1}
        assert payload["occurred_at"]
        assert alerts.alert_stats()["webhook_sent"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_alerts_throttle_coalesces_repeat_failures() -> None:
    hits: list[dict[str, object]] = []
    sent = queue.Queue()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            sent.put(payload)
            hits.append(payload)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        alerts.configure_alerting(
            webhook_url=f"http://127.0.0.1:{server.server_address[1]}/alerts",
            throttle_seconds=60.0,
        )
        alerts.emit("reconciliation_failure", message="first")
        alerts.emit("reconciliation_failure", message="second, throttled")
        delivered = sent.get(timeout=5.0)
        assert delivered["kind"] == "reconciliation_failure"
        assert delivered["message"] == "first"
        assert delivered["fields"] == {}
        assert len(hits) == 1
        assert alerts.alert_stats()["suppressed"] == 1
        assert alerts.alert_stats()["webhook_sent"] == 1
        # A different kind is NOT throttled by the same window.
        alerts.emit("data_provider_unavailable", message="other kind")
        assert sent.get(timeout=5.0)["kind"] == "data_provider_unavailable"
    finally:
        server.shutdown()
        server.server_close()


def test_alerts_webhook_event_filtering_and_disable() -> None:
    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            hits.append(json.loads(self.rfile.read(length))["kind"])
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def _await_hits(count: int, timeout: float = 5.0) -> None:
        deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        while len(hits) < count and datetime.now(UTC) < deadline:
            threading.Event().wait(0.05)
        assert len(hits) >= count

    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/alerts"
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0)
        alerts.emit("runtime_started", message="started")
        _await_hits(1)
        assert hits == ["runtime_started"]
        # Restrict the webhook event set; console/log sink still receives all.
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0, events=("runtime_started",))
        alerts.emit("data_provider_unavailable", message="not delivered to webhook")
        assert hits == ["runtime_started"]
        # Disable the webhook entirely.
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0, enabled=False)
        alerts.emit("runtime_started", message="started again")
        assert hits == ["runtime_started"]
    finally:
        server.shutdown()
        server.server_close()


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
                consecutive_losses=2,
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
