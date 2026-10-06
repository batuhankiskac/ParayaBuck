"""Regression tests for the second monitoring-layer defect pass.

Each test pins one invariant: health checks fail closed (UNKNOWN) on a missing
or unreadable ledger instead of reporting OK, a naive incident timestamp cannot
hide an active drawdown halt, one non-JSON field cannot sink a journal batch,
alert fields are redacted on every
sink, the throttle map stays bounded, and the doctor/bundle never fall back to a
cwd-relative ledger, and the ledger checks close what they open. All offline (tmp_path, no network).
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.monitoring import alerts, metrics
from parayabuck.monitoring.diagnostic import _run_doctor_checks, collect_bundle_data
from parayabuck.monitoring.health import HealthStatus, _check_execution, _check_risk
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent, create_event


def _garbage_ledger(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a sqlite database " * 64)
    return path


# ---------------------------------------------------------------------------
# Health — an unreadable or absent ledger is UNKNOWN, never OK
# ---------------------------------------------------------------------------


def test_execution_check_is_unknown_for_unreadable_ledger(tmp_path: Path) -> None:
    check = _check_execution(_garbage_ledger(tmp_path / "ledger.sqlite3"))

    assert check.status == HealthStatus.UNKNOWN


@pytest.mark.parametrize("kind", ["none", "missing", "unreadable"])
def test_risk_check_is_unknown_without_a_readable_ledger(tmp_path: Path, kind: str) -> None:
    db_path: Path | None = {
        "none": None,
        "missing": tmp_path / "absent.sqlite3",
        "unreadable": _garbage_ledger(tmp_path / "ledger.sqlite3"),
    }[kind]

    check = _check_risk(db_path, None)

    assert check.status == HealthStatus.UNKNOWN


def test_naive_incident_timestamp_keeps_drawdown_halt_visible(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db_path)
    with connection:
        connection.execute(
            "CREATE TABLE safety_incidents (id INTEGER PRIMARY KEY, account_id TEXT, reason TEXT, "
            "scope TEXT, activated_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO safety_incidents (account_id, reason, scope, activated_at, updated_at) "
            "VALUES ('acct', 'portfolio_drawdown_halt', 'global', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
    connection.close()

    snapshot = metrics.runtime_metrics(database_path=db_path)
    check = _check_risk(db_path, None)

    assert isinstance(snapshot["entry_block_duration_seconds"], float)
    assert check.status == HealthStatus.BLOCKED


# ---------------------------------------------------------------------------
# Journal — one non-JSON field must not lose the rest of its batch
# ---------------------------------------------------------------------------


def test_non_json_field_does_not_sink_journal_batch(tmp_path: Path) -> None:
    journal = EventJournal(str(tmp_path / "journal.sqlite3"))
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    batch: list[OperationalEvent] = [
        create_event(kind="runtime_started", message="before"),
        create_event(kind="runtime_started", message="odd", fields={"at": stamp}),
        create_event(kind="runtime_started", message="after"),
    ]

    journal._flush_batch(batch)

    assert journal.stats()["event_journal_written"] == 3
    assert journal.stats()["event_journal_failed"] == 0
    odd = [event for event in journal.query() if event.message == "odd"]
    assert odd[0].fields["at"] == str(stamp)


# ---------------------------------------------------------------------------
# Alerts — credentials never logged, fields redacted, throttle map bounded
# ---------------------------------------------------------------------------


def test_alert_fields_are_redacted_on_log_and_journal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from parayabuck.monitoring import journal as journal_module

    journaled: list[object] = []
    monkeypatch.setattr(journal_module, "emit_journal", lambda event: journaled.append(event))
    alerts.reset_for_tests()
    alerts.configure_alerting(throttle_seconds=0.0)

    with caplog.at_level(logging.INFO, logger="parayabuck.monitoring.alerts"):
        alerts.emit("paper_incident", message="boom", category="X", api_key_hint="leaky-value")
    alerts.reset_for_tests()

    assert "leaky-value" not in caplog.text
    assert "***REDACTED***" in caplog.text
    fields = journaled[0].fields
    assert fields["api_key_hint"] == "***REDACTED***"
    assert fields["category"] == "X"


def test_throttle_map_prunes_expired_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.monitoring import journal as journal_module

    monkeypatch.setattr(journal_module, "emit_journal", lambda event: True)
    monkeypatch.setattr(alerts, "_THROTTLE_KEYS_MAX", 3)
    alerts.reset_for_tests()
    alerts.configure_alerting(throttle_seconds=60.0)
    now = time.monotonic()
    alerts._last_emitted_at.update({f"old-{index}": now - 3600.0 for index in range(5)})
    alerts._last_emitted_at["recent"] = now

    alerts.emit("order_rejected", message="rejected", client_order_id="c-1")
    keys = set(alerts._last_emitted_at)
    alerts.reset_for_tests()

    assert not any(key.startswith("old-") for key in keys)
    assert {"recent", "order_rejected:c-1"} <= keys


# ---------------------------------------------------------------------------
# Doctor / bundle — no cwd-relative ledger when no path was supplied
# ---------------------------------------------------------------------------


def test_doctor_and_bundle_ignore_relative_ledger_without_explicit_path(tmp_path: Path) -> None:
    # A repo-shaped decoy the old fallback would have read and reported CRITICAL
    _garbage_ledger(tmp_path / "data" / "runtime" / "parayabuck.sqlite3")
    paths = {
        "config_path": tmp_path / "absent.yaml",
        "snapshot_path": tmp_path / "absent_snapshot.json",
        "journal_path": tmp_path / "absent_journal.sqlite3",
    }

    with contextlib.chdir(tmp_path):
        doctor = _run_doctor_checks(**paths, decision_db_path=None)
        bundle = collect_bundle_data(**paths, decision_db_path=None)

    decision_db = [check for check in doctor["checks"] if check["code"] == "decision_db"]
    assert decision_db[0]["status"] == "UNKNOWN"
    assert bundle["database_checks"] == {"integrity_check": "no_db"}


# ---------------------------------------------------------------------------
# G#5 — the health ledger checks close every connection they open
# ---------------------------------------------------------------------------


def test_health_ledger_checks_close_their_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db_path)
    with connection:
        connection.execute("CREATE TABLE portfolio_orders (client_order_id TEXT, status TEXT, created_at TEXT)")
        connection.execute(
            "INSERT INTO portfolio_orders VALUES ('c1', 'submitting', ?)",
            ((datetime.now(UTC) - timedelta(hours=1)).isoformat(),),
        )
        connection.execute("CREATE TABLE portfolio_rebalance_executions (phase TEXT, updated_at TEXT)")
        connection.execute(
            "CREATE TABLE safety_incidents (id INTEGER PRIMARY KEY, account_id TEXT, reason TEXT, "
            "scope TEXT, activated_at TEXT, updated_at TEXT)"
        )
    connection.close()

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(database, *args, **kwargs)  # type: ignore[call-overload]
        if str(tmp_path) in str(database):
            opened.append(conn)
        return conn  # type: ignore[no-any-return]

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    assert _check_execution(db_path).status == HealthStatus.DEGRADED
    assert _check_risk(db_path, None).status == HealthStatus.OK

    assert opened
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_bundle_skips_runtime_metrics_without_explicit_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def recording_metrics(database_path: object = None, **_: object) -> dict[str, object]:
        calls.append(database_path)
        return {"incidents": {"count": 0, "active": []}}

    monkeypatch.setattr(metrics, "runtime_metrics", recording_metrics)

    bundle = collect_bundle_data(
        config_path=tmp_path / "absent.yaml",
        snapshot_path=tmp_path / "absent_snapshot.json",
        journal_path=tmp_path / "absent_journal.sqlite3",
        decision_db_path=None,
    )

    assert calls == []
    assert bundle["runtime_metrics"] == {"status": "no_db"}
