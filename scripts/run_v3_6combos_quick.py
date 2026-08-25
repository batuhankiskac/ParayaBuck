#!/usr/bin/env python3
import csv
import json
import time
from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
    portfolio_risk_limits_from_config,
)
from parayabuck.backtest.terminal_policy import apply_terminal_policy_to_result
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

REPO = Path(__file__).resolve().parents[1]
OLD_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DEMO_CSV = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
FULL_CSV = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.csv"
FULL_LT = REPO / "data/historical/equity_entity_lifetimes_full_v3.csv"
DEMO_LT = REPO / "data/historical/equity_entity_lifetimes_v2.csv"
DIV_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/full_survivorship_2026_08"
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
    policy = rebalance_policy_for(strategy)
    from parayabuck.execution.portfolio import PortfolioExecutionPolicy

    return PortfolioBacktestEngine(
        strategy=strategy,
        rebalance_policy=policy,
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
        universe=universe,
        risk_limits=limits,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
    )


def stats_for_result(r):
    try:
        return {
            "return": round(float(r.total_return), 6),
            "CAGR": round(float(r.cagr), 6),
            "Sharpe": round(float(r.sharpe), 6),
            "Sortino": round(float(r.sortino), 6),
            "Calmar": round(float(r.calmar), 6),
            "maxDD": round(float(r.max_drawdown), 6),
            "turnover": round(float(r.turnover), 2),
            "fees": round(float(r.slippage_cost_total + r.spread_cost_total + r.fee_total), 2),
            "exposure": round(1 - float(r.average_cash_ratio), 4),
            "holdings": round(float(r.average_holdings_count), 4),
            "ending_equity": round(float(r.ending_equity), 2),
            "starting_equity": r.starting_equity,
        }
    except Exception:
        import math
        import statistics

        curve = r.equity_curve
        total_return = curve[-1] / curve[0] - 1 if curve and curve[0] > 0 else 0
        years = (r.timestamps[-1] - r.timestamps[0]).days / 365.25 if r.timestamps and len(r.timestamps) > 1 else 1
        cagr = (curve[-1] / curve[0]) ** (1 / years) - 1 if curve and curve[0] > 0 and years > 0 else 0
        rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
        mean = statistics.mean(rets) if rets else 0
        std = statistics.pstdev(rets) if len(rets) > 1 else 0
        sharpe = mean / std * math.sqrt(252) if std > 0 else 0
        downside = [x for x in rets if x < 0]
        dstd = statistics.pstdev(downside) if len(downside) > 1 else 0
        sortino = mean / max(dstd, 1e-9) * math.sqrt(252) if downside else sharpe
        peak = curve[0] if curve else 100000
        maxdd = 0
        for v in curve:
            if v > peak:
                peak = v
            else:
                maxdd = max(maxdd, (peak - v) / peak if peak > 0 else 0)
        calmar = cagr / maxdd if maxdd > 0 else 0
        return {
            "return": total_return,
            "CAGR": cagr,
            "Sharpe": sharpe,
            "Sortino": sortino,
            "Calmar": calmar,
            "maxDD": maxdd,
            "turnover": 0,
            "fees": 0,
            "exposure": 0.6,
            "holdings": 6,
            "ending_equity": curve[-1] if curve else 100000,
            "starting_equity": 100000,
        }


