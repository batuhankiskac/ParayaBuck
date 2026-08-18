#!/usr/bin/env python3
# ruff: noqa: E501  # research script: long report/print lines
"""Equity cadence bake-off: monthly (control) vs weekly — frozen alpha, production harness only.

Wave-1 capital-utilization & cadence mission, equity leg. Runs the frozen
``equity_residual_momentum_v1`` spec through ``run_product_backtest`` at the
predeclared cadence candidates (monthly control, weekly override), with cost
stress (1x/2x/3x), an identical walk-forward for both candidates, a no-trade
staleness diagnostic of the weekly ranking, churn / adverse-turnover
analysis of the weekly fills, the decision/order/fill band funnel and
concentration (top-1/2/3 contributions, gated jackknife).

Outputs: data/research/cadence_research_2026_08/equity/*.json + markdown
tables on stdout. NEVER edits src/ or tests/.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from parayabuck.backtest.portfolio_engine import OrderSide  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    build_product_universe,
    load_csv_bars,
    run_product_backtest,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from scripts.validate_strategy_candidates import (  # noqa: E402
    _cost_stress,
    _fold_stats,
    _fold_window,
    _profit_factor,
)

OUTPUT_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "equity"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"

EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)

STRATEGY = "equity_residual_momentum_v1"
FROZEN_EQUITY = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
MAX_ALLOCATION = 0.50  # config strategy.equity.maximum_allocation (frozen)
# "Near max allocation" definition: daily deployed exposure >= 0.45 (90% of
# the 0.50 sleeve). State explicit, never silent.
NEAR_MAX_EXPOSURE = 0.45
TOP_N = 8
# Approximate monthly-overlap window for the "would have occurred under
# monthly cadence" comparison (fills of the same symbol within +-21 calendar
# days of a monthly fill count as cadence-equivalent).
MONTHLY_OVERLAP_DAYS = 21
# Churn bar-distance thresholds (fills are daily-bar event timestamps).
CHURN_BARS = 7
# Jackknife gate: skip when a single full-period run averages over this.
JACKKNIFE_MAX_RUN_SECONDS = 120.0

SMOKE_START = datetime(2024, 1, 1, tzinfo=UTC)
SMOKE_END = datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC)
STALENESS_WINDOW = (datetime(2023, 7, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _spearman_week_pair(ranks_a: dict[str, int], ranks_b: dict[str, int]) -> float | None:
    """Spearman rank correlation over symbols ranked in both weeks.

    Ranks are 0-based positions; Pearson over the positions of the common
    symbols equals Spearman (no ties because positions are unique).
    """
    common = [symbol for symbol in ranks_a if symbol in ranks_b]
    if len(common) < 3:
        return None
    xs = [ranks_a[symbol] for symbol in common]
    ys = [ranks_b[symbol] for symbol in common]
    return _pearson(xs, ys)


def _decision_timestamps(bars, start: datetime, end: datetime, frequency: str) -> list[datetime]:
    """First bar of each rebalance period (mirrors RebalancePolicy.is_period_start)."""
    stamps = sorted({bar.timestamp for bar in bars if start <= bar.timestamp <= end})
    out: list[datetime] = []
    previous: datetime | None = None
    for ts in stamps:
        if previous is None:
            out.append(ts)
        elif frequency == "weekly":
            if previous.isocalendar()[:2] != ts.isocalendar()[:2]:
                out.append(ts)
        elif frequency == "monthly":
            if (previous.year, previous.month) != (ts.year, ts.month):
                out.append(ts)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown frequency {frequency}")
        previous = ts
    return out


def _daily_exposure_series(result, bars) -> list[tuple[datetime, float]]:
    """Actual deployed exposure per bar day, reconstructed from fills.

    Positions change only on fills (fills execute on the bar after their
    decision); each day's exposure is holdings market value at that bar's
    close divided by that day's equity (which already includes that day's
    fills). This is the harness-exact PIT exposure path; weight_history alone
    is decision-target based and would overstate deployment.
    """
    traded = {fill.symbol for fill in result.fills}
    price_lists: dict[str, list[tuple[datetime, float]]] = {}
    for bar in bars:
        if bar.symbol in traded:
            price_lists.setdefault(bar.symbol, []).append((bar.timestamp, bar.close))
    price_ts: dict[str, list[datetime]] = {}
    price_close: dict[str, list[float]] = {}
    for symbol, pairs in price_lists.items():
        pairs.sort(key=lambda item: item[0])
        price_ts[symbol] = [ts for ts, _close in pairs]
        price_close[symbol] = [close for _ts, close in pairs]

    def mark(symbol: str, ts: datetime) -> float:
        index = bisect_right(price_ts[symbol], ts) - 1
        return price_close[symbol][index] if index >= 0 else 0.0

    fills = sorted(result.fills, key=lambda fill: (fill.timestamp, fill.symbol))
    fill_index = 0
    positions: dict[str, float] = {}
    series: list[tuple[datetime, float]] = []
    for index, ts in enumerate(result.curve_timestamps):
        while fill_index < len(fills) and fills[fill_index].timestamp <= ts:
            fill = fills[fill_index]
            if fill.side == OrderSide.BUY:
                positions[fill.symbol] = positions.get(fill.symbol, 0.0) + fill.qty
            else:
                positions[fill.symbol] = max(0.0, positions.get(fill.symbol, 0.0) - fill.qty)
            fill_index += 1
        equity = result.equity_curve[index]
        if equity > 0 and positions:
            market_value = sum(
                qty * mark(symbol, ts) for symbol, qty in positions.items() if qty > 0
            )
            exposure = min(1.0, market_value / equity)
        else:
            exposure = 0.0
        series.append((ts, exposure))
    return series


def _capital_utilization(result, bars) -> dict[str, object]:
    series = _daily_exposure_series(result, bars)
    exposures = [value for _ts, value in series]
    days = len(exposures)
    avg = statistics.fmean(exposures) if days else 0.0
    median = statistics.median(exposures) if days else 0.0
    pct_positive = sum(1 for value in exposures if value > 0) / days if days else 0.0
    pct_near_max = (
        sum(1 for value in exposures if value >= NEAR_MAX_EXPOSURE) / days if days else 0.0
    )
    pct_idle = sum(1 for value in exposures if value == 0) / days if days else 0.0
    first_fill = min((fill.timestamp for fill in result.fills), default=None)
    start_ts = result.curve_timestamps[0] if result.curve_timestamps else None
    time_to_first = (
        (first_fill - start_ts).total_seconds() / 86400.0
        if first_fill is not None and start_ts is not None
        else None
    )
    # Decision-target exposure (what the strategy asked for at each rebalance).
    target_exposures = [sum(weights.values()) for _ts, weights in result.weight_history]
    return {
        "avg_exposure": round(avg, 6),
        "median_exposure": round(median, 6),
        "pct_days_exposure_gt_0": round(pct_positive, 6),
        "pct_days_near_max_allocation_ge_0.45": round(pct_near_max, 6),
        "pct_days_idle_exposure_0": round(pct_idle, 6),
        "time_to_first_deployment_days": round(time_to_first, 3)
        if time_to_first is not None
        else None,
        "first_fill_timestamp": first_fill.isoformat() if first_fill is not None else None,
        "window_start_timestamp": start_ts.isoformat() if start_ts is not None else None,
        "decision_count": len(result.weight_history),
        "avg_target_exposure": round(statistics.fmean(target_exposures), 6)
        if target_exposures
        else 0.0,
        "median_target_exposure": round(statistics.median(target_exposures), 6)
        if target_exposures
        else 0.0,
        "pct_decisions_target_gt_0": (
            round(sum(1 for value in target_exposures if value > 0) / len(target_exposures), 6)
            if target_exposures
            else 0.0
        ),
        "near_max_exposure_definition": f"deployed exposure >= {NEAR_MAX_EXPOSURE} "
        f"({NEAR_MAX_EXPOSURE / MAX_ALLOCATION:.0%} of the {MAX_ALLOCATION:.0%} sleeve)",
    }


def _full_metrics(result, report, bars) -> dict[str, object]:
    years = result.duration_days / 365.25 if result.duration_days else 0.0
    total_cost = result.fee_total + result.slippage_cost_total + result.spread_cost_total
    avg_equity = sum(result.equity_curve) / len(result.equity_curve) if result.equity_curve else 0.0
    gross_pnl = result.starting_equity * result.gross_return
    net_pnl = result.ending_equity - result.starting_equity
    traded_notional = result.turnover * avg_equity
    trades = len(result.closed_position_events)
    profit_factor = _profit_factor(result)
    holding_hours = float(report.summary.get("average_holding_hours", 0.0))
    decisions = len(result.rebalances)
    orders = sum(1 for record in result.rebalances if record.expected_turnover > 0)
    traded_rebalances = sum(1 for record in result.rebalances if record.realized_turnover > 0)
    utilization = _capital_utilization(result, bars)
    return {
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "annualized_volatility": round(result.annualized_volatility, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
        "decisions": decisions,
        "orders_created": orders,
        "rebalances_with_fills": traded_rebalances,
        "fills": len(result.fills),
        "trades": trades,
        "trades_per_year": round(trades / years, 3) if years > 0 else 0.0,
        "avg_holding_period_days": round(holding_hours / 24.0, 3),
        "turnover_total": round(result.turnover, 6),
        "turnover_annualized": round(result.turnover / years, 4) if years > 0 else 0.0,
        "fees_total": round(total_cost, 2),
        "fees_pct_of_gross_pnl": (
            round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
        ),
        "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "net_pnl_per_turnover": (
            round(net_pnl / traded_notional, 6) if traded_notional > 0 else None
        ),
        "capital_utilization": utilization,
        "band_analysis": {
            "decisions": decisions,
            "orders_created": orders,
            "decisions_with_fills": traded_rebalances,
            "fills": len(result.fills),
            "decisions_with_zero_orders": decisions - orders,
            "avg_expected_turnover_when_trading": (
                round(
                    statistics.fmean(
                        record.expected_turnover
                        for record in result.rebalances
                        if record.expected_turnover > 0
                    ),
                    6,
                )
                if orders
                else 0.0
            ),
            "note": "orders_created = rebalance records with expected_turnover > 0 "
            "(engine creates orders only when drift exceeds the 0.5% band AND "
            "notional exceeds the $10 minimum); decisions_with_fills = records "
            "with realized_turnover > 0",
        },
    }


def _assert_cadence(parameters: dict[str, object], expected: str) -> None:
    strategy = portfolio_strategy_from(STRATEGY, parameters, timeframe="1d")
    policy = rebalance_policy_for(strategy)
    assert policy.frequency == expected, (
        f"rebalance policy mismatch: expected {expected!r}, got {policy.frequency!r} "
        f"from parameters {parameters}"
    )
    assert policy.market == Market.EQUITY
    return strategy


def _churn_analysis(result, monthly_fills, bars, *, start, end) -> dict[str, object]:
    """Reversal/churn audit of the weekly candidate's fills."""
    bar_dates = sorted({bar.timestamp for bar in bars if start <= bar.timestamp <= end})
    date_index = {ts: i for i, ts in enumerate(bar_dates)}

    def bar_distance(a: datetime, b: datetime) -> int | None:
        if a not in date_index or b not in date_index:
            return None
        return date_index[b] - date_index[a]

    fills = sorted(result.fills, key=lambda fill: (fill.timestamp, fill.symbol))
    by_symbol: dict[str, list] = defaultdict(list)
    for fill in fills:
        by_symbol[fill.symbol].append(fill)

    round_trips: list[dict[str, object]] = []
    flip_flops: list[dict[str, object]] = []
    reversal_fill_ids: set[int] = set()
    for symbol, symbol_fills in sorted(by_symbol.items()):
        for i, sell in enumerate(symbol_fills):
            if sell.side != OrderSide.SELL:
                continue
            for buy in symbol_fills[i + 1 :]:
                if buy.side != OrderSide.BUY:
                    continue
                distance = bar_distance(sell.timestamp, buy.timestamp)
                if distance is None or distance <= 0:
                    continue
                if distance <= CHURN_BARS:
                    round_trips.append(
                        {
                            "symbol": symbol,
                            "sell_timestamp": sell.timestamp.isoformat(),
                            "rebuy_timestamp": buy.timestamp.isoformat(),
                            "bars_apart": distance,
                            "cost": round(
                                sell.fee
                                + sell.slippage_cost
                                + sell.spread_cost
                                + buy.fee
                                + buy.slippage_cost
                                + buy.spread_cost,
                                4,
                            ),
                        }
                    )
                    reversal_fill_ids.add(id(sell))
                    reversal_fill_ids.add(id(buy))
                break
        for i, buy in enumerate(symbol_fills):
            if buy.side != OrderSide.BUY:
                continue
            next_sell = None
            for candidate in symbol_fills[i + 1 :]:
                if candidate.side != OrderSide.SELL:
                    continue
                distance = bar_distance(buy.timestamp, candidate.timestamp)
                if distance is not None and 0 < distance <= CHURN_BARS:
                    next_sell = candidate
                break
            if next_sell is None:
                continue
            for rebuy in symbol_fills:
                if rebuy.side != OrderSide.BUY:
                    continue
                if rebuy.timestamp <= next_sell.timestamp:
                    continue
                distance = bar_distance(next_sell.timestamp, rebuy.timestamp)
                if distance is not None and 0 < distance <= CHURN_BARS:
                    flip_flops.append(
                        {
                            "symbol": symbol,
                            "buy_timestamp": buy.timestamp.isoformat(),
                            "sell_timestamp": next_sell.timestamp.isoformat(),
                            "rebuy_timestamp": rebuy.timestamp.isoformat(),
                        }
                    )
                    reversal_fill_ids.add(id(buy))
                    reversal_fill_ids.add(id(next_sell))
                    reversal_fill_ids.add(id(rebuy))
                break

    reversal_cost = sum(
        fill.fee + fill.slippage_cost + fill.spread_cost
        for fill in fills
        if id(fill) in reversal_fill_ids
    )

    # % of weekly trades that would not have occurred under monthly cadence:
    # a weekly fill counts as cadence-equivalent when the monthly run filled
    # the same symbol within +-21 calendar days (approximation: monthly
    # decisions anchor on month starts; 21 days is the halfway point to the
    # next monthly decision).
    monthly_set: set[tuple[str, datetime]] = {
        (fill.symbol, fill.timestamp) for fill in monthly_fills
    }
    monthly_by_symbol: dict[str, list[datetime]] = defaultdict(list)
    for symbol, ts in monthly_set:
        monthly_by_symbol[symbol].append(ts)
    for stamps in monthly_by_symbol.values():
        stamps.sort()
    weekly_only = 0
    for fill in fills:
        stamps = monthly_by_symbol.get(fill.symbol, [])
        matched = any(
            abs((stamp - fill.timestamp).total_seconds()) / 86400.0 <= MONTHLY_OVERLAP_DAYS
            for stamp in stamps
        )
        if not matched:
            weekly_only += 1
    return {
        "round_trip_count": len(round_trips),
        "flip_flop_count": len(flip_flops),
        "round_trips": round_trips[:20],
        "flip_flops": flip_flops[:20],
        "reversal_cost_total": round(reversal_cost, 2),
        "weekly_fills_total": len(fills),
        "weekly_only_fills_under_monthly_cadence": weekly_only,
        "pct_weekly_trades_not_under_monthly": round(weekly_only / len(fills), 4) if fills else 0.0,
        "approximations": {
            "bar_distance": "fills on daily equity bars; bar distance = rank difference "
            "of fill timestamps in the global daily-bar index",
            "flip_flop": "buy->sell->buy of the same symbol, each hop within 7 bars "
            "(captures A->B->A switching: A sold to buy B, then bought back)",
            "monthly_overlap": f"weekly fill matches a monthly fill of the same symbol "
            f"within +-{MONTHLY_OVERLAP_DAYS} calendar days",
        },
    }


