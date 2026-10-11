"""Consistent SQLite backups of the runtime decision database.

Uses the sqlite3 online backup API for a consistent snapshot while the
runtime WAL database is open. The snapshot is verified via
PRAGMA integrity_check before pruning old backups, and fsync durability
is enforced so a verified backup survives a crash.
"""

from __future__ import annotations

import errno
import math
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

BACKUP_PREFIX = "parayabuck-"
BACKUP_SUFFIX = ".sqlite3"
# A labelled backup (the pre-deploy snapshot the runbook requires) is
# published as ``parayabuck-keep-<label>-<stamp>.sqlite3``. It matches
# BACKUP_GLOB (so it stays a valid restore candidate) but not
# AUTO_BACKUP_GLOB, so retention pruning can never delete it while a late
# rollback may still need it.
KEEP_PREFIX = f"{BACKUP_PREFIX}keep-"
AUTO_BACKUP_GLOB = f"{BACKUP_PREFIX}[0-9]*{BACKUP_SUFFIX}"
BACKUP_GLOB = f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"
# Freshness bound for the 6 h backup timer: two consecutive missed runs mean
# the backup path is broken, and a later database loss would force a restore
# from a stale snapshot. Checked by `parayabuck doctor`.
MAX_BACKUP_AGE_HOURS = 12.0
_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


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


def _validate_label(label: str | None) -> str | None:
    """Return a filesystem-safe backup label, or None when unlabelled."""
    if label is None:
        return None
    token = label.strip()
    if not _LABEL_PATTERN.fullmatch(token):
        raise ValueError("backup label must be 1-64 characters of [A-Za-z0-9_.-] starting with a letter or digit")
    return token


def _backup_path(backup_dir: Path, stamp: datetime, *, label: str | None = None) -> Path:
    """Timestamped, collision-safe backup filename.

    A labelled backup is published as ``parayabuck-keep-<label>-<stamp>``
    so retention pruning never removes an operator-requested snapshot.
    """
    stem = f"{KEEP_PREFIX}{label}-" if label else BACKUP_PREFIX
    stem += f"{stamp:%Y%m%dT%H%M%S%f}Z"
    candidate = backup_dir / f"{stem}{BACKUP_SUFFIX}"
    counter = 1
    while candidate.exists():
        candidate = backup_dir / f"{stem}-{counter}{BACKUP_SUFFIX}"
        counter += 1
    return candidate


def _remove_candidate(path: Path) -> None:
    """Best-effort removal for an unpublished candidate and its sidecars."""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if candidate.is_file():
                candidate.unlink()
        except OSError:
            pass


def _fsync_directory(directory: Path) -> None:
    """Fsync a directory, tolerating only filesystems that do not support it."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        best_effort_errnos: set[int] = {errno.EINVAL, errno.ENOTSUP}
        eopnotsupp = getattr(errno, "EOPNOTSUPP", None)
        if eopnotsupp is not None:
            best_effort_errnos.add(eopnotsupp)
        if exc.errno not in best_effort_errnos:
            raise


def backup_database(
    source_path: str | Path,
    backup_dir: str | Path,
    *,
    retention_days: float,
    now: datetime | None = None,
    label: str | None = None,
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
    validation. ``label`` marks an operator-requested snapshot (the
    pre-deploy one): it is published outside the automatic-backup glob and
    is therefore never pruned, matching the rollback runbook.

    Crash-consistency: construction uses a same-directory staging file, and
    only a closed, durable, verified snapshot is published under the normal
    backup filename. The backup itself is atomic at the SQLite level — it
    never contains a half-committed decision+order transaction.
    """
    retention = _validate_retention(retention_days)
    keep_label = _validate_label(label)
    source = Path(source_path)
    target_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Runtime database not found: {source}")
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = now or datetime.now(UTC)
    stage_fd, stage_name = tempfile.mkstemp(prefix=".parayabuck-stage-", suffix=".tmp", dir=target_dir)
    stage = Path(stage_name)
    try:
        os.close(stage_fd)
    except BaseException:
        _remove_candidate(stage)
        raise
    destination: Path | None = None
    try:
        with (
            closing(sqlite3.connect(source)) as source_connection,
            closing(sqlite3.connect(stage)) as destination_connection,
        ):
            source_connection.execute("PRAGMA busy_timeout=5000")
            destination_connection.execute("PRAGMA busy_timeout=5000")
            # Online backup: copies a consistent snapshot, retrying on transient
            # locks while the runtime keeps writing. The source is never modified.
            source_connection.backup(destination_connection)
            # Normalize the WAL snapshot to a standalone rollback-journal file.
            destination_connection.execute("PRAGMA journal_mode=DELETE")
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
            with open(stage, "rb") as fh:
                os.fsync(fh.fileno())
        except OSError as exc:
            raise BackupDurabilityError(f"Backup durability fsync failed for {stage}: {exc}") from exc
        try:
            _fsync_directory(target_dir)
        except OSError as exc:
            raise BackupDurabilityError(f"Backup directory fsync failed for {target_dir}: {exc}") from exc

        if not verify_backup(stage):
            raise BackupVerificationError(f"Backup failed integrity check: {stage}")

        # A hard link publishes the already verified inode without overwriting a
        # backup selected concurrently. Re-select a suffixed name on collision.
        while True:
            candidate = _backup_path(target_dir, stamp, label=keep_label)
            try:
                os.link(stage, candidate)
            except FileExistsError:
                continue
            destination = candidate
            break
        stage.unlink()
        try:
            _fsync_directory(target_dir)
        except OSError as exc:
            raise BackupDurabilityError(f"Backup publication fsync failed for {target_dir}: {exc}") from exc
    except BaseException:
        _remove_candidate(stage)
        if destination is not None:
            _remove_candidate(destination)
        raise

    assert destination is not None
    # Only after successful verification do we prune expired backups.
    prune_backups(target_dir, retention_days=retention, now=stamp)
    return destination


