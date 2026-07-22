from __future__ import annotations

from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, OrderIntent, OrderState, Regime, Signal
from parayabuck.data.buffers import BarStore
from parayabuck.execution.base import ExecutionBroker
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.markets.regime import RegimeDetector
from parayabuck.monitoring.journal import DecisionJournal
from parayabuck.portfolio.allocator import PortfolioAllocator
from parayabuck.risk.manager import RiskManager
from parayabuck.signals.ranker import SignalRanker
from parayabuck.strategies import Strategy, StrategyContext, default_strategies


class TradingEngine:
    """Shared decision engine used by shadow, paper/live and backtest brokers."""

    def __init__(
        self,
        broker: ExecutionBroker,
        config: EngineConfig | None = None,
        strategies: tuple[Strategy, ...] | None = None,
        journal: DecisionJournal | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.broker = broker
        self.strategies = strategies or default_strategies()
        self.journal = journal
        self.bars = BarStore(max(500, self.config.warmup_bars))
        self.regimes = RegimeDetector()
        self.ranker = SignalRanker(self.config.minimum_signal_score)
        self.allocator = PortfolioAllocator()
        self.risk = RiskManager(self.config.risk)
        self.orders = OrderStateMachine()

    def seed(self, bars: list[Bar]) -> None:
        self.bars.seed(bars)

    def process_cycle(self, closed_bars: list[Bar]) -> list[OrderIntent]:
        signals: list[Signal] = []
        account = self.broker.account()
        for bar in sorted(closed_bars, key=lambda item: (item.timestamp, item.symbol)):
            if not self.bars.add(bar):
                continue
            series = self.bars.get(bar.symbol, bar.timeframe)
            assessment = self.regimes.detect(series)
            position = next(
                (item for item in account.positions if item.symbol == bar.symbol),
                None,
            )
            benchmark_regime = self._benchmark_regime(bar.timeframe)
            higher_regime = self._higher_timeframe_regime(bar.symbol)
            context = StrategyContext(assessment, position, higher_regime, benchmark_regime)
            produced = False
            for strategy in self.strategies:
                signal = strategy.evaluate(series, context)
                if signal is not None:
                    signals.append(signal)
                    produced = True
                    if signal.score < self.config.minimum_signal_score and self.journal:
                        self.journal.record_decision(signal, False, "score_below_minimum")
            if not produced and self.journal:
                self.journal.record_scan(bar, assessment, "no_strategy_setup")

        ranked = self.ranker.rank(signals)
        selected = self.allocator.select(ranked, account)
        selected_ids = {signal.signal_id for signal in selected}
        for signal in signals:
            if (
                signal.signal_id not in selected_ids
                and signal.score >= self.config.minimum_signal_score
                and self.journal
            ):
                self.journal.record_decision(signal, False, "lower_ranked_or_existing_position")

        accepted: list[OrderIntent] = []
        for signal in selected:
            decision = self.risk.evaluate(signal, account)
            if self.journal:
                self.journal.record_decision(signal, decision.approved, decision.reason)
            if not decision.approved or decision.order is None:
                continue
            order = decision.order
            accepted.append(order)
            lifecycle = self.orders.create(order)
            lifecycle = self.orders.transition(
                order.client_order_id, OrderState.VALIDATED, "risk_approved"
            )
            if self.journal:
                self.journal.record_order(order, lifecycle.state, "risk_approved")
            lifecycle = self.orders.transition(
                order.client_order_id, OrderState.SUBMITTED, "broker_submit"
            )
            if self.journal:
                self.journal.record_order(order, lifecycle.state, "broker_submit")
            state, fill = self.broker.submit(order)
            lifecycle = self.orders.transition(order.client_order_id, state, "broker_response")
            if self.journal:
                self.journal.record_order(order, lifecycle.state, "broker_response")
                if fill is not None:
                    self.journal.record_fill(fill)
        if self.journal:
            self.journal.record_account(account)
        return accepted

    def _benchmark_regime(self, timeframe: str) -> Regime:
        series = self.bars.get("SPY", timeframe)
        return self.regimes.detect(series).regime if series else Regime.UNKNOWN

    def _higher_timeframe_regime(self, symbol: str) -> Regime:
        series = self.bars.get(symbol, self.config.higher_timeframe)
        return self.regimes.detect(series).regime if series else Regime.UNKNOWN
