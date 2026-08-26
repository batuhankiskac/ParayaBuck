#!/usr/bin/env python3
"""Execute 6 corrected path-dependent V3 runs (H_FULL)."""

from __future__ import annotations

import csv
import json
import math
import pathlib
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timezone

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import build_product_universe, load_csv_bars, load_csv_corporate_actions
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine, TerminalInfo
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import (
    EquityResidualMomentum1dV3 as EquityResidualMomentumStrategy,
)

DATASETS = {
    "OLD": REPO / "data/historical/equity_1200_2016_2026.csv",
    "DEMO": REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv",
    "FULL": REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v4.csv",
}
LIFETIMES_V4 = REPO / "data/historical/equity_entity_lifetimes_full_v4.csv"
CA_V4 = REPO / "data/historical/equity_survivorship_full_corporate_actions_v4.csv"
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


def make_terminal_aware_engine(config, strategy, universe, tmap, cas, policy):
    limits = portfolio_risk_limits_from_config(config.risk)
    base_kwargs = dict(
        strategy=strategy,
        rebalance_policy=rebalance_policy_for(strategy),
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
    eng = TerminalAwareResearchEngine(terminal_map=tmap, policy=policy, **base_kwargs)
    eng._corporate_actions_for_run = cas
    return eng


def load_bars(path: pathlib.Path) -> list[Bar]:
    rows = []
    with path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            rows.append(
                Bar(
                    timestamp=ts,
                    symbol=row["symbol"],
                    timeframe=row.get("timeframe", "1Day") or "1Day",
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    market=Market.EQUITY,
                )
            )
    rows.sort(key=lambda b: b.timestamp)
    return rows


def load_terminal_map_for_universe(symbols: set[str]) -> dict[str, TerminalInfo]:
    worthless = set()
    if CA_V4.exists():
        for row in csv.DictReader(open(CA_V4)):
            if row["action_type"] == "worthless_removals":
                worthless.add(row["symbol"])
    tmap = {}
    for row in csv.DictReader(open(LIFETIMES_V4)):
        sym = row["canonical_symbol"]
        if sym not in symbols:
            continue
        last_bar = (
            datetime.fromisoformat(row["last_observed_bar"].replace("Z", "+00:00"))
            if row["last_observed_bar"]
            else None
        )
        if last_bar is None:
            continue
        dataset_end = datetime.fromisoformat("2026-07-31T04:00:00+00:00")
        if last_bar < dataset_end and row["current_status"] != "active":
            # All non-rename should haircut for T2/T3
            should_haircut = True
            ev = "VERIFIED_DELISTING" if sym in worthless else "POSSIBLE_DELISTING"
            tmap[sym] = TerminalInfo(
                entity_id=row["entity_id"],
                symbol=sym,
                display_symbol=sym,
                last_bar=last_bar,
                terminal_event_class=ev,
                evidence_class=row["pit_quality_tier"],
                should_haircut=should_haircut,
            )
    return tmap


def load_ca_for_universe(symbols: set[str]) -> list:
    # Load corporate actions for universe to attach to engine (dividend cash)
    from parayabuck.data.market_dataset import CorporateAction, CorporateActionType

    cas = []
    if not CA_V4.exists():
        return cas
    for row in csv.DictReader(open(CA_V4)):
        if row["symbol"] not in symbols:
            continue
        # Only cash dividends relevant for PnL; others are splits already in bars
        if row["action_type"] != "cash_dividends":
            continue
        try:
            ex_date = datetime.fromisoformat(row["ex_date"].replace("Z", "+00:00")) if row["ex_date"] else None
        except Exception:
            continue
        if ex_date is None:
            continue
        if ex_date.tzinfo is None:
            ex_date = ex_date.replace(tzinfo=UTC)
        val = float(row.get("rate") or 0)
        if not val or val <= 0:
            continue
        cas.append(
            CorporateAction(
                timestamp=ex_date,
                symbol=row["symbol"],
                action_type=CorporateActionType.DIVIDEND,
                value=val,
            )
        )
    return cas


def compute_metrics(result):
    eq = [p.equity for p in result.portfolio_history]
    rets = []
    for i in range(1, len(eq)):
        if eq[i - 1] <= 0:
            continue
        rets.append((eq[i] - eq[i - 1]) / eq[i - 1])
    total_return = (eq[-1] / eq[0] - 1) if eq and eq[0] else 0
    # CAGR
    if result.portfolio_history:
        start = result.portfolio_history[0].timestamp
        end = result.portfolio_history[-1].timestamp
        years = (end - start).days / 365.25 if (end - start).days > 0 else 1
        cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[0] > 0 else 0
    else:
        cagr = 0
    # Sharpe daily
    if len(rets) > 1:
        mean = statistics.mean(rets)
        stdev = statistics.pstdev(rets)
        sharpe = (mean / stdev * math.sqrt(252)) if stdev > 0 else 0
        # Sortino (downside)
        downside = [r for r in rets if r < 0]
        sortino = (
            (mean / statistics.pstdev(downside) * math.sqrt(252)) if downside and statistics.pstdev(downside) > 0 else 0
        )
    else:
        sharpe = sortino = 0
    # maxDD
    max_dd = 0
    peak = eq[0] if eq else 0
    for v in eq:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    # Calmar
    calmar = cagr / max_dd if max_dd > 0 else 0
    # exposure/holdings (actual, not zombie) — use portfolio_history holdings if present
    exposures = []
    holdings = []
    for p in result.portfolio_history:
        # Use holdings count from engine's live positions (result reports)
        holdings.append(len(p.holdings))
        # Exposure: sum market value / equity
        mv = sum(h.market_value for h in p.holdings.values()) if hasattr(p.holdings, "values") else 0
        exposures.append(mv / p.equity if p.equity else 0)
    avg_exp = statistics.mean(exposures) if exposures else 0
    avg_hold = statistics.mean(holdings) if holdings else 0
    return {
        "bars": len(result.bars) if hasattr(result, "bars") else len(eq),
        "days": len(eq),
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "maxDD": max_dd,
        "calmar": calmar,
        "avg_exposure": avg_exp,
        "avg_holdings": avg_hold,
        "ending_equity": eq[-1] if eq else 0,
        "starting_equity": eq[0] if eq else 0,
    }


def run_one(dataset_key: str, policy: str):
    path = DATASETS[dataset_key]
    bars = load_bars(path)
    symbols = set(b.symbol for b in bars)
    strat = EquityResidualMomentumStrategy()
    # For old dataset, no terminal map (all survivors), but use empty
    tmap = load_terminal_map_for_universe(symbols) if dataset_key != "OLD" else {}
    # For OLD, also no terminal events (all active)
    cas = load_ca_for_universe(symbols)
    # Build engine
    if policy == "T0":
        # Use T0 = stale (no terminal removal) — just run normal PortfolioBacktestEngine via TerminalAware with T0
        engine = TerminalAwareResearchEngine(
            strategy=strat,
            rebalance_policy=None,
            terminal_map=tmap,
            policy="T0",
            starting_equity=100_000,
            corporate_actions=cas,
        )
    else:
        engine = TerminalAwareResearchEngine(
            strategy=strat,
            rebalance_policy=None,
            terminal_map=tmap,
            policy=policy,
            starting_equity=100_000,
            corporate_actions=cas,
        )
    result = engine.run(bars, liquidate_at_end=True)
    metrics = compute_metrics(result)
    metrics["terminal_events"] = len(engine.terminal_events_fired)
    metrics["terminal_proceeds_total"] = sum(e.get("proceeds", 0) for e in engine.terminal_events_fired)
    metrics["dataset"] = dataset_key
    metrics["policy"] = policy
    metrics["symbols"] = len(symbols)
    metrics["bars_loaded"] = len(bars)
    return metrics, engine


if __name__ == "__main__":
    import time

    runs = [("OLD", "T0"), ("DEMO", "T0"), ("FULL", "T0"), ("FULL", "T1"), ("FULL", "T2"), ("FULL", "T3")]
    out = {}
    for ds, pol in runs:
        print(f"Running {ds}_{pol} ...", flush=True)
        t0 = time.time()
        m, eng = run_one(ds, pol)
        print(
            f"  done in {time.time() - t0:.1f}s: return {m['total_return']:.4f} sharpe {m['sharpe']:.3f} maxDD {m['maxDD']:.3f} exp {m['avg_exposure']:.3f} hold {m['avg_holdings']:.1f} term_ev {m['terminal_events']}"
        )
        out[f"{ds}_{pol}"] = m
    # Write results
    out_path = REPO / "data/research/full_survivorship_2026_08/corrected_v3_results_H.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")
