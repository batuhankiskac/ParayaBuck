"""Offline golden contracts for the read-only execution shadow comparator."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.portfolio_engine import OrderSide
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.shadow_comparator import (
    ShadowOrder,
    run_shadow_comparator,
    schedule_zero_cost_fills,
)

EPOCH = datetime(2026, 1, 5, tzinfo=UTC)


def _bar(offset: int, *, symbol: str = "AAA", market: Market = Market.EQUITY, volume: float = 100.0) -> Bar:
    price = 100.0 + offset
    return Bar(EPOCH + timedelta(days=offset), symbol, "1d", price, price + 1, price - 1, price, volume, market)


def _order(*, quantity: float = 10.0, superseded_at: datetime | None = None) -> ShadowOrder:
    return ShadowOrder("o1", "d1", EPOCH, "AAA", Market.EQUITY, OrderSide.BUY, quantity, 100.0, "filled", superseded_at=superseded_at)


def test_golden_a_next_eligible_open_uses_decision_known_volume() -> None:
    slices = schedule_zero_cost_fills(_order(quantity=5.0), [_bar(0), _bar(1, volume=999)])
    assert len(slices) == 1
    assert slices[0].timestamp == EPOCH + timedelta(days=1)
    assert slices[0].open_price == 101.0
    assert slices[0].known_average_volume == 100.0


def test_golden_b_dynamic_residual_capacity_refreshes_after_prior_fill() -> None:
    bars = [_bar(day, volume=100.0) for day in range(-20, 1)]
    bars.extend([_bar(1, volume=200.0), _bar(2, volume=300.0), _bar(3, volume=400.0)])
    slices = schedule_zero_cost_fills(_order(quantity=25.0), bars)
    assert [item.quantity for item in slices] == [10.0, 10.5, 4.5]
    assert [item.known_average_volume for item in slices] == [100.0, 105.0, 115.0]
    assert slices[1].capacity_timestamp == EPOCH + timedelta(days=1)


def test_golden_c_fill_bar_completed_volume_never_enters_its_own_capacity() -> None:
    slices = schedule_zero_cost_fills(_order(quantity=15.0), [_bar(0), _bar(1, volume=0), _bar(2, volume=1)])
    assert [item.quantity for item in slices] == [10.0, 5.0]
    assert slices[0].known_average_volume == 100.0
    assert slices[1].known_average_volume == 100.0


def test_golden_d_decision_supersession_allows_its_predecision_fill_then_stops_residual_schedule() -> None:
    slices = schedule_zero_cost_fills(
        _order(quantity=30.0, superseded_at=EPOCH + timedelta(days=2)),
        [_bar(0), _bar(1), _bar(2), _bar(3)],
    )
    # The engine fills the day-2 execution cycle before it applies the day-2
    # decision, which then clears the residual before day 3.
    assert [item.timestamp for item in slices] == [EPOCH + timedelta(days=1), EPOCH + timedelta(days=2)]
    assert sum(item.quantity for item in slices) == 20.0


def test_golden_restart_equivalence_resumes_with_prior_fill_capacity_timestamp() -> None:
    bars = [_bar(day, volume=100.0) for day in range(-20, 4)]
    uninterrupted = schedule_zero_cost_fills(_order(quantity=25.0), bars)
    resumed = schedule_zero_cost_fills(
        ShadowOrder(
            "o1", "d1", EPOCH, "AAA", Market.EQUITY, OrderSide.BUY, 15.0, 100.0, "partially_filled",
            capacity_timestamp=EPOCH + timedelta(days=1),
        ),
        bars,
    )
    assert [(item.timestamp, item.quantity) for item in resumed] == [
        (item.timestamp, item.quantity) for item in uninterrupted[1:]
    ]


def _seed_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT, configuration_digest TEXT, mode TEXT,
                started_at TEXT, ended_at TEXT, status TEXT
            );
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT, account_id TEXT, configuration_digest TEXT,
                decision_timestamp TEXT, market TEXT, strategy_name TEXT, target_json TEXT, risk_json TEXT
            );
            CREATE TABLE portfolio_orders (
                client_order_id TEXT PRIMARY KEY, decision_id TEXT, symbol TEXT, market TEXT, side TEXT,
                quantity REAL, reference_price REAL, status TEXT, broker_order_id TEXT, created_at TEXT
            );
            CREATE TABLE portfolio_execution_events (
                id INTEGER PRIMARY KEY, source TEXT, dedup_identity TEXT, client_order_id TEXT,
                broker_order_id TEXT, event_type TEXT, broker_event_at TEXT, received_at TEXT,
                raw_cumulative_filled_quantity REAL, raw_cumulative_average_fill_price REAL,
                raw_fee REAL, fee_observed INTEGER, measurement_quality TEXT
            );
            """
        )
        connection.execute("INSERT INTO portfolio_runtime_sessions VALUES ('s', 'a', 'd', 'paper', 'x', NULL, 'running')")
        for decision, market in (("de", "equity"), ("dc", "crypto")):
            connection.execute(
                "INSERT INTO portfolio_decisions VALUES (?, 's', 'a', 'd', ?, ?, 'test', '{}', '{}')",
                (decision, EPOCH.isoformat(), market),
            )
        for order, decision, symbol, market in (("oe", "de", "AAA", "equity"), ("oc", "dc", "BTC/USD", "crypto")):
            connection.execute(
                "INSERT INTO portfolio_orders VALUES (?, ?, ?, ?, 'buy', 1, 100, 'filled', '', ?)",
                (order, decision, symbol, market, EPOCH.isoformat()),
            )
            connection.execute(
                "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', ?, ?, '', 'fill', ?, ?, 1, 101, 0, 1, 'ok')",
                (order, order, EPOCH.isoformat(), EPOCH.isoformat()),
            )
        connection.commit()
    finally:
        connection.close()


def test_golden_e_f_separate_markets_and_unknown_fee_is_price_only(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    bars = [_bar(0), _bar(1), _bar(0, symbol="BTC/USD", market=Market.CRYPTO), _bar(1, symbol="BTC/USD", market=Market.CRYPTO)]
    report = run_shadow_comparator(database, bars, Config(), dataset={"path": "fixture"})
    assert set(report["aggregates_by_market"]) == {"equity", "crypto"}
    orders = {item["client_order_id"]: item for item in report["orders"]}
    assert orders["oe"]["paper_vs_zero_cost_open_bps"] is not None
    assert orders["oe"]["actual_fee_known"] is False
    assert orders["oe"]["actual_total_cost"] is None
    assert orders["oc"]["modeled_fee"] > orders["oe"]["modeled_fee"]
    assert report["transferability_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"


def test_golden_g_runtime_database_is_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    run_shadow_comparator(database, [_bar(0), _bar(1)], Config(), dataset={"path": "fixture"})
    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert after == before
