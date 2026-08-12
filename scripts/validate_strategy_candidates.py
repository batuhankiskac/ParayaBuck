#!/usr/bin/env python3
"""Multi-market strategy candidate validation harness (research only).

Evaluates the mission's two new production strategy candidates through the
SAME production code paths used by paper/live:

  * strategy logic      -> parayabuck.portfolio (equity_residual_momentum_v1,
                           crypto_time_series_momentum_v1)
  * historical universe -> build_product_universe (production scanner policy,
                           point-in-time, strictly trailing data)
  * risk overlay        -> PortfolioRiskOverlay / PortfolioRiskLimits
  * execution policy    -> PortfolioExecutionPolicy (shared band/notional)
  * cost model          -> PortfolioBacktestEngine (slippage/spread/effective
                           dated fees, crypto taker 25 bps)
  * drawdown policy     -> shadow semantics (production halts recorded, curve
                           kept trading), identical to prior research runs

Fixed primary specifications only: no parameter grid. Walk-forward is
anchored (train = everything before the fold), fold parameters are never
touched after seeing a fold.

Outputs:
  data/research/strategy_validation_v2/equity.json
  data/research/strategy_validation_v2/crypto.json
  printed summary JSON

Usage:
  python scripts/validate_strategy_candidates.py equity
  python scripts/validate_strategy_candidates.py crypto
  python scripts/validate_strategy_candidates.py all
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars

REPO = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO / "data" / "research" / "strategy_validation_v2"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_2024_2026.csv"
FOUNDATION_REPORT = REPO / "data" / "research" / "strategy_foundation_v1" / "report.json"

EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
CRYPTO_EVAL_START = datetime(2024, 12, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

EQUITY_RESIDUAL_PARAMETERS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
CRYPTO_TSM_PARAMETERS = {
    "lookback_days": 30,
    "trend_days": 15,
    "volatility_window_days": 15,
    "skip_bars": 1,
    "top_n": 3,
    "max_single_weight": 0.5,
    "rebalance_hours": 168,
}


def _fold_window(
    start: datetime, end: datetime, step_months: int, warmup_months: int
) -> list[tuple[datetime, datetime]]:
    """Anchored walk-forward test windows (train = everything before fold start)."""
    folds: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        fold_end = min(cursor + timedelta(days=30 * step_months), end)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor += timedelta(days=30 * step_months)
    return folds


def _fold_stats(result) -> dict[str, object]:
    return {
        "start": result.start_date,
        "end": result.end_date,
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "turnover": round(result.turnover, 6),
        "trade_count": len(result.closed_position_events),
        "avg_holdings": round(result.average_holdings_count, 3),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
    }


def _concentration(result) -> dict[str, object]:
    """PnL concentration: largest single-symbol realized contribution shares."""
    contributions = sorted(
        (
            (symbol, pnl)
            for symbol, pnl in ((item.symbol, item.realized_pnl) for item in result.per_symbol)
        ),
        key=lambda item: (-item[1], item[0]),
    )
    positive_total = sum(pnl for _symbol, pnl in contributions if pnl > 0)
    top = contributions[:3]
    return {
        "top_1": {"symbol": top[0][0], "realized_pnl": round(top[0][1], 2)} if top else None,
        "top_2": (
            {"symbol": top[1][0], "realized_pnl": round(top[1][1], 2)} if len(top) > 1 else None
        ),
        "top_3": (
            {"symbol": top[2][0], "realized_pnl": round(top[2][1], 2)} if len(top) > 2 else None
        ),
        "top1_share_of_positive_pnl": (
            round(top[0][1] / positive_total, 4) if positive_total > 0 else 0.0
        ),
        "top2_share_of_positive_pnl": (
            round(sum(item[1] for item in top[:2]) / positive_total, 4)
            if positive_total > 0
            else 0.0
        ),
        "top3_share_of_positive_pnl": (
            round(sum(item[1] for item in top[:3]) / positive_total, 4)
            if positive_total > 0
            else 0.0
        ),
        "max_single_weight": round(result.maximum_concentration, 4),
        "symbol_count": len(contributions),
    }


def _cost_stress(
    config,
    bars,
    *,
    market: Market,
    strategy_name: str,
    parameters: dict[str, object],
    strategy_timeframe: str | None,
    start: datetime,
    end: datetime,
    universe=None,
) -> dict[str, object]:
    runs = {}
    for multiplier in (1.0, 2.0, 3.0):
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=parameters,
            strategy_timeframe=strategy_timeframe,
            start=start,
            end=end,
            cost_multiplier=multiplier,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        result = run.result
        gross = result.gross_return
        total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
        avg_equity = _avg_equity(result)
        gross_pnl = result.starting_equity * gross
        runs[f"{multiplier:g}x"] = {
            "return": round(result.total_return, 6),
            "gross_return": round(gross, 6),
            "cagr": round(result.cagr, 6),
            "sharpe": round(result.sharpe, 6),
            "sortino": round(result.sortino, 6),
            "calmar": round(result.calmar, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "profit_factor": round(_profit_factor(result), 4),
            "turnover": round(result.turnover, 6),
            "fees_total": round(total_cost, 2),
            "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
            "fees_pct_of_gross_pnl": (
                round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
            ),
            "avg_holdings": round(result.average_holdings_count, 3),
            "avg_cash_ratio": round(result.average_cash_ratio, 4),
            "rebalance_count": result.rebalance_count,
            "trade_count": len(result.closed_position_events),
            "concentration": _concentration(result),
            "sector_coverage": _sector_coverage(result),
        }
    return runs


def _avg_equity(result) -> float:
    curve = result.equity_curve
    return sum(curve) / len(curve) if curve else result.starting_equity


def _profit_factor(result) -> float:
    gross_profit = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in result.realized_pnls if pnl < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else 0.0


def _sector_coverage(result) -> dict[str, object]:
    """Best-effort sector mix of traded symbols using the static sector map."""
    from parayabuck.markets.sectors import sector_of

    traded = {item.symbol for item in result.per_symbol}
    counts: dict[str, int] = {}
    known = 0
    for symbol in sorted(traded):
        sector = sector_of(symbol)
        if sector != "other":
            known += 1
        counts[sector] = counts.get(sector, 0) + 1
    return {
        "traded_symbols": len(traded),
        "sector_known": known,
        "sector_distribution": dict(sorted(counts.items(), key=lambda item: -item[1])),
    }


def _benchmark_bars(bars: list[Bar], symbol: str, market: Market) -> list[Bar]:
    return sorted(
        (bar for bar in bars if bar.symbol == symbol and bar.market == market),
        key=lambda bar: bar.timestamp,
    )


def _buy_and_hold_return(bars: list[Bar]) -> float:
    if len(bars) < 2 or bars[0].close <= 0:
        return 0.0
    return bars[-1].close / bars[0].close - 1.0


def _monthly_returns_from_bars(bars: list[Bar]) -> list[float]:
    by_month: dict[tuple[int, int], float] = {}
    for bar in bars:
        key = (bar.timestamp.year, bar.timestamp.month)
        by_month[key] = bar.close
    months = sorted(by_month)
    returns = []
    for index in range(1, len(months)):
        previous, current = by_month[months[index - 1]], by_month[months[index]]
        if previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def _sharpe_from_returns(returns: list[float], periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns)
    return mean / std * math.sqrt(periods_per_year) if std > 0 else 0.0


def _max_drawdown_from_bars(bars: list[Bar]) -> float:
    peak = 0.0
    max_dd = 0.0
    for bar in bars:
        peak = max(peak, bar.close)
        if peak > 0:
            max_dd = max(max_dd, (peak - bar.close) / peak)
    return max_dd


def validate_equity(config) -> dict[str, object]:
    print("Loading equity dataset...")
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print(f"  {len(bars):,} bars")
    spy = _benchmark_bars(bars, "SPY", Market.EQUITY)
    spy_return = _buy_and_hold_return(spy)
    spy_sharpe = _sharpe_from_returns(
        _monthly_returns_from_bars([bar for bar in spy if bar.timestamp >= EQUITY_START]), 12
    )
    spy_max_dd = _max_drawdown_from_bars(spy)

    print("Building point-in-time equity universe (production scanner policy)...")
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EQUITY_END)
    snapshots = universe.snapshots
    candidate_counts = [snapshot.candidate_count for snapshot in snapshots]
    selected_counts = [len(snapshot.symbols) for snapshot in snapshots]
    print(
        f"  snapshots={len(snapshots)} candidates avg={statistics.mean(candidate_counts):.0f} "
        f"selected avg={statistics.mean(selected_counts):.0f}"
    )

    summary: dict[str, object] = {
        "strategy": "equity_residual_momentum_v1",
        "parameters": EQUITY_RESIDUAL_PARAMETERS,
        "dataset": str(EQUITY_CSV.name),
        "evaluation_window": [EQUITY_START.isoformat(), EQUITY_END.isoformat()],
        "warmup": "2016 full year (formation + beta + scanner lookback)",
        "universe": {
            "snapshots": len(snapshots),
            "avg_candidate_pool": round(statistics.mean(candidate_counts), 1),
            "avg_stream_selected": round(statistics.mean(selected_counts), 1),
            "methodology": universe.methodology,
        },
        "benchmarks": {
            "SPY_buy_and_hold_return": round(spy_return, 6),
            "SPY_monthly_sharpe": round(spy_sharpe, 4),
            "SPY_max_drawdown": round(spy_max_dd, 6),
            "note": "B0/B1 diversified ETF reference lives in strategy_foundation_v1/report.json",
        },
    }
    try:
        foundation = json.loads(FOUNDATION_REPORT.read_text(encoding="utf-8"))
        summary["benchmarks"]["B0_full_period_1x"] = foundation.get("full_period_1x", {}).get("B0")
        summary["benchmarks"]["B1_full_period_1x"] = foundation.get("full_period_1x", {}).get("B1")
    except (OSError, json.JSONDecodeError):
        pass

    print("Full-period runs (1x/2x/3x cost)...")
    summary["full_period"] = _cost_stress(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v1",
        parameters=EQUITY_RESIDUAL_PARAMETERS,
        strategy_timeframe="1d",
        start=EQUITY_START,
        end=EQUITY_END,
        universe=universe,
    )

    print("Anchored walk-forward (6-month folds, 2y warmup)...")
    folds = _fold_window(datetime(2018, 1, 2, tzinfo=UTC), EQUITY_END, 6, 24)
    fold_results = []
    for index, (fold_start, fold_end) in enumerate(folds, start=1):
        run = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name="equity_residual_momentum_v1",
            parameters=EQUITY_RESIDUAL_PARAMETERS,
            strategy_timeframe="1d",
            start=fold_start,
            end=fold_end,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        stats = _fold_stats(run.result)
        stats["top_contributors"] = [
            {"symbol": item.symbol, "realized_pnl": round(item.realized_pnl, 2)}
            for item in sorted(run.result.per_symbol, key=lambda item: -item.realized_pnl)[:3]
        ]
        fold_results.append(stats)
        print(
            f"  fold {index:02d} {fold_start.date()}->{fold_end.date()} "
            f"return={stats['return']:.2%} sharpe={stats['sharpe']:.2f} "
            f"mdd={stats['max_drawdown']:.2%}"
        )
    positive = sum(1 for fold in fold_results if fold["return"] > 0)
    summary["walk_forward"] = {
        "folds": fold_results,
        "positive_fold_count": positive,
        "total_fold_count": len(fold_results),
        "positive_fold_ratio": round(positive / len(fold_results), 4) if fold_results else 0.0,
        "mean_fold_return": round(statistics.fmean([f["return"] for f in fold_results]), 6)
        if fold_results
        else 0.0,
        "median_fold_return": round(statistics.median([f["return"] for f in fold_results]), 6)
        if fold_results
        else 0.0,
    }
    return summary


def validate_crypto(config) -> dict[str, object]:
    print("Loading crypto dataset...")
    bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    print(f"  {len(bars_1h):,} 1h bars")
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    bars_4h = aggregate_historical_bars(bars_1h, "4h", require_complete=True)
    print(f"  aggregated {len(bars_1d):,} 1d / {len(bars_4h):,} 4h bars")

    print("Building point-in-time crypto universe from scanner timeframe (1h)...")
    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=CRYPTO_END)
    snapshots = universe.snapshots
    print(f"  snapshots={len(snapshots)}")

    btc_1d = _benchmark_bars(bars_1d, "BTC/USD", Market.CRYPTO)
    eth_1d = _benchmark_bars(bars_1d, "ETH/USD", Market.CRYPTO)
    eval_btc = [bar for bar in btc_1d if bar.timestamp >= CRYPTO_EVAL_START]
    eval_eth = [bar for bar in eth_1d if bar.timestamp >= CRYPTO_EVAL_START]
    btc_return = _buy_and_hold_return(eval_btc)
    eth_return = _buy_and_hold_return(eval_eth)
    btc_sharpe = _sharpe_from_returns(_monthly_returns_from_bars(eval_btc), 12)
    btc_max_dd = _max_drawdown_from_bars(eval_btc)

    summary: dict[str, object] = {
        "strategy": "crypto_time_series_momentum_v1",
        "parameters": CRYPTO_TSM_PARAMETERS,
        "dataset": str(CRYPTO_CSV.name),
        "evaluation_window": [CRYPTO_EVAL_START.isoformat(), CRYPTO_END.isoformat()],
        "warmup": "2024-08-01 .. 2024-11-30 (lookback + scanner lookback)",
        "universe": {
            "snapshots": len(snapshots),
            "methodology": universe.methodology,
            "scanner_timeframe": "1h",
        },
        "benchmarks": {
            "BTC_buy_and_hold_return": round(btc_return, 6),
            "BTC_monthly_sharpe": round(btc_sharpe, 4),
            "BTC_max_drawdown": round(btc_max_dd, 6),
            "ETH_buy_and_hold_return": round(eth_return, 6),
            "BTC_ETH_static_50_50_return": round(0.5 * (btc_return + eth_return), 6),
            "note": "static 50/50 is buy-and-hold, no rebalancing, no costs",
        },
    }

    variants: dict[str, object] = {}
    for timeframe in ("1d", "4h"):
        bars = bars_1d if timeframe == "1d" else bars_4h
        print(f"Full-period runs on {timeframe} (1x/2x/3x cost)...")
        variants[timeframe] = _cost_stress(
            config,
            bars,
            market=Market.CRYPTO,
            strategy_name="crypto_time_series_momentum_v1",
            parameters=CRYPTO_TSM_PARAMETERS,
            strategy_timeframe=timeframe,
            start=CRYPTO_EVAL_START,
            end=CRYPTO_END,
            universe=universe,
        )
        print(f"Anchored walk-forward on {timeframe} (4-month folds)...")
        folds = _fold_window(datetime(2025, 1, 1, tzinfo=UTC), CRYPTO_END, 4, 5)
        fold_results = []
        for index, (fold_start, fold_end) in enumerate(folds, start=1):
            run = run_product_backtest(
                config,
                bars,
                market=Market.CRYPTO,
                strategy_name="crypto_time_series_momentum_v1",
                parameters=CRYPTO_TSM_PARAMETERS,
                strategy_timeframe=timeframe,
                start=fold_start,
                end=fold_end,
                cost_multiplier=1.0,
                persist=False,
                persist_artifacts=False,
                universe=universe,
            )
            result = run.result
            stats = _fold_stats(result)
            stats["btc_weight_share"] = round(_btc_weight_share(result), 4)
            fold_results.append(stats)
            print(
                f"  fold {index:02d} {fold_start.date()}->{fold_end.date()} "
                f"return={stats['return']:.2%} sharpe={stats['sharpe']:.2f} "
                f"mdd={stats['max_drawdown']:.2%}"
            )
        positive = sum(1 for fold in fold_results if fold["return"] > 0)
        variants[timeframe]["walk_forward"] = {
            "folds": fold_results,
            "positive_fold_count": positive,
            "total_fold_count": len(fold_results),
            "positive_fold_ratio": round(positive / len(fold_results), 4) if fold_results else 0.0,
        }
        one_x = variants[timeframe]["1x"]
        one_x["btc_dependence"] = {
            "btc_weight_share_full_period": round(_btc_weight_share_avg(run.result), 4),
            "btc_share_of_top_contributions": round(_btc_contribution_share(run.result), 4),
            "stablecoin_pairs_traded": 0,
            "note": "BTC dependence measured as average BTC weight share of the crypto sleeve",
        }
    summary["variants"] = variants
    return summary


def _btc_weight_share(result) -> float:
    """Average BTC weight among the strategy's target weights over the run."""
    shares: list[float] = []
    for _timestamp, weights in result.weight_history:
        btc = weights.get("BTC/USD", 0.0)
        invested = sum(weights.values())
        if invested > 0:
            shares.append(btc / invested)
    return statistics.fmean(shares) if shares else 0.0


