#!/usr/bin/env python3
"""Offline paper shadow replay: V2 (production) vs V3 (MH_RAW candidate).

Mission 2026-08 §8: a PAPER-only shadow comparison. Production runtime is
NOT modified (no shadow hooks, no orders — the tool never submits anything);
instead this offline replay reproduces every scheduled equity decision and
logs, per decision timestamp:

* v2 ranks (full rank order from ``rank_candidates``);
* v3 ranks;
* v2 selected names / v3 selected names (top_n alpha targets);
* target overlap (count and Jaccard);
* turnover difference (sum of |v3_weight - v2_weight| over the union);
* hypothetical target weights for both strategies (pre-risk-overlay alpha
  targets; the production risk overlay applies identically to both).

The decision schedule is the REAL engine schedule: each strategy is run
through ``PortfolioBacktestEngine`` once and the union of its rebalance
decision timestamps defines the replay grid. At each timestamp a
point-in-time ``PortfolioContext`` is reconstructed from the raw bars and
the PIT universe (holdings/prices are irrelevant to these strategies'
targets, so the reconstruction is exact for ranks, selection and weights).

Usage:
    python scripts/research_mh_raw_shadow.py [--limit 50] [--smoke]
"""

from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from datetime import UTC, datetime
from pathlib import Path

from research_helpers import config_digest, dataset_identity, git_head

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import (
    EquityResidualMomentum1dV2,
    EquityResidualMomentum1dV3,
    rebalance_policy_for,
)
from parayabuck.portfolio.base import PortfolioContext

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/mh_raw_shadow_2026_08"

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


def _make_engine(config, strategy, universe) -> PortfolioBacktestEngine:
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
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="replay only the first N decisions")
    parser.add_argument("--smoke", action="store_true", help="short window (2024+)")
    args = parser.parse_args()

    config = load_config()
    print("loading dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(DIVIDENDS_CSV)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    equity_bars = sorted(
        (bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == "1d"),
        key=lambda bar: bar.timestamp,
    )
    by_symbol: dict[str, list[Bar]] = {}
    for bar in equity_bars:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    if args.smoke:
        equity_bars = [bar for bar in equity_bars if bar.timestamp >= datetime(2024, 1, 1, tzinfo=UTC)]
    print(f"{len(equity_bars)} equity bars, {len(actions)} dividend actions", flush=True)

    v2 = EquityResidualMomentum1dV2(dict(FROZEN_PARAMS))
    v3 = EquityResidualMomentum1dV3(dict(FROZEN_PARAMS))

    # Real engine schedule: the union of both strategies' decision timestamps.
    decision_times: set[datetime] = set()
    for strategy in (v2, v3):
        engine = _make_engine(config, strategy, universe)
        result = engine.run(equity_bars, warmup=[], corporate_actions=actions)
        decision_times.update(record.decision_timestamp for record in result.rebalances)
    ordered_times = sorted(decision_times)
    print(f"{len(ordered_times)} scheduled decisions", flush=True)
    if args.limit:
        ordered_times = ordered_times[: args.limit]

    rows: list[dict[str, object]] = []
    # Precompute per-symbol sorted bars once; each decision slices with a
    # binary search instead of scanning every bar (the engine feeds the same
    # point-in-time history).
    symbols_sorted = sorted(by_symbol)
    bar_times = {symbol: [bar.timestamp for bar in series] for symbol, series in by_symbol.items()}
    for index, timestamp in enumerate(ordered_times):
        history: dict[str, tuple[Bar, ...]] = {}
        for symbol in symbols_sorted:
            # All bars at or before the decision (the engine feeds the same).
            times = bar_times[symbol]
            cutoff = bisect_right(times, timestamp)
            if cutoff:
                history[symbol] = tuple(by_symbol[symbol][:cutoff])
        context = PortfolioContext(
            decision_timestamp=timestamp,
            market=Market.EQUITY,
            timeframe="1d",
            history=history,
            universe=universe,
            universe_version="shadow-replay",
            equity=0.0,
            holdings={},
            prices={},
            benchmark_symbol="SPY",
        )
        v2_ranked = v2.rank_candidates(context)
        v3_ranked = v3.rank_candidates(context)
        v2_portfolio = v2.decide(context)
        v3_portfolio = v3.decide(context)
        v2_selected = tuple(weight.symbol for weight in v2_portfolio.weights)
        v3_selected = tuple(weight.symbol for weight in v3_portfolio.weights)
        v2_weights = {weight.symbol: weight.weight for weight in v2_portfolio.weights}
        v3_weights = {weight.symbol: weight.weight for weight in v3_portfolio.weights}
        overlap = len(set(v2_selected) & set(v3_selected))
        union = set(v2_selected) | set(v3_selected)
        jaccard = overlap / len(union) if union else 0.0
        turnover_diff = sum(abs(v3_weights.get(symbol, 0.0) - v2_weights.get(symbol, 0.0)) for symbol in union)
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "v2_ranks": list(v2_ranked),
                "v3_ranks": list(v3_ranked),
                "v2_selected": list(v2_selected),
                "v3_selected": list(v3_selected),
                "target_overlap": overlap,
                "target_jaccard": round(jaccard, 4),
                "turnover_difference": round(turnover_diff, 6),
                "v2_target_weights": {symbol: round(weight, 6) for symbol, weight in v2_weights.items()},
                "v3_target_weights": {symbol: round(weight, 6) for symbol, weight in v3_weights.items()},
            }
        )
        if (index + 1) % 50 == 0 or index == len(ordered_times) - 1:
            print(f"replayed {index + 1}/{len(ordered_times)} decisions", flush=True)

    overlaps = [row["target_overlap"] for row in rows]  # type: ignore[union-attr]
    turnovers = [row["turnover_difference"] for row in rows]  # type: ignore[union-attr]
    divergences = sum(1 for row in rows if row["v2_selected"] != row["v3_selected"])  # type: ignore[comparison-overlap]
    summary = {
        "decisions": len(rows),
        "mean_target_overlap": round(sum(overlaps) / len(overlaps), 3) if overlaps else 0.0,
        "mean_target_jaccard": round(
            sum(row["target_jaccard"] for row in rows) / len(rows),
            4,  # type: ignore[union-attr]
        )
        if rows
        else 0.0,
        "mean_turnover_difference": round(sum(turnovers) / len(turnovers), 6) if turnovers else 0.0,
        "selection_divergent_decisions": divergences,
        "selection_divergent_fraction": round(divergences / len(rows), 4) if rows else 0.0,
    }
    artifact = {
        "mission": "equity_mh_raw_promotion_2026_08",
        "shadow_type": "offline paper replay (never submits orders; production runtime untouched)",
        "executable_strategy": "equity_residual_momentum_v2",
        "shadow_strategy": "equity_residual_momentum_v3",
        "implementation_commit": git_head(),
        "research_commit": git_head(),
        "dataset": dataset_identity(),
        "config_digest_sha256": config_digest(config),
        "generated_at": datetime.now(UTC).isoformat(),
        "eval_start": EVAL_START.date().isoformat(),
        "eval_end": EVAL_END.date().isoformat(),
        "note": (
            "target weights are pre-risk-overlay alpha targets; the production "
            "risk overlay applies identically to both strategies, so deltas are "
            "overlay-invariant. turnover_difference is the per-decision sum of "
            "absolute target-weight differences, not cumulative turnover."
        ),
        "summary": summary,
        "decisions": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / ("shadow_log_smoke.json" if args.smoke else "shadow_log.json")
    path.write_text(json.dumps(artifact, indent=1))
    print(f"shadow replay written to {path}")
    print("summary:", json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
