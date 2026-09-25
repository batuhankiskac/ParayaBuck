"""Restart-recovery tests preserving telemetry / ledger execution-quality
state (offline, paper-chaos gate).

Everything here is offline: fake brokers and real SQLite repositories. The
scenarios verify that the execution-quality capture (decision -> order ->
fill lineage, reference price, fill delay, observed slippage and fees)
survives a restart in the authoritative records, that the repository resumes
its decision boundary without duplication, and that the schema migration
backfills the capture columns for pre-migration databases.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExecutionEvent,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
)
from parayabuck.execution import telemetry
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
)
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)

pytestmark = pytest.mark.paper_chaos


class _FixedTargetStrategy:
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        self.parameters = {"targets": dict(targets)}

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, weight) for symbol, weight in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )


class _SimBroker:
    def __init__(self) -> None:
        self.positions: list[PositionSnapshot] = []
        self.open_symbols: set[str] = set()
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        equity = 100_000.0
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(
            cash=equity - value,
            equity=equity,
            peak_equity=equity,
            daily_pnl=0.0,
            positions=tuple(self.positions),
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(symbol, market, 1.0, 1.0, 0.01, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set(self.open_symbols)

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        if order.symbol in self.open_symbols:
            raise ValueError(f"Open order already exists for {order.symbol}")
        self.submitted.append(order)
        self.open_symbols.add(order.symbol)
        return "broker-" + order.client_order_id


def _runtime(
    db: Path,
    broker: _SimBroker,
    targets: dict[str, float],
) -> PortfolioRuntime:
    repository = PortfolioStateRepository(db)
    return PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(sorted(targets)),
        broker,
        repository,
        mode="paper",
        maximum_allocation=0.50,
        account_id="acct",
    )


def _bars(symbols: tuple[str, ...]) -> list[Bar]:
    return [
        Bar(datetime(2025, 1, 1, tzinfo=UTC), symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000) for symbol in symbols
    ]


def _apply_fill(
    repository: PortfolioStateRepository,
    order: PortfolioOrderRequest,
    *,
    fill_price: float,
    fee: float,
    occurred_at: datetime,
) -> None:
    repository.apply_trade_update(
        client_order_id=order.client_order_id,
        broker_order_id="broker-" + order.client_order_id,
        status="fill",
        cumulative_filled_quantity=order.quantity,
        average_fill_price=fill_price,
        occurred_at=occurred_at,
        activity_id="activity-" + order.client_order_id,
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_restart_preserves_fill_quality_telemetry(tmp_path: Path) -> None:
    """A filled decision's quality state (reference price, fill delay,
    slippage, fee) survives a repository restart and stays readable by the
    telemetry layer; the restart resumes without a duplicate decision."""
    db = tmp_path / "runtime.sqlite3"
    symbols = ("AAPL", "MSFT")
    broker = _SimBroker()
    runtime = _runtime(db, broker, {"AAPL": 0.2, "MSFT": 0.3})
    result = runtime.process_closed_bars(_bars(symbols))
    assert result.status == "submitted"
    assert result.decision_id is not None
    decision_id = result.decision_id
    assert len(broker.submitted) == 2

    # One fill at +10 bps adverse with a real fee, 4s after submission.
    order = broker.submitted[0]
    now = datetime.now(UTC)
    _apply_fill(
        runtime.repository,
        order,
        fill_price=order.reference_price * 1.001,
        fee=1.25,
        occurred_at=now + timedelta(seconds=4),
    )
    runtime.close()

    # ---- restart ----
    broker2 = _SimBroker()
    runtime2 = _runtime(db, broker2, {"AAPL": 0.2, "MSFT": 0.3})
    metrics = telemetry.execution_metrics(db)
    assert metrics["available"] is True
    assert metrics["fills"]["count"] == 1
    assert metrics["fills"]["by_market"] == {"equity": 1}
    assert metrics["fees"]["total"] == pytest.approx(1.25)
    assert metrics["fill_latency"]["count"] == 1
    assert metrics["fill_latency"]["mean"] == pytest.approx(4.0, abs=0.05)
    assert metrics["slippage"]["median"] == pytest.approx(10.0)

    comparison = telemetry.decision_expected_vs_actual(db, decision_id)
    assert comparison["available"] is True
    filled = [item for item in comparison["orders"] if item["client_order_id"] == order.client_order_id]
    assert len(filled) == 1
    assert filled[0]["slippage_bps"] == pytest.approx(10.0)
    assert filled[0]["fill_ratio"] == 1.0
    assert filled[0]["fees"] == pytest.approx(1.25)
    assert filled[0]["fill_delay_seconds"] is not None

    # Restart resumes the persisted decision boundary: replaying the same
    # bar must NOT manufacture a duplicate decision.
    replay = runtime2.process_closed_bars(_bars(symbols))
    assert replay.status in {"duplicate_decision", "not_scheduled"}
    assert broker2.submitted == []
    runtime2.close()


def test_restart_preserves_legacy_ledger_capture(tmp_path: Path) -> None:
    """The legacy ledger capture columns (decision timestamp, reference
    price, submitted/filled stamps, fill delay, slippage, actual fee) are
    persisted and survive an ExecutionLedger reopen."""
    db = tmp_path / "ledger.sqlite3"
    decision_at = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
    ledger = ExecutionLedger(str(db))
    intent = OrderIntent(
        signal_id="sig-1",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=10.0,
        entry_price=100.0,
        stop_price=95.0,
        target_price=115.0,
        strategy="equity_leveraged_trend_v1",
        client_order_id="pb-restart-1",
    )
    ledger.register_entry_order(
        "acct",
        intent,
        decision_timestamp=decision_at,
        target_weight=0.10,
        reference_price=100.0,
    )
    submitted_at = ledger.connection.execute(
        "SELECT submitted_at FROM orders WHERE client_order_id=?", ("pb-restart-1",)
    ).fetchone()[0]
    filled_at = datetime.fromisoformat(str(submitted_at)) + timedelta(seconds=20)
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id=intent.client_order_id,
            broker_order_id="broker-1",
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol=intent.symbol,
            market=intent.market,
            state=OrderState.FILLED,
            requested_qty=intent.qty,
            cumulative_filled_qty=10.0,
            average_fill_price=100.2,
            occurred_at=filled_at,
            fee=0.4,
        )
    )
    ledger.close()

    # ---- restart: fresh ledger on the same database ----
    ledger2 = ExecutionLedger(str(db))
    try:
        row = ledger2.connection.execute(
            "SELECT decision_timestamp, target_weight, reference_price, "
            "submitted_at, filled_at, fill_delay_seconds, slippage_bps, actual_fee "
            "FROM orders WHERE client_order_id=?",
            ("pb-restart-1",),
        ).fetchone()
        assert row[0] == decision_at.isoformat()
        assert row[1] == 0.10
        assert row[2] == 100.0
        assert row[3] is not None
        assert row[4] == filled_at.isoformat()
        assert row[5] == pytest.approx(20.0)
        assert row[6] == pytest.approx(20.0)  # (100.2 - 100) / 100 * 1e4
        assert row[7] == 0.4
        metrics = telemetry.execution_metrics(db)
        assert metrics["fills"]["count"] == 0  # legacy rows count under orders_with_fills
        assert metrics["orders_with_fills"]["count"] == 1
        assert metrics["slippage"]["median"] == pytest.approx(20.0)
        assert metrics["fees"]["total"] == pytest.approx(0.4)
    finally:
        ledger2.close()


def test_fill_delay_migration_backfills_legacy_fills(tmp_path: Path) -> None:
    """A pre-migration ``portfolio_fills`` table (no fill_delay_seconds
    column) gets the column AND a backfilled delay from the order's
    created_at when the repository migrates on reopen."""
    db = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db)
    try:
        connection.executescript(
            """
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                configuration_digest TEXT NOT NULL, mode TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
            );
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT '', configuration_digest TEXT NOT NULL,
                decision_timestamp TEXT NOT NULL, market TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                target_json TEXT NOT NULL, risk_json TEXT NOT NULL
            );
            CREATE TABLE portfolio_orders (
                client_order_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                quantity REAL NOT NULL, reference_price REAL NOT NULL,
                status TEXT NOT NULL, broker_order_id TEXT NOT NULL DEFAULT '',
                cumulative_filled_quantity REAL NOT NULL DEFAULT 0,
                average_fill_price REAL, created_at TEXT NOT NULL
            );
            CREATE TABLE portfolio_fills (
                activity_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, quantity REAL NOT NULL,
                price REAL NOT NULL, fee REAL NOT NULL,
                realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
                slippage REAL NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO portfolio_runtime_sessions VALUES "
            "('s1', 'acct', 'digest', 'paper', '2026-01-01T00:00:00+00:00', NULL, 'running')"
        )
        connection.execute(
            "INSERT INTO portfolio_decisions VALUES "
            "('d1', 's1', 'acct', 'digest', '2026-01-05T00:00:00+00:00', 'equity', "
            "'fixed', '{}', '{}')"
        )
        connection.execute(
            "INSERT INTO portfolio_orders VALUES "
            "('o1', 'd1', 'AAPL', 'equity', 'buy', 10.0, 100.0, 'filled', 'b1', "
            "10.0, 100.5, '2026-01-05T15:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO portfolio_fills VALUES "
            "('f1', 'o1', '2026-01-05T15:00:07+00:00', 10.0, 100.5, 0.5, 5.0, 0.0, 5.0)"
        )
        connection.commit()
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(portfolio_fills)")}
        assert "fill_delay_seconds" not in columns
    finally:
        connection.close()

    # Reopen triggers the migration (column + backfill from created_at).
    repository = PortfolioStateRepository(db)
    try:
        row = repository.store.connection.execute(
            "SELECT fill_delay_seconds FROM portfolio_fills WHERE activity_id='f1'"
        ).fetchone()
        assert row[0] == pytest.approx(7.0)
        metrics = telemetry.execution_metrics(db)
        assert metrics["fill_latency"]["count"] == 1
        assert metrics["fill_latency"]["mean"] == pytest.approx(7.0)
    finally:
        repository.close()
