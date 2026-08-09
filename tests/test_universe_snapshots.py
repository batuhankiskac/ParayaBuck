from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    build_crypto_point_in_time_universe,
    hash_point_in_time_universe,
    verify_persisted_universe,
)
from parayabuck.core.models import Bar, Market


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
