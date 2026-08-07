"""Shared statistics helpers for the backtest layer."""

from __future__ import annotations

import math


def sample_stddev(values: list[float]) -> float:
    """Sample standard deviation (n-1 denominator); 0.0 for < 2 samples."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


__all__ = ["sample_stddev"]
