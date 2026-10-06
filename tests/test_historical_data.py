from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.cache import WarmupCache
from parayabuck.markets.timeframes import is_regular_equity_time, is_session_open


def _bar(timestamp: datetime, timeframe: str = "5m") -> Bar:
    return Bar(timestamp, "AAPL", timeframe, 100, 101, 99, 100)


def test_equity_historical_filter_accepts_only_regular_session() -> None:
    assert is_regular_equity_time(datetime(2026, 7, 2, 13, 30, tzinfo=UTC))
    assert not is_regular_equity_time(datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    assert not is_regular_equity_time(datetime(2026, 7, 2, 20, 0, tzinfo=UTC))


def test_warmup_cache_requires_recent_market_data() -> None:
    now = datetime.now(UTC)
    assert AlpacaHistoricalData._cache_is_fresh([_bar(now - timedelta(days=3))], "5m", now)
    assert not AlpacaHistoricalData._cache_is_fresh([_bar(now - timedelta(days=5))], "5m", now)
    assert AlpacaHistoricalData._cache_is_fresh([_bar(now - timedelta(days=7), "1d")], "1d", now)
    assert not AlpacaHistoricalData._cache_is_fresh([_bar(now - timedelta(days=9), "1d")], "1d", now)
    # A bar newer than the window end is never "fresh" data for that window.
    assert not AlpacaHistoricalData._cache_is_fresh([_bar(now + timedelta(days=1), "1d")], "1d", now)


def test_crypto_symbols_are_rejected_before_any_provider_call() -> None:
    history = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    history._stock_client = None
    history._cache = None
    end = datetime(2026, 6, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="equity symbols only"):
        history.get_bars_batch(["SPY", "BTC/USD"], "1d", start=end - timedelta(days=10), end=end)


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
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    end_at = datetime.now(UTC)
    history.get_bars_batch(["AAPL"], "5m", start=end_at - timedelta(days=5), end=end_at, adjustment="splits")
    # The CLI passes "splits", but the Alpaca SDK enum value is "split".
    assert recorded["adjustment"] == "split"


# Fixed past window: deterministic regardless of the wall clock.
_FALLBACK_END = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)


def _daily_bars(end: datetime, days: int) -> list[Bar]:
    """One equity daily bar per weekday on ``(end - days, end)``."""
    bars: list[Bar] = []
    cursor = (end - timedelta(days=days - 1)).replace(hour=4, minute=0, second=0, microsecond=0)
    while cursor < end:
        if cursor.weekday() < 5:
            bars.append(Bar(cursor, "AAPL", "1d", 100.0, 101.0, 99.0, 100.0, 1000.0, Market.EQUITY))
        cursor += timedelta(days=1)
    return bars


def _cache_backed_history(cache_bars: list[Bar], get_stock_bars: Any) -> Any:
    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._configured_stock_feed = "iex"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._stock_client = SimpleNamespace(get_stock_bars=get_stock_bars)
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: cache_bars,
        upsert=lambda *args, **kwargs: None,
    )
    return history


def _failing_history(cache_bars: list[Bar]) -> Any:
    def _boom(_request: Any) -> Any:
        raise RuntimeError("api down")

    return _cache_backed_history(cache_bars, _boom)


def _hourly_frame(start: datetime, end: datetime, symbols: list[str]) -> Any:
    import pandas as pd

    frames = []
    timestamp = start
    while timestamp < end:
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
    return pd.DataFrame(frames)


def _regular_hours(start: datetime, end: datetime) -> int:
    count = 0
    timestamp = start
    while timestamp < end:
        count += is_session_open(timestamp)
        timestamp += timedelta(hours=1)
    return count


def test_long_window_is_split_into_row_bounded_requests() -> None:
    """A window spanning more than 10k bars must be split into multiple
    time-bounded requests instead of being rejected or silently truncated."""
    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)  # 2 years of hourly bars: >10k rows
    requests: list[tuple[datetime, datetime]] = []

    class FakeStockClient:
        def get_stock_bars(self, request: Any) -> Any:
            requests.append((request.start, request.end))
            return SimpleNamespace(df=_hourly_frame(request.start, request.end, ["AAPL"]))

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = FakeStockClient()
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    bars = history.get_bars_batch(["AAPL"], "1h", start=start, end=end)

    assert len(requests) > 1, "long window must be chunked"
    for request_start, request_end in requests:
        rows = (request_end - request_start).total_seconds() / 3600
        assert rows <= 10_000 + 1, "every chunk must stay row-bounded"
    # Only regular-session bars survive; none may be lost or duplicated.
    expected = _regular_hours(start, end)
    result = bars["AAPL"]
    assert len(result) == expected, "no bars may be lost or duplicated by chunking"
    assert len({bar.timestamp for bar in result}) == expected


