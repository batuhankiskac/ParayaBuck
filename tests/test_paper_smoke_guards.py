"""``scripts/paper_smoke.py`` refuses to compete with a running engine.

The smoke trades the same PAPER account as the engine: its open orders are
unknown-order drift for the engine, and an order placed inside the session-close
decision window can drop that decision. Both refusals are enforced in the
script, not left to the operator's memory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import scripts.paper_smoke as paper_smoke
from parayabuck.config import RuntimeMode
from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic


def _config(snapshot_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(mode=RuntimeMode.PAPER),
        observability=SimpleNamespace(snapshot_path=str(snapshot_path), snapshot_interval_seconds=30.0),
    )


def _write(path: Path, **payload: object) -> SimpleNamespace:
    write_snapshot_atomic(path, build_snapshot(**payload))
    return _config(path)


def test_fresh_heartbeat_reads_as_a_running_engine(tmp_path: Path) -> None:
    config = _write(tmp_path / "health_snapshot.json", runtime_state="running")

    assert paper_smoke._engine_is_active(config) is True


def test_clean_stop_writes_a_stopped_snapshot(tmp_path: Path) -> None:
    config = _write(tmp_path / "health_snapshot.json", runtime_state="stopped")

    assert paper_smoke._engine_is_active(config) is False


def test_stale_heartbeat_is_not_a_running_engine(tmp_path: Path) -> None:
    path = tmp_path / "health_snapshot.json"
    stale = build_snapshot(runtime_state="running")
    stale["written_at"] = "1999-01-01T00:00:00+00:00"
    write_snapshot_atomic(path, stale)

    assert paper_smoke._engine_is_active(_config(path)) is False


def test_undated_snapshot_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "health_snapshot.json"
    path.write_text('{"runtime_state": "running"}')

    assert paper_smoke._engine_is_active(_config(path)) is True


def test_absent_snapshot_is_not_a_running_engine(tmp_path: Path) -> None:
    assert paper_smoke._engine_is_active(_config(tmp_path / "missing.json")) is False


def test_session_close_window_covers_the_et_decision_hour() -> None:
    # 2026-09-30 is EDT (UTC-4): 15:45 ET is 19:45 UTC, 16:00 ET is 20:00 UTC.
    assert paper_smoke._within_session_close_guard(datetime(2026, 9, 30, 19, 45, tzinfo=UTC)) is True
    assert paper_smoke._within_session_close_guard(datetime(2026, 9, 30, 20, 0, tzinfo=UTC)) is True
    assert paper_smoke._within_session_close_guard(datetime(2026, 9, 30, 20, 30, tzinfo=UTC)) is True
    assert paper_smoke._within_session_close_guard(datetime(2026, 9, 30, 19, 44, tzinfo=UTC)) is False
    assert paper_smoke._within_session_close_guard(datetime(2026, 9, 30, 20, 31, tzinfo=UTC)) is False


def test_guards_refuse_while_the_engine_runs(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PARAYABUCK_ALLOW_PAPER_SMOKE", "YES")
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE", raising=False)
    config = _write(tmp_path / "health_snapshot.json", runtime_state="running")

    assert paper_smoke._require_guards(config) is False
    assert "engine heartbeat is live" in capsys.readouterr().out
