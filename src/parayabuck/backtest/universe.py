from __future__ import annotations

import hashlib
import statistics
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from parayabuck.config import (
    CryptoScannerConfig,
    CryptoUniverseConfig,
    EquityScannerConfig,
    EquityUniverseConfig,
)
from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import timeframe_duration
from parayabuck.universe.crypto_scanner import (
    CryptoScannerResult,
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
)
from parayabuck.universe.scanner import (
    EquityUniverseScanner,
    ScannerResult,
    equity_candidate_from_bars,
)


@dataclass(frozen=True)
class UniverseSnapshot:
    effective_at: datetime
    symbols: frozenset[str]
    scanner_version: str = ""
    config_digest: str = ""
    candidate_count: int = 0
    score_breakdown: dict[str, dict[str, float | str]] = field(default_factory=dict)
    eliminated: dict[str, str] = field(default_factory=dict)
    sector_metadata_coverage: float = 1.0
    # The STREAMED observation set at the snapshot (live parity): the
    # completeness gate requires these symbols to have a closed bar at the
    # decision timestamp, exactly like the live runtime requires its stream.
    # Empty means the legacy snapshot carried no observation set and the
    # gate falls back to the full pool.
    observation_set: frozenset[str] = frozenset()


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
        if any(
            right.effective_at < left.effective_at
            for left, right in zip(self.snapshots, self.snapshots[1:])
        ):
            object.__setattr__(
                self, "snapshots", tuple(sorted(self.snapshots, key=lambda item: item.effective_at))
            )

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
        coverage = (
            sum(snapshot.sector_metadata_coverage for snapshot in self.snapshots)
            / len(self.snapshots)
            if self.snapshots
            else 1.0
        )
        return {
            "market": self.market.value,
            "methodology": self.methodology,
            "sector_metadata_coverage": round(coverage, 6),
            "snapshots": [
                {
                    "effective_at": snapshot.effective_at.isoformat(),
                    "symbols": sorted(snapshot.symbols),
                    "observation_set": sorted(snapshot.observation_set),
                    "scanner_version": snapshot.scanner_version,
                    "config_digest": snapshot.config_digest,
                    "candidate_count": snapshot.candidate_count,
                    "score_breakdown": snapshot.score_breakdown,
                    "eliminated": snapshot.eliminated,
                    "sector_metadata_coverage": snapshot.sector_metadata_coverage,
                }
                for snapshot in self.snapshots
            ],
        }


def build_crypto_point_in_time_universe(
    bars: list[Bar],
    *,
    maximum_symbols: int,
    lookback_days: int = 30,
    minimum_history_ratio: float = 0.80,
) -> PointInTimeUniverse:
    """Compatibility wrapper using the production crypto scanner policy."""
    scanner = CryptoScannerConfig(
        lookback_days=lookback_days,
        minimum_data_completeness=minimum_history_ratio,
        minimum_median_dollar_volume=1.0,
        minimum_atr_pct=0.001,
    )
    config = CryptoUniverseConfig(
        enabled=True,
        dynamic_selection_enabled=True,
        maximum_stream_symbols=maximum_symbols,
        scanner=scanner,
    )
    return build_trailing_point_in_time_universe(
        bars,
        market=Market.CRYPTO,
        maximum_symbols=maximum_symbols,
        rebalance_frequency="monthly",
        lookback_days=lookback_days,
        minimum_history_ratio=minimum_history_ratio,
        crypto_config=config,
    )


