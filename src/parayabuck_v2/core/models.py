from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class Market(StrEnum):
    EQUITY = "equity"
    CRYPTO = "crypto"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Regime(StrEnum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


class OrderState(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


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
        if self.high < self.low or not self.low <= self.open <= self.high:
            raise ValueError("Invalid OHLC values")
        if not self.low <= self.close <= self.high or self.volume < 0:
            raise ValueError("Invalid close or volume")


@dataclass(frozen=True)
class Signal:
    symbol: str
    market: Market
    direction: Direction
    strategy: str
    score: float
    confidence: float
    entry_price: float
    invalidation_price: float
    target_price: float
    timestamp: datetime
    reasons: tuple[str, ...] = ()
    regime: Regime = Regime.UNKNOWN
    signal_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry_price - self.invalidation_price)
        reward = abs(self.target_price - self.entry_price)
        return reward / risk if risk > 0 else 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100 or not 0 <= self.confidence <= 1:
            raise ValueError("Signal score/confidence out of range")
        if min(self.entry_price, self.invalidation_price, self.target_price) <= 0:
            raise ValueError("Signal prices must be positive")


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    market: Market

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

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

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)


@dataclass(frozen=True)
class OrderIntent:
    signal_id: str
    symbol: str
    market: Market
    direction: Direction
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    strategy: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    client_order_id: str = field(default_factory=lambda: f"pb2-{uuid4().hex}")


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    qty: float
    price: float
    direction: Direction
    timestamp: datetime
    commission: float = 0.0
