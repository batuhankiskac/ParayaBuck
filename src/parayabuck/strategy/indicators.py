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
    """RSI using Wilder's smoothing (standard).

    First avg_gain/avg_loss are simple averages over `period` changes, then
    smoothed recursively: avg = (prev_avg * (period-1) + current) / period.
    """
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
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
    """ATR using Wilder's smoothing (standard)."""
    n = len(closes)
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, n):
        trs.append(true_range(highs[i], lows[i], closes[i - 1]))
    # First ATR = simple average of first `period` TRs
    atr_val = sum(trs[:period]) / period
    # Wilder smoothing for the rest
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
    return atr_val


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
    """ADX using Wilder's smoothing (standard).

    Returns the ADX (averaged DX), not a single-period DX. This matches the
    canonical Welles Wilder definition and the conventional 25/20 thresholds.
    """
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

    # Wilder smoothing of +DM, -DM, TR
    smoothed_pdm = sum(plus_dm[:period]) / period
    smoothed_mdm = sum(minus_dm[:period]) / period
    smoothed_tr = sum(trs[:period]) / period
    dxs: list[float] = []

    for i in range(period, len(trs)):
        smoothed_pdm = (smoothed_pdm * (period - 1) + plus_dm[i]) / period
        smoothed_mdm = (smoothed_mdm * (period - 1) + minus_dm[i]) / period
        smoothed_tr = (smoothed_tr * (period - 1) + trs[i]) / period
        if smoothed_tr == 0:
            continue
        plus_di = (smoothed_pdm / smoothed_tr) * 100
        minus_di = (smoothed_mdm / smoothed_tr) * 100
        denom = plus_di + minus_di
        if denom == 0:
            dxs.append(0.0)
            continue
        dxs.append(abs(plus_di - minus_di) / denom * 100)

    if len(dxs) < period:
        return None
    # ADX = Wilder smoothed average of DX
    adx_val = sum(dxs[:period]) / period
    for i in range(period, len(dxs)):
        adx_val = (adx_val * (period - 1) + dxs[i]) / period
    return adx_val


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
