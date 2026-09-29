"""Focused regression tests for the monitoring-layer defect fixes.

Each test pins one operator-visible invariant: the execution check reads the
ledger, the alert check counts undelivered webhooks, the journal drains on
stop and restarts, receipt lookup matches identity fields, bundle sections
stay parseable JSON, redaction reaches nested/decoded values and a supplied
event_id reaches both alert sinks. All offline (tmp_path, no network).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from parayabuck.monitoring import alerts, health
from parayabuck.monitoring.diagnostic import create_bundle_zip
from parayabuck.monitoring.forensics import (
    ambiguous_orders,
    decision_reconstruction,
    map_no_trade_reason,
    submitting_orders,
    unresolved_orders,
)
from parayabuck.monitoring.health import HealthStatus, _check_alerts, _check_execution
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import OperationalEvent, create_event

# ---------------------------------------------------------------------------
# Execution check — the order ledger is the only source of stuck/ambiguous orders
# ---------------------------------------------------------------------------


def _order_ledger(path: Path, orders: list[tuple[str, str, datetime]]) -> None:
    from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
    from parayabuck.storage import SQLiteStore

    store = SQLiteStore(str(path))
    repo = PortfolioStateRepository(str(path), store=store)
    session_id = repo.begin_session("acct", "digest", "paper")
    connection = store.connection
    with connection:
        connection.execute(
            "INSERT INTO portfolio_decisions (decision_id, session_id, account_id, configuration_digest, "
            "decision_timestamp, market, strategy_name, target_json, risk_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dec1", session_id, "acct", "digest", datetime.now(UTC).isoformat(), "equity", "test", "{}", "{}"),
        )
        for client_order_id, status, created_at in orders:
            connection.execute(
                "INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity, "
                "reference_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (client_order_id, "dec1", "AAPL", "equity", "BUY", 1, 1.0, status, created_at.isoformat()),
            )


def test_execution_check_flags_ambiguous_orders(tmp_path: Path) -> None:
    db = tmp_path / "ambiguous.sqlite3"
    _order_ledger(db, [("ord-1", "ambiguous", datetime.now(UTC))])

    check = _check_execution(str(db))

    assert check.status == HealthStatus.CRITICAL
    assert check.summary == "1 ambiguous orders require manual intervention"


def test_execution_check_flags_stale_submitting_orders(tmp_path: Path) -> None:
    db = tmp_path / "submitting.sqlite3"
    _order_ledger(db, [("ord-1", "submitting", datetime.now(UTC) - timedelta(minutes=20))])

    check = _check_execution(str(db))

    assert check.status == HealthStatus.DEGRADED
    assert check.summary == "1 stuck submitting orders >10m"


def test_execution_check_ok_for_fresh_submitting_order(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite3"
    _order_ledger(db, [("ord-1", "submitting", datetime.now(UTC))])

    check = _check_execution(str(db))

    assert check.status == HealthStatus.OK
    assert check.summary == "no stuck/ambiguous orders"


def test_forensic_readers_split_unresolved_orders(tmp_path: Path) -> None:
    db = tmp_path / "readers.sqlite3"
    _order_ledger(
        db,
        [
            ("ord-1", "ambiguous", datetime.now(UTC)),
            ("ord-2", "submitting", datetime.now(UTC)),
            ("ord-3", "filled", datetime.now(UTC)),
        ],
    )

    assert [o["client_order_id"] for o in ambiguous_orders(str(db))] == ["ord-1"]
    assert [o["client_order_id"] for o in submitting_orders(str(db))] == ["ord-2"]
    assert sorted(o["client_order_id"] for o in unresolved_orders(str(db))) == ["ord-1", "ord-2"]
    assert len(unresolved_orders(str(db), include_terminal=True)) == 3


@pytest.mark.parametrize(
    ("reader", "statuses"),
    [(ambiguous_orders, ["Ambiguous", "AmbigUOUS"]), (submitting_orders, ["SUBMITTING", "SubmITTing"])],
)
def test_status_readers_keep_rows_written_in_any_casing(
    tmp_path: Path, reader: Any, statuses: list[str]
) -> None:
    db = tmp_path / "casing.sqlite3"
    _order_ledger(db, [(f"ord-{index}", status, datetime.now(UTC)) for index, status in enumerate(statuses)])

    assert sorted(o["client_order_id"] for o in reader(str(db))) == [f"ord-{i}" for i in range(len(statuses))]


# ---------------------------------------------------------------------------
# Alert check — dropped means undelivered webhooks, not throttled repeats
# ---------------------------------------------------------------------------


def test_alert_check_reports_webhook_drops_only(monkeypatch: pytest.MonkeyPatch) -> None:
    stats: dict[str, int] = {
        "webhook_failed": 0,
        "webhook_attempted": 0,
        "webhook_dropped": 2,
        # Throttling is by design; it must not read as a delivery failure
        "suppressed": 7,
    }
    monkeypatch.setattr(alerts, "alert_stats", lambda: dict(stats))
    monkeypatch.setattr(health, "journal_stats", lambda: {"event_journal_dropped": 0})

    check = _check_alerts()

    assert check.status == HealthStatus.DEGRADED
    assert check.summary == "alerts dropped 2"

    monkeypatch.setattr(alerts, "alert_stats", lambda: {**stats, "webhook_dropped": 0})
    healthy = _check_alerts()
    assert healthy.status == HealthStatus.OK
    assert healthy.summary == "webhook OK"


# ---------------------------------------------------------------------------
# Journal — stop() drains instead of dropping, and a restart really restarts
# ---------------------------------------------------------------------------


def test_journal_stop_drains_queued_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = EventJournal(str(tmp_path / "drain.sqlite3"))
    parked = threading.Event()
    write_batch = journal._flush_batch

    def parked_flush(batch: list[OperationalEvent]) -> None:
        # Hold the writer until stop() has published the stop signal
        parked.set()
        assert journal._stop.wait(5.0)
        write_batch(batch)

    monkeypatch.setattr(journal, "_flush_batch", parked_flush)
    journal.start()
    journal.emit(create_event(kind="test", message="first"))
    assert parked.wait(5.0)
    for index in range(20):
        journal.emit(create_event(kind="test", message=f"queued-{index}"))

    journal.stop(timeout=5.0)

    assert journal._queue.qsize() == 0
    assert journal.stats()["event_journal_written"] == 21
    assert journal.stats()["event_journal_dropped"] == 0


def test_journal_writes_again_after_stop(tmp_path: Path) -> None:
    journal = EventJournal(str(tmp_path / "restart.sqlite3"))
    journal.emit(create_event(kind="test", message="before stop"))
    journal.stop(timeout=5.0)

    journal.start()
    journal.emit(create_event(kind="test", message="after restart"))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and journal.stats()["event_journal_written"] < 2:
        time.sleep(0.01)

    assert journal.stats()["event_journal_written"] == 2
    journal.stop(timeout=5.0)


# ---------------------------------------------------------------------------
# Receipt lookup — identity fields, never the whole serialised blob
# ---------------------------------------------------------------------------


def _write_receipt(directory: Path, name: str, payload: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _receipt_dir() -> Path:
    """The directory the runtime writes receipts to (redirected to tmp by conftest)."""
    from parayabuck.monitoring import receipt as receipt_module

    directory = receipt_module.default_receipt_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_decision_reconstruction_ignores_receipt_that_only_mentions_the_id(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    db.touch()
    _write_receipt(
        _receipt_dir(),
        "2025-01-03_equity.v2.json",
        {"schema_version": 2, "decision_id": "dec-2", "note": "cross reference dec-1"},
    )

    recon = decision_reconstruction(str(db), "dec-1")

    assert "receipt_path" not in recon


def test_decision_reconstruction_finds_receipt_by_identity_field(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    db.touch()
    _write_receipt(_receipt_dir(), "v2.json", {"schema_version": 2, "decision_id": "dec-1"})

    recon = decision_reconstruction(str(db), "dec-1")

    assert recon["receipt"]["decision_id"] == "dec-1"


def test_decision_reconstruction_falls_back_for_legacy_receipt(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    db.touch()
    _write_receipt(_receipt_dir(), "v1.json", {"strategy_name": "dec-1 equity"})

    recon = decision_reconstruction(str(db), "dec-1")

    assert recon["receipt"]["strategy_name"] == "dec-1 equity"


@pytest.mark.parametrize(
    "canonical",
    [
        "NO_TARGET_CHANGE",
        "REBALANCE_BAND",
        "MIN_NOTIONAL",
        "NO_QUALIFIED_CANDIDATES",
        "RISK_BLOCKED",
        "ENTRY_LATCH",
        "ACCOUNT_STALE",
        "UNIVERSE_UNAVAILABLE",
        "SELL_PHASE_PENDING",
        "AMBIGUOUS_ORDER",
        "EXTERNAL_OWNERSHIP",
        "MARKET_DISABLED",
    ],
)
def test_no_trade_reason_maps_canonical_vocabulary(canonical: str) -> None:
    assert map_no_trade_reason(canonical) == canonical
    assert map_no_trade_reason(f"{canonical}: 3 holdings blocked") == canonical


# ---------------------------------------------------------------------------
# Event redaction — nested values and decoded events
# ---------------------------------------------------------------------------


def test_event_redaction_reaches_nested_and_decoded_values() -> None:
    ev = create_event(
        kind="data_provider_unavailable",
        message="refresh failed",
        fields={"order_counts": {"filled": 1}, "delivery": {"webhook_token": "cleartext"}, "tradingview_secret": "x"},
    )

    assert ev.fields["tradingview_secret"] == "***REDACTED***"
    assert ev.fields["order_counts"] == {"filled": 1}
    assert isinstance(ev.fields["delivery"], dict)
    assert ev.fields["delivery"]["webhook_token"] == "***REDACTED***"

    decoded = OperationalEvent.from_dict({**ev.to_dict(), "fields": {"webhook_token": "cleartext", "phase": "sell"}})
    assert decoded.fields["webhook_token"] == "***REDACTED***"
    assert decoded.fields["phase"] == "sell"


def test_journal_query_redacts_legacy_sensitive_keys(tmp_path: Path) -> None:
    """A pre-existing row whose key merely *contains* a secret token still decodes."""
    journal = EventJournal(str(tmp_path / "legacy.sqlite3"))
    journal.emit(create_event(kind="test", message="legacy row"))
    journal.stop(timeout=5.0)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE operational_events SET fields_json = ?",
            (json.dumps({"api_key_present": True, "phase": "sell"}),),
        )

    events = journal.query(limit=10)

    assert [e.message for e in events] == ["legacy row"]
    assert events[0].fields == {"api_key_present": "***REDACTED***", "phase": "sell"}


def test_journal_stop_timeout_keeps_writer_accountable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop that times out must not double count the event or orphan the writer."""
    journal = EventJournal(str(tmp_path / "timeout.sqlite3"))
    release = threading.Event()
    write_batch = journal._flush_batch

    def slow_flush(batch: list[OperationalEvent]) -> None:
        assert release.wait(5.0)
        write_batch(batch)

    monkeypatch.setattr(journal, "_flush_batch", slow_flush)
    journal.start()
    journal.emit(create_event(kind="test", message="slow write"))
    journal.stop(timeout=0.05)

    assert journal._started is True
    assert journal._writer_thread is not None
    assert journal._writer_thread.is_alive()
    assert journal.stats()["event_journal_dropped"] == 0

    release.set()
    assert journal._writer_thread.join(5.0) is None
    assert journal.stats()["event_journal_written"] == 1
    journal.start()
    assert journal._started is True
    assert journal._writer_thread is not None
    journal.stop(timeout=5.0)


