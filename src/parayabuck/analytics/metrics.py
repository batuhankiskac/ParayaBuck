from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

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
    # Benchmark comparison metrics (populated when a benchmark series is
    # supplied to calculate_metrics). A strategy's alpha is only meaningful
    # relative to a passive buy-and-hold — without this, a strategy that
    # returned 10% in a year when SPY returned 25% looks "profitable" but
    # actually destroyed value vs. just holding the index.
    benchmark_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    excess_return: float = 0.0  # total_return - benchmark_return
    information_ratio: float = 0.0  # alpha / tracking_error

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
        ]
        if self.benchmark_return != 0.0 or self.alpha != 0.0:
            lines.append("")
            lines.append("  vs. Benchmark (SPY buy & hold):")
            lines.append(f"  Benchmark return:  {self.benchmark_return:+.2%}")
            lines.append(f"  Excess return:     {self.excess_return:+.2%}")
            lines.append(f"  Alpha (annual):    {self.alpha:+.2%}")
            lines.append(f"  Beta:              {self.beta:.2f}")
            lines.append(f"  Info ratio:        {self.information_ratio:.2f}")
        lines.append("=" * 60)
        lines.append("")
        return "\n".join(lines)


def calculate_metrics(
    equity_curve: list[float],
    trades: list[Trade],
    starting_balance: float,
    bars_per_year: int = 252,
    risk_free_rate: float = 0.04,
    benchmark_curve: list[float] | None = None,
    equity_timestamps: list[datetime] | None = None,
    benchmark_timestamps: list[datetime] | None = None,
) -> PerformanceMetrics:
    """Calculate comprehensive performance metrics from equity curve and trades.

    Args:
        benchmark_curve: Optional benchmark equity curve (e.g. SPY buy & hold
            with the same starting balance). When provided, alpha/beta/excess
            return are computed. The curve must be the same length as
            `equity_curve`; if it's longer or shorter, we align by truncating
            to the shorter.
    """

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

    # Sharpe is computed below (annualized) after volatility is finalised.

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

    closing_trades = [t for t in trades if t.closes_position]
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
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if closing_trades else 0.0

    # Annualized Sharpe (mean_return is per-bar; multiply by periods/year and
    # divide std by sqrt(periods/year) → multiply by sqrt(periods/year)).
    if volatility > 0:
        sharpe = (mean_return * bars_per_year - risk_free_rate) / volatility
    else:
        sharpe = 0.0

    # Benchmark comparison: alpha, beta, excess return, information ratio.
    # Without this a strategy that returned 10% in a year SPY returned 25%
    # looks profitable but actually destroyed value vs. just holding SPY.
    benchmark_return = 0.0
    alpha = 0.0
    beta = 0.0
    excess_return = total_return_pct
    information_ratio = 0.0
    if benchmark_curve and len(benchmark_curve) >= 2:
        # Align on actual timestamps when available. Truncating a 24/7 crypto
        # curve against regular-session SPY bars compares unrelated moments.
        if (
            equity_timestamps
            and benchmark_timestamps
            and len(equity_timestamps) == len(equity_curve)
            and len(benchmark_timestamps) == len(benchmark_curve)
        ):
            strat_by_time = dict(zip(equity_timestamps, equity_curve, strict=True))
            bench_by_time = dict(zip(benchmark_timestamps, benchmark_curve, strict=True))
            common = sorted(set(strat_by_time) & set(bench_by_time))
            strat = [strat_by_time[timestamp] for timestamp in common]
            bench = [bench_by_time[timestamp] for timestamp in common]
        else:
            n = min(len(equity_curve), len(benchmark_curve))
            bench = benchmark_curve[-n:]
            strat = equity_curve[-n:]
        if len(bench) < 2 or len(strat) < 2:
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
                excess_return=total_return_pct,
            )
        bench_return = (bench[-1] - bench[0]) / bench[0] if bench[0] > 0 else 0.0
        strat_return = (strat[-1] - strat[0]) / strat[0] if strat[0] > 0 else 0.0
        benchmark_return = bench_return
        excess_return = strat_return - bench_return

        # Per-bar returns for alpha/beta.
        bench_rets = [
            bench[i] / bench[i - 1] - 1
            for i in range(1, len(bench))
            if bench[i - 1] != 0
        ]
        strat_rets = [
            strat[i] / strat[i - 1] - 1
            for i in range(1, len(strat))
            if strat[i - 1] != 0
        ]
        m = min(len(bench_rets), len(strat_rets))
        if m > 1:
            bench_rets = bench_rets[-m:]
            strat_rets = strat_rets[-m:]
            bench_mean = sum(bench_rets) / m
            strat_mean = sum(strat_rets) / m
            cov = sum(
                (bench_rets[i] - bench_mean) * (strat_rets[i] - strat_mean)
                for i in range(m)
            ) / m
            bench_var = sum((r - bench_mean) ** 2 for r in bench_rets) / m
            if bench_var > 0:
                beta = cov / bench_var
                # Annualized alpha: strat annual return - beta * bench annual return
                # minus risk-free (CAPM-style).
                strat_annual = (1 + strat_mean) ** bars_per_year - 1
                bench_annual = (1 + bench_mean) ** bars_per_year - 1
                alpha = strat_annual - (risk_free_rate + beta * (bench_annual - risk_free_rate))
            # Tracking error & information ratio.
            active_returns = [strat_rets[i] - bench_rets[i] for i in range(m)]
            if active_returns:
                active_mean = sum(active_returns) / len(active_returns)
                active_var = (
                    sum((r - active_mean) ** 2 for r in active_returns) / len(active_returns)
                )
                tracking_error = active_var**0.5 * math.sqrt(bars_per_year)
                if tracking_error > 0:
                    information_ratio = (active_mean * bars_per_year) / tracking_error

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
        benchmark_return=benchmark_return,
        alpha=alpha,
        beta=beta,
        excess_return=excess_return,
        information_ratio=information_ratio,
    )