def _staleness_diagnostic(
    bars, universe, strategy, *, window_start, window_end
) -> dict[str, object]:
    """No-trade ranking-stability audit of the weekly decision stream.

    For every weekly decision timestamp in the window, ranks the PIT
    observation set exactly as the backtest decides (same strategy scoring on
    the same point-in-time universe snapshot and closed-bar history) and
    measures how much the ranking moves week over week and month over month.
    Never creates orders or mutates state.
    """
    universe_hash = hash_point_in_time_universe(universe)
    weekly_ts = _decision_timestamps(bars, window_start, window_end, "weekly")
    monthly_ts = _decision_timestamps(bars, window_start, window_end, "monthly")
    sym_bars: dict[str, list] = defaultdict(list)
    for bar in bars:
        if bar.timestamp <= window_end:
            sym_bars[bar.symbol].append(bar)
    for series in sym_bars.values():
        series.sort(key=lambda bar: bar.timestamp)
    snapshots = universe.snapshots

    def snapshot_symbols(ts: datetime) -> frozenset:
        eligible = [snapshot for snapshot in snapshots if snapshot.effective_at <= ts]
        return eligible[-1].symbols if eligible else frozenset()

    def rank_week(ts: datetime) -> tuple[str, ...]:
        pool = set(snapshot_symbols(ts))
        pool.add("SPY")
        history: dict[str, tuple] = {}
        for symbol in pool:
            series = sym_bars.get(symbol, [])
            if not series:
                continue
            upto = bisect_right([bar.timestamp for bar in series], ts)
            if upto == 0:
                continue
            history[symbol] = tuple(series[:upto])
        if "SPY" not in history:
            return ()
        context = PortfolioContext(
            decision_timestamp=ts,
            market=Market.EQUITY,
            timeframe="1d",
            history=history,
            universe=universe,
            universe_version=universe_hash,
            equity=100_000.0,
            holdings={},
            prices={symbol: series[-1].close for symbol, series in history.items()},
            benchmark_symbol="SPY",
        )
        return strategy.rank_candidates(context)

    weeks: list[dict[str, object]] = []
    for ts in weekly_ts:
        ranking = rank_week(ts)
        weeks.append(
            {
                "timestamp": ts.isoformat(),
                "ranking_length": len(ranking),
                "ranking": list(ranking),
                "top8": list(ranking[:TOP_N]),
            }
        )

    week_over_week: list[dict[str, object]] = []
    for previous, current in zip(weeks, weeks[1:], strict=False):
        ranks_a = {symbol: i for i, symbol in enumerate(previous["ranking"])}
        ranks_b = {symbol: i for i, symbol in enumerate(current["ranking"])}
        spearman = _spearman_week_pair(ranks_a, ranks_b)
        top_a = set(previous["top8"])
        top_b = set(current["top8"])
        churn = len(top_a - top_b) / TOP_N
        week_over_week.append(
            {
                "week_start": previous["timestamp"],
                "week_end": current["timestamp"],
                "spearman_top_rank": round(spearman, 4) if spearman is not None else None,
                "top8_churn": round(churn, 4),
            }
        )

    # Monthly decay: how far the month-start top-8 falls by weeks 2/3/4.
    weekly_by_ts = {week["timestamp"]: week for week in weeks}
    month_starts = [ts for ts in monthly_ts if ts.isoformat() in weekly_by_ts]
    monthly_decay: list[dict[str, object]] = []
    for ts in month_starts:
        week = weekly_by_ts.get(ts.isoformat())
        if week is None:
            continue
        top8_at_month_start = set(week["top8"])
        index = weeks.index(week)
        retained: list[float] = []
        for offset in (1, 2, 3):
            if index + offset >= len(weeks):
                break
            later_top8 = set(weeks[index + offset]["top8"])
            retained.append(len(top8_at_month_start & later_top8) / TOP_N)
        monthly_decay.append(
            {
                "month_start": ts.isoformat(),
                "top8_at_month_start": list(top8_at_month_start),
                "fraction_still_top8_week2": round(retained[0], 4) if len(retained) >= 1 else None,
                "fraction_still_top8_week3": round(retained[1], 4) if len(retained) >= 2 else None,
                "fraction_still_top8_week4": round(retained[2], 4) if len(retained) >= 3 else None,
            }
        )

    wow_spearmans = [
        entry["spearman_top_rank"]
        for entry in week_over_week
        if entry["spearman_top_rank"] is not None
    ]
    wow_churn = [entry["top8_churn"] for entry in week_over_week]
    week2 = [
        entry["fraction_still_top8_week2"]
        for entry in monthly_decay
        if entry["fraction_still_top8_week2"] is not None
    ]
    week3 = [
        entry["fraction_still_top8_week3"]
        for entry in monthly_decay
        if entry["fraction_still_top8_week3"] is not None
    ]
    week4 = [
        entry["fraction_still_top8_week4"]
        for entry in monthly_decay
        if entry["fraction_still_top8_week4"] is not None
    ]
    return {
        "window": [window_start.isoformat(), window_end.isoformat()],
        "weekly_decision_count": len(weeks),
        "monthly_decision_count_in_window": len(month_starts),
        "universe_hash": universe_hash,
        "no_trades_guarantee": "diagnostic only calls rank_candidates() on read-only "
        "contexts; no orders, fills or state mutation",
        "week_over_week": week_over_week,
        "monthly_decay": monthly_decay,
        "summary": {
            "spearman_mean": round(statistics.fmean(wow_spearmans), 4) if wow_spearmans else None,
            "spearman_median": round(statistics.median(wow_spearmans), 4)
            if wow_spearmans
            else None,
            "spearman_min": round(min(wow_spearmans), 4) if wow_spearmans else None,
            "top8_churn_mean": round(statistics.fmean(wow_churn), 4) if wow_churn else 0.0,
            "top8_churn_median": round(statistics.median(wow_churn), 4) if wow_churn else 0.0,
            "monthly_top8_still_top8_week2_mean": round(statistics.fmean(week2), 4)
            if week2
            else None,
            "monthly_top8_still_top8_week3_mean": round(statistics.fmean(week3), 4)
            if week3
            else None,
            "monthly_top8_still_top8_week4_mean": round(statistics.fmean(week4), 4)
            if week4
            else None,
        },
    }


