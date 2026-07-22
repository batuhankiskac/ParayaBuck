"""Unit tests for the walk-forward optimizer."""
from __future__ import annotations

from parayabuck.backtest.optimize import ParamCombo, WalkForwardResult, _grid_combos


def test_grid_combos_expands_cartesian_product():
    grid = {"fast": [5, 9], "slow": [21, 50]}
    combos = _grid_combos(grid)
    assert len(combos) == 4
    assert {"fast": 5, "slow": 21} in combos
    assert {"fast": 9, "slow": 50} in combos


def test_grid_combos_empty_grid_returns_single_empty():
    combos = _grid_combos({})
    assert combos == [{}]


def test_grid_combos_single_param():
    combos = _grid_combos({"x": [1, 2, 3]})
    assert len(combos) == 3
    assert {"x": 1} in combos


def test_param_combo_defaults():
    c = ParamCombo(params={"a": 1})
    assert c.in_sample_result is None
    assert c.out_of_sample_result is None
    assert c.in_sample_pnl == 0.0
    assert c.out_of_sample_pnl == 0.0
    assert c.in_sample_sharpe == 0.0


def test_walk_forward_result_summary_renders():
    wf = WalkForwardResult(
        strategy_name="ema_crossover",
        symbol="SPY",
        timeframe="15m",
        train_fraction=0.6,
    )
    combo1 = ParamCombo(params={"fast": 5})
    combo1.in_sample_result = in_sample_pnl_placeholder()
    combo2 = ParamCombo(params={"fast": 9})
    combo2.in_sample_result = in_sample_pnl_placeholder(200.0)
    wf.combinations = [combo1, combo2]
    text = wf.summary()
    assert "WALK-FORWARD OPTIMIZATION RESULTS" in text
    assert "ema_crossover" in text


def in_sample_pnl_placeholder(pnl: float = 100.0):
    """Build a minimal BacktestResult stub for the summary rendering."""
    from datetime import UTC, datetime

    from parayabuck.backtest.engine import BacktestResult

    return BacktestResult(
        symbol="SPY",
        timeframe="15m",
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 6, 1, tzinfo=UTC),
        total_bars=100,
        total_trades=5,
        starting_balance=10000.0,
        ending_balance=10000.0 + pnl,
        ending_equity=10000.0 + pnl,
        total_pnl=pnl,
        max_drawdown=50.0,
        win_rate=0.6,
        avg_win=20.0,
        avg_loss=-10.0,
        profit_factor=2.0,
    )
