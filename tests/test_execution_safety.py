from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.trading.enums import TimeInForce as AlpacaTIF

from parayabuck.backtest.engine import BacktestEngine
from parayabuck.config import load_config
from parayabuck.data.base import DataSource
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.live_engine import LiveTradingEngine
from parayabuck.execution.paper import PaperBroker
from parayabuck.models import Bar, Order, OrderStatus, OrderType, Position, Side
from parayabuck.risk.manager import RiskManager
from parayabuck.strategy.base import Strategy


def _bar(
    timestamp: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    symbol: str = "AAPL",
) -> Bar:
    return Bar(timestamp, symbol, "15m", open_, high, low, close, 10_000)


def test_crypto_day_order_is_normalized_to_gtc() -> None:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    submitted: list[Any] = []

    class Client:
        def get_asset(self, symbol: str) -> Any:
            return SimpleNamespace(tradable=True, fractionable=True)

        def submit_order(self, request: Any) -> Any:
            submitted.append(request)
            return SimpleNamespace(id="order-1")

        def get_order_by_id(self, order_id: str) -> Any:
            return SimpleNamespace(status="filled", filled_avg_price="50000", filled_qty="0.01")

    broker._client = Client()
    broker._asset_cache = {}
    order = broker.submit_order(Order("BTC/USD", Side.BUY, 0.01))

    assert order.status == OrderStatus.FILLED
    assert submitted[0].time_in_force == AlpacaTIF.GTC
    assert submitted[0].client_order_id.startswith("parayabuck-")


def test_paper_broker_uses_real_cash_accounting_and_limit_triggers() -> None:
    config = load_config()
    config.risk.slippage_cents = 0.0
    broker = PaperBroker(config)
    timestamp = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    broker.update_market_bar(_bar(timestamp, 100, 105, 95, 102))

    unfilled = broker.submit_order(
        Order("AAPL", Side.BUY, 10, order_type=OrderType.LIMIT, limit_price=90)
    )
    assert unfilled.status == OrderStatus.PENDING

    bought = broker.submit_order(Order("AAPL", Side.BUY, 10))
    assert bought.filled_price == pytest.approx(100)
    assert broker.get_account().balance == pytest.approx(9_000)
    assert broker.get_account().equity == pytest.approx(10_000)

    broker.update_market_bar(_bar(timestamp + timedelta(minutes=15), 110, 112, 108, 111))
    sold = broker.submit_order(Order("AAPL", Side.SELL, 10))
    assert sold.filled_price == pytest.approx(110)
    assert broker.get_account().balance == pytest.approx(10_100)
    assert broker.get_account().equity == pytest.approx(10_100)


class _Bars(DataSource):
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> list[Bar]:
        return self.bars if symbol == "AAPL" else []

    def iterate_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> Iterator[Bar]:
        yield from self.get_bars(symbol, timeframe, start, end)

    def get_latest_bar(self, symbol: str) -> Bar | None:
        return self.bars[-1]


class _BuyFirstBar(Strategy):
    name = "buy_first"

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        if self._bars_seen == 0:
            self._bars_seen += 1
            return Order(bar.symbol, Side.BUY, 1)
        self._bars_seen += 1
        return None


def test_backtest_fills_signal_on_next_bar_open() -> None:
    config = load_config()
    config.risk.slippage_cents = 0.0
    start = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    bars = [
        _bar(start, 99, 101, 98, 100),
        _bar(start + timedelta(minutes=15), 110, 112, 109, 111),
    ]
    broker = PaperBroker(config)
    engine = BacktestEngine(config, _Bars(bars), broker, RiskManager(config), benchmark_symbol=None)

    engine.run(_BuyFirstBar(), "AAPL", "15m")

    assert broker.get_account().trades[0].price == pytest.approx(110)


def test_live_engine_rejects_forming_bar() -> None:
    engine = LiveTradingEngine(
        broker=SimpleNamespace(),
        data_source=SimpleNamespace(),
        symbols=[],
        use_global_market_filter=False,
    )
    forming = _bar(datetime.now(UTC) - timedelta(minutes=5), 100, 101, 99, 100)
    closed = _bar(datetime.now(UTC) - timedelta(minutes=20), 100, 101, 99, 100)

    assert not engine._bar_is_closed(forming)
    assert engine._bar_is_closed(closed)
