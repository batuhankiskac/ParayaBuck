from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from parayabuck.backup import backup_database, verify_backup


def _database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE marker (value INTEGER)")
        connection.execute("INSERT INTO marker VALUES (42)")
        connection.commit()


@pytest.mark.parametrize("name", ["question?mark", "hash#mark", "percent%3Fmark", "white space", "Türkçe"])
def test_verify_reads_exact_filename_without_creating_files(tmp_path: Path, name: str) -> None:
    path = tmp_path / f"{name}.sqlite3"
    _database(path)
    before = set(tmp_path.iterdir())
    assert verify_backup(path) is True
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("name", ["question?mark", "hash#mark", "percent%3Fmark", "ordinary"])
def test_verify_rejects_corrupt_exact_file(tmp_path: Path, name: str) -> None:
    path = tmp_path / f"{name}.sqlite3"
    path.write_bytes(b"not a SQLite database" * 64)
    # A valid decoy must not be opened instead of the percent-encoded filename.
    _database(tmp_path / "percent?mark.sqlite3")
    before = set(tmp_path.iterdir())
    assert verify_backup(path) is False
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("name", ["missing?mark", "missing#mark", "missing%3Fmark", "ordinary"])
def test_verify_missing_path_never_creates_a_database(tmp_path: Path, name: str) -> None:
    before = set(tmp_path.iterdir())
    assert verify_backup(tmp_path / f"{name}.sqlite3") is False
    assert set(tmp_path.iterdir()) == before


def test_destination_open_failure_closes_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.sqlite3"
    _database(source)
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []
    closed: list[sqlite3.Connection] = []

    class Connection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(self)
            super().close()

    def connect(path: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if path != source:
            raise sqlite3.OperationalError("destination open failed")
        connection = real_connect(path, *args, **kwargs, factory=Connection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="destination open failed"):
            backup_database(source, tmp_path / "backups", retention_days=7)
        assert closed == opened and len(closed) == 1
    finally:
        for connection in opened:
            sqlite3.Connection.close(connection)


def test_destination_close_failure_still_closes_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.sqlite3"
    _database(source)
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []
    closed: list[sqlite3.Connection] = []

    class Connection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(self)
            super().close()
            if self is opened[1]:
                raise sqlite3.OperationalError("destination close failed")

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs, factory=Connection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="destination close failed"):
            backup_database(source, tmp_path / "backups", retention_days=7)
        assert closed == list(reversed(opened)) and len(closed) == 2
    finally:
        for connection in opened:
            sqlite3.Connection.close(connection)
