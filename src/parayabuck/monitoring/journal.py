from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import AccountSnapshot, Bar, Fill, OrderIntent, OrderState, Signal
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.storage import SQLiteStore, migrate_legacy_decision_orders


class DecisionJournal:
    """Durable audit trail for every signal, rejection, order and fill."""

    def __init__(self, path: str, *, store: SQLiteStore | None = None) -> None:
        self.path = Path(path)
        self._store = store or SQLiteStore(self.path)
        self._store.acquire()
        self.connection = self._store.connection
        self._lock = self._store.lock
        self._migrate()

    def _migrate(self) -> None:
        migrate_legacy_decision_orders(self.connection)
        self._migrate_decisions_table()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                strategy TEXT NOT NULL, score REAL NOT NULL, regime TEXT NOT NULL,
                accepted INTEGER NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_signal_id ON decisions(signal_id);
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

    def _migrate_decisions_table(self) -> None:
        columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(decisions)")}
        if not columns or "id" in columns:
            return
        self.connection.executescript(
            """
            ALTER TABLE decisions RENAME TO decisions_legacy;
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                strategy TEXT NOT NULL, score REAL NOT NULL, regime TEXT NOT NULL,
                accepted INTEGER NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO decisions(
                signal_id, timestamp, symbol, strategy, score, regime,
                accepted, reason, payload, recorded_at
            )
            SELECT signal_id, timestamp, symbol, strategy, score, regime,
                   accepted, reason, payload, timestamp
            FROM decisions_legacy;
            DROP TABLE decisions_legacy;
            """
        )

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
                """
                INSERT INTO decisions(
                    signal_id, timestamp, symbol, strategy, score, regime,
                    accepted, reason, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order.client_order_id,
                    datetime.now(UTC).isoformat(timespec="microseconds"),
                    order.symbol,
                    state.value,
                    reason,
                    payload,
                ),
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
        self._store.release()
