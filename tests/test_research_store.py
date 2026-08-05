from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.research_store import (
    UniverseSnapshotRecord,
    VersionedResearchStore,
    hash_dataset,
)
from parayabuck.markets.symbols import canonical_symbol


def _bar(
    timestamp: datetime,
    symbol: str,
    close: float,
    market: Market = Market.EQUITY,
    volume: float = 1000.0,
) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        market,
    )


def _dataset(tmp_path: Path) -> tuple[VersionedResearchStore, list[Bar]]:
    store = VersionedResearchStore(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        _bar(start + timedelta(days=index), symbol, 100.0 + index)
        for index in range(60)
        for symbol in ("SPY", "QQQ")
    ]
    bars.append(_bar(start, "SPY", 100.0))  # duplicate bar for counting
    return store, bars


def test_dataset_round_trip_preserves_bars_and_manifest(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    manifest = store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="permanent_etf_core_v1",
    )
    assert manifest.data_hash == hash_dataset(bars)
    assert set(manifest.symbols) == {"SPY", "QQQ"}
    assert manifest.duplicate_bars["SPY"] == 1

    loaded = store.load_dataset("equity_v1")
    assert set(loaded) == {"SPY", "QQQ"}
    assert len(loaded["SPY"]) == 61
    first = loaded["SPY"][0]
    assert first.symbol == "SPY"
    assert first.timestamp == bars[0].timestamp
    round_trip_bars = [bar for bars_list in loaded.values() for bar in bars_list]
    assert hash_dataset(round_trip_bars) == manifest.data_hash


def test_dataset_partitions_are_symbol_timeframe_based(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
    )
    files = sorted(path.name for path in store.dataset_dir("equity_v1").iterdir())
    assert any(name.startswith("SPY_1d") for name in files)
    assert any(name.startswith("QQQ_1d") for name in files)


def test_load_with_symbol_and_date_filters(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
    )
    cutoff = datetime(2024, 1, 20, tzinfo=UTC)
    filtered = store.load_dataset("equity_v1", symbols=("SPY",), start=cutoff)
    assert set(filtered) == {"SPY"}
    assert all(bar.timestamp >= cutoff for bar in filtered["SPY"])


def test_manifest_is_persisted_and_readable(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    manifest = store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="all",
        universe_version="v1",
        notes="audit run",
    )
    reloaded = store.manifest("equity_v1")
    assert reloaded is not None
    assert reloaded.format == manifest.format
    assert reloaded.adjustment == "all"
    assert reloaded.universe_version == "v1"
    assert reloaded.notes == "audit run"
    assert store.list_datasets() == ("equity_v1",)


def test_universe_snapshots_round_trip(tmp_path: Path) -> None:
    store, _bars = _dataset(tmp_path)
    store.save_dataset(
        "equity_v1",
        _bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
    )
    snapshots = (
        UniverseSnapshotRecord(datetime(2024, 2, 1, tzinfo=UTC), ("SPY", "QQQ")),
        UniverseSnapshotRecord(datetime(2024, 3, 1, tzinfo=UTC), ("SPY",)),
    )
    store.save_universe_snapshots("equity_v1", snapshots)
    restored = store.load_universe_snapshots("equity_v1")
    assert restored == snapshots


def test_empty_dataset_is_rejected(tmp_path: Path) -> None:
    store = VersionedResearchStore(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        store.save_dataset(
            "empty",
            [],
            timeframe="1d",
            source_feed="iex",
            adjustment="splits",
            universe_version="v1",
        )


def test_unknown_dataset_raises_and_delete_removes(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load_dataset("missing")
    store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="v1",
    )
    store.delete_dataset("equity_v1")
    assert store.list_datasets() == ()


def test_canonical_symbols_are_used_in_partitions(tmp_path: Path) -> None:
    store = VersionedResearchStore(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    crypto_bars = [_bar(start, "btc/usd", 100.0, Market.CRYPTO, volume=10_000.0)]
    store.save_dataset(
        "crypto_v1",
        crypto_bars,
        timeframe="1d",
        source_feed="crypto",
        adjustment="splits",
        universe_version="crypto_trailing_liquidity_v1",
    )
    loaded = store.load_dataset("crypto_v1")
    assert canonical_symbol("btc/usd", Market.CRYPTO) in loaded
    assert loaded["BTC/USD"][0].market == Market.CRYPTO