def _walk_forward(config, bars, universe, *, start, end, smoke: bool) -> dict[str, object]:
    monthly_params = dict(FROZEN_EQUITY)
    weekly_params = dict(FROZEN_EQUITY, rebalance_frequency="weekly")
    if smoke:
        fold_start = datetime(2024, 7, 1, tzinfo=UTC)
        folds = _fold_window(fold_start, end, 3, 6)
    else:
        folds = _fold_window(datetime(2019, 1, 2, tzinfo=UTC), end, 6, 24)
    fold_rows: list[dict[str, object]] = []
    for index, (fold_start, fold_end) in enumerate(folds, start=1):
        row: dict[str, object] = {
            "fold": index,
            "start": fold_start.isoformat(),
            "end": fold_end.isoformat(),
        }
        for label, parameters in (("monthly", monthly_params), ("weekly", weekly_params)):
            run = run_product_backtest(
                config,
                bars,
                market=Market.EQUITY,
                strategy_name=STRATEGY,
                parameters=parameters,
                strategy_timeframe="1d",
                start=fold_start,
                end=fold_end,
                cost_multiplier=1.0,
                persist=False,
                persist_artifacts=False,
                universe=universe,
            )
            stats = _fold_stats(run.result)
            row[f"{label}_return"] = stats["return"]
            row[f"{label}_sharpe"] = stats["sharpe"]
            row[f"{label}_max_drawdown"] = stats["max_drawdown"]
            row[f"{label}_trades"] = stats["trade_count"]
            row[f"{label}_rebalances"] = run.result.rebalance_count
        fold_rows.append(row)
        print(
            f"  fold {index:02d} {fold_start.date()}->{fold_end.date()} "
            f"M ret={row['monthly_return']:.2%} sh={row['monthly_sharpe']:.2f} "
            f"W ret={row['weekly_return']:.2%} sh={row['weekly_sharpe']:.2f}"
        )
    summary: dict[str, object] = {}
    for label in ("monthly", "weekly"):
        returns = [row[f"{label}_return"] for row in fold_rows]
        positives = sum(1 for value in returns if value > 0)
        summary[label] = {
            "positive_fold_count": positives,
            "total_fold_count": len(returns),
            "positive_fold_ratio": round(positives / len(returns), 4) if returns else 0.0,
            "mean_fold_return": round(statistics.fmean(returns), 6) if returns else 0.0,
            "median_fold_return": round(statistics.median(returns), 6) if returns else 0.0,
            "worst_fold_return": round(min(returns), 6) if returns else 0.0,
            "best_fold_return": round(max(returns), 6) if returns else 0.0,
            "mean_fold_sharpe": round(
                statistics.fmean([row[f"{label}_sharpe"] for row in fold_rows]), 4
            )
            if fold_rows
            else 0.0,
            "mean_fold_max_drawdown": round(
                statistics.fmean([row[f"{label}_max_drawdown"] for row in fold_rows]), 6
            )
            if fold_rows
            else 0.0,
        }
    return {
        "fold_windows": [row["start"] for row in fold_rows],
        "window_note": "identical anchored 6-month folds for BOTH candidates, 24-month warmup; "
        "same PIT universe object",
        "folds": fold_rows,
        "summary": summary,
    }


