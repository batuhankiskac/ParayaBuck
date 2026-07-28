from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from parayabuck.core.models import Bar, Market, PositionSnapshot, Regime, Signal
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class StrategyContext:
    regime: RegimeAssessment
    position: PositionSnapshot | None = None
    higher_timeframe_regime: Regime = Regime.UNKNOWN
    benchmark_regime: Regime = Regime.UNKNOWN


@dataclass(frozen=True)
class ConditionEvaluation:
    """One explainable strategy gate with machine-readable values."""

    code: str
    passed: bool
    actual: Any
    expected: str
    explanation: str


@dataclass(frozen=True)
class StrategyEvaluation:
    """A strategy result together with the evidence used to reach it."""

    strategy: str
    fired: bool
    summary: str
    conditions: tuple[ConditionEvaluation, ...] = ()
    metrics: dict[str, Any] | None = None
    signal: Signal | None = None


class Strategy(ABC):
    name: str
    market: Market
    minimum_bars: int = 50

    @abstractmethod
    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None: ...

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        """Compatibility adapter for third-party strategies without native tracing."""
        supported = self.supports(bars)
        signal = self.evaluate(bars, context) if supported else None
        condition = ConditionEvaluation(
            "strategy_support",
            supported,
            {"bars": len(bars), "market": bars[-1].market.value if bars else None},
            f"at least {self.minimum_bars} bars and market={self.market.value}",
            "Strategy has enough compatible history."
            if supported
            else "Unsupported market or history.",
        )
        return StrategyEvaluation(
            self.name,
            signal is not None,
            "Strategy fired." if signal is not None else "Strategy returned no setup.",
            (condition,),
            signal=signal,
        )

    def supports(self, bars: list[Bar]) -> bool:
        if len(bars) < self.minimum_bars or bars[-1].market != self.market:
            return False
        symbol = canonical_symbol(bars[-1].symbol, self.market)
        return all(
            bar.market == self.market and canonical_symbol(bar.symbol, bar.market) == symbol
            for bar in bars
        )
