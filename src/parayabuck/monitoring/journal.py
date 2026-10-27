"""Durable operational event journal — isolated from trading path.

Architecture choice: **Option A — Dedicated SQLite store**
(``data/runtime/observability.sqlite3``).

Rationale:
* Isolation: trading/runtime DB (``parayabuck.sqlite3``) uses WAL + busy_timeout
  for portfolio decisions/orders. Observability writes must never contend with
  those transactions or cause SQLITE_BUSY on the trading path. A separate file
  eliminates cross-domain lock contention.
* Queryability: SQLite gives indexed queries (time, kind, severity, correlation)
  needed for ``ops events`` / ``timeline`` without implementing JSONL indexing.
* Recovery: existing ``SQLiteStore`` utilities already handle WAL/synchronous
  durability and migrations — reuse without reinventing.
* Bounded async writer: single writer thread + bounded queue ensures emit is
  non-blocking and cannot OOM or spawn unbounded threads.

Alternative B (table in existing DB) rejected: couples observability failures
to trading lock state. Alternative C (JSONL) rejected: poor indexed query
performance and no atomic retention.

Isolation: ``emit()`` enqueues to a bounded queue; the single writer thread
drains to SQLite. Any exception increments ``event_journal_failed`` / dropped
counters and is logged — never propagated to the trading caller.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from parayabuck.monitoring.operational_event import OperationalEvent, mask_account_number, redact_fields

if TYPE_CHECKING:
    from parayabuck.config import ObservabilityConfig

# Halts, incidents and operator-attention signals. emit() commits these before it
# reports them written, so a crash right after the call cannot lose them.
# An explicit set, not the e-mail routing list: adding an e-mailed kind must not
# silently move it onto the caller's thread. Kinds that short-lived CLI processes
# emit (backup_failed, safety_lock_reset, risk_epoch_reset, ...) stay here, since
# an async queue would drop them when the process exits. Decision (2026-10-28): the
# set equals the EMAIL_ALERT_KINDS membership as of this audit, so today's durability
# is unchanged.
SAFETY_JOURNAL_KINDS: frozenset[str] = frozenset(
    {
        "runtime_stopped_unexpected",
        "runtime_start_failed",
        "circuit_breaker",
        "drawdown_halt",
        "risk_incident",
        "ambiguous_order",
        "paper_incident",
        "order_rejected",
        "exit_sell_rejected",
        "intent_alarm",
        "intent_recovered",
        "reconciliation_failure",
        "live_rearm_required",
        "live_authorization_expiring",
        "live_authorization_expired",
        "safety_lock_reset",
        "risk_epoch_reset",
        "live_started",
        "signal_adjustment_failure",
        "market_data_degraded",
        "data_provider_unavailable",
        "backup_failed",
    }
)

log = logging.getLogger("parayabuck.monitoring.journal")

DEFAULT_JOURNAL_PATH = "data/runtime/observability.sqlite3"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_EVENTS = 50000
DEFAULT_QUEUE_SIZE = 2048
SCHEMA_VERSION = 1

_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operational_events (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    session_id TEXT,
    account_id TEXT,
    mode TEXT,
    market TEXT,
    strategy TEXT,
    decision_id TEXT,
    client_order_id TEXT,
    broker_order_id TEXT,
    incident_id TEXT,
    correlation_id TEXT,
    fields_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_op_events_occurred_at ON operational_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_op_events_kind ON operational_events(kind);
CREATE INDEX IF NOT EXISTS idx_op_events_severity ON operational_events(severity);
CREATE INDEX IF NOT EXISTS idx_op_events_session ON operational_events(session_id);
CREATE INDEX IF NOT EXISTS idx_op_events_decision ON operational_events(decision_id);
CREATE INDEX IF NOT EXISTS idx_op_events_order ON operational_events(client_order_id);
CREATE INDEX IF NOT EXISTS idx_op_events_incident ON operational_events(incident_id);
"""


