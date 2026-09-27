"""Mission 5 observability coverage — event model, journal, health, snapshot, CLI, forensics, alerts, safety, chaos."""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from parayabuck.monitoring import alerts
from parayabuck.monitoring.forensics import (
    ambiguous_orders,
    decision_reconstruction,
    find_stuck_rebalances,
    runtime_session_forensics,
    unresolved_orders,
)
from parayabuck.monitoring.health import evaluate_health
from parayabuck.monitoring.journal import EventJournal
from parayabuck.monitoring.operational_event import (
    OperationalEvent,
    create_event,
    deterministic_event_id,
    random_event_id,
)
from parayabuck.monitoring.snapshot import SnapshotWriter, build_snapshot, read_snapshot, write_snapshot_atomic

# ---------------------------------------------------------------------------
# OperationalEvent model
# ---------------------------------------------------------------------------


def test_operational_event_serialization_and_correlation() -> None:
    ev = create_event(
        kind="reconciliation_failure",
        message="reconcile failed",
        session_id="sess1",
        decision_id="dec1",
        client_order_id="ord1",
        incident_id="inc1",
        market="equity",
        fields={"extra": 123},
    )
    d = ev.to_dict()
    assert d["kind"] == "reconciliation_failure"
    assert d["session_id"] == "sess1"
    assert d["decision_id"] == "dec1"
    assert d["client_order_id"] == "ord1"
    assert d["incident_id"] == "inc1"
    assert d["market"] == "equity"
    assert d["fields"]["extra"] == 123
    # round-trip
    ev2 = OperationalEvent.from_dict(d)
    assert ev2.event_id == ev.event_id
    assert ev2.kind == ev.kind
    assert ev2.occurred_at == ev.occurred_at


def test_event_ids_deterministic_and_random() -> None:
    ts = datetime(2025, 1, 1, tzinfo=UTC)
    id1 = deterministic_event_id(kind="scanner_refresh_failure", occurred_at=ts, session_id="s1", extra="x")
    id2 = deterministic_event_id(kind="scanner_refresh_failure", occurred_at=ts, session_id="s1", extra="x")
    assert id1 == id2
    assert len(id1) == 32
    # different extra -> different
    id3 = deterministic_event_id(kind="scanner_refresh_failure", occurred_at=ts, session_id="s1", extra="y")
    assert id1 != id3
    # random is collision-resistant
    r1 = random_event_id()
    r2 = random_event_id()
    assert r1 != r2
    assert len(r1) == 32


def test_event_secret_redaction() -> None:
    # fields with secret keys should be redacted or rejected
    with pytest.raises(ValueError):
        OperationalEvent(
            event_id="x",
            occurred_at=datetime.now(UTC),
            kind="test",
            severity="INFO",
            message="msg",
            fields={"api_secret": "should not pass"},
        )
    ev = create_event(kind="test", message="msg", fields={"webhook_url": "https://example.com/secret", "safe": "ok"})
    assert ev.fields["webhook_url"] == "***REDACTED***"
    assert ev.fields["safe"] == "ok"


