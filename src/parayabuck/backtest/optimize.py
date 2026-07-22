from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from tabulate import tabulate

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
from parayabuck.config import Config
from parayabuck.data.base import DataSource
from parayabuck.execution.paper import PaperBroker
from parayabuck.logger import get_logger
from parayabuck.risk.manager import RiskManager
from parayabuck.risk.sizer import PositionSizer
from parayabuck.strategy.base import Strategy

log = get_logger("parayabuck.backtest.optimize")


@dataclass
class ParamCombo:
    """One parameter combination from a grid search."""
    params: dict[str, Any]
    in_sample_result: BacktestResult | None = None
    out_of_sample_result: BacktestResult | None = None
    is_selected: bool = False
    periods_per_year: int = 252

    @property
    def in_sample_pnl(self) -> float:
        return self.in_sample_result.total_pnl if self.in_sample_result else 0.0

    @property
    def out_of_sample_pnl(self) -> float:
        return self.out_of_sample_result.total_pnl if self.out_of_sample_result else 0.0

    @property
    def in_sample_sharpe(self) -> float:
        # Approximate Sharpe from the equity curve if present.
        eq = self.in_sample_result.equity_curve if self.in_sample_result else []
        if len(eq) < 2:
            return 0.0
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] != 0]
        if not rets:
            return 0.0
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
        if var_r <= 0:
            return 0.0
        sharpe = (mean_r * self.periods_per_year) / (
            var_r**0.5 * self.periods_per_year**0.5
        )
        return float(sharpe)


@dataclass
class WalkForwardResult:
    strategy_name: str
    symbol: str
    timeframe: str
    train_fraction: float
    combinations: list[ParamCombo] = field(default_factory=list)
    selected: ParamCombo | None = None

    def summary(self) -> str:
        lines = [
            "",
            "=" * 70,
            "  WALK-FORWARD OPTIMIZATION RESULTS",
            "=" * 70,
            f"  Strategy:  {self.strategy_name}",
            f"  Symbol:    {self.symbol} ({self.timeframe})",
            f"  Train/In-sample fraction: {self.train_fraction:.0%}",
            f"  Parameter combinations tested: {len(self.combinations)}",
        ]
        if self.selected is not None:
            sel = self.selected
            lines.append("")
            lines.append("  Selected (best in-sample, verified out-of-sample):")
            for k, v in sel.params.items():
                lines.append(f"    {k}: {v}")
            lines.append(
                f"  In-sample PnL:     ${sel.in_sample_pnl:+.2f}  "
                f"(Sharpe {sel.in_sample_sharpe:+.2f})"
            )
            lines.append(f"  Out-of-sample PnL: ${sel.out_of_sample_pnl:+.2f}")
        lines.append("")
        lines.append("  Top 5 by in-sample PnL:")
        rows = []
        for c in sorted(self.combinations, key=lambda x: -x.in_sample_pnl)[:5]:
            rows.append(
                [
                    ", ".join(f"{k}={v}" for k, v in c.params.items()),
                    f"${c.in_sample_pnl:+.2f}",
                    f"${c.out_of_sample_pnl:+.2f}",
                    f"{c.in_sample_sharpe:+.2f}",
                ]
            )
        lines.append(
            tabulate(
                rows,
                headers=["Params", "In-Sample PnL", "OOS PnL", "In-Sample Sharpe"],
                tablefmt="simple",
            )
        )
        lines.append("=" * 70)
        return "\n".join(lines)


