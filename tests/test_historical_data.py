from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.markets.timeframes import is_regular_equity_time


def _bar(timestamp: datetime, market: Market) -> Bar:
    return Bar(timestamp, "BTC/USD" if market == Market.CRYPTO else "AAPL", "5m", 100, 101, 99, 100)


def test_equity_historical_filter_accepts_only_regular_session() -> None:
    assert is_regular_equity_time(datetime(2026, 7, 2, 13, 30, tzinfo=UTC))
    assert not is_regular_equity_time(datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    assert not is_regular_equity_time(datetime(2026, 7, 2, 20, 0, tzinfo=UTC))


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
    history.get_bars_batch(["AAPL"], "5m", start=end_at - timedelta(days=5), end=end_at, adjustment="splits")
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
            end - timedelta(minutes=last_bar_offset_minutes) - timedelta(minutes=minutes_per_bar * (count - 1 - index)),
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


def test_long_window_is_split_into_row_bounded_requests(monkeypatch: Any) -> None:
    """A window spanning more than 10k bars must be split into multiple
    time-bounded requests instead of being rejected or silently truncated."""
    import pandas as pd

    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)  # 2 years of hourly bars: >10k rows
    requests: list[tuple[datetime, datetime]] = []

    class FakeCryptoClient:
        def get_crypto_bars(self, request: Any) -> Any:
            requests.append((request.start, request.end))
            frames = []
            timestamp = request.start
            while timestamp < request.end:
                frames.append(
                    {
                        "timestamp": timestamp,
                        "symbol": "BTC/USD",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1000.0,
                    }
                )
                timestamp += timedelta(hours=1)
            return SimpleNamespace(df=pd.DataFrame(frames))

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = None
    history._crypto_client = FakeCryptoClient()
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    bars = history.get_bars_batch(["BTC/USD"], "1h", start=start, end=end)

    assert len(requests) > 1, "long window must be chunked"
    for request_start, request_end in requests:
        rows = (request_end - request_start).total_seconds() / 3600
        assert rows <= 10_000 + 1, "every chunk must stay row-bounded"
    expected = int((end - start).total_seconds() // 3600)
    result = bars["BTC/USD"]
    assert len(result) == expected, "no bars may be lost or duplicated by chunking"
    assert len({bar.timestamp for bar in result}) == expected


def test_long_window_split_covers_every_symbol_in_a_multi_symbol_batch() -> None:
    """The row-bounded window split must not truncate any symbol in a
    multi-symbol batch: every symbol receives its full date range."""
    import pandas as pd

    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)
    symbols = [f"SYM{i:02d}/USD" for i in range(20)]
    requests: list[tuple[datetime, datetime]] = []

    class FakeCryptoClient:
        def _bars(self, request: Any) -> Any:
            requests.append((request.start, request.end))
            frames = []
            timestamp = request.start
            while timestamp < request.end:
                for symbol in symbols:
                    frames.append(
                        {
                            "timestamp": timestamp,
                            "symbol": symbol,
                            "open": 100.0,
                            "high": 101.0,
                            "low": 99.0,
                            "close": 100.0,
                            "volume": 1000.0,
                        }
                    )
                timestamp += timedelta(hours=1)
            return SimpleNamespace(df=pd.DataFrame(frames))

        def get_crypto_bars(self, request: Any) -> Any:
            return self._bars(request)

        def get_stock_bars(self, request: Any) -> Any:
            return self._bars(request)

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = FakeCryptoClient()
    history._crypto_client = FakeCryptoClient()
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    bars = history.get_bars_batch(symbols, "1h", start=start, end=end)

    assert len(requests) > 1
    expected = int((end - start).total_seconds() // 3600)
    for symbol in symbols:
        series = bars[symbol]
        assert len(series) == expected, f"{symbol} lost bars to chunking"
        assert len({bar.timestamp for bar in series}) == expected


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


def _empty_crypto_history(cache_bars: list[Bar]) -> Any:
    import pandas as pd

    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = None
    history._crypto_client = SimpleNamespace(get_crypto_bars=lambda request: SimpleNamespace(df=pd.DataFrame()))
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: cache_bars,
        upsert=lambda *args, **kwargs: None,
    )
    return history


