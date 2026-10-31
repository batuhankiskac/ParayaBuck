"""Audit wave 3, worker W09: backup alerts, CLI exit codes and operator messages.

Every test uses tmp_path, fixed timestamps and no network. The dotenv path is
pinned to a missing file so no test reads a real .env.
"""

from __future__ import annotations

import argparse
import os
import runpy
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck import backup, cli
from parayabuck.config import BackupConfig, Config
from parayabuck.monitoring import email_alerts, events
from parayabuck.monitoring.journal import SAFETY_JOURNAL_KINDS, EventJournal
from parayabuck.monitoring.operational_event import create_event

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_real_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    monkeypatch.delenv("CONFIG_PATH", raising=False)


@pytest.fixture
def umask_restored():
    # main() sets the process umask to 0o077; restore it so tests do not leak it.
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def _write_file(directory: Path, name: str, mtime: float) -> Path:
    path = directory / name
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))
    return path


def _make_db(path: Path) -> Path:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.execute("INSERT INTO t VALUES (1)")
        connection.commit()
    return path


# --- R3-3-4 / D3-3-1: a stale backup set raises an e-mailed alert ---


def test_backup_stale_reason_fires_past_the_freshness_bound(tmp_path: Path) -> None:
    old = (NOW - timedelta(hours=13)).timestamp()
    _write_file(tmp_path, "parayabuck-20261030T000000000000Z.sqlite3", old)
    reason = backup.backup_stale_reason(tmp_path, now=NOW)
    assert reason is not None
    assert "13.0 h old" in reason


def test_backup_stale_reason_is_silent_for_a_fresh_set(tmp_path: Path) -> None:
    recent = (NOW - timedelta(hours=11)).timestamp()
    _write_file(tmp_path, "parayabuck-20261031T010000000000Z.sqlite3", recent)
    assert backup.backup_stale_reason(tmp_path, now=NOW) is None


def test_labelled_backups_do_not_keep_a_dead_timer_looking_fresh(tmp_path: Path) -> None:
    old = (NOW - timedelta(hours=30)).timestamp()
    _write_file(tmp_path, "parayabuck-keep-predeploy-20261030T000000000000Z.sqlite3", old)
    # No automatic set exists, so the engine stays silent (the doctor WARNs instead).
    assert backup.backup_stale_reason(tmp_path, now=NOW) is None
    assert backup.backup_stale_reason(tmp_path / "missing", now=NOW) is None


def test_engine_raises_backup_stale_once_per_renotify_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck import runtime as runtime_module

    old = (NOW - timedelta(hours=20)).timestamp()
    _write_file(tmp_path, "parayabuck-20261030T000000000000Z.sqlite3", old)
    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    engine = SimpleNamespace(
        app_config=Config(backup=BackupConfig(path=str(tmp_path))),
        _BACKUP_CHECK_INTERVAL_SECONDS=runtime_module.EngineRuntime._BACKUP_CHECK_INTERVAL_SECONDS,
        _BACKUP_STALE_RENOTIFY_SECONDS=runtime_module.EngineRuntime._BACKUP_STALE_RENOTIFY_SECONDS,
    )
    check = runtime_module.EngineRuntime._maybe_check_backup_freshness

    check(engine, now=NOW)
    check(engine, now=NOW)  # same stale episode: suppressed
    assert [kind for kind, _ in emitted] == ["backup_stale"]

    engine._backup_stale_notified_mono = time.monotonic() - 13 * 3600.0
    check(engine, now=NOW)  # a fresh window after 12 h: repeated once
    assert [kind for kind, _ in emitted] == ["backup_stale", "backup_stale"]


def test_backup_stale_is_an_e_mailed_warning_kind_and_journaled_synchronously() -> None:
    assert "backup_stale" in events.ALERT_KINDS
    assert "backup_stale" in events.EMAIL_ALERT_KINDS
    assert "backup_stale" in events.WARNING_KINDS
    assert "backup_stale" in SAFETY_JOURNAL_KINDS


# --- R3-3-5: a set that fails verification is removed ---


def test_backup_run_removes_a_set_that_fails_post_publication_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_db(tmp_path / "runtime.sqlite3")
    backups = tmp_path / "backups"
    config = SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=str(source)),
        backup=SimpleNamespace(path=str(backups), retention_days=7.0),
    )
    args = SimpleNamespace(verify_only=None, retention_days=None, label=None)
    alerts: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(cli, "_emit_ops_alert", lambda kind, message, **fields: alerts.append((kind, fields)))
    monkeypatch.setattr(backup, "verify_backup_set", lambda path: False)

    with pytest.raises(SystemExit):
        cli._run_backup(config, args)

    assert list(backups.glob(backup.AUTO_BACKUP_GLOB)) == []
    assert [(kind, fields["reason"]) for kind, fields in alerts] == [("backup_failed", "corrupt")]


