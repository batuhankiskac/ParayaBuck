from __future__ import annotations

from dataclasses import dataclass

from parayabuck.config import RiskLimits
from parayabuck.core.models import AccountSnapshot, Market, OrderIntent, Signal
from parayabuck.markets.sectors import sector_of


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    order: OrderIntent | None = None
    risk_amount: float = 0.0


class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self.consecutive_losses = 0

    def record_realized_pnl(self, pnl: float) -> None:
        self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    def evaluate(self, signal: Signal, account: AccountSnapshot) -> RiskDecision:
        if account.equity <= 0:
            return RiskDecision(False, "non_positive_equity")
        if signal.risk_reward < self.limits.minimum_risk_reward:
            return RiskDecision(False, f"risk_reward={signal.risk_reward:.2f}_below_minimum")
        if account.daily_pnl / account.equity <= -self.limits.daily_loss_limit_pct:
            return RiskDecision(False, "daily_loss_limit")
        if account.drawdown_pct >= self.limits.max_drawdown_pct:
            return RiskDecision(False, "max_drawdown_limit")

        active = tuple(position for position in account.positions if not position.is_flat)
        if len(active) >= self.limits.max_positions:
            return RiskDecision(False, "max_positions")
        gross_value = sum(position.market_value for position in active)
        gross_exposure = gross_value / account.equity
        if gross_exposure >= self.limits.max_gross_exposure_pct:
            return RiskDecision(False, "max_gross_exposure")
        if signal.market == Market.CRYPTO:
            crypto_value = sum(
                position.market_value for position in active if position.market == Market.CRYPTO
            )
            crypto_exposure = crypto_value / account.equity
            if crypto_exposure >= self.limits.max_crypto_exposure_pct:
                return RiskDecision(False, "max_crypto_exposure")
        else:
            crypto_value = 0.0

        signal_sector = sector_of(signal.symbol)
        sector_value = sum(
            position.market_value
            for position in active
            if sector_of(position.symbol) == signal_sector
        )
        if sector_value / account.equity >= self.limits.max_sector_exposure_pct:
            return RiskDecision(False, "max_sector_exposure")

        stop_distance = abs(signal.entry_price - signal.invalidation_price)
        if stop_distance <= 0:
            return RiskDecision(False, "invalid_stop_distance")
        risk_fraction = self.limits.risk_per_trade_pct
        if self.consecutive_losses >= self.limits.consecutive_loss_reduction_after:
            risk_fraction *= 0.5
        risk_amount = account.equity * risk_fraction
        qty_by_risk = risk_amount / stop_distance
        qty_by_position = account.equity * self.limits.max_position_pct / signal.entry_price
        qty_by_cash = max(0.0, account.cash * 0.95 / signal.entry_price)
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
        sizing_caps = [
            qty_by_risk,
            qty_by_position,
            qty_by_cash,
            qty_by_gross,
            qty_by_sector,
        ]
        if signal.market == Market.CRYPTO:
            remaining_crypto = max(
                0.0,
                account.equity * self.limits.max_crypto_exposure_pct - crypto_value,
            )
            sizing_caps.append(remaining_crypto / signal.entry_price)
        qty = min(sizing_caps)
        if signal.market == Market.EQUITY:
            # Alpaca bracket orders require whole shares; size down to an integer.
            qty = int(qty)
        else:
            qty = round(qty, 8)
        if qty <= 0 or qty * signal.entry_price < 5:
            return RiskDecision(False, "order_below_minimum_notional")

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
        )
