"""Backtest core benchmark: engine-only timing vs end-to-end.

Measures single-trial backtest core performance without changing semantics.
Produces machine-readable JSON for before/after comparison.

Cases:
  A - single realistic equity run (large CSV if present, else synthetic)
  B - repeated same-invariant runs (research usage, reuses prepared inputs)
  C - smaller CI-safe fixture (synthetic, no giant dataset required)

Reports: total elapsed, engine-only elapsed, decisions, bars, bars/sec, decisions/sec, peak RSS
"""

from __future__ import annotations

import gc
import hashlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import mb, peak_rss_bytes, rss_bytes, timeit
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for

ROOT = Path(__file__).resolve().parents[1]
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"
CONFIG = ROOT / "config" / "settings.yaml"

# Windows matching research_acceleration benchmarks
EQUITY_START = datetime(2019, 1, 1, tzinfo=UTC)
EQUITY_END = datetime(2021, 12, 31, tzinfo=UTC)

# CI-safe synthetic parameters
SYNTH_SYMBOLS = 50
SYNTH_DAYS = 400  # need >274 for beta window
SYNTH_START = datetime(2020, 1, 1, tzinfo=UTC)


def _synthetic_bars(symbols: int = SYNTH_SYMBOLS, days: int = SYNTH_DAYS, start: datetime = SYNTH_START) -> list[Bar]:
    """Deterministic synthetic bars (no CSV needed) for CI-safe benchmarking."""
    import math
    import random

    random.seed(42)
    bars: list[Bar] = []
    # SPY benchmark
    spy_price = 300.0
    for i in range(days):
        ts = start + timedelta(days=i)
        if ts.weekday() >= 5:
            continue
        ret = random.gauss(0.0003, 0.012)
        spy_price *= 1 + ret
        spy_price = max(10.0, spy_price)
        close = spy_price
        open_ = close * (1 + random.uniform(-0.002, 0.002))
        high = max(open_, close) * (1 + random.uniform(0.0, 0.01))
        low = min(open_, close) * (1 - random.uniform(0.0, 0.01))
        bars.append(
            Bar(
                timestamp=ts,
                symbol="SPY",
                timeframe="1d",
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=80_000_000 + random.randint(-10_000_000, 10_000_000),
                market=Market.EQUITY,
            )
        )
        for s in range(symbols):
            sym = f"SYN{s:03d}"
            # deterministic but varied, keep price deterministic for parity
            price = max(5.0, 50 + math.sin(i * 0.05 + s) * 5 + random.random() * 2 + i * 0.01)
            close2 = price
            open2 = close2 * (1 + random.uniform(-0.01, 0.01))
            high2 = max(open2, close2) * (1 + random.uniform(0.0, 0.015))
            low2 = min(open2, close2) * (1 - random.uniform(0.0, 0.015))
            bars.append(
                Bar(
                    timestamp=ts,
                    symbol=sym,
                    timeframe="1d",
                    open=open2,
                    high=high2,
                    low=low2,
                    close=close2,
                    volume=1_000_000 + random.randint(0, 2_000_000),
                    market=Market.EQUITY,
                )
            )
    return bars


def _hash_result(result) -> str:
    """Deterministic hash of key result fields for parity."""
    h = hashlib.sha256()
    h.update(str(result.ending_equity).encode())
    h.update(str(len(result.fills)).encode())
    h.update(str(len(result.rebalances)).encode())
    for fill in result.fills:
        h.update(f"{fill.timestamp.isoformat()}{fill.symbol}{fill.qty:.6f}{fill.fill_price:.6f}".encode())
    for ts, w in result.weight_history:
        h.update(ts.isoformat().encode())
        for sym, wt in sorted(w.items()):
            h.update(f"{sym}{wt:.9f}".encode())
    h.update(str(result.turnover).encode())
    h.update(str(result.max_drawdown).encode())
    h.update(str(result.cagr).encode())
    return h.hexdigest()[:16]


