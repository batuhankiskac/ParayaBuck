#!/usr/bin/env python3
# ruff: noqa: E501
"""Optimization experiment driver (2026-08).

Runs frozen-strategy VARIANTS through the production backtest harness
(``run_product_backtest``) WITHOUT modifying project code: every variant is
expressed as harness parameters (strategy parameters override) or an
in-memory ``dataclasses.replace`` copy of the loaded config (sleeve
allocation, scanner shortlist). Results land in
``data/research/optimization_2026_08/*.json``.

Subcommands: equity | crypto | b6 | analyze | wf  (append --smoke for a
short 2024H1 plumbing check).
"""

from __future__ import annotations

import dataclasses
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path("/Users/batuhankiskac/Desktop/ParayaBuck")
sys.path.insert(0, str(REPO))

from parayabuck.backtest.product import (  # noqa: E402
    build_product_universe,
    load_csv_bars,
    run_product_backtest,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402

OUT = REPO / "data" / "research" / "optimization_2026_08"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_extended.csv"
EQ_START = datetime(2017, 1, 3, tzinfo=UTC)
EQ_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
CR_START = datetime(2021, 4, 1, tzinfo=UTC)
CR_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
CONFIG_PATH = str(REPO / "config" / "settings.yaml")


def config_with_alloc(market: Market, allocation: float):
    cfg = load_config(CONFIG_PATH)
    if market == Market.EQUITY:
        return dataclasses.replace(
            cfg,
            strategy=dataclasses.replace(
                cfg.strategy,
                equity=dataclasses.replace(cfg.strategy.equity, maximum_allocation=allocation),
            ),
        )
    return dataclasses.replace(
        cfg,
        strategy=dataclasses.replace(
            cfg.strategy,
            crypto=dataclasses.replace(cfg.strategy.crypto, maximum_allocation=allocation),
        ),
    )


def config_with_shortlist(target_shortlist: int):
    cfg = load_config(CONFIG_PATH)
    return dataclasses.replace(
        cfg,
        universe=dataclasses.replace(
            cfg.universe,
            equity=dataclasses.replace(cfg.universe.equity, target_shortlist=target_shortlist),
        ),
    )


def _result_payload(label: str, product) -> dict:
    result = product.result
    invested = [
        {"ts": ts.isoformat(), "invested": sum(weights.values())}
        for ts, weights in result.weight_history
    ]
    return {
        "label": label,
        "generated_at": datetime.now(UTC).isoformat(),
        "universe_hash": hash_point_in_time_universe(product.universe),
        "universe_snapshots": len(product.universe.snapshots),
        "avg_candidate_pool": (
            statistics.fmean(s.candidate_count for s in product.universe.snapshots)
            if product.universe.snapshots
            else 0.0
        ),
        "metrics": {
            "total_return": result.total_return,
            "cagr": result.cagr,
            "sharpe": result.sharpe,
            "sortino": result.sortino,
            "calmar": result.calmar,
            "max_drawdown": result.max_drawdown,
            "max_drawdown_duration_days": result.max_drawdown_duration_days,
            "turnover": result.turnover,
            "fee_total": result.fee_total,
            "slippage_cost_total": result.slippage_cost_total,
            "spread_cost_total": result.spread_cost_total,
            "average_cash_ratio": result.average_cash_ratio,
            "average_holdings_count": result.average_holdings_count,
            "rebalance_count": result.rebalance_count,
            "closed_positions": result.closed_positions,
            "maximum_concentration": result.maximum_concentration,
            "positive_month_ratio": result.positive_month_ratio,
            "worst_month_return": result.worst_month_return,
            "worst_year_return": result.worst_year_return,
            "duration_days": result.duration_days,
            "benchmark_return": result.benchmark_return,
            "shadow_halt_events": len(result.drawdown_halt_shadow_events),
        },
        "per_symbol_top5": [
            {"symbol": item.symbol, "realized_pnl": item.realized_pnl}
            for item in sorted(result.per_symbol, key=lambda item: -item.realized_pnl)[:5]
        ],
        "equity_curve": list(result.equity_curve),
        "curve_timestamps": [ts.isoformat() for ts in result.curve_timestamps],
        "invested_weight_history": invested,
    }


def _save(label: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label}.json"
    path.write_text(json.dumps(payload, indent=1, allow_nan=False), encoding="utf-8")
    print(f"  saved {path.name} ({path.stat().st_size / 1024:.0f} KB)")


def _run_and_save(label: str, cfg, bars, market: Market, **kwargs) -> None:
    started = time.time()
    product = run_product_backtest(
        cfg, bars, market=market, persist=False, persist_artifacts=False, **kwargs
    )
    metrics = product.result
    print(
        f"  [{label}] ret={metrics.total_return:.2%} cagr={metrics.cagr:.2%} "
        f"sharpe={metrics.sharpe:.2f} mdd={metrics.max_drawdown:.2%} "
        f"cash={metrics.average_cash_ratio:.1%} fees=${metrics.fee_total:.0f} "
        f"({time.time() - started:.0f}s)",
        flush=True,
    )
    _save(label, _result_payload(label, product))


def cmd_equity(smoke: bool) -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC) if smoke else EQ_START
    end = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC) if smoke else EQ_END
    print("Loading equity dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print(f"  {len(bars):,} bars", flush=True)
    print("Building equity PIT universe (shortlist 300)...", flush=True)
    universe = build_product_universe(load_config(CONFIG_PATH), bars, market=Market.EQUITY, end=end)
    print(
        f"  snapshots={len(universe.snapshots)} "
        f"avg_pool={statistics.fmean(s.candidate_count for s in universe.snapshots):.1f}",
        flush=True,
    )
    for label, allocation in (("eq_alloc050", 0.50), ("eq_alloc060", 0.60), ("eq_alloc070", 0.70)):
        _run_and_save(
            label if not smoke else f"smoke_{label}",
            config_with_alloc(Market.EQUITY, allocation),
            bars,
            Market.EQUITY,
            strategy_timeframe="1d",
            start=start,
            end=end,
            universe=universe,
        )


def cmd_crypto(smoke: bool) -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC) if smoke else CR_START
    end = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC) if smoke else CR_END
    print("Loading crypto dataset (1h)...", flush=True)
    bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    print(f"  {len(bars_1h):,} 1h bars", flush=True)
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    print(f"  aggregated {len(bars_1d):,} 1d bars", flush=True)
    print("Building crypto PIT universe from 1h scanner bars...", flush=True)
    universe = build_product_universe(
        load_config(CONFIG_PATH), bars_1h, market=Market.CRYPTO, end=end, timeframe="1h"
    )
    print(
        f"  snapshots={len(universe.snapshots)} "
        f"avg_pool={statistics.fmean(s.candidate_count for s in universe.snapshots):.1f}",
        flush=True,
    )
    base = dict(load_config(CONFIG_PATH).strategy.crypto.parameters)
    variants = (
        ("cr_base_topn2", None, 0.10),
        ("cr_topn3", dict(base, top_n=3), 0.10),
        ("cr_alloc005", None, 0.05),
    )
    for label, parameters, allocation in variants:
        kwargs = {}
        if parameters is not None:
            kwargs["strategy_name"] = "crypto_low_variance_v2"
            kwargs["parameters"] = parameters
        _run_and_save(
            label if not smoke else f"smoke_{label}",
            config_with_alloc(Market.CRYPTO, allocation),
            bars_1d,
            Market.CRYPTO,
            strategy_timeframe="1d",
            start=start,
            end=end,
            universe=universe,
            **kwargs,
        )


