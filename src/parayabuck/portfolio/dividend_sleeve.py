"""V3 residual momentum plus a monthly dividend-growth ETF sleeve.

The runtime and backtest run exactly one strategy per market, so the V3 core
and the static dividend-growth sleeve share one equity strategy identity.
``decide`` returns V3's own target (computed on a context that hides the
sleeve symbols, so V3's scoring pool is unchanged) and carries the sleeve
intent in ``metadata["sleeve"]``. ``PortfolioRiskOverlay`` sends the core
through the canonical overlay with ``core_allocation`` and then appends the
sleeve (see ``execution.portfolio``).

Validated by ``docs/research/sleeve_candidates_v2_v3_closure.md``
(``dividend_growth_v1``): SCHD/VIG/DGRO at 1/3 each of a 0.15 sleeve, decided on
the first weekly decision of each calendar month and held otherwise; V3 core
at 0.40. The shipped configuration adds gold (GLD/OUNZ) as an equal-weight
five-ETF 0.25 sleeve with a 0.30 core
(``docs/research/gold_sleeve_production_v1.md``); the class defaults stay the
original dividend-only parameters.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import TargetPortfolio

SLEEVE_METADATA_KEY = "sleeve"
SLEEVE_MODE_DECIDE = "decide"
SLEEVE_MODE_HOLD = "hold"
_SLEEVE_PARAMETERS = ("core_allocation", "sleeve_allocation", "sleeve_symbols")
DEFAULT_SLEEVE_SYMBOLS: tuple[str, ...] = ("SCHD", "VIG", "DGRO")


class EquityResidualMomentumV3DividendSleeveV1(PortfolioStrategy):
    """V3 core (weekly) plus an equal-weight dividend-growth sleeve (monthly)."""

    name = "equity_residual_momentum_v3_dividend_sleeve_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Residual Momentum v3 + dividend-growth sleeve"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "weekly"
    cadence_label = "weekly"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        merged = dict(parameters or {})
        merged.setdefault("rebalance_frequency", "weekly")
        merged.setdefault("core_allocation", 0.40)
        merged.setdefault("sleeve_allocation", 0.15)
        merged.setdefault("sleeve_symbols", list(DEFAULT_SLEEVE_SYMBOLS))
        self.core = EquityResidualMomentum1dV3(
            {key: value for key, value in merged.items() if key not in _SLEEVE_PARAMETERS}
        )
        super().__init__(merged)

    def _validate_parameters(self) -> None:
        if self.parameters.get("rebalance_frequency") != "weekly":
            raise ValueError("The dividend-sleeve strategy requires rebalance_frequency weekly")
        core = self.param_float("core_allocation", 0.40)
        sleeve = self.param_float("sleeve_allocation", 0.15)
        if not 0 < core <= 1 or not 0 < sleeve <= 1 or core + sleeve > 1:
            raise ValueError("core_allocation and sleeve_allocation must be positive and sum to at most 1")
        raw = self.parameters.get("sleeve_symbols")
        if not isinstance(raw, (list, tuple)) or not raw or not all(isinstance(item, str) for item in raw):
            raise ValueError("sleeve_symbols must be a non-empty list of symbols")
        symbols = tuple(canonical_symbol(item, Market.EQUITY) for item in raw)
        if len(set(symbols)) != len(symbols):
            raise ValueError("sleeve_symbols must be unique")
        excluded = {str(item).strip().upper() for item in self.core.parameters.get("exclude_symbols", [])}
        if excluded & set(symbols) or "SPY" in symbols:
            raise ValueError("sleeve_symbols must not overlap the V3 reference/excluded symbols")
        self.parameters["sleeve_symbols"] = list(symbols)

    @property
    def sleeve_symbols(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.parameters["sleeve_symbols"])  # type: ignore[union-attr]

    @property
    def core_allocation(self) -> float:
        return self.param_float("core_allocation", 0.40)

    @property
    def sleeve_allocation(self) -> float:
        return self.param_float("sleeve_allocation", 0.15)

    @property
    def minimum_history_bars(self) -> int:
        return self.core.minimum_history_bars

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return self.core.requires_intermediate_risk_rebalance(self._core_context(context))

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        return self.core.rank_candidates(self._core_context(context))

    def _core_context(self, context: PortfolioContext) -> PortfolioContext:
        """V3 sees the context without the sleeve symbols.

        Pinned sleeve symbols would otherwise enter V3's scoring pool and move
        its ivol quantile and cross-sectional ranks even when V3 never targets
        them (the research invariance lesson).
        """
        hidden = set(self.sleeve_symbols)
        if not hidden & set(context.history):
            return context
        return replace(
            context,
            history={symbol: bars for symbol, bars in context.history.items() if symbol not in hidden},
            holdings={symbol: qty for symbol, qty in context.holdings.items() if symbol not in hidden},
            prices={symbol: price for symbol, price in context.prices.items() if symbol not in hidden},
        )

    def sleeve_mode(self, context: PortfolioContext) -> str:
        """``decide`` on the first weekly decision of each calendar month.

        Stateless so restarts cannot skip or repeat a month: the decision is
        the month's first when the first session of the previous ISO week
        (from the benchmark series) fell in an earlier month. An empty sleeve
        is also decided, so a fresh account does not wait for the next month.
        """
        if not any(context.holdings.get(symbol, 0.0) > 0 for symbol in self.sleeve_symbols):
            return SLEEVE_MODE_DECIDE
        session = context.decision_timestamp.astimezone(NEW_YORK).date()
        week_start = session - timedelta(days=session.weekday())
        benchmark = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        series = context.history.get(benchmark) or max(context.history.values(), key=len, default=())
        prior: list[date] = [
            day for day in (bar.timestamp.astimezone(NEW_YORK).date() for bar in series) if day < week_start
        ]
        if not prior:
            return SLEEVE_MODE_DECIDE
        previous_week_start = prior[-1] - timedelta(days=prior[-1].weekday())
        previous_decision = min(day for day in prior if day >= previous_week_start)
        if (previous_decision.year, previous_decision.month) != (session.year, session.month):
            return SLEEVE_MODE_DECIDE
        return SLEEVE_MODE_HOLD

    def sleeve_weights(self, context: PortfolioContext) -> dict[str, float]:
        """Fixed equal share of the sleeve for every symbol with history."""
        share = self.sleeve_allocation / len(self.sleeve_symbols)
        return {symbol: share for symbol in self.sleeve_symbols if context.history.get(symbol)}

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        core_target = self.core.decide(self._core_context(context))
        conflicts = sorted(weight.symbol for weight in core_target.weights if weight.symbol in self.sleeve_symbols)
        if conflicts:
            raise RuntimeError(f"V3 core targeted sleeve symbols {conflicts}; ownership would be ambiguous")
        mode = self.sleeve_mode(context)
        sleeve: dict[str, Any] = {
            "mode": mode,
            "symbols": list(self.sleeve_symbols),
            "weights": self.sleeve_weights(context) if mode == SLEEVE_MODE_DECIDE else {},
            "core_allocation": self.core_allocation,
        }
        return replace(
            core_target,
            strategy_name=self.name,
            strategy_version=self.version,
            parameters=self.parameter_tuple(),
            metadata={**core_target.metadata, SLEEVE_METADATA_KEY: sleeve},
        )


__all__ = [
    "DEFAULT_SLEEVE_SYMBOLS",
    "SLEEVE_METADATA_KEY",
    "SLEEVE_MODE_DECIDE",
    "SLEEVE_MODE_HOLD",
    "EquityResidualMomentumV3DividendSleeveV1",
]
