from __future__ import annotations

from collections import defaultdict, deque
from enum import StrEnum

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import timeframe_duration


class BarRevision(StrEnum):
    FINAL = "final"
    CORRECTION = "correction"


class BarMutation(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    STALE_REJECTED = "stale_rejected"
    GAP_DETECTED = "gap_detected"


class BarStore:
    def __init__(self, capacity: int = 500) -> None:
        self.capacity = capacity
        self._bars: dict[tuple[str, str], deque[Bar]] = defaultdict(
            lambda: deque(maxlen=self.capacity)
        )

    def add(self, bar: Bar) -> bool:
        return self.upsert(bar) in {BarMutation.INSERTED, BarMutation.GAP_DETECTED}

    def upsert(self, bar: Bar, revision: BarRevision = BarRevision.FINAL) -> BarMutation:
        key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
        series = self._bars[key]
        normalized = bar if bar.symbol == key[0] else Bar(**{**bar.__dict__, "symbol": key[0]})
        if series and normalized.timestamp == series[-1].timestamp:
            if normalized == series[-1]:
                return BarMutation.DUPLICATE
            if revision == BarRevision.CORRECTION:
                series[-1] = normalized
                return BarMutation.UPDATED
            return BarMutation.STALE_REJECTED
        if series and normalized.timestamp < series[-1].timestamp:
            if revision == BarRevision.CORRECTION:
                for index in range(len(series) - 1, -1, -1):
                    if series[index].timestamp == normalized.timestamp:
                        if series[index] == normalized:
                            return BarMutation.DUPLICATE
                        series[index] = normalized
                        return BarMutation.UPDATED
            return BarMutation.STALE_REJECTED
        mutation = BarMutation.INSERTED
        if series:
            delta = normalized.timestamp - series[-1].timestamp
            expected_seconds = timeframe_duration(bar.timeframe).total_seconds()
            # Alpaca equity bars are trade-based. A quiet IEX/SIP interval is
            # omitted rather than emitted as a zero-volume candle, so a
            # timestamp jump alone cannot distinguish normal market silence
            # from a transport gap. Crypto trades continuously and remains
            # suitable for this continuity check.
            if (
                delta.total_seconds() > expected_seconds
                and bar.market != Market.EQUITY
                and bar.timeframe != "1d"
            ):
                mutation = BarMutation.GAP_DETECTED
        series.append(normalized)
        return mutation

    def get(self, symbol: str, timeframe: str) -> list[Bar]:
        return list(self._bars[(canonical_symbol(symbol), timeframe)])

    def seed(self, bars: list[Bar]) -> None:
        grouped: dict[tuple[str, str], dict[object, Bar]] = {}
        for bar in bars:
            key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
            if key not in grouped:
                grouped[key] = {item.timestamp: item for item in self._bars[key]}
            normalized = bar if bar.symbol == key[0] else Bar(**{**bar.__dict__, "symbol": key[0]})
            grouped[key][normalized.timestamp] = normalized
        for key, by_timestamp in grouped.items():
            self._bars[key] = deque(
                sorted(by_timestamp.values(), key=lambda item: item.timestamp)[-self.capacity :],
                maxlen=self.capacity,
            )

    def is_contiguous(self, symbol: str, timeframe: str) -> bool:
        series = self.get(symbol, timeframe)
        expected_seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "1d": 86400,
        }[timeframe]
        for previous, current in zip(series, series[1:], strict=False):
            if current.market == Market.EQUITY:
                continue
            if (current.timestamp - previous.timestamp).total_seconds() > expected_seconds:
                return False
        return True
