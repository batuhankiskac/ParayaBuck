"""Corporate-action engine behavior: dividends/splits credit exactly once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
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


def _engine() -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        _HalfInvested(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0.0,
        spread_bps=0.0,
    )


def test_dividend_and_split_are_credited_exactly_once() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 101.0),
        _bar(start + timedelta(days=2), 50.5),
        _bar(start + timedelta(days=3), 51.0),
    )
    result = _engine().run(
        list(bars),
        liquidate_at_end=False,
        corporate_actions=[
            CorporateAction(start + timedelta(days=2), "SPY", CorporateActionType.SPLIT, 2.0),
            CorporateAction(start + timedelta(days=3), "SPY", CorporateActionType.DIVIDEND, 1.0),
        ],
    )
    initial_qty = result.fills[0].qty
    assert result.state.holdings["SPY"] == pytest.approx(initial_qty * 2)
    assert result.state.avg_prices["SPY"] == pytest.approx(101.0 / 2)
    assert result.dividend_total == pytest.approx(initial_qty * 2)


def test_split_on_first_fill_bar_rescales_the_resting_buy_order() -> None:
    # The day-0 decision sizes a BUY in pre-split shares; the split's ex-date
    # is the fill bar, before any position exists, so the resting order must
    # be rescaled or it buys half the intended notional at the split open.
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 50.0),
        _bar(start + timedelta(days=2), 50.0),
    )
    result = _engine().run(
        list(bars),
        liquidate_at_end=False,
        corporate_actions=[CorporateAction(start + timedelta(days=1), "SPY", CorporateActionType.SPLIT, 2.0)],
    )
    assert result.fills[0].timestamp == start + timedelta(days=1)
    assert result.fills[0].qty == pytest.approx(1_000.0)
    assert result.state.holdings["SPY"] == pytest.approx(1_000.0)
