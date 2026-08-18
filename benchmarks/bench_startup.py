"""Startup-path baseline: config load, runtime construction (with fakes for
network-dependent universe discovery), SQLite store open.

The full async start() (warmup, reconcile, catch-up) is benchmarked by the
scanner/startup worker against fakes; this script covers the synchronous
prefix of the process-start path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

import parayabuck.runtime as runtime_module
from benchmarks.harness import Report, timeit
from parayabuck.config import Config, load_config
from parayabuck.runtime import EngineRuntime
from parayabuck.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "settings.yaml"


def _monkeypatched_constructor(tmp_path: Path):
    """Return an EngineRuntime built with universe discovery faked."""
    real_init = EngineRuntime.__init__

    def init(self, api_key: str, secret_key: str, config: Config) -> None:
        config = config.__class__(
            runtime=config.runtime.__class__(
                decision_db_path=str(tmp_path / "runtime.sqlite3"),
                mode=config.runtime.mode,
                execution_timeframe=config.runtime.execution_timeframe,
                market_state_timeframe=config.runtime.market_state_timeframe,
                scanner_refresh_hours=config.runtime.scanner_refresh_hours,
            ),
            **{
                key: value
                for key, value in vars(config).items()
                if key != "runtime"
            },
        )
        real_init(self, api_key, secret_key, config)

    orig_ensure = runtime_module.ensure_active_universes
    orig_equities = runtime_module.load_active_symbols
    orig_crypto = runtime_module.load_active_crypto_symbols
    runtime_module.ensure_active_universes = lambda config, api_key, secret_key: SimpleNamespace(
        equity_status="success", crypto_status="success"
    )
    runtime_module.load_active_symbols = lambda config, log_rejection=True: (
        "SPY",
        "AAPL",
        "MSFT",
        "NVDA",
        "TSLA",
        "AMZN",
        "META",
        "GOOGL",
    )
    runtime_module.load_active_crypto_symbols = lambda config, log_rejection=True: (
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    )
    try:
        return EngineRuntime("key", "secret", load_config(str(CONFIG)))
    finally:
        runtime_module.ensure_active_universes = orig_ensure
        runtime_module.load_active_symbols = orig_equities
        runtime_module.load_active_crypto_symbols = orig_crypto


def run(report: Report) -> None:
    timing, _ = timeit(
        lambda: load_config(str(CONFIG)),
        label="config load",
        warmup=3,
        runs=10,
    )
    report.add(timing)

    timing, _ = timeit(
        lambda: SQLiteStore(str(ROOT / "data" / "smoke" / "bench_open.sqlite3")),
        label="SQLiteStore open (tmp db)",
        warmup=3,
        runs=10,
        before_each=lambda: Path(ROOT / "data" / "smoke" / "bench_open.sqlite3").unlink(
            missing_ok=True
        ),
    )
    report.add(timing)

    tmp = ROOT / "data" / "smoke" / "bench_runtime"
    tmp.mkdir(parents=True, exist_ok=True)
    timing, runtime = timeit(
        lambda: _monkeypatched_constructor(tmp),
        label="EngineRuntime construction (faked discovery)",
        warmup=0,
        runs=5,
    )
    report.add(timing)
    if runtime is not None:
        runtime._portfolio_repository.close()
        runtime.store.release()


if __name__ == "__main__":
    report = Report()
    run(report)
    report.print_table()
