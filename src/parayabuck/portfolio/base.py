from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.models import TargetPortfolio

if TYPE_CHECKING:
    pass


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
    # Optional derived numerical view (immutable) for computational hot paths.
    # ``Bar`` remains canonical; this is a validated cache built once per
    # prepared dataset. Strategies may use it if present, otherwise fall back
    # to ``history``. PAPER/LIVE contexts without prepared data will have
    # ``None`` and use the canonical path.
    numeric_history: Any | None = None

    def closes(self, symbol: str) -> list[float]:
        bars = self.history.get(canonical_symbol(symbol, self.market), ())
        return [bar.close for bar in bars]

    def in_universe(self, symbol: str) -> bool:
        """Permissive membership probe for static contexts.

        Returns ``True`` when ``universe`` is ``None`` or lacks a callable
        ``contains`` — this keeps static/non-product test contexts
        usable without a PIT universe. **Dynamic product execution (PAPER
        backtest/runtime) MUST NOT rely on this fallback**: callers on the
        product path must invoke :func:`require_product_universe` before
        ``strategy.decide()`` / ``rank_candidates()`` so a missing universe
        fails closed instead of treating every symbol as in-universe.
        """
        if self.universe is None:
            return True
        contains = getattr(self.universe, "contains", None)
        if contains is None:
            return True
        return bool(contains(symbol, self.market, self.decision_timestamp))


def require_product_universe(
    context: PortfolioContext,
    *,
    allow_static_context: bool = False,
) -> None:
    """Fail-closed invariant for **dynamic product** execution contexts.

    Validates that ``context.universe`` is not ``None``, exposes a callable
    ``contains(symbol, market, timestamp)``, and that
    ``context.universe_version`` is a non-empty string. Raises
    ``RuntimeError`` on violation so the decision path halts instead of
    silently treating every symbol as tradable.

    Call this BEFORE ``strategy.decide()`` or ``strategy.rank_candidates()``
    on the PAPER / backtest product path. Static or non-product test
    contexts that intentionally operate without a PIT universe may pass
    ``allow_static_context=True`` to retain the permissive
    :meth:`PortfolioContext.in_universe` behaviour. That opt-in must be
    explicit at the call site — the default is fail-closed.
    """
    if allow_static_context:
        return
    require_product_universe_dependency(context.universe, context.universe_version)


def require_product_universe_dependency(universe: object | None, universe_version: str) -> None:
    """Validate a product universe before any persistent runtime side effect.

    ``PortfolioRuntime`` calls this during construction, before opening its
    session row. Decision paths call :func:`require_product_universe`, which
    delegates here so the same fail-closed contract and error vocabulary are
    enforced at both composition and execution boundaries.
    """

    if universe is None:
        raise RuntimeError(
            "Product universe guard: context.universe is None — "
            "dynamic product strategies require a PIT universe (fail-closed)"
        )
    contains = getattr(universe, "contains", None)
    if not callable(contains):
        raise RuntimeError(
            "Product universe guard: context.universe must expose a callable "
            "contains(symbol, market, timestamp) (fail-closed)"
        )
    if not isinstance(universe_version, str) or not universe_version.strip():
        raise RuntimeError("Product universe guard: context.universe_version must be a non-empty string (fail-closed)")


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

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        """Optional alpha ranking of the candidate pool for observation-set
        selection (stream limit != opportunity universe).

        Returns candidate symbols sorted by the strategy's own alpha, best
        first. The base implementation returns () — meaning the strategy
        provides no pool ranking and the scanner's eligibility-quality
        stream set is used as the observation set. Strategies that implement
        this MUST share the exact scoring code with ``decide`` so BACKTEST,
        PAPER and LIVE rank identically.
        """
        return ()

    # -- typed parameter access (values are validated in _validate_parameters)

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


__all__ = ["PortfolioContext", "PortfolioStrategy", "StrategySpec", "require_product_universe"]
