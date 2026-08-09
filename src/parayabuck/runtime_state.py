from __future__ import annotations

from dataclasses import dataclass

from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.buffers import BarMutation, BarRevision, BarStore
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class IngestionResult:
    bars: tuple[Bar, ...]
    mutations: tuple[BarMutation, ...]


class RuntimeMarketState:
    """Operational bars, gaps, lifecycle, and risk state without decisions."""

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
        # operational store keeps a generous rolling window for gap repair.
        self.bars = BarStore(5000)
        self._not_ready: set[tuple[str, str]] = set()

    def seed(self, bars: list[Bar]) -> None:
        self.bars.seed(bars)

    def repair_gap(self, bars: list[Bar]) -> bool:
        self.bars.seed(bars)
        repaired = True
        for bar in bars:
            key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
            if self.bars.is_contiguous(*key):
                self._not_ready.discard(key)
            else:
                repaired = False
        return repaired

    @property
    def has_data_gaps(self) -> bool:
        return bool(self._not_ready)

    def has_data_gaps_for_market(self, market: Market) -> bool:
        return any(
            ("/" in symbol) == (market == Market.CRYPTO) for symbol, _timeframe in self._not_ready
        )

    def has_data_gaps_for_symbol(self, symbol: str) -> bool:
        wanted = canonical_symbol(symbol)
        return any(candidate == wanted for candidate, _timeframe in self._not_ready)

    def ingest_closed_bars(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations: list[BarMutation] = []
        for bar in ordered:
            mutation = self.bars.upsert(bar)
            mutations.append(mutation)
            if mutation == BarMutation.GAP_DETECTED:
                self._not_ready.add((canonical_symbol(bar.symbol, bar.market), bar.timeframe))
        return IngestionResult(ordered, tuple(mutations))

    def ingest_corrections(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations = tuple(self.bars.upsert(bar, BarRevision.CORRECTION) for bar in ordered)
        return IngestionResult(ordered, mutations)


__all__ = ["IngestionResult", "RuntimeMarketState"]
