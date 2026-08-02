from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.sweep import (
    SweepPoint,
    render_sweep_text,
    run_sweep,
    write_sweep_csv,
)
from parayabuck.config import EngineConfig
from parayabuck.core.models import Bar, Market


def _result(
    *,
    cagr: float = 0.1,
    max_drawdown: float = 0.2,
    total_trades: int = 15,
    total_return: float = 0.5,
) -> BacktestResult:
    return BacktestResult(
        starting_equity=100_000.0,
        ending_equity=150_000.0,
        total_return=total_return,
        benchmark_return=0.1,
        excess_return=total_return - 0.1,
        max_drawdown=max_drawdown,
        total_trades=total_trades,
        win_rate=0.5,
        profit_factor=1.5,
        profit_factor_is_infinite=False,
        expectancy=100.0,
        sharpe=1.0,
        sortino=1.2,
        equity_curve=(100_000.0, 150_000.0),
        realized_pnls=(100.0,) * total_trades,
        open_positions=0,
        unrealized_pnl=0.0,
        open_risk=0.0,
        liquidated_at_end=False,
        cagr=cagr,
    )


def _bars(count: int = 100) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Bar(
            timestamp=start,
            symbol="BTC/USD",
            timeframe="1d",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
            market=Market.CRYPTO,
        )
        for _ in range(count)
    ]


def test_run_sweep_ranks_by_calmar() -> None:
    config = EngineConfig()
    bars = _bars()

    def factory(cfg: EngineConfig) -> Any:
        result = _result(
            cagr=cfg.minimum_signal_score / 100,
            max_drawdown=0.1,
            total_trades=12,
        )
        return type("Engine", (), {"run": lambda self, _bars: result})()

    points = run_sweep(
        bars,
        config,
        factory,
        scores=(55.0, 65.0, 75.0),
        risks=(0.005,),
        positions=(0.10,),
    )
    assert len(points) == 3
    assert [point.minimum_signal_score for point in points] == [75.0, 65.0, 55.0]
    assert points[0].calmar == pytest.approx(0.75 / 0.1)
    assert points[0].result.total_trades == 12


def test_run_sweep_config_override_reaches_engine() -> None:
    config = EngineConfig(minimum_signal_score=50.0)
    bars = _bars()
    seen: list[tuple[float, float, float]] = []

    def factory(cfg: EngineConfig) -> Any:
        seen.append(
            (
                cfg.minimum_signal_score,
                cfg.risk.risk_per_trade_pct,
                cfg.risk.max_position_pct,
            )
        )
        return type("Engine", (), {"run": lambda self, _bars: _result(total_trades=20)})()

    run_sweep(bars, config, factory, scores=(60.0,), risks=(0.003,), positions=(0.07,))
    assert seen == [(60.0, 0.003, 0.07)]


def test_run_sweep_filters_below_min_trades() -> None:
    config = EngineConfig()
    bars = _bars()

    def factory(cfg: EngineConfig) -> Any:
        trades = 8 if cfg.minimum_signal_score > 60.0 else 20
        return type("Engine", (), {"run": lambda self, _bars: _result(total_trades=trades)})()

    points = run_sweep(
        bars,
        config,
        factory,
        scores=(55.0, 65.0, 75.0),
        risks=(0.005,),
        positions=(0.10,),
        min_trades=10,
    )
    assert [point.minimum_signal_score for point in points] == [55.0]


def test_run_sweep_rejects_nonpositive_min_trades() -> None:
    with pytest.raises(ValueError, match="min_trades"):
        run_sweep(_bars(), EngineConfig(), lambda cfg: None, min_trades=0)


def test_write_sweep_csv_and_render(tmp_path: Path) -> None:
    points = [
        SweepPoint(75.0, 0.005, 0.10, _result(cagr=0.3, max_drawdown=0.15, total_trades=20)),
        SweepPoint(65.0, 0.005, 0.10, _result(cagr=0.2, max_drawdown=0.15, total_trades=12)),
    ]
    path = write_sweep_csv(points, str(tmp_path))
    assert Path(path).name == "sweep.csv"
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("minimum_signal_score")
    assert len(lines) == 3  # header + 2 points

    text = render_sweep_text(points)
    assert "75" in text and "0.0050" in text


def test_render_sweep_text_without_points() -> None:
    assert "kombinasyon" in render_sweep_text([])


def test_sweep_point_calmar_without_drawdown() -> None:
    point = SweepPoint(70.0, 0.005, 0.10, _result(cagr=0.25, max_drawdown=0.0))
    assert point.calmar == pytest.approx(0.25)
