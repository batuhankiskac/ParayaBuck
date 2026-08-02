from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES, is_regular_equity_time
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.data.alpaca")

_TIMEFRAMES = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}

# When the API fails, a cached fallback must cover most of the requested
# window — a fresh last bar alone is not enough, otherwise a cache holding
# only the tail of the window silently serves as if it were complete data.
MIN_CACHE_COVERAGE_PCT = 0.60


def _weekday_count(start: datetime, end: datetime) -> int:
    """Approximate number of trading days (Mon-Fri) in an inclusive window."""
    total_days = (end.date() - start.date()).days + 1
    if total_days <= 0:
        return 0
    full_weeks, remainder = divmod(total_days, 7)
    weekend_days = full_weeks * 2
    weekday = start.date().weekday()
    for offset in range(remainder):
        if (weekday + offset) % 7 >= 5:
            weekend_days += 1
    return total_days - weekend_days


class AlpacaHistoricalData:
    """Bounded Alpaca REST client used to warm up the streaming engine."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        stock_feed: str = "iex",
        data_delayed: bool = False,
    ) -> None:
        self._stock_client = StockHistoricalDataClient(api_key, secret_key)
        self._crypto_client = CryptoHistoricalDataClient(api_key, secret_key)
        patch_session_timeout(self._stock_client._session, timeout=30.0)
        patch_session_timeout(self._crypto_client._session, timeout=30.0)
        feed = "delayed_sip" if data_delayed else stock_feed
        try:
            self._stock_feed = DataFeed(feed)
        except ValueError as exc:
            raise ValueError(f"Unsupported Alpaca stock feed: {feed}") from exc
        self._cache = WarmupCache()

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        adjustment: str = "raw",
    ) -> list[Bar]:
        canonical = canonical_symbol(symbol)
        return self.get_bars_batch([canonical], timeframe, start, end, adjustment=adjustment).get(
            canonical, []
        )

    def get_bars_batch(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        adjustment: str = "raw",
    ) -> dict[str, list[Bar]]:
        if timeframe not in _TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        if adjustment not in {"raw", "splits", "all"}:
            raise ValueError("adjustment must be one of raw, splits, all")
        if not symbols:
            return {}
        symbols = [canonical_symbol(symbol) for symbol in symbols]
        markets = {Market.CRYPTO if is_crypto(symbol) else Market.EQUITY for symbol in symbols}
        if len(markets) != 1:
            raise ValueError("Batch requests must not mix equity and crypto symbols")
        end_at = self._parse_datetime(end) or datetime.now(UTC)
        start_at = self._parse_datetime(start) or end_at - timedelta(days=180)
        market = markets.pop()
        if market == Market.EQUITY and str(self._stock_feed).lower().split(".")[-1] == "iex":
            end_at = min(end_at, datetime.now(UTC) - timedelta(minutes=15, seconds=5))
        feed_name = (
            "crypto" if market == Market.CRYPTO else str(self._stock_feed).lower().split(".")[-1]
        )
        cached = {
            symbol: [
                bar
                for bar in self._cache.get(
                    symbol,
                    timeframe,
                    feed=feed_name,
                    start=start_at,
                    end=end_at,
                    adjustment=adjustment,
                )
                if (
                    market != Market.EQUITY
                    or timeframe == "1d"
                    or self._is_regular_equity_timestamp(bar.timestamp)
                )
            ]
            for symbol in symbols
        }
        fresh_cached = {
            symbol: bars
            for symbol, bars in cached.items()
            if self._cache_is_fresh(bars, market, timeframe, end_at)
        }
        response: Any
        try:
            if market == Market.CRYPTO:
                response = self._crypto_client.get_crypto_bars(
                    CryptoBarsRequest(
                        symbol_or_symbols=symbols,
                        timeframe=_TIMEFRAMES[timeframe],
                        start=start_at,
                        end=end_at,
                        limit=10_000,
                    )
                )
            else:
                response = self._stock_client.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols=symbols,
                        timeframe=_TIMEFRAMES[timeframe],
                        start=start_at,
                        end=end_at,
                        limit=10_000,
                        feed=self._stock_feed,
                        adjustment=Adjustment({"splits": "split"}.get(adjustment, adjustment)),
                    )
                )
        except Exception:
            if len(fresh_cached) == len(symbols) and all(
                self._cache_covers_window(bars, market, timeframe, start_at, end_at)
                for bars in fresh_cached.values()
            ):
                log.warning("Historical API failed; using feed-matched local warmup cache")
                return fresh_cached
            raise
        frame = response.df
        if frame.empty:
            if len(fresh_cached) == len(symbols):
                log.warning("Historical API returned no rows; using feed-matched local cache")
                return fresh_cached
            return {symbol: [] for symbol in symbols}
        bars: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        for row in frame.reset_index().sort_values("timestamp").itertuples(index=False):
            timestamp = row.timestamp
            if hasattr(timestamp, "to_pydatetime"):
                timestamp = timestamp.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            timestamp = timestamp.astimezone(UTC)
            if (
                market == Market.EQUITY
                and timeframe != "1d"
                and not self._is_regular_equity_timestamp(timestamp)
            ):
                continue
            row_symbol = str(getattr(row, "symbol", symbols[0]))
            item = Bar(
                timestamp=timestamp,
                symbol=row_symbol,
                timeframe=timeframe,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                market=market,
            )
            bars.setdefault(row_symbol, []).append(item)
        total = sum(len(items) for items in bars.values())
        fetched = [bar for items in bars.values() for bar in items]
        self._cache.upsert(fetched, feed=feed_name, adjustment=adjustment)
        for symbol in symbols:
            merged = {bar.timestamp: bar for bar in cached.get(symbol, [])}
            merged.update({bar.timestamp: bar for bar in bars.get(symbol, [])})
            bars[symbol] = [merged[key] for key in sorted(merged)]
        log.info("Fetched %d %s bars for %d symbols", total, timeframe, len(symbols))
        return bars

    @staticmethod
    def _is_regular_equity_timestamp(timestamp: datetime) -> bool:
        return bool(is_regular_equity_time(timestamp))

    @staticmethod
    def _cache_is_fresh(
        bars: list[Bar],
        market: Market,
        timeframe: str,
        end_at: datetime,
    ) -> bool:
        if not bars:
            return False
        latest = max(bar.timestamp for bar in bars)
        age = end_at - latest
        if age.total_seconds() < 0:
            return False
        if market == Market.EQUITY:
            maximum_age = timedelta(days=4 if timeframe != "1d" else 8)
        else:
            minutes = TIMEFRAME_MINUTES[timeframe]
            maximum_age = timedelta(minutes=max(180, minutes * 3))
        return bool(age <= maximum_age)

    @staticmethod
    def _cache_covers_window(
        bars: list[Bar],
        market: Market,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        min_pct: float = MIN_CACHE_COVERAGE_PCT,
    ) -> bool:
        """Whether cached bars plausibly cover the requested window.

        Recency (``_cache_is_fresh``) only proves the tail exists; a cache
        holding a small fraction of a long window must not stand in for the
        whole request.
        """
        if not bars:
            return False
        minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
        if minutes <= 0:
            return True
        unique = len({bar.timestamp for bar in bars})
        if market == Market.CRYPTO:
            expected = max(0, int((end_at - start_at).total_seconds() / 60 / minutes))
        else:
            trading_days = _weekday_count(start_at, end_at)
            bars_per_day = max(1, 390 // minutes)
            expected = trading_days * bars_per_day
        return bool(expected <= 0 or unique / expected >= min_pct)

    def get_latest_crypto_quotes(self, symbols: list[str]) -> dict[str, Any]:
        canonical = [canonical_symbol(symbol, Market.CRYPTO) for symbol in symbols]
        if not canonical:
            return {}
        return dict(
            self._crypto_client.get_crypto_latest_quote(
                CryptoLatestQuoteRequest(symbol_or_symbols=canonical)
            )
        )

    @staticmethod
    def _parse_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = ["AlpacaHistoricalData"]