def _run_engine_benchmark(
    bars: list[Bar],
    market: Market,
    start: datetime,
    end: datetime,
    label: str,
    repeats: int = 3,
) -> dict:
    """Run engine benchmark with reuse, return metrics."""
    config = load_config(str(CONFIG))
    # Universe + prepared for reuse
    universe = build_product_universe(config, bars, market=market, end=end, timeframe="1d")
    from parayabuck.backtest.numeric_history import build_numeric_history
    from parayabuck.backtest.product import _prepared_window

    strat = portfolio_strategy_from(config.strategy.equity.name, parameters=config.strategy.equity.parameters)
    prepared = _prepared_window(bars, market, strat.timeframe, start, end)
    # Build numeric view once per prepared dataset (reused across trials)
    filtered_pre, warmup_pre, (ordered_pre, decision_bars_pre) = prepared
    try:
        # Use deduped combined for view (already canonicalized/sorted)
        combined_for_view = (warmup_pre or []) + ordered_pre
        numeric_view = build_numeric_history(combined_for_view, market=market, timeframe=strat.timeframe)
        # Make immutable (already in builder) and ensure not writable
    except Exception:
        numeric_view = None

    def single_engine():
        # Engine-only path: directly time PortfolioBacktestEngine.run
        strategy = portfolio_strategy_from(config.strategy.equity.name, parameters=config.strategy.equity.parameters)
        risk = portfolio_risk_limits_from_config(config.risk)
        engine = PortfolioBacktestEngine(
            strategy,
            rebalance_policy_for(strategy),
            starting_equity=config.backtest.initial_cash,
            slippage_bps=config.backtest.slippage_bps,
            spread_bps=config.backtest.spread_bps,
            commission=config.backtest.commission,
            benchmark_symbol="SPY",
            universe=universe,
            universe_version=hash_point_in_time_universe(universe),
            risk_limits=risk,
            maximum_allocation=config.strategy.equity.maximum_allocation,
            execution_policy=PortfolioExecutionPolicy(
                rebalance_band_pct=config.execution.rebalance_band_pct,
                minimum_order_notional=config.execution.minimum_order_notional,
            ),
        )
        filtered, warmup, (ordered, decision_bars) = prepared
        return engine.run(
            filtered, warmup=warmup, prepared_inputs=(ordered, decision_bars), numeric_history=numeric_view
        )

    def single_product():
        return run_product_backtest(
            config,
            bars,
            market=market,
            start=start,
            end=end,
            persist=False,
            persist_artifacts=False,
            universe=universe,
            prepared_inputs=prepared,
            numeric_history=numeric_view,
        )

    # Measure end-to-end (includes cost of run_product_backtest wrapper)
    gc.collect()
    rss_before = rss_bytes()
    peak_before = peak_rss_bytes()
    t0 = time.perf_counter()
    result = single_product()
    engine_only = time.perf_counter() - t0
    rss_after = rss_bytes()
    peak_after = peak_rss_bytes()

    # Timed runs for median
    timing_product, _ = timeit(single_product, label=f"{label}_product", warmup=1, runs=repeats)
    timing_engine, _ = timeit(single_engine, label=f"{label}_engine", warmup=1, runs=repeats)

    # Repeated same-invariant runs (research usage)
    def batch_4():
        out = []
        for _ in range(4):
            out.append(single_product())
        return out

    gc.collect()
    t0 = time.perf_counter()
    batch_4()
    batch_time = time.perf_counter() - t0
    timing_batch, _ = timeit(batch_4, label=f"{label}_batch4", warmup=0, runs=1)

    _ = single_engine()
    bars_count = len(prepared[2][0]) + len(prepared[1])
    decisions = len(result.result.rebalances)
    # Actually decisions = len(rebalances) but engine also had _decide calls for each timestamp; use rebalances as decision count
    bars_per_sec = bars_count / max(timing_engine.median, 1e-9)
    decisions_per_sec = decisions / max(timing_engine.median, 1e-9) if decisions else 0

    return {
        "label": label,
        "bars_total": bars_count,
        "decisions": decisions,
        "fills": len(result.result.fills),
        "ending_equity": result.result.ending_equity,
        "hash": _hash_result(result.result),
        "engine_median_s": round(timing_engine.median, 4),
        "engine_p95_s": round(timing_engine.p95, 4)
        if hasattr(timing_engine, "p95")
        else round(timing_engine.median, 4),
        "engine_min_s": round(timing_engine.minimum, 4),
        "engine_max_s": round(timing_engine.maximum, 4),
        "product_median_s": round(timing_product.median, 4),
        "product_single_s": round(engine_only, 4),
        "batch4_s": round(timing_batch.median, 4),
        "batch4_no_timing_s": round(batch_time, 4),
        "bars_per_sec": round(bars_per_sec, 1),
        "decisions_per_sec": round(decisions_per_sec, 2),
        "peak_rss_mb": round(mb(peak_after), 1),
        "rss_delta_mb": round(mb(rss_after - rss_before), 1),
        "peak_rss_delta_mb": round(mb(peak_after - peak_before), 1),
    }


