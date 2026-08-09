from __future__ import annotations

import math
import statistics


def momentum_at(closes: list[float], lookback_bars: int, skip_bars: int = 21) -> float:
    """Return over [t-lookback, t-skip) on completed bars, or -inf if short."""
    if len(closes) < lookback_bars + skip_bars + 1:
        return float("-inf")
    start = closes[-(lookback_bars + skip_bars + 1)]
    end = closes[-skip_bars - 1]
    if start <= 0:
        return float("-inf")
    return end / start - 1


def composite_momentum(
    closes: list[float],
    lookbacks: tuple[int, ...] = (63, 126, 252),
    skip_bars: int = 21,
) -> float:
    """Sum of normalized multi-horizon returns; each component is divided by its
    own standard deviation so no single horizon dominates."""
    returns = [momentum_at(closes, lookback, skip_bars) for lookback in lookbacks]
    if any(not math.isfinite(value) for value in returns):
        return float("-inf")
    scaled: list[float] = []
    for lookback, value in zip(lookbacks, returns, strict=True):
        window = closes[-(lookback + skip_bars) :]
        deviations = [
            window[index] / window[index - 1] - 1
            for index in range(1, len(window))
            if window[index - 1] > 0
        ]
        if len(deviations) < 5:
            return float("-inf")
        mean = statistics.fmean(deviations)
        variance = sum((item - mean) ** 2 for item in deviations) / (len(deviations) - 1)
        std = math.sqrt(variance)
        scaled.append(value / std if std > 0 else 0.0)
    return sum(scaled)


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


def simple_moving_average(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return statistics.fmean(closes[-window:])


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
    normalized = tuple(sorted(capped.items(), key=lambda item: item[0]))
    return normalized


def diagonal_portfolio_volatility(
    weights: dict[str, float], volatilities: dict[str, float]
) -> float:
    """Diagonal-approximation portfolio volatility from per-asset realized vols."""
    return math.sqrt(
        sum(weight**2 * volatilities.get(symbol, 0.0) ** 2 for symbol, weight in weights.items())
    )


__all__ = [
    "annualize_period_volatility",
    "composite_momentum",
    "diagonal_portfolio_volatility",
    "inverse_volatility_weights",
    "momentum_at",
    "realized_volatility",
    "simple_moving_average",
]
