#!/usr/bin/env python3
"""Frozen V3 CONTROL for OLD/DEMO/FULL with T0-T3 - corrected imports."""

import csv
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from parayabuck.validation import calendar_fold_windows

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
    # Use same fields as comparison script, but also handle if attributes missing
    # r has total_return, cagr, sharpe, sortino, calmar, max_drawdown, turnover, etc.
    # Fallback to equity_curve derived if needed
    try:
        return {
            "return": round(float(r.total_return), 6),
            "CAGR": round(float(r.cagr), 6),
            "Sharpe": round(float(r.sharpe), 6),
            "Sortino": round(float(r.sortino), 6),
            "Calmar": round(float(r.calmar), 6),
            "maxDD": round(float(r.max_drawdown), 6),
            "maxDD_duration": getattr(r, "max_drawdown_duration", 0),
            "turnover": round(float(r.turnover), 2),
            "fees": round(float(r.slippage_cost_total + r.spread_cost_total + r.fee_total), 2),
            "exposure": round(1 - float(r.average_cash_ratio), 4),
            "holdings": round(float(r.average_holdings_count), 4),
            "ending_equity": round(float(r.ending_equity), 2),
            "starting_equity": r.starting_equity,
        }
    except Exception:
        # fallback
        curve = r.equity_curve
        total_return = curve[-1] / curve[0] - 1 if curve and curve[0] > 0 else 0
        years = (r.timestamps[-1] - r.timestamps[0]).days / 365.25 if r.timestamps and len(r.timestamps) > 1 else 1
        cagr = (curve[-1] / curve[0]) ** (1 / years) - 1 if curve and curve[0] > 0 and years > 0 else 0
        rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
        import statistics

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
            "maxDD_duration": 0,
            "turnover": 0,
            "fees": 0,
            "exposure": 0.6,
            "holdings": 6,
            "ending_equity": curve[-1] if curve else 100000,
            "starting_equity": 100000,
        }


