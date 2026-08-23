"""Runtime and risk metric aggregation for observability endpoints.

``runtime_metrics()`` merges three sources into one JSON-serializable dict:

* persisted state — read-only queries against the runtime SQLite ledger
  (``safety_incidents``, ``risk_state``, ``runtime_sessions``; no new tables);
* process-local runtime counters — incremented by the engine via
  ``record_event`` as state transitions happen (scanner/reconciliation
  failures, data gaps, risk incidents, circuit-breaker engagements, ...);
* derived convenience fields (restart count, entry-block duration) computed
  from the persisted rows above.

The function is empty-state safe: a missing database, a corrupt row, or a
fresh installation all return the same stable shape with zero/empty values
instead of raising. Queries never mutate the ledger (read-only connection,
``mode=ro``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.config import DEFAULT_DECISION_DB_PATH

log = logging.getLogger("parayabuck.monitoring.metrics")

DEFAULT_DATABASE_PATH = DEFAULT_DECISION_DB_PATH

# Process-local counters incremented by the engine at state transitions.
# Persisted equivalents exist for restart count (runtime_sessions) and
# incident presence (safety_incidents); the counters capture EVENTS over the
# process lifetime, including ones that recovered and left no incident.
RUNTIME_COUNTER_KEYS: frozenset[str] = frozenset(
    {
        "runtime_started",
        "runtime_stopped_unexpected",
        "scanner_refresh_failure",
        "scanner_recovered",
        "reconciliation_failure",
        "reconciliation_recovered",
        "risk_incident",
        "drawdown_halt",
        "risk_epoch_reset",
        "circuit_breaker",
        "signal_adjustment_failure",
        "signal_adjustment_recovery",
        "data_gap",
        "order_rejected",
        "ambiguous_order",
    }
)

_counter_lock = threading.Lock()
_counters: dict[str, int] = {}


def record_event(kind: str) -> None:
    """Increment a process-local runtime counter (never raises).

    Unknown kinds are ignored so the engine can record events without
    coupling to the metrics vocabulary.
    """
    if kind not in RUNTIME_COUNTER_KEYS:
        return
    with _counter_lock:
        _counters[kind] = _counters.get(kind, 0) + 1


def runtime_counters() -> dict[str, int]:
    """Snapshot of the process-local counters (all keys present, zero-filled)."""
    with _counter_lock:
        return {key: _counters.get(key, 0) for key in sorted(RUNTIME_COUNTER_KEYS)}


def reset_for_tests() -> None:
    """Clear process-local counters (test helper only)."""
    with _counter_lock:
        _counters.clear()


def _default_database_path() -> str:
    """Resolve the ledger path from config without loading the full Config.

    Falls back to the runtime default when the config file is absent or the
    ``runtime.decision_db_path`` key is missing.
    """
    try:
        config_path = Path(os.getenv("CONFIG_PATH") or "config/settings.yaml")
        if config_path.exists():
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            path = str(dict(raw.get("runtime", {})).get("decision_db_path", ""))
            if path.strip():
                return path.strip()
    except Exception:
        log.warning("Runtime metrics could not resolve decision_db_path from config")
    return DEFAULT_DATABASE_PATH


def _connect_readonly(path: str | Path) -> sqlite3.Connection | None:
    """Open the ledger read-only; None when the database is missing."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
    except sqlite3.Error as exc:
        log.warning("Runtime metrics could not open ledger read-only: %s", exc)
        return None


