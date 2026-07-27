from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.config import EngineConfig
from parayabuck.core.models import AccountSnapshot, Bar, OrderIntent, OrderState, Regime, Signal
from parayabuck.data.buffers import BarMutation, BarRevision, BarStore
from parayabuck.execution.base import ExecutionBroker
from parayabuck.execution.state_machine import OrderStateMachine
from parayabuck.markets.regime import RegimeDetector
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.monitoring import AIDecisionJournal, AsyncDecisionJournal, DecisionJournal
from parayabuck.portfolio.allocator import PortfolioAllocator
from parayabuck.risk.manager import RiskManager
from parayabuck.signals.ranker import SignalRanker, ranking_score
from parayabuck.strategies import Strategy, StrategyContext, default_strategies


@dataclass(frozen=True)
class IngestionResult:
    bars: tuple[Bar, ...]
    mutations: tuple[BarMutation, ...]


@dataclass(frozen=True)
class CycleResult:
    timestamp: datetime
    symbols: tuple[str, ...]
    orders: tuple[OrderIntent, ...]
    account_available: bool = True
    missing_symbols: tuple[str, ...] = ()


class TradingEngine:
    """Shared decision engine used by shadow, paper/live and backtest brokers."""

    def __init__(
        self,
        broker: ExecutionBroker,
        config: EngineConfig | None = None,
        strategies: tuple[Strategy, ...] | None = None,
        journal: DecisionJournal | AsyncDecisionJournal | None = None,
        ai_journal: AIDecisionJournal | None = None,
        enforce_live_safety: bool = False,
        paper_mode: bool = True,
    ) -> None:
        self.config = config or EngineConfig()
        self.broker = broker
        self.strategies = strategies or default_strategies()
        self.journal = journal
        self.ai_journal = ai_journal
        self.enforce_live_safety = enforce_live_safety
        self.paper_mode = paper_mode
        self.bars = BarStore(max(500, self.config.warmup_bars))
        self.regimes = RegimeDetector()
        self.ranker = SignalRanker(self.config.minimum_signal_score)
        self.allocator = PortfolioAllocator(self.config.max_new_positions_per_cycle)
        self.risk = RiskManager(self.config.risk)
        self.orders = OrderStateMachine()
        self.entry_halt_reason: str | None = None
        self._not_ready: set[tuple[str, str]] = set()

    def seed(self, bars: list[Bar]) -> None:
        self.bars.seed(bars)

    def repair_gap(self, bars: list[Bar]) -> None:
        """Merge a validated historical backfill and mark its series ready."""
        self.bars.seed(bars)
        for bar in bars:
            self._not_ready.discard((canonical_symbol(bar.symbol, bar.market), bar.timeframe))

    @property
    def has_data_gaps(self) -> bool:
        return bool(self._not_ready)

    def process_cycle(self, closed_bars: list[Bar]) -> list[OrderIntent]:
        ingestion = self.ingest_closed_bars(closed_bars)
        tradeable = [
            bar
            for bar, mutation in zip(ingestion.bars, ingestion.mutations, strict=True)
            if mutation == BarMutation.INSERTED
        ]
        if not tradeable:
            return []
        try:
            account = self.broker.account()
        except Exception as exc:
            # Ingestion is deliberately independent from account availability.
            # Runtime health/circuit-breaker records the failure; no order can
            # be created without a fresh snapshot.
            from parayabuck.logger import get_logger

            get_logger("parayabuck.engine").warning(
                "Account snapshot unavailable after bar ingestion: %s", exc
            )
            return []
        return self._evaluate_bars(tradeable, account)

    def ingest_closed_bars(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations: list[BarMutation] = []
        for bar in ordered:
            mutation = self.bars.upsert(bar)
            mutations.append(mutation)
            key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe)
            if mutation == BarMutation.GAP_DETECTED:
                self._not_ready.add(key)
            if mutation in {BarMutation.INSERTED, BarMutation.GAP_DETECTED}:
                self._ai_call("observe_bar", bar)
        return IngestionResult(ordered, tuple(mutations))

    def ingest_corrections(self, bars: list[Bar]) -> IngestionResult:
        ordered = tuple(sorted(bars, key=lambda item: (item.timestamp, item.symbol)))
        mutations = tuple(self.bars.upsert(bar, BarRevision.CORRECTION) for bar in ordered)
        return IngestionResult(ordered, mutations)

    def evaluate_timestamp(
        self,
        timestamp: datetime,
        symbols: list[str],
        account: AccountSnapshot,
        *,
        missing_symbols: tuple[str, ...] = (),
    ) -> CycleResult:
        wanted = set(symbols)
        bars = [
            series[-1]
            for symbol in wanted
            if (series := self.bars.get(symbol, self.config.timeframe))
            and series[-1].timestamp == timestamp
            and (canonical_symbol(symbol), self.config.timeframe) not in self._not_ready
        ]
        orders = self._evaluate_bars(bars, account)
        return CycleResult(timestamp, tuple(sorted(wanted)), tuple(orders), True, missing_symbols)

    def _evaluate_bars(self, closed_bars: list[Bar], account: AccountSnapshot) -> list[OrderIntent]:
        signals: list[Signal] = []
        metadata: dict[str, dict[str, Any]] = {}
        trails: dict[str, list[dict[str, Any]]] = {}
        for bar in sorted(closed_bars, key=lambda item: (item.timestamp, item.symbol)):
            if (canonical_symbol(bar.symbol, bar.market), bar.timeframe) in self._not_ready:
                continue
            series = self.bars.get(bar.symbol, bar.timeframe)
            assessment = self.regimes.detect(series)
            position = next(
                (
                    item
                    for item in account.positions
                    if canonical_symbol(item.symbol, item.market)
                    == canonical_symbol(bar.symbol, bar.market)
                ),
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
                    quality = ranking_score(signal)
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
        held = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if not position.is_flat
        }
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
                    if canonical_symbol(signal.symbol, signal.market) in held
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
            gate_reason = self._execution_gate_reason(signal)
            if gate_reason is not None:
                trails[signal.signal_id].append(
                    {"gate": "execution_policy", "status": "failed", "reason": gate_reason}
                )
                if self.journal:
                    self.journal.record_decision(signal, False, gate_reason)
                continue
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
            filled_qty = fill.qty if fill is not None else 0.0
            if lifecycle.state == OrderState.UNKNOWN:
                self.entry_halt_reason = "ambiguous_order_state"
                self.risk.reserve(order)
            elif lifecycle.state == OrderState.REJECTED and filled_qty > 0:
                self.entry_halt_reason = "protection_failure"
            if lifecycle.state in {
                OrderState.ACCEPTED,
                OrderState.PARTIALLY_FILLED,
                OrderState.UNKNOWN,
            }:
                self.risk.reserve(order, order.qty - filled_qty)
            else:
                self.risk.release(order.client_order_id)
            if fill is not None and fill.qty > 0:
                actual_risk = abs(fill.price - order.stop_price) * fill.qty + fill.commission
                if actual_risk > decision.risk_amount * 1.05:
                    self.entry_halt_reason = "post_fill_risk_violation"
                    trails[signal.signal_id].append(
                        {
                            "gate": "post_fill_risk",
                            "status": "failed",
                            "reason": (
                                f"actual_risk={actual_risk:.4f} exceeds "
                                f"approved={decision.risk_amount:.4f}"
                            ),
                        }
                    )
            executions[signal.signal_id].update(
                {
                    "state": lifecycle.state,
                    "fill": fill,
                    "traded": fill is not None and fill.qty > 0,
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

    def _execution_gate_reason(self, signal: Signal) -> str | None:
        if not self.config.trading_enabled:
            return "trading_disabled"
        if not self.paper_mode and not self.config.live_trading_enabled:
            return "live_trading_disabled"
        if self.entry_halt_reason is not None:
            return self.entry_halt_reason
        if signal.market.value == "equity" and not self.config.equity_trading_enabled:
            return "equity_trading_disabled"
        if signal.market.value == "crypto" and not self.config.crypto_trading_enabled:
            return "crypto_trading_disabled"
        if signal.direction.value == "short" and not self.config.allow_short:
            return "short_trading_disabled"
        if self.enforce_live_safety and self.config.reject_stale_signals:
            duration = {
                "1m": timedelta(minutes=1),
                "5m": timedelta(minutes=5),
                "15m": timedelta(minutes=15),
                "30m": timedelta(minutes=30),
                "1h": timedelta(hours=1),
                "1d": timedelta(days=1),
            }[self.config.timeframe]
            age = datetime.now(UTC) - (signal.timestamp + duration)
            if age.total_seconds() > self.config.maximum_signal_age_seconds:
                return "stale_signal"
        return None

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