def cmd_b6(smoke: bool) -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC) if smoke else EQ_START
    end = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC) if smoke else EQ_END
    print("Loading equity dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print("Building equity PIT universe (shortlist 500)...", flush=True)
    cfg500 = config_with_shortlist(500)
    universe = build_product_universe(cfg500, bars, market=Market.EQUITY, end=end)
    print(
        f"  snapshots={len(universe.snapshots)} "
        f"avg_pool={statistics.fmean(s.candidate_count for s in universe.snapshots):.1f}",
        flush=True,
    )
    _run_and_save(
        "b6_shortlist500" if not smoke else "smoke_b6",
        cfg500,
        bars,
        Market.EQUITY,
        strategy_timeframe="1d",
        start=start,
        end=end,
        universe=universe,
    )


def _load(label: str) -> dict:
    return json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))


def _curve(payload: dict) -> list[tuple[datetime, float]]:
    return [
        (datetime.fromisoformat(ts), value)
        for ts, value in zip(payload["curve_timestamps"], payload["equity_curve"], strict=True)
    ]


def _series_metrics(points: list[tuple[datetime, float]]) -> dict:
    if len(points) < 3:
        return {}
    values = [value for _ts, value in points]
    days = (points[-1][0] - points[0][0]).total_seconds() / 86400
    years = days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[0] > 0 else 0.0
    returns = [
        values[index] / values[index - 1] - 1
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return {
        "total_return": values[-1] / values[0] - 1,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "days": days,
    }


def _cash_yield_overlay(payload: dict, rate: float) -> list[tuple[datetime, float]]:
    """Compound interest on the uninvested fraction along the simulated path.

    The engine records ``cash_rate_annualized`` as a hurdle metric only (it
    never credits interest to the equity curve), so the cash-yield effect is
    measured as an overlay: between consecutive curve points the cash portion
    grows at ``rate`` while the invested portion follows the simulated move.
    """
    curve = _curve(payload)
    invested = {
        datetime.fromisoformat(entry["ts"]): entry["invested"]
        for entry in payload["invested_weight_history"]
    }
    invested_timestamps = sorted(invested)
    adjusted: list[tuple[datetime, float]] = []
    previous_adjusted = curve[0][1]
    for index, (ts, value) in enumerate(curve):
        if index == 0:
            adjusted.append((ts, value))
            continue
        previous_ts, previous_control = curve[index - 1]
        candidates = [t for t in invested_timestamps if t <= previous_ts]
        if candidates:
            invested_frac = invested[max(candidates)]
        else:
            invested_frac = 1.0 - payload["metrics"]["average_cash_ratio"]
        cash_frac = max(0.0, 1.0 - min(1.0, invested_frac))
        years = (ts - previous_ts).total_seconds() / (365.25 * 86400)
        cash_growth = (1.0 + rate) ** years
        # Control applies one blended relative move ``rel`` to ALL equity
        # (cash included, earning 0). The invested-only move is therefore
        # r = (rel - c) / (1 - c); crediting g to the cash fraction gives
        # step = (1-c)*r + c*g = rel + c*(g-1).
        relative = value / previous_control if previous_control > 0 else 1.0
        step = relative + cash_frac * (cash_growth - 1.0)
        previous_adjusted *= step
        adjusted.append((ts, previous_adjusted))
    return adjusted
def _drawdown_sweep(points: list[tuple[datetime, float]]) -> dict:
    values = [value for _ts, value in points]
    peak = values[0]
    dd = []
    for value in values:
        peak = max(peak, value)
        dd.append((peak - value) / peak if peak > 0 else 0.0)
    sweep = {}
    for threshold in (0.03, 0.05, 0.075, 0.10, 0.15):
        days_above = sum(1 for value in dd if value >= threshold)
        episodes = 0
        previous = False
        for value in dd:
            current = value >= threshold
            if current and not previous:
                episodes += 1
            previous = current
        sweep[f"{threshold:.3f}"] = {"days_at_or_below": days_above, "episodes": episodes}
    return sweep


def _align_combined(eq_points, crypto_points):
    """Combined account series on equity timestamps (robustness-pack semantics:
    sleeves sized independently against one 100k account; combined = eq + cr -
    100k)."""
    cr_by_date: dict = {}
    for ts, value in crypto_points:
        cr_by_date[ts.date()] = value
    common_start = max(eq_points[0][0], crypto_points[0][0])
    common_end = min(eq_points[-1][0], crypto_points[-1][0])
    combined: list[tuple[datetime, float]] = []
    for ts, value in eq_points:
        if ts < common_start or ts > common_end:
            continue
        crypto_value = cr_by_date.get(ts.date())
        if crypto_value is None:
            earlier = [date for date in cr_by_date if date <= ts.date()]
            if not earlier:
                continue
            crypto_value = cr_by_date[max(earlier)]
        combined.append((ts, value + crypto_value - 100_000.0))
    eq_only = [(ts, value) for ts, value in eq_points if common_start <= ts <= common_end]
    return combined, eq_only, (common_start, common_end)


def cmd_analyze() -> None:
    eq = _load("eq_alloc050")
    cr = _load("cr_base_topn2")

    eq_points = _curve(eq)
    combined, eq_only, window = _align_combined(eq_points, _curve(cr))

    cr5_points = _curve(_load("cr_alloc005"))
    combined5, _eq_only5, _w5 = _align_combined(eq_points, cr5_points)

    combined_metrics = {
        "combined_base_050_010": _series_metrics(combined),
        "combined_no_crypto": _series_metrics(eq_only),
        "combined_crypto005": _series_metrics(combined5),
    }

    # -- B1 cash-yield overlay ----------------------------------------------
    controls = {
        "equity": _series_metrics(eq_points),
        "crypto": _series_metrics(_curve(cr)),
        "combined_base_050_010": combined_metrics["combined_base_050_010"],
    }
    cash_overlay = {}
    for rate in (0.03, 0.045):
        eq_adj = _cash_yield_overlay(eq, rate)
        cr_adj = _cash_yield_overlay(cr, rate)
        combined_adj, _eq_adj_only, _w = _align_combined(eq_adj, cr_adj)
        cash_overlay[f"{rate:.3f}"] = {
            "equity": _series_metrics(eq_adj),
            "crypto": _series_metrics(cr_adj),
            "combined_base_050_010": _series_metrics(combined_adj),
        }

    # -- B2 drawdown-threshold sweep ----------------------------------------
    sweeps = {
        "equity": _drawdown_sweep(eq_points),
        "crypto": _drawdown_sweep(_curve(cr)),
        "combined_base_050_010": _drawdown_sweep(combined),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "combined_window": [window[0].isoformat(), window[1].isoformat()],
        "combined_metrics": combined_metrics,
        "sleeve_controls": controls,
        "b1_cash_yield_overlay": cash_overlay,
        "b2_drawdown_threshold_sweep": sweeps,
        "crypto_topn3_vs_base": {
            "base": _load("cr_base_topn2")["metrics"],
            "topn3": _load("cr_topn3")["metrics"],
        },
        "equity_alloc_variants": {
            "alloc050": eq["metrics"],
            "alloc060": _load("eq_alloc060")["metrics"],
            "alloc070": _load("eq_alloc070")["metrics"],
        },
        "b6_shortlist500": _load("b6_shortlist500")["metrics"],
    }
    _save("analysis", payload)
    print(json.dumps({"combined": combined_metrics, "cash_overlay": cash_overlay}, indent=2))


def cmd_wf() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from research_helpers import _calendar_fold_window  # noqa: E402

    print("Loading datasets for walk-forward...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    eq_universe = build_product_universe(
        load_config(CONFIG_PATH), bars, market=Market.EQUITY, end=EQ_END
    )
    cr_universe = build_product_universe(
        load_config(CONFIG_PATH), bars_1h, market=Market.CRYPTO, end=CR_END, timeframe="1h"
    )
    eq_folds = _calendar_fold_window(datetime(2019, 1, 1, tzinfo=UTC), EQ_END, 6)
    cr_folds = _calendar_fold_window(datetime(2022, 1, 1, tzinfo=UTC), CR_END, 6)
    print(f"equity folds={len(eq_folds)} crypto folds={len(cr_folds)}", flush=True)

    results: dict[str, dict] = {}

    def _fold_record(index, fold_start, fold_end, product):
        return {
            "fold": index,
            "window": [fold_start.date().isoformat(), fold_end.date().isoformat()],
            "total_return": product.result.total_return,
            "sharpe": product.result.sharpe,
            "max_drawdown": product.result.max_drawdown,
        }

    for label, allocation in (("wf_eq_alloc050", 0.50), ("wf_eq_alloc060", 0.60)):
        fold_stats = []
        for index, (fold_start, fold_end) in enumerate(eq_folds, start=1):
            product = run_product_backtest(
                config_with_alloc(Market.EQUITY, allocation),
                bars,
                market=Market.EQUITY,
                strategy_timeframe="1d",
                start=fold_start,
                end=fold_end,
                universe=eq_universe,
                persist=False,
                persist_artifacts=False,
            )
            fold_stats.append(_fold_record(index, fold_start, fold_end, product))
            print(
                f"  [{label}] fold {index}/{len(eq_folds)} ret={product.result.total_return:.2%}",
                flush=True,
            )
        results[label] = {
            "folds": fold_stats,
            "positive_folds": sum(1 for item in fold_stats if item["total_return"] > 0),
            "median_fold_return": statistics.median(item["total_return"] for item in fold_stats),
        }

    base = dict(load_config(CONFIG_PATH).strategy.crypto.parameters)
    for label, parameters in (
        ("wf_cr_base_topn2", None),
        ("wf_cr_topn3", dict(base, top_n=3)),
    ):
        kwargs = {}
        if parameters is not None:
            kwargs = {"strategy_name": "crypto_low_variance_v2", "parameters": parameters}
        fold_stats = []
        for index, (fold_start, fold_end) in enumerate(cr_folds, start=1):
            product = run_product_backtest(
                config_with_alloc(Market.CRYPTO, 0.10),
                bars_1d,
                market=Market.CRYPTO,
                strategy_timeframe="1d",
                start=fold_start,
                end=fold_end,
                universe=cr_universe,
                persist=False,
                persist_artifacts=False,
                **kwargs,
            )
            fold_stats.append(_fold_record(index, fold_start, fold_end, product))
        results[label] = {
            "folds": fold_stats,
            "positive_folds": sum(1 for item in fold_stats if item["total_return"] > 0),
            "median_fold_return": statistics.median(item["total_return"] for item in fold_stats),
        }
        print(f"  [{label}] done", flush=True)

    _save("walkforward", {"generated_at": datetime.now(UTC).isoformat(), "results": results})


def main() -> int:
    smoke = "--smoke" in sys.argv
    command = next((arg for arg in sys.argv[1:] if not arg.startswith("--")), "equity")
    commands = {
        "equity": lambda: cmd_equity(smoke),
        "crypto": lambda: cmd_crypto(smoke),
        "b6": lambda: cmd_b6(smoke),
        "analyze": cmd_analyze,
        "wf": cmd_wf,
    }
    if command not in commands:
        raise SystemExit(f"unknown command: {command}")
    commands[command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
