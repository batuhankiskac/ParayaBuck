"""Out-of-process dead-man probe: /readyz readiness and backup freshness.

Nothing inside the web process can report that the engine never started —
every alarm lives in the runtime that failed to start, and /healthz stays
200 — and a backup timer that stopped weeks ago is invisible until a
database loss forces a stale restore. ``parayabuck ops watchdog`` (driven by
``deploy/systemd/parayabuck-watchdog.timer``) is the external check, and
``parayabuck doctor`` reports the same backup freshness.

The HTTP call is stubbed here: the tests assert the decision the probe makes
from a /readyz answer, never the network round-trip.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import parayabuck.monitoring.alerts as alerts
from parayabuck import cli
from parayabuck.backup import MAX_BACKUP_AGE_HOURS
from parayabuck.storage import SQLiteStore

CONFIG = Path("config/settings.yaml")


@pytest.fixture(autouse=True)
def _alert_sink() -> Any:
    alerts.reset_for_tests()
    alerts.configure_alerting(
        enabled=True,
        webhook_url="",
        events=sorted(alerts.ALERT_KINDS),
        throttle_seconds=0.0,
    )
    yield
    alerts.reset_for_tests()


def _delivered(kind: str) -> int:
    delivered = alerts.alert_stats()["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


# ---------------------------------------------------------------------------
# Readiness verdict
# ---------------------------------------------------------------------------


def test_readyz_503_names_the_failed_start() -> None:
    """The engine start failed and the body explains why: an external probe
    must not have to guess between 'starting' and 'dead'."""
    reason = cli._readiness_failure_reason(
        503,
        {
            "ready": False,
            "engine_state": "error",
            "engine_error": "Alpaca rejected the API key (401)",
            "start_retry_attempt": 0,
        },
    )

    assert reason is not None
    assert "HTTP 503" in reason
    assert "engine_state=error" in reason
    assert "401" in reason


def test_readyz_503_while_the_start_keeps_retrying() -> None:
    """An endless retry loop holds the state at 'starting' with no runtime:
    still not trading, and the probe reports the attempt count."""
    reason = cli._readiness_failure_reason(
        503,
        {"ready": False, "engine_state": "starting", "start_retry_attempt": 47},
    )

    assert reason is not None
    assert "engine_state=starting" in reason
    assert "start_retry_attempt=47" in reason


def test_ready_200_is_no_failure() -> None:
    assert cli._readiness_failure_reason(200, {"ready": True, "engine_state": "running"}) is None


def test_unreachable_probe_is_a_failure() -> None:
    reason = cli._readiness_failure_reason(None, None, "Connection refused")

    assert reason is not None
    assert "Connection refused" in reason


# ---------------------------------------------------------------------------
# Backup freshness verdict
# ---------------------------------------------------------------------------


def test_missing_backup_is_a_failure() -> None:
    assert cli._backup_failure_reason(None, MAX_BACKUP_AGE_HOURS) is not None


def test_stale_backup_is_a_failure_and_fresh_one_is_not() -> None:
    assert cli._backup_failure_reason(MAX_BACKUP_AGE_HOURS + 0.1, MAX_BACKUP_AGE_HOURS) is not None
    assert cli._backup_failure_reason(MAX_BACKUP_AGE_HOURS, MAX_BACKUP_AGE_HOURS) is None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _watchdog_config(backup_dir: Path) -> Any:
    return SimpleNamespace(app=SimpleNamespace(host="127.0.0.1", port=8000), backup=SimpleNamespace(path=backup_dir))


def _watchdog_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=str(config_path),
        readyz_url=None,
        timeout=1.0,
        max_backup_age_hours=None,
        json=False,
    )


def test_watchdog_alerts_on_both_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PARAYABUCK_READYZ_URL", raising=False)
    monkeypatch.setattr(cli, "load_config", lambda path: _watchdog_config(tmp_path / "backups"))
    monkeypatch.setattr(
        cli,
        "_probe_readyz",
        lambda url, timeout: (503, {"ready": False, "engine_state": "error"}, None),
    )

    exit_code = cli._ops_watchdog(_watchdog_args(tmp_path / "settings.yaml"))

    assert exit_code == 1
    assert _delivered("runtime_start_failed") == 1
    assert _delivered("backup_failed") == 1
    assert "engine_state=error" in capsys.readouterr().err


def test_watchdog_is_silent_and_green_when_everything_is_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    fresh = backups / "parayabuck-20260812T120000000000Z.sqlite3"
    fresh.write_bytes(b"SQLite format 3\x00")
    monkeypatch.delenv("PARAYABUCK_READYZ_URL", raising=False)
    monkeypatch.setattr(cli, "load_config", lambda path: _watchdog_config(backups))
    monkeypatch.setattr(cli, "_probe_readyz", lambda url, timeout: (200, {"ready": True}, None))

    exit_code = cli._ops_watchdog(_watchdog_args(tmp_path / "settings.yaml"))

    assert exit_code == 0
    assert _delivered("runtime_start_failed") == 0
    assert _delivered("backup_failed") == 0


def test_watchdog_fails_on_an_unreadable_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(path: str) -> Any:
        raise ValueError("bad yaml")

    monkeypatch.setattr(cli, "load_config", _boom)

    assert cli._ops_watchdog(_watchdog_args(tmp_path / "settings.yaml")) == 1


# ---------------------------------------------------------------------------
# doctor: the same freshness signal, without blocking the engine start
# ---------------------------------------------------------------------------


def test_doctor_warns_about_a_stale_backup_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The unit runs `doctor --strict` as ExecStartPre, so a stale backup may
    be reported but must never keep the engine from starting."""

    raw = yaml.safe_load(CONFIG.read_text())
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime.sqlite3")
    raw["backup"]["path"] = str(tmp_path / "backups")
    raw["backtest"]["output_dir"] = str(tmp_path / "reports")
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.safe_dump(raw))

    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    store.acquire()
    store.release()

    stale = tmp_path / "backups" / "parayabuck-20200101T000000000000Z.sqlite3"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"SQLite format 3\x00")
    backdated = datetime.now(UTC).timestamp() - 72 * 3600
    os.utime(stale, (backdated, backdated))

    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    args = argparse.Namespace(config=str(config_path), strict=True, paper=True, offline=True)

    exit_code = cli._run_doctor(args)

    output = capsys.readouterr().out
    assert "WARN backup stale" in output
    assert "FAIL" not in output
    assert exit_code == 0


# ---------------------------------------------------------------------------
# The backup command itself must never fail silently (it runs from a timer)
# ---------------------------------------------------------------------------


def _backup_cli_config(tmp_path: Path) -> Any:
    return SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=str(tmp_path / "runtime.sqlite3")),
        backup=SimpleNamespace(path=str(tmp_path / "backups"), retention_days=7),
    )


def test_backup_command_alerts_when_the_snapshot_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`parayabuck backup` runs unattended every 6 h: a failure that only
    printed a traceback would go unnoticed until a restore was needed."""
    from parayabuck import backup as backup_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(backup_module, "backup_database", _boom)
    args = argparse.Namespace(verify_only=None, retention_days=None, label=None)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        cli._run_backup(_backup_cli_config(tmp_path), args)

    assert _delivered("backup_failed") == 1


def test_backup_verify_only_alerts_on_a_corrupt_file(tmp_path: Path) -> None:
    junk = tmp_path / "junk.sqlite3"
    junk.write_text("not a database")
    args = argparse.Namespace(verify_only=str(junk), retention_days=None, label=None)

    assert cli._run_backup(_backup_cli_config(tmp_path), args) == 1
    assert _delivered("backup_failed") == 1
