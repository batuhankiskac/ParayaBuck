"""Regression tests for the fourth monitoring-layer defect pass.

The workers check must judge each worker exactly as the runtime did when it
wrote the snapshot. All offline (in-memory snapshots, no network).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from parayabuck.config import Config
from parayabuck.monitoring.health import HealthStatus, _check_workers
from parayabuck.runtime_health import WorkerHealth

# A Monday 11:00 ET: regular equity hours, the equity stream must deliver bars
_NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


def _worker(heartbeat: datetime, last_event: datetime | None = None) -> dict[str, object]:
    # as persisted in the snapshot JSON: datetimes are ISO strings
    return json.loads(
        json.dumps(asdict(WorkerHealth(_NOW - timedelta(hours=3), heartbeat, last_event, None, 0, True)), default=str)
    )


def test_worker_ages_are_judged_as_of_the_snapshot() -> None:
    # The snapshot is 30 s old (one default snapshot interval) when read; the
    # account loop had heartbeated 20 s before the write (15 s poll + a slow
    # broker call), within the runtime's 45 s TTL. Age from the read time is 50 s.
    written_at = _NOW - timedelta(seconds=30)
    snapshot = {
        "written_at": written_at.isoformat(),
        "workers": {"pb-account": _worker(written_at - timedelta(seconds=20))},
    }

    check = _check_workers(snapshot, _NOW, Config())

    assert check.status == HealthStatus.OK, check.summary
    snapshot["workers"] = {"pb-account": _worker(written_at - timedelta(seconds=50))}
    assert _check_workers(snapshot, _NOW, Config()).status == HealthStatus.DEGRADED


def test_stream_worker_of_market_without_symbols_is_not_stale() -> None:
    # The runtime requires a market stream only while the market is enabled
    # and has symbols (_required_workers); an empty equity universe owes no bars.
    snapshot = {
        "written_at": _NOW.isoformat(),
        "workers": {"pb-stocks": _worker(_NOW, _NOW - timedelta(hours=2))},
        "markets": {"equity": {"enabled": True, "symbol_count": 0}},
    }

    assert _check_workers(snapshot, _NOW, Config()).status == HealthStatus.OK
    snapshot["markets"] = {"equity": {"enabled": True, "symbol_count": 2}}
    assert _check_workers(snapshot, _NOW, Config()).status == HealthStatus.DEGRADED
