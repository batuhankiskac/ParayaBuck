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
    # One batched calendar call, not one per timestamp: ``is_session_open``
    # resolves each distinct date with its own (uncached, ~50-100 ms)
    # ``nyse_sessions(date, date)`` lookup, so 730 days of hourly probes
    # cost ~65 s. The batch call below returns the identical bounds.
    from parayabuck.markets.nyse_calendar import NEW_YORK, nyse_sessions

    sessions = {
        day: (market_open, market_close) for day, market_open, market_close in nyse_sessions(start.date(), end.date())
    }
    count = 0
    timestamp = start
    while timestamp < end:
        bounds = sessions.get(timestamp.astimezone(NEW_YORK).date())
        if bounds is not None:
            moment = timestamp.astimezone(UTC)
            count += bounds[0] <= moment < bounds[1]
        timestamp += timedelta(hours=1)
    return count


def _batched_session_calendar(monkeypatch: Any, start: datetime, end: datetime) -> None:
    """Pre-warm the batched NYSE range lookup for a 730-day hourly window.

    Production resolves the window with one ``nyse_sessions`` range call;
    pre-warming the process calendar cache keeps these chunking tests fast
    without touching the lookup shape under test.
    """
    from parayabuck.markets.nyse_calendar import nyse_sessions

    nyse_sessions(start.date(), end.date())


def test_long_window_is_split_into_row_bounded_requests(monkeypatch: Any) -> None:
    """A window spanning more than 10k bars must be split into multiple
    time-bounded requests instead of being rejected or silently truncated."""
    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)  # 2 years of hourly bars: >10k rows
    _batched_session_calendar(monkeypatch, start, end)
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


def test_long_window_split_covers_every_symbol_in_a_multi_symbol_batch(monkeypatch: Any) -> None:
    """The row-bounded window split must not truncate any symbol in a
    multi-symbol batch: every symbol receives its full date range."""
    import parayabuck.data.alpaca as alpaca_module

    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=730)
    symbols = [f"SYM{i:02d}" for i in range(20)]
    _batched_session_calendar(monkeypatch, start, end)
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


_OUTAGE_SYMBOLS = ("QQQ", "SPY")
# SIP: no 15-minute IEX end clamp, so request bounds are exactly the test's.
_OUTAGE_FEED = "sip"


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

    The outage-honesty mechanics under test do not depend on the session
    calendar, so every calendar day carries a bar.
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


def _plain_history(cache: Any, provider: Any) -> Any:
    import parayabuck.data.alpaca as alpaca_module

    history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
    history._stock_feed = _OUTAGE_FEED
    history._configured_stock_feed = _OUTAGE_FEED
    history._sip_fallback_active = False
    history._sip_fallback_since = None
    # No recent-SIP embargo: these tests pin exact request windows.
    history._sip_embargo = timedelta(0)
    history._stock_client = provider
    history._cache = cache
    history._provider_fallbacks = 0
    history._last_batch_degraded = False
    history.provider_calls = 0
    history.provider_symbols_requested = 0
    history.provider_bars_returned = 0
    return history


def _seed_cache(cache: Any, stamps: list[datetime]) -> None:
    """Write cache rows holding exactly ``stamps`` for every symbol."""
    for symbol in _OUTAGE_SYMBOLS:
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
            feed=_OUTAGE_FEED,
            adjustment="raw",
        )


# ---------------------------------------------------------------------------
# outage honesty: cache-served rows are degraded, never provider coverage
# ---------------------------------------------------------------------------


def test_plain_fetch_never_counts_cache_rows_as_provider_coverage(warmup_cache: Any) -> None:
    """The verifier reads ``get_bars_batch``: a symbol the provider did not
    answer may be served from the cache only as a DEGRADED answer."""
    end = _today()
    start = end - timedelta(days=8)
    grid = _day_grid(start, end)
    _seed_cache(warmup_cache, grid)
    history = _plain_history(warmup_cache, _FakeStockBars())
    history._provider_fallbacks = 0
    history._last_batch_degraded = False

    complete = history.get_bars_batch(list(_OUTAGE_SYMBOLS), "1d", start, end)
    assert history._last_batch_degraded is False
    assert {symbol: len(bars) for symbol, bars in complete.items()} == {symbol: len(grid) for symbol in complete}

    history._stock_client = _FakeStockBars(omit=("SPY",))
    partial = history.get_bars_batch(list(_OUTAGE_SYMBOLS), "1d", start, end)

    assert history._last_batch_degraded is True
    assert [bar.timestamp for bar in partial["SPY"]] == grid


def test_plain_fetch_resolves_a_due_sip_reprobe(warmup_cache: Any) -> None:
    """A downgraded client whose SIP re-probe is due queries SIP again."""

    end = _today()
    start = end - timedelta(days=8)
    provider = _FakeStockBars()
    history = _plain_history(warmup_cache, provider)
    history.get_bars_batch(list(_OUTAGE_SYMBOLS), "1d", start, end)
    history._stock_feed = "iex"
    history._sip_fallback_active = True
    history._sip_fallback_since = datetime.now(UTC) - timedelta(hours=7)

    history.get_bars_batch(list(_OUTAGE_SYMBOLS), "1d", start, end + timedelta(days=1))

    assert history._sip_fallback_active is False
