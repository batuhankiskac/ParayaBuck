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
        dataset_path=str(EQUITY_CSV),
        start=START,
        end=END,
        tier=ResearchTier.QUICK,
        workers=workers,
        trials=trials,
        persist_artifacts=False,
        persist_db=False,
    )


def run(report: Report) -> None:
    # Single runner baseline: sequential
    runner = ResearchRunner()
    # Warm cache first so measurements are not dominated by first-build cost
    print("Warming cache with one sequential run...")
    runner.run(_job(1))

    def seq():
        runner.run(_job(1))

    timing, _ = timeit(seq, label="research 4 trials workers=1 (quick, cached)", warmup=0, runs=3)
    report.add(timing)
    print(f"workers=1 median {timing.median:.3f}s")

    def par2():
        runner.run(_job(2))

    timing2, _ = timeit(par2, label="research 4 trials workers=2 (quick, cached)", warmup=0, runs=3)
    report.add(timing2)
    print(f"workers=2 median {timing2.median:.3f}s speedup {timing.median / timing2.median:.2f}x")

    # Only test 4 workers if we have enough cores and RAM
    import os

    logical = os.cpu_count() or 1
    if logical >= 4:

        def par4():
            runner.run(_job(4))

        timing4, _ = timeit(par4, label="research 4 trials workers=4 (quick, cached)", warmup=0, runs=3)
        report.add(timing4)
        print(f"workers=4 median {timing4.median:.3f}s speedup {timing.median / timing4.median:.2f}x")
    else:
        print("Skipping workers=4: not enough cores")

    # Single trial for reference
    def single():
        job = ResearchJob(
            market=Market.EQUITY,
            strategy_name="equity_residual_momentum_v2",
            dataset_path=str(EQUITY_CSV),
            start=START,
            end=END,
            tier=ResearchTier.QUICK,
            workers=1,
            trials=(ResearchTrial(label="single", cost_multiplier=1.0),),
            persist_artifacts=False,
        )
        runner.run(job)

    timing_s, _ = timeit(single, label="research single trial workers=1", warmup=0, runs=3)
    report.add(timing_s)

    # Memory: measure RSS after each run
    print(f"peak RSS {mb(peak_rss_bytes()):.1f} MB rss current {mb(rss_bytes()):.1f} MB")


if __name__ == "__main__":
    from benchmarks.harness import Report

    baseline = peak_rss_bytes()
    report = Report()
    run(report)
    report.print_table()
    print(f"peak RSS delta {mb(peak_rss_bytes() - baseline):.1f} MB")
