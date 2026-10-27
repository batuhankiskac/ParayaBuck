"""Consistent SQLite backups of the runtime decision database.

Uses the sqlite3 online backup API for a consistent snapshot while the
runtime WAL database is open. The snapshot is verified via
PRAGMA integrity_check before pruning old backups, and fsync durability
is enforced so a verified backup survives a crash.
"""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import shlex
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.execution.alpaca_models import RiskStatePersistenceError
from parayabuck.execution.risk_peak import check_peak_record
from parayabuck.live_safety import HALT_MARKER_FILENAME, operator_halt_marker_path, read_operator_halt
from parayabuck.utils.io import sweep_orphan_temp_files

log = logging.getLogger("parayabuck.backup")

# Optional off-host copy of each verified snapshot, e.g.
# ``gsutil cp {path} gs://bucket/``. Unset (the default) means no copy. The
# template is formatted with the quoted snapshot path, split with shlex and run
# without a shell; a non-zero exit, timeout or malformed template is recorded as
# ``backup_failed`` (reason ``offhost_copy_failed``) and never fails the local backup.
OFFHOST_CMD_ENV = "PARAYABUCK_BACKUP_OFFHOST_CMD"
OFFHOST_TIMEOUT_SECONDS = 900.0
_OFFHOST_STDERR_MAX = 300
BACKUP_PREFIX = "parayabuck-"
BACKUP_SUFFIX = ".sqlite3"
# A labelled backup (the pre-deploy snapshot the runbook requires) is
# published as ``parayabuck-keep-<label>-<stamp>.sqlite3``. It does not
# match AUTO_BACKUP_GLOB, so retention pruning can never delete it while a
# late rollback may still need it.
KEEP_PREFIX = f"{BACKUP_PREFIX}keep-"
AUTO_BACKUP_GLOB = f"{BACKUP_PREFIX}[0-9]*{BACKUP_SUFFIX}"
# The drawdown peak lives in these sidecars next to the decision database, not
# in the database. Each backup set carries a verified copy of every sidecar
# that exists, published as ``<snapshot stem>.<sidecar name>``.
PEAK_SIDECAR_NAMES = ("paper_equity_peak.json", "live_equity_peak.json")
AUTO_PEAK_GLOB = f"{BACKUP_PREFIX}[0-9]*_equity_peak.json"
# The operator halt marker (``parayabuck halt``) is a sidecar of the runtime
# directory too. A set carries a JSON copy of it whenever the marker exists, so
# a restore does not start unhalted. Published as ``<snapshot stem>.OPERATOR_HALT``.
HALT_COPY_NAME = HALT_MARKER_FILENAME
SET_SIDECAR_NAMES = (*PEAK_SIDECAR_NAMES, HALT_COPY_NAME)
AUTO_HALT_GLOB = f"{BACKUP_PREFIX}[0-9]*.{HALT_COPY_NAME}"
STAGE_PREFIX = ".parayabuck-stage-"
# Every temp file this module can leave in the backup directory starts with this
# prefix: staging files and the atomic_write temps of earlier runs.
ORPHAN_TEMP_PREFIX = f".{BACKUP_PREFIX}"
# A staging file younger than this may belong to a concurrent backup run; an
# older one is left by a run that was killed mid-copy.
STAGE_ORPHAN_MIN_AGE_SECONDS = 3600.0
# Freshness bound for the 6 h backup timer: two consecutive missed runs mean
# the backup path is broken, and a later database loss would force a restore
# from a stale snapshot. Checked by `parayabuck doctor`.
MAX_BACKUP_AGE_HOURS = 12.0
_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class BackupVerificationError(RuntimeError):
    """Raised when a newly created backup fails integrity verification."""


class BackupSourceMissing(FileNotFoundError):
    """The runtime database does not exist yet (first boot, before any decision).

    Nothing is written. ``backup_database`` only raises it; it does not alert.
    The CLI checks the source first and records ``backup_failed``, because the
    timer is unattended and a missing database is a failed backup. Subclasses
    FileNotFoundError so existing handlers keep working.
    """


