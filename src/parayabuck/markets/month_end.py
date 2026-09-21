"""Exchange-calendar month-end decision and first-open execution contract."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from pandas_market_calendars import get_calendar

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol

_NEW_YORK = ZoneInfo("America/New_York")


class FirstOpenUnavailableError(ValueError):
    """The requested symbol cannot execute at the contracted first open."""


@dataclass(frozen=True)
class MonthEndFirstOpen:
    """One completed month-end decision and its next-month first open."""

    decision_session: date
    decision_timestamp: datetime
    fill_session: date
    fill_timestamp: datetime

    def __post_init__(self) -> None:
        if self.decision_timestamp.tzinfo is None or self.fill_timestamp.tzinfo is None:
            raise ValueError("Month-end timestamps must be timezone-aware")
        if self.decision_timestamp >= self.fill_timestamp:
            raise ValueError("Month-end decision must precede first-open execution")


def _next_month(year: int, month: int) -> tuple[int, int]:
    if not 1 <= month <= 12:
        raise ValueError("month must be in 1..12")
    return (year + 1, 1) if month == 12 else (year, month + 1)


@cache
def month_end_first_open(
    year: int,
    month: int,
    calendar_name: str = "NYSE",
) -> MonthEndFirstOpen:
    """Return the final close of month M and first open of month M+1.

    The exchange schedule, rather than weekday arithmetic or observed symbol
    rows, owns both boundaries. This includes holidays and shortened sessions.
    """
    next_year, next_month = _next_month(year, month)
    following_year, following_month = _next_month(next_year, next_month)
    start = date(year, month, 1)
    next_start = date(next_year, next_month, 1)
    following_start = date(following_year, following_month, 1)

    schedule = get_calendar(calendar_name).schedule(
        start_date=start,
        end_date=following_start - timedelta(days=1),
    )
    decision_rows = schedule[(schedule.index.date >= start) & (schedule.index.date < next_start)]
    fill_rows = schedule[(schedule.index.date >= next_start) & (schedule.index.date < following_start)]
    if decision_rows.empty or fill_rows.empty:
        raise ValueError(f"{calendar_name} schedule has no required sessions for {year:04d}-{month:02d}")

    decision_row = decision_rows.iloc[-1]
    fill_row = fill_rows.iloc[0]
    decision_session = decision_rows.index[-1].date()
    fill_session = fill_rows.index[0].date()
    decision_timestamp = decision_row["market_close"].to_pydatetime().astimezone(UTC)
    fill_timestamp = fill_row["market_open"].to_pydatetime().astimezone(UTC)
    return MonthEndFirstOpen(
        decision_session=decision_session,
        decision_timestamp=decision_timestamp,
        fill_session=fill_session,
        fill_timestamp=fill_timestamp,
    )


def require_first_open_bar(
    window: MonthEndFirstOpen,
    bars: Iterable[Bar],
    symbol: str,
) -> Bar:
    """Return exactly the symbol's first-open bar or fail without rollover.

    A later tradable bar cannot satisfy this contract. A non-positive-open row
    is treated as unavailable. The fill session's completed daily volume is
    deliberately not inspected because it is unknown at that session's open.
    """
    requested = canonical_symbol(symbol, Market.EQUITY)
    matching = tuple(
        bar
        for bar in bars
        if bar.market == Market.EQUITY
        and bar.timeframe == "1d"
        and canonical_symbol(bar.symbol, Market.EQUITY) == requested
        and bar.timestamp.astimezone(_NEW_YORK).date() == window.fill_session
    )
    if len(matching) == 1 and matching[0].open > 0:
        return matching[0]
    raise FirstOpenUnavailableError(f"{requested} unavailable at first open {window.fill_session.isoformat()}")


__all__ = [
    "FirstOpenUnavailableError",
    "MonthEndFirstOpen",
    "month_end_first_open",
    "require_first_open_bar",
]
