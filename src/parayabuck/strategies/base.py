from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from parayabuck.core.models import Bar, Market, PositionSnapshot, Regime, Signal
from parayabuck.markets.regime import RegimeAssessment


@dataclass(frozen=True)
class StrategyContext:
    regime: RegimeAssessment
    position: PositionSnapshot | None = None
    higher_timeframe_regime: Regime = Regime.UNKNOWN
    benchmark_regime: Regime = Regime.UNKNOWN


class Strategy(ABC):
    name: str
    market: Market
    minimum_bars: int = 50

    @abstractmethod
    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None: ...

    def supports(self, bars: list[Bar]) -> bool:
        return len(bars) >= self.minimum_bars and bars[-1].market == self.market
