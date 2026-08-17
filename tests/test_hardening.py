from __future__ import annotations

import asyncio
import math
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import HTTPException

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
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.markets.sectors import classify_instrument, is_inverse_equity, sector_of
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.runtime import EngineRuntime, WorkerHealth, _Event, _EventQueue, _symbol_batches
from parayabuck.runtime_state import RuntimeMarketState
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
    broker.ledger = None
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


def test_partial_primary_batch_updates_market_state_without_legacy_decision(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bar = _bar(timestamp)
    ingested: list[tuple[str, ...]] = []

    class Engine:
        entry_halt_reason = None
        has_data_gaps = False

        def ingest_closed_bars(self, bars: list[Bar]) -> Any:
            ingested.append(tuple(bar.symbol for bar in bars))
            return SimpleNamespace(
                bars=tuple(bars),
                mutations=(SimpleNamespace(value="inserted"),),
            )

    runtime.market_state = Engine()
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

    assert ingested == [("AAPL",)]
    assert "Ranking universe is partial; missing symbols: MSFT" in caplog.text


def test_late_primary_bar_is_ingested_and_duplicate_is_correction() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    ingested: list[tuple[str, ...]] = []
    corrections: list[str] = []
    first_ingested = threading.Event()

    class Engine:
        entry_halt_reason = None
        has_data_gaps = False

        def ingest_closed_bars(self, bars: list[Bar]) -> Any:
            ingested.append(tuple(bar.symbol for bar in bars))
            first_ingested.set()
            return SimpleNamespace(
                bars=tuple(bars),
                mutations=tuple(SimpleNamespace(value="inserted") for _bar in bars),
            )

        def ingest_corrections(self, bars: list[Bar]) -> Any:
            corrections.extend(bar.symbol for bar in bars)

    runtime.market_state = Engine()
    runtime.broker = SimpleNamespace(account=lambda: AccountSnapshot(10_000, 10_000, 10_000, 0))
    runtime.config = SimpleNamespace(
        event_grace_seconds=0.0,
        maximum_queue_age_seconds=30,
        maximum_account_age_for_entry_seconds=300,
    )
    runtime._historical = None
    runtime._warming_up = False
    runtime._not_ready_symbols = set()
    runtime._last_account_at = None
    runtime._circuit_breaker_reason = None
    runtime._refresh_readiness = lambda: None
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_readiness_reasons = lambda _market=None: ()
    runtime._engine_has_symbol_gap = lambda _symbol: False
    runtime._expected_all_symbols = {"AAPL", "MSFT"}
    runtime._workers = {"pb-decisions": WorkerHealth(datetime.now(UTC), datetime.now(UTC))}
    runtime._events = _EventQueue(4096)
    runtime._event_stats_lock = threading.Lock()
    runtime._oldest_received_at = None
    runtime._coalesced_events = 0
    runtime._dropped_events = 0
    runtime._series_watermarks = {}
    runtime._portfolio_watermarks = {}
    runtime._state = "running"
    runtime._running = True
    runtime._flush_aggregators = lambda: None
    runtime._schedule_reconcile = lambda: None

    thread = threading.Thread(target=runtime._decision_loop, daemon=True)
    thread.start()
    try:
        runtime._events.put_market_event(
            _Event("primary", _bar(timestamp, "AAPL"), datetime.now(UTC))
        )
        assert first_ingested.wait(5)
        runtime._events.put_market_event(
            _Event("primary", _bar(timestamp, "MSFT"), datetime.now(UTC))
        )
        deadline = time.monotonic() + 5
        while len(ingested) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        runtime._events.put_market_event(
            _Event("primary", _bar(timestamp, "AAPL"), datetime.now(UTC))
        )
        deadline = time.monotonic() + 5
        while "AAPL" not in corrections and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        runtime._running = False
        thread.join(timeout=5)

    assert ingested == [("AAPL",), ("MSFT",)]
    assert corrections == ["AAPL"]


def test_runtime_leaves_warmup_when_at_least_one_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["AAPL", "DOG"]
    runtime._not_ready_symbols = {"AAPL", "DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(minimum_history_bars=250),
            history={"AAPL": [object()] * 250, "DOG": [object()] * 241},
        ),
    )

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "running"


