from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.research_store import (
    ProviderMetadata,
    UniverseSnapshotRecord,
    VersionedResearchStore,
    deduplicate_bars,
    hash_dataset,
)
from parayabuck.markets.symbols import canonical_symbol


def _bar(
    timestamp: datetime,
    symbol: str,
    close: float,
    market: Market = Market.EQUITY,
    volume: float = 1000.0,
    timeframe: str = "1d",
) -> Bar:
    return Bar(
        timestamp,
        symbol,
        timeframe,
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
    bars.append(_bar(start, "SPY", 100.0))  # exact duplicate bar for audit counting
    return store, bars


def _provider() -> ProviderMetadata:
    return ProviderMetadata(
        provider_name="alpaca",
        source_feed="sip",
        vendor_symbol="SPY",
        adjustment_method="all",
        currency="USD",
        timezone="America/New_York",
        exchange_calendar="NYSE",
        downloaded_at=datetime(2026, 8, 5, tzinfo=UTC),
        provider_version="1.0.0",
        research_provider_differs_from_execution=False,
    )


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
    unique, _input, _conflicts = deduplicate_bars(bars)
    assert manifest.data_hash == hash_dataset(unique)
    assert set(manifest.symbols) == {"SPY", "QQQ"}
    # The new policy collapses exact duplicates before persisting: the final
    # dataset must contain every bar exactly once and keep an audit count of
    # the collapsed input duplicates.
    assert manifest.duplicate_bars["SPY"] == 0
    assert manifest.input_duplicate_bars["SPY"] == 1
    assert manifest.conflicting_duplicate_bars == {}

    loaded = store.load_dataset("equity_v1")
    assert set(loaded) == {"SPY", "QQQ"}
    assert len(loaded["SPY"]) == 60
    first = loaded["SPY"][0]
    assert first.symbol == "SPY"
    assert first.timestamp == bars[0].timestamp
    round_trip_bars = [bar for bars_list in loaded.values() for bar in bars_list]
    assert hash_dataset(round_trip_bars) == manifest.data_hash


def test_hash_and_csv_round_trip_high_precision_crypto_prices(tmp_path: Path) -> None:
    """The manifest hash must match the persisted CSV for high-precision
    prices; a truncated writer used to break the save->load->hash contract."""
    store = VersionedResearchStore(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        _bar(
            start + timedelta(days=index),
            "BTC/USD",
            1.2545678901234567,
            Market.CRYPTO,
            timeframe="1h",
        )
        for index in range(5)
    ]
    bars.append(
        _bar(
            start + timedelta(days=6),
            "BTC/USD",
            0.00000123456789012345,
            Market.CRYPTO,
            timeframe="1h",
        )
    )
    manifest = store.save_dataset(
        "crypto_v1",
        bars,
        timeframe="1h",
        source_feed="local",
        adjustment="splits",
        universe_version="crypto_v1",
    )
    loaded = store.load_dataset("crypto_v1")
    round_trip = [bar for bars_list in loaded.values() for bar in bars_list]
    assert hash_dataset(round_trip) == manifest.data_hash
    assert all(bar.close == bar.close for bar in round_trip)  # loadable
    # Exact float round trip: the most precise value must survive byte-identical.
    precise = next(bar for bar in round_trip if bar.close < 0.0001)
    assert precise.close == 1.23456789012345e-06


def test_dataset_save_is_atomic_and_resaves_cleanly(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    first = store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="permanent_etf_core_v1",
    )
    assert store.manifest("equity_v1") == first
    # No temporary/backup siblings may remain after a successful save.
    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.name.startswith(".")]
    assert leftovers == []
    # Re-saving over an existing dataset replaces it cleanly.
    second_bars = [
        _bar(bar.timestamp, bar.symbol, bar.close + 1.0) for bar in deduplicate_bars(bars)[0]
    ]
    second = store.save_dataset(
        "equity_v1",
        second_bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="splits",
        universe_version="permanent_etf_core_v1",
    )
    assert second.data_hash != first.data_hash
    assert store.manifest("equity_v1").data_hash == second.data_hash
    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.name.startswith(".")]
    assert leftovers == []


def test_exact_duplicate_collapses_deterministically() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    original = _bar(start, "SPY", 100.0)
    duplicate = _bar(start, "SPY", 100.0)
    unique, input_duplicates, conflicting = deduplicate_bars([original, duplicate])
    assert len(unique) == 1
    assert unique[0] == original
    assert input_duplicates == {"SPY": 1}
    assert conflicting == {}


def test_conflicting_duplicate_rejects_save_and_writes_nothing(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars.append(_bar(start, "QQQ", 123.0))  # same timestamp, different OHLCV
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        store.save_dataset(
            "equity_v1",
            bars,
            timeframe="1d",
            source_feed="iex",
            adjustment="splits",
            universe_version="v1",
        )
    assert not store.dataset_dir("equity_v1").exists()


def test_hash_is_canonical_and_timeframe_aware() -> None:
    lower = _bar(datetime(2024, 1, 1, tzinfo=UTC), "spy", 100.0)
    upper = _bar(datetime(2024, 1, 1, tzinfo=UTC), "SPY", 100.0)
    assert hash_dataset([lower]) == hash_dataset([upper])
    hourly = Bar(
        datetime(2024, 1, 1, tzinfo=UTC),
        "SPY",
        "1h",
        100.0,
        101.0,
        99.0,
        100.5,
        1000.0,
        Market.EQUITY,
    )
    assert hash_dataset([hourly]) != hash_dataset(
        [_bar(datetime(2024, 1, 1, tzinfo=UTC), "SPY", 100.5)]
    )


def test_dataset_save_rejects_unknown_adjustment(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    with pytest.raises(ValueError, match="Adjustment"):
        store.save_dataset(
            "equity_v1",
            bars,
            timeframe="1d",
            source_feed="iex",
            adjustment="unknown",
            universe_version="v1",
        )


def test_provider_metadata_is_persisted_and_validated(tmp_path: Path) -> None:
    store, bars = _dataset(tmp_path)
    manifest = store.save_dataset(
        "equity_v1",
        bars,
        timeframe="1d",
        source_feed="iex",
        adjustment="all",
        universe_version="v1",
        provider_metadata=_provider(),
    )
    assert manifest.provider_metadata is not None
    assert manifest.provider_metadata.adjustment_method == "all"
    reloaded = store.manifest("equity_v1")
    assert reloaded is not None and reloaded.provider_metadata is not None
    assert reloaded.provider_metadata == _provider()

    with pytest.raises(ValueError, match="does not match"):
        store.save_dataset(
            "equity_v2",
            bars,
            timeframe="1d",
            source_feed="iex",
            adjustment="splits",
            universe_version="v1",
            provider_metadata=_provider(),
        )
    with pytest.raises(ValueError, match="unknown"):
        ProviderMetadata(
            provider_name="alpaca",
            source_feed="iex",
            vendor_symbol="SPY",
            adjustment_method="unknown",
            currency="USD",
            timezone="UTC",
            exchange_calendar="NYSE",
            downloaded_at=datetime(2026, 8, 5, tzinfo=UTC),
            provider_version="1.0.0",
            research_provider_differs_from_execution=False,
        )


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
    store.save_universe_snapshots(
        "equity_v1",
        snapshots,
        methodology="trailing_liquidity_v1",
        metadata={"listing_start": "2024-01-02", "lookback_days": 30},
    )
    restored, methodology, metadata = store.load_universe_snapshots("equity_v1")
    assert restored == snapshots
    assert methodology == "trailing_liquidity_v1"
    assert metadata["lookback_days"] == 30


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
