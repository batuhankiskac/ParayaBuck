from __future__ import annotations

from collections.abc import Sequence

from parayabuck.core.models import Bar


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """Return a real EMA seeded by the first period's SMA."""
    if period <= 0 or len(values) < period:
        return None
    seed = sum(values[:period]) / period
    multiplier = 2.0 / (period + 1)
    result = seed
    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


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
        for previous, current in zip(bars[:-1], bars[1:], strict=False)
    ]


def atr(bars: list[Bar], period: int = 14) -> float | None:
    if period <= 0 or len(bars) < period + 1:
        return None
    ranges = true_ranges(bars[-(period + 1) :])
    return sum(ranges) / period


def adx(bars: list[Bar], period: int = 14) -> float | None:
    if period <= 0 or len(bars) < period * 2 + 1:
        return None
    window = bars[-(period * 2 + 1) :]
    ranges = true_ranges(window)
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in zip(window[:-1], window[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    dx_values: list[float] = []
    for index in range(period, len(ranges) + 1):
        tr = sum(ranges[index - period : index])
        if tr <= 0:
            continue
        plus = 100 * sum(plus_dm[index - period : index]) / tr
        minus = 100 * sum(minus_dm[index - period : index]) / tr
        denom = plus + minus
        dx_values.append(100 * abs(plus - minus) / denom if denom else 0.0)
    return sum(dx_values[-period:]) / min(period, len(dx_values)) if dx_values else None


def directional_indicators(bars: list[Bar], period: int = 14) -> tuple[float, float] | None:
    """Return the latest +DI and -DI using the same windowing as ``adx``."""
    if period <= 0 or len(bars) < period + 1:
        return None
    window = bars[-(period + 1) :]
    true_range = sum(true_ranges(window))
    plus_dm = 0.0
    minus_dm = 0.0
    for previous, current in zip(window[:-1], window[1:], strict=True):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm += up if up > down and up > 0 else 0.0
        minus_dm += down if down > up and down > 0 else 0.0
    if true_range <= 0:
        return None
    return 100 * plus_dm / true_range, 100 * minus_dm / true_range
