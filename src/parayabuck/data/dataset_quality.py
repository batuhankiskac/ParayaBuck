"""Deterministic dataset quality gate for research datasets.

After a dataset is persisted, ``bars_per_symbol`` alone is not enough: every
symbol is audited for calendar-aware coverage, duplicate/conflict counts,
unexplained gaps, listing date, OHLC validity, timezone and adjustment
policy. A dataset is accepted for production research only when every gate
fails nothing:

* conflicting duplicates = 0
* final duplicates = 0
* unexplained large gaps = 0
* adjustment verified (equity requires a dividend-adjusted policy)
* benchmark present with full coverage
* data hash deterministic (manifest hash equals recomputed hash)

The equity session model is a deterministic NYSE calendar
(``nyse_weekday_holiday_v1``): weekdays minus observed NYSE holidays, so
weekends and official holidays are never counted as gaps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.research_store import (
    KNOWN_ADJUSTMENTS,
    DatasetManifest,
    hash_dataset,
)
from parayabuck.markets.symbols import canonical_symbol

EXCHANGE_CALENDAR_NAME = "nyse_weekday_holiday_v1"

# Equity production research requires a dividend-adjusted (total-return)
# policy; split-only adjustment silently breaks momentum signals around
# distributions. Crypto has no dividends, so "splits" remains acceptable.
EQUITY_ADJUSTMENTS_FOR_PRODUCTION = ("all", "adjusted")

MAX_UNEXPLAINED_GAP_DAYS = 5
MAX_STALE_TERMINAL_DAYS = 10
MIN_BENCHMARK_COVERAGE_RATIO = 0.95
MIN_COVERAGE_RATIO = 0.90


def _observed_weekday(day: date) -> date:
    """NYSE observation rule: Saturday -> Friday before, Sunday -> Monday after."""
    if day.weekday() == 5:  # Saturday
        return day - timedelta(days=1)
    if day.weekday() == 6:  # Sunday
        return day + timedelta(days=1)
    return day


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm; used for Good Friday."""
    a = year % 19
    b, c = year // 100, year % 100
    d = (19 * a + b - b // 4 - ((b - 8 * b // 25 + 1) // 3) + 15) % 30
    e = (32 + 2 * (b % 4) + 2 * (c // 4) - d - c % 4) % 7
    f = d + e - 7 * ((a + 11 * d + 22 * e) // 451) + 114
    month, day = f // 31, f % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def nyse_holidays(year: int) -> frozenset[date]:
    """Observed NYSE closure dates for one year (deterministic rules)."""
    holidays: set[date] = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == 5:  # Saturday -> observed prior-year Friday
        holidays.add(date(year - 1, 12, 31))
    elif new_year.weekday() == 6:  # Sunday -> observed Monday Jan 2
        holidays.add(date(year, 1, 2))
    else:
        holidays.add(new_year)
    holidays.add(_nth_weekday(year, 1, 0, 3))  # MLK
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    if year in {2017, 2021, 2025}:
        holidays.add(_observed_weekday(date(year, 1, 20)))  # Inauguration Day
    holidays.add(_easter(year) - timedelta(days=2))  # Good Friday
    last_monday_may = date(year, 5, 31) - timedelta(days=date(year, 5, 31).weekday())
    holidays.add(last_monday_may)  # Memorial Day
    if year >= 2022:
        holidays.add(_observed_weekday(date(year, 6, 19)))  # Juneteenth
    holidays.add(_observed_weekday(date(year, 7, 4)))  # Independence Day
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    holidays.add(_observed_weekday(date(year, 12, 25)))  # Christmas
    # Cross-year observations are kept (e.g. Jan 1 on Saturday closes the
    # prior-year Dec 31); _is_session checks the neighbouring year sets.
    return frozenset(holidays)


def _is_session(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in (
        nyse_holidays(day.year)
        | nyse_holidays(day.year - 1)
        | nyse_holidays(day.year + 1)
    )


def nyse_sessions(start: date, end: date) -> frozenset[date]:
    """Deterministic set of expected NYSE sessions between two dates."""
    sessions: set[date] = set()
    current = start
    while current <= end:
        if _is_session(current):
            sessions.add(current)
        current += timedelta(days=1)
    return frozenset(sessions)


def expected_trading_sessions(start: date, end: date) -> int:
    return len(nyse_sessions(start, end))


def _unexplained_gap_trading_days(previous: date, current: date) -> int:
    """Expected sessions strictly between two consecutive bars."""
    if (current - previous).days <= 1:
        return 0
    missing = 0
    day = previous + timedelta(days=1)
    while day < current:
        if _is_session(day):
            missing += 1
        day += timedelta(days=1)
    return missing


@dataclass(frozen=True)
class SymbolQualityReport:
    symbol: str
    market: Market
    timeframe: str
    start: datetime | None
    end: datetime | None
    bar_count: int
    expected_sessions: int
    coverage_ratio: float
    duplicate_bars: int
    conflicting_duplicate_bars: int
    largest_unexplained_gap_days: int
    listing_start: datetime | None
    stale_terminal_bar: bool
    invalid_ohlc_count: int
    nonpositive_close_count: int
    timezone_naive_count: int
    adjustment: str
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "market": self.market.value,
            "timeframe": self.timeframe,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "bar_count": self.bar_count,
            "expected_sessions": self.expected_sessions,
            "coverage_ratio": self.coverage_ratio,
            "duplicate_bars": self.duplicate_bars,
            "conflicting_duplicate_bars": self.conflicting_duplicate_bars,
            "largest_unexplained_gap_days": self.largest_unexplained_gap_days,
            "listing_start": self.listing_start.isoformat() if self.listing_start else None,
            "stale_terminal_bar": self.stale_terminal_bar,
            "invalid_ohlc_count": self.invalid_ohlc_count,
            "nonpositive_close_count": self.nonpositive_close_count,
            "timezone_naive_count": self.timezone_naive_count,
            "adjustment": self.adjustment,
            "failures": list(self.failures),
        }


def evaluate_symbol_quality(
    bars: list[Bar],
    *,
    timeframe: str,
    adjustment: str,
    data_start: datetime,
    data_end: datetime,
    max_unexplained_gap_days: int = MAX_UNEXPLAINED_GAP_DAYS,
    max_stale_terminal_days: int = MAX_STALE_TERMINAL_DAYS,
    minimum_coverage_ratio: float = MIN_COVERAGE_RATIO,
) -> SymbolQualityReport:
    """Audit one symbol partition against the deterministic NYSE calendar."""
    failures: list[str] = []
    if not bars:
        return SymbolQualityReport(
            "?",
            Market.EQUITY,
            timeframe,
            None,
            None,
            0,
            0,
            0.0,
            0,
            0,
            0,
            None,
            False,
            0,
            0,
            0,
            adjustment,
            ("no_bars",),
        )
    market = bars[0].market
    symbol = canonical_symbol(bars[0].symbol, market)
    timezone_naive = sum(1 for bar in bars if bar.timestamp.tzinfo is None)
    aware = [bar for bar in bars if bar.timestamp.tzinfo is not None]
    if not aware:
        return SymbolQualityReport(
            symbol,
            market,
            timeframe,
            None,
            None,
            len(bars),
            0,
            0.0,
            0,
            0,
            0,
            None,
            False,
            0,
            0,
            timezone_naive,
            adjustment,
            ("naive_timestamps",),
        )
    ordered = sorted(aware, key=lambda bar: bar.timestamp)
    start = ordered[0].timestamp
    end = ordered[-1].timestamp
    listing = start if start > data_start else None
    sessions_from = listing.date() if listing else data_start.date()
    expected = expected_trading_sessions(sessions_from, data_end.date())
    coverage = len(ordered) / expected if expected > 0 else 0.0
    invalid_ohlc = 0
    nonpositive_close = 0
    for bar in ordered:
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if any(not math.isfinite(value) for value in values):
            invalid_ohlc += 1
            continue
        if (
            bar.low > bar.high
            or bar.low > min(bar.open, bar.close)
            or bar.high < max(bar.open, bar.close)
        ):
            invalid_ohlc += 1
        if bar.close <= 0:
            nonpositive_close += 1
    gap_days = max(
        (_unexplained_gap_trading_days(previous.timestamp.date(), current.timestamp.date()))
        for previous, current in zip(ordered, ordered[1:])
    ) if len(ordered) > 1 else 0
    stale = (data_end - end) > timedelta(days=max_stale_terminal_days)
    if coverage < minimum_coverage_ratio:
        failures.append("coverage_below_threshold")
    if gap_days > max_unexplained_gap_days:
        failures.append("unexplained_gap")
    if stale:
        failures.append("stale_terminal_bar")
    if invalid_ohlc:
        failures.append("invalid_ohlc")
    if nonpositive_close:
        failures.append("nonpositive_price")
    if timezone_naive:
        failures.append("naive_timestamps")
    return SymbolQualityReport(
        symbol,
        market,
        timeframe,
        start,
        end,
        len(ordered),
        expected,
        coverage,
        0,
        0,
        gap_days,
        listing,
        stale,
        invalid_ohlc,
        nonpositive_close,
        timezone_naive,
        adjustment,
        tuple(failures),
    )


@dataclass(frozen=True)
class DatasetQualityReport:
    dataset_id: str
    adjustment: str
    provider_metadata_present: bool
    hash_deterministic: bool
    benchmark_symbol: str
    benchmark_coverage_ratio: float
    per_symbol: tuple[SymbolQualityReport, ...]
    failures: tuple[str, ...] = ()
    exchange_calendar: str = EXCHANGE_CALENDAR_NAME

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "adjustment": self.adjustment,
            "provider_metadata_present": self.provider_metadata_present,
            "hash_deterministic": self.hash_deterministic,
            "benchmark_symbol": self.benchmark_symbol,
            "benchmark_coverage_ratio": self.benchmark_coverage_ratio,
            "exchange_calendar": self.exchange_calendar,
            "per_symbol": [report.to_dict() for report in self.per_symbol],
            "failures": list(self.failures),
            "passed": self.passed,
        }


def evaluate_dataset_quality(
    manifest: DatasetManifest,
    by_symbol: dict[str, list[Bar]],
    *,
    benchmark_symbol: str = "SPY",
    market: Market = Market.EQUITY,
    max_unexplained_gap_days: int = MAX_UNEXPLAINED_GAP_DAYS,
    minimum_coverage_ratio: float = MIN_COVERAGE_RATIO,
) -> DatasetQualityReport:
    """Evaluate the full dataset against the production research gates."""
    failures: list[str] = []
    data_start = manifest.start
    data_end = manifest.end
    recomputed_hash = hash_dataset([bar for bars in by_symbol.values() for bar in bars])
    if manifest.adjustment not in KNOWN_ADJUSTMENTS:
        failures.append("adjustment_not_verified")
    if market == Market.EQUITY and manifest.adjustment not in EQUITY_ADJUSTMENTS_FOR_PRODUCTION:
        failures.append("adjustment_not_dividend_adjusted")
    if manifest.provider_metadata is None:
        failures.append("provider_metadata_missing")
    elif (
        manifest.provider_metadata.adjustment_method != manifest.adjustment
        or manifest.provider_metadata.adjustment_method not in KNOWN_ADJUSTMENTS
    ):
        failures.append("provider_adjustment_mismatch")
    if manifest.data_hash != recomputed_hash:
        failures.append("hash_mismatch")
    if any((manifest.conflicting_duplicate_bars or {}).values()):
        failures.append("conflicting_duplicates")
    if any((manifest.duplicate_bars or {}).values()):
        failures.append("final_duplicates")
    symbol_reports: list[SymbolQualityReport] = []
    for symbol in sorted(by_symbol):
        report = evaluate_symbol_quality(
            by_symbol[symbol],
            timeframe=manifest.timeframe,
            adjustment=manifest.adjustment,
            data_start=data_start,
            data_end=data_end,
            max_unexplained_gap_days=max_unexplained_gap_days,
            minimum_coverage_ratio=minimum_coverage_ratio,
        )
        symbol_reports.append(report)
        failures.extend(report.failures)
    benchmark_bars = by_symbol.get(canonical_symbol(benchmark_symbol, market), [])
    benchmark_expected = expected_trading_sessions(
        min((bar.timestamp for bar in benchmark_bars), default=data_start).date(),
        data_end.date(),
    )
    benchmark_coverage = (
        len(benchmark_bars) / benchmark_expected if benchmark_expected > 0 else 0.0
    )
    if (
        canonical_symbol(benchmark_symbol, market) not in by_symbol
        or benchmark_coverage < MIN_BENCHMARK_COVERAGE_RATIO
    ):
        failures.append("benchmark_missing")
    return DatasetQualityReport(
        manifest.dataset_id,
        manifest.adjustment,
        manifest.provider_metadata is not None,
        manifest.data_hash == recomputed_hash,
        canonical_symbol(benchmark_symbol, market),
        benchmark_coverage,
        tuple(symbol_reports),
        tuple(dict.fromkeys(failures)),
    )


def dataset_ready_for_production(
    manifest: DatasetManifest,
    by_symbol: dict[str, list[Bar]],
    *,
    benchmark_symbol: str = "SPY",
    market: Market = Market.EQUITY,
) -> tuple[bool, tuple[str, ...]]:
    """Fail-closed acceptance check used before any production research."""
    report = evaluate_dataset_quality(
        manifest, by_symbol, benchmark_symbol=benchmark_symbol, market=market
    )
    return report.passed, report.failures


__all__ = [
    "DatasetQualityReport",
    "EQUITY_ADJUSTMENTS_FOR_PRODUCTION",
    "EXCHANGE_CALENDAR_NAME",
    "MAX_STALE_TERMINAL_DAYS",
    "MAX_UNEXPLAINED_GAP_DAYS",
    "MIN_BENCHMARK_COVERAGE_RATIO",
    "MIN_COVERAGE_RATIO",
    "SymbolQualityReport",
    "dataset_ready_for_production",
    "evaluate_dataset_quality",
    "evaluate_symbol_quality",
    "expected_trading_sessions",
    "nyse_holidays",
    "nyse_sessions",
]
