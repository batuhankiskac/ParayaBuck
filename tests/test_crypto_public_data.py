from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.data.crypto_public import (
    BinanceDailyArchive,
    CoinbaseDailyCandles,
    PublicCryptoDataError,
    venue_close_deviation_bps,
)


def test_coinbase_paginates_at_300_and_rejects_gaps() -> None:
    calls: list[dict[str, object]] = []

    def fetch(_url: str, params: dict[str, object]) -> object:
        calls.append(params)
        start = datetime.fromisoformat(str(params["start"]))
        end = datetime.fromisoformat(str(params["end"]))
        result = []
        cursor = start
        while cursor < end:
            result.append([cursor.timestamp(), 9, 12, 10, 11, 100])
            cursor += timedelta(days=1)
        return list(reversed(result))

    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=301)
    bars = CoinbaseDailyCandles(fetch).daily_bars("BTC/USD", start, end)
    assert len(bars) == 301
    assert len(calls) == 2
    assert bars[0].open == 10

    def missing(_url: str, _params: dict[str, object]) -> object:
        return []

    with pytest.raises(PublicCryptoDataError, match="coverage"):
        CoinbaseDailyCandles(missing).daily_bars("ETH/USD", start, start + timedelta(days=1))


def test_binance_daily_archive_requires_valid_checksum() -> None:
    row = "1704067200000,10,12,9,11,100,0,0,0,0,0,0\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("BTCUSDT-1d-2024-01-01.csv", row)
    archive = buffer.getvalue()

    def fetch(url: str) -> bytes:
        if url.endswith(".CHECKSUM"):
            return f"{hashlib.sha256(archive).hexdigest()}  archive.zip\n".encode()
        return archive

    bar = BinanceDailyArchive(fetch).daily_bar("BTC/USD", date(2024, 1, 1))
    assert bar.close == 11

    def corrupt(url: str) -> bytes:
        return b"0" * 64 if url.endswith(".CHECKSUM") else archive

    with pytest.raises(PublicCryptoDataError, match="checksum"):
        BinanceDailyArchive(corrupt).daily_bar("BTC/USD", date(2024, 1, 1))


def test_venue_reconciliation_uses_overlapping_daily_closes() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    primary = CoinbaseDailyCandles(
        lambda _url, _params: [[start.timestamp(), 99, 101, 100, 100, 1]]
    ).daily_bars("BTC/USD", start, start + timedelta(days=1))
    secondary = [type(primary[0])(**{**primary[0].__dict__, "close": 100.1})]
    assert venue_close_deviation_bps(primary, secondary) == pytest.approx(9.99000999)