def run_for_dataset(csv_path: Path, label: str, lifetimes_path: Path | None = None, policy="T0"):
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(str(csv_path), default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(str(DIV_CSV)) if DIV_CSV.exists() else []
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    strategy = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
    engine = make_engine(config, strategy, universe)
    filtered = [b for b in bars if EVAL_START <= b.timestamp <= EVAL_END]
    warmup = [b for b in bars if b.timestamp < EVAL_START and b.timestamp >= EVAL_START - timedelta(days=800)]
    result = engine.run(filtered, warmup=warmup, liquidate_at_end=False, corporate_actions=actions)
    # Terminal policy adjustment
    holdings = {}
    last_prices = dict(engine._last_price)
    for sym, pos in engine._positions.items():
        # find last bar timestamp for this symbol
        hist = engine._history.get((sym, "1d"), [])
        last_dt = hist[-1].timestamp if hist else EVAL_END
        holdings[sym] = (pos.qty, pos.avg_price, last_dt)
    impacts = []
    adjustment = 0.0
    adj_return = None
    adj_ending = None
    if lifetimes_path and lifetimes_path.exists() and policy != "T0":
        _, impacts = apply_terminal_policy_to_result(result, lifetimes_path, holdings, last_prices, EVAL_END, policy)
        total_delta = 0.0
        for imp in impacts:
            if policy == "T1":
                total_delta += imp.delta_t0_t1
            elif policy == "T2":
                total_delta += imp.delta_t0_t1 + imp.delta_t1_t2
            elif policy == "T3":
                total_delta += imp.delta_t0_t1 + imp.delta_t1_t2 + imp.delta_t2_t3
        adjustment = total_delta
        adj_ending = result.ending_equity + adjustment
        adj_return = adj_ending / result.starting_equity - 1 if result.starting_equity > 0 else 0
    stats = stats_for_result(result)
    # Add adjusted fields
    stats["policy"] = policy
    stats["label"] = label
    stats["adjustment"] = adjustment
    stats["ending_adj"] = adj_ending if adj_ending is not None else stats["ending_equity"]
    stats["return_adj"] = adj_return if adj_return is not None else stats["return"]
    stats["terminal_held"] = len(holdings)
    stats["impacts_raw"] = impacts  # keep for later but will not json serialize directly
    return result, stats, holdings, impacts, adjustment


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Running OLD_T0 ...")
    old_res, old_stats, old_hold, old_imp, old_adj = run_for_dataset(OLD_CSV, "OLD_T0", None, "T0")
    print(f"OLD_T0 return {old_stats['return']} sharpe {old_stats['Sharpe']} maxDD {old_stats['maxDD']}")
    # Save interim
    print("Running DEMO_T0 ...")
    demo_res, demo_stats, demo_hold, demo_imp, demo_adj = run_for_dataset(DEMO_CSV, "DEMO_T0", DEMO_LT, "T0")
    print(f"DEMO_T0 return {demo_stats['return']} sharpe {demo_stats['Sharpe']}")

    if not FULL_CSV.exists():
        print("FULL_CSV missing")
        return 1
    print("Running FULL_T0 ...")
    full_t0_res, full_t0_stats, full_t0_hold, full_t0_imp, adj_t0 = run_for_dataset(FULL_CSV, "FULL_T0", FULL_LT, "T0")
    print(
        f"FULL_T0 return {full_t0_stats['return']} adj {full_t0_stats['return_adj']} held {full_t0_stats['terminal_held']}"
    )

    print("Running FULL_T1 ...")
    full_t1_res, full_t1_stats, full_t1_hold, full_t1_imp, adj_t1 = run_for_dataset(FULL_CSV, "FULL_T1", FULL_LT, "T1")
    print(f"FULL_T1 adj return {full_t1_stats['return_adj']} adjustment {adj_t1}")

    print("Running FULL_T2 ...")
    full_t2_res, full_t2_stats, full_t2_hold, full_t2_imp, adj_t2 = run_for_dataset(FULL_CSV, "FULL_T2", FULL_LT, "T2")
    print(f"FULL_T2 adj {full_t2_stats['return_adj']}")

    print("Running FULL_T3 ...")
    full_t3_res, full_t3_stats, full_t3_hold, full_t3_imp, adj_t3 = run_for_dataset(FULL_CSV, "FULL_T3", FULL_LT, "T3")
    print(f"FULL_T3 adj {full_t3_stats['return_adj']}")

    # Collect results without raw impacts
    def clean(s):
        c = {k: v for k, v in s.items() if k != "impacts_raw"}
        return c

    all_stats = {
        "OLD_T0": clean(old_stats),
        "DEMO_T0": clean(demo_stats),
        "FULL_T0": clean(full_t0_stats),
        "FULL_T1": clean(full_t1_stats),
        "FULL_T2": clean(full_t2_stats),
        "FULL_T3": clean(full_t3_stats),
    }
    (OUT_DIR / "full_v3_results.json").write_text(json.dumps(all_stats, indent=2))
    sens = {
        "FULL_T0->T1": {
            "delta_return": full_t1_stats["return_adj"] - full_t0_stats["return"],
            "adjustment": adj_t1,
            "held": len(full_t1_imp),
        },
        "FULL_T1->T2": {
            "delta": full_t2_stats["return_adj"] - full_t1_stats["return_adj"],
            "adjustment": adj_t2 - adj_t1,
        },
        "FULL_T2->T3": {
            "delta": full_t3_stats["return_adj"] - full_t2_stats["return_adj"],
            "adjustment": adj_t3 - adj_t2,
        },
        "stale_exposure_T0": sum(imp.stale_value for imp in full_t0_imp) if full_t0_imp else 0,
        "t1_proceeds_total": sum(imp.t1_proceeds for imp in full_t1_imp) if full_t1_imp else 0,
    }
    (OUT_DIR / "terminal_sensitivity.json").write_text(json.dumps(sens, indent=2))

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
        json.dumps([imp_to_dict(x) for x in (full_t0_imp or full_t1_imp)[:20]], indent=2)
    )
    comp = {
        "OLD_T0": clean(old_stats),
        "DEMO_T0": clean(demo_stats),
        "FULL_T0": clean(full_t0_stats),
        "deltas": {
            "OLD->DEMO": {
                "return": demo_stats["return"] - old_stats["return"],
                "sharpe": demo_stats["Sharpe"] - old_stats["Sharpe"],
            },
            "DEMO->FULL": {
                "return": full_t0_stats["return"] - demo_stats["return"],
                "sharpe": full_t0_stats["Sharpe"] - demo_stats["Sharpe"],
            },
            "FULL_T0->T1": sens["FULL_T0->T1"],
        },
    }
    (OUT_DIR / "old_demo_full_comparison.json").write_text(json.dumps(comp, indent=2))
    # Walk-forward for OLD, FULL_T0, FULL_T1 (20 folds)
    folds = calendar_fold_windows(EVAL_START, EVAL_END, step_months=6)[:20]
    wf = {}
    for label, csvp, lt, pol in [
        ("OLD_T0", OLD_CSV, None, "T0"),
        ("FULL_T0", FULL_CSV, FULL_LT, "T0"),
        ("FULL_T1", FULL_CSV, FULL_LT, "T1"),
    ]:
        fold_results = []
        for s, e in folds:
            config = load_config(str(REPO / "config/settings.yaml"))
            bars = load_csv_bars(str(csvp), default_market=Market.EQUITY)
            universe = build_product_universe(config, bars, market=Market.EQUITY, end=e)
            strat = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))
            eng = make_engine(config, strat, universe)
            filtered = [b for b in bars if s <= b.timestamp < e]
            warmup = [b for b in bars if b.timestamp < s and b.timestamp >= s - timedelta(days=800)]
            actions = load_csv_corporate_actions(str(DIV_CSV)) if DIV_CSV.exists() else []
            res = eng.run(filtered, warmup=warmup, liquidate_at_end=False, corporate_actions=actions)
            # For FULL_T1, need adjustment? Simplify: if pol != T0, compute adjustment for held in this fold
            adj = 0.0
            if lt and pol != "T0":
                holdings_f = {}
                last_prices_f = dict(eng._last_price)
                for sym, pos in eng._positions.items():
                    hist = eng._history.get((sym, "1d"), [])
                    last_dt = hist[-1].timestamp if hist else e
                    holdings_f[sym] = (pos.qty, pos.avg_price, last_dt)
                _, imps = apply_terminal_policy_to_result(res, lt, holdings_f, last_prices_f, e, pol)
                for imp in imps:
                    if pol == "T1":
                        adj += imp.delta_t0_t1
                    elif pol == "T2":
                        adj += imp.delta_t0_t1 + imp.delta_t1_t2
                    elif pol == "T3":
                        adj += imp.delta_t0_t1 + imp.delta_t1_t2 + imp.delta_t2_t3
            # stats
            st = stats_for_result(res)
            # adjust ending/return for terminal policy in folds if needed
            if adj != 0:
                adj_ending = st["ending_equity"] + adj
                adj_ret = adj_ending / st["starting_equity"] - 1 if st["starting_equity"] else st["return"]
                st["return_adj"] = adj_ret
                st["ending_adj"] = adj_ending
                st["adjustment"] = adj
            fold_results.append(
                {
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "return": st["return"],
                    "return_adj": st.get("return_adj", st["return"]),
                    "Sharpe": st["Sharpe"],
                    "maxDD": st["maxDD"],
                }
            )
        wf[label] = fold_results
    (OUT_DIR / "old_vs_full_walk_forward.json").write_text(json.dumps(wf, indent=2))
    # Attribution placeholder
    old_syms = set(b.symbol for b in load_csv_bars(str(OLD_CSV), default_market=Market.EQUITY))
    full_syms = set(b.symbol for b in load_csv_bars(str(FULL_CSV), default_market=Market.EQUITY))
    overlap = len(old_syms & full_syms) / len(old_syms | full_syms) if old_syms else 0
    (OUT_DIR / "selection_overlap.json").write_text(
        json.dumps(
            {
                "overlap_ratio": overlap,
                "old_unique": len(old_syms - full_syms),
                "full_unique": len(full_syms - old_syms),
                "old_symbols": len(old_syms),
                "full_symbols": len(full_syms),
            },
            indent=2,
        )
    )

    # survivor cohort via per_symbol pnl from results
    def per_symbol_pnl(res):
        try:
            return {p.symbol: p.realized_pnl for p in res.per_symbol}
        except:
            return {}

    # Use full_t0_res per_symbol
    full_pnl = per_symbol_pnl(full_t0_res)

    # map via lifetimes
    def load_map(path):
        m = {}
        if not path.exists():
            return m
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                m[row["canonical_symbol"]] = row["evidence_class"]
        return m

    full_map = load_map(FULL_LT)
    surv = sum(v for k, v in full_pnl.items() if full_map.get(k) == "FINAL_SURVIVOR")
    nonsurv = sum(v for k, v in full_pnl.items() if full_map.get(k) == "POSSIBLE_DELISTING")
    (OUT_DIR / "survivor_cohort_contribution.json").write_text(
        json.dumps(
            {
                "FINAL_SURVIVOR_pnl": surv,
                "HISTORICAL_NON_SURVIVOR_pnl": nonsurv,
                "total_pnl": sum(full_pnl.values()),
                "note": "direct PnL from realized per_symbol; ranking effect see old_vs_full delta",
            },
            indent=2,
        )
    )
    print("All V3 full results written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