def test_runtime_stays_in_warmup_when_no_symbol_is_ready() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.symbols = ["DOG"]
    runtime._not_ready_symbols = {"DOG"}
    runtime._running = True
    runtime._state = "warming_up"
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(minimum_history_bars=250),
            history={"DOG": [object()] * 241},
        ),
    )

    runtime._refresh_readiness()

    assert runtime._not_ready_symbols == {"DOG"}
    assert runtime._state == "warming_up"


def test_crypto_gap_quarantines_only_affected_symbol_in_operational_state() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    timestamp = datetime(2025, 1, 2, 6, 0, tzinfo=UTC)
    btc = Bar(timestamp, "BTC/USD", "5m", 100, 101, 99, 100, 10, Market.CRYPTO)
    avax = Bar(timestamp, "AVAX/USD", "5m", 40, 41, 39, 40, 10, Market.CRYPTO)
    ingested: list[tuple[str, ...]] = []

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
            ingested.append(tuple(sorted(bar.symbol for bar in bars)))
            return SimpleNamespace(
                bars=tuple(sorted(bars, key=lambda bar: bar.symbol)),
                mutations=tuple(SimpleNamespace(value="inserted") for _bar in bars),
            )

    runtime.market_state = Engine()
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
    )

    assert runtime._entry_readiness_reasons(Market.CRYPTO) == ()
    assert ingested == [("AVAX/USD", "BTC/USD")]


