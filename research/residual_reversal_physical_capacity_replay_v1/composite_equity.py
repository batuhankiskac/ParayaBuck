"""Research-only, pre-outcome V3 plus frozen reversal source composition."""

from __future__ import annotations

from dataclasses import replace

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

from .candidate import EquityShortTermResidualReversalV1, scale_sleeve_weights


class CompositeEquity(PortfolioStrategy):
    """One equity engine, two separately attributed alpha sources."""

    name = "equity_v3_plus_residual_reversal_physical_capacity_v1"
    version = "1.0.0-research"
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "weekly"

    def __init__(self, v3: EquityResidualMomentum1dV3, reversal: EquityShortTermResidualReversalV1) -> None:
        self.v3 = v3
        self.reversal = reversal
        self.raw_decisions: list[dict[str, object]] = []
        super().__init__(
            {
                "v3_budget": 0.4125,
                "reversal_budget": 0.1375,
                "reference_allocation": 0.55,
                "rebalance_frequency": "weekly",
            }
        )

    @property
    def minimum_history_bars(self) -> int:
        return max(self.v3.minimum_history_bars, self.reversal.minimum_history_bars)

    def _validate_parameters(self) -> None:
        if self.v3.name != "equity_residual_momentum_v3":
            raise ValueError("V3 incumbent identity changed")
        if self.reversal.name != "equity_short_term_residual_reversal_v1":
            raise ValueError("Frozen reversal identity changed")
        if self.parameters != {
            "v3_budget": 0.4125,
            "reversal_budget": 0.1375,
            "reference_allocation": 0.55,
            "rebalance_frequency": "weekly",
        }:
            raise ValueError("Frozen source budgets changed")

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        """Keep the corrected campaign's balanced observation stream."""
        reversal_context = replace(context, ordinary_alpha_excluded_symbols=())
        lists = (tuple(self.v3.rank_candidates(context)), tuple(self.reversal.rank_candidates(reversal_context)))
        combined = []
        for index in range(max(map(len, lists), default=0)):
            for ranked in lists:
                if index < len(ranked):
                    combined.append(canonical_symbol(ranked[index], Market.EQUITY))
        return tuple(dict.fromkeys(combined))

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        v3_target = self.v3.decide(context)
        reversal_target = self.reversal.decide(replace(context, ordinary_alpha_excluded_symbols=()))
        source_targets = {"v3": v3_target, "reversal": reversal_target}
        weights = {
            "v3": scale_sleeve_weights(v3_target, sleeve_fraction=0.75),
            "reversal": scale_sleeve_weights(reversal_target, sleeve_fraction=0.25),
        }
        source_ranks = {
            name: [
                item.symbol
                for item in sorted(target.weights, key=lambda item: (item.rank or 10**9, item.symbol))
                if weights[name].get(item.symbol, 0.0) > 0
            ]
            for name, target in source_targets.items()
        }
        requested = {name: sum(parts.values()) for name, parts in weights.items()}
        admitted = {"v3": 0.0, "reversal": 0.0}
        indices = {"v3": 0, "reversal": 0}
        priority: list[str] = []
        while any(indices[name] < len(source_ranks[name]) for name in source_ranks):
            available = [name for name in ("v3", "reversal") if indices[name] < len(source_ranks[name])]
            name = min(
                available,
                key=lambda source: (
                    admitted[source] / requested[source] if requested[source] else 1.0,
                    0 if source == "v3" else 1,
                ),
            )
            symbol = source_ranks[name][indices[name]]
            indices[name] += 1
            admitted[name] += weights[name][symbol]
            if symbol not in priority:
                priority.append(symbol)
        contributions: dict[str, dict[str, float]] = {}
        for name in ("v3", "reversal"):
            for symbol, weight in weights[name].items():
                contributions.setdefault(symbol, {"v3": 0.0, "reversal": 0.0})[name] += weight
        for symbol, parts in contributions.items():
            combined = sum(parts.values())
            if combined > 0.10:
                scale = 0.10 / combined
                contributions[symbol] = {name: value * scale for name, value in parts.items()}
        self.raw_decisions.append(
            {
                "label_time": context.decision_timestamp.isoformat(),
                "v3_symbols": source_ranks["v3"],
                "reversal_symbols": source_ranks["reversal"],
                "v3_weights": weights["v3"],
                "reversal_weights": weights["reversal"],
                "source_contributions": contributions,
                "source_priority": priority,
                "overlap_symbols": sorted(set(weights["v3"]) & set(weights["reversal"])),
            }
        )
        merged = tuple(
            TargetWeight(symbol, sum(contributions[symbol].values()), "v3_plus_reversal", rank)
            for rank, symbol in enumerate(priority, start=1)
        )
        invested = sum(item.weight for item in merged)
        return replace(
            v3_target,
            strategy_name=self.name,
            strategy_version=self.version,
            weights=merged,
            cash_weight=max(0.0, 1.0 - invested),
            rebalance_reason=RebalanceReason.SCHEDULED if merged else RebalanceReason.RISK_OFF,
            parameters=self.parameter_tuple(),
            metadata={
                "source_contributions": contributions,
                "source_ranks": source_ranks,
                "overlap_symbols": sorted(set(weights["v3"]) & set(weights["reversal"])),
                "source_requested_exposure": requested,
                "source_priority": priority,
                "no_cross_sleeve_reallocation": True,
            },
        )
