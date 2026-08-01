from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from uuid import uuid4

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market
from parayabuck.engine import TradingEngine
from parayabuck.execution.coordinator import ExecutionCoordinator
from parayabuck.logger import get_logger
from parayabuck.markets.timeframes import timeframe_duration
from parayabuck.monitoring import AIDecisionJournal
from parayabuck.strategies.base import Strategy

log = get_logger("parayabuck.backtest.engine")


@dataclass(frozen=True)
class BacktestResult:
    starting_equity: float
    ending_equity: float
    total_return: float
    benchmark_return: float
    excess_return: float
    max_drawdown: float
    total_trades: int
    win_rate: float
    profit_factor: float | None
    profit_factor_is_infinite: bool
    expectancy: float
    sharpe: float
    sortino: float
    equity_curve: tuple[float, ...]
    realized_pnls: tuple[float, ...]
    open_positions: int
    unrealized_pnl: float
    open_risk: float
    liquidated_at_end: bool


class BacktestEngine:
    def __init__(
        self,
        config: EngineConfig | None = None,
        starting_equity: float = 10_000.0,
        slippage_bps: float = 2.0,
        commission: float = 0.0,
        spread_bps: float = 0.0,
        crypto_taker_fee_bps: float = 0.0,
        strategies: tuple[Strategy, ...] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.broker = BacktestBroker(
            starting_equity,
            slippage_bps,
            commission,
            spread_bps=spread_bps,
            allow_short=self.config.allow_short,
            crypto_taker_fee_bps=crypto_taker_fee_bps,
        )
        self.run_id = run_id or (f"backtest-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}")
        self.ai_journal = None
        if self.config.ai_logging_enabled:
            try:
                self.ai_journal = AIDecisionJournal(
                    self.config.ai_log_path,
                    "backtest",
                    self.config.timeframe,
                    self.config.prediction_horizon_bars,
                    self.run_id,
                )
            except Exception:
                log.exception("AI decision journal initialization failed; backtest continues")
        self.engine = TradingEngine(
            self.broker,
            self.config,
            strategies,
            ai_journal=self.ai_journal,
        )
        self.execution = ExecutionCoordinator(self.engine.orders, self.engine.risk)
        self.starting_equity = starting_equity

    def run(self, bars: list[Bar], warmup: list[Bar] | None = None) -> BacktestResult:
        if warmup:
            self.engine.seed(warmup)
        ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol))
        curve: list[float] = []
        curve_timestamps: list[datetime] = []
        recorded_pnls = 0
        recorded_fills = 0
        pending_context: list[Bar] = []
        for _, grouped in groupby(ordered, key=lambda bar: bar.timestamp):
            cycle = list(grouped)
            primary = [bar for bar in cycle if bar.timeframe == self.config.timeframe]
            context = [bar for bar in cycle if bar.timeframe != self.config.timeframe]
            decision_time = cycle[0].timestamp
            available_context = [
                bar for bar in pending_context if self._available_at(bar) <= decision_time
            ]
            if available_context:
                self.engine.seed(available_context)
                available_ids = {id(bar) for bar in available_context}
                pending_context = [bar for bar in pending_context if id(bar) not in available_ids]
            pending_context.extend(context)
            for bar in primary:
                self.broker.advance(bar)
            self._apply_execution_events()
            if self.ai_journal is not None:
                for fill in self.broker.fills[recorded_fills:]:
                    self.ai_journal.record_broker_fill(fill)
            recorded_fills = len(self.broker.fills)
            for pnl in self.broker.realized_pnls[recorded_pnls:]:
                self.engine.risk.record_realized_pnl(pnl)
            recorded_pnls = len(self.broker.realized_pnls)
            self.engine.process_cycle(primary)
            curve.append(self.broker.account().equity)
            curve_timestamps.append(decision_time)
        benchmark = [
            bar for bar in ordered if bar.symbol == "SPY" and bar.timeframe == self.config.timeframe
        ]
        if not benchmark and len({bar.symbol for bar in ordered}) == 1:
            benchmark = [bar for bar in ordered if bar.timeframe == self.config.timeframe]
        benchmark_return = (
            benchmark[-1].close / benchmark[0].close - 1
            if len(benchmark) > 1 and benchmark[0].close > 0
            else 0.0
        )
        daily_curve: list[float] = []
        for day in sorted({timestamp.date() for timestamp in curve_timestamps}):
            daily_curve.append(
                curve[
                    max(
                        index
                        for index, timestamp in enumerate(curve_timestamps)
                        if timestamp.date() == day
                    )
                ]
            )
        has_equity = any(bar.market == Market.EQUITY for bar in ordered)
        annualizer = math.sqrt(252.0 if has_equity else 365.0)
        return self._result(curve, benchmark_return, annualizer, daily_curve)

    def _result(
        self,
        curve: list[float],
        benchmark_return: float,
        annualizer: float,
        metric_curve: list[float] | None = None,
    ) -> BacktestResult:
        ending = curve[-1] if curve else self.starting_equity
        peak = self.starting_equity
        max_drawdown = 0.0
        for equity in curve:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
        pnls = self.broker.realized_pnls
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        metric_curve = metric_curve or curve
        returns = [
            metric_curve[i] / metric_curve[i - 1] - 1
            for i in range(1, len(metric_curve))
            if metric_curve[i - 1] > 0
        ]
        downside = [value for value in returns if value < 0]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        std = self._stddev(returns)
        downside_std = self._stddev(downside)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        return BacktestResult(
            starting_equity=self.starting_equity,
            ending_equity=ending,
            total_return=ending / self.starting_equity - 1,
            benchmark_return=benchmark_return,
            excess_return=ending / self.starting_equity - 1 - benchmark_return,
            max_drawdown=max_drawdown,
            total_trades=len(pnls),
            win_rate=len(wins) / len(pnls) if pnls else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else None,
            profit_factor_is_infinite=gross_loss == 0 and gross_profit > 0,
            expectancy=sum(pnls) / len(pnls) if pnls else 0.0,
            sharpe=mean_return / std * annualizer if std > 0 else 0.0,
            sortino=mean_return / downside_std * annualizer if downside_std > 0 else 0.0,
            equity_curve=tuple(curve),
            realized_pnls=tuple(pnls),
            open_positions=len(self.broker.positions),
            unrealized_pnl=sum(
                (
                    position.current_price - position.avg_price
                    if position.direction.value == "long"
                    else position.avg_price - position.current_price
                )
                * position.qty
                for position in self.broker.positions.values()
            ),
            open_risk=sum(
                abs(position.current_price - position.stop) * position.qty
                for position in self.broker.positions.values()
            ),
            liquidated_at_end=False,
        )

    def _apply_execution_events(self) -> None:
        for event in self.broker.drain_execution_events():
            self.execution.apply(event)

    @staticmethod
    def _available_at(bar: Bar) -> datetime:
        return bar.timestamp + timeframe_duration(bar.timeframe)

    @staticmethod
    def _stddev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
