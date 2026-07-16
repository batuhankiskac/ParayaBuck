from __future__ import annotations

import math
from dataclasses import dataclass

from parayabuck.models import Trade


@dataclass
class PerformanceMetrics:
    total_return: float = 0.0
    total_return_pct: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    total_trades: int = 0
    avg_hold_bars: float = 0.0

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  PERFORMANCE METRICS",
            "=" * 60,
            f"  Total return:      ${self.total_return:,.2f} ({self.total_return_pct:+.2%})",
            f"  Annualized:        {self.annualized_return:+.2%}",
            f"  Volatility:        {self.volatility:.2%} (annualized)",
            f"  Max drawdown:      ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2%})",
            "",
            f"  Sharpe ratio:      {self.sharpe_ratio:.2f}",
            f"  Sortino ratio:     {self.sortino_ratio:.2f}",
            f"  Calmar ratio:      {self.calmar_ratio:.2f}",
            "",
            f"  Total trades:      {self.total_trades}",
            f"  Win rate:          {self.win_rate:.1%}",
            f"  Profit factor:     {self.profit_factor:.2f}",
            f"  Avg win:           ${self.avg_win:,.2f}",
            f"  Avg loss:          ${self.avg_loss:,.2f}",
            f"  Expectancy:        ${self.expectancy:,.2f}",
            "=" * 60,
            "",
        ]
        return "\n".join(lines)


def calculate_metrics(
    equity_curve: list[float],
    trades: list[Trade],
    starting_balance: float,
    bars_per_year: int = 252,
    risk_free_rate: float = 0.04,
) -> PerformanceMetrics:
    """Calculate comprehensive performance metrics from equity curve and trades."""

    if not equity_curve:
        return PerformanceMetrics()

    ending_equity = equity_curve[-1]
    total_return = ending_equity - starting_balance
    total_return_pct = total_return / starting_balance if starting_balance > 0 else 0.0

    n_periods = len(equity_curve)
    years = n_periods / bars_per_year if bars_per_year > 0 else 1
    if years > 0 and ending_equity > 0 and starting_balance > 0:
        annualized_return = (ending_equity / starting_balance) ** (1 / years) - 1
    else:
        annualized_return = 0.0

    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1
        for i in range(1, n_periods)
        if equity_curve[i - 1] != 0
    ]

    if returns:
        mean_return = sum(returns) / len(returns)
        var_return = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        volatility = var_return**0.5 * math.sqrt(bars_per_year)
    else:
        mean_return = 0.0
        volatility = 0.0

    rf_per_period = risk_free_rate / bars_per_year
    if volatility > 0:
        sharpe = ((mean_return - rf_per_period) / (volatility / math.sqrt(bars_per_year)))
    else:
        sharpe = 0.0

    downside_returns = [r for r in returns if r < 0]
    if downside_returns:
        downside_var = sum(r**2 for r in downside_returns) / len(downside_returns)
        downside_dev = downside_var**0.5 * math.sqrt(bars_per_year)
    else:
        downside_dev = 0.0
    if downside_dev > 0:
        sortino = (mean_return * bars_per_year - risk_free_rate) / downside_dev
    else:
        sortino = 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = peak - eq
        dd_pct = dd / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct

    if max_dd_pct > 0:
        calmar = annualized_return / max_dd_pct
    else:
        calmar = 0.0

    closing_trades = [t for t in trades if t.realized_pnl != -t.commission]
    wins = [t for t in closing_trades if t.realized_pnl > 0]
    losses = [t for t in closing_trades if t.realized_pnl < 0]

    win_rate = len(wins) / len(closing_trades) if closing_trades else 0.0
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = sum(abs(t.realized_pnl) for t in losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = (
        (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        if closing_trades else 0.0
    )

    return PerformanceMetrics(
        total_return=total_return,
        total_return_pct=total_return_pct,
        annualized_return=annualized_return,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        calmar_ratio=calmar,
        volatility=volatility,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        total_trades=len(closing_trades),
    )
