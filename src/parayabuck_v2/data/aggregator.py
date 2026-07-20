from __future__ import annotations

from datetime import datetime

from parayabuck_v2.core.models import Bar

_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}


class TimeframeAggregator:
    """Aggregate closed one-minute stream bars into larger closed candles."""

    def __init__(self, timeframe: str) -> None:
        if timeframe not in _MINUTES:
            raise ValueError(f"Unsupported streaming timeframe: {timeframe}")
        self.timeframe = timeframe
        self.minutes = _MINUTES[timeframe]
        self._current: dict[str, Bar] = {}

    def update(self, bar: Bar) -> Bar | None:
        if self.minutes == 1:
            return Bar(**{**bar.__dict__, "timeframe": self.timeframe})
        bucket_time = self._bucket(bar.timestamp)
        current = self._current.get(bar.symbol)
        if current is None:
            self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
            return None
        if current.timestamp == bucket_time:
            self._current[bar.symbol] = Bar(
                timestamp=current.timestamp,
                symbol=bar.symbol,
                timeframe=self.timeframe,
                open=current.open,
                high=max(current.high, bar.high),
                low=min(current.low, bar.low),
                close=bar.close,
                volume=current.volume + bar.volume,
                market=bar.market,
            )
            return None
        self._current[bar.symbol] = self._new_bucket(bar, bucket_time)
        return current

    def _bucket(self, timestamp: datetime) -> datetime:
        minute_of_day = timestamp.hour * 60 + timestamp.minute
        bucket = minute_of_day - minute_of_day % self.minutes
        return timestamp.replace(hour=bucket // 60, minute=bucket % 60, second=0, microsecond=0)

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
