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
  data/research/strategy_validation_v3/reversal.json
  printed summary JSON

Usage:
  python scripts/validate_strategy_candidates.py equity
  python scripts/validate_strategy_candidates.py crypto
  python scripts/validate_strategy_candidates.py reversal
  python scripts/validate_strategy_candidates.py all
"""

from __future__ import annotations

import argparse
import bisect
import calendar
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
OUTPUT_DIR_V3 = REPO / "data" / "research" / "strategy_validation_v3"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_2024_2026.csv"
CRYPTO_CSV_EXTENDED = REPO / "data" / "historical" / "crypto_18_1h_extended.csv"
FOUNDATION_REPORT = REPO / "data" / "research" / "strategy_foundation_v1" / "report.json"

EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
CRYPTO_EVAL_START = datetime(2024, 12, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
# Frozen reversal evaluation window (warmup 2021-01-01 .. 2021-03-31).
REVERSAL_EVAL_START = datetime(2021, 4, 1, tzinfo=UTC)
REVERSAL_STRATEGY = "crypto_cross_sectional_reversal_v1"
CRYPTO_REVERSAL_PARAMETERS = {
    "formation_bars": 70,
    "top_n": 2,
    "rebalance_hours": 168,
}

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


def _calendar_fold_window(
    start: datetime, end: datetime, step_months: int
) -> list[tuple[datetime, datetime]]:
    """Anchored walk-forward windows aligned to whole calendar months.

    ``_fold_window`` steps by 30-day blocks; the frozen reversal walk-forward
    is specified as 6-month folds from 2022-01-01 to CRYPTO_END (10 folds,
    Jan/Jul starts), which requires calendar-month stepping: 2022-01-01,
    2022-07-01, ..., 2026-07-01. Folds tile the window end-to-end.
    """
    folds: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        month_index = cursor.year * 12 + (cursor.month - 1) + step_months
        year, month0 = divmod(month_index, 12)
        month = month0 + 1
        day = min(cursor.day, calendar.monthrange(year, month)[1])
        fold_end = min(datetime(year, month, day, tzinfo=cursor.tzinfo), end)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor = fold_end
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


def validate_reversal(config, *, smoke: bool = False) -> dict[str, object]:
    """Frozen cross-sectional reversal validation on the EXTENDED crypto data.

    Production-parity constraints (Wave 1 audit, all P1):
      * P1-R1: the PIT universe is built ONCE from the extended 1h bars
        (scanner metric calibration hourly) and passed into every backtest.
      * P1-R2: the scanner config stays 1h (never touched).
      * P1-R4: weekly rebalance = rebalance_hours 168 (frozen parameters).
      * Eval window frozen 2021-04-01 .. CRYPTO_END; walk-forward frozen:
        anchored calendar 6-month folds from 2022-01-01 (10 folds).
    """
    print("Loading extended crypto dataset (1h)...")
    bars_1h = load_csv_bars(CRYPTO_CSV_EXTENDED, default_market=Market.CRYPTO)
    print(f"  {len(bars_1h):,} 1h bars")
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    print(f"  aggregated {len(bars_1d):,} 1d bars (require_complete=True)")

    eval_end = min(REVERSAL_EVAL_START + timedelta(days=92), CRYPTO_END) if smoke else CRYPTO_END
    print(
        f"  evaluation window: {REVERSAL_EVAL_START.date()} .. {eval_end.date()}"
        f"{' (smoke)' if smoke else ''}"
    )

    print("Building point-in-time crypto universe from 1h scanner bars (P1-R1)...")
    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=CRYPTO_END)
    snapshots = universe.snapshots
    candidate_counts = [snapshot.candidate_count for snapshot in snapshots]
    print(f"  snapshots={len(snapshots)} candidates avg={statistics.fmean(candidate_counts):.1f}")

    symbols = sorted({bar.symbol for bar in bars_1d})
    dataset = {
        "source": CRYPTO_CSV_EXTENDED.name,
        "window": [
            min(bar.timestamp for bar in bars_1d).isoformat(),
            max(bar.timestamp for bar in bars_1d).isoformat(),
        ],
        "symbols": symbols,
        "symbol_count": len(symbols),
        "bars_1h": len(bars_1h),
        "bars_1d": len(bars_1d),
        "earliest_date": min(bar.timestamp for bar in bars_1d).isoformat(),
        "aggregation": "1h -> 1d via aggregate_historical_bars(require_complete=True)",
    }

    print("Benchmarks over the evaluation window (BTC/ETH buy-and-hold)...")
    btc_1d = _benchmark_bars(bars_1d, "BTC/USD", Market.CRYPTO)
    eth_1d = _benchmark_bars(bars_1d, "ETH/USD", Market.CRYPTO)
    eval_btc = [bar for bar in btc_1d if REVERSAL_EVAL_START <= bar.timestamp <= eval_end]
    eval_eth = [bar for bar in eth_1d if REVERSAL_EVAL_START <= bar.timestamp <= eval_end]
    btc_return = _buy_and_hold_return(eval_btc)
    eth_return = _buy_and_hold_return(eval_eth)
    btc_sharpe = _sharpe_from_returns(_monthly_returns_from_bars(eval_btc), 12)
    btc_max_dd = _max_drawdown_from_bars(eval_btc)
    eth_sharpe = _sharpe_from_returns(_monthly_returns_from_bars(eval_eth), 12)
    eth_max_dd = _max_drawdown_from_bars(eval_eth)
    benchmarks: dict[str, object] = {
        "window": [REVERSAL_EVAL_START.isoformat(), eval_end.isoformat()],
        "BTC_buy_and_hold_return": round(btc_return, 6),
        "BTC_monthly_sharpe": round(btc_sharpe, 4),
        "BTC_max_drawdown": round(btc_max_dd, 6),
        "ETH_buy_and_hold_return": round(eth_return, 6),
        "ETH_monthly_sharpe": round(eth_sharpe, 4),
        "ETH_max_drawdown": round(eth_max_dd, 6),
        "BTC_ETH_static_50_50_return": round(0.5 * (btc_return + eth_return), 6),
        "note": "static 50/50 is buy-and-hold, no rebalancing, no costs",
    }

    print("Full-period reversal runs on 1d (1x/2x/3x cost)...")
    full_period = _cost_stress(
        config,
        bars_1d,
        market=Market.CRYPTO,
        strategy_name=REVERSAL_STRATEGY,
        parameters=CRYPTO_REVERSAL_PARAMETERS,
        strategy_timeframe="1d",
        start=REVERSAL_EVAL_START,
        end=eval_end,
        universe=universe,
    )

    print("Reversal 1x run (contribution analysis + benchmark entry)...")
    reversal_1x = run_product_backtest(
        config,
        bars_1d,
        market=Market.CRYPTO,
        strategy_name=REVERSAL_STRATEGY,
        parameters=CRYPTO_REVERSAL_PARAMETERS,
        strategy_timeframe="1d",
        start=REVERSAL_EVAL_START,
        end=eval_end,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=universe,
    )
    reversal_result = reversal_1x.result
    benchmarks[REVERSAL_STRATEGY + "_1x"] = _run_summary_stats(reversal_result)

    print("Reference: old TSM on 1d (extended window, 1x cost)...")
    old_tsm = run_product_backtest(
        config,
        bars_1d,
        market=Market.CRYPTO,
        strategy_name="crypto_time_series_momentum_v1",
        parameters=CRYPTO_TSM_PARAMETERS,
        strategy_timeframe="1d",
        start=REVERSAL_EVAL_START,
        end=eval_end,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=universe,
    )
    benchmarks["crypto_time_series_momentum_v1_1d_1x"] = _run_summary_stats(old_tsm.result)
    print(f"  old TSM 1d: ret={old_tsm.result.total_return:.2%} sharpe={old_tsm.result.sharpe:.2f}")

    print("Reference: old cross-sectional 1h on extended 1h bars (1x cost, P1-R1 universe)...")
    old_csm = run_product_backtest(
        config,
        bars_1h,
        market=Market.CRYPTO,
        strategy_name="crypto_cross_sectional_momentum_1h_v1",
        parameters=config.strategy.crypto.parameters,
        strategy_timeframe="1h",
        start=REVERSAL_EVAL_START,
        end=eval_end,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=universe,
    )
    benchmarks["crypto_cross_sectional_momentum_1h_v1_1x"] = _run_summary_stats(old_csm.result)
    print(f"  old CSM 1h: ret={old_csm.result.total_return:.2%} sharpe={old_csm.result.sharpe:.2f}")

    print("Anchored walk-forward (calendar 6-month folds from 2022-01-01)...")
    folds = _calendar_fold_window(datetime(2022, 1, 1, tzinfo=UTC), eval_end, 6)
    fold_results = []
    for index, (fold_start, fold_end) in enumerate(folds, start=1):
        run = run_product_backtest(
            config,
            bars_1d,
            market=Market.CRYPTO,
            strategy_name=REVERSAL_STRATEGY,
            parameters=CRYPTO_REVERSAL_PARAMETERS,
            strategy_timeframe="1d",
            start=fold_start,
            end=fold_end,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        result = run.result
        stats = _fold_stats(result)
        stats["top_contributors"] = [
            {"symbol": item.symbol, "realized_pnl": round(item.realized_pnl, 2)}
            for item in sorted(result.per_symbol, key=lambda item: -item.realized_pnl)[:3]
        ]
        fold_results.append(stats)
        print(
            f"  fold {index:02d} {fold_start.date()}->{fold_end.date()} "
            f"return={stats['return']:.2%} sharpe={stats['sharpe']:.2f} "
            f"mdd={stats['max_drawdown']:.2%}"
        )
    positive = sum(1 for fold in fold_results if fold["return"] > 0)
    sorted_folds = sorted(fold_results, key=lambda item: item["return"])
    walk_forward: dict[str, object] = {
        "folds": fold_results,
        "positive_fold_count": positive,
        "negative_fold_count": len(fold_results) - positive,
        "total_fold_count": len(fold_results),
        "positive_fold_ratio": round(positive / len(fold_results), 4) if fold_results else 0.0,
        "mean_fold_return": (
            round(statistics.fmean([f["return"] for f in fold_results]), 6) if fold_results else 0.0
        ),
        "median_fold_return": (
            round(statistics.median([f["return"] for f in fold_results]), 6)
            if fold_results
            else 0.0
        ),
        "worst_fold": (
            {
                "start": sorted_folds[0]["start"],
                "end": sorted_folds[0]["end"],
                "return": sorted_folds[0]["return"],
            }
            if sorted_folds
            else None
        ),
        "best_fold": (
            {
                "start": sorted_folds[-1]["start"],
                "end": sorted_folds[-1]["end"],
                "return": sorted_folds[-1]["return"],
            }
            if sorted_folds
            else None
        ),
        "methodology": (
            "anchored: each fold runs the frozen strategy from fold_start with warmup = "
            "all bars before it; 6-month calendar folds, 1x cost, same PIT universe"
        ),
    }

    print("Mechanism diagnostics (would-be weekly selections, forward 1w returns)...")
    diagnostics = _reversal_mechanism_diagnostics(
        bars_1d,
        universe,
        start=REVERSAL_EVAL_START,
        end=eval_end,
    )

    print("Contribution analysis (per-symbol, BTC/ETH shares, calendar half-years)...")
    contribution = _contribution_analysis(reversal_result)

    summary: dict[str, object] = {
        "strategy": REVERSAL_STRATEGY,
        "parameters": CRYPTO_REVERSAL_PARAMETERS,
        "dataset": dataset,
        "evaluation_window": [REVERSAL_EVAL_START.isoformat(), eval_end.isoformat()],
        "warmup": "2021-01-01 .. 2021-03-31 (formation 70d + scanner lookback)",
        "smoke": smoke,
        "universe": {
            "snapshots": len(snapshots),
            "methodology": universe.methodology,
            "scanner_timeframe": "1h",
            "avg_candidate_pool": (
                round(statistics.fmean(candidate_counts), 1) if candidate_counts else 0.0
            ),
            "note": (
                "PIT universe built once from the extended 1h bars (P1-R1); "
                "scanner metric calibration is hourly"
            ),
        },
        "benchmarks": benchmarks,
        "full_period": full_period,
        "walk_forward": walk_forward,
        "diagnostics": diagnostics,
        "contribution": contribution,
    }

    one_x = full_period["1x"]
    wf = walk_forward
    diag = diagnostics["overall"]
    print("=== REVERSAL KEY NUMBERS ===")
    print(
        f"  reversal 1x: return={one_x['return']:.2%} cagr={one_x['cagr']:.2%} "
        f"sharpe={one_x['sharpe']:.2f} sortino={one_x['sortino']:.2f} "
        f"mdd={one_x['max_drawdown']:.2%} pf={one_x['profit_factor']:.2f} "
        f"turnover={one_x['turnover']:.2%} trades={one_x['trade_count']} "
        f"rebalances={one_x['rebalance_count']}"
    )
    print(
        f"  reversal 2x: return={full_period['2x']['return']:.2%} "
        f"sharpe={full_period['2x']['sharpe']:.2f} mdd={full_period['2x']['max_drawdown']:.2%}"
    )
    print(
        f"  reversal 3x: return={full_period['3x']['return']:.2%} "
        f"sharpe={full_period['3x']['sharpe']:.2f} mdd={full_period['3x']['max_drawdown']:.2%}"
    )
    print(
        f"  benchmarks: BTC={btc_return:.2%} ETH={eth_return:.2%} "
        f"50/50={0.5 * (btc_return + eth_return):.2%} "
        f"old TSM 1d={old_tsm.result.total_return:.2%} "
        f"old CSM 1h={old_csm.result.total_return:.2%}"
    )
    print(
        f"  walk-forward: {wf['positive_fold_count']}/{wf['total_fold_count']} positive folds, "
        f"mean={wf['mean_fold_return']:.2%} median={wf['median_fold_return']:.2%}"
    )
    if wf["best_fold"] is not None and wf["worst_fold"] is not None:
        print(f"    best={wf['best_fold']['return']:.2%} worst={wf['worst_fold']['return']:.2%}")
    print(
        f"  diagnostics: {diag['count']} weekly selections, "
        f"mean forward 1w return={diag['mean_forward_weekly_return']:.2%} "
        f"win rate={diag['win_rate']:.1%}"
    )
    top = contribution["top_1"]
    print(
        f"  contribution: top_1={top['symbol'] if top else None} "
        f"BTC={contribution['BTC/USD']['share_of_positive_pnl']:.1%} "
        f"ETH={contribution['ETH/USD']['share_of_positive_pnl']:.1%}"
    )
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


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation; None when it cannot be computed meaningfully."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denom_x == 0 or denom_y == 0:
        return None
    return numerator / (denom_x * denom_y)


def _reversal_mechanism_diagnostics(
    bars_1d: list[Bar],
    universe,
    *,
    start: datetime,
    end: datetime,
    formation_bars: int = 70,
    top_n: int = 2,
    rebalance_hours: int = 168,
    vol_days: int = 14,
    corr_days: int = 60,
    btc_symbol: str = "BTC/USD",
) -> dict[str, object]:
    """Standalone would-be-selection audit of the frozen reversal signal.

    Pure bar math over the 1d bars (no engine, no strategy modification). For
    every weekly decision date — the first 1d bar of each ``rebalance_hours``-
    aligned epoch block, identical to ``RebalancePolicy.is_period_start`` —
    recompute the strategy's selection exactly as the frozen strategy scores
    it (trailing ``formation_bars`` close-to-close return on completed bars
    ending at the decision bar, qualify iff < 0, rank most-negative-first with
    symbol tie-break, top ``top_n``, equal weight) and the forward one-week
    return of each held name (close at the next decision date over the entry
    close at the decision date). Observations are bucketed by trailing 14-day
    realized volatility (annualized sqrt(365)), trailing 14-day median daily
    dollar volume (bar volume x close), trailing 60-day daily-return
    correlation vs BTC/USD and formation-return depth.
    """
    block_seconds = rebalance_hours * 3600

    def _block_key(ts: datetime) -> int:
        return int(ts.timestamp()) // block_seconds

    by_symbol: dict[str, list[Bar]] = {}
    for bar in bars_1d:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    for series in by_symbol.values():
        series.sort(key=lambda bar: bar.timestamp)
    times_by_symbol = {s: [b.timestamp for b in series] for s, series in by_symbol.items()}
    closes_by_symbol = {s: [b.close for b in series] for s, series in by_symbol.items()}
    volumes_by_symbol = {s: [b.volume for b in series] for s, series in by_symbol.items()}
    close_at = {s: dict(zip(times_by_symbol[s], closes_by_symbol[s])) for s in by_symbol}

    timestamps = sorted({bar.timestamp for bar in bars_1d})
    decision_dates: list[datetime] = []
    last_key: int | None = None
    for ts in timestamps:
        if ts < start or ts > end:
            continue
        key = _block_key(ts)
        if key != last_key:
            decision_dates.append(ts)
            last_key = key

    vol_buckets = [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, float("inf"))]
    vol_labels = ["<50%", "50-100%", "100-200%", ">200%"]
    dv_buckets = [(0.0, 1e3), (1e3, 1e5), (1e5, 1e6), (1e6, 1e7), (1e7, float("inf"))]
    dv_labels = ["<1k", "1k-100k", "100k-1M", "1M-10M", ">=10M"]
    corr_buckets = [(-1.0, -0.5), (-0.5, 0.0), (0.0, 0.5), (0.5, 0.8), (0.8, 1.0)]
    corr_labels = ["<-0.5", "-0.5..0", "0..0.5", "0.5..0.8", ">=0.8"]
    depth_buckets = [
        (float("-inf"), -0.5),
        (-0.5, -0.3),
        (-0.3, -0.15),
        (-0.15, 0.0),
    ]
    depth_labels = ["<-50%", "-50..-30%", "-30..-15%", "-15..0%"]

    def _bucket(value: float | None, buckets, labels) -> str:
        if value is None:
            return "insufficient"
        for (low, high), label in zip(buckets, labels):
            if low <= value < high:
                return label
        return labels[-1]

    observations: list[dict[str, object]] = []
    for index, decision in enumerate(decision_dates):
        if index + 1 >= len(decision_dates):
            break  # last decision has no forward window
        d_next = decision_dates[index + 1]
        qualified: list[tuple[float, str]] = []
        for symbol in sorted(by_symbol):
            if not universe.contains(symbol, Market.CRYPTO, decision):
                continue
            times = times_by_symbol[symbol]
            i0 = bisect.bisect_right(times, decision)
            if i0 < formation_bars + 1:
                continue
            closes = closes_by_symbol[symbol]
            base = closes[i0 - formation_bars - 1]
            if base <= 0:
                continue
            formation_return = closes[i0 - 1] / base - 1
            if not math.isfinite(formation_return) or formation_return >= 0:
                continue
            qualified.append((formation_return, symbol))
        qualified.sort(key=lambda item: (item[0], item[1]))
        for formation_return, symbol in qualified[:top_n]:
            times = times_by_symbol[symbol]
            i0 = bisect.bisect_right(times, decision)
            i1 = bisect.bisect_right(times, d_next)
            if i1 <= i0:
                continue  # no bar between decisions (data hole)
            closes = closes_by_symbol[symbol]
            forward_return = closes[i1 - 1] / closes[i0 - 1] - 1

            vol_window = closes[max(0, i0 - vol_days - 1) : i0]
            if len(vol_window) >= 4:
                returns = [
                    vol_window[k] / vol_window[k - 1] - 1
                    for k in range(1, len(vol_window))
                    if vol_window[k - 1] > 0
                ]
                vol = statistics.stdev(returns) * math.sqrt(365) if len(returns) >= 3 else None
            else:
                vol = None

            dv_window = [
                volumes_by_symbol[symbol][k] * closes_by_symbol[symbol][k]
                for k in range(max(0, i0 - vol_days), i0)
            ]
            dv = statistics.median(dv_window) if dv_window else None

            lo = bisect.bisect_right(times, decision - timedelta(days=corr_days))
            window_times = times[lo:i0]
            btc_map = close_at.get(btc_symbol, {})
            pairs = [
                (closes[lo + k], btc_map[t]) for k, t in enumerate(window_times) if t in btc_map
            ]
            sym_rets: list[float] = []
            btc_rets: list[float] = []
            for k in range(1, len(pairs)):
                if pairs[k - 1][0] > 0 and pairs[k - 1][1] > 0:
                    sym_rets.append(pairs[k][0] / pairs[k - 1][0] - 1)
                    btc_rets.append(pairs[k][1] / pairs[k - 1][1] - 1)
            corr = _pearson(sym_rets, btc_rets) if len(sym_rets) >= 3 else None

            observations.append(
                {
                    "decision_date": decision.isoformat(),
                    "symbol": symbol,
                    "formation_return": round(formation_return, 6),
                    "forward_return": round(forward_return, 6),
                    "depth": _bucket(formation_return, depth_buckets, depth_labels),
                    "vol": _bucket(vol, vol_buckets, vol_labels),
                    "dv": _bucket(dv, dv_buckets, dv_labels),
                    "corr": _bucket(corr, corr_buckets, corr_labels),
                }
            )

    buckets: dict[str, dict[str, dict[str, float | int]]] = {
        "formation_return_depth": {},
        "realized_volatility_14d": {},
        "median_daily_dollar_volume_14d": {},
        "btc_correlation_60d": {},
    }
    for bucket_name, key in (
        ("formation_return_depth", "depth"),
        ("realized_volatility_14d", "vol"),
        ("median_daily_dollar_volume_14d", "dv"),
        ("btc_correlation_60d", "corr"),
    ):
        groups: dict[str, list[float]] = {}
        for ob in observations:
            groups.setdefault(str(ob[key]), []).append(float(ob["forward_return"]))
        for label, returns in groups.items():
            buckets[bucket_name][label] = {
                "count": len(returns),
                "mean_forward_weekly_return": round(statistics.fmean(returns), 6),
                "win_rate": round(sum(1 for r in returns if r > 0) / len(returns), 4),
            }

    overall = [float(ob["forward_return"]) for ob in observations]
    return {
        "methodology": (
            f"would-be weekly selection on completed 1d bars (trailing {formation_bars}-bar "
            f"return < 0, top {top_n}, most-negative-first, symbol tie-break) and forward "
            "one-week close-to-close return per held name; decision dates are the first 1d "
            f"bar of each {rebalance_hours}h epoch block (RebalancePolicy parity); "
            "diagnostic only - the strategy itself is untouched"
        ),
        "formation_bars": formation_bars,
        "top_n": top_n,
        "rebalance_hours": rebalance_hours,
        "weekly_decision_count": len(decision_dates),
        "observation_count": len(observations),
        "overall": {
            "count": len(overall),
            "mean_forward_weekly_return": round(statistics.fmean(overall), 6) if overall else 0.0,
            "win_rate": round(sum(1 for r in overall if r > 0) / len(overall), 4)
            if overall
            else 0.0,
        },
        "buckets": buckets,
    }


def _half_year_pnl(result) -> list[dict[str, object]]:
    """Realized PnL by calendar half-year, measured from the equity curve.

    Each period's PnL is the equity change from the END of the previous
    period to the END of this period. Anchoring a period at its FIRST curve
    point would silently drop the boundary-day moves between periods, so
    consecutive periods would not telescope to the run's net PnL.
    """
    periods: list[dict[str, object]] = []
    current: tuple[int, str] | None = None
    period_start: float | None = None
    previous_end: float | None = None
    for timestamp, equity in zip(result.curve_timestamps, result.equity_curve):
        key = (timestamp.year, "H1" if timestamp.month <= 6 else "H2")
        if key != current:
            if current is not None and period_start is not None and previous_end is not None:
                periods.append(
                    {
                        "period": f"{current[0]}-{current[1]}",
                        "pnl": round(previous_end - period_start, 2),
                        "return": (
                            round(previous_end / period_start - 1, 6) if period_start > 0 else None
                        ),
                    }
                )
            current = key
            period_start = previous_end if previous_end is not None else equity
        previous_end = equity
    if current is not None and period_start is not None and previous_end is not None:
        periods.append(
            {
                "period": f"{current[0]}-{current[1]}",
                "pnl": round(previous_end - period_start, 2),
                "return": (round(previous_end / period_start - 1, 6) if period_start > 0 else None),
            }
        )
    return periods


def _contribution_analysis(result) -> dict[str, object]:
    """Per-symbol PnL concentration, BTC/ETH shares and calendar half-years."""
    per = sorted(result.per_symbol, key=lambda item: -item.realized_pnl)
    total = sum(item.realized_pnl for item in result.per_symbol)
    positive = sum(item.realized_pnl for item in result.per_symbol if item.realized_pnl > 0)

    def _share(pnl: float) -> float:
        if positive > 0:
            return pnl / positive
        return 0.0

    def _entry(item) -> dict[str, object] | None:
        if item is None:
            return None
        return {
            "symbol": item.symbol,
            "realized_pnl": round(item.realized_pnl, 2),
            "share_of_positive_pnl": round(_share(item.realized_pnl), 4),
        }

    def _symbol_share(symbol: str) -> dict[str, object]:
        pnl = next((item.realized_pnl for item in result.per_symbol if item.symbol == symbol), 0.0)
        return {
            "realized_pnl": round(pnl, 2),
            "share_of_total_pnl": round(pnl / total, 4) if total != 0 else 0.0,
            "share_of_positive_pnl": round(_share(pnl), 4),
        }

    half_years = _half_year_pnl(result)
    half_year_sum = sum(float(item["pnl"]) for item in half_years)
    net_pnl = result.starting_equity * result.total_return
    if abs(half_year_sum - net_pnl) > max(1.0, abs(net_pnl) * 1e-4):
        raise ValueError(
            "Calendar half-year PnL does not telescope to the run's net PnL "
            f"({half_year_sum:.2f} vs {net_pnl:.2f}); attribution is unreliable"
        )
    return {
        "top_1": _entry(per[0] if len(per) > 0 else None),
        "top_2": _entry(per[1] if len(per) > 1 else None),
        "top_3": _entry(per[2] if len(per) > 2 else None),
        "BTC/USD": _symbol_share("BTC/USD"),
        "ETH/USD": _symbol_share("ETH/USD"),
        "calendar_half_years": half_years,
    }


def _run_summary_stats(result) -> dict[str, object]:
    """Compact per-strategy stats for the side-by-side benchmark table."""
    return {
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "profit_factor": round(_profit_factor(result), 4),
        "turnover": round(result.turnover, 6),
        "trade_count": len(result.closed_position_events),
        "rebalance_count": result.rebalance_count,
        "avg_holdings": round(result.average_holdings_count, 3),
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
    }


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
    parser.add_argument("target", choices=("equity", "crypto", "references", "reversal", "all"))
    parser.add_argument("--config", default=str(REPO / "config" / "settings.yaml"))
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="reversal quick smoke: truncate the eval window to ~3 months",
    )
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
    if args.target in {"reversal", "all"}:
        report["reversal"] = validate_reversal(config, smoke=args.smoke)
        OUTPUT_DIR_V3.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR_V3 / "reversal.json").write_text(
            json.dumps(report["reversal"], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    if args.target in {"references", "all"}:
        report["references"] = validate_references(config)
        (OUTPUT_DIR / "references.json").write_text(
            json.dumps(report["references"], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    summary_path = OUTPUT_DIR_V3 if args.target in {"reversal", "all"} else OUTPUT_DIR
    (summary_path / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
