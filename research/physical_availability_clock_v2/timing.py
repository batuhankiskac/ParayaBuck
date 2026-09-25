"""Research-only physical completion rules; never changes canonical Bar.timestamp."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

import exchange_calendars as xcals
import pandas as pd

from parayabuck.core.models import Bar, Market

_CALENDAR = xcals.get_calendar("XNYS", start="2016-01-01", end="2026-12-31")
_HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class ReplayObservation:
    bar: Bar
    label_time: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        if self.bar.timestamp != self.label_time or self.available_at < self.label_time:
            raise ValueError("Observation label/availability mismatch")


@dataclass(frozen=True)
class PhysicalReplayTime:
    market: Market
    bar: Bar
    label_time: datetime
    available_at: datetime
    decision_at: datetime
    order_created_at: datetime
    execution_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.bar.market != self.market or self.bar.timestamp != self.label_time:
            raise ValueError("Physical clock record does not match its source bar")
        if not self.label_time < self.available_at <= self.decision_at <= self.order_created_at:
            raise ValueError("Physical decision/order precedes source availability")
        if self.execution_at is not None and self.execution_at <= self.order_created_at:
            raise ValueError("New order cannot use an earlier or same-boundary price event")


def equity_daily_available_at(session_date: date) -> datetime:
    session = pd.Timestamp(session_date)
    if not _CALENDAR.is_session(session):
        raise ValueError(f"Not an XNYS session: {session_date}")
    return cast(datetime, _CALENDAR.session_close(session).to_pydatetime()).astimezone(UTC)


def equity_session_open(session_date: date) -> datetime:
    session = pd.Timestamp(session_date)
    if not _CALENDAR.is_session(session):
        raise ValueError(f"Not an XNYS session: {session_date}")
    return cast(datetime, _CALENDAR.session_open(session).to_pydatetime()).astimezone(UTC)


def equity_action_effective_open(ex_date: date) -> datetime:
    """Match the canonical queue: a non-session ex-date applies at the next open."""
    session = _CALENDAR.date_to_session(pd.Timestamp(ex_date), direction="next")
    return cast(datetime, _CALENDAR.session_open(session).to_pydatetime()).astimezone(UTC)


def equity_next_execution_open(session_date: date) -> datetime:
    session = pd.Timestamp(session_date)
    if not _CALENDAR.is_session(session):
        raise ValueError(f"Not an XNYS session: {session_date}")
    next_session = _CALENDAR.next_session(session)
    result = cast(datetime, _CALENDAR.session_open(next_session).to_pydatetime()).astimezone(UTC)
    if result <= equity_daily_available_at(session_date):
        raise AssertionError("Next execution open must follow completed daily bar")
    return result


def crypto_hourly_available_at(bar: Bar) -> datetime:
    if bar.market != Market.CRYPTO or bar.timeframe != "1h":
        raise ValueError("Expected a crypto source hourly bar")
    if bar.timestamp.tzinfo is None or bar.timestamp.minute or bar.timestamp.second or bar.timestamp.microsecond:
        raise ValueError("Expected an aware hour-start label")
    result: datetime = bar.timestamp + _HOUR
    return result


def crypto_daily_observation(daily_bar: Bar, components: list[Bar]) -> ReplayObservation:
    if daily_bar.market != Market.CRYPTO or daily_bar.timeframe != "1d":
        raise ValueError("Expected a crypto daily aggregate")
    ordered = sorted(components, key=lambda bar: bar.timestamp)
    expected = [daily_bar.timestamp + i * _HOUR for i in range(24)]
    if len(ordered) != 24 or [bar.timestamp for bar in ordered] != expected:
        raise ValueError("Incomplete or noncontiguous daily components")
    if any(bar.symbol != daily_bar.symbol for bar in ordered):
        raise ValueError("Daily components have a different symbol")
    return ReplayObservation(
        bar=daily_bar,
        label_time=daily_bar.timestamp,
        available_at=max(crypto_hourly_available_at(bar) for bar in ordered),
    )
