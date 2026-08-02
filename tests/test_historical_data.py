from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

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


def test_splits_adjustment_maps_to_sdk_split(monkeypatch: Any) -> None:
    import parayabuck.data.alpaca as alpaca_module

    recorded: dict[str, Any] = {}

    class FakeStockBarsRequest:
        def __init__(self, **kwargs: Any) -> None:
            recorded["adjustment"] = kwargs["adjustment"]

    monkeypatch.setattr(alpaca_module, "StockBarsRequest", FakeStockBarsRequest)
    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = SimpleNamespace(
        get_stock_bars=lambda request: SimpleNamespace(df=__import__("pandas").DataFrame())
    )
    history._crypto_client = None
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    end_at = datetime.now(UTC)
    history.get_bars_batch(
        ["AAPL"], "5m", start=end_at - timedelta(days=5), end=end_at, adjustment="splits"
    )
    # The CLI passes "splits", but the Alpaca SDK enum value is "split".
    assert recorded["adjustment"] == "split"


def _crypto_bars(
    end: datetime,
    count: int,
    *,
    minutes_per_bar: int = 5,
    last_bar_offset_minutes: int = 60,
) -> list[Bar]:
    return [
        Bar(
            end
            - timedelta(minutes=last_bar_offset_minutes)
            - timedelta(minutes=minutes_per_bar * (count - 1 - index)),
            "BTC/USD",
            "5m",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            Market.CRYPTO,
        )
        for index in range(count)
    ]


def _crypto_history(cache_bars: list[Bar]) -> Any:
    import parayabuck.data.alpaca as alpaca_module

    def _boom(_request: Any) -> Any:
        raise RuntimeError("api down")

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = None
    history._crypto_client = SimpleNamespace(get_crypto_bars=_boom)
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: cache_bars,
        upsert=lambda *args, **kwargs: None,
    )
    return history


def test_incomplete_cache_is_not_used_as_api_fallback() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    # A fresh tail (last bar 1h before end) but only ~2 days of a 30-day
    # window: recency alone must not make this acceptable as fallback data.
    history = _crypto_history(_crypto_bars(end, count=2 * 24 * 12))
    with pytest.raises(RuntimeError, match="api down"):
        history.get_bars_batch(["BTC/USD"], "5m", start=start, end=end)


def test_complete_cache_is_used_as_api_fallback() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=10)
    full = _crypto_bars(end, count=10 * 24 * 12)  # 10 days of 5m bars
    history = _crypto_history(full)
    result = history.get_bars_batch(["BTC/USD"], "5m", start=start, end=end)
    assert result == {"BTC/USD": full}
