from __future__ import annotations

import errno
import math
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

"""Consistent SQLite backups of the runtime decision database.

Uses the sqlite3 online backup API for a consistent snapshot while the
runtime WAL database is open. The snapshot is verified via
PRAGMA integrity_check before pruning old backups, and fsync durability
is enforced so a verified backup survives a crash.
"""

BACKUP_PREFIX = "parayabuck-"
BACKUP_SUFFIX = ".sqlite3"


class BackupVerificationError(RuntimeError):
    """Raised when a newly created backup fails integrity verification."""


class BackupDurabilityError(RuntimeError):
    """Raised when the backup file cannot be fsynced durably.

    The new snapshot is removed (or marked unusable) and previous verified
    backups are preserved; pruning is not performed.
    """


def _validate_retention(retention_days: float) -> float:
    if not math.isfinite(retention_days) or retention_days <= 0:
        raise ValueError("retention_days must be a positive number")
    return retention_days


def _backup_path(backup_dir: Path, stamp: datetime) -> Path:
    """Timestamped, collision-safe backup filename."""
    stem = f"{BACKUP_PREFIX}{stamp:%Y%m%dT%H%M%S%f}Z"
    candidate = backup_dir / f"{stem}{BACKUP_SUFFIX}"
    counter = 1
    while candidate.exists():
        candidate = backup_dir / f"{stem}-{counter}{BACKUP_SUFFIX}"
        counter += 1
    return candidate


def backup_database(
    source_path: str | Path,
    backup_dir: str | Path,
    *,
    retention_days: float,
    now: datetime | None = None,
) -> Path:
    """Create a consistent snapshot of ``source_path`` under ``backup_dir``.

    Uses the sqlite3 online backup API, which is safe on a WAL database that
    another process has open: the snapshot contains every committed
    transaction at the instant the backup runs and excludes any in-flight
    uncommitted work. After the snapshot, the backup is verified via
    ``verify_backup``; IF VALID, backups older than ``retention_days`` are
    pruned (oldest first) and the verified path is returned. IF INVALID, the
    new invalid backup is deleted, previous backups are preserved, and a
    ``BackupVerificationError`` is raised — pruning never happens before
    validation.

    Crash-consistency: a backup that is interrupted (process crash, power
    loss) may leave a partial file; callers should run ``verify_backup`` on
    the returned path before trusting it, and restore logic must prefer the
    last verified backup. The backup itself is atomic at the SQLite level —
    it never contains a half-committed decision+order transaction.
    """
    retention = _validate_retention(retention_days)
    source = Path(source_path)
    target_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Runtime database not found: {source}")
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = now or datetime.now(UTC)
    destination = _backup_path(target_dir, stamp)
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.execute("PRAGMA busy_timeout=5000")
        destination_connection.execute("PRAGMA busy_timeout=5000")
        # Online backup: copies a consistent snapshot, retrying on transient
        # locks while the runtime keeps writing. The source is never modified.
        source_connection.backup(destination_connection)
        # The backup API copies the source file header verbatim, so a backup
        # of a WAL runtime database is itself a WAL-format file and would
        # spawn -wal/-shm sidecars on the next open. Normalize the snapshot
        # to a standalone rollback-journal database: portable, restorable
        # read-only, and free of sidecar litter.
        destination_connection.execute("PRAGMA journal_mode=DELETE")
        # Ensure the destination is fsynced and not WAL: a standalone file
        # with no sidecars survives a crash without needing WAL recovery.
        destination_connection.commit()
    # Durability: fsync file and directory.
    # - File fsync failure means the snapshot is NOT durable: remove the new
    #   file (and sidecars), preserve previous verified backups, do NOT prune,
    #   and raise BackupDurabilityError.
    # - Directory fsync failure: only EINVAL/ENOTSUP/EOPNOTSUPP indicate the
    #   OS/filesystem does not support fsync on directories and are treated
    #   as best-effort (file fsync already succeeded). All other errors —
    #   notably EPERM/EACCES/EBADF on Linux which are permission failures —
    #   must be treated as durability failures: remove the new file
    #   (and sidecars), preserve previous backups, do NOT prune, and raise
    #   BackupDurabilityError.
    try:
        with open(destination, "rb") as fh:
            os.fsync(fh.fileno())
    except OSError as exc:
        # File fsync failed — snapshot not durable.
        for p in (destination, Path(f"{destination}-wal"), Path(f"{destination}-shm")):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
        raise BackupDurabilityError(f"Backup durability fsync failed for {destination}: {exc}") from exc
    try:
        dir_fd = os.open(target_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        # Only EINVAL and ENOTSUP/EOPNOTSUPP are best-effort.
        _best_effort_errnos: set[int] = {errno.EINVAL, errno.ENOTSUP}
        _eop = getattr(errno, "EOPNOTSUPP", None)
        if _eop is not None:
            _best_effort_errnos.add(_eop)
        if exc.errno in _best_effort_errnos:
            pass  # filesystem does not support directory fsync; file fsync already succeeded
        else:
            for p in (destination, Path(f"{destination}-wal"), Path(f"{destination}-shm")):
                try:
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            raise BackupDurabilityError(f"Backup directory fsync failed for {target_dir}: {exc}") from exc
    # Verify BEFORE prune: invalid backup must be deleted and must not prune.
    if not verify_backup(destination):
        # Delete the corrupt/incomplete new file; preserve previous backups.
        try:
            destination.unlink()
        except OSError:
            pass
        for sidecar in (Path(f"{destination}-wal"), Path(f"{destination}-shm")):
            try:
                if sidecar.is_file():
                    sidecar.unlink()
            except OSError:
                pass
        raise BackupVerificationError(f"Backup failed integrity check and was removed: {destination}")
    # Only after successful verification do we prune expired backups.
    prune_backups(target_dir, retention_days=retention, now=stamp)
    return destination


def verify_backup(path: str | Path) -> bool:
    """Run ``PRAGMA integrity_check`` on a backup; True when it reads cleanly."""
    try:
        connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def prune_backups(
    backup_dir: str | Path,
    *,
    retention_days: float,
    now: datetime | None = None,
) -> list[Path]:
    """Delete backups older than ``retention_days`` (oldest first).

    Files are ordered by modification time, not by name, so the timestamp in
    the filename is informational only. Returns the removed paths.
    """
    retention = _validate_retention(retention_days)
    cutoff = (now or datetime.now(UTC)).timestamp() - retention * 86_400
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    backups = sorted(
        (candidate for candidate in directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if candidate.is_file()),
        key=lambda candidate: candidate.stat().st_mtime,
    )
    removed: list[Path] = []
    for candidate in backups:
        if candidate.stat().st_mtime < cutoff:
            candidate.unlink()
            # Drop any WAL sidecar siblings left by pre-normalization backups.
            for sidecar in (Path(f"{candidate}-wal"), Path(f"{candidate}-shm")):
                if sidecar.is_file():
                    sidecar.unlink()
            removed.append(candidate)
    return removed


def latest_verified_backup(backup_dir: str | Path) -> Path | None:
    """Return the most recent verified backup in ``backup_dir``, or None."""
    directory = Path(backup_dir)
    if not directory.is_dir():
        return None
    backups = sorted(
        (candidate for candidate in directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if candidate.is_file()),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for candidate in backups:
        if verify_backup(candidate):
            return candidate
    return None


__all__ = [
    "BACKUP_PREFIX",
    "BACKUP_SUFFIX",
    "BackupDurabilityError",
    "BackupVerificationError",
    "backup_database",
    "latest_verified_backup",
    "prune_backups",
    "verify_backup",
]
