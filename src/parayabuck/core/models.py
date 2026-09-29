from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class Market(StrEnum):
    """Asset class of a symbol.

    ParayaBuck trades equities only. ``CRYPTO`` classifies foreign crypto
    holdings, orders and historical ledger rows (the brokerage account is
    shared) so they are recognized and left alone; no code path trades,
    sizes, streams, protects or backtests crypto.
    """

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
    RECONCILIATION_REQUIRED = "reconciliation_required"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    UNKNOWN = "unknown"


class OrderRole(StrEnum):
    ENTRY = "entry"
    TAKE_PROFIT = "take_profit"
    PROTECTIVE_STOP = "protective_stop"
    MANUAL_EXIT = "manual_exit"
    EMERGENCY_EXIT = "emergency_exit"


class ProtectionState(StrEnum):
    UNPROTECTED = "unprotected"
    REQUESTED = "requested"
    ACTIVE = "active"
    PARTIALLY_PROTECTED = "partially_protected"
    OVER_PROTECTED = "over_protected"
    TRIGGERED_UNFILLED = "triggered_unfilled"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionEvent:
    account_id: str
    client_order_id: str
    broker_order_id: str
    parent_order_id: str | None
    role: OrderRole
    symbol: str
    market: Market
    state: OrderState | None
    requested_qty: float
    cumulative_filled_qty: float
    average_fill_price: float | None
    occurred_at: datetime
    fee: float = 0.0
    activity_id: str | None = None


@dataclass(frozen=True)
class ExecutionTransition:
    previous_state: OrderState | None
    current_state: OrderState | None
    fill_delta: float
    position_delta: float
    realized_pnl_delta: float
    entry_client_order_id: str | None = None


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
    stop_price: float | None = None
    initial_risk: float | None = None
    bars_held: int = 0
    trailing_stop: float | None = None
    highest_high: float | None = None
    lowest_low: float | None = None
    pending_trailing_stop: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.qty):
            raise ValueError("Position qty must be finite")
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
    client_order_id: str = field(default_factory=lambda: f"parayabuck-{uuid4().hex}")


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    qty: float
    price: float
    direction: Direction
    timestamp: datetime
    commission: float = 0.0
