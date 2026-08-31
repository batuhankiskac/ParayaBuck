from __future__ import annotations

import hashlib

import pytest

from parayabuck.research.normalized_hash import sha256_bytes


@pytest.mark.parametrize(
    ("payload", "expected_normalized"),
    [
        (b"", b""),
        (b"alpha\nbeta\n", b"alpha\nbeta\n"),
        (b"alpha\r\nbeta\r\n", b"alpha\nbeta\n"),
        (b"alpha\r\nbeta\nlast\r\n", b"alpha\nbeta\nlast\n"),
        (b"prefix\r", b"prefix\r"),
        (b"left\r\nright\r\nend", b"left\nright\nend"),
    ],
)
def test_lf_normalized_sha256_matches_byte_replacement(payload: bytes, expected_normalized: bytes) -> None:
    expected = hashlib.sha256(expected_normalized).hexdigest()
    for chunk_size in (1, 2, 3, 7, 1024):
        assert sha256_bytes(payload, normalize_newlines=True, chunk_size=chunk_size) == expected


def test_streaming_normalization_handles_crlf_split_at_every_boundary() -> None:
    payload = b"a\r\nb\r\nc\r\n\r\nfinal"
    expected = hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()
    for chunk_size in range(1, len(payload) + 1):
        assert sha256_bytes(payload, normalize_newlines=True, chunk_size=chunk_size) == expected


def test_non_normalized_path_remains_byte_exact() -> None:
    payload = b"a\r\nb\r\n"
    assert sha256_bytes(payload, chunk_size=1) == hashlib.sha256(payload).hexdigest()


def test_chunk_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        sha256_bytes(b"x", chunk_size=0)
