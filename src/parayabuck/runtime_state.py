from __future__ import annotations

from dataclasses import dataclass

from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar
from parayabuck.data.buffers import BarMutation, BarRevision, BarStore
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class IngestionResult:
    bars: tuple[Bar, ...]
    mutations: tuple[BarMutation, ...]


class RuntimeMarketState:
    """Operational bars and lifecycle state without decisions."""

    def __init__(
        self,
        config: EngineConfig,
        *,
        allowed_trade_symbols: set[str] | None = None,
    ) -> None:
        self.config = config
        self.allowed_trade_symbols = (
            {canonical_symbol(symbol) for symbol in allowed_trade_symbols}
            if allowed_trade_symbols is not None
            else None
        )
        # Strategy warmup requirements are owned by PortfolioRuntime. This
        # operational store keeps a generous rolling window of execution bars.
        self.bars = BarStore(5000)

    def seed(self, bars: list[Bar]) -> None:
        self.bars.seed(bars)

    def ingest_closed_bars(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations = tuple(self.bars.upsert(bar) for bar in ordered)
        return IngestionResult(ordered, mutations)

    def ingest_corrections(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations = tuple(self.bars.upsert(bar, BarRevision.CORRECTION) for bar in ordered)
        return IngestionResult(ordered, mutations)


__all__ = ["IngestionResult", "RuntimeMarketState"]
