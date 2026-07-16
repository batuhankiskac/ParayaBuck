from __future__ import annotations

from parayabuck.logger import get_logger

log = get_logger("parayabuck.risk.kelly")


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Full Kelly fraction.

    f* = (p * b - q) / b
    where p = win_rate, q = 1 - p, b = win_loss_ratio (avg_win / avg_loss)

    Returns 0.0 if edge is negative (don't trade).
    """
    if win_rate <= 0 or win_rate >= 1:
        return 0.0
    if win_loss_ratio <= 0:
        return 0.0
    p = win_rate
    q = 1 - win_rate
    b = win_loss_ratio
    f = (p * b - q) / b
    return max(f, 0.0)


def fractional_kelly(
    win_rate: float,
    win_loss_ratio: float,
    fraction: float = 0.25,
    max_alloc: float = 0.25,
) -> float:
    """Fractional Kelly with cap.

    Args:
        win_rate: Historical win probability (0-1)
        win_loss_ratio: avg_win / avg_loss (positive)
        fraction: Use 1/4 Kelly by default (safer, less volatile)
        max_alloc: Cap allocation per trade at 25% of capital

    Returns: fraction of capital to risk (0 - max_alloc)
    """
    full = kelly_fraction(win_rate, win_loss_ratio)
    fk = full * fraction
    capped = min(fk, max_alloc)
    if capped < 0.01:
        log.debug(
            f"Kelly too small: win_rate={win_rate:.2f}, ratio={win_loss_ratio:.2f} → {capped:.4f}"
        )
    return max(capped, 0.0)


def kelly_position_size(
    capital: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    entry_price: float,
    stop_price: float,
    kelly_fraction_value: float = 0.25,
    max_alloc_pct: float = 0.10,
) -> int:
    """Calculate position size using fractional Kelly.

    Args:
        capital: Total account equity
        win_rate: Historical win probability
        avg_win: Average winning trade dollar amount
        avg_loss: Average losing trade dollar amount (positive value)
        entry_price: Expected entry price
        stop_price: Stop-loss price
        kelly_fraction_value: Fraction of full Kelly to use (0.25 = quarter Kelly)
        max_alloc_pct: Max % of capital to allocate to one trade

    Returns: Number of shares to buy (int, >= 0)
    """
    if avg_loss <= 0 or entry_price <= 0:
        return 0

    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return 0

    win_loss_ratio = avg_win / avg_loss
    alloc_fraction = fractional_kelly(
        win_rate, win_loss_ratio, kelly_fraction_value, max_alloc_pct
    )

    capital_to_risk = capital * alloc_fraction
    qty = int(capital_to_risk / risk_per_share)
    return max(qty, 0)
