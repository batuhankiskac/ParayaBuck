"""Physical replay calendar and event ordering regression checks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from research.physical_availability_clock_v2.timing import (
    PhysicalReplayTime,
    crypto_daily_observation,
    equity_action_effective_open,
    equity_daily_available_at,
    equity_next_execution_open,
    equity_session_open,
)


def crypto_bar(at: datetime, timeframe: str) -> Bar:
    return Bar(at, "BTC/USD", timeframe, 1.0, 1.0, 1.0, 1.0, 1.0, Market.CRYPTO)


def test_xnys_dst_holiday_and_early_close() -> None:
    assert equity_daily_available_at(date(2016, 3, 11)) == datetime(2016, 3, 11, 21, tzinfo=UTC)
    assert equity_daily_available_at(date(2016, 3, 14)) == datetime(2016, 3, 14, 20, tzinfo=UTC)
    assert equity_daily_available_at(date(2016, 11, 25)) == datetime(2016, 11, 25, 18, tzinfo=UTC)
    assert equity_daily_available_at(date(2024, 7, 3)) == datetime(2024, 7, 3, 17, tzinfo=UTC)
    assert equity_next_execution_open(date(2024, 7, 3)) == equity_session_open(date(2024, 7, 5))
    with pytest.raises(ValueError, match="Not an XNYS session"):
        equity_daily_available_at(date(2024, 7, 4))
    assert equity_action_effective_open(date(2017, 5, 29)) == equity_session_open(date(2017, 5, 30))


def test_complete_crypto_day_and_strict_new_order_price_event() -> None:
    label = datetime(2021, 4, 1, tzinfo=UTC)
    daily = crypto_bar(label, "1d")
    hours = [crypto_bar(label + timedelta(hours=index), "1h") for index in range(24)]
    observed = crypto_daily_observation(daily, hours)
    assert observed.available_at == datetime(2021, 4, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="Incomplete"):
        crypto_daily_observation(daily, hours[:-1])
    with pytest.raises(ValueError, match="earlier or same-boundary"):
        PhysicalReplayTime(
            Market.CRYPTO,
            daily,
            label,
            observed.available_at,
            observed.available_at,
            observed.available_at,
            observed.available_at,
        )
    record = PhysicalReplayTime(
        Market.CRYPTO,
        daily,
        label,
        observed.available_at,
        observed.available_at,
        observed.available_at,
        observed.available_at + timedelta(hours=1),
    )
    assert record.execution_at > record.order_created_at