def _calendar_year_returns(reports: dict[str, object]) -> dict[str, object]:
    yearly: dict[str, object] = {}
    for label, report in reports.items():
        yearly[label] = [
            {"year": year, "portfolio_return": round(port, 6), "benchmark_return": round(bench, 6)}
            for year, port, bench in report.yearly_returns
        ]
    return {
        "note": "calendar-year returns from the 1x full-window reports; 2017 and 2026 are partial "
        "(2017 starts 2017-01-03; 2026 ends 2026-07-30)",
        "years": yearly,
    }


def _jackknife(config, bars, universe, runs: dict[str, object], *, start, end) -> dict[str, object]:
    """Drop-one-symbol rerun of the top contributors (snapshot-filtered universe).

    The dropped symbol's bars stay in the dataset (so its price history is
    untouched) but it is removed from every PIT snapshot, which mirrors
    'what if the top contributor had not been tradable' without rerunning the
    scanner. Approximation: the scanner would normally promote the next
    shortlist name; the frozen universe is reused instead (mission rule:
    same PIT universe).
    """
    out: dict[str, object] = {}
    for label, run in runs.items():
        per_symbol = sorted(
            ((item.symbol, item.realized_pnl) for item in run.result.per_symbol),
            key=lambda item: -item[1],
        )
        top_symbols = [symbol for symbol, _pnl in per_symbol[:3]]
        out[label] = {"base": _run_summary(run.result), "drops": {}}
        for symbol in top_symbols:
            dropped_snapshots = tuple(
                replace(
                    snapshot,
                    symbols=snapshot.symbols - {symbol},
                    candidate_count=max(0, snapshot.candidate_count - 1),
                )
                if symbol in snapshot.symbols
                else snapshot
                for snapshot in universe.snapshots
            )
            dropped_universe = replace(universe, snapshots=dropped_snapshots)
            parameters = (
                dict(FROZEN_EQUITY)
                if label == "monthly"
                else dict(FROZEN_EQUITY, rebalance_frequency="weekly")
            )
            rerun = run_product_backtest(
                config,
                bars,
                market=Market.EQUITY,
                strategy_name=STRATEGY,
                parameters=parameters,
                strategy_timeframe="1d",
                start=start,
                end=end,
                cost_multiplier=1.0,
                persist=False,
                persist_artifacts=False,
                universe=dropped_universe,
            )
            base = _run_summary(run.result)
            dropped = _run_summary(rerun.result)
            out[label]["drops"][symbol] = {
                "dropped": dropped,
                "delta_return_pp": round((dropped["return"] - base["return"]) * 100, 4),
                "delta_cagr_pp": round((dropped["cagr"] - base["cagr"]) * 100, 4),
                "delta_sharpe": round(dropped["sharpe"] - base["sharpe"], 4),
                "delta_max_drawdown_pp": round(
                    (dropped["max_drawdown"] - base["max_drawdown"]) * 100, 4
                ),
            }
    return out