# ---------------------------------------------------------------------------
# Alert identity — one event_id for the journal and the webhook payload
# ---------------------------------------------------------------------------


def test_supplied_event_id_reaches_both_sinks(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.monitoring import journal as journal_module

    journaled: list[OperationalEvent] = []
    posted: list[dict[str, object]] = []
    monkeypatch.setattr(journal_module, "emit_journal", lambda event: journaled.append(event))
    monkeypatch.setattr(alerts, "_enqueue_webhook", lambda url, payload: posted.append(payload))
    alerts.reset_for_tests()
    alerts.configure_alerting(
        enabled=True,
        webhook_url="http://127.0.0.1:1/unused",
        events=("paper_incident",),
        throttle_seconds=0.0,
    )

    alerts.emit("paper_incident", message="boom", category="PROVIDER_UNAVAILABLE", event_id="fixed-id")
    alerts.reset_for_tests()

    assert journaled[0].event_id == "fixed-id"
    assert posted[0]["event_id"] == "fixed-id"


# ---------------------------------------------------------------------------
# Diagnostic bundle — every section stays parseable JSON
# ---------------------------------------------------------------------------


def _read_bundle_text(tmp_path: Path, snapshot: dict[str, object], section: str) -> str:
    snapshot_path = tmp_path / "health_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    bundle_path = create_bundle_zip(
        tmp_path / "bundle.zip",
        config_path=tmp_path / "absent.yaml",
        snapshot_path=snapshot_path,
        journal_path=tmp_path / "absent_journal.sqlite3",
        decision_db_path=tmp_path / "absent_db.sqlite3",
    )
    with zipfile.ZipFile(bundle_path) as archive:
        return archive.read(f"{section}.json").decode("utf-8")


