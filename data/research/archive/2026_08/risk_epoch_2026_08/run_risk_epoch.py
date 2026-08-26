#!/usr/bin/env python3
# ruff: noqa: E501
"""Mission 01 — Production risk parity and drawdown hysteresis research driver.

Runs the three required variants through the production harness:
  A. SHADOW_RISK (legacy research, existing behavior)
  B. PRODUCTION_RISK (exact paper latch)
  C. ONE_SHOT_HYSTERESIS_V1 (5% -> 50% de-risk -> re-arm at 3%)

Preserves identical data/universe/warmup/costs/execution/rebalance bands.

Outputs JSON artifacts under data/research/risk_epoch_2026_08/
and prints markdown tables for the report.

No project code is modified at runtime; all variants are harness parameters.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from parayabuck.backtest.product import (  # noqa: E402
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
    run_product_backtest,
)
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402

CONFIG_PATH = str(REPO / "config" / "settings.yaml")
OUT = REPO / "data" / "research" / "risk_epoch_2026_08"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
EQUITY_DIV_CSV = REPO / "data" / "historical" / "equity_1200_dividends.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_expanded_1h.csv"

EQ_START = datetime(2017, 1, 3, tzinfo=UTC)
EQ_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
CR_START = datetime(2021, 4, 1, tzinfo=UTC)
CR_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _metrics(result) -> dict:
    return {
        "total_return": result.total_return,
        "cagr": result.cagr,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "max_drawdown": result.max_drawdown,
        "max_drawdown_duration_days": result.max_drawdown_duration_days,
        "annualized_volatility": result.annualized_volatility,
        "turnover": result.turnover,
        "fee_total": result.fee_total,
        "slippage_cost_total": result.slippage_cost_total,
        "spread_cost_total": result.spread_cost_total,
        "average_cash_ratio": result.average_cash_ratio,
        "average_holdings_count": result.average_holdings_count,
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "maximum_concentration": result.maximum_concentration,
        "worst_month_return": result.worst_month_return,
        "worst_year_return": result.worst_year_return,
        "positive_month_ratio": result.positive_month_ratio,
        "duration_days": result.duration_days,
        "benchmark_return": result.benchmark_return,
        "dividend_total": result.dividend_total,
        "risk_mode": result.risk_mode,
        "drawdown_episodes": result.drawdown_episodes,
        "decisions_blocked": result.decisions_blocked,
        "latched_bars": result.latched_bars,
        "latched_days": result.latched_days,
        "defensive_de_risks": result.defensive_de_risks,
        "recovery_times_days": list(result.recovery_times_days),
        "final_drawdown_state": result.final_drawdown_state,
        "production_latched": result.production_latched,
        "shadow_halt_events": len(result.drawdown_halt_shadow_events),
        "production_halt_events": len(result.drawdown_halt_production_events),
        "time_to_recovery_days": result.time_to_recovery_days,
    }


def _run_one(config, bars, market, risk_mode, corporate_actions, universe, start, end, cost_mult, label):
    t0 = time.time()
    product = run_product_backtest(
        config,
        bars,
        market=market,
        start=start,
        end=end,
        corporate_actions=corporate_actions,
        universe=universe,
        persist=False,
        persist_artifacts=False,
        cost_multiplier=cost_mult,
        risk_mode=risk_mode,
    )
    dt = time.time() - t0
    metrics = _metrics(product.result)
    metrics["run_label"] = label
    metrics["elapsed_seconds"] = dt
    metrics["universe_hash"] = hash_point_in_time_universe(universe)
    metrics["universe_snapshots"] = len(product.universe.snapshots)
    # Rebalance-level summary for parity checks
    metrics["rebalance_reasons_sample"] = [r.risk_overlay_reasons for r in product.result.rebalances[:3]]
    return product, metrics


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config = load_config(CONFIG_PATH)
    print(f"Config: equity alloc {config.strategy.equity.maximum_allocation:.2f} crypto {config.strategy.crypto.maximum_allocation:.2f} gross {config.risk.max_gross_exposure_pct:.2f} daily {config.risk.daily_loss_limit_pct:.3f} dd {config.risk.max_drawdown_pct:.2f}")
    print(f"HEAD {Path(REPO/'.git').exists()}")
    # Load data
    print("Loading equity bars...")
    equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    equity_divs = load_csv_corporate_actions(EQUITY_DIV_CSV)
    print(f" equity bars {len(equity_bars)} divs {len(equity_divs)}")
    print("Loading crypto bars...")
    crypto_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    crypto_1d = aggregate_historical_bars(crypto_1h, "1d", require_complete=True)
    print(f" crypto 1h {len(crypto_1h)} 1d {len(crypto_1d)}")

    print("Building universes...")
    equity_universe = build_product_universe(config, equity_bars, market=Market.EQUITY, end=EQ_END)
    crypto_universe = build_product_universe(config, crypto_1h, market=Market.CRYPTO, end=CR_END, timeframe="1h")
    print(f" equity universe {len(equity_universe.snapshots)} hash {hash_point_in_time_universe(equity_universe)[:12]}")
    print(f" crypto universe {len(crypto_universe.snapshots)} hash {hash_point_in_time_universe(crypto_universe)[:12]}")

    all_results = {}
    risk_modes = ["shadow", "production", "one_shot_hysteresis_v1"]
    labels = {"shadow": "SHADOW_RISK", "production": "PRODUCTION_RISK", "one_shot_hysteresis_v1": "ONE_SHOT_HYSTERESIS_V1"}

    # Canonical full-window runs
    for mode in risk_modes:
        label = labels[mode]
        print(f"\n=== Equity {label} ===")
        _, m = _run_one(config, equity_bars, Market.EQUITY, mode, equity_divs, equity_universe, EQ_START, EQ_END, 1.0, f"equity_{mode}_1x")
        all_results[f"equity_{mode}_1x"] = m
        print(f"  Return {m['total_return']:.2%} CAGR {m['cagr']:.2%} Sharpe {m['sharpe']:.2f} maxDD {m['max_drawdown']:.2%} episodes {m['drawdown_episodes']} blocked {m['decisions_blocked']} latched {m['latched_days']} de_risk {m['defensive_de_risks']} state {m['final_drawdown_state']}")

        print(f"=== Crypto {label} ===")
        _, m2 = _run_one(config, crypto_1d, Market.CRYPTO, mode, None, crypto_universe, CR_START, CR_END, 1.0, f"crypto_{mode}_1x")
        all_results[f"crypto_{mode}_1x"] = m2
        print(f"  Return {m2['total_return']:.2%} CAGR {m2['cagr']:.2%} Sharpe {m2['sharpe']:.2f} maxDD {m2['max_drawdown']:.2%} episodes {m2['drawdown_episodes']} blocked {m2['decisions_blocked']}")

    # Cost stress 2x/3x for equity and crypto (only need 1x above, so 2x/3x now)
    for mode in risk_modes:
        for mult in [2.0, 3.0]:
            _, m = _run_one(config, equity_bars, Market.EQUITY, mode, equity_divs, equity_universe, EQ_START, EQ_END, mult, f"equity_{mode}_{mult:.0f}x")
            all_results[f"equity_{mode}_{mult:.0f}x"] = m
            _, m2 = _run_one(config, crypto_1d, Market.CRYPTO, mode, None, crypto_universe, CR_START, CR_END, mult, f"crypto_{mode}_{mult:.0f}x")
            all_results[f"crypto_{mode}_{mult:.0f}x"] = m2
            print(f"cost {mult}x {mode} eq CAGR {m['cagr']:.2%} cr CAGR {m2['cagr']:.2%}")

    # Combined metrics (robustness-pack semantics: combined = equity + crypto - 100k)
    # For each risk mode, combine the 1x curves where windows overlap (2021-04..2026-07)
    for mode in risk_modes:
        eq_key = f"equity_{mode}_1x"
        cr_key = f"crypto_{mode}_1x"
        # Need to recompute combined via separate harness? For now approximate using returns addition
        # We have the detailed weighted method via summing equity curves: done via research_helpers but we approximate here
        # Instead run a quick combined estimate using the same engine but summing is done in analysis stage
        pass

    # Walk-forward for equity (anchored 6-month folds from 2019-01)
    print("\n=== Walk-forward equity ===")
    try:
        from scripts.research_helpers import _calendar_fold_window  # type: ignore
    except ImportError:
        sys.path.insert(0, str(REPO / "scripts"))
        from research_helpers import _calendar_fold_window

    folds = _calendar_fold_window(datetime(2019, 1, 1, tzinfo=UTC), EQ_END, 6)
    print(f"  {len(folds)} folds")
    walk_results = {}
    for mode in risk_modes:
        fold_returns = []
        wins = 0
        baseline = all_results["equity_shadow_1x"]["cagr"]  # placeholder
        for idx, (fold_start, fold_end) in enumerate(folds):
            _, m = _run_one(config, equity_bars, Market.EQUITY, mode, equity_divs, equity_universe, fold_start, fold_end, 1.0, f"wf_eq_{mode}_{idx}")
            fold_returns.append(m["total_return"])
        walk_results[mode] = {
            "fold_returns": fold_returns,
            "positive": sum(1 for r in fold_returns if r > 0),
            "median": sorted(fold_returns)[len(fold_returns)//2] if fold_returns else 0,
            "mean": sum(fold_returns)/len(fold_returns) if fold_returns else 0,
            "worst": min(fold_returns) if fold_returns else 0,
            "best": max(fold_returns) if fold_returns else 0,
        }
        print(f"  {mode}: pos {walk_results[mode]['positive']}/{len(folds)} median {walk_results[mode]['median']:.2%} mean {walk_results[mode]['mean']:.2%}")

    # Regime breakdown (2020 crash, 2022 bear, 2024-2026)
    regimes = {
        "2020_crash": (datetime(2020, 2, 19, tzinfo=UTC), datetime(2020, 4, 30, tzinfo=UTC)),
        "2022_bear": (datetime(2022, 1, 1, tzinfo=UTC), datetime(2022, 12, 31, tzinfo=UTC)),
        "2024_2026": (datetime(2024, 1, 1, tzinfo=UTC), EQ_END),
    }
    regime_results = {}
    for name, (rstart, rend) in regimes.items():
        for mode in risk_modes:
            _, m = _run_one(config, equity_bars, Market.EQUITY, mode, equity_divs, equity_universe, rstart, rend, 1.0, f"regime_{name}_{mode}")
            regime_results[f"{name}_{mode}"] = m
            print(f" regime {name} {mode}: ret {m['total_return']:.2%} cagr {m['cagr']:.2%} maxDD {m['max_drawdown']:.2%}")

    # Persist artifacts
    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "equity_allocation": config.strategy.equity.maximum_allocation,
            "crypto_allocation": config.strategy.crypto.maximum_allocation,
            "gross": config.risk.max_gross_exposure_pct,
            "cash_buffer": config.risk.cash_buffer_pct,
            "daily_loss_limit_pct": config.risk.daily_loss_limit_pct,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
            "max_positions": config.risk.max_positions,
            "rebalance_band_pct": config.execution.rebalance_band_pct,
            "minimum_order_notional": config.execution.minimum_order_notional,
        },
        "data_hashes": {
            "equity_csv": file_hash(EQUITY_CSV),
            "div_csv": file_hash(EQUITY_DIV_CSV),
            "crypto_csv": file_hash(CRYPTO_CSV),
        },
        "results": all_results,
        "walk_forward": walk_results,
        "regimes": regime_results,
        "folds": [(s.isoformat(), e.isoformat()) for s, e in folds],
    }
    (OUT / "results.json").write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {OUT / 'results.json'}")

    # Also write CSV summary for report
    lines = ["label,total_return,cagr,sharpe,max_drawdown,episodes,blocked,latched_days,de_risks,state"]
    for k, v in all_results.items():
        if "1x" in k:
            lines.append(f"{k},{v['total_return']:.6f},{v['cagr']:.6f},{v['sharpe']:.4f},{v['max_drawdown']:.6f},{v['drawdown_episodes']},{v['decisions_blocked']},{v['latched_days']},{v['defensive_de_risks']},{v['final_drawdown_state']}")
    (OUT / "summary_1x.csv").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT / 'summary_1x.csv'}")


if __name__ == "__main__":
    main()
