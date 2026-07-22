from __future__ import annotations

from collections import defaultdict, deque

from parayabuck.core.models import Bar


class BarStore:
    def __init__(self, capacity: int = 500) -> None:
        self.capacity = capacity
        self._bars: dict[tuple[str, str], deque[Bar]] = defaultdict(
            lambda: deque(maxlen=self.capacity)
        )

    def add(self, bar: Bar) -> bool:
        key = (bar.symbol, bar.timeframe)
        series = self._bars[key]
        if series and bar.timestamp <= series[-1].timestamp:
            return False
        series.append(bar)
        return True

    def get(self, symbol: str, timeframe: str) -> list[Bar]:
        return list(self._bars[(symbol, timeframe)])

    def seed(self, bars: list[Bar]) -> None:
        for bar in sorted(bars, key=lambda item: item.timestamp):
            self.add(bar)
