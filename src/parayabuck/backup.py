from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

"""Consistent SQLite backups of the runtime decision database.

The runtime ledger is a WAL-mode SQLite database that stays open for the
lifetime of the process, so a raw file copy is NOT safe: committed frames may
still live in ``-wal`` when the main file is copied. This module uses the
``sqlite3`` online backup API (``Connection.backup``), which produces a
consistent snapshot of every committed transaction regardless of WAL state,
without requiring the runtime to stop or checkpoint.

Backups are timestamped files under a configured directory and pruned by
retention (oldest deleted first). Stdlib only.

Durability & crash-consistency semantics (WAL, synchronous=NORMAL, busy_timeout):
* Source databases are opened with ``busy_timeout=5000`` so a concurrent
  runtime write does not fail the backup with SQLITE_BUSY; the backup API
  retries internally while the runtime keeps writing.
* The snapshot is transaction-consistent: it contains every committed
  transaction at the instant the backup runs and excludes in-flight
  uncommitted work — a crash mid-backup never produces a half-decision file.
* The backup API copies the source file header verbatim, so a backup of a
  WAL runtime is itself WAL-format; it is normalized to DELETE mode
  (``PRAGMA journal_mode=DELETE``) so the snapshot is a standalone
  rollback-journal file with no ``-wal``/``-shm`` sidecars and is portable
  and restorable read-only.
* Corruption recovery: ``verify_backup`` runs ``PRAGMA integrity_check`` on
  the snapshot (read-only open); a backup that fails verification is
  discarded and the runtime falls back to the previous verified backup or
  fails closed per production policy.
* Concurrent handling: pruning deletes only files older than
  ``retention_days`` by mtime (oldest first) and never touches the
  just-created file; sidecar siblings (``-wal``/``-shm``) from
  pre-normalization backups are cleaned up.
"""

BACKUP_PREFIX = "parayabuck-"
BACKUP_SUFFIX = ".sqlite3"


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
    uncommitted work. After the snapshot, backups older than
    ``retention_days`` are pruned (oldest first). Returns the created backup
    path.

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
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
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
    finally:
        destination_connection.close()
        source_connection.close()

    prune_backups(target_dir, retention_days=retention, now=stamp)
    return destination


def verify_backup(path: str | Path) -> bool:
    """Run ``PRAGMA integrity_check`` on a backup; True when it reads cleanly."""
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
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


__all__ = ["BACKUP_PREFIX", "BACKUP_SUFFIX", "backup_database", "prune_backups", "verify_backup"]