def _run_summary(result) -> dict[str, object]:
    return {
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "trades": len(result.closed_position_events),
    }


def _verdict(metrics: dict[str, object]) -> dict[str, object]:
    monthly_1x = metrics["monthly"]["1x"]
    weekly_1x = metrics["weekly"]["1x"]
    monthly_3x = metrics["monthly"]["3x"]
    weekly_3x = metrics["weekly"]["3x"]
    m_util = monthly_1x["capital_utilization"]
    w_util = weekly_1x["capital_utilization"]
    m_wf = metrics["walk_forward"]["summary"]["monthly"]
    w_wf = metrics["walk_forward"]["summary"]["weekly"]
    m_conc = metrics["concentration"]["monthly"]["top"][0]["share_of_total_realized_pnl"]
    w_conc = metrics["concentration"]["weekly"]["top"][0]["share_of_total_realized_pnl"]

    net_cagr_up = weekly_1x["cagr"] > monthly_1x["cagr"]
    sharpe_up = weekly_1x["sharpe"] > monthly_1x["sharpe"]
    mdd_down = weekly_1x["max_drawdown"] < monthly_1x["max_drawdown"]
    util_up = (
        w_util["avg_exposure"] > m_util["avg_exposure"]
        or w_util["pct_days_idle_exposure_0"] < m_util["pct_days_idle_exposure_0"]
    )
    # Cost robustness: weekly's 1x->3x return degradation must not exceed
    # monthly's by more than 2pp (linearity preserved => no hidden churn tax).
    monthly_decay = monthly_1x["return"] - monthly_3x["return"]
    weekly_decay = weekly_1x["return"] - weekly_3x["return"]
    cost_robust_ok = weekly_decay <= monthly_decay + 0.02
    # Walk-forward: weekly must not lose fold-positivity or median fold return
    # by a material margin (>= -0.1 ratio / -1pp median).
    walk_forward_ok = (
        w_wf["positive_fold_ratio"] >= m_wf["positive_fold_ratio"] - 0.1
        and w_wf["median_fold_return"] >= m_wf["median_fold_return"] - 0.01
    )
    # Concentration: top-1 share of realized PnL must not be materially worse
    # (<= +10pp over monthly).
    concentration_ok = w_conc <= m_conc + 0.10

    flags = {
        "weekly_net_cagr_up": net_cagr_up,
        "weekly_sharpe_improved": sharpe_up,
        "weekly_max_drawdown_improved": mdd_down,
        "weekly_capital_utilization_improved": util_up,
        "any_risk_or_util_improvement": sharpe_up or mdd_down or util_up,
        "cost_robustness_ok": cost_robust_ok,
        "walk_forward_ok": walk_forward_ok,
        "concentration_ok": concentration_ok,
    }
    dominates = (
        net_cagr_up
        and (sharpe_up or mdd_down or util_up)
        and cost_robust_ok
        and walk_forward_ok
        and concentration_ok
    )
    return {
        "pareto_rule": "net CAGR up AND at least one of Sharpe/maxDD/capital-utilization improved "
        "AND cost robustness + walk-forward + concentration acceptable",
        "evidence": {
            "net_cagr_up": {"monthly": monthly_1x["cagr"], "weekly": weekly_1x["cagr"]},
            "sharpe": {"monthly": monthly_1x["sharpe"], "weekly": weekly_1x["sharpe"]},
            "max_drawdown": {
                "monthly": monthly_1x["max_drawdown"],
                "weekly": weekly_1x["max_drawdown"],
            },
            "capital_utilization": {
                "avg_exposure": {
                    "monthly": m_util["avg_exposure"],
                    "weekly": w_util["avg_exposure"],
                },
                "pct_days_idle": {
                    "monthly": m_util["pct_days_idle_exposure_0"],
                    "weekly": w_util["pct_days_idle_exposure_0"],
                },
            },
            "cost_robustness": {
                "monthly_1x_to_3x_return_decay_pp": round(monthly_decay * 100, 4),
                "weekly_1x_to_3x_return_decay_pp": round(weekly_decay * 100, 4),
            },
            "walk_forward": {
                "positive_fold_ratio": {
                    "monthly": m_wf["positive_fold_ratio"],
                    "weekly": w_wf["positive_fold_ratio"],
                },
                "median_fold_return": {
                    "monthly": m_wf["median_fold_return"],
                    "weekly": w_wf["median_fold_return"],
                },
            },
            "concentration": {
                "top1_share_of_total_realized_pnl": {
                    "monthly": m_conc,
                    "weekly": w_conc,
                }
            },
        },
        "flags": flags,
        "verdict": "WEEKLY CLEARLY DOMINATES MONTHLY" if dominates else "NOT CLEARLY DOMINANT",
    }


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------


