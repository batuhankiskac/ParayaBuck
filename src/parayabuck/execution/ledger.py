from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from parayabuck.core.models import Market
from parayabuck.logger import get_logger
from parayabuck.risk.state import RiskState, SafetyIncident
from parayabuck.storage import (
    SQLiteStore,
    check_runtime_schema_version,
    migrate_legacy_decision_orders,
)

log = get_logger("parayabuck.execution.ledger")

# Sentinel scope of a ``safety_incidents`` row whose timestamps cannot be
# parsed. The row is still returned (a halt is never dropped silently); its
# timestamps fall back to the epoch so it sorts as the oldest incident.
CORRUPT_INCIDENT_SCOPE = "corrupt"
_CORRUPT_INCIDENT_TIME = datetime(1970, 1, 1, tzinfo=UTC)

# Numeric fields every ``risk_state`` payload must carry. A missing key is an
# error, never a default: a renamed key would otherwise read as a zero peak.
RISK_STATE_REQUIRED_FIELDS = ("day_start_equity", "peak_equity", "realized_pnl", "unrealized_pnl", "fees")


def parse_risk_state_payload(raw: str | bytes) -> dict[str, Any]:
    """The single strict reader of a ``risk_state`` payload.

    Shared by ``ExecutionLedger.load_risk_state`` and the peak-sidecar query,
    so both agree on which rows are readable. Raises ``ValueError`` (a
    ``json`` decode error is one too) for a payload that is not a JSON object,
    misses a required field, or holds a non-finite required number.
    ``peak_epoch`` and ``circuit_breaker_reason`` are optional.
    """
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("risk_state payload is not a JSON object")
    parsed: dict[str, Any] = {}
    for name in RISK_STATE_REQUIRED_FIELDS:
        if name not in payload:
            raise ValueError(f"risk_state payload is missing {name!r}")
        value = float(payload[name])
        if not math.isfinite(value):
            raise ValueError(f"risk_state payload field {name!r} is not finite")
        parsed[name] = value
    parsed["circuit_breaker_reason"] = payload.get("circuit_breaker_reason")
    parsed["peak_epoch"] = int(payload.get("peak_epoch", 0) or 0)
    return parsed


# Legacy single-name/bracket ledger rows that still claim ownership or a
# working order. Nothing in the VT35 runtime manages them any more, so their
# presence means the database predates the portfolio path and must be closed
# out by an operator before the engine trades.
_LEGACY_OPEN_POSITION_STATUSES = ("open", "manual_intervention_required")


class LegacyLedgerStateError(RuntimeError):
    """The database still holds open rows of the removed legacy ledger.

    Raised at ledger construction (engine startup). The legacy managed-position
    and protective-stop stack was deleted; an open ``managed_positions`` row or
    a non-terminal ``orders`` row would otherwise be silently ignored while the
    broker may still hold the shares or the order.
    """


@dataclass(frozen=True)
class ManagedPosition:
    """Legacy open-position view: only what the startup guard still reads.

    The legacy single-name/bracket stack is deleted; the engine keeps opening
    old databases and refuses startup while ``managed_positions`` still claims
    an open row. Only the symbol (pin set), market, and status (manual
    intervention gate) are read anywhere — the protective-stop fields were
    last read by the W4-removed web account snapshot.
    """

    canonical_symbol: str
    market: Market
    status: str