class BackupDurabilityError(RuntimeError):
    """Raised when the backup file cannot be fsynced durably.

    The new snapshot is removed (or marked unusable) and previous verified
    backups are preserved; pruning is not performed.
    """


class OffhostCopyError(RuntimeError):
    """The configured off-host copy command failed, timed out or was malformed."""


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


def _set_sidecar_path(snapshot: Path, name: str) -> Path:
    """Where the set member ``name`` of ``snapshot`` lives."""
    return snapshot.with_name(f"{snapshot.stem}.{name}")


def _backup_path(
    backup_dir: Path, stamp: datetime, *, label: str | None = None, sidecar_names: list[str] | tuple[str, ...] = ()
) -> Path:
    """Timestamped, collision-safe backup filename.

    A labelled backup is published as ``parayabuck-keep-<label>-<stamp>``
    so retention pruning never removes an operator-requested snapshot. A name
    is taken when the snapshot or one of its set members already exists.
    """
    stem = f"{KEEP_PREFIX}{label}-" if label else BACKUP_PREFIX
    stem += f"{stamp:%Y%m%dT%H%M%S%f}Z"
    candidate = backup_dir / f"{stem}{BACKUP_SUFFIX}"
    counter = 1
    while any(path.exists() for path in (candidate, *(_set_sidecar_path(candidate, name) for name in sidecar_names))):
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


def _valid_peak_payload(payload: str) -> bool:
    """True when ``payload`` is a peak record the runtime accepts.

    Uses the runtime's own check (``check_peak_record``), so a sidecar the backup
    keeps is one the engine would load.
    """
    try:
        check_peak_record(json.loads(payload), "<backup sidecar>")
    except (ValueError, RiskStatePersistenceError):
        return False
    return True


def _valid_halt_copy(payload: str) -> bool:
    """True when ``payload`` is a halt record copy: a JSON object."""
    try:
        return isinstance(json.loads(payload), dict)
    except ValueError:
        return False


def _record_sidecar_failure(path: Path, detail: str, *, reason: str = "sidecar_invalid") -> None:
    """Log one sidecar left out of a backup set and journal it as ``backup_failed``.

    The set still publishes: the snapshot is verified on its own, and only the
    named sidecar is missing from it.
    """
    log.error("Backup set written without %s: %s", path.name, detail)
    try:
        from parayabuck.monitoring.alerts import emit

        emit(
            "backup_failed",
            message=f"Backup set written without {path.name}: {detail}",
            reason=reason,
            sidecar_path=str(path),
        )
    except Exception:  # pragma: no cover - alerting must never mask the backup result
        log.exception("Could not emit backup_failed for the sidecar %s", path.name)


def _stage_sidecar(target_dir: Path, name: str, payload: str) -> Path | None:
    """Write ``payload`` to a staging file in the backup directory and read it back.

    Returns the staging path, or None when the copy does not read back identical;
    the failed staging file is removed in that case.
    """
    fd, staged_name = tempfile.mkstemp(prefix=STAGE_PREFIX, suffix=f".{name}", dir=target_dir)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        written = staged.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _remove_candidate(staged)
        return None
    if written != payload:
        _remove_candidate(staged)
        return None
    return staged


