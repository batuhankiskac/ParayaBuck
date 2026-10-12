"""Backtest benchmark: VT35 product backtest on the committed golden fixture.

Times CSV loading and ``run_product_backtest`` on
``tests/fixtures/vt35_golden_bars.csv`` (QQQ/QLD/SPY 2019-2022, the same bars
``tests/test_vt35_golden.py`` locks), so it always runs from a clean checkout.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, timeit
from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import Config, load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vt35_golden_bars.csv"
CONFIG = ROOT / "config" / "settings.yaml"


def run(report: Report) -> None:
    config: Config = load_config(str(CONFIG))
    timing, bars = timeit(
        lambda: load_csv_bars(FIXTURE, default_market=Market.EQUITY),
        label="golden fixture CSV load (3,024 rows)",
        warmup=1,
        runs=5,
    )
    report.add(timing)

    with tempfile.TemporaryDirectory() as output_dir:

        def equity_run() -> None:
            run_product_backtest(
                config,
                bars,
                market=Market.EQUITY,
                output_dir=output_dir,
                persist_artifacts=False,
            )

        timing, _ = timeit(equity_run, label="VT35 backtest 2019-2022", warmup=1, runs=5)
        report.add(timing)


if __name__ == "__main__":
    import argparse

    from benchmarks.harness import mb, peak_rss_bytes

    argparse.ArgumentParser(description="VT35 product backtest timing on the committed golden bars.").parse_args()

    baseline_rss = peak_rss_bytes()
    report = Report()
    run(report)
    report.print_table()
    print(
        f"peak RSS delta over benchmark run: {mb(peak_rss_bytes() - baseline_rss):.1f} MB "
        f"(baseline {mb(baseline_rss):.1f} MB -> {mb(peak_rss_bytes()):.1f} MB)"
    )
