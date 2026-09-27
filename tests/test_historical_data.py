from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.cache import WarmupCache
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


# ---------------------------------------------------------------------------
# incremental (cache-merge) fetch — the live PAPER warmup path
# ---------------------------------------------------------------------------

_INCREMENTAL_SCOPE = "warmup/unit-test"
_INCREMENTAL_DIGEST = "digest-a"
_INCREMENTAL_SYMBOLS = ("BTC/USD", "ETH/USD")


@pytest.fixture
def warmup_cache(tmp_path: Path) -> Any:
    """A real ``WarmupCache`` under ``tmp_path`` — never touches data/cache/."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        yield cache
    finally:
        cache.close()


def _hour_grid(start: datetime, end: datetime) -> list[datetime]:
    """Hourly bar timestamps on ``[start, end]``; crypto trades 24/7."""
    cursor = start.replace(minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(hours=1)
    stamps: list[datetime] = []
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(hours=1)
    return stamps


class _FakeCryptoBars:
    """Deterministic 1h crypto provider: synthetic rows, no network, no credentials."""

    def __init__(
        self,
        *,
        omit: tuple[str, ...] = (),
        close_overrides: dict[datetime, float] | None = None,
        reverse: bool = False,
    ) -> None:
        self.requests: list[tuple[list[str], datetime, datetime]] = []
        self._omit = set(omit)
        self._close_overrides = close_overrides or {}
        self._reverse = reverse

    def get_crypto_bars(self, request: Any) -> Any:
        import pandas as pd

        # The SDK hands the request bounds back naive; the production client
        # reads every returned timestamp as UTC, so the fake does the same.
        start = request.start.replace(tzinfo=UTC) if request.start.tzinfo is None else request.start.astimezone(UTC)
        end = request.end.replace(tzinfo=UTC) if request.end.tzinfo is None else request.end.astimezone(UTC)
        symbols = sorted(str(symbol) for symbol in request.symbol_or_symbols)
        self.requests.append((symbols, start, end))
        rows = [
            {
                "symbol": symbol,
                "timestamp": stamp,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": self._close_overrides.get(stamp, 100.0),
                "volume": 1000.0,
            }
            for stamp in _hour_grid(start, end)
            for symbol in symbols
            if symbol not in self._omit
        ]
        frame = pd.DataFrame(rows)
        if self._reverse:
            frame = frame.iloc[::-1]
        return SimpleNamespace(df=frame)


def _incremental_history(cache: Any, provider: Any) -> Any:
    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._configured_stock_feed = "iex"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._stock_client = None
    history._crypto_client = provider
    history._cache = cache
    return history


def _seed_crypto_cache(cache: Any, stamps: list[datetime]) -> None:
    """Write a scope-valid cache holding exactly ``stamps`` for every symbol."""
    for symbol in _INCREMENTAL_SYMBOLS:
        cache.upsert(
            [
                Bar(
                    timestamp=stamp,
                    symbol=symbol,
                    timeframe="1h",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.0,
                    volume=1000.0,
                    market=Market.CRYPTO,
                )
                for stamp in stamps
            ],
            feed="crypto",
            adjustment="raw",
        )
    cache.scope_set(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST, "unit-test")


def _expected_window(
    start: datetime,
    end: datetime,
    *,
    close_overrides: dict[datetime, float] | None = None,
) -> dict[str, list[tuple[datetime, float]]]:
    """The (timestamp, close) pairs a complete download of the window returns."""
    overrides = close_overrides or {}
    return {
        symbol: [(stamp, overrides.get(stamp, 100.0)) for stamp in _hour_grid(start, end)]
        for symbol in _INCREMENTAL_SYMBOLS
    }


def _window_pairs(result: dict[str, list[Bar]]) -> dict[str, list[tuple[datetime, float]]]:
    return {symbol: [(bar.timestamp, bar.close) for bar in bars] for symbol, bars in result.items()}


def _fetch_incremental(history: Any, start: datetime, end: datetime, **kwargs: Any) -> dict[str, list[Bar]]:
    return history.get_bars_batch_incremental(
        list(_INCREMENTAL_SYMBOLS),
        "1h",
        start=start,
        end=end,
        scope=_INCREMENTAL_SCOPE,
        config_digest=_INCREMENTAL_DIGEST,
        **kwargs,
    )


def test_incremental_fetch_extends_a_valid_cache_with_a_tail_download_only(warmup_cache: Any) -> None:
    """A scope-valid, head-covered, fresh cache must download only the tail
    (plus the overlap bars) and still return the complete window."""
    provider = _FakeCryptoBars()
    history = _incremental_history(warmup_cache, provider)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=8)

    first = _fetch_incremental(history, start, end)
    assert len(provider.requests) == 1, "a cache with no scope row must be fully downloaded"
    assert provider.requests[0][1] == start
    assert _window_pairs(first) == _expected_window(start, end)

    later = end + timedelta(hours=1)
    second = _fetch_incremental(history, start, later)
    assert len(provider.requests) == 2
    symbols, tail_start, tail_end = provider.requests[-1]
    assert symbols == sorted(_INCREMENTAL_SYMBOLS)
    # The overlap re-downloads the two bars before the newest cached bar.
    assert tail_start == end - timedelta(hours=2)
    assert tail_end == later
    assert _window_pairs(second) == _expected_window(start, later)


def test_incremental_fetch_serves_a_full_cache_hit_without_calling_the_provider(warmup_cache: Any) -> None:
    """When the cache already reaches the request end there is nothing to
    download, so the provider must not be called at all."""
    provider = _FakeCryptoBars()
    history = _incremental_history(warmup_cache, provider)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=6)
    _seed_crypto_cache(warmup_cache, _hour_grid(start, end))

    result = _fetch_incremental(history, start, end, overlap_timeframes=0)

    assert provider.requests == []
    assert _window_pairs(result) == _expected_window(start, end)


def test_incremental_merge_dedups_the_overlap_and_keeps_every_cached_bar(warmup_cache: Any) -> None:
    """The tail overlaps the cache by design: the merge must produce exactly
    the cached window extended by the tail — no gaps, no duplicate timestamps
    (even when the provider answers out of order), the provider wins on a
    corrected bar, and a symbol the provider omits keeps its cached bars."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=6)
    correction = end - timedelta(hours=2)  # first bar of the overlap tail
    provider = _FakeCryptoBars(omit=("ETH/USD",), close_overrides={correction: 100.5}, reverse=True)
    _seed_crypto_cache(warmup_cache, _hour_grid(start, end))
    history = _incremental_history(warmup_cache, provider)

    result = _fetch_incremental(history, start, end)

    assert len(provider.requests) == 1
    assert provider.requests[0][1] == correction
    expected = _expected_window(start, end, close_overrides={correction: 100.5})
    # ETH/USD had no provider rows in the tail: its cached window must survive.
    expected["ETH/USD"] = _expected_window(start, end)["ETH/USD"]
    assert _window_pairs(result) == expected
    btc = result["BTC/USD"]
    assert [bar.timestamp for bar in btc] == sorted({bar.timestamp for bar in btc})
    assert next(bar.close for bar in btc if bar.timestamp == correction) == 100.5


