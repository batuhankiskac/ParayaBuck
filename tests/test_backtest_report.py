from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.report import build_report
from parayabuck.backtest.trades import TradeRecord
from parayabuck.core.models import Bar, Direction, Market
from parayabuck.signals.calibration import fit_score_calibration


def _result() -> BacktestResult:
    return BacktestResult(
        starting_equity=100_000.0,
        ending_equity=130_000.0,
        total_return=0.30,
        benchmark_return=0.20,
        excess_return=0.10,
        max_drawdown=0.10,
        total_trades=6,
        win_rate=0.5,
        profit_factor=1.8,
        profit_factor_is_infinite=False,
        expectancy=5_000.0,
        sharpe=1.2,
        sortino=1.5,
        equity_curve=(100_000.0, 90_000.0, 105_000.0, 110_000.0, 120_000.0, 125_000.0, 130_000.0),
        realized_pnls=(5_000.0, -7_000.0, 12_000.0, 10_000.0, 5_000.0, 5_000.0),
        open_positions=0,
        unrealized_pnl=0.0,
        open_risk=0.0,
        liquidated_at_end=False,
    )


def _timestamps() -> list[datetime]:
    return [
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 2, 1, tzinfo=UTC),
        datetime(2024, 3, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        datetime(2025, 2, 3, tzinfo=UTC),
        datetime(2025, 3, 3, tzinfo=UTC),
        datetime(2025, 4, 1, tzinfo=UTC),
    ]


def _trade(exit_year: int, pnl: float, reason: str = "target") -> TradeRecord:
    return TradeRecord(
        client_order_id=f"order-{exit_year}-{pnl}",
        symbol="AAPL",
        market=Market.EQUITY,
        strategy="equity_etf_dual_momentum_v1",
        direction=Direction.LONG,
        qty=10.0,
        entry_time=datetime(exit_year - 1, 6, 1, tzinfo=UTC),
        exit_time=datetime(exit_year, 3, 1, tzinfo=UTC),
        entry_price=100.0,
        exit_price=100.0 + pnl / 10,
        pnl=pnl,
        return_pct=pnl / 1000,
        mae_pct=0.02,
        mfe_pct=0.05,
        exit_reason=reason,
        fees=1.0,
    )


def test_report_monthly_returns_compound_to_total() -> None:
    report = build_report(_result(), curve_timestamps=_timestamps(), trades=())
    compound = 1.0
    for months in report.monthly.values():
        for value in months.values():
            compound *= 1 + value
    assert compound - 1 == pytest.approx(0.30)


def test_report_yearly_returns_chain_correctly() -> None:
    report = build_report(_result(), curve_timestamps=_timestamps(), trades=())
    years = {item.year: item for item in report.yearly}
    assert set(years) == {2024, 2025}
    chained = 1.0
    for year in (2024, 2025):
        chained *= 1 + years[year].return_pct
    assert chained - 1 == pytest.approx(0.30)
    assert years[2024].max_drawdown == pytest.approx(0.10, abs=1e-3)


def test_report_year_trades_and_benchmark() -> None:
    benchmark = [
        Bar(datetime(2024, 1, 2, tzinfo=UTC), "SPY", "1d", 100, 100, 100, 100),
        Bar(datetime(2024, 12, 31, tzinfo=UTC), "SPY", "1d", 120, 120, 120, 120),
        Bar(datetime(2025, 12, 31, tzinfo=UTC), "SPY", "1d", 108, 108, 108, 108),
    ]
    report = build_report(
        _result(),
        curve_timestamps=_timestamps(),
        trades=(_trade(2024, 1_000), _trade(2024, -500), _trade(2025, 2_000)),
        benchmark_bars=benchmark,
    )
    years = {item.year: item for item in report.yearly}
    assert years[2024].trades == 2
    assert years[2024].win_rate == 0.5
    assert years[2024].benchmark_return_pct == pytest.approx(0.20)
    assert years[2025].benchmark_return_pct == pytest.approx(-0.10)
    assert report.summary["benchmark_symbol"] == "SPY"


def test_report_by_symbol_breakdown() -> None:
    report = build_report(
        _result(),
        curve_timestamps=_timestamps(),
        trades=(
            _trade(2024, 1_000, reason="stop"),
            _trade(2024, -500, reason="stop"),
            _trade(2025, 2_000, reason="target"),
        ),
    )
    assert len(report.by_symbol) == 1
    symbol = report.by_symbol[0]
    assert symbol.symbol == "AAPL"
    assert symbol.trades == 3
    assert symbol.net_pnl == 2_500.0
    assert symbol.win_rate == pytest.approx(2 / 3)
    assert symbol.stop_exits == 2
    assert symbol.target_exits == 1


def test_report_includes_strategy_score_deciles_and_regimes() -> None:
    trades = tuple(
        replace(_trade(2025, float(index - 5)), entry_score=50 + index) for index in range(1, 11)
    )
    report = build_report(_result(), curve_timestamps=_timestamps(), trades=trades)
    assert len(report.score_deciles) == 10
    assert "equity_etf_dual_momentum_v1" in report.score_expectancy_monotonic
    assert report.by_regime[0].strategy == "equity_etf_dual_momentum_v1"


def test_score_calibration_uses_strategy_internal_expected_r() -> None:
    trades = tuple(
        replace(
            _trade(2025, float(index - 10)),
            entry_score=50 + index,
            initial_risk=1.0,
            qty=1.0,
        )
        for index in range(20)
    )
    calibration = fit_score_calibration(
        trades,
        strategy="equity_etf_dual_momentum_v1",
        bins=5,
        minimum_observations=20,
    )
    assert calibration.monotonic
    high = calibration.expected_r(70)
    low = calibration.expected_r(50)
    assert high is not None and low is not None and high > low


def test_report_drawdown_series_and_equity_curve() -> None:
    report = build_report(_result(), curve_timestamps=_timestamps(), trades=())
    assert len(report.drawdown_series) == 7
    assert report.drawdown_series[-1].equity == 130_000.0
    assert report.equity_curve[0] == ("2024-01-02T00:00:00+00:00", 100_000.0)
    worst = max(point.drawdown_pct for point in report.drawdown_series)
    assert worst == pytest.approx(0.10)


def test_report_write_csvs(tmp_path: object) -> None:
    import os

    report = build_report(
        _result(),
        curve_timestamps=_timestamps(),
        trades=(_trade(2024, 1_000), _trade(2024, -500)),
    )
    directory = str(tmp_path)
    written = report.write_csvs(directory)
    names = {os.path.basename(path) for path in written}
    assert {
        "summary.csv",
        "yearly_returns.csv",
        "monthly_returns.csv",
        "by_symbol.csv",
        "trades.csv",
        "equity_curve.csv",
        "drawdowns.csv",
    } <= names
    trades_csv = os.path.join(directory, "trades.csv")
    with open(trades_csv) as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 3  # header + 2 trades
