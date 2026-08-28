"""Target-weight planning and account-level portfolio risk enforcement."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from parayabuck.config import RiskLimits
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.utils import is_crypto


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
class PortfolioExecutionPolicy:
    """Shared order-existence policy consumed by backtest, paper and live.

    The exact same (target, current) pair must result in the same decision
    about whether an order SHOULD EXIST in BACKTEST, PAPER and LIVE. Fill
    mechanics may differ; order-existence policy must not. ``rebalance_band``
    skips sub-band drift so backtest and production do not diverge on small
    turnover, and ``minimum_order_notional`` is the floor for a produced
    order (production also enforces the broker-authoritative minimum, which
    is at least as large).
    """

    rebalance_band_pct: float = 0.005
    minimum_order_notional: float = 10.0

    def __post_init__(self) -> None:
        if not 0 <= self.rebalance_band_pct < 1:
            raise ValueError("rebalance_band_pct must be in [0, 1)")
        if not math.isfinite(self.minimum_order_notional) or self.minimum_order_notional < 0:
            raise ValueError("minimum_order_notional must be finite and non-negative")


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


def portfolio_risk_limits_from_config(risk: RiskLimits) -> PortfolioRiskLimits:
    """Single semantic mapping shared by backtest, paper, and live."""
    return PortfolioRiskLimits(
        daily_loss_limit=risk.daily_loss_limit_pct,
        drawdown_halt=risk.max_drawdown_pct,
        maximum_gross=risk.max_gross_exposure_pct,
        maximum_equity=risk.max_gross_exposure_pct,
        maximum_crypto=risk.max_crypto_exposure_pct,
        maximum_equity_asset=risk.max_position_pct,
        maximum_crypto_asset=risk.max_crypto_position_pct,
        minimum_cash=risk.cash_buffer_pct,
        maximum_positions=risk.max_positions,
        maximum_crypto_positions=risk.max_crypto_positions,
    )


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
        enforce_drawdown_halt: bool = True,
    ) -> RiskOverlayDecision:
        """Apply the portfolio risk overlay to a strategy target.

        ``enforce_drawdown_halt``: when False (research backtests), a
        drawdown breach is a SHADOW event — the ``portfolio_drawdown_halt``
        reason is still recorded on the decision (where production would have
        halted) but the target is not capped, so the alpha curve keeps
        trading. Production (paper/live) always enforces the halt until an
        operator starts a new risk epoch.
        """
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
        if (
            not enforce_drawdown_halt
            and "portfolio_drawdown_halt" in halted_reasons
            and "daily_loss_limit" not in halted_reasons
        ):
            # Research backtest: record the drawdown breach as a shadow event
            # (production would have halted here) but keep the curve trading.
            # Daily-loss breaches are always enforced (session-scoped, they
            # self-recover; the drawdown peak does not).
            halted = False

        # Only the external portion of a mixed position is immutable: the
        # managed quantity remains mutable inventory the bot may rebalance.
        # Unapproved symbols are fully immutable (the bot holds no claim).
        def immutable_value(position: PositionSnapshot) -> float:
            if canonical_symbol(position.symbol, position.market) not in approved:
                return position.market_value
            return abs(position.external_qty) * position.current_price

        immutable_gross = sum(
            immutable_value(position) / equity
            for position in account.positions
            if not position.is_flat
            and (canonical_symbol(position.symbol, position.market) not in approved or position.external_qty > 1e-12)
        )
        immutable_sleeve = sum(
            immutable_value(position) / equity
            for position in account.positions
            if not position.is_flat
            and position.market == market
            and (canonical_symbol(position.symbol, position.market) not in approved or position.external_qty > 1e-12)
        )
        desired = {canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights}
        # Strategy alpha priority per canonical symbol (0 = unranked). When
        # the strategy declares ranks, position-count enforcement below
        # retains best-ranked names; otherwise the legacy target-weight
        # order applies unchanged.
        rank_by_symbol = {canonical_symbol(weight.symbol, market): weight.rank for weight in target.weights}
        if mode not in {"backtest", "paper", "live"}:
            raise ValueError(f"Unsupported portfolio execution mode: {mode}")
        per_asset = self.limits.maximum_crypto_asset if market == Market.CRYPTO else self.limits.maximum_equity_asset
        # Immutable external inventory per symbol: never sellable, never
        # scaled. The strategy's total desired weight for a mixed symbol is
        # split internally into external_floor + managed_desired; only the
        # managed part is mutable by the bot.
        external_weight_by_symbol: dict[str, float] = {}
        for position in account.positions:
            if position.is_flat or position.external_qty <= 1e-12:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            external_weight_by_symbol[symbol] = (
                external_weight_by_symbol.get(symbol, 0.0)
                + abs(position.external_qty) * position.current_price / equity
            )
        for symbol in list(desired):
            desired[symbol] = min(desired[symbol], per_asset)
            current = current_weights.get(symbol, 0.0)
            external_weight = external_weight_by_symbol.get(symbol, 0.0)
            managed_weight = sum(
                abs(position.managed_qty) * position.current_price / equity
                for position in account.positions
                if canonical_symbol(position.symbol, position.market) == symbol
                and position.managed_qty > 1e-12
                and not position.is_flat
            )
            if managed_weight <= 1e-12 and external_weight > 0:
                # Purely external exposure: the bot neither owns nor may sell
                # this symbol, so it is not tradable inventory at all. Keep it
                # as immutable gross and drop it from the tradable target set;
                # otherwise a strategy BUY could grow a manually opened
                # position and consume a portfolio slot.
                desired.pop(symbol)
                continue
            if external_weight > per_asset and external_weight > desired[symbol]:
                # A total-per-symbol limit exceeded solely by immutable manual
                # inventory: never sell the manual inventory, block new managed
                # additions and surface the violation.
                halted_reasons.append(f"external_exceeds_asset_limit:{symbol}")
            if halted:
                desired[symbol] = min(desired[symbol], current)
            # External quantities are exposure, never inventory this runtime
            # is authorized to sell; for mixed positions keep the floor.
            desired[symbol] = max(desired[symbol], external_weight)

        position_limit = (
            self.limits.maximum_crypto_positions if market == Market.CRYPTO else self.limits.maximum_positions
        )
        # Position-count policy: the LIMIT is risk enforcement and stays
        # intact, but WHO survives must follow the strategy's declared alpha
        # rank when one exists. Sorting by target weight instead would invent
        # a second ranking system: inverse-vol sizing (and the per-asset cap,
        # which pins several names at exactly the cap) is not alpha, so
        # weight order can evict the strategy's best ideas on symbol-order
        # tie-breaks. Unranked targets keep the legacy weight ordering.
        if any(rank > 0 for rank in rank_by_symbol.values()):
            priority = sorted(
                desired,
                key=lambda symbol: (
                    rank_by_symbol.get(symbol, 0) or math.inf,
                    -desired[symbol],
                    symbol,
                ),
            )
        else:
            priority = sorted(desired, key=lambda symbol: (-desired[symbol], symbol))
        kept_symbols = set(priority[:position_limit])
        if len(desired) > len(kept_symbols):
            desired = {symbol: weight for symbol, weight in desired.items() if symbol in kept_symbols}
            halted_reasons.append("position_count_limit")

        sleeve_limit = self.limits.maximum_crypto if market == Market.CRYPTO else self.limits.maximum_equity
        # Internally separate the three exposure layers: immutable external
        # inventory (never sellable), managed desired exposure (mutable by the
        # planner) and the total desired account exposure (external + managed
        # + unapproved). maximum_allocation caps the MANAGED sleeve only;
        # maximum_gross and the cash buffer apply to the TOTAL account
        # exposure, with the external layer counted exactly once.
        external_in_desired = sum(
            abs(position.external_qty) * position.current_price / equity
            for position in account.positions
            if not position.is_flat
            and position.external_qty > 1e-12
            and canonical_symbol(position.symbol, position.market) in desired
        )
        managed_desired = max(0.0, sum(desired.values()) - external_in_desired)
        managed_limit = max(
            0.0,
            min(
                maximum_allocation,
                sleeve_limit - immutable_sleeve,
                self.limits.maximum_gross - immutable_gross,
                1.0 - self.limits.minimum_cash - immutable_gross,
            ),
        )
        if managed_desired > managed_limit and managed_desired > 0:
            scale = managed_limit / managed_desired
            # Scale ONLY the managed desired exposure of each symbol. The
            # external floor is immutable inventory and must never shrink as
            # part of the bot's allocation. final = external_floor +
            # managed_desired * scale, so the managed sleeve lands exactly on
            # the limit while external exposure is preserved.
            desired = {
                symbol: external_weight_by_symbol.get(symbol, 0.0)
                + max(0.0, weight - external_weight_by_symbol.get(symbol, 0.0)) * scale
                for symbol, weight in desired.items()
            }
            halted_reasons.append("exposure_scaled_to_limit")
        weights = tuple(
            TargetWeight(symbol, weight, "portfolio_risk_overlay")
            for symbol, weight in sorted(desired.items())
            if weight > 1e-12
        )
        # gross_after is the actual expected account gross: immutable external
        # exposure plus the managed weights. The external floor carried inside
        # the target weights must not be counted a second time.
        weight_symbols = {weight.symbol for weight in weights}
        external_in_weights = sum(
            abs(position.external_qty) * position.current_price / equity
            for position in account.positions
            if not position.is_flat
            and position.external_qty > 1e-12
            and canonical_symbol(position.symbol, position.market) in weight_symbols
        )
        adjusted = replace(
            target,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in weights)),
            metadata={
                **target.metadata,
                "risk_overlay_reasons": tuple(halted_reasons),
                "risk_overlay_halted": halted,
                "risk_overlay_managed_exposure": sum(weight.weight for weight in weights) - external_in_weights,
            },
        )
        gross_after = immutable_gross + sum(weight.weight for weight in weights) - external_in_weights
        return RiskOverlayDecision(
            adjusted,
            halted,
            tuple(halted_reasons),
            gross_before,
            gross_after,
        )


class TargetToOrderPlanner:
    def __init__(self, policy: PortfolioExecutionPolicy | None = None) -> None:
        self.policy = policy or PortfolioExecutionPolicy()

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
        decision_id: str | None = None,
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Build rebalance orders from a target.

        ``decision_id`` is the account-local, configuration-scoped decision
        identity from the runtime repository; when provided it seeds the
        client order ids so that two configurations sharing a strategy name,
        version and timestamp never produce the same broker idempotency key.
        """
        approved = {canonical_symbol(symbol, market) for symbol in approved_symbols}
        # Open orders arrive broker-wide (``all_open_order_symbols``); only
        # this market's working orders must suppress order placement here. A
        # cross-market symbol is never canonicalized under this market: an
        # equity order like IWM must not raise ``Cannot infer crypto
        # base/quote`` in the crypto planner (and vice versa).
        open_symbols = {
            canonical_symbol(symbol, market)
            for symbol in (open_order_symbols or set())
            if is_crypto(symbol) == (market == Market.CRYPTO)
        }
        positions = {
            canonical_symbol(position.symbol, position.market): position
            for position in account.positions
            if position.market == market and not position.is_flat
        }
        target_weights = {canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights}
        symbols = approved & (set(target_weights) | set(positions))
        decision_id = decision_id or _decision_id(target)
        orders: list[PortfolioOrderRequest] = []
        for symbol in sorted(symbols):
            if symbol in open_symbols:
                continue
            price = prices.get(symbol) or (positions[symbol].current_price if symbol in positions else 0.0)
            rule = rules.get(symbol)
            if price <= 0 or rule is None:
                continue
            position = positions.get(symbol)
            current_qty = position.qty if position is not None else 0.0
            managed_qty = position.managed_qty if position is not None else 0.0
            target_qty = target_weights.get(symbol, 0.0) * account.equity / price
            current_weight = current_qty * price / account.equity if account.equity > 0 else 0.0
            target_weight = target_weights.get(symbol, 0.0)
            # Shared execution policy: sub-band drift produces no order in
            # backtest, paper OR live. This keeps turnover/cost parity between
            # the historical simulator and the live broker path.
            if abs(target_weight - current_weight) < self.policy.rebalance_band_pct:
                continue
            delta = target_qty - current_qty
            if delta < 0:
                # The bot may only sell its own (managed) units; manual
                # inventory in a mixed position is never a sellable source.
                delta = -min(abs(delta), managed_qty)
                if abs(delta) <= 1e-12:
                    continue
            if delta > 0 and position is not None and managed_qty <= 1e-12:
                # Never grow a manually opened position; the risk overlay
                # already excludes purely external symbols, this is defense
                # in depth against an external-position BUY.
                continue
            side = PortfolioOrderSide.BUY if delta > 0 else PortfolioOrderSide.SELL
            quantity = floor_increment(abs(delta), rule.quantity_increment)
            if position is not None and side == PortfolioOrderSide.SELL:
                available = position.qty_available if position.qty_available is not None else position.qty
                quantity = min(quantity, max(0.0, available))
                quantity = floor_increment(quantity, rule.quantity_increment)
            if quantity < rule.minimum_order_size:
                continue
            if quantity * price < max(self.policy.minimum_order_notional, rule.minimum_order_notional):
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


