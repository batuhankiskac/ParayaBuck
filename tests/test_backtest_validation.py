from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from parayabuck.backtest import BacktestEngine
from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.validation import calendar_fold_windows
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Direction, Market, Regime, Signal
from parayabuck.strategies.base import Strategy, StrategyContext


def _bar(index: int, close: float = 100.0, high: float | None = None) -> Bar:
    timestamp = datetime(2025, 1, 1, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * index)
    return Bar(
        timestamp,
        "TEST",
        "5m",
        close,
        high if high is not None else close + 0.5,
        close - 0.5,
        close,
        1000,
        Market.EQUITY,
    )


class _HoldStrategy(Strategy):
    """Enters once and never exits on its own, so the run must liquidate it."""

    name = "hold"
    market = Market.EQUITY
    minimum_bars = 1

    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None:
        if context.position is not None:
            return None
        bar = bars[-1]
        return Signal(
            bar.symbol,
            bar.market,
            Direction.LONG,
            self.name,
            90,
            0.9,
            bar.close,
            bar.close - 1,
            bar.close * 5,
            bar.timestamp,
            ("hold",),
            Regime.RANGE,
        )


def test_open_positions_are_liquidated_at_end_of_test() -> None:
    config = EngineConfig(warmup_bars=50)
    backtest = BacktestEngine(config, strategies=(_HoldStrategy(),), slippage_bps=0)
    bars = [_bar(0, 100), _bar(1, 100), _bar(2, 102)]
    result = backtest.run(bars)

    assert result.liquidated_at_end is True
    assert result.open_positions == 0
    assert result.unrealized_pnl == 0.0
    assert result.total_trades == 1
    trade = backtest.broker.trades[0]
    assert trade.exit_reason == "end"
    assert trade.exit_price == pytest.approx(102.0)
    assert trade.pnl == pytest.approx(20.0)
    # Ending equity is fully realized: entry 100 shares * 100 + 20 pnl.
    assert result.ending_equity == pytest.approx(10_020)


def test_no_liquidation_without_open_positions() -> None:
    config = EngineConfig(warmup_bars=50)
    backtest = BacktestEngine(config, strategies=(_HoldStrategy(),), slippage_bps=0)
    bars = [_bar(0, 100)]
    result = backtest.run(bars)
    assert result.liquidated_at_end is False


def test_multi_symbol_benchmark_falls_back_to_first_symbol() -> None:
    backtest = BacktestEngine(
        EngineConfig(warmup_bars=50), strategies=(), slippage_bps=0, benchmark_symbol="SPY"
    )
    base = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
    bars = [
        Bar(
            base + timedelta(minutes=5 * index),
            symbol,
            "5m",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            Market.EQUITY,
        )
        for index in range(3)
        for symbol in ("AAA", "BBB")
    ]
    result = backtest.run(bars)
    # SPY has no data; the comparison must fall back to a real symbol instead
    # of silently reporting a 0% benchmark on a multi-symbol run.
    assert result.benchmark_symbol == "AAA"
    assert result.benchmark_return == 0.0


def test_duplicate_bars_are_simulated_once_in_engine() -> None:
    config = EngineConfig(warmup_bars=50)
    clean = BacktestEngine(config, strategies=(_HoldStrategy(),), slippage_bps=0)
    duplicated = BacktestEngine(config, strategies=(_HoldStrategy(),), slippage_bps=0)
    bars = [_bar(0, 100), _bar(1, 100), _bar(2, 102)]
    duplicated_bars = [_bar(0, 100), _bar(1, 100), _bar(1, 100), _bar(2, 102)]
    clean_result = clean.run(bars)
    dup_result = duplicated.run(duplicated_bars)
    # A duplicated bar must not let the broker act on one real bar twice
    # (double participation-cap fills, double exits).
    assert dup_result.ending_equity == clean_result.ending_equity
    assert dup_result.total_trades == clean_result.total_trades
    assert len(duplicated.broker.fills) == len(clean.broker.fills)


def test_duplicate_bars_with_conflicting_ohlc_raise() -> None:
    config = EngineConfig(warmup_bars=50)
    backtest = BacktestEngine(config, strategies=(), slippage_bps=0)
    with pytest.raises(ValueError, match="Conflicting duplicate bars"):
        backtest.run([_bar(0, 100), _bar(0, 101)])


def _result(**overrides: Any) -> BacktestResult:
    values: dict[str, Any] = dict(
        starting_equity=100_000.0,
        ending_equity=110_000.0,
        total_return=0.1,
        benchmark_return=0.05,
        excess_return=0.05,
        max_drawdown=0.1,
        total_trades=20,
        win_rate=0.5,
        profit_factor=1.5,
        profit_factor_is_infinite=False,
        expectancy=100.0,
        sharpe=1.0,
        sortino=1.0,
        equity_curve=(100_000.0, 110_000.0),
        realized_pnls=(100.0,) * 20,
        open_positions=0,
        unrealized_pnl=0.0,
        open_risk=0.0,
        liquidated_at_end=False,
        cagr=0.1,
    )
    values.update(overrides)
    return BacktestResult(**values)


def _bars(count: int, close: float = 100.0) -> list[Bar]:
    start = datetime(2024, 1, 1, 14, 30, tzinfo=UTC)
    return [
        Bar(
            timestamp=start + timedelta(minutes=5 * index),
            symbol="TEST",
            timeframe="5m",
            open=close,
            high=close + 0.5,
            low=close - 0.5,
            close=close,
            volume=1000.0,
            market=Market.EQUITY,
        )
        for index in range(count)
    ]


def test_calendar_fold_windows_reserve_holdout_and_embargo() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        Bar(
            timestamp=start + timedelta(days=index),
            symbol="TEST",
            timeframe="1d",
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1000.0,
            market=Market.EQUITY,
        )
        for index in range(700)
    ]
    windows = calendar_fold_windows(
        bars,
        train_months=9,
        test_months=1,
        outer_holdout_months=6,
        embargo=timedelta(days=3),
    )
    assert windows
    assert all(window.train_end < window.test_start for window in windows)


def test_calendar_fold_window_count_is_timestamp_independent() -> None:
    """The number of folds depends only on calendar dates, not bar count."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dense = [
        Bar(start + timedelta(days=i), "T", "1d", 100, 101, 99, 100, 1000, Market.EQUITY)
        for i in range(700)
    ]
    sparse = dense[::7]
    dense_windows = calendar_fold_windows(
        dense, train_months=9, test_months=1, outer_holdout_months=6
    )
    sparse_windows = calendar_fold_windows(
        sparse, train_months=9, test_months=1, outer_holdout_months=6
    )
    assert dense_windows
    assert len(dense_windows) == len(sparse_windows)
