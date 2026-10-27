"""Regression tests for the 2026-10-27 audit market-data fixes (worker W17).

Covers T9-1 (incomplete provider windows are not seeded), T9-3 (non-positive
bar prices rejected), T9-4 (dropped corrections are logged), T9-6 (IEX-served
answers are not the SIP basis), D8-11 (an unparseable row timestamp skips one
row), and C8-2 (warmup does not hold the ingestion lock while seeding).
All inputs are synthetic; no provider, network or credential is touched.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.data.enums import DataFeed

from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.data.alpaca import AlpacaHistoricalData, BatchOutcome
from parayabuck.data.cache import WarmupCache
from parayabuck.runtime import EngineRuntime

START = datetime(2026, 10, 5, tzinfo=UTC)
END = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)


def _weekday_sessions() -> list[date]:
    """NYSE sessions 2026-10-05 .. 2026-10-23 (no exchange holidays in the span)."""
    days = [date(2026, 10, 5) + timedelta(days=offset) for offset in range(19)]
    return [day for day in days if day.weekday() < 5]


def _daily_frame(sessions: list[date], *, symbol: str = "QLD", extra_rows: list[dict] | None = None) -> pd.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "timestamp": datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
        }
        for day in sessions
    ]
    rows.extend(extra_rows or [])
    return pd.DataFrame(rows).set_index(["symbol", "timestamp"])


def _history(tmp_path, frame: pd.DataFrame, *, stock_feed: str = "iex") -> AlpacaHistoricalData:
    history = AlpacaHistoricalData(
        "test-key",
        "test-secret",
        stock_feed=stock_feed,
        cache=WarmupCache(str(tmp_path / "warmup.sqlite")),
    )
    history._stock_client = SimpleNamespace(get_stock_bars=lambda request: SimpleNamespace(df=frame))
    return history


def _equity_runtime(seeded: list[list[Bar]]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_timeframes = {"1d"}
    runtime.aggregators = {}
    runtime._portfolio_lock = threading.RLock()
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=150),
            seed=seeded.append,
        ),
    )
    return runtime


# T9-3: a non-positive price is never a valid bar.


@pytest.mark.parametrize("low", [0.0, -1.0])
def test_bar_rejects_a_zero_or_negative_low(low: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        Bar(datetime(2026, 10, 27, 20, 0, tzinfo=UTC), "QLD", "1d", 1.0, 2.0, low, 1.5, 10.0)


def test_bar_accepts_positive_prices_with_a_zero_volume() -> None:
    bar = Bar(datetime(2026, 10, 27, 20, 0, tzinfo=UTC), "QLD", "1d", 1.0, 2.0, 0.5, 1.5, 0.0)
    assert bar.low == 0.5


# T9-1: a provider window with a hole is reported, so warmup leaves it not ready.


def test_a_daily_window_with_a_missing_session_is_flagged_incomplete(tmp_path) -> None:
    sessions = _weekday_sessions()
    complete = _history(tmp_path, _daily_frame(sessions))
    outcome = BatchOutcome()
    bars = complete.get_bars_batch(["QLD"], "1d", start=START, end=END, adjustment="splits", outcome=outcome)
    assert len(bars["QLD"]) == len(sessions)
    assert outcome.incomplete == set()
    complete.close()

    holed = _history(tmp_path, _daily_frame([day for day in sessions if day != date(2026, 10, 14)]))
    outcome = BatchOutcome()
    bars = holed.get_bars_batch(["QLD"], "1d", start=START, end=END, adjustment="splits", outcome=outcome)
    assert len(bars["QLD"]) == len(sessions) - 1
    assert outcome.incomplete == {"QLD"}
    holed.close()


def test_warmup_skips_a_symbol_whose_provider_window_is_incomplete() -> None:
    seeded: list[list[Bar]] = []
    runtime = _equity_runtime(seeded)
    bar = Bar(datetime(2026, 10, 23, 4, 0, tzinfo=UTC), "QQQ", "1d", 1, 1, 1, 1, 1000, Market.EQUITY)

    class _History:
        def get_bars_batch(self, symbols, timeframe, *, start=None, end=None, adjustment="raw", outcome=None):
            outcome.incomplete.add("QLD")
            return {symbol: [bar] for symbol in symbols}

    runtime._warmup_symbols(_History(), ["QQQ", "QLD"], retry=False)

    assert [[item.symbol for item in batch] for batch in seeded] == [["QQQ"]]


# D8-11: one row with an unparseable timestamp is skipped, not fatal to the batch.


def test_an_unparseable_row_timestamp_skips_one_row_and_keeps_the_batch(tmp_path) -> None:
    sessions = _weekday_sessions()
    bad_row = {
        "symbol": "QLD",
        "timestamp": pd.NaT,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
    }
    history = _history(tmp_path, _daily_frame(sessions, extra_rows=[bad_row]))

    outcome = BatchOutcome()
    bars = history.get_bars_batch(["QLD"], "1d", start=START, end=END, adjustment="splits", outcome=outcome)

    assert len(bars["QLD"]) == len(sessions)
    assert outcome.incomplete == set()
    history.close()


# T9-6: an answer served by the IEX downgrade is not the SIP basis.


def test_an_iex_downgraded_answer_is_marked_degraded(tmp_path) -> None:
    sessions = _weekday_sessions()
    history = _history(tmp_path, _daily_frame(sessions), stock_feed="sip")
    history._sip_fallback_active = True
    history._stock_feed = DataFeed("iex")
    outcome = BatchOutcome()
    history.get_bars_batch(["QLD"], "1d", start=START, end=END, adjustment="splits", outcome=outcome)
    assert outcome.degraded is True

    history._sip_fallback_active = False
    history._stock_feed = DataFeed("sip")
    outcome = BatchOutcome()
    history.get_bars_batch(["QLD"], "1d", start=START, end=END, adjustment="splits", outcome=outcome)
    assert outcome.degraded is False
    history.close()


# C8-2: warmup must not wait on the ingestion lock while it seeds.


def test_warmup_does_not_hold_the_ingestion_lock_while_seeding() -> None:
    seeded: list[list[Bar]] = []
    runtime = _equity_runtime(seeded)
    ingestion_lock = threading.Lock()
    runtime._ingestion_lock = ingestion_lock
    bar = Bar(datetime(2026, 10, 23, 4, 0, tzinfo=UTC), "QQQ", "1d", 1, 1, 1, 1, 1000, Market.EQUITY)

    class _History:
        def get_bars_batch(self, symbols, timeframe, *, start=None, end=None, adjustment="raw", outcome=None):
            return {symbol: [bar] for symbol in symbols}

    ingestion_lock.acquire()  # a stream bar holding the ingestion lock
    try:
        worker = threading.Thread(
            target=runtime._warmup_symbols,
            args=(_History(), ["QQQ"]),
            kwargs={"retry": False},
            daemon=True,
        )
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        ingestion_lock.release()
    assert len(seeded) == 1


# T9-4: a correction of an already-emitted bar is logged instead of dropped silently.


def test_a_correction_of_an_emitted_bar_is_logged(caplog) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    aggregator = TimeframeAggregator("5m")
    runtime.aggregators = {"5m": aggregator}
    runtime._workers = {}

    def minute(hour_utc: int, minute_value: int, close: float) -> Bar:
        return Bar(
            datetime(2026, 10, 27, hour_utc, minute_value, tzinfo=UTC),
            "SPY",
            "1m",
            100.0,
            max(101.0, close),
            99.0,
            close,
            10.0,
            Market.EQUITY,
        )

    # 11:00-11:04 ET fills the 5m bucket; the 11:05 minute closes it (emitted).
    for minute_value in range(5):
        aggregator.update(minute(15, minute_value, 100.0))
    assert aggregator.update(minute(15, 5, 100.0)) is not None

    correction = minute(15, 2, 102.0)
    with caplog.at_level(logging.WARNING):
        asyncio.run(runtime._on_bar(correction, corrected=True))

    assert any("already-emitted" in record.getMessage() for record in caplog.records)


def test_a_correction_of_the_open_bar_is_not_logged_as_dropped(caplog) -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    aggregator = TimeframeAggregator("5m")
    runtime.aggregators = {"5m": aggregator}
    runtime._workers = {}
    open_minute = Bar(datetime(2026, 10, 27, 15, 1, tzinfo=UTC), "SPY", "1m", 100, 101, 99, 100, 10, Market.EQUITY)
    aggregator.update(open_minute)

    correction = Bar(datetime(2026, 10, 27, 15, 1, tzinfo=UTC), "SPY", "1m", 100, 101, 99, 101, 10, Market.EQUITY)
    with caplog.at_level(logging.WARNING):
        asyncio.run(runtime._on_bar(correction, corrected=True))

    assert not any("already-emitted" in record.getMessage() for record in caplog.records)