def _query_rows(
    connection: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
) -> list[tuple[Any, ...]]:
    try:
        return list(connection.execute(sql, params).fetchall())
    except sqlite3.Error as exc:
        log.warning("Runtime metrics query failed (%s): %s", sql.splitlines()[0].strip(), exc)
        return []


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = _query_rows(
        connection,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return bool(row)


def _sessions_metrics(connection: sqlite3.Connection) -> dict[str, object]:
    if not _table_exists(connection, "runtime_sessions"):
        return {
            "total": 0,
            "completed": 0,
            "unended": 0,
            "restart_count": 0,
            "latest_started_at": None,
        }
    rows = _query_rows(
        connection,
        "SELECT COUNT(*), "
        "SUM(CASE WHEN ended_at IS NULL THEN 1 ELSE 0 END), "
        "MAX(started_at) FROM runtime_sessions",
    )
    total, unended, latest = rows[0] if rows else (0, 0, None)
    total = int(total or 0)
    unended = int(unended or 0)
    # Each runtime_sessions row records one engine start; the first start is
    # not a restart, so restarts == starts - 1 (clamped at zero). Crashed
    # sessions never get an end marker and are still counted as starts, so a
    # crash-and-restart cycle increments restart_count correctly.
    return {
        "total": total,
        "completed": total - unended,
        "unended": unended,
        "restart_count": max(0, total - 1),
        "latest_started_at": latest,
    }


def runtime_session_restart_count(connection: sqlite3.Connection) -> int:
    """Restart count from persisted sessions: starts minus one, clamped at 0.

    Each ``runtime_sessions`` row records one engine start; the first start is
    not a restart. Crashed sessions never get an end marker and are still
    counted as starts, so a crash-and-restart cycle increments correctly.
    Public single source of truth: the web dashboard's fallback path calls
    this instead of restating the formula.
    """
    if not _table_exists(connection, "runtime_sessions"):
        return 0
    rows = _query_rows(connection, "SELECT COUNT(*) FROM runtime_sessions")
    total = int(rows[0][0]) if rows else 0
    return max(0, total - 1)


def _incidents_metrics(connection: sqlite3.Connection, account_id: str | None) -> dict[str, object]:
    if not _table_exists(connection, "safety_incidents"):
        return {"count": 0, "active": []}
    if account_id:
        rows = _query_rows(
            connection,
            "SELECT reason, scope, activated_at, updated_at FROM safety_incidents "
            "WHERE account_id=? ORDER BY activated_at DESC, id DESC",
            (account_id,),
        )
    else:
        rows = _query_rows(
            connection,
            "SELECT reason, scope, activated_at, updated_at FROM safety_incidents "
            "ORDER BY activated_at DESC, id DESC",
        )
    incidents = [
        {
            "reason": str(row[0]),
            "scope": str(row[1]),
            "activated_at": row[2],
            "updated_at": row[3],
        }
        for row in rows
    ]
    return {"count": len(incidents), "active": incidents}


def _risk_state_metrics(
    connection: sqlite3.Connection, account_id: str | None
) -> dict[str, object]:
    """Latest risk_state row per account (session-scoped, JSON payload)."""
    if not _table_exists(connection, "risk_state"):
        return {"latest": []}
    if account_id:
        rows = _query_rows(
            connection,
            "SELECT account_id, session_date, payload, updated_at FROM risk_state "
            "WHERE account_id=? ORDER BY session_date DESC, updated_at DESC",
            (account_id,),
        )
    else:
        rows = _query_rows(
            connection,
            "SELECT account_id, session_date, payload, updated_at FROM risk_state "
            "ORDER BY session_date DESC, updated_at DESC",
        )
    latest_by_account: dict[str, dict[str, object]] = {}
    for row in rows:
        account = str(row[0])
        if account in latest_by_account:
            continue
        try:
            payload = json.loads(str(row[2]))
        except (TypeError, ValueError):
            log.warning("Ignoring corrupt risk_state payload for account=%s", account)
            continue
        latest_by_account[account] = {
            "account_id": account,
            "session_date": row[1],
            "updated_at": row[3],
            **{str(key): value for key, value in dict(payload).items()},
        }
    return {"latest": list(latest_by_account.values())}


def _entry_block_duration_seconds(incidents: dict[str, object]) -> float | None:
    """Duration the drawdown halt has been blocking entries, from its
    persisted incident activation (None when no drawdown halt is active)."""
    active = incidents.get("active")
    if not isinstance(active, list):
        return None
    for incident in active:
        if not isinstance(incident, dict):
            continue
        if incident.get("reason") != "portfolio_drawdown_halt":
            continue
        activated_at = incident.get("activated_at")
        if not activated_at:
            return None
        try:
            activated = datetime.fromisoformat(str(activated_at))
        except ValueError:
            return None
        return max(0.0, (datetime.now(UTC) - activated).total_seconds())
    return None


def runtime_metrics(
    database_path: str | Path | None = None, *, account_id: str | None = None
) -> dict[str, object]:
    """Aggregate runtime and risk metrics from persisted state plus counters.

    ``database_path`` defaults to the configured ``runtime.decision_db_path``
    (``data/runtime/parayabuck.sqlite3``). ``account_id`` narrows persisted
    incident/risk-state queries; counters are process-local regardless.
    Never raises: a missing/unreadable ledger returns the empty-state shape.
    """
    path = Path(database_path) if database_path is not None else Path(_default_database_path())
    counters = runtime_counters()
    connection = _connect_readonly(path)
    if connection is None:
        sessions: dict[str, Any] = {
            "total": 0,
            "completed": 0,
            "unended": 0,
            "restart_count": 0,
            "latest_started_at": None,
        }
        incidents: dict[str, Any] = {"count": 0, "active": []}
        risk_state: dict[str, Any] = {"latest": []}
    else:
        try:
            sessions = _sessions_metrics(connection)
            incidents = _incidents_metrics(connection, account_id)
            risk_state = _risk_state_metrics(connection, account_id)
        finally:
            connection.close()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "database_path": str(path),
        "account_id": account_id,
        "counters": counters,
        "sessions": sessions,
        "incidents": incidents,
        "risk_state": risk_state,
        # Derived convenience fields.
        "restart_count": int(sessions["restart_count"]),
        "scanner_failures": int(counters["scanner_refresh_failure"]),
        "scanner_recoveries": int(counters["scanner_recovered"]),
        "reconciliation_failures": int(counters["reconciliation_failure"]),
        "reconciliation_recoveries": int(counters["reconciliation_recovered"]),
        "data_gap_events": int(counters["data_gap"]),
        "daily_loss_incidents": int(counters["risk_incident"]),
        "drawdown_incidents": int(counters["drawdown_halt"]),
        "circuit_breaker_events": int(counters["circuit_breaker"]),
        "order_rejected_events": int(counters["order_rejected"]),
        "ambiguous_order_events": int(counters["ambiguous_order"]),
        "entry_block_duration_seconds": _entry_block_duration_seconds(incidents),
    }


__all__ = [
    "RUNTIME_COUNTER_KEYS",
    "record_event",
    "reset_for_tests",
    "runtime_counters",
    "runtime_metrics",
]
