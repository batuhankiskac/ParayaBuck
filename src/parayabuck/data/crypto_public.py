"""Free, checksum-aware crypto research data sources."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import requests

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol

JsonFetcher = Callable[[str, dict[str, object]], object]
BytesFetcher = Callable[[str], bytes]


class PublicCryptoDataError(RuntimeError):
    pass


class CoinbaseDailyCandles:
    endpoint = "https://api.exchange.coinbase.com/products/{product}/candles"
    maximum_candles = 300

    def __init__(self, fetch_json: JsonFetcher | None = None) -> None:
        self._fetch_json = fetch_json or self._default_fetch_json

    @staticmethod
    def _default_fetch_json(url: str, params: dict[str, object]) -> object:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Coinbase range must be ordered and timezone-aware")
        canonical = canonical_symbol(symbol, Market.CRYPTO)
        if canonical not in {"BTC/USD", "ETH/USD"}:
            raise ValueError("The approvable Coinbase universe is BTC/USD and ETH/USD")
        product = canonical.replace("/", "-")
        cursor = _utc_day(start)
        stop = _utc_day(end)
        rows: dict[datetime, Bar] = {}
        chunk = timedelta(days=self.maximum_candles)
        while cursor < stop:
            chunk_end = min(stop, cursor + chunk)
            payload = self._fetch_json(
                self.endpoint.format(product=product),
                {
                    "granularity": 86400,
                    "start": cursor.isoformat(),
                    "end": chunk_end.isoformat(),
                },
            )
            if not isinstance(payload, list):
                raise PublicCryptoDataError("Coinbase candles response is not a list")
            for item in payload:
                if not isinstance(item, list | tuple) or len(item) < 6:
                    raise PublicCryptoDataError("Malformed Coinbase candle")
                timestamp = _utc_day(datetime.fromtimestamp(float(item[0]), tz=UTC))
                if cursor <= timestamp < chunk_end:
                    rows[timestamp] = Bar(
                        timestamp,
                        canonical,
                        "1d",
                        float(item[3]),
                        float(item[2]),
                        float(item[1]),
                        float(item[4]),
                        float(item[5]),
                        Market.CRYPTO,
                    )
            cursor = chunk_end
        ordered = [rows[key] for key in sorted(rows)]
        _require_daily_coverage(ordered, _utc_day(start), stop, "Coinbase")
        return ordered


class BinanceDailyArchive:
    root = "https://data.binance.vision/data/spot/daily/klines"

    def __init__(self, fetch_bytes: BytesFetcher | None = None) -> None:
        self._fetch_bytes = fetch_bytes or self._default_fetch_bytes

    @staticmethod
    def _default_fetch_bytes(url: str) -> bytes:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return bytes(response.content)

    def daily_bar(self, symbol: str, day: date) -> Bar:
        canonical = canonical_symbol(symbol, Market.CRYPTO)
        if canonical not in {"BTC/USD", "ETH/USD"}:
            raise ValueError("The validation archive universe is BTC/USD and ETH/USD")
        pair = canonical.replace("/USD", "USDT").replace("/", "")
        filename = f"{pair}-1d-{day.isoformat()}.zip"
        url = f"{self.root}/{pair}/1d/{filename}"
        archive = self._fetch_bytes(url)
        checksum_payload = self._fetch_bytes(url + ".CHECKSUM").decode("ascii", errors="strict")
        expected = checksum_payload.split()[0].lower()
        actual = hashlib.sha256(archive).hexdigest()
        if len(expected) != 64 or expected != actual:
            raise PublicCryptoDataError(f"Binance checksum mismatch: {filename}")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                names = bundle.namelist()
                if len(names) != 1:
                    raise PublicCryptoDataError("Binance archive must contain one CSV")
                content = bundle.read(names[0]).decode("utf-8")
        except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
            raise PublicCryptoDataError("Invalid Binance daily archive") from exc
        records = list(csv.reader(io.StringIO(content)))
        if len(records) != 1 or len(records[0]) < 6:
            raise PublicCryptoDataError("Binance daily archive must contain one kline")
        row = records[0]
        timestamp = _timestamp_from_binance(row[0])
        if timestamp.date() != day:
            raise PublicCryptoDataError("Binance archive date does not match filename")
        return Bar(
            timestamp,
            canonical,
            "1d",
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
            Market.CRYPTO,
        )


def venue_close_deviation_bps(primary: list[Bar], secondary: list[Bar]) -> float:
    left = {bar.timestamp: bar.close for bar in primary}
    right = {bar.timestamp: bar.close for bar in secondary}
    common = sorted(set(left) & set(right))
    if not common:
        raise PublicCryptoDataError("Venue reconciliation has no overlapping candles")
    return max(abs(left[key] / right[key] - 1.0) * 10_000 for key in common)


def _require_daily_coverage(bars: list[Bar], start: datetime, end: datetime, provider: str) -> None:
    actual = {bar.timestamp for bar in bars}
    expected: set[datetime] = set()
    cursor = start
    while cursor < end:
        expected.add(cursor)
        cursor += timedelta(days=1)
    missing = sorted(expected - actual)
    if missing:
        preview = ", ".join(item.date().isoformat() for item in missing[:3])
        raise PublicCryptoDataError(
            f"{provider} daily coverage has {len(missing)} gap(s): {preview}"
        )


def _timestamp_from_binance(raw: str) -> datetime:
    value = int(raw)
    divisor = 1_000_000 if value > 10**14 else 1_000
    return _utc_day(datetime.fromtimestamp(value / divisor, tz=UTC))


def _utc_day(value: datetime) -> datetime:
    utc = value.astimezone(UTC)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0)


__all__ = [
    "BinanceDailyArchive",
    "CoinbaseDailyCandles",
    "PublicCryptoDataError",
    "venue_close_deviation_bps",
]
