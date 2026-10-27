"""Filesystem helpers."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger("parayabuck.utils.io")

# Temp files younger than this may still be written by a live process; the orphan
# sweep only removes older ones (a process killed mid-write leaves them behind).
ORPHAN_TEMP_MIN_AGE_SECONDS = 3600.0


def _directory_fsync_unsupported(exc: OSError) -> bool:
    """True for errors meaning the filesystem cannot fsync a directory at all."""
    unsupported = {errno.EINVAL, errno.ENOTSUP}
    eopnotsupp = getattr(errno, "EOPNOTSUPP", None)
    if eopnotsupp is not None:
        unsupported.add(eopnotsupp)
    return exc.errno in unsupported


def _fsync_directory(directory: Path) -> None:
    """Fsync ``directory``; filesystems without directory fsync are a no-op."""
    # O_DIRECTORY ensures we fsync the directory itself, not a file.
    # Fall back to O_RDONLY alone on platforms without O_DIRECTORY.
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(directory, flags)
    except OSError as exc:
        if _directory_fsync_unsupported(exc):
            return
        raise
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        if _directory_fsync_unsupported(exc):
            return
        raise
    finally:
        os.close(directory_fd)


def _finish_committed_write(path: Path, mode: int | None) -> None:
    """Post-rename steps of ``atomic_write``.

    The rename is the commit point: the new content is already visible, so a
    failure here is logged instead of raised. Raising would make the caller
    report a failed write for a file that was in fact replaced (for example a
    mode change answered with HTTP 500 while the settings file already says
    the new value).
    """
    try:
        if mode is not None:
            os.chmod(path, mode)
        _fsync_directory(path.parent)
    except OSError as exc:
        log.warning("Wrote %s but a post-rename step failed (durability or mode may be stale): %s", path, exc)


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    """Atomically replace ``path`` with ``content``.

    Writes to a temporary file in the same directory, fsyncs it, replaces the
    target with ``os.replace`` (atomic on POSIX) and fsyncs the directory so
    the rename survives a crash. On any failure before the rename the temporary
    file is removed; the target is never left partially written.

    A symlinked destination is resolved first, so the regular file behind the
    link is replaced and the link itself is kept. Failures after the rename
    (chmod, directory fsync) are logged, not raised.
    """
    target = Path(os.path.realpath(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    fdopen_entered = False
    try:
        if mode is not None:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # fdopen took ownership of the descriptor: the with-block closes
            # it. Before this point (e.g. a failed fchmod) the finally below
            # must close it or every failed write leaks one fd forever.
            fdopen_entered = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if not fdopen_entered:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    _finish_committed_write(target, mode)


def sweep_orphan_temp_files(
    directory: str | Path,
    prefix: str,
    *,
    min_age_seconds: float = ORPHAN_TEMP_MIN_AGE_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """Remove regular files in ``directory`` named ``prefix*`` that are at least ``min_age_seconds`` old.

    Temp files from ``atomic_write`` (``.<name>.XXXX``) or from a process killed
    mid-write are never renamed onto their target, so nothing else removes
    them. The age floor keeps the sweep from deleting a temp file that a
    concurrent writer is still filling. The prefix must be non-empty so the
    sweep cannot match the whole directory. Per-file errors are logged and
    skipped. Returns the removed paths.
    """
    if not prefix:
        raise ValueError("orphan sweep prefix must be non-empty")
    base = Path(directory)
    if not base.is_dir():
        return []
    reference = time.time() if now is None else now
    removed: list[Path] = []
    for candidate in base.iterdir():
        if not candidate.name.startswith(prefix):
            continue
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if reference - candidate.stat().st_mtime < min_age_seconds:
                continue
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("Could not remove orphan temp file %s: %s", candidate, exc)
            continue
        removed.append(candidate)
    return removed


__all__ = ["ORPHAN_TEMP_MIN_AGE_SECONDS", "atomic_write", "sweep_orphan_temp_files"]
