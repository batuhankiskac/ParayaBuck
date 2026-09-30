from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    TIMEFRAME_MINUTES,
    is_session_open,
    weekday_count,
)
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

# A configured SIP feed that the account is not entitled to answers 401/403.
# Anything else — a DNS failure, a timeout, a 5xx — is a transport error and
# must NOT downgrade history for the life of the process.
_ENTITLEMENT_STATUS_CODES = frozenset({401, 403})

# Explicit entitlement sentences in the RESPONSE BODY only. Bare words like
# "sip" are deliberately absent: a transport error's message embeds the
# request URL, and every Alpaca bar request carries `feed=sip`, so a
# substring match on the message turned a network blip into a permanent IEX
# downgrade.
_ENTITLEMENT_BODY_PATTERNS = (
    r"\bentitlement\b",
    r"\bnot entitled\b",
    r"\binsufficient\s+\S+\s+privileges?\b",
    r"\bunauthorized\b[^.]*\bfeed\b",
    r"\bno access to\b",
)

# Request URLs are stripped before any body matching: only the server's
# answer may classify an error, never the endpoint we asked.
_REQUEST_URL = re.compile(r"https?://\S+")

# How long the SIP -> IEX downgrade is trusted before one bounded re-probe
# tries SIP again, so granting the entitlement (or a data-plan change) heals
# without a restart.
_SIP_REPROBE_INTERVAL = timedelta(hours=6)


