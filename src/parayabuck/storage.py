from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class SQLiteStore:
    """Shared SQLite connection and transaction lock for one runtime database.

    The runtime database is opened once per process and shared between the
    execution ledger and the portfolio repository via ``store.connection`` and
    ``store.lock``. ``acquire``/``release`` track ownership so the last
    holder closes the connection; callers never close ``connection`` directly.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        self._references = 0
        self._closed = False

    def acquire(self) -> None:
        """Claim shared ownership of the connection; raises if already closed."""
        with self.lock:
            if self._closed:
                raise RuntimeError("SQLite store is closed")
            self._references += 1

    def release(self) -> None:
        """Release one ownership claim; closes the connection on last release."""
        with self.lock:
            if self._references <= 0:
                return
            self._references -= 1
            if self._references == 0 and not self._closed:
                self.connection.close()
                self._closed = True


def migrate_legacy_decision_orders(connection: sqlite3.Connection) -> bool:
    """Move the legacy journal ``orders`` table before ledger schema creation."""
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(orders)")}
    legacy_columns = {
        "client_order_id",
        "timestamp",
        "symbol",
        "state",
        "reason",
        "payload",
    }
    if not legacy_columns.issubset(columns) or "broker_order_id" in columns:
        return False
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_orders (
            client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
            state TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (client_order_id, timestamp, state)
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO decision_orders(
            client_order_id, timestamp, symbol, state, reason, payload
        )
        SELECT client_order_id, timestamp, symbol, state, reason, payload
        FROM orders
        """
    )
    connection.execute("DROP TABLE orders")
    return True


__all__ = ["SQLiteStore", "migrate_legacy_decision_orders"]