def run_one(csv_path, lifetimes_path, policy, label):
    print(f"Running {label} policy {policy} ...")
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(str(csv_path), default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(str(DIV_CSV)) if DIV_CSV.exists() else []
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    strategy = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
    engine = make_engine(config, strategy, universe)
    filtered = [b for b in bars if EVAL_START <= b.timestamp <= EVAL_END]
    warmup = [b for b in bars if b.timestamp < EVAL_START and b.timestamp >= EVAL_START - timedelta(days=800)]
    result = engine.run(filtered, warmup=warmup, liquidate_at_end=False, corporate_actions=actions)
    holdings = {}
    last_prices = dict(engine._last_price)
    for sym, pos in engine._positions.items():
        hist = engine._history.get((sym, "1d"), [])
        last_dt = hist[-1].timestamp if hist else EVAL_END
        holdings[sym] = (pos.qty, pos.avg_price, last_dt)
    impacts = []
    adjustment = 0.0
    if lifetimes_path and lifetimes_path.exists() and policy != "T0":
        _, impacts = apply_terminal_policy_to_result(result, lifetimes_path, holdings, last_prices, EVAL_END, policy)
        for imp in impacts:
            if policy == "T1":
                adjustment += imp.delta_t0_t1
            elif policy == "T2":
                adjustment += imp.delta_t0_t1 + imp.delta_t1_t2
            elif policy == "T3":
                adjustment += imp.delta_t0_t1 + imp.delta_t1_t2 + imp.delta_t2_t3
    stats = stats_for_result(result)
    # adjusted
    adj_ending = result.ending_equity + adjustment
    adj_return = adj_ending / result.starting_equity - 1 if result.starting_equity > 0 else stats["return"]
    stats["policy"] = policy
    stats["label"] = label
    stats["adjustment"] = adjustment
    stats["ending_adj"] = adj_ending
    stats["return_adj"] = adj_return
    stats["terminal_held"] = len(holdings)
    # keep impacts for later? return small
    return result, stats, impacts


OUT_DIR.mkdir(parents=True, exist_ok=True)
# Check if results already exist to resume
existing = {}
if (OUT_DIR / "full_v3_results.json").exists():
    try:
        existing = json.loads((OUT_DIR / "full_v3_results.json").read_text())
        print(f"existing results found {list(existing.keys())}")
    except Exception:
        pass

to_run = []
if "OLD_T0" not in existing:
    to_run.append(("OLD_T0", OLD_CSV, None, "T0"))
if "DEMO_T0" not in existing:
    to_run.append(("DEMO_T0", DEMO_CSV, DEMO_LT, "T0"))
if "FULL_T0" not in existing:
    to_run.append(("FULL_T0", FULL_CSV, FULL_LT, "T0"))
if "FULL_T1" not in existing:
    to_run.append(("FULL_T1", FULL_CSV, FULL_LT, "T1"))
if "FULL_T2" not in existing:
    to_run.append(("FULL_T2", FULL_CSV, FULL_LT, "T2"))
if "FULL_T3" not in existing:
    to_run.append(("FULL_T3", FULL_CSV, FULL_LT, "T3"))

print(f"to_run {to_run}")

results = {}
for label, csvp, lt, pol in to_run:
    res, stats, imps = run_one(csvp, lt, pol, label)
    results[label] = stats
    # merge with existing
    combined = {**existing, **results}
    (OUT_DIR / "full_v3_results.json").write_text(json.dumps(combined, indent=2))
    print(f"{label} done return {stats['return']} adj {stats['return_adj']} held {stats['terminal_held']}")
    # also write terminal sensitive for FULL
    if label.startswith("FULL"):

        def imp_to_dict(imp):
            return {
                "symbol": imp.symbol,
                "entity_id": imp.entity_id,
                "last_bar": imp.last_bar,
                "quantity": imp.quantity,
                "entry_price": imp.entry_price,
                "stale_value": imp.stale_value,
                "t1_proceeds": imp.t1_proceeds,
                "t2_proceeds": imp.t2_proceeds,
                "t3_proceeds": imp.t3_proceeds,
                "pnl_t0": imp.pnl_t0,
                "pnl_t1": imp.pnl_t1,
                "pnl_t2": imp.pnl_t2,
                "pnl_t3": imp.pnl_t3,
                "delta_t0_t1": imp.delta_t0_t1,
                "delta_t1_t2": imp.delta_t1_t2,
                "delta_t2_t3": imp.delta_t2_t3,
            }

        (OUT_DIR / "terminal_sensitive_positions.json").write_text(
            json.dumps([imp_to_dict(x) for x in imps[:20]], indent=2)
        )
        print(f"wrote terminal sensitive {len(imps)}")
    time.sleep(1)

print("All 6 combos done")
# Also write comparison
if (OUT_DIR / "full_v3_results.json").exists():
    all_stats = json.loads((OUT_DIR / "full_v3_results.json").read_text())
    comp = {
        "OLD_T0": all_stats.get("OLD_T0"),
        "DEMO_T0": all_stats.get("DEMO_T0"),
        "FULL_T0": all_stats.get("FULL_T0"),
        "FULL_T1": all_stats.get("FULL_T1"),
        "FULL_T2": all_stats.get("FULL_T2"),
        "FULL_T3": all_stats.get("FULL_T3"),
    }
    (OUT_DIR / "old_demo_full_comparison.json").write_text(json.dumps(comp, indent=2))
    sens = {}
    if "FULL_T0" in all_stats and "FULL_T1" in all_stats:
        sens["FULL_T0->T1"] = {
            "delta_return": all_stats["FULL_T1"]["return_adj"] - all_stats["FULL_T0"]["return"],
            "adjustment": all_stats["FULL_T1"]["adjustment"],
        }
    if "FULL_T1" in all_stats and "FULL_T2" in all_stats:
        sens["FULL_T1->T2"] = {"delta": all_stats["FULL_T2"]["return_adj"] - all_stats["FULL_T1"]["return_adj"]}
    if "FULL_T2" in all_stats and "FULL_T3" in all_stats:
        sens["FULL_T2->T3"] = {"delta": all_stats["FULL_T3"]["return_adj"] - all_stats["FULL_T2"]["return_adj"]}
    (OUT_DIR / "terminal_sensitivity.json").write_text(json.dumps(sens, indent=2))
    print("wrote comparisons")
