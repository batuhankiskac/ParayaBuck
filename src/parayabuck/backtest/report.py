from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.trades import TradeRecord
from parayabuck.core.models import Bar, Market

MONTH_NAMES = [
    "Oca",
    "Şub",
    "Mar",
    "Nis",
    "May",
    "Haz",
    "Tem",
    "Ağu",
    "Eyl",
    "Eki",
    "Kas",
    "Ara",
]


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


@dataclass(frozen=True)
class YearPerformance:
    year: int
    return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    trades: int
    win_rate: float
    max_drawdown: float


@dataclass(frozen=True)
class SymbolPerformance:
    symbol: str
    market: Market
    trades: int
    wins: int
    gross_profit: float
    gross_loss: float
    net_pnl: float
    win_rate: float
    average_holding_days: float
    average_mae_pct: float
    average_mfe_pct: float
    stop_exits: int
    target_exits: int


@dataclass(frozen=True)
class DrawdownPoint:
    timestamp: datetime
    equity: float
    peak_equity: float
    drawdown_pct: float


@dataclass(frozen=True)
class ScoreDecilePerformance:
    strategy: str
    decile: int
    minimum_score: float
    maximum_score: float
    trades: int
    expectancy: float
    profit_factor: float | None


@dataclass(frozen=True)
class RegimePerformance:
    strategy: str
    regime: str
    trades: int
    net_pnl: float
    expectancy: float
    profit_factor: float | None