def _stage_set_sidecars(source_dir: Path, target_dir: Path, staged: list[tuple[str, Path]]) -> None:
    """Stage every set member that exists beside the database, each read back before publication.

    A peak sidecar that is unreadable or fails the runtime's check is left out of
    the set and recorded as ``backup_failed`` (reason ``sidecar_invalid``). A
    missing sidecar is skipped silently: a host that has not traded has none.
    The halt marker is copied whenever it exists, as ``read_operator_halt``
    reads it, so an unreadable marker is still kept as a halt. Staged paths are
    appended to ``staged`` as they are written so the caller can clean up.
    """
    for name in PEAK_SIDECAR_NAMES:
        sidecar = source_dir / name
        try:
            payload = sidecar.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as exc:
            _record_sidecar_failure(sidecar, f"unreadable ({exc})")
            continue
        if not _valid_peak_payload(payload):
            _record_sidecar_failure(sidecar, "failed the peak-record check")
            continue
        stage = _stage_sidecar(target_dir, name, payload)
        if stage is None:
            _record_sidecar_failure(sidecar, "copy did not read back identical", reason="sidecar_copy_failed")
            continue
        staged.append((name, stage))
    marker = operator_halt_marker_path(source_dir)
    record = read_operator_halt(marker)
    if record is not None:
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        stage = _stage_sidecar(target_dir, HALT_COPY_NAME, payload)
        if stage is None:
            _record_sidecar_failure(marker, "copy did not read back identical", reason="sidecar_copy_failed")
        else:
            staged.append((HALT_COPY_NAME, stage))


def _file_mtime(path: Path) -> float | None:
    """Modification time of a regular file; None when it vanished or cannot be read."""
    try:
        if not path.is_file():
            return None
        return path.stat().st_mtime
    except OSError:
        return None


