from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.markets.month_end import (
    FirstOpenUnavailableError,
    month_end_first_open,
    require_first_open_bar,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight

NEW_YORK = ZoneInfo("America/New_York")


def _bar(
    session: date,
    *,
    symbol: str = "AAA",
    volume: float = 1_000_000.0,
    open_price: float = 100.0,
) -> Bar:
    timestamp = datetime(session.year, session.month, session.day, tzinfo=NEW_YORK)
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=open_price,
        high=101.0,
        low=min(99.0, open_price),
        close=100.0,
        volume=volume,
        market=Market.EQUITY,
    )


@pytest.mark.parametrize(
    ("year", "month", "decision_session", "decision_utc", "fill_session", "fill_utc"),
    (
        # Normal weekday transition.
        (
            2024,
            1,
            date(2024, 1, 31),
            datetime(2024, 1, 31, 21, tzinfo=UTC),
            date(2024, 2, 1),
            datetime(2024, 2, 1, 14, 30, tzinfo=UTC),
        ),
        # Friday month-end and a Saturday start to the next calendar month.
        (
            2024,
            5,
            date(2024, 5, 31),
            datetime(2024, 5, 31, 20, tzinfo=UTC),
            date(2024, 6, 3),
            datetime(2024, 6, 3, 13, 30, tzinfo=UTC),
        ),
        # Sunday start to the next calendar month.
        (
            2021,
            7,
            date(2021, 7, 30),
            datetime(2021, 7, 30, 20, tzinfo=UTC),
            date(2021, 8, 2),
            datetime(2021, 8, 2, 13, 30, tzinfo=UTC),
        ),
        # The first weekday of the next month is the NYSE Labor Day holiday.
        (
            2024,
            8,
            date(2024, 8, 30),
            datetime(2024, 8, 30, 20, tzinfo=UTC),
            date(2024, 9, 3),
            datetime(2024, 9, 3, 13, 30, tzinfo=UTC),
        ),
        # December-to-January year transition; New Year's Day is closed.
        (
            2024,
            12,
            date(2024, 12, 31),
            datetime(2024, 12, 31, 21, tzinfo=UTC),
            date(2025, 1, 2),
            datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
        ),
        # Leap-year February.
        (
            2024,
            2,
            date(2024, 2, 29),
            datetime(2024, 2, 29, 21, tzinfo=UTC),
            date(2024, 3, 1),
            datetime(2024, 3, 1, 14, 30, tzinfo=UTC),
        ),
        # Shortened post-Thanksgiving final session.
        (
            2024,
            11,
            date(2024, 11, 29),
            datetime(2024, 11, 29, 18, tzinfo=UTC),
            date(2024, 12, 2),
            datetime(2024, 12, 2, 14, 30, tzinfo=UTC),
        ),
    ),
)
def test_nyse_month_end_first_open_calendar_contract(
    year: int,
    month: int,
    decision_session: date,
    decision_utc: datetime,
    fill_session: date,
    fill_utc: datetime,
) -> None:
    window = month_end_first_open(year, month)

    assert window.decision_session == decision_session
    assert window.decision_timestamp == decision_utc
    assert window.fill_session == fill_session
    assert window.fill_timestamp == fill_utc
    assert window.decision_timestamp < window.fill_timestamp


def test_first_open_bar_must_exist_for_the_requested_symbol() -> None:
    window = month_end_first_open(2024, 1)
    bars = (
        _bar(date(2024, 2, 1), symbol="OTHER"),
        _bar(date(2024, 2, 2), symbol="AAA"),
    )

    with pytest.raises(FirstOpenUnavailableError, match="AAA.*2024-02-01"):
        require_first_open_bar(window, bars, "AAA")


def test_nonpositive_open_fails_closed_without_later_fallback() -> None:
    window = month_end_first_open(2024, 1)
    bars = (
        _bar(date(2024, 2, 1), open_price=0.0),
        _bar(date(2024, 2, 2), volume=1_000_000.0),
    )

    with pytest.raises(FirstOpenUnavailableError, match="AAA.*2024-02-01"):
        require_first_open_bar(window, bars, "AAA")


def test_completed_fill_day_volume_is_not_used_at_the_open() -> None:
    window = month_end_first_open(2024, 1)
    first = _bar(date(2024, 2, 1), volume=0.0)

    assert require_first_open_bar(window, (first,), "AAA") is first


def test_exact_first_open_is_selected_and_following_session_is_not_used() -> None:
    window = month_end_first_open(2024, 1)
    first = _bar(date(2024, 2, 1))
    following = _bar(date(2024, 2, 2))

    selected = require_first_open_bar(window, (following, first), "AAA")

    assert selected is first
    assert selected.timestamp.astimezone(NEW_YORK).date() == window.fill_session
    assert window.fill_timestamp == datetime(2024, 2, 1, 14, 30, tzinfo=UTC)


class _LegacyMonthlyStrategy(PortfolioStrategy):
    name = "legacy_monthly_defect_fixture"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        invested = 0.5 if context.decision_timestamp.month == 2 else 0.0
        weights = (TargetWeight("AAA", invested),) if invested else ()
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - invested,
            universe_version="synthetic",
        )


def test_documents_legacy_monthly_path_decides_february_first_and_fills_february_second() -> None:
    """Pre-fix evidence: the legacy monthly path contains the extra session.

    This test deliberately characterizes the old path. The corrected reusable
    primitive above is separate so historical strategy identities are not
    silently reinterpreted by this maintenance change.
    """
    bars = [
        _bar(date(2024, 1, 30)),
        _bar(date(2024, 1, 31)),
        _bar(date(2024, 2, 1)),
        _bar(date(2024, 2, 2)),
    ]
    engine = PortfolioBacktestEngine(
        _LegacyMonthlyStrategy(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        slippage_bps=0.0,
        spread_bps=0.0,
        allow_static_universe=True,
    )

    result = engine.run(bars, liquidate_at_end=False)

    february_target = next(target for target in engine._rebalances if target.decision_timestamp.month == 2)
    february_fill = next(fill for fill in result.fills if fill.decision_timestamp.month == 2)
    assert february_target.decision_timestamp.astimezone(NEW_YORK).date() == date(2024, 2, 1)
    assert february_fill.timestamp.astimezone(NEW_YORK).date() == date(2024, 2, 2)
