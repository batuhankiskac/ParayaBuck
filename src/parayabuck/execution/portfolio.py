"""Target-weight planning and account-level portfolio risk enforcement."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight


class PortfolioOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class AssetTradingRules:
    symbol: str
    market: Market
    minimum_order_size: float
    quantity_increment: float
    price_increment: float
    minimum_order_notional: float

    def __post_init__(self) -> None:
        values = (
            self.minimum_order_size,
            self.quantity_increment,
            self.price_increment,
            self.minimum_order_notional,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Asset trading rules must be finite and non-negative")
        if self.quantity_increment <= 0 or self.price_increment <= 0:
            raise ValueError("Asset increments must be positive")


@dataclass(frozen=True)
class PortfolioOrderRequest:
    client_order_id: str
    decision_id: str
    symbol: str
    market: Market
    side: PortfolioOrderSide
    quantity: float
    reference_price: float
    decision_timestamp: datetime
    strategy_name: str
    strategy_version: str
    risk_reducing: bool


@dataclass(frozen=True)
class PortfolioRiskLimits:
    daily_loss_limit: float = 0.005
    drawdown_halt: float = 0.05
    maximum_gross: float = 0.60
    maximum_equity: float = 0.50
    maximum_crypto: float = 0.10
    maximum_equity_asset: float = 0.10
    maximum_crypto_asset: float = 0.05
    minimum_cash: float = 0.40
    maximum_positions: int = 6
    maximum_crypto_positions: int = 3


@dataclass(frozen=True)
class RiskOverlayDecision:
    target: TargetPortfolio
    halted: bool
    reasons: tuple[str, ...]
    gross_before: float
    gross_after: float


class PortfolioRiskOverlay:
    def __init__(self, limits: PortfolioRiskLimits | None = None) -> None:
        self.limits = limits or PortfolioRiskLimits()

    def apply(
        self,
        target: TargetPortfolio,
        account: AccountSnapshot,
        *,
        market: Market,
        approved_symbols: tuple[str, ...],
        maximum_allocation: float,
        mode: str,
    ) -> RiskOverlayDecision:
        equity = account.equity
        if equity <= 0:
            raise ValueError("Account equity must be positive")
        approved = {canonical_symbol(symbol, market) for symbol in approved_symbols}
        current_weights = {
            canonical_symbol(position.symbol, position.market): position.market_value / equity
            for position in account.positions
            if not position.is_flat
        }
        gross_before = sum(current_weights.values())
        daily_loss = max(0.0, -account.daily_pnl / equity)
        halted_reasons: list[str] = []
        if daily_loss >= self.limits.daily_loss_limit:
            halted_reasons.append("daily_loss_limit")
        if account.drawdown_pct >= self.limits.drawdown_halt:
            halted_reasons.append("portfolio_drawdown_halt")
        halted = bool(halted_reasons)

        immutable_gross = sum(
            position.market_value / equity
            for position in account.positions
            if not position.is_flat
            and (
                canonical_symbol(position.symbol, position.market) not in approved
                or position.ownership != "managed"
            )
        )
        immutable_sleeve = sum(
            position.market_value / equity
            for position in account.positions
            if not position.is_flat
            and position.market == market
            and (
                canonical_symbol(position.symbol, position.market) not in approved
                or position.ownership != "managed"
            )
        )
        desired = {
            canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights
        }
        if mode not in {"backtest", "paper", "live"}:
            raise ValueError(f"Unsupported portfolio execution mode: {mode}")
        per_asset = (
            self.limits.maximum_crypto_asset
            if market == Market.CRYPTO
            else self.limits.maximum_equity_asset
        )
        for symbol in list(desired):
            desired[symbol] = min(desired[symbol], per_asset)
            current = current_weights.get(symbol, 0.0)
            if halted:
                desired[symbol] = min(desired[symbol], current)
            # External quantities are exposure, never inventory this runtime
            # is authorized to sell.
            external_weight = sum(
                position.market_value / equity
                for position in account.positions
                if canonical_symbol(position.symbol, position.market) == symbol
                and position.ownership != "managed"
            )
            desired[symbol] = max(desired[symbol], external_weight)

        position_limit = (
            self.limits.maximum_crypto_positions
            if market == Market.CRYPTO
            else self.limits.maximum_positions
        )
        kept_symbols = {
            symbol
            for symbol, _weight in sorted(desired.items(), key=lambda item: (-item[1], item[0]))[
                :position_limit
            ]
        }
        if len(desired) > len(kept_symbols):
            desired = {
                symbol: weight for symbol, weight in desired.items() if symbol in kept_symbols
            }
            halted_reasons.append("position_count_limit")

        sleeve_limit = (
            self.limits.maximum_crypto if market == Market.CRYPTO else self.limits.maximum_equity
        )
        allocation_limit = min(
            maximum_allocation,
            sleeve_limit,
            self.limits.maximum_gross - immutable_gross,
            1.0 - self.limits.minimum_cash - immutable_gross,
        )
        mutable_limit = max(0.0, allocation_limit - immutable_sleeve)
        desired_total = sum(desired.values())
        if desired_total > mutable_limit and desired_total > 0:
            scale = mutable_limit / desired_total
            desired = {symbol: weight * scale for symbol, weight in desired.items()}
            halted_reasons.append("exposure_scaled_to_limit")
        weights = tuple(
            TargetWeight(symbol, weight, "portfolio_risk_overlay")
            for symbol, weight in sorted(desired.items())
            if weight > 1e-12
        )
        adjusted = replace(
            target,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in weights)),
            metadata={
                **target.metadata,
                "risk_overlay_reasons": tuple(halted_reasons),
                "risk_overlay_halted": halted,
            },
        )
        return RiskOverlayDecision(
            adjusted,
            halted,
            tuple(halted_reasons),
            gross_before,
            immutable_gross + sum(weight.weight for weight in weights),
        )


class TargetToOrderPlanner:
    def plan(
        self,
        target: TargetPortfolio,
        account: AccountSnapshot,
        *,
        market: Market,
        prices: dict[str, float],
        rules: dict[str, AssetTradingRules],
        approved_symbols: tuple[str, ...],
        open_order_symbols: set[str] | None = None,
    ) -> tuple[PortfolioOrderRequest, ...]:
        approved = {canonical_symbol(symbol, market) for symbol in approved_symbols}
        open_symbols = {
            canonical_symbol(symbol, market) for symbol in (open_order_symbols or set())
        }
        positions = {
            canonical_symbol(position.symbol, position.market): position
            for position in account.positions
            if position.market == market and not position.is_flat
        }
        target_weights = {
            canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights
        }
        symbols = approved & (set(target_weights) | set(positions))
        decision_id = _decision_id(target)
        orders: list[PortfolioOrderRequest] = []
        for symbol in sorted(symbols):
            if symbol in open_symbols:
                continue
            price = prices.get(symbol) or (
                positions[symbol].current_price if symbol in positions else 0.0
            )
            rule = rules.get(symbol)
            if price <= 0 or rule is None:
                continue
            position = positions.get(symbol)
            current_qty = position.qty if position is not None else 0.0
            target_qty = target_weights.get(symbol, 0.0) * account.equity / price
            delta = target_qty - current_qty
            if delta < 0 and position is not None and position.ownership != "managed":
                continue
            side = PortfolioOrderSide.BUY if delta > 0 else PortfolioOrderSide.SELL
            quantity = _floor_increment(abs(delta), rule.quantity_increment)
            if position is not None and side == PortfolioOrderSide.SELL:
                available = (
                    position.qty_available if position.qty_available is not None else position.qty
                )
                quantity = min(quantity, max(0.0, available))
                quantity = _floor_increment(quantity, rule.quantity_increment)
            if quantity < rule.minimum_order_size:
                continue
            if quantity * price < rule.minimum_order_notional:
                continue
            orders.append(
                PortfolioOrderRequest(
                    client_order_id=_client_order_id(decision_id, symbol, side),
                    decision_id=decision_id,
                    symbol=symbol,
                    market=market,
                    side=side,
                    quantity=quantity,
                    reference_price=price,
                    decision_timestamp=target.decision_timestamp,
                    strategy_name=target.strategy_name,
                    strategy_version=target.strategy_version,
                    risk_reducing=side == PortfolioOrderSide.SELL,
                )
            )
        return tuple(
            sorted(
                orders,
                key=lambda order: (
                    0 if order.side == PortfolioOrderSide.SELL else 1,
                    order.symbol,
                ),
            )
        )


def _floor_increment(value: float, increment: float) -> float:
    raw = Decimal(str(value))
    step = Decimal(str(increment))
    return float((raw / step).to_integral_value(rounding=ROUND_DOWN) * step)


def _decision_id(target: TargetPortfolio) -> str:
    payload = "|".join(
        (
            target.strategy_name,
            target.strategy_version,
            target.decision_timestamp.isoformat(),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _client_order_id(decision_id: str, symbol: str, side: PortfolioOrderSide) -> str:
    digest = hashlib.sha256(f"{decision_id}|{symbol}|{side.value}".encode()).hexdigest()
    return f"pbp-{digest[:32]}"


__all__ = [
    "AssetTradingRules",
    "PortfolioOrderRequest",
    "PortfolioOrderSide",
    "PortfolioRiskLimits",
    "PortfolioRiskOverlay",
    "RiskOverlayDecision",
    "TargetToOrderPlanner",
]