def test_long_window_split_covers_every_symbol_in_a_multi_symbol_batch() -> None:
    """The row-bounded window split must not truncate any symbol in a
    multi-symbol batch: every symbol receives its full date range."""
    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)
    symbols = [f"SYM{i:02d}" for i in range(20)]
    requests: list[tuple[datetime, datetime]] = []

    class FakeStockClient:
        def get_stock_bars(self, request: Any) -> Any:
            requests.append((request.start, request.end))
            return SimpleNamespace(df=_hourly_frame(request.start, request.end, symbols))

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "iex"
    history._stock_client = FakeStockClient()
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )

    bars = history.get_bars_batch(symbols, "1h", start=start, end=end)

    assert len(requests) > 1
    expected = _regular_hours(start, end)
    for symbol in symbols:
        series = bars[symbol]
        assert len(series) == expected, f"{symbol} lost bars to chunking"
        assert len({bar.timestamp for bar in series}) == expected


def test_incomplete_cache_is_not_used_as_api_fallback() -> None:
    end = _FALLBACK_END
    start = end - timedelta(days=30)
    # A fresh tail (last bar the same day) but only ~2 sessions of a 30-day
    # window: recency alone must not make this acceptable as fallback data.
    history = _failing_history(_daily_bars(end, days=2))
    with pytest.raises(RuntimeError, match="api down"):
        history.get_bars_batch(["AAPL"], "1d", start=start, end=end)


def test_complete_cache_is_used_as_api_fallback() -> None:
    end = _FALLBACK_END
    start = end - timedelta(days=10)
    full = _daily_bars(end, days=10)
    history = _failing_history(full)
    result = history.get_bars_batch(["AAPL"], "1d", start=start, end=end)
    assert result == {"AAPL": full}


def _empty_history(cache_bars: list[Bar]) -> Any:
    import pandas as pd

    return _cache_backed_history(cache_bars, lambda request: SimpleNamespace(df=pd.DataFrame()))


def test_fresh_tail_cache_not_used_when_api_returns_empty() -> None:
    end = _FALLBACK_END
    start = end - timedelta(days=30)
    # A fresh 2-session tail must not stand in for a 30-day window when the
    # API returns no rows: the response is empty, not the cache tail.
    history = _empty_history(_daily_bars(end, days=2))
    result = history.get_bars_batch(["AAPL"], "1d", start=start, end=end)
    assert result == {"AAPL": []}


def test_full_cache_is_used_when_api_returns_empty() -> None:
    end = _FALLBACK_END
    start = end - timedelta(days=10)
    full = _daily_bars(end, days=10)
    history = _empty_history(full)
    result = history.get_bars_batch(["AAPL"], "1d", start=start, end=end)
    assert result == {"AAPL": full}


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


def _sip_history(get_stock_bars: Any, embargo_minutes: float = 15.0) -> Any:
    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = "sip"
    history._configured_stock_feed = "sip"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._sip_embargo = timedelta(minutes=embargo_minutes)
    history._stock_client = SimpleNamespace(get_stock_bars=get_stock_bars)
    history._cache = SimpleNamespace(
        get=lambda *args, **kwargs: [],
        upsert=lambda *args, **kwargs: None,
    )
    return history


def test_recent_sip_data_restriction_is_not_an_entitlement_error() -> None:
    """Alpaca Basic's 15-minute recent-SIP restriction must not downgrade the
    process to IEX: the error propagates and the SIP feed stays in use."""
    import parayabuck.data.alpaca as alpaca_module

    error = RuntimeError('{"message":"subscription does not permit querying recent SIP data"}')
    assert alpaca_module.AlpacaHistoricalData._is_entitlement_error(error) is False
    history = _sip_history(lambda request: (_ for _ in ()).throw(error))
    now = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="recent SIP data"):
        history.get_bars_batch(["AAPL"], "1d", start=now - timedelta(days=30), end=now)
    assert history._sip_fallback_active is False
    assert history.feed_status()["effective"] == "sip"