def _grid_combos(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand a {param: [values]} grid into a list of param dicts."""
    if not grid:
        return [{}]
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    out: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        out.append(dict(zip(keys, combo, strict=True)))
    return out


def _load_strategy_by_name(name: str, params: dict[str, Any]) -> Strategy:
    """Mirror of auto_pilot's loader, kept here to avoid a circular import."""
    from parayabuck.backtest.auto_pilot import _load_strategy_by_name as _load

    return _load(name, params)


def walk_forward_optimize(
    config: Config,
    data_source: DataSource,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    param_grid: dict[str, list[Any]],
    train_fraction: float = 0.6,
    metric: str = "pnl",
) -> WalkForwardResult:
    """Walk-forward grid-search optimisation.

    Splits the period `[start, end]` into in-sample (train) and out-of-sample
    (test) by `train_fraction`. For each parameter combination in `param_grid`:

      1. Run a backtest on the in-sample period; record PnL / Sharpe.
      2. Pick the combination with the best in-sample metric.
      3. Run a backtest on the out-of-sample period with the selected
         combination; record PnL.

    This is the simplest walk-forward scheme (single split). It exposes
    over-fitting: if the in-sample PnL is great but the OOS PnL is negative,
    the parameters are over-fit and shouldn't be deployed live. Without this
    check, every parameter optimization is just curve-fitting to noise.

    Args:
        param_grid: e.g. {"fast_ema": [5, 9, 12], "slow_ema": [21, 26, 50]}.
        train_fraction: fraction of the period used for in-sample training.
            0.6 means the first 60% trains, the last 40% tests.
        metric: "pnl" or "sharpe" — the in-sample metric to maximise when
            selecting the best parameter combo.
    """
    wf = WalkForwardResult(
        strategy_name=strategy_name,
        symbol=symbol,
        timeframe=timeframe,
        train_fraction=train_fraction,
    )

    combos = _grid_combos(param_grid)
    log.info(
        f"Walk-forward: {len(combos)} combos on {symbol} {strategy_name}, "
        f"train_fraction={train_fraction:.0%}"
    )

    # Convert start/end to datetimes so we can split the range.
    from datetime import datetime

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    split_dt = start_dt + (end_dt - start_dt) * train_fraction
    split_str = split_dt.isoformat()

    for i, params in enumerate(combos):
        combo = ParamCombo(
            params=params,
            periods_per_year=BacktestEngine._bars_per_year(symbol, timeframe),
        )
        try:
            combo.in_sample_result = _run_single(
                config, data_source, strategy_name, params,
                symbol, timeframe, start, split_str,
            )
            combo.out_of_sample_result = _run_single(
                config, data_source, strategy_name, params,
                symbol, timeframe, split_str, end,
            )
            log.info(
                f"  combo {i + 1}/{len(combos)} {params}: "
                f"in-sample=${combo.in_sample_pnl:+.2f}, "
                f"OOS=${combo.out_of_sample_pnl:+.2f}"
            )
        except Exception as e:
            log.warning(f"  combo {params} failed: {e}")
            continue
        wf.combinations.append(combo)

    if not wf.combinations:
        return wf

    # Select the best in-sample combo.
    if metric == "sharpe":
        wf.selected = max(wf.combinations, key=lambda c: c.in_sample_sharpe)
    else:
        wf.selected = max(wf.combinations, key=lambda c: c.in_sample_pnl)
    wf.selected.is_selected = True
    return wf


def _run_single(
    config: Config,
    data_source: DataSource,
    strategy_name: str,
    params: dict[str, Any],
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
) -> BacktestResult:
    """Run one backtest with the given strategy + params over the period."""
    from copy import deepcopy

    sub_config = deepcopy(config)
    strategy = _load_strategy_by_name(strategy_name, params)
    broker = PaperBroker(sub_config)
    risk_mgr = RiskManager(sub_config)
    sizer = PositionSizer(
        risk_pct=sub_config.risk.risk_per_trade / sub_config.account.starting_balance,
        max_qty=sub_config.risk.max_position_size,
    )
    engine = BacktestEngine(
        config=sub_config,
        data_source=data_source,
        broker=broker,
        risk_manager=risk_mgr,
        position_sizer=sizer,
        benchmark_symbol=None,  # disable benchmark for optimizer speed
    )
    return engine.run(strategy, symbol, timeframe, start, end)


__all__ = ["walk_forward_optimize", "WalkForwardResult", "ParamCombo"]
