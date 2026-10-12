"""Startup-path baseline: config load, runtime construction, SQLite store open.

The full async start() (warmup, reconcile, catch-up) is benchmarked by the
scanner/startup worker against fakes; this script covers the synchronous
prefix of the process-start path.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import replace

from benchmarks.harness import Report, timeit
from parayabuck.config import load_config
from parayabuck.runtime import EngineRuntime
from parayabuck.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "settings.yaml"


def _constructor(tmp_path: Path) -> EngineRuntime:
    """Return an EngineRuntime whose decision database lives under ``tmp_path``."""
    config = load_config(str(CONFIG))
    config = replace(config, runtime=replace(config.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    return EngineRuntime("key", "secret", config)


def run(report: Report) -> None:
    # A fresh throwaway directory per run: never under the repository.
    scratch = Path(tempfile.mkdtemp(prefix="bench_startup_"))
    timing, _ = timeit(
        lambda: load_config(str(CONFIG)),
        label="config load",
        warmup=3,
        runs=10,
    )
    report.add(timing)

    timing, _ = timeit(
        lambda: SQLiteStore(str(scratch / "bench_open.sqlite3")),
        label="SQLiteStore open (tmp db)",
        warmup=3,
        runs=10,
        before_each=lambda: (scratch / "bench_open.sqlite3").unlink(missing_ok=True),
    )
    report.add(timing)

    tmp = scratch / "bench_runtime"
    tmp.mkdir(parents=True, exist_ok=True)
    timing, runtime = timeit(
        lambda: _constructor(tmp),
        label="EngineRuntime construction",
        warmup=0,
        runs=5,
    )
    report.add(timing)
    if runtime is not None:
        runtime._portfolio_repository.close()
        runtime.store.release()


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Startup-path timings: config load, SQLite open, runtime construction."
    ).parse_args()
    report = Report()
    run(report)
    report.print_table()