@dataclass(frozen=True)
class BacktestReport:
    summary: dict[str, object]
    period: dict[str, object]
    yearly: tuple[YearPerformance, ...]
    monthly: dict[int, dict[int, float]]
    by_symbol: tuple[SymbolPerformance, ...]
    trades: tuple[TradeRecord, ...]
    drawdown_series: tuple[DrawdownPoint, ...]
    equity_curve: tuple[tuple[str, float], ...]
    monte_carlo: dict[str, object] | None
    score_deciles: tuple[ScoreDecilePerformance, ...]
    score_expectancy_monotonic: dict[str, bool]
    by_regime: tuple[RegimePerformance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "period": self.period,
            "yearly": [asdict(item) for item in self.yearly],
            "monthly": {str(year): months for year, months in self.monthly.items()},
            "by_symbol": [asdict(item) for item in self.by_symbol],
            "trades": [trade.to_dict() for trade in self.trades],
            "drawdown_series": [
                {
                    "timestamp": point.timestamp.isoformat(),
                    "equity": point.equity,
                    "peak_equity": point.peak_equity,
                    "drawdown_pct": point.drawdown_pct,
                }
                for point in self.drawdown_series
            ],
            "equity_curve": [
                {"timestamp": timestamp, "equity": equity}
                for timestamp, equity in self.equity_curve
            ],
            "monte_carlo": self.monte_carlo,
            "score_deciles": [asdict(item) for item in self.score_deciles],
            "score_expectancy_monotonic": self.score_expectancy_monotonic,
            "by_regime": [asdict(item) for item in self.by_regime],
        }

    def write_csvs(self, output_dir: str) -> list[str]:
        import csv
        from pathlib import Path

        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for key, value in self.summary.items():
                writer.writerow([key, value])
        written.append(str(directory / "summary.csv"))
        with (directory / "yearly_returns.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "year",
                    "return_pct",
                    "benchmark_return_pct",
                    "excess_pct",
                    "trades",
                    "win_rate",
                    "max_drawdown",
                ]
            )
            for year_item in self.yearly:
                writer.writerow(
                    [
                        year_item.year,
                        year_item.return_pct,
                        year_item.benchmark_return_pct,
                        year_item.excess_return_pct,
                        year_item.trades,
                        year_item.win_rate,
                        year_item.max_drawdown,
                    ]
                )
        written.append(str(directory / "yearly_returns.csv"))
        with (directory / "monthly_returns.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["year", *MONTH_NAMES])
            for year in sorted(self.monthly):
                writer.writerow(
                    [year, *[self.monthly[year].get(month, "") for month in range(1, 13)]]
                )
        written.append(str(directory / "monthly_returns.csv"))
        with (directory / "by_symbol.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "symbol",
                    "market",
                    "trades",
                    "wins",
                    "win_rate",
                    "net_pnl",
                    "gross_profit",
                    "gross_loss",
                    "avg_holding_days",
                    "avg_mae_pct",
                    "avg_mfe_pct",
                    "stop_exits",
                    "target_exits",
                ]
            )
            for symbol_item in self.by_symbol:
                writer.writerow(
                    [
                        symbol_item.symbol,
                        symbol_item.market.value,
                        symbol_item.trades,
                        symbol_item.wins,
                        symbol_item.win_rate,
                        symbol_item.net_pnl,
                        symbol_item.gross_profit,
                        symbol_item.gross_loss,
                        symbol_item.average_holding_days,
                        symbol_item.average_mae_pct,
                        symbol_item.average_mfe_pct,
                        symbol_item.stop_exits,
                        symbol_item.target_exits,
                    ]
                )
        written.append(str(directory / "by_symbol.csv"))
        with (directory / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(self.trades[0].to_dict().keys()) if self.trades else ["no_trades"])
            for trade in self.trades:
                writer.writerow(list(trade.to_dict().values()))
        written.append(str(directory / "trades.csv"))
        with (directory / "equity_curve.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "equity"])
            writer.writerows(self.equity_curve)
        written.append(str(directory / "equity_curve.csv"))
        with (directory / "drawdowns.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "equity", "peak_equity", "drawdown_pct"])
            for point in self.drawdown_series:
                writer.writerow(
                    [
                        point.timestamp.isoformat(),
                        point.equity,
                        point.peak_equity,
                        point.drawdown_pct,
                    ]
                )
        written.append(str(directory / "drawdowns.csv"))
        with (directory / "score_deciles.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "strategy",
                    "decile",
                    "minimum_score",
                    "maximum_score",
                    "trades",
                    "expectancy",
                    "profit_factor",
                ]
            )
            for item in self.score_deciles:
                writer.writerow(
                    [
                        item.strategy,
                        item.decile,
                        item.minimum_score,
                        item.maximum_score,
                        item.trades,
                        item.expectancy,
                        item.profit_factor,
                    ]
                )
        written.append(str(directory / "score_deciles.csv"))
        with (directory / "by_regime.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["strategy", "regime", "trades", "net_pnl", "expectancy", "profit_factor"]
            )
            for regime_item in self.by_regime:
                writer.writerow(
                    [
                        regime_item.strategy,
                        regime_item.regime,
                        regime_item.trades,
                        regime_item.net_pnl,
                        regime_item.expectancy,
                        regime_item.profit_factor,
                    ]
                )
        written.append(str(directory / "by_regime.csv"))
        return written

    def render_text(self) -> str:
        lines: list[str] = []
        summary = self.summary

        def pct(key: str) -> float:
            return _as_float(summary.get(key)) * 100

        lines.append("=" * 78)
        lines.append("PARAYABUCK BACKTEST RAPORU")
        lines.append("=" * 78)
        lines.append(f"Periyot: {summary.get('start_date', '')} -> {summary.get('end_date', '')}")
        lines.append(
            f"  Süre: {summary.get('duration_days', 0)} gün  "
            f"| Sembol: {self.period.get('symbols_count', 0)}  "
            f"| Timeframe: {self.period.get('timeframe', '')}"
        )
        lines.append("")
        lines.append("-- ÖZET METRİKLER --")
        lines.append(
            f"  Başlangıç    : {summary.get('starting_equity', 0):,.2f}"
            f"  Bitiş: {summary.get('ending_equity', 0):,.2f}"
        )
        lines.append(f"  Toplam Getiri: {pct('total_return'):.2f}%   CAGR: {pct('cagr'):.2f}%")
        lines.append(
            f"  Benchmark    : {pct('benchmark_return'):.2f}%   Excess: {pct('excess_return'):.2f}%"
        )
        lines.append(
            f"  Max Drawdown : {pct('max_drawdown'):.2f}%   "
            f"({summary.get('max_drawdown_duration_days') or 0} gün)"
        )
        lines.append(
            f"  Sharpe       : {summary.get('sharpe', 0):.2f}   "
            f"Sortino: {summary.get('sortino', 0):.2f}   Calmar: {summary.get('calmar', 0):.2f}"
        )
        lines.append(
            f"  Volatilite   : {pct('annualized_volatility'):.2f}% (yıllık)   "
            f"İşlem/Yıl: {summary.get('trades_per_year', 0):.1f}"
        )
        lines.append("")
        lines.append("-- İŞLEM İSTATİSTİKLERİ --")
        lines.append(
            f"  Toplam İşlem : {summary.get('total_trades', 0)}   "
            f"Kazanma Oranı: {pct('win_rate'):.2f}%"
        )
        profit_factor = summary.get("profit_factor")
        profit_factor_text = f"{profit_factor:.2f}" if profit_factor is not None else "-"
        lines.append(
            f"  Profit Factor: {profit_factor_text}   "
            f"Expectancy: {summary.get('expectancy', 0):.2f}"
        )
        lines.append(
            f"  Avg Kazanç   : {summary.get('average_win', 0):,.2f}   "
            f"Avg Kayıp: {summary.get('average_loss', 0):,.2f}   "
            f"Payoff: {summary.get('payoff_ratio', 0):.2f}"
        )
        lines.append(
            f"  En İyi İşlem : {summary.get('best_trade_pnl', 0):,.2f}   "
            f"En Kötü: {summary.get('worst_trade_pnl', 0):,.2f}"
        )
        lines.append(
            f"  Max Ardışık Kazanç: {summary.get('max_consecutive_wins', 0)}   "
            f"Max Ardışık Kayıp: {summary.get('max_consecutive_losses', 0)}"
        )
        lines.append(
            f"  Ort. Holding : {summary.get('average_holding_days', 0):.1f} gün   "
            f"Medyan: {summary.get('median_holding_days', 0):.1f} gün"
        )
        lines.append("")
        lines.append("-- YILLIK PERFORMANS --")
        lines.append(
            f"  {'Yıl':<6}{'Getiri':>10}{'Benchmark':>12}{'Excess':>10}"
            f"{'İşlem':>8}{'Kazanma%':>10}{'MaxDD':>10}"
        )
        for year_item in self.yearly:
            lines.append(
                f"  {year_item.year:<6}{year_item.return_pct * 100:>9.2f}%"
                f"{year_item.benchmark_return_pct * 100:>11.2f}%"
                f"{year_item.excess_return_pct * 100:>9.2f}%{year_item.trades:>8}"
                f"{year_item.win_rate * 100:>9.2f}%{year_item.max_drawdown * 100:>9.2f}%"
            )
        lines.append("")
        lines.append("-- AYLIK GETİRİ MATRİSİ (%) --")
        header = "  " + "".join(f"{name:>7}" for name in MONTH_NAMES) + f"{'Yıl':>9}"
        lines.append(header)
        for year in sorted(self.monthly):
            row = [self.monthly[year].get(month) for month in range(1, 13)]
            annual = 1.0
            for value in row:
                if value is not None:
                    annual *= 1 + value
            cells = "".join(
                f"{value * 100:>7.1f}" if value is not None else f"{'-':>7}" for value in row
            )
            lines.append(f"  {cells}{(annual - 1) * 100:>8.1f}%")
        lines.append("")
        lines.append("-- SEMBOL BAZINDA DAĞILIM --")
        lines.append(
            f"  {'Sembol':<12}{'Piyasa':<8}{'İşlem':>7}{'Kazanma%':>10}{'Net PnL':>14}"
            f"{'Ort. Holding':>14}{'Stop':>7}{'Target':>8}"
        )
        for symbol_item in sorted(self.by_symbol, key=lambda entry: entry.net_pnl, reverse=True):
            lines.append(
                f"  {symbol_item.symbol:<12}{symbol_item.market.value:<8}{symbol_item.trades:>7}"
                f"{symbol_item.win_rate * 100:>9.2f}%"
                f"{symbol_item.net_pnl:>14,.2f}{symbol_item.average_holding_days:>13.1f}g"
                f"{symbol_item.stop_exits:>7}{symbol_item.target_exits:>8}"
            )
        if self.monte_carlo:
            lines.append("")
            lines.append("-- MONTE CARLO (trade sıralaması riski) --")
            monte = self.monte_carlo
            median_max_dd = _as_float(monte.get("median_max_drawdown")) * 100
            worst_max_dd = _as_float(monte.get("worst_5pct_max_drawdown")) * 100
            lines.append(
                f"  Medyan Bitiş  : {monte.get('median_ending_equity', 0):,.2f}   "
                f"Kötü %5 : {monte.get('worst_5pct_ending_equity', 0):,.2f}"
            )
            lines.append(f"  Medyan MaxDD  : {median_max_dd:.2f}%   Kötü %5 : {worst_max_dd:.2f}%")
        lines.append("")
        lines.append("=" * 78)
        return "\n".join(lines)


def build_report(
    result: BacktestResult,
    *,
    curve_timestamps: list[datetime],
    trades: tuple[TradeRecord, ...],
    benchmark_bars: list[Bar] | None = None,
    timeframe: str = "",
    symbols: list[str] | None = None,
    monte_carlo: dict[str, object] | None = None,
) -> BacktestReport:
    curve = list(result.equity_curve)
    timestamps = list(curve_timestamps)
    if len(curve) != len(timestamps):
        trimmed = min(len(curve), len(timestamps))
        curve = curve[:trimmed]
        timestamps = timestamps[:trimmed]
    starting = result.starting_equity
    yearly = _yearly_performance(curve, timestamps, benchmark_bars, starting, trades)
    monthly = _monthly_returns(curve, timestamps, starting)
    drawdowns = _drawdown_series(curve, timestamps)
    summary = asdict(result)
    summary.pop("equity_curve")
    summary.pop("realized_pnls")
    summary["benchmark_symbol"] = getattr(result, "benchmark_symbol", "") or (
        benchmark_bars[0].symbol if benchmark_bars else ""
    )
    traded_symbols = {trade.symbol for trade in trades}
    if symbols:
        traded_symbols.update(symbols)
    return BacktestReport(
        summary=summary,
        period={
            "start": summary.get("start_date", ""),
            "end": summary.get("end_date", ""),
            "timeframe": timeframe,
            "symbols_count": len(traded_symbols),
        },
        yearly=yearly,
        monthly=monthly,
        by_symbol=_symbol_performance(trades),
        trades=trades,
        drawdown_series=drawdowns,
        equity_curve=tuple(
            (ts.isoformat(), equity) for ts, equity in zip(timestamps, curve, strict=True)
        ),
        monte_carlo=monte_carlo,
        score_deciles=_score_decile_performance(trades),
        score_expectancy_monotonic=_score_monotonicity(trades),
        by_regime=_regime_performance(trades),
    )


def _yearly_performance(
    curve: list[float],
    timestamps: list[datetime],
    benchmark_bars: list[Bar] | None,
    starting_equity: float,
    trades: tuple[TradeRecord, ...],
) -> tuple[YearPerformance, ...]:
    by_year: dict[int, list[int]] = {}
    for index, timestamp in enumerate(timestamps):
        by_year.setdefault(timestamp.year, []).append(index)
    trades_by_year: dict[int, list[float]] = {}
    for trade in trades:
        trades_by_year.setdefault(trade.exit_time.year, []).append(trade.pnl)
    benchmark_years = _benchmark_yearly_returns(benchmark_bars)
    yearly: list[YearPerformance] = []
    previous_year_equity = starting_equity
    for year in sorted(by_year):
        indices = by_year[year]
        year_curve = [curve[index] for index in indices]
        year_end = year_curve[-1]
        return_pct = year_end / previous_year_equity - 1 if previous_year_equity > 0 else 0.0
        peak = previous_year_equity
        max_drawdown = 0.0
        for equity in year_curve:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
        year_pnls = trades_by_year.get(year, [])
        wins = sum(1 for pnl in year_pnls if pnl > 0)
        benchmark = benchmark_years.get(year)
        yearly.append(
            YearPerformance(
                year=year,
                return_pct=return_pct,
                benchmark_return_pct=benchmark or 0.0,
                excess_return_pct=return_pct - (benchmark or 0.0),
                trades=len(year_pnls),
                win_rate=wins / len(year_pnls) if year_pnls else 0.0,
                max_drawdown=max_drawdown,
            )
        )
        previous_year_equity = year_end
    return tuple(yearly)


def _benchmark_yearly_returns(benchmark_bars: Sequence[Bar] | None) -> dict[int, float]:
    if not benchmark_bars:
        return {}
    ordered = sorted(benchmark_bars, key=lambda bar: bar.timestamp)
    by_year: dict[int, list[float]] = {}
    for bar in ordered:
        by_year.setdefault(bar.timestamp.year, []).append(bar.close)
    returns: dict[int, float] = {}
    previous_close: float | None = None
    for year in sorted(by_year):
        closes = by_year[year]
        anchor = previous_close if previous_close is not None else closes[0]
        returns[year] = closes[-1] / anchor - 1 if anchor > 0 else 0.0
        previous_close = closes[-1]
    return returns


def _monthly_returns(
    curve: list[float],
    timestamps: list[datetime],
    starting_equity: float,
) -> dict[int, dict[int, float]]:
    buckets: dict[tuple[int, int], list[int]] = {}
    for index, timestamp in enumerate(timestamps):
        buckets.setdefault((timestamp.year, timestamp.month), []).append(index)
    monthly: dict[int, dict[int, float]] = {}
    previous_equity = starting_equity
    for (year, month), indices in sorted(buckets.items()):
        month_equity = curve[indices[-1]]
        monthly.setdefault(year, {})[month] = month_equity / previous_equity - 1
        previous_equity = month_equity
    return monthly


def _drawdown_series(curve: list[float], timestamps: list[datetime]) -> tuple[DrawdownPoint, ...]:
    peak = curve[0] if curve else 0.0
    points: list[DrawdownPoint] = []
    for timestamp, equity in zip(timestamps, curve, strict=True):
        peak = max(peak, equity)
        points.append(
            DrawdownPoint(
                timestamp=timestamp.astimezone(UTC),
                equity=equity,
                peak_equity=peak,
                drawdown_pct=(peak - equity) / peak if peak > 0 else 0.0,
            )
        )
    return tuple(points)


def _symbol_performance(trades: tuple[TradeRecord, ...]) -> tuple[SymbolPerformance, ...]:
    grouped: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        grouped.setdefault(trade.symbol, []).append(trade)
    results: list[SymbolPerformance] = []
    for symbol in sorted(grouped):
        symbol_trades = grouped[symbol]
        pnls = [trade.pnl for trade in symbol_trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        holding = [trade.holding_days for trade in symbol_trades]
        results.append(
            SymbolPerformance(
                symbol=symbol,
                market=symbol_trades[0].market,
                trades=len(symbol_trades),
                wins=len(wins),
                gross_profit=sum(wins),
                gross_loss=abs(sum(losses)),
                net_pnl=sum(pnls),
                win_rate=len(wins) / len(pnls) if pnls else 0.0,
                average_holding_days=sum(holding) / len(holding) if holding else 0.0,
                average_mae_pct=sum(trade.mae_pct for trade in symbol_trades) / len(symbol_trades),
                average_mfe_pct=sum(trade.mfe_pct for trade in symbol_trades) / len(symbol_trades),
                stop_exits=sum(
                    1
                    for trade in symbol_trades
                    if trade.exit_reason in {"stop", "break_even", "trailing_stop"}
                ),
                target_exits=sum(1 for trade in symbol_trades if trade.exit_reason == "target"),
            )
        )
    return tuple(results)


def _score_decile_performance(
    trades: tuple[TradeRecord, ...],
) -> tuple[ScoreDecilePerformance, ...]:
    grouped: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        grouped.setdefault(trade.strategy, []).append(trade)
    results: list[ScoreDecilePerformance] = []
    for strategy, strategy_trades in sorted(grouped.items()):
        ordered = sorted(strategy_trades, key=lambda trade: trade.entry_score)
        bucket_count = min(10, len(ordered))
        if bucket_count == 0:
            continue
        buckets: list[list[TradeRecord]] = [[] for _ in range(bucket_count)]
        for index, trade in enumerate(ordered):
            bucket_index = min(bucket_count - 1, index * bucket_count // len(ordered))
            buckets[bucket_index].append(trade)
        for index, bucket_trades in enumerate(buckets, start=1):
            pnls = [trade.pnl for trade in bucket_trades]
            gross_profit = sum(pnl for pnl in pnls if pnl > 0)
            gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
            results.append(
                ScoreDecilePerformance(
                    strategy,
                    index,
                    min(trade.entry_score for trade in bucket_trades),
                    max(trade.entry_score for trade in bucket_trades),
                    len(bucket_trades),
                    sum(pnls) / len(pnls),
                    gross_profit / gross_loss if gross_loss > 0 else None,
                )
            )
    return tuple(results)


def _score_monotonicity(trades: tuple[TradeRecord, ...]) -> dict[str, bool]:
    deciles = _score_decile_performance(trades)
    grouped: dict[str, list[ScoreDecilePerformance]] = {}
    for item in deciles:
        grouped.setdefault(item.strategy, []).append(item)
    return {
        strategy: all(
            later.expectancy >= earlier.expectancy
            for earlier, later in zip(items, items[1:])
        )
        for strategy, items in grouped.items()
    }


def _regime_performance(trades: tuple[TradeRecord, ...]) -> tuple[RegimePerformance, ...]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for trade in trades:
        grouped.setdefault((trade.strategy, trade.entry_regime.value), []).append(trade.pnl)
    results: list[RegimePerformance] = []
    for (strategy, regime), pnls in sorted(grouped.items()):
        gross_profit = sum(pnl for pnl in pnls if pnl > 0)
        gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
        results.append(
            RegimePerformance(
                strategy,
                regime,
                len(pnls),
                sum(pnls),
                sum(pnls) / len(pnls),
                gross_profit / gross_loss if gross_loss > 0 else None,
            )
        )
    return tuple(results)


__all__ = [
    "BacktestReport",
    "DrawdownPoint",
    "SymbolPerformance",
    "ScoreDecilePerformance",
    "RegimePerformance",
    "YearPerformance",
    "build_report",
]