def build_trailing_point_in_time_universe(
    bars: list[Bar],
    *,
    market: Market,
    maximum_symbols: int,
    rebalance_frequency: str = "daily",
    lookback_days: int = 60,
    minimum_history_ratio: float = 0.80,
    minimum_price: float = 0.0,
    minimum_dollar_volume: float = 0.0,
    pinned_symbols: tuple[str, ...] = (),
    target_shortlist: int | None = None,
    equity_config: EquityUniverseConfig | None = None,
    crypto_config: CryptoUniverseConfig | None = None,
) -> PointInTimeUniverse:
    """Run the production scanner policy on strictly trailing historical metrics.

    ``target_shortlist`` mirrors the live equity pipeline's Stage-A gate: the
    trailing median-dollar-volume leaders (up to the target) are the only
    symbols that reach the scanner, so a broad downloaded universe is
    evaluated exactly like the live market is.
    """
    if maximum_symbols <= 0:
        raise ValueError("maximum_symbols must be positive")
    if rebalance_frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("rebalance_frequency must be daily, weekly, or monthly")
    if not 0 < minimum_history_ratio <= 1:
        raise ValueError("minimum_history_ratio must be in (0, 1]")
    if target_shortlist is not None and target_shortlist < maximum_symbols:
        raise ValueError("target_shortlist must cover the active universe")
    selected_bars = sorted(
        (bar for bar in bars if bar.market == market),
        key=lambda item: (item.timestamp, item.symbol),
    )
    if not selected_bars:
        return PointInTimeUniverse(market, (), "trailing_scanner_v1")
    timeframes = {bar.timeframe for bar in selected_bars}
    if len(timeframes) != 1:
        raise ValueError("Point-in-time selection requires one timeframe")
    timeframe = next(iter(timeframes))
    duration = timeframe_duration(timeframe)
    expected = max(1, int(timedelta(days=lookback_days) / duration))
    timestamps = sorted({bar.timestamp for bar in selected_bars})
    snapshots: list[UniverseSnapshot] = []
    previous_period: tuple[int, ...] | None = None
    pinned = tuple(dict.fromkeys(canonical_symbol(symbol, market) for symbol in pinned_symbols))
    if len(pinned) > maximum_symbols:
        raise ValueError("Pinned symbols exceed point-in-time universe capacity")
    # Single forward pass with a rolling per-symbol window: every bar enters
    # the trailing window once and leaves it once, so a multi-year broad
    # universe stays O(bars) instead of O(timestamps * bars).
    window: dict[str, deque[Bar]] = {}
    add_index = 0
    total = len(selected_bars)
    for timestamp in timestamps:
        period = _period_key(timestamp, rebalance_frequency)
        cutoff = timestamp - timedelta(days=lookback_days)
        while add_index < total and selected_bars[add_index].timestamp < timestamp:
            bar = selected_bars[add_index]
            add_index += 1
            if bar.timestamp < cutoff:
                continue
            window.setdefault(canonical_symbol(bar.symbol, market), deque()).append(bar)
        if period == previous_period:
            continue
        previous_period = period
        for symbol, series in window.items():
            while series and series[0].timestamp < cutoff:
                series.popleft()
        window = {symbol: series for symbol, series in window.items() if series}
        by_symbol: dict[str, list[Bar]] = {
            symbol: list(series) for symbol, series in window.items()
        }
        # Each per-symbol series is built from ``selected_bars`` (sorted by
        # (timestamp, symbol)) and only ever shrinks from the front, so it is
        # timestamp-ascending: the latest bar of the latest series is the
        # window's data timestamp.
        data_timestamp = max(
            (series[-1].timestamp for series in by_symbol.values()),
            default=timestamp - duration,
        )
        result: ScannerResult | CryptoScannerResult
        if market == Market.EQUITY:
            configured = equity_config or EquityUniverseConfig(
                maximum_stream_symbols=maximum_symbols,
                reference_symbols=(),
                scanner=EquityScannerConfig(
                    minimum_price=max(minimum_price, 0.01),
                    minimum_median_dollar_volume=max(minimum_dollar_volume, 1.0),
                    minimum_data_completeness=minimum_history_ratio,
                ),
            )
            if target_shortlist is not None:
                # Stage-A parity with the live pipeline: rank the trailing
                # window by median dollar volume and admit only the top target
                # before the production scanner runs. The sort key is a total
                # order (median-dollars, symbol), so the result is independent
                # of the input order and one sort suffices.
                shortlisted = sorted(
                    by_symbol.items(),
                    key=lambda item: (-_median_dollar_volume(item[1]), item[0]),
                )[:target_shortlist]
            else:
                shortlisted = sorted(by_symbol.items())
            equity_candidates = [
                equity_candidate_from_bars(
                    symbol,
                    history,
                    expected_bar_count=min(20, expected),
                )
                for symbol, history in shortlisted
            ]
            result = EquityUniverseScanner(configured).scan(
                equity_candidates,
                data_timestamp=data_timestamp,
                selection_timestamp=timestamp,
                pinned_symbols=pinned,
            )
        else:
            configured_crypto = crypto_config or CryptoUniverseConfig(
                enabled=True,
                dynamic_selection_enabled=True,
                maximum_stream_symbols=maximum_symbols,
                pinned_symbols=pinned,
                trade_symbols=pinned,
                scanner=replace(
                    CryptoScannerConfig(),
                    lookback_days=lookback_days,
                    minimum_data_completeness=minimum_history_ratio,
                    minimum_median_dollar_volume=max(minimum_dollar_volume, 1.0),
                ),
            )
            btc_history = by_symbol.get("BTC/USD", [])
            crypto_candidates = [
                crypto_candidate_from_bars(
                    symbol,
                    history,
                    expected_bar_count=expected,
                    tradable=True,
                    status="active",
                    btc_bars=btc_history,
                    spread_bps=None,
                )
                for symbol, history in sorted(by_symbol.items())
            ]
            result = CryptoUniverseScanner(configured_crypto).scan(
                crypto_candidates,
                data_timestamp=data_timestamp,
                selection_timestamp=timestamp,
            )
        if not result.selected_symbols and not result.candidate_symbols:
            continue
        # The scanner reserves references/inverse ETFs unconditionally (matching
        # the live pipeline), but a reserved symbol without bars in this
        # window cannot be evaluated or traded: drop it so the decision gate
        # never blocks on data the run does not have.
        selected = tuple(symbol for symbol in result.selected_symbols if symbol in by_symbol)
        if not selected and not result.candidate_symbols:
            continue
        raw_observation = getattr(result, "observation_set", ())
        observation = frozenset(symbol for symbol in raw_observation if symbol in by_symbol)
        snapshots.append(
            UniverseSnapshot(
                timestamp,
                frozenset(selected),
                result.scanner_version,
                result.config_digest,
                len(result.candidate_symbols),
                result.score_breakdown,
                result.eliminated,
                getattr(result, "sector_metadata_coverage", 1.0),
                observation,
            )
        )
    return PointInTimeUniverse(
        market,
        tuple(snapshots),
        (
            f"{rebalance_frequency} production scanner policy on trailing {lookback_days}d "
            "metrics; inputs strictly precede snapshot; "
            + (
                f"Stage-A shortlist capped at {target_shortlist} symbols by trailing "
                "median dollar volume; "
                if target_shortlist is not None
                else ""
            )
            + "historical spread and broad sector/tradability metadata unavailable "
            "and explicitly degraded"
        ),
    )


def _median_dollar_volume(bars: list[Bar]) -> float:
    # ``bars`` is the trailing-window series, already timestamp-ascending
    # (see the builder's forward-pass invariant), so the last 20 are the
    # trailing 20 without a redundant sort.
    values = [float(bar.close) * float(bar.volume) for bar in bars[-20:]]
    return statistics.median(values) if values else 0.0


def _period_key(timestamp: datetime, frequency: str) -> tuple[int, ...]:
    if frequency == "daily":
        return timestamp.year, timestamp.month, timestamp.day
    if frequency == "weekly":
        year, week, _weekday = timestamp.isocalendar()
        return year, week
    return timestamp.year, timestamp.month


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
            "Universe snapshot hash mismatch: persisted "
            f"{expected_hash[:12]}... vs recomputed {recomputed[:12]}..."
        )


__all__ = [
    "PointInTimeUniverse",
    "UniverseSnapshot",
    "build_crypto_point_in_time_universe",
    "build_trailing_point_in_time_universe",
    "hash_point_in_time_universe",
    "verify_persisted_universe",
]