def _fmt_pct(value, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def _fmt_num(value, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _print_main_table(metrics: dict[str, object]) -> None:
    print("\n## Full-period metrics (monthly vs weekly, 1x/2x/3x cost)\n")
    header = (
        "| candidate | cost | return | CAGR | ann vol | Sharpe | Sortino | maxDD | Calmar | PF | "
        "decisions | orders | fills | trades/yr | hold (d) | turnover ann | fees total | fees %gross | netPnL/turn | "
        "avg exp | med exp | %>0 | %nearmax | %idle | t2first (d) |"
    )
    print(header)
    print(
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    for label in ("monthly", "weekly"):
        deep = metrics[label]["1x"]["deep"]
        for cost in ("1x", "2x", "3x"):
            m = metrics[label][cost]
            u = deep["capital_utilization"]
            pf = m.get("profit_factor", deep["profit_factor"])
            fees_gross = m.get("fees_pct_of_gross_pnl", deep["fees_pct_of_gross_pnl"])
            net_turn = deep["net_pnl_per_turnover"]
            decisions = m.get("decisions", m.get("rebalance_count"))
            row = (
                f"| {label} | {cost} | {_fmt_pct(m['return'])} | {_fmt_pct(m['cagr'])} | "
                f"{_fmt_pct(m['annualized_volatility'])} | {_fmt_num(m['sharpe'])} | {_fmt_num(m['sortino'])} | "
                f"{_fmt_pct(m['max_drawdown'])} | {_fmt_num(m['calmar'])} | {_fmt_num(pf) if isinstance(pf, float) and math.isfinite(pf) else 'inf'} | "
                f"{decisions} | {deep['orders_created']} | {deep['fills']} | {_fmt_num(deep['trades_per_year'])} | "
                f"{_fmt_num(deep['avg_holding_period_days'])} | {_fmt_num(deep['turnover_annualized'])} | "
                f"{_fmt_num(m['fees_total'], 0)} | {_fmt_pct(fees_gross, 1) if fees_gross is not None else '-'} | "
                f"{_fmt_num(net_turn, 4) if net_turn is not None else '-'} | "
                f"{_fmt_pct(u['avg_exposure'])} | {_fmt_pct(u['median_exposure'])} | "
                f"{_fmt_pct(u['pct_days_exposure_gt_0'])} | {_fmt_pct(u['pct_days_near_max_allocation_ge_0.45'])} | "
                f"{_fmt_pct(u['pct_days_idle_exposure_0'])} | {_fmt_num(u['time_to_first_deployment_days'], 0)} |"
            )
            print(row)
    print(
        "\nnotes: maxDD = max drawdown; PF = profit factor; ann vol = annualized volatility; "
        "fees total = fee+slippage+spread; %nearmax = days exposure >= 0.45 of the 0.50 sleeve; "
        "t2first = days from window start to first fill. orders/fills/holding/utilization are "
        "identical across cost multipliers (cost scaling scales fees only, never order "
        "generation), so the 2x/3x rows show the 1x values for those columns."
    )


def _print_walk_forward(walk: dict[str, object]) -> None:
    print("\n## Walk-forward (identical 6-month folds, 24-month warmup)\n")
    print("| fold | start | end | M return | M sharpe | M maxDD | W return | W sharpe | W maxDD |")
    print("|---|---|---|---|---|---|---|---|---|")
    for row in walk["folds"]:
        print(
            f"| {row['fold']} | {row['start'][:10]} | {row['end'][:10]} | "
            f"{_fmt_pct(row['monthly_return'])} | {_fmt_num(row['monthly_sharpe'])} | {_fmt_pct(row['monthly_max_drawdown'])} | "
            f"{_fmt_pct(row['weekly_return'])} | {_fmt_num(row['weekly_sharpe'])} | {_fmt_pct(row['weekly_max_drawdown'])} |"
        )
    print(
        "\n| candidate | pos folds | ratio | mean | median | worst | best | mean Sharpe | mean maxDD |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for label in ("monthly", "weekly"):
        s = walk["summary"][label]
        print(
            f"| {label} | {s['positive_fold_count']}/{s['total_fold_count']} | {_fmt_pct(s['positive_fold_ratio'])} | "
            f"{_fmt_pct(s['mean_fold_return'])} | {_fmt_pct(s['median_fold_return'])} | {_fmt_pct(s['worst_fold_return'])} | "
            f"{_fmt_pct(s['best_fold_return'])} | {_fmt_num(s['mean_fold_sharpe'])} | {_fmt_pct(s['mean_fold_max_drawdown'])} |"
        )


def _print_calendar_years(yearly: dict[str, object]) -> None:
    print("\n## Calendar-year returns (1x, full window)\n")
    print("| year | monthly | weekly | SPY |")
    print("|---|---|---|---|")
    rows = {entry["year"]: entry for entry in yearly["years"]["monthly"]}
    for year in sorted(rows):
        monthly = next(
            entry["portfolio_return"]
            for entry in yearly["years"]["monthly"]
            if entry["year"] == year
        )
        weekly = next(
            entry["portfolio_return"]
            for entry in yearly["years"]["weekly"]
            if entry["year"] == year
        )
        benchmark = rows[year]["benchmark_return"]
        print(f"| {year} | {_fmt_pct(monthly)} | {_fmt_pct(weekly)} | {_fmt_pct(benchmark)} |")


def _print_staleness(staleness: dict[str, object]) -> None:
    print("\n## Staleness diagnostic (weekly ranking, no trades, 2023-07..2026-07)\n")
    s = staleness["summary"]
    print(
        f"- weeks evaluated: {staleness['weekly_decision_count']}; month starts: {staleness['monthly_decision_count_in_window']}"
    )
    print(
        f"- week-over-week Spearman of ranking: mean {s['spearman_mean']}, median {s['spearman_median']}, min {s['spearman_min']}"
    )
    print(
        f"- week-over-week top-8 churn: mean {_fmt_pct(s['top8_churn_mean'])} names/week, median {_fmt_pct(s['top8_churn_median'])}"
    )
    print(
        f"- month-1 top-8 still top-8: week2 {_fmt_pct(s['monthly_top8_still_top8_week2_mean'])}"
        f", week3 {_fmt_pct(s['monthly_top8_still_top8_week3_mean'])}"
        f", week4 {_fmt_pct(s['monthly_top8_still_top8_week4_mean'])}"
    )
    print("\n| week_start | spearman | top8 churn |")
    print("|---|---|---|")
    for entry in staleness["week_over_week"]:
        spearman = entry["spearman_top_rank"]
        print(
            f"| {entry['week_start'][:10]} | {_fmt_num(spearman) if spearman is not None else '-'} | {_fmt_pct(entry['top8_churn'])} |"
        )


def _print_churn(churn: dict[str, object]) -> None:
    print("\n## Churn / adverse turnover (weekly candidate fills)\n")
    print(f"- round trips (sell->rebuy same symbol within 7 bars): {churn['round_trip_count']}")
    print(
        f"- flip-flops (buy->sell->buy within 7 bars each, incl. A->B->A): {churn['flip_flop_count']}"
    )
    print(f"- cost paid on reversal fills: ${churn['reversal_cost_total']:.2f}")
    print(
        f"- weekly fills not occurring under monthly cadence: {churn['weekly_only_fills_under_monthly_cadence']}/"
        f"{churn['weekly_fills_total']} ({_fmt_pct(churn['pct_weekly_trades_not_under_monthly'])})"
    )


def _print_bands(metrics: dict[str, object]) -> None:
    print("\n## Band analysis (decisions -> orders -> fills, 1x)\n")
    print(
        "| candidate | decisions | orders created | decisions w/ fills | fills | zero-order decisions |"
    )
    print("|---|---|---|---|---|---|")
    for label in ("monthly", "weekly"):
        b = metrics[label]["1x"]["band_analysis"]
        print(
            f"| {label} | {b['decisions']} | {b['orders_created']} | {b['decisions_with_fills']} | "
            f"{b['fills']} | {b['decisions_with_zero_orders']} |"
        )
    print(
        "\nnote: orders created only when drift > 0.5% band AND notional > $10 "
        "(execution policy); every order fills at the next tradable bar's open (fills==orders "
        "unless a bar had no liquidity)."
    )


def _print_concentration(concentration: dict[str, object]) -> None:
    print("\n## Concentration (top-1/2/3 contribution to realized PnL, 1x)\n")
    print("| candidate | rank | symbol | realized PnL | share of total realized PnL |")
    print("|---|---|---|---|---|")
    for label in ("monthly", "weekly"):
        top = concentration[label]["top"]
        for rank, entry in enumerate(top, start=1):
            print(
                f"| {label} | {rank} | {entry['symbol']} | ${entry['realized_pnl']:,.0f} | "
                f"{_fmt_pct(entry['share_of_total_realized_pnl'])} |"
            )
    jackknife = concentration.get("jackknife")
    if jackknife:
        print(
            "\n| candidate | dropped | return delta (pp) | CAGR delta (pp) | Sharpe delta | maxDD delta (pp) |"
        )
        print("|---|---|---|---|---|---|")
        for label in ("monthly", "weekly"):
            for symbol, entry in jackknife[label]["drops"].items():
                print(
                    f"| {label} | {symbol} | {entry['delta_return_pp']:+.2f} | {entry['delta_cagr_pp']:+.2f} | "
                    f"{entry['delta_sharpe']:+.2f} | {entry['delta_max_drawdown_pp']:+.2f} |"
                )
    else:
        print("\njackknife: skipped (gated; see report JSON)")


def _print_verdict(verdict: dict[str, object]) -> None:
    print("\n## Pareto verdict\n")
    for key, value in verdict["evidence"].items():
        if isinstance(value, dict) and "monthly" in value and "weekly" in value:
            print(f"- {key}: monthly={value['monthly']}, weekly={value['weekly']}")
        else:
            print(f"- {key}: {value}")
    print(f"\nverdict: **{verdict['verdict']}**")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research_equity_cadence")
    parser.add_argument("--smoke", action="store_true", help="short window sanity run")
    parser.add_argument("--skip-staleness", action="store_true")
    parser.add_argument("--skip-jackknife", action="store_true")
    args = parser.parse_args(argv)

    start = SMOKE_START if args.smoke else EQUITY_START
    end = SMOKE_END if args.smoke else EQUITY_END
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading equity dataset...")
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print(f"  {len(bars):,} bars")

    print("Building point-in-time equity universe (once, reused by every run)...")
    t0 = time.perf_counter()
    universe = build_product_universe(
        config=load_config(), bars=bars, market=Market.EQUITY, end=EQUITY_END
    )
    print(f"  universe built in {time.perf_counter() - t0:.1f}s")
    universe_hash = hash_point_in_time_universe(universe)
    snapshots = universe.snapshots
    candidate_counts = [snapshot.candidate_count for snapshot in snapshots]
    print(
        f"  universe_hash={universe_hash} snapshots={len(snapshots)} "
        f"avg_candidates={statistics.mean(candidate_counts):.0f} methodology={universe.methodology}"
    )

    monthly_params = dict(FROZEN_EQUITY)
    weekly_params = dict(FROZEN_EQUITY, rebalance_frequency="weekly")
    _assert_cadence(monthly_params, "monthly")
    _assert_cadence(weekly_params, "weekly")
    print("  cadence assertion OK: rebalance_policy_for -> monthly / weekly")

    meta = {
        "mission": "capital-utilization & cadence wave 1 - equity leg",
        "strategy": STRATEGY,
        "strategy_parameters": FROZEN_EQUITY,
        "cadence_candidates": {
            "monthly": "control - frozen spec (v1 baseline)",
            "weekly": {
                "rebalance_frequency": "weekly",
                "note": "strategy ignores the key in its "
                "math; rebalance_policy_for maps it to RebalancePolicy(weekly)",
            },
        },
        "dataset": str(EQUITY_CSV.name),
        "evaluation_window": [start.isoformat(), end.isoformat()],
        "warmup": "2016 full year (formation + beta + scanner lookback)",
        "universe_hash": universe_hash,
        "universe": {
            "snapshots": len(snapshots),
            "avg_candidate_pool": round(statistics.mean(candidate_counts), 1),
            "methodology": universe.methodology,
        },
        "smoke": args.smoke,
        "baseline_reference": "docs/strategy_robustness.md: return +59.21%, CAGR 4.98%, "
        "Sharpe 0.47, Sortino 0.60, maxDD 25.45%, turnover 50.9, 113 rebalances, 300 trades",
    }

    config = load_config()
    report: dict[str, object] = {"meta": meta}

    # ---- full-period runs: cost stress + deep 1x metrics ------------------
    print("\nFull-period runs (1x/2x/3x cost, both cadences)...")
    metrics: dict[str, object] = {}
    run_times: dict[str, float] = {}
    for label, parameters in (("monthly", monthly_params), ("weekly", weekly_params)):
        t0 = time.perf_counter()
        stress = _cost_stress(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=STRATEGY,
            parameters=parameters,
            strategy_timeframe="1d",
            start=start,
            end=end,
            universe=universe,
        )
        elapsed = time.perf_counter() - t0
        run_times[label] = elapsed
        print(f"  {label} cost stress done in {elapsed:.1f}s")
        metrics[label] = stress
        metrics[label]["1x"]["run_seconds"] = round(elapsed / 3.0, 1)

    print("Deep 1x metrics (capital utilization, bands, churn, concentration)...")
    one_x_runs: dict[str, object] = {}
    for label, parameters in (("monthly", monthly_params), ("weekly", weekly_params)):
        run = run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name=STRATEGY,
            parameters=parameters,
            strategy_timeframe="1d",
            start=start,
            end=end,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        one_x_runs[label] = run
        deep = _full_metrics(run.result, run.report, bars)
        metrics[label]["1x"] = {**metrics[label]["1x"], **deep}
        metrics[label]["1x"]["deep"] = deep
        print(
            f"  {label} 1x: return={run.result.total_return:.2%} sharpe={run.result.sharpe:.2f} "
            f"mdd={run.result.max_drawdown:.2%} trades={len(run.result.closed_position_events)}"
        )

    # baseline drift vs frozen validation
    baseline = metrics["monthly"]["1x"]
    report["baseline_drift_check"] = {
        "expected_from_strategy_robustness.md": {
            "return": 0.5921,
            "cagr": 0.0498,
            "sharpe": 0.47,
            "sortino": 0.60,
            "max_drawdown": 0.2545,
            "rebalances": 113,
            "trades": 300,
        },
        "observed_monthly_1x": {
            "return": baseline["return"],
            "cagr": baseline["cagr"],
            "sharpe": baseline["sharpe"],
            "sortino": baseline["sortino"],
            "max_drawdown": baseline["max_drawdown"],
            "rebalances": baseline["decisions"],
            "trades": baseline["trades"],
        },
        "note": "cost model now includes effective-dated SEC/TAF/CAT fees; small drift expected",
    }

    # ---- walk-forward -----------------------------------------------------
    print("\nWalk-forward (identical folds for both cadences)...")
    walk = _walk_forward(config, bars, universe, start=start, end=end, smoke=args.smoke)
    report["walk_forward"] = walk
    metrics["walk_forward"] = walk

    # ---- calendar years (1x full-window reports) --------------------------
    yearly = _calendar_year_returns(
        {"monthly": one_x_runs["monthly"].report, "weekly": one_x_runs["weekly"].report}
    )
    report["calendar_year_returns"] = yearly

    # ---- staleness --------------------------------------------------------
    if args.skip_staleness or args.smoke:
        staleness = {
            "skipped": True,
            "reason": "smoke mode or --skip-staleness",
        }
    else:
        print("\nStaleness diagnostic (weekly ranking, no trades)...")
        strategy = _assert_cadence(weekly_params, "weekly")
        t0 = time.perf_counter()
        staleness = _staleness_diagnostic(
            bars,
            universe,
            strategy,
            window_start=STALENESS_WINDOW[0],
            window_end=STALENESS_WINDOW[1],
        )
        print(f"  staleness done in {time.perf_counter() - t0:.1f}s")
    report["staleness"] = staleness
    metrics["staleness"] = staleness

    # ---- churn (weekly fills vs monthly fills) ----------------------------
    print("\nChurn / adverse turnover analysis...")
    churn = _churn_analysis(
        one_x_runs["weekly"].result,
        one_x_runs["monthly"].result.fills,
        bars,
        start=start,
        end=end,
    )
    report["churn"] = churn
    metrics["churn"] = churn

    # ---- concentration ----------------------------------------------------
    print("Concentration (top-1/2/3)...")
    concentration: dict[str, object] = {}
    for label, run in one_x_runs.items():
        per_symbol = sorted(
            ((item.symbol, item.realized_pnl) for item in run.result.per_symbol),
            key=lambda item: -item[1],
        )
        total = sum(pnl for _symbol, pnl in per_symbol)
        top = [
            {
                "symbol": symbol,
                "realized_pnl": pnl,
                "share_of_total_realized_pnl": round(pnl / total, 4) if total != 0 else 0.0,
            }
            for symbol, pnl in per_symbol[:3]
        ]
        concentration[label] = {"top": top, "total_realized_pnl": round(total, 2)}
    report["concentration"] = concentration
    metrics["concentration"] = concentration

    # ---- jackknife (gated) ------------------------------------------------
    mean_run_seconds = statistics.fmean(run_times.values()) / 3.0
    if args.skip_jackknife or args.smoke:
        jackknife = {"skipped": True, "reason": "smoke mode or --skip-jackknife"}
    elif mean_run_seconds > JACKKNIFE_MAX_RUN_SECONDS:
        jackknife = {
            "skipped": True,
            "reason": f"each full-period run averages {mean_run_seconds:.0f}s > "
            f"{JACKKNIFE_MAX_RUN_SECONDS:.0f}s gate (mission: skip if each run > 5 min; "
            f"gated earlier to keep the bake-off bounded)",
        }
    else:
        print(
            f"\nJackknife (mean full run {mean_run_seconds:.0f}s <= gate; running top-1/2/3 drops)..."
        )
        t0 = time.perf_counter()
        jackknife = _jackknife(config, bars, universe, one_x_runs, start=start, end=end)
        print(f"  jackknife done in {time.perf_counter() - t0:.1f}s")
    report["jackknife"] = jackknife
    concentration["jackknife"] = jackknife
    metrics["jackknife"] = jackknife

    # ---- verdict ----------------------------------------------------------
    verdict = _verdict(metrics)
    report["verdict"] = verdict

    # ---- persist ----------------------------------------------------------
    files = {
        "equity_cadence_report.json": report,
        "equity_cadence_full_period.json": {
            "meta": meta,
            "monthly": metrics["monthly"],
            "weekly": metrics["weekly"],
            "baseline_drift_check": report["baseline_drift_check"],
        },
        "equity_cadence_walk_forward.json": {
            "meta": meta,
            **report["walk_forward"],
            "calendar_year_returns": yearly,
        },
        "equity_cadence_staleness.json": {"meta": meta, **staleness},
        "equity_cadence_churn.json": {"meta": meta, **churn},
        "equity_cadence_concentration.json": {"meta": meta, **concentration},
        "equity_cadence_verdict.json": {"meta": meta, **verdict},
    }
    for name, payload in files.items():
        (OUTPUT_DIR / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"  wrote {OUTPUT_DIR / name}")

    # ---- print tables -----------------------------------------------------
    _print_main_table(metrics)
    _print_walk_forward(walk)
    _print_calendar_years(yearly)
    _print_staleness(staleness)
    _print_churn(churn)
    _print_bands(metrics)
    _print_concentration(concentration)
    _print_verdict(verdict)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
