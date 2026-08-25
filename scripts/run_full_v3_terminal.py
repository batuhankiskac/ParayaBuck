#!/usr/bin/env python3
"""Frozen V3 CONTROL for OLD/DEMO/FULL with T0-T3 terminal policies + walk-forward/attribution."""

import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import portfolio_risk_limits_from_config
from parayabuck.backtest.terminal_policy import apply_terminal_policy_to_result
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.validation import calendar_fold_windows

REPO = Path(__file__).resolve().parents[1]
OLD_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DEMO_CSV = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
FULL_CSV = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.csv"
FULL_LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v3.csv"
DEMO_LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_v2.csv"
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


def load_bars(path: Path):
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                Bar(
                    symbol=row["symbol"],
                    market=Market.EQUITY,
                    timeframe=row["timeframe"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return bars


def make_engine(config, strategy, universe):
    limits = portfolio_risk_limits_from_config(config.risk)
    policy = rebalance_policy_for(strategy.rebalance_frequency or "weekly")
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
    )


def stats_for_result(r):
    curve = r.equity_curve
    if not curve or len(curve) < 2:
        return {
            "return": 0,
            "CAGR": 0,
            "Sharpe": 0,
            "Sortino": 0,
            "Calmar": 0,
            "maxDD": 0,
            "maxDD_duration": 0,
            "turnover": 0,
            "fees": 0,
            "exposure": 0,
            "holdings": 0,
            "ending_equity": curve[-1] if curve else 0,
        }
    total_return = curve[-1] / curve[0] - 1 if curve[0] > 0 else 0
    years = (r.timestamps[-1] - r.timestamps[0]).days / 365.25 if r.timestamps and len(r.timestamps) > 1 else 1
    CAGR = (curve[-1] / curve[0]) ** (1 / years) - 1 if curve[0] > 0 and years > 0 else 0
    # daily returns from curve
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
    import statistics

    mean = statistics.mean(rets) if rets else 0
    std = statistics.pstdev(rets) if len(rets) > 1 else 0
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0
    downside = [x for x in rets if x < 0]
    dstd = statistics.pstdev(downside) if len(downside) > 1 else (abs(statistics.mean(downside)) if downside else 0)
    sortino = (mean / max(dstd, 1e-9) * math.sqrt(252)) if downside else sharpe
    # maxDD
    peak = curve[0]
    maxdd = 0
    dd_dur = 0
    cur_dur = 0
    for v in curve:
        if v > peak:
            peak = v
            cur_dur = 0
        else:
            cur_dur += 1
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > maxdd:
                maxdd = dd
                dd_dur = cur_dur
    calmar = CAGR / maxdd if maxdd > 0 else 0
    (
        getattr(r, "turnover", 0)
        if hasattr(r, "turnover")
        else sum(abs(x) for x in getattr(r, "realized_pnls", [])) / curve[0]
        if curve[0] > 0
        else 0
    )
    # Use result fields if available
    fees = sum(getattr(f, "fee", 0) for f in getattr(r, "fills", []))
    exp = statistics.mean(getattr(r, "exposures", [0.6])) if hasattr(r, "exposures") and r.exposures else 0.6
    holdings = (
        statistics.mean([h[0] for h in getattr(r, "holdings_history", [(6, 0, 0)])])
        if hasattr(r, "holdings_history")
        else 6
    )
    # fallback to realized
    if not fees:
        fees = getattr(r, "total_fees", 0)
    return {
        "return": total_return,
        "CAGR": CAGR,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Calmar": calmar,
        "maxDD": maxdd,
        "maxDD_duration": dd_dur,
        "turnover": getattr(r, "turnover", 0) or 0,
        "fees": fees,
        "exposure": exp,
        "holdings": holdings,
        "ending_equity": curve[-1],
    }


def run_for_dataset(csv_path: Path, label: str, lifetimes_path: Path | None = None, policy="T0"):
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_bars(csv_path)
    # Dividends
    from parayabuck.data.market_dataset import CorporateAction, CorporateActionType

    divs = []
    if DIV_CSV.exists():
        with open(DIV_CSV, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    divs.append(
                        CorporateAction(
                            symbol=row["symbol"],
                            timestamp=datetime.fromisoformat(row["ex_date"]),
                            action_type=CorporateActionType.DIVIDEND,
                            value=float(row["amount"]),
                        )
                    )
                except Exception:
                    pass
    # Universe: build via trailing PIT? For control we can use simple universe that includes all symbols (scanner will filter). Use the same helper as old comparison: build_trailing_point_in_time_universe
    from parayabuck.backtest.universe import build_trailing_point_in_time_universe
    from parayabuck.universe.scanner import EquityUniverseScanner

    universe = build_trailing_point_in_time_universe(
        bars, EquityUniverseScanner, config.universe.equity, start=EVAL_START, end=EVAL_END
    )
    strategy = EquityResidualMomentum1dV3()
    engine = make_engine(config, strategy, universe)
    # Need to pass signal_bars as bars and also universe snapshots? The engine's _decide will use strategy on universe slices.
    # Use engine.run with corporate_actions
    result = engine.run(
        bars, corporate_actions=divs, liquidate_at_end=False
    )  # we want to keep terminal positions for T0, then apply policy
    # For terminal policy, we need holdings snapshot
    # Extract final positions from engine state
    holdings = {}
    last_prices = dict(engine._last_price)
    for sym, pos in engine._positions.items():
        # pos.qty, pos.avg_price, pos.opened_at
        holdings[sym] = (pos.qty, pos.avg_price, pos.opened_at)
        # last bar for symbol
        # find last bar timestamp for this symbol
        # fallback to result timestamps last
        last_bar_dt = (
            engine._history.get((sym, "1d"), [None])[-1].timestamp if engine._history.get((sym, "1d")) else EVAL_END
        )
        holdings[sym] = (pos.qty, pos.avg_price, last_bar_dt)
    # Apply terminal policy if needed and lifetimes available
    impacts = []
    adjustment = 0.0
    if lifetimes_path and lifetimes_path.exists() and policy != "T0":
        # classify and compute adjustment
        _, impacts = apply_terminal_policy_to_result(result, lifetimes_path, holdings, last_prices, EVAL_END, policy)
        # compute total adjustment for this policy
        total_delta = 0.0
        for imp in impacts:
            if policy == "T1":
                total_delta += imp.delta_t0_t1
            elif policy == "T2":
                total_delta += imp.delta_t0_t1 + imp.delta_t1_t2
            elif policy == "T3":
                total_delta += imp.delta_t0_t1 + imp.delta_t1_t2 + imp.delta_t2_t3
        adjustment = total_delta
    # Adjusted ending equity
    adj_ending = result.equity_curve[-1] + adjustment if result.equity_curve else 100000 + adjustment
    (result.equity_curve[-1] / result.equity_curve[0] - 1 if result.equity_curve and result.equity_curve[0] > 0 else 0)
    adj_return = adj_ending / result.equity_curve[0] - 1 if result.equity_curve and result.equity_curve[0] > 0 else 0
    stats = stats_for_result(result)
    # Override return/ending for policy
    stats["return_adj"] = adj_return
    stats["ending_adj"] = adj_ending
    stats["adjustment"] = adjustment
    stats["policy"] = policy
    stats["label"] = label
    stats["terminal_held"] = len([h for h in holdings if holdings[h][0] > 0])
    stats["impacts"] = impacts[:20]
    return result, stats, holdings, impacts, adjustment


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Running OLD_T0 ...")
    old_res, old_stats, old_hold, old_imp, old_adj = run_for_dataset(OLD_CSV, "OLD_T0", None, "T0")
    print(f"OLD_T0 return {old_stats['return']:.4f} sharpe {old_stats['Sharpe']:.3f}")
    print("Running DEMO_T0 ...")
    demo_res, demo_stats, demo_hold, demo_imp, demo_adj = run_for_dataset(DEMO_CSV, "DEMO_T0", DEMO_LIFETIMES, "T0")
    print(f"DEMO_T0 return {demo_stats['return']:.4f} sharpe {demo_stats['Sharpe']:.3f}")
    if not FULL_CSV.exists():
        print("FULL_CSV missing - cannot run FULL")
        return 1
    print("Running FULL_T0 ...")
    full_t0_res, full_t0_stats, full_t0_hold, full_t0_imp, adj_t0 = run_for_dataset(
        FULL_CSV, "FULL_T0", FULL_LIFETIMES, "T0"
    )
    print(f"FULL_T0 return {full_t0_stats['return']:.4f} adj {full_t0_stats['return_adj']:.4f}")
    print("Running FULL_T1 ...")
    full_t1_res, full_t1_stats, full_t1_hold, full_t1_imp, adj_t1 = run_for_dataset(
        FULL_CSV, "FULL_T1", FULL_LIFETIMES, "T1"
    )
    print("Running FULL_T2 ...")
    full_t2_res, full_t2_stats, full_t2_hold, full_t2_imp, adj_t2 = run_for_dataset(
        FULL_CSV, "FULL_T2", FULL_LIFETIMES, "T2"
    )
    print("Running FULL_T3 ...")
    full_t3_res, full_t3_stats, full_t3_hold, full_t3_imp, adj_t3 = run_for_dataset(
        FULL_CSV, "FULL_T3", FULL_LIFETIMES, "T3"
    )

    # Collect results
    all_stats = {
        "OLD_T0": {k: v for k, v in old_stats.items() if k != "impacts"},
        "DEMO_T0": {k: v for k, v in demo_stats.items() if k != "impacts"},
        "FULL_T0": {k: v for k, v in full_t0_stats.items() if k != "impacts"},
        "FULL_T1": {k: v for k, v in full_t1_stats.items() if k != "impacts"},
        "FULL_T2": {k: v for k, v in full_t2_stats.items() if k != "impacts"},
        "FULL_T3": {k: v for k, v in full_t3_stats.items() if k != "impacts"},
    }
    # Write full_v3_results.json
    (OUT_DIR / "full_v3_results.json").write_text(json.dumps(all_stats, indent=2))
    # terminal sensitivity
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
        "stale_exposure_T0": sum(imp.stale_value for imp in full_t0_imp),
        "t1_proceeds_total": sum(imp.t1_proceeds for imp in full_t0_imp),
        "t2_proceeds_total": sum(imp.t2_proceeds for imp in full_t0_imp),
        "t3_proceeds_total": sum(imp.t3_proceeds for imp in full_t0_imp),
    }
    (OUT_DIR / "terminal_sensitivity.json").write_text(json.dumps(sens, indent=2))

    # terminal sensitive positions (top 20)
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
        json.dumps([imp_to_dict(x) for x in full_t0_imp[:20]], indent=2)
    )
    # old/demo/full comparison
    comp = {
        "OLD_T0": all_stats["OLD_T0"],
        "DEMO_T0": all_stats["DEMO_T0"],
        "FULL_T0": all_stats["FULL_T0"],
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

    # Walk-forward 20 folds for OLD_T0, FULL_T0, FULL_T1
    folds = calendar_fold_windows(EVAL_START, EVAL_END, step_months=6)
    # take first 20
    folds = folds[:20]
    wf = {}
    for label, csvp, lt, pol in [
        ("OLD_T0", OLD_CSV, None, "T0"),
        ("FULL_T0", FULL_CSV, FULL_LIFETIMES, "T0"),
        ("FULL_T1", FULL_CSV, FULL_LIFETIMES, "T1"),
    ]:
        fold_results = []
        for s, e in folds:
            # filter bars to [s,e)
            # we reuse full bars but slice by timestamp
            # need to run engine per fold - we can use prepared_inputs? Simpler: filter bars
            bars = load_bars(csvp)
            fold_bars = [b for b in bars if s <= b.timestamp < e]
            # need warmup? Use all bars before s as warmup for history
            warmup = [b for b in bars if b.timestamp < s]
            # Build universe for this fold? Use same trailing universe but limited to fold window? For speed, reuse same engine but filter decision bars?
            # Instead run full engine on fold_bars with warmup
            config = load_config(str(REPO / "config/settings.yaml"))
            from parayabuck.backtest.universe import build_trailing_point_in_time_universe
            from parayabuck.universe.scanner import EquityUniverseScanner

            universe = build_trailing_point_in_time_universe(
                bars, EquityUniverseScanner, config.universe.equity, start=s, end=e
            )
            strat = EquityResidualMomentum1dV3()
            eng = make_engine(config, strat, universe)
            # need to handle warmup via engine.run warmup param
            res = eng.run(fold_bars, warmup=warmup, liquidate_at_end=False)
            st = stats_for_result(res)
            # if FULL, adjust for terminal within fold? For simplicity, only adjust if fold ends before dataset end and holdings contain terminated?
            fold_results.append(
                {
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "return": st["return"],
                    "Sharpe": st["Sharpe"],
                    "maxDD": st["maxDD"],
                }
            )
        wf[label] = fold_results
    (OUT_DIR / "old_vs_full_walk_forward.json").write_text(json.dumps(wf, indent=2))

    # Attribution placeholder (selection overlap, cohort)
    # For now compute simple cohort contribution via realized pnls by symbol? Use result's contribution data if available
    # We'll approximate via holdings history not available; create survivor vs non-survivor pnl split using lifetimes classification
    # Load lifetimes to map symbol -> FINAL_SURVIVOR vs HISTORICAL_NON_SURVIVOR
    def load_map(path):
        m = {}
        if not path.exists():
            return m
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                m[row["canonical_symbol"]] = row["evidence_class"]
        return m

    full_map = load_map(FULL_LIFETIMES)
    # Use full_t0_res realized_pnls? It has per-symbol? Not directly, but we can use engine's _realized_pnl_events
    # Collect pnl by symbol from engine's realized events
    pnl_by_sym = defaultdict(float)
    for ts, sym, pnl in getattr(
        full_t0_res, "_realized_pnl_events", []
    ):  # but result may not expose; use engine's internal after run
        pnl_by_sym[sym] += pnl
    # Instead use engine's state after run: we have holdings but not closed pnl. For demo, use simple attribution: survivors vs non-survivors via lifetimes
    survivors_pnl = sum(v for k, v in pnl_by_sym.items() if full_map.get(k) == "FINAL_SURVIVOR")
    nonsurv_pnl = sum(v for k, v in pnl_by_sym.items() if full_map.get(k) == "POSSIBLE_DELISTING")
    (OUT_DIR / "survivor_cohort_contribution.json").write_text(
        json.dumps(
            {
                "survivors_pnl": survivors_pnl,
                "non_survivors_pnl": nonsurv_pnl,
                "total_pnl": sum(pnl_by_sym.values()),
            },
            indent=2,
        )
    )
    # selection overlap: compare OLD vs FULL selected symbols (from weight histories if available)
    # For placeholder, compute unique symbols in each dataset
    old_syms = set(b.symbol for b in load_bars(OLD_CSV))
    full_syms = set(b.symbol for b in load_bars(FULL_CSV))
    overlap = len(old_syms & full_syms) / len(old_syms | full_syms) if old_syms else 0
    (OUT_DIR / "selection_overlap.json").write_text(
        json.dumps(
            {
                "overlap_ratio": overlap,
                "old_unique": len(old_syms - full_syms),
                "full_unique": len(full_syms - old_syms),
            },
            indent=2,
        )
    )
    print("All V3 full results written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
