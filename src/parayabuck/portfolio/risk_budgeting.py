"""Small deterministic covariance-aware allocation helpers."""

from __future__ import annotations

import math
from typing import cast

import numpy as np


def aligned_returns(
    closes_by_symbol: dict[str, list[float]], window: int
) -> tuple[tuple[str, ...], np.ndarray]:
    symbols = tuple(sorted(closes_by_symbol))
    if not symbols:
        return (), np.empty((0, 0), dtype=float)
    length = min(len(closes_by_symbol[symbol]) for symbol in symbols)
    usable = min(length, window + 1)
    if usable < 3:
        return (), np.empty((0, 0), dtype=float)
    rows: list[list[float]] = []
    for symbol in symbols:
        closes = closes_by_symbol[symbol][-usable:]
        rows.append(
            [
                closes[index] / closes[index - 1] - 1
                for index in range(1, len(closes))
                if closes[index - 1] > 0
            ]
        )
    return symbols, np.asarray(rows, dtype=float).T


def covariance_budget_weights(
    closes_by_symbol: dict[str, list[float]],
    *,
    window: int,
    max_weight: float,
    annual_volatility_target: float,
    periods_per_year: int,
    allocation_cap: float = 1.0,
) -> tuple[dict[str, float], float]:
    """Long-only regularized minimum-variance budget with hard caps.

    The inverse covariance matrix captures cross-asset correlation. Negative
    unconstrained weights are projected to zero, then all weights are capped
    iteratively. The final scale is always volatility-targeted and can only
    reduce exposure.
    """
    symbols, returns = aligned_returns(closes_by_symbol, window)
    if not symbols or returns.shape[0] < 2:
        return {}, 0.0
    covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))
    ridge = max(1e-12, float(np.trace(covariance)) / max(1, len(symbols)) * 1e-6)
    inverse = np.linalg.pinv(covariance + np.eye(len(symbols)) * ridge)
    raw = np.maximum(inverse @ np.ones(len(symbols)), 0.0)
    if not np.isfinite(raw).all() or float(raw.sum()) <= 0:
        raw = np.ones(len(symbols), dtype=float)
    weights = raw / float(raw.sum()) * allocation_cap
    weights = _cap_and_redistribute(weights, max_weight, allocation_cap)
    portfolio_variance = float(weights @ covariance @ weights)
    annual_volatility = math.sqrt(max(0.0, portfolio_variance) * periods_per_year)
    scale = min(1.0, annual_volatility_target / annual_volatility) if annual_volatility > 0 else 0.0
    scaled = weights * scale
    return (
        {
            symbol: float(weight)
            for symbol, weight in zip(symbols, scaled, strict=True)
            if weight > 1e-12
        },
        scale,
    )


def _cap_and_redistribute(
    weights: np.ndarray, max_weight: float, allocation_cap: float
) -> np.ndarray:
    capped = np.zeros_like(weights)
    remaining = set(range(len(weights)))
    remaining_total = min(allocation_cap, float(weights.sum()))
    source = weights.copy()
    while remaining and remaining_total > 1e-12:
        source_total = sum(float(source[index]) for index in remaining)
        if source_total <= 0:
            break
        newly_capped: set[int] = set()
        for index in remaining:
            proposed = remaining_total * float(source[index]) / source_total
            if proposed >= max_weight:
                capped[index] = max_weight
                remaining_total -= max_weight
                newly_capped.add(index)
        if not newly_capped:
            for index in remaining:
                capped[index] = remaining_total * float(source[index]) / source_total
            break
        remaining -= newly_capped
    return cast(np.ndarray, np.asarray(capped, dtype=float))


__all__ = ["aligned_returns", "covariance_budget_weights"]
