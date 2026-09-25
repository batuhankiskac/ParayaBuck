"""Research-only composite equity engine for ETF diversifier Stage B.

The shared-ledger harness supports one engine per market, so the V3 sleeve and
the ETF sleeve share one equity engine. ``CompositeEquity`` returns V3's own
target and carries the ETF sleeve's intent in metadata; ``CompositeOverlay``
sends the V3 part through the canonical risk overlay alone (own allocation,
shipped slot cap, ETF slots excluded) and then appends the ETF part. Frozen by
``PREREGISTRATION_STAGE_B.md``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.portfolio import RiskOverlayDecision
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from research.physical_availability_clock_v2 import shared_replay as shared

ETF_SLEEVE_SYMBOLS = frozenset({"SPY", "TLT", "IEF", "GLD", "DBC", "BIL"})
ETF_MAXIMUM_NAME_WEIGHT = 0.10


def is_etf_key(key: tuple[Market, str]) -> bool:
    return key[0] == Market.EQUITY and key[1] in ETF_SLEEVE_SYMBOLS


class CompositeEquity(PortfolioStrategy):
    """V3 weekly target plus a monthly ETF sleeve intent (metadata only)."""

    name = "etf_diversifier_v1_composite"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "weekly"

    def __init__(
        self,
        v3: PortfolioStrategy,
        etf: PortfolioStrategy | None,
        *,
        v3_allocation: float,
        etf_allocation: float,
    ) -> None:
        self.v3 = v3
        self.etf = etf
        self.v3_allocation = v3_allocation
        self.etf_allocation = etf_allocation if etf is not None else 0.0
        self._etf_month: tuple[int, int] | None = None
        self._etf_raw: dict[str, float] = {}
        self.etf_decision_count = 0
        super().__init__({})
        # The engine derives its weekly policy from these parameters.
        self.parameters = dict(v3.parameters)

    def _validate_parameters(self) -> None:
        return None

    @property
    def minimum_history_bars(self) -> int:
        return self.v3.minimum_history_bars

    def supports(self, context: PortfolioContext) -> bool:
        return self.v3.supports(context)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        v3_target = self.v3.decide(context)
        conflicts = sorted(weight.symbol for weight in v3_target.weights if weight.symbol in ETF_SLEEVE_SYMBOLS)
        if conflicts and self.etf is not None:
            raise RuntimeError(f"V3 targeted ETF-sleeve symbols {conflicts}; ownership would be ambiguous")
        mode = "disabled"
        if self.etf is not None:
            session = context.decision_timestamp.astimezone(NEW_YORK).date()
            month = (session.year, session.month)
            if month != self._etf_month:
                etf_target = self.etf.decide(context)
                self._etf_raw = {weight.symbol: weight.weight for weight in etf_target.weights}
                self._etf_month = month
                self.etf_decision_count += 1
                mode = "decide"
            else:
                mode = "hold"
        return replace(
            v3_target,
            metadata={**v3_target.metadata, "composite": {"etf_mode": mode, "etf_raw": dict(self._etf_raw)}},
        )


class CompositeOverlay(shared._SharedOverlay):
    """V3 part through the canonical overlay alone, then the ETF part appended."""

    def __init__(self, base: shared._SharedOverlay, strategy: CompositeEquity) -> None:
        super().__init__(base._overlay, base._account)
        self._strategy = strategy
        self.halt_counts = {"daily_loss_limit_decisions": 0, "etf_increase_blocked": 0}

    def _reservations(self, market: Market, reserved_position_keys: tuple) -> tuple:
        pending = {
            (current, order.symbol) for current in shared.ORDER_MARKETS for order in self._account.pending[current]
        }
        deferred = {
            (current, order.symbol)
            for current in shared.ORDER_MARKETS
            if current != market
            for order in self._account.deferred_buys[current]
        }
        keys = self._account.same_cycle_buy_reservations | set(reserved_position_keys) | pending | deferred
        return tuple(sorted((key for key in keys if not is_etf_key(key)), key=lambda item: (item[0].value, item[1])))

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
        reserved_position_keys: tuple = (),
        open_order_symbols: tuple[str, ...] = (),
    ) -> RiskOverlayDecision:
        del account, maximum_allocation
        snapshot = self._account.engines[market]._account_snapshot()
        v3_target = replace(target, weights=tuple(w for w in target.weights if w.symbol not in ETF_SLEEVE_SYMBOLS))
        decision = self._overlay.apply(
            v3_target,
            snapshot,
            market=market,
            approved_symbols=approved_symbols,
            maximum_allocation=self._strategy.v3_allocation,
            mode=mode,
            enforce_drawdown_halt=enforce_drawdown_halt,
            reserved_position_keys=self._reservations(market, reserved_position_keys),
            open_order_symbols=open_order_symbols,
        )
        if "daily_loss_limit" in decision.reasons:
            self.halt_counts["daily_loss_limit_decisions"] += 1
        composite: dict[str, Any] = target.metadata.get("composite", {"etf_mode": "disabled", "etf_raw": {}})
        equity = snapshot.equity
        current = {
            position.symbol: position.market_value / equity
            for position in snapshot.positions
            if position.market == Market.EQUITY and position.symbol in ETF_SLEEVE_SYMBOLS and not position.is_flat
        }
        if composite["etf_mode"] == "decide":
            etf = {
                symbol: min(ETF_MAXIMUM_NAME_WEIGHT, self._strategy.etf_allocation * raw)
                for symbol, raw in composite["etf_raw"].items()
            }
        elif composite["etf_mode"] == "hold":
            etf = dict(current)
        else:
            etf = {}
        if "daily_loss_limit" in decision.reasons:
            capped = {symbol: min(weight, current.get(symbol, 0.0)) for symbol, weight in etf.items()}
            if capped != etf:
                self.halt_counts["etf_increase_blocked"] += 1
            etf = capped
        limits = self._overlay.limits
        other_market_gross = sum(
            position.market_value / equity
            for position in snapshot.positions
            if position.market != market and not position.is_flat
        )
        v3_total = sum(weight.weight for weight in decision.target.weights)
        room = max(0.0, min(limits.maximum_gross, 1.0 - limits.minimum_cash) - other_market_gross - v3_total)
        etf_total = sum(etf.values())
        if etf_total > room > 0 or (etf_total > 0 and room <= 0):
            scale = room / etf_total if etf_total > 0 else 0.0
            etf = {symbol: weight * scale for symbol, weight in etf.items()}
        etf_weights = tuple(
            TargetWeight(symbol, weight, "etf_sleeve") for symbol, weight in sorted(etf.items()) if weight > 1e-12
        )
        merged = tuple(sorted((*decision.target.weights, *etf_weights), key=lambda weight: weight.symbol))
        adjusted = replace(
            decision.target,
            weights=merged,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in merged)),
            metadata={**decision.target.metadata, "etf_sleeve_weights": dict(etf)},
        )
        return RiskOverlayDecision(
            adjusted,
            decision.halted,
            decision.reasons,
            decision.gross_before,
            decision.gross_after + sum(weight.weight for weight in etf_weights),
        )


class EtfExcludedOverlay(shared._SharedOverlay):
    """Crypto overlay that does not count ETF-sleeve holdings as account slots."""

    def apply(self, target: TargetPortfolio, account: AccountSnapshot, **kwargs: Any) -> RiskOverlayDecision:
        etf_slots = sum(
            1
            for (market, symbol), position in self._account.positions.items()
            if is_etf_key((market, symbol)) and position.qty > 0
        )
        etf_slots += len(
            {order.symbol for order in self._account.pending[Market.EQUITY] if order.symbol in ETF_SLEEVE_SYMBOLS}
            - {symbol for market, symbol in self._account.positions if market == Market.EQUITY}
        )
        base_limits = self._overlay.limits
        self._overlay.limits = replace(base_limits, maximum_positions=base_limits.maximum_positions + etf_slots)
        try:
            return super().apply(target, account, **kwargs)
        finally:
            self._overlay.limits = base_limits


def admit_excluding_etf(original):  # type: ignore[no-untyped-def]
    """Slot admission where ETF-sleeve orders and holdings never consume slots."""

    def admit(orders, account, maximum_positions, reserved_position_keys):  # type: ignore[no-untyped-def]
        other = tuple(order for order in orders if not is_etf_key((order.market, order.symbol)))
        filtered = replace(
            account,
            positions=tuple(
                position for position in account.positions if not is_etf_key((position.market, position.symbol))
            ),
        )
        reserved = tuple(key for key in reserved_position_keys if not is_etf_key(key))
        admitted = set(id(order) for order in original(other, filtered, maximum_positions, reserved))
        return tuple(order for order in orders if id(order) in admitted or is_etf_key((order.market, order.symbol)))

    return admit
