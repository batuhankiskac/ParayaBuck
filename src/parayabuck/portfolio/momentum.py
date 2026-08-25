from __future__ import annotations

import math
import statistics


def realized_volatility(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        return 0.0
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(len(closes) - window, len(closes))
        if closes[index - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def reject_quantile_threshold(values: list[float], quantile: float) -> float:
    """Nearest-rank empirical ``quantile``-threshold on index space [0, n-1].

    Contract: keeping the values ``<= threshold`` retains approximately the
    lowest ``quantile`` fraction of a distinct-valued sample — exactly
    ``floor(quantile * (n - 1)) + 1`` of n values — and rejects approximately
    the highest ``1 - quantile`` fraction. The classic ``sorted[int(q * n)]``
    index keeps one value too many (q*n + 1 of n), which materially over-retains
    for small candidate pools.

    Ties at the threshold are KEPT: equal values carry no distinguishing
    information, so retention may exceed the nominal fraction when ties
    straddle the cut. Deterministic for any input order. Requires a
    non-empty sample; ``quantile`` must be in [0, 1].
    """
    if not values:
        raise ValueError("reject_quantile_threshold requires a non-empty sample")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(values)
    return ordered[int(quantile * (len(ordered) - 1))]


def annualize_period_volatility(period_volatility: float, bars_per_year: float) -> float:
    return period_volatility * math.sqrt(bars_per_year)


def inverse_volatility_weights(
    symbols: tuple[str, ...],
    volatilities: dict[str, float],
    *,
    maximum_weight: float,
) -> tuple[tuple[str, float], ...]:
    """Inverse-vol weights capped at ``maximum_weight``; the unallocated
    residual is intentionally left as cash instead of being renormalized, so
    the cap is a real exposure limit even with a single qualifying asset."""
    if not symbols:
        return ()
    raw: dict[str, float] = {}
    for symbol in symbols:
        volatility = volatilities.get(symbol, 0.0)
        raw[symbol] = 1.0 / volatility if volatility > 0 else 1.0
    total = sum(raw.values())
    if total <= 0:
        return ()
    weights = {symbol: value / total for symbol, value in raw.items()}
    capped = {symbol: min(value, maximum_weight) for symbol, value in weights.items()}
    return tuple(sorted(capped.items(), key=lambda item: item[0]))


def diagonal_portfolio_volatility(weights: dict[str, float], volatilities: dict[str, float]) -> float:
    """Diagonal-approximation portfolio volatility from per-asset realized vols."""
    return math.sqrt(sum(weight**2 * volatilities.get(symbol, 0.0) ** 2 for symbol, weight in weights.items()))


def conservative_portfolio_volatility(
    weights: dict[str, float],
    volatilities: dict[str, float],
    *,
    correlation_floor: float = 0.25,
) -> float:
    """Volatility estimate with a positive pairwise-correlation floor.

    A short rolling covariance matrix is unstable for a changing scanner
    universe. This deterministic shrinkage-style floor avoids the materially
    optimistic zero-correlation assumption while remaining robust.
    """
    if not 0 <= correlation_floor <= 1:
        raise ValueError("correlation_floor must be in [0, 1]")
    exposures = [weight * volatilities.get(symbol, 0.0) for symbol, weight in weights.items()]
    diagonal = sum(exposure**2 for exposure in exposures)
    cross = sum(
        2 * correlation_floor * exposures[left] * exposures[right]
        for left in range(len(exposures))
        for right in range(left + 1, len(exposures))
    )
    return math.sqrt(max(0.0, diagonal + cross))


__all__ = [
    "annualize_period_volatility",
    "conservative_portfolio_volatility",
    "diagonal_portfolio_volatility",
    "inverse_volatility_weights",
    "realized_volatility",
    "reject_quantile_threshold",
]
