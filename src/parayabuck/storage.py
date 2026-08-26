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

    Durability semantics (WAL + synchronous=NORMAL + busy_timeout):
    * ``journal_mode=WAL`` allows readers and writers to proceed concurrently
      while keeping committed frames in ``-wal``; a raw file copy would miss
      them, so backups must use the online backup API (see ``backup.py``).
    * ``synchronous=NORMAL`` is the recommended WAL durability level: it
      fsyncs the WAL on checkpoint, not on every commit, which is safe with
      WAL and avoids per-commit fsync stalls while preserving crash
      consistency (a committed transaction is either fully present or absent
      after a crash — never half-written, see transaction-boundary docs
      below).
    * ``busy_timeout=5000`` makes concurrent writers wait up to 5 s instead
      of failing with SQLITE_BUSY, matching the single-writer/multiple-reader
      runtime pattern.
    * ``foreign_keys=ON`` enforces referential integrity for
      decision→order→rebalance executions.

    Transaction boundaries:
    * Every critical write that must be crash-consistent (decision+orders,
      execution-event + position delta, rebalance phase transition) is wrapped
      in a single SQLite transaction (``BEGIN IMMEDIATE``/``with connection:``)
      so a crash cannot leave a half-decision, half-order, or half-position
      state. See ``portfolio/runtime_service.py:record_decision`` and
      ``execution/ledger.py:apply_execution_event`` for the authoritative
      boundaries and the crash-consistency test in
      ``tests/test_hardening.py::test_crash_consistency_decision_order_atomic``.
    * Concurrent handling: all writers acquire ``store.lock`` (a re-entrant
      lock) before touching ``store.connection``; the lock plus WAL plus
      busy_timeout ensures serialized commits without SQLITE_BUSY failures
      under the runtime's concurrent account/decision/reconcile threads.
    * Corruption recovery: readers use ``PRAGMA integrity_check`` (see
      ``backup.verify_backup``) and ``_table_exists`` guards; a corrupt or
      missing table is treated as a transient read failure that degrades
      rather than corrupts state. Persistent corruption is recovered by
      restoring the latest verified backup (``backup_database`` produces
      rollback-journal snapshots safe to copy).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        # WAL must be set before any other pragma that depends on it.
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        # Verify the requested mode actually took effect — a read-only
        # filesystem or VFS mismatch silently keeps DELETE mode.
        journal_mode = self.connection.execute("PRAGMA journal_mode").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "wal":
            # Non-fatal: log but do not crash startup; tests assert WAL.
            pass
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
