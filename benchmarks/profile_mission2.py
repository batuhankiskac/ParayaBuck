"""Profile after-Mission1 (c4943f7) for Mission2."""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import _prepared_window, build_product_universe, load_csv_bars
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for

CONFIG = Path("config/settings.yaml")
CSV = Path("data/historical/equity_1200_2016_2026.csv")
EQUITY_START = datetime(2019, 1, 1, tzinfo=UTC)
EQUITY_END = datetime(2019, 6, 30, tzinfo=UTC)  # 6m for quick profile
LARGE_END = datetime(2021, 12, 31, tzinfo=UTC)


def run_one(start, end, label):
    config = load_config(str(CONFIG))
    bars = load_csv_bars(str(CSV), default_market=Market.EQUITY)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=end, timeframe="1d")
    strat = portfolio_strategy_from(config.strategy.equity.name, parameters=config.strategy.equity.parameters)
    filtered, warmup, (ordered, decision_bars) = _prepared_window(bars, Market.EQUITY, strat.timeframe, start, end)
    risk = portfolio_risk_limits_from_config(config.risk)
    engine = PortfolioBacktestEngine(
        strat,
        rebalance_policy_for(strat),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )

    def do():
        return engine.run(filtered, warmup=warmup, prepared_inputs=(ordered, decision_bars))

    pr = cProfile.Profile()
    pr.enable()
    result = do()
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.CUMULATIVE)
    ps.print_stats(60)
    print(
        f"\n=== {label} {start.date()}->{end.date()} bars={len(ordered) + len(warmup)} fills={len(result.fills)} rebalances={len(result.rebalances)} ==="
    )
    print(s.getvalue())
    # Also by time
    s2 = io.StringIO()
    ps2 = pstats.Stats(pr, stream=s2).sort_stats(pstats.SortKey.TIME)
    ps2.print_stats(60)
    print("--- by time ---")
    print(s2.getvalue())
    # Count np.array and tolist
    for func in [
        "array",
        "tolist",
        "searchsorted",
        "_aligned_returns",
        "_ols_beta",
        "_volatility",
        "_compounded",
        "market_close",
    ]:
        s3 = io.StringIO()
        ps3 = pstats.Stats(pr, stream=s3)
        # filter
        print(f"--- {func} ---")
        # manually iterate
        for k, v in ps3.stats.items():
            if func in str(k):
                print(k, v[:3])
    return result


if __name__ == "__main__":
    run_one(EQUITY_START, EQUITY_END, "6m v3")
    run_one(EQUITY_START, LARGE_END, "3y v3")