def _floor_to_grid(value: datetime, minutes: int) -> datetime:
    """Floor a UTC timestamp down to the ``minutes`` grid boundary.

    Bars are aligned to the timeframe grid (e.g. hourly bars at ``:00``), so
    cache reads and head-coverage checks must compare against the floored
    window start or the first grid-aligned bar is excluded by a raw boundary
    carrying seconds.
    """
    epoch_minutes = int(value.timestamp() // 60)
    return datetime.fromtimestamp((epoch_minutes - epoch_minutes % minutes) * 60, tz=UTC)


def _first_expected_bar(timeframe: str, start: datetime) -> datetime:
    """The earliest bar timestamp a full fetch could return for ``start``.

    Daily bars keep the window's original bucket hour (they are not aligned
    to the UTC midnight grid) and sit on regular sessions, so a three-day
    allowance absorbs weekend/holiday gaps. Intraday bars only exist on
    regular sessions: for a mid-session start the first bar is the next grid
    point at/after start; for a pre-market/after-close/weekend start it is
    the next session's 09:30 ET bar. This is the head-coverage reference for
    incremental reuse: a cache whose oldest bar is NEWER than this cannot
    cover the requested window head and must be fully refetched.
    """
    if timeframe == "1d":
        return start + timedelta(days=3)
    local = start.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    if local.weekday() < 5 and 9 * 60 + 30 <= minute < 16 * 60:
        return start
    cursor = local.replace(hour=9, minute=30, second=0, microsecond=0)
    if cursor <= local:
        cursor += timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor.astimezone(UTC)


class AlpacaHistoricalData:
    """Bounded Alpaca equity REST client used to warm up the streaming engine.

    Dedup & measurement: ``get_bars_batch`` deduplicates identical consecutive
    provider calls (same sorted symbols, timeframe, start, end, adjustment)
    within a short TTL (60 s) so repeated signal-verification latches that
    happen on the same decision timestamp do not re-hit Alpaca. The cache
    is measurement-aware: a dedup hit returns the cached bars without counting
    as a new provider call.
    The dedup is bounded (single-entry, TTL) and never weakens fail-closed
    semantics — a new timestamp, new symbol set, or expired TTL triggers a
    fresh provider fetch and fresh adequacy checks.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        stock_feed: str = "iex",
        data_delayed: bool = False,
        cache: WarmupCache | str | Path | None = None,
        *,
        sip_embargo_minutes: float = 15.0,
    ) -> None:
        self._stock_client = StockHistoricalDataClient(api_key, secret_key)
        patch_session_timeout(self._stock_client._session, timeout=30.0)
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
        # When the bounded SIP -> IEX downgrade engaged, so one re-probe can
        # be scheduled per ``_SIP_REPROBE_INTERVAL`` instead of trusting a
        # stale downgrade for the life of the process.
        self._sip_fallback_since: datetime | None = None
        self._sip_quote_fallback_active = False
        # Alpaca's Basic data plan rejects SIP bar queries whose end reaches
        # into the most recent 15 minutes. Callers wire this from
        # ``signal_cross_check.sip_embargo_minutes`` (the single setting for
        # this data-plan property); 0 disables the clamp for accounts with a
        # real-time SIP subscription.
        if not math.isfinite(sip_embargo_minutes) or sip_embargo_minutes < 0:
            raise ValueError("sip_embargo_minutes must be non-negative")
        self._sip_embargo = timedelta(minutes=sip_embargo_minutes)
        if cache is None:
            self._cache = WarmupCache()
        elif isinstance(cache, WarmupCache):
            self._cache = cache
        else:
            self._cache = WarmupCache(str(cache))
        # Dedup identical consecutive provider calls (measure/bounded).
        self._last_batch_key: tuple[object, ...] | None = None
        self._last_batch_result: dict[str, list[Bar]] | None = None
        self._last_batch_at: datetime | None = None
        self._dedup_ttl_seconds: float = 60.0
        # Bumped whenever a window is answered without real provider rows (the
        # outage cache fallback or an empty response). The incremental path
        # compares it across its fetches so such rows are never stamped as
        # written under the current scope digest.
        self._provider_fallbacks: int = 0
        self._last_batch_degraded: bool = False
        # Observability counters (measured provider calls).
        self.provider_calls: int = 0
        self.provider_symbols_requested: int = 0
        self.provider_bars_returned: int = 0

    def feed_status(self) -> dict[str, object]:
        """Configured and effective equity historical feed for reporting."""
        bars_effective = "iex" if self._sip_fallback_active else self._configured_stock_feed
        quote_effective = (
            "iex" if self._sip_quote_fallback_active or self._sip_fallback_active else self._configured_stock_feed
        )
        fallback_since = getattr(self, "_sip_fallback_since", None)
        return {
            "configured": self._configured_stock_feed,
            "effective": bars_effective,
            "quote_effective": quote_effective,
            "sip_fallback_active": self._sip_fallback_active,
            "sip_quote_fallback_active": self._sip_quote_fallback_active,
            # A downgrade is bounded, not permanent: the next SIP request at
            # or after this instant re-probes and restores the configured feed.
            "sip_fallback_since": (fallback_since.isoformat() if isinstance(fallback_since, datetime) else None),
            "sip_reprobe_interval_minutes": int(_SIP_REPROBE_INTERVAL.total_seconds() // 60),
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
        session = getattr(self._stock_client, "_session", None)
        if session is not None:
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
        canonical = canonical_symbol(symbol, Market.EQUITY)
        return self.get_bars_batch([canonical], timeframe, start, end, adjustment=adjustment).get(canonical, [])

    def get_bars_batch(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        adjustment: str = "raw",
    ) -> dict[str, list[Bar]]:
        if not symbols:
            return {}
        symbols, start_at, end_at = self._resolve_batch_request(symbols, timeframe, start, end, adjustment)
        # Dedup identical consecutive provider calls (bounded, fail-closed):
        # same sorted symbols + window + adjustment within TTL returns cached
        # bars without hitting Alpaca. This prevents repeated identical
        # verification calls (per-symbol trailing-window fix) from hammering
        # the provider while keeping measurement of symbols/bars/provider_calls.
        dedup_key = (tuple(sorted(symbols)), timeframe, start_at.isoformat(), end_at.isoformat(), adjustment)
        now = datetime.now(UTC)
        last_at = getattr(self, "_last_batch_at", None)
        if (
            dedup_key == getattr(self, "_last_batch_key", None)
            and last_at is not None
            and (now - last_at).total_seconds() < getattr(self, "_dedup_ttl_seconds", 60.0)
            and getattr(self, "_last_batch_result", None) is not None
        ):
            if getattr(self, "_last_batch_degraded", False):
                # The replayed result carries the original call's degradation.
                self._provider_fallbacks = getattr(self, "_provider_fallbacks", 0) + 1
            # Return a shallow copy so callers cannot mutate the cached dict.
            return {symbol: list(bars) for symbol, bars in self._last_batch_result.items()}  # type: ignore[union-attr]
        fallbacks_before = getattr(self, "_provider_fallbacks", 0)
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
                # long histories download completely.
                result = self._fetch_chunked_windows(
                    symbols,
                    timeframe,
                    start_at,
                    end_at,
                    minutes,
                    adjustment,
                )
                self._last_batch_key = dedup_key
                self._last_batch_result = {s: list(b) for s, b in result.items()}
                self._last_batch_at = now
                self._last_batch_degraded = getattr(self, "_provider_fallbacks", 0) != fallbacks_before
                return result
        result = self._fetch_window(
            symbols,
            timeframe,
            start_at,
            end_at,
            adjustment,
        )
        self._last_batch_key = dedup_key
        self._last_batch_result = {s: list(b) for s, b in result.items()}
        self._last_batch_at = now
        self._last_batch_degraded = getattr(self, "_provider_fallbacks", 0) != fallbacks_before
        return result

    def get_session_daily_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        *,
        feed: str = "sip",
        adjustment: str = "splits",
    ) -> dict[str, Bar]:
        """One uncached daily-bar request on an explicit feed (signal cross-check).

        Returns at most one bar per symbol: the newest daily bar in
        ``[start, end]``. Deliberately bypasses the warmup cache, the dedup
        and the sticky SIP->IEX entitlement fallback of ``get_bars_batch``:
        the caller wants THIS feed or nothing, and a failure here must not
        change the feed the signal history is fetched from. Errors propagate.
        """
        if not symbols:
            return {}
        response = self._stock_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=list(symbols),
                timeframe=_TIMEFRAMES["1d"],
                start=start,
                end=end,
                feed=DataFeed(feed),
                adjustment=Adjustment({"splits": "split"}.get(adjustment, adjustment)),
            )
        )
        try:
            self.provider_calls += 1
            self.provider_symbols_requested += len(symbols)
        except Exception:
            pass
        frame = response.df
        result: dict[str, Bar] = {}
        if frame.empty:
            return result
        for row in frame.reset_index().sort_values("timestamp").itertuples(index=False):
            timestamp = row.timestamp
            if hasattr(timestamp, "to_pydatetime"):
                timestamp = timestamp.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            symbol = canonical_symbol(str(getattr(row, "symbol", symbols[0])), Market.EQUITY)
            result[symbol] = Bar(
                timestamp=timestamp.astimezone(UTC),
                symbol=symbol,
                timeframe="1d",
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                market=Market.EQUITY,
            )
        return result

    def _resolve_batch_request(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime | str | None,
        end: datetime | str | None,
        adjustment: str,
    ) -> tuple[list[str], datetime, datetime]:
        """Validate and normalize one batch request; shared by the plain and
        incremental fetch paths so both enforce identical contracts."""
        if not symbols:
            return [], datetime.now(UTC), datetime.now(UTC)
        if timeframe not in _TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        if adjustment not in {"raw", "splits", "all"}:
            raise ValueError("adjustment must be one of raw, splits, all")
        unsupported = sorted(symbol for symbol in symbols if is_crypto(symbol))
        if unsupported:
            raise ValueError(f"Historical data supports equity symbols only: {', '.join(unsupported)}")
        symbols = [canonical_symbol(symbol, Market.EQUITY) for symbol in symbols]
        end_at = self._parse_datetime(end) or datetime.now(UTC)
        start_at = self._parse_datetime(start) or end_at - timedelta(days=180)
        if start_at >= end_at:
            raise ValueError("Request window must end after it starts")
        return symbols, start_at, end_at

    def get_bars_batch_incremental(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        adjustment: str = "raw",
        *,
        scope: str,
        config_digest: str,
        identity: str = "",
        overlap_timeframes: int = 2,
    ) -> dict[str, list[Bar]]:
        """Cache-assisted incremental batch fetch returning the SAME bars a
        full ``get_bars_batch`` would return, while downloading only the
        missing tail.

        Validation (fail closed to a full refetch when ANY check fails):

        * scope digest — the cached rows must have been written under the
          same ``config_digest`` (recorded via ``scope_meta``); a mismatch
          means the bars may have been produced under a different
          configuration.
        * window head coverage — the cache must reach back to the request
          start; a cache holding only the tail of the window is never
          silently served as if it were complete history.
        * tail freshness — a cache whose newest bar is older than the
          market/timeframe staleness limit is distrustful (a long-dormant
          or partially-written cache) and is fully refetched.

        When the cache validates, each symbol downloads only
        ``[last_cached - overlap_timeframes * step, end]``: the overlap
        re-fetches every bar that could have been forming (and therefore
        partial) when it was cached, so closed cached bars (immutable) plus
        the fresh tail equal a full-window download exactly.
        """
        if not symbols:
            return {}
        if not scope or not config_digest:
            raise ValueError("Incremental fetch requires scope and config_digest")
        symbols, start_at, end_at = self._resolve_batch_request(symbols, timeframe, start, end, adjustment)
        cache = self._cache
        if not isinstance(cache, WarmupCache):
            # Fakes/tests without the real cache degrade to the plain fetch.
            return self.get_bars_batch(symbols, timeframe, start, end, adjustment=adjustment)
        end_at = self._clamp_recent_end(end_at)
        feed_name = self._effective_stock_feed_name
        minutes = TIMEFRAME_MINUTES[timeframe]
        step = timedelta(minutes=minutes)
        fallbacks_before = getattr(self, "_provider_fallbacks", 0)
        # Bars are aligned to the timeframe grid, while request boundaries
        # carry sub-bucket seconds. Cache reads must use the floored start or
        # the first (grid-aligned) bar is excluded by ``timestamp >= start``
        # and silently dropped from a warm reuse. API requests keep the raw
        # boundary exactly as the plain fetch uses it.
        grid_start = _floor_to_grid(start_at, minutes)

        full: list[str] = []
        tail: list[str] = []
        tail_starts: dict[str, datetime] = {}
        head_reference = _first_expected_bar(timeframe, grid_start)
        # The scope checks and the merge below need the same cached window, so
        # each symbol's bars are materialised once and reused.
        windows: dict[str, list[Bar]] = {}
        if cache.scope_is_valid(scope, config_digest):
            for symbol in symbols:
                cached = windows[symbol] = self._cached_window(
                    cache, symbol, timeframe, feed_name, adjustment, grid_start, end_at
                )
                if not cached:
                    full.append(symbol)
                    continue
                if cached[0].timestamp > head_reference:
                    full.append(symbol)
                    continue
                latest = cached[-1].timestamp
                if self._cache_tail_is_stale(timeframe, latest):
                    full.append(symbol)
                    continue
                tail.append(symbol)
                tail_starts[symbol] = latest
        else:
            full = list(symbols)

        fetched: dict[str, list[Bar]] = {}
        if full:
            fetched.update(self.get_bars_batch(full, timeframe, start_at, end_at, adjustment=adjustment))
        if tail:
            tail_start = max(grid_start, min(tail_starts.values()) - overlap_timeframes * step)
            tail_rows = len(tail) * int((end_at - tail_start).total_seconds() // 60 // minutes)
            if tail_rows > _MAX_ROWS_PER_REQUEST:
                # Defensive: a tail window that alone exceeds the row budget
                # (only possible with a huge symbol count) is fetched as a
                # full window so truncation can never occur.
                fetched.update(self.get_bars_batch(tail, timeframe, start_at, end_at, adjustment=adjustment))
            elif tail_start < end_at:
                fetched.update(self._fetch_window(tail, timeframe, tail_start, end_at, adjustment))
            else:
                # Cache already extends to the request end; nothing to fetch.
                for symbol in tail:
                    fetched[symbol] = windows[symbol]
        result: dict[str, list[Bar]] = {}
        for symbol in symbols:
            window = windows.get(symbol)
            if window is None:
                # An invalid scope reaches the cache for the first time here,
                # after the refetch has refreshed it.
                window = windows[symbol] = self._cached_window(
                    cache, symbol, timeframe, feed_name, adjustment, grid_start, end_at
                )
            merged = {bar.timestamp: bar for bar in window}
            merged.update({bar.timestamp: bar for bar in fetched.get(symbol, [])})
            result[symbol] = [merged[key] for key in sorted(merged)]
        # Only a refresh the provider actually answered may (re)stamp the
        # scope: outage-served cache rows can predate this config digest.
        if getattr(self, "_provider_fallbacks", 0) == fallbacks_before:
            cache.scope_set(scope, config_digest, identity)
        return result

    @staticmethod
    def _cached_window(
        cache: WarmupCache,
        symbol: str,
        timeframe: str,
        feed_name: str,
        adjustment: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[Bar]:
        bars = cache.get(
            symbol,
            timeframe,
            market=Market.EQUITY,
            feed=feed_name,
            start=start_at,
            end=end_at,
            adjustment=adjustment,
        )
        if timeframe != "1d":
            return [bar for bar in bars if is_session_open(bar.timestamp)]
        return bars

    @staticmethod
    def _cache_tail_is_stale(
        timeframe: str,
        latest: datetime,
        now: datetime | None = None,
    ) -> bool:
        """Fail-closed staleness limit for incremental reuse.

        These are generous: the head-coverage check is the primary guard
        against serving an incomplete window. The tail limit exists to
        refetch a cache that is so old it may predate data corrections or a
        partially-written table, and to satisfy the fail-closed contract for
        cache reuse.
        """
        now = now or datetime.now(UTC)
        if timeframe == "1d":
            return now - latest > timedelta(days=45)
        return now - latest > timedelta(days=7)

    def _fetch_chunked_windows(
        self,
        symbols: list[str],
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        minutes: int,
        adjustment: str,
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

    def _clamp_recent_end(self, end_at: datetime) -> datetime:
        """Keep a bar request out of the feed's unavailable recent window.

        IEX keeps its historical 15-minute clamp. SIP is clamped to
        ``now - sip_embargo``: Alpaca's Basic plan rejects SIP queries that
        reach into the last 15 minutes ("subscription does not permit querying
        recent SIP data"), which would otherwise fail every ``end=now``
        request (warmup, hourly split-basis verifier).
        """
        feed_name = self._effective_stock_feed_name
        if feed_name == "iex":
            return min(end_at, datetime.now(UTC) - timedelta(minutes=15, seconds=5))
        if feed_name == "sip":
            embargo = getattr(self, "_sip_embargo", timedelta(minutes=15))
            if embargo > timedelta(0):
                return min(end_at, datetime.now(UTC) - embargo)
        return end_at

    @staticmethod
    def _error_status_code(exc: Exception) -> int | None:
        """The HTTP status carried by an API/HTTP error, if any."""
        for holder in (exc, getattr(exc, "response", None)):
            status = getattr(holder, "status_code", None)
            if isinstance(status, int):
                return status
        return None

    @staticmethod
    def _error_body(exc: Exception) -> str:
        """The server's answer, with the request URL removed.

        Only the body may classify an error. A transport error stringifies to
        the request that failed — including ``?feed=sip`` — so matching the
        raw message is how a DNS hiccup was read as a missing entitlement.
        """
        response = getattr(exc, "response", None)
        for value in (getattr(response, "text", None), getattr(exc, "body", None)):
            if isinstance(value, str) and value.strip():
                return value
        return _REQUEST_URL.sub(" ", f"{exc}")

    @staticmethod
    def _is_entitlement_error(exc: Exception) -> bool:
        """Whether ``exc`` is a genuine Alpaca SIP entitlement failure.

        Exactly two shapes qualify: a 401/403 status, or an explicit
        entitlement sentence in the response body. Alpaca's Basic plan
        answers 403 for "subscription does not permit querying recent SIP
        data" too — a data-plan embargo, not a missing entitlement — so that
        sentence is excluded first, whatever the status. Everything else
        (DNS, timeouts, connection resets, 5xx) is NOT an entitlement
        problem, and a real outage still propagates to the caller instead of
        downgrading the process to IEX history for good.
        """
        body = AlpacaHistoricalData._error_body(exc).lower()
        if re.search(r"\brecent\b[^.]*\bdata\b", body):
            return False
        if AlpacaHistoricalData._error_status_code(exc) in _ENTITLEMENT_STATUS_CODES:
            return True
        return any(re.search(pattern, body) for pattern in _ENTITLEMENT_BODY_PATTERNS)

    def _sip_reprobe_due(self, now: datetime) -> bool:
        """Whether the SIP -> IEX downgrade has aged past the re-probe interval."""
        since = getattr(self, "_sip_fallback_since", None)
        if not isinstance(since, datetime):
            return False
        return now - since >= _SIP_REPROBE_INTERVAL

    def _restore_sip_feed(self) -> None:
        """Undo the downgrade after a successful SIP request."""
        self._stock_feed = DataFeed(self._configured_stock_feed)
        self._sip_fallback_active = False
        self._sip_fallback_since = None

    def _fetch_window(
        self,
        symbols: list[str],
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        adjustment: str,
    ) -> dict[str, list[Bar]]:
        # A downgraded client re-probes SIP on the next request once the
        # interval has passed: the entitlement may since have been granted.
        # Bounded to one attempt per interval, and a re-probe that fails with
        # an entitlement error re-downgrades below instead of flapping.
        if getattr(self, "_sip_fallback_active", False) and self._sip_reprobe_due(datetime.now(UTC)):
            log.info("Re-probing the SIP historical feed after a bounded downgrade")
            self._restore_sip_feed()
        end_at = self._clamp_recent_end(end_at)
        feed_name = self._effective_stock_feed_name
        cached = {
            symbol: self._cached_window(self._cache, symbol, timeframe, feed_name, adjustment, start_at, end_at)
            for symbol in symbols
        }
        fresh_cached = {
            symbol: bars for symbol, bars in cached.items() if self._cache_is_fresh(bars, timeframe, end_at)
        }

        def _fallback(message: str) -> dict[str, list[Bar]] | None:
            """Serve the local cache when the provider failed or returned nothing."""
            if len(fresh_cached) == len(symbols) and all(
                self._cache_covers_window(bars, timeframe, start_at, end_at) for bars in fresh_cached.values()
            ):
                log.warning(message)
                self._provider_fallbacks = getattr(self, "_provider_fallbacks", 0) + 1
                return fresh_cached
            return None

        response: Any
        try:
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
            # A configured SIP entitlement the account does not have is a
            # common paper-account situation: degrade to the IEX historical
            # feed instead of failing every warmup request. The downgrade is
            # bounded — a re-probe restores SIP once it answers again — and
            # surfaced via feed_status() so reports can label the coverage
            # honestly. Only a GENUINE entitlement error (401/403 or an
            # explicit entitlement body) gets here; a transport error
            # mentioning `feed=sip` does not.
            if (
                not self._sip_fallback_active
                and self._configured_stock_feed == "sip"
                and self._is_entitlement_error(exc)
            ):
                log.warning(
                    "SIP historical entitlement unavailable (%s); degrading to the "
                    "IEX historical feed until a re-probe succeeds",
                    exc,
                )
                self._stock_feed = DataFeed("iex")
                self._sip_fallback_active = True
                self._sip_fallback_since = datetime.now(UTC)
                return self._fetch_window(
                    symbols,
                    timeframe,
                    start_at,
                    end_at,
                    adjustment,
                )
            served = _fallback(f"Historical API failed ({exc}); using feed-matched local warmup cache")
            if served is not None:
                return served
            raise
        frame = response.df
        if frame.empty:
            served = _fallback("Historical API returned no rows; using feed-matched local cache")
            if served is not None:
                return served
            self._provider_fallbacks = getattr(self, "_provider_fallbacks", 0) + 1
            return {symbol: [] for symbol in symbols}
        bars: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
        for row in frame.reset_index().sort_values("timestamp").itertuples(index=False):
            timestamp = row.timestamp
            if hasattr(timestamp, "to_pydatetime"):
                timestamp = timestamp.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            timestamp = timestamp.astimezone(UTC)
            if timeframe != "1d" and not is_session_open(timestamp):
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
                market=Market.EQUITY,
            )
            bars.setdefault(row_symbol, []).append(item)
        total = sum(len(items) for items in bars.values())
        fetched = [bar for items in bars.values() for bar in items]
        # Use atomic replace_window for full fetches so stale rows already in
        # the cache are removed. Fall back to upsert if replace reports
        # failure (empty payload or error).
        replaced = False
        if fetched:
            try:
                replaced = self._cache.replace_window(
                    fetched, feed=feed_name, adjustment=adjustment, start=start_at, end=end_at
                )
            except Exception as exc:
                log.warning("replace_window failed; falling back to upsert (%s)", exc)
                replaced = False
        if not replaced and fetched:
            self._cache.upsert(fetched, feed=feed_name, adjustment=adjustment)
        for symbol in symbols:
            merged = {bar.timestamp: bar for bar in cached.get(symbol, [])}
            merged.update({bar.timestamp: bar for bar in bars.get(symbol, [])})
            bars[symbol] = [merged[key] for key in sorted(merged)]
        log.info("Fetched %d %s bars for %d symbols", total, timeframe, len(symbols))
        # Measured provider-call observability (for signal/universe hardening).
        try:
            self.provider_calls += 1
            self.provider_symbols_requested += len(symbols)
            self.provider_bars_returned += total
        except Exception:
            pass
        return bars

    @staticmethod
    def _cache_is_fresh(
        bars: list[Bar],
        timeframe: str,
        end_at: datetime,
    ) -> bool:
        if not bars:
            return False
        latest = max(bar.timestamp for bar in bars)
        age = end_at - latest
        if age.total_seconds() < 0:
            return False
        maximum_age = timedelta(days=4 if timeframe != "1d" else 8)
        return bool(age <= maximum_age)

    @staticmethod
    def _cache_covers_window(
        bars: list[Bar],
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
        trading_days = weekday_count(start_at, end_at)
        bars_per_day = max(1, 390 // minutes)
        expected = trading_days * bars_per_day
        return bool(expected <= 0 or unique / expected >= min_pct)

    @staticmethod
    def _parse_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = ["AlpacaHistoricalData"]
