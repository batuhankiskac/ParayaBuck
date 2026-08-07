from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import groupby
from uuid import uuid4

from parayabuck.backtest.broker import BacktestBroker
from parayabuck.backtest.stats import sample_stddev
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market
from parayabuck.engine import StrategyProtocol, TradingEngine
from parayabuck.execution.coordinator import ExecutionCoordinator
from parayabuck.logger import get_logger
from parayabuck.markets.timeframes import timeframe_duration
from parayabuck.monitoring import AIDecisionJournal

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
    benchmark_symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    duration_days: int = 0
    cagr: float = 0.0
    annualized_volatility: float = 0.0
    calmar: float = 0.0
    trades_per_year: float = 0.0
    average_holding_days: float = 0.0
    median_holding_days: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    payoff_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown_duration_days: int = 0


class BacktestEngine:
    def __init__(
        self,
        config: EngineConfig | None = None,
        starting_equity: float = 10_000.0,
        slippage_bps: float = 2.0,
        commission: float = 0.0,
        spread_bps: float = 0.0,
        crypto_taker_fee_bps: float = 0.0,
        strategies: tuple[StrategyProtocol, ...] | None = None,
        run_id: str | None = None,
        allowed_trade_symbols: set[str] | None = None,
        benchmark_symbol: str = "SPY",
        max_entry_participation_pct: float = 0.10,
        max_exit_participation_pct: float = 0.10,
        historical_universe_filter: Callable[[str, Market, datetime], bool] | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.broker = BacktestBroker(
            starting_equity,
            slippage_bps,
            commission,
            spread_bps=spread_bps,
            allow_short=self.config.allow_short,
            crypto_taker_fee_bps=crypto_taker_fee_bps,
            max_entry_participation_pct=max_entry_participation_pct,
            max_exit_participation_pct=max_exit_participation_pct,
        )
        self.benchmark_symbol = benchmark_symbol
        self.benchmark_bars: list[Bar] = []
        self.used_benchmark_symbol = benchmark_symbol
        self.curve_timestamps: list[datetime] = []
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
            allowed_trade_symbols=allowed_trade_symbols,
            historical_universe_filter=historical_universe_filter,
        )
        self.execution = ExecutionCoordinator(self.engine.orders, self.engine.risk)
        self.starting_equity = starting_equity
        self._liquidated_since_last_run = False
        self._daily_equity_for_volatility: dict[date, float] = {}

    @staticmethod
    def _deduplicate_bars(bars: list[Bar]) -> list[Bar]:
        """Collapse duplicate bars, keeping the first occurrence of each.

        Adjacent download chunks share an inclusive cache boundary, so the
        same (symbol, timeframe, timestamp) can legitimately appear twice;
        simulating it twice would let one real bar consume the participation
        cap twice. Identical duplicates are collapsed silently, but
        conflicting OHLCV for the same bar is a data error and raises.
        """
        unique: dict[tuple[str, str, datetime], Bar] = {}
        for bar in bars:
            key = (bar.symbol, bar.timeframe, bar.timestamp)
            previous = unique.get(key)
            if previous is None:
                unique[key] = bar
            elif (previous.open, previous.high, previous.low, previous.close, previous.volume) != (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            ):
                raise ValueError(
                    f"Conflicting duplicate bars for {bar.symbol} {bar.timeframe} "
                    f"at {bar.timestamp.isoformat()}"
                )
        return list(unique.values())

    def run(
        self,
        bars: list[Bar],
        warmup: list[Bar] | None = None,
        *,
        liquidate_at_end: bool = True,
    ) -> BacktestResult:
        """Run one backtest window on this engine instance.

        The broker accumulates across calls; each result reports only the
        pnls/fills produced by that call (see the offset bookkeeping below),
        so the same instance may be re-run for later windows. Bars are
        deduplicated (conflicting duplicates raise) and processed in
        timestamp order.
        """
        if warmup:
            self.engine.seed(warmup)
        self._liquidated_since_last_run = False
        # The broker accumulates across run() calls on the same instance
        # (resume / walk-forward patterns); record the starting offsets so
        # this run only reports the pnls and fills it produced itself.
        pnl_offset = len(self.broker.realized_pnls)
        trades_offset = len(self.broker.trades)
        fills_offset = len(self.broker.fills)
        ordered = sorted(self._deduplicate_bars(bars), key=lambda bar: (bar.timestamp, bar.symbol))
        curve: list[float] = []
        curve_timestamps: list[datetime] = []
        recorded_pnls = pnl_offset
        recorded_fills = fills_offset
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
            current_equity = self.broker.account().equity
            self._update_volatility_scale(decision_time, current_equity)
            self.engine.process_cycle(primary)
            curve.append(self.broker.account().equity)
            curve_timestamps.append(decision_time)
        self.curve_timestamps = curve_timestamps
        if liquidate_at_end and self.broker.positions:
            last_primary: dict[str, Bar] = {}
            for last_candidate in ordered:
                if last_candidate.timeframe == self.config.timeframe:
                    last_primary[last_candidate.symbol] = last_candidate
            for symbol in list(self.broker.positions):
                last_bar = last_primary.get(symbol)
                if last_bar is not None:
                    self.broker.liquidate_at_end(last_bar)
            self._liquidated_since_last_run = True
            if curve:
                curve.append(self.broker.account().equity)
                curve_timestamps.append(curve_timestamps[-1])
        benchmark = [
            bar
            for bar in ordered
            if bar.symbol == self.benchmark_symbol and bar.timeframe == self.config.timeframe
        ]
        used_benchmark_symbol = self.benchmark_symbol
        if not benchmark:
            # The requested benchmark has no data (for example a multi-symbol
            # CSV without SPY): fall back to the first available symbol so the
            # comparison column never silently reports 0%.
            fallback_symbols = sorted(
                {bar.symbol for bar in ordered if bar.timeframe == self.config.timeframe}
            )
            if fallback_symbols:
                fallback = fallback_symbols[0]
                benchmark = [
                    bar
                    for bar in ordered
                    if bar.symbol == fallback and bar.timeframe == self.config.timeframe
                ]
                used_benchmark_symbol = fallback
        self.benchmark_bars = benchmark
        self.used_benchmark_symbol = used_benchmark_symbol
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
        return self._result(
            curve,
            benchmark_return,
            daily_curve,
            curve_timestamps,
            used_benchmark_symbol,
            pnl_offset=pnl_offset,
            trades_offset=trades_offset,
        )

    def _update_volatility_scale(self, timestamp: datetime, equity: float) -> None:
        current_day = timestamp.date()
        self._daily_equity_for_volatility[current_day] = equity
        completed = [
            self._daily_equity_for_volatility[day]
            for day in sorted(self._daily_equity_for_volatility)
            if day < current_day
        ][-21:]
        if len(completed) < 2:
            return
        returns = [
            completed[index] / completed[index - 1] - 1
            for index in range(1, len(completed))
            if completed[index - 1] > 0
        ]
        if not returns:
            return
        alpha = 2 / 21
        variance = returns[0] ** 2
        for value in returns[1:]:
            variance = alpha * value**2 + (1 - alpha) * variance
        self.engine.risk.set_realized_volatility(math.sqrt(variance * 252))

    def _result(
        self,
        curve: list[float],
        benchmark_return: float,
        metric_curve: list[float] | None = None,
        curve_timestamps: list[datetime] | None = None,
        benchmark_symbol: str = "",
        *,
        pnl_offset: int = 0,
        trades_offset: int = 0,
    ) -> BacktestResult:
        ending = curve[-1] if curve else self.starting_equity
        peak = self.starting_equity
        peak_index = 0
        max_drawdown = 0.0
        max_drawdown_duration_days = 0
        for index, equity in enumerate(curve):
            if equity > peak:
                peak = equity
                peak_index = index
            elif equity < peak and peak > 0:
                drawdown = (peak - equity) / peak
                max_drawdown = max(max_drawdown, drawdown)
                if curve_timestamps:
                    duration_days = (
                        curve_timestamps[index] - curve_timestamps[peak_index]
                    ).total_seconds() / 86_400
                    max_drawdown_duration_days = max(max_drawdown_duration_days, int(duration_days))
        pnls = self.broker.realized_pnls[pnl_offset:]
        trades = self.broker.trades[trades_offset:]
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
        std = sample_stddev(returns)
        downside_std = sample_stddev(downside)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        holding_days = [trade.holding_days for trade in trades]
        streaks: list[bool] = [pnl > 0 for pnl in pnls]
        max_wins = 0
        max_losses = 0
        current = 0
        previous: bool | None = None
        for is_win in streaks:
            if previous is not None and is_win != previous:
                current = 0
            current += 1
            if is_win:
                max_wins = max(max_wins, current)
            else:
                max_losses = max(max_losses, current)
            previous = is_win
        start_ts = curve_timestamps[0] if curve_timestamps else None
        end_ts = curve_timestamps[-1] if curve_timestamps else None
        duration_days = (
            int((end_ts - start_ts).total_seconds() / 86_400) if start_ts and end_ts else 0
        )
        years = duration_days / 365.0
        total_return = ending / self.starting_equity - 1
        cagr = (
            ((ending / self.starting_equity) ** (1 / years) - 1)
            if years > 0 and ending > 0
            else 0.0
        )
        # Annualization follows the observed bar calendar of the metric curve,
        # so mixed equity+crypto portfolios do not assume a single 252/365-day
        # year: Sharpe and volatility are self-consistent with the data.
        periods_per_year = len(returns) / years if years > 0 else 0.0
        annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        return BacktestResult(
            starting_equity=self.starting_equity,
            ending_equity=ending,
            total_return=total_return,
            benchmark_return=benchmark_return,
            excess_return=total_return - benchmark_return,
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
            liquidated_at_end=not self.broker.positions and self._liquidated_since_last_run,
            benchmark_symbol=benchmark_symbol,
            start_date=start_ts.isoformat() if start_ts else "",
            end_date=end_ts.isoformat() if end_ts else "",
            duration_days=duration_days,
            cagr=cagr,
            annualized_volatility=std * annualizer if std > 0 else 0.0,
            calmar=cagr / max_drawdown if max_drawdown > 0 else 0.0,
            trades_per_year=len(pnls) / years if years > 0 else 0.0,
            average_holding_days=sum(holding_days) / len(holding_days) if holding_days else 0.0,
            median_holding_days=sorted(holding_days)[len(holding_days) // 2]
            if holding_days
            else 0.0,
            best_trade_pnl=max(pnls) if pnls else 0.0,
            worst_trade_pnl=min(pnls) if pnls else 0.0,
            average_win=avg_win,
            average_loss=avg_loss,
            payoff_ratio=avg_win / abs(avg_loss) if avg_loss < 0 else 0.0,
            max_consecutive_wins=max_wins,
            max_consecutive_losses=max_losses,
            max_drawdown_duration_days=max_drawdown_duration_days,
        )

    def _apply_execution_events(self) -> None:
        for event in self.broker.drain_execution_events():
            self.execution.apply(event)

    @staticmethod
    def _available_at(bar: Bar) -> datetime:
        duration: timedelta = timeframe_duration(bar.timeframe)
        return bar.timestamp + duration
