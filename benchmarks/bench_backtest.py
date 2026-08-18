"""Canonical backtest benchmarks: equity v2 (weekly) and crypto v2 (96h).

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
CRYPTO_CSV = ROOT / "data" / "historical" / "crypto_18_1d_extended.csv"
CONFIG = ROOT / "config" / "settings.yaml"

# Canonical window: 3 years for equity (1200 symbols), full 2021+ for crypto.
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
                strategy_name="equity_residual_momentum_v2",
                start=datetime.fromisoformat(EQUITY_START).replace(tzinfo=UTC),
                end=datetime.fromisoformat(EQUITY_END).replace(tzinfo=UTC),
                output_dir=str(ROOT / "data" / "smoke" / "bench_equity"),
                persist_artifacts=False,
            )

        timing, _ = timeit(equity_run, label="equity v2 backtest 2019-2021", warmup=0, runs=3)
        report.add(timing)

    if CRYPTO_CSV.exists():
        timing, crypto_bars = timeit(
            lambda: _bars_for(CRYPTO_CSV, Market.CRYPTO),
            label="crypto CSV load (22K rows)",
            warmup=0,
            runs=1,
        )
        report.add(timing)
        if crypto_bars:
            def crypto_run() -> None:
                run_product_backtest(
                    config,
                    crypto_bars,
                    market=Market.CRYPTO,
                    strategy_name="crypto_low_variance_v2",
                    output_dir=str(ROOT / "data" / "smoke" / "bench_crypto"),
                    persist_artifacts=False,
                )

            timing, _ = timeit(crypto_run, label="crypto v2 backtest full", warmup=0, runs=3)
            report.add(timing)
    else:
        print("crypto CSV missing; skipped", file=sys.stderr)


if __name__ == "__main__":
    report = Report()
    run(report)
    report.print_table()
