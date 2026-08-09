from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.dataset_quality import (
    evaluate_dataset_quality,
    evaluate_symbol_quality,
    expected_trading_sessions,
    nyse_holidays,
    nyse_sessions,
)
from parayabuck.data.research_store import (
    ProviderMetadata,
    VersionedResearchStore,
    deduplicate_bars,
)


def _bar(
    timestamp: datetime,
    close: float,
    symbol: str = "SPY",
    market: Market = Market.EQUITY,
) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        1000.0,
        market,
    )


def _full_dataset_bars(start: datetime, days: int, symbol: str = "SPY") -> list[Bar]:
    sessions = sorted(nyse_sessions(start.date(), (start + timedelta(days=days)).date()))
    return [
        _bar(datetime(day.year, day.month, day.day, tzinfo=UTC), 100.0 + index, symbol)
        for index, day in enumerate(sessions)
    ]


def test_nyse_holidays_known_dates() -> None:
    holidays = nyse_holidays(2020)
    for known in (
        datetime(2020, 1, 1).date(),
        datetime(2020, 1, 20).date(),  # MLK
        datetime(2020, 2, 17).date(),  # Washington
        datetime(2020, 4, 10).date(),  # Good Friday
        datetime(2020, 5, 25).date(),  # Memorial Day
        datetime(2020, 7, 3).date(),  # July 4 observed Friday (Saturday holiday)
        datetime(2020, 9, 7).date(),  # Labor Day
        datetime(2020, 11, 26).date(),  # Thanksgiving
        datetime(2020, 12, 25).date(),  # Christmas
    ):
        assert known in holidays
    assert datetime(2020, 7, 2).date() not in holidays


def test_nyse_holidays_weekend_observation() -> None:
    # Jan 1 2022 was a Saturday: the NYSE does NOT observe a Saturday New
    # Year on the prior Friday (verified: 2021-12-31 has real SPY bars), so
    # that Friday was a regular session.
    assert datetime(2021, 12, 31).date() in nyse_sessions(
        datetime(2021, 12, 27).date(), datetime(2022, 1, 4).date()
    )
    # Jan 1 2023 was a Sunday: observed Monday Jan 2 (no bars exist then).
    assert datetime(2023, 1, 2).date() not in nyse_sessions(
        datetime(2022, 12, 27).date(), datetime(2023, 1, 5).date()
    )
    # Juneteenth observed from 2022.
    assert datetime(2021, 6, 18).date() in nyse_sessions(
        datetime(2021, 6, 14).date(), datetime(2021, 6, 21).date()
    )
    assert datetime(2022, 6, 20).date() not in nyse_sessions(
        datetime(2022, 6, 15).date(), datetime(2022, 6, 22).date()
    )


def test_expected_sessions_excludes_weekends_and_holidays() -> None:
    # 2024-07-01 (Mon) .. 2024-07-12 (Fri): weekdays = 10, July 4 (Thu) closed.
    start = datetime(2024, 7, 1, tzinfo=UTC)
    end = datetime(2024, 7, 12, tzinfo=UTC)
    assert expected_trading_sessions(start.date(), end.date()) == 9


def test_clean_dataset_passes_symbol_quality() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=365)
    bars = _full_dataset_bars(start, 365)
    report = evaluate_symbol_quality(
        bars,
        timeframe="1d",
        adjustment="all",
        data_start=start,
        data_end=end,
    )
    assert report.failures == ()
    assert report.coverage_ratio == 1.0
    assert report.largest_unexplained_gap_days == 0
    assert not report.stale_terminal_bar


def test_missing_weekday_is_an_unexplained_gap() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    sessions = sorted(nyse_sessions(start.date(), (start + timedelta(days=60)).date()))
    bars = [
        _bar(datetime(day.year, day.month, day.day, tzinfo=UTC), 100.0 + index)
        for index, day in enumerate(sessions)
    ]
    timestamps = {bar.timestamp for bar in bars}
    mid = next(
        timestamp
        for timestamp in timestamps
        if timestamp.month == 1
        and timestamp.day >= 10
        and timestamp.weekday() < 5
        and timestamp + timedelta(days=1) in timestamps
    )
    bars = [bar for bar in bars if bar.timestamp != mid]
    report = evaluate_symbol_quality(
        bars,
        timeframe="1d",
        adjustment="all",
        data_start=start,
        data_end=start + timedelta(days=60),
        max_unexplained_gap_days=0,
    )
    assert "unexplained_gap" in report.failures
    assert report.largest_unexplained_gap_days == 1


