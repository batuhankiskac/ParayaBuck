from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache
from parayabuck.logger import get_logger
from parayabuck.markets.nyse_calendar import NEW_YORK, nyse_sessions
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    TIMEFRAME_MINUTES,
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


def _normalize_row_timestamp(raw: object) -> datetime:
    """Provider row timestamp as UTC (pandas, naive and aware inputs)."""
    timestamp: Any = raw
    if hasattr(timestamp, "to_pydatetime"):
        timestamp = timestamp.to_pydatetime()
    assert isinstance(timestamp, datetime)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    normalized: datetime = timestamp.astimezone(UTC)
    return normalized


def _session_bounds_for(dates: list[date]) -> dict[date, tuple[datetime, datetime] | None]:
    """One batched NYSE range lookup for many intraday session dates.

    A per-timestamp ``is_session_open`` call resolves its own uncached
    single-date schedule (~50-100 ms), so a 730-day hourly window costs tens
    of seconds on the live warmup path. The batched ``nyse_sessions`` range
    carries exactly the same bounds; dates missing from the range (a calendar
    failure) fall back to the degraded per-date lookup, which carries its
    own fallback.
    """
    unique = sorted(set(dates))
    if not unique:
        return {}
    try:
        ranged = {
            day: (market_open, market_close)
            for day, market_open, market_close in nyse_sessions(min(unique), max(unique))
        }
    except Exception:
        ranged = {}
    bounds: dict[date, tuple[datetime, datetime] | None] = dict(ranged)
    for day in unique:
        if day not in bounds:
            try:
                from parayabuck.markets.nyse_calendar import session_bounds

                bounds[day] = session_bounds(day)
            except Exception:
                bounds[day] = None
    return bounds


