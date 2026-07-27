from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from parayabuck.core.models import Bar, Market

_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
_NEW_YORK = ZoneInfo("America/New_York")


class TimeframeAggregator:
    """Aggregate closed one-minute stream bars into larger closed candles."""

    def __init__(self, timeframe: str) -> None:
        if timeframe not in _MINUTES:
            raise ValueError(f"Unsupported streaming timeframe: {timeframe}")
        self.timeframe = timeframe
        self.minutes = _MINUTES[timeframe]
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
            local = value.astimezone(_NEW_YORK)
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
            if bar.timestamp + duration <= now:
                flushed.append(self._current.pop(symbol))
                self._last_components[symbol] = self._components.pop(symbol, {})
        return sorted(flushed, key=lambda bar: (bar.timestamp, bar.symbol))

    def flush_session_close(self, session_close: datetime) -> list[Bar]:
        close_date = session_close.astimezone(_NEW_YORK).date()
        flushed: list[Bar] = []
        for symbol, bar in list(self._current.items()):
            if (
                bar.market == Market.EQUITY
                and bar.timestamp.astimezone(_NEW_YORK).date() == close_date
            ):
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
        ordered = [components[key] for key in sorted(components)]
        first, last = ordered[0], ordered[-1]
        return Bar(
            timestamp=timestamp,
            symbol=first.symbol,
            timeframe=self.timeframe,
            open=first.open,
            high=max(bar.high for bar in ordered),
            low=min(bar.low for bar in ordered),
            close=last.close,
            volume=sum(bar.volume for bar in ordered),
            market=first.market,
        )
