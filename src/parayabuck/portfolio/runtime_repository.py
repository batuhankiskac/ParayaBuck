"""PortfolioStateRepository — persistence / DB.

Extracted from runtime_service.py to isolate DB concerns. Knows nothing about
strategy decisions, risk policy, or broker APIs.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
    _client_order_id,
)
from parayabuck.logger import get_logger
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.ownership_rebase import (
    ADJUST,
    BUY,
    FOREIGN_SELL_KIND,
    SELL,
    SPLIT,
    SPLIT_ADJUST,
    SPLIT_REBASE_KIND,
    classify_share_change,
    open_position_cost_bases,
    open_position_lots,
)
from parayabuck.portfolio.runtime_models import (
    _TERMINAL_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES_SQL,
    OpenPortfolioOrder,
    RebalanceExecutionState,
    _decision_id,
)
from parayabuck.portfolio.runtime_ownership import net_owned_quantities
from parayabuck.storage import SQLiteStore

log = get_logger("parayabuck.portfolio.runtime_repository")

# Historical ledger rows of the removed crypto sleeve (orders and ownership
# adjustments with market='crypto') still load, but ownership reads skip them:
# the runtime is equity-only and leaves any crypto holding alone.
_LEGACY_CRYPTO_MARKET = Market.CRYPTO.value

# A late or racing observation (a stream event applied before the submit
# path records ``submitted``, a reconciliation snapshot taken before the
# fill) must never move a final order back to a working status: the row
# would re-enter ``open_orders`` and hold a position slot until the next
# reconciliation. ``done_for_day`` is excluded because Alpaca resumes such
# an order on the next session.
_FINAL_ORDER_STATUSES_SQL = ", ".join(f"'{status}'" for status in sorted(_TERMINAL_ORDER_STATUSES - {"done_for_day"}))
_NON_REGRESSING_STATUS_SQL = (
    f"CASE WHEN status IN ({_FINAL_ORDER_STATUSES_SQL}) "
    f"AND ? NOT IN ({TERMINAL_ORDER_STATUSES_SQL}) THEN status ELSE ? END"
)


def _execution_price(
    previous_quantity: float,
    previous_average: float | None,
    cumulative_quantity: float,
    cumulative_average: float,
    fill_delta: float,
) -> float:
    """Price of the newly filled delta, derived from cumulative averages.

    Broker updates report the order's cumulative average, so the second
    partial fill of an order must not be recorded at the blended price:
    ``(avg2 * cum2 - avg1 * cum1) / delta`` is what that execution paid. With
    no earlier fill (or no earlier average) the cumulative average is the
    execution price; a non-positive or non-finite derivation (inconsistent
    broker rounding) falls back to the cumulative average.
    """
    if previous_quantity <= 0 or previous_average is None or fill_delta <= 0:
        return cumulative_average
    derived = (cumulative_average * cumulative_quantity - previous_average * previous_quantity) / fill_delta
    if not math.isfinite(derived) or derived <= 0:
        return cumulative_average
    return derived


def _parse_instant(value: object) -> datetime:
    """A stored ISO timestamp as an aware UTC datetime (naive means UTC)."""
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _latest_timestamp(*values: object) -> datetime | None:
    """Latest of the stored ISO timestamps (naive values read as UTC)."""
    parsed: list[datetime] = []
    for value in values:
        if not value:
            continue
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        parsed.append(moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC))
    return max(parsed, default=None)


def _at_or_before(first: object, second: object) -> bool:
    """Whether ``first`` happened at or before ``second``.

    Both are ISO strings or datetimes. An unreadable or missing timestamp
    fails closed: an adjustment whose ordering cannot be proven is never
    consumed.
    """
    moments: list[datetime] = []
    for value in (first, second):
        if isinstance(value, datetime):
            moment = value
        elif value:
            try:
                moment = datetime.fromisoformat(str(value))
            except ValueError:
                return False
        else:
            return False
        moments.append(moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC))
    return moments[0] <= moments[1]


class PortfolioStateRepository:
    def __init__(self, path: str | Path, *, store: SQLiteStore | None = None) -> None:
        self.store = store or SQLiteStore(path)
        self._owns_store = store is None
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
                CREATE TABLE IF NOT EXISTS portfolio_execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    dedup_identity TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    broker_order_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    broker_event_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_cumulative_filled_quantity REAL NOT NULL,
                    raw_cumulative_average_fill_price REAL,
                    raw_fee REAL,
                    fee_observed INTEGER NOT NULL,
                    measurement_quality TEXT NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES portfolio_orders(client_order_id),
                    UNIQUE(source, dedup_identity)
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
                    -- +1 adds the outstanding quantity to ownership (a split
                    -- rebase), -1 removes it (a liquidation or a rebase down
                    -- onto the broker's count). Every historical row is a
                    -- reduction, which is why the default is -1.
                    sign REAL NOT NULL DEFAULT -1.0,
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
                CREATE TABLE IF NOT EXISTS portfolio_ledger_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    client_order_salt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_buy_admission_observations (
                    observation_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    account_equity REAL NOT NULL,
                    account_cash REAL NOT NULL,
                    realized_gross REAL NOT NULL,
                    committed_buy_notional REAL NOT NULL,
                    gross_headroom REAL NOT NULL,
                    cash_headroom REAL NOT NULL,
                    admitted_batch_notional REAL NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES portfolio_orders(client_order_id)
                );
                """
            )
            session_columns = {
                str(row[1]) for row in self.store.connection.execute("PRAGMA table_info(portfolio_runtime_sessions)")
            }
            if "manifest_checksum" in session_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_runtime_sessions RENAME COLUMN manifest_checksum TO configuration_digest"
                )
            if "stage" in session_columns:
                self.store.connection.execute("ALTER TABLE portfolio_runtime_sessions RENAME COLUMN stage TO mode")
            decision_columns = {
                str(row[1]) for row in self.store.connection.execute("PRAGMA table_info(portfolio_decisions)")
            }
            if "manifest_checksum" in decision_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_decisions RENAME COLUMN manifest_checksum TO configuration_digest"
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
            columns = {str(row[1]) for row in self.store.connection.execute("PRAGMA table_info(portfolio_orders)")}
            if "cumulative_filled_quantity" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN cumulative_filled_quantity REAL NOT NULL DEFAULT 0"
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
                self.store.connection.execute("ALTER TABLE portfolio_orders ADD COLUMN average_fill_price REAL")
                self.store.connection.execute(
                    """UPDATE portfolio_orders
                       SET average_fill_price = (
                           SELECT SUM(f.quantity * f.price) / NULLIF(SUM(f.quantity), 0)
                           FROM portfolio_fills f
                           WHERE f.client_order_id = portfolio_orders.client_order_id
                       )
                       WHERE average_fill_price IS NULL"""
                )
            # These times intentionally do not reuse ``created_at``.  That
            # timestamp means the logical order was persisted, whereas the
            # following fields describe the bounded broker-call interval.
            if "submit_call_started_at" not in columns:
                self.store.connection.execute("ALTER TABLE portfolio_orders ADD COLUMN submit_call_started_at TEXT")
            if "submit_call_returned_at" not in columns:
                self.store.connection.execute("ALTER TABLE portfolio_orders ADD COLUMN submit_call_returned_at TEXT")
            fill_columns = {str(row[1]) for row in self.store.connection.execute("PRAGMA table_info(portfolio_fills)")}
            # Execution-quality capture (telemetry, REPORT ONLY): submission ->
            # fill delay per fill. Nullable; legacy rows stay valid.
            if "fill_delay_seconds" not in fill_columns:
                self.store.connection.execute("ALTER TABLE portfolio_fills ADD COLUMN fill_delay_seconds REAL")
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
            adjustment_columns = {
                str(row[1])
                for row in self.store.connection.execute("PRAGMA table_info(portfolio_ownership_adjustments)")
            }
            if "sign" not in adjustment_columns:
                # Every adjustment written before ownership rebases existed
                # reduced ownership, so the column default is the truth for
                # those rows.
                self.store.connection.execute(
                    "ALTER TABLE portfolio_ownership_adjustments ADD COLUMN sign REAL NOT NULL DEFAULT -1.0"
                )
            self._ensure_hot_path_indexes()
            # Epoch for monotonic peak: old history must not undo an explicit
            # operator risk-epoch reset. The epoch is stored alongside
            # high_water (and the sidecar JSON) and every persisted RiskState
            # row carries its epoch. A missing sidecar restores the max peak
            # only from rows/values whose epoch equals the current epoch.
            hw_columns = {
                str(row[1]) for row in self.store.connection.execute("PRAGMA table_info(portfolio_high_water)")
            }
            if "epoch" not in hw_columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_high_water ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0"
                )
            # One random salt per ledger, created once and reused forever, so
            # client order ids stay idempotent across restarts of this ledger
            # but never collide with ids another ledger (a fresh DB, or a
            # second process on the same Alpaca key) derives for the same
            # decision. Written last: an earlier write would open the
            # transaction the decisions rebuild needs to be outside of.
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_ledger_identity(singleton, client_order_salt, created_at)
                   VALUES (1, ?, ?)""",
                (uuid4().hex[:12], datetime.now(UTC).isoformat()),
            )

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

    def has_decision(self, account_id: str, configuration_digest: str, timestamp: datetime, market: Market) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT 1 FROM portfolio_decisions
                   WHERE account_id = ? AND configuration_digest = ?
                     AND decision_timestamp = ? AND market = ?""",
                (account_id, configuration_digest, timestamp.isoformat(), market.value),
            ).fetchone()
        return row is not None

    def recent_decision_timestamps(
        self, account_id: str, configuration_digest: str, market: Market, limit: int = 32
    ) -> tuple[datetime, ...]:
        """The newest ``limit`` persisted decision timestamps, newest first."""
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT decision_timestamp FROM portfolio_decisions
                   WHERE account_id = ? AND configuration_digest = ? AND market = ?
                   ORDER BY decision_timestamp DESC LIMIT ?""",
                (account_id, configuration_digest, market.value, limit),
            ).fetchall()
        return tuple(datetime.fromisoformat(str(row[0])) for row in rows)

    def latest_decision_timestamp(self, account_id: str, configuration_digest: str, market: Market) -> datetime | None:
        """Return the last persisted decision boundary for restart-safe scheduling."""
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT MAX(decision_timestamp) FROM portfolio_decisions
                   WHERE account_id = ? AND configuration_digest = ? AND market = ?""",
                (account_id, configuration_digest, market.value),
            ).fetchone()
        return datetime.fromisoformat(str(row[0])) if row and row[0] is not None else None

    def latest_decision_target(self, account_id: str, market: Market) -> tuple[datetime, dict[str, float]] | None:
        """Newest persisted decision for ``market`` (any configuration digest)
        as ``(decision_timestamp, {symbol: post-risk account weight})``.

        Read-only observability for the intent watchdog: a configuration
        change must not make the previous identity's last decision vanish.
        """
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT decision_timestamp, target_json FROM portfolio_decisions
                   WHERE account_id = ? AND market = ?
                   ORDER BY decision_timestamp DESC LIMIT 1""",
                (account_id, market.value),
            ).fetchone()
        if row is None:
            return None
        weights: dict[str, float] = {}
        try:
            raw = json.loads(str(row[1]))
            for item in raw.get("weights", ()) if isinstance(raw, dict) else ():
                weights[str(item["symbol"])] = float(item["weight"])
        except (TypeError, ValueError, KeyError):
            weights = {}
        return datetime.fromisoformat(str(row[0])), weights

    def latest_decision_id(self, account_id: str, market: Market) -> str | None:
        """Id of the newest persisted decision for ``market`` (any digest)."""
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT decision_id FROM portfolio_decisions
                   WHERE account_id = ? AND market = ?
                   ORDER BY decision_timestamp DESC LIMIT 1""",
                (account_id, market.value),
            ).fetchone()
        return str(row[0]) if row is not None and row[0] is not None else None

    def order_side(self, client_order_id: str) -> str | None:
        """Persisted side (``buy``/``sell``) of one portfolio order, if known."""
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT side FROM portfolio_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return str(row[0]) if row is not None and row[0] is not None else None

    def latest_decision_cross_check(self, account_id: str, market: Market) -> dict[str, Any] | None:
        """``signal_cross_check`` metadata of the newest decision for ``market``.

        Read-only observability for the intent watchdog; None when the
        newest decision carries no cross-check record.
        """
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT target_json FROM portfolio_decisions
                   WHERE account_id = ? AND market = ?
                   ORDER BY decision_timestamp DESC LIMIT 1""",
                (account_id, market.value),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return None
        metadata = raw.get("metadata") if isinstance(raw, dict) else None
        check = metadata.get("signal_cross_check") if isinstance(metadata, dict) else None
        return dict(check) if isinstance(check, dict) else None

    def record_decision_with_orders(
        self,
        session_id: str,
        account_id: str,
        configuration_digest: str,
        target: TargetPortfolio,
        risk: RiskOverlayDecision,
        market: Market,
        orders: tuple[PortfolioOrderRequest, ...],
        *,
        initial_status: str = "submitting",
        buy_orders_json: str | None = None,
        rebalance_phase: str | None = None,
    ) -> str:
        """Atomically persist a decision, its orders, and optional rebalance phase.

        Crash-consistency: the decision row, every order row for that decision,
        and (when ``rebalance_phase`` is set) the rebalance-execution row are
        written in a SINGLE SQLite transaction (``BEGIN IMMEDIATE`` via
        ``with connection``). A crash mid-write cannot leave a half-decision
        (decision without orders) or a half-rebalance (decision+orders without
        the sell_pending marker). The transaction is bounded by ``store.lock``
        plus WAL ``busy_timeout=5000`` so concurrent writers serialize.
        """
        decision_id: str = _decision_id(account_id, configuration_digest, target.decision_timestamp, market)
        created_at = datetime.now(UTC).isoformat()
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
            for order in orders:
                self.store.connection.execute(
                    """INSERT OR IGNORE INTO portfolio_orders(
                        client_order_id, decision_id, symbol, market, side, quantity,
                        reference_price, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        order.client_order_id,
                        decision_id,
                        order.symbol,
                        order.market.value,
                        order.side.value,
                        order.quantity,
                        order.reference_price,
                        initial_status,
                        created_at,
                    ),
                )
            if rebalance_phase is not None:
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
                        rebalance_phase,
                        "active",
                        buy_orders_json or "[]",
                        created_at,
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

    def record_submit_call_started(self, client_order_id: str, occurred_at: datetime) -> None:
        """Record an observed broker-call start without changing submission state.

        Telemetry failure must never turn a previously valid order submission
        into a different execution decision, so callers deliberately isolate
        exceptions from this best-effort measurement write.
        """
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_orders
                   SET submit_call_started_at=COALESCE(submit_call_started_at, ?)
                   WHERE client_order_id=?""",
                (occurred_at.isoformat(), client_order_id),
            )

    def record_submit_call_returned(self, client_order_id: str, occurred_at: datetime) -> None:
        """Record an observed broker-call return without changing order state."""
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_orders
                   SET submit_call_returned_at=COALESCE(submit_call_returned_at, ?)
                   WHERE client_order_id=?""",
                (occurred_at.isoformat(), client_order_id),
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

    def current_rebalance_execution(self, account_id: str, market: Market) -> RebalanceExecutionState | None:
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

    def client_order_salt(self, decision_id: str) -> str:
        """Ledger salt for the client order ids of ``decision_id``.

        A decision that already persisted legacy unsalted ids
        (``parayabuck-{decision_id}-{symbol}-{side}``) keeps the legacy form,
        so a re-plan of an in-flight pre-salt decision regenerates the same
        ids and stays idempotent. Every other decision uses this ledger's
        persisted salt.
        """
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT client_order_id, symbol, side FROM portfolio_orders WHERE decision_id = ?",
                (decision_id,),
            ).fetchall()
            if any(
                str(row[0]) == _client_order_id(decision_id, str(row[1]), PortfolioOrderSide(str(row[2])))
                for row in rows
                if str(row[2]) in {"buy", "sell"}
            ):
                return ""
            salt = self.store.connection.execute(
                "SELECT client_order_salt FROM portfolio_ledger_identity WHERE singleton = 1"
            ).fetchone()
        if salt is None or not str(salt[0]):
            raise RuntimeError("Portfolio ledger has no client order salt")
        return str(salt[0])

    def orders_for_decision(self, decision_id: str, side: str | None = None) -> tuple[dict[str, object], ...]:
        """Order rows attributed to one decision (used by rebalance recovery)."""
        query = """SELECT client_order_id, symbol, market, side, quantity,
                          status, cumulative_filled_quantity, broker_order_id,
                          reference_price, average_fill_price
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
                "broker_order_id": str(row[7] or ""),
                "reference_price": float(row[8] or 0.0),
                "average_fill_price": float(row[9]) if row[9] is not None else None,
            }
            for row in rows
        )

    def submitting_orders(self, account_id: str, market: Market) -> tuple[dict[str, object], ...]:
        """Rows of one account and market still at ``submitting``.

        Attributed through the same decision/session lineage as
        ``open_orders``; each row carries its ``decision_id``.
        """
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT o.client_order_id, o.decision_id, o.status, o.broker_order_id
                   FROM portfolio_orders o
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE s.account_id = ? AND o.market = ? AND o.status = 'submitting'
                   ORDER BY o.created_at""",
                (account_id, market.value),
            ).fetchall()
        return tuple(
            {
                "client_order_id": str(row[0]),
                "decision_id": str(row[1]),
                "status": str(row[2]),
                "broker_order_id": str(row[3] or ""),
            }
            for row in rows
        )

    def record_buy_admission_observation(
        self,
        decision_id: str,
        client_order_ids: tuple[str, ...],
        *,
        account_equity: float,
        account_cash: float,
        realized_gross: float,
        committed_buy_notional: float,
        gross_headroom: float,
        cash_headroom: float,
        admitted_batch_notional: float,
    ) -> None:
        """Mirror final BUY headroom without affecting the order authority."""
        observed_at = datetime.now(UTC).isoformat()
        with self.store.lock, self.store.connection:
            self.store.connection.executemany(
                """INSERT INTO portfolio_buy_admission_observations
                   (observation_id, client_order_id, decision_id, observed_at, account_equity,
                    account_cash, realized_gross, committed_buy_notional,
                    gross_headroom, cash_headroom, admitted_batch_notional)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        uuid4().hex,
                        client_order_id,
                        decision_id,
                        observed_at,
                        account_equity,
                        account_cash,
                        realized_gross,
                        committed_buy_notional,
                        gross_headroom,
                        cash_headroom,
                        admitted_batch_notional,
                    )
                    for client_order_id in client_order_ids
                ),
            )

    def adopt_order_quantity(self, client_order_id: str, quantity: float) -> None:
        """Raise a local order's quantity to the broker's ordered quantity.

        Only reconciliation calls this, after verifying the broker order's
        identity, symbol and side; the quantity never shrinks.
        """
        with self.store.lock, self.store.connection:
            cursor = self.store.connection.execute(
                """UPDATE portfolio_orders SET quantity = ?
                   WHERE client_order_id = ? AND quantity < ?""",
                (float(quantity), client_order_id, float(quantity)),
            )
            if cursor.rowcount == 0:
                row = self.store.connection.execute(
                    "SELECT quantity FROM portfolio_orders WHERE client_order_id = ?", (client_order_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"Cannot adopt quantity for unknown portfolio order: {client_order_id}")

    def update_order(self, client_order_id: str, status: str, broker_order_id: str = "") -> None:
        """Advance an order's status; an empty ``broker_order_id`` keeps the
        recorded broker id instead of erasing it."""
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                f"""UPDATE portfolio_orders SET status = {_NON_REGRESSING_STATUS_SQL},
                       broker_order_id = COALESCE(NULLIF(?, ''), broker_order_id)
                   WHERE client_order_id = ?""",
                (status, status, broker_order_id, client_order_id),
            )

    def clear_stale_ownership(self, account_id: str, symbol: str, market: Market) -> float:
        """Record an explicit ownership reconciliation adjustment when the
        broker holds no position and no real fill could be recovered.

        This never books a synthetic precise SELL fill at an invented price:
        an unknown exit is an anomaly, not a normal trading fill. The
        adjustment reduces owned quantity and can be consumed later by a real
        fill arriving after reconciliation (stream downtime race). Idempotent:
        returns the adjusted quantity (0.0 when nothing is owned anymore).

        The read and the insert share one lock hold: a late SELL fill applied
        by the trade-update thread between them would already have removed
        the quantity, and the clear would then drive the net negative."""
        with self.store.lock:
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
            "Recorded stale-ownership adjustment: account=%s symbol=%s qty=%s (no broker position found)",
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
        sign: float = -1.0,
    ) -> None:
        """Persist an explicit ownership reconciliation adjustment/anomaly.

        ``sign`` is -1 (the default, and the only value historical rows
        carry) for a reduction and +1 for an increase; a split rebase is the
        only audited way to raise ownership.
        """
        if not kind.strip():
            raise ValueError("Adjustment kind is required")
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError("Adjustment quantity must be finite and positive")
        if sign not in (1.0, -1.0):
            raise ValueError("Adjustment sign must be +1 or -1")
        with self.store.lock, self.store.connection:
            self._insert_ownership_adjustment_tx(account_id, symbol, market, quantity, kind, note, sign)

    def _insert_ownership_adjustment_tx(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        quantity: float,
        kind: str,
        note: str,
        sign: float = -1.0,
    ) -> None:
        self.store.connection.execute(
            """INSERT INTO portfolio_ownership_adjustments(
                adjustment_id, account_id, symbol, market, quantity, sign, kind,
                note, created_at, consumed_quantity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                uuid4().hex,
                account_id,
                symbol,
                market.value,
                quantity,
                float(sign),
                kind,
                note,
                datetime.now(UTC).isoformat(),
            ),
        )

    def _consume_ownership_adjustments_tx(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        sell_delta: float,
        order_created_at: datetime | None,
    ) -> None:
        """Consume pending stale-clear adjustments up to a real SELL fill.

        The race: broker stop fills while the stream is delayed; reconcile
        sees a flat broker and records an adjustment; the real fill arrives
        late. The real fill is the ground truth, so it cancels the provisional
        adjustment instead of booking a second SELL on top of it.

        Only an order submitted BEFORE the adjustment can prove that sale. A
        newer SELL is an unrelated trade and consumes nothing: cancelling a
        newer exit against an older adjustment added phantom owned shares
        back and then halted the runtime on ownership drift.
        """
        if sell_delta <= 0:
            return
        remaining = float(sell_delta)
        rows = self.store.connection.execute(
            """SELECT adjustment_id, quantity, consumed_quantity, created_at
               FROM portfolio_ownership_adjustments
               WHERE account_id = ? AND symbol = ? AND market = ? AND kind = 'stale_ownership_clear'
                 AND consumed_quantity < quantity
               ORDER BY created_at""",
            (account_id, symbol, market.value),
        ).fetchall()
        for adjustment_id, quantity, consumed, created_at in rows:
            if remaining <= 1e-12:
                break
            if not _at_or_before(order_created_at, created_at):
                continue
            capacity = float(quantity) - float(consumed)
            consume = min(capacity, remaining)
            self.store.connection.execute(
                """UPDATE portfolio_ownership_adjustments
                   SET consumed_quantity = consumed_quantity + ?
                   WHERE adjustment_id = ?""",
                (consume, str(adjustment_id)),
            )
            remaining -= consume

    def has_order(self, client_order_id: str) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT 1 FROM portfolio_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return row is not None

    def open_orders(self, account_id: str = "unknown", market: Market | None = None) -> tuple[OpenPortfolioOrder, ...]:
        """Non-terminal portfolio orders attributed to one account.

        Orders are attributed through the persisted decision/session lineage,
        so manual/external broker orders can never appear here: a manual
        order must not suppress ownership drift or consume universe pins.
        """
        query = f"""
            SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                   o.cumulative_filled_quantity, o.broker_order_id, o.reference_price,
                   o.status, o.created_at, o.submit_call_started_at, o.submit_call_returned_at
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
                broker_order_id=str(row[6] or ""),
                reference_price=float(row[7] or 0.0),
                status=str(row[8] or ""),
                last_updated_at=_latest_timestamp(row[9], row[10], row[11]),
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
        fee: float | None,
        realized_pnl: float,
        unrealized_pnl: float,
        source: str = "other",
        received_at: datetime | None = None,
        fee_observed: bool | None = None,
    ) -> bool:
        # Fail closed on hostile broker facts BEFORE any write: a non-finite
        # cumulative wedges ownership at inf forever (a later legit update
        # computes max(inf, cum) = inf), a NaN fee silently drops the
        # portfolio_fills row (NaN <> NaN breaks the PK) while the order
        # cumulative still advances, and a non-positive fill price books a
        # worthless lot that later poisons split corroboration.
        try:
            cumulative_value = float(cumulative_filled_quantity)
        except (TypeError, ValueError):
            cumulative_value = math.nan
        try:
            average_value = None if average_fill_price is None else float(average_fill_price)
        except (TypeError, ValueError):
            average_value = math.nan
        try:
            fee_value = None if fee is None else float(fee)
        except (TypeError, ValueError):
            fee_value = math.nan
        invalid = not math.isfinite(cumulative_value) or cumulative_value < 0
        if not invalid and average_value is not None and cumulative_value > 0:
            invalid = not math.isfinite(average_value) or average_value <= 0
        observed = fee is not None if fee_observed is None else bool(fee_observed)
        if not invalid and fee_value is not None and observed:
            invalid = not math.isfinite(fee_value)
        if invalid:
            log.error(
                "Rejecting non-finite fill facts: client_order_id=%s status=%s cumulative=%r average=%r fee=%r",
                client_order_id,
                status,
                cumulative_filled_quantity,
                average_fill_price,
                fee,
            )
            try:
                from parayabuck.monitoring import alerts

                alerts.emit(
                    "paper_incident",
                    message=(
                        f"Rejected non-finite fill facts for {client_order_id} "
                        f"(cumulative={cumulative_filled_quantity!r} average={average_fill_price!r} "
                        f"fee={fee!r}); order left unbooked for the operator."
                    ),
                    category="invalid_fill_facts",
                    incident_id=f"invalid_fill_facts:{client_order_id}",
                )
            except Exception:
                log.warning("Alert emission failed for rejected fill facts of %s", client_order_id, exc_info=True)
            return False
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                """SELECT o.cumulative_filled_quantity, o.reference_price, o.side,
                          o.market, s.account_id, o.created_at, o.symbol,
                          o.average_fill_price
                   FROM portfolio_orders o
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE o.client_order_id = ?""",
                (client_order_id,),
            ).fetchone()
            if row is None:
                return False
            # ``portfolio_orders.cumulative_filled_quantity`` remains
            # the authoritative *net owned* quantity; this journal is solely
            # execution measurement and never drives execution behavior.
            raw_cumulative = float(cumulative_filled_quantity)
            observed_fee = fee is not None if fee_observed is None else bool(fee_observed)
            raw_fee = float(fee) if observed_fee and fee is not None else None
            event_received_at = received_at or datetime.now(UTC)
            event_identity = activity_id or (f"{client_order_id}:{status}:{occurred_at.isoformat()}:{raw_cumulative}")
            previous_raw = self.store.connection.execute(
                """SELECT raw_cumulative_filled_quantity, raw_cumulative_average_fill_price
                   FROM portfolio_execution_events
                   WHERE client_order_id=?
                   ORDER BY id DESC LIMIT 1""",
                (client_order_id,),
            ).fetchone()
            measurement_quality = self._raw_execution_quality(
                raw_cumulative,
                average_fill_price,
                previous_raw,
            )
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_execution_events(
                    source, dedup_identity, client_order_id, broker_order_id,
                    event_type, broker_event_at, received_at,
                    raw_cumulative_filled_quantity, raw_cumulative_average_fill_price,
                    raw_fee, fee_observed, measurement_quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    event_identity,
                    client_order_id,
                    broker_order_id,
                    status,
                    occurred_at.isoformat(),
                    event_received_at.isoformat(),
                    raw_cumulative,
                    average_fill_price,
                    raw_fee,
                    int(observed_fee),
                    measurement_quality,
                ),
            )
            previous = float(row[0] or 0.0)
            reference = float(row[1] or 0.0)
            side = str(row[2])
            market = str(row[3] or "")
            account_id = str(row[4])
            created_at = str(row[5]) if row[5] is not None else None
            symbol = str(row[6])
            fill_delta = max(0.0, cumulative_filled_quantity - previous)
            delay_seconds: float | None = None
            if created_at:
                try:
                    raw_delay = (occurred_at - datetime.fromisoformat(created_at)).total_seconds()
                    if raw_delay >= 0:
                        delay_seconds = raw_delay
                except (TypeError, ValueError):
                    delay_seconds = None
            order_created_at = _latest_timestamp(created_at)
            # The stored cumulative average belongs to the stored cumulative
            # quantity: a late or duplicate event with a lower cumulative must
            # not overwrite it (mirroring the max() guard on quantity), or the
            # next real delta is priced from a stale average.
            stored_average = average_fill_price if fill_delta > 0 or row[7] is None else None
            self.store.connection.execute(
                f"""UPDATE portfolio_orders SET status = {_NON_REGRESSING_STATUS_SQL},
                   broker_order_id = ?, cumulative_filled_quantity = ?,
                   average_fill_price = COALESCE(?, average_fill_price)
                   WHERE client_order_id = ?""",
                (
                    status,
                    status,
                    broker_order_id,
                    max(previous, cumulative_filled_quantity),
                    stored_average,
                    client_order_id,
                ),
            )
            if fill_delta > 0 and average_fill_price is not None:
                fill_price = _execution_price(
                    previous,
                    float(row[7]) if row[7] is not None else None,
                    cumulative_filled_quantity,
                    average_fill_price,
                    fill_delta,
                )
                direction = 1.0 if side == "buy" else -1.0
                slippage = (fill_price - reference) * fill_delta * direction
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
                        fill_price,
                        float(fee or 0.0),
                        realized_pnl,
                        unrealized_pnl,
                        slippage,
                        delay_seconds,
                    ),
                )
                if side == "sell":
                    # A real SELL fill proves the sale that a stale-clear
                    # adjustment assumed; consume the adjustment instead of
                    # booking a second SELL on top of it. Only an order
                    # submitted before the adjustment may do so — a newer
                    # exit is an unrelated trade, and the cleared shares stay
                    # cleared (the row remains an operator-visible anomaly).
                    self._consume_ownership_adjustments_tx(
                        account_id,
                        symbol,
                        Market(market),
                        fill_delta,
                        order_created_at,
                    )
        return True

    @staticmethod
    def _raw_execution_quality(
        cumulative_quantity: float,
        cumulative_average_price: float | None,
        previous: tuple[object, ...] | None,
    ) -> str:
        """Classify a cumulative broker observation without repairing it.

        The raw event is retained even when it cannot safely produce an
        incremental fill.  Ownership continues through the established,
        fail-closed path above.
        """
        if cumulative_quantity < 0:
            return "invalid_negative_cumulative_quantity"
        if previous is None:
            if cumulative_quantity == 0:
                return "status_only"
            return (
                "first_fill"
                if cumulative_average_price and cumulative_average_price > 0
                else "missing_cumulative_average"
            )
        previous_quantity = float(str(previous[0] or 0.0))
        previous_average = float(str(previous[1])) if previous[1] is not None else None
        if cumulative_quantity < previous_quantity:
            return "out_of_order_or_decreasing_cumulative_quantity"
        if cumulative_quantity == previous_quantity:
            return "duplicate_or_zero_delta"
        if not cumulative_average_price or cumulative_average_price <= 0:
            return "missing_cumulative_average"
        if previous_quantity > 0 and previous_average is None:
            return "previous_cumulative_average_missing"
        if previous_quantity > 0 and previous_average is not None:
            delta_notional = cumulative_quantity * cumulative_average_price - previous_quantity * previous_average
            if delta_notional <= 0:
                return "invalid_incremental_notional"
        return "valid_incremental"

    def update_high_water(self, account_id: str, equity: float) -> float:
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                "SELECT equity, epoch FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is not None:
                existing_equity = float(row[0])
                existing_epoch = int(row[1] or 0)
                high_water = max(existing_equity, equity)
            else:
                existing_epoch = 0
                high_water = equity
            self.store.connection.execute(
                """INSERT INTO portfolio_high_water(account_id, equity, updated_at, epoch)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     equity = excluded.equity, updated_at = excluded.updated_at, epoch = excluded.epoch""",
                (account_id, high_water, datetime.now(UTC).isoformat(), existing_epoch),
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
            row = self.store.connection.execute(
                "SELECT epoch FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            new_epoch = int(row[0] or 0) + 1 if row is not None else 1
            self.store.connection.execute(
                """INSERT INTO portfolio_high_water(account_id, equity, updated_at, epoch)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     equity = excluded.equity, updated_at = excluded.updated_at, epoch = excluded.epoch""",
                (account_id, equity, datetime.now(UTC).isoformat(), new_epoch),
            )

    def owned_quantities(self, account_id: str = "unknown", market: Market | None = None) -> dict[str, float]:
        """Net filled quantity per symbol for one account.

        Orders are attributed to an account through the persisted
        decision/session lineage, so a stale paper session can never mark
        symbols as owned for a different (live) account. Legacy crypto rows
        are skipped: the equity-only runtime never owns a crypto holding.
        Delegates pure net calculation to ``runtime_ownership`` for testability.
        """
        query = """
            SELECT o.symbol, o.side, o.cumulative_filled_quantity
            FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ? AND o.market != ?
        """
        params: tuple[object, ...] = (account_id, _LEGACY_CRYPTO_MARKET)
        if market is not None:
            query += " AND o.market = ?"
            params = (*params, market.value)
        with self.store.lock:
            rows = self.store.connection.execute(query, params).fetchall()
            # An adjustment belongs to the market it was raised for; without
            # the same market filter it is netted into the other market's
            # ownership and the ownership invariant trips. ``sign`` is -1 for
            # a reduction (every historical row) and +1 for a split rebase,
            # so the outstanding quantity is signed here and the pure net
            # calculation simply adds it.
            adjustments_query = """SELECT symbol, SUM(sign * (quantity - consumed_quantity))
                   FROM portfolio_ownership_adjustments
                   WHERE account_id = ? AND consumed_quantity < quantity AND market != ?"""
            adjustments_params: tuple[object, ...] = (account_id, _LEGACY_CRYPTO_MARKET)
            if market is not None:
                adjustments_query += " AND market = ?"
                adjustments_params = (*adjustments_params, market.value)
            adjustments_query += " GROUP BY symbol"
            adjustments = self.store.connection.execute(
                adjustments_query,
                adjustments_params,
            ).fetchall()
        # Pure computation delegated
        order_tuples = [(str(r[0]), str(r[1]), float(r[2] or 0.0)) for r in rows]
        adj_tuples = [(str(r[0]), float(r[1] or 0.0)) for r in adjustments]
        return net_owned_quantities(order_tuples, adj_tuples)

    def owned_symbols(self, account_id: str = "unknown", market: Market | None = None) -> set[str]:
        """Symbols the portfolio path still owns: net filled quantity > 0.

        Ownership is computed from cumulative fills (buy minus sell) so a
        partially or fully exited position loses ``managed`` status exactly
        when its broker quantity reaches zero. This is the authoritative
        portfolio-side input to the broker's unified managed-position view.
        """
        return set(self.owned_quantities(account_id, market))

    def ownership_adjustments(self, account_id: str = "unknown") -> tuple[dict[str, object], ...]:
        """Outstanding ownership reconciliation adjustments/anomalies.

        Legacy crypto rows are left out: the equity-only runtime ignores them."""
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT adjustment_id, symbol, market, quantity, consumed_quantity,
                          sign, kind, note, created_at
                   FROM portfolio_ownership_adjustments
                   WHERE account_id = ? AND consumed_quantity < quantity AND market != ?
                   ORDER BY created_at""",
                (account_id, _LEGACY_CRYPTO_MARKET),
            ).fetchall()
        return tuple(
            {
                "adjustment_id": str(row[0]),
                "symbol": str(row[1]),
                "market": str(row[2]),
                "quantity": float(row[3]),
                "consumed_quantity": float(row[4]),
                "outstanding_quantity": float(row[3]) - float(row[4]),
                "sign": float(row[5]),
                "kind": str(row[6]),
                "note": str(row[7]),
                "created_at": str(row[8]),
            }
            for row in rows
        )

    def latest_account_id(self) -> str | None:
        """Account of the most recent runtime session (operator tooling)."""
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT account_id FROM portfolio_runtime_sessions
                   ORDER BY started_at DESC, rowid DESC LIMIT 1"""
            ).fetchone()
        return str(row[0]) if row is not None and row[0] else None

    def _owned_events(
        self,
        account_id: str,
        symbol: str,
        market: Market,
    ) -> list[tuple[datetime, str, float, float | None, float]]:
        """Chronological ownership events of one symbol for one account.

        ``(occurred_at, kind, quantity, price, fee)`` for every fill and every
        outstanding ownership adjustment, ordered the way the FIFO replay
        consumes them: fills before adjustments at the same instant, matching
        ``open_position_cost_bases``' documented replay.
        """
        with self.store.lock:
            fills = self.store.connection.execute(
                """SELECT f.occurred_at, o.side, f.quantity, f.price, f.fee
                   FROM portfolio_fills f
                   JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                   JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
                   WHERE s.account_id = ? AND o.symbol = ? AND o.market = ? AND f.quantity > 0""",
                (account_id, symbol, market.value),
            ).fetchall()
            adjustments = self.store.connection.execute(
                """SELECT created_at, sign * (quantity - consumed_quantity), kind
                   FROM portfolio_ownership_adjustments
                   WHERE account_id = ? AND symbol = ? AND market = ? AND consumed_quantity < quantity""",
                (account_id, symbol, market.value),
            ).fetchall()
        events: list[tuple[tuple[datetime, int], tuple[str, float, float | None, float]]] = []
        for occurred_at, side, quantity, price, fee in fills:
            kind = BUY if str(side).lower() == "buy" else SELL
            instant = _parse_instant(occurred_at)
            events.append(((instant, 0), (kind, float(quantity), float(price), float(fee or 0.0))))
        for created_at, signed_quantity, adjustment_kind in adjustments:
            # A booked reverse split rescales the open lots (fewer shares,
            # same total cost); every other reduction depletes like a sale.
            event_kind = (
                SPLIT_ADJUST if str(adjustment_kind) == SPLIT_REBASE_KIND and float(signed_quantity) < 0 else ADJUST
            )
            events.append(
                ((_parse_instant(created_at), 1), (event_kind, float(signed_quantity), None, 0.0)),
            )
        events.sort(key=lambda item: item[0])
        return [(occurred_at, *event) for (occurred_at, _), event in events]

    def owned_cost_bases(self, account_id: str, symbol: str, market: Market) -> tuple[float, ...]:
        """``(fifo, average_cost)`` entry price of the current owned position,
        or ``()`` when nothing is open (see ``open_position_cost_bases``)."""
        return open_position_cost_bases(
            (kind, quantity, price) for _, kind, quantity, price, _ in self._owned_events(account_id, symbol, market)
        )

    def owned_fifo_lots(
        self,
        account_id: str,
        symbol: str,
        market: Market,
    ) -> tuple[tuple[float, float], ...]:
        """FIFO open lots of the bot's own units: ``(quantity, unit cost)``, oldest first.

        The unit cost of a bought lot is the fill price plus that fill's fees
        per unit — the cost basis the research engine's lot book carries
        (``docs/research/gain_band_v3.md``, arm A1u) — so the downsize-gain-band
        policy reads the same gain fraction in PAPER/LIVE as in the backtest.
        Returns ``()`` when nothing is open.
        """
        events: list[tuple[str, float, float | None]] = []
        for _, kind, quantity, price, fee in self._owned_events(account_id, symbol, market):
            if kind == BUY and price is not None and quantity > 0:
                events.append((kind, quantity, price + fee / quantity))
            else:
                events.append((kind, quantity, price))
        return open_position_lots(events)

    def reconcile_broker_quantity(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        broker_quantity: float,
        broker_average_cost: float | None = None,
    ) -> float | None:
        """Rebase ownership onto a broker share count that differs from ours.

        A corporate action or another consumer of the shared Alpaca key makes
        the repository's net-fill ownership disagree with the broker, and
        every decision (exits included) is dropped while it does. Returns the
        owned quantity after an audited adjustment was recorded, or ``None``
        when the counts agree or the difference is not provable (the caller
        then halts with an actionable incident for the operator to rebase
        explicitly).

        Read and write share one lock hold, so a concurrent fill can never be
        rebased onto a stale owned quantity.
        """
        with self.store.lock, self.store.connection:
            owned = self.owned_quantities(account_id, market).get(symbol, 0.0)
            if owned <= 1e-12 or not math.isfinite(broker_quantity) or broker_quantity <= 0:
                return None
            bases = self.owned_cost_bases(account_id, symbol, market)
            change = classify_share_change(
                owned,
                broker_quantity,
                owned_average_cost=bases[0] if bases else None,
                broker_average_cost=broker_average_cost,
                owned_average_cost_candidates=bases[1:],
            )
            if not change.rebasable and broker_quantity > owned:
                self._alert_unconfirmed_surplus(account_id, symbol, market, owned, broker_quantity, change.reason)
            if not change.rebasable:
                log.warning(
                    "Ownership rebase declined: account=%s symbol=%s owned=%s broker=%s reason=%s",
                    account_id,
                    symbol,
                    owned,
                    broker_quantity,
                    change.reason,
                )
                return None
            self._insert_ownership_adjustment_tx(
                account_id,
                symbol,
                market,
                abs(change.adjustment_quantity),
                kind=SPLIT_REBASE_KIND if change.kind == SPLIT else FOREIGN_SELL_KIND,
                note=(
                    f"{change.reason}; owned {owned:g} -> {broker_quantity:g} "
                    f"(ratio {change.ratio:g}, operator rebase available: "
                    "parayabuck ops rebase-ownership)"
                ),
                sign=1.0 if change.adjustment_quantity > 0 else -1.0,
            )
        log.warning(
            "Ownership rebased on broker quantity: account=%s symbol=%s owned=%s -> %s kind=%s reason=%s",
            account_id,
            symbol,
            owned,
            broker_quantity,
            change.kind,
            change.reason,
        )
        return broker_quantity

    def _alert_unconfirmed_surplus(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        owned: float,
        broker_quantity: float,
        reason: str,
    ) -> None:
        """Raise an operator incident (once per episode) for broker shares on a
        bot-owned symbol that no fill or provable split explains.

        The surplus is left external, so the bot never trades it; but an
        unconfirmed forward split would otherwise silently halve what the
        strategy believes it holds, so it must not pass unnoticed.
        """
        key = (account_id, market.value, symbol, round(owned, 6), round(broker_quantity, 6))
        alerted = self.__dict__.setdefault("_surplus_alerts", set())
        if key in alerted:
            return
        message = (
            f"Broker holds {broker_quantity:g} {symbol} but the bot owns {owned:g} and no fill or "
            f"confirmed split explains the surplus ({reason}). The surplus is treated as external. "
            "If a forward split happened, adopt it with "
            f"`parayabuck ops rebase-ownership {symbol} --broker-qty {broker_quantity:g} --confirm`."
        )
        log.error("Unconfirmed ownership surplus: account=%s %s", account_id, message)
        try:
            from parayabuck.monitoring import alerts

            alerts.emit(
                "paper_incident",
                message=message,
                category="ownership_surplus_unconfirmed",
                incident_id=f"ownership_surplus:{market.value}:{symbol}:{broker_quantity:g}",
                symbol=symbol,
                market=market.value,
                owned_quantity=owned,
                broker_quantity=broker_quantity,
            )
        except Exception:
            log.warning("Alert emission failed for the ownership surplus of %s", symbol, exc_info=True)
            return
        alerted.add(key)

    def rebase_ownership_to_broker_quantity(
        self,
        account_id: str,
        symbol: str,
        market: Market,
        broker_quantity: float,
        *,
        kind: str = "operator_rebase",
        note: str = "",
        broker_average_cost: float | None = None,
    ) -> float | None:
        """Operator-verified adoption of the broker's share count.

        Unconditional by design: the operator has read the real position off
        the broker. Read and write share one lock hold, so the resulting
        owned quantity is exactly ``broker_quantity``. A zero-owned symbol
        adopts a positive broker quantity as an explicit increase (the
        recovery the surplus alert prescribes); adopting zero from zero is
        a no-op that still returns the broker quantity.

        A decrease is booked as a reverse split (``split_rebase``: the open
        lots are rescaled, not sold) when the caller passes that ``kind``
        explicitly, or when ``broker_average_cost`` corroborates a standard
        split ratio exactly as the automatic reconcile path requires.
        Otherwise it depletes the lots like a sale.
        """
        if not math.isfinite(broker_quantity) or broker_quantity < 0:
            raise ValueError("Broker quantity must be finite and non-negative")
        with self.store.lock, self.store.connection:
            owned = self.owned_quantities(account_id, market).get(symbol, 0.0)
            if abs(broker_quantity - owned) <= 1e-12:
                return broker_quantity
            if kind == "operator_rebase" and 1e-12 < broker_quantity < owned and broker_average_cost is not None:
                bases = self.owned_cost_bases(account_id, symbol, market)
                change = classify_share_change(
                    owned,
                    broker_quantity,
                    owned_average_cost=bases[0] if bases else None,
                    broker_average_cost=broker_average_cost,
                    owned_average_cost_candidates=bases[1:],
                )
                if change.kind == SPLIT:
                    kind = SPLIT_REBASE_KIND
                    note = note or f"operator rebase: {change.reason}; owned {owned:g} -> {broker_quantity:g}"
            self._insert_ownership_adjustment_tx(
                account_id,
                symbol,
                market,
                abs(broker_quantity - owned),
                kind=kind,
                note=note or f"operator rebase onto the broker count: {owned:g} -> {broker_quantity:g}",
                sign=1.0 if broker_quantity > owned else -1.0,
            )
        return broker_quantity

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
        if not self._owns_store:
            return
        try:
            self.store.acquire()
        except RuntimeError:
            # The shared store is already closed: cleanup stays a no-op like
            # ExecutionLedger.close() so retry/double-stop paths never raise.
            return
        self.store.release()
