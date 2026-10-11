"""Corporate actions consumed by the portfolio backtest engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CorporateActionType(StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"


@dataclass(frozen=True)
class CorporateAction:
    timestamp: datetime
    symbol: str
    action_type: CorporateActionType
    value: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Corporate-action timestamps must be timezone-aware")
        if not math.isfinite(self.value) or self.value <= 0:
            raise ValueError("Corporate-action value must be finite and positive")


__all__ = [
    "CorporateAction",
    "CorporateActionType",
]