def main() -> int:
    machine = {
        "head": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if Path(".git").exists()
        else "unknown",
        "python": __import__("platform").python_version(),
        "platform": __import__("platform").platform(),
    }
    output: dict = {"generated_at": datetime.now(UTC).isoformat(), "machine": machine, "cases": {}}

    # Case C: CI-safe synthetic (always runs)
    print("=== Case C: CI-safe synthetic ===")
    synth_bars = _synthetic_bars()
    print(f"synthetic bars {len(synth_bars)} symbols {SYNTH_SYMBOLS} days {SYNTH_DAYS}")
    # For synthetic, universe window is synthetic range
    synth_start = SYNTH_START + timedelta(days=300)  # leave warmup
    synth_end = SYNTH_START + timedelta(days=390)
    # adjust to weekdays
    synth_metrics = _run_engine_benchmark(synth_bars, Market.EQUITY, synth_start, synth_end, label="synthetic")
    print(
        f"C synthetic engine median {synth_metrics['engine_median_s']}s product {synth_metrics['product_median_s']}s bars/sec {synth_metrics['bars_per_sec']}"
    )
    output["cases"]["C_synthetic"] = synth_metrics

    # Case A & B: realistic if CSV present
    if EQUITY_CSV.exists():
        print("\n=== Case A: realistic equity 2019-2021 ===")
        bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
        print(f"CSV bars {len(bars)}")
        metrics_a = _run_engine_benchmark(bars, Market.EQUITY, EQUITY_START, EQUITY_END, label="equity_3y")
        print(
            f"A engine median {metrics_a['engine_median_s']}s product {metrics_a['product_median_s']}s batch4 {metrics_a['batch4_s']}s"
        )
        output["cases"]["A_realistic_3y"] = metrics_a

        # Case B is essentially batch4 of A; also do smaller window for faster profiling
        print("\n=== Case B: repeated 6m window (research) ===")
        small_start = datetime(2019, 1, 1, tzinfo=UTC)
        small_end = datetime(2019, 6, 30, tzinfo=UTC)
        metrics_b = _run_engine_benchmark(bars, Market.EQUITY, small_start, small_end, label="equity_6m")
        print(f"B engine median {metrics_b['engine_median_s']}s batch4 {metrics_b['batch4_s']}s")
        output["cases"]["B_6m"] = metrics_b
    else:
        print("EQUITY CSV missing, skipping realistic cases")
        output["cases"]["A_realistic_3y"] = None
        output["cases"]["B_6m"] = None

    out_path = ROOT / "benchmarks" / "backtest_core_before.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
