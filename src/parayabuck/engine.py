from __future__ import annotations

from typing import Any

from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, OrderIntent, OrderState, Regime, Signal
from parayabuck.data.buffers import BarStore
from parayabuck.execution.base import ExecutionBroker
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.markets.regime import RegimeDetector
from parayabuck.monitoring import AIDecisionJournal, DecisionJournal
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
        ai_journal: AIDecisionJournal | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.broker = broker
        self.strategies = strategies or default_strategies()
        self.journal = journal
        self.ai_journal = ai_journal
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
        metadata: dict[str, dict[str, Any]] = {}
        trails: dict[str, list[dict[str, Any]]] = {}
        account = self.broker.account()
        for bar in sorted(closed_bars, key=lambda item: (item.timestamp, item.symbol)):
            if not self.bars.add(bar):
                continue
            self._ai_call("observe_bar", bar)
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
            evaluations = []
            for strategy in self.strategies:
                evaluation = strategy.evaluate_with_trace(series, context)
                evaluations.append(evaluation)
                signal = evaluation.signal
                if signal is not None:
                    signals.append(signal)
                    produced = True
                    metadata[signal.signal_id] = {
                        "bar": bar,
                        "assessment": assessment,
                        "higher_regime": higher_regime,
                        "benchmark_regime": benchmark_regime,
                        "evaluation": evaluation,
                    }
                    trails[signal.signal_id] = [
                        {
                            "gate": "strategy",
                            "status": "passed",
                            "reason": evaluation.summary,
                        }
                    ]
                    if signal.score < self.config.minimum_signal_score and self.journal:
                        self.journal.record_decision(signal, False, "score_below_minimum")
            self._ai_call(
                "record_scan",
                bar,
                assessment,
                position.symbol if position is not None else "none",
                tuple(evaluations),
            )
            if not produced and self.journal:
                self.journal.record_scan(bar, assessment, "no_strategy_setup")

        ranked = self.ranker.rank(signals)
        rank_by_id = {signal.signal_id: index for index, signal in enumerate(ranked, 1)}
        for signal in signals:
            if signal.score < self.config.minimum_signal_score:
                trails[signal.signal_id].append(
                    {
                        "gate": "minimum_score",
                        "status": "failed",
                        "reason": (
                            f"score {signal.score:.2f} is below "
                            f"{self.config.minimum_signal_score:.2f}"
                        ),
                    }
                )
            else:
                trails[signal.signal_id].append(
                    {
                        "gate": "minimum_score",
                        "status": "passed",
                        "reason": (
                            f"score {signal.score:.2f} meets {self.config.minimum_signal_score:.2f}"
                        ),
                    }
                )
                if signal.signal_id in rank_by_id:
                    quality = signal.score * signal.confidence * min(signal.risk_reward, 3.0)
                    trails[signal.signal_id].append(
                        {
                            "gate": "ranking",
                            "status": "passed",
                            "reason": (
                                f"rank {rank_by_id[signal.signal_id]} of {len(ranked)}; "
                                f"quality={quality:.3f}"
                            ),
                        }
                    )
                else:
                    trails[signal.signal_id].append(
                        {
                            "gate": "ranking",
                            "status": "failed",
                            "reason": "a higher-quality signal for the same symbol won",
                        }
                    )
        selected = self.allocator.select(ranked, account)
        selected_ids = {signal.signal_id for signal in selected}
        held = {position.symbol for position in account.positions if not position.is_flat}
        for signal in signals:
            if signal.signal_id in selected_ids:
                trails[signal.signal_id].append(
                    {
                        "gate": "allocation",
                        "status": "passed",
                        "reason": "portfolio capacity was available and no position was held",
                    }
                )
            elif signal.signal_id in rank_by_id:
                reason = (
                    "an existing position is already held"
                    if signal.symbol in held
                    else "cycle capacity was consumed by a higher-ranked candidate"
                )
                trails[signal.signal_id].append(
                    {"gate": "allocation", "status": "failed", "reason": reason}
                )
            if (
                signal.signal_id not in selected_ids
                and signal.score >= self.config.minimum_signal_score
                and self.journal
            ):
                self.journal.record_decision(signal, False, "lower_ranked_or_existing_position")

        accepted: list[OrderIntent] = []
        executions: dict[str, dict[str, Any]] = {}
        for signal in selected:
            decision = self.risk.evaluate(signal, account)
            trails[signal.signal_id].append(
                {
                    "gate": "risk",
                    "status": "passed" if decision.approved else "failed",
                    "reason": decision.reason,
                }
            )
            executions[signal.signal_id] = {
                "order": decision.order,
                "state": None,
                "fill": None,
                "diagnostics": decision.diagnostics,
                "traded": False,
            }
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
            executions[signal.signal_id].update(
                {
                    "state": lifecycle.state,
                    "fill": fill,
                    "traded": lifecycle.state
                    in {
                        OrderState.ACCEPTED,
                        OrderState.PARTIALLY_FILLED,
                        OrderState.FILLED,
                    },
                }
            )
            trails[signal.signal_id].append(
                {
                    "gate": "broker",
                    "status": ("passed" if executions[signal.signal_id]["traded"] else "failed"),
                    "reason": f"broker_response={lifecycle.state.value}",
                }
            )
            if self.journal:
                self.journal.record_order(order, lifecycle.state, "broker_response")
                if fill is not None:
                    self.journal.record_fill(fill)
        for signal in signals:
            meta = metadata[signal.signal_id]
            execution = executions.get(
                signal.signal_id,
                {
                    "order": None,
                    "state": None,
                    "fill": None,
                    "diagnostics": {},
                    "traded": False,
                },
            )
            self._ai_call(
                "record_signal",
                meta["bar"],
                signal,
                meta["evaluation"],
                meta["assessment"],
                meta["higher_regime"].value,
                meta["benchmark_regime"].value,
                trails[signal.signal_id],
                execution["traded"],
                execution["order"],
                execution["state"],
                execution["fill"],
                execution["diagnostics"],
            )
        if self.journal:
            self.journal.record_account(account)
        if closed_bars:
            self._ai_call(
                "record_account",
                max(bar.timestamp for bar in closed_bars),
                {
                    "cash": account.cash,
                    "equity": account.equity,
                    "peak_equity": account.peak_equity,
                    "daily_pnl": account.daily_pnl,
                    "realized_pnl": account.realized_pnl,
                    "unrealized_pnl": account.unrealized_pnl,
                    "drawdown_pct": account.drawdown_pct,
                    "positions": [
                        {
                            "symbol": position.symbol,
                            "qty": position.qty,
                            "avg_price": position.avg_price,
                            "current_price": position.current_price,
                            "unrealized_pnl": position.unrealized_pnl,
                        }
                        for position in account.positions
                    ],
                },
            )
        return accepted

    def _ai_call(self, method: str, *args: Any) -> Any:
        if self.ai_journal is None:
            return None
        try:
            return getattr(self.ai_journal, method)(*args)
        except Exception:
            from parayabuck.logger import get_logger

            get_logger("parayabuck.engine").exception(
                "AI decision journal failed during %s; trading continues", method
            )
            return None

    def _benchmark_regime(self, timeframe: str) -> Regime:
        series = self.bars.get("SPY", timeframe)
        return self.regimes.detect(series).regime if series else Regime.UNKNOWN

    def _higher_timeframe_regime(self, symbol: str) -> Regime:
        series = self.bars.get(symbol, self.config.higher_timeframe)
        return self.regimes.detect(series).regime if series else Regime.UNKNOWN
