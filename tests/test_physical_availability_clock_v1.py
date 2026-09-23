from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from research.physical_availability_clock_v1.timing import (
    crypto_daily_observation,
    crypto_hourly_available_at,
    equity_daily_available_at,
    equity_next_execution_open,
)


def _bar(timestamp: datetime, timeframe: str = "1h") -> Bar:
    return Bar(timestamp, "BTC/USD", timeframe, 1.0, 2.0, 0.5, 1.5, 10.0, Market.CRYPTO)


def test_equity_session_calendar_dst_weekend_holiday_and_early_close() -> None:
    assert equity_daily_available_at(date(2016, 3, 11)) == datetime(2016, 3, 11, 21, tzinfo=UTC)
    assert equity_daily_available_at(date(2016, 3, 14)) == datetime(2016, 3, 14, 20, tzinfo=UTC)
    assert equity_next_execution_open(date(2016, 3, 11)) == datetime(2016, 3, 14, 13, 30, tzinfo=UTC)
    assert equity_daily_available_at(date(2016, 11, 25)) == datetime(2016, 11, 25, 18, tzinfo=UTC)
    assert equity_next_execution_open(date(2024, 7, 3)) == datetime(2024, 7, 5, 13, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="Not an XNYS session"):
        equity_daily_available_at(date(2024, 7, 4))


def test_crypto_component_completion_and_incomplete_day() -> None:
    start = datetime(2021, 4, 1, tzinfo=UTC)
    components = [_bar(start + i * timedelta(hours=1)) for i in range(24)]
    daily = _bar(start, "1d")
    assert crypto_hourly_available_at(components[-1]) == datetime(2021, 4, 2, tzinfo=UTC)
    observation = crypto_daily_observation(daily, components)
    assert observation.label_time == start
    assert observation.available_at == datetime(2021, 4, 2, tzinfo=UTC)
    assert observation.bar is daily
    with pytest.raises(ValueError, match="Incomplete"):
        crypto_daily_observation(daily, components[:-1])
    with pytest.raises(ValueError, match="Incomplete"):
        crypto_daily_observation(daily, components[:12] + components[13:])


def test_crypto_weekend_completion() -> None:
    saturday = datetime(2021, 4, 3, tzinfo=UTC)
    observation = crypto_daily_observation(
        _bar(saturday, "1d"), [_bar(saturday + i * timedelta(hours=1)) for i in range(24)]
    )
    assert observation.available_at == datetime(2021, 4, 4, tzinfo=UTC)
