from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData


def _bar(timestamp: datetime, market: Market) -> Bar:
    return Bar(timestamp, "BTC/USD" if market == Market.CRYPTO else "AAPL", "5m", 100, 101, 99, 100)


def test_equity_historical_filter_accepts_only_regular_session() -> None:
    assert AlpacaHistoricalData._is_regular_equity_timestamp(
        datetime(2026, 7, 2, 13, 30, tzinfo=UTC)
    )
    assert not AlpacaHistoricalData._is_regular_equity_timestamp(
        datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    )
    assert not AlpacaHistoricalData._is_regular_equity_timestamp(
        datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )


def test_warmup_cache_requires_recent_market_data() -> None:
    now = datetime.now(UTC)
    assert AlpacaHistoricalData._cache_is_fresh(
        [_bar(now - timedelta(hours=2), Market.CRYPTO)],
        Market.CRYPTO,
        "5m",
        now,
    )
    assert not AlpacaHistoricalData._cache_is_fresh(
        [_bar(now - timedelta(hours=4), Market.CRYPTO)],
        Market.CRYPTO,
        "5m",
        now,
    )
    assert not AlpacaHistoricalData._cache_is_fresh(
        [_bar(now - timedelta(days=5), Market.EQUITY)],
        Market.EQUITY,
        "5m",
        now,
    )
