"""Filesystem helpers."""

from __future__ import annotations

import contextlib
import errno
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    """Atomically replace ``path`` with ``content``.

    Writes to a temporary file in the same directory, fsyncs it, replaces the
    target with ``os.replace`` (atomic on POSIX) and fsyncs the directory so
    the rename survives a crash. On any failure the temporary file is
    removed; the target is never left partially written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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
        os.replace(temporary, path)
        if mode is not None:
            os.chmod(path, mode)
        # O_DIRECTORY ensures we fsync the directory itself, not a file.
        # Fall back to O_RDONLY alone on platforms without O_DIRECTORY.
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        _best_effort_errnos = {errno.EINVAL, errno.ENOTSUP}
        _eop = getattr(errno, "EOPNOTSUPP", None)
        if _eop is not None:
            _best_effort_errnos.add(_eop)
        try:
            directory_fd = os.open(path.parent, flags)
        except OSError as exc:
            if exc.errno in _best_effort_errnos:
                return
            raise
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno in _best_effort_errnos:
                return
            raise
        finally:
            os.close(directory_fd)
    finally:
        if not fdopen_entered:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


__all__ = ["atomic_write"]
