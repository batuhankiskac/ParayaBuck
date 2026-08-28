"""Strategy-math isolated microbenchmark for Mission 2.

Isolates 150 symbols, 273-bar beta window, SPY benchmark, 26 decisions
as requested in spec 26. Reports per-component times.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parayabuck.backtest.numeric_history import (
    aligned_returns_numeric,
    build_numeric_history,
    compounded_return_array,
    ols_beta_arrays,
    volatility_array,
)
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum import _aligned_returns, _compounded_return, _ols_beta, _volatility

SYNTH_SYMBOLS = 150
DAYS = 500
START = datetime(2020, 1, 1, tzinfo=UTC)


def make_bars():
    bars = []
    for d in range(DAYS):
        ts = START + timedelta(days=d)
        # skip weekends to mimic trading days (5/7)
        if ts.weekday() >= 5:
            continue
        # SPY
        spy_close = 300 + d * 0.1 + (d % 5) * 0.2
        bars.append(
            Bar(
                timestamp=ts,
                symbol="SPY",
                timeframe="1d",
                open=spy_close,
                high=spy_close * 1.01,
                low=spy_close * 0.99,
                close=spy_close,
                volume=1_000_000,
                market=Market.EQUITY,
            )
        )
        for i in range(SYNTH_SYMBOLS):
            sym = f"S{i:03d}"
            close = 50 + i * 0.5 + d * 0.05 + (d % 7) * 0.1
            bars.append(
                Bar(
                    timestamp=ts,
                    symbol=sym,
                    timeframe="1d",
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=500_000,
                    market=Market.EQUITY,
                )
            )
    # Add some missing bars randomly for 10% of symbols on random days
    import random

    random.seed(0)
    filtered = []
    for bar in bars:
        if bar.symbol != "SPY" and random.random() < 0.02:
            continue
        filtered.append(bar)
    return filtered


def build_contexts(bars, numeric_view=None):
    # Build 26 decision contexts weekly
    # Create simple universe with all symbols
    # Use 26 decision timestamps: every 7 days after first 274 days
    sorted_ts = sorted({b.timestamp for b in bars})
    # need at least 274 bars before first decision
    decisions = sorted_ts[274::7][:26]
    # Build history dict for each decision timestamp (only up to that ts)
    # For legacy path, we need dict of tuple[Bar]
    # For numeric, we use view but still need history for fallback
    # We will create contexts with history trimmed to last 274+1 bars
    from collections import defaultdict

    by_symbol = defaultdict(list)
    for b in sorted(bars, key=lambda x: (x.timestamp, x.symbol)):
        by_symbol[b.symbol].append(b)
    contexts = []
    for ts in decisions:
        hist = {}
        for sym, lst in by_symbol.items():
            # filter <= ts and take last 274+1
            filtered = [bar for bar in lst if bar.timestamp <= ts]
            if len(filtered) > 275:
                filtered = filtered[-275:]
            if filtered:
                hist[sym] = tuple(filtered)
        ctx = PortfolioContext(
            decision_timestamp=ts,
            market=Market.EQUITY,
            timeframe="1d",
            history=hist,
            universe=None,
            universe_version="test",
            equity=100_000,
            holdings={},
            prices={},
            benchmark_symbol="SPY",
            numeric_history=numeric_view,
        )
        contexts.append(ctx)
    return contexts


def bench_legacy(contexts):
    # Time components
    t_aligned = 0
    t_beta = 0
    t_resid = 0
    t_vol = 0
    t_comp = 0
    t_total = 0
    # Use strategy instance to leverage existing logic but micro time
    for ctx in contexts:
        # replicate inner loop of _score_candidates without universe filtering (use all)
        hist = ctx.history
        spy_bars = hist.get("SPY", ())
        market_dict = {b.timestamp: b.close for b in spy_bars if b.close > 0}
        for sym, bars in hist.items():
            if sym == "SPY":
                continue
            if len(bars) < 274:
                continue
            s0 = time.perf_counter()
            aligned = _aligned_returns(bars, market_dict, 273)
            t_aligned += time.perf_counter() - s0
            if aligned is None:
                continue
            sr, mr = aligned
            s0 = time.perf_counter()
            beta = _ols_beta(sr, mr)
            t_beta += time.perf_counter() - s0
            s0 = time.perf_counter()
            residuals = [s - beta * m for s, m in zip(sr, mr, strict=True)]
            t_resid += time.perf_counter() - s0
            s0 = time.perf_counter()
            _ = _volatility(residuals[-63:])
            t_vol += time.perf_counter() - s0
            s0 = time.perf_counter()
            _ = _compounded_return(residuals[-252 - 21 : -21])
            t_comp += time.perf_counter() - s0
        t_total += 0  # not needed
    return dict(aligned=t_aligned, beta=t_beta, residual=t_resid, vol=t_vol, compounded=t_comp)


def bench_numeric(contexts, view, bench_series):
    t_aligned = 0
    t_beta = 0
    t_resid = 0
    t_vol = 0
    t_comp = 0
    for ctx in contexts:
        for sym in list(view.series_by_symbol.keys())[:150]:
            if sym == "SPY":
                continue
            stock_series = view.series(sym)
            if stock_series is None:
                continue
            s0 = time.perf_counter()
            aligned = aligned_returns_numeric(stock_series, bench_series, ctx.decision_timestamp, 273)
            t_aligned += time.perf_counter() - s0
            if aligned is None:
                continue
            sr, mr = aligned
            s0 = time.perf_counter()
            beta = ols_beta_arrays(sr, mr)
            t_beta += time.perf_counter() - s0
            s0 = time.perf_counter()
            residuals = sr - beta * mr
            t_resid += time.perf_counter() - s0
            s0 = time.perf_counter()
            _ = volatility_array(residuals[-63:])
            t_vol += time.perf_counter() - s0
            s0 = time.perf_counter()
            _ = compounded_return_array(residuals[-252 - 21 : -21])
            t_comp += time.perf_counter() - s0
    return dict(aligned=t_aligned, beta=t_beta, residual=t_resid, vol=t_vol, compounded=t_comp)


def main():
    print("Building synthetic bars...")
    bars = make_bars()
    print(f"bars {len(bars)} symbols {len({b.symbol for b in bars})}")
    view = build_numeric_history(bars, market=Market.EQUITY, timeframe="1d")
    bench_series = view.series("SPY")
    contexts_legacy = build_contexts(bars, numeric_view=None)
    contexts_numeric = build_contexts(bars, numeric_view=view)
    print(f"contexts {len(contexts_legacy)} decisions, each ~{len(contexts_legacy[0].history)} symbols")

    # Warmup
    bench_legacy(contexts_legacy[:1])
    bench_numeric(contexts_numeric[:1], view, bench_series)

    repeats = 3
    legacy_times = []
    numeric_times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        bench_legacy(contexts_legacy)
        legacy_times.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        bench_numeric(contexts_numeric, view, bench_series)
        numeric_times.append(time.perf_counter() - t0)

    # Single run detailed
    leg = bench_legacy(contexts_legacy)
    num = bench_numeric(contexts_numeric, view, bench_series)
    print("\n=== Strategy-math microbenchmark (150 sym, 273 window, 26 decisions) ===")
    print(
        f"Legacy total median {sorted(legacy_times)[1]:.4f}s, Numeric median {sorted(numeric_times)[1]:.4f}s, speedup {sorted(legacy_times)[1] / sorted(numeric_times)[1]:.2f}x"
    )
    print("Legacy components:")
    for k, v in leg.items():
        print(f"  {k:12s}: {v:.4f}s")
    print("Numeric components:")
    for k, v in num.items():
        print(f"  {k:12s}: {v:.4f}s")
    print("Component speedups:")
    for k in leg:
        if num[k] > 0:
            print(f"  {k:12s}: {leg[k] / num[k]:.2f}x")

    # Also benchmark _score_candidates total via strategy
    strat = portfolio_strategy_from("equity_residual_momentum_v3", parameters={})

    # Need universe for strategy; we already have contexts with numeric view
    # Time full _score_candidates
    def bench_score(ctxs):
        t0 = time.perf_counter()
        for ctx in ctxs:
            strat._score_candidates(ctx)
        return time.perf_counter() - t0

    for _ in range(2):
        bench_score(contexts_legacy)
        bench_score(contexts_numeric)
    t_leg = []
    t_num = []
    for _ in range(repeats):
        t_leg.append(bench_score(contexts_legacy))
        t_num.append(bench_score(contexts_numeric))
    print("\nFull _score_candidates (V3, 150 sym, 26 decisions):")
    print(f"  legacy median {sorted(t_leg)[1]:.4f}s")
    print(f"  numeric median {sorted(t_num)[1]:.4f}s speedup {sorted(t_leg)[1] / sorted(t_num)[1]:.2f}x")
    # V3 multi-horizon is included


if __name__ == "__main__":
    main()