class AlpacaHistoricalData:
    """Bounded Alpaca equity REST client used to warm up the streaming engine.

    ``_last_batch_degraded`` tells the caller of the latest ``get_bars_batch``
    whether any part of its answer came from the local cache instead of the
    provider (the split-basis verifier treats such an answer as unverified).
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
        # Bumped whenever a window is answered without real provider rows (the
        # outage cache fallback or an empty response), so the split-basis
        # verifier never counts cache rows as provider coverage.
        self._provider_fallbacks: int = 0
        self._last_batch_degraded: bool = False
        # Observability counters (measured provider calls).
        self.provider_calls: int = 0
        self.provider_symbols_requested: int = 0
        self.provider_bars_returned: int = 0

    def feed_status(self) -> dict[str, object]:
        """Configured and effective equity historical feed for reporting."""
        bars_effective = "iex" if self._sip_fallback_active else self._configured_stock_feed
        fallback_since = getattr(self, "_sip_fallback_since", None)
        return {
            "configured": self._configured_stock_feed,
            "effective": bars_effective,
            # Quote and bar coverage share the downgrade: no quote-only path
            # ever sets a separate flag, so both keys derive from the bar
            # fallback the health surface already reads.
            "quote_effective": bars_effective,
            "sip_fallback_active": self._sip_fallback_active,
            "sip_quote_fallback_active": self._sip_fallback_active,
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
                self._last_batch_degraded = getattr(self, "_provider_fallbacks", 0) != fallbacks_before
                return result
        result = self._fetch_window(
            symbols,
            timeframe,
            start_at,
            end_at,
            adjustment,
        )
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
        """Validate and normalize one batch request."""
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
            bounds = _session_bounds_for([bar.timestamp.astimezone(NEW_YORK).date() for bar in bars])
            kept: list[Bar] = []
            for bar in bars:
                window = bounds.get(bar.timestamp.astimezone(NEW_YORK).date())
                if window is not None and window[0] <= bar.timestamp.astimezone(UTC) < window[1]:
                    kept.append(bar)
            return kept
        return bars

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

    def _reprobe_sip_if_due(self) -> None:
        """Lift an aged SIP -> IEX downgrade so the next request re-probes SIP.

        The entitlement may since have been granted. Bounded to one attempt
        per interval, and a re-probe that fails with an entitlement error
        re-downgrades in ``_fetch_window`` instead of flapping. Callers that
        pick a cache feed name run this FIRST so the name matches the feed
        the request will actually use.
        """
        if getattr(self, "_sip_fallback_active", False) and self._sip_reprobe_due(datetime.now(UTC)):
            log.info("Re-probing the SIP historical feed after a bounded downgrade")
            self._restore_sip_feed()

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
        self._reprobe_sip_if_due()
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
        intraday_bounds: dict[date, tuple[datetime, datetime] | None] | None = None
        if timeframe != "1d":
            intraday_bounds = _session_bounds_for(
                [
                    _normalize_row_timestamp(row.timestamp).astimezone(NEW_YORK).date()
                    for row in frame.reset_index().itertuples(index=False)
                ]
            )
        # Rows are keyed by the REQUESTED symbol: a provider row for a symbol
        # nobody asked for (a successor ticker after a corporate action) is
        # dropped, never leaked into the answer or the chunked merge.
        requested = {canonical_symbol(symbol, Market.EQUITY): symbol for symbol in symbols}
        unrequested: set[str] = set()
        invalid: dict[str, int] = {}
        # A stable sort keeps the provider's order among same-instant rows so
        # the de-duplication below is deterministically last-wins.
        for row in frame.reset_index().sort_values("timestamp", kind="stable").itertuples(index=False):
            timestamp = _normalize_row_timestamp(row.timestamp)
            if timeframe != "1d":
                assert intraday_bounds is not None
                window = intraday_bounds.get(timestamp.astimezone(NEW_YORK).date())
                if window is None or not (window[0] <= timestamp < window[1]):
                    continue
            row_symbol = str(getattr(row, "symbol", symbols[0]))
            try:
                key = requested.get(canonical_symbol(row_symbol, Market.EQUITY))
            except ValueError:
                key = None
            if key is None:
                unrequested.add(row_symbol)
                continue
            # One hostile row (NaN, inf, high < low) must not abort every
            # other symbol's window: skip it, and the per-symbol completeness
            # and history-adequacy gates fail that symbol closed.
            try:
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
            except (TypeError, ValueError):
                invalid[key] = invalid.get(key, 0) + 1
                continue
            bars[key].append(item)
        if unrequested:
            log.warning(
                "Historical API returned %s rows for unrequested symbols %s; dropped",
                timeframe,
                ",".join(sorted(unrequested)),
            )
        if invalid:
            log.warning(
                "Historical API returned invalid %s rows (%s); skipped",
                timeframe,
                ", ".join(f"{symbol}={count}" for symbol, count in sorted(invalid.items())),
            )
        # Same-instant rows (a republished or corrected bar) keep the LAST
        # one, exactly as the chunked merge does, so the in-memory seed and
        # the cache upsert can never read different closes for one session.
        for symbol, items in bars.items():
            deduped = {bar.timestamp: bar for bar in items}
            bars[symbol] = [deduped[key] for key in sorted(deduped)]
        total = sum(len(items) for items in bars.values())
        fetched = [bar for items in bars.values() for bar in items]
        # A partial provider window must never evict good cached bars: only
        # replace the cached [start, end] window when every answered symbol
        # covers it — spanning the window AND containing every cached
        # session in it (a provider hole must not delete a bar the cache
        # already holds); otherwise upsert.
        cached_in_window = {
            symbol: [bar for bar in cached.get(symbol, []) if start_at <= bar.timestamp <= end_at] for symbol in symbols
        }
        complete = {
            symbol
            for symbol in symbols
            if self._window_is_complete(bars.get(symbol, []), start_at, end_at, cached_in_window.get(symbol, []))
        }
        replaced = False
        if fetched and complete == set(symbols):
            try:
                replaced = self._cache.replace_window(
                    fetched, feed=feed_name, adjustment=adjustment, start=start_at, end=end_at
                )
            except Exception as exc:
                log.warning("replace_window failed; falling back to upsert (%s)", exc)
                replaced = False
        if not replaced and fetched:
            self._cache.upsert(fetched, feed=feed_name, adjustment=adjustment)
            if complete != set(symbols):
                log.warning(
                    "Historical API returned a partial %s window for %s; cached bars preserved (upsert, no window replace)",
                    timeframe,
                    ",".join(sorted(set(symbols) - complete)),
                )
        # The provider is authoritative for the window it answered: cached
        # rows are never mixed into its answer. A symbol it returned nothing
        # for is served from the cache, and the answer is marked degraded so
        # no caller (the split-basis verifier above all) counts cache rows as
        # provider coverage.
        cache_only = sorted(symbol for symbol in symbols if not bars.get(symbol) and cached.get(symbol))
        for symbol in cache_only:
            bars[symbol] = list(cached[symbol])
        if cache_only:
            log.warning(
                "Historical API returned no %s rows for %s; served from the local cache (degraded)",
                timeframe,
                ",".join(cache_only),
            )
            self._provider_fallbacks = getattr(self, "_provider_fallbacks", 0) + 1
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
    def _expected_session_slots(timeframe: str, start_at: datetime, end_at: datetime) -> dict[date, int] | None:
        """Completed bar slots per NYSE session inside ``[start_at, end_at]``.

        Holidays carry no bars, so the window is measured against the
        exchange calendar, never Mon-Fri weekdays. A daily bar is labelled
        00:00 New York on its session date and only counts once the session
        has closed inside the window. ``None`` when the calendar fails.
        """
        minutes = TIMEFRAME_MINUTES[timeframe]
        try:
            sessions = nyse_sessions(start_at.astimezone(NEW_YORK).date(), end_at.astimezone(NEW_YORK).date())
        except Exception:
            return None
        slots: dict[date, int] = {}
        for day, market_open, market_close in sessions:
            if timeframe == "1d":
                if start_at <= datetime.combine(day, time(0), NEW_YORK) and market_close <= end_at:
                    slots[day] = 1
                continue
            low, high = max(market_open, start_at), min(market_close, end_at)
            count = int((high - low).total_seconds() // (minutes * 60)) if high > low else 0
            if count > 0:
                slots[day] = count
        return slots

    @staticmethod
    def _window_is_complete(
        bars: list[Bar], start_at: datetime, end_at: datetime, cached: list[Bar] | None = None
    ) -> bool:
        """Whether fetched rows may atomically replace the cached window.

        The fetched rows must contain every session the cache already holds
        in the window (a provider hole must keep the write to an upsert so
        cached bars survive) AND cover the window's NYSE sessions: every
        completed session for daily bars, the first and last session plus
        ``MIN_CACHE_COVERAGE_PCT`` of the session bar slots for intraday
        bars (thin names legitimately skip minutes). A calendar failure
        fails closed to an upsert.
        """
        if not bars:
            return False
        stamps = sorted({bar.timestamp for bar in bars})
        if cached:
            cached_stamps = {bar.timestamp for bar in cached}
            if not cached_stamps.issubset(set(stamps)):
                return False
        timeframe = bars[0].timeframe
        minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
        if minutes <= 0:
            return True
        expected = AlpacaHistoricalData._expected_session_slots(timeframe, start_at, end_at)
        if expected is None:
            return False
        if not expected:
            return True
        days = {stamp.astimezone(NEW_YORK).date() for stamp in stamps}
        if timeframe == "1d":
            return set(expected).issubset(days)
        if min(expected) not in days or max(expected) not in days:
            return False
        return len(stamps) >= MIN_CACHE_COVERAGE_PCT * sum(expected.values())

    @staticmethod
    def _parse_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = ["AlpacaHistoricalData"]
