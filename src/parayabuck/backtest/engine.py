from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from uuid import uuid4

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar
from parayabuck.engine import TradingEngine
from parayabuck.logger import get_logger
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
    profit_factor: float
    expectancy: float
    sharpe: float
    sortino: float
    equity_curve: tuple[float, ...]
    realized_pnls: tuple[float, ...]


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
        self.starting_equity = starting_equity

    def run(self, bars: list[Bar], warmup: list[Bar] | None = None) -> BacktestResult:
        if warmup:
            self.engine.seed(warmup)
        ordered = sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol))
        curve: list[float] = []
        recorded_pnls = 0
        recorded_fills = 0
        for _, grouped in groupby(ordered, key=lambda bar: bar.timestamp):
            cycle = list(grouped)
            primary = [bar for bar in cycle if bar.timeframe == self.config.timeframe]
            context = [bar for bar in cycle if bar.timeframe != self.config.timeframe]
            if context:
                self.engine.seed(context)
            for bar in primary:
                self.broker.advance(bar)
            if self.ai_journal is not None:
                for fill in self.broker.fills[recorded_fills:]:
                    self.ai_journal.record_broker_fill(fill)
            recorded_fills = len(self.broker.fills)
            for pnl in self.broker.realized_pnls[recorded_pnls:]:
                self.engine.risk.record_realized_pnl(pnl)
            recorded_pnls = len(self.broker.realized_pnls)
            self.engine.process_cycle(primary)
            curve.append(self.broker.account().equity)
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
        timestamps = sorted({bar.timestamp for bar in ordered})
        annualizer = self._annualizer(timestamps)
        return self._result(curve, benchmark_return, annualizer)

    def _result(
        self, curve: list[float], benchmark_return: float, annualizer: float
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
        returns = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
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
            profit_factor=gross_profit / gross_loss
            if gross_loss > 0
            else (math.inf if gross_profit > 0 else 0.0),
            expectancy=sum(pnls) / len(pnls) if pnls else 0.0,
            sharpe=mean_return / std * annualizer if std > 0 else 0.0,
            sortino=mean_return / downside_std * annualizer if downside_std > 0 else 0.0,
            equity_curve=tuple(curve),
            realized_pnls=tuple(pnls),
        )

    @staticmethod
    def _stddev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))

    @staticmethod
    def _annualizer(timestamps: list[datetime]) -> float:
        if len(timestamps) < 2:
            return 0.0
        intervals = sorted(
            (right - left).total_seconds()
            for left, right in zip(timestamps, timestamps[1:], strict=False)
            if right > left
        )
        if not intervals:
            return 0.0
        median_seconds = intervals[len(intervals) // 2]
        observed_days = {timestamp.date() for timestamp in timestamps}
        calendar_span = max(1, (timestamps[-1].date() - timestamps[0].date()).days + 1)
        sessions_per_year = min(365.0, len(observed_days) / calendar_span * 365.25)
        periods_per_session = 86_400 / median_seconds
        return math.sqrt(max(1.0, sessions_per_year * periods_per_session))
