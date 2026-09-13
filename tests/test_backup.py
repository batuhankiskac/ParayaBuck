from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backup import (
    BackupDurabilityError,
    backup_database,
    latest_verified_backup,
    prune_backups,
    verify_backup,
)
from parayabuck.storage import SQLiteStore


def _make_runtime_db(path: Path, *, rows: int = 3) -> SQLiteStore:
    """Open a WAL-mode runtime database exactly like the ledger does, with
    committed rows held in the WAL (the store stays open, so no checkpoint)."""
    store = SQLiteStore(path)  # PRAGMA journal_mode=WAL
    store.acquire()
    connection = store.connection
    connection.execute("CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO decisions(payload) VALUES (?)",
        ((f"payload-{index}",) for index in range(rows)),
    )
    connection.commit()
    return store


def _table_counts(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])


def test_backup_produces_valid_restorable_database(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source, rows=3)
    try:
        # The runtime connection is still open with committed rows in the WAL.
        backup_path = backup_database(
            source,
            tmp_path / "backups",
            retention_days=7,
            now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        )
    finally:
        store.release()

    assert backup_path.is_file()
    assert backup_path.name.startswith("parayabuck-")
    assert backup_path.name.endswith(".sqlite3")
    assert verify_backup(backup_path) is True
    assert latest_verified_backup(backup_path.parent) == backup_path
    # The snapshot is normalized to a standalone rollback-journal database:
    # no -wal/-shm sidecars, restorable read-only without extra files.
    assert not Path(f"{backup_path}-wal").exists()
    assert not Path(f"{backup_path}-shm").exists()
    assert sqlite3.connect(backup_path).execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    restored = sqlite3.connect(backup_path)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert _table_counts(restored) == 3
        assert restored.execute("SELECT payload FROM decisions ORDER BY id LIMIT 1").fetchone() == ("payload-0",)
    finally:
        restored.close()


def test_backup_snapshot_excludes_uncommitted_transactions(tmp_path: Path) -> None:
    """The online backup captures only committed state: an in-flight
    transaction on the live connection must not appear in the snapshot."""
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source, rows=2)
    try:
        connection = store.connection
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO decisions(payload) VALUES ('uncommitted')")
        # Connection remains mid-transaction while the backup runs.
        first = backup_database(
            source,
            tmp_path / "backups",
            retention_days=7,
            now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        )
        restored = sqlite3.connect(first)
        try:
            assert _table_counts(restored) == 2
        finally:
            restored.close()

        connection.rollback()
        connection.execute("INSERT INTO decisions(payload) VALUES ('committed-after')")
        connection.commit()
        second = backup_database(
            source,
            tmp_path / "backups",
            retention_days=7,
            now=datetime(2026, 8, 12, 12, 1, tzinfo=UTC),
        )
        restored = sqlite3.connect(second)
        try:
            assert _table_counts(restored) == 3
            assert any(row[0] == "committed-after" for row in restored.execute("SELECT payload FROM decisions"))
        finally:
            restored.close()
    finally:
        store.release()


def test_retention_removes_oldest_backups(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source, rows=1)
    backup_dir = tmp_path / "backups"
    # Anchor retention to the real clock so pruning cutoffs and the actual
    # file mtimes always line up.
    anchor = datetime.now(UTC)
    try:
        ages = [timedelta(days=10), timedelta(days=2), timedelta(days=0)]
        created: list[Path] = []
        for offset in ages:
            stamp = anchor - offset
            created.append(backup_database(source, backup_dir, retention_days=7, now=stamp))
        # Retention sorts by file mtime, not by the name timestamp.
        for path, offset in zip(created, ages, strict=True):
            old = (anchor - offset).timestamp()
            os.utime(path, (old, old))
        removed = prune_backups(backup_dir, retention_days=7, now=anchor)
    finally:
        store.release()

    assert sorted(path.name for path in removed) == [created[0].name]
    remaining = sorted(path.name for path in backup_dir.iterdir())
    assert remaining == sorted(path.name for path in created[1:])


def test_backup_itself_prunes_older_than_retention(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source, rows=1)
    backup_dir = tmp_path / "backups"
    anchor = datetime.now(UTC)
    try:
        stale = backup_database(source, backup_dir, retention_days=7, now=anchor - timedelta(days=14))
        os.utime(stale, (anchor.timestamp() - 14 * 86_400,) * 2)
        fresh = backup_database(source, backup_dir, retention_days=7, now=anchor)
    finally:
        store.release()

    assert not stale.exists()
    assert fresh.exists()
    assert sorted(path.name for path in backup_dir.iterdir()) == [fresh.name]


def test_backup_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Runtime database not found"):
        backup_database(tmp_path / "missing.sqlite3", tmp_path / "backups", retention_days=7)


def test_backup_rejects_invalid_retention(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source, rows=1)
    try:
        with pytest.raises(ValueError, match="retention_days"):
            backup_database(source, tmp_path / "backups", retention_days=0)
    finally:
        store.release()


def test_verify_backup_rejects_corrupt_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "parayabuck-corrupt.sqlite3"
    corrupt.write_bytes(b"this is not a sqlite database" * 8)
    assert verify_backup(corrupt) is False
    assert verify_backup(tmp_path / "missing.sqlite3") is False


def test_verify_backup_rejects_zero_byte_file(tmp_path: Path) -> None:
    empty = tmp_path / "parayabuck-empty.sqlite3"
    empty.touch()

    assert verify_backup(empty) is False