def test_discard_backup_set_removes_the_snapshot_and_its_members_only(tmp_path: Path) -> None:
    snapshot = _write_file(tmp_path, "parayabuck-20261031T010000000000Z.sqlite3", NOW.timestamp())
    member = _write_file(tmp_path, "parayabuck-20261031T010000000000Z.paper_equity_peak.json", NOW.timestamp())
    unrelated = _write_file(tmp_path, "parayabuck-20261030T010000000000Z.sqlite3", NOW.timestamp())

    backup.discard_backup_set(snapshot)

    assert not snapshot.exists()
    assert not member.exists()
    assert unrelated.exists()


# --- R3-5-3: a failed unlink of the staging name keeps the published snapshot ---


def test_unlink_failure_after_publication_keeps_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_db(tmp_path / "runtime.sqlite3")
    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith(backup.STAGE_PREFIX) and self.name.endswith(".tmp"):
            raise OSError("simulated unlink failure")
        return original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7.0, now=NOW)

    assert destination.is_file()
    assert backup.verify_backup(destination)


# --- R3-3-7: the retention floor applies to the config as well as the flag ---


def test_backup_config_refuses_a_retention_under_one_day() -> None:
    with pytest.raises(ValueError):
        BackupConfig(retention_days=0.1)
    assert BackupConfig(retention_days=1.0).retention_days == 1.0


# --- R3-3-6: the start budget covers the worst-case copy, and SIGTERM alerts ---


def test_backup_start_budget_covers_seven_offhost_copies() -> None:
    unit = (REPO / "deploy" / "systemd" / "parayabuck-backup.service").read_text(encoding="utf-8")
    value = int(next(line.split("=", 1)[1] for line in unit.splitlines() if line.startswith("TimeoutStartSec=")))
    assert value >= 7 * backup.OFFHOST_TIMEOUT_SECONDS


def test_backup_sigterm_records_backup_failed_and_restores_the_handler(
    monkeypatch: pytest.MonkeyPatch, umask_restored: None
) -> None:
    monkeypatch.setattr(email_alerts, "configure_email_alerts", lambda *args, **kwargs: None)
    monkeypatch.setattr(email_alerts, "current_sink", lambda: None)
    alerts: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(cli, "_emit_ops_alert", lambda kind, message, **fields: alerts.append((kind, fields)))
    seen_handler: list[object] = []

    def interrupted(config: object, args: object) -> int:
        seen_handler.append(signal.getsignal(signal.SIGTERM))
        raise cli._BackupTerminated("signal 15")

    monkeypatch.setattr(cli, "_run_backup", interrupted)
    before = signal.getsignal(signal.SIGTERM)
    config = SimpleNamespace(
        alerting=SimpleNamespace(email=None), runtime=SimpleNamespace(mode=SimpleNamespace(value="PAPER"))
    )

    code = cli._run_backup_with_email(config, SimpleNamespace())

    assert code == cli.EXIT_FAILURE
    assert seen_handler == [cli._raise_backup_terminated]
    assert alerts == [("backup_failed", {"reason": "terminated"})]
    assert signal.getsignal(signal.SIGTERM) is before


def test_email_flush_budget_covers_four_attempts_and_the_backoff() -> None:
    # 4 attempts x 10 s socket timeout + 5 + 15 + 45 s backoff = 105 s.
    assert cli._EMAIL_FLUSH_SECONDS >= 105.0


# --- F3-5-6 / D3-5-12, F3-5-8, F3-5-7: usage errors exit 64 and never fetch ---


def test_backup_label_with_a_bad_character_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli._parser().parse_args(["backup", "--label", "bad label!"])
    assert exc.value.code == cli.EXIT_USAGE
    assert cli._parser().parse_args(["backup", "--label", "predeploy"]).label == "predeploy"


def test_backtest_malformed_date_is_refused_before_the_config_loads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    def config_must_not_load(*args: object, **kwargs: object) -> Config:
        raise AssertionError("the configuration must not load for a usage error")

    monkeypatch.setattr(cli, "load_config", config_must_not_load)
    assert cli.main(["backtest", "--start", "2026-13-99"]) == cli.EXIT_USAGE
    assert "--start" in capsys.readouterr().err