def _read_bundle_section(tmp_path: Path, snapshot: dict[str, object], section: str) -> dict[str, object]:
    parsed: dict[str, object] = json.loads(_read_bundle_text(tmp_path, snapshot, section))
    return parsed


def test_bundle_caps_rows_and_keeps_section_parseable(tmp_path: Path) -> None:
    snapshot = {
        "written_at": datetime.now(UTC).isoformat(),
        "rows": [{"index": index, "pad": "x" * 120} for index in range(5000)],
    }

    section = _read_bundle_section(tmp_path, snapshot, "runtime_snapshot")

    assert len(section["rows"]) == 2000


def test_bundle_marks_oversized_section_instead_of_corrupting_it(tmp_path: Path) -> None:
    snapshot = {"written_at": datetime.now(UTC).isoformat(), "blob": "x" * 1_200_000}

    section = _read_bundle_section(tmp_path, snapshot, "runtime_snapshot")

    assert section["truncated"] is True
    assert section["size_chars"] > 500_000


def test_bundle_bounds_section_measured_after_escaping(tmp_path: Path) -> None:
    # Quotes and newlines triple in size once the preview is re-escaped by json.dumps.
    snapshot = {"written_at": datetime.now(UTC).isoformat(), "blob": 'a"\n' * 400_000}

    text = _read_bundle_text(tmp_path, snapshot, "runtime_snapshot")

    assert len(text) <= 500_000
    assert json.loads(text)["truncated"] is True
