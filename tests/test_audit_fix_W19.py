"""Audit fixes W19: backup set, backup skip and prune/off-host ordering, atomic
write post-commit handling, orphan temp sweep, and the repair script's engine
liveness and ledger-backup failure paths.

Every test works on ``tmp_path`` with synthetic fixtures. Nothing touches the
network, credentials, data/ or logs/.
"""

from __future__ import annotations

import errno
import importlib.util
import json
import logging
import os
import sqlite3
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.backup as backup
import parayabuck.utils.io as io_module
from parayabuck.monitoring.snapshot import build_snapshot, write_snapshot_atomic
from parayabuck.utils.io import atomic_write, sweep_orphan_temp_files

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PEAK = json.dumps({"peak_equity": 1234.5, "epoch": 2, "account_id": "acct-1"})


def _load_repair():
    """Load ``scripts/repair_local_state.py`` by path (``scripts/`` is not a package)."""
    path = _REPO_ROOT / "scripts" / "repair_local_state.py"
    spec = importlib.util.spec_from_file_location("repair_local_state_w19", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _age(path: Path, seconds: float) -> None:
    """Set ``path``'s mtime to ``seconds`` in the past."""
    stamp = datetime.now(UTC).timestamp() - seconds
    os.utime(path, (stamp, stamp))


def _make_db(path: Path) -> Path:
    """A small synthetic decision database (rollback journal, no WAL)."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO decisions (payload) VALUES ('synthetic')")
    return path


# ------------------------------------------------------------------ utils/io


def test_atomic_write_replaces_a_symlinked_destination_and_keeps_the_link(tmp_path: Path) -> None:
    """S9-5: the regular file behind a managed symlink is replaced, the link survives."""
    managed = tmp_path / "managed" / "settings.yaml"
    managed.parent.mkdir()
    managed.write_text("mode: paper\n", encoding="utf-8")
    link = tmp_path / "config" / "settings.yaml"
    link.parent.mkdir()
    link.symlink_to(managed)

    atomic_write(link, "mode: live\n")

    assert link.is_symlink()
    assert managed.read_text(encoding="utf-8") == "mode: live\n"


def test_chmod_failure_after_rename_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S9-1: the rename is the commit point, so a post-rename chmod error must not fail the write."""
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def failing_chmod(path: object, mode: int, **kwargs: object) -> None:
        raise OSError(errno.EIO, "injected chmod failure")

    monkeypatch.setattr(io_module.os, "chmod", failing_chmod)
    with caplog.at_level(logging.WARNING, logger="parayabuck.utils.io"):
        atomic_write(target, "new", mode=0o600)

    assert target.read_text(encoding="utf-8") == "new"
    assert any("post-rename step failed" in record.getMessage() for record in caplog.records)


def test_directory_fsync_failure_after_rename_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S9-1: an EIO on the directory fsync happens after the rename and is only logged."""
    target = tmp_path / "state.json"
    real_fsync = os.fsync

    def failing_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(io_module.os, "fsync", failing_directory_fsync)
    with caplog.at_level(logging.WARNING, logger="parayabuck.utils.io"):
        atomic_write(target, "committed")

    assert target.read_text(encoding="utf-8") == "committed"
    assert any("post-rename step failed" in record.getMessage() for record in caplog.records)


def test_failure_before_the_rename_still_raises_and_keeps_the_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a failed rename is a real failure; the old file stays and no temp file is left."""
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def failing_replace(src: object, dst: object) -> None:
        raise OSError(errno.EIO, "injected replace failure")

    monkeypatch.setattr(io_module.os, "replace", failing_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        atomic_write(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".state.json.*")) == []


def test_orphan_sweep_removes_only_old_files_with_the_prefix(tmp_path: Path) -> None:
    """S9-7: orphans from an interrupted atomic_write are swept; fresh temp files and other names survive."""
    orphan = tmp_path / ".settings.yaml.k3j2h1xq"
    orphan.write_text("partial", encoding="utf-8")
    _age(orphan, 2 * 3600)
    fresh = tmp_path / ".settings.yaml.live0000"
    fresh.write_text("being written", encoding="utf-8")
    unrelated = tmp_path / "settings.yaml"
    unrelated.write_text("mode: paper\n", encoding="utf-8")
    _age(unrelated, 2 * 3600)
    other_prefix = tmp_path / ".other.old"
    other_prefix.write_text("x", encoding="utf-8")
    _age(other_prefix, 2 * 3600)

    removed = sweep_orphan_temp_files(tmp_path, ".settings.yaml.")

    assert removed == [orphan]
    assert not orphan.exists()
    assert fresh.exists() and unrelated.exists() and other_prefix.exists()


def test_orphan_sweep_refuses_an_empty_prefix_and_a_missing_directory(tmp_path: Path) -> None:
    """S9-7 guard: an empty prefix would match the whole directory; a missing directory is a no-op."""
    keep = tmp_path / "keep.txt"
    keep.write_text("x", encoding="utf-8")
    _age(keep, 2 * 3600)
    with pytest.raises(ValueError):
        sweep_orphan_temp_files(tmp_path, "")
    assert keep.exists()
    assert sweep_orphan_temp_files(tmp_path / "absent", ".x.") == []


# -------------------------------------------------------------------- backup


def test_missing_source_is_a_skip_exception_that_writes_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R8-3: a missing source is the dedicated skip (a FileNotFoundError subclass), logged, nothing written."""
    backup_dir = tmp_path / "backups"
    with caplog.at_level(logging.WARNING, logger="parayabuck.backup"):
        with pytest.raises(backup.BackupSourceMissing) as caught:
            backup.backup_database(tmp_path / "absent.sqlite3", backup_dir, retention_days=7)

    assert isinstance(caught.value, FileNotFoundError)
    assert not backup_dir.exists()
    assert any("does not exist yet" in record.getMessage() for record in caplog.records)


def test_backup_set_includes_verified_peak_sidecars(tmp_path: Path) -> None:
    """BK-5: each peak sidecar next to the database is copied beside the snapshot and verified."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = _make_db(runtime / "parayabuck.sqlite3")
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    (runtime / "live_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    paper_copy = destination.with_name(f"{destination.stem}.paper_equity_peak.json")
    live_copy = destination.with_name(f"{destination.stem}.live_equity_peak.json")
    assert paper_copy.read_text(encoding="utf-8") == _PEAK
    assert live_copy.read_text(encoding="utf-8") == _PEAK
    assert backup.verify_backup(destination)


def test_backup_without_sidecars_publishes_only_the_database(tmp_path: Path) -> None:
    """A host that has not traded has no sidecar; the set is the database alone."""
    source = _make_db(tmp_path / "parayabuck.sqlite3")
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert sorted(path.name for path in backup_dir.iterdir()) == [destination.name]


def test_invalid_peak_sidecar_fails_the_whole_backup_set(tmp_path: Path) -> None:
    """BK-5: a sidecar that does not verify leaves no half-complete set behind."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = _make_db(runtime / "parayabuck.sqlite3")
    (runtime / "paper_equity_peak.json").write_text('{"peak_equity": -5}', encoding="utf-8")
    backup_dir = tmp_path / "backups"

    with pytest.raises(backup.BackupVerificationError, match="Peak sidecar failed validation"):
        backup.backup_database(source, backup_dir, retention_days=7)

    assert list(backup_dir.glob("parayabuck-*")) == []
    assert list(backup_dir.glob(".parayabuck-stage-*")) == []


def test_offhost_copy_runs_for_the_database_and_each_peak_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BK-5: the off-host copy covers the whole backup set, not only the database."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = _make_db(runtime / "parayabuck.sqlite3")
    (runtime / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    copied: list[Path] = []
    monkeypatch.setattr(backup, "copy_offhost", lambda path, **kwargs: copied.append(Path(path)) or True)

    destination = backup.backup_database(source, tmp_path / "backups", retention_days=7)

    assert copied == [destination, destination.with_name(f"{destination.stem}.paper_equity_peak.json")]


def test_prune_removes_expired_peak_copies_and_keeps_labelled_ones(tmp_path: Path) -> None:
    """BK-5: a peak copy ages out with its snapshot; a labelled keep set is never pruned."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    expired = backup_dir / "parayabuck-20260101T000000000000Z.paper_equity_peak.json"
    expired.write_text(_PEAK, encoding="utf-8")
    _age(expired, 30 * 86_400)
    labelled = backup_dir / "parayabuck-keep-predeploy-20260101T000000000000Z.paper_equity_peak.json"
    labelled.write_text(_PEAK, encoding="utf-8")
    _age(labelled, 30 * 86_400)
    recent = backup_dir / "parayabuck-20261026T000000000000Z.paper_equity_peak.json"
    recent.write_text(_PEAK, encoding="utf-8")

    removed = backup.prune_backups(backup_dir, retention_days=7)

    assert removed == [expired]
    assert labelled.exists() and recent.exists()


def test_prune_failure_is_logged_and_the_offhost_copy_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """BK-6: a prune error is logged; the verified backup is returned and copied off-host."""
    source = _make_db(tmp_path / "parayabuck.sqlite3")
    copied: list[Path] = []

    def failing_prune(*args: Any, **kwargs: Any) -> list[Path]:
        raise OSError("injected prune failure")

    monkeypatch.setattr(backup, "prune_backups", failing_prune)
    monkeypatch.setattr(backup, "copy_offhost", lambda path, **kwargs: copied.append(Path(path)) or True)
    with caplog.at_level(logging.ERROR, logger="parayabuck.backup"):
        destination = backup.backup_database(source, tmp_path / "backups", retention_days=7)

    assert destination.is_file() and backup.verify_backup(destination)
    assert copied == [destination]
    assert any("retention prune failed" in record.getMessage() for record in caplog.records)


def test_orphan_staging_files_are_swept_when_a_backup_starts(tmp_path: Path) -> None:
    """BK-10: a staging file left by a killed run is removed; a fresh one (possibly a concurrent run) is not."""
    source = _make_db(tmp_path / "parayabuck.sqlite3")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    orphan = backup_dir / ".parayabuck-stage-killed.tmp"
    orphan.write_bytes(b"partial")
    _age(orphan, 2 * 3600)
    concurrent = backup_dir / ".parayabuck-stage-running.tmp"
    concurrent.write_bytes(b"in progress")

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert not orphan.exists()
    assert concurrent.exists()
    assert destination.is_file()


# -------------------------------------------------------------------- repair


@pytest.fixture
def repair(monkeypatch: pytest.MonkeyPatch):
    module = _load_repair()
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    return module


@pytest.fixture
def engine_paths(repair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Route every path the repair script reads or writes into ``tmp_path``."""
    snapshot_path = tmp_path / "runtime" / "health_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    config = SimpleNamespace(
        observability=SimpleNamespace(
            snapshot_path=str(snapshot_path),
            snapshot_interval_seconds=30.0,
            journal_path=str(tmp_path / "runtime" / "observability.sqlite3"),
        ),
        runtime=SimpleNamespace(decision_db_path=str(tmp_path / "runtime" / "parayabuck.sqlite3")),
    )
    monkeypatch.setattr(repair, "_heartbeat_config", lambda: config)
    monkeypatch.setattr(repair, "_load_config", lambda config_path=None: config)
    monkeypatch.setattr(repair, "WARMUP_FILES", [])
    monkeypatch.setattr(repair, "OBS_FILES", [])
    monkeypatch.setattr(repair, "QUARANTINE_ROOT", tmp_path / "quarantine")
    return SimpleNamespace(config=config, snapshot_path=snapshot_path, ledger=Path(config.runtime.decision_db_path))


def _stopped_snapshot(path: Path, *, age_seconds: float) -> None:
    snapshot = build_snapshot(runtime_state="stopped")
    snapshot["written_at"] = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    write_snapshot_atomic(path, snapshot)


def test_activating_unit_is_refused_even_with_a_stale_stopped_snapshot(
    repair, engine_paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BK-3: during a restart the snapshot still says stopped; systemd says activating, so the repair refuses."""
    _stopped_snapshot(engine_paths.snapshot_path, age_seconds=24 * 3600)
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: "activating")

    assert repair.check_engine_not_running() is False


@pytest.mark.parametrize("state", ["active", "deactivating", "reloading", "unknown"])
def test_any_non_inactive_unit_state_fails_closed(repair, engine_paths, monkeypatch, state: str) -> None:
    """BK-3: only inactive or failed lets the snapshot decide; anything else refuses."""
    _stopped_snapshot(engine_paths.snapshot_path, age_seconds=24 * 3600)
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: state)

    assert repair.check_engine_not_running() is False


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_inactive_unit_still_lets_a_stopped_snapshot_through(repair, engine_paths, monkeypatch, state: str) -> None:
    """BK-3 regression: a cleanly stopped engine (systemd inactive) keeps the documented repair path."""
    _stopped_snapshot(engine_paths.snapshot_path, age_seconds=600)
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: state)

    assert repair.check_engine_not_running() is True


