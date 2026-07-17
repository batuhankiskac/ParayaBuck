from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"Bar high ({self.high}) < low ({self.low})")
        if self.close > self.high or self.close < self.low:
            raise ValueError(f"Bar close ({self.close}) outside H/L range")


@dataclass
class Order:
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    tif: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    filled_qty: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    filled_at: datetime | None = None
    order_id: str | None = None

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"Order qty must be positive, got {self.qty}")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type} requires limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError(f"{self.order_type} requires stop_price")


@dataclass
class Position:
    symbol: str
    qty: float = 0.0          # positive = long, negative = short, 0 = flat
    avg_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    def unrealized_pnl(self, current_price: float, point_value: float = 1.0) -> float:
        if self.is_flat:
            return 0.0
        return (current_price - self.avg_price) * self.qty * point_value


@dataclass
class Trade:
    timestamp: datetime
    symbol: str
    side: Side
    qty: float
    price: float
    commission: float = 0.0
    realized_pnl: float = 0.0  # PnL realized on closing fills (0 for entries)
    trade_id: str | None = None
    order_id: str | None = None


@dataclass
class Account:
    balance: float = 0.0
    equity: float = 0.0
    peak_equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    daily_pnl: float = 0.0
    trades: list[Trade] = field(default_factory=list)
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        return self.peak_equity - self.equity if self.peak_equity > 0 else 0.0

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]
