from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tabulate import tabulate

from parayabuck.config import Config
from parayabuck.data.base import DataSource
from parayabuck.execution.paper import PaperBroker
from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.risk.manager import RiskManager
from parayabuck.risk.sizer import PositionSizer
from parayabuck.strategy.base import Strategy

log = get_logger("parayabuck.backtest")


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    total_bars: int
    total_trades: int
    starting_balance: float
    ending_balance: float
    ending_equity: float
    total_pnl: float
    max_drawdown: float
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  BACKTEST RESULTS",
            "=" * 60,
            f"  Symbol:            {self.symbol} ({self.timeframe})",
            f"  Period:            {self.start.date()} → {self.end.date()}",
            f"  Total bars:        {self.total_bars:,}",
            f"  Total trades:      {self.total_trades}",
            "",
            f"  Starting balance:  ${self.starting_balance:,.2f}",
            f"  Ending balance:    ${self.ending_balance:,.2f}",
            f"  Ending equity:     ${self.ending_equity:,.2f}",
            f"  Total PnL:         ${self.total_pnl:,.2f}",
            f"  Max drawdown:      ${self.max_drawdown:,.2f}",
            "",
            f"  Win rate:          {self.win_rate:.1%}",
            f"  Avg win:           ${self.avg_win:,.2f}",
            f"  Avg loss:          ${self.avg_loss:,.2f}",
            f"  Profit factor:     {self.profit_factor:.2f}",
            "=" * 60,
            "",
        ]
        return "\n".join(lines)

    def trade_table(self, last_n: int = 20) -> str:
        if not self.trades:
            return "  (no trades)"
        rows = []
        for t in self.trades[-last_n:]:
            rows.append([
                t["timestamp"].strftime("%Y-%m-%d %H:%M"),
                t["side"],
                t["qty"],
                f"{t['price']:.2f}",
                f"${t['pnl']:+.2f}",
            ])
        return str(
            tabulate(rows, headers=["Time", "Side", "Qty", "Price", "PnL"], tablefmt="simple")
        )


class BacktestEngine:
    """Event-driven backtest engine.

    Iterates over historical bars, feeds them to the strategy, passes
    any generated orders through the risk manager, and executes them
    on the paper broker.
    """

    def __init__(
        self,
        config: Config,
        data_source: DataSource,
        broker: PaperBroker,
        risk_manager: RiskManager,
        position_sizer: PositionSizer | None = None,
    ) -> None:
        self.config = config
        self.data = data_source
        self.broker = broker
        self.risk = risk_manager
        self.position_sizer = position_sizer or PositionSizer(
            risk_pct=config.risk.risk_per_trade / config.account.starting_balance,
            max_qty=config.risk.max_position_size,
        )

    def run(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str,
        start: str | None = None,
        end: str | None = None,
    ) -> BacktestResult:
        log.info(f"Starting backtest: {strategy.name} on {symbol} {timeframe}")
        strategy.on_init()

        bars = self.data.get_bars(symbol, timeframe, start, end)
        if not bars:
            raise ValueError(f"No bars found for {symbol} {timeframe}")

        log.info(f"Loaded {len(bars):,} bars from {bars[0].timestamp} to {bars[-1].timestamp}")

        max_dd = 0.0
        current_date = None
        equity_curve: list[float] = []

        for bar in bars:
            # Reset daily PnL on new day
            bar_date = bar.timestamp.date()
            if current_date != bar_date:
                if current_date is not None:
                    self.broker.reset_daily_pnl()
                current_date = bar_date

            # Update market price for this bar
            self.broker.update_market_price(bar.symbol, bar.close, bar.timestamp)

            # Update position sizer history
            self.position_sizer.update_history(
                bar.symbol, bar.high, bar.low, bar.close
            )

            # Get current position
            position = self.broker.get_position(bar.symbol)

            # Ask strategy for an order
            order = strategy.on_bar(bar, position)
            if order is not None:
                # Size the order using ATR/Kelly
                order = self.position_sizer.size_order(
                    order, self.broker.get_account()
                )
                # Pass through risk manager
                order = self.risk.check_order(order, self.broker.get_account())
                if order is not None:
                    self.broker.submit_order(order)

            # Track max drawdown and equity curve
            account = self.broker.get_account()
            equity_curve.append(account.equity)
            max_dd = max(max_dd, account.drawdown)

            # Record closed trades for Kelly
            for t in account.trades:
                if t.realized_pnl != 0 and t.realized_pnl != -t.commission:
                    self.position_sizer.record_trade(t.symbol, t.realized_pnl)

            # Check risk halt
            self.risk.check_account(account)
            if self.risk.is_halted:
                log.warning(f"Trading halted: {self.risk.halt_reason}")
                break

        strategy.on_stop()

        return self._build_result(strategy, symbol, timeframe, bars, max_dd, equity_curve)

    def _build_result(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str,
        bars: list[Bar],
        max_drawdown: float,
        equity_curve: list[float],
    ) -> BacktestResult:
        account = self.broker.get_account()
        trades = account.trades

        # Use realized_pnl from each trade (closing fills have it, entries = -commission)
        trade_pnls: list[dict[str, Any]] = []
        wins: list[float] = []
        losses: list[float] = []
        gross_profit = 0.0
        gross_loss = 0.0

        for t in trades:
            pnl = t.realized_pnl
            trade_pnls.append({
                "timestamp": t.timestamp,
                "side": t.side.value,
                "qty": t.qty,
                "price": t.price,
                "pnl": pnl,
            })
            if pnl > 0:
                wins.append(pnl)
                gross_profit += pnl
            elif pnl < 0:
                losses.append(pnl)
                gross_loss += abs(pnl)

        # Only count closing trades for win rate (entries have pnl = -commission)
        closing_trades = [t for t in trades if t.realized_pnl != -t.commission]
        win_rate = len(wins) / len(closing_trades) if closing_trades else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        result = BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            start=bars[0].timestamp,
            end=bars[-1].timestamp,
            total_bars=len(bars),
            total_trades=len(trades),
            starting_balance=self.config.account.starting_balance,
            ending_balance=account.balance,
            ending_equity=account.equity,
            total_pnl=account.equity - self.config.account.starting_balance,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            trades=trade_pnls,
            equity_curve=equity_curve,
        )

        return result
