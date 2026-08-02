from __future__ import annotations

import asyncio
import math
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import HTTPException

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.config import EngineConfig
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
    Signal,
)
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.data.buffers import BarMutation, BarRevision, BarStore
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.markets.sectors import classify_instrument, is_inverse_equity, sector_of
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.runtime import EngineRuntime, WorkerHealth, _Event, _EventQueue, _symbol_batches
from parayabuck.web import server
from tests.helpers import apply_entry_fill

pytestmark = pytest.mark.paper_chaos


def test_market_models_reject_non_finite_values_and_naive_signal_time() -> None:
    aware = datetime.now(UTC)
    with pytest.raises(ValueError, match="finite"):
        Bar(aware, "AAPL", "5m", 100, 101, 99, 100, math.nan, Market.EQUITY)
    with pytest.raises(ValueError, match="positive"):
        Signal(
            "AAPL",
            Market.EQUITY,
            Direction.LONG,
            "test",
            80,
            0.8,
            math.nan,
            95,
            110,
            aware,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        Signal(
            "AAPL",
            Market.EQUITY,
            Direction.LONG,
            "test",
            80,
            0.8,
            100,
            95,
            110,
            datetime.now(),
        )


def test_execution_ledger_preserves_and_moves_legacy_decision_orders(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE orders (
            client_order_id TEXT NOT NULL, timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
            state TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (client_order_id, timestamp, state)
        );
        INSERT INTO orders VALUES (
            'legacy-order', '2026-07-27T12:00:00+00:00', 'AAPL',
            'submitted', 'broker_submit', '{}'
        );
        """
    )
    connection.close()

    ledger = ExecutionLedger(str(path))

    order_columns = {str(row[1]) for row in ledger.connection.execute("PRAGMA table_info(orders)")}
    assert "broker_order_id" in order_columns
    assert "parent_broker_order_id" in order_columns
    preserved = ledger.connection.execute(
        """
        SELECT client_order_id, state, reason
        FROM decision_orders
        """
    ).fetchall()
    assert preserved == [("legacy-order", "submitted", "broker_submit")]
    ledger.close()


def test_ledger_rejects_a_second_active_entry_for_the_same_symbol(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    first = OrderIntent(
        symbol="MSFT",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=1,
        entry_price=100,
        stop_price=95,
        target_price=110,
        strategy="test",
        signal_id="one",
    )
    second = OrderIntent(
        symbol="MSFT",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=1,
        entry_price=100,
        stop_price=95,
        target_price=110,
        strategy="test",
        signal_id="two",
    )
    ledger.register_entry_order("acct", first)

    with pytest.raises(ValueError, match="Single-managed-entry invariant"):
        ledger.register_entry_order("acct", second)

    ledger.close()


def test_rejected_broker_entry_releases_its_ledger_reservation(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    intent = OrderIntent(
        symbol="MSFT",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=1,
        entry_price=100,
        stop_price=95,
        target_price=110,
        strategy="test",
        signal_id="one",
    )
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._account_id = "acct"
    broker.ledger = ledger
    broker._validated_values = lambda _intent: (1.0, 95.0, 110.0)
    broker._validate_pre_submit = lambda _intent: None
    broker._submit_with_recovery = lambda *_args: (_ for _ in ()).throw(ValueError("rejected"))

    state, fill = broker.submit(intent)

    assert (state, fill) == (OrderState.REJECTED, None)
    assert ledger.pending_entry_orders("acct") == []
    ledger.register_entry_order("acct", intent)
    ledger.close()


def _bar(
    timestamp: datetime,
    symbol: str = "AAPL",
    close: float = 100,
    market: Market = Market.EQUITY,
) -> Bar:
    return Bar(timestamp, symbol, "5m", close, close + 1, close - 1, close, 100, market)


def test_canonical_crypto_symbols_and_adapters() -> None:
    legacy = normalize_symbol("btcusd", Market.CRYPTO)
    assert legacy.canonical == "BTC/USD"
    assert legacy.market_data_symbol == "BTC/USD"
    assert legacy.trading_symbol == "BTC/USD"
    assert legacy.asset_lookup_symbol == "BTCUSD"
    assert same_symbol("BTCUSD", "btc/usd")
    assert canonical_symbol("aapl") == "AAPL"
    assert not same_symbol("AAPL", "AAPL/USD")


def test_legacy_crypto_symbols_have_crypto_sector_classification() -> None:
    assert sector_of("BTCUSD") == "crypto"
    assert sector_of("AAPL") == "technology"
    assert sector_of("SH") == "inverse_market"
    assert classify_instrument("PSQ").instrument_category == "inverse_equity_etf"
    assert is_inverse_equity("DOG")


def test_crypto_asset_lookup_uses_legacy_adapter() -> None:
    looked_up: list[str] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._asset_cache = {}
    broker._client = SimpleNamespace(
        get_asset=lambda symbol: looked_up.append(symbol) or SimpleNamespace()
    )
    broker._asset("BTC/USD")
    assert looked_up == ["BTCUSD"]


def test_bar_store_correction_updates_without_retriggering() -> None:
    store = BarStore()
    timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    original = _bar(timestamp)
    corrected = _bar(timestamp, close=100.5)
    assert store.upsert(original) == BarMutation.INSERTED
    assert store.upsert(corrected) == BarMutation.STALE_REJECTED
    assert store.upsert(corrected, BarRevision.CORRECTION) == BarMutation.UPDATED
    assert store.get("AAPL", "5m")[-1].close == 100.5


def test_sparse_equity_bars_are_not_mistaken_for_transport_gaps() -> None:
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    equities = BarStore()
    crypto = BarStore()

    assert equities.upsert(_bar(start)) == BarMutation.INSERTED
    assert equities.upsert(_bar(start + timedelta(minutes=10))) == BarMutation.INSERTED

    assert crypto.upsert(_bar(start, symbol="BTC/USD", market=Market.CRYPTO)) == (
        BarMutation.INSERTED
    )
    assert (
        crypto.upsert(_bar(start + timedelta(minutes=10), symbol="BTC/USD", market=Market.CRYPTO))
        == BarMutation.GAP_DETECTED
    )


def test_warmup_symbol_batches_stay_below_alpaca_row_limit() -> None:
    symbols = [f"SYM{index}" for index in range(21)]
    batches = list(_symbol_batches(symbols))

    assert [len(batch) for batch in batches] == [8, 8, 5]
    assert [symbol for batch in batches for symbol in batch] == symbols


def test_partial_ranking_universe_is_evaluated_after_grace_period(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bar = _bar(timestamp)
    evaluated: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    class Engine:
        entry_halt_reason = None
        has_data_gaps = False

        def ingest_closed_bars(self, bars: list[Bar]) -> Any:
            return SimpleNamespace(
                bars=tuple(bars),
                mutations=(SimpleNamespace(value="inserted"),),
            )

        def evaluate_timestamp(
            self,
            _timestamp: datetime,
            symbols: list[str],
            _account: AccountSnapshot,
            *,
            missing_symbols: tuple[str, ...],
        ) -> Any:
            evaluated.append((tuple(symbols), missing_symbols))
            return SimpleNamespace(orders=())

    runtime.engine = Engine()
    runtime.broker = SimpleNamespace(account=lambda: AccountSnapshot(10_000, 10_000, 10_000, 0))
    runtime._historical = None
    runtime._warming_up = False
    runtime._not_ready_symbols = set()
    runtime._last_account_at = None
    runtime._circuit_breaker_reason = None
    runtime._refresh_readiness = lambda: None
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_readiness_reasons = lambda _market=None: ()
    runtime._engine_has_symbol_gap = lambda _symbol: False

    runtime._process_timestamp(timestamp, {"AAPL": bar}, {"AAPL", "MSFT"})

    assert evaluated == [(("AAPL",), ("MSFT",))]
    assert "Ranking universe is partial; missing symbols: MSFT" in caplog.text


def test_runtime_leaves_warmup_when_at_least_one_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["AAPL", "DOG"]
    runtime._not_ready_symbols = {"AAPL", "DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime.config = SimpleNamespace(timeframe="5m", higher_timeframe="1h", warmup_bars=250)

    class Bars:
        @staticmethod
        def get(symbol: str, _timeframe: str) -> list[object]:
            return [object()] * (250 if symbol == "AAPL" else 241)

    runtime.engine = SimpleNamespace(bars=Bars())

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "running"


def test_runtime_stays_in_warmup_when_no_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["DOG"]
    runtime._not_ready_symbols = {"DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime.config = SimpleNamespace(timeframe="5m", higher_timeframe="1h", warmup_bars=250)
    runtime.engine = SimpleNamespace(
        bars=SimpleNamespace(get=lambda _symbol, _timeframe: [object()] * 241)
    )

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "warming_up"


def test_crypto_gap_quarantines_only_affected_symbol_and_allows_btc_order() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    timestamp = datetime(2025, 1, 2, 6, 0, tzinfo=UTC)
    btc = Bar(timestamp, "BTC/USD", "5m", 100, 101, 99, 100, 10, Market.CRYPTO)
    avax = Bar(timestamp, "AVAX/USD", "5m", 40, 41, 39, 40, 10, Market.CRYPTO)
    evaluated: list[tuple[str, ...]] = []
    created_orders: list[OrderIntent] = []

    class Engine:
        entry_halt_reason = None
        has_data_gaps = True

        def __init__(self) -> None:
            self._not_ready = {("AVAX/USD", "5m"), ("AVAX/USD", "1h")}
            self.orders = SimpleNamespace(orders={})

        def has_data_gaps_for_market(self, market: Market) -> bool:
            return market == Market.CRYPTO

        def has_data_gaps_for_symbol(self, symbol: str) -> bool:
            return any(candidate == symbol for candidate, _timeframe in self._not_ready)

        def ingest_closed_bars(self, bars: list[Bar]) -> Any:
            return SimpleNamespace(
                bars=tuple(sorted(bars, key=lambda bar: bar.symbol)),
                mutations=tuple(SimpleNamespace(value="inserted") for _bar in bars),
            )

        def evaluate_timestamp(
            self,
            _timestamp: datetime,
            symbols: list[str],
            _account: AccountSnapshot,
            *,
            missing_symbols: tuple[str, ...],
        ) -> Any:
            del missing_symbols
            evaluated.append(tuple(symbols))
            order = OrderIntent(
                signal_id="btc-signal",
                symbol="BTC/USD",
                market=Market.CRYPTO,
                direction=Direction.LONG,
                qty=0.1,
                entry_price=100,
                stop_price=95,
                target_price=110,
                strategy="test",
            )
            created_orders.append(order)
            self.orders.orders[order.client_order_id] = SimpleNamespace(state=OrderState.ACCEPTED)
            return SimpleNamespace(orders=(order,))

    runtime.engine = Engine()
    runtime.broker = SimpleNamespace(account=lambda: AccountSnapshot(10_000, 10_000, 10_000, 0))
    runtime.config = SimpleNamespace(
        maximum_queue_age_seconds=30,
        maximum_account_age_for_entry_seconds=300,
    )
    runtime._historical = None
    runtime._warming_up = False
    runtime._not_ready_symbols = set()
    runtime._last_account_at = datetime.now(UTC)
    runtime._circuit_breaker_reason = None
    runtime._state = "running"
    runtime._workers = {}
    runtime._market_entry_halts = {Market.CRYPTO: None}
    runtime._running = True
    runtime._refresh_readiness = lambda: None
    runtime._persist_risk_state = lambda _account: None
    runtime.health = lambda: SimpleNamespace(queue_age_seconds=0)

    runtime._process_timestamp(
        timestamp,
        {"BTC/USD": btc, "AVAX/USD": avax},
        {"BTC/USD", "AVAX/USD"},
        market=Market.CRYPTO,
    )

    assert runtime._entry_readiness_reasons(Market.CRYPTO) == ()
    assert evaluated == [("BTC/USD",)]
    assert [order.symbol for order in created_orders] == ["BTC/USD"]


def test_symbol_gap_check_covers_primary_and_higher_timeframes() -> None:
    engine = TradingEngine(
        SimpleNamespace(),
        EngineConfig(),
    )
    engine._not_ready.update({("AVAX/USD", "5m"), ("AVAX/USD", "1h")})

    assert engine.has_data_gaps_for_symbol("AVAX/USD")
    assert not engine.has_data_gaps_for_symbol("BTC/USD")


def test_blocking_trade_update_worker_stays_healthy_while_thread_is_alive() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=10)
    runtime.symbols = ["AAPL"]
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15,
        maximum_account_age_for_entry_seconds=30,
        maximum_queue_age_seconds=30,
    )
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=True),
        "pb-account": WorkerHealth(now, now, alive=True),
        "pb-trade-updates": WorkerHealth(stale, stale, alive=True),
        "pb-stocks": WorkerHealth(stale, stale, last_event_at=now, alive=True),
    }
    runtime._running = True
    runtime._state = "running"
    runtime._last_account_at = now
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    runtime._events = SimpleNamespace(qsize=lambda: 0)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.engine = SimpleNamespace(
        has_data_gaps_for_market=lambda _market: False,
        has_data_gaps_for_symbol=lambda _symbol: False,
    )

    assert runtime.health().healthy is True
    assert runtime.health().state == "running"


def test_stale_crypto_stream_event_degrades_runtime() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    now = datetime.now(UTC)
    stale = now - timedelta(minutes=10)
    runtime.symbols = ["BTC/USD"]
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15,
        maximum_account_age_for_entry_seconds=30,
        maximum_queue_age_seconds=30,
    )
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=True),
        "pb-account": WorkerHealth(now, now, alive=True),
        "pb-trade-updates": WorkerHealth(now, now, alive=True),
        "pb-crypto": WorkerHealth(stale, stale, last_event_at=stale, alive=True),
    }
    runtime._running = True
    runtime._state = "running"
    runtime._last_account_at = now
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    runtime._events = SimpleNamespace(qsize=lambda: 0)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.engine = SimpleNamespace(
        has_data_gaps_for_market=lambda _market: False,
        has_data_gaps_for_symbol=lambda _symbol: False,
    )

    health = runtime.health()

    assert health.healthy is False
    assert health.state == "degraded"
    assert health.markets["crypto"]["stream_healthy"] is False


def test_queue_overflow_never_evicts_final_primary_bar() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._events = _EventQueue(maxsize=2)
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime._oldest_received_at = None
    runtime._circuit_breaker_reason = None
    timestamp = datetime.now(UTC)
    first = Bar(timestamp, "AAPL", "5m", 100, 101, 99, 100, 1_000, Market.EQUITY)
    second = Bar(timestamp, "MSFT", "5m", 100, 101, 99, 100, 1_000, Market.EQUITY)
    higher = Bar(timestamp, "AAPL", "1h", 100, 101, 99, 100, 1_000, Market.EQUITY)
    runtime._events.put_nowait(_Event("primary", first, timestamp))
    runtime._events.put_nowait(_Event("primary", second, timestamp))

    runtime._put_event("higher", higher)

    assert [item.kind for item in runtime._events.snapshot() if item is not None] == [
        "primary",
        "primary",
    ]
    assert runtime._circuit_breaker_reason == "final_market_event_queue_overflow"
    assert runtime._dropped_events == 1


def test_equity_hour_buckets_anchor_at_0930_new_york_across_dst() -> None:
    aggregator = TimeframeAggregator("1h")
    winter = datetime(2025, 1, 2, 15, 31, tzinfo=UTC)  # 10:31 ET
    summer = datetime(2025, 7, 2, 14, 31, tzinfo=UTC)  # 10:31 ET
    assert aggregator._bucket(winter, Market.EQUITY) == datetime(2025, 1, 2, 15, 30, tzinfo=UTC)
    assert aggregator._bucket(summer, Market.EQUITY) == datetime(2025, 7, 2, 14, 30, tzinfo=UTC)


def test_account_failure_does_not_drop_ingested_bar() -> None:
    class FailingBroker:
        def account(self) -> AccountSnapshot:
            raise TimeoutError("account unavailable")

        def submit(self, _order: OrderIntent) -> Any:
            raise AssertionError("must not submit")

        def reconcile(self) -> None:
            return

    engine = TradingEngine(FailingBroker(), EngineConfig())
    bar = _bar(datetime.now(UTC))
    assert engine.process_cycle([bar]) == []
    assert engine.bars.get("AAPL", "5m") == [bar]


def test_external_position_is_not_modified_by_default() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(managed_for_account=lambda _account: {})
    broker._account_id = "acct"
    broker.account = lambda: AccountSnapshot(
        1_000,
        10_000,
        10_000,
        0,
        positions=(PositionSnapshot("AAPL", 10, 100, 101, Market.EQUITY),),
    )
    broker._submit_stop = lambda *_args, **_kwargs: pytest.fail(
        "external position must remain untouched"
    )
    broker.reconcile()


def test_reconcile_closes_stale_managed_entry_missing_from_broker_snapshot() -> None:
    owned = ManagedPosition(
        "acct",
        "XLE",
        Market.EQUITY,
        "test",
        "signal",
        "parayabuck-entry",
        "broker-order",
        170,
        58.59,
        56.0,
        64.0,
        "open",
    )
    closed: list[str] = []
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._account_id = "acct"
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"XLE": owned},
        managed_entries_for_account=lambda _account: [owned],
        mark_position_closed=lambda _account, entry: closed.append(entry),
    )
    broker.account = lambda: AccountSnapshot(
        1_000,
        10_000,
        10_000,
        0,
        positions=(),
    )

    broker.reconcile()

    assert closed == ["parayabuck-entry"]


def test_reconcile_protects_only_owned_quantity_when_external_quantity_coexists() -> None:
    submitted: list[float] = []
    owned = ManagedPosition(
        "acct",
        "BTC/USD",
        Market.CRYPTO,
        "test",
        "signal",
        "parayabuck-entry",
        "broker-order",
        0.01,
        100,
        95,
        110,
        "open",
    )
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(managed_for_account=lambda _account: {"BTC/USD": owned})
    broker._account_id = "acct"
    broker.account = lambda: AccountSnapshot(
        1_000,
        10_000,
        10_000,
        0,
        positions=(
            PositionSnapshot(
                "BTC/USD",
                0.51,
                100,
                101,
                Market.CRYPTO,
                qty_available=0.51,
            ),
        ),
    )
    broker._has_protective_order = lambda *_args: False
    broker._available_position_qty = lambda _symbol, qty, **_kwargs: qty
    broker._submit_stop = lambda _symbol, _market, qty, *_args, **_kwargs: (
        submitted.append(qty) or True
    )
    broker.reconcile()
    assert submitted == [pytest.approx(0.01)]


def test_backtest_checks_stop_on_entry_bar_and_gap_price() -> None:
    broker = BacktestBroker(10_000, slippage_bps=0)
    created = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    order = OrderIntent(
        "signal",
        "AAPL",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=created,
    )
    broker.submit(order)
    gap_bar = Bar(
        created + timedelta(minutes=5),
        "AAPL",
        "5m",
        94,
        96,
        93,
        95,
        100,
        Market.EQUITY,
    )
    broker.advance(gap_bar)
    assert broker.realized_pnls == [0]  # entry and emergency stop both fill at the gap open
    assert "AAPL" not in broker.positions


def test_backtest_emits_insufficient_cash_reason_and_rejects_short() -> None:
    broker = BacktestBroker(10)
    created = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    expensive = OrderIntent(
        "signal",
        "AAPL",
        Market.EQUITY,
        Direction.LONG,
        1,
        100,
        95,
        110,
        "test",
        created_at=created,
    )
    broker.submit(expensive)
    broker.advance(_bar(created + timedelta(minutes=5)))
    assert broker.order_rejections[expensive.client_order_id] == "insufficient_cash"
    short = OrderIntent(
        "short",
        "AAPL",
        Market.EQUITY,
        Direction.SHORT,
        1,
        100,
        105,
        90,
        "test",
    )
    with pytest.raises(ValueError, match="Short execution is disabled"):
        broker.submit(short)


def test_atomic_credentials_preserve_unmanaged_fields_and_never_touch_example(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    env.write_text("UNMANAGED=value\nALPACA_API_KEY=old\n")
    example.write_text("ALPACA_SECRET_KEY=your_secret_key_here\n")
    before = example.read_bytes()
    server._atomic_update_credentials(env, "new-key", "new-secret")
    assert "UNMANAGED=value" in env.read_text()
    assert "ALPACA_API_KEY=new-key" in env.read_text()
    assert "ALPACA_SECRET_KEY=new-secret" in env.read_text()
    assert example.read_bytes() == before
    assert os.stat(env).st_mode & 0o777 == 0o600


def test_direct_settings_call_cannot_auto_restart_into_live(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class Manager:
        config_path = tmp_path / "settings.yaml"
        env_path = tmp_path / ".env"
        engine_state = "running"
        broker = None
        _live_armed = True

        def is_paper_mode(self) -> bool:
            return True

        def get_credentials(self) -> tuple[str, str]:
            return "key", "secret"

    manager = Manager()
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "alpaca": {"paper": True, "stock_feed": "iex"},
                "symbols": ["AAPL"],
                "engine": {"timeframe": "5m", "higher_timeframe": "1h"},
            }
        )
    )
    monkeypatch.setattr(server, "manager", manager)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            server.save_settings(server.SettingsPayload(paper_mode=False, restart_engine=True))
        )
    assert error.value.status_code == 400


def test_execution_ledger_applies_cumulative_entry_and_child_exit_deltas(
    tmp_path: Path,
) -> None:
    ledger = ExecutionLedger(str(tmp_path / "execution.sqlite3"))
    created = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    order = OrderIntent(
        "signal",
        "AAPL",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=created,
        client_order_id="parayabuck-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")

    def event(
        client_id: str,
        broker_id: str,
        role: OrderRole,
        cumulative: float,
        state: OrderState,
        *,
        parent: str | None = None,
        price: float = 100,
    ) -> ExecutionEvent:
        return ExecutionEvent(
            "acct",
            client_id,
            broker_id,
            parent,
            role,
            "AAPL",
            Market.EQUITY,
            state,
            10,
            cumulative,
            price,
            created + timedelta(minutes=cumulative),
        )

    deltas = [
        ledger.apply_execution_event(
            event(
                "parayabuck-entry",
                "broker-entry",
                OrderRole.ENTRY,
                cumulative,
                state,
            )
        ).fill_delta
        for cumulative, state in (
            (2, OrderState.PARTIALLY_FILLED),
            (5, OrderState.PARTIALLY_FILLED),
            (10, OrderState.FILLED),
        )
    ]
    assert deltas == [2, 3, 5]
    assert ledger.managed_for_account("acct")["AAPL"].owned_qty == 10

    exit_deltas = [
        ledger.apply_execution_event(
            event(
                "target-child",
                "broker-target",
                OrderRole.TAKE_PROFIT,
                cumulative,
                state,
                parent="broker-entry",
                price=110,
            )
        ).fill_delta
        for cumulative, state in (
            (4, OrderState.PARTIALLY_FILLED),
            (10, OrderState.FILLED),
        )
    ]
    assert exit_deltas == [4, 6]
    duplicate = ledger.apply_execution_event(
        event(
            "target-child",
            "broker-target",
            OrderRole.TAKE_PROFIT,
            10,
            OrderState.FILLED,
            parent="broker-entry",
            price=110,
        )
    )
    out_of_order = ledger.apply_execution_event(
        event(
            "target-child",
            "broker-target",
            OrderRole.TAKE_PROFIT,
            4,
            OrderState.PARTIALLY_FILLED,
            parent="broker-entry",
            price=110,
        )
    )
    assert duplicate.fill_delta == 0
    assert out_of_order.fill_delta == 0
    assert ledger.managed_for_account("acct") == {}
    row = ledger.connection.execute(
        """
        SELECT entry_filled_qty, exit_filled_qty, remaining_owned_qty, status
        FROM managed_positions WHERE entry_client_order_id='parayabuck-entry'
        """
    ).fetchone()
    assert row == (10.0, 10.0, 0.0, "closed")
    ledger.close()


def test_late_entry_fee_restates_realized_pnl(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "fees.sqlite3"))
    order = OrderIntent(
        "signal",
        "AAPL",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        client_order_id="parayabuck-fee-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "AAPL", 10, 100, Direction.LONG, datetime.now(UTC)),
        "broker-entry",
    )
    exit_transition = ledger.apply_execution_event(
        ExecutionEvent(
            "acct",
            "target-exit",
            "broker-target",
            "broker-entry",
            OrderRole.TAKE_PROFIT,
            "AAPL",
            Market.EQUITY,
            OrderState.FILLED,
            10,
            10,
            110,
            datetime.now(UTC),
        )
    )
    assert exit_transition.realized_pnl_delta == 100

    assert ledger.apply_fee_activity(
        "acct",
        activity_id="fee-1",
        broker_order_id="broker-entry",
        fee=5,
        occurred_at=datetime.now(UTC),
    )
    realized = ledger.connection.execute(
        "SELECT realized_pnl FROM managed_positions WHERE entry_client_order_id=?",
        (order.client_order_id,),
    ).fetchone()[0]
    assert realized == pytest.approx(95)
    ledger.close()


def test_aggregator_waits_for_finalization_grace() -> None:
    aggregator = TimeframeAggregator("5m", finalization_grace_seconds=2)
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    aggregator.update(_bar(start))
    assert aggregator.flush_until(start + timedelta(minutes=5, seconds=1)) == []
    assert len(aggregator.flush_until(start + timedelta(minutes=5, seconds=2))) == 1


def test_safety_lock_reset_requires_clean_reconciliation() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ambiguous_order_state"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime.engine = SimpleNamespace(has_data_gaps=False, entry_halt_reason="locked")
    reconciled: list[bool] = []
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: reconciled.append(True),
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(managed_entries_for_account=lambda _account: ())
    persisted: list[AccountSnapshot] = []
    runtime._persist_risk_state = persisted.append

    runtime.reset_safety_lock()

    assert reconciled == [True]
    assert runtime._circuit_breaker_reason is None
    assert runtime.engine.entry_halt_reason is None
    assert runtime._state == "running"
    assert persisted == [account]


def test_safety_lock_reset_refuses_manual_intervention() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime.engine = SimpleNamespace(has_data_gaps=False, entry_halt_reason="locked")
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: None)
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (
            SimpleNamespace(status="manual_intervention_required"),
        )
    )

    with pytest.raises(ValueError, match="manual intervention"):
        runtime.reset_safety_lock()


def test_live_arm_token_is_bound_to_credentials_and_expires_on_change(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = server.EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(yaml.safe_dump({"alpaca": {"paper": True}, "symbols": ["AAPL"]}))
    credentials = ["key-a", "secret-a"]
    monkeypatch.setattr(manager, "get_credentials", lambda: tuple(credentials))
    now = datetime.now(UTC)
    manager._pending_live_arm = server.LiveArmToken(
        "account-a",
        "1234",
        manager._credential_fingerprint(*credentials),
        manager._config_digest(paper=False),
        now,
        now + timedelta(minutes=1),
        "nonce",
    )
    manager._live_request_suffix = "1234"
    manager.confirm_live_arming("LIVE 1234")
    manager._require_valid_live_arm(*credentials)

    credentials[1] = "secret-b"
    with pytest.raises(ValueError, match="fresh confirmation"):
        manager._require_valid_live_arm(*credentials)
    assert not manager._live_armed
