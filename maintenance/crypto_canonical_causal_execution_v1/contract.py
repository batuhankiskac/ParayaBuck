"""Frozen research-only timing rules for the pinned crypto hourly source."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from parayabuck.core.models import Bar, Market

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def complete_daily_available_at(daily: Bar, components: Sequence[Bar]) -> datetime:
    if daily.market != Market.CRYPTO or daily.timeframe != "1d":
        raise ValueError("Expected a crypto daily bar")
    ordered = sorted(components, key=lambda item: item.timestamp)
    expected = [daily.timestamp + index * HOUR for index in range(24)]
    if len(ordered) != 24 or [bar.timestamp for bar in ordered] != expected:
        raise ValueError("Incomplete daily source components")
    if any(bar.symbol != daily.symbol or bar.market != Market.CRYPTO for bar in ordered):
        raise ValueError("Daily source identity mismatch")
    available_at: datetime = max(bar.timestamp + HOUR for bar in ordered)
    return available_at


def first_causal_hour(hours: Sequence[Bar], *, boundary: datetime, order_created_at: datetime) -> Bar | None:
    """Return one actual hourly opening event later than both known times."""
    eligible = (bar for bar in hours if bar.timestamp > boundary and bar.timestamp > order_created_at)
    return min(eligible, key=lambda bar: bar.timestamp, default=None)