def test_latest_verified_backup_skips_newer_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source)
    backup_dir = tmp_path / "backups"
    try:
        genuine = backup_database(source, backup_dir, retention_days=7)
        invalid = backup_dir / "parayabuck-newer.sqlite3"
        invalid.touch()
        newer = genuine.stat().st_mtime + 1
        os.utime(invalid, (newer, newer))

        assert latest_verified_backup(backup_dir) == genuine
    finally:
        store.release()


def test_failed_online_backup_leaves_no_discoverable_artifact_and_does_not_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source)
    backup_dir = tmp_path / "backups"
    try:
        previous = backup_database(source, backup_dir, retention_days=7)
        expired = backup_dir / "parayabuck-19990101T000000000000Z.sqlite3"
        shutil.copy(previous, expired)
        old = datetime.now(UTC).timestamp() - 20 * 86_400
        os.utime(expired, (old, old))
        before = set(backup_dir.glob("parayabuck-*.sqlite3"))

        real_connect = sqlite3.connect

        class FailingSourceConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self.connection = connection

            def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
                return self.connection.execute(*args, **kwargs)

            def backup(self, destination: sqlite3.Connection) -> None:
                destination.execute("CREATE TABLE incomplete (id INTEGER)")
                destination.commit()
                raise sqlite3.OperationalError("forced backup failure")

            def close(self) -> None:
                self.connection.close()

        def failing_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(database, *args, **kwargs)
            if Path(database) == source:
                return FailingSourceConnection(connection)  # type: ignore[return-value]
            return connection

        monkeypatch.setattr("parayabuck.backup.sqlite3.connect", failing_connect)

        with pytest.raises(sqlite3.OperationalError, match="forced backup failure"):
            backup_database(source, backup_dir, retention_days=7)

        assert set(backup_dir.glob("parayabuck-*.sqlite3")) == before
        assert not list(backup_dir.glob(".parayabuck-stage-*"))
        assert previous.exists()
        assert expired.exists()
    finally:
        store.release()


def test_backup_publication_does_not_overwrite_concurrent_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source)
    backup_dir = tmp_path / "backups"
    stamp = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    real_link = os.link
    collided: Path | None = None

    def collide_once(source_path: str | Path, destination_path: str | Path) -> None:
        nonlocal collided
        if collided is None:
            collided = Path(destination_path)
            shutil.copyfile(source_path, collided)
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), collided)
        real_link(source_path, destination_path)

    try:
        monkeypatch.setattr(os, "link", collide_once)
        published = backup_database(source, backup_dir, retention_days=7, now=stamp)
    finally:
        store.release()

    assert collided is not None
    assert published != collided
    assert published.name.endswith("-1.sqlite3")
    assert verify_backup(collided)
    assert verify_backup(published)


def test_prune_backups_missing_directory_is_noop(tmp_path: Path) -> None:
    assert prune_backups(tmp_path / "nope", retention_days=7) == []


def test_backup_directory_fsync_eacces_raises_and_preserves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directory fsync EACCES must be treated as durability failure (Linux)."""
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source)
    try:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Previous verified backup that must be preserved.
        prev = backup_database(source, backup_dir, retention_days=7)
        assert prev.exists()
        # Expired backup that would be pruned if prune ran — must survive the failure.
        expired = backup_dir / "parayabuck-19990101T000000000000Z.sqlite3"
        shutil.copy(prev, expired)
        old_mtime = prev.stat().st_mtime - 20 * 86_400
        os.utime(expired, (old_mtime, old_mtime))
        # Also age prev so we can distinguish, but keep it within retention.
        # Make prev look slightly older than now but still inside retention.
        prev_mtime = prev.stat().st_mtime - 1 * 86_400
        os.utime(prev, (prev_mtime, prev_mtime))
        before = set(p.name for p in backup_dir.iterdir())
        assert prev.name in before and expired.name in before
        calls = {"n": 0}

        def fake_fsync(fd: int) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # file fsync succeeds
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        monkeypatch.setattr(os, "fsync", fake_fsync)
        with pytest.raises(BackupDurabilityError, match="directory fsync"):
            backup_database(source, backup_dir, retention_days=7)
        after = set(p.name for p in backup_dir.iterdir())
        # New backup must have been cleaned up; previous and expired preserved, no prune.
        assert after == before, f"expected no net files added, before={before} after={after}"
        assert prev.exists()
        assert expired.exists()
        assert calls["n"] == 2
    finally:
        store.release()


def test_backup_directory_fsync_eperm_raises_and_preserves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directory fsync EPERM must be treated as durability failure (Linux)."""
    source = tmp_path / "runtime.sqlite3"
    store = _make_runtime_db(source)
    try:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        prev = backup_database(source, backup_dir, retention_days=7)
        assert prev.exists()
        expired = backup_dir / "parayabuck-19990101T000000000000Z.sqlite3"
        shutil.copy(prev, expired)
        old_mtime = prev.stat().st_mtime - 20 * 86_400
        os.utime(expired, (old_mtime, old_mtime))
        prev_mtime = prev.stat().st_mtime - 1 * 86_400
        os.utime(prev, (prev_mtime, prev_mtime))
        before = set(p.name for p in backup_dir.iterdir())
        calls = {"n": 0}

        def fake_fsync(fd: int) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            raise OSError(errno.EPERM, os.strerror(errno.EPERM))

        monkeypatch.setattr(os, "fsync", fake_fsync)
        with pytest.raises(BackupDurabilityError, match="directory fsync"):
            backup_database(source, backup_dir, retention_days=7)
        after = set(p.name for p in backup_dir.iterdir())
        assert after == before
        assert prev.exists()
        assert expired.exists()
        assert calls["n"] == 2
    finally:
        store.release()
