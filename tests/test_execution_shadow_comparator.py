"""Offline golden contracts for the read-only execution shadow comparator."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine, PortfolioFill
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.shadow_comparator import (
    ShadowOrder,
    _order_record,
    run_shadow_comparator,
    schedule_zero_cost_fills,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

EPOCH = datetime(2026, 1, 5, tzinfo=UTC)


def _bar(offset: int, *, symbol: str = "AAA", market: Market = Market.EQUITY, volume: float = 100.0) -> Bar:
    price = 100.0 + offset
    return Bar(EPOCH + timedelta(days=offset), symbol, "1d", price, price + 1, price - 1, price, volume, market)


def _order(*, quantity: float = 10.0, superseded_at: datetime | None = None) -> ShadowOrder:
    return ShadowOrder(
        "o1", "d1", EPOCH, "AAA", Market.EQUITY, OrderSide.BUY, quantity, 100.0, "filled", superseded_at=superseded_at
    )


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
            "o1",
            "d1",
            EPOCH,
            "AAA",
            Market.EQUITY,
            OrderSide.BUY,
            15.0,
            100.0,
            "partially_filled",
            capacity_timestamp=EPOCH + timedelta(days=1),
        ),
        bars,
    )
    assert [(item.timestamp, item.quantity) for item in resumed] == [
        (item.timestamp, item.quantity) for item in uninterrupted[1:]
    ]


class _ParityStrategy(PortfolioStrategy):
    name = "shadow_parity"
    version = "1"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            (TargetWeight("AAA", 0.25),),
            0.75,
            universe_version="test",
        )


class _TracingEngine(PortfolioBacktestEngine):
    """Record engine facts immediately around the real fill implementation."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fill_trace: list[tuple[PortfolioFill, float, datetime, float]] = []

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:
        pending = next(
            (order for order in self._pending if order.symbol == bar.symbol and order.side == side),
            None,
        )
        known_average = self._known_average_volume(pending, bar.timeframe) if pending is not None else 0.0
        capacity_timestamp = pending.created_at if pending is not None else bar.timestamp
        before = len(self._fills)
        super()._fill_side(bar, side)
        if len(self._fills) > before:
            remaining = next(
                (order.qty for order in self._pending if order.symbol == bar.symbol and order.side == side),
                0.0,
            )
            self.fill_trace.append((self._fills[-1], known_average, capacity_timestamp, remaining))


def test_differential_golden_real_engine_matches_shadow_tranches() -> None:
    """Hard parity: this invokes the engine event loop, not a copied fixture."""
    config = Config()
    warmup = [_bar(day, volume=100.0) for day in range(-20, 0)]
    execution = [_bar(0, volume=100.0), _bar(1, volume=200.0), _bar(2, volume=300.0), _bar(3, volume=400.0)]
    engine = _TracingEngine(
        _ParityStrategy(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=10_000.0,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        max_participation_pct=0.10,
        liquidity_lookback_bars=20,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        allow_static_universe=True,
    )
    engine.run(execution, warmup=warmup, liquidate_at_end=False)

    order = _order(quantity=25.0)
    comparator = _order_record(
        order,
        {
            "quantity": 25.0,
            "vwap": 100.0,
            "fee_known": False,
            "quality": "fixture",
            "price_comparison_eligible": True,
        },
        [*warmup, *execution],
        config,
    )
    expected = comparator["schedule"]
    assert len(engine.fill_trace) == len(expected) == 3
    for (fill, known_average, capacity_timestamp, remaining), expected_slice in zip(
        engine.fill_trace, expected, strict=True
    ):
        assert fill.timestamp.isoformat() == expected_slice["timestamp"]
        assert fill.qty == pytest.approx(expected_slice["quantity"])
        assert fill.reference_price == pytest.approx(expected_slice["open"])
        assert known_average == pytest.approx(expected_slice["known_average_volume"])
        assert capacity_timestamp.isoformat() == expected_slice["capacity_timestamp"]
        assert remaining == pytest.approx(
            max(0.0, 25.0 - sum(item["quantity"] for item in expected[: expected.index(expected_slice) + 1]))
        )
        assert fill.fill_price == pytest.approx(expected_slice["modeled_price"])
        assert fill.fee == pytest.approx(expected_slice["modeled_fee"])


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
        connection.execute(
            "INSERT INTO portfolio_runtime_sessions VALUES ('s', 'a', 'd', 'paper', 'x', NULL, 'running')"
        )
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
                "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', ?, ?, '', 'fill', ?, ?, 1, 101, 0, 1, 'first_fill')",
                (order, order, EPOCH.isoformat(), EPOCH.isoformat()),
            )
        connection.commit()
    finally:
        connection.close()


