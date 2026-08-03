from __future__ import annotations

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


def _next_month(value: datetime) -> datetime:
    current = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    absolute = current.year * 12 + current.month
    year, month = divmod(absolute, 12)
    return current.replace(year=year, month=month + 1)


__all__ = [
    "PointInTimeUniverse",
    "UniverseSnapshot",
    "build_crypto_point_in_time_universe",
]
