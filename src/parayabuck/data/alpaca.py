from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestQuoteRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES, is_regular_equity_time, weekday_count
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.data.alpaca")

_TIMEFRAMES = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "4h": TimeFrame(4, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}

# When the API fails, a cached fallback must cover most of the requested
# window — a fresh last bar alone is not enough, otherwise a cache holding
# only the tail of the window silently serves as if it were complete data.
MIN_CACHE_COVERAGE_PCT = 0.60

# Alpaca caps a single bars response at 10,000 rows; windows spanning more
# rows are split into multiple time-bounded requests.
_MAX_ROWS_PER_REQUEST = 10_000


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
        self._configured_stock_feed = str(self._stock_feed).lower().split(".")[-1]
        # Set when a configured SIP historical feed hits an entitlement error
        # and the client degrades to IEX for the rest of the process. Bars and
        # latest quotes are gated separately by Alpaca: the quote endpoint has
        # its own entitlement, so a quote-only failure must not downgrade bar
        # coverage.
        self._sip_fallback_active = False
        self._sip_quote_fallback_active = False
        self._cache = WarmupCache()

    def feed_status(self) -> dict[str, object]:
        """Configured and effective equity historical feed for reporting."""
        bars_effective = "iex" if self._sip_fallback_active else self._configured_stock_feed
        quote_effective = (
            "iex"
            if self._sip_quote_fallback_active or self._sip_fallback_active
            else self._configured_stock_feed
        )
        return {
            "configured": self._configured_stock_feed,
            "effective": bars_effective,
            "quote_effective": quote_effective,
            "sip_fallback_active": self._sip_fallback_active,
            "sip_quote_fallback_active": self._sip_quote_fallback_active,
            "note": (
                "IEX prints reflect one exchange, not the consolidated SIP/NBBO "
                "tape; liquidity and spread proxies derived from IEX data are "
                "conservative estimates of full-market conditions."
                if self._sip_fallback_active or self._configured_stock_feed == "iex"
                else "SIP consolidates the national market system tape."
            ),
        }

    def close(self) -> None:
        """Release HTTP session pools and the local cache connection."""
        for client in (self._stock_client, self._crypto_client):
            session = getattr(client, "_session", None)
            if session is None:
                continue
            try:
                session.close()
            except Exception as exc:
                log.warning("Historical client session close warning: %s", exc)
        try:
            self._cache.close()
        except Exception as exc:
            log.warning("Warmup cache close warning: %s", exc)

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

    def get_latest_stock_quotes(self, symbols: list[str]) -> dict[str, Any]:
        """Latest NBBO quotes through the market-data client.

        Latest quotes are served by ``StockHistoricalDataClient``, not the
        trading client; using the trading client here silently degrades every
        equity scan to a neutral spread score. The quote endpoint has a
        separate SIP entitlement from historical bars: when the configured
        SIP feed is not entitled, the request falls back to IEX once and is
        labeled by ``feed_status`` instead of failing the whole scan.
        """
        if not symbols:
            return {}
        try:
            quote_feed = (
                DataFeed("iex")
                if self._sip_quote_fallback_active or self._sip_fallback_active
                else self._stock_feed
            )
            response: Any = self._stock_client.get_stock_latest_quote(
                StockLatestQuoteRequest(
                    symbol_or_symbols=[canonical_symbol(symbol) for symbol in symbols],
                    feed=quote_feed,
                )
            )
        except Exception as exc:
            if (
                not getattr(self, "_sip_quote_fallback_active", False)
                and not getattr(self, "_sip_fallback_active", False)
                and self._effective_stock_feed_name == "sip"
                and self._is_entitlement_error(exc)
            ):
                log.warning(
                    "SIP latest-quote entitlement unavailable (%s); degrading "
                    "to IEX quotes (bar coverage keeps its configured feed)",
                    exc,
                )
                self._sip_quote_fallback_active = True
                return self.get_latest_stock_quotes(symbols)
            raise
        return dict(response or {})

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
        if start_at >= end_at:
            raise ValueError("Request window must end after it starts")
        minutes = TIMEFRAME_MINUTES.get(timeframe)
        if minutes is not None:
            # Alpaca caps a single bars response at 10,000 ROWS total, across
            # every symbol in the request. Splitting by per-symbol rows would
            # silently truncate a multi-symbol batch (e.g. 10 symbols x 2,400
            # daily bars) to 1,000 bars per symbol, leaving incomplete
            # histories for the whole backtest. Chunk by the TOTAL expected
            # rows instead.
            expected_rows = len(symbols) * int((end_at - start_at).total_seconds() // 60 // minutes)
            if expected_rows > _MAX_ROWS_PER_REQUEST:
                # Alpaca caps responses at 10,000 rows and silently truncates
                # beyond it. Split the date window into row-bounded chunks so
                # long crypto/equity histories download completely.
                return self._fetch_chunked_windows(
                    symbols,
                    timeframe,
                    start_at,
                    end_at,
                    minutes,
                    adjustment,
                    markets.pop(),
                )
        else:
            markets.pop()
        return self._fetch_window(
            symbols,
            timeframe,
            start_at,
            end_at,
            adjustment,
            markets.pop(),
        )

    def _fetch_chunked_windows(
        self,
        symbols: list[str],
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        minutes: int,
        adjustment: str,
        market: Market,
    ) -> dict[str, list[Bar]]:
        span = (end_at - start_at).total_seconds()
        step_seconds = minutes * 60
        total_steps = max(1, int(span // step_seconds))
        # Alpaca caps a single bars response at 10,000 ROWS across every
        # symbol in the request. Every chunk after the first re-requests the
        # boundary bar (one overlap bar per symbol) so the seam can never
        # drop a bar; the row budget must therefore reserve the overlap rows
        # plus a safety margin BEFORE splitting, otherwise a boundary-tight
        # request (e.g. 20 symbols x 500 bars) silently truncates.
        overlap_rows = len(symbols)
        safety_margin = 100
        usable_rows_per_chunk = _MAX_ROWS_PER_REQUEST - overlap_rows - safety_margin
        expected_rows = len(symbols) * total_steps
        chunk_count = max(1, math.ceil(expected_rows / usable_rows_per_chunk))
        # Whole-step chunk boundaries keep one time grid across chunks. Each
        # chunk then carries at most len(symbols) * steps_per_chunk rows plus
        # the one-bar overlap, which is inside the reserved budget.
        steps_per_chunk = max(1, total_steps // chunk_count)
        actual_chunk_count = math.ceil(total_steps / steps_per_chunk)
        overlap = timedelta(minutes=minutes)
        merged: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        for index in range(actual_chunk_count):
            chunk_start_step = index * steps_per_chunk
            chunk_end_step = min(total_steps, (index + 1) * steps_per_chunk)
            chunk_start = start_at + timedelta(seconds=chunk_start_step * step_seconds)
            # The last chunk absorbs the remainder of the window so the
            # floored chunk size never leaves a data tail unfetched.
            chunk_end = (
                end_at
                if index == actual_chunk_count - 1
                else start_at + timedelta(seconds=chunk_end_step * step_seconds)
            )
            if index > 0:
                chunk_start = max(start_at, chunk_start - overlap)
            chunk_end = min(end_at, chunk_end)
            if chunk_end <= chunk_start:
                continue
            for symbol, bars in self._fetch_window(
                symbols,
                timeframe,
                chunk_start,
                chunk_end,
                adjustment,
                market,
            ).items():
                merged[symbol].extend(bars)
        for symbol in symbols:
            deduped = {bar.timestamp: bar for bar in merged[symbol]}
            merged[symbol] = [deduped[key] for key in sorted(deduped)]
        return merged

    @property
    def _effective_stock_feed_name(self) -> str:
        configured = getattr(self, "_configured_stock_feed", "")
        if not configured:
            configured = str(self._stock_feed).lower().split(".")[-1]
        if getattr(self, "_sip_fallback_active", False):
            return "iex"
        return configured

    @staticmethod
    def _is_entitlement_error(exc: Exception) -> bool:
        """Best-effort detection of an Alpaca market-data entitlement error.

        Entitlement failures surface as API errors mentioning feed/SIP/
        entitlement; anything ambiguous is NOT treated as an entitlement
        problem so real outages still propagate to the caller.
        """
        message = f"{exc}".lower()
        return any(
            token in message
            for token in (
                "entitlement",
                "not entitled",
                "unauthorized feed",
                "feed.",
                "sip",
                "data feed",
            )
        )

    def _fetch_window(
        self,
        symbols: list[str],
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        adjustment: str,
        market: Market,
    ) -> dict[str, list[Bar]]:
        if market == Market.EQUITY and self._effective_stock_feed_name == "iex":
            end_at = min(end_at, datetime.now(UTC) - timedelta(minutes=15, seconds=5))
        feed_name = "crypto" if market == Market.CRYPTO else self._effective_stock_feed_name
        cached = {
            symbol: [
                bar
                for bar in self._cache.get(
                    symbol,
                    timeframe,
                    market=market,
                    feed=feed_name,
                    start=start_at,
                    end=end_at,
                    adjustment=adjustment,
                )
                if (
                    market != Market.EQUITY
                    or timeframe == "1d"
                    or is_regular_equity_time(bar.timestamp)
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
                        limit=_MAX_ROWS_PER_REQUEST,
                    )
                )
            else:
                response = self._stock_client.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols=symbols,
                        timeframe=_TIMEFRAMES[timeframe],
                        start=start_at,
                        end=end_at,
                        limit=_MAX_ROWS_PER_REQUEST,
                        feed=self._stock_feed,
                        adjustment=Adjustment({"splits": "split"}.get(adjustment, adjustment)),
                    )
                )
        except Exception as exc:
            # A configured SIP entitlement that the account does not have is a
            # common paper-account situation: degrade to the IEX historical
            # feed instead of failing every warmup/scanner request. The
            # fallback is sticky for the process and surfaced via feed_status()
            # so reports can label the data coverage honestly.
            if (
                market == Market.EQUITY
                and not self._sip_fallback_active
                and self._configured_stock_feed == "sip"
                and self._is_entitlement_error(exc)
            ):
                log.warning(
                    "SIP historical entitlement unavailable (%s); degrading to "
                    "IEX historical feed for the rest of this process",
                    exc,
                )
                self._stock_feed = DataFeed("iex")
                self._sip_fallback_active = True
                return self._fetch_window(
                    symbols,
                    timeframe,
                    start_at,
                    end_at,
                    adjustment,
                    market,
                )
            if len(fresh_cached) == len(symbols) and all(
                self._cache_covers_window(bars, market, timeframe, start_at, end_at)
                for bars in fresh_cached.values()
            ):
                log.warning(
                    "Historical API failed (%s); using feed-matched local warmup cache",
                    exc,
                )
                return fresh_cached
            raise
        frame = response.df
        if frame.empty:
            if len(fresh_cached) == len(symbols) and all(
                self._cache_covers_window(bars, market, timeframe, start_at, end_at)
                for bars in fresh_cached.values()
            ):
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
                and not is_regular_equity_time(timestamp)
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
            trading_days = weekday_count(start_at, end_at)
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