def _btc_weight_share_avg(result) -> float:
    return _btc_weight_share(result)


def _btc_contribution_share(result) -> float:
    btc = next((item.realized_pnl for item in result.per_symbol if item.symbol == "BTC/USD"), 0.0)
    total = sum(item.realized_pnl for item in result.per_symbol)
    return btc / total if total != 0 else 0.0


def validate_references(config) -> dict[str, object]:
    """Reference/diagnostic runs of the frozen V1/V2 families on the same data,
    cost model and risk policy (historical reference ONLY, never re-optimized)."""
    references: dict[str, object] = {"equity": {}, "crypto": {}}

    print("Reference equity runs (dynamic_trend V1, long-horizon V2, 1x cost)...")
    equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    equity_universe = build_product_universe(
        config, equity_bars, market=Market.EQUITY, end=EQUITY_END
    )
    for name, parameters in (
        ("equity_dynamic_trend_1d_v1", None),
        ("equity_long_horizon_momentum_1d_v2", None),
    ):
        run = run_product_backtest(
            config,
            equity_bars,
            market=Market.EQUITY,
            strategy_name=name,
            parameters=parameters,
            strategy_timeframe="1d",
            start=EQUITY_START,
            end=EQUITY_END,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            universe=equity_universe,
        )
        result = run.result
        references["equity"][name] = {
            "return": round(result.total_return, 6),
            "cagr": round(result.cagr, 6),
            "sharpe": round(result.sharpe, 6),
            "sortino": round(result.sortino, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "profit_factor": round(_profit_factor(result), 4),
            "trade_count": len(result.closed_position_events),
            "turnover": round(result.turnover, 6),
            "avg_holdings": round(result.average_holdings_count, 3),
            "avg_cash_ratio": round(result.average_cash_ratio, 4),
        }
        print(f"  {name}: ret={result.total_return:.2%} sharpe={result.sharpe:.2f}")

    print("Reference crypto run (old cross-sectional 1h, 1x cost)...")
    crypto_bars = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    crypto_universe = build_product_universe(
        config, crypto_bars, market=Market.CRYPTO, end=CRYPTO_END
    )
    run = run_product_backtest(
        config,
        crypto_bars,
        market=Market.CRYPTO,
        strategy_name="crypto_cross_sectional_momentum_1h_v1",
        parameters=None,
        strategy_timeframe="1h",
        start=CRYPTO_EVAL_START,
        end=CRYPTO_END,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=crypto_universe,
    )
    result = run.result
    references["crypto"]["crypto_cross_sectional_momentum_1h_v1"] = {
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "profit_factor": round(_profit_factor(result), 4),
        "trade_count": len(result.closed_position_events),
        "turnover": round(result.turnover, 6),
        "avg_holdings": round(result.average_holdings_count, 3),
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
    }
    print(f"  old 1h cross-sectional: ret={result.total_return:.2%} sharpe={result.sharpe:.2f}")
    return references


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_strategy_candidates")
    parser.add_argument("target", choices=("equity", "crypto", "all", "references"))
    parser.add_argument("--config", default=str(REPO / "config" / "settings.yaml"))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"generated_at": datetime.now(UTC).isoformat()}
    if args.target in {"equity", "all"}:
        report["equity"] = validate_equity(config)
        (OUTPUT_DIR / "equity.json").write_text(
            json.dumps(report["equity"], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    if args.target in {"crypto", "all"}:
        report["crypto"] = validate_crypto(config)
        (OUTPUT_DIR / "crypto.json").write_text(
            json.dumps(report["crypto"], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    if args.target in {"references", "all"}:
        report["references"] = validate_references(config)
        (OUTPUT_DIR / "references.json").write_text(
            json.dumps(report["references"], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
