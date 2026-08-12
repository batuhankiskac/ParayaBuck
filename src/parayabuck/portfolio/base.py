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


@dataclass(frozen=True)
class StrategySpec:
    """Immutable description of a strategy's declared trading-layer contract.

    The strategy owns its signal timeframe: there is no global strategy
    timeframe, and ``supported_timeframes`` is the ONLY set of timeframes a
    strategy may ever be run on (the UI restricts choice to it). Scanner,
    signal, rebalance, execution and reconciliation cadences are independent
    responsibilities and are NOT collapsed here.
    """

    name: str
    version: str
    market: Market
    display_name: str
    primary_timeframe: str
    supported_timeframes: tuple[str, ...]
    required_timeframes: tuple[str, ...]
    rebalance_policy: str
    minimum_history_bars: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "market": self.market.value,
            "display_name": self.display_name,
            "primary_timeframe": self.primary_timeframe,
            "supported_timeframes": list(self.supported_timeframes),
            "required_timeframes": list(self.required_timeframes),
            "rebalance_policy": self.rebalance_policy,
            "minimum_history_bars": self.minimum_history_bars,
        }


class PortfolioStrategy(ABC):
    """Produces target portfolios from point-in-time market state.

    ``timeframe`` is the strategy's PRIMARY signal timeframe and is owned by
    the strategy, never by a global runtime selector. ``supported_timeframes``
    may list additional VALIDATED variants; anything else is rejected.
    """

    name: str
    version: str = "1.0.0"
    market: Market
    timeframe: str
    display_name: str = ""
    # Validated alternative signal timeframes (in addition to ``timeframe``).
    # Empty means the strategy is single-timeframe and MUST NOT be run on any
    # other resolution.
    supported_timeframes: tuple[str, ...] = ()
    # Additional timeframes whose bars must be seeded/aggregated before the
    # strategy can decide (e.g. a lower-resolution reference series).
    required_timeframes: tuple[str, ...] = ()
    # Declared decision cadence ("monthly", "weekly", "hours:N"). Informational
    # metadata; the executable policy mapping lives in
    # ``parayabuck.portfolio.rebalance_policy_for``.
    rebalance_policy_declared: str = "monthly"

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

    def supports_timeframe(self, timeframe: str) -> bool:
        """Whether ``timeframe`` is a validated signal resolution for this strategy."""
        return timeframe == self.timeframe or timeframe in self.supported_timeframes

    def spec(self) -> StrategySpec:
        """The strategy's declared trading-layer contract (name, timeframes, cadence)."""
        return StrategySpec(
            name=self.name,
            version=self.version,
            market=self.market,
            display_name=self.display_name or self.name,
            primary_timeframe=self.timeframe,
            supported_timeframes=self.supported_timeframes,
            required_timeframes=self.required_timeframes,
            rebalance_policy=self.rebalance_policy_declared,
            minimum_history_bars=self.minimum_history_bars,
        )

    def supports(self, context: PortfolioContext) -> bool:
        if context.market != self.market or not self.supports_timeframe(context.timeframe):
            return False
        longest = max((len(bars) for bars in context.history.values()), default=0)
        return longest >= self.minimum_history_bars

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        """Whether a non-scheduled decision may only reduce portfolio risk."""
        return False

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


__all__ = ["PortfolioContext", "PortfolioStrategy", "StrategySpec"]
