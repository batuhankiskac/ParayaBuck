#!/usr/bin/env python3
"""research_equity_staleness.py — Wave 5 equity signal-staleness diagnostic.

Measures how much the residual-momentum RANKING changes between scheduled
monthly decisions (diagnostic only — NEVER triggers trades). Reuses the exact
decision scoring (strategy.rank_candidates shares decide()'s math) over the
PIT universe, sampled weekly on the canonical window.

Outputs: data/research/cadence_research_2026_08/equity/equity_staleness.json
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.product import build_product_universe, load_csv_bars
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
OUT = REPO / "data" / "research" / "cadence_research_2026_08" / "equity" / "equity_staleness.json"
SAMPLE_START = datetime(2023, 7, 1, tzinfo=UTC)
SAMPLE_END = datetime(2026, 7, 30, tzinfo=UTC)
TOP_N = 8
WINDOW_WEEKS = 4  # how far a monthly decision's top-8 is tracked


def _spearman(a: list[str], b: list[str]) -> float:
    """Rank correlation between two ordered lists (shared members only)."""
    rank_b = {symbol: index for index, symbol in enumerate(b)}
    shared = [(index, rank_b[symbol]) for index, symbol in enumerate(a) if symbol in rank_b]
    n = len(shared)
    if n < 2:
        return float("nan")
    xs = [x for x, _y in shared]
    ys = [y for _x, y in shared]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return float("nan")
    return cov / math.sqrt(var_x * var_y)


def main() -> int:
    config = load_config()
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=None)
    strategy = portfolio_strategy_from(
        "equity_residual_momentum_v1",
        {
            "formation_bars": 252,
            "skip_bars": 21,
            "beta_window_bars": 273,
            "residual_vol_window": 63,
            "top_n": TOP_N,
            "max_single_weight": 0.10,
            "ivol_reject_quantile": 0.70,
            "exclude_symbols": ["SPY"],
        },
    )
    # per-symbol sorted timestamp lists for O(log n) slicing
    by_symbol: dict[str, list] = {}
    for bar in bars:
        if bar.market != Market.EQUITY or bar.timeframe != strategy.timeframe:
            continue
        by_symbol.setdefault(bar.symbol, []).append(bar)
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda item: item.timestamp)
    symbol_times = {symbol: [b.timestamp for b in series] for symbol, series in by_symbol.items()}

    def rank_at(ts: datetime) -> list[str]:
        history: dict[str, tuple] = {}
        prices: dict[str, float] = {}
        for symbol, times in symbol_times.items():
            index = bisect.bisect_right(times, ts)
            if index == 0:
                continue
            series = by_symbol[symbol]
            history[symbol] = tuple(series[:index])
            prices[symbol] = series[index - 1].close
        context = PortfolioContext(
            decision_timestamp=ts,
            market=Market.EQUITY,
            timeframe=strategy.timeframe,
            history=history,
            universe=universe,
            universe_version="staleness-diagnostic",
            equity=100_000.0,
            holdings={},
            prices=prices,
            benchmark_symbol="SPY",
        )
        return list(strategy.rank_candidates(context))

    # weekly Monday samples
    samples: list[tuple[datetime, list[str]]] = []
    cursor = SAMPLE_START
    while cursor <= SAMPLE_END:
        if cursor.weekday() != 0:  # Monday
            cursor += timedelta(days=1)
            continue
        ranked = rank_at(cursor)
        if ranked:
            samples.append((cursor, ranked))
        cursor += timedelta(days=7)
    print(f"weekly samples: {len(samples)}")

    # week-over-week rank correlation + top-N churn
    correlations = []
    churns = []
    for (ts_a, a), (ts_b, b) in zip(samples, samples[1:], strict=False):
        correlations.append(_spearman(a[:50], b[:50]))
        top_a = set(a[:TOP_N])
        top_b = set(b[:TOP_N])
        churns.append(len(top_a ^ top_b) / TOP_N)
    correlations = [c for c in correlations if not math.isnan(c)]
    mean_rank_corr = statistics.fmean(correlations)
    median_rank_corr = statistics.median(correlations)
    mean_top8_churn = statistics.fmean(churns)
    median_top8_churn = statistics.median(churns)

    # monthly-decision top-8 persistence over the following 3 weekly samples
    # (a decision at the first Monday of a month -> weeks 1..3 after)
    monthly_weeks = [
        (idx, samples[idx])
        for idx in range(1, len(samples) - WINDOW_WEEKS)
        if samples[idx][0].month != samples[idx - 1][0].month
    ]
    persistence: list[list[float]] = []
    for idx, (_ts, ranked) in monthly_weeks:
        top = set(ranked[:TOP_N])
        row = []
        for offset in range(1, WINDOW_WEEKS + 1):
            follow = set(samples[idx + offset][1][:TOP_N])
            row.append(len(top & follow) / TOP_N)
        persistence.append(row)
    mean_persistence = (
        [statistics.fmean([row[w] for row in persistence]) for w in range(WINDOW_WEEKS)] if persistence else []
    )
    # average time a symbol stays in top-8 (mean consecutive weeks in top-8)
    in_top8: dict[str, int] = {}
    for _ts, ranked in samples:
        for symbol in ranked[:TOP_N]:
            in_top8[symbol] = in_top8.get(symbol, 0) + 1

    result = {
        "strategy": strategy.name,
        "window": [SAMPLE_START.isoformat(), SAMPLE_END.isoformat()],
        "samples": len(samples),
        "rank_correlation_week_over_week": {
            "mean": mean_rank_corr,
            "median": median_rank_corr,
            "min": min(correlations) if correlations else None,
        },
        "top8_churn_week_over_week": {
            "mean": mean_top8_churn,
            "median": median_top8_churn,
        },
        "monthly_top8_persistence": {
            "weeks": [w + 1 for w in range(WINDOW_WEEKS)],
            "mean_fraction_still_top8": mean_persistence,
            "n_monthly_decisions": len(monthly_weeks),
        },
        "top8_membership": {
            "symbols_ever_in_top8": len(in_top8),
            "max_weeks_single_symbol": max(in_top8.values()) if in_top8 else 0,
        },
        "note": (
            "diagnostic only; ranking from strategy.rank_candidates (shares decide() scoring) "
            "on PIT universe; weekly Monday samples; Spearman over top-50; top-8 churn = "
            "symmetric set difference / 8."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
