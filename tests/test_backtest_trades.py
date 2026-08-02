from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.core.models import Bar, Direction, Market, OrderIntent


def _bar(
    offset: int,
    *,
    symbol: str = "AAPL",
    open: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
) -> Bar:
    timestamp = datetime(2025, 1, 1, 14, 30, tzinfo=UTC) + timedelta(days=offset)
    return Bar(timestamp, symbol, "1d", open, high, low, close, 1000, Market.EQUITY)


def _order(
    *,
    stop: float = 98.0,
    target: float = 105.0,
    strategy: str = "test_strategy",
    qty: float = 10.0,
) -> OrderIntent:
    return OrderIntent(
        signal_id="sig-1",
        symbol="AAPL",
        market=Market.EQUITY,
        direction=Direction.LONG,
        qty=qty,
        entry_price=100.0,
        stop_price=stop,
        target_price=target,
        strategy=strategy,
        created_at=datetime(2024, 12, 31, 14, 30, tzinfo=UTC),
    )


def test_broker_records_target_exit_with_mae_mfe() -> None:
    broker = BacktestBroker(10_000.0)
    broker.submit(_order(target=102.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=102.5, low=98.5, close=100.0))
    assert len(broker.trades) == 1
    trade = broker.trades[0]
    assert trade.symbol == "AAPL"
    assert trade.strategy == "test_strategy"
    assert trade.direction == Direction.LONG
    assert trade.exit_reason == "target"
    assert trade.qty == 10
    assert trade.exit_price == pytest.approx(101.9796, abs=1e-3)
    assert trade.entry_time == datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
    assert trade.exit_time == datetime(2025, 1, 2, 14, 30, tzinfo=UTC)
    assert trade.holding_days == 1.0
    assert trade.pnl > 0
    assert trade.mae_pct == pytest.approx(0.0152, abs=1e-4)  # low 98.5 vs 100.02 entry
    assert trade.mfe_pct == pytest.approx(0.0248, abs=1e-4)  # high 102.5 vs entry
    assert trade.return_pct == pytest.approx(0.019796, abs=1e-3)


def test_broker_records_stop_exit_when_both_hit_conservatively() -> None:
    broker = BacktestBroker(10_000.0)
    broker.submit(_order(target=102.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=102.5, low=97.0, close=100.0))
    trade = broker.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.pnl < 0
    assert trade.exit_price < 98.0  # stop 98 minus slippage
    assert trade.mae_pct == pytest.approx(0.0302, abs=1e-4)
    assert trade.mfe_pct == pytest.approx(0.0248, abs=1e-4)


def test_mae_mfe_ignore_entry_bar_extremes() -> None:
    broker = BacktestBroker(10_000.0)
    broker.submit(_order(target=104.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=101.0, low=99.5, close=100.0))
    broker.advance(_bar(2, open=100.0, high=105.0, low=99.5, close=100.0))
    trade = broker.trades[0]
    assert trade.exit_reason == "target"
    assert trade.mfe_pct == pytest.approx(0.0498, abs=1e-4)
    # Bar 0's low 99 (= 1% adverse) must not count; only bar 1-2 lows do.
    assert trade.mae_pct == pytest.approx(0.0052, abs=1e-4)


def test_strategy_name_propagated() -> None:
    broker = BacktestBroker(10_000.0)
    broker.submit(_order(strategy="stock_trend_pullback"))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=106.0, low=99.0, close=100.0))
    assert broker.trades[0].strategy == "stock_trend_pullback"


def test_rejected_order_creates_no_trade() -> None:
    broker = BacktestBroker(100.0)
    broker.submit(_order(qty=10.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    assert broker.positions == {}
    assert broker.trades == []
    assert "insufficient_cash" in broker.order_rejections.values()


def test_trade_dict_is_json_serializable() -> None:
    import json

    broker = BacktestBroker(10_000.0)
    broker.submit(_order(target=102.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=102.5, low=98.5, close=100.0))
    payload = broker.trades[0].to_dict()
    round_trip = json.loads(json.dumps(payload))
    assert round_trip["symbol"] == "AAPL"
    assert round_trip["exit_reason"] == "target"


def test_participation_cap_produces_partial_entry_fill() -> None:
    broker = BacktestBroker(20_000.0, max_entry_participation_pct=0.02)
    broker.submit(_order(qty=50.0))  # bar volume 1000 -> max 20 per bar
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(1, open=100.0, high=101.0, low=99.0, close=100.0))
    broker.advance(_bar(2, open=100.0, high=101.0, low=99.0, close=100.0))
    position = broker.positions["AAPL"]
    assert position.qty == pytest.approx(50.0)
    assert position.avg_price == pytest.approx(100.02)  # same fill price each bar
    assert len(broker.fills) == 3


def test_participation_cap_produces_partial_stop_exit() -> None:
    broker = BacktestBroker(20_000.0, max_exit_participation_pct=0.02)
    broker.submit(_order(stop=98.0, target=105.0, qty=100.0))
    broker.advance(_bar(0, open=100.0, high=101.0, low=99.0, close=100.0))
    # Bar volume 1000 * 0.02 = 20 -> only 20 of 100 can exit at the stop.
    broker.advance(_bar(1, open=100.0, high=102.5, low=97.0, close=100.0))
    assert broker.positions["AAPL"].qty == pytest.approx(80.0)
    assert len(broker.trades) == 1
    assert broker.trades[0].qty == pytest.approx(20.0)
    assert broker.trades[0].exit_reason == "stop"
    # 20-share partial stop exits continue on every subsequent bar until flat.
    for offset in range(2, 6):
        broker.advance(_bar(offset, open=96.0, high=96.5, low=95.0, close=96.0))
    assert "AAPL" not in broker.positions
    assert len(broker.trades) == 5  # 20-share partial stops until fully out
    assert all(trade.exit_reason == "stop" for trade in broker.trades)


def test_zero_volume_bar_blocks_entry_fill() -> None:
    broker = BacktestBroker(10_000.0)
    broker.submit(_order(qty=10.0))
    no_volume = _bar(0, open=100.0, high=101.0, low=99.0, close=100.0)
    no_volume = Bar(
        no_volume.timestamp, no_volume.symbol, no_volume.timeframe,
        no_volume.open, no_volume.high, no_volume.low, no_volume.close,
        0.0, no_volume.market,
    )
    broker.advance(no_volume)
    assert broker.positions == {}
    assert broker.pending
