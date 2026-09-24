"""Structural target receipt around the byte-identical archived candidate."""

from __future__ import annotations

from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio

from .candidate import EquityShortTermResidualReversalV1


class RecordingReversal(EquityShortTermResidualReversalV1):
    def __init__(self, parameters: dict[str, object]) -> None:
        self.raw_targets: list[dict[str, object]] = []
        super().__init__(parameters)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        target = super().decide(context)
        self.raw_targets.append(
            {
                "label_time": context.decision_timestamp.isoformat(),
                "weights": [[item.symbol, item.weight, item.rank] for item in target.weights],
            }
        )
        return target
