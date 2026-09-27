from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import (
    CashRateObservation,
    CorporateAction,
    CorporateActionType,
    DatasetLineage,
    MarketDataset,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight


class _HalfInvested(PortfolioStrategy):
    name = "half_invested"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("SPY", 0.5),),
            cash_weight=0.5,
            universe_version="test",
        )


def _bar(timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp,
        "SPY",
        "1d",
        close,
        close,
        close,
        close,
        1_000_000,
        Market.EQUITY,
    )


def _lineage() -> DatasetLineage:
    return DatasetLineage(
        dataset_id="dual-series-test",
        parent_dataset_ids=(),
        provider="test",
        source_feed="fixture",
        adjustment="signal=all;execution=splits",
        downloaded_at=datetime(2025, 1, 1, tzinfo=UTC),
        data_hash="fixture-hash",
    )


def test_dual_series_uses_signal_history_and_executable_fill_prices() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    signal = tuple(_bar(start + timedelta(days=i), 100.0 + i) for i in range(3))
    execution = (
        _bar(start, 50.0),
        _bar(start + timedelta(days=1), 51.0),
        _bar(start + timedelta(days=2), 52.0),
    )
    dataset = MarketDataset(
        Market.EQUITY,
        "1d",
        signal,
        execution,
        _lineage(),
    )
    engine = PortfolioBacktestEngine(
        _HalfInvested(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0.0,
        spread_bps=0.0,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=False)
    assert result.fills[0].reference_price == 51.0
    assert result.benchmark_return == pytest.approx(0.02)
    assert dataset.content_digest() == dataset.content_digest()


def test_dividend_and_split_are_credited_exactly_once() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    signal = tuple(_bar(start + timedelta(days=i), 100.0 + i) for i in range(4))
    execution = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 101.0),
        _bar(start + timedelta(days=2), 50.5),
        _bar(start + timedelta(days=3), 51.0),
    )
    dataset = MarketDataset(
        Market.EQUITY,
        "1d",
        signal,
        execution,
        _lineage(),
        corporate_actions=(
            CorporateAction(start + timedelta(days=2), "SPY", CorporateActionType.SPLIT, 2.0),
            CorporateAction(start + timedelta(days=3), "SPY", CorporateActionType.DIVIDEND, 1.0),
        ),
    )
    engine = PortfolioBacktestEngine(
        _HalfInvested(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0.0,
        spread_bps=0.0,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=False)
    initial_qty = result.fills[0].qty
    assert result.state.holdings["SPY"] == pytest.approx(initial_qty * 2)
    assert result.state.avg_prices["SPY"] == pytest.approx(101.0 / 2)
    assert result.dividend_total == pytest.approx(initial_qty * 2)


def test_split_on_first_fill_bar_rescales_the_resting_buy_order() -> None:
    # The day-0 decision sizes a BUY in pre-split shares; the split's ex-date
    # is the fill bar, before any position exists, so the resting order must
    # be rescaled or it buys half the intended notional at the split open.
    start = datetime(2025, 1, 1, tzinfo=UTC)
    signal = tuple(_bar(start + timedelta(days=i), 100.0) for i in range(3))
    execution = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 50.0),
        _bar(start + timedelta(days=2), 50.0),
    )
    dataset = MarketDataset(
        Market.EQUITY,
        "1d",
        signal,
        execution,
        _lineage(),
        corporate_actions=(CorporateAction(start + timedelta(days=1), "SPY", CorporateActionType.SPLIT, 2.0),),
    )
    engine = PortfolioBacktestEngine(
        _HalfInvested(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0.0,
        spread_bps=0.0,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=False)
    assert result.fills[0].timestamp == start + timedelta(days=1)
    assert result.fills[0].qty == pytest.approx(1_000.0)
    assert result.state.holdings["SPY"] == pytest.approx(1_000.0)


def test_market_dataset_rejects_missing_execution_symbol() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    qqq = Bar(start, "QQQ", "1d", 100, 100, 100, 100, 1_000, Market.EQUITY)
    with pytest.raises(ValueError, match="No executable series"):
        MarketDataset(
            Market.EQUITY,
            "1d",
            (qqq,),
            (_bar(start, 100.0),),
            _lineage(),
        )


def test_cash_hurdle_uses_point_in_time_rate_series() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = tuple(_bar(start + timedelta(days=index), 100.0) for index in range(366))
    dataset = MarketDataset(
        Market.EQUITY,
        "1d",
        bars,
        bars,
        _lineage(),
        cash_rates=(CashRateObservation(start, 0.10),),
    )
    engine = PortfolioBacktestEngine(
        _HalfInvested(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0,
        spread_bps=0,
    )
    result = engine.run_dataset(dataset, liquidate_at_end=False)
    assert result.cash_hurdle_return == pytest.approx(0.10, rel=0.01)
    assert result.cash_yield > 0
    without_rate = MarketDataset(Market.EQUITY, "1d", bars, bars, _lineage())
    assert dataset.content_digest() != without_rate.content_digest()
