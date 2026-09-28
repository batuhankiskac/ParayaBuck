from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from parayabuck.core.models import Bar


def rsi(values: list[float], period: int = 14) -> float | None:
    if period <= 0 or len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    avg_gain = sum(max(change, 0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0) for change in changes[:period]) / period
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    """True range for each consecutive bar pair (previous, current)."""
    return [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    ]
