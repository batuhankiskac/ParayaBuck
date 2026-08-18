"""Production portfolio decision runtime shared by paper and live execution."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
    _floor_increment,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import bar_is_closed, equity_day_bar_is_closed
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.storage import SQLiteStore

# Alpaca order states stored by the repository: canonical statuses written by
# record_order/update_order plus raw TradingStream event names written by
# apply_trade_update ("fill", "partial_fill"). An order is terminal once it
# cannot fill any more quantity.
_TERMINAL_ORDER_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "done_for_day",
        "fill",
    }
)

# Order outcomes that are neither terminal nor provably working: the order
# may or may not exist at the broker. Dependent BUY orders must not be
# submitted while a required SELL sits in this state.
_AMBIGUOUS_ORDER_STATUSES = frozenset({"reconciliation_required", "manual_intervention_required"})

# Two-phase rebalance execution phases (persisted, restart-safe).
REBALANCE_PHASE_SELL_PENDING = "sell_pending"
REBALANCE_PHASE_BUY_READY = "buy_ready"
REBALANCE_PHASE_BUY_PENDING = "buy_pending"
REBALANCE_PHASE_COMPLETE = "complete"
REBALANCE_PHASE_BLOCKED = "blocked"
_NON_TERMINAL_REBALANCE_PHASES = frozenset(
    {
        REBALANCE_PHASE_SELL_PENDING,
        REBALANCE_PHASE_BUY_READY,
        REBALANCE_PHASE_BUY_PENDING,
    }
)


def is_terminal_order_status(status: str) -> bool:
    return str(status).lower() in _TERMINAL_ORDER_STATUSES


# Single SQL source for the terminal set above: queries must never hardcode
# a second copy that can drift from is_terminal_order_status.
TERMINAL_ORDER_STATUSES_SQL = ", ".join(
    f"'{status}'" for status in sorted(_TERMINAL_ORDER_STATUSES)
)


log = get_logger("parayabuck.portfolio.runtime_service")


class PortfolioRuntimeBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules: ...

    def all_open_order_symbols(self) -> set[str]: ...

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str: ...


class PortfolioDecisionService:
    """Build a strategy context exclusively from finalized point-in-time bars."""

    def build_context(
        self,
        strategy: PortfolioStrategy,
        history: dict[str, list[Bar]],
        account: AccountSnapshot,
        decision_timestamp: datetime,
        *,
        universe: object | None,
        universe_version: str,
    ) -> PortfolioContext:
        if any(
            not series or series[-1].timestamp != decision_timestamp for series in history.values()
        ):
            raise ValueError("Portfolio context requires a complete closed-bar batch")
        return PortfolioContext(
            decision_timestamp=decision_timestamp,
            market=strategy.market,
            timeframe=strategy.timeframe,
            history={symbol: tuple(series) for symbol, series in history.items()},
            universe=universe,
            universe_version=universe_version,
            equity=account.equity,
            holdings={
                canonical_symbol(position.symbol, position.market): position.qty
                for position in account.positions
                if position.market == strategy.market
            },
            prices={symbol: series[-1].close for symbol, series in history.items()},
            benchmark_symbol="BTC/USD" if strategy.market == Market.CRYPTO else "SPY",
        )


@dataclass(frozen=True)
class OpenPortfolioOrder:
    """One non-terminal portfolio order attributed to an account."""

    client_order_id: str
    symbol: str
    market: Market
    side: str
    quantity: float
    cumulative_filled_quantity: float


@dataclass(frozen=True)
class RebalanceExecutionState:
    """Persisted two-phase rebalance lifecycle for one decision.

    A decision with both SELL and BUY legs executes in two phases: the
    risk-reducing sells are submitted first and the dependent buys only after
    every required sell is terminal and a fresh account snapshot proves the
    remaining BUY exposure is still safe. The state survives process restarts
    so a rebalance interrupted between phases resumes exactly once.
    """

    decision_id: str
    account_id: str
    market: Market
    phase: str
    status: str
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.phase in _NON_TERMINAL_REBALANCE_PHASES and self.status == "active"


@dataclass(frozen=True)
class PortfolioRuntimeResult:
    decision_id: str | None
    target: TargetPortfolio | None
    risk: RiskOverlayDecision | None
    orders: tuple[PortfolioOrderRequest, ...]
    submitted_order_ids: tuple[str, ...]
    status: str


class PortfolioStateRepository:
    def __init__(self, path: str | Path, *, store: SQLiteStore | None = None) -> None:
        self.store = store or SQLiteStore(path)
        self._owns_store = store is None
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_runtime_sessions (
                    session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_decisions (
                    decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '', configuration_digest TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL, market TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    target_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                    UNIQUE(account_id, configuration_digest, decision_timestamp, market),
                    FOREIGN KEY(session_id) REFERENCES portfolio_runtime_sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_orders (
                    client_order_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                    quantity REAL NOT NULL, reference_price REAL NOT NULL,
                    status TEXT NOT NULL, broker_order_id TEXT NOT NULL DEFAULT '',
                    cumulative_filled_quantity REAL NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES portfolio_decisions(decision_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_fills (
                    activity_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, quantity REAL NOT NULL,
                    price REAL NOT NULL, fee REAL NOT NULL,
                    realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
                    slippage REAL NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES portfolio_orders(client_order_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_high_water (
                    account_id TEXT PRIMARY KEY, equity REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_ownership_adjustments (
                    adjustment_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    consumed_quantity REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS portfolio_rebalance_executions (
                    decision_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    buy_orders_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES portfolio_decisions(decision_id)
                );
                """
            )
            session_columns = {
                str(row[1])
                for row in self.store.connection.execute(
                    "PRAGMA table_info(portfolio_runtime_sessions)"
                )
            }
            if "manifest_checksum" in session_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_runtime_sessions RENAME COLUMN "
                    "manifest_checksum TO configuration_digest"
                )
            if "stage" in session_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_runtime_sessions RENAME COLUMN stage TO mode"
                )
            decision_columns = {
                str(row[1])
                for row in self.store.connection.execute("PRAGMA table_info(portfolio_decisions)")
            }
            if "manifest_checksum" in decision_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_decisions RENAME COLUMN "
                    "manifest_checksum TO configuration_digest"
                )
            if "account_id" not in decision_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_decisions ADD COLUMN account_id TEXT NOT NULL DEFAULT ''"
                )
            self._rebuild_decisions_unique_constraint()
            self.store.connection.execute(
                """UPDATE portfolio_decisions SET account_id = COALESCE((
                       SELECT s.account_id FROM portfolio_runtime_sessions s
                       WHERE s.session_id = portfolio_decisions.session_id
                   ), '') WHERE account_id = ''"""
            )
            columns = {
                str(row[1])
                for row in self.store.connection.execute("PRAGMA table_info(portfolio_orders)")
            }
            if "cumulative_filled_quantity" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN "
                    "cumulative_filled_quantity REAL NOT NULL DEFAULT 0"
                )
                # Legacy databases kept fills in portfolio_fills while the
                # orders table had no cumulative quantity. Restore the real
                # bookkeeping instead of leaving migrated orders at zero.
                self.store.connection.execute(
                    """UPDATE portfolio_orders
                       SET cumulative_filled_quantity = COALESCE((
                           SELECT SUM(f.quantity) FROM portfolio_fills f
                           WHERE f.client_order_id = portfolio_orders.client_order_id
                       ), 0)
                       WHERE cumulative_filled_quantity = 0"""
                )
            if "average_fill_price" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN average_fill_price REAL"
                )
                self.store.connection.execute(
                    """UPDATE portfolio_orders
                       SET average_fill_price = (
                           SELECT SUM(f.quantity * f.price) / NULLIF(SUM(f.quantity), 0)
                           FROM portfolio_fills f
                           WHERE f.client_order_id = portfolio_orders.client_order_id
                       )
                       WHERE average_fill_price IS NULL"""
                )
            fill_columns = {
                str(row[1])
                for row in self.store.connection.execute("PRAGMA table_info(portfolio_fills)")
            }
            # Execution-quality capture (telemetry, REPORT ONLY): submission ->
            # fill delay per fill. Nullable; legacy rows stay valid.
            if "fill_delay_seconds" not in fill_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_fills ADD COLUMN fill_delay_seconds REAL"
                )
                self.store.connection.execute(
                    """UPDATE portfolio_fills
                       SET fill_delay_seconds = (
                           SELECT CASE
                               WHEN o.created_at IS NOT NULL AND
                                    julianday(portfolio_fills.occurred_at) >=
                                    julianday(o.created_at)
                               THEN (julianday(portfolio_fills.occurred_at)
                                     - julianday(o.created_at)) * 86400.0
                               ELSE NULL
                           END
                           FROM portfolio_orders o
                           WHERE o.client_order_id = portfolio_fills.client_order_id
                       )
                       WHERE fill_delay_seconds IS NULL"""
                )
            self._ensure_hot_path_indexes()

    def _ensure_hot_path_indexes(self) -> None:
        """Additive, backward-compatible hot-path indexes (idempotent).

        Created last so legacy databases (which gain ``account_id`` columns
        and a rebuilt ``portfolio_decisions`` table earlier in ``_migrate``)
        end up with the same final schema as fresh ones. All consumers are
        read-only dashboard/report queries; index maintenance is amortized
        against the existing per-transaction writes.
        """
        self.store.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_runtime_sessions_account
                ON portfolio_runtime_sessions(account_id);
            CREATE INDEX IF NOT EXISTS idx_portfolio_decisions_account_ts
                ON portfolio_decisions(account_id, decision_timestamp);
            CREATE INDEX IF NOT EXISTS idx_portfolio_decisions_session_id
                ON portfolio_decisions(session_id);
            CREATE INDEX IF NOT EXISTS idx_portfolio_orders_decision_id
                ON portfolio_orders(decision_id);
            CREATE INDEX IF NOT EXISTS idx_portfolio_fills_client_order_id
                ON portfolio_fills(client_order_id);
            CREATE INDEX IF NOT EXISTS idx_portfolio_ownership_adjustments_account
                ON portfolio_ownership_adjustments(account_id, consumed_quantity);
            """
        )

    def _rebuild_decisions_unique_constraint(self) -> None:
        """Replace the pre-account UNIQUE(configuration_digest, timestamp, market)
        with UNIQUE(account_id, configuration_digest, timestamp, market).

        SQLite cannot drop a UNIQUE constraint in place, so the table is
        rebuilt (the documented foreign-key-preserving recipe) whenever the
        legacy definition is detected. Fresh databases already carry the new
        constraint and skip the rebuild.
        """
        row = self.store.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'portfolio_decisions'"
        ).fetchone()
        if row is None:
            return
        create_sql = str(row[0])
        if "UNIQUE(account_id, configuration_digest, decision_timestamp, market)" in create_sql:
            return
        self.store.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            # An interrupted previous rebuild must not brick startup: the
            # staging table is dropped and recreated idempotently.
            self.store.connection.execute("DROP TABLE IF EXISTS portfolio_decisions_account_scoped")
            self.store.connection.execute(
                """
                CREATE TABLE portfolio_decisions_account_scoped (
                    decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '', configuration_digest TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL, market TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    target_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                    UNIQUE(account_id, configuration_digest, decision_timestamp, market),
                    FOREIGN KEY(session_id) REFERENCES portfolio_runtime_sessions(session_id)
                )
                """
            )
            self.store.connection.execute(
                """
                INSERT INTO portfolio_decisions_account_scoped(
                    decision_id, session_id, account_id, configuration_digest,
                    decision_timestamp, market, strategy_name, target_json, risk_json
                )
                SELECT decision_id, session_id, account_id, configuration_digest,
                       decision_timestamp, market, strategy_name, target_json, risk_json
                FROM portfolio_decisions
                """
            )
            self.store.connection.execute("DROP TABLE portfolio_decisions")
            self.store.connection.execute(
                "ALTER TABLE portfolio_decisions_account_scoped RENAME TO portfolio_decisions"
            )
            # The INSERT above opened a transaction; commit before restoring
            # foreign keys, otherwise the pragma would be a silent no-op.
            self.store.connection.commit()
        finally:
            self.store.connection.execute("PRAGMA foreign_keys=ON")

    def begin_session(self, account_id: str, configuration_digest: str, mode: str) -> str:
        session_id = uuid4().hex
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT INTO portfolio_runtime_sessions(
                    session_id, account_id, configuration_digest, mode, started_at, status
                ) VALUES (?, ?, ?, ?, ?, 'running')""",
                (
                    session_id,
                    account_id,
                    configuration_digest,
                    mode,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return session_id

    def end_session(self, session_id: str, status: str) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_runtime_sessions
                   SET ended_at = ?, status = ? WHERE session_id = ?""",
                (datetime.now(UTC).isoformat(), status, session_id),
            )

    def has_decision(
        self, account_id: str, configuration_digest: str, timestamp: datetime, market: Market
    ) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT 1 FROM portfolio_decisions
                   WHERE account_id = ? AND configuration_digest = ?
                     AND decision_timestamp = ? AND market = ?""",
                (account_id, configuration_digest, timestamp.isoformat(), market.value),
            ).fetchone()
        return row is not None

    def latest_decision_timestamp(
        self, account_id: str, configuration_digest: str, market: Market
    ) -> datetime | None:
        """Return the last persisted decision boundary for restart-safe scheduling."""
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT MAX(decision_timestamp) FROM portfolio_decisions
                   WHERE account_id = ? AND configuration_digest = ? AND market = ?""",
                (account_id, configuration_digest, market.value),
            ).fetchone()
        return datetime.fromisoformat(str(row[0])) if row and row[0] is not None else None

    def record_decision(
        self,
        session_id: str,
        account_id: str,
        configuration_digest: str,
        target: TargetPortfolio,
        risk: RiskOverlayDecision,
        market: Market,
    ) -> str:
        decision_id = _decision_id(
            account_id, configuration_digest, target.decision_timestamp, market
        )
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_decisions(
                    decision_id, session_id, account_id, configuration_digest,
                    decision_timestamp, market, strategy_name, target_json, risk_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    session_id,
                    account_id,
                    configuration_digest,
                    target.decision_timestamp.isoformat(),
                    market.value,
                    target.strategy_name,
                    json.dumps(target.to_dict(), sort_keys=True, default=str),
                    json.dumps(asdict(risk), sort_keys=True, default=str),
                ),
            )
        return decision_id

    def record_order(self, order: PortfolioOrderRequest, status: str) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_orders(
                    client_order_id, decision_id, symbol, market, side, quantity,
                    reference_price, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.client_order_id,
                    order.decision_id,
                    order.symbol,
                    order.market.value,
                    order.side.value,
                    order.quantity,
                    order.reference_price,
                    status,
                    datetime.now(UTC).isoformat(),
                ),
            )

    # -- two-phase rebalance execution lifecycle ----------------------------

    def save_rebalance_execution(
        self,
        decision_id: str,
        account_id: str,
        market: Market,
        phase: str,
        *,
        buy_orders_json: str = "[]",
        status: str = "active",
    ) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT INTO portfolio_rebalance_executions(
                    decision_id, account_id, market, phase, status, buy_orders_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    phase = excluded.phase,
                    status = excluded.status,
                    buy_orders_json = excluded.buy_orders_json,
                    updated_at = excluded.updated_at""",
                (
                    decision_id,
                    account_id,
                    market.value,
                    phase,
                    status,
                    buy_orders_json,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def update_rebalance_execution(
        self,
        decision_id: str,
        phase: str,
        *,
        status: str | None = None,
    ) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_rebalance_executions
                   SET phase = ?, status = COALESCE(?, status), updated_at = ?
                   WHERE decision_id = ?""",
                (phase, status, datetime.now(UTC).isoformat(), decision_id),
            )

    def current_rebalance_execution(
        self, account_id: str, market: Market
    ) -> RebalanceExecutionState | None:
        """The newest persisted rebalance execution for one account/market.

        Includes completed and blocked executions so callers can distinguish
        "no two-phase rebalance has ever run" from "the last one is done".
        """
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT decision_id, account_id, market, phase, status, updated_at
                   FROM portfolio_rebalance_executions
                   WHERE account_id = ? AND market = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (account_id, market.value),
            ).fetchall()
        if not rows:
            return None
        row = rows[0]
        return RebalanceExecutionState(
            decision_id=str(row[0]),
            account_id=str(row[1]),
            market=Market(str(row[2])),
            phase=str(row[3]),
            status=str(row[4]),
            updated_at=datetime.fromisoformat(str(row[5])),
        )

    def decision_row(self, decision_id: str) -> dict[str, object] | None:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT target_json, risk_json, market, decision_timestamp
                   FROM portfolio_decisions WHERE decision_id = ?""",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "target_json": str(row[0]),
            "risk_json": str(row[1]),
            "market": str(row[2]),
            "decision_timestamp": str(row[3]),
        }

    def rebalance_buy_orders(self, decision_id: str) -> tuple[dict[str, object], ...]:
        """The BUY orders planned at decision time (deferred for the SELL phase).

        Used as a price fallback when restart recovery runs before warmup has
        repopulated history: the deferred BUY side must still be re-plannable.
        """
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT buy_orders_json FROM portfolio_rebalance_executions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        if row is None or not row[0]:
            return ()
        try:
            parsed = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return ()
        return tuple(dict(item) for item in parsed if isinstance(item, dict))

    def orders_for_decision(
        self, decision_id: str, side: str | None = None
    ) -> tuple[dict[str, object], ...]:
        """Order rows attributed to one decision (used by rebalance recovery)."""
        query = """SELECT client_order_id, symbol, market, side, quantity,
                          status, cumulative_filled_quantity
                   FROM portfolio_orders WHERE decision_id = ?"""
        params: tuple[object, ...] = (decision_id,)
        if side is not None:
            query += " AND side = ?"
            params = (decision_id, side)
        query += " ORDER BY created_at"
        with self.store.lock:
            rows = self.store.connection.execute(query, params).fetchall()
        return tuple(
            {
                "client_order_id": str(row[0]),
                "symbol": str(row[1]),
                "market": str(row[2]),
                "side": str(row[3]),
                "quantity": float(row[4] or 0.0),
                "status": str(row[5]),
                "cumulative_filled_quantity": float(row[6] or 0.0),
            }
            for row in rows
        )

    def update_order(self, client_order_id: str, status: str, broker_order_id: str = "") -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_orders SET status = ?, broker_order_id = ?
                   WHERE client_order_id = ?""",
                (status, broker_order_id, client_order_id),
            )

    def record_protective_order(
        self,
        account_id: str,
        client_order_id: str,
        symbol: str,
        market: Market,
        quantity: float,
        reference_price: float,
    ) -> None:
        """Record a protective/emergency stop for a portfolio-owned position.

        The stop is inserted as a SELL leg under the owning entry's decision,
        so a later fill flows through ``apply_trade_update`` and decrements
        the net owned quantity exactly like a planned exit. Without this, a
        stop fill would leave the repository claiming ownership of a flat
        broker position forever. ``reference_price`` is the stop trigger price
        so slippage is measured against the stop, not the entry.
        """
        with self.store.lock, self.store.connection:
            lineage = self._latest_entry_lineage(account_id, symbol, market)
            if lineage is None:
                # No portfolio entry lineage: booking a SELL leg would violate
                # the decision foreign key and could grant managed ownership
                # without a matching buy. The stop itself is already accepted
                # broker-side; ownership release for it is a reconcile concern.
                log.error(
                    "Protective stop for %s has no portfolio entry lineage; "
                    "ownership bookkeeping skipped",
                    symbol,
                )
                return
            decision_id, _entry_reference = lineage
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_orders(
                    client_order_id, decision_id, symbol, market, side, quantity,
                    reference_price, status, created_at
                ) VALUES (?, ?, ?, ?, 'sell', ?, ?, 'submitted', ?)""",
                (
                    client_order_id,
                    decision_id,
                    symbol,
                    market.value,
                    quantity,
                    reference_price,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def clear_stale_ownership(self, account_id: str, symbol: str, market: Market) -> float:
        """Record an explicit ownership reconciliation adjustment when the
        broker holds no position and no real fill could be recovered.

        This never books a synthetic precise SELL fill at an invented price:
        an unknown exit is an anomaly, not a normal trading fill. The
        adjustment reduces owned quantity and can be consumed later by a real
        fill arriving after reconciliation (stream downtime race). Idempotent:
        returns the adjusted quantity (0.0 when nothing is owned anymore)."""
        owned = self.owned_quantities(account_id, market).get(symbol, 0.0)
        if owned <= 1e-12:
            return 0.0
        self.record_ownership_adjustment(
            account_id,
            symbol,
            market,
            owned,
            kind="stale_ownership_clear",
            note="broker position absent and no real fill found in activity history",
        )
        log.warning(
            "Recorded stale-ownership adjustment: account=%s symbol=%s qty=%s "
            "(no broker position found)",
            account_id,
            symbol,
            owned,
        )
        return owned

    def record_ownership_adjustment(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        quantity: float,
        *,
        kind: str,
        note: str = "",
    ) -> None:
        """Persist an explicit ownership reconciliation adjustment/anomaly."""
        if not kind.strip():
            raise ValueError("Adjustment kind is required")
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("Adjustment quantity must be finite and positive")
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT INTO portfolio_ownership_adjustments(
                    adjustment_id, account_id, symbol, market, quantity, kind,
                    note, created_at, consumed_quantity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    uuid4().hex,
                    account_id,
                    symbol,
                    market.value,
                    quantity,
                    kind,
                    note,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _consume_ownership_adjustments_tx(
        self, account_id: str, symbol: str, market: Market, sell_delta: float
    ) -> None:
        """Consume pending stale-clear adjustments up to a real SELL fill.

        The race: broker stop fills while the stream is delayed; reconcile
        sees a flat broker and records an adjustment; the real fill arrives
        late. The real fill is the ground truth, so it cancels the provisional
        adjustment instead of booking a second SELL on top of it."""
        if sell_delta <= 0:
            return
        remaining = float(sell_delta)
        rows = self.store.connection.execute(
            """SELECT adjustment_id, quantity, consumed_quantity
               FROM portfolio_ownership_adjustments
               WHERE account_id = ? AND symbol = ? AND market = ? AND kind = 'stale_ownership_clear'
                 AND consumed_quantity < quantity
               ORDER BY created_at""",
            (account_id, symbol, market.value),
        ).fetchall()
        for adjustment_id, quantity, consumed in rows:
            if remaining <= 1e-12:
                break
            capacity = float(quantity) - float(consumed)
            consume = min(capacity, remaining)
            self.store.connection.execute(
                """UPDATE portfolio_ownership_adjustments
                   SET consumed_quantity = consumed_quantity + ?
                   WHERE adjustment_id = ?""",
                (consume, str(adjustment_id)),
            )
            remaining -= consume

    def _latest_entry_lineage(
        self, account_id: str, symbol: str, market: Market
    ) -> tuple[str, float] | None:
        """Decision id and reference price of the most recent BUY entry row."""
        row = self.store.connection.execute(
            """
            SELECT o.decision_id, o.reference_price FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE o.symbol = ? AND o.market = ? AND o.side = 'buy'
              AND s.account_id = ?
            ORDER BY o.created_at DESC LIMIT 1
            """,
            (symbol, market.value, account_id),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), float(row[1] or 0.0)

    def has_order(self, client_order_id: str) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT 1 FROM portfolio_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return row is not None

    def open_orders(
        self, account_id: str = "unknown", market: Market | None = None
    ) -> tuple[OpenPortfolioOrder, ...]:
        """Non-terminal portfolio orders attributed to one account.

        Orders are attributed through the persisted decision/session lineage,
        so manual/external broker orders can never appear here: a manual
        order must not suppress ownership drift or consume universe pins.
        """
        query = f"""
            SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                   o.cumulative_filled_quantity
            FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ? AND o.status NOT IN ({TERMINAL_ORDER_STATUSES_SQL})
        """
        params: tuple[object, ...] = (account_id,)
        if market is not None:
            query += " AND o.market = ?"
            params = (account_id, market.value)
        query += " ORDER BY o.created_at"
        with self.store.lock:
            rows = self.store.connection.execute(query, params).fetchall()
        return tuple(
            OpenPortfolioOrder(
                client_order_id=str(row[0]),
                symbol=str(row[1]),
                market=Market(str(row[2])),
                side=str(row[3]),
                quantity=float(row[4] or 0.0),
                cumulative_filled_quantity=float(row[5] or 0.0),
            )
            for row in rows
        )

    def apply_trade_update(
        self,
        *,
        client_order_id: str,
        broker_order_id: str,
        status: str,
        cumulative_filled_quantity: float,
        average_fill_price: float | None,
        occurred_at: datetime,
        activity_id: str,
        fee: float,
        realized_pnl: float,
        unrealized_pnl: float,
    ) -> bool:
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                """SELECT o.cumulative_filled_quantity, o.reference_price, o.side,
                          s.account_id, o.created_at
                   FROM portfolio_orders o
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE o.client_order_id = ?""",
                (client_order_id,),
            ).fetchone()
            if row is None:
                return False
            previous = float(row[0] or 0.0)
            fill_delta = max(0.0, cumulative_filled_quantity - previous)
            reference = float(row[1] or 0.0)
            side = str(row[2])
            account_id = str(row[3])
            created_at = str(row[4]) if row[4] is not None else None
            delay_seconds: float | None = None
            if created_at:
                try:
                    raw_delay = (occurred_at - datetime.fromisoformat(created_at)).total_seconds()
                    if raw_delay >= 0:
                        delay_seconds = raw_delay
                except (TypeError, ValueError):
                    delay_seconds = None
            self.store.connection.execute(
                """UPDATE portfolio_orders SET status = ?, broker_order_id = ?,
                   cumulative_filled_quantity = ?, average_fill_price = ?
                   WHERE client_order_id = ?""",
                (
                    status,
                    broker_order_id,
                    max(previous, cumulative_filled_quantity),
                    average_fill_price,
                    client_order_id,
                ),
            )
            if fill_delta > 0 and average_fill_price is not None:
                direction = 1.0 if side == "buy" else -1.0
                slippage = (average_fill_price - reference) * fill_delta * direction
                self.store.connection.execute(
                    """INSERT OR IGNORE INTO portfolio_fills(
                        activity_id, client_order_id, occurred_at, quantity, price,
                        fee, realized_pnl, unrealized_pnl, slippage, fill_delay_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        activity_id,
                        client_order_id,
                        occurred_at.isoformat(),
                        fill_delta,
                        average_fill_price,
                        fee,
                        realized_pnl,
                        unrealized_pnl,
                        slippage,
                        delay_seconds,
                    ),
                )
                if side == "sell":
                    # A real SELL fill proves the sale that a stale-clear
                    # adjustment assumed; consume the adjustment instead of
                    # booking a second SELL on top of it.
                    symbol = self.store.connection.execute(
                        "SELECT symbol, market FROM portfolio_orders WHERE client_order_id = ?",
                        (client_order_id,),
                    ).fetchone()
                    if symbol is not None:
                        self._consume_ownership_adjustments_tx(
                            account_id,
                            str(symbol[0]),
                            Market(str(symbol[1])),
                            fill_delta,
                        )
        return True

    def update_high_water(self, account_id: str, equity: float) -> float:
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                "SELECT equity FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            high_water = max(float(row[0]) if row else 0.0, equity)
            self.store.connection.execute(
                """INSERT INTO portfolio_high_water(account_id, equity, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     equity = excluded.equity, updated_at = excluded.updated_at""",
                (account_id, high_water, datetime.now(UTC).isoformat()),
            )
        return high_water

    def high_water(self, account_id: str) -> float:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT equity FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return float(row[0]) if row is not None else 0.0

    def reset_high_water(self, account_id: str, equity: float) -> None:
        """Rebase the persisted portfolio high-water to ``equity`` (a new
        risk epoch started by the operator after a verified drawdown
        incident). Unlike ``update_high_water`` this REPLACES the baseline
        instead of taking the max, so decisions resume measuring drawdown
        from the reset-time equity."""
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT INTO portfolio_high_water(account_id, equity, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     equity = excluded.equity, updated_at = excluded.updated_at""",
                (account_id, equity, datetime.now(UTC).isoformat()),
            )

    def owned_quantities(
        self, account_id: str = "unknown", market: Market | None = None
    ) -> dict[str, float]:
        """Net filled quantity per symbol for one account.

        Orders are attributed to an account through the persisted
        decision/session lineage, so a stale paper session can never mark
        symbols as owned for a different (live) account.
        """
        query = """
            SELECT o.symbol, o.market, o.side, o.cumulative_filled_quantity
            FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ?
        """
        params: tuple[object, ...] = (account_id,)
        if market is not None:
            query += " AND o.market = ?"
            params = (account_id, market.value)
        with self.store.lock:
            rows = self.store.connection.execute(query, params).fetchall()
            adjustments = self.store.connection.execute(
                """SELECT symbol, SUM(quantity - consumed_quantity)
                   FROM portfolio_ownership_adjustments
                   WHERE account_id = ? AND consumed_quantity < quantity
                   GROUP BY symbol""",
                (account_id,),
            ).fetchall()
        net: dict[str, float] = {}
        for symbol, _market, side, filled in rows:
            filled_qty = float(filled or 0.0)
            if filled_qty <= 0:
                continue
            net[str(symbol)] = net.get(str(symbol), 0.0) + (
                filled_qty if str(side) == "buy" else -filled_qty
            )
        for symbol, adjusted in adjustments:
            net[str(symbol)] = net.get(str(symbol), 0.0) - float(adjusted or 0.0)
        # Negative net ownership is an invariant violation, never something to
        # silently filter out: it means more quantity left the account than
        # was ever booked as owned (double-sell, misattributed fill, or an
        # adjustment that a real fill later contradicted).
        negative = {symbol: quantity for symbol, quantity in net.items() if quantity < -1e-9}
        if negative:
            raise RuntimeError(
                "Negative net portfolio ownership (invariant violation): "
                + ", ".join(
                    f"{symbol}={quantity:.6f}" for symbol, quantity in sorted(negative.items())
                )
            )
        return {symbol: quantity for symbol, quantity in net.items() if quantity > 1e-12}

    def owned_symbols(self, account_id: str = "unknown", market: Market | None = None) -> set[str]:
        """Symbols the portfolio path still owns: net filled quantity > 0.

        Ownership is computed from cumulative fills (buy minus sell) so a
        partially or fully exited position loses ``managed`` status exactly
        when its broker quantity reaches zero. This is the authoritative
        portfolio-side input to the broker's unified managed-position view.
        """
        return set(self.owned_quantities(account_id, market))

    def ownership_adjustments(self, account_id: str = "unknown") -> tuple[dict[str, object], ...]:
        """Outstanding ownership reconciliation adjustments/anomalies."""
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT adjustment_id, symbol, market, quantity, consumed_quantity,
                          kind, note, created_at
                   FROM portfolio_ownership_adjustments
                   WHERE account_id = ? AND consumed_quantity < quantity
                   ORDER BY created_at""",
                (account_id,),
            ).fetchall()
        return tuple(
            {
                "adjustment_id": str(row[0]),
                "symbol": str(row[1]),
                "market": str(row[2]),
                "quantity": float(row[3]),
                "consumed_quantity": float(row[4]),
                "outstanding_quantity": float(row[3]) - float(row[4]),
                "kind": str(row[5]),
                "note": str(row[6]),
                "created_at": str(row[7]),
            }
            for row in rows
        )

    def operations_snapshot(self, account_id: str) -> dict[str, object]:
        """Operator-facing snapshot scoped to one account.

        Every section (last decision, pending orders, recent fills) is
        attributed through the persisted fills -> orders -> decisions ->
        sessions -> account lineage, so switching credentials can never show
        another account's fills or orders.
        """
        with self.store.lock:
            last_decision = self.store.connection.execute(
                """SELECT decision_timestamp, strategy_name, market, target_json, risk_json
                   FROM portfolio_decisions
                   WHERE account_id = ?
                   ORDER BY decision_timestamp DESC LIMIT 1""",
                (account_id,),
            ).fetchone()
            pending = self.store.connection.execute(
                f"""SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                          o.status, o.created_at
                   FROM portfolio_orders o
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE s.account_id = ?
                     AND o.status NOT IN ({TERMINAL_ORDER_STATUSES_SQL})
                   ORDER BY o.created_at DESC LIMIT 25""",
                (account_id,),
            ).fetchall()
            fills = self.store.connection.execute(
                """SELECT f.activity_id, f.client_order_id, f.occurred_at, f.quantity,
                          f.price, f.fee, f.realized_pnl, f.unrealized_pnl, f.slippage
                   FROM portfolio_fills f
                   JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE s.account_id = ?
                   ORDER BY f.occurred_at DESC LIMIT 25""",
                (account_id,),
            ).fetchall()
        return {
            "last_decision": (
                {
                    "timestamp": str(last_decision[0]),
                    "strategy": str(last_decision[1]),
                    "market": str(last_decision[2]),
                    "target": json.loads(str(last_decision[3])),
                    "risk": json.loads(str(last_decision[4])),
                }
                if last_decision is not None
                else None
            ),
            "pending_orders": [
                dict(
                    zip(
                        (
                            "client_order_id",
                            "symbol",
                            "market",
                            "side",
                            "quantity",
                            "status",
                            "created_at",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in pending
            ],
            "recent_fills": [
                dict(
                    zip(
                        (
                            "activity_id",
                            "client_order_id",
                            "occurred_at",
                            "quantity",
                            "price",
                            "fee",
                            "realized_pnl",
                            "unrealized_pnl",
                            "slippage",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in fills
            ],
            "reconciliation_anomalies": list(self.ownership_adjustments(account_id)),
        }

    def close(self) -> None:
        if self._owns_store:
            self.store.acquire()
            self.store.release()


class PortfolioRuntime:
    def __init__(
        self,
        strategy: PortfolioStrategy,
        symbols: tuple[str, ...],
        broker: PortfolioRuntimeBroker,
        repository: PortfolioStateRepository,
        *,
        mode: str,
        maximum_allocation: float,
        risk_limits: PortfolioRiskLimits | None = None,
        universe: object | None = None,
        universe_version: str = "runtime",
        account_id: str = "unknown",
        live_dollar_cap: float = 0.0,
        execution_policy: PortfolioExecutionPolicy | None = None,
    ) -> None:
        if mode not in {"paper", "live"}:
            raise ValueError("PortfolioRuntime mode must be paper or live")
        if not symbols:
            raise ValueError("PortfolioRuntime requires an active universe")
        if not 0 < maximum_allocation <= 1:
            raise ValueError("maximum_allocation must be in (0, 1]")
        if mode == "live" and live_dollar_cap <= 0:
            raise ValueError("Live portfolio execution requires a positive hard dollar cap")
        self.strategy = strategy
        self.market = strategy.market
        self.symbols = tuple(
            dict.fromkeys(canonical_symbol(symbol, strategy.market) for symbol in symbols)
        )
        self.broker = broker
        self.repository = repository
        self.mode = mode
        self.maximum_allocation = maximum_allocation
        self.live_dollar_cap = live_dollar_cap
        self.universe = universe
        self.universe_version = universe_version
        self.account_id = account_id
        self.policy = rebalance_policy_for(self.strategy)
        self.execution_policy = execution_policy or PortfolioExecutionPolicy()
        self.decision_service = PortfolioDecisionService()
        self.risk_overlay = PortfolioRiskOverlay(risk_limits)
        self.planner = TargetToOrderPlanner(self.execution_policy)
        self.history: dict[str, list[Bar]] = {symbol: [] for symbol in self.symbols}
        self.configuration_digest = configuration_digest(
            strategy,
            mode,
            maximum_allocation,
            universe_version,
            self.risk_overlay.limits,
            self.execution_policy,
        )
        self.previous_timestamp = repository.latest_decision_timestamp(
            account_id, self.configuration_digest, self.market
        )
        self.session_id = repository.begin_session(account_id, self.configuration_digest, mode)
        # Startup catch-up observability: last attempt status and when it was
        # attempted (UTC). Set on every startup_catch_up call, including
        # blocked/early returns; used by the engine health surface.
        self.startup_catch_up_status: str | None = None
        self.startup_catch_up_at: datetime | None = None

    def seed(self, bars: list[Bar]) -> None:
        """Seed only closed, approved bars without producing a decision.

        Forming (in-progress) bars are rejected: Alpaca's historical feed
        includes the partial bar for the current bucket, and an appended
        forming bar would block the true closed bar (same timestamp) via the
        ``series[-1].timestamp < bar.timestamp`` append rule, leaving a
        partial close in decision history indefinitely.
        """
        now = datetime.now(UTC)
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            if (
                bar.market != self.market
                or bar.timeframe != self.strategy.timeframe
                or symbol not in self.history
            ):
                continue
            if self.market == Market.CRYPTO:
                if not bar_is_closed(bar.timestamp, bar.timeframe, now):
                    continue
            elif bar.timeframe == "1d" and not equity_day_bar_is_closed(bar.timestamp, now):
                continue
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        timestamp_sets = [
            {bar.timestamp for bar in series} for series in self.history.values() if series
        ]
        if len(timestamp_sets) == len(self.history):
            common_timestamps = set.intersection(*timestamp_sets)
            if common_timestamps:
                common_latest = max(common_timestamps)
                self.previous_timestamp = max(
                    filter(None, (self.previous_timestamp, common_latest))
                )

    def startup_catch_up(self, *, entry_latch: str | None = None) -> PortfolioRuntimeResult:
        """Decide immediately on the latest completed warmup bars.

        Restores BACKTEST/PAPER parity: the backtest engine fires its initial
        decision on the first usable bar (``is_period_start(None, ts)`` is
        True), while a runtime seeded mid-period would otherwise sit in cash
        until the next cadence boundary. This forces a full-target decision on
        the latest closed bars — reusing every existing gate in
        ``process_closed_bars`` (duplicate decision, rebalance advance/phase,
        managed-batch completeness, benchmark, high-water, risk overlay,
        planner, two-phase sells-then-buys, idempotency keys) — only when the
        deployment has never decided and holds no managed position in this
        market. Never re-enters after ANY prior decision (restart safety).
        """
        self.startup_catch_up_at = datetime.now(UTC)
        series_by_symbol = dict(self.history)
        timestamps = [
            series[-1].timestamp if series else None for series in series_by_symbol.values()
        ]
        if any(timestamp is None for timestamp in timestamps) or any(
            len(series) < self.strategy.minimum_history_bars for series in series_by_symbol.values()
        ):
            self.startup_catch_up_status = "warming_up"
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if len({timestamp for timestamp in timestamps}) != 1:
            self.startup_catch_up_status = "incomplete_strategy_batch"
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        timestamp = timestamps[0]
        assert timestamp is not None
        if self.repository.has_decision(
            self.account_id, self.configuration_digest, timestamp, self.market
        ):
            self.startup_catch_up_status = "duplicate_decision"
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        if (
            self.repository.latest_decision_timestamp(
                self.account_id, self.configuration_digest, self.market
            )
            is not None
        ):
            # Any prior decision for this deployment means the cadence is
            # already running: a catch-up would be an out-of-cadence re-entry.
            self.startup_catch_up_status = "catch_up_superseded"
            return PortfolioRuntimeResult(None, None, None, (), (), "catch_up_superseded")
        account = self.broker.account()
        if any(
            position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
            for position in account.positions
        ):
            # Already invested in this market: never layer a catch-up entry on
            # top of an existing managed position.
            self.startup_catch_up_status = "already_invested"
            return PortfolioRuntimeResult(None, None, None, (), (), "already_invested")
        # Let a persisted two-phase rebalance advance first; a still-active
        # rebalance defers the catch-up (submitting fresh BUYs while an older
        # SELL is working could stack risk-increasing exposure).
        self.advance_rebalance()
        current_rebalance = self.repository.current_rebalance_execution(
            self.account_id, self.market
        )
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            self.startup_catch_up_status = "rebalance_in_progress"
            return PortfolioRuntimeResult(
                current_rebalance.decision_id, None, None, (), (), "rebalance_in_progress"
            )
        bars = [series[-1] for series in series_by_symbol.values()]
        result = self.process_closed_bars(
            bars,
            entry_latch=entry_latch,
            force_scheduled=True,
            decision_origin="startup_catch_up",
        )
        self.startup_catch_up_status = result.status
        return result

    def update_symbols(self, symbols: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Replace ephemeral members without changing the approved policy identity."""
        updated = tuple(dict.fromkeys(canonical_symbol(symbol, self.market) for symbol in symbols))
        if not updated:
            raise ValueError("PortfolioRuntime requires an active universe")
        current = set(self.symbols)
        desired = set(updated)
        added = tuple(sorted(desired - current))
        removed = tuple(sorted(current - desired))
        for symbol in added:
            self.history[symbol] = []
        for symbol in removed:
            self.history.pop(symbol, None)
        self.symbols = updated
        return added, removed

    def process_closed_bars(
        self,
        bars: list[Bar],
        *,
        entry_latch: str | None = None,
        force_scheduled: bool = False,
        decision_origin: str = "scheduled",
    ) -> PortfolioRuntimeResult:
        eligible = [
            bar
            for bar in bars
            if bar.market == self.market
            and bar.timeframe == self.strategy.timeframe
            and canonical_symbol(bar.symbol, bar.market) in self.history
        ]
        if not eligible:
            return PortfolioRuntimeResult(None, None, None, (), (), "no_eligible_bars")
        timestamp = max(bar.timestamp for bar in eligible)
        batch_symbols = {
            canonical_symbol(bar.symbol, bar.market)
            for bar in eligible
            if bar.timestamp == timestamp
        }
        for bar in sorted(eligible, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        # ``boundary`` is the real cadence; ``scheduled`` additionally honors
        # a forced catch-up decision (startup catch-up), which must apply the
        # FULL target like any boundary decision.
        boundary = self.policy.is_period_start(self.previous_timestamp, timestamp)
        scheduled = boundary or force_scheduled
        # Receiving a timed-out partial batch explicitly skips that rebalance
        # period. A later day must not turn it into a silent delayed rebalance.
        self.previous_timestamp = timestamp
        warming_symbols = tuple(
            sorted(
                symbol
                for symbol, series in self.history.items()
                if len(series) < self.strategy.minimum_history_bars
            )
        )
        missing_symbols = tuple(
            sorted(
                symbol
                for symbol, series in self.history.items()
                if not series or series[-1].timestamp != timestamp
            )
        )
        if warming_symbols:
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if missing_symbols:
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        ready_history = dict(self.history)
        if self.repository.has_decision(
            self.account_id, self.configuration_digest, timestamp, self.market
        ):
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        # A persisted two-phase rebalance may have become ready since the last
        # event (trade update, restart, or reconcile). Advance it first, then
        # defer any new decision while the previous rebalance is still in
        # flight: submitting a fresh BUY while an older SELL is working could
        # stack risk-increasing exposure on top of the not-yet-sold position.
        self.advance_rebalance()
        current_rebalance = self.repository.current_rebalance_execution(
            self.account_id, self.market
        )
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            if current_rebalance.phase == REBALANCE_PHASE_BLOCKED:
                return PortfolioRuntimeResult(
                    current_rebalance.decision_id, None, None, (), (), "rebalance_blocked"
                )
            return PortfolioRuntimeResult(
                current_rebalance.decision_id,
                None,
                None,
                (),
                (),
                "rebalance_in_progress",
            )
        account = self.broker.account()
        managed = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if position.market == self.market
            and not position.is_flat
            and position.managed_qty > 1e-12
        }
        if not managed.issubset(batch_symbols):
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_managed_batch")
        if self.market == Market.CRYPTO and "BTC/USD" not in ready_history:
            return PortfolioRuntimeResult(None, None, None, (), (), "benchmark_not_ready")
        # Non-scheduled batch fast path: when the strategy does not override
        # the intermediate-risk hook (base implementation is constant False),
        # the batch can never produce a decision, so the persisted
        # high-water update and the full context build below are pure waste.
        # The strategy-hook identity check keeps exact semantics for any
        # strategy that DOES override the hook: those keep the original path
        # (context built, hook evaluated, possible risk-reducing decision).
        if (
            not scheduled
            and type(self.strategy).requires_intermediate_risk_rebalance
            is PortfolioStrategy.requires_intermediate_risk_rebalance
        ):
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        persisted_high_water = self.repository.high_water(self.account_id)
        account = replace(
            account,
            peak_equity=max(account.peak_equity, persisted_high_water, account.equity),
        )
        self.repository.update_high_water(self.account_id, account.equity)
        context = self.decision_service.build_context(
            self.strategy,
            ready_history,
            account,
            timestamp,
            universe=self.universe,
            universe_version=self.universe_version,
        )
        if not scheduled and not self.strategy.requires_intermediate_risk_rebalance(context):
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        raw_target = self.strategy.decide(context)
        if entry_latch is not None:
            # Entry-risk latch: the strategy may propose anything, but the
            # runtime may only reduce risk. Cap every target at the current
            # weight so the planner emits SELL orders only; entries stay
            # blocked while protective/risk-reducing exits keep flowing.
            current_weights = {
                canonical_symbol(position.symbol, position.market): (
                    position.market_value / account.equity
                )
                for position in account.positions
                if position.market == self.market and not position.is_flat
            }
            raw_target = replace(
                raw_target,
                weights=tuple(
                    TargetWeight(
                        weight.symbol,
                        min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                        "entry_risk_latch_reduction",
                    )
                    for weight in raw_target.weights
                    if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
                ),
                cash_weight=1.0
                - sum(
                    min(weight.weight, current_weights.get(weight.symbol, 0.0))
                    for weight in raw_target.weights
                ),
                metadata={
                    **raw_target.metadata,
                    "entry_risk_latch": entry_latch,
                },
            )
        if not scheduled:
            current_weights = {
                canonical_symbol(position.symbol, position.market): (
                    position.market_value / account.equity
                )
                for position in account.positions
                if position.market == self.market and not position.is_flat
            }
            reducing_weights = tuple(
                TargetWeight(
                    weight.symbol,
                    min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                    "intermediate_risk_reduction_only",
                )
                for weight in raw_target.weights
                if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
            )
            raw_target = replace(
                raw_target,
                weights=reducing_weights,
                cash_weight=1.0 - sum(weight.weight for weight in reducing_weights),
                metadata={**raw_target.metadata, "intermediate_risk_reduction": True},
            )
        if force_scheduled:
            # Tag the decision origin on the FINAL target so both the normal
            # forced path and any entry-latch/reduction path carry it into the
            # persisted decision and the returned result.
            raw_target = replace(
                raw_target,
                metadata={**raw_target.metadata, "decision_origin": decision_origin},
            )
        if any(
            canonical_symbol(weight.symbol, self.market) not in self.symbols
            for weight in raw_target.weights
        ):
            raise ValueError("Strategy emitted a symbol outside the active universe")
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(
                maximum_allocation, self.live_dollar_cap / max(1.0, account.equity)
            )
        risk = self.risk_overlay.apply(
            raw_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
        )
        rules = {
            symbol: self.broker.asset_trading_rules(symbol, self.market) for symbol in self.symbols
        }
        # The order idempotency key must be scoped to the account-local
        # decision identity (account + configuration digest + timestamp +
        # market), otherwise two configurations sharing a strategy name,
        # version and timestamp collide on the same client_order_id and the
        # second decision's orders are silently dropped by the repository.
        decision_id = _decision_id(
            self.account_id, self.configuration_digest, timestamp, self.market
        )
        planned = self.planner.plan(
            risk.target,
            account,
            market=self.market,
            prices=context.prices,
            rules=rules,
            approved_symbols=self.symbols,
            open_order_symbols=self.broker.all_open_order_symbols(),
            decision_id=decision_id,
        )
        decision_id = self.repository.record_decision(
            self.session_id,
            self.account_id,
            self.configuration_digest,
            risk.target,
            risk,
            self.market,
        )
        orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
        )
        sells = tuple(order for order in orders if order.side == PortfolioOrderSide.SELL)
        buys = tuple(order for order in orders if order.side == PortfolioOrderSide.BUY)
        if not buys:
            # Pure risk-reducing decision: nothing depends on a sell outcome,
            # so the whole order set can be submitted immediately.
            submitted = self._submit_orders(orders)
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "submitted",
            )
        if sells:
            # Two-phase rebalance. Submit the risk-reducing sells now and
            # persist the dependent buys in the rebalance-execution state;
            # they are submitted only after every required sell is terminal
            # and a fresh account snapshot proves the exposure is safe.
            self.repository.save_rebalance_execution(
                decision_id,
                self.account_id,
                self.market,
                REBALANCE_PHASE_SELL_PENDING,
                buy_orders_json=json.dumps(
                    [asdict(order) for order in buys], sort_keys=True, default=str
                ),
            )
            submitted = self._submit_orders(sells)
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "sell_phase_submitted",
            )
        # Pure BUY decision with no SELL leg: there is nothing to wait for,
        # so the BUY phase begins immediately.
        submitted = self._submit_orders(buys)
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            orders,
            submitted,
            "submitted",
        )

    def advance_rebalance(self) -> PortfolioRuntimeResult | None:
        """Advance a persisted two-phase rebalance by one step.

        ``sell_pending``: while any required SELL is still working (or its
        outcome is ambiguous), the dependent BUY orders stay deferred. When
        every required SELL is terminal, a fresh broker account snapshot is
        taken, the remaining BUY side is re-planned against the safe managed
        capacity and those BUY orders are submitted exactly once. A
        rejected/canceled/expired/partially-filled SELL therefore yields the
        re-planned (smaller) BUY quantities; an ambiguous SELL outcome blocks
        the rebalance with ``reconciliation_required`` and submits nothing.

        ``buy_pending``: once every deferred BUY is terminal the rebalance is
        marked complete.

        Returns the transition result, or ``None`` when the rebalance cannot
        advance yet (or nothing is in flight). This is safe to call from a
        trade-update callback, a bar decision and process startup, and is
        restart-safe because all phase transitions are persisted.
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current is None or current.phase == REBALANCE_PHASE_COMPLETE:
            return None
        if current.phase == REBALANCE_PHASE_BLOCKED:
            return PortfolioRuntimeResult(
                current.decision_id, None, None, (), (), "rebalance_blocked"
            )
        if current.phase == REBALANCE_PHASE_BUY_READY:
            return self._submit_deferred_buys(current.decision_id)
        if current.phase == REBALANCE_PHASE_BUY_PENDING:
            buys = self.repository.orders_for_decision(current.decision_id, side="buy")
            if buys and any(str(item["status"]) == "submitting" for item in buys):
                resolved = self._resolve_stuck_submission_rows(current.decision_id, buys, "buy")
                if resolved is None:
                    return None
                buys = resolved
            if buys and all(is_terminal_order_status(str(item["status"])) for item in buys):
                self.repository.update_rebalance_execution(
                    current.decision_id, REBALANCE_PHASE_COMPLETE
                )
                return PortfolioRuntimeResult(
                    current.decision_id, None, None, (), (), "rebalance_complete"
                )
            return None
        # sell_pending: wait until every required sell is terminal.
        sells = self.repository.orders_for_decision(current.decision_id, side="sell")
        if not sells:
            # No sell rows recorded for a sell_pending rebalance: nothing can
            # ever become terminal, so re-plan the buys from the fresh state.
            self.repository.update_rebalance_execution(
                current.decision_id, REBALANCE_PHASE_BUY_READY
            )
            return self._submit_deferred_buys(current.decision_id)
        if any(str(item["status"]) == "submitting" for item in sells):
            # A crash between repository persistence and broker acknowledgement
            # leaves a row at ``submitting``. Resolve it against the broker so
            # a restart never wedges the rebalance; a broker that is
            # unreachable leaves the row for a later attempt.
            resolved = self._resolve_stuck_submission_rows(current.decision_id, sells, "sell")
            if resolved is None:
                return None
            sells = resolved
        statuses = [str(item["status"]) for item in sells]
        if any(status in _AMBIGUOUS_ORDER_STATUSES for status in statuses):
            self.repository.update_rebalance_execution(
                current.decision_id,
                REBALANCE_PHASE_BLOCKED,
                status="reconciliation_required",
            )
            return PortfolioRuntimeResult(
                current.decision_id, None, None, (), (), "rebalance_blocked"
            )
        if not all(is_terminal_order_status(status) for status in statuses):
            return None
        self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_BUY_READY)
        return self._submit_deferred_buys(current.decision_id)

    def _resolve_stuck_submission_rows(
        self, decision_id: str, orders: tuple[dict[str, object], ...], side: str
    ) -> tuple[dict[str, object], ...] | None:
        """Resolve order rows left at ``submitting`` by a crash between
        repository persistence and broker acknowledgement.

        The broker's deterministic client-order-id lookup proves whether the
        order ever reached Alpaca: an unknown id is canceled (it can never
        fill), a filled order is recovered at its real price through the
        broker's fill-recovery callback, and a working order is marked
        submitted. Returns the re-read rows, or ``None`` when the broker was
        unreachable or lacks the lookup — the caller retries without
        guessing.
        """
        resolver = getattr(self.broker, "resolve_portfolio_submission", None)
        if resolver is None:
            return None
        changed = False
        for item in orders:
            if str(item["status"]) != "submitting":
                continue
            client_order_id = str(item["client_order_id"])
            try:
                outcome = resolver(client_order_id, str(item.get("broker_order_id") or ""))
            except Exception:
                # Broker unreachable: do not guess; retry on a later advance.
                continue
            if outcome is None:
                continue
            if isinstance(outcome, tuple):
                status, broker_order_id = outcome
            else:
                status, broker_order_id = outcome, str(item.get("broker_order_id") or "")
            self.repository.update_order(client_order_id, status, broker_order_id)
            changed = True
        if not changed:
            return orders
        return self.repository.orders_for_decision(decision_id, side=side)

    def _submit_deferred_buys(self, decision_id: str) -> PortfolioRuntimeResult | None:
        """Re-plan and submit the BUY side of a completed SELL phase.

        The original decision target is re-applied against a FRESH broker
        account snapshot so the submitted BUY quantities reflect the actual
        post-sell state: a rejected or partially-filled SELL leaves more
        exposure, so the re-planned BUY is smaller (or absent) instead of
        blindly replaying the original quantity.
        """
        decision = self.repository.decision_row(decision_id)
        if decision is None:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BLOCKED)
            return None
        try:
            adjusted_target = TargetPortfolio.from_dict(
                json.loads(str(decision["risk_json"]))["target"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.error("Cannot restore rebalance target for %s: %s", decision_id, exc)
            self.repository.update_rebalance_execution(
                decision_id, REBALANCE_PHASE_BLOCKED, status="reconciliation_required"
            )
            return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_blocked")
        account = self.broker.account()
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(
                maximum_allocation, self.live_dollar_cap / max(1.0, account.equity)
            )
        risk = self.risk_overlay.apply(
            adjusted_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
        )
        prices: dict[str, float] = {
            symbol: series[-1].close for symbol, series in self.history.items() if series
        }
        for position in account.positions:
            prices.setdefault(
                canonical_symbol(position.symbol, position.market), position.current_price
            )
        # Restart recovery can run before warmup repopulated history; the
        # decision-time reference prices of the deferred BUY orders are the
        # authoritative fallback so the BUY side is never lost.
        for stored in self.repository.rebalance_buy_orders(decision_id):
            symbol = str(stored.get("symbol", ""))
            reference = stored.get("reference_price")
            if symbol and reference is not None:
                prices.setdefault(symbol, float(str(reference)))
        rules = {
            symbol: self.broker.asset_trading_rules(symbol, self.market) for symbol in self.symbols
        }
        planned = self.planner.plan(
            risk.target,
            account,
            market=self.market,
            prices=prices,
            rules=rules,
            approved_symbols=self.symbols,
            open_order_symbols=self.broker.all_open_order_symbols(),
            decision_id=decision_id,
        )
        buy_orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
            if order.side == PortfolioOrderSide.BUY
        )
        if not buy_orders:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            return PortfolioRuntimeResult(
                decision_id, risk.target, risk, (), (), "rebalance_complete"
            )
        buy_orders = self._size_buys_to_safe_capacity(buy_orders, account)
        if not buy_orders:
            log.warning(
                "Deferred BUY phase has no safe capacity; decision=%s submits nothing",
                decision_id,
            )
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            return PortfolioRuntimeResult(
                decision_id, risk.target, risk, (), (), "rebalance_complete"
            )
        submitted = self._submit_orders(buy_orders)
        self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BUY_PENDING)
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            buy_orders,
            submitted,
            "buy_phase_submitted",
        )

    def _size_buys_to_safe_capacity(
        self,
        buy_orders: tuple[PortfolioOrderRequest, ...],
        account: AccountSnapshot,
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Cap the re-planned BUY side by what the fresh account can absorb.

        A rejected/canceled/expired/partial SELL leaves the account with more
        exposure and less cash than the original decision assumed. The BUY
        phase must therefore be re-sized against three hard constraints, all
        evaluated on the FRESH broker snapshot:

        * cash: buy notional must not exceed available cash;
        * maximum gross: current broker gross + buy notional must stay within
          the configured maximum gross exposure;
        * live hard dollar cap: managed exposure after the buys must stay
          within the authorized cap.

        Orders that fall below the broker minimum after scaling are dropped;
        if nothing remains, the BUY phase submits nothing. Margin is never
        used as a risk control.
        """
        prices: dict[str, float] = {order.symbol: order.reference_price for order in buy_orders}
        for position in account.positions:
            prices.setdefault(
                canonical_symbol(position.symbol, position.market), position.current_price
            )
        equity = max(1e-9, account.equity)
        current_gross = sum(
            position.market_value for position in account.positions if not position.is_flat
        )
        managed_value = sum(
            abs(position.managed_qty) * position.current_price
            for position in account.positions
            if not position.is_flat and position.managed_qty > 1e-12
        )
        limits = self.risk_overlay.limits
        gross_capacity = max(0.0, limits.maximum_gross * equity - current_gross)
        cash_capacity = max(0.0, account.cash)
        if self.mode == "live":
            cash_capacity = min(cash_capacity, max(0.0, self.live_dollar_cap - managed_value))
        capacity = min(gross_capacity, cash_capacity)
        notional = sum(
            order.quantity * prices.get(order.symbol, order.reference_price) for order in buy_orders
        )
        if notional <= 0:
            return ()
        if notional > capacity:
            scale = capacity / notional
            if scale <= 0:
                return ()
            buy_orders = tuple(
                PortfolioOrderRequest(
                    **{
                        **asdict(order),
                        "quantity": _floor_increment(
                            order.quantity * scale,
                            self.broker.asset_trading_rules(
                                order.symbol, self.market
                            ).quantity_increment,
                        ),
                    }
                )
                for order in buy_orders
            )
        sized: list[PortfolioOrderRequest] = []
        for order in buy_orders:
            rule = self.broker.asset_trading_rules(order.symbol, self.market)
            price = prices.get(order.symbol, order.reference_price)
            if order.quantity < rule.minimum_order_size:
                continue
            if order.quantity * price < rule.minimum_order_notional:
                continue
            sized.append(order)
        return tuple(sized)

    def _submit_orders(self, orders: tuple[PortfolioOrderRequest, ...]) -> tuple[str, ...]:
        """Persist and submit one order batch with broker idempotency keys.

        The client order id is deterministic per decision/symbol/side, so a
        crash between persistence and broker acknowledgement cannot produce a
        duplicate broker order (Alpaca rejects a reused client id and the
        broker's ambiguity recovery resolves the existing order).
        """
        submitted: list[str] = []
        for order in orders:
            self.repository.record_order(order, "submitting")
            try:
                broker_order_id = self.broker.submit_portfolio_order(order)
            except Exception:
                self.repository.update_order(order.client_order_id, "reconciliation_required")
                raise
            self.repository.update_order(order.client_order_id, "submitted", broker_order_id)
            submitted.append(broker_order_id)
        return tuple(submitted)

    def close(self, status: str = "stopped") -> None:
        self.repository.end_session(self.session_id, status)


