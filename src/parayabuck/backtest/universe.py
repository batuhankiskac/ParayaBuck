from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.features.indicators import atr
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import timeframe_duration


@dataclass(frozen=True)
class UniverseSnapshot:
    effective_at: datetime
    symbols: frozenset[str]


@dataclass(frozen=True)
class PointInTimeUniverse:
    market: Market
    snapshots: tuple[UniverseSnapshot, ...]
    methodology: str

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        if market != self.market:
            return False
        eligible = [snapshot for snapshot in self.snapshots if snapshot.effective_at <= timestamp]
        if not eligible:
            return False
        return canonical_symbol(symbol, market) in eligible[-1].symbols

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market.value,
            "methodology": self.methodology,
            "snapshots": [
                {
                    "effective_at": snapshot.effective_at.isoformat(),
                    "symbols": sorted(snapshot.symbols),
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
    """Re-select liquid spot pairs monthly using strictly trailing information."""
    crypto = [bar for bar in bars if bar.market == Market.CRYPTO]
    if not crypto or maximum_symbols <= 0:
        return PointInTimeUniverse(Market.CRYPTO, (), "trailing_liquidity_atr_integrity_v1")
    if not 0 < minimum_history_ratio <= 1:
        raise ValueError("Minimum history ratio must be in (0, 1]")
    timeframe = crypto[0].timeframe
    if any(bar.timeframe != timeframe for bar in crypto):
        raise ValueError("Point-in-time selection requires one timeframe")
    first = min(bar.timestamp for bar in crypto)
    last = max(bar.timestamp for bar in crypto)
    rebalance = _next_month(first)
    duration = timeframe_duration(timeframe)
    expected = max(1, int(timedelta(days=lookback_days) / duration))
    snapshots: list[UniverseSnapshot] = []
    while rebalance <= last:
        start = rebalance - timedelta(days=lookback_days)
        by_symbol: dict[str, list[Bar]] = {}
        for bar in crypto:
            if start <= bar.timestamp < rebalance:
                by_symbol.setdefault(canonical_symbol(bar.symbol, bar.market), []).append(bar)
        ranked: list[tuple[float, str]] = []
        for symbol, history in by_symbol.items():
            ordered = sorted(history, key=lambda bar: bar.timestamp)
            unique = {bar.timestamp for bar in ordered}
            if len(unique) < expected * minimum_history_ratio:
                continue
            if any(
                current.timestamp - previous.timestamp > duration * 3
                for previous, current in zip(ordered, ordered[1:])
            ):
                continue
            atr_value = atr(ordered, 14)
            if atr_value is None or atr_value <= 0 or ordered[-1].close <= 0:
                continue
            dollar_volume = sum(bar.close * bar.volume for bar in ordered) / len(ordered)
            if dollar_volume <= 0:
                continue
            volatility_penalty = max(1.0, atr_value / ordered[-1].close * 100)
            ranked.append((dollar_volume / volatility_penalty, symbol))
        ranked.sort(reverse=True)
        snapshots.append(
            UniverseSnapshot(
                rebalance,
                frozenset(symbol for _, symbol in ranked[:maximum_symbols]),
            )
        )
        rebalance = _next_month(rebalance)
    return PointInTimeUniverse(
        Market.CRYPTO,
        tuple(snapshots),
        "monthly trailing 30d dollar-volume, ATR and continuity; no future bars",
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
) -> PointInTimeUniverse:
    """Build scanner-like snapshots using only information before each decision.

    This is a practical point-in-time reconstruction from the supplied bars. Asset
    metadata still comes from the candidate data source, so callers must separately
    disclose any survivorship limitation in that source.
    """
    if maximum_symbols <= 0:
        raise ValueError("maximum_symbols must be positive")
    if rebalance_frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("rebalance_frequency must be daily, weekly, or monthly")
    if not 0 < minimum_history_ratio <= 1:
        raise ValueError("minimum_history_ratio must be in (0, 1]")
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
    for timestamp in timestamps:
        period = _period_key(timestamp, rebalance_frequency)
        if period == previous_period:
            continue
        previous_period = period
        start = timestamp - timedelta(days=lookback_days)
        by_symbol: dict[str, list[Bar]] = {}
        for bar in selected_bars:
            if bar.timestamp >= timestamp:
                break
            if bar.timestamp >= start:
                by_symbol.setdefault(canonical_symbol(bar.symbol, market), []).append(bar)
        ranked: list[tuple[float, str]] = []
        for symbol, history in by_symbol.items():
            ordered = sorted(history, key=lambda item: item.timestamp)
            if len({bar.timestamp for bar in ordered}) < expected * minimum_history_ratio:
                continue
            close = ordered[-1].close
            if close < minimum_price or close <= 0:
                continue
            dollar_volumes = [bar.close * bar.volume for bar in ordered if bar.volume > 0]
            if not dollar_volumes:
                continue
            liquidity = statistics.median(dollar_volumes)
            if liquidity < minimum_dollar_volume:
                continue
            atr_value = atr(ordered, min(14, len(ordered)))
            if atr_value is None or atr_value <= 0:
                continue
            volatility = atr_value / close
            quality = liquidity / max(0.005, volatility)
            ranked.append((quality, symbol))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        chosen = list(pinned)
        chosen.extend(symbol for _score, symbol in ranked if symbol not in chosen)
        snapshots.append(UniverseSnapshot(timestamp, frozenset(chosen[:maximum_symbols])))
    return PointInTimeUniverse(
        market,
        tuple(snapshots),
        (
            f"{rebalance_frequency} trailing {lookback_days}d median dollar-volume, "
            "ATR and completeness; inputs strictly precede snapshot"
        ),
    )


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


def _next_month(value: datetime) -> datetime:
    current = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    absolute = current.year * 12 + current.month
    year, month = divmod(absolute, 12)
    return current.replace(year=year, month=month + 1)


__all__ = [
    "PointInTimeUniverse",
    "UniverseSnapshot",
    "build_crypto_point_in_time_universe",
    "build_trailing_point_in_time_universe",
    "hash_point_in_time_universe",
    "verify_persisted_universe",
]
