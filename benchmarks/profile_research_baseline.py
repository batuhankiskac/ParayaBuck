"""Profile research baseline: Scenario A/B/C before optimization."""

from __future__ import annotations

import cProfile
import hashlib
import json
import os
import platform
import pstats
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import mb, peak_rss_bytes, rss_bytes, timeit
from parayabuck.backtest.product import (
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    optimize_grid,
    run_product_backtest,
)
from parayabuck.config import load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"
PIT_CSV = ROOT / "data" / "historical" / "equity_pit_symmetric_2016_2026_v5.csv"
CONFIG = ROOT / "config" / "settings.yaml"

EQUITY_START = datetime(2019, 1, 1, tzinfo=UTC)
EQUITY_END = datetime(2021, 12, 31, tzinfo=UTC)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _machine_info() -> dict:
    total_gb = 0
    avail_gb = 0
    cpu_physical = None
    try:
        import psutil  # type: ignore

        mem = psutil.virtual_memory()
        total_gb = round(mem.total / (1024**3), 1)
        avail_gb = round(mem.available / (1024**3), 1)
        cpu_physical = psutil.cpu_count(logical=False)
    except Exception:
        try:
            total_str = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip()
            total_gb = round(int(total_str) / (1024**3), 1)
        except Exception:
            total_gb = 0
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count_logical": os.cpu_count(),
        "cpu_count_physical": cpu_physical,
        "ram_total_gb": total_gb,
        "ram_available_gb": avail_gb,
        "host": f"{platform.system()} {platform.release()} {platform.machine()} {platform.processor()}".strip(),
    }


def _head_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _profile_top_functions(fn) -> list[dict]:
    pr = cProfile.Profile()
    pr.enable()
    fn()
    pr.disable()
    ps = pstats.Stats(pr)
    ps.sort_stats(pstats.SortKey.CUMULATIVE)
    top = []
    for func, (cc, nc, tt, ct, callers) in list(ps.stats.items())[:12]:
        filename, lineno, func_name = func
        top.append(
            {
                "func": f"{Path(filename).name}:{func_name}:{lineno}",
                "cumtime": round(ct, 4),
                "tottime": round(tt, 4),
                "calls": cc,
            }
        )
    return top


