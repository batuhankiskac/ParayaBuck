"""Benchmark research runner: sequential vs parallel, with invariant reuse."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, mb, peak_rss_bytes, rss_bytes, timeit
from parayabuck.core.models import Market
from parayabuck.research import ResearchJob, ResearchTier, ResearchTrial
from parayabuck.research.runner import ResearchRunner

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "settings.yaml"
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"

START = datetime(2019, 1, 1, tzinfo=UTC)
END = datetime(2021, 12, 31, tzinfo=UTC)


def _job(workers: int | str) -> ResearchJob:
    trials = tuple(ResearchTrial(label=f"c{m}x", cost_multiplier=m) for m in (1.0, 1.5, 2.0, 3.0))
    return ResearchJob(
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        tier=ResearchTier.QUICK,
        workers=workers,
        trials=trials,
        start=START,
        end=END,
        dataset_path=str(EQUITY_CSV),
    )


def _memory_report(result) -> dict:
    """Build corrected memory evidence from TrialResult fields.

    Returns dict with:
      parent_peak_rss_mb
      unique_worker_count
      worker_peak_rss_mb_by_pid
      sum_worker_peak_rss_mb
      upper_bound_parent_plus_worker_peaks_mb (upper-bound, not simultaneous)
    """
    parent_peak = mb(peak_rss_bytes())
    # aggregate worker peaks by pid (max per pid, since each worker may handle multiple trials)
    by_pid: dict[int, float] = {}
    for r in result.results:
        if r.worker_pid is not None and r.worker_peak_rss_mb is not None:
            pid = r.worker_pid
            by_pid[pid] = max(by_pid.get(pid, 0.0), float(r.worker_peak_rss_mb))
    # For workers=1, worker is parent, so pid may equal parent pid; still count unique
    worker_peak_by_pid = {str(k): round(v, 1) for k, v in sorted(by_pid.items())}
    sum_worker = round(sum(by_pid.values()), 1) if by_pid else 0.0
    # Upper bound: parent peak + sum of unique worker peaks (peaks may not be simultaneous)
    # If worker is parent (workers=1), avoid double counting: upper bound is max(parent, sum)
    # For clarity, we report parent + sum but note it is an upper-bound estimate.
    # When workers=1, parent and worker are same process, so upper bound is parent itself.
    if len(by_pid) == 1 and result.workers == 1:
        upper = round(parent_peak, 1)
    else:
        upper = round(parent_peak + sum_worker, 1)
    return {
        "parent_peak_rss_mb": round(parent_peak, 1),
        "unique_worker_count": len(by_pid),
        "worker_peak_rss_mb_by_pid": worker_peak_by_pid,
        "sum_worker_peak_rss_mb": sum_worker,
        "upper_bound_parent_plus_worker_peaks_mb": upper,
        "note": "sum of independent peak RSS values is an upper-bound estimate, not necessarily simultaneous observed peak",
    }


def run(report: Report) -> None:
    # Single runner baseline: sequential
    runner = ResearchRunner()
    # Warm cache first so measurements are not dominated by first-build cost
    print("Warming cache with one sequential run...")
    warm = runner.run(_job(1))
    print(f"warm parent {mb(peak_rss_bytes()):.1f} MB, workers {warm.workers} actual, mem {_memory_report(warm)}")

    def seq():
        return runner.run(_job(1))

    timing, res1 = timeit(seq, label="research 4 trials workers=1 (quick, cached)", warmup=0, runs=3)
    report.add(timing)
    mem1 = _memory_report(res1) if res1 else {}
    print(f"workers=1 median {timing.median:.3f}s mem {mem1}")

    def par2():
        return runner.run(_job(2))

    timing2, res2 = timeit(par2, label="research 4 trials workers=2 (quick, cached)", warmup=0, runs=3)
    report.add(timing2)
    mem2 = _memory_report(res2) if res2 else {}
    print(f"workers=2 median {timing2.median:.3f}s speedup {timing.median / timing2.median:.2f}x mem {mem2}")

    # Only test 4 workers if we have enough cores and RAM
    import os

    logical = os.cpu_count() or 1
    if logical >= 4:

        def par4():
            return runner.run(_job(4))

        timing4, res4 = timeit(par4, label="research 4 trials workers=4 (quick, cached)", warmup=0, runs=3)
        report.add(timing4)
        mem4 = _memory_report(res4) if res4 else {}
        print(f"workers=4 median {timing4.median:.3f}s speedup {timing.median / timing4.median:.2f}x mem {mem4}")
    else:
        print("Skipping workers=4: not enough cores")

    # Single trial for reference
    def single():
        job = ResearchJob(
            market=Market.EQUITY,
            strategy_name="equity_residual_momentum_v2",
            tier=ResearchTier.QUICK,
            workers=1,
            trials=(ResearchTrial(label="single", cost_multiplier=1.0),),
            start=START,
            end=END,
            dataset_path=str(EQUITY_CSV),
        )
        return runner.run(job)

    timing_s, res_s = timeit(single, label="research single trial workers=1", warmup=0, runs=3)
    report.add(timing_s)
    mem_s = _memory_report(res_s) if res_s else {}
    print(f"single median {timing_s.median:.3f}s mem {mem_s}")

    # Memory: measure RSS after each run
    print(f"peak RSS {mb(peak_rss_bytes()):.1f} MB rss current {mb(rss_bytes()):.1f} MB")
    # Also report final memory evidence table
    print("\nCorrected memory evidence (upper-bound):")
    print(f"  workers=1: {mem1}")
    print(f"  workers=2: {mem2}")
    if logical >= 4:
        print(f"  workers=4: {mem4}")


if __name__ == "__main__":
    from benchmarks.harness import Report

    report = Report()
    baseline = peak_rss_bytes()
    run(report)
    report.print_table()
    print(f"peak RSS delta {mb(peak_rss_bytes() - baseline):.1f} MB")
