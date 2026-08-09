from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.providers import (
    ExternalAdjustedDailyProvider,
    LocalResearchDataProvider,
    ProviderReconciliationReport,
    reconcile_providers,
)
from parayabuck.data.research_store import VersionedResearchStore


def _bar(timestamp: datetime, close: float, symbol: str = "SPY") -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        1000.0,
        Market.EQUITY,
    )


def _daily_csv(closes: list[tuple[str, float]]) -> str:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for day, close in closes:
        lines.append(
            f"{day},{close * 0.999:.2f},{close * 1.001:.2f},{close * 0.998:.2f},{close:.2f},1000"
        )
    return "\n".join(lines) + "\n"


def _stooq_provider(close_by_day: dict[str, float]) -> ExternalAdjustedDailyProvider:
    def fetch(vendor: str, start: datetime, end: datetime) -> str:
        return _daily_csv(
            [
                (day, price)
                for day, price in sorted(close_by_day.items())
                if start.date().isoformat() <= day <= end.date().isoformat()
            ]
        )

    return ExternalAdjustedDailyProvider(raw_fetcher=fetch)


def test_external_provider_parses_adjusted_daily_bars() -> None:
    provider = _stooq_provider({"2020-01-02": 100.0, "2020-01-03": 101.0, "2020-01-06": 102.0})
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 31, tzinfo=UTC)
    bars = provider.fetch_bars("SPY", "1d", start, end, "adjusted")
    assert [bar.close for bar in bars] == [100.0, 101.0, 102.0]
    assert bars[0].symbol == "SPY"
    assert provider.provider_name == "stooq"
    assert provider.adjustment_capabilities() == ("adjusted",)
    metadata = provider.metadata("SPY")
    assert metadata.adjustment_method == "adjusted"
    assert metadata.vendor_symbol == "spy.us"


def test_external_provider_rejects_unsupported_adjustment_and_timeframe() -> None:
    provider = _stooq_provider({"2020-01-02": 100.0})
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="does not support adjustment"):
        provider.fetch_bars("SPY", "1d", start, end, "all")
    with pytest.raises(ValueError, match="only serves 1d"):
        provider.fetch_bars("SPY", "1h", start, end, "adjusted")
    with pytest.raises(ValueError, match="mapping"):
        provider.fetch_bars("UNKNOWN", "1d", start, end, "adjusted")


def test_local_provider_reads_dataset_partition(tmp_path) -> None:
    store = VersionedResearchStore(tmp_path)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    bars = [_bar(start + timedelta(days=index), 100.0 + index) for index in range(30)]
    store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
    )
    provider = LocalResearchDataProvider(store, "equity_v1")
    assert provider.provider_name == "local"
    assert provider.adjustment_capabilities() == ("splits",)
    fetched = provider.fetch_bars(
        "SPY",
        "1d",
        datetime(2020, 1, 5, tzinfo=UTC),
        datetime(2020, 1, 10, tzinfo=UTC),
        "splits",
    )
    assert len(fetched) == 6
    assert fetched[0].timestamp.day == 5


def test_local_provider_requires_existing_dataset(tmp_path) -> None:
    store = VersionedResearchStore(tmp_path)
    with pytest.raises(ValueError, match="Unknown research dataset"):
        LocalResearchDataProvider(store, "missing")


def _series(days: list[tuple[datetime, float]]) -> list[Bar]:
    return [_bar(day, close) for day, close in days]


def test_reconciliation_passes_on_matching_returns() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    closes_a = [100.0 + index * 0.5 for index in range(100)]
    closes_b = [close * 1.01 for close in closes_a]  # level shift, same returns
    days = [start + timedelta(days=index) for index in range(100)]
    report = reconcile_providers(
        "SPY",
        _series(list(zip(days, closes_a))),
        _series(list(zip(days, closes_b))),
        provider_a="stooq",
        provider_b="alpaca",
    )
    assert report.passed
    assert report.overlapping_days == 99
    assert report.max_abs_return_diff == pytest.approx(0.0, abs=1e-12)
    assert report.abnormal_days == ()


def test_reconciliation_flags_corporate_action_days() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    days = [start + timedelta(days=index) for index in range(50)]
    closes_a = [100.0 + index for index in range(50)]
    closes_b = list(closes_a)
    # Day 25: provider A pays a dividend (flat return), provider B is total return.
    closes_b[24] = 90.0
    report = reconcile_providers(
        "SPY",
        _series(list(zip(days, closes_a))),
        _series(list(zip(days, closes_b))),
        provider_a="stooq",
        provider_b="alpaca",
    )
    assert not report.passed
    assert any(not flag for _day, _ra, _rb, flag in report.abnormal_days)


def test_reconciliation_without_overlap_fails_closed() -> None:
    a = _series([(datetime(2024, 1, 1, tzinfo=UTC), 100.0)])
    b = _series([(datetime(2024, 2, 1, tzinfo=UTC), 100.0)])
    report = reconcile_providers("SPY", a, b, provider_a="a", provider_b="b")
    assert isinstance(report, ProviderReconciliationReport)
    assert not report.passed
    assert report.overlapping_days == 0


def test_reconciliation_uses_trading_dates_and_tolerates_flat_side() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    days = [start + timedelta(days=index) for index in range(40)]
    closes_a = [100.0 + index for index in range(40)]
    closes_b = list(closes_a)
    # Corporate action day (Jan 11): feed B's return is flat while feed A
    # keeps its normal move; from the next day both feeds move identically so
    # the level shift never leaks into a second abnormal day.
    closes_b[10] = closes_a[9]
    for index in range(11, 40):
        closes_b[index] = closes_b[index - 1] * closes_a[index] / closes_a[index - 1]
    # Feed A timestamps at 04:00 UTC, feed B at 05:00 UTC: dates must still
    # match and the flat-side day must be tolerated.
    a = [_bar(day.replace(hour=4), close) for day, close in zip(days, closes_a)]
    b = [_bar(day.replace(hour=5), close) for day, close in zip(days, closes_b)]
    report = reconcile_providers("SPY", a, b, provider_a="a", provider_b="b")
    assert report.overlapping_days == 39
    assert report.passed
    assert any(flag for _day, _ra, _rb, flag in report.abnormal_days)
