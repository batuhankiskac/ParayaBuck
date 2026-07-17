from __future__ import annotations

from parayabuck.logger import get_logger
from parayabuck.strategy.indicators import atr

log = get_logger("parayabuck.risk.volatility")


def volatility_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
    max_qty: int = 1000,
    side: str = "BUY",
) -> tuple[int, float]:
    """ATR-based position sizing.

    position_size = (capital * risk_pct) / (ATR * atr_multiplier)

    Args:
        capital: Account equity
        risk_pct: Fraction of capital to risk per trade (e.g. 0.01 = 1%)
        entry_price: Expected entry price
        highs: Historical high prices
        lows: Historical low prices
        closes: Historical close prices
        atr_period: ATR lookback period
        atr_multiplier: Stop distance = ATR * multiplier
        max_qty: Hard cap on shares
        side: "BUY" (long, stop below) or "SELL" (short, stop above)

    Returns: (quantity, stop_price)
    """
    atr_val = atr(highs, lows, closes, atr_period)
    if atr_val is None or atr_val <= 0:
        log.warning("ATR not available or zero, returning 0 size")
        return 0, entry_price

    stop_distance = atr_val * atr_multiplier
    risk_amount = capital * risk_pct
    qty = int(risk_amount / stop_distance)
    qty = min(qty, max_qty)
    qty = max(qty, 0)

    if side.upper() == "SELL":
        stop_price = entry_price + stop_distance
    else:
        stop_price = entry_price - stop_distance
    return qty, stop_price


def calc_stop_loss(
    entry_price: float,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    side: str = "BUY",
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
) -> float:
    """Calculate ATR-based stop-loss price.

    For BUY: stop below entry by ATR * multiplier
    For SELL: stop above entry by ATR * multiplier
    """
    atr_val = atr(highs, lows, closes, atr_period)
    if atr_val is None:
        atr_val = entry_price * 0.02

    stop_distance = atr_val * atr_multiplier
    if side.upper() == "BUY":
        return entry_price - stop_distance
    else:
        return entry_price + stop_distance


def calc_take_profit(
    entry_price: float,
    stop_price: float,
    risk_reward_ratio: float = 2.0,
    side: str = "BUY",
) -> float:
    """Calculate take-profit based on risk/reward ratio."""
    risk = abs(entry_price - stop_price)
    reward = risk * risk_reward_ratio
    if side.upper() == "BUY":
        return entry_price + reward
    else:
        return entry_price - reward
