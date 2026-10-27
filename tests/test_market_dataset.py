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


def test_dividend_is_credited_exactly_once_and_split_events_are_refused() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 101.0),
        _bar(start + timedelta(days=2), 101.0),
        _bar(start + timedelta(days=3), 102.0),
    )
    result = _engine().run(
        list(bars),
        liquidate_at_end=False,
        corporate_actions=[CorporateAction(start + timedelta(days=3), "SPY", CorporateActionType.DIVIDEND, 1.0)],
    )
    initial_qty = result.fills[0].qty
    assert result.state.holdings["SPY"] == pytest.approx(initial_qty)
    assert result.dividend_total == pytest.approx(initial_qty)
    # The engine refuses SPLIT events on raw bars (T8-6): it rescales quantities
    # but not the price history, so a split is never credited here.
    split = CorporateAction(start + timedelta(days=2), "SPY", CorporateActionType.SPLIT, 2.0)
    with pytest.raises(ValueError, match="refuses SPLIT"):
        _engine().run(list(bars), liquidate_at_end=False, corporate_actions=[split])


def test_split_on_first_fill_bar_is_refused_rather_than_half_sizing_the_buy() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = (
        _bar(start, 100.0),
        _bar(start + timedelta(days=1), 50.0),
        _bar(start + timedelta(days=2), 50.0),
    )
    split = CorporateAction(start + timedelta(days=1), "SPY", CorporateActionType.SPLIT, 2.0)
    with pytest.raises(ValueError, match="refuses SPLIT"):
        _engine().run(list(bars), liquidate_at_end=False, corporate_actions=[split])