def test_fresh_tail_cache_not_used_when_api_returns_empty() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    # A fresh 2-day tail must not stand in for a 30-day window when the API
    # returns no rows: the response is empty, not the cache tail.
    history = _empty_crypto_history(_crypto_bars(end, count=2 * 24 * 12))
    result = history.get_bars_batch(["BTC/USD"], "5m", start=start, end=end)
    assert result == {"BTC/USD": []}


def test_full_cache_is_used_when_api_returns_empty() -> None:
    end = datetime.now(UTC)
    start = end - timedelta(days=10)
    full = _crypto_bars(end, count=10 * 24 * 12)
    history = _empty_crypto_history(full)
    result = history.get_bars_batch(["BTC/USD"], "5m", start=start, end=end)
    assert result == {"BTC/USD": full}


def test_sip_entitlement_failure_falls_back_to_iex(monkeypatch: Any) -> None:
    """A SIP entitlement error degrades the historical feed to IEX once and
    labels the fallback so reports can describe the data coverage honestly."""
    import parayabuck.data.alpaca as alpaca_module

    calls: list[tuple[str, str]] = []
    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "sip"
    history._configured_stock_feed = "sip"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._stock_client = SimpleNamespace(
        get_stock_bars=lambda request: (
            calls.append(("sip", str(request.feed)))
            or (_ for _ in ()).throw(RuntimeError("Insufficient data privileges: feed=sip is not entitled"))
        )
    )
    history._crypto_client = None
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    end = datetime(2026, 8, 5, 20, 0, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="not entitled"):
        history.get_bars_batch(["AAPL"], "1d", start=end - timedelta(days=30), end=end)
    # Entitlement failures are detected and the fallback engages.
    assert history._sip_fallback_active
    assert history.feed_status()["effective"] == "iex"
    assert history.feed_status()["sip_fallback_active"] is True
    assert "not the consolidated" in history.feed_status()["note"]
    # Non-entitlement failures never trigger the fallback.
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._stock_client.get_stock_bars = lambda request: (_ for _ in ()).throw(ConnectionError("connection reset"))
    with pytest.raises(ConnectionError, match="connection reset"):
        history.get_bars_batch(["AAPL"], "1d", start=end - timedelta(days=30), end=end)
    assert history._sip_fallback_active is False


def test_feed_status_labels_iex_coverage_honestly() -> None:
    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._configured_stock_feed = "iex"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    status = history.feed_status()
    assert status["configured"] == "iex"
    assert status["effective"] == "iex"
    assert "not the consolidated SIP/NBBO" in status["note"]


def test_batch_download_chunks_by_total_rows_not_per_symbol(monkeypatch: Any) -> None:
    """Alpaca caps a bars response at 10,000 rows TOTAL. A multi-symbol batch
    spanning years must be split by total expected rows, otherwise every
    symbol in the batch is silently truncated to 1,000 bars."""
    import parayabuck.data.alpaca as alpaca_module

    requests: list[tuple[object, object]] = []

    class CappedClient:
        def get_stock_bars(self, request: object) -> Any:
            requests.append((getattr(request, "start", None), getattr(request, "end", None)))
            return SimpleNamespace(df=__import__("pandas").DataFrame())

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "sip"
    history._configured_stock_feed = "sip"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._stock_client = CappedClient()
    history._crypto_client = None
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )
    start = datetime(2019, 5, 1, tzinfo=UTC)
    end = datetime(2026, 7, 31, tzinfo=UTC)
    # 12 symbols x ~2650 daily rows = ~31,800 total rows -> at least 4 chunks.
    history.get_bars_batch([f"SYM{i}" for i in range(12)], "1d", start=start, end=end)
    assert len(requests) >= 4
    # Chunks are time-bounded so each stays under the 10,000-row cap.
    for chunk_start, chunk_end in requests:
        rows = int((chunk_end - chunk_start).total_seconds() // 86400)
        assert rows * 12 <= 10_000

