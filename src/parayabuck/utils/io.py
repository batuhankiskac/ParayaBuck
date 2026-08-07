"""Filesystem helpers."""

from __future__ import annotations

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
    try:
        if mode is not None:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if mode is not None:
            os.chmod(path, mode)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = ["atomic_write"]
