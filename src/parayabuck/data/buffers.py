from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

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
        key = (canonical_symbol(symbol), timeframe)
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

    def is_contiguous(self, symbol: str, timeframe: str) -> bool:
        series = self.get(symbol, timeframe)
        expected_seconds = timeframe_duration(timeframe).total_seconds()
        for previous, current in pairwise(series):
            if current.market == Market.EQUITY:
                continue
            if (current.timestamp - previous.timestamp).total_seconds() > expected_seconds:
                return False
        return True
