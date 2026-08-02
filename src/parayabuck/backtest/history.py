"""Multi-year historical data downloader with chunked requests and on-disk caching.

Alpaca caps every bars request at ~10,000 rows, so long windows are fetched in
time chunks. Fetched bars are persisted in the shared warmup cache
(``data/cache/warmup.sqlite3``), making repeated runs nearly free.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES, weekday_count
from parayabuck.utils import is_crypto

# Alpaca caps every bars request at 10,000 rows. Chunk sizes are computed per
# market at download time (crypto trades 24/7, equity only during the regular
# session) so a chunk never exceeds the ceiling.
MAX_BARS_PER_REQUEST = 10_000
SUPPORTED_TIMEFRAMES = frozenset(TIMEFRAME_MINUTES)

# Equity bars per day for a regular (09:30-16:00) session.
EQUITY_BARS_PER_DAY_MINUTES = 390

# Equity intraday history on Alpaca is only reliably available for the most
# recent ~120 trading days (5m). Multi-year equity tests must use 1d (or 1h).
EQUITY_MAX_DAYS: dict[str, int] = {
    "1m": 20,
    "5m": 120,
    "15m": 180,
    "30m": 365,
    "1h": 720,
    "1d": 10_000,
}

DEEP_BACKTEST_TIMEFRAMES = ("1d", "1h", "30m", "15m", "5m")


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    market: Market
    bars: int
    start: datetime
    end: datetime
    expected_bars: int = 0
    coverage_pct: float = 0.0
    max_gap_minutes: int = 0
    duplicate_bars: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "market": self.market.value,
            "bars": self.bars,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "expected_bars": self.expected_bars,
            "coverage_pct": self.coverage_pct,
            "max_gap_minutes": self.max_gap_minutes,
            "duplicate_bars": self.duplicate_bars,
        }


def validate_range(
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
) -> None:
    """Fail fast when the requested window is not fetchable for the given timeframe."""
    requested_days = max(0, (end - start).days)
    if any(not is_crypto(symbol) for symbol in symbols):
        limit = EQUITY_MAX_DAYS.get(timeframe, 0)
        if limit and requested_days > limit:
            raise ValueError(
                f"Equity {timeframe} history on Alpaca is limited to ~{limit} days "
                f"(requested {requested_days}). Use --timeframe 1d for multi-year tests."
            )


def _chunk_days(timeframe: str, symbol: str) -> int:
    """Chunk size in days that keeps the request safely below the 10k bar ceiling."""
    minutes = TIMEFRAME_MINUTES[timeframe]
    bars_per_day = 24 * 60 if is_crypto(symbol) else EQUITY_BARS_PER_DAY_MINUTES
    if timeframe == "1d":
        return 9_500
    return max(1, int((MAX_BARS_PER_REQUEST * 0.95) / bars_per_day * minutes))


def download_history(
    history: AlpacaHistoricalData,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    adjustment: str = "raw",
    progress: Callable[[int, int, SymbolCoverage], None] | None = None,
) -> dict[str, list[Bar]]:
    """Download per-symbol bars for the window, chunked under the API limit.

    ``adjustment`` selects the corporate-action price basis (raw/splits/all);
    deep multi-year backtests should use splits so splits do not create false
    breakouts and stop distances.
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    collected: dict[str, list[Bar]] = {}
    for index, symbol in enumerate(symbols, start=1):
        chunk_days = _chunk_days(timeframe, symbol)
        cursor = start
        symbol_bars: list[Bar] = []
        while cursor < end:
            segment_end = min(cursor + timedelta(days=chunk_days), end)
            symbol_bars.extend(
                history.get_bars(
                    symbol, timeframe, start=cursor, end=segment_end, adjustment=adjustment
                )
            )
            cursor = segment_end
        collected[symbol] = symbol_bars
        if progress is not None:
            coverage = coverage_for(symbol, symbol_bars)
            progress(index, len(symbols), coverage)
    return collected


def coverage_for(
    symbol: str,
    bars: list[Bar],
    *,
    timeframe: str = "",
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
) -> SymbolCoverage:
    """Coverage stats for one symbol's bars.

    When the requested window and timeframe are supplied, the coverage ratio
    compares unique bars against an expected count (crypto trades 24/7;
    equity sessions are approximated as weekdays with a regular 390-minute
    session). ``duplicate_bars`` flags repeated timestamps.

    ``max_gap_minutes`` is the largest gap between consecutive bars. For
    intraday equity only same-session gaps count, because overnight and
    weekend pauses are structural, not missing data; for daily equity and
    crypto the raw minute gap is reported.
    """
    market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
    now = datetime.now(UTC)
    if not bars:
        return SymbolCoverage(symbol, market, 0, now, now)
    timestamps = sorted(bar.timestamp for bar in bars)
    unique = len(set(timestamps))
    expected = 0
    if timeframe and expected_start is not None and expected_end is not None:
        minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
        if minutes:
            if market == Market.CRYPTO:
                total_minutes = (expected_end - expected_start).total_seconds() / 60
                expected = max(0, int(total_minutes / minutes))
            else:
                trading_days = weekday_count(expected_start, expected_end)
                bars_per_day = max(1, EQUITY_BARS_PER_DAY_MINUTES // minutes)
                expected = trading_days * bars_per_day
    gaps = [
        int((later - earlier).total_seconds() // 60)
        for earlier, later in zip(timestamps, timestamps[1:])
    ]
    max_gap = max(gaps, default=0)
    interval_minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
    if market == Market.EQUITY and 0 < interval_minutes < 1440:
        same_session = [
            gap
            for (earlier, later), gap in zip(zip(timestamps, timestamps[1:]), gaps)
            if earlier.date() == later.date()
        ]
        max_gap = max(same_session, default=0)
    return SymbolCoverage(
        symbol=symbol,
        market=market,
        bars=len(bars),
        start=timestamps[0],
        end=timestamps[-1],
        expected_bars=expected,
        coverage_pct=(unique / expected) if expected > 0 else 0.0,
        max_gap_minutes=max_gap,
        duplicate_bars=len(bars) - unique,
    )


def coverage_table(coverage: list[SymbolCoverage]) -> str:
    lines = [
        "  " + f"{'Sembol':<14}{'Piyasa':<8}{'Bar':>8}{'Beklenen':>10}"
        f"{'Kapsam%':>8}{'Boşluk':>8}  {'Başlangıç':<22}{'Bitiş':<22}"
    ]
    for item in coverage:
        lines.append(
            f"  {item.symbol:<14}{item.market.value:<8}{item.bars:>8}"
            f"{item.expected_bars or '-':>10}{item.coverage_pct * 100:>7.1f}%"
            f"{item.max_gap_minutes:>8}  "
            f"{item.start.isoformat():<22}{item.end.isoformat():<22}"
        )
    return "\n".join(lines)


__all__ = [
    "DEEP_BACKTEST_TIMEFRAMES",
    "EQUITY_MAX_DAYS",
    "MAX_BARS_PER_REQUEST",
    "SUPPORTED_TIMEFRAMES",
    "SymbolCoverage",
    "coverage_for",
    "coverage_table",
    "download_history",
    "validate_range",
]
