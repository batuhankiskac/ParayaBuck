"""Regression tests for the third monitoring-layer defect pass.

Each test pins one invariant: a healthy runtime's snapshot is not reported as
entry-blocked or stale, webhook query-string credentials never reach the log,
filled orders are not "unresolved", and explicit paths are never swapped for
cwd-relative defaults. All offline (tmp_path, no network).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from parayabuck.config import Config
from parayabuck.monitoring import health as health_module
from parayabuck.monitoring.alerts import _scrub_webhook_url
from parayabuck.monitoring.diagnostic import collect_bundle_data
from parayabuck.monitoring.forensics import decision_reconstruction, unresolved_orders
from parayabuck.monitoring.health import HealthStatus, _check_risk, _check_workers
from parayabuck.monitoring.journal import get_journal
from parayabuck.monitoring.operational_event import create_event
from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic
from parayabuck.runtime_health import WorkerHealth

# A Saturday: equity is outside regular hours, crypto must still deliver bars
_NOW = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


def _worker(heartbeat: datetime, last_event: datetime | None = None) -> dict[str, object]:
    # as persisted in the snapshot JSON: datetimes are ISO strings
    return json.loads(
        json.dumps(asdict(WorkerHealth(_NOW - timedelta(hours=3), heartbeat, last_event, None, 0, True)), default=str)
    )


def test_risk_check_ignores_unlatched_markets() -> None:
    # The runtime always reports every market; None means "not latched"
    check = _check_risk(None, {"entry_risk_latch": {"equity": None, "crypto": None}})

    assert check.status != HealthStatus.BLOCKED
    latched = _check_risk(None, {"entry_risk_latch": {"equity": None, "crypto": "daily_loss_limit"}})
    assert latched.status == HealthStatus.BLOCKED


def test_workers_check_follows_runtime_liveness_rules() -> None:
    workers = {
        "pb-decisions": _worker(_NOW),
        # event-driven: heartbeats only at (re)start
        "pb-trade-updates": _worker(_NOW - timedelta(hours=3)),
        # hourly monitor pass
        "pb-signal-adjustment": _worker(_NOW - timedelta(minutes=40)),
        # 1-minute bars: 70 s since the last bar is within the stream window
        "pb-crypto": _worker(_NOW - timedelta(seconds=70), _NOW - timedelta(seconds=70)),
        # weekend: equity stream owes no bars
        "pb-stocks": _worker(_NOW - timedelta(hours=20), _NOW - timedelta(hours=20)),
    }

    check = _check_workers({"workers": workers}, _NOW, Config())

    assert check.status == HealthStatus.OK, check.summary
    workers["pb-crypto"] = _worker(_NOW, _NOW - timedelta(minutes=10))
    stale = _check_workers({"workers": workers}, _NOW, Config())
    assert stale.status == HealthStatus.DEGRADED
    assert stale.observed == ["pb-crypto"]


def test_worker_liveness_constants_match_runtime() -> None:
    from parayabuck import runtime

    assert health_module._SIGNAL_ADJUSTMENT_CHECK_SECONDS == runtime._SIGNAL_ADJUSTMENT_CHECK_SECONDS
    assert health_module._STREAM_WORKER_MARKETS == {
        worker: market.value for worker, market in runtime._MARKET_BY_STREAM_WORKER.items()
    }


def test_webhook_scrub_removes_query_string_credentials() -> None:
    url = "https://chat.example.test/v1/spaces/AAA/messages?key=SECRETKEY&token=SECRETTOKEN"
    # requests' ConnectionError text carries the path and query, not the full URL
    text = (
        "HTTPSConnectionPool(host='chat.example.test', port=443): Max retries exceeded with url: "
        "/v1/spaces/AAA/messages?key=SECRETKEY&token=SECRETTOKEN (Caused by NewConnectionError())"
    )

    scrubbed = _scrub_webhook_url(text, url)

    assert "SECRETKEY" not in scrubbed
    assert "SECRETTOKEN" not in scrubbed
    assert "/v1/spaces/AAA" not in scrubbed


def test_stream_filled_orders_are_not_unresolved(tmp_path: Path) -> None:
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE portfolio_orders (client_order_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO portfolio_orders VALUES (?, ?)",
        [("o-fill", "fill"), ("o-dfd", "done_for_day"), ("o-open", "submitted")],
    )
    conn.commit()
    conn.close()

    assert [o["client_order_id"] for o in unresolved_orders(db)] == ["o-open"]


def test_reconstruction_never_falls_back_from_an_explicit_journal(tmp_path: Path) -> None:
    db = tmp_path / "ledger.sqlite3"
    sqlite3.connect(db).close()
    # the process-default journal holds an event for this decision
    assert get_journal().emit_sync(create_event(kind="decision_summary", message="x", decision_id="d1"))

    result = decision_reconstruction(db, "d1", journal_path=tmp_path / "missing.sqlite3")

    assert result["operational_events"] == []
    assert not (tmp_path / "missing.sqlite3").exists()


def test_bundle_status_uses_the_bundle_config(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.yaml"
    settings = {
        "runtime": {"mode": "paper"},
        "symbols": ["AAPL"],
        "engine": {"event_queue_capacity": 10},
        "strategy": {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1", "maximum_allocation": 0.55}},
    }
    config_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    snapshot_path = tmp_path / "health_snapshot.json"
    write_snapshot_atomic(snapshot_path, build_snapshot(runtime_health={"queue_size": 50}, runtime_state="running"))

    data = collect_bundle_data(
        config_path=config_path,
        snapshot_path=snapshot_path,
        journal_path=tmp_path / "journal.sqlite3",
        decision_db_path=None,
    )

    queue = next(c for c in data["status"]["checks"] if c["code"] == "queue")
    assert queue["status"] == "CRITICAL"
    assert queue["threshold"]["cap"] == 10
