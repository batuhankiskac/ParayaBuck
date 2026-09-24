"""Small synthetic source attribution and arbitration invariants."""

from datetime import UTC, datetime

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from research.residual_reversal_physical_capacity_replay_v1.composite_equity import CompositeEquity


class _Source:
    minimum_history_bars = 1

    def __init__(self, name: str, weights: tuple[TargetWeight, ...]) -> None:
        self.name = name
        self.weights = weights

    def rank_candidates(self, _context: PortfolioContext) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.weights)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            self.name, "1", context.decision_timestamp, self.weights, 1 - sum(item.weight for item in self.weights)
        )


def test_overlap_preserves_two_sources_and_one_account_target() -> None:
    v3 = _Source("equity_residual_momentum_v3", (TargetWeight("A", 0.25, rank=1), TargetWeight("B", 0.25, rank=2)))
    reversal = _Source(
        "equity_short_term_residual_reversal_v1", (TargetWeight("A", 0.25, rank=1), TargetWeight("C", 0.25, rank=2))
    )
    composite = CompositeEquity(v3, reversal)  # type: ignore[arg-type]
    context = PortfolioContext(
        datetime(2024, 1, 2, tzinfo=UTC), Market.EQUITY, "1d", {}, None, "test", 100000, {}, {}, "SPY"
    )
    target = composite.decide(context)
    assert len(target.weights) == 3
    assert target.weights[0].symbol == "A"
    assert target.metadata["overlap_symbols"] == ["A"]
    parts = target.metadata["source_contributions"]["A"]
    assert parts["v3"] > 0 and parts["reversal"] > 0
    assert abs(sum(parts.values()) - target.weight_for("A", Market.EQUITY)) < 1e-12
    assert target.metadata["no_cross_sleeve_reallocation"] is True
