from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from parayabuck.backtest.history import coverage_for
from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache


def _equity_bar(offset_days: int, symbol: str = "AAPL") -> Bar:
    timestamp = datetime(2025, 1, 6, 14, 30, tzinfo=UTC) + timedelta(days=offset_days)
    return Bar(timestamp, symbol, "1d", 100.0, 101.0, 99.0, 100.0, 1000.0, Market.EQUITY)


def test_warmup_cache_migrates_legacy_schema(tmp_path) -> None:
    db = tmp_path / "warmup.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute(
        """
        CREATE TABLE bars (
            feed TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY(feed, symbol, timeframe, timestamp)
        )
        """
    )
    connection.execute(
        "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "iex",
            "AAPL",
            "1d",
            "2025-01-02T14:30:00+00:00",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            "2025-01-02T15:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    cache = WarmupCache(str(db))
    fetched = cache.get(
        "AAPL",
        "1d",
        market=Market.EQUITY,
        feed="iex",
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 3, tzinfo=UTC),
        adjustment="raw",
    )
    assert len(fetched) == 1
    assert fetched[0].symbol == "AAPL"
    assert fetched[0].close == 100.0
    assert fetched[0].market == Market.EQUITY
    # Legacy data was fetched on the RAW price basis; it must not satisfy an
    # all-adjusted lookup.
    assert (
        cache.get(
            "AAPL",
            "1d",
            market=Market.EQUITY,
            feed="iex",
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 3, tzinfo=UTC),
            adjustment="all",
        )
        == []
    )
    # The migrated table accepts the current upsert path without errors.
    cache.upsert(
        [
            Bar(
                datetime(2025, 1, 3, tzinfo=UTC),
                "AAPL",
                "1d",
                101.0,
                102.0,
                100.0,
                101.0,
                900.0,
                Market.EQUITY,
            )
        ],
        feed="iex",
        adjustment="all",
    )
    fetched = cache.get(
        "AAPL",
        "1d",
        market=Market.EQUITY,
        feed="iex",
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 4, tzinfo=UTC),
        adjustment="all",
    )
    assert len(fetched) == 1  # only the freshly upserted bar


def test_warmup_cache_separates_markets_with_identical_symbols(tmp_path) -> None:
    db = tmp_path / "warmup.sqlite3"
    cache = WarmupCache(str(db))
    timestamp = datetime(2025, 1, 6, 14, 30, tzinfo=UTC)
    cache.upsert(
        [Bar(timestamp, "AAPL", "1d", 100.0, 101.0, 99.0, 100.0, 1000.0, Market.EQUITY)],
        feed="iex",
    )
    cache.upsert(
        [Bar(timestamp, "BTC/USD", "1d", 200.0, 201.0, 199.0, 200.0, 1000.0, Market.CRYPTO)],
        feed="crypto",
    )
    fetched_equity = cache.get(
        "AAPL",
        "1d",
        market=Market.EQUITY,
        feed="iex",
        start=timestamp,
        end=timestamp + timedelta(days=1),
    )
    fetched_crypto = cache.get(
        "BTC/USD",
        "1d",
        market=Market.CRYPTO,
        feed="crypto",
        start=timestamp,
        end=timestamp + timedelta(days=1),
    )
    assert [bar.close for bar in fetched_equity] == [100.0]
    assert [bar.close for bar in fetched_crypto] == [200.0]
    cache.close()


def test_coverage_for_equity_reports_expected_bars_gaps_duplicates() -> None:
    start = datetime(2025, 1, 6, tzinfo=UTC)  # Monday
    end = start + timedelta(days=4)  # Friday: 5 trading days
    bars = [_equity_bar(offset) for offset in range(5)]
    bars.append(bars[-1])  # duplicate timestamp

    coverage = coverage_for("AAPL", bars, timeframe="1d", expected_start=start, expected_end=end)
    assert coverage.expected_bars == 5
    assert coverage.bars == 6
    assert coverage.duplicate_bars == 1
    assert coverage.coverage_pct == 1.0
    # 1d bars are 24h apart by construction: 1440 is the regular interval.
    assert coverage.max_gap_minutes == 1440


def test_coverage_for_crypto_uses_24_7_expected_bars() -> None:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    end = start + timedelta(hours=2)
    bars = [
        Bar(
            start + timedelta(minutes=30 * index),
            "BTC/USD",
            "5m",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            Market.CRYPTO,
        )
        for index in range(3)
    ]
    coverage = coverage_for("BTC/USD", bars, timeframe="5m", expected_start=start, expected_end=end)
    assert coverage.expected_bars == 24
    assert coverage.bars == 3
    assert coverage.coverage_pct == 3 / 24


def test_coverage_for_intraday_equity_ignores_overnight_gaps() -> None:
    start = datetime(2025, 1, 6, 14, 30, tzinfo=UTC)  # Monday 14:30 UTC
    bars = [
        Bar(
            start + timedelta(minutes=5 * index),
            "AAPL",
            "5m",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            Market.EQUITY,
        )
        for index in range(3)
    ]
    # Next session, Tuesday 14:30 UTC: an overnight pause, not missing data.
    bars.append(
        Bar(
            start + timedelta(days=1),
            "AAPL",
            "5m",
            100.0,
            101.0,
            99.0,
            100.0,
            1000.0,
            Market.EQUITY,
        )
    )
    coverage = coverage_for("AAPL", bars, timeframe="5m")
    assert coverage.bars == 4
    assert coverage.max_gap_minutes == 5  # only the same-session 5-minute gaps