def test_sip_request_end_is_clamped_to_now_minus_embargo() -> None:
    """A SIP bar request ending at now (the hourly split-basis verifier and
    warmup) is clamped to now - embargo so it never reaches the recent window."""
    ends: list[datetime] = []

    def _record(request: Any) -> Any:
        end = request.end
        ends.append(end if end.tzinfo is not None else end.replace(tzinfo=UTC))
        return SimpleNamespace(df=__import__("pandas").DataFrame())

    history = _sip_history(_record, embargo_minutes=15.0)
    before = datetime.now(UTC)
    history.get_bars_batch(["AAPL"], "1d", start=before - timedelta(days=30), end=before)
    after = datetime.now(UTC)
    assert len(ends) == 1
    assert before - timedelta(minutes=15) <= ends[0] <= after - timedelta(minutes=15)

    # An end already older than the embargo is left untouched.
    ends.clear()
    old_end = before - timedelta(days=1)
    history.get_bars_batch(["AAPL"], "1d", start=old_end - timedelta(days=30), end=old_end)
    assert ends == [old_end]

    # A zero embargo (real-time SIP subscription) disables the clamp.
    ends.clear()
    history = _sip_history(_record, embargo_minutes=0.0)
    history.get_bars_batch(["AAPL"], "1d", start=before - timedelta(days=30), end=before)
    assert ends == [before]


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
_INCREMENTAL_SYMBOLS = ("QQQ", "SPY")
# SIP: no 15-minute IEX end clamp, so request bounds are exactly the test's.
_INCREMENTAL_FEED = "sip"


