#!/usr/bin/env python3
import sys
from pathlib import Path
from datetime import UTC, datetime
import json

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from parayabuck.backtest.product import build_product_universe, load_csv_bars, load_csv_corporate_actions, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.data.aggregator import aggregate_historical_bars
from research_helpers import _calendar_fold_window

CONFIG_PATH = str(REPO / "config" / "settings.yaml")
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
EQUITY_DIV_CSV = REPO / "data" / "historical" / "equity_1200_dividends.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_expanded_1h.csv"
EQ_START = datetime(2017, 1, 3, tzinfo=UTC)
EQ_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)

config = load_config(CONFIG_PATH)
equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
equity_divs = load_csv_corporate_actions(EQUITY_DIV_CSV)
equity_universe = build_product_universe(config, equity_bars, market=Market.EQUITY, end=EQ_END)

folds = _calendar_fold_window(datetime(2019, 1, 1, tzinfo=UTC), EQ_END, 6)
print(f"{len(folds)} folds")

def run_mode(mode):
    rets=[]
    for s,e in folds:
        prod = run_product_backtest(config, equity_bars, market=Market.EQUITY, start=s, end=e, corporate_actions=equity_divs, universe=equity_universe, persist=False, persist_artifacts=False, risk_mode=mode)
        rets.append(prod.result.total_return)
    pos=sum(1 for r in rets if r>0)
    median=sorted(rets)[len(rets)//2] if rets else 0
    mean=sum(rets)/len(rets) if rets else 0
    print(f"{mode}: pos {pos}/{len(rets)} median {median:.2%} mean {mean:.2%} worst {min(rets):.2%} best {max(rets):.2%}")
    return {"pos":pos,"median":median,"mean":mean,"worst":min(rets) if rets else 0,"best":max(rets) if rets else 0,"rets":rets}

for mode in ["shadow","production","one_shot_hysteresis_v1"]:
    res=run_mode(mode)
    Path(f"/tmp/wf_{mode}.json").write_text(json.dumps(res,indent=2))
