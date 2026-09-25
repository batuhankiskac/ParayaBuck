from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from parayabuck.runtime import EngineRuntime, WorkerHealth

_UNIVERSE = tuple(f"SYM{i:03d}" for i in range(6))


class _CountingBroker:
    def __init__(self) -> None:
        self.account_calls = 0
        self.submitted: list[object] = []

    def account(self) -> AccountSnapshot:
        self.account_calls += 1
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0)

    def asset_trading_rules(self, symbol: str, market: Market):
        from parayabuck.execution.portfolio import AssetTradingRules

        return AssetTradingRules(symbol, market, 0.01, 0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def submit_portfolio_order(self, order) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _strategy() -> EquityLeveragedTrendV1:
    """A real product strategy with a reduced SMA window so the 220-bar
    fixture satisfies warmup and produces a target on every daily batch."""
    return EquityLeveragedTrendV1({"signal_symbol": _UNIVERSE[0], "hold_symbol": _UNIVERSE[1], "sma_bars": 5})


def _bars(start: datetime, count: int) -> list[Bar]:
    bars: list[Bar] = []
    for index in range(count):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(_UNIVERSE):
            close = 100 * (1 + 0.0003 + offset * 0.00001) ** (index + 1)
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close,
                    close,
                    close,
                    close,
                    1_000_000,
                    Market.EQUITY,
                )
            )
    return bars


def _engine(tmp_path: Path, broker: _CountingBroker) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._state = "running"
    runtime._running = True
    now = datetime.now(UTC)
    runtime._workers = {
        "pb-decisions": WorkerHealth(now, now, alive=True),
        "pb-trade-updates": WorkerHealth(now, now, alive=True),
        "pb-account": WorkerHealth(now, now, alive=True),
        "pb-stocks": WorkerHealth(now, now, alive=True),
        "pb-crypto": WorkerHealth(now, now, alive=True),
    }
    runtime._events = SimpleNamespace(qsize=lambda: 0, empty=lambda: True)
    runtime._last_account_at = datetime.now(UTC)
    runtime._last_account_snapshot = broker.account()
    runtime._last_account_snapshot_at = datetime.now(UTC)
    runtime._oldest_received_at = None
    runtime._dropped_events = 0
    runtime._coalesced_events = 0
    runtime.symbols = list(_UNIVERSE)
    runtime._symbols_by_market = {Market.EQUITY: set(_UNIVERSE), Market.CRYPTO: set()}
    runtime._market_enabled = {Market.EQUITY: True, Market.CRYPTO: False}
    runtime._strategy_enabled = {Market.EQUITY: True, Market.CRYPTO: False}
    runtime._not_ready_symbols = set()
    runtime._circuit_breaker_reason = None
    runtime.config = SimpleNamespace(
        account_poll_interval_seconds=15.0,
        maximum_account_age_for_entry_seconds=30.0,
        maximum_queue_age_seconds=30.0,
    )
    runtime._universe_refresh_status = {"equity": "success", "crypto": "success"}
    runtime._last_universe_refresh_error = {}
    runtime._market_entry_halts = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._last_reconciliation_at = None
    runtime._last_universe_refresh_at = None
    runtime._next_universe_refresh_at = None
    runtime.strategy_status = "configured"
    runtime._strategy_specs = ()
    runtime._portfolio_repository = None
    runtime.ledger = None
    runtime.broker = SimpleNamespace(account_id="acct", account=broker.account)
    runtime.market_state = SimpleNamespace(has_data_gaps=False, has_data_gaps_for_market=lambda market: False)
    runtime._managed_pin_symbols = set()
    runtime._last_portfolio_status = {}
    runtime._universe_retry_at = {}
    runtime._universe_retry_attempt = {}
    runtime._portfolio_lock = threading.RLock()
    runtime._state_lock = threading.RLock()
    runtime._warming_up = False
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    runtime._portfolio_runtimes = (
        PortfolioRuntime(
            _strategy(),
            _UNIVERSE,
            broker,
            repository,
            mode="paper",
            maximum_allocation=0.50,
            allow_static_universe=True,
        ),
    )
    return runtime


def test_decision_cycle_issues_at_most_one_broker_account_fetch(tmp_path) -> None:
    """The decision path must not re-fetch the full broker account per batch
    while a fresh snapshot is available: one fetch per cycle (or zero when
    the account loop's snapshot is reused), never one per market per batch."""
    broker = _CountingBroker()
    engine = _engine(tmp_path, broker)
    runtime = engine._portfolio_runtimes[0]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    runtime.seed(_bars(start, 220))
    broker.account_calls = 0
    for day in range(10):
        batch = [bar for bar in _bars(start + timedelta(days=221 + day), 1) if bar.market == Market.EQUITY]
        # The account loop refreshed the snapshot at its last poll.
        engine._last_account_at = datetime.now(UTC)
        engine._process_portfolio_timestamp(batch)
    assert broker.account_calls == 0
    # Reconciliation keeps _last_account_at fresh without refreshing the
    # snapshot; the stale snapshot then crosses the freshness boundary and
    # exactly one fresh fetch happens for the whole cycle, shared by every
    # market in the batch.
    engine._last_account_at = datetime.now(UTC)
    engine._last_account_snapshot_at = datetime.now(UTC) - timedelta(seconds=60)
    engine._process_portfolio_timestamp(
        [bar for bar in _bars(start + timedelta(days=231), 1) if bar.market == Market.EQUITY]
    )
    assert broker.account_calls == 1