@pytest.fixture
def warmup_cache(tmp_path: Path) -> Any:
    """A real ``WarmupCache`` under ``tmp_path`` — never touches data/cache/."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        yield cache
    finally:
        cache.close()


def _day_grid(start: datetime, end: datetime) -> list[datetime]:
    """Daily bar timestamps on ``[start, end]`` at 00:00 UTC.

    The merge mechanics under test do not depend on the session calendar,
    so every calendar day carries a bar.
    """
    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(days=1)
    stamps: list[datetime] = []
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(days=1)
    return stamps


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class _FakeStockBars:
    """Deterministic 1d equity provider: synthetic rows, no network, no credentials."""

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

    def get_stock_bars(self, request: Any) -> Any:
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
            for stamp in _day_grid(start, end)
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
    history._stock_feed = _INCREMENTAL_FEED
    history._configured_stock_feed = _INCREMENTAL_FEED
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    # No recent-SIP embargo: these tests pin exact request windows.
    history._sip_embargo = timedelta(0)
    history._stock_client = provider
    history._cache = cache
    return history


def _seed_cache(cache: Any, stamps: list[datetime]) -> None:
    """Write a scope-valid cache holding exactly ``stamps`` for every symbol."""
    for symbol in _INCREMENTAL_SYMBOLS:
        cache.upsert(
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
            feed=_INCREMENTAL_FEED,
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
        symbol: [(stamp, overrides.get(stamp, 100.0)) for stamp in _day_grid(start, end)]
        for symbol in _INCREMENTAL_SYMBOLS
    }


def _window_pairs(result: dict[str, list[Bar]]) -> dict[str, list[tuple[datetime, float]]]:
    return {symbol: [(bar.timestamp, bar.close) for bar in bars] for symbol, bars in result.items()}


def _fetch_incremental(history: Any, start: datetime, end: datetime, **kwargs: Any) -> dict[str, list[Bar]]:
    return history.get_bars_batch_incremental(
        list(_INCREMENTAL_SYMBOLS),
        "1d",
        start=start,
        end=end,
        scope=_INCREMENTAL_SCOPE,
        config_digest=_INCREMENTAL_DIGEST,
        **kwargs,
    )


def test_incremental_fetch_extends_a_valid_cache_with_a_tail_download_only(warmup_cache: Any) -> None:
    """A scope-valid, head-covered, fresh cache must download only the tail
    (plus the overlap bars) and still return the complete window."""
    provider = _FakeStockBars()
    history = _incremental_history(warmup_cache, provider)
    end = _today()
    start = end - timedelta(days=8)

    first = _fetch_incremental(history, start, end)
    assert len(provider.requests) == 1, "a cache with no scope row must be fully downloaded"
    assert provider.requests[0][1] == start
    assert _window_pairs(first) == _expected_window(start, end)

    later = end + timedelta(days=1)
    second = _fetch_incremental(history, start, later)
    assert len(provider.requests) == 2
    symbols, tail_start, tail_end = provider.requests[-1]
    assert symbols == sorted(_INCREMENTAL_SYMBOLS)
    # The overlap re-downloads the two bars before the newest cached bar.
    assert tail_start == end - timedelta(days=2)
    assert tail_end == later
    assert _window_pairs(second) == _expected_window(start, later)


def test_incremental_fetch_serves_a_full_cache_hit_without_calling_the_provider(warmup_cache: Any) -> None:
    """When the cache already reaches the request end there is nothing to
    download, so the provider must not be called at all."""
    provider = _FakeStockBars()
    history = _incremental_history(warmup_cache, provider)
    end = _today()
    start = end - timedelta(days=6)
    _seed_cache(warmup_cache, _day_grid(start, end))

    result = _fetch_incremental(history, start, end, overlap_timeframes=0)

    assert provider.requests == []
    assert _window_pairs(result) == _expected_window(start, end)


def test_incremental_merge_dedups_the_overlap_and_keeps_every_cached_bar(warmup_cache: Any) -> None:
    """The tail overlaps the cache by design: the merge must produce exactly
    the cached window extended by the tail — no gaps, no duplicate timestamps
    (even when the provider answers out of order), the provider wins on a
    corrected bar, and a symbol the provider omits keeps its cached bars."""
    end = _today()
    start = end - timedelta(days=6)
    correction = end - timedelta(days=2)  # first bar of the overlap tail
    provider = _FakeStockBars(omit=("SPY",), close_overrides={correction: 100.5}, reverse=True)
    _seed_cache(warmup_cache, _day_grid(start, end))
    history = _incremental_history(warmup_cache, provider)

    result = _fetch_incremental(history, start, end)

    assert len(provider.requests) == 1
    assert provider.requests[0][1] == correction
    expected = _expected_window(start, end, close_overrides={correction: 100.5})
    # SPY had no provider rows in the tail: its cached window must survive.
    expected["SPY"] = _expected_window(start, end)["SPY"]
    assert _window_pairs(result) == expected
    qqq = result["QQQ"]
    assert [bar.timestamp for bar in qqq] == sorted({bar.timestamp for bar in qqq})
    assert next(bar.close for bar in qqq if bar.timestamp == correction) == 100.5


class _FailingStockBars:
    """Provider outage: every request raises."""

    def __init__(self) -> None:
        self.calls = 0

    def get_stock_bars(self, request: Any) -> Any:
        self.calls += 1
        raise RuntimeError("provider down")


class _EmptyStockBars:
    """Provider answers with no rows at all."""

    def get_stock_bars(self, request: Any) -> Any:
        import pandas as pd

        return SimpleNamespace(df=pd.DataFrame())


def _seed_old_scope_cache(cache: Any, start: datetime, end: datetime) -> None:
    """Fresh, window-covering rows recorded under a PREVIOUS config digest."""
    _seed_cache(cache, _day_grid(start, end))
    cache.scope_set(_INCREMENTAL_SCOPE, "digest-old", "unit-test")


def test_normal_incremental_fetch_stamps_the_scope(warmup_cache: Any) -> None:
    history = _incremental_history(warmup_cache, _FakeStockBars())
    end = _today()

    _fetch_incremental(history, end - timedelta(days=6), end)

    assert warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_outage_served_cache_rows_are_not_stamped_into_the_new_scope(warmup_cache: Any) -> None:
    end = _today()
    start = end - timedelta(days=6)
    _seed_old_scope_cache(warmup_cache, start, end)
    provider = _FailingStockBars()
    history = _incremental_history(warmup_cache, provider)

    result = _fetch_incremental(history, start, end)

    assert provider.calls == 1
    assert _window_pairs(result) == _expected_window(start, end)
    assert not warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_empty_provider_response_does_not_stamp_the_scope(warmup_cache: Any) -> None:
    history = _incremental_history(warmup_cache, _EmptyStockBars())
    end = _today()

    result = _fetch_incremental(history, end - timedelta(days=6), end)

    assert result == {symbol: [] for symbol in _INCREMENTAL_SYMBOLS}
    assert not warmup_cache.scope_is_valid(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST)


def test_empty_equity_tail_keeps_a_valid_scope_and_the_next_refresh_stays_incremental(
    warmup_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routine live case (weekend/holiday/no new closed bar): a scope-valid
    RAW equity 1d cache whose tail request returns no rows must stay scope-valid,
    serve the cached window, and keep the next refresh a tail-only download."""
    import pandas as pd

    import parayabuck.data.alpaca as alpaca_module

    monkeypatch.setattr(alpaca_module, "StockBarsRequest", lambda **kwargs: SimpleNamespace(**kwargs))
    requests: list[Any] = []

    def empty_stock_bars(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(df=pd.DataFrame())

    history = _incremental_history(warmup_cache, SimpleNamespace(get_stock_bars=empty_stock_bars))
    history._stock_feed = "iex"
    history._configured_stock_feed = "iex"
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
            adjustment="raw",
        )
    warmup_cache.scope_set(_INCREMENTAL_SCOPE, _INCREMENTAL_DIGEST, "unit-test")

    def refresh() -> dict[str, list[Bar]]:
        return history.get_bars_batch_incremental(
            symbols,
            "1d",
            start=start,
            end=end,
            adjustment="raw",
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


def test_split_adjusted_incremental_fetch_always_downloads_the_full_window(warmup_cache: Any) -> None:
    """A split rebases every older adjusted bar, so cached adjusted rows are
    never trusted: a scope-valid cache still gets a full-window download and
    the provider's rebased closes replace the stale cached ones."""
    end = _today()
    start = end - timedelta(days=8)
    history = _incremental_history(warmup_cache, _FakeStockBars())
    history.get_bars_batch_incremental(
        list(_INCREMENTAL_SYMBOLS),
        "1d",
        start=start,
        end=end,
        adjustment="splits",
        scope=_INCREMENTAL_SCOPE,
        config_digest=_INCREMENTAL_DIGEST,
    )
    first_bar = min(_day_grid(start, end))
    rebased = _FakeStockBars(close_overrides={first_bar: 100.5})
    history._stock_client = rebased

    result = history.get_bars_batch_incremental(
        list(_INCREMENTAL_SYMBOLS),
        "1d",
        start=start,
        end=end,
        adjustment="splits",
        scope=_INCREMENTAL_SCOPE,
        config_digest=_INCREMENTAL_DIGEST,
    )

    assert [request[1] for request in rebased.requests] == [start]
    assert next(bar.close for bar in result["QQQ"] if bar.timestamp == first_bar) == 100.5


def test_plain_fetch_never_counts_cache_rows_as_provider_coverage(warmup_cache: Any) -> None:
    """The verifier reads ``get_bars_batch``: a symbol the provider did not
    answer may be served from the cache only as a DEGRADED answer."""
    end = _today()
    start = end - timedelta(days=8)
    grid = _day_grid(start, end)
    _seed_cache(warmup_cache, grid)
    history = _incremental_history(warmup_cache, _FakeStockBars())
    history._provider_fallbacks = 0
    history._last_batch_degraded = False

    complete = history.get_bars_batch(list(_INCREMENTAL_SYMBOLS), "1d", start, end)
    assert history._last_batch_degraded is False
    assert {symbol: len(bars) for symbol, bars in complete.items()} == {symbol: len(grid) for symbol in complete}

    history._stock_client = _FakeStockBars(omit=("SPY",))
    partial = history.get_bars_batch(list(_INCREMENTAL_SYMBOLS), "1d", start, end)

    assert history._last_batch_degraded is True
    assert [bar.timestamp for bar in partial["SPY"]] == grid


def test_incremental_fetch_resolves_a_due_sip_reprobe_before_choosing_the_cache_feed(warmup_cache: Any) -> None:
    """A downgraded client whose SIP re-probe is due queries SIP; the cache
    must be read under SIP too, not under the stale IEX name (which would
    discard the valid SIP cache and force a full download)."""
    end = _today()
    start = end - timedelta(days=8)
    provider = _FakeStockBars()
    history = _incremental_history(warmup_cache, provider)
    _fetch_incremental(history, start, end)
    history._stock_feed = "iex"
    history._sip_fallback_active = True
    history._sip_fallback_since = datetime.now(UTC) - timedelta(hours=7)

    _fetch_incremental(history, start, end + timedelta(days=1))

    assert history._sip_fallback_active is False
    assert provider.requests[-1][1] == end - timedelta(days=2)