def test_symbol_gap_check_covers_all_operational_timeframes() -> None:
    state = RuntimeMarketState(EngineConfig())
    state._not_ready.update({("AVAX/USD", "5m"), ("AVAX/USD", "1h")})

    assert state.has_data_gaps_for_symbol("AVAX/USD")
    assert not state.has_data_gaps_for_symbol("BTC/USD")


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
    runtime.market_state = SimpleNamespace(
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
    runtime.market_state = SimpleNamespace(
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
    state = RuntimeMarketState(EngineConfig())
    bar = _bar(datetime.now(UTC))
    state.ingest_closed_bars([bar])
    assert state.bars.get("AAPL", "5m") == [bar]


def test_external_position_is_not_modified_by_default() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = None
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {},
        managed_entries_for_account=lambda _account: (),
        mark_manual_intervention=lambda *_args: None,
        set_protection_state=lambda *_args: None,
        apply_fee_activity=lambda *args: True,
    )
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
    broker._client = SimpleNamespace(_request=lambda *_args, **_kwargs: [])
    broker.ledger = None
    broker._account_id = "acct"
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"XLE": owned},
        managed_entries_for_account=lambda _account: [owned],
        mark_position_closed=lambda _account, entry: closed.append(entry),
        mark_manual_intervention=lambda *_args: None,
        set_protection_state=lambda *_args: None,
        managed_entry_broker_order_ids=lambda _account, _symbol: set(),
        apply_fee_activity=lambda *args: True,
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
    closed: list[str] = []
    owned = ManagedPosition(
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
    broker._client = SimpleNamespace(_request=lambda *_args, **_kwargs: [])
    broker.ledger = None
    broker.manage_external_positions = False
    broker.ledger = SimpleNamespace(
        managed_for_account=lambda _account: {"BTC/USD": owned},
        managed_entries_for_account=lambda _account: [owned],
        mark_position_closed=lambda _account, entry: closed.append(entry),
        mark_manual_intervention=lambda *_args: None,
        set_protection_state=lambda *_args: None,
        managed_entry_broker_order_ids=lambda _account, _symbol: set(),
        apply_fee_activity=lambda *_args, **_kwargs: True,
    )
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


def test_atomic_credentials_preserve_unmanaged_fields_and_never_touch_example(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    env.write_text("UNMANAGED=value\nALPACA_API_KEY=old\n")
    example.write_text("ALPACA_SECRET_KEY=your_secret_key_here\n")
    before = example.read_bytes()
    server._atomic_update_credentials(env, "new-key", "new-secret")
    written = env.read_text()
    assert "UNMANAGED=value" in written
    # Values are quoted so '#'/'=' inside a key or secret cannot corrupt the
    # dotenv file on the next load.
    assert 'ALPACA_API_KEY="new-key"' in written
    assert 'ALPACA_SECRET_KEY="new-secret"' in written
    assert example.read_bytes() == before
    assert os.stat(env).st_mode & 0o777 == 0o600


def test_atomic_credentials_quote_values_with_special_characters(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("")
    server._atomic_update_credentials(env, "key#with=sign", "secret with spaces")
    assert 'ALPACA_API_KEY="key#with=sign"' in env.read_text()
    assert 'ALPACA_SECRET_KEY="secret with spaces"' in env.read_text()


def test_direct_settings_call_cannot_auto_restart_into_live(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = server.EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "running"
    manager._live_armed = True
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "alpaca": {"stock_stream_feed": "iex", "stock_historical_feed": "sip"},
                "runtime": {"mode": "paper", "execution_timeframe": "5m"},
                "symbols": ["AAPL"],
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
    runtime._market_entry_halts = {
        Market.EQUITY: None,
        Market.CRYPTO: "crypto_protection_failure",
    }
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    reconciled: list[bool] = []
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: reconciled.append(True),
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    persisted: list[AccountSnapshot] = []
    runtime._persist_risk_state = persisted.append
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}

    runtime.reset_safety_lock()

    assert reconciled == [True]
    assert runtime._circuit_breaker_reason is None
    assert runtime._state == "running"
    assert persisted == [account]
    # A verified protection pass (the successful broker reconciliation) clears
    # the crypto protection-failure halt; nothing is erased blindly.
    assert runtime._market_entry_halts == {Market.EQUITY: None, Market.CRYPTO: None}


def test_safety_lock_reset_is_granular_per_market_halt() -> None:
    """Global ownership drift + crypto ambiguous exit: resolving ownership
    must NOT clear the crypto halt whose exit state is still unresolved."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {
        Market.EQUITY: None,
        Market.CRYPTO: "ambiguous_crypto_exit",
    }
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: None,  # ownership reconciliation succeeds
        account=lambda: account,
    )
    # A crypto managed exit is still in flight (not provably terminal).
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (
            SimpleNamespace(
                market=Market.CRYPTO,
                exit_client_order_id="exit-1",
                target_state="target_exit_submitted",
                status="open",
            ),
        ),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None  # ownership resolved
    assert runtime._market_entry_halts[Market.EQUITY] is None
    # The ambiguous crypto exit was NOT erased: no terminal-state proof.
    assert runtime._market_entry_halts[Market.CRYPTO] == "ambiguous_crypto_exit"

    # Once the exit is provably terminal (verified reconciliation), a later
    # reset clears the crypto halt too.
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (
            SimpleNamespace(
                market=Market.CRYPTO,
                exit_client_order_id="exit-1",
                target_state="target_exit_filled",
                status="open",
            ),
        ),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.reset_safety_lock()
    assert runtime._market_entry_halts[Market.CRYPTO] is None


def test_safety_lock_reset_retains_unrecognized_halt() -> None:
    """A market halt with no verified recovery path is never erased."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {
        Market.EQUITY: "unknown_halt_reason",
        Market.CRYPTO: None,
    }
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: None,
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}

    runtime.reset_safety_lock()

    assert runtime._circuit_breaker_reason is None
    assert runtime._market_entry_halts[Market.EQUITY] == "unknown_halt_reason"


def test_safety_lock_reset_subscription_halt_needs_resync() -> None:
    """subscription_state_unknown clears only after a successful authoritative
    stream resync; a failing resync keeps the halt active."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {
        Market.EQUITY: "subscription_state_unknown",
        Market.CRYPTO: None,
    }
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: None,
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._resync_pending = True
    runtime._symbols_by_market = {Market.EQUITY: {"SPY"}, Market.CRYPTO: set()}
    resynced: list[bool] = []
    runtime.stream = SimpleNamespace(
        clear_rollback_failure=lambda: None,
        update_subscriptions=lambda stocks, crypto: resynced.append(True),
    )

    runtime.reset_safety_lock()

    assert resynced == [True]
    assert runtime._circuit_breaker_reason is None
    assert runtime._market_entry_halts[Market.EQUITY] is None

    # A resync failure must retain the halt.
    runtime._market_entry_halts = {
        Market.EQUITY: "subscription_state_unknown",
        Market.CRYPTO: None,
    }
    runtime._resync_pending = False

    def failing_resync() -> None:
        raise RuntimeError("socket broken")

    runtime._resync_stream_if_needed = failing_resync
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.reset_safety_lock()
    assert runtime._market_entry_halts[Market.EQUITY] == "subscription_state_unknown"


def test_safety_lock_reset_universe_halt_needs_valid_refresh() -> None:
    """invalid_dynamic_universe clears only when a scanner refresh produces a
    valid snapshot; a failing refresh keeps the halt active."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "ownership_drift"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime._running = True
    runtime._state = "halted"
    runtime._market_entry_halts = {
        Market.EQUITY: "invalid_dynamic_universe",
        Market.CRYPTO: None,
    }
    runtime.market_state = SimpleNamespace(has_data_gaps=False)
    account = AccountSnapshot(10_000, 10_000, 10_000, 0)
    runtime.broker = SimpleNamespace(
        account_id="acct",
        reconcile=lambda: None,
        account=lambda: account,
    )
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (),
        clear_safety_incident=lambda _account, _reason=None: None,
    )
    runtime._persist_risk_state = lambda _account: None
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._resync_pending = False
    cleared: list[Market] = []

    def successful_refresh(market: Market) -> None:
        runtime._market_entry_halts[market] = None
        cleared.append(market)

    runtime._refresh_runtime_universes = successful_refresh
    runtime.reset_safety_lock()
    assert cleared == [Market.EQUITY]
    assert runtime._market_entry_halts[Market.EQUITY] is None

    # A failing refresh leaves the halt in place.
    runtime._market_entry_halts = {
        Market.EQUITY: "invalid_dynamic_universe",
        Market.CRYPTO: None,
    }

    def failing_refresh(market: Market) -> None:
        raise RuntimeError("scanner down")

    runtime._refresh_runtime_universes = failing_refresh
    runtime._circuit_breaker_reason = "reconciliation_failed"
    runtime.reset_safety_lock()
    assert runtime._market_entry_halts[Market.EQUITY] == "invalid_dynamic_universe"


