"""Streaming SHA-256 helpers for research artifact provenance.

The historical dataset checkout can use either LF or CRLF line endings.  A
normalised digest must therefore treat a CRLF pair as one LF even when the
pair is split across two reads.  A lone CR remains a CR; this matches the
standard byte replacement policy while avoiding accidental content changes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, BinaryIO


def _update_normalized(digest: Any, chunk: bytes, pending_cr: bool) -> bool:
    """Update ``digest`` with ``chunk`` while carrying a trailing CR boundary."""
    if not chunk:
        return pending_cr
    normalized = bytearray()
    for byte in chunk:
        if pending_cr:
            if byte == 0x0A:
                normalized.append(0x0A)
                pending_cr = False
                continue
            normalized.append(0x0D)
            pending_cr = False
        if byte == 0x0D:
            pending_cr = True
        else:
            normalized.append(byte)
    digest.update(normalized)
    return pending_cr


def sha256_stream(stream: BinaryIO, *, normalize_newlines: bool = False, chunk_size: int = 1 << 20) -> str:
    """Return a streaming SHA-256 digest for a binary stream.

    ``chunk_size`` is intentionally configurable so callers and tests can
    exercise CRLF pairs at every possible read boundary.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    if not normalize_newlines:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
        return digest.hexdigest()

    pending_cr = False
    for chunk in iter(lambda: stream.read(chunk_size), b""):
        pending_cr = _update_normalized(digest, chunk, pending_cr)
    if pending_cr:
        digest.update(b"\r")
    return digest.hexdigest()


def sha256_bytes(data: bytes, *, normalize_newlines: bool = False, chunk_size: int = 1 << 20) -> str:
    """Return a SHA-256 digest for ``data`` using the shared streaming path."""
    from io import BytesIO

    return sha256_stream(BytesIO(data), normalize_newlines=normalize_newlines, chunk_size=chunk_size)


def sha256_file(path: Path, *, normalize_newlines: bool = False, chunk_size: int = 1 << 20) -> str:
    """Return a SHA-256 digest for a file using bounded reads."""
    with path.open("rb") as stream:
        return sha256_stream(stream, normalize_newlines=normalize_newlines, chunk_size=chunk_size)