class ExecutionLedger:
    """Runtime safety state: risk-state rows, persistent safety incidents and
    runtime sessions, plus the read-only legacy ownership view.

    The legacy single-name/bracket tables (``managed_positions``, ``orders``,
    ``fee_activities``) are still created so an old database keeps opening,
    but nothing writes them any more: portfolio execution lives in
    ``PortfolioStateRepository``. Opening a database whose legacy tables still
    claim an open position or a working order fails closed with
    :class:`LegacyLedgerStateError`. Old databases keep their dead
    ``order_events``/``executions``/``reconcile_watermarks``/``protective_orders``
    tables harmlessly; fresh databases no longer create them.
    """

    def __init__(self, path: str, *, store: SQLiteStore | None = None) -> None:
        self._store = store or SQLiteStore(Path(path))
        self._store.acquire()
        self.connection = self._store.connection
        self._lock = self._store.lock
        try:
            self._migrate()
            self._refuse_legacy_open_state()
        except BaseException:
            self._store.release()
            raise

    def _refuse_legacy_open_state(self) -> None:
        """Fail closed when legacy ledger rows still claim live broker state.

        Two cheap indexed-or-tiny SELECTs. The rows are never modified here:
        closing them out needs an operator to confirm the broker side first.
        """
        with self._lock:
            positions = self.connection.execute(
                """
                SELECT account_id, canonical_symbol, entry_client_order_id, status
                FROM managed_positions
                WHERE status IN (?, ?)
                ORDER BY opened_at LIMIT 5
                """,
                _LEGACY_OPEN_POSITION_STATUSES,
            ).fetchall()
            orders = self.connection.execute(
                """
                SELECT client_order_id, canonical_symbol, order_role
                FROM orders WHERE terminal_state IS NULL
                ORDER BY updated_at LIMIT 5
                """
            ).fetchall()
        if not positions and not orders:
            return
        found: list[str] = []
        if positions:
            found.append(
                "managed_positions open rows: "
                + ", ".join(f"{row[1]} entry={row[2]} status={row[3]} account={row[0]}" for row in positions)
            )
        if orders:
            found.append(
                "orders non-terminal rows: " + ", ".join(f"{row[0]} ({row[1]}, role={row[2]})" for row in orders)
            )
        raise LegacyLedgerStateError(
            "Legacy execution-ledger state is still open and nothing manages it any more ("
            + "; ".join(found)
            + "). Manual remedy: stop the engine, back up the database, confirm at the broker that "
            "each listed position/order is flat or no longer working (flatten it, cancel it, or adopt the "
            "shares with `parayabuck ops rebase-ownership`), then close the legacy rows with "
            "\"UPDATE managed_positions SET status='closed', owned_qty=0, remaining_owned_qty=0, "
            "closed_at=CURRENT_TIMESTAMP WHERE status IN ('open','manual_intervention_required'); "
            "UPDATE orders SET terminal_state='canceled' WHERE terminal_state IS NULL;\" and restart."
        )

    def _migrate(self) -> None:
        with self._lock:
            # Refuse a newer schema before any DDL touches it.
            check_runtime_schema_version(self.connection)
            migrate_legacy_decision_orders(self.connection)
            self._create_initial_schema()
            self._migrate_v2_managed_positions()
            self._migrate_v3_active_position_index()
            self._migrate_v4_circuit_breaker_backfill()
            self._migrate_v5_execution_quality_columns()
            self._migrate_v6_watermark()
            # The schema version is stamped once, by PortfolioStateRepository,
            # after its own migrations finish. Stamping here would mark the
            # database as current while those migrations have not run yet.
            self.connection.commit()

    def _create_initial_schema(self) -> None:
        self.connection.executescript(
            """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS managed_positions (
                    account_id TEXT NOT NULL,
                    canonical_symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    source_signal_id TEXT NOT NULL,
                    entry_client_order_id TEXT NOT NULL,
                    broker_order_id TEXT,
                    owned_qty REAL NOT NULL,
                    filled_avg_price REAL NOT NULL,
                    original_stop REAL NOT NULL,
                    original_target REAL NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    PRIMARY KEY (account_id, canonical_symbol, entry_client_order_id)
                );
                CREATE TABLE IF NOT EXISTS fee_activities (
                    activity_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    fee REAL NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_state (
                    account_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, session_date)
                );
                CREATE TABLE IF NOT EXISTS safety_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, reason)
                );
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    broker_order_id TEXT,
                    parent_broker_order_id TEXT,
                    entry_client_order_id TEXT,
                    order_role TEXT NOT NULL,
                    canonical_symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_qty REAL NOT NULL,
                    cumulative_filled_qty REAL NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    terminal_state TEXT,
                    signal_id TEXT,
                    strategy TEXT,
                    entry_price REAL,
                    stop_price REAL,
                    target_price REAL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
                """
        )

    def _migrate_v2_managed_positions(self) -> None:
        self._ensure_column("managed_positions", "entry_filled_qty", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("managed_positions", "exit_filled_qty", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("managed_positions", "remaining_owned_qty", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("managed_positions", "realized_pnl", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("managed_positions", "entry_fees", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("managed_positions", "exit_fees", "REAL NOT NULL DEFAULT 0")
        self._ensure_column(
            "managed_positions",
            "protection_state",
            "TEXT NOT NULL DEFAULT 'unprotected'",
        )
        self._ensure_column("managed_positions", "protective_stop_client_order_id", "TEXT")
        self._ensure_column("managed_positions", "protective_stop_broker_order_id", "TEXT")
        self._ensure_column("managed_positions", "stop_state", "TEXT NOT NULL DEFAULT 'pending'")
        self._ensure_column("managed_positions", "target_state", "TEXT NOT NULL DEFAULT 'watching'")
        self._ensure_column("managed_positions", "exit_client_order_id", "TEXT")
        self._ensure_column("managed_positions", "exit_broker_order_id", "TEXT")
        self._ensure_column("managed_positions", "updated_at", "TEXT")
        self._ensure_column("managed_positions", "current_stop", "REAL")
        self.connection.execute(
            """
                UPDATE managed_positions
                SET entry_filled_qty=owned_qty, remaining_owned_qty=owned_qty
                WHERE entry_filled_qty=0 AND remaining_owned_qty=0 AND owned_qty>0
                """
        )
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (2, CURRENT_TIMESTAMP)
                """
        )
        self.connection.execute(
            """
                UPDATE managed_positions
                SET updated_at=COALESCE(updated_at, opened_at)
                """
        )
        self.connection.execute(
            """
                UPDATE managed_positions
                SET current_stop=COALESCE(current_stop, original_stop)
                """
        )

    def _migrate_v3_active_position_index(self) -> None:
        # A symbol may have historical rows, but never two positions that
        # still claim ownership.  The table primary key intentionally
        # includes the entry id for audit history, so enforce the runtime
        # ownership rule separately with a partial unique index.
        self.connection.execute(
            """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_managed_position
                ON managed_positions(account_id, canonical_symbol)
                WHERE status IN ('open', 'manual_intervention_required')
                """
        )
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (3, CURRENT_TIMESTAMP)
                """
        )

    def _migrate_v4_circuit_breaker_backfill(self) -> None:
        # v4: persistent circuit-breaker incidents. A halt persisted in the
        # SESSION_SCOPED risk_state row (with a non-null
        # circuit_breaker_reason) must fail safe across the session-day
        # boundary, so the LATEST such row per account backfills an
        # incident. The backfill is gated on the version stamp: it runs
        # exactly once, otherwise an operator-reset incident would be
        # resurrected from a historical row on the next open. INSERT OR
        # IGNORE keeps it idempotent even if a crash lands between the
        # backfill and the stamp.
        applied_versions = {int(row[0]) for row in self.connection.execute("SELECT version FROM schema_migrations")}
        if 4 not in applied_versions:
            self.connection.execute(
                """
                    INSERT OR IGNORE INTO safety_incidents(
                        account_id, reason, scope, activated_at, updated_at
                    )
                    SELECT
                        account_id,
                        json_extract(payload, '$.circuit_breaker_reason'),
                        'global',
                        updated_at,
                        updated_at
                    FROM risk_state
                    WHERE json_extract(payload, '$.circuit_breaker_reason') IS NOT NULL
                      AND rowid = (
                          SELECT r2.rowid
                          FROM risk_state r2
                          WHERE r2.account_id = risk_state.account_id
                            AND json_extract(
                                r2.payload, '$.circuit_breaker_reason'
                            ) IS NOT NULL
                          ORDER BY r2.session_date DESC, r2.updated_at DESC
                          LIMIT 1
                      )
                    """
            )
            self.connection.execute(
                """
                    INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                    VALUES (4, CURRENT_TIMESTAMP)
                    """
            )

    def _migrate_v5_execution_quality_columns(self) -> None:
        # v5: execution-quality capture columns on the authoritative
        # ``orders`` record (decision timestamp, signal/reference price,
        # target weight, spread estimate, submission/fill timestamps, fill
        # delay, observed slippage and fees). All columns are nullable
        # except actual_fee so legacy rows remain valid; the telemetry
        # layer aggregates whatever is present and reports None otherwise.
        for column, definition in (
            ("decision_timestamp", "TEXT"),
            ("target_weight", "REAL"),
            ("reference_price", "REAL"),
            ("spread_estimate_bps", "REAL"),
            ("submitted_at", "TEXT"),
            ("filled_at", "TEXT"),
            ("fill_delay_seconds", "REAL"),
            ("slippage_bps", "REAL"),
            ("actual_fee", "REAL NOT NULL DEFAULT 0"),
        ):
            self._ensure_column("orders", column, definition)
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (5, CURRENT_TIMESTAMP)
                """
        )

    def _migrate_v6_watermark(self) -> None:
        # v6: version stamp only. It creates no table or column, and no
        # fill-activity reconciliation watermark is persisted anywhere; the
        # stamp is kept so existing databases stay at version 6.
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (6, CURRENT_TIMESTAMP)
                """
        )

    def _ensure_column(self, table: str, name: str, definition: str) -> None:
        # Validate and quote identifiers to prevent injection
        if not table.replace("_", "").isalnum() or not table[0].isalpha():
            raise ValueError(f"Invalid table name: {table!r}")
        if not name.replace("_", "").isalnum() or not name[0].isalpha():
            raise ValueError(f"Invalid column name: {name!r}")
        quoted_table = '"' + table.replace('"', '""') + '"'
        columns = {str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({quoted_table})")}
        if name not in columns:
            quoted_col = '"' + name.replace('"', '""') + '"'
            self.connection.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_col} {definition}")

    def managed_entries_for_account(self, account_id: str) -> list[ManagedPosition]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT account_id, canonical_symbol, market, strategy, source_signal_id,
                       entry_client_order_id, broker_order_id, remaining_owned_qty,
                       entry_filled_qty, exit_filled_qty, filled_avg_price,
                       original_stop, original_target, current_stop, realized_pnl, entry_fees,
                       exit_fees, protection_state, status,
                       protective_stop_client_order_id, protective_stop_broker_order_id,
                       stop_state, target_state, exit_client_order_id, exit_broker_order_id,
                       updated_at
                FROM managed_positions
                WHERE account_id=? AND status IN ('open', 'manual_intervention_required')
                  AND remaining_owned_qty > 0
                ORDER BY opened_at
                """,
                (account_id,),
            ).fetchall()
        return [self._managed_from_row(row) for row in rows]

    @staticmethod
    def _managed_from_row(row: tuple[object, ...]) -> ManagedPosition:
        return ManagedPosition(
            canonical_symbol=str(row[1]),
            market=Market(str(row[2])),
            status=str(row[18]),
        )

    def total_fees_for_account(self, account_id: str) -> float:
        """Observed broker fees of this account's portfolio fills.

        ``portfolio_fills`` lives in the same runtime database (written by
        ``PortfolioStateRepository``); an absent table (a ledger-only test
        database) reads as no fees. A recovery fill with ``fee_observed=0``
        stores a ``0.0`` placeholder, which is NOT an observed free fill and
        is excluded so the snapshot agrees with the dashboard's ``unknown``
        fee-completeness. The exclusion is per fill (its own journal event,
        keyed by ``activity_id``), never per order: a later unobserved
        recovery event on the same order must not erase an observed fee.
        """
        with self._lock:
            exists = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_fills'"
            ).fetchone()
            if exists is None:
                return 0.0
            has_events = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_execution_events'"
            ).fetchone()
            if has_events is None:
                row = self.connection.execute(
                    """
                    SELECT COALESCE(SUM(f.fee), 0)
                    FROM portfolio_fills f
                    JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                    JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                    JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                    WHERE s.account_id = ?
                    """,
                    (account_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    """
                    SELECT COALESCE(SUM(f.fee), 0)
                    FROM portfolio_fills f
                    JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                    JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                    JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                    WHERE s.account_id = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM portfolio_execution_events e
                        WHERE e.client_order_id = f.client_order_id
                        AND e.dedup_identity = f.activity_id
                        AND e.fee_observed = 0
                    )
                    """,
                    (account_id,),
                ).fetchone()
        return float(row[0] if row else 0.0)

    def save_risk_state(self, state: RiskState) -> None:
        payload = json.dumps(
            {
                "day_start_equity": state.day_start_equity,
                "peak_equity": state.peak_equity,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl": state.unrealized_pnl,
                "fees": state.fees,
                "circuit_breaker_reason": state.circuit_breaker_reason,
                "peak_epoch": int(getattr(state, "peak_epoch", 0) or 0),
            },
            sort_keys=True,
        )
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO risk_state(account_id, session_date, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, session_date) DO UPDATE SET
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (
                    state.account_id,
                    state.session_date.isoformat(),
                    payload,
                    state.updated_at.isoformat(),
                ),
            )
            self.connection.commit()

    def load_risk_state(self, account_id: str, session_date: date) -> RiskState | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT payload, updated_at FROM risk_state
                WHERE account_id=? AND session_date=?
                """,
                (account_id, session_date.isoformat()),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = parse_risk_state_payload(row[0])
            return RiskState(
                account_id=account_id,
                session_date=session_date,
                day_start_equity=payload["day_start_equity"],
                peak_equity=payload["peak_equity"],
                realized_pnl=payload["realized_pnl"],
                unrealized_pnl=payload["unrealized_pnl"],
                fees=payload["fees"],
                circuit_breaker_reason=payload["circuit_breaker_reason"],
                updated_at=datetime.fromisoformat(str(row[1])),
                peak_epoch=payload["peak_epoch"],
            )
        except (TypeError, ValueError, KeyError):
            # A corrupt/truncated risk_state row (bad JSON payload, missing
            # field, or unparsable updated_at) must not fail runtime startup:
            # the state is treated as absent and rebuilt from the account.
            log.warning(
                "Ignoring corrupt risk_state row for account=%s session=%s",
                account_id,
                session_date,
            )
            return None

    def save_safety_incident(
        self,
        account_id: str,
        reason: str,
        activated_at: datetime | None = None,
    ) -> None:
        """Record (or refresh) a PERSISTENT_INCIDENT circuit-breaker halt.

        Upserts by ``(account_id, reason)``: a re-save of the same reason
        preserves the original ``activated_at`` and only bumps ``updated_at``.
        """
        now = datetime.now(UTC)
        activated = activated_at if activated_at is not None else now
        with self._lock:
            # The store runs WAL at synchronous=NORMAL, which survives a process
            # crash but can lose the newest commits on an OS or power crash
            # before a checkpoint. A halt row must survive that, so this one
            # commit runs at FULL (fsync per commit) and the store's level is
            # restored afterwards (R2-9-2). PRAGMA runs outside the transaction.
            self.connection.execute("PRAGMA synchronous=FULL")
            try:
                self.connection.execute(
                    """
                    INSERT INTO safety_incidents(
                        account_id, reason, scope, activated_at, updated_at
                    )
                    VALUES (?, ?, 'global', ?, ?)
                    ON CONFLICT(account_id, reason) DO UPDATE SET
                        scope=excluded.scope, updated_at=excluded.updated_at
                    """,
                    (account_id, reason, activated.isoformat(), now.isoformat()),
                )
                self.connection.commit()
            finally:
                self.connection.execute("PRAGMA synchronous=NORMAL")

    def load_safety_incidents(self, account_id: str) -> tuple[SafetyIncident, ...]:
        """Return the persistent incidents for an account, most recent first."""
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT account_id, reason, scope, activated_at, updated_at
                FROM safety_incidents
                WHERE account_id=?
                ORDER BY activated_at DESC, id DESC
                """,
                (account_id,),
            ).fetchall()
        incidents: list[SafetyIncident] = []
        for row in rows:
            try:
                activated_at = datetime.fromisoformat(row[3])
                updated_at = datetime.fromisoformat(row[4])
                scope = str(row[2])
            except (TypeError, ValueError):
                # A corrupt row still describes a halt: keep it (fail closed)
                # with a sentinel scope and epoch timestamps instead of
                # silently dropping the incident.
                log.warning(
                    "Corrupt safety_incident row kept as a halt for account=%s reason=%s",
                    account_id,
                    row[1],
                )
                activated_at = updated_at = _CORRUPT_INCIDENT_TIME
                scope = CORRUPT_INCIDENT_SCOPE
            incidents.append(
                SafetyIncident(
                    account_id=str(row[0]),
                    reason=str(row[1]),
                    scope=scope,
                    activated_at=activated_at,
                    updated_at=updated_at,
                )
            )
        return tuple(incidents)

    def clear_safety_incident(self, account_id: str, reason: str | None = None) -> None:
        """Clear a verified incident, or every incident for the account when
        ``reason`` is None (used only after a reconciliation that proves the
        whole account healthy)."""
        with self._lock:
            if reason is None:
                self.connection.execute(
                    "DELETE FROM safety_incidents WHERE account_id=?",
                    (account_id,),
                )
            else:
                self.connection.execute(
                    "DELETE FROM safety_incidents WHERE account_id=? AND reason=?",
                    (account_id, reason),
                )
            self.connection.commit()

    def record_runtime_session(self, account_id: str, mode: str) -> str:
        """Record one engine start in the existing ``runtime_sessions`` table.

        Returns the session id; the caller stores it and pairs it with
        ``end_runtime_session`` so ``runtime_metrics()`` can derive restart
        counts (every row is one start; an unended row is a crash).
        """
        session_id = uuid4().hex
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO runtime_sessions(
                    session_id, account_id, mode, state, started_at
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (session_id, account_id or "unknown", mode, datetime.now(UTC).isoformat()),
            )
            self.connection.commit()
        return session_id

    def end_runtime_session(self, session_id: str, state: str = "stopped") -> None:
        """Mark a recorded engine session ended (idempotent)."""
        with self._lock:
            self.connection.execute(
                """
                UPDATE runtime_sessions
                SET state = ?, ended_at = ?
                WHERE session_id = ? AND ended_at IS NULL
                """,
                (state, datetime.now(UTC).isoformat(), session_id),
            )
            self.connection.commit()

    def close(self) -> None:
        self._store.release()


__all__ = ["CORRUPT_INCIDENT_SCOPE", "ExecutionLedger", "LegacyLedgerStateError", "ManagedPosition"]