def _decision_id(account_id: str, checksum: str, timestamp: datetime, market: Market) -> str:
    import hashlib

    return hashlib.sha256(
        f"{account_id}|{checksum}|{timestamp.isoformat()}|{market.value}".encode()
    ).hexdigest()[:24]


def configuration_digest(
    strategy: PortfolioStrategy,
    mode: str,
    maximum_allocation: float,
    universe_version: str,
    risk_limits: PortfolioRiskLimits | None = None,
    execution_policy: PortfolioExecutionPolicy | None = None,
) -> str:
    import hashlib

    payload = json.dumps(
        {
            "strategy": strategy.name,
            "version": strategy.version,
            "parameters": strategy.parameter_tuple(),
            "mode": mode,
            "maximum_allocation": maximum_allocation,
            "scanner_policy_digest": universe_version,
            "risk_limits": asdict(risk_limits or PortfolioRiskLimits()),
            "execution_policy": asdict(execution_policy or PortfolioExecutionPolicy()),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "configuration_digest",
    "PortfolioRuntime",
    "PortfolioRuntimeBroker",
    "PortfolioRuntimeResult",
    "PortfolioDecisionService",
    "PortfolioStateRepository",
    "RebalanceExecutionState",
    "REBALANCE_PHASE_SELL_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_BLOCKED",
]