def test_late_listing_is_not_a_gap_and_is_recorded() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    listing = datetime(2024, 7, 1, tzinfo=UTC)  # Monday
    end = listing + timedelta(days=200)
    bars = _full_dataset_bars(listing, 200)
    report = evaluate_symbol_quality(
        bars,
        timeframe="1d",
        adjustment="all",
        data_start=start,
        data_end=end,
    )
    assert report.listing_start is not None
    assert report.listing_start.date() == listing.date()
    assert "unexplained_gap" not in report.failures
    assert report.coverage_ratio == 1.0


def _raw_bar(timestamp: datetime, open_: float, high: float, low: float, close: float) -> Bar:
    """Construct an invalid-OHLC Bar bypassing constructor validation."""
    bar = Bar.__new__(Bar)
    object.__setattr__(bar, "timestamp", timestamp)
    object.__setattr__(bar, "symbol", "SPY")
    object.__setattr__(bar, "timeframe", "1d")
    object.__setattr__(bar, "open", open_)
    object.__setattr__(bar, "high", high)
    object.__setattr__(bar, "low", low)
    object.__setattr__(bar, "close", close)
    object.__setattr__(bar, "volume", 1000.0)
    object.__setattr__(bar, "market", Market.EQUITY)
    return bar


def test_invalid_ohlc_and_naive_timestamps_fail() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _full_dataset_bars(start, 30)
    bad = _raw_bar(start + timedelta(days=8), 100.0, 90.0, 95.0, 98.0)
    naive = _raw_bar((start + timedelta(days=9)).replace(tzinfo=None), 100.0, 101.0, 99.0, 100.0)
    report = evaluate_symbol_quality(
        [*bars, bad, naive],
        timeframe="1d",
        adjustment="all",
        data_start=start,
        data_end=start + timedelta(days=30),
    )
    assert "invalid_ohlc" in report.failures
    assert "naive_timestamps" in report.failures
    assert "nonpositive_price" not in report.failures


def test_nonpositive_close_fails() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _full_dataset_bars(start, 30)
    bars[5] = _bar(bars[5].timestamp, 0.0)
    report = evaluate_symbol_quality(
        bars,
        timeframe="1d",
        adjustment="all",
        data_start=start,
        data_end=start + timedelta(days=30),
    )
    assert "nonpositive_price" in report.failures


def _provider(adjustment: str) -> ProviderMetadata:
    return ProviderMetadata(
        provider_name="alpaca",
        source_feed="sip",
        vendor_symbol="SPY",
        adjustment_method=adjustment,
        currency="USD",
        timezone="America/New_York",
        exchange_calendar="NYSE",
        downloaded_at=datetime(2026, 8, 5, tzinfo=UTC),
        provider_version="1.0.0",
        research_provider_differs_from_execution=False,
    )


def test_dataset_gate_rejects_split_only_equity() -> None:
    store = VersionedResearchStore("/tmp/quality-gate-test-unused")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _full_dataset_bars(start, 120)
    unique, _input, _conflicts = deduplicate_bars(bars)
    manifest = store.save_dataset(
        "equity_v1",
        unique,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
        provider_metadata=_provider("splits"),
    )
    by_symbol = {"SPY": unique}
    report = evaluate_dataset_quality(manifest, by_symbol, benchmark_symbol="SPY")
    assert "adjustment_not_dividend_adjusted" in report.failures
    assert not report.passed

    manifest_all = store.save_dataset(
        "equity_v2",
        unique,
        timeframe="1d",
        source_feed="iex",
        adjustment="all",
        universe_version="v1",
        provider_metadata=_provider("all"),
    )
    report_all = evaluate_dataset_quality(manifest_all, by_symbol, benchmark_symbol="SPY")
    assert report_all.passed, report_all.failures


