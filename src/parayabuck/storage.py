from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class StorageInitializationError(RuntimeError):
    """Raised when the SQLite runtime store cannot be initialized safely.

    Covers WAL not taking effect, foreign_keys off, busy_timeout too low,
    unexpected synchronous mode, or the parent directory being unwritable.
    """


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
      trade update + fill, rebalance phase transition) is wrapped in a single
      SQLite transaction (``BEGIN IMMEDIATE``/``with connection:``) so a crash
      cannot leave a half-decision, half-order, or half-fill state. See
      ``portfolio/runtime_repository.py:record_decision_with_orders`` and
      ``portfolio/runtime_repository.py:apply_trade_update`` for the
      authoritative boundaries and the crash-consistency test in
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
        # Ensure parent exists; fail clearly if it cannot be created or is
        # unwritable (read-only mount, permission error, stale NFS).
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageInitializationError(f"Cannot create database directory {self.path.parent}: {exc}") from exc
        # No os.access TOCTOU check — mkdir success does not guarantee writability
        # on read-only mounts. Rely on sqlite3.connect to fail with clear error.
        try:
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
        except sqlite3.Error as exc:
            raise StorageInitializationError(f"Cannot open SQLite database at {self.path}: {exc}") from exc
        # Setup and read-back verification share one cleanup boundary: a
        # partially initialized store must never retain an open connection.
        try:
            # WAL must be set before any other pragma that depends on it.
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=5000")
            journal_mode = self.connection.execute("PRAGMA journal_mode").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise StorageInitializationError(
                    f"SQLite journal_mode is {journal_mode[0] if journal_mode else None!r}, expected WAL"
                )
            foreign_keys = self.connection.execute("PRAGMA foreign_keys").fetchone()
            try:
                fk_val = int(foreign_keys[0]) if foreign_keys is not None else 0
            except Exception:
                fk_val = 0
            if fk_val != 1:
                raise StorageInitializationError(f"SQLite foreign_keys is {fk_val}, expected ON (1)")
            busy = self.connection.execute("PRAGMA busy_timeout").fetchone()
            try:
                busy_val = int(busy[0]) if busy is not None else 0
            except Exception:
                busy_val = 0
            if busy_val < 5000:
                raise StorageInitializationError(f"SQLite busy_timeout is {busy_val}, expected >= 5000")
            sync = self.connection.execute("PRAGMA synchronous").fetchone()
            raw_sync = sync[0] if sync is not None else None
            # Normalize: SQLite may return int (0|1|2|3) or string ("NORMAL").
            if isinstance(raw_sync, str):
                sync_ok = raw_sync.lower() == "normal" or raw_sync.strip() == "1"
            else:
                try:
                    sync_ok = int(raw_sync) == 1  # type: ignore[arg-type]
                except Exception:
                    sync_ok = False
            if not sync_ok:
                raise StorageInitializationError(f"SQLite synchronous is {raw_sync!r}, expected NORMAL (1)")
        except BaseException as exc:
            # Cleanup also runs on interruption; never mask the original cause.
            try:
                self.connection.close()
            except Exception:
                pass
            if isinstance(exc, sqlite3.Error):
                raise StorageInitializationError(f"Failed to configure SQLite pragmas: {exc}") from exc
            raise
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
            if self._references == 1 and not self._closed:
                self.connection.close()
                self._closed = True
            self._references -= 1


def migrate_legacy_decision_orders(connection: sqlite3.Connection) -> bool:
    """Drop an unmigrated legacy journal ``orders`` table.

    The 2026-07 decision journal (``client_order_id/timestamp/symbol/state/
    reason/payload``) has no production reader — forensics reads the current
    ``portfolio_orders``/``portfolio_fills`` tables (or the portfolio-ledger
    ``orders`` shape with ``broker_order_id``), never ``decision_orders``.
    Keeping a write-only ``decision_orders`` copy would preserve rows no code
    can interpret, so the legacy journal shape is dropped so old databases
    still open onto the portfolio-ledger schema.
    """
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
    connection.execute("DROP TABLE orders")
    return True


__all__ = ["SQLiteStore", "StorageInitializationError", "migrate_legacy_decision_orders"]
