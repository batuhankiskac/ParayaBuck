from __future__ import annotations

import hashlib
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from parayabuck.config import EquityUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.universe.equity import STATIC_EQUITY_UNIVERSE_VERSION, equity_universe_digest


@dataclass(frozen=True)
class UniverseSnapshot:
    effective_at: datetime
    symbols: frozenset[str]
    scanner_version: str = ""
    config_digest: str = ""
    candidate_count: int = 0
    # The STREAMED observation set at the snapshot (live parity): the
    # completeness gate requires these symbols to have a closed bar at the
    # decision timestamp, exactly like the live runtime requires its stream.
    # Empty means the legacy snapshot carried no observation set and the
    # gate falls back to the full pool.
    # Order is part of the deployment contract: references/pins lead.
    # Managed pins may later displace the tail.
    observation_set: tuple[str, ...] = ()
    maximum_stream_symbols: int = 0
    pinned_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class PointInTimeUniverse:
    market: Market
    snapshots: tuple[UniverseSnapshot, ...]
    methodology: str

    def __post_init__(self) -> None:
        # Membership lookups use bisect, which requires effective_at-ascending
        # snapshots. The builders produce ascending order; enforcing it here
        # (a no-op for already-sorted input) keeps contains() exact for any
        # direct construction too.
        if any(right.effective_at < left.effective_at for left, right in pairwise(self.snapshots)):
            object.__setattr__(self, "snapshots", tuple(sorted(self.snapshots, key=lambda item: item.effective_at)))

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        if market != self.market:
            return False
        # Snapshots are effective_at-ascending, so the last snapshot effective
        # at or before ``timestamp`` is found by bisection (O(log n)) instead
        # of a linear scan on every universe membership probe.
        index = bisect_right(self.snapshots, timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return False
        return canonical_symbol(symbol, market) in self.snapshots[index - 1].symbols

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market.value,
            "methodology": self.methodology,
            "snapshots": [
                {
                    "effective_at": snapshot.effective_at.isoformat(),
                    "symbols": sorted(snapshot.symbols),
                    "observation_set": list(snapshot.observation_set),
                    "maximum_stream_symbols": snapshot.maximum_stream_symbols,
                    "pinned_symbols": list(snapshot.pinned_symbols),
                    "scanner_version": snapshot.scanner_version,
                    "config_digest": snapshot.config_digest,
                    "candidate_count": snapshot.candidate_count,
                }
                for snapshot in self.snapshots
            ],
        }


def build_static_equity_point_in_time_universe(
    bars: list[Bar],
    config: EquityUniverseConfig,
) -> PointInTimeUniverse:
    """Point-in-time view of the static equity universe.

    Each configured symbol joins at its first bar, so a symbol listed later
    in the bar set is never a member before it has data. A new snapshot is
    emitted only when the membership changes.
    """
    configured = set(config.symbols)
    digest = equity_universe_digest(config)
    snapshots: list[UniverseSnapshot] = []
    members: set[str] = set()
    for bar in sorted(
        (bar for bar in bars if bar.market == Market.EQUITY),
        key=lambda item: (item.timestamp, item.symbol),
    ):
        symbol = canonical_symbol(bar.symbol, Market.EQUITY)
        if symbol not in configured or symbol in members:
            continue
        members.add(symbol)
        observation = tuple(item for item in config.symbols if item in members)
        snapshot = UniverseSnapshot(
            effective_at=bar.timestamp,
            symbols=frozenset(observation),
            scanner_version=STATIC_EQUITY_UNIVERSE_VERSION,
            config_digest=digest,
            candidate_count=len(observation),
            observation_set=observation,
            maximum_stream_symbols=config.maximum_stream_symbols,
            pinned_symbols=observation,
        )
        if snapshots and snapshots[-1].effective_at == bar.timestamp:
            snapshots[-1] = snapshot
        else:
            snapshots.append(snapshot)
    return PointInTimeUniverse(
        Market.EQUITY,
        tuple(snapshots),
        "static configured equity universe; each symbol joins at its first bar",
    )


def hash_point_in_time_universe(universe: PointInTimeUniverse) -> str:
    """Deterministic hash of methodology + snapshot boundaries and members.

    Equal data and methodology must always produce the same hash so a
    persisted snapshot set can be verified against a recomputed one and a
    silently different universe is rejected.
    """
    digest = hashlib.sha256()
    digest.update(universe.market.value.encode())
    digest.update("|".join(universe.methodology.split()).encode())
    for snapshot in sorted(universe.snapshots, key=lambda item: item.effective_at):
        digest.update(snapshot.effective_at.isoformat().encode())
        digest.update(b"\n")
        digest.update("|".join(sorted(snapshot.symbols)).encode())
        digest.update(b"\n")
        digest.update("|".join(snapshot.observation_set).encode())
        digest.update(b"\n")
        digest.update(str(snapshot.maximum_stream_symbols).encode())
        digest.update(b"\n")
        digest.update("|".join(snapshot.pinned_symbols).encode())
        digest.update(b"\n")
        # Two retired, always-empty observation-strategy identity slots. The
        # separators stay so persisted universe hashes remain valid.
        digest.update(b"\n\n")
        digest.update(snapshot.scanner_version.encode())
        digest.update(snapshot.config_digest.encode())
    return digest.hexdigest()


def verify_persisted_universe(
    universe: PointInTimeUniverse,
    expected_hash: str,
) -> None:
    """Fail-closed check that the run's universe matches the persisted one."""
    if not expected_hash:
        return
    recomputed = hash_point_in_time_universe(universe)
    if recomputed != expected_hash:
        raise ValueError(
            f"Universe snapshot hash mismatch: persisted {expected_hash[:12]}... vs recomputed {recomputed[:12]}..."
        )


__all__ = [
    "PointInTimeUniverse",
    "UniverseSnapshot",
    "build_static_equity_point_in_time_universe",
    "hash_point_in_time_universe",
    "verify_persisted_universe",
]
