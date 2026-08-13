"""Execution-telemetry contract tests.

Covers the pure computation helpers (slippage bps math, fill delay,
expected-vs-actual), the cost-calibration aggregation over a seeded runtime
database, execution/forward metrics over real ledger tables, and the
empty-state guarantee (a fresh or missing database never raises and yields
explicit zeros / None). Everything is offline: local SQLite only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import (
    Direction,
    ExecutionEvent,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
)
from parayabuck.execution import telemetry
from parayabuck.execution.ledger import ExecutionLedger


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    return (
        _linear(ordered, 50),
        _linear(ordered, 75),
        _linear(ordered, 95),
    )


def _linear(ordered: list[float], pct: float) -> float:
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_slippage_bps_buy_and_sell_adverse_and_favorable() -> None:
    # BUY: fill above reference is adverse (positive).
    assert telemetry.slippage_bps(101.0, 100.0, "buy") == 100.0
    # BUY: fill below reference is favorable (negative).
    assert telemetry.slippage_bps(99.5, 100.0, "buy") == -50.0
    # SELL: fill below reference is adverse (positive).
    assert telemetry.slippage_bps(99.0, 100.0, "sell") == 100.0
    # SELL: fill above reference is favorable (negative).
    assert telemetry.slippage_bps(101.0, 100.0, "sell") == -100.0
    # Legacy entry directions map to the same convention.
    assert telemetry.slippage_bps(101.0, 100.0, "long") == 100.0
    assert telemetry.slippage_bps(99.0, 100.0, "short") == 100.0


def test_slippage_bps_invalid_inputs_return_none() -> None:
    assert telemetry.slippage_bps(0.0, 100.0, "buy") is None
    assert telemetry.slippage_bps(100.0, 0.0, "buy") is None
    assert telemetry.slippage_bps(100.0, 100.0, "unknown_side") is None


def test_fill_delay_seconds() -> None:
    submitted = "2026-01-01T15:00:00+00:00"
    assert telemetry.fill_delay_seconds("2026-01-01T15:00:05+00:00", submitted) == 5.0
    assert telemetry.fill_delay_seconds("2026-01-01T15:00:00+00:00", submitted) == 0.0
    # Fill before submission (clock skew) -> None, never negative.
    assert telemetry.fill_delay_seconds("2026-01-01T14:59:59+00:00", submitted) is None
    assert telemetry.fill_delay_seconds("not-a-date", submitted) is None
    assert telemetry.fill_delay_seconds("2026-01-01T15:00:05+00:00", None) is None


def test_expected_vs_actual_full_fill() -> None:
    order = {
        "client_order_id": "o1",
        "symbol": "AAPL",
        "market": "equity",
        "side": "buy",
        "quantity": 10.0,
        "reference_price": 100.0,
        "status": "filled",
        "created_at": "2026-01-01T15:00:00+00:00",
    }
    fills = [
        {
            "quantity": 6.0,
            "price": 100.5,
            "fee": 0.1,
            "occurred_at": "2026-01-01T15:00:05+00:00",
        },
        {
            "quantity": 4.0,
            "price": 100.5,
            "fee": 0.05,
            "occurred_at": "2026-01-01T15:00:06+00:00",
        },
    ]
    result = telemetry.expected_vs_actual(order, fills)
    assert result["expected_quantity"] == 10.0
    assert result["actual_quantity"] == 10.0
    assert result["fill_ratio"] == 1.0
    assert result["average_fill_price"] == 100.5
    assert result["fill_price_delta_bps"] == 50.0
    assert result["slippage_bps"] == 50.0  # buy adverse
    assert result["fees"] == 0.15
    assert result["fill_count"] == 2
    assert result["fill_delay_seconds"] == 5.0
    assert result["filled"] is True
    assert result["complete"] is True


def test_expected_vs_actual_partial_and_unfilled() -> None:
    order = {
        "client_order_id": "o2",
        "symbol": "ETH/USD",
        "market": "crypto",
        "side": "buy",
        "quantity": 2.0,
        "reference_price": 3000.0,
        "status": "partially_filled",
        "created_at": "2026-01-01T16:00:00+00:00",
    }
    partial = telemetry.expected_vs_actual(
        order,
        [
            {
                "quantity": 1.0,
                "price": 3003.0,
                "fee": 0.25,
                "occurred_at": "2026-01-01T16:00:10+00:00",
            }
        ],
    )
    assert partial["fill_ratio"] == 0.5
    assert partial["complete"] is False
    assert partial["slippage_bps"] == 10.0
    assert partial["fill_delay_seconds"] == 10.0
    unfilled = telemetry.expected_vs_actual(order, [])
    assert unfilled["filled"] is False
    assert unfilled["actual_quantity"] == 0.0
    assert unfilled["fill_ratio"] == 0.0
    assert unfilled["average_fill_price"] is None
    assert unfilled["fill_delay_seconds"] is None


# --------------------------------------------------------------------------
# Seeded database helpers
# --------------------------------------------------------------------------


def _seed_portfolio_database(path: Path) -> None:
    """Insert a session, decisions, orders and fills with known costs."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
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
                target_json TEXT NOT NULL, risk_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolio_orders (
                client_order_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                quantity REAL NOT NULL, reference_price REAL NOT NULL,
                status TEXT NOT NULL, broker_order_id TEXT NOT NULL DEFAULT '',
                cumulative_filled_quantity REAL NOT NULL DEFAULT 0,
                average_fill_price REAL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolio_fills (
                activity_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, quantity REAL NOT NULL,
                price REAL NOT NULL, fee REAL NOT NULL,
                realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
                slippage REAL NOT NULL, fill_delay_seconds REAL
            );
            CREATE TABLE IF NOT EXISTS risk_state (
                account_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(account_id, session_date)
            );
            """
        )
        connection.execute(
            "INSERT INTO portfolio_runtime_sessions VALUES "
            "('s1', 'acct', 'digest', 'paper', '2026-01-01T00:00:00+00:00', NULL, 'running')"
        )
        for market, decision_id, timestamp in (
            ("equity", "d-eq-1", "2026-01-05T00:00:00+00:00"),
            ("equity", "d-eq-2", "2026-02-05T00:00:00+00:00"),
            ("crypto", "d-cr-1", "2026-01-05T00:00:00+00:00"),
        ):
            target = {
                "weights": [
                    {"symbol": "AAPL", "weight": 0.20, "reason": "t"},
                    {"symbol": "MSFT", "weight": 0.10, "reason": "t"},
                ]
                if market == "equity"
                else [{"symbol": "BTC/USD", "weight": 0.05, "reason": "t"}],
                "metadata": {"qualified_symbols": 8},
            }
            connection.execute(
                "INSERT INTO portfolio_decisions VALUES "
                "(?, 's1', 'acct', 'digest', ?, ?, ?, ?, '{}')",
                (decision_id, timestamp, market, "strategy_" + market, json.dumps(target)),
            )
        # Equity decision d-eq-2: two buys with 5 / 10 / 15 bps adverse fills.
        connection.execute(
            "INSERT INTO portfolio_orders VALUES "
            "('o-eq-a', 'd-eq-2', 'AAPL', 'equity', 'buy', 100.0, 100.0, "
            "'filled', 'b1', 100.0, 100.05, '2026-02-05T15:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO portfolio_orders VALUES "
            "('o-eq-b', 'd-eq-2', 'MSFT', 'equity', 'buy', 50.0, 200.0, "
            "'filled', 'b2', 50.0, 200.20, '2026-02-05T15:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO portfolio_orders VALUES "
            "('o-cr-a', 'd-cr-1', 'BTC/USD', 'crypto', 'buy', 0.5, 3000.0, "
            "'filled', 'b3', 0.5, 3004.5, '2026-01-05T15:00:00+00:00')"
        )
        # Fills: AAPL +5 bps, MSFT +10 bps, BTC +15 bps (fees included).
        for activity, client, occurred, qty, price, fee in (
            ("f1", "o-eq-a", "2026-02-05T15:00:03+00:00", 100.0, 100.05, 0.5),
            ("f2", "o-eq-b", "2026-02-05T15:00:05+00:00", 50.0, 200.20, 0.5),
            ("f3", "o-cr-a", "2026-01-05T15:00:07+00:00", 0.5, 3004.5, 3.75),
        ):
            created = (
                "2026-02-05T15:00:00+00:00" if activity != "f3" else "2026-01-05T15:00:00+00:00"
            )
            connection.execute(
                "INSERT INTO portfolio_fills VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, 0.0, ?)",
                (
                    activity,
                    client,
                    occurred,
                    qty,
                    price,
                    fee,
                    (
                        datetime.fromisoformat(occurred) - datetime.fromisoformat(created)
                    ).total_seconds(),
                ),
            )
        connection.execute(
            "INSERT INTO risk_state(account_id, session_date, payload, updated_at) "
            "VALUES ('acct', '2026-02-05', ?, '2026-02-05T16:00:00+00:00')",
            (
                json.dumps(
                    {
                        "day_start_equity": 100000.0,
                        "peak_equity": 101000.0,
                        "realized_pnl": 100.0,
                        "unrealized_pnl": 50.0,
                        "fees": 10.0,
                        "consecutive_losses": 0,
                        "circuit_breaker_reason": None,
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------------------------------
# Cost calibration aggregation
# --------------------------------------------------------------------------


def test_cost_calibration_aggregates_median_p75_p95(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _seed_portfolio_database(db)
    calibration = telemetry.cost_calibration(db)
    observed = calibration["observed"]["slippage_bps"]
    # Fills: +5, +10, +15 bps -> median 10, p75 12.5, p95 14.5.
    assert observed["count"] == 3
    assert observed["median"] == pytest.approx(10.0)
    assert observed["p75"] == pytest.approx(12.5)
    assert observed["p95"] == pytest.approx(14.5)
    comparison = calibration["comparison"]
    assert comparison["modeled_adverse_bps"] == pytest.approx(3.0)  # 2 bps slippage + 2/2 spread
    # Median 10 bps is well above the modeled 3 bps -> above_model (report only).
    assert comparison["verdict"] == "above_model"
    assert calibration["policy"].startswith("REPORT ONLY")
    # Observed fees: 0.5 + 0.5 + 3.75.
    assert calibration["observed"]["total_fees"] == pytest.approx(4.75)


def test_cost_calibration_no_observed_data_is_report_only(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    calibration = telemetry.cost_calibration(db)
    assert calibration["available"] is True
    assert calibration["comparison"]["verdict"] == "no_observed_data"
    assert calibration["observed"]["slippage_bps"]["count"] == 0


# --------------------------------------------------------------------------
# Metrics over the seeded database
# --------------------------------------------------------------------------


def test_execution_metrics_counts_orders_fills_rejects_and_fees(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _seed_portfolio_database(db)
    metrics = telemetry.execution_metrics(db)
    assert metrics["available"] is True
    assert metrics["orders"]["total"] == 3
    assert metrics["fills"]["count"] == 3
    assert metrics["fills"]["by_market"] == {"equity": 2, "crypto": 1}
    assert metrics["fees"]["total"] == pytest.approx(4.75)
    assert metrics["rejects"]["count"] == 0
    latency = metrics["fill_latency"]
    assert latency["count"] == 3
    assert latency["mean"] == pytest.approx(5.0)
    slippage = metrics["slippage"]
    assert slippage["median"] == pytest.approx(10.0)
    assert slippage["by_market"]["crypto"]["median"] == pytest.approx(15.0)


def test_forward_metrics_decisions_turnover_and_portfolio(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _seed_portfolio_database(db)
    metrics = telemetry.forward_metrics(db)
    assert metrics["available"] is True
    strategy = metrics["strategy"]
    assert strategy["decisions"]["count"] == 3
    assert strategy["decisions"]["by_market"] == {"equity": 2, "crypto": 1}
    assert strategy["candidate_pool_size"]["mean"] == 8.0
    assert strategy["observation_set_churn"]["total"] == 0
    turnover = strategy["target_turnover"]["by_market"]["equity"]
    # d-eq-1: AAPL 0.20 + MSFT 0.10 -> d-eq-2: same weights -> turnover 0.
    assert turnover["mean"] == 0.0
    portfolio = metrics["portfolio"]
    assert portfolio["pnl"]["realized"] == 100.0  # risk_state payload
    assert portfolio["pnl"]["unrealized"] == 50.0
    assert portfolio["drawdown"]["peak_equity"] == 101000.0
    assert portfolio["equity_estimate"] == 100140.0  # 100000 + 100 + 50 - 10
    assert portfolio["gross_exposure"] is None  # not persisted -> explicit None


def test_decision_expected_vs_actual_reads_orders_and_fills(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _seed_portfolio_database(db)
    result = telemetry.decision_expected_vs_actual(db, "d-eq-2")
    assert result["available"] is True
    assert result["decision"]["market"] == "equity"
    by_symbol = {order["symbol"]: order for order in result["orders"]}
    assert by_symbol["AAPL"]["fill_ratio"] == 1.0
    assert by_symbol["AAPL"]["slippage_bps"] == 5.0
    assert by_symbol["MSFT"]["slippage_bps"] == 10.0
    assert by_symbol["AAPL"]["fill_delay_seconds"] == 3.0


# --------------------------------------------------------------------------
# Empty state
# --------------------------------------------------------------------------


def test_empty_and_missing_database_never_raise(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.sqlite3"
    for function in (telemetry.execution_metrics, telemetry.forward_metrics):
        result = function(missing)
        assert result["available"] is False
    assert telemetry.cost_calibration(missing)["available"] is False
    assert telemetry.latest_decision_ids(missing) == {}
    assert telemetry.decision_expected_vs_actual(missing, "x")["available"] is False

    fresh = tmp_path / "fresh.sqlite3"
    sqlite3.connect(fresh).close()
    metrics = telemetry.execution_metrics(fresh)
    assert metrics["available"] is True
    assert metrics["orders"]["total"] == 0
    assert metrics["fills"]["count"] == 0
    assert metrics["rejects"]["count"] == 0
    assert metrics["slippage"]["median"] is None
    assert metrics["fill_latency"]["mean"] is None
    assert metrics["fees"]["total"] == 0.0
    forward = telemetry.forward_metrics(fresh)
    assert forward["available"] is True
    assert forward["strategy"]["decisions"]["count"] == 0
    assert forward["execution"]["fill_rate"] is None
    # JSON-serializable in every state.
    json.dumps(metrics)
    json.dumps(forward)


# --------------------------------------------------------------------------
# Legacy ledger capture (migration v5 columns)
# --------------------------------------------------------------------------


def _intent(symbol: str, *, direction: Direction = Direction.LONG) -> OrderIntent:
    return OrderIntent(
        signal_id="sig-1",
        symbol=symbol,
        market=Market.EQUITY,
        direction=direction,
        qty=10.0,
        entry_price=100.0,
        stop_price=95.0,
        target_price=115.0,
        strategy="equity_residual_momentum_v1",
        client_order_id="pb-" + symbol,
    )


def test_ledger_capture_columns_populated_on_fill(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    try:
        decision_at = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
        intent = _intent("AAPL")
        ledger.register_entry_order(
            "acct",
            intent,
            decision_timestamp=decision_at,
            target_weight=0.10,
            reference_price=100.0,
            spread_estimate_bps=1.5,
        )
        row = ledger.connection.execute(
            "SELECT decision_timestamp, target_weight, reference_price, "
            "spread_estimate_bps, submitted_at FROM orders WHERE client_order_id=?",
            ("pb-AAPL",),
        ).fetchone()
        assert row[0] == decision_at.isoformat()
        assert row[1] == 0.10
        assert row[2] == 100.0
        assert row[3] == 1.5
        assert row[4] is not None  # submitted_at

        # The fill lands 30s after the (real-time) submission stamp.
        submitted_at = datetime.fromisoformat(str(row[4]))
        filled_at = submitted_at + timedelta(seconds=30)
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
                average_fill_price=100.5,
                occurred_at=filled_at,
                fee=0.25,
            )
        )
        row = ledger.connection.execute(
            "SELECT filled_at, fill_delay_seconds, slippage_bps, actual_fee, terminal_state "
            "FROM orders WHERE client_order_id=?",
            ("pb-AAPL",),
        ).fetchone()
        assert row[0] == filled_at.isoformat()
        assert row[1] == 30.0  # fill delay vs submitted_at (registered ~now)
        assert row[2] == 50.0  # (100.5 - 100) / 100 * 1e4, long = buy
        assert row[3] == 0.25
        assert row[4] == "filled"

        metrics = telemetry.execution_metrics(tmp_path / "ledger.sqlite3")
        assert metrics["fills"]["count"] == 0  # legacy rows count under orders_with_fills
        assert metrics["orders_with_fills"]["count"] == 1
        assert metrics["slippage"]["median"] == 50.0
        assert metrics["fees"]["total"] == 0.25
        assert metrics["fill_latency"]["mean"] == 30.0
    finally:
        ledger.close()


def test_ledger_capture_short_sell_slippage_is_adverse_positive(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    try:
        intent = _intent("MSFT", direction=Direction.SHORT)
        ledger.register_entry_order("acct", intent, reference_price=200.0)
        filled_at = datetime.now(UTC) + timedelta(seconds=5)
        ledger.apply_execution_event(
            ExecutionEvent(
                account_id="acct",
                client_order_id=intent.client_order_id,
                broker_order_id="broker-2",
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=intent.symbol,
                market=intent.market,
                state=OrderState.FILLED,
                requested_qty=intent.qty,
                cumulative_filled_qty=10.0,
                average_fill_price=199.0,
                occurred_at=filled_at,
            )
        )
        row = ledger.connection.execute(
            "SELECT slippage_bps FROM orders WHERE client_order_id=?", ("pb-MSFT",)
        ).fetchone()
        assert row[0] == 50.0  # short filled below reference = adverse
    finally:
        ledger.close()
