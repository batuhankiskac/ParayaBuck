from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from parayabuck.core.models import AccountSnapshot, Bar, Fill, OrderIntent, OrderState, Signal
from parayabuck.markets.regime import RegimeAssessment


class DecisionJournal:
    """Durable audit trail for every signal, rejection, order and fill."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        self._migrate_legacy_orders_table()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                signal_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                strategy TEXT NOT NULL, score REAL NOT NULL, regime TEXT NOT NULL,
                accepted INTEGER NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_orders (
                client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (client_order_id, timestamp, state)
            );
            CREATE TABLE IF NOT EXISTS fills (
                client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                qty REAL NOT NULL, price REAL NOT NULL, commission REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_snapshots (
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP, cash REAL NOT NULL, equity REAL NOT NULL,
                peak_equity REAL NOT NULL, daily_pnl REAL NOT NULL, positions TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scans (
                timestamp TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                regime TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (timestamp, symbol, timeframe)
            );
            """
        )
        self.connection.commit()

    def _migrate_legacy_orders_table(self) -> None:
        """Move the old audit table out of the execution ledger's namespace."""
        columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(orders)")
        }
        legacy_columns = {
            "client_order_id",
            "timestamp",
            "symbol",
            "state",
            "reason",
            "payload",
        }
        if not legacy_columns.issubset(columns) or "broker_order_id" in columns:
            return
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_orders (
                client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (client_order_id, timestamp, state)
            )
            """
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO decision_orders(
                client_order_id, timestamp, symbol, state, reason, payload
            )
            SELECT client_order_id, timestamp, symbol, state, reason, payload
            FROM orders
            """
        )
        self.connection.execute("DROP TABLE orders")

    def record_scan(self, bar: Bar, assessment: RegimeAssessment, reason: str) -> None:
        payload = json.dumps(
            {
                "close": bar.close,
                "trend_strength": assessment.trend_strength,
                "volatility_pct": assessment.volatility_pct,
                "regime_reasons": assessment.reasons,
            }
        )
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO scans VALUES (?, ?, ?, ?, ?, ?)",
                (
                    bar.timestamp.isoformat(),
                    bar.symbol,
                    bar.timeframe,
                    assessment.regime.value,
                    reason,
                    payload,
                ),
            )
            self.connection.commit()

    def record_decision(self, signal: Signal, accepted: bool, reason: str) -> None:
        payload = json.dumps({"reasons": signal.reasons, "risk_reward": signal.risk_reward})
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal.signal_id,
                    signal.timestamp.isoformat(),
                    signal.symbol,
                    signal.strategy,
                    signal.score,
                    signal.regime.value,
                    int(accepted),
                    reason,
                    payload,
                ),
            )
            self.connection.commit()

    def record_order(self, order: OrderIntent, state: OrderState, reason: str = "") -> None:
        payload = json.dumps(
            {
                "qty": order.qty,
                "entry": order.entry_price,
                "stop": order.stop_price,
                "target": order.target_price,
            }
        )
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO decision_orders
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
                """,
                (order.client_order_id, order.symbol, state.value, reason, payload),
            )
            self.connection.commit()

    def record_fill(self, fill: Fill) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?)",
                (
                    fill.client_order_id,
                    fill.timestamp.isoformat(),
                    fill.symbol,
                    fill.qty,
                    fill.price,
                    fill.commission,
                ),
            )
            self.connection.commit()

    def record_account(self, account: AccountSnapshot) -> None:
        positions = json.dumps(
            [
                {"symbol": p.symbol, "qty": p.qty, "price": p.current_price}
                for p in account.positions
            ]
        )
        with self._lock:
            self.connection.execute(
                """INSERT INTO account_snapshots
                (cash, equity, peak_equity, daily_pnl, positions)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    account.cash,
                    account.equity,
                    account.peak_equity,
                    account.daily_pnl,
                    positions,
                ),
            )
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()
