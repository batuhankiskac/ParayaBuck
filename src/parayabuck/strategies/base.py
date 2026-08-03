from __future__ import annotations

import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from parayabuck.core.models import (
    Bar,
    Direction,
    ExitPolicy,
    Market,
    PositionSnapshot,
    Regime,
    Signal,
)
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class StrategyContext:
    regime: RegimeAssessment
    position: PositionSnapshot | None = None
    higher_timeframe_regime: Regime = Regime.UNKNOWN
    benchmark_regime: Regime = Regime.UNKNOWN
    relative_strength: float | None = None


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
    version: str = "1.0.0"
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

    @staticmethod
    def closes(bars: list[Bar]) -> list[float]:
        return [bar.close for bar in bars]

    @staticmethod
    def nonzero_volume_gate(bars: list[Bar], ratio: float) -> bool:
        if not bars:
            return False
        available = sum(1 for bar in bars if math.isfinite(bar.volume) and bar.volume > 0)
        return available >= max(1, int(ratio * len(bars)))

    @staticmethod
    def relative_volume(
        current: Bar,
        baseline: list[Bar],
        *,
        median: bool = False,
        positive_only: bool = False,
    ) -> float:
        volumes = [
            bar.volume
            for bar in baseline
            if math.isfinite(bar.volume) and (bar.volume > 0 or not positive_only)
        ]
        if not volumes:
            return 1.0
        typical = statistics.median(volumes) if median else statistics.fmean(volumes)
        return current.volume / typical if typical > 0 else 1.0

    def long_signal(
        self,
        current: Bar,
        score: float,
        stop: float,
        target: float,
        reasons: tuple[str, ...],
        regime: Regime,
        *,
        exit_policy: ExitPolicy | None = None,
        volatility_at_entry: float | None = None,
        minimum_risk_reward: float | None = None,
        reentry_cooldown_bars: int = 0,
    ) -> Signal:
        bounded_score = min(100.0, score)
        return Signal(
            symbol=current.symbol,
            market=self.market,
            direction=Direction.LONG,
            strategy=self.name,
            score=bounded_score,
            confidence=min(1.0, bounded_score / 100),
            entry_price=current.close,
            invalidation_price=stop,
            target_price=target,
            timestamp=current.timestamp,
            reasons=reasons,
            regime=regime,
            strategy_version=self.version,
            exit_policy=exit_policy or ExitPolicy(),
            volatility_at_entry=volatility_at_entry,
            minimum_risk_reward=minimum_risk_reward,
            reentry_cooldown_bars=reentry_cooldown_bars,
        )
