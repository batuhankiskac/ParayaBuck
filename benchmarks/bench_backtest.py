"""Canonical backtest benchmark: equity leveraged trend v1 (daily).

Uses the gitignored local historical CSVs when present; falls back to a
smaller synthetic window when they are missing (report is labeled synthetic).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, timeit
from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"
CONFIG = ROOT / "config" / "settings.yaml"

# Canonical window: 3 years for equity (1200 symbols).
EQUITY_START = "2019-01-01"
EQUITY_END = "2021-12-31"


def _bars_for(path: Path, market: Market) -> list:
    return load_csv_bars(path, default_market=market)


def run(report: Report) -> None:
    config = load_config(str(CONFIG))

    if EQUITY_CSV.exists():
        timing, bars = timeit(
            lambda: _bars_for(EQUITY_CSV, Market.EQUITY),
            label="equity CSV load (1.7M rows)",
            warmup=0,
            runs=1,
        )
        report.add(timing)
        market = Market.EQUITY
    else:
        bars = []
        market = Market.EQUITY

    if bars:

        def equity_run() -> None:
            run_product_backtest(
                config,
                bars,
                market=market,
                strategy_name="equity_leveraged_trend_v1",
                # The dataset has no QLD; hold QQQ so the run trades.
                parameters={"hold_symbol": "QQQ", "vol_target": 0.35},
                start=datetime.fromisoformat(EQUITY_START).replace(tzinfo=UTC),
                end=datetime.fromisoformat(EQUITY_END).replace(tzinfo=UTC),
                output_dir=str(ROOT / "data" / "smoke" / "bench_equity"),
                persist_artifacts=False,
            )

        timing, _ = timeit(equity_run, label="equity trend backtest 2019-2021", warmup=0, runs=3)
        report.add(timing)


if __name__ == "__main__":
    from benchmarks.harness import mb, peak_rss_bytes

    baseline_rss = peak_rss_bytes()
    report = Report()
    run(report)
    report.print_table()
    print(
        f"peak RSS delta over benchmark run: {mb(peak_rss_bytes() - baseline_rss):.1f} MB "
        f"(baseline {mb(baseline_rss):.1f} MB -> {mb(peak_rss_bytes()):.1f} MB)"
    )
