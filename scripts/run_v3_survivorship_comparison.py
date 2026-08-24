#!/usr/bin/env python3
# ruff: noqa: E501
"""Frozen V3 CONTROL old vs new survivorship-aware dataset comparison."""

import json
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.validation import calendar_fold_windows

REPO = Path(__file__).resolve().parents[1]
OLD_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
NEW_CSV = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
DIV_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/survivorship_aware_2026_08"

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)

FROZEN_PARAMS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


def make_engine(config, strategy, universe):
    limits = portfolio_risk_limits_from_config(config.risk)
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=limits,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
    )


def run_for_dataset(csv_path: Path, label: str):
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(str(csv_path), default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(str(DIV_CSV))
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    strategy = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
    engine = make_engine(config, strategy, universe)
    # Filter bars to EVAL window
    filtered = [b for b in bars if EVAL_START <= b.timestamp <= EVAL_END]
    from datetime import timedelta

    warmup = [
        b
        for b in bars
        if b.timestamp < EVAL_START and b.timestamp >= EVAL_START - timedelta(days=800)
    ]
    result = engine.run(filtered, warmup=warmup, liquidate_at_end=True, corporate_actions=actions)
    return result, bars, universe


def stats_for_result(r):
    return {
        "return": round(float(r.total_return), 6),
        "cagr": round(float(r.cagr), 6),
        "sharpe": round(float(r.sharpe), 6),
        "sortino": round(float(r.sortino), 6),
        "calmar": round(float(r.calmar), 6),
        "max_drawdown": round(float(r.max_drawdown), 6),
        "turnover": round(float(r.turnover), 6),
        "average_exposure": round(1 - float(r.average_cash_ratio), 4),
        "average_holdings": round(float(r.average_holdings_count), 4),
        "fees_total": round(float(r.slippage_cost_total + r.spread_cost_total + r.fee_total), 2),
        "starting_equity": r.starting_equity,
        "ending_equity": round(float(r.ending_equity), 2),
        "per_symbol": [{"symbol": p.symbol, "pnl": round(p.realized_pnl, 2)} for p in r.per_symbol],
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Running OLD dataset...")
    old_result, old_bars, old_universe = run_for_dataset(OLD_CSV, "old")
    old_stats = stats_for_result(old_result)
    print(
        f"OLD: return {old_stats['return']} sharpe {old_stats['sharpe']} maxDD {old_stats['max_drawdown']}"
    )

    print("Running NEW dataset...")
    new_result, new_bars, new_universe = run_for_dataset(NEW_CSV, "new")
    new_stats = stats_for_result(new_result)
    print(
        f"NEW: return {new_stats['return']} sharpe {new_stats['sharpe']} maxDD {new_stats['max_drawdown']}"
    )

    # Walk-forward 20 folds for both
    folds = calendar_fold_windows(EVAL_START, EVAL_END, step_months=6)
    old_folds = []
    new_folds = []
    config = load_config(str(REPO / "config/settings.yaml"))
    actions = load_csv_corporate_actions(str(DIV_CSV))
    for s, e in folds:
        # Old
        bars_old = load_csv_bars(str(OLD_CSV), default_market=Market.EQUITY)
        uni_old = build_product_universe(config, bars_old, market=Market.EQUITY, end=EVAL_END)
        strat_old = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
        eng_old = make_engine(config, strat_old, uni_old)
        filt_old = [b for b in bars_old if s <= b.timestamp <= e]
        from datetime import timedelta

        warm_old = [
            b for b in bars_old if b.timestamp < s and b.timestamp >= s - timedelta(days=800)
        ]
        res_old = eng_old.run(
            filt_old, warmup=warm_old, liquidate_at_end=True, corporate_actions=actions
        )
        old_folds.append(
            {
                "start": s.isoformat(),
                "end": e.isoformat(),
                "return": round(float(res_old.total_return), 6),
                "sharpe": round(float(res_old.sharpe), 6),
                "max_drawdown": round(float(res_old.max_drawdown), 6),
            }
        )

        # New
        bars_new = load_csv_bars(str(NEW_CSV), default_market=Market.EQUITY)
        uni_new = build_product_universe(config, bars_new, market=Market.EQUITY, end=EVAL_END)
        strat_new = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
        eng_new = make_engine(config, strat_new, uni_new)
        filt_new = [b for b in bars_new if s <= b.timestamp <= e]
        warm_new = [
            b for b in bars_new if b.timestamp < s and b.timestamp >= s - timedelta(days=800)
        ]
        res_new = eng_new.run(
            filt_new, warmup=warm_new, liquidate_at_end=True, corporate_actions=actions
        )
        new_folds.append(
            {
                "start": s.isoformat(),
                "end": e.isoformat(),
                "return": round(float(res_new.total_return), 6),
                "sharpe": round(float(res_new.sharpe), 6),
                "max_drawdown": round(float(res_new.max_drawdown), 6),
            }
        )

    # Attribution by cohort for new dataset
    # For new dataset, determine which symbols are FINAL_SURVIVOR vs HISTORICAL_NON_SURVIVOR
    # Use lifetimes
    import csv

    lifetimes = {}
    with open(REPO / "data/historical/equity_entity_lifetimes_v2.csv", newline="") as f:
        for row in csv.DictReader(f):
            lifetimes[row["symbol"]] = row
    # For new result per_symbol, categorize
    survivor_pnl = 0
    nonsurvivor_pnl = 0
    for p in new_result.per_symbol:
        sym = p.symbol
        # Use lifetimes evidence_class
        ev = lifetimes.get(sym, {}).get("evidence_class", "UNKNOWN")
        if ev == "FINAL_SURVIVOR":
            survivor_pnl += p.realized_pnl
        else:
            nonsurvivor_pnl += p.realized_pnl
    old_vs_new = {
        "old": old_stats,
        "new": new_stats,
        "delta_return": round(new_stats["return"] - old_stats["return"], 6),
        "delta_sharpe": round(new_stats["sharpe"] - old_stats["sharpe"], 6),
        "delta_maxDD": round(new_stats["max_drawdown"] - old_stats["max_drawdown"], 6),
        "old_folds": old_folds,
        "new_folds": new_folds,
        "attribution": {
            "survivor_pnl": round(survivor_pnl, 2),
            "nonsurvivor_pnl": round(nonsurvivor_pnl, 2),
            "total_pnl": round(survivor_pnl + nonsurvivor_pnl, 2),
        },
        "note": "Frozen V3 CONTROL, same economics, different datasets. Survivorship-aware dataset includes 224 recovered inactive histories (demo sample).",
    }
    (OUT_DIR / "old_vs_new_control.json").write_text(json.dumps(old_vs_new, indent=2))
    (OUT_DIR / "old_vs_new_walk_forward.json").write_text(
        json.dumps({"old": old_folds, "new": new_folds}, indent=2)
    )
    print(
        f"Done. Old vs new written. Delta return {old_vs_new['delta_return']} sharpe {old_vs_new['delta_sharpe']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