def test_safety_lock_reset_refuses_manual_intervention() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = "protection_failure"
    runtime._events = SimpleNamespace(empty=lambda: True)
    runtime._not_ready_symbols = set()
    runtime._reconcile_lock = threading.Lock()
    runtime.market_state = SimpleNamespace(has_data_gaps=False, entry_halt_reason="locked")
    runtime.broker = SimpleNamespace(account_id="acct", reconcile=lambda: None)
    runtime.ledger = SimpleNamespace(
        managed_entries_for_account=lambda _account: (
            SimpleNamespace(status="manual_intervention_required"),
        ),
        clear_safety_incident=lambda _account, _reason=None: None,
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
    )
    manager._live_request_suffix = "1234"
    manager.confirm_live_arming("LIVE 1234")
    manager._require_valid_live_arm(*credentials)

    credentials[1] = "secret-b"
    with pytest.raises(ValueError, match="fresh confirmation"):
        manager._require_valid_live_arm(*credentials)
    assert not manager._live_armed


def test_fill_activity_recovery_merges_activities_of_one_order(tmp_path: Path) -> None:
    """A single broker order can produce several FILL activities (partial
    fills, fee legs). The offline-recovery path must merge them into ONE
    ledger row instead of colliding on the unique broker_order_id index."""
    from parayabuck.execution.ledger import ExecutionLedger

    ledger = ExecutionLedger(str(tmp_path / "activities.sqlite3"))
    activities = [
        {
            "id": "act-1",
            "order_id": "broker-exit",
            "side": "sell",
            "symbol": "AAPL",
            "qty": "5",
            "price": "101",
            "commission": "0.5",
            "transaction_time": "2026-01-02T15:00:00+00:00",
        },
        {
            "id": "act-2",
            "order_id": "broker-exit",
            "side": "sell",
            "symbol": "AAPL",
            "qty": "5",
            "price": "102",
            "commission": "0.5",
            "transaction_time": "2026-01-02T15:01:00+00:00",
        },
    ]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(_request=lambda *_args, **_kwargs: activities)
    broker.ledger = ledger
    broker._account_id = "acct"

    assert broker._reconcile_fill_activity_fees() is True

    rows = ledger.connection.execute(
        """SELECT client_order_id, broker_order_id, cumulative_filled_qty
           FROM orders WHERE broker_order_id = 'broker-exit'"""
    ).fetchall()
    # One merged row per broker order; the second activity updated it instead
    # of colliding on the unique index.
    assert rows == [("alpaca-fill-activity-broker-exit", "broker-exit", 5.0)]
    fee_rows = ledger.connection.execute("SELECT COUNT(*) FROM fee_activities").fetchone()[0]
    assert fee_rows == 2
    ledger.close()
