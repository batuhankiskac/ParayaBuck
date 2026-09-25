from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    build_crypto_point_in_time_universe,
    build_static_equity_point_in_time_universe,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
    verify_persisted_universe,
)
from parayabuck.config import (
    CryptoScannerConfig,
    CryptoUniverseConfig,
    EquityUniverseConfig,
)
from parayabuck.core.models import Bar, Market
from parayabuck.universe import STATIC_EQUITY_UNIVERSE_VERSION, equity_universe_digest
from parayabuck.universe.crypto_scanner import (
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
)


def _equity_bar(timestamp: datetime, symbol: str, close: float = 100.0) -> Bar:
    return Bar(
        timestamp,
        symbol,
        "1d",
        close * 0.999,
        close * 1.006,
        close * 0.994,
        close,
        2_000_000,
        Market.EQUITY,
    )


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


def test_universe_hash_commits_observation_order_and_capacity() -> None:
    effective = datetime(2025, 1, 2, tzinfo=UTC)
    members = frozenset({"SPY", "AAA", "BBB"})
    base = UniverseSnapshot(
        effective,
        members,
        observation_set=("SPY", "AAA", "BBB"),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY",),
        observation_strategy_name="equity_leveraged_trend_v1",
        observation_strategy_version="3.0.0",
    )
    reordered = UniverseSnapshot(
        effective,
        members,
        observation_set=("SPY", "BBB", "AAA"),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY",),
        observation_strategy_name="equity_leveraged_trend_v1",
        observation_strategy_version="3.0.0",
    )
    resized = UniverseSnapshot(
        effective,
        members,
        observation_set=base.observation_set,
        maximum_stream_symbols=4,
        pinned_symbols=("SPY",),
        observation_strategy_name="equity_leveraged_trend_v1",
        observation_strategy_version="3.0.0",
    )
    base_universe = PointInTimeUniverse(Market.EQUITY, (base,), "same")
    reordered_universe = PointInTimeUniverse(Market.EQUITY, (reordered,), "same")
    resized_universe = PointInTimeUniverse(Market.EQUITY, (resized,), "same")
    assert hash_point_in_time_universe(base_universe) != hash_point_in_time_universe(reordered_universe)
    assert hash_point_in_time_universe(base_universe) != hash_point_in_time_universe(resized_universe)


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
        maximum_symbols=3,
        lookback_days=2,
        minimum_history_ratio=0.8,
        crypto_config=config,
    )
    snapshot = universe.snapshots[-1]
    history = {
        symbol: [bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at]
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


def test_static_equity_universe_joins_each_symbol_at_its_first_bar() -> None:
    """A symbol is never a member before it has data, and a snapshot is
    emitted only when the membership actually changes."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    config = EquityUniverseConfig(maximum_stream_symbols=4, symbols=("SPY", "QQQ", "QLD"))
    bars = [_equity_bar(start + timedelta(days=day), symbol) for day in range(5) for symbol in ("SPY", "QQQ")]
    bars += [_equity_bar(start + timedelta(days=2 + day), "QLD", 50.0) for day in range(3)]
    universe = build_static_equity_point_in_time_universe(bars, config)

    # SPY and QQQ share their first bar, so they collapse into one snapshot;
    # QLD joins two days later and no further snapshot is emitted.
    assert [snapshot.effective_at for snapshot in universe.snapshots] == [start, start + timedelta(days=2)]
    first, second = universe.snapshots
    assert first.symbols == frozenset({"SPY", "QQQ"})
    assert first.observation_set == ("SPY", "QQQ")
    assert second.symbols == frozenset({"SPY", "QQQ", "QLD"})
    # Observation order follows configuration, not bar-arrival order.
    assert second.observation_set == ("SPY", "QQQ", "QLD")
    assert second.pinned_symbols == ("SPY", "QQQ", "QLD")
    assert second.maximum_stream_symbols == 4
    assert second.scanner_version == STATIC_EQUITY_UNIVERSE_VERSION
    assert second.config_digest == equity_universe_digest(config)
    assert not universe.contains("QLD", Market.EQUITY, start + timedelta(days=1))
    assert universe.contains("QLD", Market.EQUITY, start + timedelta(days=2))


def test_static_equity_digest_commits_symbols_and_stream_capacity() -> None:
    base = EquityUniverseConfig(maximum_stream_symbols=4, symbols=("SPY", "QQQ", "QLD"))
    assert equity_universe_digest(base) == equity_universe_digest(
        EquityUniverseConfig(maximum_stream_symbols=4, symbols=("SPY", "QQQ", "QLD"))
    )
    reordered = EquityUniverseConfig(maximum_stream_symbols=4, symbols=("QLD", "QQQ", "SPY"))
    resized = EquityUniverseConfig(maximum_stream_symbols=5, symbols=("SPY", "QQQ", "QLD"))
    dropped = EquityUniverseConfig(maximum_stream_symbols=4, symbols=("SPY", "QQQ"))
    digests = {
        equity_universe_digest(base),
        equity_universe_digest(reordered),
        equity_universe_digest(resized),
        equity_universe_digest(dropped),
    }
    assert len(digests) == 4


def test_static_equity_universe_ignores_bars_outside_the_configured_set() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    config = EquityUniverseConfig(maximum_stream_symbols=3, symbols=("SPY", "QQQ"))
    bars = [_equity_bar(start + timedelta(days=day), symbol) for day in range(3) for symbol in ("SPY", "QQQ", "GLD")]
    universe = build_static_equity_point_in_time_universe(bars, config)
    assert len(universe.snapshots) == 1
    assert universe.snapshots[0].symbols == frozenset({"SPY", "QQQ"})
    assert not universe.contains("GLD", Market.EQUITY, bars[-1].timestamp)


def test_incremental_universe_handles_wide_datasets_without_rescanning() -> None:
    """A broad multi-year crypto universe must build in linear time: the
    rolling-window builder keeps a wide dataset well inside a bound that a
    per-timestamp full rescan would blow past."""
    import time

    start = datetime(2021, 1, 1, tzinfo=UTC)
    symbols = tuple(f"SYM{index:03d}/USD" for index in range(60))
    bars = [
        Bar(
            start + timedelta(days=day),
            symbol,
            "1d",
            99.0,
            102.0,
            98.0,
            100.0 + day * 0.001,
            5_000_000.0,
            Market.CRYPTO,
        )
        for day in range(1200)
        for symbol in symbols
    ]
    began = time.monotonic()
    universe = build_trailing_point_in_time_universe(
        bars,
        maximum_symbols=8,
        lookback_days=60,
        minimum_history_ratio=0.5,
    )
    elapsed = time.monotonic() - began
    assert universe.snapshots, "wide dataset must produce snapshots"
    assert elapsed < 30.0, f"incremental builder too slow: {elapsed:.1f}s"
