from __future__ import annotations

from abc import ABC, abstractmethod

from parayabuck.core.models import (
    Bar,
    ConditionEvaluation,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    Signal,
    StrategyContext,
    StrategyEvaluation,
)
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.markets.symbols import canonical_symbol


class Strategy(ABC):
    """Test seam for signal strategies.

    Concrete signal strategies no longer ship with the package (the runtime
    runs portfolio strategies); tests define subclasses that produce the
    signals the decision pipeline consumes.
    """

    name: str
    version: str = "1.0.0"
    market: Market
    minimum_bars: int = 50

    @abstractmethod
    def evaluate(self, bars: list[Bar], context: StrategyContext) -> Signal | None: ...

    def evaluate_with_trace(self, bars: list[Bar], context: StrategyContext) -> StrategyEvaluation:
        """Compatibility adapter for strategies without native tracing."""
        supported = self.supports(bars)
        signal = self.evaluate(bars, context) if supported else None
        condition = ConditionEvaluation(
            "strategy_support",
            supported,
            {"bars": len(bars), "market": bars[-1].market.value if bars else None},
            f"at least {self.minimum_bars} bars and market={self.market.value}",
            "Strategy has enough compatible history."
            if supported
            else "Unsupported market or history.",
        )
        return StrategyEvaluation(
            self.name,
            signal is not None,
            "Strategy fired." if signal is not None else "Strategy returned no setup.",
            (condition,),
            signal=signal,
        )

    def supports(self, bars: list[Bar]) -> bool:
        if len(bars) < self.minimum_bars or bars[-1].market != self.market:
            return False
        symbol = canonical_symbol(bars[-1].symbol, self.market)
        return all(
            bar.market == self.market
            and (bar.symbol == symbol or canonical_symbol(bar.symbol, bar.market) == symbol)
            for bar in bars
        )


def apply_entry_fill(
    ledger: ExecutionLedger,
    account_id: str,
    order: OrderIntent,
    fill: Fill,
    broker_order_id: str,
) -> None:
    ledger.apply_execution_event(
        ExecutionEvent(
            account_id=account_id,
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol=order.symbol,
            market=order.market,
            state=OrderState.FILLED,
            requested_qty=order.qty,
            cumulative_filled_qty=fill.qty,
            average_fill_price=fill.price,
            occurred_at=fill.timestamp,
            fee=fill.commission,
        )
    )


__all__ = ["Strategy", "apply_entry_fill"]
