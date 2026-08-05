from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.models import TargetPortfolio


@dataclass(frozen=True)
class PortfolioContext:
    """Everything a portfolio strategy may use at a decision point.

    ``history`` contains only completed bars whose timestamp is at or before
    ``decision_timestamp``; momentum lookbacks can never reach future bars.
    Universe membership is evaluated through ``universe`` snapshots that are
    effective at the decision timestamp.
    """

    decision_timestamp: datetime
    market: Market
    timeframe: str
    history: dict[str, tuple[Bar, ...]]
    universe: object
    universe_version: str
    equity: float
    holdings: dict[str, float]
    prices: dict[str, float]
    benchmark_symbol: str

    def closes(self, symbol: str) -> list[float]:
        bars = self.history.get(canonical_symbol(symbol, self.market), ())
        return [bar.close for bar in bars]

    def in_universe(self, symbol: str) -> bool:
        if self.universe is None:
            return True
        contains = getattr(self.universe, "contains", None)
        if contains is None:
            return True
        return bool(contains(symbol, self.market, self.decision_timestamp))

    def realized_volatility(self, symbol: str, window: int) -> float:
        import math
        import statistics

        closes = self.closes(symbol)
        if len(closes) < window + 1:
            return 0.0
        returns = [
            closes[index] / closes[index - 1] - 1
            for index in range(len(closes) - window, len(closes))
            if closes[index - 1] > 0
        ]
        if len(returns) < 2:
            return 0.0
        mean = statistics.fmean(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return math.sqrt(variance)

    def trailing_return(self, symbol: str, lookback_bars: int) -> float:
        """Return over the last ``lookback_bars`` completed bars, skipping the most
        recent bar so the strategy never reacts to the bar it just closed."""
        closes = self.closes(symbol)
        if len(closes) < lookback_bars + 2:
            return float("-inf")
        start = closes[-lookback_bars - 2]
        end = closes[-2]
        if start <= 0:
            return float("-inf")
        return end / start - 1


class PortfolioStrategy(ABC):
    """Produces target portfolios from point-in-time market state."""

    name: str
    version: str = "1.0.0"
    market: Market
    timeframe: str

    @property
    def minimum_history_bars(self) -> int:
        return 50

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self.parameters = dict(parameters or {})
        self._validate_parameters()

    @abstractmethod
    def _validate_parameters(self) -> None: ...

    @abstractmethod
    def decide(self, context: PortfolioContext) -> TargetPortfolio: ...

    def parameter_tuple(self) -> tuple[tuple[str, object], ...]:
        return tuple(sorted(self.parameters.items()))

    def supports(self, context: PortfolioContext) -> bool:
        if context.market != self.market or context.timeframe != self.timeframe:
            return False
        longest = max((len(bars) for bars in context.history.values()), default=0)
        return longest >= self.minimum_history_bars

    # -- typed parameter access (values are validated in _validate_parameters)

    def param_str(self, name: str, default: str) -> str:
        value = self.parameters.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    def param_int(self, name: str, default: int) -> int:
        value = self.parameters.get(name, default)
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        return int(value)

    def param_float(self, name: str, default: float) -> float:
        value = self.parameters.get(name, default)
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        return float(value)

    def param_optional_float(self, name: str) -> float | None:
        value = self.parameters.get(name)
        if value is None:
            return None
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric or null")
        return float(value)


__all__ = ["PortfolioContext", "PortfolioStrategy"]