def test_golden_e_f_equity_only_and_unknown_fee_is_price_only(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    bars = [
        _bar(0),
        _bar(1),
        _bar(0, symbol="BTC/USD", market=Market.CRYPTO),
        _bar(1, symbol="BTC/USD", market=Market.CRYPTO),
    ]
    report = run_shadow_comparator(database, bars, Config(), dataset={"path": "fixture"})
    # Historical crypto rows still load but are outside the equity-only model.
    assert set(report["aggregates_by_market"]) == {"equity"}
    orders = {item["client_order_id"]: item for item in report["orders"]}
    assert set(orders) == {"oe"}
    assert orders["oe"]["paper_vs_zero_cost_open_bps"] is not None
    assert orders["oe"]["actual_fee_known"] is False
    assert orders["oe"]["actual_total_cost"] is None
    assert report["transferability_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"


def test_golden_g_runtime_database_is_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    run_shadow_comparator(database, [_bar(0), _bar(1)], Config(), dataset={"path": "fixture"})
    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert after == before


def test_mixed_timeframes_fail_closed_with_explicit_report_reason(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    mixed = [_bar(0), replace(_bar(1), timeframe="1h")]
    with pytest.raises(ValueError, match="noncomparable_mixed_timeframes"):
        schedule_zero_cost_fills(_order(), mixed)
    report = run_shadow_comparator(database, mixed, Config(), dataset={"path": "fixture"})
    equity = next(item for item in report["orders"] if item["market"] == "equity")
    assert equity["schedule"] == []
    assert "noncomparable_mixed_timeframes" in equity["excluded_reasons"]


def test_conflicting_duplicate_bars_match_engine_rejection(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    conflicting = [_bar(0), _bar(1), replace(_bar(1), close=101.5)]
    with pytest.raises(ValueError, match="Conflicting duplicate bars"):
        schedule_zero_cost_fills(_order(), conflicting)
    with pytest.raises(ValueError, match="Conflicting duplicate bars"):
        run_shadow_comparator(database, conflicting, Config(), dataset={"path": "fixture"})


def test_zero_order_same_market_decision_supersedes_residual(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE portfolio_orders SET quantity=30 WHERE client_order_id='oe'")
        connection.execute(
            "INSERT INTO portfolio_decisions VALUES ('zero', 's', 'a', 'd', ?, 'equity', 'test', '{}', '{}')",
            ((EPOCH + timedelta(days=2)).isoformat(),),
        )
    report = run_shadow_comparator(database, [_bar(day) for day in range(4)], Config(), dataset={"path": "fixture"})
    equity = next(item for item in report["orders"] if item["client_order_id"] == "oe")
    assert [item["timestamp"] for item in equity["schedule"]] == [
        (EPOCH + timedelta(days=1)).isoformat(),
        (EPOCH + timedelta(days=2)).isoformat(),
    ]


def test_invalid_raw_event_quality_is_excluded_from_price_comparison(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', 'late', 'oe', '', 'fill', ?, ?, 0.5, 100, NULL, 0, 'out_of_order_or_decreasing_cumulative_quantity')",
            ((EPOCH + timedelta(days=1)).isoformat(), (EPOCH + timedelta(days=1)).isoformat()),
        )
    report = run_shadow_comparator(database, [_bar(0), _bar(1)], Config(), dataset={"path": "fixture"})
    equity = next(item for item in report["orders"] if item["client_order_id"] == "oe")
    assert equity["paper_vwap"] is None
    assert equity["comparison_eligibility"] == {
        "final_gross_execution_quantity": False,
        "final_vwap": False,
        "price_only": False,
        "total_cost": False,
    }
    assert any(reason.startswith("raw_execution_quality_excluded:") for reason in equity["excluded_reasons"])
