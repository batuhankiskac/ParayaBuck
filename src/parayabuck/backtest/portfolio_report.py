from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestResult,
    RebalanceRecord,
)
from parayabuck.core.models import Bar

MONTH_NAMES = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


def _csv_cell(value: object) -> object:
    """Neutralise spreadsheet formula injection in text cells (broker-supplied symbols)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _benchmark_yearly_returns(benchmark_bars: Sequence[Bar] | None) -> dict[int, float | None]:
    """Calendar-year benchmark returns; ``None`` where no valid anchor exists (never a fabricated 0.0)."""
    if not benchmark_bars:
        return {}
    by_year: dict[int, list[float]] = {}
    for bar in sorted(benchmark_bars, key=lambda item: item.timestamp):
        by_year.setdefault(bar.timestamp.year, []).append(bar.close)
    returns: dict[int, float | None] = {}
    previous_close: float | None = None
    for year in sorted(by_year):
        closes = by_year[year]
        anchor = previous_close if previous_close is not None else closes[0]
        valid = math.isfinite(anchor) and anchor > 0 and math.isfinite(closes[-1])
        returns[year] = closes[-1] / anchor - 1 if valid else None
        previous_close = closes[-1]
    return returns


ROLLING_BETA_WINDOW_DAYS = 63


@dataclass(frozen=True)
class PortfolioReport:
    summary: dict[str, object]
    rebalances: tuple[RebalanceRecord, ...]
    fills: tuple[dict[str, object], ...]
    weight_history: tuple[tuple[str, dict[str, float]], ...]
    universe_history: tuple[tuple[str, tuple[str, ...]], ...]
    monthly_returns: dict[int, dict[int, float]]
    yearly_returns: tuple[tuple[int, float, float | None], ...]
    regime_performance: dict[str, object]
    rolling_beta: tuple[float, ...]
    per_symbol: tuple[dict[str, object], ...]
    equity_curve: tuple[tuple[str, float], ...]
    drawdowns: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "rebalances": [
                {
                    **asdict(record),
                    "decision_timestamp": record.decision_timestamp.isoformat(),
                }
                for record in self.rebalances
            ],
            "fills": list(self.fills),
            "weight_history": [
                {"timestamp": timestamp, "weights": weights} for timestamp, weights in self.weight_history
            ],
            "universe_history": [
                {"timestamp": timestamp, "symbols": list(symbols)} for timestamp, symbols in self.universe_history
            ],
            "monthly_returns": {str(year): months for year, months in self.monthly_returns.items()},
            "yearly_returns": [
                {"year": year, "return_fraction": return_fraction, "benchmark_fraction": benchmark_fraction}
                for year, return_fraction, benchmark_fraction in self.yearly_returns
            ],
            "regime_performance": self.regime_performance,
            "rolling_beta": list(self.rolling_beta),
            "per_symbol": list(self.per_symbol),
            "equity_curve": [{"timestamp": timestamp, "equity": equity} for timestamp, equity in self.equity_curve],
            "drawdowns": [{"timestamp": timestamp, "drawdown": drawdown} for timestamp, drawdown in self.drawdowns],
        }

    def write_csvs(self, output_dir: str) -> list[str]:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        summary_path = directory / "portfolio_summary.csv"
        with summary_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for key, value in self.summary.items():
                writer.writerow([_csv_cell(key), _csv_cell(value)])
        written.append(str(directory / "portfolio_summary.csv"))
        with (directory / "rebalances.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "rebalance_number",
                    "decision_timestamp",
                    "reason",
                    "target_cash_weight",
                    "turnover",
                    "cost",
                    "universe_version",
                ]
            )
            for record in self.rebalances:
                writer.writerow(
                    [
                        record.rebalance_number,
                        record.decision_timestamp.isoformat(),
                        _csv_cell(record.reason),
                        f"{record.target_cash_weight:.6f}",
                        f"{record.realized_turnover:.6f}",
                        f"{record.cost:.6f}",
                        _csv_cell(record.universe_version),
                    ]
                )
        written.append(str(directory / "rebalances.csv"))
        with (directory / "weight_history.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "symbol", "weight"])
            for timestamp, weights in self.weight_history:
                for symbol, weight in sorted(weights.items()):
                    writer.writerow([timestamp, _csv_cell(symbol), f"{weight:.6f}"])
        written.append(str(directory / "weight_history.csv"))
        with (directory / "fills.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp",
                    "symbol",
                    "side",
                    "qty",
                    "reference_price",
                    "fill_price",
                    "slippage_cost",
                    "spread_cost",
                    "fee",
                ]
            )
            for fill in self.fills:
                writer.writerow(
                    [
                        fill["timestamp"],
                        _csv_cell(fill["symbol"]),
                        _csv_cell(fill["side"]),
                        fill["qty"],
                        fill["reference_price"],
                        fill["fill_price"],
                        fill["slippage_cost"],
                        fill["spread_cost"],
                        fill["fee"],
                    ]
                )
        written.append(str(directory / "fills.csv"))
        with (directory / "universe_history.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "symbols"])
            for timestamp, symbols in self.universe_history:
                writer.writerow([timestamp, _csv_cell(",".join(symbols))])
        written.append(str(directory / "universe_history.csv"))
        with (directory / "monthly_returns.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["year", *MONTH_NAMES])
            for year in sorted(self.monthly_returns):
                writer.writerow(
                    [
                        year,
                        *[self.monthly_returns[year].get(month, "") for month in range(1, 13)],
                    ]
                )
        written.append(str(directory / "monthly_returns.csv"))
        with (directory / "per_symbol.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["symbol", "market", "realized_pnl", "fees", "final_weight"])
            for contribution in self.per_symbol:
                writer.writerow(
                    [
                        _csv_cell(contribution["symbol"]),
                        _csv_cell(contribution["market"]),
                        contribution["realized_pnl"],
                        contribution["fees"],
                        contribution["final_weight"],
                    ]
                )
        written.append(str(directory / "per_symbol.csv"))
        with (directory / "equity_curve.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "equity"])
            writer.writerows(self.equity_curve)
        written.append(str(directory / "equity_curve.csv"))
        with (directory / "drawdowns.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "drawdown"])
            writer.writerows(self.drawdowns)
        written.append(str(directory / "drawdowns.csv"))
        return written


def build_portfolio_report(
    result: PortfolioBacktestResult,
    *,
    strategy_name: str,
    strategy_version: str,
) -> PortfolioReport:
    closed_pnls = [pnl for _timestamp, _symbol, pnl in result.closed_position_events]
    wins = [pnl for pnl in closed_pnls if pnl > 0]
    losses = [pnl for pnl in closed_pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    maximum_exposure = max(
        (sum(weights.values()) for _timestamp, weights in result.weight_history),
        default=0.0,
    )
    maximum_positions = max(
        (sum(1 for weight in weights.values() if weight > 0) for _timestamp, weights in result.weight_history),
        default=0,
    )
    holding_hours = _average_holding_hours(result)
    summary = {
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "duration_days": result.duration_days,
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "total_return": result.total_return,
        "benchmark_return": result.benchmark_return,
        "benchmark_price_return": result.benchmark_price_return,
        "benchmark_return_basis": result.benchmark_return_basis,
        "benchmark_symbol": result.benchmark_symbol,
        "excess_return": result.excess_return,
        "gross_return": result.gross_return,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "max_drawdown_duration_days": result.max_drawdown_duration_days,
        "cagr": result.cagr,
        "annualized_volatility": result.annualized_volatility,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "fee_total": result.fee_total,
        "turnover": result.turnover,
        "average_holdings_count": result.average_holdings_count,
        "average_cash_ratio": result.average_cash_ratio,
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "maximum_concentration": result.maximum_concentration,
        "worst_month_return": result.worst_month_return,
        "worst_year_return": result.worst_year_return,
        "positive_month_ratio": result.positive_month_ratio,
        "realized_pnl_count": len(result.realized_pnls),
        "realized_pnl_total": sum(result.realized_pnls),
        "number_of_trades": len(closed_pnls),
        "win_rate": len(wins) / len(closed_pnls) if closed_pnls else 0.0,
        "average_win": sum(wins) / len(wins) if wins else 0.0,
        "average_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "expectancy": sum(closed_pnls) / len(closed_pnls) if closed_pnls else 0.0,
        "best_trade": max(closed_pnls, default=0.0),
        "worst_trade": min(closed_pnls, default=0.0),
        "average_holding_hours": holding_hours,
        "average_exposure": 1.0 - result.average_cash_ratio,
        "maximum_exposure": maximum_exposure,
        "time_in_market": result.time_in_market,
        "average_positions": result.average_holdings_count,
        "maximum_positions": maximum_positions,
        # Price-only buy-and-hold total return per decision symbol (not SPY),
        # close-to-close over the same curve window as the strategy.
        "benchmarks": dict(sorted(result.benchmarks.items())),
        # Labels for the ratios and cash flows: turnover counts buys and sells
        # together; Sharpe and Sortino take a zero risk-free rate.
        "turnover_basis": "two-sided: total traded notional (buys plus sells) divided by average equity",
        "sharpe_basis": "risk-free rate 0; idle cash interest, when modelled, is not deducted from the return series",
        "dividend_treatment": {
            "total": result.dividend_total,
            "basis": "gross cash dividend per share credited to held shares; no withholding tax is applied",
            "attribution": (
                "cash income credited to cash; excluded from realized PnL, win rate, profit factor and "
                "per-symbol contributions"
            ),
        },
    }
    fills: list[dict[str, object]] = [
        {
            "timestamp": fill.timestamp.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": fill.qty,
            "reference_price": fill.reference_price,
            "fill_price": fill.fill_price,
            "slippage_cost": fill.slippage_cost,
            "spread_cost": fill.spread_cost,
            "fee": fill.fee,
        }
        for fill in result.fills
    ]
    weight_history = tuple((timestamp.isoformat(), weights) for timestamp, weights in result.weight_history)
    universe_history = tuple((timestamp.isoformat(), symbols) for timestamp, symbols in result.universe_history)
    monthly = _monthly_matrix(result)
    yearly = _yearly_returns(result)
    beta_series = _rolling_beta(result)
    regime: dict[str, object] = {
        "up_months": 0,
        "down_months": 0,
        "mean_up_month_return": 0.0,
        "mean_down_month_return": 0.0,
        "mean_rolling_beta": sum(beta_series) / len(beta_series) if beta_series else 0.0,
        "last_rolling_beta": beta_series[-1] if beta_series else 0.0,
    }
    up_months = 0
    down_months = 0
    up_returns: list[float] = []
    down_returns: list[float] = []
    benchmark_monthly = _benchmark_monthly_returns(result.benchmark_bars)
    for (year, month), portfolio_return in _flat_monthly(result).items():
        benchmark_value = benchmark_monthly.get((year, month))
        if benchmark_value is None:
            continue
        if benchmark_value >= 0:
            up_months += 1
            up_returns.append(portfolio_return)
        else:
            down_months += 1
            down_returns.append(portfolio_return)
    regime["up_months"] = up_months
    regime["down_months"] = down_months
    if up_returns:
        regime["mean_up_month_return"] = sum(up_returns) / len(up_returns)
    if down_returns:
        regime["mean_down_month_return"] = sum(down_returns) / len(down_returns)
    per_symbol: list[dict[str, object]] = [
        {
            "symbol": contribution.symbol,
            "market": contribution.market.value,
            "realized_pnl": contribution.realized_pnl,
            "fees": contribution.fees,
            "final_weight": contribution.final_weight,
        }
        for contribution in result.per_symbol
    ]
    equity_curve = tuple(
        (timestamp.isoformat(), equity)
        for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True)
    )
    peak = result.starting_equity
    drawdowns: list[tuple[str, float]] = []
    for timestamp, equity in equity_curve:
        peak = max(peak, equity)
        drawdowns.append((timestamp, (peak - equity) / peak if peak > 0 else 0.0))
    return PortfolioReport(
        summary=summary,
        rebalances=result.rebalances,
        fills=tuple(fills),
        weight_history=weight_history,
        universe_history=universe_history,
        monthly_returns=monthly,
        yearly_returns=yearly,
        regime_performance=regime,
        rolling_beta=beta_series,
        per_symbol=tuple(per_symbol),
        equity_curve=equity_curve,
        drawdowns=tuple(drawdowns),
    )


def _average_holding_hours(result: PortfolioBacktestResult) -> float:
    quantities: dict[str, float] = {}
    opened_at: dict[str, datetime] = {}
    durations: list[float] = []
    for fill in sorted(result.fills, key=lambda item: (item.timestamp, item.symbol)):
        current = quantities.get(fill.symbol, 0.0)
        if fill.side.value == "buy":
            if current <= 1e-12:
                opened_at[fill.symbol] = fill.timestamp
            quantities[fill.symbol] = current + fill.qty
            continue
        remaining = max(0.0, current - fill.qty)
        quantities[fill.symbol] = remaining
        if remaining <= 1e-12 and fill.symbol in opened_at:
            durations.append((fill.timestamp - opened_at.pop(fill.symbol)).total_seconds() / 3600)
    return sum(durations) / len(durations) if durations else 0.0


def _flat_monthly(result: PortfolioBacktestResult) -> dict[tuple[int, int], float]:
    month_end: dict[tuple[int, int], float] = {}
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        month_end[(timestamp.year, timestamp.month)] = equity
    monthly: dict[tuple[int, int], float] = {}
    previous = result.starting_equity
    for month_key in sorted(month_end):
        value = month_end[month_key]
        monthly[month_key] = value / previous - 1 if previous > 0 else 0.0
        previous = value
    return monthly


def _monthly_matrix(result: PortfolioBacktestResult) -> dict[int, dict[int, float]]:
    matrix: dict[int, dict[int, float]] = {}
    for (year, month), value in _flat_monthly(result).items():
        matrix.setdefault(year, {})[month] = value
    return matrix


def _yearly_returns(result: PortfolioBacktestResult) -> tuple[tuple[int, float, float | None], ...]:
    by_year: dict[int, list[float]] = {}
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve, strict=True):
        by_year.setdefault(timestamp.year, []).append(equity)
    benchmark_yearly = _benchmark_yearly_returns(result.benchmark_bars)
    yearly: list[tuple[int, float, float | None]] = []
    previous = result.starting_equity
    for year in sorted(by_year):
        values = by_year[year]
        yearly.append(
            (
                year,
                values[-1] / previous - 1 if previous > 0 else 0.0,
                benchmark_yearly.get(year),
            )
        )
        previous = values[-1]
    return tuple(yearly)


def _benchmark_monthly_returns(benchmark_bars: tuple[Bar, ...]) -> dict[tuple[int, int], float]:
    if not benchmark_bars:
        return {}
    ordered = sorted(benchmark_bars, key=lambda bar: bar.timestamp)
    by_month: dict[tuple[int, int], list[float]] = {}
    for bar in ordered:
        by_month.setdefault((bar.timestamp.year, bar.timestamp.month), []).append(bar.close)
    returns: dict[tuple[int, int], float] = {}
    previous: float | None = None
    for month_key in sorted(by_month):
        closes = by_month[month_key]
        if previous is not None and previous > 0:
            returns[month_key] = closes[-1] / previous - 1
        previous = closes[-1]
    return returns


def _rolling_beta(result: PortfolioBacktestResult) -> tuple[float, ...]:
    benchmark_bars = sorted(result.benchmark_bars, key=lambda bar: bar.timestamp)
    benchmark_by_date: dict[datetime, float] = {bar.timestamp: bar.close for bar in benchmark_bars}
    portfolio_by_date: dict[datetime, float] = dict(zip(result.curve_timestamps, result.equity_curve, strict=True))
    common_dates = sorted(set(portfolio_by_date) & set(benchmark_by_date))
    # Build (portfolio, benchmark) pairs jointly: filtering the two series
    # independently desynchronizes them when only one leg has a non-positive
    # base value, corrupting (or crashing) the window slices below.
    aligned_returns = [
        (
            portfolio_by_date[common_dates[index]] / portfolio_by_date[common_dates[index - 1]] - 1,
            benchmark_by_date[common_dates[index]] / benchmark_by_date[common_dates[index - 1]] - 1,
        )
        for index in range(1, len(common_dates))
        if portfolio_by_date[common_dates[index - 1]] > 0 and benchmark_by_date[common_dates[index - 1]] > 0
    ]
    portfolio_returns = [pair[0] for pair in aligned_returns]
    benchmark_returns = [pair[1] for pair in aligned_returns]
    if len(portfolio_returns) < ROLLING_BETA_WINDOW_DAYS:
        return ()
    betas: list[float] = []
    for index in range(ROLLING_BETA_WINDOW_DAYS, len(portfolio_returns) + 1):
        portfolio_window = portfolio_returns[index - ROLLING_BETA_WINDOW_DAYS : index]
        benchmark_window = benchmark_returns[index - ROLLING_BETA_WINDOW_DAYS : index]
        mean_portfolio = sum(portfolio_window) / len(portfolio_window)
        mean_benchmark = sum(benchmark_window) / len(benchmark_window)
        covariance = sum(
            (p - mean_portfolio) * (b - mean_benchmark) for p, b in zip(portfolio_window, benchmark_window, strict=True)
        ) / len(portfolio_window)
        benchmark_variance = sum((value - mean_benchmark) ** 2 for value in benchmark_window) / len(benchmark_window)
        if benchmark_variance > 0:
            betas.append(covariance / benchmark_variance)
    return tuple(betas)


__all__ = [
    "MONTH_NAMES",
    "PortfolioReport",
    "build_portfolio_report",
]
