from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass


def sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def ema_series(values: Sequence[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out: list[float] = []
    e = sum(values[:period]) / period
    out.append(e)
    for v in values[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


@dataclass
class BollingerBands:
    upper: float
    middle: float
    lower: float
    bandwidth: float


def bollinger(
    values: Sequence[float], period: int = 20, num_std: float = 2.0
) -> BollingerBands | None:
    if len(values) < period:
        return None
    window = list(values[-period:])
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance**0.5
    upper = mid + num_std * std
    lower = mid - num_std * std
    return BollingerBands(
        upper=upper, middle=mid, lower=lower, bandwidth=(upper - lower) / mid if mid else 0
    )


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> float | None:
    n = len(closes)
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, n):
        trs.append(true_range(highs[i], lows[i], closes[i - 1]))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def vwap(highs: Sequence[float], lows: Sequence[float], volumes: Sequence[int]) -> float:
    total_pv = 0.0
    total_v = 0
    for hi, lo, v in zip(highs, lows, volumes, strict=True):
        tp = (hi + lo) / 2
        total_pv += tp * v
        total_v += v
    if total_v == 0:
        return 0.0
    return total_pv / total_v


def adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> float | None:
    n = len(closes)
    if n < period * 2 + 1:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm = up if (up > down and up > 0) else 0.0
        mdm = down if (down > up and down > 0) else 0.0
        plus_dm.append(pdm)
        minus_dm.append(mdm)
        trs.append(true_range(highs[i], lows[i], closes[i - 1]))

    atr_val = sum(trs[-period:]) / period
    if atr_val == 0:
        return 0.0
    plus_di = (sum(plus_dm[-period:]) / period) / atr_val * 100
    minus_di = (sum(minus_dm[-period:]) / period) / atr_val * 100
    if plus_di + minus_di == 0:
        return 0.0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    return dx


def zscore(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = list(values[-period:])
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance**0.5
    if std == 0:
        return 0.0
    return float((values[-1] - mean) / std)


def rolling_mean(values: Sequence[float], period: int) -> float | None:
    return sma(values, period)


def rolling_std(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = list(values[-period:])
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    return float(variance**0.5)


def linear_regression_slope(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    y = list(values[-period:])
    n = period
    x_mean = (n - 1) / 2
    y_mean = sum(y) / n
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


class IndicatorBuffer:
    """Rolling buffer for indicator calculations."""

    def __init__(self, maxlen: int) -> None:
        self._buf: deque[float] = deque(maxlen=maxlen)

    def push(self, value: float) -> None:
        self._buf.append(value)

    @property
    def values(self) -> list[float]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)
