from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from parayabuck.core.models import Bar, Market
from parayabuck.logger import get_logger
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.data.alpaca")

_TIMEFRAMES = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}


class AlpacaHistoricalData:
    """Bounded Alpaca REST client used to warm up the streaming engine."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        stock_feed: str = "iex",
        data_delayed: bool = False,
    ) -> None:
        self._stock_client = StockHistoricalDataClient(api_key, secret_key)
        self._crypto_client = CryptoHistoricalDataClient(api_key, secret_key)
        patch_session_timeout(self._stock_client._session, timeout=30.0)
        patch_session_timeout(self._crypto_client._session, timeout=30.0)
        feed = "delayed_sip" if data_delayed else stock_feed
        try:
            self._stock_feed = DataFeed(feed)
        except ValueError as exc:
            raise ValueError(f"Unsupported Alpaca stock feed: {feed}") from exc

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> list[Bar]:
        if timeframe not in _TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        end_at = self._parse_datetime(end) or datetime.now(UTC)
        start_at = self._parse_datetime(start) or end_at - timedelta(days=180)
        market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
        response: Any
        if market == Market.CRYPTO:
            response = self._crypto_client.get_crypto_bars(
                CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=_TIMEFRAMES[timeframe],
                    start=start_at,
                    end=end_at,
                    limit=10_000,
                )
            )
        else:
            response = self._stock_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=_TIMEFRAMES[timeframe],
                    start=start_at,
                    end=end_at,
                    limit=10_000,
                    feed=self._stock_feed,
                    adjustment=Adjustment.ALL,
                )
            )
        frame = response.df
        if frame.empty:
            return []
        bars: list[Bar] = []
        for row in frame.reset_index().sort_values("timestamp").itertuples(index=False):
            timestamp = row.timestamp
            if hasattr(timestamp, "to_pydatetime"):
                timestamp = timestamp.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            bars.append(
                Bar(
                    timestamp=timestamp.astimezone(UTC),
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                    market=market,
                )
            )
        log.info("Fetched %d %s bars for %s", len(bars), timeframe, symbol)
        return bars

    @staticmethod
    def _parse_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = ["AlpacaHistoricalData"]
