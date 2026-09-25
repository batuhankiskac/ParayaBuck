#!/usr/bin/env python3
"""Replay the shipped V3 equity sleeve and persist its daily account curves.

Reuses the canonical V3 replay inputs and wiring from
``research/month_end_first_open_correctness_v1/replay_v3_parity.py`` with the
CURRENT shipped configuration (allocation 0.55, max_positions 8) and writes the
1x/2x/3x equity curves the ETF diversifier combination study consumes.

    .venv/bin/python research/etf_diversifier_v1/run_v3_curves.py \
        --out research/etf_diversifier_v1/V3_CURVES.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research/month_end_first_open_correctness_v1"))

import replay_v3_parity as canonical  # noqa: E402

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
    run_product_backtest,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    config = load_config(canonical.CONFIG_PATH)
    bars = load_csv_bars(canonical.BARS_PATH, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(canonical.LIFETIMES_PATH)
    corporate_actions = load_csv_corporate_actions(canonical.CORPORATE_ACTIONS_PATH)
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=canonical.END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    prepared = _prepared_window(bars, Market.EQUITY, strategy.timeframe, canonical.START, canonical.END)
    _filtered, warmup, (ordered, _decision_bars) = prepared
    numeric_history = build_numeric_history(warmup + ordered, market=Market.EQUITY, timeframe=strategy.timeframe)

    runs = {}
    for multiplier in canonical.COST_MULTIPLIERS:
        product = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=strategy.name,
            parameters=dict(selection.parameters),
            strategy_timeframe=strategy.timeframe,
            start=canonical.START,
            end=canonical.END,
            cost_multiplier=multiplier,
            persist=False,
            persist_artifacts=False,
            corporate_actions=corporate_actions,
            universe=universe,
            prepared_inputs=prepared,
            numeric_history=numeric_history,
            terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
        )
        result = product.result
        runs[str(multiplier)] = {
            "curve_timestamps": [ts.isoformat() for ts in result.curve_timestamps],
            "equity_curve": [float(value) for value in result.equity_curve],
            "fee_total": float(result.fee_total),
            "slippage_cost_total": float(result.slippage_cost_total),
            "spread_cost_total": float(result.spread_cost_total),
            "fill_count": len(result.fills),
            "max_positions_held": max((len(weights) for _ts, weights in result.weight_history), default=0),
            "summary": canonical._jsonable(product.report.summary),
        }
        print(f"V3 {multiplier:.1f}x done: ending={result.ending_equity:.2f} fills={len(result.fills)}", flush=True)

    payload = {
        "strategy": {"name": strategy.name, "version": strategy.version},
        "maximum_allocation": float(selection.maximum_allocation),
        "max_positions": int(config.risk.max_positions),
        "input_identity": {
            "bars_sha256": canonical._file_sha256(canonical.BARS_PATH),
            "lifetimes_sha256": canonical._file_sha256(canonical.LIFETIMES_PATH),
            "corporate_actions_sha256": canonical._file_sha256(canonical.CORPORATE_ACTIONS_PATH),
            "config_sha256": canonical._file_sha256(canonical.CONFIG_PATH),
            "universe_sha256": hash_point_in_time_universe(universe),
        },
        "runs": runs,
    }
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