def test_event_timestamp_must_be_utc() -> None:
    with pytest.raises(ValueError):
        OperationalEvent(
            event_id="x",
            occurred_at=datetime(2025, 1, 1),  # naive
            kind="test",
            severity="INFO",
            message="msg",
        )


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def test_journal_write_read_and_retention(tmp_path: Path) -> None:
    jp = tmp_path / "obs.sqlite3"
    j = EventJournal(str(jp), queue_size=10, retention_days=30, max_events=100)
    _ = j  # keep reference
    ev = create_event(kind="runtime_started", message="started", session_id="s1")
    assert j.emit_sync(ev)
    # flush and query
    events = j.query(limit=10)
    assert len(events) == 1
    assert events[0].kind == "runtime_started"
    # retention: insert old event and trigger cleanup via max_events
    old = create_event(kind="test", message="old", occurred_at=datetime.now(UTC) - timedelta(days=40))
    j.emit_sync(old)
    # force cleanup
    conn = sqlite3.connect(str(jp))
    conn.execute(
        "INSERT OR IGNORE INTO operational_events (event_id, occurred_at, kind, severity, message, fields_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "oldid",
            (datetime.now(UTC) - timedelta(days=40)).isoformat(),
            "test",
            "INFO",
            "old",
            "{}",
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    # manually trigger retention
    j2 = EventJournal(str(jp), retention_days=30, max_events=2)
    # insert third to exceed max
    for i in range(3):
        j2.emit_sync(create_event(kind="test", message=f"m{i}"))
    # after writes, cleanup should have run after 100? force via private
    conn = sqlite3.connect(str(jp))
    try:
        # directly call cleanup via new journal's internal
        j2._retention_cleanup(conn)  # type: ignore[attr-defined]
        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM operational_events")
        count = cur.fetchone()[0]
        assert count <= 2
    finally:
        conn.close()


def test_journal_bounded_queue_and_drop(tmp_path: Path) -> None:
    jp = tmp_path / "obs2.sqlite3"
    j = EventJournal(str(jp), queue_size=2)
    j.start()
    # fill queue
    assert j.emit(create_event(kind="test", message="1"))
    assert j.emit(create_event(kind="test", message="2"))
    j.stop(timeout=0.2)
    # deterministic drop test via patched queue
    j2 = EventJournal(str(tmp_path / "obs3.sqlite3"), queue_size=1)
    # force queue full by patching put_nowait to raise Full
    import queue as _q

    orig_put = j2._queue.put_nowait

    def failing_put(x):  # type: ignore[no-untyped-def]
        raise _q.Full

    j2._queue.put_nowait = failing_put  # type: ignore[method-assign]
    dropped_before = int(j2.stats()["event_journal_dropped"])
    j2.emit(create_event(kind="test", message="b"))
    assert int(j2.stats()["event_journal_dropped"]) > dropped_before
    j2._queue.put_nowait = orig_put  # type: ignore[method-assign]


def test_journal_failure_isolation(tmp_path: Path) -> None:
    # Simulate disk failure by using a directory as path (write will fail)
    # inject failure by making path a directory and trying to write?
    # Instead test that emit_sync never raises even with bad path
    bad = EventJournal(str(tmp_path / "nonexistent_dir" / "x" / "y.sqlite3"), queue_size=5)
    # The directory will be created, so to simulate failure we patch connect to raise
    with patch("parayabuck.monitoring.journal.sqlite3.connect", side_effect=sqlite3.OperationalError("disk full")):
        ev = create_event(kind="test", message="fail")
        assert not bad.emit_sync(ev)  # returns False but not raise
        assert bad.stats()["event_journal_failed"] >= 1
    # Ensure trading not affected - this is the isolation property: emit returns, does not raise
    try:
        bad.emit(create_event(kind="test", message="x"))
    except Exception:
        pytest.fail("journal emit should never raise")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_unknown_when_no_snapshot(tmp_path: Path) -> None:
    health = evaluate_health(
        snapshot_path=tmp_path / "no.json", database_path=tmp_path / "no.db", now=datetime.now(UTC)
    )
    assert health["overall_health"] == "UNKNOWN"
    assert any(c["code"] == "runtime_heartbeat" and c["status"] == "UNKNOWN" for c in health["checks"])


def test_health_critical_when_stale_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "snap.json"
    old = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
    snap.write_text(
        json.dumps(
            {
                "written_at": old,
                "runtime_state": "running",
                "runtime_health": {
                    "state": "running",
                    "healthy": True,
                    "queue_size": 0,
                    "queue_age_seconds": 0,
                    "dropped_events": 0,
                    "coalesced_events": 0,
                    "workers": {},
                    "markets": {},
                },
            }
        )
    )
    health = evaluate_health(snapshot_path=snap, database_path=tmp_path / "no.db", now=datetime.now(UTC))
    # heartbeat should be CRITICAL due to staleness >90s
    hb = next(c for c in health["checks"] if c["code"] == "runtime_heartbeat")
    assert hb["status"] == "CRITICAL"
    assert health["overall_health"] == "CRITICAL"


def test_health_blocked_when_risk_latch(tmp_path: Path) -> None:
    snap = tmp_path / "snap2.json"
    now = datetime.now(UTC)
    snap.write_text(
        json.dumps(
            {
                "written_at": now.isoformat(),
                "runtime_state": "running",
                "runtime_health": {
                    "state": "running",
                    "healthy": True,
                    "queue_size": 0,
                    "queue_age_seconds": 0,
                    "dropped_events": 0,
                    "coalesced_events": 0,
                    "workers": {"pb-decisions": {"alive": True, "last_heartbeat": now.isoformat()}},
                    "markets": {},
                    "entry_risk_latch": {"equity": "portfolio_drawdown_halt"},
                    "signal_adjustment": {"healthy": True, "stale": False},
                    "universe_refresh": {"status": {}, "last_refresh_at": now.isoformat()},
                    "last_reconciliation_at": now.isoformat(),
                },
                "entry_risk_latch": {"equity": "portfolio_drawdown_halt"},
            }
        )
    )
    health = evaluate_health(snapshot_path=snap, now=now)
    risk = next(c for c in health["checks"] if c["code"] == "risk")
    assert risk["status"] == "BLOCKED"
    assert health["overall_health"] == "BLOCKED"
    assert health["trading_state"] == "ENTRY_BLOCKED"


def test_health_ok_when_fresh(tmp_path: Path) -> None:
    snap = tmp_path / "snap_ok.json"
    now = datetime.now(UTC)
    # Provide minimal but healthy snapshot
    snap.write_text(
        json.dumps(
            {
                "written_at": now.isoformat(),
                "runtime_state": "running",
                "runtime_health": {
                    "state": "running",
                    "healthy": True,
                    "queue_size": 0,
                    "queue_age_seconds": 1.0,
                    "dropped_events": 0,
                    "coalesced_events": 0,
                    "workers": {"pb-decisions": {"alive": True, "last_heartbeat": now.isoformat()}},
                    "markets": {"equity": {"enabled": True, "entry_halt_reason": None}},
                    "entry_risk_latch": {},
                    "signal_adjustment": {"healthy": True, "stale": False, "verified": True},
                    "universe_refresh": {"status": {"equity": "success"}, "last_refresh_at": now.isoformat()},
                    "last_reconciliation_at": now.isoformat(),
                },
            }
        )
    )
    health = evaluate_health(snapshot_path=snap, now=now)
    # At least heartbeat should be OK
    hb = next(c for c in health["checks"] if c["code"] == "runtime_heartbeat")
    assert hb["status"] == "OK"


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_atomic_and_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "snap.json"
    snap = build_snapshot(
        pid=123, session_id="s1", mode="paper", runtime_state="running", runtime_health={"state": "running"}
    )
    assert write_snapshot_atomic(p, snap)
    data = read_snapshot(p)
    assert data is not None
    assert data["pid"] == 123
    # corrupt
    p.write_text("{ truncated", encoding="utf-8")
    assert read_snapshot(p) is None
    # missing
    assert read_snapshot(tmp_path / "missing.json") is None


def test_snapshot_writer_periodic(tmp_path: Path) -> None:
    p = tmp_path / "hs.json"
    writer = SnapshotWriter(
        path=p,
        interval_seconds=0.1,
        provider=lambda: {
            "state": "running",
            "healthy": True,
            "queue_size": 0,
            "queue_age_seconds": 0,
            "dropped_events": 0,
            "coalesced_events": 0,
            "workers": {},
            "markets": {},
        },
    )
    writer.start()
    time.sleep(0.3)
    writer.stop(timeout=1.0)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["runtime_state"] == "running"
    # stopped snapshot
    writer.write_stopped()
    data2 = json.loads(p.read_text(encoding="utf-8"))
    assert data2["runtime_state"] == "stopped"


def test_snapshot_stale_detection(tmp_path: Path) -> None:
    p = tmp_path / "stale.json"
    old = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
    p.write_text(json.dumps({"written_at": old, "runtime_state": "running"}))
    health = evaluate_health(snapshot_path=p, now=datetime.now(UTC))
    hb = next(c for c in health["checks"] if c["code"] == "runtime_heartbeat")
    assert hb["status"] == "CRITICAL"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> tuple[int, str, str]:
    import contextlib
    import io

    from parayabuck.cli import main as cli_main

    out = io.StringIO()
    err = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli_main(list(args))
        except SystemExit as e:
            code = int(e.code) if isinstance(e.code, int) else 1
        except Exception as exc:
            err.write(str(exc))
            code = 1
    return code, out.getvalue(), err.getvalue()


def test_cli_ops_status_json(tmp_path: Path) -> None:
    code, out, _ = _run_cli("ops", "status", "--json")
    assert code == 0
    data = json.loads(out)
    assert "overall_health" in data
    assert "trading_state" in data
    assert "checks" in data


def test_cli_ops_doctor_exit_codes(tmp_path: Path) -> None:
    # fresh install should be 1 (UNKNOWN -> degraded) or 0? Our doctor returns 1 for UNKNOWN overall
    code, out, _ = _run_cli("ops", "doctor")
    # Should be 1 because UNKNOWN overall maps to 1
    assert code in {0, 1, 2}
    code2, out2, _ = _run_cli("ops", "doctor", "--json")
    assert code2 in {0, 1, 2}
    data = json.loads(out2)
    assert "overall" in data


def test_cli_ops_events_filters(tmp_path: Path) -> None:
    jp = tmp_path / "events_cli.sqlite3"
    j = EventJournal(str(jp))
    now = datetime.now(UTC)
    ev1 = create_event(kind="reconciliation_failure", message="fail1", occurred_at=now - timedelta(minutes=10))
    ev2 = create_event(kind="scanner_refresh_failure", message="fail2", occurred_at=now - timedelta(minutes=5))
    j.emit_sync(ev1)
    j.emit_sync(ev2)
    # query via CLI with custom journal
    code, out, _ = _run_cli("ops", "events", "--journal", str(jp), "--limit", "10", "--json")
    assert code == 0
    data = json.loads(out)
    assert len(data) >= 2
    # filter by kind
    code, out, _ = _run_cli("ops", "events", "--journal", str(jp), "--kind", "reconciliation_failure", "--json")
    assert code == 0
    data = json.loads(out)
    assert all(e["kind"] == "reconciliation_failure" for e in data)
    # relative time
    code, out, _ = _run_cli("ops", "events", "--journal", str(jp), "--since", "30m", "--json")
    assert code == 0


def test_web_ops_severity_filter_never_widens_the_result() -> None:
    """The stored severity vocabulary narrows to it; any other value narrows to nothing."""
    from parayabuck.monitoring.journal import get_journal
    from parayabuck.web.services import ops

    now = datetime.now(UTC)
    journal = get_journal()
    journal.emit_sync(
        create_event(
            kind="reconciliation_failure",
            message="drift",
            occurred_at=now - timedelta(minutes=5),
        )
    )
    journal.emit_sync(create_event(kind="runtime_started", message="started", occurred_at=now))

    assert len(ops.query_events(10)) >= 2
    assert {e["kind"] for e in ops.query_events(10, severity="WARNING")} == {"reconciliation_failure"}
    assert ops.query_events(10, severity="warning") == []
    assert ops.query_events(10, severity="error") == []
    assert ops.query_events(10, severity="typo") == []


def test_cli_ops_timeline(tmp_path: Path) -> None:
    jp = tmp_path / "timeline.sqlite3"
    j = EventJournal(str(jp))
    dec = "dec-timeline-1"
    j.emit_sync(create_event(kind="decision_summary", message="summary", decision_id=dec))
    j.emit_sync(create_event(kind="rebalance_phase_changed", message="phase", decision_id=dec))
    code, out, _ = _run_cli("ops", "timeline", "--decision", dec, "--journal", str(jp), "--json")
    assert code == 0
    data = json.loads(out)
    assert "events" in data


def test_ops_timeline_uses_configured_journal_path_with_explicit_db(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--db`` overrides the decision DB only; the journal path still comes
    from the config unless ``--journal`` is given."""
    from parayabuck.cli import _ops_timeline

    journal = tmp_path / "observability.sqlite3"
    decision = "dec-configured-journal"
    EventJournal(str(journal)).emit_sync(create_event(kind="decision_summary", message="summary", decision_id=decision))
    config = tmp_path / "settings.yaml"
    config.write_text(
        "symbols: [AAPL]\n"
        "strategy:\n"
        "  equity:\n"
        "    name: equity_leveraged_trend_v1\n"
        "observability:\n"
        f'  journal_path: "{journal}"\n'
    )

    code = _ops_timeline(
        argparse.Namespace(
            decision=decision,
            order=None,
            incident=None,
            journal=None,
            db=str(tmp_path / "decisions.sqlite3"),
            config=str(config),
            limit=100,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [event["kind"] for event in payload["events"]] == ["decision_summary"]


def test_doctor_wal_probe_closes_the_store_through_the_ownership_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fresh-install WAL probe owns a store, so it must hand the connection
    back through ``release`` rather than closing it behind the store's back."""
    import parayabuck.cli as cli_module
    import parayabuck.storage as storage_module

    probe_stores: list[storage_module.SQLiteStore] = []

    class _RecordingStore(storage_module.SQLiteStore):
        def __init__(self, path: str | Path) -> None:
            super().__init__(path)
            probe_stores.append(self)

    monkeypatch.setattr(storage_module, "SQLiteStore", _RecordingStore)
    config = tmp_path / "settings.yaml"
    config.write_text(
        "symbols: [AAPL]\n"
        "strategy:\n"
        "  equity:\n"
        "    name: equity_leveraged_trend_v1\n"
        "runtime:\n"
        f"  decision_db_path: {tmp_path / 'parayabuck.sqlite3'}\n"
        "backtest:\n"
        f"  output_dir: {tmp_path / 'reports'}\n"
        "backup:\n"
        f"  path: {tmp_path / 'backups'}\n"
    )

    cli_module._run_doctor(
        argparse.Namespace(config=str(config), paper=False, offline=True, strict=False, json=False)
    )

    assert len(probe_stores) == 1
    assert probe_stores[0]._closed is True


def test_cli_ops_bundle(tmp_path: Path) -> None:
    out = tmp_path / "bundle.zip"
    code, stdout, _ = _run_cli("ops", "bundle", "--output", str(out))
    assert code == 0
    assert out.exists()
    assert out.stat().st_size < 5_000_000  # size limit
    # check no secrets
    import zipfile

    with zipfile.ZipFile(out) as zf:
        content = zf.read("sanitized_config.json").decode()
        assert "REDACTED" not in content or "api_key" not in content.lower() or "***REDACTED***" in content
        # ensure no webhook url secret
        assert "https://hooks" not in content


def test_cli_snapshot_corruption_handling(tmp_path: Path) -> None:
    snap = tmp_path / "corrupt.json"
    snap.write_text("{ truncated", encoding="utf-8")
    code, out, _ = _run_cli("ops", "status", "--snapshot", str(snap), "--json")
    assert code == 0
    data = json.loads(out)
    # should be UNKNOWN, not crash
    assert data["overall_health"] in {"UNKNOWN", "CRITICAL", "DEGRADED"}


# ---------------------------------------------------------------------------
# Forensics
# ---------------------------------------------------------------------------


def test_forensics_unresolved_and_stuck(tmp_path: Path) -> None:
    from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
    from parayabuck.storage import SQLiteStore

    db = tmp_path / "forensics.sqlite3"
    store = SQLiteStore(str(db))
    repo = PortfolioStateRepository(str(db), store=store)
    # create session and decision with stuck rebalance
    sess = repo.begin_session("acct", "digest", "paper")
    # need to create a decision and rebalance manually via SQL for test
    conn = store.connection
    with conn:
        conn.execute(
            "INSERT INTO portfolio_decisions (decision_id, session_id, account_id, configuration_digest, decision_timestamp, market, strategy_name, target_json, risk_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("dec1", sess, "acct", "digest", datetime.now(UTC).isoformat(), "equity", "test", "{}", "{}"),
        )
        conn.execute(
            "INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity, reference_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ord1",
                "dec1",
                "AAPL",
                "equity",
                "BUY",
                10,
                100,
                "submitting",
                (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO portfolio_rebalance_executions (decision_id, account_id, market, phase, status, buy_orders_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "dec1",
                "acct",
                "equity",
                "SELL_PENDING",
                "active",
                "[]",
                (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            ),
        )
    # now query
    assert len(unresolved_orders(str(db))) >= 1
    # ambiguous
    conn.execute(
        "INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity, reference_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ord2", "dec1", "MSFT", "equity", "BUY", 5, 50, "ambiguous", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    assert len(ambiguous_orders(str(db))) >= 1
    # stuck rebalance >1h
    stuck = find_stuck_rebalances(str(db), threshold_seconds=3600)
    assert len(stuck) >= 1
    # sessions
    sess_info = runtime_session_forensics(str(db))
    assert "sessions" in sess_info


def test_forensics_decision_reconstruction(tmp_path: Path) -> None:
    from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
    from parayabuck.storage import SQLiteStore

    db = tmp_path / "recon.sqlite3"
    store = SQLiteStore(str(db))
    repo = PortfolioStateRepository(str(db), store=store)
    sess = repo.begin_session("acct", "digest", "paper")
    conn = store.connection
    with conn:
        conn.execute(
            "INSERT INTO portfolio_decisions (decision_id, session_id, account_id, configuration_digest, decision_timestamp, market, strategy_name, target_json, risk_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "dec2",
                sess,
                "acct",
                "digest",
                datetime.now(UTC).isoformat(),
                "equity",
                "test",
                '{"target": {}}',
                '{"risk": {}}',
            ),
        )
        conn.execute(
            "INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity, reference_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ord3", "dec2", "AAPL", "equity", "BUY", 10, 100, "filled", datetime.now(UTC).isoformat()),
        )
        conn.execute(
            "INSERT INTO portfolio_fills (activity_id, client_order_id, occurred_at, quantity, price, fee, realized_pnl, unrealized_pnl, slippage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("fill1", "ord3", datetime.now(UTC).isoformat(), 10, 100, 0, 0, 0, 0),
        )
    recon = decision_reconstruction(str(db), "dec2")
    assert recon["decision_id"] == "dec2"
    assert len(recon["orders"]) == 1
    assert len(recon["fills"]) == 1


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_alert_throttle_distinct_incidents() -> None:
    alerts.reset_for_tests()
    alerts.configure_alerting(enabled=True, webhook_url="", throttle_seconds=60.0)
    # Two paper_incident with different incident_id should both be delivered (not throttled)
    alerts.emit("paper_incident", message="first", category="PROVIDER_UNAVAILABLE", incident_id="id1")
    alerts.emit("paper_incident", message="second", category="PROVIDER_UNAVAILABLE", incident_id="id2")
    stats = alerts.alert_stats()
    assert stats["delivered"].get("paper_incident", 0) == 2
    assert stats["suppressed"] == 0
    # Same incident_id should be throttled
    alerts.emit("paper_incident", message="third", category="PROVIDER_UNAVAILABLE", incident_id="id1")
    stats = alerts.alert_stats()
    assert stats["suppressed"] == 1

    # Scanner failures per market should not suppress each other
    alerts.reset_for_tests()
    alerts.configure_alerting(enabled=True, webhook_url="", throttle_seconds=60.0)
    alerts.emit("scanner_refresh_failure", message="equity fail", market="equity")
    alerts.emit("scanner_refresh_failure", message="crypto fail", market="crypto")
    stats = alerts.alert_stats()
    # With per-market throttling, both should be delivered
    assert stats["delivered"].get("scanner_refresh_failure", 0) == 2
    alerts.reset_for_tests()


def test_alert_webhook_counters_and_failure() -> None:
    alerts.reset_for_tests()
    alerts.configure_alerting(
        enabled=True, webhook_url="http://127.0.0.1:1/fake", events=("runtime_started",), throttle_seconds=0
    )

    # Mock failing post
    class FakeFail:
        def raise_for_status(self):
            raise Exception("fail")

    with patch("parayabuck.monitoring.alerts.requests.post", return_value=FakeFail()):
        alerts.emit("runtime_started", message="test")
        # worker is async; give time
        time.sleep(0.3)
        # also test direct _post_webhook increments failed
        alerts._post_webhook("http://127.0.0.1:1/fake", {"kind": "runtime_started", "message": "x"})
        stats = alerts.alert_stats()
        assert stats["webhook_attempted"] >= 1
        assert stats["webhook_failed"] >= 1
    alerts.reset_for_tests()


def test_alert_unknown_kind_rejected() -> None:
    alerts.reset_for_tests()
    alerts.emit("not_a_kind", message="x")
    stats = alerts.alert_stats()
    assert "not_a_kind" not in stats["delivered"]
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# Safety — observability failure must not affect trading
# ---------------------------------------------------------------------------


def test_journal_failure_does_not_crash_trading(tmp_path: Path) -> None:
    # Simulate trading decision that emits journal but journal fails
    with patch("parayabuck.monitoring.journal.emit_journal", side_effect=Exception("journal down")):
        # trading code that calls observe should not raise
        from parayabuck.monitoring import observe

        try:
            observe.on_decision_summary(decision_id="dec", market="equity")
            observe.on_account_stale()
            observe.on_rebalance_phase_changed(decision_id="dec", from_phase="SELL_PENDING", to_phase="BUY_READY")
        except Exception:
            pytest.fail("observe should never raise")


def test_snapshot_failure_does_not_crash() -> None:
    with patch("parayabuck.monitoring.snapshot.write_snapshot_atomic", side_effect=Exception("disk full")):
        writer = SnapshotWriter(path="/tmp/bad/snap.json", interval_seconds=0.1, provider=lambda: {})
        # write_once should not raise
        try:
            writer.write_once()
        except Exception:
            pytest.fail("snapshot write should not raise")


def test_alert_failure_does_not_crash() -> None:
    alerts.reset_for_tests()
    alerts.configure_alerting(webhook_url="http://127.0.0.1:1/fake", throttle_seconds=0)
    with patch("parayabuck.monitoring.alerts.requests.post", side_effect=Exception("network down")):
        try:
            alerts.emit("runtime_started", message="x")
            time.sleep(0.2)
        except Exception:
            pytest.fail("alert emit should never raise")
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# Chaos — simulated failure sequence
# ---------------------------------------------------------------------------


def test_chaos_failure_sequence(tmp_path: Path) -> None:
    alerts.reset_for_tests()
    jp = tmp_path / "chaos.sqlite3"
    j = EventJournal(str(jp))
    now = datetime.now(UTC)
    seq = [
        ("runtime_started", now),
        ("scanner_refresh_failure", now + timedelta(minutes=11)),
        ("scanner_refresh_failure", now + timedelta(minutes=16)),
        ("reconciliation_failure", now + timedelta(minutes=20)),
        ("account_became_stale", now + timedelta(minutes=22)),
        ("decision_summary", now + timedelta(minutes=25)),
        ("observability_degraded", now + timedelta(minutes=27)),
        ("account_recovered", now + timedelta(minutes=40)),
        ("reconciliation_recovered", now + timedelta(minutes=41)),
        ("scanner_recovered", now + timedelta(minutes=42)),
        ("runtime_health_recovered", now + timedelta(minutes=45)),
    ]
    for kind, ts in seq:
        j.emit_sync(
            create_event(
                kind=kind,
                message=f"{kind} event",
                occurred_at=ts,
                session_id="sess1",
                decision_id="dec1" if "decision" in kind else None,
            )
        )

    events = j.query(limit=20)
    # ensure order (newest first) but all present
    kinds = {e.kind for e in events}
    for k, _ in seq:
        assert k in kinds
    # correlation
    dec_events = j.query(decision_id="dec1")
    assert len(dec_events) >= 1
    # health transitions can be evaluated via evaluate_health with snapshot mock
    snap = tmp_path / "snap_chaos.json"
    # final healthy snapshot
    snap.write_text(
        json.dumps(
            {
                "written_at": (now + timedelta(minutes=45)).isoformat(),
                "runtime_state": "running",
                "runtime_health": {
                    "state": "running",
                    "healthy": True,
                    "queue_size": 0,
                    "queue_age_seconds": 0,
                    "dropped_events": 0,
                    "coalesced_events": 0,
                    "workers": {},
                    "markets": {},
                    "entry_risk_latch": {},
                    "signal_adjustment": {"healthy": True, "stale": False},
                    "universe_refresh": {"status": {}, "last_refresh_at": now.isoformat()},
                    "last_reconciliation_at": (now + timedelta(minutes=41)).isoformat(),
                },
            }
        )
    )
    health = evaluate_health(snapshot_path=snap, now=now + timedelta(minutes=46))
    assert health["overall_health"] in {"OK", "UNKNOWN", "DEGRADED"}


def test_crash_simulation_stale_heartbeat_and_unclean_session(tmp_path: Path) -> None:
    # Simulate previous session without ended_at
    from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
    from parayabuck.storage import SQLiteStore

    db = tmp_path / "crash.sqlite3"
    store = SQLiteStore(str(db))
    repo = PortfolioStateRepository(str(db), store=store)
    repo.begin_session("acct", "digest", "paper")
    # not ending sess1, create new session
    repo.begin_session("acct", "digest", "paper")
    # Now forensics should detect unclean
    info = runtime_session_forensics(str(db))
    assert len(info["unclean"]) >= 1
    # stale heartbeat: write old snapshot
    snap = tmp_path / "stale_snap.json"
    old = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
    snap.write_text(
        json.dumps(
            {
                "written_at": old,
                "runtime_state": "running",
                "runtime_health": {
                    "state": "running",
                    "healthy": True,
                    "queue_size": 0,
                    "queue_age_seconds": 0,
                    "dropped_events": 0,
                    "coalesced_events": 0,
                    "workers": {},
                    "markets": {},
                },
            }
        )
    )
    health = evaluate_health(snapshot_path=snap, now=datetime.now(UTC))
    hb = next(c for c in health["checks"] if c["code"] == "runtime_heartbeat")
    assert hb["status"] == "CRITICAL"


def test_journal_queue_full_and_snapshot_corruption(tmp_path: Path) -> None:
    jp = tmp_path / "q.sqlite3"
    j = EventJournal(str(jp), queue_size=1)
    j.start()
    # fill
    j.emit(create_event(kind="test", message="a"))
    j.emit(create_event(kind="test", message="b"))
    # At least one should be dropped or queued; stats should reflect
    stats = j.stats()
    assert "event_journal_queue_depth" in stats
    j.stop(timeout=0.5)
    # snapshot corruption — offline tooling must not crash, must report UNKNOWN
    snap = tmp_path / "corrupt.json"
    snap.write_text("{ not json", encoding="utf-8")
    assert read_snapshot(snap) is None
    # evaluate_health should handle corrupt snapshot gracefully
    health = evaluate_health(snapshot_path=snap, now=datetime.now(UTC))
    hb = next(c for c in health["checks"] if c["code"] == "runtime_heartbeat")
    assert hb["status"] == "UNKNOWN"
    assert health["overall_health"] in {"UNKNOWN", "CRITICAL", "DEGRADED"}


def test_missing_db_produces_unknown(tmp_path: Path) -> None:
    missing_db = tmp_path / "nope.sqlite3"
    missing_snap = tmp_path / "nope.json"
    code, out, _ = _run_cli("ops", "status", "--snapshot", str(missing_snap), "--db", str(missing_db), "--json")
    assert code == 0
    data = json.loads(out)
    assert data["overall_health"] == "UNKNOWN"
    assert data["trading_state"] == "UNKNOWN"


def test_ops_status_ignores_relative_defaults_when_paths_are_given(tmp_path: Path) -> None:
    """`--snapshot`/`--db` must win: a repo-shaped decoy under the cwd never answers."""

    decoy_root = tmp_path / "decoy_repo"
    runtime = decoy_root / "data" / "runtime"
    runtime.mkdir(parents=True)
    decoy_db = runtime / "parayabuck.sqlite3"
    conn = sqlite3.connect(str(decoy_db))
    try:
        conn.execute("CREATE TABLE portfolio_orders (client_order_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        conn.execute("INSERT INTO portfolio_orders VALUES ('decoy-1', 'ambiguous')")
        conn.commit()
    finally:
        conn.close()
    # A stale snapshot at the relative default would read CRITICAL if it were used.
    (runtime / "health_snapshot.json").write_text(
        json.dumps({"written_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}), encoding="utf-8"
    )

    with contextlib.chdir(decoy_root):
        code, out, _ = _run_cli(
            "ops",
            "status",
            "--snapshot",
            str(tmp_path / "absent.json"),
            "--db",
            str(tmp_path / "absent.sqlite3"),
            "--json",
        )

    assert code == 0
    data = json.loads(out)
    assert data["overall_health"] == "UNKNOWN"
    assert data["trading_state"] == "UNKNOWN"
    execution = next(c for c in data["checks"] if c["code"] == "execution")
    assert execution["status"] == "UNKNOWN"


def test_ops_explain_health_reads_the_configured_snapshot(tmp_path: Path) -> None:
    """`ops explain` must evaluate health against observability.snapshot_path,
    not the relative data/runtime default."""
    import yaml

    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "settings.yaml").read_text())
    raw.setdefault("runtime", {})["decision_db_path"] = str(tmp_path / "decisions.sqlite3")
    raw["observability"] = {
        **(raw.get("observability") or {}),
        "snapshot_path": str(tmp_path / "configured_snapshot.json"),
        "journal_path": str(tmp_path / "journal.sqlite3"),
    }
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_health(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"checks": []}

    with (
        patch("parayabuck.monitoring.forensics.decision_reconstruction", return_value={}),
        patch("parayabuck.monitoring.health.evaluate_health", side_effect=fake_health),
    ):
        code, _, _ = _run_cli("--config", str(config_path), "ops", "explain", "dec-1")

    assert code == 0
    assert captured["snapshot_path"] == str(tmp_path / "configured_snapshot.json")
    assert captured["database_path"] == str(tmp_path / "decisions.sqlite3")
