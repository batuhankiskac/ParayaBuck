from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Bar, Market


@dataclass(frozen=True)
class MomentumSelection:
    as_of: datetime
    weights: tuple[tuple[str, float], ...]
    raw_momentum: tuple[tuple[str, float], ...]


def dual_momentum_weights(
    bars: list[Bar],
    as_of: datetime,
    *,
    lookback_bars: int = 252,
    skip_recent_bars: int = 21,
    top_n: int = 3,
    volatility_window: int = 20,
) -> MomentumSelection:
    """Monthly 12-1 ETF momentum with absolute filter and inverse-vol weights."""
    if lookback_bars <= skip_recent_bars or top_n <= 0 or volatility_window < 2:
        raise ValueError("Invalid dual-momentum window")
    histories = _histories(bars, as_of, Market.EQUITY)
    ranked: list[tuple[float, float, str]] = []
    raw: list[tuple[str, float]] = []
    for symbol, history in histories.items():
        if len(history) < lookback_bars + 1:
            continue
        start = history[-lookback_bars - 1].close
        end = history[-skip_recent_bars - 1].close
        momentum = end / start - 1 if start > 0 else -1.0
        raw.append((symbol, momentum))
        if momentum <= 0:
            continue
        volatility = _realized_volatility(history[-volatility_window - 1 :])
        if volatility > 0:
            ranked.append((momentum, volatility, symbol))
    ranked.sort(reverse=True)
    selected = ranked[:top_n]
    inverse = [(symbol, 1 / volatility) for _, volatility, symbol in selected]
    total = sum(value for _, value in inverse)
    weights = tuple((symbol, value / total) for symbol, value in inverse) if total else ()
    return MomentumSelection(as_of, weights, tuple(sorted(raw)))


def crypto_cross_sectional_momentum(
    bars: list[Bar],
    as_of: datetime,
    *,
    btc_regime_positive: bool,
    lookback_bars: int = 60,
    top_n: int = 3,
    liquidity_window: int = 24 * 7,
) -> MomentumSelection:
    """Rank Alpaca spot pairs using trailing return and trailing dollar liquidity."""
    if not btc_regime_positive:
        return MomentumSelection(as_of, (), ())
    histories = _histories(bars, as_of, Market.CRYPTO)
    ranked: list[tuple[float, float, str]] = []
    raw: list[tuple[str, float]] = []
    for symbol, history in histories.items():
        if len(history) < max(lookback_bars + 1, liquidity_window):
            continue
        start = history[-lookback_bars - 1].close
        momentum = history[-1].close / start - 1 if start > 0 else -1.0
        raw.append((symbol, momentum))
        liquidity = sum(
            bar.close * bar.volume for bar in history[-liquidity_window:]
        ) / liquidity_window
        if momentum > 0 and liquidity > 0:
            ranked.append((momentum, liquidity, symbol))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = ranked[:top_n]
    if not selected:
        return MomentumSelection(as_of, (), tuple(sorted(raw)))
    equal_weight = 1 / len(selected)
    return MomentumSelection(
        as_of,
        tuple((symbol, equal_weight) for _, _, symbol in selected),
        tuple(sorted(raw)),
    )


def time_series_momentum_target(
    bars: list[Bar],
    as_of: datetime,
    *,
    lookback_bars: int = 252,
    volatility_window: int = 20,
    annualized_volatility_target: float = 0.08,
) -> tuple[tuple[str, float], ...]:
    """Long-or-cash daily TSM exposure, capped at 1x with no leverage."""
    histories = _histories(bars, as_of, Market.EQUITY)
    results: list[tuple[str, float]] = []
    for symbol, history in histories.items():
        if len(history) < max(lookback_bars + 1, volatility_window + 1):
            continue
        start = history[-lookback_bars - 1].close
        momentum = history[-1].close / start - 1 if start > 0 else -1.0
        if momentum <= 0:
            results.append((symbol, 0.0))
            continue
        volatility = _realized_volatility(history[-volatility_window - 1 :]) * math.sqrt(252)
        exposure = min(1.0, annualized_volatility_target / volatility) if volatility > 0 else 0.0
        results.append((symbol, max(0.0, exposure)))
    return tuple(sorted(results))


def _histories(bars: list[Bar], as_of: datetime, market: Market) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = {}
    for bar in bars:
        if bar.market == market and bar.timestamp < as_of:
            grouped.setdefault(bar.symbol, []).append(bar)
    return {
        symbol: sorted(history, key=lambda bar: bar.timestamp)
        for symbol, history in grouped.items()
    }


def _realized_volatility(bars: list[Bar]) -> float:
    returns = [
        bars[index].close / bars[index - 1].close - 1
        for index in range(1, len(bars))
        if bars[index - 1].close > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    return math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))


__all__ = [
    "MomentumSelection",
    "crypto_cross_sectional_momentum",
    "dual_momentum_weights",
    "time_series_momentum_target",
]