def test_dataset_gate_detects_missing_benchmark_and_metadata() -> None:
    store = VersionedResearchStore("/tmp/quality-gate-test-unused")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _full_dataset_bars(start, 120, symbol="QQQ")
    unique, _input, _conflicts = deduplicate_bars(bars)
    manifest = store.save_dataset(
        "equity_v1",
        unique,
        timeframe="1d",
        source_feed="iex",
        adjustment="all",
        universe_version="v1",
    )
    report = evaluate_dataset_quality(manifest, {"QQQ": unique}, benchmark_symbol="SPY")
    assert "benchmark_missing" in report.failures
    assert "provider_metadata_missing" in report.failures
    assert not report.passed


def test_dataset_gate_detects_hash_mismatch() -> None:
    store = VersionedResearchStore("/tmp/quality-gate-test-unused")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _full_dataset_bars(start, 120)
    unique, _input, _conflicts = deduplicate_bars(bars)
    manifest = store.save_dataset(
        "equity_v1",
        unique,
        timeframe="1d",
        source_feed="iex",
        adjustment="all",
        universe_version="v1",
    )
    report = evaluate_dataset_quality(manifest, {"SPY": unique[:-1]}, benchmark_symbol="SPY")
    assert "hash_mismatch" in report.failures
    assert not report.passed


def test_inauguration_day_is_a_regular_session_in_2017_and_2021() -> None:
    # Jan 20 2017 (Friday) and Jan 20 2021 (Wednesday) were regular NYSE
    # sessions (verified against actual SPY bars in the v2 dataset). The
    # handwritten calendar wrongly marked them closed; the verified package
    # calendar does not.
    for day in (
        datetime(2017, 1, 20).date(),
        datetime(2021, 1, 20).date(),
        datetime(2023, 1, 20).date(),
    ):
        assert day in nyse_sessions(day, day)
    # 2025-01-20 fell on MLK Day: closed for that reason.
    assert datetime(2025, 1, 20).date() not in nyse_sessions(
        datetime(2025, 1, 20).date(), datetime(2025, 1, 20).date()
    )


def test_calendar_matches_known_good_friday_oracle() -> None:
    # Easter 2018 was April 1, so Good Friday was 2018-03-30; the previous
    # handwritten algorithm produced 2018-04-06 (wrong) and caused both
    # coverage > 100% and an unexplained gap of 1 on every symbol.
    assert datetime(2018, 3, 30).date() not in nyse_sessions(
        datetime(2018, 3, 30).date(), datetime(2018, 3, 30).date()
    )
    assert datetime(2018, 4, 6).date() in nyse_sessions(
        datetime(2018, 4, 6).date(), datetime(2018, 4, 6).date()
    )
    assert datetime(2019, 4, 19).date() not in nyse_sessions(
        datetime(2019, 4, 19).date(), datetime(2019, 4, 19).date()
    )
    assert datetime(2022, 4, 15).date() not in nyse_sessions(
        datetime(2022, 4, 15).date(), datetime(2022, 4, 15).date()
    )


def test_calendar_matches_actual_v2_dataset_bars() -> None:
    """Oracle check: every persisted bar must be a verified calendar session."""
    import pytest

    from parayabuck.data.research_store import VersionedResearchStore

    store = VersionedResearchStore("data/research")
    by_symbol = store.load_dataset("equity_etf_1d_v2")
    if not by_symbol:
        pytest.skip("equity_etf_1d_v2 dataset not present")
    sessions = nyse_sessions(datetime(2016, 1, 1).date(), datetime(2026, 12, 31).date())
    for symbol, bars in by_symbol.items():
        non_sessions = [
            bar.timestamp.date().isoformat() for bar in bars if bar.timestamp.date() not in sessions
        ]
        assert not non_sessions, f"{symbol} has bars outside the calendar: {non_sessions[:5]}"
