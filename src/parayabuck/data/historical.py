from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pandas as pd

from parayabuck.data.base import DataSource
from parayabuck.models import Bar


class HistoricalCSVSource(DataSource):
    """Read historical OHLCV bars from a CSV file.

    Expected columns: timestamp, open, high, low, close, volume
    """

    def __init__(self, data_dir: str = "data/historical") -> None:
        self.data_dir = Path(data_dir)
        self._cache: dict[str, list[Bar]] = {}

    def _resolve_path(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / f"{symbol}_{timeframe}.csv"

    def _load_csv(self, symbol: str, timeframe: str) -> list[Bar]:
        path = self._resolve_path(symbol, timeframe)
        if not path.exists():
            raise FileNotFoundError(
                f"Data file not found: {path}\n"
                f"Expected columns: timestamp, open, high, low, close, volume"
            )

        df = pd.read_csv(path, parse_dates=["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        bars: list[Bar] = []
        for row in df.itertuples(index=False):
            ts = row.timestamp
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            bars.append(
                Bar(
                    timestamp=ts,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(getattr(row, "volume", 0)),
                )
            )
        return bars

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> list[Bar]:
        cache_key = f"{symbol}_{timeframe}"
        if cache_key not in self._cache:
            self._cache[cache_key] = self._load_csv(symbol, timeframe)

        bars = self._cache[cache_key]

        if start:
            start_dt = pd.Timestamp(start)
            bars = [b for b in bars if pd.Timestamp(b.timestamp) >= start_dt]
        if end:
            end_dt = pd.Timestamp(end)
            bars = [b for b in bars if pd.Timestamp(b.timestamp) <= end_dt]

        return bars

    def iterate_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> Iterator[Bar]:
        yield from self.get_bars(symbol, timeframe, start, end)

    def get_latest_bar(self, symbol: str) -> Bar | None:
        return None
