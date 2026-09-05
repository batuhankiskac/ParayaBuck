"""Research-only correction for the high-proximity 364 history contract.

The frozen screen adapter requests a 364-price window but inherits the
incumbent's 23-bar minimum-history declaration.  The native engine trims
non-benchmark context histories to the declared minimum (or its 274-bar
global floor), so that declaration must be explicit before any corrected
economic replay.  This module leaves the frozen screen runner untouched.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from parayabuck.core.models import Market  # noqa: E402
from scripts.research.portfolio_expansion_crypto_screen_2026_09 import (  # noqa: E402
    HighProximity364Adapter,
)


WINDOW_BARS = 364
TOP_N = 2
REBALANCE_HOURS = 96


class HighProximity364HistoryFixedAdapter(HighProximity364Adapter):
    """Corrected research identity with an explicit 364-bar contract.

    The score consumes the last 364 consecutive completed daily prices,
    including the current price.  Therefore 364 bars, rather than 365, are
    the required history length; there are 363 intervals inside that window.
    The inherited score, descending rank, HYST4 retention, top-2 equal weight,
    96-hour cadence, and market identity are unchanged.
    """

    name = "research_high_proximity364_history_fixed"
    version = "1.0.0-research-history-fixed"
    market = Market.CRYPTO

    @property
    def minimum_history_bars(self) -> int:
        return WINDOW_BARS

    def _validate_parameters(self) -> None:
        super()._validate_parameters()
        if self.parameters.get("top_n", TOP_N) != TOP_N:
            raise ValueError("corrected high-proximity freezes top_n=2")
        if self.parameters.get("rebalance_hours", REBALANCE_HOURS) != REBALANCE_HOURS:
            raise ValueError("corrected high-proximity freezes rebalance_hours=96")


def build_strategy() -> HighProximity364HistoryFixedAdapter:
    """Build the corrected candidate with no tunable research parameters."""

    return HighProximity364HistoryFixedAdapter(
        {"top_n": TOP_N, "rebalance_hours": REBALANCE_HOURS}
    )


__all__ = [
    "HighProximity364HistoryFixedAdapter",
    "REBALANCE_HOURS",
    "TOP_N",
    "WINDOW_BARS",
    "build_strategy",
]
