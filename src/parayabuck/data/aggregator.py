from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise

from parayabuck.core.models import Bar, Market
from parayabuck.markets.timeframes import NEW_YORK, TIMEFRAME_MINUTES


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

    def update(self, bar: Bar) -> Bar | None:
        if self.minutes == 1:
            return Bar(**{**bar.__dict__, "timeframe": self.timeframe})
        bucket_time = self._bucket(bar.timestamp, bar.market)
        current = self._current.get(bar.symbol)
        if current is None:
            self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
            self._components[bar.symbol] = {bar.timestamp: bar}
            return None
        if current.timestamp == bucket_time:
            self._components[bar.symbol][bar.timestamp] = bar
            self._current[bar.symbol] = self._aggregate(self._components[bar.symbol], bucket_time)
            return None
        self._last_components[bar.symbol] = self._components.get(bar.symbol, {})
        self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
        self._components[bar.symbol] = {bar.timestamp: bar}
        return current

    def update_correction(self, bar: Bar) -> Bar | None:
        if self.minutes == 1:
            return Bar(**{**bar.__dict__, "timeframe": self.timeframe})
        bucket_time = self._bucket(bar.timestamp, bar.market)
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

    def _bucket(self, timestamp: datetime, market: Market) -> datetime:
        value = timestamp
        if market == Market.EQUITY:
            local = value.astimezone(NEW_YORK)
            anchor = local.replace(hour=9, minute=30, second=0, microsecond=0)
            elapsed = max(0, int((local - anchor).total_seconds() // 60))
            bucket_offset = elapsed - elapsed % self.minutes
            return (anchor + timedelta(minutes=bucket_offset)).astimezone(value.tzinfo)
        minute_of_day = value.hour * 60 + value.minute
        bucket = minute_of_day - minute_of_day % self.minutes
        return value.replace(hour=bucket // 60, minute=bucket % 60, second=0, microsecond=0)

    def flush_until(self, now: datetime) -> list[Bar]:
        flushed: list[Bar] = []
        duration = timedelta(minutes=self.minutes)
        for symbol, bar in list(self._current.items()):
            if bar.timestamp + duration + self.finalization_grace <= now:
                flushed.append(self._current.pop(symbol))
                self._last_components[symbol] = self._components.pop(symbol, {})
        return sorted(flushed, key=lambda bar: (bar.timestamp, bar.symbol))

    def flush_session_close(self, session_close: datetime) -> list[Bar]:
        close_date = session_close.astimezone(NEW_YORK).date()
        flushed: list[Bar] = []
        for symbol, bar in list(self._current.items()):
            if bar.market == Market.EQUITY and bar.timestamp.astimezone(NEW_YORK).date() == close_date:
                flushed.append(self._current.pop(symbol))
                self._last_components[symbol] = self._components.pop(symbol, {})
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


def aggregate_historical_bars(
    bars: list[Bar],
    target_timeframe: str,
    *,
    require_complete: bool = True,
) -> list[Bar]:
    """Aggregate closed historical bars without leaking incomplete target bars."""
    if not bars:
        return []
    source_timeframes = {bar.timeframe for bar in bars}
    if len(source_timeframes) != 1:
        raise ValueError("Historical aggregation requires one source timeframe")
    source_timeframe = next(iter(source_timeframes))
    source_minutes = TIMEFRAME_MINUTES[source_timeframe]
    target_minutes = TIMEFRAME_MINUTES[target_timeframe]
    if target_minutes < source_minutes or target_minutes % source_minutes:
        raise ValueError("Target timeframe must be an integer multiple of the source")
    if target_minutes == source_minutes:
        return [Bar(**{**bar.__dict__, "timeframe": target_timeframe}) for bar in bars]

    aggregator = TimeframeAggregator(target_timeframe)
    buckets: dict[tuple[str, datetime], list[Bar]] = {}
    for bar in sorted(bars, key=lambda item: (item.symbol, item.timestamp)):
        bucket = aggregator._bucket(bar.timestamp, bar.market)
        buckets.setdefault((bar.symbol, bucket), []).append(bar)
    expected = target_minutes // source_minutes
    result: list[Bar] = []
    source_delta = timedelta(minutes=source_minutes)
    # Each bucket list holds one symbol appended in timestamp order already.
    for (_symbol, bucket), ordered in sorted(buckets.items(), key=lambda item: (item[0][1], item[0][0])):
        contiguous = all(
            current.timestamp - previous.timestamp == source_delta for previous, current in pairwise(ordered)
        )
        if require_complete and (len(ordered) != expected or not contiguous):
            continue
        result.append(_combine_bars(ordered, bucket, target_timeframe))
    return result


__all__ = ["TimeframeAggregator", "aggregate_historical_bars"]
