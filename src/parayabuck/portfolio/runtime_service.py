"""Production portfolio decision runtime shared by paper and live execution."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.storage import SQLiteStore

log = get_logger("parayabuck.portfolio.runtime_service")


class PortfolioRuntimeBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules: ...

    def open_portfolio_order_symbols(self) -> set[str]: ...

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
            if "average_fill_price" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN average_fill_price REAL"
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
        """Book a filled SELL leg for the owned quantity when the broker holds
        no position: a missed stop fill (stream downtime, manual sell) leaves
        the repository claiming a managed position that no longer exists, and
        nothing else ever zeroes it. Idempotent: returns the cleared quantity
        (0.0 when nothing is owned anymore)."""
        owned = self.owned_quantities(account_id, market).get(symbol, 0.0)
        if owned <= 1e-12:
            return 0.0
        with self.store.lock, self.store.connection:
            lineage = self._latest_entry_lineage(account_id, symbol, market)
            if lineage is None:
                return 0.0
            decision_id, reference_price = lineage
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_orders(
                    client_order_id, decision_id, symbol, market, side, quantity,
                    reference_price, status, cumulative_filled_quantity, created_at
                ) VALUES (?, ?, ?, ?, 'sell', ?, ?, 'filled', ?, ?)""",
                (
                    f"pbp-reconcile-{uuid4().hex}",
                    decision_id,
                    symbol,
                    market.value,
                    owned,
                    reference_price,
                    owned,
                    datetime.now(UTC).isoformat(),
                ),
            )
        log.warning(
            "Cleared stale portfolio ownership: account=%s symbol=%s qty=%s "
            "(no broker position found)",
            account_id,
            symbol,
            owned,
        )
        return owned

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
                """SELECT cumulative_filled_quantity, reference_price, side
                   FROM portfolio_orders WHERE client_order_id = ?""",
                (client_order_id,),
            ).fetchone()
            if row is None:
                return False
            previous = float(row[0] or 0.0)
            fill_delta = max(0.0, cumulative_filled_quantity - previous)
            reference = float(row[1] or 0.0)
            side = str(row[2])
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
                        fee, realized_pnl, unrealized_pnl, slippage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    ),
                )
        return True

    def record_fill(
        self,
        *,
        activity_id: str,
        client_order_id: str,
        occurred_at: datetime,
        quantity: float,
        price: float,
        fee: float,
        realized_pnl: float,
        unrealized_pnl: float,
        slippage: float,
    ) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_fills(
                    activity_id, client_order_id, occurred_at, quantity, price,
                    fee, realized_pnl, unrealized_pnl, slippage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    activity_id,
                    client_order_id,
                    occurred_at.isoformat(),
                    quantity,
                    price,
                    fee,
                    realized_pnl,
                    unrealized_pnl,
                    slippage,
                ),
            )

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

    def managed_symbols(self) -> set[str]:
        """Symbols with portfolio-order lineage, used to pin broker positions."""
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT DISTINCT symbol FROM portfolio_orders"
            ).fetchall()
        return {str(row[0]) for row in rows}

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
        net: dict[str, float] = {}
        for symbol, _market, side, filled in rows:
            filled_qty = float(filled or 0.0)
            if filled_qty <= 0:
                continue
            net[str(symbol)] = net.get(str(symbol), 0.0) + (
                filled_qty if str(side) == "buy" else -filled_qty
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

    def operations_snapshot(self, account_id: str | None = None) -> dict[str, object]:
        with self.store.lock:
            if account_id is None:
                last_decision = self.store.connection.execute(
                    """SELECT decision_timestamp, strategy_name, market, target_json, risk_json
                       FROM portfolio_decisions ORDER BY decision_timestamp DESC LIMIT 1"""
                ).fetchone()
            else:
                last_decision = self.store.connection.execute(
                    """SELECT decision_timestamp, strategy_name, market, target_json, risk_json
                       FROM portfolio_decisions
                       WHERE account_id = ?
                       ORDER BY decision_timestamp DESC LIMIT 1""",
                    (account_id,),
                ).fetchone()
            pending = self.store.connection.execute(
                """SELECT client_order_id, symbol, market, side, quantity, status, created_at
                   FROM portfolio_orders
                   WHERE status NOT IN ('filled', 'canceled', 'cancelled', 'rejected', 'expired')
                   ORDER BY created_at DESC LIMIT 25"""
            ).fetchall()
            fills = self.store.connection.execute(
                """SELECT activity_id, client_order_id, occurred_at, quantity, price, fee,
                          realized_pnl, unrealized_pnl, slippage
                   FROM portfolio_fills ORDER BY occurred_at DESC LIMIT 25"""
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
        self.decision_service = PortfolioDecisionService()
        self.risk_overlay = PortfolioRiskOverlay(risk_limits)
        self.planner = TargetToOrderPlanner()
        self.history: dict[str, list[Bar]] = {symbol: [] for symbol in self.symbols}
        self.configuration_digest = configuration_digest(
            strategy,
            mode,
            maximum_allocation,
            universe_version,
            self.risk_overlay.limits,
        )
        self.previous_timestamp = repository.latest_decision_timestamp(
            account_id, self.configuration_digest, self.market
        )
        self.session_id = repository.begin_session(account_id, self.configuration_digest, mode)

    def seed(self, bars: list[Bar]) -> None:
        """Seed only closed, approved bars without producing a decision."""
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            if (
                bar.market != self.market
                or bar.timeframe != self.strategy.timeframe
                or symbol not in self.history
            ):
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

    def process_closed_bars(self, bars: list[Bar]) -> PortfolioRuntimeResult:
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
        scheduled = self.policy.is_period_start(self.previous_timestamp, timestamp)
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
            open_order_symbols=self.broker.open_portfolio_order_symbols(),
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
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            orders,
            tuple(submitted),
            "submitted",
        )

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
]
