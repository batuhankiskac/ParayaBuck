#!/usr/bin/env python3
"""TRAIN-only parameter sweep for equity_dynamic_trend_1d_v1 (Phase 17).

The optimizer may see TRAIN only (2020-01-01 .. 2024-12-31). The universe is
built once and shared across trials. Ranked by the configured
risk_adjusted metric. Prints one line per trial.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["CONFIG_PATH"] = os.path.join(ROOT, "config", "settings.yaml")

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    build_product_universe,
    load_csv_bars,
    ranking_score,
)
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402

TRAIN_START = datetime(2020, 1, 1, tzinfo=UTC)
TRAIN_END = datetime(2024, 12, 31, tzinfo=UTC)
CSV = os.path.join(ROOT, "data", "historical", "equity_1200_2019_2026.csv")


def main() -> int:
    config = load_config()
    bars = load_csv_bars(CSV, default_market=Market.EQUITY)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=TRAIN_END)
    risk = portfolio_risk_limits_from_config(config.risk)
    selection = config.strategy.equity
    defaults = dict(selection.parameters)
    grid = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    results: list[tuple[float, dict[str, object], float, float, float, float]] = []
    for parameters in grid:
        merged = {**defaults, **parameters}
        strategy = portfolio_strategy_from(selection.name, merged)
        engine = PortfolioBacktestEngine(
            strategy,
            rebalance_policy_for(strategy, merged),
            starting_equity=config.backtest.initial_cash,
            slippage_bps=config.backtest.slippage_bps,
            spread_bps=config.backtest.spread_bps,
            commission=config.backtest.commission,
            crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
            equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
            benchmark_symbol="SPY",
            universe=universe,
            universe_version="optimization",
            risk_limits=risk,
            maximum_allocation=selection.maximum_allocation,
            execution_policy=PortfolioExecutionPolicy(
                rebalance_band_pct=config.execution.rebalance_band_pct,
                minimum_order_notional=config.execution.minimum_order_notional,
            ),
        )
        warmup = [b for b in bars if b.timestamp < TRAIN_START]
        filtered = [b for b in bars if TRAIN_START <= b.timestamp <= TRAIN_END]
        result = engine.run(filtered, warmup=warmup, liquidate_at_end=False)
        score = ranking_score(result, config.backtest.ranking_metric)
        results.append(
            (
                score,
                merged,
                result.total_return,
                result.sharpe,
                result.max_drawdown,
                len(result.closed_position_events),
            )
        )
        print(
            f"TRIAL score={score:.4f} return={result.total_return:.4f} "
            f"sharpe={result.sharpe:.3f} dd={result.max_drawdown:.3f} "
            f"trades={len(result.closed_position_events)} "
            f"params={json.dumps(parameters, sort_keys=True)}",
            flush=True,
        )
    results.sort(key=lambda item: item[0], reverse=True)
    print("BEST: " + json.dumps(results[0], default=str) if results else "NO TRIALS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
