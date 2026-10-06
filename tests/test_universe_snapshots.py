from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    build_static_equity_point_in_time_universe,
    hash_point_in_time_universe,
)
from parayabuck.config import EquityUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.universe import STATIC_EQUITY_UNIVERSE_VERSION, equity_universe_digest


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


_CONFIG = EquityUniverseConfig(maximum_stream_symbols=4, symbols=("SPY", "QQQ", "QLD"))


def _equity_universe(config: EquityUniverseConfig = _CONFIG) -> PointInTimeUniverse:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = [_equity_bar(start + timedelta(days=day), symbol) for day in range(5) for symbol in ("SPY", "QQQ")]
    bars += [_equity_bar(start + timedelta(days=2 + day), "QLD", 50.0) for day in range(3)]
    return build_static_equity_point_in_time_universe(bars, config)


def test_universe_hash_is_deterministic() -> None:
    assert hash_point_in_time_universe(_equity_universe()) == hash_point_in_time_universe(_equity_universe())


def test_universe_hash_is_stable_across_releases() -> None:
    # Persisted universe hashes (backtest reports, research receipts) must
    # keep verifying: the digest byte layout is frozen.
    assert hash_point_in_time_universe(_equity_universe()) == (
        "4ecd5224601c1b6a2885777f389d2a6fb87abb656babaa73c789088a3cc68f80"
    )


def test_universe_hash_differs_for_different_methodology_or_members() -> None:
    universe = _equity_universe()
    base_hash = hash_point_in_time_universe(universe)
    # Same snapshot members, different market label -> different hash.
    relabeled = PointInTimeUniverse(Market.CRYPTO, universe.snapshots, universe.methodology)
    assert hash_point_in_time_universe(relabeled) != base_hash
    # Different methodology text -> different hash.
    other_method = PointInTimeUniverse(universe.market, universe.snapshots, "other_methodology")
    assert hash_point_in_time_universe(other_method) != base_hash
    # Different snapshot membership -> different hash.
    changed_members = (
        UniverseSnapshot(
            universe.snapshots[0].effective_at,
            frozenset({"SPY"}),
        ),
    )
    different = PointInTimeUniverse(Market.EQUITY, changed_members, universe.methodology)
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
    )
    reordered = UniverseSnapshot(
        effective,
        members,
        observation_set=("SPY", "BBB", "AAA"),
        maximum_stream_symbols=3,
        pinned_symbols=("SPY",),
    )
    resized = UniverseSnapshot(
        effective,
        members,
        observation_set=base.observation_set,
        maximum_stream_symbols=4,
        pinned_symbols=("SPY",),
    )
    base_universe = PointInTimeUniverse(Market.EQUITY, (base,), "same")
    reordered_universe = PointInTimeUniverse(Market.EQUITY, (reordered,), "same")
    resized_universe = PointInTimeUniverse(Market.EQUITY, (resized,), "same")
    assert hash_point_in_time_universe(base_universe) != hash_point_in_time_universe(reordered_universe)
    assert hash_point_in_time_universe(base_universe) != hash_point_in_time_universe(resized_universe)


def test_static_equity_universe_joins_each_symbol_at_its_first_bar() -> None:
    """A symbol is never a member before it has data, and a snapshot is
    emitted only when the membership actually changes."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    universe = _equity_universe()

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
    assert second.config_digest == equity_universe_digest(_CONFIG)
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
