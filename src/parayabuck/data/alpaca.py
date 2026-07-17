from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.data.base import DataSource
from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.data.alpaca")

_TIMEFRAME_MAP: dict[str, TimeFrame] = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}


def parse_timeframe(tf: str) -> TimeFrame:
    if tf not in _TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe '{tf}'. Supported: {list(_TIMEFRAME_MAP)}")
    return _TIMEFRAME_MAP[tf]


class AlpacaDataSource(DataSource):
    """Historical and live stock bar data from Alpaca."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        self._stock_client = StockHistoricalDataClient(api_key, secret_key)
        self._crypto_client = CryptoHistoricalDataClient(api_key, secret_key)
        self._cache: dict[str, list[Bar]] = {}

    def _fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None,
        end: datetime | str | None,
    ) -> list[Bar]:
        tf = parse_timeframe(timeframe)

        # Default: last 6 months if not specified
        end_dt = self._parse_dt(end) or datetime.now(UTC)
        start_dt = self._parse_dt(start) or (end_dt - timedelta(days=180))

        crypto = is_crypto(symbol)

        # Free plan: can't query recent stock data (last ~15 min for SIP).
        # Crypto has no such delay — it's real-time on the free plan.
        if not crypto and end_dt > datetime.now(UTC) - timedelta(minutes=20):
            end_dt = datetime.now(UTC) - timedelta(minutes=20)

        kind = "crypto" if crypto else "stock"
        log.info(f"Fetching {symbol} {timeframe} {kind} bars: {start_dt.date()} → {end_dt.date()}")
        response: Any
        if crypto:
            response = self._crypto_client.get_crypto_bars(
                CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf,
                    start=start_dt,
                    end=end_dt,
                    limit=10000,
                )
            )
        else:
            response = self._stock_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf,
                    start=start_dt,
                    end=end_dt,
                    limit=10000,
                )
            )
        df = response.df

        if df.empty:
            return []

        # df is multi-indexed (symbol, timestamp), reset to get columns
        df = df.reset_index()
        df = df.sort_values("timestamp")

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
                    volume=int(row.volume),
                )
            )

        log.info(f"Got {len(bars)} bars for {symbol}")
        return bars

    @staticmethod
    def _parse_dt(val: datetime | str | None) -> datetime | None:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        return datetime.fromisoformat(val).replace(tzinfo=UTC)

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        use_cache: bool = True,
    ) -> list[Bar]:
        cache_key = f"{symbol}_{timeframe}_{start}_{end}"
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]
        bars = self._fetch_bars(symbol, timeframe, start, end)
        if use_cache:
            self._cache[cache_key] = bars
        return bars

    def clear_cache(self) -> None:
        self._cache.clear()

    def clear_cache_for_symbol(self, symbol: str) -> None:
        keys_to_remove = [k for k in self._cache if k.startswith(f"{symbol}_")]
        for k in keys_to_remove:
            del self._cache[k]

    def iterate_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> Iterator[Bar]:
        yield from self.get_bars(symbol, timeframe, start, end)

    def get_latest_bar(self, symbol: str) -> Bar | None:
        bars = self._fetch_bars(symbol, "1m", None, None)
        return bars[-1] if bars else None
