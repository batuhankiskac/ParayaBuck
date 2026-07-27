from __future__ import annotations

import asyncio
import os
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
    ExecutionStatus,
    Market,
    OrderIntent,
    OrderState,
    PositionSnapshot,
    Regime,
    Signal,
)
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.data.buffers import BarMutation, BarRevision, BarStore
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ManagedPosition
from parayabuck.markets.sectors import sector_of
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.portfolio.allocator import PortfolioAllocator
from parayabuck.web import server


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


def test_legacy_crypto_position_blocks_canonical_signal_and_counts_as_crypto() -> None:
    signal = Signal(
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        "test",
        90,
        0.9,
        100,
        95,
        110,
        datetime.now(UTC),
        regime=Regime.RANGE,
    )
    account = AccountSnapshot(
        1_000,
        10_000,
        10_000,
        0,
        positions=(PositionSnapshot("BTCUSD", 0.5, 90, 100, Market.CRYPTO),),
    )
    assert PortfolioAllocator().select([signal], account) == []
    assert sector_of("BTCUSD") == "crypto"
    assert sector_of("AAPL") == "technology"


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
    broker._submit_stop = lambda _symbol, _market, qty, *_args: submitted.append(qty) or True
    broker.reconcile()
    assert submitted == [pytest.approx(0.01)]


def test_execution_status_keeps_partial_fill_when_terminal_state_is_cancelled() -> None:
    status = ExecutionStatus.from_values(2, 0.5, 100, OrderState.CANCELLED)
    assert status.has_any_fill
    assert status.position_opened
    assert status.remaining_qty == 1.5


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