def verify_backup(path: str | Path) -> bool:
    """Run ``PRAGMA integrity_check`` on a backup; True when it reads cleanly."""
    candidate = Path(path)
    try:
        if candidate.stat().st_size == 0:
            return False
        with candidate.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                return False
        connection = sqlite3.connect(f"{candidate.resolve().as_uri()}?mode=ro", uri=True)
    except (OSError, sqlite3.Error):
        return False
    valid = False
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        valid = row is not None and row[0] == "ok"
    except sqlite3.Error:
        valid = False
    try:
        connection.close()
    except sqlite3.Error:
        return False
    return valid


def prune_backups(
    backup_dir: str | Path,
    *,
    retention_days: float,
    now: datetime | None = None,
) -> list[Path]:
    """Delete automatic backups older than ``retention_days`` (oldest first).

    Only the automatic (timer) backups are eligible: a labelled
    ``parayabuck-keep-*`` snapshot is an operator-requested restore point
    that retention must never delete. Files are ordered by modification time,
    not by name, so the timestamp in the filename is informational only.
    Returns the removed paths.
    """
    retention = _validate_retention(retention_days)
    cutoff = (now or datetime.now(UTC)).timestamp() - retention * 86_400
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    backups = sorted(
        (candidate for candidate in directory.glob(AUTO_BACKUP_GLOB) if candidate.is_file()),
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


def latest_backup_age_hours(backup_dir: str | Path, *, now: datetime | None = None) -> float | None:
    """Age in hours of the newest AUTOMATIC backup in ``backup_dir``; None when there is none.

    The freshness check behind ``parayabuck doctor``: a backup timer that stopped running weeks
    ago is otherwise indistinguishable from a healthy one until a database
    loss forces a restore from a stale snapshot. Only the timer's automatic
    snapshots count: a labelled ``parayabuck-keep-*`` snapshot is an
    operator-requested restore point that retention never prunes, so counting
    it would mask a dead timer for 12 h after every manual backup.
    """
    directory = Path(backup_dir)
    if not directory.is_dir():
        return None
    newest: float | None = None
    for candidate in directory.glob(AUTO_BACKUP_GLOB):
        if not candidate.is_file():
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    if newest is None:
        return None
    reference = (now or datetime.now(UTC)).timestamp()
    return max(0.0, (reference - newest) / 3600.0)


__all__ = [
    "AUTO_BACKUP_GLOB",
    "BACKUP_GLOB",
    "BACKUP_PREFIX",
    "BACKUP_SUFFIX",
    "KEEP_PREFIX",
    "MAX_BACKUP_AGE_HOURS",
    "BackupDurabilityError",
    "BackupVerificationError",
    "backup_database",
    "latest_backup_age_hours",
    "prune_backups",
    "verify_backup",
]
