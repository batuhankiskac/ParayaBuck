from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from parayabuck.markets.regime import RegimeAssessment


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


class TrailingStopType(StrEnum):
    NONE = "none"
    CHANDELIER = "chandelier"


@dataclass(frozen=True)
class ExitPolicy:
    """Serializable, strategy-independent rules for managing an open trade.

    Dynamic stops are calculated from completed bars and become active on the
    following bar.  The policy deliberately excludes partial take-profit rules
    so the first research surface stays small and auditable.
    """

    policy_id: str = "fixed"
    initial_stop_atr_multiple: float | None = None
    target_r_multiple: float | None = None
    break_even_trigger_r: float | None = None
    trailing_type: TrailingStopType = TrailingStopType.NONE
    trailing_atr_multiple: float | None = None
    trailing_atr_period: int = 14
    trailing_activation_r: float | None = None
    max_holding_bars: int | None = None
    regime_exit: bool = False

    @property
    def is_dynamic(self) -> bool:
        return any(
            (
                self.break_even_trigger_r is not None,
                self.trailing_type != TrailingStopType.NONE,
                self.max_holding_bars is not None,
                self.regime_exit,
            )
        )

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("Exit policy id cannot be empty")
        positive_values = (
            self.initial_stop_atr_multiple,
            self.target_r_multiple,
            self.break_even_trigger_r,
            self.trailing_atr_multiple,
            self.trailing_activation_r,
        )
        if any(
            value is not None and (not math.isfinite(value) or value <= 0)
            for value in positive_values
        ):
            raise ValueError("Exit policy numeric values must be finite and positive")
        if self.max_holding_bars is not None and self.max_holding_bars <= 0:
            raise ValueError("Maximum holding bars must be positive")
        if self.trailing_atr_period <= 0:
            raise ValueError("Trailing ATR period must be positive")
        if self.trailing_type == TrailingStopType.NONE and self.trailing_atr_multiple is not None:
            raise ValueError("A trailing multiple requires an active trailing type")
        if self.trailing_type != TrailingStopType.NONE and self.trailing_atr_multiple is None:
            raise ValueError("An active trailing type requires a trailing multiple")


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
        if not all(
            math.isfinite(value)
            for value in (self.open, self.high, self.low, self.close, self.volume)
        ):
            raise ValueError("Bar values must be finite")
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
    strategy_version: str = "1.0.0"
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    volatility_at_entry: float | None = None
    minimum_risk_reward: float | None = None
    reentry_cooldown_bars: int = 0
    normalized_expected_r: float | None = None

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry_price - self.invalidation_price)
        reward = abs(self.target_price - self.entry_price)
        return reward / risk if risk > 0 else 0.0

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Signal timestamps must be timezone-aware")
        if (
            not math.isfinite(self.score)
            or not math.isfinite(self.confidence)
            or not 0 <= self.score <= 100
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Signal score/confidence out of range")
        prices = (self.entry_price, self.invalidation_price, self.target_price)
        if any(not math.isfinite(value) for value in prices) or min(prices) <= 0:
            raise ValueError("Signal prices must be positive")
        if not self.strategy_version.strip():
            raise ValueError("Strategy version cannot be empty")
        if self.volatility_at_entry is not None and (
            not math.isfinite(self.volatility_at_entry) or self.volatility_at_entry <= 0
        ):
            raise ValueError("Entry volatility must be finite and positive")
        if self.minimum_risk_reward is not None and (
            not math.isfinite(self.minimum_risk_reward) or self.minimum_risk_reward <= 0
        ):
            raise ValueError("Minimum risk/reward must be finite and positive")
        if self.reentry_cooldown_bars < 0:
            raise ValueError("Re-entry cooldown cannot be negative")
        if self.normalized_expected_r is not None and not math.isfinite(
            self.normalized_expected_r
        ):
            raise ValueError("Normalized expected R must be finite")


@dataclass(frozen=True)
class StrategyContext:
    """Everything a signal strategy may observe at a decision point."""

    regime: RegimeAssessment
    position: PositionSnapshot | None = None
    higher_timeframe_regime: Regime = Regime.UNKNOWN
    benchmark_regime: Regime = Regime.UNKNOWN
    relative_strength: float | None = None


@dataclass(frozen=True)
class ConditionEvaluation:
    """One explainable strategy gate with machine-readable values."""

    code: str
    passed: bool
    actual: Any
    expected: str
    explanation: str


@dataclass(frozen=True)
class StrategyEvaluation:
    """A strategy result together with the evidence used to reach it."""

    strategy: str
    fired: bool
    summary: str
    conditions: tuple[ConditionEvaluation, ...] = ()
    metrics: dict[str, Any] | None = None
    signal: Signal | None = None


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    market: Market
    qty_available: float | None = None
    ownership: str = "external"
    stop_price: float | None = None
    initial_risk: float | None = None
    bars_held: int = 0
    trailing_stop: float | None = None
    highest_high: float | None = None
    lowest_low: float | None = None
    pending_trailing_stop: float | None = None

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
    crypto_buying_power: float | None = None
    crypto_trading_enabled: bool = True
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
    strategy_version: str = "1.0.0"
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    volatility_at_entry: float | None = None
    signal_score: float = 0.0
    normalized_expected_r: float | None = None
    signal_regime: Regime = Regime.UNKNOWN


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    qty: float
    price: float
    direction: Direction
    timestamp: datetime
    commission: float = 0.0
