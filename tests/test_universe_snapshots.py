from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    build_crypto_point_in_time_universe,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
    verify_persisted_universe,
)
from parayabuck.config import (
    CryptoScannerConfig,
    CryptoUniverseConfig,
    EquityScannerConfig,
    EquityUniverseConfig,
)
from parayabuck.core.models import Bar, Market
from parayabuck.universe.crypto_scanner import (
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
)
from parayabuck.universe.scanner import EquityUniverseScanner, equity_candidate_from_bars


def _crypto_bar(timestamp: datetime, symbol: str, close: float, volume: float) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1h",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        volume,
        Market.CRYPTO,
    )


def _crypto_history(days: int = 240, volume: float = 100_000.0) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD")
    bars: list[Bar] = []
    for index in range(days * 24):
        timestamp = start + timedelta(hours=index)
        for symbol in symbols:
            drift = 0.0001 * (index % 7)
            bars.append(_crypto_bar(timestamp, symbol, 100.0 * (1 + drift), volume))
    return bars


def test_universe_hash_is_deterministic() -> None:
    history = _crypto_history()
    first = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    second = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    assert hash_point_in_time_universe(first) == hash_point_in_time_universe(second)


def test_universe_hash_differs_for_different_methodology_or_members() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    base_hash = hash_point_in_time_universe(universe)
    # Same snapshot members, different market label -> different hash.
    relabeled = PointInTimeUniverse(Market.EQUITY, universe.snapshots, universe.methodology)
    assert hash_point_in_time_universe(relabeled) != base_hash
    # Different methodology text -> different hash.
    other_method = PointInTimeUniverse(universe.market, universe.snapshots, "other_methodology")
    assert hash_point_in_time_universe(other_method) != base_hash
    # Different snapshot membership -> different hash.
    changed_members = (
        UniverseSnapshot(
            universe.snapshots[0].effective_at,
            frozenset({"BTC/USD", "ETH/USD"}),
        ),
    )
    different = PointInTimeUniverse(Market.CRYPTO, changed_members, universe.methodology)
    assert hash_point_in_time_universe(different) != base_hash


def test_verify_persisted_universe_rejects_silent_recompute() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    persisted = hash_point_in_time_universe(universe)
    verify_persisted_universe(universe, persisted)  # no error
    different = build_crypto_point_in_time_universe(history, maximum_symbols=2)
    with pytest.raises(ValueError, match="mismatch"):
        verify_persisted_universe(different, persisted)
    # Empty expected hash means "no constraint" (legacy experiments).
    verify_persisted_universe(different, "")


def test_persisted_snapshot_records_round_trip() -> None:
    history = _crypto_history()
    universe = build_crypto_point_in_time_universe(history, maximum_symbols=3)
    assert universe.snapshots
    assert hash_point_in_time_universe(universe) == hash_point_in_time_universe(universe)
    first_snapshot = universe.snapshots[0]
    assert first_snapshot.symbols
    allowed = ("BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD")
    assert all(symbol in allowed for symbol in first_snapshot.symbols)


def test_historical_equity_adapter_uses_same_production_scanner_policy() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [
        Bar(
            start + timedelta(days=day),
            symbol,
            "1d",
            100,
            102,
            98,
            100 + day * drift,
            volume,
            Market.EQUITY,
        )
        for day in range(30)
        for symbol, drift, volume in (("AAA", 0.01, 100_000), ("ZZZ", 0.02, 2_000_000))
    ]
    config = EquityUniverseConfig(
        maximum_stream_symbols=1,
        target_shortlist=1,
        reference_symbols=(),
        scanner=EquityScannerConfig(
            minimum_price=1,
            minimum_median_dollar_volume=1,
            minimum_data_completeness=0.9,
            minimum_atr_pct=0.001,
        ),
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=1,
        lookback_days=30,
        minimum_history_ratio=0.9,
        equity_config=config,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: [
            bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at
        ]
        for symbol in ("AAA", "ZZZ")
    }
    direct = EquityUniverseScanner(config).scan(
        [
            equity_candidate_from_bars(symbol, series, expected_bar_count=20)
            for symbol, series in history.items()
        ],
        data_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        selection_timestamp=snapshot.effective_at,
    )
    assert snapshot.symbols == frozenset(direct.selected_symbols)
    assert snapshot.scanner_version == direct.scanner_version


def test_historical_crypto_adapter_uses_same_production_scanner_policy() -> None:
    bars = _crypto_history(days=4)
    scanner_config = CryptoScannerConfig(
        lookback_days=2,
        minimum_data_completeness=0.8,
        minimum_median_dollar_volume=1,
        minimum_atr_pct=0.001,
    )
    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=3,
        scanner=scanner_config,
    )
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.CRYPTO,
        maximum_symbols=3,
        lookback_days=2,
        minimum_history_ratio=0.8,
        crypto_config=config,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: [
            bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at
        ]
        for symbol in {bar.symbol for bar in bars}
    }
    direct = CryptoUniverseScanner(config).scan(
        [
            crypto_candidate_from_bars(
                symbol,
                series,
                expected_bar_count=48,
                tradable=True,
                status="active",
                btc_bars=history["BTC/USD"],
            )
            for symbol, series in history.items()
        ],
        data_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        selection_timestamp=snapshot.effective_at,
    )
    assert snapshot.symbols == frozenset(direct.selected_symbols)
    assert snapshot.scanner_version == direct.scanner_version
