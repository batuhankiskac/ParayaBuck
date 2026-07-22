from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime

from parayabuck.broker_models import Bar


class DataSource(ABC):
    """Abstract base for market data sources (historical or live)."""

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> list[Bar]:
        """Return a list of bars for the given symbol and timeframe."""
        ...

    @abstractmethod
    def iterate_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> Iterator[Bar]:
        """Yield bars one-by-one — used by backtest and live loops."""
        ...

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Bar | None:
        """Return the most recent bar (for live trading)."""
        ...
