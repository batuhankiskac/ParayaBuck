from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.risk.manager import RiskState
from parayabuck.storage import SQLiteStore, migrate_legacy_decision_orders


@dataclass(frozen=True)
class ManagedPosition:
    account_id: str
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
    """Small transactional ownership ledger shared by execution and reconciliation."""

    def __init__(self, path: str, *, store: SQLiteStore | None = None) -> None:
        self.path = Path(path)
        self._store = store or SQLiteStore(self.path)
        self._store.acquire()
        self.connection = self._store.connection
        self._lock = self._store.lock
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            migrate_legacy_decision_orders(self.connection)
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
            self._ensure_column("managed_positions", "entry_filled_qty", "REAL NOT NULL DEFAULT 0")
            self._ensure_column("managed_positions", "exit_filled_qty", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(
                "managed_positions", "remaining_owned_qty", "REAL NOT NULL DEFAULT 0"
            )
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
            self._ensure_column(
                "managed_positions", "stop_state", "TEXT NOT NULL DEFAULT 'pending'"
            )
            self._ensure_column(
                "managed_positions", "target_state", "TEXT NOT NULL DEFAULT 'watching'"
            )
            self._ensure_column("managed_positions", "exit_client_order_id", "TEXT")
            self._ensure_column("managed_positions", "exit_broker_order_id", "TEXT")
            self._ensure_column("managed_positions", "updated_at", "TEXT")
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
            self.connection.commit()

    def _ensure_column(self, table: str, name: str, definition: str) -> None:
        columns = {str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def register_entry_order(
        self,
        account_id: str,
        order: OrderIntent,
        broker_order_id: str | None = None,
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
                raise ValueError(
                    "Single-managed-entry invariant violated for "
                    f"{symbol}: existing={existing_open[0]}"
                )
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
                raise ValueError(
                    "Single-managed-entry invariant violated for "
                    f"{symbol}: pending={existing_pending[0]}"
                )
            self.connection.execute(
                """
                INSERT INTO orders(
                    client_order_id, account_id, broker_order_id,
                    parent_broker_order_id, entry_client_order_id, order_role,
                    canonical_symbol, market, side, requested_qty,
                    cumulative_filled_qty, signal_id, strategy, entry_price,
                    stop_price, target_price, updated_at
                ) VALUES (?, ?, ?, NULL, ?, 'entry', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    broker_order_id=COALESCE(excluded.broker_order_id, orders.broker_order_id),
                    requested_qty=excluded.requested_qty,
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
        average = (
            (previous_average * previous_qty + price * qty_delta) / total_entry
            if total_entry > 0
            else price
        )
        self.connection.execute(
            """
            INSERT INTO managed_positions(
                account_id, canonical_symbol, market, strategy, source_signal_id,
                entry_client_order_id, broker_order_id, owned_qty, entry_filled_qty,
                exit_filled_qty, remaining_owned_qty, filled_avg_price,
                original_stop, original_target, realized_pnl, entry_fees, exit_fees,
                protection_state, status, opened_at, closed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, ?, 0,
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

    def register_protective_stop(
        self,
        account_id: str,
        entry_client_order_id: str,
        client_order_id: str,
        broker_order_id: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute(
                """
                UPDATE managed_positions
                SET protective_stop_client_order_id=?,
                    protective_stop_broker_order_id=?,
                    stop_state='open', protection_state='active', updated_at=?
                WHERE account_id=? AND entry_client_order_id=?
                  AND status IN ('open', 'manual_intervention_required')
                """,
                (
                    client_order_id,
                    broker_order_id,
                    now,
                    account_id,
                    entry_client_order_id,
                ),
            )
            self.connection.commit()

    def claim_symbol_exit(
        self,
        account_id: str,
        entry_client_order_id: str,
        *,
        reason: str,
    ) -> ManagedPosition | None:
        """Atomically win the stop/target race for one managed entry."""
        target_state = "target_triggered" if reason == "target" else "stop_failover_triggered"
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                updated = self.connection.execute(
                    """
                    UPDATE managed_positions
                    SET target_state=?, stop_state='cancel_requested', updated_at=?
                    WHERE account_id=? AND entry_client_order_id=?
                      AND market='crypto' AND status='open'
                      AND remaining_owned_qty > 0
                      AND target_state IN ('watching', 'protected')
                      AND stop_state IN ('open', 'triggered_unfilled')
                      AND exit_client_order_id IS NULL
                    """,
                    (
                        target_state,
                        now,
                        account_id,
                        entry_client_order_id,
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        if not updated.rowcount:
            return None
        return next(
            (
                item
                for item in self.managed_entries_for_account(account_id)
                if item.entry_client_order_id == entry_client_order_id
            ),
            None,
        )

    def set_exit_state(
        self,
        account_id: str,
        entry_client_order_id: str,
        *,
        stop_state: str | None = None,
        target_state: str | None = None,
        exit_client_order_id: str | None = None,
        exit_broker_order_id: str | None = None,
        clear_exit_order: bool = False,
    ) -> None:
        assignments = ["updated_at=?"]
        values: list[object] = [datetime.now(UTC).isoformat()]
        for column, value in (
            ("stop_state", stop_state),
            ("target_state", target_state),
            ("exit_client_order_id", exit_client_order_id),
            ("exit_broker_order_id", exit_broker_order_id),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        if clear_exit_order:
            assignments.extend(("exit_client_order_id=NULL", "exit_broker_order_id=NULL"))
        values.extend((account_id, entry_client_order_id))
        with self._lock:
            self.connection.execute(
                f"""
                UPDATE managed_positions SET {", ".join(assignments)}
                WHERE account_id=? AND entry_client_order_id=?
                """,
                values,
            )
            self.connection.commit()

    def managed_entries_for_account(self, account_id: str) -> list[ManagedPosition]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT account_id, canonical_symbol, market, strategy, source_signal_id,
                       entry_client_order_id, broker_order_id, remaining_owned_qty,
                       entry_filled_qty, exit_filled_qty, filled_avg_price,
                       original_stop, original_target, realized_pnl, entry_fees,
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
            account_id=str(row[0]),
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
            realized_pnl=float(str(row[13])),
            entry_fees=float(str(row[14])),
            exit_fees=float(str(row[15])),
            protection_state=ProtectionState(str(row[16])),
            status=str(row[17]),
            protective_stop_client_order_id=str(row[18]) if row[18] else None,
            protective_stop_broker_order_id=str(row[19]) if row[19] else None,
            stop_state=str(row[20]),
            target_state=str(row[21]),
            exit_client_order_id=str(row[22]) if row[22] else None,
            exit_broker_order_id=str(row[23]) if row[23] else None,
            updated_at=datetime.fromisoformat(str(row[24])) if row[24] else None,
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
        """Apply one Alpaca fill-activity fee exactly once."""
        if fee <= 0 or not activity_id or not broker_order_id:
            return False
        now = occurred_at.isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                order = self.connection.execute(
                    """
                    SELECT entry_client_order_id, order_role
                    FROM orders WHERE broker_order_id=?
                    """,
                    (broker_order_id,),
                ).fetchone()
                if order is None or order[0] is None:
                    self.connection.rollback()
                    return False
                inserted = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO fee_activities(
                        activity_id, client_order_id, fee, occurred_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (activity_id, str(order[0]), fee, now),
                )
                if not inserted.rowcount:
                    self.connection.commit()
                    return False
                if OrderRole(str(order[1])) == OrderRole.ENTRY:
                    self.connection.execute(
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
                        (fee, fee, now, account_id, str(order[0])),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE managed_positions
                        SET exit_fees=exit_fees+?, realized_pnl=realized_pnl-?, updated_at=?
                        WHERE account_id=? AND entry_client_order_id=?
                        """,
                        (fee, fee, now, account_id, str(order[0])),
                    )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

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
                           entry_client_order_id, order_role
                    FROM orders WHERE client_order_id=?
                    """,
                    (event.client_order_id,),
                ).fetchone()
                previous_filled = float(existing[0]) if existing else 0.0
                previous_average = (
                    float(existing[1]) if existing and existing[1] is not None else None
                )
                previous_state = (
                    OrderState(existing[2]) if existing and existing[2] is not None else None
                )
                entry_client_order_id = str(existing[3]) if existing and existing[3] else None
                role = OrderRole(existing[4]) if existing else event.role
                if role == OrderRole.MANUAL_EXIT and event.role != OrderRole.MANUAL_EXIT:
                    role = event.role
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
                if entry_client_order_id is None and event.role != OrderRole.ENTRY:
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
                if entry_client_order_id is None and event.role != OrderRole.ENTRY:
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

                requested = max(0.0, event.requested_qty)
                cumulative = min(max(0.0, event.cumulative_filled_qty), requested)
                effective_cumulative = max(previous_filled, cumulative)
                fill_delta = max(0.0, cumulative - previous_filled)
                fill_price = event.average_fill_price or previous_average or 0.0
                if (
                    fill_delta > 0
                    and event.average_fill_price is not None
                    and previous_average is not None
                    and previous_filled > 0
                ):
                    fill_price = max(
                        0.0,
                        (event.average_fill_price * cumulative - previous_average * previous_filled)
                        / fill_delta,
                    )
                current_state = event.state
                if previous_state in terminal_states and current_state not in terminal_states:
                    current_state = previous_state
                terminal_state = (
                    current_state.value
                    if current_state in terminal_states
                    else (previous_state.value if previous_state in terminal_states else None)
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
                self.connection.execute(
                    """
                    INSERT INTO orders(
                        client_order_id, account_id, broker_order_id,
                        parent_broker_order_id, entry_client_order_id, order_role,
                        canonical_symbol, market, side, requested_qty,
                        cumulative_filled_qty, average_fill_price, terminal_state,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
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
                position_delta = 0.0
                realized_pnl_delta = 0.0
                if fill_delta > 0 and fill_price > 0 and entry_client_order_id:
                    if role == OrderRole.ENTRY:
                        order = self._load_order_intent_tx(entry_client_order_id)
                        if order is not None:
                            self._record_entry_fill_delta_tx(
                                event.account_id,
                                order,
                                fill_delta,
                                fill_price,
                                fee_delta,
                                event.broker_order_id,
                                now,
                            )
                            position_delta = fill_delta
                    else:
                        realized_pnl_delta = self._record_exit_fill_delta_tx(
                            event.account_id,
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
                            (fee_delta, now, event.account_id, entry_client_order_id),
                        )
                    else:
                        self.connection.execute(
                            """
                            UPDATE managed_positions
                            SET exit_fees=exit_fees+?, realized_pnl=realized_pnl-?, updated_at=?
                            WHERE account_id=? AND entry_client_order_id=?
                            """,
                            (
                                fee_delta,
                                fee_delta,
                                now,
                                event.account_id,
                                entry_client_order_id,
                            ),
                        )
                        realized_pnl_delta = -fee_delta
                self.connection.commit()
                return ExecutionTransition(
                    previous_state=previous_state,
                    current_state=current_state,
                    fill_delta=fill_delta,
                    position_delta=position_delta,
                    reservation_delta=-fill_delta if role == OrderRole.ENTRY else 0.0,
                    realized_pnl_delta=realized_pnl_delta,
                    protection_state=ProtectionState.UNPROTECTED,
                    entry_client_order_id=entry_client_order_id,
                )
            except Exception:
                self.connection.rollback()
                raise

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

    def pending_entry_orders(self, account_id: str) -> list[tuple[OrderIntent, float]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT client_order_id, cumulative_filled_qty
                FROM orders
                WHERE account_id=? AND order_role='entry'
                  AND cumulative_filled_qty < requested_qty
                  AND terminal_state IS NULL
                """,
                (account_id,),
            ).fetchall()
            result: list[tuple[OrderIntent, float]] = []
            for client_order_id, cumulative in rows:
                order = self._load_order_intent_tx(str(client_order_id))
                if order is not None:
                    result.append((order, max(0.0, order.qty - float(cumulative))))
            return result

    def entry_order_for_broker_id(
        self, account_id: str, broker_order_id: str
    ) -> tuple[OrderIntent, float] | None:
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

    def load_risk_state(self, account_id: str, session_date: object) -> RiskState | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT payload, updated_at FROM risk_state
                WHERE account_id=? AND session_date=?
                """,
                (account_id, str(session_date)),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        from datetime import date

        return RiskState(
            account_id=account_id,
            session_date=date.fromisoformat(str(session_date)),
            day_start_equity=float(payload["day_start_equity"]),
            peak_equity=float(payload["peak_equity"]),
            realized_pnl=float(payload["realized_pnl"]),
            unrealized_pnl=float(payload["unrealized_pnl"]),
            fees=float(payload["fees"]),
            consecutive_losses=int(payload["consecutive_losses"]),
            circuit_breaker_reason=payload.get("circuit_breaker_reason"),
            updated_at=datetime.fromisoformat(row[1]),
        )

    def close(self) -> None:
        self._store.release()


__all__ = ["ExecutionLedger", "ManagedPosition"]