class _FailingCryptoBars:
    """Provider outage: every request raises."""

    def __init__(self) -> None:
        self.calls = 0

    def get_crypto_bars(self, request: Any) -> Any:
        self.calls += 1
        raise RuntimeError("provider down")


class _EmptyCryptoBars:
    """Provider answers with no rows at all."""

    def get_crypto_bars(self, request: Any) -> Any:
        import pandas as pd

        return SimpleNamespace(df=pd.DataFrame())


def _seed_old_scope_cache(cache: Any, start: datetime, end: datetime) -> None:
    """Fresh, window-covering rows recorded under a PREVIOUS config digest."""
    _seed_crypto_cache(cache, _hour_grid(start, end))
    cache.scope_set(_INCREMENTAL_SCOPE, "digest-old", "unit-test")


def test_normal_incremental_fetch_stamps_the_scope(warmup_cache: Any) -> None:
    history = _incremental_history(warmup_cache, _FakeCryptoBars())
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    _fetch_incremental(history, end - timedelta(hours=6), end)

    assert warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_outage_served_cache_rows_are_not_stamped_into_the_new_scope(warmup_cache: Any) -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=6)
    _seed_old_scope_cache(warmup_cache, start, end)
    provider = _FailingCryptoBars()
    history = _incremental_history(warmup_cache, provider)

    result = _fetch_incremental(history, start, end)

    assert provider.calls == 1
    assert _window_pairs(result) == _expected_window(start, end)
    assert not warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_empty_provider_response_does_not_stamp_the_scope(warmup_cache: Any) -> None:
    history = _incremental_history(warmup_cache, _EmptyCryptoBars())
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    result = _fetch_incremental(history, end - timedelta(hours=6), end)

    assert result == {symbol: [] for symbol in _INCREMENTAL_SYMBOLS}
    assert not warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_dedup_replay_of_an_outage_served_batch_does_not_stamp_the_scope(warmup_cache: Any) -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=6)
    _seed_old_scope_cache(warmup_cache, start, end)
    history = _incremental_history(warmup_cache, _FailingCryptoBars())
    history.get_bars_batch(list(_INCREMENTAL_SYMBOLS), "1h", start, end)
    # The provider recovers, but the identical request inside the dedup TTL
    # replays the outage-served result instead of calling it.
    healthy = _FakeCryptoBars()
    history._crypto_client = healthy

    _fetch_incremental(history, start, end)

    assert healthy.requests == []
    assert not warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_empty_equity_tail_keeps_a_valid_scope_and_the_next_refresh_stays_incremental(
    warmup_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routine live case (weekend/holiday/no new closed bar): a scope-valid
    equity 1d cache whose tail request returns no rows must stay scope-valid,
    serve the cached window, and keep the next refresh a tail-only download."""
    import pandas as pd

    import parayabuck.data.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "StockBarsRequest", lambda **kwargs: SimpleNamespace(**kwargs))
    requests: list[Any] = []

    def empty_stock_bars(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(df=pd.DataFrame())

    history = _incremental_history(warmup_cache, None)
    history._stock_client = SimpleNamespace(get_stock_bars=empty_stock_bars)
    symbols = ["SPY", "QQQ", "QLD"]
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    day = start.replace(hour=4, minute=0, second=0, microsecond=0)
    stamps: list[datetime] = []
    while day < end - timedelta(hours=1):
        if day.weekday() < 5:
            stamps.append(day)
        day += timedelta(days=1)
    for symbol in symbols:
        warmup_cache.upsert(
            [
                Bar(
                    timestamp=stamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.0,
                    volume=1000.0,
                    market=Market.EQUITY,
                )
                for stamp in stamps
            ],
            feed="iex",
            adjustment="splits",
        )
    warmup_cache.scope_set(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST, "unit-test")

    def refresh() -> dict[str, list[Bar]]:
        return history.get_bars_batch_incremental(
            symbols,
            "1d",
            start=start,
            end=end,
            adjustment="splits",
            scope=_INCREMENTAL_SCOPE,
            config_digest=_INCREMENTAL_DIGEST,
        )

    first = refresh()
    assert warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)
    assert {symbol: [bar.timestamp for bar in bars] for symbol, bars in first.items()} == {
        symbol: stamps for symbol in symbols
    }
    second = refresh()

    assert second == first
    assert warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)
    assert len(requests) == 2
    # Both refreshes asked only for the overlap tail, never the full window.
    for request in requests:
        assert request.start >= stamps[-1] - timedelta(days=2)
