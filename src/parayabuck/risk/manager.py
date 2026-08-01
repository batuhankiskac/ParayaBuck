from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from parayabuck.config import RiskLimits
from parayabuck.core.models import AccountSnapshot, Direction, Market, OrderIntent, Signal
from parayabuck.markets.sectors import sector_of
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    order: OrderIntent | None = None
    risk_amount: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskState:
    account_id: str
    session_date: date
    day_start_equity: float
    peak_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    consecutive_losses: int
    circuit_breaker_reason: str | None
    updated_at: datetime


class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self.consecutive_losses = 0
        self._reservations: dict[str, OrderIntent] = {}
        self._lock = threading.RLock()

    def reserve(self, order: OrderIntent, remaining_qty: float | None = None) -> None:
        with self._lock:
            qty = order.qty if remaining_qty is None else max(0.0, remaining_qty)
            if qty <= 0:
                self._reservations.pop(order.client_order_id, None)
                return
            self._reservations[order.client_order_id] = OrderIntent(
                **{**order.__dict__, "qty": qty}
            )

    def release(self, client_order_id: str) -> None:
        with self._lock:
            self._reservations.pop(client_order_id, None)

    def record_realized_pnl(self, pnl: float) -> None:
        with self._lock:
            self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    def evaluate(self, signal: Signal, account: AccountSnapshot) -> RiskDecision:
        with self._lock:
            return self._evaluate_unlocked(signal, account)

    def _evaluate_unlocked(self, signal: Signal, account: AccountSnapshot) -> RiskDecision:
        diagnostics: dict[str, Any] = {
            "equity": account.equity,
            "cash": account.cash,
            "daily_pnl": account.daily_pnl,
            "drawdown_pct": account.drawdown_pct,
            "signal_risk_reward": signal.risk_reward,
            "active_positions": len(
                tuple(position for position in account.positions if not position.is_flat)
            ),
            "pending_entry_orders": len(self._reservations),
        }

        def reject(reason: str, **details: Any) -> RiskDecision:
            return RiskDecision(False, reason, diagnostics={**diagnostics, **details})

        if account.equity <= 0:
            return reject("non_positive_equity")
        if signal.direction == Direction.FLAT:
            return reject("flat_signal")
        if signal.market == Market.CRYPTO and not account.crypto_trading_enabled:
            return reject("crypto_trading_not_active")
        if signal.market == Market.CRYPTO and signal.direction == Direction.SHORT:
            return reject("crypto_short_not_supported")
        if signal.direction == Direction.LONG and not (
            signal.invalidation_price < signal.entry_price < signal.target_price
        ):
            return reject("invalid_long_price_geometry")
        if signal.direction == Direction.SHORT and not (
            signal.target_price < signal.entry_price < signal.invalidation_price
        ):
            return reject("invalid_short_price_geometry")
        if signal.risk_reward < self.limits.minimum_risk_reward - 1e-9:
            return reject(
                f"risk_reward={signal.risk_reward:.2f}_below_minimum",
                minimum_risk_reward=self.limits.minimum_risk_reward,
            )
        if account.daily_pnl / account.equity <= -self.limits.daily_loss_limit_pct:
            return reject(
                "daily_loss_limit",
                daily_pnl_pct=account.daily_pnl / account.equity,
                daily_loss_limit_pct=self.limits.daily_loss_limit_pct,
            )
        if account.drawdown_pct >= self.limits.max_drawdown_pct:
            return reject(
                "max_drawdown_limit",
                max_drawdown_pct=self.limits.max_drawdown_pct,
            )

        active = tuple(position for position in account.positions if not position.is_flat)
        reserved_symbols = {
            canonical_symbol(order.symbol, order.market) for order in self._reservations.values()
        }
        if canonical_symbol(signal.symbol, signal.market) in reserved_symbols:
            return reject("pending_order_for_symbol")
        if len(active) + len(reserved_symbols) >= self.limits.max_positions:
            return reject("max_positions", max_positions=self.limits.max_positions)
        reserved_gross = sum(order.qty * order.entry_price for order in self._reservations.values())
        gross_value = sum(position.market_value for position in active) + reserved_gross
        gross_exposure = gross_value / account.equity
        if gross_exposure >= self.limits.max_gross_exposure_pct:
            return reject(
                "max_gross_exposure",
                gross_exposure_pct=gross_exposure,
                max_gross_exposure_pct=self.limits.max_gross_exposure_pct,
            )
        if signal.market == Market.CRYPTO:
            crypto_positions = sum(
                1 for position in active if position.market == Market.CRYPTO
            ) + sum(1 for order in self._reservations.values() if order.market == Market.CRYPTO)
            if crypto_positions >= self.limits.max_crypto_positions:
                return reject(
                    "max_crypto_positions",
                    crypto_positions=crypto_positions,
                    max_crypto_positions=self.limits.max_crypto_positions,
                )
            crypto_value = sum(
                position.market_value for position in active if position.market == Market.CRYPTO
            ) + sum(
                order.qty * order.entry_price
                for order in self._reservations.values()
                if order.market == Market.CRYPTO
            )
            crypto_exposure = crypto_value / account.equity
            if crypto_exposure >= self.limits.max_crypto_exposure_pct:
                return reject(
                    "max_crypto_exposure",
                    crypto_exposure_pct=crypto_exposure,
                    max_crypto_exposure_pct=self.limits.max_crypto_exposure_pct,
                )
        else:
            crypto_value = 0.0

        signal_sector = sector_of(signal.symbol)
        sector_value = sum(
            position.market_value
            for position in active
            if sector_of(position.symbol) == signal_sector
        ) + sum(
            order.qty * order.entry_price
            for order in self._reservations.values()
            if sector_of(order.symbol) == signal_sector
        )
        if sector_value / account.equity >= self.limits.max_sector_exposure_pct:
            return reject(
                "max_sector_exposure",
                sector=signal_sector,
                sector_exposure_pct=sector_value / account.equity,
                max_sector_exposure_pct=self.limits.max_sector_exposure_pct,
            )

        stop_distance = abs(signal.entry_price - signal.invalidation_price)
        if stop_distance <= 0:
            return reject("invalid_stop_distance")
        risk_fraction = self.limits.risk_per_trade_pct
        if self.consecutive_losses >= self.limits.consecutive_loss_reduction_after:
            risk_fraction *= 0.5
        risk_amount = account.equity * risk_fraction
        qty_by_risk = risk_amount / stop_distance
        qty_by_position = account.equity * self.limits.max_position_pct / signal.entry_price
        if signal.market == Market.CRYPTO:
            qty_by_position = min(
                qty_by_position,
                account.equity * self.limits.max_crypto_position_pct / signal.entry_price,
            )
        available_cash = account.cash
        if signal.market == Market.CRYPTO and account.crypto_buying_power is not None:
            available_cash = min(available_cash, account.crypto_buying_power)
        qty_by_cash = max(
            0.0,
            available_cash * (1 - self.limits.cash_buffer_pct) / signal.entry_price,
        )
        qty_by_gross = (
            max(
                0.0,
                account.equity * self.limits.max_gross_exposure_pct - gross_value,
            )
            / signal.entry_price
        )
        qty_by_sector = (
            max(
                0.0,
                account.equity * self.limits.max_sector_exposure_pct - sector_value,
            )
            / signal.entry_price
        )
        sizing_caps = {
            "risk": qty_by_risk,
            "position_value": qty_by_position,
            "cash": qty_by_cash,
            "gross_exposure": qty_by_gross,
            "sector_exposure": qty_by_sector,
        }
        if signal.market == Market.CRYPTO:
            remaining_crypto = max(
                0.0,
                account.equity * self.limits.max_crypto_exposure_pct - crypto_value,
            )
            sizing_caps["crypto_exposure"] = remaining_crypto / signal.entry_price
            sizing_caps["crypto_order_notional"] = (
                self.limits.maximum_crypto_order_notional / signal.entry_price
            )
        binding_cap = min(sizing_caps, key=lambda name: sizing_caps[name])
        qty = sizing_caps[binding_cap]
        if signal.market == Market.EQUITY:
            # Alpaca bracket orders require whole shares; size down to an integer.
            qty = int(qty)
        else:
            qty = float(Decimal(str(qty)).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN))
        minimum_notional = (
            self.limits.minimum_crypto_order_notional if signal.market == Market.CRYPTO else 0
        )
        diagnostics.update(
            {
                "risk_fraction": risk_fraction,
                "risk_amount": risk_amount,
                "stop_distance": stop_distance,
                "gross_exposure_pct": gross_exposure,
                "reserved_gross_exposure": reserved_gross,
                "sector": signal_sector,
                "sector_exposure_pct": sector_value / account.equity,
                "sizing_caps_qty": sizing_caps,
                "binding_cap": binding_cap,
                "final_qty": qty,
                "final_notional": qty * signal.entry_price,
            }
        )
        if qty <= 0 or qty * signal.entry_price < minimum_notional:
            return reject(
                "order_below_minimum_notional",
                **{
                    key: value
                    for key, value in diagnostics.items()
                    if key not in {"equity", "cash"}
                },
            )

        return RiskDecision(
            True,
            "approved",
            OrderIntent(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                market=signal.market,
                direction=signal.direction,
                qty=qty,
                entry_price=signal.entry_price,
                stop_price=signal.invalidation_price,
                target_price=signal.target_price,
                strategy=signal.strategy,
                created_at=signal.timestamp,
            ),
            risk_amount,
            diagnostics,
        )