def _set_sidecars(snapshot: Path) -> list[Path]:
    """The set members that exist next to ``snapshot`` (regular files only)."""
    return [path for path in (_set_sidecar_path(snapshot, name) for name in SET_SIDECAR_NAMES) if path.is_file()]


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

    The set members (peak sidecars and the operator halt copy) are staged and
    checked first. A member that is invalid is left out and alerted, and the
    snapshot still publishes. A failure of the snapshot itself removes the new
    set.

    Crash-consistency: construction uses a same-directory staging file, and
    only a closed, durable, verified snapshot is published under the normal
    backup filename. Members are linked before the snapshot, and the snapshot's
    hard link is the commit point: a crash before it leaves at most orphan
    members, which prune ages out, and never a snapshot without its members.
    The backup itself is atomic at the SQLite level — it never contains a
    half-committed decision+order transaction.
    """
    retention = _validate_retention(retention_days)
    keep_label = _validate_label(label)
    source = Path(source_path)
    target_dir = Path(backup_dir)
    if not source.is_file():
        # Before the first decision there is nothing to back up. Raising the
        # dedicated subclass lets the caller skip without an alert.
        log.warning("Backup skipped: runtime database %s does not exist yet", source)
        raise BackupSourceMissing(f"Runtime database not found: {source}")
    target_dir.mkdir(parents=True, exist_ok=True)
    # A run killed mid-copy (SIGKILL, power loss) leaves staging files, and an
    # older run left atomic_write temps for its sidecar copies. Sweep the ones
    # from earlier runs; the age floor spares a file a concurrent run is writing.
    for stale in sweep_orphan_temp_files(target_dir, ORPHAN_TEMP_PREFIX, min_age_seconds=STAGE_ORPHAN_MIN_AGE_SECONDS):
        log.info("Removed orphan backup temp file %s", stale.name)

    stamp = now or datetime.now(UTC)
    stage_fd, stage_name = tempfile.mkstemp(prefix=STAGE_PREFIX, suffix=".tmp", dir=target_dir)
    stage = Path(stage_name)
    try:
        os.close(stage_fd)
    except BaseException:
        _remove_candidate(stage)
        raise
    destination: Path | None = None
    staged: list[tuple[str, Path]] = []
    set_copies: list[Path] = []
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
        #   file (and its set members), preserve previous verified backups, do
        #   NOT prune, and raise BackupDurabilityError.
        # - Directory fsync failure: only EINVAL/ENOTSUP/EOPNOTSUPP indicate the
        #   OS/filesystem does not support fsync on directories and are treated
        #   as best-effort (file fsync already succeeded). All other errors —
        #   notably EPERM/EACCES/EBADF on Linux which are permission failures —
        #   must be treated as durability failures: remove the new file
        #   (and its set members), preserve previous backups, do NOT prune, and
        #   raise BackupDurabilityError.
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

        _stage_set_sidecars(source.parent, target_dir, staged)
        sidecar_names = [name for name, _ in staged]
        # Publication order: members first, the snapshot last. A hard link
        # publishes an already verified inode without overwriting a backup
        # selected concurrently; a name collision on the snapshot re-selects a
        # suffixed name together with its members.
        while True:
            candidate = _backup_path(target_dir, stamp, label=keep_label, sidecar_names=sidecar_names)
            for name, staged_path in staged:
                copy = _set_sidecar_path(candidate, name)
                os.link(staged_path, copy)
                set_copies.append(copy)
            try:
                _fsync_directory(target_dir)
            except OSError as exc:
                raise BackupDurabilityError(f"Backup member fsync failed for {target_dir}: {exc}") from exc
            try:
                os.link(stage, candidate)
            except FileExistsError:
                for copy in set_copies:
                    _remove_candidate(copy)
                set_copies.clear()
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
        for copy in set_copies:
            _remove_candidate(copy)
        raise
    finally:
        # The published members are hard links, so dropping the staging names
        # leaves the set intact; on failure this removes the unpublished copies.
        for _, staged_path in staged:
            _remove_candidate(staged_path)

    assert destination is not None
    # Only after successful verification do we prune expired backups. A prune
    # failure is logged and never blocks the verified set or its off-host copy.
    try:
        prune_backups(target_dir, retention_days=retention, now=stamp)
    except Exception:
        log.exception("Backup retention prune failed in %s; the new backup is kept", target_dir)
    # The local set is verified and kept either way; an off-host failure is
    # recorded as an alert (backup_failed) instead of discarding it. The sidecars
    # of the previous automatic set are sent again: a sidecar whose off-host copy
    # failed then is retried on this run.
    for artifact in (destination, *set_copies, *_previous_set_sidecars(target_dir, destination)):
        try:
            copy_offhost(artifact)
        except OffhostCopyError as exc:
            _record_offhost_failure(artifact, exc)
    return destination


def _previous_set_sidecars(backup_dir: Path, current: Path) -> list[Path]:
    """Sidecar members of the newest automatic set other than ``current``.

    Only the members are repeated, not the snapshot: they are small, and a set
    whose sidecars never reached the off-host copy is the failure this covers.
    """
    previous: Path | None = None
    newest: float | None = None
    for candidate in backup_dir.glob(AUTO_BACKUP_GLOB):
        if candidate == current:
            continue
        mtime = _file_mtime(candidate)
        if mtime is not None and (newest is None or mtime > newest):
            previous, newest = candidate, mtime
    return [] if previous is None else _set_sidecars(previous)


def offhost_command(template: str, path: str | Path) -> list[str]:
    """Format the copy template with the quoted snapshot path and split it into argv."""
    try:
        formatted = template.format(path=shlex.quote(str(path)))
    except (KeyError, IndexError, ValueError) as exc:
        raise OffhostCopyError(f"{OFFHOST_CMD_ENV} has an invalid placeholder: {exc}") from None
    try:
        argv = shlex.split(formatted)
    except ValueError as exc:
        raise OffhostCopyError(f"{OFFHOST_CMD_ENV} is not a valid command: {exc}") from None
    if not argv:
        raise OffhostCopyError(f"{OFFHOST_CMD_ENV} formats to an empty command")
    return argv


def copy_offhost(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] | None = None,
    timeout: float = OFFHOST_TIMEOUT_SECONDS,
) -> bool:
    """Copy one verified snapshot off the host with ``PARAYABUCK_BACKUP_OFFHOST_CMD``.

    Returns False (nothing run) when the variable is unset or empty. Returns True
    after a zero exit. Raises ``OffhostCopyError`` otherwise. ``runner`` is the
    injectable ``subprocess.run``.
    """
    source = os.environ if env is None else env
    template = (source.get(OFFHOST_CMD_ENV) or "").strip()
    if not template:
        return False
    argv = offhost_command(template, path)
    run = runner or subprocess.run
    try:
        completed = run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise OffhostCopyError(f"off-host copy timed out after {timeout:g}s") from None
    except OSError as exc:
        raise OffhostCopyError(f"off-host copy could not start: {type(exc).__name__}") from None
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()[-_OFFHOST_STDERR_MAX:]
        detail = f": {stderr}" if stderr else ""
        raise OffhostCopyError(f"off-host copy exited {completed.returncode}{detail}")
    log.info("Off-host copy of %s completed", Path(path).name)
    return True


def _record_offhost_failure(path: Path, error: OffhostCopyError) -> None:
    """Journal the failure as the same ``backup_failed`` alert the timer already raises."""
    log.error("Off-host backup copy failed for %s: %s", path.name, error)
    try:
        from parayabuck.monitoring.alerts import emit

        emit(
            "backup_failed",
            message=f"Off-host backup copy failed for {path.name}: {error}",
            reason="offhost_copy_failed",
            backup_path=str(path),
        )
    except Exception:  # pragma: no cover - alerting must never mask the backup result
        log.exception("Could not emit backup_failed for the off-host copy failure")


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


def verify_backup_set(path: str | Path) -> bool:
    """``verify_backup`` for the snapshot and every set member it carries.

    A missing member is fine (a host that has not traded has no peak sidecar and
    no halt). A member that exists must be a regular file that passes its own
    check: the peak record check for a peak sidecar, a JSON object for the halt
    copy. Any other state fails the set, so a restore is never gated on the
    database alone.
    """
    candidate = Path(path)
    if not verify_backup(candidate):
        return False
    for name in SET_SIDECAR_NAMES:
        member = _set_sidecar_path(candidate, name)
        if not member.exists():
            continue
        if not member.is_file():
            return False
        try:
            payload = member.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        valid = _valid_halt_copy(payload) if name == HALT_COPY_NAME else _valid_peak_payload(payload)
        if not valid:
            return False
    return True


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
    A file that cannot be removed is logged and skipped; the rest of the prune
    still runs. Returns the removed paths.
    """
    retention = _validate_retention(retention_days)
    cutoff = (now or datetime.now(UTC)).timestamp() - retention * 86_400
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    # Set members age out with the snapshot they were taken with.
    aged: list[tuple[float, Path]] = []
    for candidate in (
        *directory.glob(AUTO_BACKUP_GLOB),
        *directory.glob(AUTO_PEAK_GLOB),
        *directory.glob(AUTO_HALT_GLOB),
    ):
        mtime = _file_mtime(candidate)
        if mtime is not None:
            aged.append((mtime, candidate))
    removed: list[Path] = []
    for mtime, candidate in sorted(aged, key=lambda item: item[0]):
        if mtime >= cutoff:
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            # Removed concurrently by another run; nothing left to do.
            continue
        except OSError as exc:
            log.warning("Could not prune %s: %s", candidate.name, exc)
            continue
        # Drop any WAL sidecar siblings left by pre-normalization backups.
        for sidecar in (Path(f"{candidate}-wal"), Path(f"{candidate}-shm")):
            try:
                if sidecar.is_file():
                    sidecar.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.warning("Could not prune %s: %s", sidecar.name, exc)
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
    "BACKUP_PREFIX",
    "BACKUP_SUFFIX",
    "HALT_COPY_NAME",
    "KEEP_PREFIX",
    "MAX_BACKUP_AGE_HOURS",
    "OFFHOST_CMD_ENV",
    "BackupDurabilityError",
    "BackupSourceMissing",
    "BackupVerificationError",
    "OffhostCopyError",
    "backup_database",
    "copy_offhost",
    "latest_backup_age_hours",
    "offhost_command",
    "prune_backups",
    "verify_backup",
    "verify_backup_set",
]
