from __future__ import annotations

from datetime import datetime, timedelta

from parayabuck.core.models import Bar
from parayabuck.logger import get_logger
from parayabuck.markets.timeframes import NEW_YORK, TIMEFRAME_MINUTES

log = get_logger("parayabuck.data.aggregator")


class TimeframeAggregator:
    """Aggregate closed one-minute stream bars into larger closed candles."""

    def __init__(self, timeframe: str, finalization_grace_seconds: float = 0.0) -> None:
        if timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"Unsupported streaming timeframe: {timeframe}")
        self.timeframe = timeframe
        self.minutes = TIMEFRAME_MINUTES[timeframe]
        if finalization_grace_seconds < 0:
            raise ValueError("finalization_grace_seconds cannot be negative")
        self.finalization_grace = timedelta(seconds=finalization_grace_seconds)
        self._current: dict[str, Bar] = {}
        self._components: dict[str, dict[datetime, Bar]] = {}
        self._last_components: dict[str, dict[datetime, Bar]] = {}
        # Latest bucket already emitted as final, per symbol.
        self._closed_through: dict[str, datetime] = {}
        # Symbols whose session-close bucket was flushed WITHOUT its last
        # expected minute, mapped to the newest minute actually received.
        # The runtime falls back to the provider daily bar for these.
        self.incomplete_session_close: dict[str, datetime] = {}

    def update(self, bar: Bar) -> Bar | None:
        if self.minutes == 1:
            return Bar(**{**bar.__dict__, "timeframe": self.timeframe})
        bucket_time = self._bucket(bar.timestamp)
        closed_through = self._closed_through.get(bar.symbol)
        if closed_through is not None and bucket_time <= closed_through:
            # A late minute of a bucket that a flush already emitted must not
            # reopen it: the one-minute stub would later be emitted as a
            # second "final" candle for the same timestamp.
            self._log_dropped_late_minute(bar, bucket_time)
            return None
        current = self._current.get(bar.symbol)
        if current is None:
            self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
            self._components[bar.symbol] = {bar.timestamp: bar}
            return None
        if current.timestamp == bucket_time:
            self._components[bar.symbol][bar.timestamp] = bar
            self._current[bar.symbol] = self._aggregate(self._components[bar.symbol], bucket_time)
            return None
        if bucket_time < current.timestamp:
            # A late minute for an already-closed bucket must not close the
            # forming bucket early (a partial candle would be emitted as
            # final) nor reopen the old bucket; corrections go through
            # ``update_correction``.
            self._log_dropped_late_minute(bar, bucket_time)
            return None
        self._last_components[bar.symbol] = self._components.get(bar.symbol, {})
        self._closed_through[bar.symbol] = current.timestamp
        self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
        self._components[bar.symbol] = {bar.timestamp: bar}
        return current

    def _log_dropped_late_minute(self, bar: Bar, bucket_time: datetime) -> None:
        # The closed candle permanently lacks this minute; surface it so a
        # finalization grace shorter than the feed latency is visible.
        log.warning(
            "Late stream minute dropped: symbol=%s timeframe=%s minute=%s bucket=%s",
            bar.symbol,
            self.timeframe,
            bar.timestamp.isoformat(),
            bucket_time.isoformat(),
        )

    def update_correction(self, bar: Bar) -> Bar | None:
        if self.minutes == 1:
            return Bar(**{**bar.__dict__, "timeframe": self.timeframe})
        bucket_time = self._bucket(bar.timestamp)
        current = self._current.get(bar.symbol)
        if current is not None and current.timestamp == bucket_time:
            components = self._components[bar.symbol]
            if bar.timestamp not in components:
                return None
            components[bar.timestamp] = bar
            self._current[bar.symbol] = self._aggregate(components, bucket_time)
            return None
        previous_components = self._last_components.get(bar.symbol)
        if not previous_components or bar.timestamp not in previous_components:
            return None
        previous_components[bar.timestamp] = bar
        return self._aggregate(previous_components, bucket_time)

    def _bucket(self, timestamp: datetime) -> datetime:
        """Session-anchored bucket: buckets start at the 09:30 ET open."""
        local = timestamp.astimezone(NEW_YORK)
        anchor = local.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed = max(0, int((local - anchor).total_seconds() // 60))
        bucket_offset = elapsed - elapsed % self.minutes
        return (anchor + timedelta(minutes=bucket_offset)).astimezone(timestamp.tzinfo)

    def flush_until(self, now: datetime) -> list[Bar]:
        flushed: list[Bar] = []
        duration = timedelta(minutes=self.minutes)
        for symbol, bar in list(self._current.items()):
            if bar.timestamp + duration + self.finalization_grace <= now:
                flushed.append(self._current.pop(symbol))
                self._closed_through[symbol] = bar.timestamp
                self._last_components[symbol] = self._components.pop(symbol, {})
        return sorted(flushed, key=lambda bar: (bar.timestamp, bar.symbol))

    def flush_session_close(
        self,
        session_close: datetime,
        *,
        expected_close: datetime | None = None,
    ) -> list[Bar]:
        """Flush the open bucket of ``session_close``'s New York date.

        With ``expected_close`` (the session's real NYSE close, early closes
        included) a bucket is only emitted when its newest one-minute
        component reaches the session's last minute. A bucket whose tail
        minutes never arrived — a stalled stream, or a final minute more than
        the grace late — is withheld and recorded in
        :attr:`incomplete_session_close`: a daily bar built from a 15:58
        close is a wrong decision input, not a slightly stale one.
        """
        close_date = session_close.astimezone(NEW_YORK).date()
        last_expected_minute = None if expected_close is None else expected_close - timedelta(minutes=1)
        self.incomplete_session_close = {}
        flushed: list[Bar] = []
        for symbol, bar in list(self._current.items()):
            if bar.timestamp.astimezone(NEW_YORK).date() != close_date:
                continue
            components = self._components.get(symbol, {})
            newest_component = max(components) if components else None
            # Always retire the bucket, complete or not: the flush runs on
            # every decision-loop iteration and must report an incomplete
            # session once, not on every pass.
            self._closed_through[symbol] = bar.timestamp
            self._last_components[symbol] = self._components.pop(symbol, {})
            candidate = self._current.pop(symbol)
            if last_expected_minute is not None and (
                newest_component is None or newest_component < last_expected_minute
            ):
                self.incomplete_session_close[symbol] = newest_component or candidate.timestamp
                log.error(
                    "Session-close bucket incomplete: symbol=%s timeframe=%s session=%s "
                    "newest_minute=%s expected_minute=%s",
                    symbol,
                    self.timeframe,
                    close_date.isoformat(),
                    newest_component.isoformat() if newest_component else "none",
                    last_expected_minute.astimezone(NEW_YORK).isoformat(),
                )
                continue
            flushed.append(candidate)
        return sorted(flushed, key=lambda bar: (bar.timestamp, bar.symbol))

    def _new_bucket(self, bar: Bar, timestamp: datetime) -> Bar:
        return Bar(
            timestamp=timestamp,
            symbol=bar.symbol,
            timeframe=self.timeframe,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            market=bar.market,
        )

    def _aggregate(self, components: dict[datetime, Bar], timestamp: datetime) -> Bar:
        return _combine_bars([components[key] for key in sorted(components)], timestamp, self.timeframe)


def _combine_bars(ordered: list[Bar], timestamp: datetime, timeframe: str) -> Bar:
    """Build one aggregated bar from time-ordered component bars."""
    first, last = ordered[0], ordered[-1]
    return Bar(
        timestamp=timestamp,
        symbol=first.symbol,
        timeframe=timeframe,
        open=first.open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=last.close,
        volume=sum(bar.volume for bar in ordered),
        market=first.market,
    )


__all__ = ["TimeframeAggregator"]
