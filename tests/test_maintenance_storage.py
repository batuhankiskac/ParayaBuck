from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from parayabuck.storage import SQLiteStore, StorageInitializationError


@pytest.mark.parametrize(
    "statement",
    ["PRAGMA journal_mode", "PRAGMA foreign_keys", "PRAGMA busy_timeout", "PRAGMA synchronous"],
)
def test_pragma_read_error_closes_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str) -> None:
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []
    closed: list[sqlite3.Connection] = []
    failure = sqlite3.OperationalError("injected pragma read failure")

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            if sql == statement:
                raise failure
            return super().execute(sql, parameters)

        def close(self) -> None:
            closed.append(self)
            super().close()

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs, factory=Connection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        # Capture both types so the first assertion specifically proves cleanup.
        with pytest.raises((StorageInitializationError, sqlite3.Error)) as error:
            SQLiteStore(tmp_path / "runtime.sqlite3")
        assert closed == opened and len(closed) == 1
        assert isinstance(error.value, StorageInitializationError)
        assert error.value.__cause__ is failure
    finally:
        for connection in opened:
            sqlite3.Connection.close(connection)


def test_last_release_can_retry_failed_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = sqlite3.connect
    close_calls: list[sqlite3.Connection] = []

    class Connection(sqlite3.Connection):
        def close(self) -> None:
            close_calls.append(self)
            if len(close_calls) == 1:
                raise sqlite3.OperationalError("close failed once")
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real_connect(*a, **kw, factory=Connection))
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    store.acquire()
    try:
        with pytest.raises(sqlite3.OperationalError, match="close failed once"):
            store.release()
        store.release()
        assert len(close_calls) == 2
        with pytest.raises(RuntimeError, match="closed"):
            store.acquire()
    finally:
        sqlite3.Connection.close(store.connection)


def test_shared_ownership_only_closes_on_last_release(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    store.acquire()
    store.acquire()
    try:
        store.release()
        assert store.connection.execute("SELECT 1").fetchone() == (1,)
        store.release()
        store.release()
        with pytest.raises(sqlite3.ProgrammingError):
            store.connection.execute("SELECT 1")
        with pytest.raises(RuntimeError, match="closed"):
            store.acquire()
    finally:
        sqlite3.Connection.close(store.connection)


@pytest.mark.parametrize(
    "statement",
    [
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA busy_timeout=5000",
    ],
)
def test_pragma_setup_failure_preserves_cause_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    real_connect = sqlite3.connect
    failure = sqlite3.OperationalError("injected setup failure")
    closed: list[sqlite3.Connection] = []

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            if sql == statement:
                raise failure
            return super().execute(sql, parameters)

        def close(self) -> None:
            closed.append(self)
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real_connect(*a, **kw, factory=Connection))
    with pytest.raises(StorageInitializationError) as error:
        SQLiteStore(tmp_path / "runtime.sqlite3")
    assert error.value.__cause__ is failure
    assert len(closed) == 1


@pytest.mark.parametrize(
    ("statement", "reported_sql", "message"),
    [
        ("PRAGMA journal_mode", "SELECT 'delete'", "expected WAL"),
        ("PRAGMA foreign_keys", "SELECT 0", "expected ON"),
        ("PRAGMA foreign_keys", "SELECT 'invalid'", "expected ON"),
        ("PRAGMA busy_timeout", "SELECT 4999", "expected >= 5000"),
        ("PRAGMA busy_timeout", "SELECT 'invalid'", "expected >= 5000"),
        ("PRAGMA synchronous", "SELECT 2", "expected NORMAL"),
        ("PRAGMA synchronous", "SELECT NULL", "expected NORMAL"),
        ("PRAGMA synchronous", "SELECT 'OFF'", "expected NORMAL"),
    ],
)
def test_invalid_readback_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    reported_sql: str,
    message: str,
) -> None:
    real_connect = sqlite3.connect
    closed: list[sqlite3.Connection] = []

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            return super().execute(reported_sql if sql == statement else sql, parameters)

        def close(self) -> None:
            closed.append(self)
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real_connect(*a, **kw, factory=Connection))
    with pytest.raises(StorageInitializationError, match=message):
        SQLiteStore(tmp_path / "runtime.sqlite3")
    assert len(closed) == 1


def test_failed_cleanup_does_not_mask_initialization_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = sqlite3.connect
    original = sqlite3.OperationalError("initial failure")

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            raise original

        def close(self) -> None:
            super().close()
            raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: real_connect(*a, **kw, factory=Connection))
    with pytest.raises(StorageInitializationError) as error:
        SQLiteStore(tmp_path / "runtime.sqlite3")
    assert error.value.__cause__ is original
