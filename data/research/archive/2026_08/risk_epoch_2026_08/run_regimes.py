#!/usr/bin/env python3
import sys
from pathlib import Path
from datetime import UTC, datetime
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from parayabuck.backtest.product import build_product_universe, load_csv_bars, load_csv_corporate_actions, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market

CONFIG_PATH = str(REPO / "config" / "settings.yaml")
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
EQUITY_DIV_CSV = REPO / "data" / "historical" / "equity_1200_dividends.csv"
EQ_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
config = load_config(CONFIG_PATH)
bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
divs = load_csv_corporate_actions(EQUITY_DIV_CSV)
univ = build_product_universe(config, bars, market=Market.EQUITY, end=EQ_END)
regimes = {
    "2020_crash": (datetime(2020,2,19, tzinfo=UTC), datetime(2020,4,30, tzinfo=UTC)),
    "2022_bear": (datetime(2022,1,1, tzinfo=UTC), datetime(2022,12,31, tzinfo=UTC)),
    "2024_2026": (datetime(2024,1,1, tzinfo=UTC), EQ_END),
}
for name,(s,e) in regimes.items():
    for mode in ["shadow","production","one_shot_hysteresis_v1"]:
        prod = run_product_backtest(config, bars, market=Market.EQUITY, start=s, end=e, corporate_actions=divs, universe=univ, persist=False, persist_artifacts=False, risk_mode=mode)
        print(f"{name} {mode}: ret {prod.result.total_return:.2%} cagr {prod.result.cagr:.2%} maxDD {prod.result.max_drawdown:.2%} sharpe {prod.result.sharpe:.2f}")