def _fields_json(fields: Mapping[str, object]) -> str:
    """Serialise event fields; a non-JSON value (datetime, Decimal) is stringified.

    ``default=str`` keeps one odd field from failing the whole batch insert and
    losing every other event flushed alongside it.
    """
    return json.dumps(dict(fields), separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass
class JournalStats:
    written: int = 0
    failed: int = 0
    dropped: int = 0
    last_success_at: str | None = None
    # A failure (write error or drop) newer than the last success means the
    # journal is failing now; an old one only describes history.
    last_failure_at: str | None = None


class EventJournal:
    """Bounded async durable journal."""

    def __init__(
        self,
        path: str | Path = DEFAULT_JOURNAL_PATH,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        self.path = Path(path)
        self.queue_size = max(1, int(queue_size))
        self.retention_days = max(0, int(retention_days))
        self.max_events = max(0, int(max_events))
        self._queue: queue.Queue[OperationalEvent | None] = queue.Queue(maxsize=self.queue_size)
        self._writer_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = JournalStats()
        self._writes_since_cleanup = 0
        self._started = False
        self._read_only = False
        self._ensure_schema()

    @classmethod
    def open_readonly(cls, path: str | Path) -> EventJournal:
        """A query-only handle that never creates the journal file or schema.

        Bypasses ``__init__`` (and its schema ensure) entirely so read paths
        — forensics, diagnostic bundles — cannot create a missing journal.
        ``query()`` already opens SQLite in ``mode=ro``; ``emit()`` on this
        handle is a no-op returning False.
        """
        self = cls.__new__(cls)
        self.path = Path(path)
        self.queue_size = 1
        self.retention_days = 0
        self.max_events = 0
        self._queue = queue.Queue(maxsize=1)
        self._writer_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stats = JournalStats()
        self._writes_since_cleanup = 0
        self._started = False
        self._read_only = True
        return self

    # ------------------------------------------------------------------ schema
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        try:
            conn = self._connect()
            try:
                conn.executescript(_JOURNAL_SCHEMA)
                cur = conn.execute("SELECT version FROM schema_version WHERE version=?", (SCHEMA_VERSION,))
                if cur.fetchone() is None:
                    conn.execute(
                        "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                        (SCHEMA_VERSION, datetime.now(UTC).isoformat()),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            log.warning("journal schema ensure failed", exc_info=True)

    # ---------------------------------------------------------------- writer
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._writer_thread = threading.Thread(target=self._writer_loop, name="pb-journal-writer", daemon=True)
            self._writer_thread.start()
            self._started = True

    def stop(self, *, timeout: float = 2.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop.set()
            # unblock writer
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            thread = self._writer_thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            if thread is not None and thread.is_alive():
                # The writer is still draining: keep the journal started (a later
                # start() must not spawn a second writer) and leave the queued
                # residue alone — it is still owned by the live writer.
                log.warning(
                    "journal stop timed out after %.3fs with %d events still queued",
                    timeout,
                    self._queue.qsize(),
                )
                return
            # A later start() must spawn a fresh writer instead of enqueuing onto a dead one
            self._started = False
            self._writer_thread = None
            leftover = self._queue.qsize()
            if leftover:
                self._stats.dropped += leftover
                self._stats.last_failure_at = datetime.now(UTC).isoformat()
                log.warning("journal stopped with %d queued events dropped", leftover)

    def _writer_loop(self) -> None:
        try:
            self._drain_loop()
        finally:
            with self._lock:
                if self._writer_thread is threading.current_thread():
                    # Writer retired on its own (e.g. stop() timed out): let a
                    # later start() spawn a fresh one instead of enqueuing blind.
                    self._writer_thread = None
                    self._started = False

    def _drain_loop(self) -> None:
        batch: list[OperationalEvent] = []
        # Keep draining after stop so queued events are written, not lost silently
        while not self._stop.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                if batch:
                    self._flush_batch(batch)
                    batch.clear()
                continue
            if item is None:
                if batch:
                    self._flush_batch(batch)
                break
            batch.append(item)
            # drain additional without blocking
            while len(batch) < 100:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    self._flush_batch(batch)
                    batch.clear()
                    return
                batch.append(nxt)
            self._flush_batch(batch)
            batch.clear()

    def _flush_batch(self, batch: list[OperationalEvent], *, maintain: bool = True) -> bool:
        """Write one batch in a single transaction; True only once it is committed.

        ``maintain`` runs the retention sweep. Only the writer thread passes True:
        a synchronous safety write runs on the caller's thread, which may hold a
        trading lock, and must not pay for a DELETE sweep.
        """
        if not batch:
            return True
        try:
            conn = self._connect()
            try:
                now_iso = datetime.now(UTC).isoformat()
                rows = [
                    (
                        e.event_id,
                        e.occurred_at.isoformat(),
                        e.kind,
                        e.severity,
                        e.message,
                        e.session_id,
                        e.account_id,
                        e.mode,
                        e.market,
                        e.strategy,
                        e.decision_id,
                        e.client_order_id,
                        e.broker_order_id,
                        e.incident_id,
                        e.correlation_id,
                        _fields_json(e.fields),
                        now_iso,
                    )
                    for e in batch
                ]
                conn.executemany(
                    """INSERT OR IGNORE INTO operational_events
                    (event_id, occurred_at, kind, severity, message,
                     session_id, account_id, mode, market, strategy,
                     decision_id, client_order_id, broker_order_id,
                     incident_id, correlation_id, fields_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()
                with self._lock:
                    self._stats.written += len(rows)
                    self._stats.last_success_at = now_iso
                    self._writes_since_cleanup += len(rows)
                    run_cleanup = maintain and self._writes_since_cleanup >= 100
                    if run_cleanup:
                        self._writes_since_cleanup = 0
                if run_cleanup:
                    self._retention_cleanup(conn)
            finally:
                conn.close()
            return True
        except Exception:
            with self._lock:
                self._stats.failed += len(batch)
                self._stats.last_failure_at = datetime.now(UTC).isoformat()
            log.warning("journal batch write failed for %d events", len(batch), exc_info=True)
            return False

    def _retention_cleanup(self, conn: sqlite3.Connection) -> None:
        try:
            if self.retention_days > 0:
                cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
                conn.execute("DELETE FROM operational_events WHERE occurred_at < ?", (cutoff,))
            if self.max_events > 0:
                cur = conn.execute("SELECT COUNT(*) FROM operational_events")
                count = int(cur.fetchone()[0])
                if count > self.max_events:
                    excess = count - self.max_events
                    conn.execute(
                        "DELETE FROM operational_events WHERE event_id IN "
                        "(SELECT event_id FROM operational_events ORDER BY occurred_at ASC LIMIT ?)",
                        (excess,),
                    )
            conn.commit()
        except Exception:
            log.warning("journal retention cleanup failed", exc_info=True)

    # ---------------------------------------------------------------- emit
    def emit(self, event: OperationalEvent) -> bool:
        """Enqueue event for durable write. Never raises, never blocks trading.

        Returns True if queued, False if dropped (queue full). Safety kinds
        (``SAFETY_JOURNAL_KINDS``) are committed in the caller's thread before
        emit returns, so True means the row is on disk. Fields are redacted here
        too, so every write path stores the redacted copy.
        A read-only handle never writes: emit is a no-op returning False.
        """
        if getattr(self, "_read_only", False):
            return False
        try:
            # The top-level account id is masked like the fields, so the column and
            # every reader carry the same last-four form.
            account_id = mask_account_number(event.account_id) if event.account_id else None
            event = replace(event, fields=redact_fields(event.fields), account_id=account_id)
            if event.kind in SAFETY_JOURNAL_KINDS:
                return self._flush_batch([event], maintain=False)
            # ensure writer started lazily
            if not self._started:
                self.start()
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            with self._lock:
                self._stats.dropped += 1
                self._stats.last_failure_at = datetime.now(UTC).isoformat()
            log.warning("journal queue full; dropping event kind=%s", event.kind)
            return False
        except Exception:
            with self._lock:
                self._stats.failed += 1
                self._stats.last_failure_at = datetime.now(UTC).isoformat()
            log.warning("journal emit failed for kind=%s", event.kind, exc_info=True)
            return False

    def query(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        kind: str | None = None,
        severity: str | None = None,
        market: str | None = None,
        decision_id: str | None = None,
        client_order_id: str | None = None,
        incident_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        order: str = "desc",
    ) -> list[OperationalEvent]:
        try:
            limit = max(1, int(limit))
        except Exception:
            limit = 100
        try:
            # Read-only: a query must never create the journal file.
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                sql = "SELECT * FROM operational_events WHERE 1=1"
                params: list[object] = []
                if since is not None:
                    sql += " AND occurred_at >= ?"
                    params.append(since.isoformat())
                if until is not None:
                    sql += " AND occurred_at <= ?"
                    params.append(until.isoformat())
                if kind is not None:
                    sql += " AND kind = ?"
                    params.append(kind)
                if severity is not None:
                    sql += " AND severity = ?"
                    params.append(severity)
                if market is not None:
                    sql += " AND market = ?"
                    params.append(market)
                if decision_id is not None:
                    sql += " AND decision_id = ?"
                    params.append(decision_id)
                if client_order_id is not None:
                    sql += " AND client_order_id = ?"
                    params.append(client_order_id)
                if incident_id is not None:
                    sql += " AND incident_id = ?"
                    params.append(incident_id)
                if session_id is not None:
                    sql += " AND session_id = ?"
                    params.append(session_id)
                sql += f" ORDER BY occurred_at {'DESC' if order == 'desc' else 'ASC'} LIMIT ?"
                params.append(limit)
                cur = conn.execute(sql, tuple(params))
                rows = cur.fetchall()
                events: list[OperationalEvent] = []
                for row in rows:
                    fields = {}
                    try:
                        fields = json.loads(row["fields_json"] or "{}")
                    except Exception:
                        fields = {"_corrupt_fields": row["fields_json"]}
                    # parse occurred_at
                    occ = row["occurred_at"]
                    try:
                        occ_dt = datetime.fromisoformat(occ)
                        if occ_dt.tzinfo is None:
                            occ_dt = occ_dt.replace(tzinfo=UTC)
                    except Exception:
                        occ_dt = datetime.now(UTC)
                    events.append(
                        OperationalEvent(
                            event_id=row["event_id"],
                            occurred_at=occ_dt,
                            kind=row["kind"],
                            severity=row["severity"],
                            message=row["message"],
                            session_id=row["session_id"],
                            account_id=row["account_id"],
                            mode=row["mode"],
                            market=row["market"],
                            strategy=row["strategy"],
                            decision_id=row["decision_id"],
                            client_order_id=row["client_order_id"],
                            broker_order_id=row["broker_order_id"],
                            incident_id=row["incident_id"],
                            correlation_id=row["correlation_id"],
                            fields=redact_fields(fields),
                        )
                    )
                return events
            finally:
                conn.close()
        except Exception:
            log.warning("journal query failed", exc_info=True)
            return []

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "event_journal_written": self._stats.written,
                "event_journal_failed": self._stats.failed,
                "event_journal_dropped": self._stats.dropped,
                "event_journal_queue_depth": self._queue.qsize(),
                "event_journal_last_success_at": self._stats.last_success_at,
                "event_journal_last_failure_at": self._stats.last_failure_at,
            }


# ----------------------------------------------------------------------
# Global singleton (process-local)
# ----------------------------------------------------------------------
_global_journal: EventJournal | None = None
_global_lock = threading.Lock()
# Set once ``configure_global_journal`` has decided this process's global
# journal, so a second runtime construction cannot replace (and orphan the
# writer thread of) the one already installed.
_global_is_configured = False


_isolated_journals: dict[str, EventJournal] = {}


def get_journal(path: str | Path | None = None) -> EventJournal:
    global _global_journal
    with _global_lock:
        if _global_journal is None:
            _global_journal = EventJournal(path or DEFAULT_JOURNAL_PATH)
        elif path is not None and str(path) != str(_global_journal.path):
            # allow tests to request isolated path; one handle (and one writer
            # thread) per path, not a new never-stopped writer on every call
            key = str(path)
            isolated = _isolated_journals.get(key)
            if isolated is None:
                isolated = _isolated_journals[key] = EventJournal(path)
            return isolated
        return _global_journal


def current_journal_path() -> Path:
    """Path of the process-global journal, without constructing it.

    The default path when no global was installed yet. Read paths use this so
    they look where this process writes, yet never create a journal file.
    """
    with _global_lock:
        journal = _global_journal
    return journal.path if journal is not None else Path(DEFAULT_JOURNAL_PATH)


def configure_global_journal(observability: ObservabilityConfig) -> EventJournal | None:
    """Install the process-global journal from the ``observability:`` config block.

    The engine writes every operational event through the one process-global
    journal, and the CLI/web read it back with ``ops events``. When nothing
    applied ``observability.journal_path`` (and the retention/max/queue knobs)
    here, that journal was built from the hard-coded defaults, so a configured
    custom path left ``ops events`` reading an empty file during an incident.
    Called once from the runtime constructor — never from a hot path.

    First decision wins. A global already sitting at an explicit non-default
    path belongs to whoever installed it (``get_journal(path)``, a forensics
    read, a test isolation harness) and configuration does not steal it.
    """
    global _global_journal, _global_is_configured
    with _global_lock:
        if _global_is_configured:
            return _global_journal
        if _global_journal is not None and str(_global_journal.path) != DEFAULT_JOURNAL_PATH:
            _global_is_configured = True
            return _global_journal
        _global_journal = EventJournal(
            observability.journal_path,
            queue_size=observability.event_queue_size,
            retention_days=observability.event_retention_days,
            max_events=observability.event_max_events,
        )
        _global_is_configured = True
        return _global_journal


def emit_journal(event: OperationalEvent, *, path: str | Path | None = None) -> bool:
    """Emit to global journal without raising (trading-safe)."""
    try:
        journal = get_journal(path)
        return journal.emit(event)
    except Exception:
        log.warning("emit_journal failed for kind=%s", event.kind, exc_info=True)
        return False


def _zero_journal_stats() -> dict[str, object]:
    return {
        "event_journal_written": 0,
        "event_journal_failed": 0,
        "event_journal_dropped": 0,
        "event_journal_queue_depth": 0,
        "event_journal_last_success_at": None,
        "event_journal_last_failure_at": None,
    }


def journal_stats(path: str | Path | None = None) -> dict[str, object]:
    """Process-local counters of the global journal; zeros when none was built.

    Counters live in memory, so a journal this process never constructed (or
    a different path) has nothing to report. Reading stats must never
    construct one either: ``EventJournal.__init__`` creates the file and its
    schema, which would materialise the default journal from a read path.
    """
    try:
        with _global_lock:
            journal = _global_journal
        if journal is None or (path is not None and str(path) != str(journal.path)):
            return _zero_journal_stats()
        return journal.stats()
    except Exception:
        return _zero_journal_stats()


__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_QUEUE_SIZE",
    "DEFAULT_RETENTION_DAYS",
    "EventJournal",
    "SCHEMA_VERSION",
    "configure_global_journal",
    "current_journal_path",
    "emit_journal",
    "get_journal",
    "journal_stats",
]
