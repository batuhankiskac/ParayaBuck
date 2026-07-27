from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Fill, Market, OrderIntent
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.risk.manager import RiskState


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


class ExecutionLedger:
    """Small transactional ownership ledger shared by execution and reconciliation."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
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
                CREATE TABLE IF NOT EXISTS broker_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    UNIQUE(client_order_id, qty, price, timestamp)
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
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
                """
            )
            self.connection.commit()

    def record_entry_fill(
        self,
        account_id: str,
        order: OrderIntent,
        fill: Fill,
        broker_order_id: str | None = None,
    ) -> None:
        symbol = canonical_symbol(order.symbol, order.market)
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    """
                    SELECT owned_qty FROM managed_positions
                    WHERE account_id=? AND canonical_symbol=? AND entry_client_order_id=?
                    """,
                    (account_id, symbol, order.client_order_id),
                ).fetchone()
                owned_qty = max(float(existing[0]) if existing else 0.0, fill.qty)
                self.connection.execute(
                    """
                    INSERT INTO managed_positions(
                        account_id, canonical_symbol, market, strategy, source_signal_id,
                        entry_client_order_id, broker_order_id, owned_qty, filled_avg_price,
                        original_stop, original_target, status, opened_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL)
                    ON CONFLICT(account_id, canonical_symbol, entry_client_order_id) DO UPDATE SET
                        broker_order_id=excluded.broker_order_id,
                        owned_qty=excluded.owned_qty,
                        filled_avg_price=excluded.filled_avg_price,
                        status='open',
                        closed_at=NULL
                    """,
                    (
                        account_id,
                        symbol,
                        order.market.value,
                        order.strategy,
                        order.signal_id,
                        order.client_order_id,
                        broker_order_id,
                        owned_qty,
                        fill.price,
                        order.stop_price,
                        order.target_price,
                        now,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO broker_fills
                    (client_order_id, symbol, qty, price, commission, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.client_order_id,
                        symbol,
                        fill.qty,
                        fill.price,
                        fill.commission,
                        fill.timestamp.isoformat(),
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def managed_for_account(self, account_id: str) -> dict[str, ManagedPosition]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT account_id, canonical_symbol, market, strategy, source_signal_id,
                       entry_client_order_id, broker_order_id, owned_qty, filled_avg_price,
                       original_stop, original_target, status
                FROM managed_positions
                WHERE account_id=? AND status IN ('open', 'manual_intervention_required')
                ORDER BY opened_at
                """,
                (account_id,),
            ).fetchall()
        result: dict[str, ManagedPosition] = {}
        for row in rows:
            item = ManagedPosition(
                account_id=row[0],
                canonical_symbol=row[1],
                market=Market(row[2]),
                strategy=row[3],
                source_signal_id=row[4],
                entry_client_order_id=row[5],
                broker_order_id=row[6],
                owned_qty=float(row[7]),
                filled_avg_price=float(row[8]),
                original_stop=float(row[9]),
                original_target=float(row[10]),
                status=row[11],
            )
            previous = result.get(item.canonical_symbol)
            if previous is None:
                result[item.canonical_symbol] = item
            else:
                result[item.canonical_symbol] = ManagedPosition(
                    **{
                        **previous.__dict__,
                        "owned_qty": previous.owned_qty + item.owned_qty,
                    }
                )
        return result

    def record_order_update(
        self,
        client_order_id: str,
        broker_order_id: str,
        event_type: str,
        broker_timestamp: datetime,
        requested_qty: float,
        filled_qty: float,
        average_fill_price: float | None,
        terminal_state: str | None,
    ) -> None:
        payload = json.dumps(
            {
                "requested_qty": requested_qty,
                "filled_qty": filled_qty,
                "average_fill_price": average_fill_price,
            },
            sort_keys=True,
        )
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO order_events(
                        client_order_id, broker_order_id, event_type,
                        broker_timestamp, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        client_order_id,
                        broker_order_id,
                        event_type,
                        broker_timestamp.isoformat(),
                        payload,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO executions(
                        client_order_id, requested_qty, filled_qty,
                        average_fill_price, terminal_state, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_order_id) DO UPDATE SET
                        requested_qty=excluded.requested_qty,
                        filled_qty=MAX(executions.filled_qty, excluded.filled_qty),
                        average_fill_price=COALESCE(
                            excluded.average_fill_price, executions.average_fill_price
                        ),
                        terminal_state=COALESCE(
                            excluded.terminal_state, executions.terminal_state
                        ),
                        updated_at=excluded.updated_at
                    """,
                    (
                        client_order_id,
                        requested_qty,
                        filled_qty,
                        average_fill_price,
                        terminal_state,
                        broker_timestamp.isoformat(),
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

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
        with self._lock:
            self.connection.close()


__all__ = ["ExecutionLedger", "ManagedPosition"]