def floor_increment(value: float, increment: float) -> float:
    """Floor ``value`` to a whole multiple of the broker ``increment``.

    Single source of truth for order-quantity rounding: used by the planner,
    by AlpacaBroker submission/fill-verification paths and by the portfolio
    repository so all three can never disagree on a floored quantity.
    """
    if not math.isfinite(value) or not math.isfinite(increment) or increment <= 0:
        raise ValueError(
            f"floor_increment requires finite positive increment, got value={value!r} increment={increment!r}"
        )
    raw = Decimal(str(value))
    step = Decimal(str(increment))
    return float((raw / step).to_integral_value(rounding=ROUND_DOWN) * step)


def floor_increment_decimal(value: Decimal, increment: Decimal) -> Decimal:
    """``floor_increment`` for callers already holding exact ``Decimal`` values.

    Same operation sequence as :func:`floor_increment` — divide, round down to
    a whole multiple, multiply back — without a float round-trip that could
    perturb broker-exact quantities.
    """
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


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
    """Deterministic broker idempotency key for one decision leg.

    Format ``parayabuck-{decision_id}-{symbol}-{side}`` is stable across
    restarts and unique per decision/symbol/side. The decision_id itself is
    already scoped to account+digest+timestamp+market, so the full key is
    globally unique without hashing. Persisted via ``portfolio_orders``;
    duplicate decisions map to the same logical order and ``INSERT OR IGNORE``
    prevents a second broker submission. Partial fills keep the same key and
    only the residual is re-planned; broker timeouts leave the row at
    ``reconciliation_required`` and require a ``reconcile()`` before retry.
    """
    canonical = canonical_symbol(symbol, Market.CRYPTO if "/" in symbol else Market.EQUITY)
    # Use the canonical symbol in the key so ``BTC/USD`` vs ``BTCUSD`` do not
    # create duplicate broker orders for the same logical leg.
    return f"parayabuck-{decision_id}-{canonical}-{side.value}"


__all__ = [
    "AssetTradingRules",
    "PortfolioExecutionPolicy",
    "PortfolioOrderRequest",
    "PortfolioOrderSide",
    "PortfolioRiskLimits",
    "PortfolioRiskOverlay",
    "RiskOverlayDecision",
    "TargetToOrderPlanner",
    "floor_increment",
    "floor_increment_decimal",
]
