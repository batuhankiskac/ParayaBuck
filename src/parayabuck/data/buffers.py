from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol


class BarRevision(StrEnum):
    FINAL = "final"
    CORRECTION = "correction"


class BarMutation(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    STALE_REJECTED = "stale_rejected"


class BarStore:
    def __init__(self, capacity: int = 500) -> None:
        self.capacity = capacity
        self._bars: dict[tuple[str, str], deque[Bar]] = defaultdict(lambda: deque(maxlen=self.capacity))

    def _normalize(self, bar: Bar, canonical: str) -> Bar:
        """Return ``bar`` with a canonical symbol, preserving validation."""
        if bar.symbol == canonical:
            return bar
        return replace(bar, symbol=canonical)

    def upsert(self, bar: Bar, revision: BarRevision = BarRevision.FINAL) -> BarMutation:
        key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
        series = self._bars[key]
        normalized = self._normalize(bar, key[0])
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
        # Alpaca equity bars are trade-based. A quiet IEX/SIP interval is
        # omitted rather than emitted as a zero-volume candle, so a timestamp
        # jump alone cannot distinguish normal market silence from a
        # transport gap; a newer bar is always a plain insert.
        series.append(normalized)
        return BarMutation.INSERTED

    def get(self, symbol: str, timeframe: str, market: Market | None = None) -> list[Bar]:
        key = (canonical_symbol(symbol, market), timeframe)
        series = self._bars.get(key)
        if series is None:
            return []
        return list(series)

    def seed(self, bars: list[Bar]) -> None:
        grouped: dict[tuple[str, str], dict[datetime, Bar]] = {}
        for bar in bars:
            key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
            if key not in grouped:
                existing = self._bars.get(key)
                grouped[key] = {item.timestamp: item for item in existing} if existing else {}
            normalized = self._normalize(bar, key[0])
            grouped[key][normalized.timestamp] = normalized
        for key, by_timestamp in grouped.items():
            self._bars[key] = deque(
                sorted(by_timestamp.values(), key=lambda item: item.timestamp)[-self.capacity :],
                maxlen=self.capacity,
            )