def main() -> int:
    config = load_config(str(CONFIG))
    csv_path = EQUITY_CSV if EQUITY_CSV.exists() else PIT_CSV
    if not csv_path.exists():
        print(f"ERROR: no dataset found at {EQUITY_CSV} or {PIT_CSV}", file=sys.stderr)
        return 1

    dataset_hash = _sha256_file(csv_path)
    machine = _machine_info()
    head = _head_sha()
    print(f"Dataset: {csv_path} SHA={dataset_hash[:12]}.. size={csv_path.stat().st_size / (1024**2):.1f} MB")
    print(f"Machine: {machine}")
    print(f"HEAD: {head}")

    print("\n=== Scenario A: single equity backtest 2019-2021 ===")
    t0 = time.perf_counter()
    rss_before = rss_bytes()
    bars = load_csv_bars(csv_path, default_market=Market.EQUITY)
    csv_time = time.perf_counter() - t0
    rss_after_csv = rss_bytes()
    print(
        f"CSV load: {csv_time:.3f}s bars={len(bars)} rss_delta={mb(rss_after_csv - rss_before):.1f} MB peak={mb(peak_rss_bytes()):.1f} MB"
    )

    t0 = time.perf_counter()
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EQUITY_END)
    universe_time = time.perf_counter() - t0
    print(f"Universe: {universe_time:.3f}s snapshots={len(universe.snapshots)}")

    t0 = time.perf_counter()
    from parayabuck.portfolio import portfolio_strategy_from

    strat = portfolio_strategy_from("equity_residual_momentum_v2", parameters=config.strategy.equity.parameters)
    prepared = _prepared_window(bars, Market.EQUITY, strat.timeframe, EQUITY_START, EQUITY_END)
    prep_time = time.perf_counter() - t0
    print(f"Prepared window: {prep_time:.3f}s filtered={len(prepared[0])} ordered={len(prepared[2][0])}")

    def single_run():
        return run_product_backtest(
            config,
            bars,
            market=Market.EQUITY,
            strategy_name="equity_residual_momentum_v2",
            start=EQUITY_START,
            end=EQUITY_END,
            output_dir=str(ROOT / "data" / "smoke" / "profile_single"),
            persist=False,
            persist_artifacts=False,
            universe=universe,
            prepared_inputs=prepared,
        )

    t0 = time.perf_counter()
    result_reuse = single_run()
    engine_reuse_time = time.perf_counter() - t0
    print(
        f"Engine+report (with prepared+universe): {engine_reuse_time:.3f}s total_return={result_reuse.result.total_return:.4f}"
    )

    t0 = time.perf_counter()
    _ = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        start=EQUITY_START,
        end=EQUITY_END,
        output_dir=str(ROOT / "data" / "smoke" / "profile_single_full"),
        persist=False,
        persist_artifacts=False,
    )
    full_time = time.perf_counter() - t0
    print(f"Engine+report (without prepared, includes universe+prep): {full_time:.3f}s")

    timing, _ = timeit(single_run, label="single_backtest_reuse", warmup=1, runs=3)
    print(
        f"Single reuse timing: med={timing.median:.3f}s p95={timing.p95:.3f} min={timing.minimum:.3f} max={timing.maximum:.3f}"
    )

    top = _profile_top_functions(single_run)
    print("Top functions single run:")
    for t in top[:8]:
        print(f"  {t['func']} cum={t['cumtime']} tot={t['tottime']}")

    print("\n=== Scenario B: 4-trial batch same window diff cost_multiplier ===")
    cost_multipliers = [1.0, 1.5, 2.0, 3.0]

    def batch_sequential_naive():
        results = []
        for cm in cost_multipliers:
            r = run_product_backtest(
                config,
                bars,
                market=Market.EQUITY,
                strategy_name="equity_residual_momentum_v2",
                start=EQUITY_START,
                end=EQUITY_END,
                cost_multiplier=cm,
                output_dir=str(ROOT / "data" / "smoke" / "profile_batch_naive"),
                persist=False,
                persist_artifacts=False,
            )
            results.append(r)
        return results

    def batch_sequential_reuse():
        results = []
        for cm in cost_multipliers:
            r = run_product_backtest(
                config,
                bars,
                market=Market.EQUITY,
                strategy_name="equity_residual_momentum_v2",
                start=EQUITY_START,
                end=EQUITY_END,
                cost_multiplier=cm,
                output_dir=str(ROOT / "data" / "smoke" / "profile_batch_reuse"),
                persist=False,
                persist_artifacts=False,
                universe=universe,
                prepared_inputs=prepared,
            )
            results.append(r)
        return results

    t0 = time.perf_counter()
    batch_sequential_naive()
    naive_batch_time = time.perf_counter() - t0
    print(f"Batch naive (4 trials no reuse): {naive_batch_time:.3f}s rss_peak={mb(peak_rss_bytes()):.1f} MB")

    t0 = time.perf_counter()
    batch_sequential_reuse()
    reuse_batch_time = time.perf_counter() - t0
    print(f"Batch reuse (4 trials with reuse): {reuse_batch_time:.3f}s")

    timing_naive, _ = timeit(batch_sequential_naive, label="batch_4_naive", warmup=0, runs=1)
    timing_reuse, _ = timeit(batch_sequential_reuse, label="batch_4_reuse", warmup=0, runs=1)
    print(f"Batch naive (1 run): med={timing_naive.median:.3f}s")
    print(
        f"Batch reuse (1 run): med={timing_reuse.median:.3f}s speedup reuse={timing_naive.median / max(timing_reuse.median, 1e-9):.2f}x"
    )

    print("\n=== Scenario C: optimize_grid small workload ===")
    grid = {"mh_raw_window": [12, 9], "ivol_window": [0.70, 0.60]}
    t0 = time.perf_counter()
    opt_result = optimize_grid(
        config,
        bars,
        market=Market.EQUITY,
        grid=grid,
        strategy_name="equity_residual_momentum_v2",
        train_start=EQUITY_START,
        train_end=EQUITY_END,
        output_dir=str(ROOT / "data" / "smoke" / "profile_opt"),
    )
    opt_time = time.perf_counter() - t0
    print(f"optimize_grid 4 combos: {opt_time:.3f}s combinations={opt_result['combination_count']}")

    timing_opt, _ = timeit(
        lambda: optimize_grid(
            config,
            bars,
            market=Market.EQUITY,
            grid=grid,
            strategy_name="equity_residual_momentum_v2",
            train_start=EQUITY_START,
            train_end=EQUITY_END,
            output_dir=str(ROOT / "data" / "smoke" / "profile_opt2"),
        ),
        label="optimize_grid_4",
        warmup=0,
        runs=1,
    )
    print(f"optimize_grid (1 run): med={timing_opt.median:.3f}s")

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "head": head,
        "machine": machine,
        "dataset": {
            "path": str(csv_path),
            "sha256": dataset_hash,
            "size_bytes": csv_path.stat().st_size,
            "bars_total": len(bars),
        },
        "scenario_a_single_equity_2019_2021": {
            "csv_load_s": round(csv_time, 4),
            "universe_construction_s": round(universe_time, 4),
            "prepared_window_s": round(prep_time, 4),
            "engine_run_with_reuse_s": round(engine_reuse_time, 4),
            "engine_run_without_reuse_s": round(full_time, 4),
            "timing_reuse_median_s": round(timing.median, 4),
            "timing_reuse_p95_s": round(timing.p95, 4),
            "timing_reuse_min_s": round(timing.minimum, 4),
            "timing_reuse_max_s": round(timing.maximum, 4),
            "peak_rss_mb": round(mb(peak_rss_bytes()), 1),
            "total_return": result_reuse.result.total_return,
            "cagr": result_reuse.result.cagr,
            "sharpe": result_reuse.result.sharpe,
            "max_drawdown": result_reuse.result.max_drawdown,
            "trade_count": result_reuse.report.summary.get("number_of_trades"),
        },
        "scenario_b_4trial_batch": {
            "trials": 4,
            "cost_multipliers": cost_multipliers,
            "naive_no_reuse_s": round(naive_batch_time, 4),
            "naive_median_s": round(timing_naive.median, 4),
            "reuse_s": round(reuse_batch_time, 4),
            "reuse_median_s": round(timing_reuse.median, 4),
            "speedup_reuse_vs_naive": round(timing_naive.median / max(timing_reuse.median, 1e-9), 3),
            "throughput_naive_trials_per_sec": round(4 / max(timing_naive.median, 1e-9), 4),
            "throughput_reuse_trials_per_sec": round(4 / max(timing_reuse.median, 1e-9), 4),
        },
        "scenario_c_optimize_grid": {
            "grid": grid,
            "combinations": 4,
            "single_run_s": round(opt_time, 4),
            "median_s": round(timing_opt.median, 4),
            "p95_s": round(timing_opt.p95, 4),
            "min_s": round(timing_opt.minimum, 4),
            "max_s": round(timing_opt.maximum, 4),
        },
        "profiler_top_functions_single": top,
        "peak_rss_mb_overall": round(mb(peak_rss_bytes()), 1),
        "notes": {
            "workers": "baseline is sequential only; parallel not yet implemented",
            "date_range": "2019-01-01 to 2021-12-31",
            "strategy": "equity_residual_momentum_v2",
            "market": "equity",
            "timeframe": "1d",
        },
    }

    out_path = ROOT / "benchmarks" / "research_acceleration_baseline.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote baseline to {out_path}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
