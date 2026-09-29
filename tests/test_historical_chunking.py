"""Historical chunking row-budget tests (Phase 7).

Alpaca caps a single bars response at 10,000 rows across every symbol in the
request and silently truncates beyond it. The chunked-window fetcher must
split so that NO request — including the one-bar overlap every chunk after
the first carries — can exceed the row budget, while still delivering every
timestamp exactly once per symbol.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import _MAX_ROWS_PER_REQUEST, AlpacaHistoricalData
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES


class _RecordingFetcher:
    """Replaces _fetch_window with a conservative half-open [start, end)
    bar generator: bars exist on the timeframe grid inside the window only,
    matching Alpaca's real truncation behavior (never more generous)."""

    def __init__(self, timeframe: str) -> None:
        self.timeframe = timeframe
        self.requests: list[tuple[list[str], datetime, datetime]] = []
        self.bars_per_request: list[int] = []

    def __call__(self, symbols, timeframe, start, end, adjustment) -> dict[str, list[Bar]]:
        self.requests.append((list(symbols), start, end))
        step = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        result: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        cursor = start
        while cursor < end:
            for symbol in symbols:
                result[symbol].append(
                    Bar(
                        cursor,
                        symbol,
                        timeframe,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1000,
                        Market.EQUITY,
                    )
                )
            cursor += step
        self.bars_per_request.append(sum(len(items) for items in result.values()))
        return result


def _fetch(
    symbols: list[str],
    timeframe: str,
    days: int,
) -> tuple[dict[str, list[Bar]], _RecordingFetcher]:
    minutes = TIMEFRAME_MINUTES[timeframe]
    client = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    recording = _RecordingFetcher(timeframe)
    client._fetch_window = recording
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=days)
    merged = client._fetch_chunked_windows(symbols, timeframe, start, end, minutes, "raw")
    return merged, recording


def _assert_budget_and_completeness(
    symbols: list[str],
    timeframe: str,
    days: int,
) -> None:
    merged, recording = _fetch(symbols, timeframe, days)
    minutes = TIMEFRAME_MINUTES[timeframe]
    step = timedelta(minutes=minutes)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=days)
    # 1. No request exceeds the Alpaca row budget, overlap included.
    for symbol_count, bars in zip(
        (len(req_symbols) for req_symbols, _, _ in recording.requests),
        recording.bars_per_request,
        strict=True,
    ):
        assert bars <= _MAX_ROWS_PER_REQUEST, (symbol_count, bars)
    # 2. Every symbol receives every grid timestamp exactly once.
    expected_steps = max(1, int((end - start).total_seconds() / step.total_seconds()))
    for symbol in symbols:
        timestamps = [bar.timestamp for bar in merged[symbol]]
        assert len(timestamps) == expected_steps, (symbol, len(timestamps), expected_steps)
        assert timestamps == sorted(set(timestamps))
        assert timestamps[0] == start
        assert timestamps[-1] == end - step


def test_boundary_just_under_10000_rows() -> None:
    # 9,999 expected rows: a single chunk, still inside the budget.
    symbols = [f"SYM{i}" for i in range(10)]
    _assert_budget_and_completeness(symbols, "1d", 999)


def test_boundary_exactly_10000_rows() -> None:
    symbols = [f"SYM{i}" for i in range(10)]
    _assert_budget_and_completeness(symbols, "1d", 1000)


def test_boundary_just_over_10000_rows() -> None:
    symbols = [f"SYM{i}" for i in range(10)]
    merged, recording = _fetch(symbols, "1d", 1001)
    assert len(recording.requests) >= 2
    _assert_budget_and_completeness(symbols, "1d", 1001)


def test_boundary_20000_rows() -> None:
    symbols = [f"SYM{i}" for i in range(20)]
    merged, recording = _fetch(symbols, "1d", 1000)
    assert len(recording.requests) >= 2
    _assert_budget_and_completeness(symbols, "1d", 1000)


def test_boundary_just_over_20000_rows() -> None:
    symbols = [f"SYM{i}" for i in range(20)]
    merged, recording = _fetch(symbols, "1d", 1001)
    assert len(recording.requests) >= 3
    _assert_budget_and_completeness(symbols, "1d", 1001)


def test_twenty_symbols_never_exceed_budget() -> None:
    symbols = [f"SYM{i}" for i in range(20)]
    _assert_budget_and_completeness(symbols, "1d", 3650)


def test_intraday_timeframe_chunking() -> None:
    """1h bars across many days: budget holds per request."""
    symbols = ["SPY", "QQQ", "IWM", "DIA"]
    merged, recording = _fetch(symbols, "1h", 400)
    _assert_budget_and_completeness(symbols, "1h", 400)
    assert len(recording.requests) >= 2


def test_chunked_requests_have_no_duplicate_final_bar() -> None:
    symbols = [f"SYM{i}" for i in range(20)]
    merged, _ = _fetch(symbols, "1d", 1001)
    for symbol in symbols:
        timestamps = [bar.timestamp for bar in merged[symbol]]
        assert len(timestamps) == len(set(timestamps))
