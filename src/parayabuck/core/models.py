from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Market(StrEnum):
    """Asset class of a symbol.

    ParayaBuck trades equities only. ``CRYPTO`` classifies foreign crypto
    holdings, orders and historical ledger rows (the brokerage account is
    shared) so they are recognized and left alone; no code path trades,
    sizes, streams, protects or backtests crypto.
    """

    EQUITY = "equity"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    market: Market = Market.EQUITY

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar timestamps must be timezone-aware")
        if not all(math.isfinite(value) for value in (self.open, self.high, self.low, self.close, self.volume)):
            raise ValueError("Bar values must be finite")
        # A zero or negative price is never a traded price. Left in, a
        # non-positive close reads as a downtrend and can force a full exit.
        if self.low <= 0:
            raise ValueError("Bar prices must be positive")
        if self.high < self.low or not self.low <= self.open <= self.high:
            raise ValueError("Invalid OHLC values")
        if not self.low <= self.close <= self.high or self.volume < 0:
            raise ValueError("Invalid close or volume")


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    market: Market
    qty_available: float | None = None
    ownership: str = "external"
    # Quantity of this position owned (and therefore sellable) by the managed
    # portfolio path. The remainder is manual/external inventory that the bot
    # must never sell; a single managed/external flag cannot express a mixed
    # position where a user owns part of the same lot.
    managed_qty: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.qty):
            raise ValueError("Position qty must be finite")
        if not math.isfinite(self.avg_price) or not math.isfinite(self.current_price):
            raise ValueError("Position prices must be finite")
        if not math.isfinite(self.managed_qty) or self.managed_qty < -1e-12 or self.managed_qty > abs(self.qty) + 1e-12:
            raise ValueError("managed_qty must be finite, non-negative, and no greater than abs(qty)")

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    @property
    def external_qty(self) -> float:
        return abs(self.qty) - self.managed_qty

    @property
    def market_value(self) -> float:
        return abs(self.qty) * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_price) * self.qty


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    equity: float
    peak_equity: float
    daily_pnl: float
    positions: tuple[PositionSnapshot, ...] = ()
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    fees: float = 0.0
    # Alpaca ``non_marginable_buying_power`` (None when the broker did not
    # report it): the LIVE gap-safe BUY cap never spends above it.
    non_marginable_buying_power: float | None = None
    # Risk epoch the peak was measured in (broker peak sidecar). A consumer
    # that stores the peak must stamp this epoch, not the live one (C2-4-5).
    peak_epoch: int | None = None

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