def test_crashed_unit_with_a_stale_running_snapshot_does_not_block(repair, engine_paths, monkeypatch) -> None:
    """BK-3 regression: a crashed engine leaves a running snapshot that has aged out; the repair may proceed."""
    snapshot = build_snapshot(runtime_state="running")
    snapshot["written_at"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    write_snapshot_atomic(engine_paths.snapshot_path, snapshot)
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: "failed")

    assert repair.check_engine_not_running() is True


def test_systemctl_answer_is_parsed_and_failures_fail_closed(repair, monkeypatch: pytest.MonkeyPatch) -> None:
    """BK-3: systemctl's first output line is the state; a missing binary means no systemd, a timeout is unknown."""

    def completed(stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: completed("activating\n"))
    assert repair._systemd_unit_state() == "activating"

    monkeypatch.setattr(repair.subprocess, "run", lambda *a, **k: completed(""))
    assert repair._systemd_unit_state() == "unknown"

    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(repair.subprocess, "run", missing)
    assert repair._systemd_unit_state() is None

    def hangs(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    monkeypatch.setattr(repair.subprocess, "run", hangs)
    assert repair._systemd_unit_state() == "unknown"


def _unreadable_ledger(path: Path) -> Path:
    path.write_bytes(b"this is not an sqlite database\x00" * 64)
    return path


def test_ledger_backup_failure_raises_instead_of_copying_the_main_file(repair, tmp_path: Path) -> None:
    """BK-8: a failed sqlite backup aborts; the script no longer falls back to copy2 and calls it a backup."""
    ledger = _unreadable_ledger(tmp_path / "parayabuck.sqlite3")
    qdir = tmp_path / "quarantine" / "quarantine_test"
    (qdir / "runtime").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="ledger backup failed"):
        repair.backup_ledger(qdir, ledger)

    assert list((qdir / "runtime").glob("parayabuck.sqlite3.backup.*")) == []


def test_ledger_backup_success_is_a_consistent_sqlite_copy(repair, tmp_path: Path) -> None:
    """BK-8 regression: the sqlite backup API path still produces a readable copy."""
    ledger = _make_db(tmp_path / "parayabuck.sqlite3")
    qdir = tmp_path / "quarantine" / "quarantine_test"
    (qdir / "runtime").mkdir(parents=True)

    copy = repair.backup_ledger(qdir, ledger)

    with sqlite3.connect(copy) as connection:
        assert connection.execute("SELECT payload FROM decisions").fetchall() == [("synthetic",)]


def test_main_aborts_before_repairing_when_the_ledger_backup_fails(
    repair, engine_paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BK-8: with --confirm and no usable backup, main returns 1 and never repairs the ledger."""
    _unreadable_ledger(engine_paths.ledger)
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: "inactive")

    def must_not_repair(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("repair_runtime_db ran without a ledger backup")

    monkeypatch.setattr(repair, "repair_runtime_db", must_not_repair)

    assert repair.main(["--confirm"]) == 1
    assert "The ledger was not repaired" in capsys.readouterr().out
