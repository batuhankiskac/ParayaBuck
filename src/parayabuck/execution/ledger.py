from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from parayabuck.core.models import (
    Direction,
    ExecutionEvent,
    ExecutionTransition,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    ProtectionState,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.risk.state import RiskState, SafetyIncident
from parayabuck.storage import SQLiteStore, migrate_legacy_decision_orders

log = get_logger("parayabuck.execution.ledger")

# The side of an order row is the side of the position, not of the order
# stream: an exit/stop closes a lot, so it always trades against the entry.
_OPPOSITE_SIDES = {"long": "sell", "buy": "sell", "sell": "buy", "short": "buy"}

# Client-id prefix of the manual-exit rows the FILL-activity backfill records
# for broker fills that are already complete (see
# ``ReconciliationService._reconcile_fill_activity_fees``).  Such a row is a
# settled historical fact, not a working execution awaiting a lot link.
FILL_ACTIVITY_RECOVERY_PREFIX = "alpaca-fill-activity-"


@dataclass(frozen=True)
class ManagedPosition:
    canonical_symbol: str
    market: Market
    strategy: str
    source_signal_id: str
    entry_client_order_id: str
    broker_order_id: str | None
    owned_qty: float
    filled_avg_price: float
    original_stop: float
    original_target: float
    status: str
    current_stop: float | None = None
    entry_filled_qty: float = 0.0
    exit_filled_qty: float = 0.0
    realized_pnl: float = 0.0
    entry_fees: float = 0.0
    exit_fees: float = 0.0
    protection_state: ProtectionState = ProtectionState.UNPROTECTED
    protective_stop_client_order_id: str | None = None
    protective_stop_broker_order_id: str | None = None
    stop_state: str = "pending"
    target_state: str = "watching"
    exit_client_order_id: str | None = None
    exit_broker_order_id: str | None = None
    updated_at: datetime | None = None


class ExecutionLedger:
    """Small transactional ownership ledger shared by execution and reconciliation.

    Hardening: every mutation is idempotent and crash-safe. Order events use
    ``INSERT OR IGNORE`` on (client_order_id, event_type, broker_timestamp,
    payload); managed quantity moves through ``_record_entry_fill_delta_tx``
    / ``_record_exit_fill_delta_tx`` inside ``BEGIN IMMEDIATE`` so a replayed
    fill never double-counts owned_qty. ``holdings == broker after reconcile``
    is enforced by ``reconcile()`` in the broker layer which compares ledger
    state to the broker snapshot and never silently overwrites a material
    difference. Risk and watermark state are persisted per-account and restored
    via ``_restore_persistent_state`` so a restart never loses fills or
    position accounting.
    """

    def __init__(self, path: str, *, store: SQLiteStore | None = None) -> None:
        self._store = store or SQLiteStore(Path(path))
        self._store.acquire()
        self.connection = self._store.connection
        self._lock = self._store.lock
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            migrate_legacy_decision_orders(self.connection)
            self._create_initial_schema()
            self._migrate_v2_managed_positions()
            self._migrate_v3_active_position_index()
            self._migrate_v4_circuit_breaker_backfill()
            self._migrate_v5_execution_quality_columns()
            self._migrate_v6_watermark()
            self._migrate_v7_fee_booking_marker()
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
                CREATE TABLE IF NOT EXISTS order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL,
                    broker_order_id TEXT,
                    event_type TEXT NOT NULL,
                    broker_timestamp TEXT,
                    payload TEXT NOT NULL,
                    UNIQUE(client_order_id, event_type, broker_timestamp, payload)
                );
                CREATE TABLE IF NOT EXISTS executions (
                    client_order_id TEXT PRIMARY KEY,
                    requested_qty REAL NOT NULL,
                    filled_qty REAL NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    terminal_state TEXT,
                    protection_state TEXT NOT NULL DEFAULT 'unprotected',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fee_activities (
                    activity_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    fee REAL NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconcile_watermarks (
                    account_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    watermark TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, kind)
                );
                CREATE TABLE IF NOT EXISTS protective_orders (
                    client_order_id TEXT PRIMARY KEY,
                    entry_client_order_id TEXT NOT NULL,
                    canonical_symbol TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    qty REAL NOT NULL,
                    trigger_price REAL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_id
                    ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_orders_parent
                    ON orders(parent_broker_order_id);
                CREATE INDEX IF NOT EXISTS idx_orders_entry
                    ON orders(entry_client_order_id);
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
        # v6: persisted fill-activity reconciliation watermark. The
        # per-minute reconcile previously rescanned the FULL fill-activity
        # history every pass; the watermark lets it sync incrementally
        # from a bounded window while keeping the same idempotent
        # fee/fill-recovery application.
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (6, CURRENT_TIMESTAMP)
                """
        )

    def _migrate_v7_fee_booking_marker(self) -> None:
        # v7: ``fee_activities.booked`` records whether a recorded fee has
        # actually been added to its managed lot's ``entry_fees`` /
        # ``exit_fees``. The activity primary key alone only proves the fee
        # was SEEN: a fee applied before its fill existed matched 0
        # ``managed_positions`` rows, and the next pass short-circuited on
        # the key, so the commission stayed unbooked for good. The DEFAULT is
        # 1 on purpose -- every row written before this migration was applied
        # under the old code, and defaulting them to pending would
        # double-count them on the next pass. New rows are inserted with 0
        # and flipped to 1 only once the lot update lands.
        self._ensure_column("fee_activities", "booked", "INTEGER NOT NULL DEFAULT 1")
        self.connection.execute(
            """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (7, CURRENT_TIMESTAMP)
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

    def register_entry_order(
        self,
        account_id: str,
        order: OrderIntent,
        broker_order_id: str | None = None,
        *,
        decision_timestamp: datetime | None = None,
        target_weight: float | None = None,
        reference_price: float | None = None,
        spread_estimate_bps: float | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        symbol = canonical_symbol(order.symbol, order.market)
        with self._lock:
            existing_open = self.connection.execute(
                """
                SELECT entry_client_order_id FROM managed_positions
                WHERE account_id=? AND canonical_symbol=?
                  AND status IN ('open', 'manual_intervention_required')
                  AND entry_client_order_id<>?
                LIMIT 1
                """,
                (account_id, symbol, order.client_order_id),
            ).fetchone()
            if existing_open is not None:
                raise ValueError(f"Single-managed-entry invariant violated for {symbol}: existing={existing_open[0]}")
            existing_pending = self.connection.execute(
                """
                SELECT client_order_id FROM orders
                WHERE account_id=? AND canonical_symbol=? AND order_role='entry'
                  AND client_order_id<>?
                  AND (terminal_state IS NULL OR terminal_state NOT IN
                       ('filled', 'cancelled', 'canceled', 'expired', 'rejected'))
                LIMIT 1
                """,
                (account_id, symbol, order.client_order_id),
            ).fetchone()
            if existing_pending is not None:
                raise ValueError(f"Single-managed-entry invariant violated for {symbol}: pending={existing_pending[0]}")
            self.connection.execute(
                """
                INSERT INTO orders(
                    client_order_id, account_id, broker_order_id,
                    parent_broker_order_id, entry_client_order_id, order_role,
                    canonical_symbol, market, side, requested_qty,
                    cumulative_filled_qty, signal_id, strategy, entry_price,
                    stop_price, target_price, updated_at, decision_timestamp,
                    target_weight, reference_price, spread_estimate_bps, submitted_at
                ) VALUES (?, ?, ?, NULL, ?, 'entry', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    broker_order_id=COALESCE(excluded.broker_order_id, orders.broker_order_id),
                    requested_qty=excluded.requested_qty,
                    reference_price=COALESCE(excluded.reference_price, orders.reference_price),
                    submitted_at=COALESCE(excluded.submitted_at, orders.submitted_at),
                    updated_at=excluded.updated_at
                """,
                (
                    order.client_order_id,
                    account_id,
                    broker_order_id,
                    order.client_order_id,
                    symbol,
                    order.market.value,
                    order.direction.value,
                    order.qty,
                    order.signal_id,
                    order.strategy,
                    order.entry_price,
                    order.stop_price,
                    order.target_price,
                    now,
                    decision_timestamp.isoformat() if decision_timestamp is not None else None,
                    target_weight,
                    reference_price,
                    spread_estimate_bps,
                    now,
                ),
            )
            self.connection.commit()

    def _record_entry_fill_delta_tx(
        self,
        account_id: str,
        order: OrderIntent,
        qty_delta: float,
        price: float,
        fee_delta: float,
        broker_order_id: str | None,
        now: str,
    ) -> None:
        symbol = canonical_symbol(order.symbol, order.market)
        existing = self.connection.execute(
            """
            SELECT entry_filled_qty, filled_avg_price
            FROM managed_positions
            WHERE account_id=? AND canonical_symbol=? AND entry_client_order_id=?
            """,
            (account_id, symbol, order.client_order_id),
        ).fetchone()
        previous_qty = float(existing[0]) if existing else 0.0
        previous_average = float(existing[1]) if existing else 0.0
        total_entry = previous_qty + qty_delta
        average = (previous_average * previous_qty + price * qty_delta) / total_entry if total_entry > 0 else price
        self.connection.execute(
            """
            INSERT INTO managed_positions(
                account_id, canonical_symbol, market, strategy, source_signal_id,
                entry_client_order_id, broker_order_id, owned_qty, entry_filled_qty,
                exit_filled_qty, remaining_owned_qty, filled_avg_price,
                original_stop, original_target, current_stop, realized_pnl, entry_fees, exit_fees,
                protection_state, status, opened_at, closed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?, 0,
                      'unprotected', 'open', ?, NULL, ?)
            ON CONFLICT(account_id, canonical_symbol, entry_client_order_id) DO UPDATE SET
                broker_order_id=COALESCE(
                    excluded.broker_order_id, managed_positions.broker_order_id
                ),
                owned_qty=managed_positions.remaining_owned_qty + excluded.entry_filled_qty,
                entry_filled_qty=managed_positions.entry_filled_qty + excluded.entry_filled_qty,
                remaining_owned_qty=(
                    managed_positions.remaining_owned_qty + excluded.entry_filled_qty
                ),
                filled_avg_price=excluded.filled_avg_price,
                entry_fees=managed_positions.entry_fees + excluded.entry_fees,
                status='open',
                closed_at=NULL,
                updated_at=excluded.updated_at
            """,
            (
                account_id,
                symbol,
                order.market.value,
                order.strategy,
                order.signal_id,
                order.client_order_id,
                broker_order_id,
                qty_delta,
                qty_delta,
                qty_delta,
                average,
                order.stop_price,
                order.target_price,
                order.stop_price,
                fee_delta,
                now,
                now,
            ),
        )

    def _record_exit_fill_delta_tx(
        self,
        account_id: str,
        entry_client_order_id: str,
        qty_delta: float,
        price: float,
        fee_delta: float,
        now: str,
    ) -> float:
        row = self.connection.execute(
            """
            SELECT entry_filled_qty, exit_filled_qty, remaining_owned_qty,
                   filled_avg_price, market, entry_fees
            FROM managed_positions
            WHERE account_id=? AND entry_client_order_id=?
              AND status IN ('open', 'manual_intervention_required')
            ORDER BY opened_at LIMIT 1
            """,
            (account_id, entry_client_order_id),
        ).fetchone()
        if row is None:
            return 0.0
        remaining = max(0.0, float(row[2]))
        applied = min(remaining, qty_delta)
        if applied <= 0:
            return 0.0
        entry_price = float(row[3])
        order_row = self.connection.execute(
            "SELECT side FROM orders WHERE client_order_id=?",
            (entry_client_order_id,),
        ).fetchone()
        is_short = bool(order_row and str(order_row[0]) == Direction.SHORT.value)
        gross_pnl = (entry_price - price) * applied if is_short else (price - entry_price) * applied
        entry_fee_allocation = float(row[5]) * applied / float(row[0]) if float(row[0]) > 0 else 0.0
        net_pnl = gross_pnl - fee_delta - entry_fee_allocation
        remaining_after = max(0.0, remaining - applied)
        status = "closed" if remaining_after <= 1e-12 else "open"
        self.connection.execute(
            """
            UPDATE managed_positions
            SET exit_filled_qty=exit_filled_qty+?,
                remaining_owned_qty=?,
                owned_qty=?,
                realized_pnl=realized_pnl+?,
                exit_fees=exit_fees+?,
                status=?,
                closed_at=CASE WHEN ?='closed' THEN ? ELSE NULL END,
                updated_at=?
            WHERE account_id=? AND entry_client_order_id=?
            """,
            (
                applied,
                remaining_after,
                remaining_after,
                net_pnl,
                fee_delta,
                status,
                status,
                now,
                now,
                account_id,
                entry_client_order_id,
            ),
        )
        return net_pnl

    def mark_position_closed(
        self, account_id: str, entry_client_order_id: str, *, closed_at: datetime | None = None
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                UPDATE managed_positions
                SET owned_qty=0, remaining_owned_qty=0, status='closed',
                    closed_at=?, updated_at=?
                WHERE account_id=? AND entry_client_order_id=?
                """,
                (
                    (closed_at or datetime.now(UTC)).isoformat(),
                    datetime.now(UTC).isoformat(),
                    account_id,
                    entry_client_order_id,
                ),
            )
            self.connection.commit()

    def mark_manual_intervention(self, account_id: str, entry_client_order_id: str) -> None:
        with self._lock:
            self.connection.execute(
                """
                UPDATE managed_positions
                SET status='manual_intervention_required', updated_at=?
                WHERE account_id=? AND entry_client_order_id=? AND status='open'
                """,
                (datetime.now(UTC).isoformat(), account_id, entry_client_order_id),
            )
            self.connection.commit()

    def set_protection_state(
        self,
        account_id: str,
        entry_client_order_id: str,
        state: ProtectionState,
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                UPDATE managed_positions SET protection_state=?, updated_at=?
                WHERE account_id=? AND entry_client_order_id=?
                """,
                (
                    state.value,
                    datetime.now(UTC).isoformat(),
                    account_id,
                    entry_client_order_id,
                ),
            )
            self.connection.commit()

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
            strategy=str(row[3]),
            source_signal_id=str(row[4]),
            entry_client_order_id=str(row[5]),
            broker_order_id=str(row[6]) if row[6] else None,
            owned_qty=float(str(row[7])),
            entry_filled_qty=float(str(row[8])),
            exit_filled_qty=float(str(row[9])),
            filled_avg_price=float(str(row[10])),
            original_stop=float(str(row[11])),
            original_target=float(str(row[12])),
            current_stop=float(str(row[13])) if row[13] is not None else None,
            realized_pnl=float(str(row[14])),
            entry_fees=float(str(row[15])),
            exit_fees=float(str(row[16])),
            protection_state=ProtectionState(str(row[17])),
            status=str(row[18]),
            protective_stop_client_order_id=str(row[19]) if row[19] else None,
            protective_stop_broker_order_id=str(row[20]) if row[20] else None,
            stop_state=str(row[21]),
            target_state=str(row[22]),
            exit_client_order_id=str(row[23]) if row[23] else None,
            exit_broker_order_id=str(row[24]) if row[24] else None,
            updated_at=datetime.fromisoformat(str(row[25])) if row[25] else None,
        )

    def managed_for_account(self, account_id: str) -> dict[str, ManagedPosition]:
        rows = self.managed_entries_for_account(account_id)
        result: dict[str, ManagedPosition] = {}
        for item in rows:
            previous = result.get(item.canonical_symbol)
            if previous is None:
                result[item.canonical_symbol] = item
            else:
                raise ValueError(
                    "Single-managed-entry invariant violated for "
                    f"{item.canonical_symbol}: {previous.entry_client_order_id}, "
                    f"{item.entry_client_order_id}"
                )
        return result

    def total_fees_for_account(self, account_id: str) -> float:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT COALESCE(SUM(entry_fees + exit_fees), 0)
                FROM managed_positions WHERE account_id=?
                """,
                (account_id,),
            ).fetchone()
        return float(row[0] if row else 0.0)

    def apply_fee_activity(
        self,
        account_id: str,
        *,
        activity_id: str,
        broker_order_id: str,
        fee: float,
        occurred_at: datetime,
    ) -> bool:
        """Apply one Alpaca fill-activity fee exactly once, repairably.

        The fee row is written when the activity is first seen, but it only
        counts against the managed lot once the ``managed_positions`` update
        actually matched a row -- that is what ``fee_activities.booked``
        records. The reconciler re-reads the 24 h overlap window on every
        pass, so a fee whose lot did not exist yet (a cold start that has to
        recover the fill itself from account activity) is booked by a later
        pass instead of being stranded by the activity primary key.
        """
        if fee <= 0 or not activity_id or not broker_order_id:
            return False
        now = occurred_at.isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                order = self.connection.execute(
                    """
                    SELECT client_order_id, entry_client_order_id, order_role
                    FROM orders WHERE broker_order_id=?
                    """,
                    (broker_order_id,),
                ).fetchone()
                if order is None:
                    self.connection.rollback()
                    return False
                entry_client_order_id = order[1]
                inserted = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO fee_activities(
                        activity_id, client_order_id, fee, occurred_at, booked
                    ) VALUES (?, ?, ?, ?, 0)
                    """,
                    (activity_id, str(entry_client_order_id or order[0]), fee, now),
                )
                if not inserted.rowcount:
                    recorded = self.connection.execute(
                        "SELECT booked FROM fee_activities WHERE activity_id=?",
                        (activity_id,),
                    ).fetchone()
                    if recorded is None or int(recorded[0] or 0):
                        self.connection.commit()
                        return False
                    # Recorded but never booked: the lot may exist now, so
                    # fall through and complete the booking instead of
                    # treating the primary-key hit as "already applied".
                if entry_client_order_id is None:
                    # The fee is real and must be recorded, but this order is
                    # not linked to a managed lot, so there is no
                    # managed_positions row to book it against.  Every
                    # reconcile pass can rediscover the same order, so this
                    # routine path must not warn.
                    log.debug(
                        "Fee recorded without a managed lot: activity_id=%s client_order_id=%s fee=%s",
                        activity_id,
                        order[0],
                        fee,
                    )
                    self.connection.execute(
                        "UPDATE fee_activities SET booked=1 WHERE activity_id=?",
                        (activity_id,),
                    )
                    self.connection.commit()
                    return True
                if OrderRole(str(order[2])) == OrderRole.ENTRY:
                    booked = self.connection.execute(
                        """
                        UPDATE managed_positions
                        SET entry_fees=entry_fees+?,
                            realized_pnl=realized_pnl-(
                                CASE
                                    WHEN entry_filled_qty > 0
                                    THEN ? * MIN(exit_filled_qty, entry_filled_qty)
                                             / entry_filled_qty
                                    ELSE 0
                                END
                            ),
                            updated_at=?
                        WHERE account_id=? AND entry_client_order_id=?
                        """,
                        (fee, fee, now, account_id, str(entry_client_order_id)),
                    )
                else:
                    booked = self.connection.execute(
                        """
                        UPDATE managed_positions
                        SET exit_fees=exit_fees+?, realized_pnl=realized_pnl-?, updated_at=?
                        WHERE account_id=? AND entry_client_order_id=?
                        """,
                        (fee, fee, now, account_id, str(entry_client_order_id)),
                    )
                if booked.rowcount:
                    self.connection.execute(
                        "UPDATE fee_activities SET booked=1 WHERE activity_id=?",
                        (activity_id,),
                    )
                else:
                    # No lot to book against yet. The row stays pending and a
                    # later pass re-reads the activity and retries; every
                    # reconcile pass can rediscover the same order, so this
                    # transient state must not warn.
                    log.debug(
                        "Fee recorded before its managed lot exists: activity_id=%s client_order_id=%s fee=%s",
                        activity_id,
                        order[0],
                        fee,
                    )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    def get_reconcile_watermark(self, account_id: str, kind: str) -> datetime | None:
        """Last successfully reconciled watermark for a recovery pass, or None."""
        with self._lock:
            row = self.connection.execute(
                """
                SELECT watermark FROM reconcile_watermarks
                WHERE account_id=? AND kind=?
                """,
                (account_id, kind),
            ).fetchone()
        if row is None or not row[0]:
            return None
        watermark = datetime.fromisoformat(str(row[0]))
        if watermark.tzinfo is None:
            watermark = watermark.replace(tzinfo=UTC)
        return watermark

    def set_reconcile_watermark(self, account_id: str, kind: str, watermark: datetime) -> None:
        """Persist the watermark only after a pass completed without exception."""
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO reconcile_watermarks(account_id, kind, watermark, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, kind) DO UPDATE SET
                    watermark=excluded.watermark, updated_at=excluded.updated_at
                """,
                (account_id, kind, watermark.isoformat(), datetime.now(UTC).isoformat()),
            )
            self.connection.commit()

    def holdings_match_broker(self, account_id: str, broker_quantities: dict[str, float], tol: float = 1e-9) -> bool:
        """True when ledger owned quantities equal broker quantities within tol.

        Used by restart tests to assert ``holdings == broker after reconcile``
        without silently overwriting a material ``QUANTITY_MISMATCH``.
        """
        ledger_quantities: dict[str, float] = {}
        for pos in self.managed_entries_for_account(account_id):
            if pos.status in {"open", "manual_intervention_required"}:
                ledger_quantities[pos.canonical_symbol] = ledger_quantities.get(pos.canonical_symbol, 0.0) + float(
                    pos.owned_qty
                )
        all_symbols = set(ledger_quantities) | set(broker_quantities)
        for sym in all_symbols:
            if abs(ledger_quantities.get(sym, 0.0) - broker_quantities.get(sym, 0.0)) > tol:
                return False
        return True

    def apply_execution_event(self, event: ExecutionEvent) -> ExecutionTransition:
        """Apply one broker event atomically using cumulative fill quantities."""
        terminal_states = {
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.MANUAL_INTERVENTION_REQUIRED,
        }
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    """
                    SELECT cumulative_filled_qty, average_fill_price, terminal_state,
                           entry_client_order_id, order_role, side
                    FROM orders WHERE client_order_id=?
                    """,
                    (event.client_order_id,),
                ).fetchone()
                previous_filled = float(existing[0]) if existing else 0.0
                previous_average = float(existing[1]) if existing and existing[1] is not None else None
                previous_state = OrderState(existing[2]) if existing and existing[2] is not None else None
                existing_side = str(existing[5] or "").strip().lower() if existing else ""
                role = OrderRole(existing[4]) if existing else event.role
                if role == OrderRole.MANUAL_EXIT and event.role != OrderRole.MANUAL_EXIT:
                    role = event.role
                entry_client_order_id = self._resolve_entry_client_order_id(event, existing)

                requested = max(0.0, event.requested_qty)
                cumulative = min(max(0.0, event.cumulative_filled_qty), requested)
                effective_cumulative = max(previous_filled, cumulative)
                fill_delta = max(0.0, cumulative - previous_filled)
                fill_price = self._delta_fill_price(previous_average, previous_filled, cumulative, fill_delta, event)
                current_state = event.state
                if previous_state in terminal_states and current_state not in terminal_states:
                    current_state = previous_state
                terminal_state = (
                    current_state.value
                    if current_state in terminal_states
                    else (
                        previous_state.value
                        if previous_state is not None and previous_state in terminal_states
                        else None
                    )
                )
                now = event.occurred_at.isoformat()
                fee_delta = 0.0
                if event.fee > 0:
                    activity_id = event.activity_id or (
                        f"{event.client_order_id}:{event.occurred_at.isoformat()}:"
                        f"{event.cumulative_filled_qty}:{event.fee}"
                    )
                    inserted_fee = self.connection.execute(
                        """
                        INSERT OR IGNORE INTO fee_activities(
                            activity_id, client_order_id, fee, occurred_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (activity_id, event.client_order_id, event.fee, now),
                    )
                    if inserted_fee.rowcount:
                        fee_delta = event.fee
                # A non-entry fill that cannot be linked to a managed lot is a
                # real fill with no owner: never drop it silently, never stamp
                # the row terminal, and log the recovery action.  The gate is
                # the missing link, not the fill delta — reconciliation
                # re-emits the same event on every pass, and a delta-gated
                # guard would let the replay re-stamp the row terminal.
                # Backfilled rows for already-completed broker fills (every
                # portfolio SELL, which lives outside this ledger) are stamped
                # terminal as reported; only real execution events stay open.
                if (
                    role != OrderRole.ENTRY
                    and entry_client_order_id is None
                    and effective_cumulative > 0
                    and not event.client_order_id.startswith(FILL_ACTIVITY_RECOVERY_PREFIX)
                ):
                    terminal_state = None
                    if fill_delta > 0 or fee_delta > 0:
                        log.warning(
                            "Unlinked execution fill: client_order_id=%s broker_order_id=%s "
                            "role=%s fill_delta=%s fee=%s "
                            "recovery_action=await_managed_entry_link",
                            event.client_order_id,
                            event.broker_order_id,
                            role.value,
                            fill_delta,
                            fee_delta,
                        )
                # Never guess the side: the event carries no direction, so the
                # only real evidence is the linked lot's recorded side.  With
                # no link the row keeps the previous empty value, which the
                # conflict clause backfills once the link appears.
                order_side = self._resolved_order_side(
                    event.client_order_id, role, entry_client_order_id, existing_side
                )
                self.connection.execute(
                    """
                    INSERT INTO orders(
                        client_order_id, account_id, broker_order_id,
                        parent_broker_order_id, entry_client_order_id, order_role,
                        canonical_symbol, market, side, requested_qty,
                        cumulative_filled_qty, average_fill_price, terminal_state,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_order_id) DO UPDATE SET
                        account_id=excluded.account_id,
                        broker_order_id=COALESCE(excluded.broker_order_id, orders.broker_order_id),
                        parent_broker_order_id=COALESCE(
                            excluded.parent_broker_order_id, orders.parent_broker_order_id
                        ),
                        entry_client_order_id=COALESCE(
                            excluded.entry_client_order_id, orders.entry_client_order_id
                        ),
                        order_role=excluded.order_role,
                        side=CASE WHEN orders.side='' THEN excluded.side ELSE orders.side END,
                        requested_qty=MAX(orders.requested_qty, excluded.requested_qty),
                        cumulative_filled_qty=MAX(
                            orders.cumulative_filled_qty, excluded.cumulative_filled_qty
                        ),
                        average_fill_price=CASE
                            WHEN excluded.cumulative_filled_qty >= orders.cumulative_filled_qty
                            THEN COALESCE(excluded.average_fill_price, orders.average_fill_price)
                            ELSE orders.average_fill_price
                        END,
                        terminal_state=COALESCE(orders.terminal_state, excluded.terminal_state),
                        updated_at=excluded.updated_at
                    """,
                    (
                        event.client_order_id,
                        event.account_id,
                        event.broker_order_id or None,
                        event.parent_order_id,
                        entry_client_order_id,
                        role.value,
                        canonical_symbol(event.symbol, event.market),
                        event.market.value,
                        order_side,
                        requested,
                        effective_cumulative,
                        event.average_fill_price,
                        terminal_state,
                        now,
                    ),
                )
                payload = json.dumps(
                    {
                        "role": role.value,
                        "requested_qty": requested,
                        "cumulative_filled_qty": cumulative,
                        "average_fill_price": event.average_fill_price,
                    },
                    sort_keys=True,
                )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO order_events(
                        client_order_id, broker_order_id, event_type,
                        broker_timestamp, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.client_order_id,
                        event.broker_order_id,
                        current_state.value if current_state else "unknown_event",
                        now,
                        payload,
                    ),
                )
                position_delta, realized_pnl_delta = self._apply_position_deltas_tx(
                    event.account_id,
                    role,
                    entry_client_order_id,
                    fill_delta,
                    fill_price,
                    fee_delta,
                    event.broker_order_id,
                    now,
                )
                # Execution-quality capture (telemetry, REPORT ONLY): record
                # the terminal fill timestamp, fill delay vs submission,
                # adverse slippage vs the decision reference price, and the
                # actual fee on the authoritative orders row. None of this
                # changes order behavior.
                if (
                    fill_delta > 0
                    or fee_delta > 0
                    or (current_state in terminal_states and event.average_fill_price is not None)
                ):
                    captured = self.connection.execute(
                        """
                        SELECT submitted_at, reference_price, side
                        FROM orders WHERE client_order_id=?
                        """,
                        (event.client_order_id,),
                    ).fetchone()
                    submitted_at = str(captured[0]) if captured and captured[0] else None
                    reference = float(captured[1]) if captured and captured[1] is not None else None
                    direction = str(captured[2]) if captured and captured[2] else ""
                    delay: float | None = None
                    slippage: float | None = None
                    if fill_delta > 0 and fill_price > 0:
                        if submitted_at:
                            try:
                                occurred_dt = event.occurred_at
                                submitted_dt = datetime.fromisoformat(submitted_at)
                                raw_delay = (occurred_dt - submitted_dt).total_seconds()
                                if raw_delay >= 0:
                                    delay = raw_delay
                            except (TypeError, ValueError):
                                delay = None
                        if reference and reference > 0:
                            sign: float | None = None
                            if direction in {"long", "buy"}:
                                sign = 1.0
                            elif direction in {"sell", "short"}:
                                sign = -1.0
                            if sign is not None:
                                slippage = (fill_price - reference) / reference * 10_000.0 * sign
                    self.connection.execute(
                        """
                        UPDATE orders
                        SET filled_at=COALESCE(?, filled_at),
                            fill_delay_seconds=COALESCE(?, fill_delay_seconds),
                            slippage_bps=COALESCE(?, slippage_bps),
                            actual_fee=actual_fee+?,
                            updated_at=?
                        WHERE client_order_id=?
                        """,
                        (
                            now if fill_delta > 0 else None,
                            delay,
                            slippage,
                            fee_delta,
                            now,
                            event.client_order_id,
                        ),
                    )
                self.connection.commit()
                return ExecutionTransition(
                    previous_state=previous_state,
                    current_state=current_state,
                    fill_delta=fill_delta,
                    position_delta=position_delta,
                    realized_pnl_delta=realized_pnl_delta,
                    entry_client_order_id=entry_client_order_id,
                )
            except Exception:
                self.connection.rollback()
                raise

    def _resolved_order_side(
        self, client_order_id: str, role: OrderRole, entry_client_order_id: str | None, existing_side: str
    ) -> str:
        """The side a fill order row really traded, or ``""`` when unknown.

        ``ExecutionEvent`` has no direction field, so the role says nothing
        about the side: a short entry still exits with a buy, and a buy-side
        protective stop on a short closes with a buy.  Guessing from the role
        would stamp the wrong sign on the slippage read of every such row.
        """
        if role == OrderRole.ENTRY or not entry_client_order_id or entry_client_order_id == client_order_id:
            return existing_side
        row = self.connection.execute(
            "SELECT side FROM orders WHERE client_order_id=?",
            (entry_client_order_id,),
        ).fetchone()
        entry_side = str(row[0] or "").strip().lower() if row else ""
        return _OPPOSITE_SIDES.get(entry_side, existing_side)

    def _resolve_entry_client_order_id(self, event: ExecutionEvent, existing: tuple[object, ...] | None) -> str | None:
        """Link a child event to its managed entry lot.

        Order of preference: the stored entry id, the parent broker id, an
        unambiguous broker-generated prefix match, then a single open lot for
        the symbol at the event time. Never guess between multiple lots.
        """
        entry_client_order_id = str(existing[3]) if existing and existing[3] else None
        role = OrderRole(str(existing[4])) if existing else event.role
        if entry_client_order_id is None and event.parent_order_id:
            parent = self.connection.execute(
                """
                SELECT COALESCE(entry_client_order_id, client_order_id)
                FROM orders WHERE broker_order_id=?
                """,
                (event.parent_order_id,),
            ).fetchone()
            if parent:
                entry_client_order_id = str(parent[0])
        if entry_client_order_id is None and role != OrderRole.ENTRY:
            named_parent = self.connection.execute(
                """
                SELECT entry_client_order_id
                FROM managed_positions
                WHERE account_id=? AND ? LIKE entry_client_order_id || '-%'
                ORDER BY length(entry_client_order_id) DESC LIMIT 1
                """,
                (event.account_id, event.client_order_id),
            ).fetchone()
            if named_parent:
                entry_client_order_id = str(named_parent[0])
        if entry_client_order_id is None and role != OrderRole.ENTRY:
            # Alpaca bracket child updates can omit parent_order_id and
            # use broker-generated UUID client ids. Link only when one
            # managed lot is an unambiguous match; never guess between
            # multiple same-symbol entries.
            candidates = self.connection.execute(
                """
                SELECT entry_client_order_id
                FROM managed_positions
                WHERE account_id=? AND canonical_symbol=? AND market=?
                  AND status IN ('open', 'manual_intervention_required')
                  AND remaining_owned_qty > 0
                  AND opened_at <= ?
                ORDER BY opened_at
                """,
                (
                    event.account_id,
                    canonical_symbol(event.symbol, event.market),
                    event.market.value,
                    event.occurred_at.isoformat(),
                ),
            ).fetchall()
            if len(candidates) == 1:
                entry_client_order_id = str(candidates[0][0])
        if role == OrderRole.ENTRY:
            entry_client_order_id = event.client_order_id
        return entry_client_order_id

    @staticmethod
    def _delta_fill_price(
        previous_average: float | None,
        previous_filled: float,
        cumulative: float,
        fill_delta: float,
        event: ExecutionEvent,
    ) -> float:
        """Weighted price of the newly-filled delta from cumulative averages."""
        average_fill_price = event.average_fill_price
        if fill_delta > 0 and average_fill_price is not None and previous_average is not None and previous_filled > 0:
            return max(
                0.0,
                (average_fill_price * cumulative - previous_average * previous_filled) / fill_delta,
            )
        return event.average_fill_price or previous_average or 0.0

    def _apply_position_deltas_tx(
        self,
        account_id: str,
        role: OrderRole,
        entry_client_order_id: str | None,
        fill_delta: float,
        fill_price: float,
        fee_delta: float,
        broker_order_id: str | None,
        now: str,
    ) -> tuple[float, float]:
        """Apply entry/exit fill and fee-only deltas inside the open transaction."""
        position_delta = 0.0
        realized_pnl_delta = 0.0
        if fill_delta > 0 and fill_price > 0 and entry_client_order_id:
            if role == OrderRole.ENTRY:
                order = self._load_order_intent_tx(entry_client_order_id)
                if order is not None:
                    self._record_entry_fill_delta_tx(
                        account_id,
                        order,
                        fill_delta,
                        fill_price,
                        fee_delta,
                        broker_order_id,
                        now,
                    )
                    position_delta = fill_delta
            else:
                realized_pnl_delta = self._record_exit_fill_delta_tx(
                    account_id,
                    entry_client_order_id,
                    fill_delta,
                    fill_price,
                    fee_delta,
                    now,
                )
                position_delta = -fill_delta
        elif fee_delta > 0 and entry_client_order_id:
            if role == OrderRole.ENTRY:
                self.connection.execute(
                    """
                    UPDATE managed_positions
                    SET entry_fees=entry_fees+?, updated_at=?
                    WHERE account_id=? AND entry_client_order_id=?
                    """,
                    (fee_delta, now, account_id, entry_client_order_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE managed_positions
                    SET exit_fees=exit_fees+?, realized_pnl=realized_pnl-?, updated_at=?
                    WHERE account_id=? AND entry_client_order_id=?
                    """,
                    (fee_delta, fee_delta, now, account_id, entry_client_order_id),
                )
                realized_pnl_delta = -fee_delta
        return position_delta, realized_pnl_delta

    def _load_order_intent_tx(self, client_order_id: str) -> OrderIntent | None:
        row = self.connection.execute(
            """
            SELECT signal_id, canonical_symbol, market, side, requested_qty,
                   entry_price, stop_price, target_price, strategy
            FROM orders WHERE client_order_id=?
            """,
            (client_order_id,),
        ).fetchone()
        if row is None or any(row[index] is None for index in (0, 5, 6, 7, 8)):
            return None
        return OrderIntent(
            signal_id=str(row[0]),
            symbol=str(row[1]),
            market=Market(row[2]),
            direction=Direction(row[3]),
            qty=float(row[4]),
            entry_price=float(row[5]),
            stop_price=float(row[6]),
            target_price=float(row[7]),
            strategy=str(row[8]),
            client_order_id=client_order_id,
        )

    def managed_order_ids(self, account_id: str, canonical: str) -> set[str]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT o.broker_order_id
                FROM orders o
                JOIN managed_positions p
                  ON p.entry_client_order_id=o.entry_client_order_id
                WHERE p.account_id=? AND p.canonical_symbol=?
                  AND p.status IN ('open', 'manual_intervention_required')
                  AND o.broker_order_id IS NOT NULL
                """,
                (account_id, canonical),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def managed_entry_broker_order_ids(self, account_id: str, canonical: str) -> set[str]:
        """Return every still-open entry parent for an aggregated symbol position."""
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT broker_order_id
                FROM managed_positions
                WHERE account_id=? AND canonical_symbol=?
                  AND status IN ('open', 'manual_intervention_required')
                  AND remaining_owned_qty > 0
                  AND broker_order_id IS NOT NULL
                """,
                (account_id, canonical),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def managed_client_order_ids(self, account_id: str, canonical: str) -> set[str]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT o.client_order_id
                FROM orders o
                JOIN managed_positions p
                  ON p.entry_client_order_id=o.entry_client_order_id
                WHERE p.account_id=? AND p.canonical_symbol=?
                  AND p.status IN ('open', 'manual_intervention_required')
                """,
                (account_id, canonical),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def order_for_broker_id(self, account_id: str, broker_order_id: str) -> str | None:
        """Client order id of any ledger order with this broker order id.

        Used by fill-activity recovery to avoid re-booking an exit whose
        broker order is already in the ledger under a different client id
        (e.g. a portfolio SELL row or an earlier recovery row); inserting a
        second row would violate the unique broker-order index.
        """
        with self._lock:
            row = self.connection.execute(
                """
                SELECT client_order_id FROM orders
                WHERE account_id=? AND broker_order_id=?
                LIMIT 1
                """,
                (account_id, broker_order_id),
            ).fetchone()
            if row is None:
                return None
            return str(row[0])

    def entry_order_for_broker_id(self, account_id: str, broker_order_id: str) -> tuple[OrderIntent, float] | None:
        """Return a durable entry and its known cumulative fill for recovery."""
        with self._lock:
            row = self.connection.execute(
                """
                SELECT client_order_id, cumulative_filled_qty
                FROM orders
                WHERE account_id=? AND broker_order_id=? AND order_role='entry'
                LIMIT 1
                """,
                (account_id, broker_order_id),
            ).fetchone()
            if row is None:
                return None
            order = self._load_order_intent_tx(str(row[0]))
            if order is None:
                return None
            return order, float(row[1] or 0)

    def save_risk_state(self, state: RiskState) -> None:
        payload = json.dumps(
            {
                "day_start_equity": state.day_start_equity,
                "peak_equity": state.peak_equity,
                "realized_pnl": state.realized_pnl,
                "unrealized_pnl": state.unrealized_pnl,
                "fees": state.fees,
                "consecutive_losses": state.consecutive_losses,
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
            payload = json.loads(row[0])
            return RiskState(
                account_id=account_id,
                session_date=session_date,
                day_start_equity=float(payload["day_start_equity"]),
                peak_equity=float(payload["peak_equity"]),
                realized_pnl=float(payload["realized_pnl"]),
                unrealized_pnl=float(payload["unrealized_pnl"]),
                fees=float(payload["fees"]),
                consecutive_losses=int(payload["consecutive_losses"]),
                circuit_breaker_reason=payload.get("circuit_breaker_reason"),
                updated_at=datetime.fromisoformat(str(row[1])),
                peak_epoch=int(payload.get("peak_epoch", 0) or 0),
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
                incidents.append(
                    SafetyIncident(
                        account_id=str(row[0]),
                        reason=str(row[1]),
                        scope=str(row[2]),
                        activated_at=datetime.fromisoformat(row[3]),
                        updated_at=datetime.fromisoformat(row[4]),
                    )
                )
            except (TypeError, ValueError):
                # A corrupt incident row must not fail runtime startup; the
                # halt it describes is skipped (fail-open for the row itself).
                log.warning(
                    "Ignoring corrupt safety_incident row for account=%s reason=%s",
                    account_id,
                    row[1],
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


__all__ = ["ExecutionLedger", "ManagedPosition"]