def test_optimize_malformed_grid_is_refused_before_any_bar_is_fetched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    def bars_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("no bars may be fetched for a usage error")

    monkeypatch.setattr(cli, "_load_bars", bars_must_not_load)
    assert cli.main(["optimize", "--grid", "novalue"]) == cli.EXIT_USAGE
    assert "--grid" in capsys.readouterr().err


def test_authorize_live_without_an_operator_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(revoke_standing=False, operator="  ", standing_days=None, expires_minutes=60)
    assert cli._authorize_live(Config(), args) == cli.EXIT_USAGE
    assert "--operator" in capsys.readouterr().err


# --- F3-5-2, F3-5-3: a broken or missing settings file is not a fresh install ---


def test_ops_events_refuses_a_missing_settings_file_and_says_so_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    code = cli.main(["--config", str(tmp_path / "missing.yaml"), "ops", "events", "--json"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_MISSING
    assert '"error"' in captured.out
    assert "settings could not be loaded" in captured.err


def test_ops_events_with_an_explicit_journal_does_not_need_the_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    code = cli.main(
        ["--config", str(tmp_path / "missing.yaml"), "ops", "events", "--journal", str(tmp_path / "none.sqlite3")]
    )
    assert code == 0
    assert "No event journal found" in capsys.readouterr().out


def test_ops_timeline_exits_failure_when_the_journal_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    journal = tmp_path / "observability.sqlite3"
    journal.write_bytes(b"this is not a sqlite database" * 100)
    code = cli.main(["--config", str(tmp_path / "missing.yaml"), "ops", "timeline", "--journal", str(journal)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILURE
    assert "timeline journal query failed" in captured.err
    assert "No operational events found" not in captured.out


def test_halt_resume_on_a_missing_runtime_directory_exits_missing(tmp_path: Path, umask_restored: None) -> None:
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            decision_db_path=str(tmp_path / "absent" / "runtime.sqlite3"), mode=SimpleNamespace(value="PAPER")
        )
    )
    assert cli._run_halt(config, SimpleNamespace(command="resume", reason=None)) == cli.EXIT_MISSING


# --- P3-4-5: ops events shows the full order id ---


def test_ops_events_prints_the_full_client_order_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], umask_restored: None
) -> None:
    journal_path = tmp_path / "observability.sqlite3"
    journal = EventJournal(journal_path, retention_days=3650)
    order_id = "parayabuck-equity-20261031-0001-abcdef"
    journal.emit(create_event(kind="order_rejected", message="rejected", client_order_id=order_id, occurred_at=NOW))
    journal.stop()

    code = cli.main(["--config", str(tmp_path / "missing.yaml"), "ops", "events", "--journal", str(journal_path)])

    assert code == 0
    assert f"order={order_id}" in capsys.readouterr().out


# --- F3-5-4: operator audit rows are committed before the command returns ---


def test_operator_audit_kinds_are_synchronous_safety_kinds() -> None:
    for kind in ("operator_halt_set", "operator_halt_cleared", "ownership_rebased"):
        assert kind in SAFETY_JOURNAL_KINDS


# --- F3-5-11: python -m parayabuck forwards its arguments to the CLI ---


def test_python_m_parayabuck_dispatches_its_arguments_to_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def record(argv: list[str] | None = None) -> int:
        seen.append(list(sys.argv[1:]) if argv is None else list(argv))
        return 0

    monkeypatch.setattr(cli, "main", record)
    monkeypatch.setattr(sys, "argv", ["parayabuck", "halt", "--reason", "drill"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("parayabuck.__main__", run_name="__main__")
    assert exc.value.code == 0
    assert seen == [["halt", "--reason", "drill"]]


# --- D3-3-5: a doctor --strict blocker is mailed ---


def test_doctor_blocker_is_mailed_as_runtime_start_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_alerts, "configure_email_alerts", lambda *args, **kwargs: None)
    monkeypatch.setattr(email_alerts, "current_sink", lambda: None)
    alerts: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(cli, "_emit_ops_alert", lambda kind, message, **fields: alerts.append((kind, message, fields)))

    cli._alert_doctor_blocker(Config(), ["credential file owner-only", "config parses"])

    assert len(alerts) == 1
    kind, message, fields = alerts[0]
    assert kind == "runtime_start_failed"
    assert "credential file owner-only" in message
    assert fields["reason"] == "doctor_blocker"


def test_doctor_blocker_without_a_loadable_config_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_emit(*args: object, **kwargs: object) -> None:
        raise AssertionError("no channel exists without a configuration")

    monkeypatch.setattr(cli, "_emit_ops_alert", must_not_emit)
    cli._alert_doctor_blocker(None, ["config parses"])
